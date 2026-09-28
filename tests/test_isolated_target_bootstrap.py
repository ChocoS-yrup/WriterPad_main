"""New synthetic bootstrap impact tests; no old test methods are executed."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
import isolated_target_bootstrap as b
from tests import test_integrated_editor as fixture
from tests.test_isolated_read_launcher import jwt

c = b.c
MAIN = '10000000-0000-4000-8000-000000000003'
PROTECTED = '10000000-0000-4000-8000-000000000007'


class BootstrapTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.now, self.active = 1000, True
        patcher = patch.object(fixture,'ACCOUNT',c.ACCOUNT)
        patcher.start(); self.addCleanup(patcher.stop)
        self.nodes = [dict(id=MAIN,kind='folder',parent=None,name='메인',deleted=False,revision=1,srev=1),
            dict(id=c.PARENT,kind='folder',parent=MAIN,name='메모장',deleted=False,revision=3,srev=1),
            dict(id=PROTECTED,kind='document',parent=c.PARENT,name='합성보호.txt',content='보호용 합성\n',deleted=False,revision=9,srev=4)]
        self.orders = [dict(id=fixture.order_id(None),parent=None,children=[MAIN],revision=1),
            dict(id=fixture.order_id(MAIN),parent=MAIN,children=[c.PARENT],revision=1),
            dict(id=b.PARENT_ORDER,parent=c.PARENT,children=[PROTECTED],revision=7)]
        self.server = fixture.Server(self.nodes,self.orders)
        self.before_nodes, self.before_orders = deepcopy(self.server.nodes),deepcopy(self.server.orders)
        self.meta = dict(format='windows-isolated-target-metadata-proposal-v1',endpoint=c.ENDPOINT,
            account_id=c.ACCOUNT,server_project_id=c.PROJECT,complete=False,nodes=[
                dict(id=n['id'],kind='text' if n['kind']=='document' else 'folder',parent_id=n['parent'],
                     path=self.path(n['id']),revision=n['revision'],
                     structure_revision=n['srev'] if n['kind']=='document' else None,deleted=n['deleted'],
                     **(c.body_meta({'content':n['content']}) if n['kind']=='document' else
                        dict(utf8_bytes=None,ends_lf=None,sha256=None))) for n in self.nodes])
        self.order_ref=dict(format='windows-retained-orders-v1',complete=False,orders=[
            dict(id=o['id'],parent_id=o['parent'],children=o['children'],revision=o['revision']) for o in self.orders])
        self.metadata_bytes=c.encoded(self.meta); self.orders_bytes=c.encoded(self.order_ref)
        self.body='신규 합성 본문 e\u0301 한글\n'.encode('utf-8')
        self.plan=b.prepare_plan(self.metadata_bytes,self.orders_bytes,self.body,str(uuid4()))
        self.scope=dict(format=b.BUILD,run_id=str(uuid4()),plan_sha256=c.digest(c.encoded(self.plan)),
            not_before=1000,expires_at=1180,max_requests=16,max_writes=4,max_seconds=180)
        self.calls=[]; self.hook=None; self.post_hook=None; self.commits=0

    def path(self,key):
        n=self.server.nodes[key]
        return (self.path(n['parent'])+'/' if n['parent'] else '')+n['name']

    @property
    def directory(self): return self.base/'new-runs'/self.scope['run_id']

    async def handler(self,request):
        self.calls.append((request.method,request.url.path))
        self.assertNotIn('cookie',request.headers)
        if self.hook:
            answer=self.hook(request)
            if answer is not None:return answer
        name=request.url.path.rsplit('/',1)[-1]
        response=self.server(request)
        if name in ('atomic_structure_commit','document_commit'):self.commits+=1
        if name=='documents':
            rows=response.json()
            for row in rows:row['relative_path']=self.path(row['document_id'])
            response=httpx.Response(response.status_code,json=rows,headers=response.headers)
        if self.post_hook:
            answer=self.post_hook(name,response)
            if answer is not None:response=answer
        response.headers['set-cookie']='synthetic=COOKIE_SECRET; Path=/; Secure'
        return response

    async def run_bootstrap(self):
        return await b.prepare_baseline(self.base/'new-runs',self.scope,self.plan,
            self.metadata_bytes,self.orders_bytes,self.body,transport=httpx.MockTransport(self.handler),
            access_token=jwt(exp=1400),api_key='synthetic-api-key',check_current=lambda:self.active,clock=lambda:self.now)

    def assert_blocked(self,result):
        self.assertEqual(result['status'],'stopped')
        self.assertFalse(result['baseline_applied'])
        with self.assertRaises(c.ReadStopped):b.read_candidate(self.directory)

    async def test_create_and_prepare_receipt_bound_raw_baseline_preserves_originals(self):
        result=await self.run_bootstrap()
        self.assertEqual((result['status'],result['http_reserved'],result['writes_acknowledged']),('candidate-prepared',16,4))
        candidate=b.read_candidate(self.directory)
        self.assertTrue(candidate['body_bytes_verified'])
        self.assertFalse(candidate['baseline_ready'])
        self.assertEqual(candidate['candidate']['documents'][0]['content'].encode(),self.body)
        self.assertEqual(candidate['candidate']['documents'][1]['content'],'')
        self.assertEqual(self.server.nodes[PROTECTED],self.before_nodes[PROTECTED])
        self.assertEqual(self.server.orders[b.PARENT_ORDER]['children'],[PROTECTED,c.ROOT_CANDIDATE])
        reqs=c.parse((self.directory/'creation-requests.json').read_bytes())
        self.assertEqual(reqs[3]['ordered_intents'][0]['base_revision'],7)
        self.assertEqual(self.plan['initial_revisions'],None)
        for p in self.directory.iterdir():
            if p.is_file():
                self.assertNotIn(b'COOKIE_SECRET',p.read_bytes())
                self.assertNotIn(jwt(exp=1400).encode(),p.read_bytes())

    async def test_finished_run_never_replayed(self):
        await self.run_bootstrap()
        with self.assertRaisesRegex(c.ReadStopped,'RUN_ALREADY_USED'):await self.run_bootstrap()
        self.assertEqual(len(self.calls),16)

    async def test_previous_real_run_ids_refused_without_io(self):
        for ident in b.ENDED:
            self.scope['run_id']=ident
            with self.assertRaisesRegex(c.ReadStopped,'ENDED_RUN_REFUSED'):await self.run_bootstrap()
        self.assertEqual(self.calls,[])

    async def test_plan_tampering_is_rejected(self):
        self.plan['body_id']=str(uuid4())
        with self.assertRaisesRegex(c.ReadStopped,'PLAN_CHANGED'):await self.run_bootstrap()
        self.assertEqual(self.calls,[])

    async def test_scope_limits_cannot_expand(self):
        for field,value in [('max_requests',17),('max_writes',5),('max_seconds',181)]:
            old=self.scope[field];self.scope[field]=value
            with self.assertRaises(c.ReadStopped):await self.run_bootstrap()
            self.scope[field]=old
        self.assertEqual(self.calls,[])

    async def test_expired_scope_no_requests(self):
        self.now=1180
        result=await self.run_bootstrap();self.assert_blocked(result)
        self.assertEqual(self.calls,[])

    async def test_wrong_account_blocks_before_tables_and_writes(self):
        self.hook=lambda r:httpx.Response(200,json={'id':str(uuid4())})
        result=await self.run_bootstrap();self.assert_blocked(result)
        self.assertEqual((result['http_reserved'],result['writes_reserved']),(1,0))

    async def test_contract_difference_blocks_writes(self):
        def modify(name,response):
            if name=='get_sync_handshake':
                value=response.json();value['migration_epoch']=2
                return httpx.Response(200,json=value)
        self.post_hook=modify
        result=await self.run_bootstrap();self.assert_blocked(result)
        self.assertEqual(result['writes_reserved'],0)

    async def test_candidate_collision_in_fresh_preflight_blocks_creation(self):
        self.server.nodes[c.ROOT_CANDIDATE]=dict(id=c.ROOT_CANDIDATE,kind='folder',parent=c.PARENT,name=c.ROOT_NAME,deleted=False,revision=1,srev=1)
        result=await self.run_bootstrap();self.assert_blocked(result)
        self.assertEqual(result['writes_reserved'],0)

    async def test_changed_protected_body_blocks_creation(self):
        self.server.nodes[PROTECTED]['content']='다른 합성 본문'
        result=await self.run_bootstrap();self.assert_blocked(result)
        self.assertEqual(result['writes_reserved'],0)

    async def test_changed_shared_parent_revision_blocks_creation(self):
        self.server.orders[b.PARENT_ORDER]['revision']+=1
        result=await self.run_bootstrap();self.assert_blocked(result)
        self.assertEqual(result['writes_reserved'],0)

    async def test_truncated_count_blocks_writes(self):
        self.server.count_delta=1
        result=await self.run_bootstrap();self.assert_blocked(result)
        self.assertEqual(result['writes_reserved'],0)

    async def test_authority_ends_before_first_commit(self):
        def end(name,response):
            if name=='tree_orders':self.active=False
        self.post_hook=end
        result=await self.run_bootstrap();self.assert_blocked(result)
        self.assertEqual(result['writes_reserved'],0)

    async def test_lost_first_write_response_no_repost_or_cleanup(self):
        def lose(request):
            if request.url.path.endswith('atomic_structure_commit'):self.server.lose_next=True
        self.hook=lose
        result=await self.run_bootstrap();self.assert_blocked(result)
        self.assertTrue(result['write_outcome_uncertain'])
        self.assertEqual(result['writes_reserved'],1)
        self.assertIn(c.ROOT_CANDIDATE,self.server.nodes)
        with self.assertRaises(c.ReadStopped):await self.run_bootstrap()
        self.assertEqual(len(self.calls),8)

    async def test_second_write_rejected_keeps_created_root_and_stops(self):
        def reject(request):
            if request.url.path.endswith('document_commit'):self.server.reject_next=True
        self.hook=reject
        result=await self.run_bootstrap();self.assert_blocked(result)
        self.assertEqual(result['writes_acknowledged'],1)
        self.assertIn(c.ROOT_CANDIDATE,self.server.nodes)
        self.assertNotIn(b.BODY_ID,self.server.nodes)

    async def test_shared_order_compare_and_swap_race_no_overwrite(self):
        def race(request):
            if len(self.calls)==11:self.server.orders[b.PARENT_ORDER]['revision']+=1
        self.hook=race
        result=await self.run_bootstrap();self.assert_blocked(result)
        self.assertEqual(result['writes_acknowledged'],3)
        self.assertEqual(self.server.orders[b.PARENT_ORDER]['children'],[PROTECTED])

    async def test_mismatched_receipt_stops_next_write(self):
        def bad(name,response):
            if name=='atomic_structure_commit':
                value=response.json();value['batch_id']=str(uuid4())
                return httpx.Response(200,json=value)
        self.post_hook=bad
        result=await self.run_bootstrap();self.assert_blocked(result)
        self.assertEqual(result['writes_reserved'],1)

    async def test_protected_body_changed_after_creation_blocks_baseline(self):
        def mutate(request):
            if len(self.calls)==12:self.server.nodes[PROTECTED]['content']='외부 변경 합성'
        self.hook=mutate
        result=await self.run_bootstrap();self.assert_blocked(result)
        self.assertEqual(result['writes_acknowledged'],4)

    async def test_new_body_mismatch_blocks_baseline(self):
        def mutate(request):
            if len(self.calls)==12:self.server.nodes[b.BODY_ID]['content']='다름'
        self.hook=mutate
        result=await self.run_bootstrap();self.assert_blocked(result)
        self.assertEqual(result['reason'],'NEW_BODY_CHANGED')

    async def test_new_order_mismatch_blocks_baseline(self):
        def mutate(request):
            if len(self.calls)==12:self.server.orders[b.ORDER_ID]['children'].reverse()
        self.hook=mutate
        result=await self.run_bootstrap();self.assert_blocked(result)
        self.assertEqual(result['reason'],'NEW_ORDER_CHANGED')

    async def test_reservation_disk_error_prevents_write_dispatch(self):
        original=c.new_file
        def fail(path,raw):
            if path.name.endswith('reserved.json') and c.parse(raw).get('writes_reserved')==1:
                original(path,raw)
                raise OSError('synthetic durability failure')
            original(path,raw)
        with patch.object(c,'new_file',fail):result=await self.run_bootstrap()
        self.assert_blocked(result)
        self.assertEqual((result['writes_reserved'],len(self.calls)),(1,7))

    async def test_cancel_during_write_preserves_uncertainty_and_claim(self):
        def cancel(request):
            if len(self.calls)==8:raise asyncio.CancelledError()
        self.hook=cancel
        with self.assertRaises(asyncio.CancelledError):await self.run_bootstrap()
        terminal=c.parse(next(self.directory.glob('*-terminal.json')).read_bytes())
        self.assertEqual(terminal['reason'],'CANCELLED')
        self.assertTrue(terminal['write_outcome_uncertain'])

    async def test_deadline_during_write_does_not_build_baseline(self):
        def delayed(request):
            if len(self.calls)==8:self.now=1180
        self.hook=delayed
        result=await self.run_bootstrap();self.assert_blocked(result)
        self.assertEqual(result['reason'],'DEADLINE_REACHED')

    async def test_readback_evidence_tampering_blocks_candidate_reader(self):
        await self.run_bootstrap()
        (self.directory/'Q14.body').write_bytes(b'[]')
        with self.assertRaisesRegex(c.ReadStopped,'CANDIDATE_FILES_CHANGED'):b.read_candidate(self.directory)

    async def test_missing_terminal_blocks_candidate_reader(self):
        await self.run_bootstrap()
        next(self.directory.glob('*-terminal.json')).unlink()
        with self.assertRaisesRegex(c.ReadStopped,'CANDIDATE_NOT_FINISHED'):b.read_candidate(self.directory)

    async def test_candidate_reader_rejects_reparse_terminal_before_reading(self):
        await self.run_bootstrap()
        terminal=next(self.directory.glob('*-terminal.json'))
        original=Path.lstat
        def reparse(path,*args,**kwargs):
            info=original(path,*args,**kwargs)
            if path==terminal:
                return SimpleNamespace(st_mode=info.st_mode,st_file_attributes=0x400)
            return info
        with patch.object(Path,'lstat',reparse):
            with self.assertRaisesRegex(c.ReadStopped,'LINK_REFUSED'):b.read_candidate(self.directory)
