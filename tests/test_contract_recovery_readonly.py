"""Independent authenticated reader: synthetic client, no claim or DB writes."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sync_contract import SyncContractError
from tests import test_contract_http_zero_recovery as fixtures
from tests.test_sync_contract_stage8 import DEFAULT_SUBJECT


class RecoveryReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.HttpZeroRecoveryTests();self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.manager,self.store=self.case.manager,self.case.store
        self.client=self.case.case.fixture.fixture.client
        self.client.auth.get_user=Mock(return_value=SimpleNamespace(user=SimpleNamespace(id=DEFAULT_SUBJECT)))

    def inspect(self):return self.manager.inspect_recovery_server_readonly()

    def test_success_only_reads_auth_and_one_rpc_without_db_write_or_refresh(self):
        before=self.case.case.fixture.snapshot()
        with patch.object(self.store,'_transaction',side_effect=AssertionError('DB write forbidden')),\
             patch.object(self.store,'set_contract_path_enabled',side_effect=AssertionError('gate forbidden')),\
             patch.object(self.manager,'ensure_session_valid',side_effect=AssertionError('refresh forbidden')):
            report=self.inspect()
        self.client.auth.get_user.assert_called_once_with(jwt=self.client._antigravity_access_token)
        self.assertEqual(len(self.case.proof_calls),1);self.assertEqual(self.case.case.calls,[])
        self.assertEqual(len(report['proof']),11)
        self.assertTrue(report['ledger_empty']);self.assertFalse(report['execution_authorized'])
        self.assertFalse(report['stale']);self.assertFalse(report['recovery_round_present'])
        self.assertIsNone(self.store.recovery_round(self.case.batch))
        self.assertEqual(before,self.case.case.fixture.snapshot());self.case.assert_preserved()

    def test_nonempty_counts_are_observed_without_claim_or_send(self):
        self.case.proof_change=lambda d:d['counts'].update(attempts=1)
        report=self.inspect()
        self.assertIsNone(report['error_code']);self.assertFalse(report['ledger_empty'])
        self.assertEqual(report['proof']['counts']['attempts'],1)
        self.assertIsNone(self.store.recovery_round(self.case.batch));self.case.assert_preserved()

    def test_read_error_does_not_export_exception_text_or_tokens(self):
        def fail(_):raise TimeoutError('secret raw token '+self.client._antigravity_access_token)
        self.case.proof_change=fail
        report=self.inspect()
        self.assertEqual(report['error_code'],'RECOVERY_READ_FAILED');self.assertIsNone(report['proof'])
        self.assertNotIn(self.client._antigravity_access_token,json.dumps(report));self.case.assert_preserved()

    def test_wrong_user_never_calls_rpc(self):
        self.client.auth.get_user.return_value=SimpleNamespace(user=SimpleNamespace(id='other'))
        report=self.inspect()
        self.assertEqual(report['error_code'],'RECOVERY_READ_ACCOUNT_MISMATCH')
        self.assertEqual(self.case.proof_calls,[]);self.case.assert_preserved()

    def test_context_change_during_auth_prevents_rpc(self):
        def change(**kwargs):
            self.manager._contract_epoch+=1
            return SimpleNamespace(user=SimpleNamespace(id=DEFAULT_SUBJECT))
        self.client.auth.get_user.side_effect=change
        report=self.inspect()
        self.assertTrue(report['stale']);self.assertEqual(self.case.proof_calls,[])

    def test_context_change_during_rpc_marks_saved_proof_stale(self):
        self.case.proof_change=lambda d:setattr(self.manager,'_contract_epoch',self.manager._contract_epoch+1)
        report=self.inspect()
        self.assertTrue(report['stale']);self.assertIsNotNone(report['proof'])
        self.assertFalse(report['execution_authorized']);self.case.assert_preserved()

    def test_token_change_during_rpc_is_stale_even_with_same_account(self):
        self.case.proof_change=lambda d:setattr(self.client,'_antigravity_access_token','changed')
        self.assertTrue(self.inspect()['stale'])

    def test_gate_open_fails_before_any_auth_or_rpc(self):
        self.store.set_contract_path_enabled(self.case.key,True)
        with self.assertRaises(SyncContractError):self.inspect()
        self.client.auth.get_user.assert_not_called();self.assertEqual(self.case.proof_calls,[])
        self.store.set_contract_path_enabled(self.case.key,False);self.case.assert_preserved()

    def test_duplicate_read_and_sender_are_blocked_while_reading(self):
        def duplicate(d):
            with self.assertRaises(SyncContractError):self.inspect()
            with self.assertRaises(SyncContractError):self.case.send()
        self.case.proof_change=duplicate
        self.inspect()
        self.assertEqual(len(self.case.proof_calls),1);self.assertIsNone(self.store.recovery_round(self.case.batch))
        self.case.assert_preserved()

    def test_existing_recovery_cannot_be_used_as_reader_probe(self):
        self.store.claim_http_zero_recovery(self.case.key,self.case.envelope,self.case.approval)
        before=self.case.case.fixture.snapshot()
        with self.assertRaises(SyncContractError):self.inspect()
        self.assertEqual(self.case.case.fixture.snapshot(),before);self.assertEqual(self.case.proof_calls,[])

    def test_fresh_nonce_per_independent_read(self):
        first=self.inspect();second=self.inspect()
        self.assertNotEqual(first['proof']['nonce'],second['proof']['nonce']);self.case.assert_preserved()

    def test_export_uses_cached_snapshot_and_rejects_project_path(self):
        report=self.inspect()
        self.manager._contract_epoch+=1
        path=self.case.case.fixture.root.parent.parent/'recovery-read.json'
        self.addCleanup(lambda:path.unlink(missing_ok=True))
        with patch.object(self.manager,'inspect_recovery_server_readonly',side_effect=AssertionError('No new query')):
            saved=self.manager.export_recovery_server_readonly(str(path))
        self.assertEqual(saved['proof'],report['proof']);self.assertTrue(saved['context_changed_since_observation'])
        with self.assertRaises(SyncContractError):
            self.manager.export_recovery_server_readonly(str(self.case.case.fixture.root/'forbidden.json'))
        self.assertEqual(len(self.case.proof_calls),1)

    def test_ui_read_has_no_approval_and_export_cancel_has_no_effect(self):
        from settings_panel import SettingsPanel
        target=SimpleNamespace(btn_recovery_server_read=Mock(),lbl_recovery_server_read=Mock())
        worker=SimpleNamespace(resultReady=Mock(),finished=Mock())
        with patch('sync_manager.SyncManager',return_value=self.manager),\
             patch.object(self.manager,'launch_recovery_server_readonly',return_value=worker) as launch,\
             patch.object(self.manager,'_start_worker') as start,\
             patch('settings_panel.QMessageBox.question',side_effect=AssertionError('No approval')),\
             patch('settings_panel.QFileDialog.getSaveFileName',return_value=('','')):
            SettingsPanel.read_recovery_server(target);SettingsPanel.export_recovery_server(target)
        launch.assert_called_once_with();start.assert_called_once_with(worker)
        self.assertIsNone(self.store.recovery_round(self.case.batch));self.case.assert_preserved()


if __name__=='__main__':unittest.main()
