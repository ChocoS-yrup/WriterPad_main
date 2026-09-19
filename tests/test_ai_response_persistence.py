"""New response persistence boundaries; synthetic project and fake workers only."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_assistant_safety as safety
from tests.qt_app import APP


class AIResponsePersistenceTests(unittest.TestCase):
    setUp = safety.AssistantSafetyTests.setUp
    dispose_widget = safety.AssistantSafetyTests.dispose_widget
    start_request = safety.AssistantSafetyTests.start_request

    def test_cost_failure_still_displays_and_saves_response(self):
        self.widget.show()
        worker = self.start_request()
        with patch.object(self.widget.pm, "log_api_cost", side_effect=OSError("full")):
            worker.complete("새 응답")
        self.assertEqual(self.widget.ai_panel.current_panel.result_editor.toPlainText(), "새 응답")
        saved = list((Path(self.widget.pm.project_path) / "AI/초안").glob("*.md"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].read_text(encoding="utf-8"), "새 응답")
        self.assertTrue(self.widget._ai_recovery_dialogs[0].saved)

    def test_history_failure_retains_original_context_copy_and_retry_export(self):
        self.widget.show()
        self.widget.pm.save_chapter_text("초안", 1, "기존 원고")
        worker = self.start_request()
        self.widget.current_chapter = 8  # queued result still belongs to chapter 1
        with patch.object(self.widget.pm, "save_ai_response", side_effect=OSError("full")):
            worker.complete("1화 생성 응답")
        recovery = self.widget._ai_recovery_dialogs[0]
        self.assertEqual(recovery.request.chapter, 1)
        self.assertEqual(self.widget.pm.load_chapter_text("초안", 1), "기존 원고")
        recovery.copy_button.click()
        self.assertEqual(APP.clipboard().text(), "1화 생성 응답")
        target = Path(self.temp.name) / "사본.md"
        with patch("ai_response_recovery.QFileDialog.getSaveFileName", return_value=("", "")):
            recovery.save_button.click()
        self.assertFalse(recovery.saved)
        target.write_text("기존 사본", encoding="utf-8")
        with patch("ai_response_recovery.QFileDialog.getSaveFileName", return_value=(str(target), "")), patch("ai_response_recovery.QMessageBox.warning"):
            recovery.save_button.click()
        self.assertEqual(target.read_text(encoding="utf-8"), "기존 사본")
        self.assertFalse(recovery.saved)
        target = target.with_name("새 사본.md")
        with patch("ai_response_recovery.QFileDialog.getSaveFileName", return_value=(str(target), "")):
            recovery.save_button.click()
        self.assertEqual(target.read_text(encoding="utf-8"), "1화 생성 응답")
        self.assertTrue(recovery.saved)

    def test_directory_and_write_failures_are_reported_with_response_retained(self):
        for location in ("project_manager.os.makedirs", "project_manager.os.replace"):
            worker = self.start_request()
            with patch(location, side_effect=OSError("denied")):
                worker.complete("보존할 결과")
            dialog = self.widget._ai_recovery_dialogs[-1]
            self.assertFalse(dialog.saved)
            self.assertEqual(dialog.editor.toPlainText(), "보존할 결과")
            dialog.discarded = True

    def test_cost_replace_failure_preserves_prior_history(self):
        pm = self.widget.pm
        path = Path(pm.project_path) / "cost_history.json"
        before = json.dumps([{"prior": True}]).encode()
        path.write_bytes(before)
        with patch("project_manager.os.replace", side_effect=OSError("full")):
            with self.assertRaises(OSError):
                pm.log_api_cost("초안", "fake", 1, 1)
        self.assertEqual(path.read_bytes(), before)
        path.write_bytes(b"broken json")
        with self.assertRaises(ValueError):
            pm.log_api_cost("초안", "fake", 1, 1)
        self.assertEqual(path.read_bytes(), b"broken json")

    def test_unsaved_recovery_prevents_shutdown_and_survives_new_request(self):
        from PyQt6.QtWidgets import QMessageBox
        worker = self.start_request()
        with patch.object(self.widget.pm, "save_ai_response", side_effect=OSError("full")):
            worker.complete("이전 응답")
        dialog = self.widget._ai_recovery_dialogs[0]
        with patch("ai_response_recovery.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            self.assertFalse(self.widget.check_unsaved_changes(is_final_quit=True))
            dialog.reject()
        new = self.start_request()
        new.complete("새 응답")
        self.assertEqual(dialog.response_text, "이전 응답")
        dialog.discarded = True

    def test_close_checks_recovery_before_any_failing_settings_write(self):
        from PyQt6.QtGui import QCloseEvent
        from PyQt6.QtWidgets import QMessageBox
        from main import MainWindow
        from types import SimpleNamespace
        worker = self.start_request()
        with patch.object(self.widget.pm, "save_ai_response", side_effect=OSError("full")):
            worker.complete("닫기 전 보존할 응답")
        with patch("ai_response_recovery.QMessageBox.question", return_value=QMessageBox.StandardButton.No), patch.object(self.widget.pm, "set_project_setting", side_effect=AssertionError("must not write")), patch.object(self.widget.pm, "save_global_config", side_effect=AssertionError("must not write")):
            event = QCloseEvent()
            MainWindow.closeEvent(SimpleNamespace(assistant_mode=self.widget), event)
            self.assertFalse(event.isAccepted())
            event = QCloseEvent()
            self.widget.closeEvent(event)
            self.assertFalse(event.isAccepted())
        self.widget._ai_recovery_dialogs[0].discarded = True


if __name__ == "__main__":
    unittest.main()
