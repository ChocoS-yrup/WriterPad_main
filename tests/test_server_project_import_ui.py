import os
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox

from project_dialogs import ProjectSelectionDialog, ServerProjectImportDialog
from project_trash import TrashedProject
from server_project_import import (
    LOCAL_MISSING,
    LOCAL_OTHER,
    ServerProject,
)
from sync_manager import load_or_create_device_id


class _BlockingCatalogService:
    def __init__(self, projects):
        self.projects = projects
        self.calls = 0
        self.started = threading.Event()
        self.release = threading.Event()

    def list_projects(self):
        self.calls += 1
        self.started.set()
        self.release.wait(timeout=5)
        return self.projects


class _BlockingImportService:
    def __init__(self, local_project_name):
        self.local_project_name = local_project_name
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()

    def import_project(self, project_id, server_name, local_project_name):
        self.calls.append((project_id, server_name, local_project_name))
        self.started.set()
        self.release.wait(timeout=5)
        return SimpleNamespace(local_project_name=self.local_project_name)


class _ServerTrashService:
    def __init__(self):
        self.calls = []

    def trash_server_project(self, project_id, project_name):
        self.calls.append((project_id, project_name))
        return TrashedProject(
            entry_id=project_id,
            project_id=project_id,
            project_name=project_name,
            trashed_at="2026-07-29T10:00:00+00:00",
            server_available=True,
        )


def _project(name="서버 작품", imported=False, incomplete=False):
    return ServerProject(
        project_id=str(uuid.uuid4()),
        name=name,
        created_at="2026-07-27T10:00:00Z",
        updated_at="2026-07-28T10:00:00Z",
        already_imported=imported,
        local_project_name="재개 로컬명" if incomplete else "",
        import_incomplete=incomplete,
    )


class ServerProjectImportUiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.pm = SimpleNamespace(
            workspace_dir=str(Path(self.temp_dir.name, "작품목록"))
        )
        Path(self.pm.workspace_dir).mkdir()
        self.sync_manager = SimpleNamespace(supabase=object())

    def _dialog(self):
        dialog = ServerProjectImportDialog(
            self.pm,
            sync_manager=self.sync_manager,
            store=object(),
            device_id=str(uuid.uuid4()),
            auto_refresh=False,
        )
        self.addCleanup(dialog.close)
        return dialog

    def _process_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        self.app.processEvents()
        return predicate()

    def test_catalog_rows_show_masked_id_state_and_updated_time(self):
        dialog = self._dialog()
        available = _project("가져올 작품")
        imported = _project("이미 가져온 작품", imported=True)
        incomplete = _project("재개할 작품", incomplete=True)

        dialog._populate_projects([available, imported, incomplete])

        texts = [
            dialog.list_widget.item(row).data(
                Qt.ItemDataRole.AccessibleTextRole
            )
            for row in range(dialog.list_widget.count())
        ]
        self.assertIn(available.masked_project_id, texts[0])
        self.assertNotIn(available.project_id, texts[0])
        self.assertIn("최근 수정 2026-07-28", texts[0])
        self.assertNotIn("T10:00:00Z", texts[0])
        self.assertIn("[가져옴]", texts[1])
        self.assertTrue(
            dialog.list_widget.item(1).flags()
            & Qt.ItemFlag.ItemIsSelectable
        )
        self.assertIn("[가져오기 재개]", texts[2])
        row_widget = dialog.list_widget.itemWidget(
            dialog.list_widget.item(0)
        )
        labels = [label.text() for label in row_widget.findChildren(QLabel)]
        self.assertIn("가져올 작품", labels)
        self.assertIn("가져올 수 있음", labels)
        self.assertTrue(any("최근 수정" in text for text in labels))
        self.assertGreaterEqual(
            dialog.list_widget.item(0).sizeHint().height(), 68
        )
        self.assertEqual(dialog.list_widget.item(0).text(), "")

    def test_other_environment_and_missing_folder_have_safe_actions(self):
        dialog = self._dialog()
        other_path = Path(
            self.temp_dir.name, "다른 실행환경", "다른 작품"
        )
        other_path.mkdir(parents=True)
        other = ServerProject(
            project_id=str(uuid.uuid4()),
            name="다른 환경 작품",
            created_at="2026-07-27T10:00:00Z",
            updated_at="2026-07-28T10:00:00Z",
            already_imported=True,
            local_project_name="다른 작품",
            local_state=LOCAL_OTHER,
            local_path=str(other_path),
        )
        missing = ServerProject(
            project_id=str(uuid.uuid4()),
            name="누락 작품",
            created_at="2026-07-27T10:00:00Z",
            updated_at="2026-07-28T10:00:00Z",
            already_imported=False,
            local_project_name="누락 작품",
            local_state=LOCAL_MISSING,
            local_path=str(Path(self.temp_dir.name, "없는 작품")),
        )

        dialog._populate_projects([other, missing])

        first_accessible = dialog.list_widget.item(0).data(
            Qt.ItemDataRole.AccessibleTextRole
        )
        self.assertIn("다른 실행환경에 있음", first_accessible)
        self.assertTrue(dialog.open_folder_button.isEnabled())
        self.assertFalse(dialog.import_button.isEnabled())
        with patch(
            "project_dialogs.QDesktopServices.openUrl",
            return_value=True,
        ) as open_url:
            self.assertTrue(dialog.open_selected_folder())
        open_url.assert_called_once()

        dialog.list_widget.setCurrentRow(1)
        second_accessible = dialog.list_widget.item(1).data(
            Qt.ItemDataRole.AccessibleTextRole
        )
        self.assertIn("로컬 파일 없음", second_accessible)
        self.assertFalse(dialog.open_folder_button.isEnabled())
        self.assertTrue(dialog.import_button.isEnabled())
        self.assertIn("다시 가져오기", dialog.import_button.text())
        self.assertFalse(dialog.local_name_input.isEnabled())

    def test_any_catalog_project_can_move_to_server_trash(self):
        dialog = self._dialog()
        project = _project("서버에만 있는 테스트 작품")
        dialog._populate_projects([project])
        service = _ServerTrashService()
        dialog.trash_service = service
        dialog.catalog_service = SimpleNamespace(list_projects=lambda: [])

        with patch(
            "project_dialogs.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.assertTrue(dialog.trash_selected_server_project())
        self.assertTrue(self._process_until(
            lambda: not dialog.is_busy and dialog.list_widget.count() == 0
        ))

        self.assertEqual(
            service.calls, [(project.project_id, project.name)]
        )

    def test_refresh_worker_is_async_and_blocks_duplicate_actions(self):
        dialog = self._dialog()
        service = _BlockingCatalogService([_project()])
        dialog.catalog_service = service

        self.assertTrue(dialog.refresh_server_projects())
        self.assertTrue(service.started.wait(timeout=1))
        self.assertTrue(dialog.is_busy)
        self.assertFalse(dialog.refresh_button.isEnabled())
        self.assertFalse(dialog.import_button.isEnabled())
        self.assertFalse(dialog.refresh_server_projects())
        self.assertEqual(service.calls, 1)

        worker = dialog._worker
        service.release.set()
        self.assertTrue(worker.wait(2000))
        self.assertTrue(self._process_until(lambda: not dialog.is_busy))
        self.assertEqual(dialog.list_widget.count(), 1)
        self.assertTrue(dialog.refresh_button.isEnabled())

    def test_import_worker_uses_selected_local_name_and_accepts_after_success(self):
        dialog = self._dialog()
        project = _project("원래 서버 작품명")
        dialog._populate_projects([project])
        dialog.local_name_input.setText("충돌을 피한 로컬 작품명")
        service = _BlockingImportService("충돌을 피한 로컬 작품명")
        dialog.import_service = service

        self.assertTrue(dialog.import_selected_project())
        self.assertTrue(service.started.wait(timeout=1))
        self.assertTrue(dialog.is_busy)
        self.assertFalse(dialog.import_selected_project())
        self.assertEqual(len(service.calls), 1)

        worker = dialog._worker
        service.release.set()
        self.assertTrue(worker.wait(2000))
        self.assertTrue(self._process_until(lambda: not dialog.is_busy))
        self.assertEqual(
            service.calls[0],
            (
                project.project_id,
                "원래 서버 작품명",
                "충돌을 피한 로컬 작품명",
            ),
        )
        self.assertEqual(
            dialog.imported_project_name, "충돌을 피한 로컬 작품명"
        )
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_empty_server_project_explains_that_documents_will_sync_later(self):
        dialog = self._dialog()
        payload = SimpleNamespace(
            local_project_name="빈 서버 작품",
            document_count=0,
        )

        with patch.object(QMessageBox, "information") as information:
            dialog._finish_import(True, payload)

        self.assertIn("서버에 동기화된 문서가 0개", dialog.status_label.text())
        self.assertTrue(dialog._accept_after_worker)
        information.assert_called_once()
        title = information.call_args.args[1]
        message = information.call_args.args[2]
        self.assertEqual(title, "서버 문서 없음")
        self.assertIn("iPad에서 문서 동기화가 완료되면", message)
        self.assertIn("Windows가 자동으로 내려받습니다", message)

    def test_cancel_waits_for_worker_instead_of_destroying_running_thread(self):
        dialog = self._dialog()
        service = _BlockingCatalogService([])
        dialog.catalog_service = service
        dialog.refresh_server_projects()
        self.assertTrue(service.started.wait(timeout=1))

        dialog._request_close()

        self.assertTrue(dialog.is_busy)
        self.assertFalse(dialog.cancel_button.isEnabled())
        self.assertIn("안전하게 끝나면", dialog.status_label.text())

        worker = dialog._worker
        service.release.set()
        self.assertTrue(worker.wait(2000))
        self.assertTrue(self._process_until(lambda: not dialog.is_busy))
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)

    def test_device_id_is_stable_and_invalid_file_is_replaced(self):
        data_dir = Path(self.temp_dir.name, "app-data")
        with patch("runtime_profile.app_data_dir", return_value=str(data_dir)):
            first = load_or_create_device_id()
            second = load_or_create_device_id()
            self.assertEqual(first, second)
            uuid.UUID(first)

            Path(data_dir, ".device_id").write_text(
                "invalid-device-id", encoding="utf-8"
            )
            repaired = load_or_create_device_id()

        uuid.UUID(repaired)
        self.assertNotEqual(repaired, "invalid-device-id")

    def test_selection_dialog_entry_refreshes_and_selects_imported_project(self):
        class _ProjectManager:
            def __init__(self, workspace_dir):
                self.workspace_dir = workspace_dir
                self.global_config = {}
                self.projects = []

            def get_all_projects(self):
                return list(self.projects)

            def save_project_order(self, ordered):
                self.projects = list(ordered)

        manager = _ProjectManager(self.pm.workspace_dir)
        selection = ProjectSelectionDialog(manager)
        self.addCleanup(selection.close)

        class _CompletedImportDialog:
            imported_project_name = "새로 가져온 작품"

            def __init__(self, pm, parent):
                manager.projects = ["새로 가져온 작품"]

            def exec(self):
                return QDialog.DialogCode.Accepted

        with patch(
            "project_dialogs.ServerProjectImportDialog",
            _CompletedImportDialog,
        ):
            selection.btn_server_import.click()

        self.assertEqual(selection.list_widget.count(), 1)
        self.assertEqual(
            selection.list_widget.currentItem().text(), "새로 가져온 작품"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
