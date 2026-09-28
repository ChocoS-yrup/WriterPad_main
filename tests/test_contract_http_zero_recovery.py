"""Synthetic original stop + one separate recovery, real store/C9, no network."""
import copy
import json
import sqlite3
import threading
import multiprocessing
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import contract_http_zero_recovery as recovery
import reviewed_contract_sender as sender
from sync_contract import SyncContractError, json_sha256
from sync_v2_store import SyncV2Store
from tests import test_reviewed_contract_sender as fixtures


def claim_in_child(db_path, key, envelope, approval, barrier, results):
    # Child process has its own owner token. Only this synthetic DB is opened.
    sender.REVIEWED_BATCH = envelope['request']['batch']['batch_id']
    sender.REVIEWED_REQUEST_SHA256 = envelope['request_sha256']
    recovery.ORIGINAL_EXECUTION_ROWS_SHA256 = approval['original_execution_sha256']
    recovery.ORIGINAL_PREPARATION_ROWS_SHA256 = approval['original_preparation_sha256']
    store = SyncV2Store(db_path)
    barrier.wait(timeout=15)
    try:
        rid = store.claim_http_zero_recovery(key,envelope,approval)
        results.put(('claimed',rid))
    except SyncContractError as error:
        results.put(('denied',error.code))


class HttpZeroRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.ReviewedSenderTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.manager, self.store = self.case.manager, self.case.store
        self.envelope, self.key = self.case.envelope, self.case.key
        self.batch = self.case.batch
        self.store.claim_reviewed_execution(self.key, self.envelope, {'manual_once':True})
        self.store.stop_reviewed_execution(self.batch)
        self.original = self.originals()
        pins = patch.multiple(recovery, ORIGINAL_PREPARATION_ROWS_SHA256=json_sha256(self.original[0]),
                              ORIGINAL_EXECUTION_ROWS_SHA256=json_sha256(self.original[1]))
        pins.start(); self.addCleanup(pins.stop)
        self.approval = {'kind':recovery.APPROVAL_KIND, 'approval_id':str(uuid.uuid4()),
                         'batch_id':self.batch, 'request_sha256':self.case.sha,
                         'original_preparation_sha256':recovery.ORIGINAL_PREPARATION_ROWS_SHA256,
                         'original_execution_sha256':recovery.ORIGINAL_EXECUTION_ROWS_SHA256,
                         'account_marker':self.envelope['account_marker'], 'approved_at':sender.now(), 'manual_once':True}
        self.proof_calls = []
        self.proof_change = None
        self.case.fixture.fixture.client.rpc = self.rpc

    def originals(self):
        with self.store._reader() as c:
            return tuple([dict(r) for r in c.execute('SELECT * FROM '+t)] for t in
                         ('sync_contract_preparations','sync_reviewed_executions'))

    def rpc(self, name, params):
        if name != recovery.LEDGER_RPC:
            return self.case.rpc(name, params)
        def execute():
            self.proof_calls.append(copy.deepcopy(params))
            data = {'kind':recovery.PROOF_KIND, 'project_id':params['p_project_id'],
                    'batch_id':params['p_batch_id'], 'request_sha256':params['p_request_sha256'],
                    'operation_ids':params['p_operation_ids'], 'nonce':params['p_nonce'],
                    'account_marker':self.envelope['account_marker'], 'complete':True, 'authorized':True,
                    'counts':dict.fromkeys(('batches','operations','attempts','results'),0), 'checked_at':sender.now()}
            if self.proof_change:
                self.proof_change(data)
            return SimpleNamespace(data=data)
        return SimpleNamespace(execute=execute)

    def send(self):
        return self.manager.send_http_zero_recovery_once(approval=self.approval)

    def assert_preserved(self):
        self.assertEqual(self.originals(), self.original)
        self.case.assert_closed_and_original()

    def test_success_one_identical_request_separate_receipt_and_trace(self):
        before = self.case.fixture.snapshot()
        self.assertEqual(self.send(), self.case.response)
        self.assertEqual(self.case.writes, [self.case.request])
        self.assertEqual(len(self.proof_calls), 2)
        self.assertNotEqual(self.proof_calls[0]['p_nonce'],self.proof_calls[1]['p_nonce'])
        r = self.store.recovery_round(self.batch)
        self.assertEqual((r['state'],r['http_attempts']),('committed',1))
        self.assertEqual(json.loads(r['approval_json']),self.approval)
        with self.store._reader() as c:
            receipt = dict(c.execute('SELECT * FROM sync_reviewed_recovery_receipts').fetchone())
            events = [json.loads(x[0]) for x in c.execute('SELECT metadata_json FROM sync_reviewed_recovery_events ORDER BY event_id')]
        self.assertEqual(receipt['recovery_id'],r['recovery_id'])
        self.assertEqual(receipt['response_sha256'],json_sha256(self.case.response))
        stages = [e['stage'] for e in events]
        for s in ('claimed','initial_remote_before','ledger_verified','second_remote_before','http_before',
                  'http_attempt_durable','response_received','gate_closed'):
            self.assertIn(s,stages)
        self.assertLess(stages.index('http_attempt_durable'),stages.index('response_received'))
        after = self.case.fixture.snapshot()
        for table in ('sync_folders','sync_documents','sync_tree_orders','sync_operations','sync_structure_operations','sync_contract_batches'):
            self.assertEqual(before[0][table],after[0][table])
        self.assertEqual(before[1],after[1]); self.assert_preserved()

    def test_no_old_wrong_or_stale_approval_has_no_claim_gate_or_network(self):
        bad = [None, True, {'manual_once':True}, json.loads(self.original[1][0]['approval_json'])]
        for field,value in [('kind','old'),('request_sha256','0'*64),('batch_id',str(uuid.uuid4())),
                            ('original_execution_sha256','0'*64),('original_preparation_sha256','0'*64),
                            ('account_marker','other'),('manual_once',1),('approval_id','invalid'),
                            ('approved_at',(datetime.now(timezone.utc)-timedelta(minutes=6)).isoformat()),
                            ('approved_at',self.original[1][0]['started_at'])]:
            item = dict(self.approval); item[field]=value; bad.append(item)
        with patch.object(self.store,'set_contract_path_enabled',side_effect=AssertionError('gate forbidden')):
            for approval in bad:
                with self.subTest(approval=approval),self.assertRaises(SyncContractError):
                    self.manager.send_http_zero_recovery_once(approval=approval)
        self.assertIsNone(self.store.recovery_round(self.batch))
        self.assertEqual(self.case.calls,[]); self.assertEqual(self.proof_calls,[]); self.assert_preserved()

    def test_tampered_original_is_rejected_even_with_matching_new_approval(self):
        with self.store._transaction() as c:
            c.execute('DROP TRIGGER sync_reviewed_executions_no_reset')
            c.execute("UPDATE sync_reviewed_executions SET owner_token='tampered'")
        with self.assertRaises(SyncContractError):self.send()
        self.assertIsNone(self.store.recovery_round(self.batch));self.assertEqual(self.case.calls,[])

    def test_http1_uncertain_receipt_incomplete_and_missing_history_not_candidates(self):
        with self.store._transaction() as c:
            c.execute('DROP TRIGGER sync_reviewed_executions_no_reset')
            for state,attempts,receipt,finished in [('uncertain',1,None,sender.now()),('committed',1,'{}',sender.now()),
                                                  ('rejected',1,'{}',sender.now()),('stopped',0,'{}',sender.now()),
                                                  ('stopped',0,None,None),('preparing',0,None,None)]:
                c.execute('UPDATE sync_reviewed_executions SET state=?,http_attempts=?,response_json=?,finished_at=?',
                          (state,attempts,receipt,finished))
                # Even a freshly pinned but intrinsically invalid row must fail.
                e = [dict(r) for r in c.execute('SELECT * FROM sync_reviewed_executions')]
                with patch.object(recovery,'ORIGINAL_EXECUTION_ROWS_SHA256',json_sha256(e)):
                    with self.assertRaises(SyncContractError):recovery.original_rows(c,self.key,self.envelope)
            c.execute('DROP TRIGGER sync_reviewed_executions_no_delete')
            c.execute('DELETE FROM sync_reviewed_executions')
            with self.assertRaises(SyncContractError):recovery.original_rows(c,self.key,self.envelope)

    def test_envelope_tamper_never_claims(self):
        bad = copy.deepcopy(self.envelope);bad['baseline_sha256']='0'*64
        with self.assertRaises(SyncContractError):self.store.claim_http_zero_recovery(self.key,bad,self.approval)
        self.assertIsNone(self.store.recovery_round(self.batch))

    def test_failed_or_missing_server_reader_stops_http0_and_preserves_original(self):
        def fail(_):raise TimeoutError('synthetic unavailable RPC')
        self.proof_change=fail
        with self.assertRaises(TimeoutError):self.send()
        self.assertEqual(self.store.recovery_round(self.batch)['state'],'stopped')
        self.assertEqual(self.store.recovery_round(self.batch)['http_attempts'],0)
        self.assertEqual(self.case.writes,[]);self.assert_preserved()

    def test_proof_requires_all_fields_permission_counts_nonce_identity_freshness(self):
        dispatch = recovery.HttpZeroRecoveryDispatch(self.manager,self.envelope,self.manager._contract_dispatch_context(),'unused')
        dispatch.trace = Mock()
        changes = [lambda d,k=k:d.pop(k) for k in ('complete','authorized','counts','checked_at','nonce')]
        changes += [lambda d,k=k:d.update({k:False}) for k in ('complete','authorized')]
        changes += [lambda d,k=k:d['counts'].update({k:1}) for k in ('batches','operations','attempts','results')]
        changes += [lambda d:d.update(nonce='old'),lambda d:d.update(account_marker='other'),
                    lambda d:d.update(operation_ids=[]),lambda d:d.update(request_sha256='0'*64),
                    lambda d:d.update(counts={'batches':0}),lambda d:d['counts'].update(batches=False),
                    lambda d:d.update(checked_at='2020-01-01T00:00:00+00:00')]
        for change in changes:
            self.proof_change=change
            with self.subTest(change=change),self.assertRaises(SyncContractError):recovery.check_server_ledger(dispatch)
        dispatch.trace.assert_not_called();self.assertIsNone(self.store.recovery_round(self.batch))

    def test_ledger_appearing_at_second_boundary_stops_and_closes(self):
        def exists(d):
            if len(self.proof_calls)==2:d['counts']['attempts']=1
        self.proof_change=exists
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.case.writes,[]);self.assert_preserved()

    def test_initial_ledger_existence_never_opens_gate(self):
        self.proof_change=lambda d:d['counts'].update(batches=1)
        gate = self.store.set_contract_path_enabled
        with patch.object(self.store,'set_contract_path_enabled',wraps=gate) as setter:
            with self.assertRaises(SyncContractError):self.send()
        self.assertEqual([x.args[1] for x in setter.call_args_list],[False]);self.assert_preserved()

    def test_concurrent_connections_only_one_durable_claim(self):
        other = SyncV2Store(self.store.db_path)
        barrier = threading.Barrier(2)
        def claim(store):
            barrier.wait(timeout=5)
            try:return store.claim_http_zero_recovery(self.key,self.envelope,self.approval)
            except SyncContractError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(claim,s) for s in (self.store,other)]
            result=[f.result(timeout=10) for f in futures]
        self.assertEqual(sum(x is not None for x in result),1)
        self.assertEqual(self.case.writes,[]);self.assert_preserved()

    def test_duplicate_click_and_reopen_cannot_use_second_approval(self):
        def duplicate(_):
            with self.assertRaises(SyncContractError):self.send()
        self.case.on_read=duplicate
        self.send();self.case.on_read=None
        self.manager._v2_store=SyncV2Store(self.store.db_path)
        self.approval['approval_id']=str(uuid.uuid4());self.approval['approved_at']=sender.now()
        with self.assertRaises(SyncContractError):self.send()
        with self.assertRaises(SyncContractError):self.case.send()
        self.assertEqual(len(self.case.writes),1);self.assert_preserved()

    def test_two_real_processes_cannot_create_two_rounds(self):
        ctx = multiprocessing.get_context('spawn')
        barrier,results=ctx.Barrier(2),ctx.Queue()
        processes=[ctx.Process(target=claim_in_child,args=(self.store.db_path,self.key,self.envelope,self.approval,barrier,results)) for _ in range(2)]
        try:
            for process in processes:process.start()
            answers=[results.get(timeout=30) for _ in range(2)]
            for process in processes:
                process.join(timeout=10);self.assertEqual(process.exitcode,0)
            self.assertEqual(sorted(x[0] for x in answers),['claimed','denied'])
        finally:
            for process in processes:
                if process.is_alive():process.terminate();process.join(timeout=5)
            results.close()
        # Reopen records a stop, never resumes the winning process's round.
        SyncV2Store(self.store.db_path)
        self.assertEqual(self.store.recovery_round(self.batch)['state'],'stopped')
        self.assert_preserved()

    def test_crash_before_marker_never_resumes_or_rewrites_original(self):
        rid = self.store.claim_http_zero_recovery(self.key,self.envelope,self.approval)
        self.store.set_contract_path_enabled(self.key,True)
        with patch.object(sender,'PROCESS_TOKEN','next-process'):SyncV2Store(self.store.db_path)
        self.assertEqual(self.store.recovery_round(self.batch)['state'],'stopped')
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.case.calls,[]);self.assert_preserved()

    def test_crash_after_marker_is_uncertain_and_late_receipt_can_be_preserved(self):
        rid = self.store.claim_http_zero_recovery(self.key,self.envelope,self.approval)
        self.store.check_http_zero_round(self.key,self.envelope,rid,mark_attempt=True)
        self.store.set_contract_path_enabled(self.key,True)
        with patch.object(sender,'PROCESS_TOKEN','next-process'):SyncV2Store(self.store.db_path)
        self.assertEqual(self.store.recovery_round(self.batch)['state'],'uncertain')
        self.store.finish_http_zero_response(rid,self.case.request,self.case.response)
        report = self.store.inspect_http_zero_recovery(self.key,self.envelope)
        self.assertEqual(report['receipt_status'],'committed');self.assertEqual(report['round']['state'],'uncertain')
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.case.writes,[]);self.assert_preserved()

    def test_terminal_receipt_before_gate_close_crash_closes_on_reopen(self):
        self.send()
        self.store.set_contract_path_enabled(self.key,True)
        terminal = self.store.recovery_round(self.batch)
        with patch.object(sender,'PROCESS_TOKEN','next-process'):SyncV2Store(self.store.db_path)
        self.assertEqual(self.store.recovery_round(self.batch),terminal)
        self.assert_preserved()

    def test_write_marker_is_committed_before_transport_and_loss_never_retries(self):
        def loss():
            row=self.store.recovery_round(self.batch)
            self.assertEqual((row['state'],row['http_attempts']),('attempted',1))
            raise TimeoutError('lost response')
        self.case.on_write=loss
        with self.assertRaises(TimeoutError):self.send()
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.store.recovery_round(self.batch)['state'],'uncertain')
        self.assertEqual(len(self.case.writes),1);self.assert_preserved()

    def test_rejection_is_stored_only_in_recovery_receipt(self):
        self.case.response={'kind':'atomic_structure_commit_failure','batch_id':self.batch,
                            'batch_payload_sha256':self.case.request['batch']['batch_payload_sha256'],
                            'status':'rejected','applied':False,'results':[],
                            'error':{'code':'REVISION_CONFLICT','message':'synthetic','failed_sequence':2}}
        self.send();self.assertEqual(self.store.recovery_round(self.batch)['state'],'rejected');self.assert_preserved()

    def test_invalid_response_is_uncertain(self):
        self.case.response['results'][1]['result_revision']=99
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.store.recovery_round(self.batch)['state'],'uncertain');self.assert_preserved()

    def test_late_response_uses_original_store_after_project_switch(self):
        def switch():self.manager._v2_context=None;self.manager._v2_store=None
        self.case.on_write=switch
        self.send();self.assertEqual(self.store.recovery_round(self.batch)['state'],'committed');self.assert_preserved()

    def test_c9_blocked_then_resolved_at_rpc_construction_stops(self):
        def change(name):
            if name=='atomic_structure_commit':
                op=self.store.enqueue(self.manager._v2_context,'synthetic.txt','synthetic')
                self.store.mark_blocked(op['operation_id'],'INVALID_ARGUMENT')
                self.store.cancel_operation(op['operation_id'],str(uuid.uuid4()))
        self.case.on_rpc=change
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.case.writes,[]);self.assert_preserved()

    def test_account_change_at_second_proof_stops_before_http(self):
        def change(_):
            if len(self.proof_calls)==2:self.manager._contract_epoch+=1
        self.proof_change=change
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.case.writes,[]);self.assert_preserved()

    def test_gate_change_at_rpc_construction_still_hits_c9(self):
        def close(name):
            if name=='atomic_structure_commit':self.manager.disable_contract_path()
        self.case.on_rpc=close
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.case.writes,[]);self.assert_preserved()

    def test_local_order_change_at_second_boundary_stops(self):
        def change(_):
            if len(self.proof_calls)==2:
                with self.store._transaction() as c:c.execute('UPDATE sync_tree_orders SET revision=2')
        self.proof_change=change
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.case.writes,[]);self.assert_preserved()

    def test_predicate_failure_is_saved_on_round_after_transaction_rollback(self):
        def busy(name):
            if name=='projects':self.manager._active_server_syncs=1
        self.case.on_read=busy
        try:
            with self.assertRaises(SyncContractError):self.send()
        finally:self.manager._active_server_syncs=0
        with self.store._reader() as c:
            events=[json.loads(r[0]) for r in c.execute('SELECT metadata_json FROM sync_reviewed_recovery_events')]
        failed=[e for e in events if e.get('error_code')=='CONTRACT_PREPARATION_NOT_READY']
        self.assertEqual(len(failed),1)
        self.assertFalse(failed[0]['observation']['conditions']['server_work_idle'])
        self.assertEqual(self.case.writes,[]);self.assert_preserved()

    def test_condition_change_during_recovery_inspection_marks_report_stale(self):
        real=self.store.inspect_http_zero_recovery
        def change(*args):
            result=real(*args);self.manager._contract_epoch+=1;return result
        with patch.object(self.store,'inspect_http_zero_recovery',side_effect=change):
            report=self.manager.inspect_reviewed_contract_readiness()
        self.assertTrue(report['stale']);self.assertFalse(report['observation']['all_conditions_met'])

    def test_soft_exit_before_http_closes_gate_and_does_not_resume(self):
        def stop(name):
            if name=='atomic_structure_commit':raise SystemExit('synthetic exit')
        self.case.on_rpc=stop
        with self.assertRaises(SystemExit):self.send()
        self.assertEqual(self.store.recovery_round(self.batch)['state'],'stopped')
        self.assertEqual(self.case.writes,[]);self.assert_preserved()

    def test_other_process_invalidates_preparing_round_before_attempt(self):
        def reopen(_):
            if len(self.proof_calls)==2:
                with patch.object(sender,'PROCESS_TOKEN','other-process'):SyncV2Store(self.store.db_path)
        self.proof_change=reopen
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.case.writes,[]);self.assert_preserved()

    def test_general_retry_and_recovery_inspection_never_dispatch(self):
        def drain(_):
            with patch.object(self.manager,'_launch_contract_structure_batch') as s,patch.object(self.manager,'_launch_v2_operation') as d:
                self.manager.retry_pending_syncs();self.manager.retry_pending_syncs(manual=True)
            s.assert_not_called();d.assert_not_called()
        self.case.on_read=drain
        before=self.case.fixture.snapshot()
        report=self.manager.inspect_reviewed_contract_readiness()
        self.assertTrue(report['http_zero_recovery']['local_candidate'])
        self.assertFalse(report['http_zero_recovery']['execution_authorized'])
        self.assertEqual(self.case.fixture.snapshot(),before)
        self.send()
        before=self.case.fixture.snapshot()
        self.manager.inspect_reviewed_contract_readiness()
        self.assertEqual(self.case.fixture.snapshot(),before);self.assertEqual(len(self.case.writes),1)

    def test_schema_8010_upgrade_preserves_both_original_rows(self):
        with self.store._transaction() as c:
            for t in ('sync_reviewed_recovery_receipts','sync_reviewed_recovery_events','sync_reviewed_recoveries'):
                c.execute('DROP TABLE '+t)
            c.execute('PRAGMA user_version=8010')
        before=self.case.fixture.snapshot()
        SyncV2Store(self.store.db_path)
        after=self.case.fixture.snapshot()
        for table,rows in before[0].items():self.assertEqual(after[0][table],rows)
        self.assertEqual(after[1],before[1]);self.assert_preserved()

    def test_recovery_rows_and_receipts_cannot_reset_or_delete(self):
        self.send()
        for sql in ("UPDATE sync_reviewed_recoveries SET state='preparing',http_attempts=0",
                    "UPDATE sync_reviewed_recoveries SET approval_json='{}'",'DELETE FROM sync_reviewed_recoveries',
                    'DELETE FROM sync_reviewed_recovery_events','DELETE FROM sync_reviewed_recovery_receipts',
                    "UPDATE sync_reviewed_recovery_receipts SET response_json='{}'"):
            with self.subTest(sql=sql),self.assertRaises(sqlite3.IntegrityError):
                with self.store._transaction() as c:c.execute(sql)
        self.assert_preserved()

    def test_ui_no_does_not_claim_or_launch(self):
        from settings_panel import SettingsPanel,QMessageBox
        target=SimpleNamespace(lbl_contract_readiness=Mock())
        with patch('sync_manager.SyncManager',return_value=self.manager),patch.object(self.manager,'launch_http_zero_recovery_once') as launch:
            with patch('settings_panel.QMessageBox.question',return_value=QMessageBox.StandardButton.No):
                SettingsPanel.send_http_zero_recovery(target)
        launch.assert_not_called();self.assertIsNone(self.store.recovery_round(self.batch));self.assert_preserved()


if __name__=='__main__':unittest.main()
