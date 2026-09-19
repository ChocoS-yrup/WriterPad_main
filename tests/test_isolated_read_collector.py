"""Independent collector: synthetic bytes, temporary paths and MockTransport only."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

import isolated_read_collector as c
from sync_contract import SERVER_CAPABILITIES


class CollectorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'isolated'
        self.now = 1000
        self.calls = []
        self.doc = '10000000-0000-4000-8000-000000000001'
        self.top_order = '10000000-0000-4000-8000-000000000002'
        self.child_order = '10000000-0000-4000-8000-000000000003'
        self.text = 'synthetic original\n'
        self.rows = {
            'user': {'id': c.ACCOUNT},
            'get_sync_handshake': dict(project_id=c.PROJECT, project_sync_mode='ID_BASED', migration_epoch=1,
                server_protocol_version=3, supported_protocol_versions=[3],
                server_contract_sha256=c.CANONICAL_CONTRACT_SHA256,
                canonical_contract_sha256=c.CANONICAL_CONTRACT_SHA256,
                server_capabilities=list(SERVER_CAPABILITIES), contract_version=c.CONTRACT_VERSION),
            'projects': [dict(project_id=c.PROJECT, owner_id=c.ACCOUNT, name=c.PROJECT_NAME, deleted_at=None)],
            'project_sync_settings': [dict(project_id=c.PROJECT, project_sync_mode='ID_BASED', migration_epoch=1)],
            'documents': [dict(project_id=c.PROJECT, document_id=self.doc, parent_folder_id=c.PARENT,
                name='합성.txt', relative_path='메모장/합성.txt', revision=1, structure_revision=1,
                is_deleted=False, content=self.text)],
            'folders': [dict(project_id=c.PROJECT, folder_id=c.PARENT, parent_folder_id=None,
                name='메모장', revision=1, is_deleted=False)],
            'tree_orders': [dict(project_id=c.PROJECT, tree_order_id=self.top_order, parent_folder_id=None,
                                revision=1, children=[c.PARENT]),
                            dict(project_id=c.PROJECT, tree_order_id=self.child_order, parent_folder_id=c.PARENT,
                                 revision=1, children=[self.doc])]}
        self.metadata = dict(format='windows-isolated-target-metadata-proposal-v1', endpoint=c.ENDPOINT,
            account_id=c.ACCOUNT, server_project_id=c.PROJECT, complete=False, nodes=[
                dict(id=c.PARENT, kind='folder', parent_id=None, path='메모장', revision=1,
                     structure_revision=None, deleted=False, utf8_bytes=None, ends_lf=None, sha256=None),
                dict(id=self.doc, kind='text', parent_id=c.PARENT, path='메모장/합성.txt', revision=1,
                     structure_revision=1, deleted=False, **c.body_meta(self.rows['documents'][0]))])
        self.orders = dict(format='windows-retained-orders-v1', complete=False, orders=[
            dict(id=self.top_order, parent_id=None, revision=1, children=[c.PARENT]),
            dict(id=self.child_order, parent_id=c.PARENT, revision=1, children=[self.doc])])
        self.hook = None
        self.last_scope = None

    async def handler(self, request):
        self.calls.append(request)
        if self.hook:
            value = await self.hook(request)
            if value is not None:
                return value
        name = request.url.path.rsplit('/', 1)[-1]
        value = self.rows[name]
        headers = {'content-range': f'0-{max(0,len(value)-1)}/{len(value)}'} if isinstance(value, list) else {}
        return httpx.Response(200, content=c.encoded(value), headers=headers)

    def scope(self, **changes):
        value = dict(format='windows-isolated-read-scope-v1', run_id=str(uuid4()), endpoint=c.ENDPOINT,
            account_id=c.ACCOUNT, project_id=c.PROJECT, max_requests=7, max_seconds=180, expires_at=1200,
            reference_sha256=dict(metadata=c.digest(c.encoded(self.metadata)), orders=c.digest(c.encoded(self.orders))))
        value.update(changes)
        return value

    async def run_collector(self, scope=None):
        self.last_scope = scope or self.scope()
        return await c.collect(self.root, self.last_scope, c.encoded(self.metadata), c.encoded(self.orders),
            transport=httpx.MockTransport(self.handler), access_token='synthetic-secret-token',
            api_key='synthetic-secret-key', clock=lambda: self.now)

    def inspect(self):
        return c.inspect(self.root / self.last_scope['run_id'])

    async def test_normal_exact_seven_metadata_only_and_no_apply(self):
        sentinel = Path(self.tmp.name) / 'original.sqlite3'
        sentinel.write_bytes(b'original sentinel')
        result = await self.run_collector()
        self.assertEqual(result['status'], 'observed')
        self.assertEqual(result['http_used'], 7)
        self.assertFalse(result['complete'])
        self.assertFalse(result['baseline_ready'])
        self.assertFalse(result['atomic_snapshot'])
        self.assertEqual(sentinel.read_bytes(), b'original sentinel')
        self.assertEqual(len(self.calls), 7)
        self.assertEqual([(r.method,r.url.path) for r in self.calls], [(m,p) for m,p,_,_ in c.requests()])
        for r in self.calls[2:]:
            self.assertEqual(dict(r.url.params), {'project_id':'eq.'+c.PROJECT,'select':'*','limit':'10000'})
            self.assertEqual(r.headers['Prefer'], 'count=exact')
        self.assertEqual(self.inspect()['http_used'], 7)
        self.assertNotIn(self.text, json.dumps(result))
        directory = self.root / self.last_scope['run_id']
        self.assertIn(self.text.encode().replace(b'\n',b'\\n'), (directory/'Q5.body').read_bytes())
        for p in directory.iterdir():
            self.assertNotIn(b'synthetic-secret-token', p.read_bytes())
            self.assertNotIn(b'synthetic-secret-key', p.read_bytes())

    async def test_account_contract_owner_and_mode_stop_before_next_request(self):
        cases = [('user','id','wrong',1), ('get_sync_handshake','migration_epoch',2,2),
                 ('projects','owner_id','wrong',3), ('project_sync_settings','migration_epoch',2,4)]
        for table, field, value, used in cases:
            with self.subTest(table=table):
                row = self.rows[table][0] if isinstance(self.rows[table],list) else self.rows[table]
                old = row[field]; row[field] = value
                result = await self.run_collector()
                self.assertEqual((result['status'],result['http_used']), ('stopped',used))
                row[field] = old

    async def test_http_failures_redirect_and_rate_limit_never_retry(self):
        for status in (301,401,403,429,500):
            with self.subTest(status=status):
                async def hook(request):
                    return httpx.Response(status, json={'error':'synthetic'}, headers={'location':c.ENDPOINT+'/other'})
                self.hook = hook
                before = len(self.calls)
                result = await self.run_collector()
                self.assertEqual(result['stop_reason'], 'HTTP_STATUS_REFUSED')
                self.assertEqual(len(self.calls)-before, 1)
                self.assertEqual(self.inspect()['http_used'], 1)

    async def test_invalid_json_and_duplicate_keys_stop(self):
        for raw in (b'{', b'{"id":"a","id":"b"}', b'{"number":NaN}'):
            async def hook(request):
                return httpx.Response(200, content=raw)
            self.hook = hook
            result = await self.run_collector()
            self.assertEqual(result['status'], 'stopped')
            self.assertEqual(result['http_used'], 1)

    async def test_count_truncation_unknown_offset_and_missing_header(self):
        for header in ('0-0/1001','*/1','1-1/1',None):
            async def hook(request):
                if request.url.path.endswith('/documents'):
                    return httpx.Response(200, json=self.rows['documents'],
                        headers={} if header is None else {'content-range':header})
            self.hook = hook
            result = await self.run_collector()
            self.assertEqual((result['http_used'],result['stop_reason']), (5,'INCOMPLETE_TABLE_COUNT'))

    async def test_new_uuid_collision_in_raw_special_metadata_stops(self):
        self.rows['documents'].append(dict(project_id=c.PROJECT, document_id=c.ROOT_CANDIDATE,
                                          relative_path='__antigravity__/metadata'))
        result = await self.run_collector()
        self.assertEqual((result['http_used'],result['stop_reason']), (5,'CANDIDATE_COLLISION'))

    async def test_candidate_order_collision_stops(self):
        self.rows['tree_orders'][0]['tree_order_id'] = 'e3c2f57d-e116-5c5d-8941-1bcf0dfce53c'
        result = await self.run_collector()
        self.assertEqual(result['stop_reason'], 'CANDIDATE_COLLISION')

    async def test_protected_body_difference_reported_without_normalizing(self):
        self.rows['documents'][0]['content'] = self.text.rstrip('\n')
        result = await self.run_collector()
        self.assertEqual((result['stop_reason'],result['http_used']), ('REFERENCE_DIFFERENCE',5))
        self.assertIn('ends_lf', result['differences'][0]['fields'])
        self.assertEqual(self.metadata['nodes'][1]['sha256'], c.digest(self.text.encode()))

    async def test_parent_difference_and_path_mismatch_stop(self):
        original = deepcopy(self.rows['documents'][0])
        for field,value,used in [('parent_folder_id',None,5),('relative_path','wrong/합성.txt',6)]:
            self.rows['documents'][0] = dict(original, **{field:value})
            result = await self.run_collector()
            self.assertEqual(result['http_used'], used)
            self.assertEqual(result['stop_reason'], 'REFERENCE_DIFFERENCE')

    async def test_bad_graph_even_when_reference_matches_is_not_accepted(self):
        self.rows['folders'][0]['parent_folder_id'] = c.PARENT
        self.metadata['nodes'][0]['parent_id'] = c.PARENT
        result = await self.run_collector()
        self.assertEqual(result['stop_reason'], 'PARENT_CYCLE_OR_MISSING')

    async def test_duplicate_nodes_and_missing_order_are_refused(self):
        self.rows['documents'].append(deepcopy(self.rows['documents'][0]))
        result = await self.run_collector()
        self.assertEqual(result['stop_reason'], 'DUPLICATE_NODE')
        self.rows['documents'].pop()
        self.rows['tree_orders'].pop()
        result = await self.run_collector()
        self.assertEqual(result['stop_reason'], 'ORDER_MISSING')

    async def test_partial_order_snapshot_does_not_retry(self):
        self.rows['tree_orders'][1]['children'] = []
        result = await self.run_collector()
        self.assertEqual((result['stop_reason'],result['http_used']), ('INCOMPLETE_ORDER',7))

    async def test_special_metadata_kept_raw_separate_from_manuscripts(self):
        special='10000000-0000-4000-8000-000000000009'
        self.rows['documents'].append(dict(project_id=c.PROJECT, document_id=special,
                                          relative_path='__antigravity__/metadata'))
        result = await self.run_collector()
        self.assertEqual(result['status'],'observed')
        self.assertEqual(result['special_metadata_ids'], [special])
        self.assertEqual(len(result['nodes']),2)

    async def test_transport_error_keeps_reservation_and_same_run_cannot_restart(self):
        async def hook(request):
            raise httpx.ConnectError('do not log synthetic-secret-token')
        self.hook = hook
        scope = self.scope()
        result = await self.run_collector(scope)
        self.assertEqual(self.inspect()['http_used'],1)
        self.assertEqual(result['stop_reason'], 'VALIDATION_OR_IO_FAILED')
        with self.assertRaises(FileExistsError):
            await self.run_collector(scope)
        self.assertEqual(len(self.calls),1)

    async def test_total_deadline_cancels_slow_transport(self):
        async def hook(request):
            await asyncio.sleep(10)
        self.hook = hook
        start=time.monotonic()
        result = await self.run_collector(self.scope(max_seconds=0.05))
        self.assertLess(time.monotonic()-start,1)
        self.assertEqual((result['stop_reason'],result['http_used']), ('DEADLINE_OR_REQUEST_TIMEOUT',1))
        self.assertEqual(self.inspect()['http_used'],1)

    async def test_cancelled_invocation_preserves_attempt_no_replay(self):
        entered=asyncio.Event()
        async def hook(request):
            entered.set()
            await asyncio.sleep(10)
        self.hook=hook
        scope=self.scope()
        task=asyncio.create_task(self.run_collector(scope))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.inspect()['http_used'],1)
        self.assertEqual(self.inspect()['terminal'],'stopped')
        with self.assertRaises(FileExistsError):
            await self.run_collector(scope)

    async def test_clock_rollback_stops_and_does_not_return_usage(self):
        async def hook(request):
            self.now=999
        self.hook=hook
        result=await self.run_collector()
        self.assertEqual(result['stop_reason'],'CLOCK_ROLLBACK')
        self.assertEqual(self.inspect()['http_used'],1)

    async def test_invalid_scopes_and_ended_run_fail_without_creating_run(self):
        for changes in ({'run_id':c.ENDED_RUN}, {'max_requests':8}, {'max_requests':True},
                        {'max_seconds':181}, {'expires_at':1000}, {'endpoint':'https://example.invalid'},
                        {'reference_sha256':{}}, {'extra':1}):
            with self.subTest(changes=changes), self.assertRaises(c.ReadStopped):
                await self.run_collector(self.scope(**changes))
        self.assertFalse(self.root.exists())
        self.assertEqual(self.calls,[])

    async def test_response_limit_and_secret_echo_leave_no_secret_body(self):
        with patch.object(c,'MAX_RESPONSE',1):
            result=await self.run_collector()
        self.assertEqual(result['stop_reason'],'RESPONSE_TOO_LARGE')
        async def hook(request):
            return httpx.Response(200, content=b'synthetic-secret-token')
        self.hook=hook
        result=await self.run_collector()
        self.assertEqual(result['stop_reason'],'CREDENTIAL_IN_RESPONSE')
        for p in (self.root/self.last_scope['run_id']).iterdir():
            self.assertNotIn(b'synthetic-secret-token',p.read_bytes())

    async def test_response_and_ledger_tampering_are_detected_read_only(self):
        await self.run_collector()
        directory=self.root/self.last_scope['run_id']
        original=(directory/'Q1.body').read_bytes()
        (directory/'Q1.body').write_bytes(b'changed')
        with self.assertRaisesRegex(c.ReadStopped,'RESPONSE_CHANGED'):
            self.inspect()
        (directory/'Q1.body').write_bytes(original)
        with (directory/'journal.jsonl').open('ab') as out:
            out.write(b'partial')
        with self.assertRaisesRegex(c.ReadStopped,'PARTIAL_JOURNAL_PRESERVED'):
            self.inspect()

    async def test_scope_and_observation_hashes_are_bound_to_journal(self):
        await self.run_collector()
        directory=self.root/self.last_scope['run_id']
        raw=(directory/'scope.json').read_bytes()
        (directory/'scope.json').write_bytes(raw+b' ')
        with self.assertRaisesRegex(c.ReadStopped,'SCOPE_FILE_CHANGED'):
            self.inspect()
        (directory/'scope.json').write_bytes(raw)
        (directory/'observation.json').write_bytes(b'{}')
        with self.assertRaisesRegex(c.ReadStopped,'OBSERVATION_CHANGED'):
            self.inspect()


if __name__ == '__main__':
    unittest.main()
