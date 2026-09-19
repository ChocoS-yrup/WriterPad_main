"""Explicit release and repeat preparation: synthetic context, no network."""
import json
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch
from PyQt6.QtWidgets import QMessageBox
from tests import test_general_test_gate as fixtures
from tests import test_general_test_gate_ui as ui_fixtures
from handshake_lifecycle import ContractDispatchPaused
import general_test_gate as gate


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.GeneralTestGateTests()
        self.case.setUp();self.addCleanup(self.case.doCleanups)
        self.manager,self.store,self.client=self.case.manager,self.case.store,self.case.client
        self.case.prepare();self.ticket=self.case.ticket
        gate.activate(self.manager,self.ticket)

    def baseline(self):
        gate.prepare_release(self.manager,self.ticket)
        gate.start_pull(self.manager,self.ticket)
        self.case.finish_pull()

    def test_release_requires_new_baseline_and_fresh_handshake(self):
        with self.assertRaises(gate.PreparationError):gate.release(self.manager,self.ticket)
        self.baseline();before=len(self.client.calls)
        result=gate.release(self.manager,self.ticket)
        self.assertEqual(len(self.client.calls),before+1)
        self.assertFalse(result["outbound_send_held"])
        self.assertTrue(self.manager.contract_path_enabled())
        self.assertTrue(self.manager._uses_contract_structure())
        self.assertFalse(gate.writes_held(self.manager))
        self.assertTrue(all(name in {"get_sync_handshake","get_project_status"} for name,_ in self.client.calls))
        with self.assertRaises(gate.PreparationError):gate.release(self.manager,self.ticket)

    def test_old_contract_and_legacy_dispatch_contexts_do_not_resume(self):
        self.baseline();old=self.manager._contract_dispatch_context()
        gate.release(self.manager,self.ticket);self.client.calls.clear()
        with self.assertRaises(ContractDispatchPaused):self.manager._check_contract_dispatch({},old)
        result=self.manager._process_v2_operation("synthetic",dispatch_context=old)
        self.assertEqual(result["error"],"GENERAL_TEST_AUTHORITY_CHANGED")
        self.assertEqual(self.client.calls,[])

    def test_new_normal_folder_request_does_not_use_fixed_empty_fixture(self):
        self.baseline();gate.release(self.manager,self.ticket)
        with patch("general_test_gate._baseline",side_effect=AssertionError("empty fixture must not gate general sync")):
            request=self.manager.queue_atomic_structure_batch([{
                "entity_kind":"folder","entity_id":str(uuid.uuid4()),"intent_kind":"create","base_revision":0,
                "payload":{"name":"synthetic new folder","parent_folder_id":self.case.folders[0]["id"]}}],retry=False)
            self.manager._check_contract_dispatch(request,self.manager._contract_dispatch_context())
            report=gate.current_observation(self.manager,self.ticket)
            self.assertEqual(report["pending_operations"],1)
        self.assertTrue(all(name in {"get_sync_handshake","get_project_status"} for name,_ in self.client.calls))

    def test_release_state_survives_restart_and_reprepare_reholds_without_send(self):
        self.baseline();gate.release(self.manager,self.ticket)
        restarted=SimpleNamespace(_v2_context=dict(self.manager._v2_context),_v2_store=self.store)
        self.assertFalse(gate.writes_held(restarted))
        self.client.calls.clear();ticket=gate.reprepare(self.manager)
        self.assertTrue(gate.writes_held(restarted))
        self.assertFalse(self.manager.contract_path_enabled())
        self.assertFalse(ticket["ready"]);self.assertEqual(self.client.calls,[])
        gate.handshake(self.manager,ticket);gate.start_pull(self.manager,ticket);self.case.finish_pull()
        self.assertTrue(gate.observation(self.manager,ticket)["baseline_ready"])

    def test_reprepare_preserves_user_changes_and_reports_nonempty_baseline(self):
        self.baseline();gate.release(self.manager,self.ticket)
        from project_creation_v1 import create_item
        from project_identity_v1 import read_identity
        create_item(str(self.case.root),self.case.folders[0]["id"],"synthetic manuscript",False)
        before=read_identity(str(self.case.root))
        ticket=gate.reprepare(self.manager)
        gate.handshake(self.manager,ticket);gate.start_pull(self.manager,ticket);self.case.finish_pull()
        with self.assertRaises(gate.PreparationError):gate.observation(self.manager,ticket)
        self.assertEqual(read_identity(str(self.case.root)),before)
        self.assertTrue(gate.writes_held(self.manager))

    def test_failed_atomic_publication_keeps_hold_and_retry_succeeds(self):
        self.baseline();before=gate._hold_path(self.store).read_bytes()
        with patch("general_test_gate.os.replace",side_effect=OSError("synthetic disk error")):
            with self.assertRaises(OSError):gate.release(self.manager,self.ticket)
        self.assertEqual(gate._hold_path(self.store).read_bytes(),before)
        self.assertTrue(gate.writes_held(self.manager))
        gate.release(self.manager,self.ticket)
        self.assertFalse(gate.writes_held(self.manager))

    def test_pending_change_during_final_handshake_keeps_hold(self):
        self.baseline();original=self.client._answer
        def pending():
            result=original();self.manager._retry_queue["synthetic"]=object();return result
        with patch.object(self.client,"_answer",side_effect=pending):
            with self.assertRaises(gate.PreparationError):gate.release(self.manager,self.ticket)
        self.assertTrue(gate.writes_held(self.manager))

    def test_closed_gate_or_view_change_during_handshake_keeps_hold(self):
        self.baseline();original=self.client._answer
        def close_gate():
            result=original();self.store.set_contract_path_enabled(self.manager._v2_context["local_key"],False);return result
        with patch.object(self.client,"_answer",side_effect=close_gate):
            with self.assertRaises(gate.PreparationError):gate.release(self.manager,self.ticket)
        self.assertTrue(gate.writes_held(self.manager))

    def test_failed_release_pull_keeps_hold_and_no_hidden_dispatch(self):
        gate.prepare_release(self.manager,self.ticket);gate.start_pull(self.manager,self.ticket)
        worker=self.case.workers[-1];worker.resultReady.emit(False,"synthetic failure");worker.finished.emit()
        with self.assertRaises(gate.PreparationError):gate.release(self.manager,self.ticket)
        self.assertTrue(gate.writes_held(self.manager))
        self.assertTrue(all(name in {"get_sync_handshake","get_project_status"} for name,_ in self.client.calls))

    def test_release_network_error_or_corrupt_marker_stays_held(self):
        self.baseline();self.client.reply=TimeoutError("synthetic timeout")
        with self.assertRaises(TimeoutError):gate.release(self.manager,self.ticket)
        self.assertTrue(gate.writes_held(self.manager))
        gate._hold_path(self.store).write_text('{"state":"released"',encoding='utf-8')
        self.assertTrue(gate.writes_held(self.manager))


class ReleaseUITests(unittest.TestCase):
    def setUp(self):
        self.ui=ui_fixtures.GeneralTestGateUITests()
        self.ui.setUp();self.addCleanup(self.ui.doCleanups)
        self.card=self.ui.card;self.case=self.ui.case
        self.ui.prepare()
        with patch("general_test_gate_ui.QMessageBox.question",return_value=QMessageBox.StandardButton.Yes):
            self.card.activate_button.click()
        self.ui.until(lambda:self.card.worker is None)

    def test_cancel_then_release_full_ui_and_duplicate_click(self):
        self.assertTrue(self.card.release_button.isEnabled())
        with patch("general_test_gate_ui.QMessageBox.question",return_value=QMessageBox.StandardButton.No):
            self.card.release_button.click()
        self.assertTrue(gate.writes_held(self.case.manager))
        with patch("general_test_gate_ui.QMessageBox.question",return_value=QMessageBox.StandardButton.Yes):
            self.card.release_button.click();self.card.release_button.click()
        self.ui.until(lambda:self.card.worker is None)
        self.assertTrue(self.card.waiting_for_pull)
        self.case.finish_pull();self.card._poll_pull()
        self.ui.until(lambda:self.card.worker is None)
        self.assertFalse(gate.writes_held(self.case.manager))
        self.assertFalse(self.card.release_button.isEnabled())
        self.assertFalse(self.card.prepare_button.isEnabled())
        self.assertIn("해제했습니다",self.card.status.text())
        self.assertTrue(self.card.reprepare_button.isEnabled())

    def test_view_selection_changes_during_network_keeps_hold(self):
        original=self.case.client._answer
        def changed():
            result=original();self.card.pm.project_path=str(self.case.workspace);return result
        with patch.object(self.case.client,"_answer",side_effect=changed),patch(
                "general_test_gate_ui.QMessageBox.question",return_value=QMessageBox.StandardButton.Yes):
            self.card.release_button.click()
            self.ui.until(lambda:self.card.worker is None)
        self.assertTrue(gate.writes_held(self.case.manager))
        self.assertFalse(self.card.release_button.isEnabled())
