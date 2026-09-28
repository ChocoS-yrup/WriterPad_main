"""Readiness inspection and stage records on synthetic state only."""
import copy
import json
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch

from contract_readiness_diagnostics import observe_readiness, format_readiness
from sync_contract import SyncContractError
from tests import test_reviewed_contract_sender as fixtures


class ReadinessDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.ReviewedSenderTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.manager, self.store = self.case.manager, self.case.store

    def stopped(self):
        self.store.claim_reviewed_execution(self.case.key,self.case.envelope,{'manual_once':True})
        self.store.stop_reviewed_execution(self.case.batch)

    def test_stopped_inspection_and_export_preserve_all_state_without_dispatch(self):
        self.stopped()
        before=self.case.fixture.snapshot()
        with ExitStack() as stack:
            calls=[]
            for target,names in ((self.manager,('send_reviewed_contract_once','launch_reviewed_contract_once','perform_contract_handshake','retry_pending_syncs','prepare_reverse_contract_review')),
                                 (self.store,('claim_reviewed_execution','set_contract_path_enabled')),
                                 (self.case.fixture.fixture.client,('rpc','table'))):
                for name in names:
                    calls.append(stack.enter_context(patch.object(target,name,side_effect=AssertionError('Forbidden '+name))))
            report=self.manager.inspect_reviewed_contract_readiness()
            self.assertTrue(report['observation']['all_conditions_met'])
            self.assertTrue(report['already_executed']);self.assertFalse(report['execution_authorized'])
            self.assertEqual(report['execution']['state'],'stopped')
            path=self.case.fixture.root.parent.parent/'readiness.json'
            saved=self.manager.export_reviewed_readiness(str(path))
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')),saved)
            path.unlink()
            for call in calls:call.assert_not_called()
        self.assertEqual(self.case.fixture.snapshot(),before)
        self.assertIn('재실행 불가',format_readiness(report))

    def test_each_predicate_and_multiple_failures(self):
        manager=self.manager
        mutations=[('_auth_retry_blocked',True,'auth_unblocked'),('_shutting_down',True,'not_shutting_down'),
                   ('_v2_worker',object(),'document_worker_idle'),('_v2_structure_worker',object(),'structure_worker_idle'),
                   ('_active_server_syncs',1,'server_work_idle'),('_v2_structure_authority','unknown','authority_allowed')]
        for attr,value,code in mutations:
            with self.subTest(code=code),patch.object(manager,attr,value):
                report=manager.inspect_reviewed_contract_readiness()
                self.assertIn(code,report['observation']['failed_conditions'])
        with patch.object(manager,'contract_handshake_is_fresh',return_value=False) as call:
            self.assertIn('handshake_fresh',manager.inspect_reviewed_contract_readiness()['observation']['failed_conditions'])
            call.assert_called_once()
        self.store.set_contract_path_enabled(self.case.key,True)
        self.assertIn('gate_allowed',manager.inspect_reviewed_contract_readiness()['observation']['failed_conditions'])
        self.store.set_contract_path_enabled(self.case.key,False)
        self.assertIn('context_matches',observe_readiness(manager,('stale',))['failed_conditions'])
        with patch.object(manager,'_auth_retry_blocked',True),patch.object(manager,'_active_server_syncs',2):
            report=manager.inspect_reviewed_contract_readiness()
            self.assertEqual(set(report['observation']['failed_conditions']),{'auth_unblocked','server_work_idle'})

    def test_authority_subconditions_are_individually_observed(self):
        for attr,value,key in (('_v2_structure_authority_identity',None,'identity_matches'),
                               ('_contract_project_state_context',None,'project_context_matches')):
            with self.subTest(key=key),patch.object(self.manager,attr,value):
                report=self.manager.inspect_reviewed_contract_readiness()
                self.assertFalse(report['observation']['authority'][key])
        with patch.object(self.manager,'_current_project_server_state',return_value='trashed') as call:
            report=self.manager.inspect_reviewed_contract_readiness()
            self.assertFalse(report['observation']['authority']['project_active']);call.assert_called_once()

    def test_context_change_during_predicate_collection_is_stale(self):
        def change():
            self.manager._contract_epoch+=1
            return True
        with patch.object(self.manager,'contract_handshake_is_fresh',side_effect=change) as call:
            report=self.manager.inspect_reviewed_contract_readiness()
        call.assert_called_once()
        self.assertTrue(report['stale']);self.assertFalse(report['observation']['all_conditions_met'])
        self.assertIn('observation_current',report['observation']['failed_conditions'])

    def test_context_change_during_store_read_is_stale(self):
        real=self.store.reviewed_execution
        def change(batch):
            row=real(batch);self.manager._contract_epoch+=1;return row
        with patch.object(self.store,'reviewed_execution',side_effect=change):
            report=self.manager.inspect_reviewed_contract_readiness()
        self.assertTrue(report['stale']);self.assertFalse(report['observation']['all_conditions_met'])

    def test_export_does_not_refresh_observation_and_rejects_project_path(self):
        report=self.manager.inspect_reviewed_contract_readiness()
        self.manager._contract_epoch+=1
        with patch.object(self.manager,'inspect_reviewed_contract_readiness',side_effect=AssertionError('Refresh forbidden')):
            path=self.case.fixture.root.parent.parent/'readiness.json'
            saved=self.manager.export_reviewed_readiness(str(path));path.unlink()
        self.assertTrue(saved['context_changed_since_observation'])
        self.assertEqual(saved['observation']['observed_at'],report['observation']['observed_at'])
        with self.assertRaises(SyncContractError):self.manager.export_reviewed_readiness(str(self.case.fixture.root/'report.json'))

    def test_success_stage_records_and_existing_response_are_separate(self):
        self.case.send()
        rows=self.store.diagnostics(self.case.key,limit=100)
        stages={row['metadata']['stage'] for row in rows if row['event']=='reviewed_readiness'}
        self.assertTrue({'initial_remote_before','initial_remote_after','gate_before','gate_after',
                         'second_remote_before','second_remote_after','rpc_constructed','http_before','response_received','gate_closed'}<=stages)
        self.assertEqual(self.store.reverse_contract_preparation(self.case.key),self.case.envelope)
        self.assertEqual(self.store.reviewed_execution(self.case.batch)['state'],'committed')

    def test_failed_predicates_recorded_once_and_survive_rollback(self):
        def busy(name):
            if name=='projects':self.manager._active_server_syncs=1
        self.case.on_read=busy
        try:
            with patch('contract_readiness_diagnostics.observe_readiness',wraps=observe_readiness) as observe:
                with self.assertRaises(SyncContractError):self.case.send()
            observe.assert_called_once()
        finally:self.manager._active_server_syncs=0
        rows=self.store.diagnostics(self.case.key,limit=100)
        failures=[row['metadata'] for row in rows if row['metadata'].get('error_code')=='CONTRACT_PREPARATION_NOT_READY']
        self.assertEqual(len(failures),1)
        self.assertEqual(failures[0]['stage'],'initial_remote_before')
        self.assertFalse(failures[0]['observation']['conditions']['server_work_idle'])
        self.assertEqual(self.store.reviewed_execution(self.case.batch)['http_attempts'],0)
        self.assertEqual(self.case.writes,[])
        raw=json.dumps(rows)
        for secret in ('synthetic-refresh-token','writer_device_id','ordered_intents','account_marker'):
            self.assertNotIn(secret,raw)

    def test_ui_read_and_cancel_export_never_send(self):
        from settings_panel import SettingsPanel
        target=SimpleNamespace(lbl_contract_readiness=Mock())
        with patch('sync_manager.SyncManager',return_value=self.manager),patch.object(self.manager,'launch_reviewed_contract_once') as send:
            result=SettingsPanel.read_contract_readiness(target)
            with patch('settings_panel.QFileDialog.getSaveFileName',return_value=('','')):
                SettingsPanel.export_contract_readiness(target)
        send.assert_not_called();self.assertIsNotNone(result)
        self.assertIsNone(self.store.reviewed_execution(self.case.batch))


if __name__=='__main__':unittest.main()
