import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6.QtCore import QEvent
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtTest import QTest
from tests.qt_app import APP
from tests import test_project_archive as fixtures
from project_archive_dialog import ProjectArchiveDialog


class ProjectArchiveUITests(unittest.TestCase):
    setUp = fixtures.ProjectArchiveTests.setUp
    write = fixtures.ProjectArchiveTests.write

    def dialog(self):
        dialog = ProjectArchiveDialog(str(self.project))
        dialog.show()
        def dispose():
            self.assertIsNone(dialog.worker)
            dialog.deleteLater()
            APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.addCleanup(dispose)
        return dialog

    def finish(self, dialog):
        for _ in range(500):
            if dialog.worker is None:
                break
            QTest.qWait(10)
        self.assertIsNone(dialog.worker, "worker did not finish")

    def test_ui_export_verify_restore_uses_real_backend(self):
        dialog = self.dialog()
        with patch("project_archive_dialog.QFileDialog.getExistingDirectory", return_value=str(self.base)), patch("PyQt6.QtWidgets.QInputDialog.getText", return_value=(self.package.name, True)):
            dialog.create_button.click()
        self.finish(dialog)
        self.assertTrue(dialog.result[0], dialog.result)
        with patch("project_archive_dialog.QFileDialog.getExistingDirectory", return_value=str(self.package)):
            dialog.verify_button.click()
        self.finish(dialog)
        self.assertTrue(dialog.result[0], dialog.result)
        with patch("project_archive_dialog.QFileDialog.getExistingDirectory", side_effect=[str(self.package), str(self.base)]), patch("PyQt6.QtWidgets.QInputDialog.getText", return_value=(self.destination.name, True)):
            dialog.restore_button.click()
        self.finish(dialog)
        self.assertTrue(dialog.result[0], dialog.result)
        self.assertEqual(dialog.result[1]["status"], "openable")
        self.assertIn("독립 복원 완료", dialog.notice.text())
        self.assertEqual((Path(dialog.result[1]["project"]) / "AI/초안/001화_응답.md").read_bytes(), "AI 응답 이력".encode())

    def test_file_picker_cancellation_never_starts_work(self):
        dialog = self.dialog()
        with patch("project_archive_dialog.QFileDialog.getExistingDirectory", return_value=""):
            dialog.create_button.click()
            dialog.verify_button.click()
            dialog.restore_button.click()
        self.assertIsNone(dialog.worker)
        self.assertIsNone(dialog.result)
        self.assertFalse(self.package.exists())

    def test_existing_backup_is_reported_as_failure_and_preserved(self):
        dialog = self.dialog()
        self.package.mkdir()
        (self.package / "keep").write_bytes(b"prior backup")
        with patch.object(dialog, "_new_destination", return_value=str(self.package)):
            dialog.create_button.click()
        self.finish(dialog)
        self.assertFalse(dialog.result[0])
        self.assertIn("완료하지 못했습니다", dialog.notice.text())
        self.assertEqual((self.package / "keep").read_bytes(), b"prior backup")

    def test_worker_cannot_be_destroyed_by_close_or_escape(self):
        dialog = self.dialog()
        release = threading.Event()
        def slow():
            if not release.wait(5):
                raise RuntimeError("test timeout")
            return {}
        dialog._start(slow, (), "검증")
        try:
            event = QCloseEvent()
            dialog.closeEvent(event)
            self.assertFalse(event.isAccepted())
            dialog.reject()
            self.assertTrue(dialog.isVisible())
            self.assertFalse(dialog.restore_button.isEnabled())
        finally:
            release.set()
            self.finish(dialog)

    def test_project_picker_passes_selected_path_without_opening_project(self):
        from project_dialogs import ProjectSelectionDialog
        pm = SimpleNamespace(
            workspace_dir=str(self.workspace), global_config={},
            get_all_projects=lambda: [self.project.name],
        )
        picker = ProjectSelectionDialog(pm)
        with patch("project_archive_dialog.ProjectArchiveDialog") as factory:
            picker.btn_archive.click()
        factory.assert_called_once_with(str(self.project), picker)
        factory.return_value.exec.assert_called_once()
        self.assertIsNone(picker.selected_project)
        picker.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)


if __name__ == "__main__":
    unittest.main()
