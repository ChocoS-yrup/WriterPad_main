"""Fixed child policy: real temporary SQLite/C9, synthetic client and parent pins."""
import copy
import json
import multiprocessing
import sqlite3
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import contract_post_coordination_resume as resume
import contract_http_zero_recovery as recovery
import reviewed_contract_sender as sender
from sync_contract import SyncContractError, json_sha256
from sync_v2_store import SyncV2Store
from handshake_lifecycle import ContractDispatchPaused
from tests import test_contract_http_zero_recovery as fixtures
from tests.test_sync_contract_stage8 import DEFAULT_SUBJECT


def process_claim(path, key, envelope, approval, barrier, results):
    sender.REVIEWED_BATCH=approval['batch_id'];sender.REVIEWED_REQUEST_SHA256=approval['request_sha256']
    recovery.ORIGINAL_PREPARATION_ROWS_SHA256=approval['original_preparation_sha256']
    recovery.ORIGINAL_EXECUTION_ROWS_SHA256=approval['original_execution_sha256']
    resume.PARENT_ID=approval['parent_recovery_id'];resume.PARENT_ROWS_SHA256=approval['parent_rows_sha256']
    resume.PARENT_EVENTS_SHA256=approval['parent_events_sha256']
    with patch.object(resume,'execution_build_sha256',return_value=approval['execution_build_sha256']):
        store=SyncV2Store(path)
        barrier.wait(timeout=15)
        try:
            rid=resume.ResumeLedger(store).claim_http_zero_recovery(key,envelope,approval)
            results.put(('claimed',rid))
        except (SyncContractError,sqlite3.IntegrityError) as error:
            results.put(('denied',getattr(error,'code',type(error).__name__)))


class PostCoordinationResumeTests(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.HttpZeroRecoveryTests();self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.manager,self.store=self.case.manager,self.case.store
        self.envelope,self.key,self.batch=self.case.envelope,self.case.key,self.case.batch
        def stop(name):
            if name=='projects':self.manager._begin_structure_authority_selection()
        self.case.case.on_read=stop
        with self.assertRaises(SyncContractError):self.case.send()
        self.case.case.on_read=None
        parent=self.store.recovery_round(self.batch)
        with self.store._reader() as c:
            rows=[dict(r) for r in c.execute('SELECT * FROM sync_reviewed_recoveries ORDER BY recovery_id')]
            events=[dict(r) for r in c.execute('SELECT event_id,recovery_id,metadata_json FROM sync_reviewed_recovery_events ORDER BY event_id')]
        self.assertEqual(len(events),4)
        pins=patch.multiple(resume,PARENT_ID=parent['recovery_id'],PARENT_ROWS_SHA256=json_sha256(rows),PARENT_EVENTS_SHA256=json_sha256(events))
        pins.start();self.addCleanup(pins.stop)
        build=patch.object(resume,'execution_build_sha256',return_value='a'*64)
        build.start();self.addCleanup(build.stop)
        self.manager._accept_structure_authority('legacy')
        self.manager.perform_contract_handshake()
        self.manager._current_pull_coordinator().update(pulling=False,pull_pending=False,baseline_validated=True)
        self.manager._v2_pull_worker=None;self.manager._v2_pull_worker_identity=None
        self.approval=resume.new_approval(self.envelope)
        self.ledger=resume.ResumeLedger(self.store)
        self.original=self.history_snapshot()
        self.case.case.calls.clear();self.case.case.reads.clear();self.case.proof_calls.clear()
        self.client=self.case.case.fixture.fixture.client
        self.client.auth.get_user=Mock(return_value=SimpleNamespace(user=SimpleNamespace(id=DEFAULT_SUBJECT)))

    def history_snapshot(self):
        with self.store._reader() as c:
            return {t:[dict(r) for r in c.execute('SELECT * FROM '+t)] for t in (
                'sync_contract_preparations','sync_reviewed_executions','sync_reviewed_recoveries',
                'sync_reviewed_recovery_events','sync_reviewed_recovery_receipts')}

    def send(self):return self.manager.send_post_coordination_resume_once(approval=self.approval)

    def assert_preserved(self):
        self.assertEqual(self.history_snapshot(),self.original)
        self.case.case.assert_closed_and_original()

    def test_migration_8011_adds_empty_tables_without_rewriting_history(self):
        with self.store._transaction() as c:
            for table in (resume.RECEIPTS,resume.EVENTS,resume.ROUNDS):c.execute('DROP TABLE '+table)
            c.execute('PRAGMA user_version=8011')
        with patch.object(sender,'PROCESS_TOKEN','migration-process'):
            reopened=SyncV2Store(self.store.db_path)
        with reopened._reader() as c:
            self.assertEqual(c.execute('PRAGMA user_version').fetchone()[0],8012)
            for table in (resume.ROUNDS,resume.EVENTS,resume.RECEIPTS):self.assertEqual(c.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
        self.assert_preserved()

    def test_success_reuses_request_and_two_fresh_proofs_separate_receipt(self):
        self.assertEqual(self.send(),self.case.case.response)
        self.assertEqual(self.case.case.writes,[self.case.case.request])
        self.assertEqual(len(self.case.proof_calls),2)
        self.assertNotEqual(self.case.proof_calls[0]['p_nonce'],self.case.proof_calls[1]['p_nonce'])
        row=self.ledger.recovery_round(self.batch)
        self.assertEqual((row['state'],row['http_attempts']),('committed',1))
        self.assertEqual(row['parent_recovery_id'],resume.PARENT_ID)
        self.assertEqual(row['execution_build_sha256'],'a'*64)
        with self.store._reader() as c:
            receipt=dict(c.execute('SELECT * FROM '+resume.RECEIPTS).fetchone())
            stages=[json.loads(r[0])['stage'] for r in c.execute('SELECT metadata_json FROM '+resume.EVENTS+' ORDER BY event_id')]
        self.assertEqual(receipt['response_sha256'],json_sha256(self.case.case.response))
        self.assertLess(stages.index('http_attempt_durable'),stages.index('response_received'))
        self.assertEqual(stages[-1],'gate_closed')
        self.assert_preserved()

    def test_old_changed_parent_policy_build_hash_account_or_approval_denied_without_claim(self):
        variants=[self.case.approval,None,True]
        for key,value in [('policy','next_policy'),('parent_recovery_id','new-parent'),('parent_rows_sha256','0'*64),
            ('parent_events_sha256','0'*64),('execution_build_sha256','b'*64),('account_marker','other'),
            ('approval_id',self.case.approval['approval_id']),('manual_once',1),
            ('approved_at',(datetime.now(timezone.utc)-timedelta(minutes=6)).isoformat())]:
            a=dict(self.approval);a[key]=value;variants.append(a)
        with patch.object(self.store,'set_contract_path_enabled',side_effect=AssertionError('gate forbidden')):
            for a in variants:
                with self.subTest(approval=a),self.assertRaises(SyncContractError):
                    self.manager.send_post_coordination_resume_once(approval=a)
        self.assertIsNone(self.ledger.recovery_round(self.batch));self.assertEqual(self.case.case.calls,[])
        self.assert_preserved()

    def test_wrong_parent_row_and_event_hash_are_rejected(self):
        for field in ('PARENT_ROWS_SHA256','PARENT_EVENTS_SHA256','PARENT_ID'):
            with patch.object(resume,field,'wrong'),self.assertRaises(SyncContractError):self.send()
        self.assertIsNone(self.ledger.recovery_round(self.batch));self.assert_preserved()

    def test_modified_parent_event_is_rejected(self):
        with self.store._transaction() as c:
            c.execute('DROP TRIGGER sync_reviewed_recovery_events_no_update')
            c.execute("UPDATE sync_reviewed_recovery_events SET metadata_json='{}' WHERE event_id=(SELECT min(event_id) FROM sync_reviewed_recovery_events)")
        with self.assertRaises(SyncContractError):self.send()
        self.assertIsNone(self.ledger.recovery_round(self.batch))

    def test_parent_http1_uncertain_and_receipt_rejected_even_if_repinned(self):
        with self.store._transaction() as c:
            c.execute('DROP TRIGGER sync_reviewed_recovery_no_reset')
            c.execute("UPDATE sync_reviewed_recoveries SET state='uncertain',http_attempts=1")
            parent=[dict(r) for r in c.execute('SELECT * FROM sync_reviewed_recoveries')]
        with patch.object(resume,'PARENT_ROWS_SHA256',json_sha256(parent)),self.assertRaises(SyncContractError):self.send()
        with self.store._transaction() as c:
            c.execute("UPDATE sync_reviewed_recoveries SET state='stopped',http_attempts=0")
            c.execute('INSERT INTO sync_reviewed_recovery_receipts VALUES (?,?,?,?)',(resume.PARENT_ID,'{}','a',sender.now()))
            parent=[dict(r) for r in c.execute('SELECT * FROM sync_reviewed_recoveries')]
        with patch.object(resume,'PARENT_ROWS_SHA256',json_sha256(parent)),self.assertRaises(SyncContractError):self.send()
        self.assertIsNone(self.ledger.recovery_round(self.batch))

    def test_original_http1_is_still_rejected(self):
        with self.store._transaction() as c:
            c.execute('DROP TRIGGER sync_reviewed_executions_no_reset')
            c.execute("UPDATE sync_reviewed_executions SET state='uncertain',http_attempts=1")
        with self.assertRaises(SyncContractError):self.send()
        self.assertIsNone(self.ledger.recovery_round(self.batch))

    def test_read_only_parent_path_uses_current_auth_and_no_claim_or_db_write(self):
        with patch.object(self.store,'_transaction',side_effect=AssertionError('DB write forbidden')),\
             patch.object(self.store,'set_contract_path_enabled',side_effect=AssertionError('gate forbidden')),\
             patch.object(self.manager,'ensure_session_valid',side_effect=AssertionError('refresh forbidden')):
            report=self.manager.inspect_post_coordination_server_readonly()
        self.assertEqual(report['kind'],'post_coordination_readonly_observation')
        self.assertTrue(report['preserved_history_verified']);self.assertTrue(report['ledger_empty'])
        self.assertFalse(report['execution_authorized']);self.assertFalse(report['recovery_round_present'])
        self.client.auth.get_user.assert_called_once_with(jwt=self.client._antigravity_access_token)
        self.assertEqual(len(self.case.proof_calls),1)
        path=self.case.case.fixture.root.parent.parent/'post-read.json'
        try:
            self.manager.export_post_coordination_server_readonly(str(path))
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['proof'],report['proof'])
        finally:
            if path.exists():path.unlink()
        self.assertIsNone(self.ledger.recovery_round(self.batch));self.assert_preserved()

    def test_readonly_nonzero_and_missing_proof_are_not_execution_permission(self):
        self.case.proof_change=lambda d:d['counts'].update(attempts=1)
        report=self.manager.inspect_post_coordination_server_readonly()
        self.assertFalse(report['ledger_empty']);self.assertFalse(report['execution_authorized'])
        self.case.proof_change=lambda d:d.pop('complete')
        report=self.manager.inspect_post_coordination_server_readonly()
        self.assertIsNotNone(report['error_code'])
        self.assertIsNone(self.ledger.recovery_round(self.batch));self.assert_preserved()

    def test_readonly_wrong_account_and_context_switch_stop_without_claim(self):
        self.client.auth.get_user.return_value=SimpleNamespace(user=SimpleNamespace(id='other'))
        report=self.manager.inspect_post_coordination_server_readonly()
        self.assertEqual(report['error_code'],'RECOVERY_READ_ACCOUNT_MISMATCH');self.assertEqual(self.case.proof_calls,[])
        self.client.auth.get_user.return_value=SimpleNamespace(user=SimpleNamespace(id=DEFAULT_SUBJECT))
        self.case.proof_change=lambda d:setattr(self.manager,'_auth_context_generation',self.manager._auth_context_generation+1)
        self.assertTrue(self.manager.inspect_post_coordination_server_readonly()['stale'])
        self.assertIsNone(self.ledger.recovery_round(self.batch));self.assert_preserved()

    def test_ui_cancel_creates_neither_approval_nor_worker(self):
        from settings_panel import SettingsPanel
        from PyQt6.QtWidgets import QMessageBox
        target=SimpleNamespace(lbl_contract_readiness=Mock(),_contract_review_error=str)
        with patch('sync_manager.SyncManager',return_value=self.manager),\
             patch('settings_panel.QMessageBox.question',return_value=QMessageBox.StandardButton.No) as question,\
             patch.object(resume,'new_approval') as approval,\
             patch.object(self.manager,'launch_post_coordination_resume_once') as launch:
            SettingsPanel.send_post_coordination_resume(target)
            question.assert_called_once();approval.assert_not_called();launch.assert_not_called()
        self.assertIsNone(self.ledger.recovery_round(self.batch));self.assert_preserved()

    def test_ui_explicit_yes_creates_fresh_policy_approval_before_launch(self):
        from settings_panel import SettingsPanel
        from PyQt6.QtWidgets import QMessageBox
        target=SimpleNamespace(lbl_contract_readiness=Mock(),btn_post_coordination_resume=Mock(),_contract_review_error=str)
        worker=Mock()
        with patch('sync_manager.SyncManager',return_value=self.manager),\
             patch('settings_panel.QMessageBox.question',return_value=QMessageBox.StandardButton.Yes),\
             patch.object(self.manager,'launch_post_coordination_resume_once',return_value=worker) as launch,\
             patch.object(self.manager,'_start_worker') as start:
            SettingsPanel.send_post_coordination_resume(target)
            approval=launch.call_args.kwargs['approval']
            self.assertEqual(approval['kind'],resume.APPROVAL_KIND)
            self.assertEqual(approval['parent_recovery_id'],resume.PARENT_ID)
            self.assertNotEqual(approval['approval_id'],self.case.approval['approval_id'])
            self.assertEqual(approval['execution_build_sha256'],'a'*64)
            start.assert_called_once_with(worker)
        self.assertIsNone(self.ledger.recovery_round(self.batch));self.assert_preserved()

    def test_source_interpreter_is_not_an_installed_execution_build(self):
        # Use the original implementation rather than this fixture's build pin.
        from pathlib import Path
        import ast
        source=ast.parse(Path(resume.__file__).read_text(encoding='utf-8'))
        fn=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='execution_build_sha256')
        fn.decorator_list=[]
        ns={'sys':SimpleNamespace(frozen=False),'fail':recovery.fail}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'build-id-test','exec'),ns)
        with self.assertRaises(SyncContractError):ns['execution_build_sha256']()

    def test_preparation_and_pull_not_ready_are_non_consuming(self):
        for attr,value in [('_auth_retry_blocked',True),('_shutting_down',True),('_v2_structure_authority','unknown'),('_v2_structure_worker',object())]:
            with patch.object(self.manager,attr,value),self.assertRaises(SyncContractError):self.send()
        coordinator=self.manager._current_pull_coordinator();coordinator['pulling']=True
        with patch.object(resume.ResumeLedger,'claim_http_zero_recovery') as claim,self.assertRaises(SyncContractError):self.send()
        claim.assert_not_called();coordinator['pulling']=False
        self.assertIsNone(self.ledger.recovery_round(self.batch));self.assertEqual(self.case.case.calls,[])
        self.assert_preserved()

    def test_review_first_defers_pull_and_preserves_epoch(self):
        def arrive(name):
            if name=='projects':
                epoch=self.manager._contract_write_epoch
                self.assertFalse(self.manager.pull_remote_changes_async(reason='general'))
                self.assertEqual(self.manager._contract_write_epoch,epoch)
        self.case.case.on_read=arrive
        with patch.object(self.manager,'_queue_pull_after_review') as wake:
            self.send();wake.assert_called_once()
        self.assertTrue(self.manager._current_pull_coordinator()['pull_pending'])
        self.assert_preserved()

    def test_nonzero_incomplete_and_old_proof_each_stop_new_round_at_http0(self):
        # One round per independent fixture; a consumed row is never reset for another test.
        self.case.proof_change=lambda d:d['counts'].update(results=1)
        with self.assertRaises(SyncContractError):self.send()
        row=self.ledger.recovery_round(self.batch)
        self.assertEqual((row['state'],row['http_attempts']),('stopped',0))
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.case.case.writes,[]);self.assert_preserved()

    def test_missing_proof_stops_at_http0(self):
        self.case.proof_change=lambda d:d.pop('authorized')
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.ledger.recovery_round(self.batch)['http_attempts'],0);self.assert_preserved()

    def test_shutdown_after_claim_preserves_stopped_zero(self):
        self.case.case.on_read=lambda name:setattr(self.manager,'_shutting_down',True)
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.ledger.recovery_round(self.batch)['state'],'stopped')
        self.assertEqual(self.ledger.recovery_round(self.batch)['http_attempts'],0)
        self.assert_preserved()

    def test_c9_invalidation_at_rpc_construction_still_blocks_http(self):
        def change(name):
            if name=='atomic_structure_commit':self.manager._contract_write_epoch+=1
        self.case.case.on_rpc=change
        with self.assertRaises(ContractDispatchPaused):self.send()
        self.assertEqual(self.ledger.recovery_round(self.batch)['http_attempts'],0)
        self.assertEqual(self.case.case.writes,[]);self.assert_preserved()

    def test_stale_proof_stops_at_http0(self):
        self.case.proof_change=lambda d:d.update(checked_at='2020-01-01T00:00:00+00:00')
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.ledger.recovery_round(self.batch)['http_attempts'],0);self.assert_preserved()

    def test_second_proof_detects_late_server_record(self):
        def second(d):
            if len(self.case.proof_calls)==2:d['counts']['operations']=1
        self.case.proof_change=second
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(len(self.case.proof_calls),2);self.assertEqual(self.case.case.writes,[]);self.assert_preserved()

    def test_remote_revision_mismatch_keeps_request_and_stops(self):
        self.case.case.remote['tree_orders'][0]['revision']+=1
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(self.case.case.writes,[]);self.assert_preserved()

    def test_duplicate_claim_is_atomic_across_processes(self):
        ctx=multiprocessing.get_context('spawn');barrier=ctx.Barrier(2);results=ctx.Queue()
        args=(self.store.db_path,self.key,self.envelope,self.approval,barrier,results)
        children=[ctx.Process(target=process_claim,args=args) for _ in range(2)]
        for child in children:child.start()
        for child in children:
            child.join(20)
            if child.is_alive():child.terminate();child.join();self.fail('Child claim timed out')
            self.assertEqual(child.exitcode,0)
        outcomes=[results.get(timeout=5)[0] for _ in children]
        self.assertEqual(sorted(outcomes),['claimed','denied'])
        results.close();results.join_thread()
        with self.store._reader() as c:self.assertEqual(c.execute('SELECT count(*) FROM '+resume.ROUNDS).fetchone()[0],1)
        self.assert_preserved()

    def test_reopen_stops_claimed_round_and_cannot_create_another(self):
        rid=self.ledger.claim_http_zero_recovery(self.key,self.envelope,self.approval)
        with patch.object(sender,'PROCESS_TOKEN','another-process'):
            reopened=SyncV2Store(self.store.db_path)
        row=resume.ResumeLedger(reopened).recovery_round(self.batch)
        self.assertEqual((row['state'],row['http_attempts']),('stopped',0))
        with self.assertRaises(SyncContractError):self.send()
        self.assert_preserved()

    def test_response_loss_is_uncertain_and_never_automatically_resumed(self):
        def lost():raise TimeoutError('synthetic response lost')
        self.case.case.on_write=lost
        with self.assertRaises(TimeoutError):self.send()
        row=self.ledger.recovery_round(self.batch)
        self.assertEqual((row['state'],row['http_attempts']),('uncertain',1))
        with self.assertRaises(SyncContractError):self.send()
        self.assertEqual(len(self.case.case.writes),1);self.assert_preserved()

    def test_late_receipt_is_separate_from_terminal_uncertain_state(self):
        rid=self.ledger.claim_http_zero_recovery(self.key,self.envelope,self.approval)
        self.ledger.check_http_zero_round(self.key,self.envelope,rid,mark_attempt=True)
        self.ledger.stop_http_zero_recovery(rid)
        before=self.ledger.recovery_round(self.batch)
        self.ledger.finish_http_zero_response(rid,self.case.case.request,self.case.case.response)
        self.assertEqual(self.ledger.recovery_round(self.batch),before)
        with self.store._reader() as c:self.assertEqual(c.execute('SELECT count(*) FROM '+resume.RECEIPTS).fetchone()[0],1)
        self.assert_preserved()

    def test_reopen_after_durable_http_keeps_uncertain_and_closes_gate(self):
        rid=self.ledger.claim_http_zero_recovery(self.key,self.envelope,self.approval)
        self.ledger.check_http_zero_round(self.key,self.envelope,rid,mark_attempt=True)
        self.store.set_contract_path_enabled(self.key,True)
        with patch.object(sender,'PROCESS_TOKEN','another-process'):
            reopened=SyncV2Store(self.store.db_path)
        row=resume.ResumeLedger(reopened).recovery_round(self.batch)
        self.assertEqual((row['state'],row['http_attempts']),('uncertain',1))
        self.assert_preserved()

    def test_rejected_receipt_is_terminal(self):
        self.case.case.response={'kind':'atomic_structure_commit_failure','batch_id':self.batch,
            'batch_payload_sha256':self.case.case.request['batch']['batch_payload_sha256'],
            'status':'rejected','applied':False,'results':[],
            'error':{'code':'REVISION_CONFLICT','message':'synthetic rejection','failed_sequence':2}}
        self.send();self.assertEqual(self.ledger.recovery_round(self.batch)['state'],'rejected')
        with self.assertRaises(SyncContractError):self.send()
        self.assert_preserved()

    def test_new_ledger_is_append_only_and_fixed_policy_cannot_chain(self):
        rid=self.ledger.claim_http_zero_recovery(self.key,self.envelope,self.approval)
        self.ledger.stop_http_zero_recovery(rid)
        with self.store._transaction() as c:
            for sql in (f"UPDATE {resume.ROUNDS} SET state='preparing'",f"UPDATE {resume.ROUNDS} SET policy='v2'",
                        f'DELETE FROM {resume.ROUNDS}',f"UPDATE {resume.EVENTS} SET metadata_json='{{}}'",f'DELETE FROM {resume.EVENTS}'):
                with self.assertRaises(sqlite3.IntegrityError):c.execute(sql)
        self.approval['parent_recovery_id']=rid
        with self.assertRaises(SyncContractError):self.send()
        self.assert_preserved()

    def test_new_round_never_enters_ordinary_dispatcher(self):
        self.ledger.claim_http_zero_recovery(self.key,self.envelope,self.approval)
        self.assertIsNone(self.store.next_ready_structure_batch(self.key))
        self.assertEqual(self.store.counts(self.key)['total'],0)
        with patch.object(self.manager,'send_post_coordination_resume_once') as send:
            self.manager.retry_pending_syncs()
            send.assert_not_called()
        self.assertEqual(self.case.case.writes,[]);self.assert_preserved()


if __name__=='__main__':unittest.main()
