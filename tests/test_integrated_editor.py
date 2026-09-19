"""Integrated Windows scenarios: real private SQLite/TXT/Qt, mock HTTP only."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from integrated_editor_plan import BUILD_ID, PROJECT_ID, PROJECT_NAME, STAGING_URL, PROTECTED_DOCUMENT_ID
from integrated_editor_store import IntegratedStore, EditorError, order_id
from integrated_editor_sync import IntegratedNetwork, AutoSync, validate_response
from sync_contract import (json_sha256, CANONICAL_CONTRACT_SHA256, CONTRACT_VERSION, SERVER_CAPABILITIES)

ACCOUNT = '10000000-0000-4000-8000-000000000001'
DEVICE = '10000000-0000-4000-8000-000000000002'
ROOT_ID = '10000000-0000-4000-8000-000000000003'
PROTECTED_PARENT = '10000000-0000-4000-8000-000000000004'


def response_for(req):
    results = []
    for intent in req['ordered_intents']:
        result = dict(sequence=intent['sequence'], operation_id=intent['operation_id'],
                      result_revision=intent['base_revision'] + 1)
        if req['kind'] == 'document_commit_request':
            p = intent['payload']
            result.update(document_id=intent['document_id'], **{k: p[k] for k in (
                'structure_revision', 'parent_folder_id', 'name', 'content_sha256', 'content_byte_count', 'is_deleted')})
        else:
            result['entity_id'] = intent['entity_id']
        results.append(result)
    return dict(kind=req['kind'].replace('_request', '_success'), batch_id=req['batch']['batch_id'],
        batch_payload_sha256=req['batch']['batch_payload_sha256'], status='committed', applied=True, results=results)


class Server:
    def __init__(self, nodes, orders):
        self.nodes = {n['id']: deepcopy(n) for n in nodes}
        self.orders = {o['id']: deepcopy(o) for o in orders}
        self.receipts, self.calls = {}, []
        self.lose_next = False
        self.reject_next = False
        self.count_delta = 0
        self.receipt_mutator = lambda table, rows: rows
        self.transport_error = False
        self.intercept = None

    def __call__(self, request):
        name = request.url.path.rsplit('/', 1)[-1]
        self.calls.append((request.method, name))
        if self.intercept:
            self.intercept(request)
        if self.transport_error:
            raise httpx.ConnectError('synthetic offline')
        if name == 'user':
            return httpx.Response(200, json={'id': ACCOUNT})
        if name == 'get_sync_handshake':
            return httpx.Response(200, json=dict(project_id=PROJECT_ID, project_sync_mode='ID_BASED',
                migration_epoch=1, server_protocol_version=3, supported_protocol_versions=[3],
                server_contract_sha256=CANONICAL_CONTRACT_SHA256, canonical_contract_sha256=CANONICAL_CONTRACT_SHA256,
                server_capabilities=list(SERVER_CAPABILITIES), contract_version=CONTRACT_VERSION))
        if name in ('document_commit', 'atomic_structure_commit'):
            req = json.loads(request.content)['p_request']
            result = response_for(req)
            nodes, orders = deepcopy(self.nodes), deepcopy(self.orders)
            if self.reject_next:
                self.reject_next = False
                result.update(kind=req['kind'].replace('_request', '_failure'), status='rejected', applied=False,
                              results=[], error=dict(code='REVISION_CONFLICT', message='synthetic conflict', failed_sequence=1))
            else:
                for intent in req['ordered_intents']:
                    kind, action, p = intent['entity_kind'], intent['intent_kind'], intent['payload']
                    key = intent.get('document_id', intent.get('entity_id'))
                    old = (orders if kind == 'tree_order' else nodes).get(key)
                    rev = (old['srev'] if kind == 'document' and name == 'atomic_structure_commit' else old['revision']) if old else 0
                    if rev != intent['base_revision']:
                        raise AssertionError('client did not preserve predecessor revision')
                    if kind == 'tree_order':
                        if set(p['children']) != {k for k, n in nodes.items() if n['parent'] == p['parent_folder_id'] and not n['deleted']}:
                            raise AssertionError('incomplete order write')
                        orders[key] = dict(id=key, parent=p['parent_folder_id'], children=p['children'], revision=rev + 1)
                    elif name == 'document_commit':
                        nodes[key] = dict(id=key, kind='document', parent=p['parent_folder_id'], name=p['name'],
                            content=p['content'], deleted=p['is_deleted'], revision=rev + 1, srev=p['structure_revision'])
                    else:
                        if action == 'create':
                            nodes[key] = dict(id=key, kind=kind, parent=p['parent_folder_id'], name=p['name'],
                                              deleted=False, revision=1, srev=1)
                        else:
                            if action == 'rename':
                                nodes[key]['name'] = p['name']
                            elif action == 'move':
                                nodes[key]['parent'] = p['parent_folder_id']
                            elif action in ('delete', 'restore'):
                                nodes[key]['deleted'] = action == 'delete'
                                if action == 'restore':
                                    nodes[key].update(parent=p['parent_folder_id'], name=p['name'])
                            nodes[key]['srev' if kind == 'document' else 'revision'] = rev + 1
                self.nodes, self.orders = nodes, orders
            batch = req['batch']
            self.receipts[batch['batch_id']] = dict(sync_batches=dict(batch, project_id=PROJECT_ID,
                writer_user_id=ACCOUNT, project_sync_mode='ID_BASED', migration_epoch=1, request_sha256=json_sha256(req)),
                sync_batch_results=dict(batch_id=batch['batch_id'], applied=result['applied'], response=result,
                                        response_sha256=json_sha256(result)))
            # The real RPC stores a sorted capability array, not wire order.
            self.receipts[batch['batch_id']]['sync_batches']['client_capabilities'] = sorted(batch['client_capabilities'])
            if self.lose_next:
                self.lose_next = False
                raise httpx.ReadError('synthetic response loss')
            return httpx.Response(200, json=result)
        if name in ('sync_batches', 'sync_batch_results'):
            batch = request.url.params['batch_id'].removeprefix('eq.')
            rows = [deepcopy(self.receipts[batch][name])] if batch in self.receipts else []
            rows = self.receipt_mutator(name, rows)
        elif name == 'projects':
            rows = [dict(project_id=PROJECT_ID, name=PROJECT_NAME, owner_id=ACCOUNT, deleted_at=None)]
        elif name == 'project_sync_settings':
            rows = [dict(project_id=PROJECT_ID, project_sync_mode='ID_BASED', migration_epoch=1)]
        elif name in ('documents', 'folders'):
            kind = 'document' if name == 'documents' else 'folder'
            rows = []
            for n in self.nodes.values():
                if n['kind'] == kind:
                    r = dict(project_id=PROJECT_ID, parent_folder_id=n['parent'], name=n['name'],
                             is_deleted=n['deleted'], revision=n['revision'])
                    r[kind + '_id'] = n['id']
                    if kind == 'document':
                        r.update(content=n['content'], structure_revision=n['srev'], relative_path=n['name'])
                    rows.append(r)
        elif name == 'tree_orders':
            rows = [dict(project_id=PROJECT_ID, tree_order_id=o['id'], parent_folder_id=o['parent'],
                         children=o['children'], revision=o['revision']) for o in self.orders.values()]
        else:
            raise AssertionError('unexpected endpoint: ' + name)
        count = len(rows) + self.count_delta
        return httpx.Response(200, json=rows, headers={'content-range': f'0-{max(0,len(rows)-1)}/{count}'})


class IntegratedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1000
        self.directory = Path(self.tmp.name) / 'integrated'
        self.store = self.reopen()
        self.nodes = [dict(id=ROOT_ID, kind='folder', parent=None, name='원고', deleted=False, revision=1, srev=1),
            dict(id=PROTECTED_PARENT, kind='folder', parent=ROOT_ID, name='완료 문서', deleted=False, revision=1, srev=1),
            dict(id=PROTECTED_DOCUMENT_ID, kind='document', parent=PROTECTED_PARENT, name='완료.txt',
                 deleted=False, revision=9, srev=1, content='보존할 완료 본문\n')]
        self.orders = [dict(id=order_id(None), parent=None, children=[ROOT_ID], revision=1),
            dict(id=order_id(ROOT_ID), parent=ROOT_ID, children=[PROTECTED_PARENT], revision=1),
            dict(id=order_id(PROTECTED_PARENT), parent=PROTECTED_PARENT, children=[PROTECTED_DOCUMENT_ID], revision=2)]
        self.store.seed(self.nodes, self.orders)
        self.server = Server(self.nodes, self.orders)
        self.authorized = True

    def reopen(self):
        return IntegratedStore(self.directory, device_id=DEVICE, local_project_id='windows-local-project', clock=lambda: self.now)

    def create(self, kind='document', parent=ROOT_ID, name='새 글.txt', content='첫 글\n'):
        return self.store.create(kind, parent, name, content=content)

    def approval(self, **overrides):
        s = self.store.state()
        value = dict(run_id=str(uuid4()), project_id=PROJECT_ID, account_id=ACCOUNT, device_id=DEVICE,
            build_id=BUILD_ID, url=STAGING_URL, writable_ids=[k for k, r in s['nodes'].items() if not r['protected']],
            order_ids=list(s['orders']), max_requests=1000, max_seconds=600, expires_at=5000, automatic=True,
            automatic_policy=dict(version=1, max_empty_cycles=100))
        value.update(overrides)
        return value

    def network(self, approval=None):
        network = IntegratedNetwork(self.store, approval or self.approval(), access_token='synthetic-token',
            api_key='synthetic-key', check_current=lambda: self.authorized, transport=httpx.MockTransport(self.server))
        self.addCleanup(network.close)
        return network

    def drain(self, network):
        for _ in range(100):
            if not any(j['status'] != 'completed' for j in self.store.state()['jobs']):
                return
            result = network.cycle()
            self.assertNotIn(result, ('blocked', 'conflict'))
        self.fail('queue did not drain')

    def assert_protected(self):
        n = self.store.state()['nodes'][PROTECTED_DOCUMENT_ID]
        self.assertEqual(n['local'], self.nodes[-1])
        self.assertEqual(self.server.nodes[PROTECTED_DOCUMENT_ID], self.nodes[-1])
        self.assertEqual(self.server.orders[order_id(PROTECTED_PARENT)]['revision'], 2)

    def test_local_edit_switch_restart_unicode_empty_and_queue_isolation(self):
        a = self.create()
        b = self.create(name='둘.txt')
        self.store.select(a)
        self.store.preserve_draft(a, '가 e\u0301 é 😀\n마지막 LF 없음')
        self.store.save(a)
        self.store.select(b)
        self.store.preserve_draft(b, '')
        self.store.save(b)
        other = self.reopen()
        self.assertEqual(other.select(a), '가 e\u0301 é 😀\n마지막 LF 없음')
        self.assertEqual(other.select(b), '')
        self.assertEqual((other.files / (b + '.txt')).read_bytes(), b'')
        self.assertEqual(self.server.calls, [])
        self.assert_protected()

    def test_multiple_bodies_structures_delete_restore_and_order_in_one_run(self):
        folder = self.create('folder', name='시험 폴더')
        a = self.create(parent=folder)
        b = self.create(parent=folder, name='둘.txt')
        self.store.preserve_draft(a, '다음 본문')
        self.store.save(a)
        self.store.relocate(a, parent=ROOT_ID, name='이동한 글.txt')
        self.store.reorder(ROOT_ID, [a, folder, PROTECTED_PARENT])
        self.store.lifecycle(folder, True)
        self.store.lifecycle(folder, False)
        network = self.network()
        self.drain(network)
        self.assertEqual(self.server.nodes[a]['parent'], ROOT_ID)
        self.assertEqual(self.server.nodes[a]['content'], '다음 본문')
        self.assertFalse(self.server.nodes[b]['deleted'])
        self.assertEqual(network.cycle(), 'received')
        self.assert_protected()

    def test_immutable_first_request_keeps_followup_edit(self):
        a = self.create()
        first = self.store.prepare_next()
        self.store.preserve_draft(a, '후속 본문')
        self.store.save(a)
        self.assertEqual(self.store.prepare_next()['request'], first['request'])
        net = self.network()
        self.drain(net)
        self.assertEqual(self.server.nodes[a]['content'], '후속 본문')
        self.assertEqual(self.store.state()['nodes'][a]['draft'], '후속 본문')

    def test_body_response_loss_restart_receipt_only(self):
        self.create()
        net = self.network()
        self.server.lose_next = True
        with self.assertRaises(httpx.ReadError):
            net.cycle()
        approval = net.approval
        original = deepcopy(self.store.state()['batches'][0])
        self.store = self.reopen()
        recovery = self.network(approval)
        self.assertEqual(recovery.cycle(), 'recovered')
        recovered = self.store.state()['batches'][0]
        self.assertEqual(recovered['request'], original['request'])
        self.assertEqual(recovered['sha256'], original['sha256'])
        self.assertEqual(self.server.calls.count(('POST', 'document_commit')), 1)
        self.assertEqual(self.server.calls.count(('GET', 'sync_batches')), 1)
        self.assertEqual(self.server.calls.count(('GET', 'sync_batch_results')), 1)

    def test_structure_response_loss_receipt_only(self):
        self.create('folder', name='폴더')
        net = self.network()
        self.server.lose_next = True
        with self.assertRaises(httpx.ReadError):
            net.cycle()
        self.assertEqual(net.cycle(), 'recovered')
        self.assertEqual(self.server.calls.count(('POST', 'atomic_structure_commit')), 1)

    def test_missing_or_wrong_receipt_never_resends(self):
        self.create()
        net = self.network()
        self.server.lose_next = True
        with self.assertRaises(httpx.ReadError):
            net.cycle()
        for change in (
            lambda t, rows: [],
            lambda t, rows: [dict(r, writer_user_id=DEVICE) for r in rows] if t == 'sync_batches' else rows,
            lambda t, rows: [dict(r, response_sha256='0'*64) for r in rows] if t == 'sync_batch_results' else rows):
            self.server.receipt_mutator = change
            with self.assertRaises(EditorError):
                net.cycle()
        self.assertEqual(self.server.calls.count(('POST', 'document_commit')), 1)

    def test_response_local_apply_failure_uses_durable_response(self):
        self.create()
        net = self.network()
        with patch.object(self.store, 'complete', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                net.cycle()
        self.assertEqual(net.cycle(), 'recovered')
        self.assertEqual(self.server.calls.count(('POST', 'document_commit')), 1)
        self.assertNotIn(('GET', 'sync_batches'), self.server.calls)

    def test_receipt_capability_members_types_and_metadata_remain_strict(self):
        self.create()
        net = self.network()
        self.server.lose_next = True
        with self.assertRaises(httpx.ReadError):
            net.cycle()
        original = deepcopy(self.store.state()['batches'][0])
        caps = original['request']['batch']['client_capabilities']
        invalid = [caps[:-1], caps + ['unknown_capability'], caps + [caps[0]],
                   'not-an-array', None, caps[:-1] + [1], caps[:-1] + [True],
                   caps[:-1] + [{}], caps[:-1] + ['']]
        mutations = [('client_capabilities', value) for value in invalid]
        mutations += [('request_sha256', '0'*64), ('batch_payload_sha256', '0'*64),
                      ('writer_user_id', DEVICE), ('client_build_id', 'different-build'),
                      ('extra_metadata', 'unexpected')]
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                self.server.receipt_mutator = lambda t, rows: [dict(r, **{field:value}) for r in rows] if t == 'sync_batches' else rows
                with self.assertRaisesRegex(EditorError, 'RECEIPT_REQUEST_CHANGED'):
                    net.cycle()
                current = self.store.state()['batches'][0]
                self.assertEqual(current, original)
        self.assertEqual(self.server.calls.count(('POST', 'document_commit')), 1)
        self.assertNotIn(('GET', 'sync_batch_results'), self.server.calls)
        # A valid reordered receipt still recovers the same uncertain batch.
        self.server.receipt_mutator = lambda t, rows: [dict(r, client_capabilities=list(reversed(caps))) for r in rows] if t == 'sync_batches' else rows
        self.assertEqual(net.cycle(), 'recovered')
        self.assertEqual(self.store.state()['batches'][0]['request'], original['request'])
        self.assertEqual(self.server.calls.count(('POST', 'document_commit')), 1)

    def test_rejected_write_blocks_dependencies_and_retains_bodies(self):
        a = self.create()
        self.server.reject_next = True
        net = self.network()
        self.assertEqual(net.cycle(), 'blocked')
        with self.assertRaisesRegex(EditorError, 'CONFLICT'):
            net.cycle()
        self.assertEqual(self.store.state()['nodes'][a]['draft'], '첫 글\n')
        self.assertEqual(self.server.calls.count(('POST', 'document_commit')), 1)

    def test_receive_conflict_preserves_base_local_remote_and_draft(self):
        a = self.create()
        net = self.network()
        self.drain(net)
        self.store.preserve_draft(a, '미저장 초안')
        self.server.nodes[a].update(content='원격 편집', revision=2)
        self.assertEqual(net.cycle(receive_only=True), 'conflict')
        state = self.store.state()
        self.assertEqual(state['nodes'][a]['draft'], '미저장 초안')
        self.assertEqual(state['conflicts'][-1]['details'][0]['remote']['content'], '원격 편집')
        self.assertEqual(state['nodes'][a]['base']['content'], '첫 글\n')

    def test_clean_receive_and_partial_file_projection_resume(self):
        a = self.create()
        net = self.network()
        self.drain(net)
        self.server.nodes[a].update(content='iPad 수신\n', revision=2)
        with patch('integrated_editor_store.os.replace', side_effect=OSError('projection interrupted')):
            with self.assertRaises(OSError):
                net.cycle(receive_only=True)
        self.assertEqual(self.store.state()['nodes'][a]['base']['revision'], 2)
        other = self.reopen()
        other.materialize()
        self.assertEqual((other.files / (a + '.txt')).read_text('utf-8'), 'iPad 수신\n')
        self.assertEqual(other.select(a), 'iPad 수신\n')

    def test_external_file_change_not_overwritten(self):
        a = self.create()
        target = self.store.files / (a + '.txt')
        target.write_bytes(b'external bytes')
        with self.assertRaisesRegex(EditorError, 'EXTERNAL_FILE'):
            self.store.materialize()
        self.assertEqual(target.read_bytes(), b'external bytes')

    def test_request_budget_includes_auth_and_reads_survives_restart(self):
        self.create()
        approval = self.approval(max_requests=2)
        net = self.network(approval)
        with self.assertRaisesRegex(EditorError, 'LIMIT'):
            net.cycle()
        self.store = self.reopen()
        with self.assertRaisesRegex(EditorError, 'LIMIT'):
            self.network(approval).cycle()
        self.assertEqual(len(self.server.calls), 2)
        self.assertEqual(self.store.state()['runs'][approval['run_id']]['used'], 2)

    def test_last_permitted_reply_applies_then_stops(self):
        a = self.create()
        net = self.network(self.approval(max_requests=8))
        self.assertEqual(net.cycle(), 'sent')
        self.assertEqual(self.store.state()['nodes'][a]['base']['revision'], 1)
        with self.assertRaisesRegex(EditorError, 'LIMIT'):
            net.cycle()
        self.assertEqual(len(self.server.calls), 8)

    def test_expiry_and_clock_rollback_do_not_reset_on_restart(self):
        for offset in (20, -1):
            approval = self.approval(max_seconds=10)
            self.store.open_run(approval)
            before = self.now
            self.now += offset
            with self.assertRaisesRegex(EditorError, 'LIMIT'):
                self.network(approval).cycle()
            self.now = before
        self.assertEqual(self.server.calls, [])

    def test_same_execution_id_cannot_extend_budget(self):
        approval = self.approval()
        self.store.open_run(approval)
        with self.assertRaisesRegex(EditorError, 'SCOPE_CHANGED'):
            self.store.open_run(dict(approval, max_requests=2000))

    def test_manual_and_auto_share_dispatch_lock(self):
        self.create()
        net = self.network()
        with self.store.dispatch_lock:
            self.assertEqual(net.cycle(), 'busy')
            self.assertEqual(net.cycle(automatic=True), 'busy')
        self.assertEqual(self.server.calls, [])

    def test_empty_auto_budget_last_reply_restart_toggle_and_manual(self):
        approval = self.approval(automatic_policy=dict(version=1, max_empty_cycles=2))
        net = self.network(approval)
        auto = AutoSync(net, clock=lambda: self.now, interval=1)
        auto.enabled = True
        self.assertEqual(auto.tick(), 'received')
        self.assertTrue(auto.enabled)
        self.assertEqual(self.store.state()['runs'][net.run_id]['empty_cycles_used'], 1)
        self.now += 1
        self.assertEqual(auto.tick(), 'received')
        self.assertFalse(auto.enabled)
        run = self.store.state()['runs'][net.run_id]
        self.assertEqual(run['empty_cycles_used'], 2)
        self.assertFalse(run['stopped'])
        count = len(self.server.calls)
        self.store = self.reopen()
        net = self.network(approval)
        auto = AutoSync(net, clock=lambda: self.now, interval=1)
        auto.enabled = True
        with self.assertRaisesRegex(EditorError, 'EMPTY_AUTO_LIMIT_REACHED'):
            auto.tick()
        self.assertFalse(auto.enabled)
        self.assertEqual(len(self.server.calls), count)
        self.assertEqual(self.store.state()['runs'][net.run_id]['used'], run['used'])
        self.assertEqual(net.cycle(receive_only=True), 'received')
        self.assertEqual(self.store.state()['runs'][net.run_id]['empty_cycles_used'], 2)

    def test_empty_auto_reservation_survives_failure_and_interruption(self):
        for error in (httpx.ConnectError('offline'), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                approval = self.approval(automatic_policy=dict(version=1, max_empty_cycles=1))
                net = self.network(approval)
                before = len(self.server.calls)
                with patch.object(net.http, 'send', side_effect=error):
                    with self.assertRaises(type(error)):
                        net.cycle(automatic=True)
                self.store = self.reopen()
                run = self.store.state()['runs'][net.run_id]
                self.assertEqual((run['empty_cycles_used'], run['used']), (1, 1))
                net = self.network(approval)
                with self.assertRaisesRegex(EditorError, 'EMPTY_AUTO_LIMIT_REACHED'):
                    net.cycle(automatic=True)
                self.assertEqual(len(self.server.calls), before)

    def test_empty_auto_idle_busy_and_nonempty_cycles_are_not_charged(self):
        self.create()
        net = self.network(self.approval(automatic_policy=dict(version=1, max_empty_cycles=1)))
        auto = AutoSync(net, clock=lambda: self.now)
        self.assertEqual(auto.tick(), 'idle')
        auto.enabled, auto.online = True, False
        self.assertEqual(auto.tick(), 'idle')
        auto.online, auto.foreground = True, False
        self.assertEqual(auto.tick(), 'idle')
        with self.store.dispatch_lock:
            self.assertEqual(net.cycle(automatic=True), 'busy')
        self.assertEqual(net.cycle(automatic=True), 'sent')
        self.assertEqual(self.store.state()['runs'][net.run_id]['empty_cycles_used'], 0)

    def test_empty_auto_changed_snapshot_still_charged_and_last_reply_applied(self):
        key = self.create()
        net = self.network(self.approval(automatic_policy=dict(version=1, max_empty_cycles=1)))
        self.drain(net)
        self.server.nodes[key].update(content='마지막 허용 수신\n', revision=2)
        self.assertEqual(net.cycle(automatic=True), 'received')
        self.assertEqual(self.store.state()['nodes'][key]['draft'], '마지막 허용 수신\n')
        self.assertEqual(self.store.state()['runs'][net.run_id]['empty_cycles_used'], 1)

    def test_legacy_scope_has_no_counter_migration_and_auto_is_refused(self):
        approval = self.approval()
        del approval['automatic_policy']
        net = self.network(approval)
        before = deepcopy(self.store.state()['runs'][net.run_id])
        with self.assertRaisesRegex(EditorError, 'AUTOMATIC_POLICY_REQUIRED'):
            net.cycle(automatic=True)
        self.assertEqual(self.server.calls, [])
        self.assertEqual(self.store.state()['runs'][net.run_id], before)
        with self.assertRaisesRegex(EditorError, 'EXECUTION_SCOPE_CHANGED'):
            self.store.open_run(dict(approval, automatic_policy=dict(version=1, max_empty_cycles=6)))
        self.store.stop_run(net.run_id)
        stopped = deepcopy(self.store.state()['runs'][net.run_id])
        self.store = self.reopen()
        self.store.open_run(approval)
        self.assertEqual(self.store.state()['runs'][net.run_id], stopped)
        with self.assertRaisesRegex(EditorError, 'EXECUTION_LIMIT_REACHED'):
            self.network(approval).cycle()
        self.assertEqual(self.server.calls, [])

    def test_auto_policy_validation_zero_and_missing_counter_fail_closed(self):
        for policy in (None, {}, dict(version=True, max_empty_cycles=1),
                       dict(version=2, max_empty_cycles=1), dict(version=1, max_empty_cycles=-1),
                       dict(version=1, max_empty_cycles=True), dict(version=1, max_empty_cycles=1.0),
                       dict(version=1, max_empty_cycles=1, extra=1)):
            with self.subTest(policy=policy), self.assertRaisesRegex(EditorError, 'INVALID_AUTOMATIC_POLICY'):
                self.network(self.approval(automatic_policy=policy))
        net = self.network(self.approval(automatic_policy=dict(version=1, max_empty_cycles=0)))
        with self.assertRaisesRegex(EditorError, 'EMPTY_AUTO_LIMIT_REACHED'):
            net.cycle(automatic=True)
        other = self.network()
        with self.store.change('synthetic_corrupt_counter') as state:
            del state['runs'][other.run_id]['empty_cycles_used']
        with self.assertRaisesRegex(EditorError, 'AUTOMATIC_COUNTER_INVALID'):
            other.cycle(automatic=True)
        self.assertEqual(self.server.calls, [])

    def test_auto_reconnect_lost_write_recovers_without_resending(self):
        self.create()
        net = self.network()
        auto = AutoSync(net, clock=lambda: self.now, interval=1)
        auto.enabled = True
        auto.foreground = False
        self.assertEqual(auto.tick(), 'idle')
        auto.foreground = True
        self.server.lose_next = True
        self.assertEqual(auto.tick(), 'retry_wait')
        self.now += 5
        self.assertEqual(auto.tick(), 'recovered')
        self.assertEqual(self.server.calls.count(('POST', 'document_commit')), 1)
        self.assertEqual(self.store.state()['runs'][net.run_id]['empty_cycles_used'], 0)

    def test_joint_j05_save_registers_job_atomically_before_txt_projection_and_batch(self):
        key = self.create()
        net = self.network()
        self.drain(net)
        self.store.preserve_draft(key, '저장 원본\n')
        old_file = (self.store.files / (key + '.txt')).read_bytes()
        observed = []
        materialize = self.store.materialize
        def inspect_projection_boundary():
            state = self.store.state()
            pending = [j for j in state['jobs'] if j['status'] != 'completed']
            self.assertEqual(state['nodes'][key]['local']['content'], '저장 원본\n')
            self.assertEqual(len(pending), 1)
            self.assertIsNone(pending[0]['batch_id'])
            self.assertEqual(pending[0]['after']['content'], '저장 원본\n')
            self.assertEqual((self.store.files / (key + '.txt')).read_bytes(), old_file)
            observed.append(pending[0]['id'])
            materialize()
        with patch.object(self.store, 'materialize', side_effect=inspect_projection_boundary):
            self.store.save(key)
        self.assertEqual(len(observed), 1)
        before_calls = len(self.server.calls)
        batch = self.store.prepare_next()
        self.assertEqual(len(self.server.calls), before_calls)
        self.assertEqual(batch['status'], 'prepared')
        self.assertEqual(net.cycle(automatic=True), 'sent')
        self.assertEqual(len(self.server.calls) - before_calls, 8)
        self.assertEqual(self.store.state()['runs'][net.run_id]['empty_cycles_used'], 0)
        self.assertEqual((self.store.files / (key + '.txt')).read_bytes(), '저장 원본\n'.encode('utf-8'))

    def test_joint_j07_direct_backend_is_not_ui_draft_guard(self):
        key = self.create()
        net = self.network()
        self.drain(net)
        self.store.preserve_draft(key, '미저장 직접 진입 초안')
        before_node = deepcopy(self.store.state()['nodes'][key])
        before_file = (self.store.files / (key + '.txt')).read_bytes()
        before_calls = len(self.server.calls)
        self.assertEqual(net.cycle(automatic=True), 'received')
        self.assertEqual(len(self.server.calls) - before_calls, 7)
        self.assertEqual(self.store.state()['runs'][net.run_id]['empty_cycles_used'], 1)
        self.assertEqual(self.store.state()['nodes'][key], before_node)
        self.assertEqual((self.store.files / (key + '.txt')).read_bytes(), before_file)

    def test_joint_j08_exhausted_empty_budget_blocks_new_saved_job(self):
        key = self.create()
        net = self.network(self.approval(automatic_policy=dict(version=1, max_empty_cycles=1)))
        self.drain(net)
        self.assertEqual(net.cycle(automatic=True), 'received')
        self.store.preserve_draft(key, '한도 뒤 저장\n')
        self.store.save(key)
        jobs = deepcopy(self.store.state()['jobs'])
        before_calls = len(self.server.calls)
        with self.assertRaisesRegex(EditorError, 'EMPTY_AUTO_LIMIT_REACHED'):
            net.cycle(automatic=True)
        self.assertEqual(len(self.server.calls), before_calls)
        self.assertEqual(self.store.state()['jobs'], jobs)
        self.assertEqual(self.store.state()['runs'][net.run_id]['empty_cycles_used'], 1)

    def test_joint_j09_abort_before_http_reservation_keeps_empty_charge(self):
        approval = self.approval(automatic_policy=dict(version=1, max_empty_cycles=1))
        net = self.network(approval)
        with patch.object(net, 'authenticate', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                net.cycle(automatic=True)
        self.store = self.reopen()
        run = self.store.state()['runs'][net.run_id]
        self.assertEqual((run['empty_cycles_used'], run['used']), (1, 0))
        with self.assertRaisesRegex(EditorError, 'EMPTY_AUTO_LIMIT_REACHED'):
            self.network(approval).cycle(automatic=True)
        self.assertEqual(self.server.calls, [])

    def test_incomplete_snapshot_count_refuses_all_changes(self):
        self.create()
        net = self.network()
        before = self.store.state()['nodes']
        self.server.count_delta = 1
        with self.assertRaisesRegex(EditorError, 'COUNT'):
            net.cycle()
        self.assertEqual(self.store.state()['nodes'], before)
        self.assertNotIn(('POST', 'document_commit'), self.server.calls)

    def test_protected_scope_and_cycle_collision_preserve_state(self):
        before = self.store.state()['nodes']
        with self.assertRaisesRegex(EditorError, 'PROTECTED'):
            self.store.preserve_draft(PROTECTED_DOCUMENT_ID, 'bad')
        with self.assertRaisesRegex(EditorError, 'ORDER_PROTECTED'):
            self.create(parent=PROTECTED_PARENT)
        folder = self.create('folder', name='safe')
        with self.assertRaisesRegex(EditorError, 'CYCLE'):
            self.store.relocate(folder, parent=folder, name='safe')
        with self.assertRaisesRegex(EditorError, 'COLLISION'):
            self.create('folder', name='SAFE')
        self.assertEqual(self.store.state()['nodes'][PROTECTED_DOCUMENT_ID], before[PROTECTED_DOCUMENT_ID])

    def test_out_of_scope_request_has_zero_http(self):
        self.create()
        net = self.network(self.approval(writable_ids=[]))
        with self.assertRaisesRegex(EditorError, 'OUTSIDE_SCOPE'):
            net.cycle()
        self.assertEqual(self.server.calls, [])

    def test_direct_transport_method_cannot_bypass_serial_scope(self):
        net = self.network()
        with self.assertRaisesRegex(EditorError, 'SERIAL'):
            net.request('POST', '/rest/v1/rpc/document_commit', payload={'p_request': {}})
        self.assertEqual(self.server.calls, [])

    def test_restore_does_not_revive_older_independent_deleted_child(self):
        folder = self.create('folder', name='folder')
        a = self.create(parent=folder)
        b = self.create(parent=folder, name='older.txt')
        self.store.lifecycle(b, True)
        self.store.lifecycle(folder, True)
        self.store.lifecycle(folder, False)
        state = self.store.state()
        self.assertTrue(state['nodes'][b]['local']['deleted'])
        self.assertFalse(state['nodes'][a]['local']['deleted'])

    def test_runtime_is_disabled_and_store_audit_detects_tampering(self):
        from integrated_editor_runtime import runtime_paths
        with self.assertRaisesRegex(EditorError, 'CANDIDATE'):
            runtime_paths()
        with self.store.connection() as conn:
            conn.execute("UPDATE events SET event='tampered' WHERE sequence=1")
        with self.assertRaisesRegex(EditorError, 'AUDIT'):
            self.reopen()

    def test_protected_db_wal_presence_or_bytes_change_ends_authority(self):
        from integrated_editor_runtime import protected_files_match, copy_source_database
        db = Path(self.tmp.name) / 'original.bin'
        wal = Path(self.tmp.name) / 'original.bin-wal'
        db.write_bytes(b'original')
        pins = {str(db): hashlib.sha256(b'original').hexdigest(), str(wal): None}
        self.assertTrue(protected_files_match(pins))
        wal.write_bytes(b'new gate state in WAL')
        self.assertFalse(protected_files_match(pins))
        pins[str(wal)] = hashlib.sha256(wal.read_bytes()).hexdigest()
        self.assertTrue(protected_files_match(pins))
        copied = copy_source_database(db, self.directory)
        self.assertEqual(copied.read_bytes(), b'original')
        self.assertEqual(Path(str(copied) + '-wal').read_bytes(), wal.read_bytes())
        copied.write_bytes(b'copy only')
        self.assertTrue(protected_files_match(pins))
        db.write_bytes(b'changed')
        self.assertFalse(protected_files_match(pins))

    def test_new_local_order_cannot_overwrite_concurrent_remote_order(self):
        folder = self.create('folder', name='folder')
        self.create(parent=folder)
        # A peer has established the same order while our first reorder is still queued.
        incoming = dict(id=order_id(folder), parent=folder, children=[], revision=1)
        self.assertFalse(self.store.accept_snapshot(self.nodes, self.orders + [incoming], []))
        self.assertEqual(self.store.state()['conflicts'][-1]['details'][0]['node_id'], order_id(folder))

    def test_rejected_write_can_receive_remote_conflict_without_resending(self):
        a = self.create()
        net = self.network()
        self.drain(net)
        self.store.preserve_draft(a, '내 수정')
        self.store.save(a)
        self.server.reject_next = True
        self.assertEqual(net.cycle(), 'blocked')
        self.server.nodes[a].update(content='동시 원격 수정', revision=2)
        writes = self.server.calls.count(('POST', 'document_commit'))
        self.assertEqual(net.cycle(receive_only=True), 'conflict')
        self.assertEqual(self.store.state()['conflicts'][-1]['details'][0]['remote']['content'], '동시 원격 수정')
        self.assertEqual(self.server.calls.count(('POST', 'document_commit')), writes)

    def test_auto_waits_for_peer_structure_completion_within_same_budget(self):
        net = self.network()
        auto = AutoSync(net, clock=lambda: self.now, interval=1)
        auto.enabled = True
        peer = str(uuid4())
        self.server.nodes[peer] = dict(id=peer, kind='document', parent=ROOT_ID, name='peer.txt',
                                      deleted=False, revision=1, srev=1, content='peer')
        self.assertEqual(auto.tick(), 'retry_wait')
        self.assertTrue(auto.enabled)
        self.assertNotIn(peer, self.store.state()['nodes'])
        self.server.orders[order_id(ROOT_ID)]['children'].append(peer)
        self.server.orders[order_id(ROOT_ID)]['revision'] += 1
        self.now += 2
        self.assertEqual(auto.tick(), 'received')
        self.assertEqual(self.store.state()['nodes'][peer]['draft'], 'peer')

    def test_cancel_after_wire_leaves_uncertain_request(self):
        self.create()
        net = self.network()
        self.server.intercept = lambda r: setattr(self, 'authorized', False) if r.url.path.endswith('/document_commit') else None
        with self.assertRaisesRegex(EditorError, 'AUTHORITY'):
            net.cycle()
        self.assertEqual(self.store.prepare_next()['status'], 'uncertain')
        self.authorized = True
        self.server.intercept = None
        self.assertEqual(net.cycle(), 'recovered')


class IntegratedWidgetTests(unittest.TestCase):
    def setUp(self):
        from tests.qt_app import APP
        self.app = APP
        self.seed = IntegratedTests()
        self.seed.setUp()
        self.addCleanup(self.seed.doCleanups)
        from integrated_editor_ui import IntegratedWritingWidget
        self.a = self.seed.create()
        self.b = self.seed.create(name='둘.txt')
        self.widget = IntegratedWritingWidget(self.seed.store)
        self.addCleanup(self.widget.close)

    def joint_scheduler(self):
        import time
        w = self.widget
        net = self.seed.network(self.seed.approval(automatic_policy=dict(version=1, max_empty_cycles=1)))
        self.seed.drain(net)
        w.rebuild_tree(self.a)
        w.network = net
        w.scheduler = AutoSync(net, clock=time.monotonic, interval=10)
        with self.seed.store.change('joint_synthetic_active') as state:
            state.update(active_run=net.run_id, automatic_enabled=False)
        w.autosync.setChecked(True)
        w.foreground.set()
        w.sync_timer.setInterval(10)
        return net

    def assert_joint_scheduler_waits(self, net):
        from PyQt6.QtTest import QTest
        w = self.widget
        before = self.seed.store.state()
        before_calls = len(self.seed.server.calls)
        before_files = {p.name: p.read_bytes() for p in self.seed.store.files.glob('*.txt')}
        before_text = w.editor.text_with_pending_input_method()
        with patch.object(w, 'start_sync', wraps=w.start_sync) as entry:
            QTest.qWait(90)  # Cross several actual 10ms UI timer deadlines.
            self.assertGreaterEqual(entry.call_count, 2)
        self.assertIsNone(w.worker)
        self.assertEqual(self.seed.store.state(), before)
        self.assertEqual(len(self.seed.server.calls), before_calls)
        self.assertEqual(before_files, {p.name: p.read_bytes() for p in self.seed.store.files.glob('*.txt')})
        self.assertEqual(w.editor.text_with_pending_input_method(), before_text)
        self.assertEqual(self.seed.store.state()['runs'][net.run_id]['empty_cycles_used'], 0)

    def test_joint_j01_clean_ui_timer_reaches_mock_transport(self):
        import time
        from PyQt6.QtTest import QTest
        net = self.joint_scheduler()
        before = len(self.seed.server.calls)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            QTest.qWait(20)
            if not self.widget.scheduler.enabled and self.widget.worker is None:
                break
        self.assertIsNone(self.widget.worker)
        self.assertEqual(len(self.seed.server.calls) - before, 7)
        self.assertEqual(self.seed.store.state()['runs'][net.run_id]['empty_cycles_used'], 1)

    def test_joint_closed_widget_queued_resume_never_reads_store(self):
        from PyQt6.QtTest import QTest
        w = self.widget
        w.close()
        self.assertTrue(w.closing)
        with patch.object(w.store, 'state', side_effect=AssertionError('closed store accessed')) as read:
            w.resume_approved_execution()
            QTest.qWait(30)
            read.assert_not_called()

    def test_joint_j02_unsaved_ui_wait_preserves_autosave_timer_and_followup_draft(self):
        w = self.widget
        net = self.joint_scheduler()
        w.editor.setPlainText('미저장 첫 초안')
        self.assertTrue(w.idle_timer.isActive())
        self.assert_joint_scheduler_waits(net)
        self.assertTrue(w.idle_timer.isActive())
        # An ordinary explicit save may create a job; a subsequent edit must
        # still wait, without replacing that original job or forcing a save.
        w.save()
        jobs = deepcopy(self.seed.store.state()['jobs'])
        w.autosave.setChecked(False)
        w.editor.setPlainText('미저장 후속 초안\n')
        self.assert_joint_scheduler_waits(net)
        self.assertEqual(self.seed.store.state()['jobs'], jobs)

    def test_joint_j02_retained_other_document_draft_blocks_after_reopen(self):
        w = self.widget
        net = self.joint_scheduler()
        self.seed.store.preserve_draft(self.b, '다른 문서 보존 초안')
        w.store = self.seed.reopen()
        self.assert_joint_scheduler_waits(net)

    def test_joint_j03_clean_ime_without_preedit_waits(self):
        w = self.widget
        net = self.joint_scheduler()
        self.assertFalse(w.editor.document().isModified())
        w.editor._is_composing = True
        w.editor._ime_preedit_text = ''
        self.addCleanup(setattr, w.editor, '_is_composing', False)
        self.assertFalse(w.editor.has_pending_input_method())
        self.assert_joint_scheduler_waits(net)

    def test_joint_j04_persistence_failure_is_separate_from_dirty_and_recovers_explicitly(self):
        w = self.widget
        net = self.joint_scheduler()
        # Failure even with clean text must be an independent guard.
        with patch.object(self.seed.store, 'preserve_draft', side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError):
                w.preserve()
        self.assertTrue(w.draft_persistence_failed)
        self.assert_joint_scheduler_waits(net)
        # A successful ordinary preserve clears the error, not an automatic save.
        w.preserve()
        self.assertFalse(w.draft_persistence_failed)
        w.autosave.setChecked(False)
        with patch.object(self.seed.store, 'preserve_draft', side_effect=OSError('synthetic disk failure')):
            w.editor.setPlainText('메모리에만 남은 초안')
        self.assertTrue(w.draft_persistence_failed)
        self.assert_joint_scheduler_waits(net)
        self.assertNotEqual(self.seed.store.state()['nodes'][self.a]['draft'], w.editor.toPlainText())

    def test_joint_j02_normal_autosave_releases_wait_without_automatic_forced_save(self):
        from unittest.mock import Mock
        w = self.widget
        net = self.joint_scheduler()
        w.editor.setPlainText('일반 자동저장 완료\n')
        self.assert_joint_scheduler_waits(net)
        w.idle_timer.timeout.emit()
        state = self.seed.store.state()
        self.assertEqual(state['last_save_boundary']['source'], 'autosave_idle')
        self.assertEqual(state['nodes'][self.a]['draft'], state['nodes'][self.a]['local']['content'])
        self.assertIsNone(w.automatic_draft_wait_reason())
        # Observe worker dispatch separately; J01 covers a real Qt worker and HTTP.
        with patch('integrated_editor_ui.SyncWorker') as worker_class:
            worker_class.return_value = Mock()
            w.start_sync(True)
            worker_class.assert_called_once()
            worker_class.return_value.start.assert_called_once()
        w.worker = None
        self.assertEqual(self.seed.store.state()['runs'][net.run_id]['empty_cycles_used'], 0)

    def test_ui_switch_and_save_use_document_identity(self):
        w = self.widget
        w.rebuild_tree(self.a)
        w.editor.setPlainText('Windows 집필 😀\n')
        w.save()
        w.rebuild_tree(self.b)
        w.editor.setPlainText('둘째 초안')
        w.preserve()
        self.assertEqual(self.seed.store.state()['nodes'][self.a]['local']['content'], 'Windows 집필 😀\n')
        self.assertEqual(self.seed.store.state()['nodes'][self.b]['draft'], '둘째 초안')
        self.assertEqual(self.seed.server.calls, [])

    def test_received_text_is_not_overwritten_by_old_ui_text(self):
        w = self.widget
        w.rebuild_tree(self.a)
        w.editor.setPlainText('이전 로컬 편집')
        w.save()
        net = self.seed.network()
        self.seed.drain(net)
        self.seed.server.nodes[self.a].update(content='새 원격 본문',
            revision=self.seed.server.nodes[self.a]['revision'] + 1)
        net.cycle(receive_only=True)
        w.sync_outcome(True, 'received')
        self.assertEqual(w.editor.toPlainText(), '새 원격 본문')
        self.assertEqual(self.seed.store.state()['nodes'][self.a]['draft'], '새 원격 본문')
        w.save()
        boundary = self.seed.store.state()['last_save_boundary']
        self.assertEqual(boundary['input_source'], 'remote_snapshot')
        self.assertFalse(boundary['changed'])

    def test_paste_enter_idle_explicit_and_switch_boundaries_preserve_distinct_jobs(self):
        from PyQt6.QtCore import QMimeData, QEvent, Qt
        from PyQt6.QtGui import QKeyEvent
        w = self.widget
        w.rebuild_tree(self.a)
        w.editor.selectAll()
        mime = QMimeData()
        mime.setText('붙여넣기')
        w.editor.insertFromMimeData(mime)
        state = self.seed.store.state()
        self.assertEqual(state['last_input_boundary']['source'], 'paste')
        self.assertFalse(state['last_input_boundary']['after']['ends_lf'])
        self.assertTrue(w.idle_timer.isActive())
        w.idle_timer.timeout.emit()
        saved = self.seed.store.state()['last_save_boundary']
        self.assertEqual((saved['source'], saved['input_source'], saved['changed']),
                         ('autosave_idle', 'paste', True))
        w.editor.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return,
                                         Qt.KeyboardModifier.NoModifier, '\r'))
        w.buttons['save'].click()
        saved = self.seed.store.state()['last_save_boundary']
        self.assertEqual((saved['source'], saved['input_source']), ('save_button', 'enter'))
        self.assertTrue(saved['after']['ends_lf'])
        self.assertEqual(saved['after']['bytes'], len('붙여넣기\n'.encode('utf-8')))
        w.save_shortcut.activated.emit()
        saved = self.seed.store.state()['last_save_boundary']
        self.assertEqual((saved['source'], saved['changed']), ('save_shortcut', False))
        jobs = [j['after']['content'] for j in self.seed.store.state()['jobs']
                if j['kind'] == 'body' and j['node_id'] == self.a]
        self.assertEqual(jobs, ['붙여넣기', '붙여넣기\n'])
        w.editor.setPlainText('전환 전 편집')
        other = w.tree.findItems('둘.txt', Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive)[0]
        w.tree.setCurrentItem(other)
        saved = self.seed.store.state()['last_save_boundary']
        self.assertEqual((saved['source'], saved['node_id']), ('selection_change', self.a))
        self.assertEqual(self.seed.reopen().state()['nodes'][self.a]['local']['content'], '전환 전 편집')
        self.assertEqual(self.seed.server.calls, [])

    def test_ime_boundary_is_identified_and_does_not_force_save(self):
        from PyQt6.QtGui import QInputMethodEvent
        w = self.widget
        w.rebuild_tree(self.a)
        event = QInputMethodEvent()
        event.setCommitString('한')
        w.editor.inputMethodEvent(event)
        self.assertEqual(self.seed.store.state()['last_input_boundary']['source'], 'input_method')
        w.save()
        self.assertEqual(self.seed.store.state()['last_save_boundary']['input_source'], 'input_method')
        self.assertEqual(self.seed.server.calls, [])

    def test_auto_limit_ui_switch_and_restart_cannot_reenable(self):
        from unittest.mock import Mock
        from integrated_editor_ui import IntegratedWritingWidget
        w = self.widget
        net = self.seed.network(self.seed.approval(automatic_policy=dict(version=1, max_empty_cycles=1)))
        self.seed.drain(net)
        with self.seed.store.change('synthetic_active_run') as state:
            state.update(active_run=net.run_id, automatic_enabled=False)
        w.network = net
        w.scheduler = AutoSync(net, clock=lambda: self.seed.now)
        w.autosync.setChecked(True)
        self.assertEqual(w.scheduler.tick(), 'received')
        w.sync_outcome(True, 'received')
        self.assertFalse(w.autosync.isChecked())
        self.assertFalse(w.scheduler.enabled)
        self.assertIn('허용량', w.status.text())
        w.last_message = '초안 기록 실패'
        w.refresh()
        self.assertIn('초안 기록 실패', w.status.text())
        self.assertIn('허용량', w.status.text())
        count = len(self.seed.server.calls)
        w.autosync.setChecked(True)
        self.assertFalse(w.autosync.isChecked())
        self.assertFalse(self.seed.store.state()['automatic_enabled'])
        factory = Mock()
        reopened = IntegratedWritingWidget(self.seed.reopen(), network_factory=factory)
        self.addCleanup(reopened.close)
        reopened.foreground.set()
        reopened.autosync.setChecked(True)
        reopened.resume_approved_execution()
        factory.assert_not_called()
        self.assertFalse(reopened.autosync.isChecked())
        self.assertEqual(len(self.seed.server.calls), count)

    def test_startup_and_auto_without_scope_do_not_create_network(self):
        self.widget.autosync.setChecked(True)
        self.widget.start_sync(True)
        self.widget.start_sync(False)
        self.assertIsNone(self.widget.network)
        self.assertIsNone(self.widget.worker)
        self.assertEqual(self.seed.server.calls, [])

    def test_restart_resumes_only_same_approved_unexpired_execution_in_foreground(self):
        from unittest.mock import Mock
        from integrated_editor_ui import IntegratedWritingWidget
        approval = self.seed.approval()
        self.seed.store.open_run(approval)
        with self.seed.store.change('resume_fixture') as state:
            state.update(active_run=approval['run_id'], automatic_enabled=True)
        fake = Mock(run_id=approval['run_id'])
        factory = Mock(return_value=fake)
        w = IntegratedWritingWidget(self.seed.store, network_factory=factory)
        self.addCleanup(w.close)
        w.foreground.clear()
        w.resume_approved_execution()
        factory.assert_not_called()
        w.foreground.set()
        w.resume_approved_execution()
        self.assertEqual(w.network.run_id, approval['run_id'])
        self.assertTrue(w.scheduler.enabled)
        fake.cycle.assert_not_called()
        used = self.seed.store.state()['runs'][approval['run_id']]['used']
        self.assertEqual(used, 0)
