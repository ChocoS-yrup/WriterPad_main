"""Offscreen Qt, fake network, real bound manager and preparation worker."""
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from PyQt6.QtWidgets import QMessageBox
from tests.qt_app import APP
from tests import test_general_test_gate as core
from general_test_gate_ui import GeneralTestGateCard


class GeneralTestGateUITests(unittest.TestCase):
    def setUp(self):
        self.case = core.GeneralTestGateTests()
        self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.card = GeneralTestGateCard(SimpleNamespace(project_path=str(self.case.root)))
        self.card.bind_manager(self.case.manager)
        self.addCleanup(self.dispose)

    def dispose(self):
        if self.card.worker is not None:
            self.card.worker.wait(5000)
            APP.processEvents()
        self.card.poll.stop()
        self.card.deleteLater()
        APP.processEvents()

    def until(self, predicate):
        deadline = time.perf_counter() + 5
        while not predicate() and time.perf_counter() < deadline:
            APP.processEvents()
        self.assertTrue(predicate(), self.card.status.text())

    def prepare(self):
        self.card.prepare_button.click()
        self.until(lambda:self.card.worker is None)
        self.assertTrue(self.card.waiting_for_pull, self.card.status.text())
        self.case.finish_pull()
        self.card._poll_pull()
        self.assertIsNotNone(self.card.report, self.card.status.text())

    def test_default_hidden_requires_bound_manager_and_matching_view(self):
        self.assertTrue(self.card.isHidden())
        self.card.pm.project_path = str(self.case.workspace)
        self.card.prepare_button.click()
        self.assertIsNone(self.card.report)
        self.assertEqual(self.case.client.calls, [])

    def test_prepare_cancel_confirm_and_export_preserve_hold(self):
        self.assertFalse(self.card.activate_button.isEnabled())
        self.prepare()
        self.assertTrue(self.card.activate_button.isEnabled())
        with patch("general_test_gate_ui.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            self.card.activate_button.click()
        self.assertFalse(self.case.manager.contract_path_enabled())
        with patch("general_test_gate_ui.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.card.activate_button.click()
        self.until(lambda:self.card.worker is None)
        self.assertTrue(self.card.report["gate_open"])
        self.assertTrue(self.card.report["outbound_send_held"])
        self.assertFalse(self.card.activate_button.isEnabled())
        target = self.case.workspace / "synthetic-observation.json"
        with patch("general_test_gate_ui.QFileDialog.getSaveFileName", return_value=(str(target), "JSON")):
            self.card.export_button.click()
            original = target.read_bytes()
            report = json.loads(original)
            self.assertTrue(report["c9"]["all_conditions_met"])
            self.assertNotIn("synthetic.invalid", str(report))
            self.assertNotIn(str(self.case.root), str(report))
            self.assertNotIn("header.", str(report))
            self.card.export_button.click()
            self.assertEqual(target.read_bytes(), original)

    def test_changed_selection_while_confirmation_open_cannot_activate(self):
        self.prepare()
        def change(*args):
            self.card.pm.project_path = str(self.case.workspace)
            return QMessageBox.StandardButton.Yes
        with patch("general_test_gate_ui.QMessageBox.question", side_effect=change):
            self.card.activate_button.click()
        self.assertFalse(self.case.manager.contract_path_enabled())
        self.assertIsNone(self.card.report)

    def test_failed_network_clears_buttons_retains_hold(self):
        self.case.client.reply = TimeoutError("synthetic secret error")
        self.card.prepare_button.click()
        self.until(lambda:self.card.worker is None)
        self.assertFalse(self.card.activate_button.isEnabled())
        self.assertFalse(self.card.export_button.isEnabled())
        self.assertNotIn("synthetic secret", self.card.status.text())
        self.assertTrue(core.gate.writes_held(self.case.manager))
