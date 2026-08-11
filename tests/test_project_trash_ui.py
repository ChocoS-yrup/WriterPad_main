import os
import threading
import time
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox

from project_dialogs import ProjectManagementDialog, ProjectTrashDialog
from project_trash import TrashedProject


class _ProjectManager:
    def __init__(self, projects):
        self.projects = list(projects)
        self.delete_calls = []
        self.saved_orders = []

    def get_all_projects(self):
        return list(self.projects)

    def rename_project(self, old_name, new_name):
        index = self.projects.index(old_name)
        self.projects[index] = new_name
        return True, ""

    def save_project_order(self, ordered_projects):
        self.projects = list(ordered_projects)
        self.saved_orders.append(list(ordered_projects))

    def delete_project(self, project_name):
        self.delete_calls.append(project_name)
        raise AssertionError("관리 UI는 즉시 삭제 경로를 호출하면 안 됩니다.")


class _TrashService:
    def __init__(self, pm, entries=None):
        self.pm = pm
        self.entries = list(entries or [])
        self.calls = []
        self.thread_ids = []
        self.last_server_error = ""

    def list_projects(self):
        self.calls.append(("list", None))
        self.thread_ids.append(threading.get_ident())
        return list(self.entries)

    def trash_project(self, project_name):
        self.calls.append(("trash", project_name))
        self.thread_ids.append(threading.get_ident())
        self.pm.projects.remove(project_name)
        entry = TrashedProject(
            entry_id=str(uuid.uuid4()),
            project_name=project_name,
            trashed_at="2026-07-28T10:00:00+00:00",
            local_available=True,
        )
        self.entries.append(entry)
        return entry

    def restore_project(self, entry):
        self.calls.append(("restore", entry.project_name))
        self.thread_ids.append(threading.get_ident())
        self.entries.remove(entry)
        self.pm.projects.append(entry.project_name)
        return entry.project_name

    def purge_project(self, entry):
        self.calls.append(("purge", entry.project_name))
        self.thread_ids.append(threading.get_ident())
        self.entries.remove(entry)
        return True


class _BlockingTrashService(_TrashService):
    def __init__(self, pm):
        super().__init__(pm)
        self.started = threading.Event()
        self.release = threading.Event()

    def trash_project(self, project_name):
        self.started.set()
        self.release.wait(timeout=5)
        return super().trash_project(project_name)


class ProjectTrashUiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _process_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        self.app.processEvents()
        return predicate()

    def test_management_moves_to_trash_without_immediate_delete(self):
        pm = _ProjectManager(["보존할 작품"])
        service = _TrashService(pm)
        dialog = ProjectManagementDialog(pm, trash_service=service)
        self.addCleanup(dialog.close)
        main_thread_id = threading.get_ident()

        with patch("project_dialogs.QMessageBox.information"):
            self.assertTrue(dialog._start_trash("보존할 작품"))
            worker = dialog._worker
            self.assertTrue(worker.wait(2000))
            self.assertTrue(self._process_until(lambda: not dialog.is_busy))

        self.assertEqual(pm.delete_calls, [])
        self.assertEqual(service.calls, [("trash", "보존할 작품")])
        self.assertNotEqual(service.thread_ids[0], main_thread_id)
        self.assertEqual(dialog.list_widget.count(), 0)
        self.assertIn("휴지통으로 이동했습니다", dialog.status_label.text())

    def test_management_blocks_duplicate_actions_and_waits_to_close(self):
        pm = _ProjectManager(["느린 작품"])
        service = _BlockingTrashService(pm)
        dialog = ProjectManagementDialog(pm, trash_service=service)
        self.addCleanup(dialog.close)

        self.assertTrue(dialog._start_trash("느린 작품"))
        self.assertTrue(service.started.wait(timeout=1))
        self.assertTrue(dialog.is_busy)
        self.assertFalse(dialog.rename_button.isEnabled())
        self.assertFalse(dialog.trash_button.isEnabled())
        self.assertFalse(dialog.open_trash_button.isEnabled())
        self.assertFalse(dialog._start_trash("느린 작품"))

        dialog.close()
        self.assertTrue(dialog.is_busy)
        self.assertIn("안전하게 끝나면", dialog.status_label.text())

        worker = dialog._worker
        with patch("project_dialogs.QMessageBox.information"):
            service.release.set()
            self.assertTrue(worker.wait(2000))
            self.assertTrue(self._process_until(lambda: not dialog.is_busy))
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)

    def test_management_arrow_buttons_reorder_and_persist_projects(self):
        pm = _ProjectManager(["첫 작품", "둘째 작품", "셋째 작품"])
        dialog = ProjectManagementDialog(
            pm, trash_service=_TrashService(pm)
        )
        self.addCleanup(dialog.close)

        dialog.list_widget.setCurrentRow(1)
        self.assertTrue(dialog.move_up_button.isEnabled())
        self.assertTrue(dialog.move_down_button.isEnabled())

        dialog.move_up_button.click()
        self.assertEqual(
            pm.projects, ["둘째 작품", "첫 작품", "셋째 작품"]
        )
        self.assertEqual(dialog.list_widget.currentRow(), 0)
        self.assertFalse(dialog.move_up_button.isEnabled())
        self.assertTrue(dialog.move_down_button.isEnabled())

        dialog.move_down_button.click()
        self.assertEqual(
            pm.projects, ["첫 작품", "둘째 작품", "셋째 작품"]
        )
        self.assertEqual(
            pm.saved_orders,
            [
                ["둘째 작품", "첫 작품", "셋째 작품"],
                ["첫 작품", "둘째 작품", "셋째 작품"],
            ],
        )

    def test_management_drag_reorder_persists_projects(self):
        pm = _ProjectManager(["첫 작품", "둘째 작품", "셋째 작품"])
        dialog = ProjectManagementDialog(
            pm, trash_service=_TrashService(pm)
        )
        self.addCleanup(dialog.close)

        self.assertTrue(
            dialog.list_widget.model().moveRow(
                dialog.list_widget.rootIndex(),
                0,
                dialog.list_widget.rootIndex(),
                3,
            )
        )
        self.app.processEvents()

        self.assertEqual(
            dialog._current_project_order(),
            ["둘째 작품", "셋째 작품", "첫 작품"],
        )
        self.assertEqual(pm.projects, ["둘째 작품", "셋째 작품", "첫 작품"])

    def test_trash_list_has_readable_location_time_and_masked_id(self):
        project_id = str(uuid.uuid4())
        entry = TrashedProject(
            entry_id=project_id,
            project_name="서버와 로컬 작품",
            project_id=project_id,
            trashed_at="2026-07-28T10:11:12+00:00",
            local_available=True,
            server_available=True,
        )
        pm = _ProjectManager([])
        dialog = ProjectTrashDialog(
            _TrashService(pm, [entry]), auto_refresh=False
        )
        self.addCleanup(dialog.close)

        dialog._populate_entries([entry])

        item = dialog.list_widget.item(0)
        accessible = item.data(Qt.ItemDataRole.AccessibleTextRole)
        self.assertIn("서버 · 로컬", accessible)
        self.assertIn("삭제 시각 2026-07-28", accessible)
        row = dialog.list_widget.itemWidget(item)
        labels = [label.text() for label in row.findChildren(QLabel)]
        self.assertIn("서버와 로컬 작품", labels)
        self.assertIn("서버 · 로컬", labels)
        self.assertTrue(any(project_id[:8] in text for text in labels))
        self.assertFalse(any(project_id in text for text in labels))
        self.assertTrue(dialog.restore_button.isEnabled())
        self.assertTrue(dialog.purge_button.isEnabled())

    def test_restore_refreshes_trash_and_returns_project_to_manager(self):
        entry = TrashedProject(
            entry_id=str(uuid.uuid4()),
            project_name="복원할 작품",
            trashed_at="2026-07-28T10:00:00+00:00",
            local_available=True,
        )
        pm = _ProjectManager([])
        service = _TrashService(pm, [entry])
        dialog = ProjectTrashDialog(service, auto_refresh=False)
        self.addCleanup(dialog.close)
        dialog._populate_entries([entry])

        self.assertTrue(dialog._start_restore(entry))
        self.assertTrue(self._process_until(
            lambda: not dialog.is_busy and dialog.list_widget.count() == 0
        ))

        self.assertEqual(
            service.calls,
            [("restore", "복원할 작품"), ("list", None)],
        )
        self.assertEqual(pm.projects, ["복원할 작품"])
        self.assertIn("복원했습니다", dialog.status_label.text())

    def test_permanent_delete_requires_exact_project_name(self):
        entry = TrashedProject(
            entry_id=str(uuid.uuid4()),
            project_name="정확한 작품명",
            trashed_at="2026-07-28T10:00:00+00:00",
            local_available=True,
        )
        pm = _ProjectManager([])
        service = _TrashService(pm, [entry])
        dialog = ProjectTrashDialog(service, auto_refresh=False)
        self.addCleanup(dialog.close)
        dialog._populate_entries([entry])

        with (
            patch(
                "project_dialogs.QInputDialog.getText",
                return_value=("정확하지 않은 작품명", True),
            ),
            patch("project_dialogs.QMessageBox.warning") as warning,
        ):
            self.assertFalse(dialog.purge_selected())
        warning.assert_called_once()
        self.assertFalse(any(call[0] == "purge" for call in service.calls))

        with patch(
            "project_dialogs.QInputDialog.getText",
            return_value=("정확한 작품명", True),
        ):
            self.assertTrue(dialog.purge_selected())
            self.assertTrue(self._process_until(
                lambda: not dialog.is_busy
                and dialog.list_widget.count() == 0
            ))
        self.assertIn(("purge", "정확한 작품명"), service.calls)
        self.assertIn("영구 삭제했습니다", dialog.status_label.text())

    def test_first_delete_confirmation_is_recoverable_trash(self):
        pm = _ProjectManager(["확인할 작품"])
        service = _TrashService(pm)
        dialog = ProjectManagementDialog(pm, trash_service=service)
        self.addCleanup(dialog.close)
        dialog.list_widget.setCurrentRow(0)

        with patch(
            "project_dialogs.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ) as question:
            self.assertFalse(dialog.on_delete())

        message = question.call_args.args[2]
        self.assertIn("휴지통", message)
        self.assertIn("복원", message)
        self.assertNotIn("복구할 수 없습니다", message)
        self.assertEqual(service.calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
