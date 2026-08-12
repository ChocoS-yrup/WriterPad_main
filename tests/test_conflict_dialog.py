"""In-app conflict resolution: pick a version, apply it, clear the conflict."""

import os
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMessageBox

from conflict_dialog import (
    LOCAL_LABEL,
    MERGED_LABEL,
    REMOTE_LABEL,
    ConflictResolutionDialog,
    build_conflict_view,
    has_conflict_markers,
)
from mode_writing import WritingModeWidget
from project_manager_writing import WritingProjectManager
from sync_manager import SyncManager
from sync_v2_store import SyncV2Store


MERGED_BODY = "<<<<<<< 내 로컬 편집본\n내 문장\n=======\n서버 문장\n>>>>>>> 서버 최신본\n"


def _view(path="메인/원고/1권/002화.txt"):
    return {
        "path": path,
        "merged": MERGED_BODY,
        "local": "내 문장",
        "remote": "서버 문장",
        "report": "차이점 비교 본문",
    }


class ConflictMarkerTestCase(unittest.TestCase):
    def test_marker_detection(self):
        self.assertTrue(has_conflict_markers(MERGED_BODY))
        self.assertFalse(has_conflict_markers("정리된 원고"))
        self.assertFalse(has_conflict_markers(""))


class ConflictViewSourceTestCase(unittest.TestCase):
    def test_hand_edited_conflict_file_wins_over_the_stored_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir, "집필모드")
            (root / "백업" / "충돌").mkdir(parents=True)
            (root / "백업" / "충돌" / "002화 (내 로컬 편집본 2026-08-12 101010).txt").write_text(
                "손으로 고친 로컬본", encoding="utf-8"
            )
            wpm = SimpleNamespace(writing_root_path=str(root))

            view = build_conflict_view(
                wpm,
                {
                    "local_path": "메인/원고/1권/002화.txt",
                    "conflict_merged": MERGED_BODY,
                    "conflict_local": "저장된 로컬본",
                    "conflict_remote": "서버 문장",
                    "conflict_base": "공통 조상",
                },
                report_builder=lambda *_: "생성된 비교",
            )

            self.assertEqual(view["local"], "손으로 고친 로컬본")
            self.assertEqual(view["remote"], "서버 문장")
            self.assertEqual(view["report"], "생성된 비교")

    def test_missing_folder_falls_back_to_stored_snapshots(self):
        view = build_conflict_view(
            SimpleNamespace(writing_root_path="/존재하지 않는 경로"),
            {
                "local_path": "메인/원고/1권/002화.txt",
                "conflict_merged": MERGED_BODY,
                "conflict_local": "저장된 로컬본",
                "conflict_remote": "서버 문장",
                "conflict_base": "공통 조상",
            },
            report_builder=lambda *_: "생성된 비교",
        )

        self.assertEqual(view["local"], "저장된 로컬본")
        self.assertEqual(view["merged"], MERGED_BODY)


class ConflictDialogTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self, views=None):
        dialog = ConflictResolutionDialog(views or [_view()])
        self.addCleanup(dialog.deleteLater)
        return dialog

    def test_three_tabs_carry_the_three_versions(self):
        dialog = self._dialog()

        labels = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
        self.assertEqual(labels, [MERGED_LABEL, LOCAL_LABEL, REMOTE_LABEL])

        dialog.tabs.setCurrentIndex(1)
        self.assertEqual(dialog.selected_content(), "내 문장")
        dialog.tabs.setCurrentIndex(2)
        self.assertEqual(dialog.selected_content(), "서버 문장")
        dialog.tabs.setCurrentIndex(0)
        self.assertEqual(dialog.selected_content(), MERGED_BODY)

    def test_merge_tab_shows_the_comparison_report(self):
        dialog = self._dialog()

        self.assertEqual(dialog._report_view.toPlainText(), "차이점 비교 본문")
        self.assertTrue(dialog._report_view.isReadOnly())

    def test_every_tab_is_editable(self):
        dialog = self._dialog()

        for index in range(dialog.tabs.count()):
            dialog.tabs.setCurrentIndex(index)
            editor = dialog._editors[index]
            self.assertFalse(editor.isReadOnly())

        dialog.tabs.setCurrentIndex(0)
        dialog._merged_editor.setPlainText("마커를 지운 원고")
        self.assertEqual(dialog.selected_content(), "마커를 지운 원고")

    def test_remaining_markers_are_warned_about_before_applying(self):
        dialog = self._dialog()
        dialog.tabs.setCurrentIndex(0)

        with patch.object(
            QMessageBox, "warning", return_value=QMessageBox.StandardButton.No
        ) as warning:
            dialog._confirm()
        warning.assert_called_once()
        self.assertNotEqual(dialog.result(), int(dialog.DialogCode.Accepted))

    def test_cleaned_merge_applies_without_a_warning(self):
        dialog = self._dialog()
        dialog.tabs.setCurrentIndex(0)
        dialog._merged_editor.setPlainText("마커를 지운 원고")

        with patch.object(QMessageBox, "warning") as warning:
            dialog._confirm()
        warning.assert_not_called()
        self.assertEqual(dialog.selected_content(), "마커를 지운 원고")

    def test_empty_version_is_flagged_and_confirmed_before_applying(self):
        view = _view()
        view["local"] = ""
        dialog = self._dialog([view])
        dialog.tabs.setCurrentIndex(1)

        self.assertIn("빈 문서", dialog.status.text())

        with patch.object(
            QMessageBox, "warning", return_value=QMessageBox.StandardButton.No
        ) as warning:
            dialog._confirm()
        warning.assert_called_once()
        self.assertNotEqual(dialog.result(), int(dialog.DialogCode.Accepted))

    def test_empty_version_can_still_be_applied_deliberately(self):
        view = _view()
        view["local"] = ""
        dialog = self._dialog([view])
        dialog.tabs.setCurrentIndex(1)

        with patch.object(
            QMessageBox, "warning", return_value=QMessageBox.StandardButton.Yes
        ):
            dialog._confirm()
        self.assertEqual(dialog.result(), int(dialog.DialogCode.Accepted))

    def test_document_selector_appears_only_for_multiple_conflicts(self):
        single = self._dialog()
        self.assertFalse(single.document_selector.isVisibleTo(single))

        many = self._dialog([_view("메인/원고/1권/002화.txt"), _view("메인/원고/1권/003화.txt")])
        self.assertTrue(many.document_selector.isVisibleTo(many))
        many.document_selector.setCurrentIndex(1)
        self.assertEqual(
            many.selected_document_path(), "메인/원고/1권/003화.txt"
        )


class ConflictApplyTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_applying_a_choice_writes_the_document_and_queues_a_save(self):
        wpm = MagicMock()
        wpm.write_text_file.return_value = True
        sync_manager = MagicMock()
        target = SimpleNamespace(
            wpm=wpm,
            sync_manager=sync_manager,
            pm=SimpleNamespace(current_project="작품"),
            current_loaded_file_left="메인/원고/1권/002화.txt",
            current_loaded_file_right=None,
            is_dirty_left=True,
            is_dirty_right=False,
            left_editor=None,
            right_editor=None,
            lbl_current_doc=MagicMock(),
            lbl_r_doc=MagicMock(),
            on_sync_finished=MagicMock(),
            last_snapshot_contents={},
        )

        with patch.object(WritingModeWidget, "_accept_persisted_snapshot"):
            applied = WritingModeWidget.apply_conflict_choice(
                target, "메인/원고/1권/002화.txt", "선택한 원고", REMOTE_LABEL
            )

        self.assertTrue(applied)
        wpm.write_text_file.assert_called_once_with(
            "메인/원고/1권/002화.txt", "선택한 원고"
        )
        sync_manager.upload_content_async.assert_called_once()
        args = sync_manager.upload_content_async.call_args.args
        self.assertEqual(args[2], "메인/원고/1권/002화.txt")
        self.assertEqual(args[3], "선택한 원고")

    def test_failed_write_does_not_queue_a_save(self):
        wpm = MagicMock()
        wpm.write_text_file.return_value = False
        sync_manager = MagicMock()
        target = SimpleNamespace(
            wpm=wpm, sync_manager=sync_manager,
            pm=SimpleNamespace(current_project="작품"),
        )

        with patch.object(QMessageBox, "warning"):
            applied = WritingModeWidget.apply_conflict_choice(
                target, "메인/원고/1권/002화.txt", "내용", LOCAL_LABEL
            )

        self.assertFalse(applied)
        sync_manager.upload_content_async.assert_not_called()

    def test_resolving_a_conflict_never_deletes_the_conflict_folder(self):
        """충돌 폴더는 해결 뒤에도 그대로 남아야 한다. 유일한 원본 사본이다."""
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "충돌 보존 작품"
            wpm.writing_root_path = str(
                Path(temp_dir, wpm.current_project, "집필모드")
            )
            conflict_dir = Path(wpm.writing_root_path, "백업", "충돌")
            conflict_dir.mkdir(parents=True)
            artifacts = {
                "002화 (3방향 병합 충돌 2026-08-12 101010).txt": MERGED_BODY,
                "002화 (내 로컬 편집본 2026-08-12 101010).txt": "내 문장",
                "002화 (서버 최신본 2026-08-12 101010).txt": "서버 문장",
                "002화 (차이점 비교 2026-08-12 101010).txt": "차이점 비교 본문",
            }
            for name, body in artifacts.items():
                (conflict_dir / name).write_text(body, encoding="utf-8")

            path = "메인/원고/1권/002화.txt"
            self.assertTrue(wpm.write_text_file(path, "충돌 이전 원고"))

            target = SimpleNamespace(
                wpm=wpm,
                sync_manager=MagicMock(),
                pm=SimpleNamespace(current_project=wpm.current_project),
                current_loaded_file_left=None,
                current_loaded_file_right=None,
                is_dirty_left=False,
                is_dirty_right=False,
                left_editor=None,
                right_editor=None,
                lbl_current_doc=MagicMock(),
                lbl_r_doc=MagicMock(),
                on_sync_finished=MagicMock(),
                last_snapshot_contents={},
            )

            with patch.object(WritingModeWidget, "_accept_persisted_snapshot"):
                self.assertTrue(
                    WritingModeWidget.apply_conflict_choice(
                        target, path, "서버 문장", REMOTE_LABEL
                    )
                )

            # 원고는 선택한 내용으로 바뀐다.
            self.assertEqual(wpm.read_text_file(path), "서버 문장")
            # 충돌 사본은 이름과 내용 모두 그대로 남는다.
            for name, body in artifacts.items():
                artifact = conflict_dir / name
                self.assertTrue(artifact.exists(), name)
                self.assertEqual(artifact.read_text(encoding="utf-8"), body)

    def test_backup_folder_is_never_treated_as_a_cloud_document(self):
        from sync_manager import is_live_document_path

        self.assertFalse(is_live_document_path("백업/충돌/002화 (서버 최신본).txt"))
        self.assertFalse(is_live_document_path("백업"))
        self.assertTrue(is_live_document_path("메인/원고/1권/002화.txt"))

    def test_store_lists_documents_waiting_for_a_choice(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "충돌 목록 작품"
            wpm.writing_root_path = str(
                Path(temp_dir, wpm.current_project, "집필모드")
            )
            Path(wpm.writing_root_path).mkdir(parents=True)
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            manager = SyncManager()
            previous = (
                manager._v2_store, manager._v2_context, manager._v2_wpm
            )
            try:
                manager.configure_v2(
                    wpm, wpm.current_project, str(uuid.uuid4()), store=store
                )
                local_key = manager._v2_context["local_key"]
                path = "메인/원고/1권/002화.txt"
                operation = store.enqueue(manager._v2_context, path, "내 문장")
                self.assertEqual(store.conflict_documents(local_key), [])

                store.mark_conflict(
                    operation["operation_id"],
                    remote_revision=3,
                    remote_path=path,
                    remote_content="서버 문장",
                    merged_content=MERGED_BODY,
                    local_content="내 문장",
                )

                documents = store.conflict_documents(local_key)
                self.assertEqual(len(documents), 1)
                self.assertEqual(documents[0]["local_path"], path)
                self.assertEqual(documents[0]["conflict_remote"], "서버 문장")
                self.assertEqual(documents[0]["conflict_merged"], MERGED_BODY)
            finally:
                (
                    manager._v2_store, manager._v2_context, manager._v2_wpm
                ) = previous


if __name__ == "__main__":
    unittest.main(verbosity=2)
