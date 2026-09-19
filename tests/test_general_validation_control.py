"""Gate lifecycle/undo on synthetic data; no Auth or real network for recovery."""
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from general_validation_boundary import GeneralValidationDenied
from general_validation_control import TemporaryWriteScope, pending_controls, restore_pending_controls, scope_path
from general_validation_ui import available_action
from general_test_gate import writes_held
import general_validation_plan as plan
import test_general_validation_runtime as runtime


class ControlTests(unittest.TestCase):
    setUp = runtime.RuntimeTests.setUp
    tearDown = runtime.RuntimeTests.tearDown
    new_service = runtime.RuntimeTests.new_service

    def scope(self, guard=None):
        return TemporaryWriteScope(store=self.store,local_key=self.key,directory=self.journals,
            stage='create',guard=guard or (lambda:self.service._local(None)))

    def test_only_target_gate_changes_and_ordinary_dispatch_remains_held(self):
        before = self.service._fingerprint()
        scope = self.scope()
        scope.open()
        self.assertEqual(self.store.get_project(self.key)['contract_path_enabled'],1)
        manager = SimpleNamespace(_v2_context=self.context,_v2_store=self.store)
        self.assertTrue(writes_held(manager))
        self.assertEqual(self.store.operation(self.other_op['operation_id']),self.other_op)
        scope.restore()
        self.assertEqual(self.service._fingerprint(),before)
        self.assertFalse(pending_controls(self.journals))

    def test_persistence_failure_rolls_back_gate_and_blocks_reexecution(self):
        before = self.store.get_project(self.key)
        with patch('general_validation_control.os.fsync',side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError):self.scope().open()
        self.assertEqual(self.store.get_project(self.key),before)
        self.assertEqual(available_action(self.journals),'restore')
        restore_pending_controls(self.store,self.key,self.journals)
        self.assertEqual(self.store.get_project(self.key),before)

    def test_cancel_after_undo_record_before_gate_commit_rolls_back(self):
        before = self.store.get_project(self.key)
        calls = []
        def guard():
            calls.append(1)
            if len(calls)==3:raise GeneralValidationDenied('SYNTHETIC_CANCEL')
        scope = self.scope(guard)
        with self.assertRaises(GeneralValidationDenied):scope.open()
        scope.restore()
        self.assertEqual(self.store.get_project(self.key),before)
        self.assertFalse(pending_controls(self.journals))

    def test_crash_undo_restores_gate_without_network_or_changing_files_or_queue(self):
        before = self.service._fingerprint()
        self.scope().open()  # Simulate process death without finally.
        self.service.ticket.cancel()
        self.current = False
        self.assertEqual(available_action(self.journals),'restore')
        restore_pending_controls(self.store,self.key,self.journals)
        self.assertEqual(self.service._fingerprint(),before)
        self.assertEqual(self.server.calls,[])
        restore_pending_controls(self.store,self.key,self.journals)

    def test_foreground_loss_after_gate_commit_still_restores_exact_project_row(self):
        before = self.store.get_project(self.key)
        open_scope = TemporaryWriteScope.open
        def cancel(scope):
            open_scope(scope)
            self.service.ticket.cancel()
        with patch.object(TemporaryWriteScope,'open',cancel):
            with self.assertRaises(Exception):self.service.run('create')
        self.assertEqual(self.store.get_project(self.key),before)
        self.assertFalse((self.writing/plan.PATH).exists())
        self.assertFalse(pending_controls(self.journals))

    def test_order_failure_restores_gate_but_keeps_created_body_and_attempt(self):
        before = self.store.get_project(self.key)
        hold = self.hold.read_bytes()
        self.server.failure = 'order'
        with self.assertRaises(GeneralValidationDenied):self.service.run('create')
        self.assertEqual(self.store.get_project(self.key),before)
        self.assertEqual(self.hold.read_bytes(),hold)
        self.assertEqual(self.store.get_document_by_id(plan.DOCUMENT_ID)['revision'],1)
        self.assertEqual(self.store.counts(self.key)['inflight'],1)
        self.assertIsNone(available_action(self.journals))

    def test_gate_drift_refuses_to_overwrite_independent_edit_and_hold_stays_held(self):
        scope = self.scope();scope.open()
        with self.store._transaction() as conn:
            conn.execute('UPDATE sync_projects SET updated_at=? WHERE local_key=?',('independent-change',self.key))
        with self.assertRaisesRegex(GeneralValidationDenied,'CONTROL_RESTORE_CONFLICT'):scope.restore()
        self.assertEqual(self.store.get_project(self.key)['updated_at'],'independent-change')
        self.assertEqual(available_action(self.journals),'restore')
        self.assertTrue(writes_held(SimpleNamespace(_v2_context=self.context,_v2_store=self.store)))

    def test_hold_drift_stops_send_restores_gate_and_does_not_overwrite_hold(self):
        before = self.store.get_project(self.key)
        changed = json.dumps({'format':1,'project_id':plan.PROJECT_ID,'state':'held','external':'change'}).encode()
        def change(name):
            if name=='document_commit':self.hold.write_bytes(changed)
        self.server.on_request = change
        with self.assertRaisesRegex(GeneralValidationDenied,'SEND_HOLD_CHANGED'):self.service.run('create')
        self.assertEqual(self.store.get_project(self.key),before)
        self.assertEqual(self.hold.read_bytes(),changed)
        self.assertEqual(self.store.get_document_by_id(plan.DOCUMENT_ID)['revision'],0)

    def test_missing_or_released_hold_never_opens_gate(self):
        self.hold.write_text(json.dumps({'format':1,'project_id':plan.PROJECT_ID,'state':'released'}),'utf-8')
        with self.assertRaisesRegex(GeneralValidationDenied,'ORIGINAL_SEND_HOLD'):self.scope().open()
        self.hold.unlink()  # Isolated fixture only.
        with self.assertRaisesRegex(GeneralValidationDenied,'ORIGINAL_SEND_HOLD'):self.scope().open()
        self.assertEqual(self.store.get_project(self.key)['contract_path_enabled'],0)

    def test_restore_marker_failure_cannot_report_completed_or_enable_next_send(self):
        from general_validation_control import _write_exclusive
        before = self.store.get_project(self.key)
        def fail_marker(path,value):
            if str(path).endswith('.restored'):raise OSError('synthetic marker failure')
            return _write_exclusive(path,value)
        with patch('general_validation_control._write_exclusive',side_effect=fail_marker):
            with self.assertRaises(OSError):self.service.run('create')
        self.assertEqual(self.store.get_project(self.key),before)
        self.assertEqual(available_action(self.journals),'restore')
        restore_pending_controls(self.store,self.key,self.journals)
        self.assertIsNone(available_action(self.journals))
        self.assertEqual(self.store.get_document_by_id(plan.DOCUMENT_ID)['revision'],1)

    def test_changed_undo_scope_cannot_target_another_project(self):
        scope = self.scope();scope.open()
        original = scope.path.read_bytes()
        record = json.loads(original)
        record['local_key'] = self.other_op['local_key']
        scope.path.write_text(json.dumps(record),'utf-8')
        with self.assertRaisesRegex(GeneralValidationDenied,'CONTROL_RECORD_SCOPE_CHANGED'):scope.restore()
        self.assertEqual(self.store.operation(self.other_op['operation_id']),self.other_op)
        scope.path.write_bytes(original)
        scope.restore()

    def test_remote_baseline_mismatch_never_reserves_or_opens_gate(self):
        before = self.store.get_project(self.key)
        self.server.rows['project_sync_settings'][0]['migration_epoch'] = 2
        with self.assertRaisesRegex(GeneralValidationDenied,'REMOTE_MODE_CHANGED'):self.service.run('create')
        self.assertEqual(self.store.get_project(self.key),before)
        self.assertFalse(scope_path(self.journals,'create').exists())

    def test_recovery_worker_does_not_authenticate_or_require_old_foreground_grant(self):
        from general_validation_ui import ValidationWorker
        self.service.ticket.cancel()
        worker = ValidationWorker('restore',self.service.ticket)
        outcomes = []
        worker.outcome.connect(lambda success,text:outcomes.append(success))
        with patch('general_validation_ui.open_service',side_effect=AssertionError('Auth forbidden')):
            with patch('general_validation_ui.restore_controls_only') as restore:
                worker.run()
                restore.assert_called_once_with()
        self.assertEqual(outcomes,[True])

    def test_existing_plaintext_hold_is_preserved_through_create_and_restore(self):
        from general_validation_control import LEGACY_HELD_MARKER
        self.hold.write_bytes(LEGACY_HELD_MARKER)
        before = self.store.get_project(self.key)
        self.assertEqual(self.service.run('create')['revision'],1)
        self.assertEqual(self.hold.read_bytes(),LEGACY_HELD_MARKER)
        self.assertEqual(self.store.get_project(self.key),before)
        self.assertTrue(writes_held(SimpleNamespace(_v2_context=self.context,_v2_store=self.store)))

    def test_unrecognized_plaintext_hold_is_not_accepted(self):
        self.hold.write_bytes(b'preparation-only; no outbound release\n')
        before = self.store.get_project(self.key)
        with self.assertRaises(ValueError):self.scope().open()
        self.assertEqual(self.store.get_project(self.key),before)
        self.assertFalse(scope_path(self.journals,'create').exists())


if __name__=='__main__':unittest.main()
