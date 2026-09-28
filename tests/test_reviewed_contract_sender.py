"""Actual sender/C9/store with synthetic request IDs and a no-network client."""
import copy
import json
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import reviewed_contract_sender as sender
from sync_contract import SyncContractError, json_sha256
from sync_v2_store import SyncV2Store, STAGE8_USER_VERSION
from tests import test_contract_preparation as preparation_fixtures
from tests import test_sync_contract_stage8 as fixtures


class ReviewedSenderTests(unittest.TestCase):
    def setUp(self):
        self.fixture = preparation_fixtures.ContractPreparationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.manager = self.fixture.manager
        self.store = self.fixture.store
        self.key = self.fixture.key
        self.envelope = self.fixture.prepare()
        self.request = self.envelope['request']
        self.batch = self.request['batch']['batch_id']
        self.sha = self.envelope['request_sha256']
        pin = patch.multiple(sender, REVIEWED_BATCH=self.batch, REVIEWED_REQUEST_SHA256=self.sha)
        pin.start(); self.addCleanup(pin.stop)
        self.calls = []; self.reads = []; self.writes = []
        self.on_read = None; self.on_rpc = None; self.on_write = None
        self.response = {'kind':'atomic_structure_commit_success','status':'committed','applied':True,
                         'batch_id':self.batch,'batch_payload_sha256':self.request['batch']['batch_payload_sha256'],
                         'results':[{'sequence':i['sequence'],'entity_id':i['entity_id'],'operation_id':i['operation_id'],
                                     'result_revision':i['base_revision']+1} for i in self.request['ordered_intents']]}
        with self.store._transaction() as c:
            b=self.store._reverse_preparation_baseline(c,self.key)
        self.remote={
            'projects':[{'project_id':self.request['project_id'],'owner_id':fixtures.DEFAULT_SUBJECT}],
            'project_members':[{'user_id':fixtures.DEFAULT_SUBJECT,'role':'editor'}],
            'folders':[{k:f[k] for k in ('folder_id','parent_folder_id','name','revision','is_deleted')} for f in b['folders']],
            'documents':[{**{k:d[k] for k in ('document_id','parent_folder_id','revision','structure_revision','name','is_deleted')},'relative_path':d['server_path']} for d in b['documents']],
            'tree_orders':[{'tree_order_id':o['tree_order_id'],'parent_folder_id':o['parent_folder_id'],
                            'children':json.loads(o['children_json']),'revision':o['revision']} for o in b['tree_orders']],
        }
        client=self.fixture.fixture.client
        client.auth=SimpleNamespace(
            get_user=lambda:SimpleNamespace(user=SimpleNamespace(id=fixtures.DEFAULT_SUBJECT)),
            get_session=lambda:SimpleNamespace(access_token=client._antigravity_access_token,
                                               refresh_token='synthetic-refresh-token'),
        )
        persist = patch.object(self.manager, '_persist_supabase_session')
        persist.start(); self.addCleanup(persist.stop)
        client.rpc=self.rpc
        client.table=self.table

    def table(self, name):
        test=self
        class Query:
            def select(self, columns, count=None):
                test.assertNotIn('content',columns);test.assertEqual(count,'exact');return self
            def eq(self, key, value):
                test.assertIn((key,value),(('project_id',test.request['project_id']),('user_id',fixtures.DEFAULT_SUBJECT)));return self
            def order(self, _key):return self
            def range(self, start, end):return self
            def execute(self):
                test.reads.append(name)
                if test.on_read:test.on_read(name)
                return SimpleNamespace(data=copy.deepcopy(test.remote[name]), count=len(test.remote[name]))
        return Query()

    def rpc(self, name, params):
        if self.on_rpc:self.on_rpc(name)
        def execute():
            self.calls.append(name)
            if name=='get_sync_handshake':return SimpleNamespace(data=fixtures.supported_handshake())
            if name=='get_project_status':return SimpleNamespace(data={'project_id':self.request['project_id'],'state':'active'})
            self.assertEqual(name,'atomic_structure_commit')
            self.assertEqual(params['p_request'],self.request)
            self.writes.append(copy.deepcopy(params['p_request']))
            if self.on_write:self.on_write()
            return SimpleNamespace(data=copy.deepcopy(self.response))
        return SimpleNamespace(execute=execute)

    def send(self):
        return self.manager.send_reviewed_contract_once(self.batch,self.sha,approved=True)

    def assert_closed_and_original(self):
        self.assertFalse(self.store.contract_path_enabled(self.key))
        self.assertEqual(self.store.reverse_contract_preparation(self.key),self.envelope)
        self.assertIsNone(self.store.next_ready_structure_batch(self.key))
        self.assertEqual(self.store.counts(self.key)['total'],0)
        self.assertFalse((self.fixture.root/'메인/원고/3권').exists())

    def test_success_persists_receipt_and_never_projects_or_enqueues(self):
        before=self.fixture.snapshot()
        self.assertEqual(self.send(),self.response)
        self.assertEqual(len(self.writes),1)
        self.assertEqual(self.reads,['projects','folders','documents','tree_orders']*2)
        row=self.store.reviewed_execution(self.batch)
        self.assertEqual((row['state'],row['http_attempts']),('committed',1))
        self.assertEqual(json.loads(row['response_json']),self.response)
        self.assertEqual(row['response_sha256'],json_sha256(self.response))
        self.assert_closed_and_original()
        after=self.fixture.snapshot()
        for table in ('sync_folders','sync_documents','sync_tree_orders','sync_operations','sync_structure_operations'):
            self.assertEqual(before[0][table],after[0][table])
        self.assertEqual(before[1],after[1])

    def test_rejection_is_terminal_and_not_retried_after_reopen(self):
        self.response={'kind':'atomic_structure_commit_failure','batch_id':self.batch,
                       'batch_payload_sha256':self.request['batch']['batch_payload_sha256'],
                       'status':'rejected','applied':False,'results':[],
                       'error':{'code':'REVISION_CONFLICT','message':'synthetic rejection','failed_sequence':2}}
        self.send()
        self.assertEqual(self.store.reviewed_execution(self.batch)['state'],'rejected')
        self.manager._v2_store=SyncV2Store(self.store.db_path)
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(len(self.writes),1)
        self.assert_closed_and_original()

    def test_response_loss_is_uncertain_and_cannot_resume(self):
        def lost():raise TimeoutError('synthetic lost response')
        self.on_write=lost
        with self.assertRaises(TimeoutError):self.send()
        self.assertEqual(self.store.reviewed_execution(self.batch)['state'],'uncertain')
        self.on_write=None
        self.manager._v2_store=SyncV2Store(self.store.db_path)
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(len(self.writes),1)
        self.assert_closed_and_original()

    def test_bad_receipt_is_uncertain_not_completed(self):
        self.response['results'][1]['result_revision']=99
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.store.reviewed_execution(self.batch)['state'],'uncertain')
        self.assertIsNone(self.store.reviewed_execution(self.batch)['response_json'])
        self.assert_closed_and_original()

    def test_duplicate_click_during_slow_read_is_not_second_send(self):
        def duplicate(name):
            if len(self.reads)==1:
                with self.assertRaises(SyncContractError):self.send()
        self.on_read=duplicate
        self.send()
        self.assertEqual(len(self.writes),1)
        self.assert_closed_and_original()

    def test_old_8009_preparation_migrates_unchanged(self):
        with self.store._transaction() as c:
            c.execute('DROP TABLE sync_reviewed_executions');c.execute('PRAGMA user_version=8009')
            original = tuple(c.execute('SELECT * FROM sync_contract_preparations').fetchone())
        before = self.fixture.snapshot()
        reopened=SyncV2Store(self.store.db_path)
        self.assertEqual(reopened.reverse_contract_preparation(self.key),self.envelope)
        self.assertIsNone(reopened.reviewed_execution(self.batch))
        with reopened._reader() as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],STAGE8_USER_VERSION)
            self.assertEqual(tuple(c.execute('SELECT * FROM sync_contract_preparations').fetchone()),original)
        after = self.fixture.snapshot()
        self.assertEqual({k:v for k,v in after[0].items() if k!='sync_reviewed_executions'},before[0])
        self.assertEqual(after[1],before[1])

    def test_crash_before_http_blocks_restart_without_network(self):
        self.store.claim_reviewed_execution(self.key,self.envelope,{'manual_once':True})
        self.store.set_contract_path_enabled(self.key, True)
        with patch.object(sender,'PROCESS_TOKEN','synthetic-new-process'):
            self.manager._v2_store=SyncV2Store(self.store.db_path)
        self.assertFalse(self.store.contract_path_enabled(self.key))
        self.assertEqual(self.store.reviewed_execution(self.batch)['state'],'stopped')
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.calls,[]);self.assertEqual(self.writes,[])

    def test_crash_after_attempt_blocks_restart_without_network(self):
        self.store.claim_reviewed_execution(self.key,self.envelope,{'manual_once':True})
        self.store.mark_reviewed_http_attempt(self.batch)
        self.store.set_contract_path_enabled(self.key, True)
        with patch.object(sender,'PROCESS_TOKEN','synthetic-new-process'):
            self.manager._v2_store=SyncV2Store(self.store.db_path)
        self.assertFalse(self.store.contract_path_enabled(self.key))
        self.assertEqual(self.store.reviewed_execution(self.batch)['state'],'uncertain')
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.calls,[]);self.assertEqual(self.writes,[])

    def test_unapproved_or_wrong_hash_never_claims(self):
        for batch,sha,approved in ((self.batch,self.sha,False),(self.batch,'0'*64,True),('wrong',self.sha,True)):
            with self.assertRaises(SyncContractError):self.manager.send_reviewed_contract_once(batch,sha,approved=approved)
        self.assertIsNone(self.store.reviewed_execution(self.batch));self.assertEqual(self.calls,[])

    def test_server_revision_changes_before_http_stops_without_attempt(self):
        def mutate(name):
            if name=='tree_orders' and self.reads.count(name)==2:self.remote['tree_orders'][0]['revision']=2
        self.on_read=mutate
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.writes,[])
        self.assertEqual(self.store.reviewed_execution(self.batch)['state'],'stopped')
        self.assert_closed_and_original()

    def test_server_missing_or_additional_rows_fail_closed(self):
        self.remote['folders'].append(dict(self.remote['folders'][0]))
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.writes,[]);self.assert_closed_and_original()

    def test_query_error_never_arms_or_sends(self):
        def fail(name):raise TimeoutError('synthetic read failure')
        self.on_read=fail
        with self.assertRaises(TimeoutError):self.send()
        self.assertEqual(self.writes,[]);self.assert_closed_and_original()

    def test_real_sdk_select_count_and_range_with_mock_transport(self):
        import httpx
        from postgrest import SyncPostgrestClient
        requests=[]
        def handler(request):
            requests.append(request)
            table=request.url.path.rsplit('/',1)[-1]
            data=self.remote[table]
            self.assertEqual(request.method,'GET')
            self.assertIn('count=exact',request.headers.get('prefer',''))
            self.assertEqual(request.url.params['project_id'],'eq.'+self.request['project_id'])
            self.assertNotIn('content',request.url.params['select'])
            return httpx.Response(200,json=data,headers={'content-range':f'0-{len(data)-1}/{len(data)}'})
        http=httpx.Client(transport=httpx.MockTransport(handler));self.addCleanup(http.close)
        sdk=SyncPostgrestClient('https://example.invalid/rest/v1',http_client=http)
        self.fixture.fixture.client.table=sdk.from_
        self.send()
        self.assertEqual(len(requests),8)
        self.assertEqual(len(self.writes),1)

    def assert_bad_metadata_result_stops(self, data, count):
        query=Mock()
        query.select.return_value=query;query.eq.return_value=query
        query.order.return_value=query;query.range.return_value=query
        query.execute.return_value=SimpleNamespace(data=data,count=count)
        self.fixture.fixture.client.table=lambda name:query if name=='folders' else self.table(name)
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.writes,[]);self.assert_closed_and_original()

    def test_truncated_metadata_response_never_sends(self):
        self.assert_bad_metadata_result_stops(self.remote['folders'][:-1],12)

    def test_missing_metadata_count_never_sends(self):
        self.assert_bad_metadata_result_stops(self.remote['folders'],None)

    def test_blocked_then_cancelled_at_http_boundary_invalidates_c9(self):
        import uuid
        def transition(name):
            if name=='atomic_structure_commit':
                op=self.store.enqueue(self.manager._v2_context,'synthetic.txt','synthetic')
                self.store.mark_blocked(op['operation_id'],'INVALID_ARGUMENT')
                self.store.cancel_operation(op['operation_id'],str(uuid.uuid4()))
        self.on_rpc=transition
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.writes,[]);self.assert_closed_and_original()

    def test_project_status_permission_failure_stops(self):
        original=self.rpc
        def forbidden(name,params):
            if name=='get_project_status':
                def fail():raise SyncContractError('FORBIDDEN')
                return SimpleNamespace(execute=fail)
            return original(name,params)
        self.fixture.fixture.client.rpc=forbidden
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.writes,[]);self.assert_closed_and_original()

    def test_viewer_cannot_reach_write_rpc(self):
        self.remote['projects'][0]['owner_id']=fixtures.OTHER_PROJECT_ID
        self.remote['project_members'][0]['role']='viewer'
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.writes,[]);self.assert_closed_and_original()

    def test_editor_membership_is_checked_at_both_boundaries(self):
        self.remote['projects'][0]['owner_id']=fixtures.OTHER_PROJECT_ID
        self.send()
        self.assertEqual(self.reads.count('project_members'),2)
        self.assertEqual(len(self.writes),1)

    def test_local_change_at_rpc_construction_caught_by_c9(self):
        def mutate(name):
            if name=='atomic_structure_commit':
                with self.store._transaction() as c:c.execute('UPDATE sync_tree_orders SET revision=2')
        self.on_rpc=mutate
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.writes,[]);self.assert_closed_and_original()

    def test_gate_closure_at_rpc_construction_caught_by_c9(self):
        def close(name):
            if name=='atomic_structure_commit':self.manager.disable_contract_path()
        self.on_rpc=close
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.writes,[]);self.assert_closed_and_original()

    def test_account_switch_before_http_stops(self):
        def switch(name):
            if name=='atomic_structure_commit':self.fixture.fixture.client._antigravity_access_token=fixtures.access_token_with_subject(fixtures.OTHER_PROJECT_ID)
        self.on_rpc=switch
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.writes,[]);self.assert_closed_and_original()

    def test_late_response_kept_in_original_store_after_project_release(self):
        def release():
            self.manager._v2_context=None
            self.manager._v2_store=None
        self.on_write=release
        self.send()
        self.assertEqual(self.store.reviewed_execution(self.batch)['state'],'committed')
        self.assert_closed_and_original()

    def test_build_mismatch_cannot_modify_original(self):
        with patch.object(sender,'CLIENT_BUILD_ID','different-build'):
            with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.writes,[]);self.assert_closed_and_original()

    def test_execution_remains_outside_automatic_and_general_manual_retry(self):
        def drain(name):
            with patch.object(self.manager,'_launch_contract_structure_batch') as structure, patch.object(self.manager,'_launch_v2_operation') as document:
                self.manager.retry_pending_syncs()
                self.manager.retry_pending_syncs(manual=True)
            structure.assert_not_called();document.assert_not_called()
        self.on_read=drain
        self.send()
        self.assertIsNone(self.store.next_ready_structure_batch(self.key))
        self.assertEqual(len(self.writes),1)

    def test_execution_ledger_cannot_reset(self):
        self.send()
        for sql in ("UPDATE sync_reviewed_executions SET state='preparing',http_attempts=0",'DELETE FROM sync_reviewed_executions'):
            with self.assertRaises(sqlite3.IntegrityError):
                with self.store._transaction() as c:c.execute(sql)

    def test_ui_no_is_side_effect_free(self):
        from settings_panel import SettingsPanel, QMessageBox
        target=SimpleNamespace(lbl_contract_review=Mock())
        with patch('sync_manager.SyncManager',return_value=self.manager), patch('settings_panel.QMessageBox.question',return_value=QMessageBox.StandardButton.No):
            SettingsPanel.send_contract_review(target)
        self.assertIsNone(self.store.reviewed_execution(self.batch));self.assertEqual(self.calls,[])


if __name__=='__main__':unittest.main()
