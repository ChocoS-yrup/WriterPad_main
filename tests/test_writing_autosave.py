import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QResizeEvent
from PyQt6.QtWidgets import QApplication

from mode_assistant import AssistantModeWidget
from mode_writing import WritingModeWidget
from sync_manager import SyncManager
from text_editor import SmartTextEdit
from writing_controller import WritingController
from writing_controller import AUTOSAVE_IDLE_INTERVAL_MS


class WritingIdleAutosaveTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _controller(self, content="유휴 자동저장 본문", write_success=True):
        path = "메인/원고/1권/006화.txt"
        wpm = MagicMock()
        wpm.write_text_file.return_value = write_success
        sync_manager = MagicMock()
        sync_manager.can_save_path.return_value = True
        sync_manager.would_erase_nonempty_document.return_value = False
        persisted = MagicMock()
        controller = WritingController(
            wpm,
            sync_manager,
            SimpleNamespace(current_project="서버 작품"),
            "device-id",
            lambda: [path],
            lambda requested: content if requested == path else None,
            persisted,
        )
        # These tests exercise autosave state only.  Starting a real lease
        # QThread here makes the controller's local lifetime race the worker
        # startup on slower Windows CI runners.
        controller.acquire_lock_async = MagicMock()
        controller.pending_autosave_paths.add(path)
        return path, wpm, sync_manager, persisted, controller

    def test_remote_snapshot_cancels_stale_autosave_for_same_path(self):
        path, _wpm, _sync, _persisted, controller = self._controller()
        other_path = "메인/원고/1권/007화.txt"
        controller.pending_autosave_paths.add(other_path)
        controller.idle_timer.start()

        controller.accept_remote_snapshot(path, "아이패드 최신 내용")

        self.assertNotIn(path, controller.pending_autosave_paths)
        self.assertIn(other_path, controller.pending_autosave_paths)
        self.assertEqual(
            controller.last_snapshot_contents[path], "아이패드 최신 내용"
        )
        self.assertTrue(controller.idle_timer.isActive())

        controller.accept_remote_snapshot(other_path, "다른 최신 내용")

        self.assertFalse(controller.pending_autosave_paths)
        self.assertFalse(controller.idle_timer.isActive())

    def test_idle_autosave_runs_within_about_one_second(self):
        _path, _wpm, _sync, _persisted, controller = self._controller()

        self.assertEqual(AUTOSAVE_IDLE_INTERVAL_MS, 800)
        self.assertEqual(controller.idle_timer.interval(), 800)
        self.assertLessEqual(controller.idle_timer.interval(), 1000)

    def test_idle_save_persists_current_file_backup_and_durable_sync(self):
        path, wpm, sync_manager, persisted, controller = self._controller()

        controller.sync_file()

        wpm.write_text_file.assert_called_once_with(
            path, "유휴 자동저장 본문"
        )
        sync_manager.upload_autosave_async.assert_called_once_with(
            wpm, path, "유휴 자동저장 본문"
        )
        sync_manager.upload_content_async.assert_called_once_with(
            wpm, "서버 작품", path, "유휴 자동저장 본문"
        )
        persisted.assert_called_once_with(
            path, "유휴 자동저장 본문", True
        )
        self.assertNotIn(path, controller.pending_autosave_paths)

    def test_empty_autosave_never_overwrites_a_nonempty_synced_document(self):
        path, wpm, sync_manager, persisted, controller = self._controller(
            content=""
        )
        sync_manager.would_erase_nonempty_document.return_value = True

        controller.sync_file()

        wpm.write_text_file.assert_not_called()
        sync_manager.upload_autosave_async.assert_not_called()
        sync_manager.upload_content_async.assert_not_called()
        sync_manager.report_empty_content_guard.assert_called_once_with(path)
        persisted.assert_called_once_with(path, "", False)
        self.assertIn(path, controller.pending_autosave_paths)

    def test_user_whole_document_deletion_is_saved_and_keeps_prior_backup(self):
        path, wpm, sync_manager, persisted, controller = self._controller(
            content=""
        )
        sync_manager.would_erase_nonempty_document.return_value = True
        controller.last_snapshot_contents[path] = "삭제 전 원고"
        controller.notify_text_changed(path)

        controller.sync_file()

        sync_manager.report_empty_content_guard.assert_not_called()
        sync_manager.upload_autosave_async.assert_called_once_with(
            wpm, path, "삭제 전 원고"
        )
        wpm.write_text_file.assert_called_once_with(path, "")
        sync_manager.upload_content_async.assert_called_once_with(
            wpm,
            "서버 작품",
            path,
            "",
            force_overwrite=True,
        )
        persisted.assert_called_once_with(path, "", True)
        self.assertNotIn(path, controller.pending_autosave_paths)
        self.assertNotIn(path, controller.user_edited_paths)
        self.assertEqual(controller.last_snapshot_contents[path], "")

    def test_empty_loaded_snapshot_is_not_trusted_as_user_deletion(self):
        path, wpm, sync_manager, persisted, controller = self._controller(
            content=""
        )
        sync_manager.would_erase_nonempty_document.return_value = True
        controller.last_snapshot_contents[path] = ""
        controller.notify_text_changed(path)

        controller.sync_file()

        sync_manager.report_empty_content_guard.assert_called_once_with(path)
        wpm.write_text_file.assert_not_called()
        sync_manager.upload_content_async.assert_not_called()
        persisted.assert_called_once_with(path, "", False)

    def test_cancelled_manual_empty_save_keeps_the_document_dirty(self):
        from PyQt6.QtWidgets import QMessageBox

        path = "메인/원고/1권/002화.txt"
        manager = MagicMock()
        manager.would_erase_nonempty_document.return_value = True
        panel = SimpleNamespace(sync_manager=manager)

        with patch(
            "mode_writing.QMessageBox.warning",
            return_value=QMessageBox.StandardButton.No,
        ):
            result = WritingModeWidget._confirm_empty_document_save(
                panel, path, ""
            )

        self.assertIsNone(result)
        manager.report_empty_content_guard.assert_called_once_with(path)

    def test_known_user_empty_save_does_not_ask_for_confirmation(self):
        path = "메인/원고/1권/002화.txt"
        manager = MagicMock()
        manager.would_erase_nonempty_document.return_value = True
        panel = SimpleNamespace(sync_manager=manager)

        with patch("mode_writing.QMessageBox.warning") as warning:
            result = WritingModeWidget._confirm_empty_document_save(
                panel, path, "", user_initiated=True
            )

        self.assertTrue(result)
        warning.assert_not_called()
        manager.report_empty_content_guard.assert_not_called()

    def test_failed_current_file_write_keeps_autosave_pending(self):
        path, _wpm, sync_manager, persisted, controller = self._controller(
            write_success=False
        )

        controller.sync_file()

        sync_manager.upload_content_async.assert_not_called()
        persisted.assert_called_once_with(
            path, "유휴 자동저장 본문", False
        )
        self.assertIn(path, controller.pending_autosave_paths)
        self.assertTrue(controller.idle_timer.isActive())

    def test_dirty_flag_clears_only_if_editor_still_matches_saved_snapshot(self):
        path = "메인/원고/1권/006화.txt"
        editor = MagicMock()
        editor.toPlainText.return_value = "저장된 본문"
        panel = SimpleNamespace(
            current_loaded_file_left=path,
            current_loaded_file_right=None,
            left_editor=editor,
            right_editor=MagicMock(),
            is_dirty_left=True,
            is_dirty_right=False,
            _refresh_storage_status_for_editor_state=MagicMock(),
        )

        WritingModeWidget.on_idle_autosave_persisted(
            panel, path, "저장된 본문", True
        )

        self.assertFalse(panel.is_dirty_left)
        editor.document().setModified.assert_called_once_with(False)
        panel._refresh_storage_status_for_editor_state.assert_called_once_with()

        editor.reset_mock()
        editor.toPlainText.return_value = "저장 뒤 추가로 입력한 본문"
        panel.is_dirty_left = True
        panel._refresh_storage_status_for_editor_state.reset_mock()
        WritingModeWidget.on_idle_autosave_persisted(
            panel, path, "저장된 본문", True
        )
        self.assertTrue(panel.is_dirty_left)
        editor.document().setModified.assert_not_called()
        panel._refresh_storage_status_for_editor_state.assert_called_once_with()

    def test_typing_immediately_marks_editor_dirty_and_refreshes_status(self):
        path = "메인/원고/1권/006화.txt"
        editor = MagicMock()
        editor.isReadOnly.return_value = False
        editor.toPlainText.return_value = "새로 입력한 본문"
        panel = SimpleNamespace(
            sender=lambda: editor,
            active_editor=editor,
            left_editor=editor,
            right_editor=MagicMock(),
            current_loaded_file_left=path,
            current_loaded_file_right=None,
            is_dirty_left=False,
            is_dirty_right=False,
            update_tree_icon=MagicMock(),
            controller=MagicMock(),
            _refresh_storage_status_for_editor_state=MagicMock(),
            update_editor_statistics=MagicMock(),
        )

        WritingModeWidget.on_editor_text_changed(panel)

        self.assertTrue(panel.is_dirty_left)
        panel.controller.notify_text_changed.assert_called_once_with(path)
        panel._refresh_storage_status_for_editor_state.assert_called_once_with()

    def test_background_autosave_saves_preedit_without_forcing_commit(self):
        path = "메인/원고/1권/006화.txt"
        editor = MagicMock()
        editor.isReadOnly.return_value = False
        editor.has_pending_input_method.return_value = True
        editor.text_with_pending_input_method.return_value = "이미 확정된 문장이다"
        panel = SimpleNamespace(
            current_loaded_file_left=path,
            current_loaded_file_right=None,
            left_editor=editor,
            right_editor=MagicMock(),
        )

        content = WritingModeWidget.get_editor_content(panel, path)

        self.assertEqual(content, "이미 확정된 문장이다")
        editor.commit_pending_input_method.assert_not_called()

    def test_empty_document_with_only_a_preedit_still_saves_the_syllable(self):
        editor = MagicMock()
        editor.has_pending_input_method.return_value = True
        editor.text_with_pending_input_method.return_value = "ㄷ"

        content = WritingModeWidget._editor_text_for_background_save(editor)

        # A preedit over an emptied document is not an empty save, so the
        # non-empty-content guard has nothing to refuse.
        self.assertEqual(content, "ㄷ")
        editor.commit_pending_input_method.assert_not_called()

    def test_autosave_clears_dirty_once_the_preedit_is_persisted_too(self):
        path = "메인/원고/1권/006화.txt"
        editor = MagicMock()
        editor.has_pending_input_method.return_value = True
        editor.text_with_pending_input_method.return_value = "이미 확정된 문장이다"
        panel = SimpleNamespace(
            current_loaded_file_left=path,
            current_loaded_file_right=None,
            left_editor=editor,
            right_editor=MagicMock(),
            is_dirty_left=True,
            is_dirty_right=False,
            _refresh_storage_status_for_editor_state=MagicMock(),
        )

        WritingModeWidget.on_idle_autosave_persisted(
            panel, path, "이미 확정된 문장이다", True
        )

        # 로컬 저장 대기 must not stay on screen for the whole pause just
        # because the last Korean syllable is still in composition.
        self.assertFalse(panel.is_dirty_left)
        editor.document().setModified.assert_called_once_with(False)

    def test_autosave_keeps_dirty_when_the_composition_moved_on(self):
        path = "메인/원고/1권/006화.txt"
        editor = MagicMock()
        editor.has_pending_input_method.return_value = True
        editor.text_with_pending_input_method.return_value = "이미 확정된 문장이닫"
        panel = SimpleNamespace(
            current_loaded_file_left=path,
            current_loaded_file_right=None,
            left_editor=editor,
            right_editor=MagicMock(),
            is_dirty_left=True,
            is_dirty_right=False,
            _refresh_storage_status_for_editor_state=MagicMock(),
        )

        WritingModeWidget.on_idle_autosave_persisted(
            panel, path, "이미 확정된 문장이다", True
        )

        self.assertTrue(panel.is_dirty_left)
        editor.document().setModified.assert_not_called()

    def test_background_autosave_keeps_active_composition_pending(self):
        path, wpm, sync_manager, persisted, controller = self._controller(
            content=None
        )

        controller.sync_file()

        self.assertIn(path, controller.pending_autosave_paths)
        self.assertTrue(controller.idle_timer.isActive())
        wpm.write_text_file.assert_not_called()
        sync_manager.upload_autosave_async.assert_not_called()
        sync_manager.upload_content_async.assert_not_called()
        persisted.assert_not_called()

    def test_typing_defers_binder_scan_and_full_document_statistics(self):
        path = "메인/원고/1권/006화.txt"
        editor = MagicMock()
        editor.isReadOnly.return_value = False
        panel = SimpleNamespace(
            sender=lambda: editor,
            active_editor=editor,
            left_editor=editor,
            right_editor=MagicMock(),
            current_loaded_file_left=path,
            current_loaded_file_right=None,
            is_dirty_left=False,
            is_dirty_right=False,
            update_tree_icon=MagicMock(),
            controller=MagicMock(),
            _refresh_storage_status_for_editor_state=MagicMock(),
            schedule_editor_metadata_refresh=MagicMock(),
            update_editor_statistics=MagicMock(),
        )

        WritingModeWidget.on_editor_text_changed(panel)

        self.assertTrue(panel.is_dirty_left)
        panel.controller.notify_text_changed.assert_called_once_with(path)
        panel.schedule_editor_metadata_refresh.assert_called_once_with(path)
        editor.toPlainText.assert_not_called()
        panel.update_tree_icon.assert_not_called()
        panel.update_editor_statistics.assert_not_called()

    def test_manual_save_failure_does_not_claim_editor_was_saved(self):
        path = "메인/원고/1권/006화.txt"
        editor = MagicMock()
        editor.toPlainText.return_value = "디스크 쓰기 실패 본문"
        panel = SimpleNamespace(
            current_loaded_file_left=path,
            current_loaded_file_right=None,
            left_editor=editor,
            right_editor=MagicMock(),
            is_dirty_left=True,
            is_dirty_right=False,
            wpm=MagicMock(),
            sync_manager=MagicMock(),
            controller=MagicMock(),
            pm=SimpleNamespace(current_project="서버 작품"),
            on_sync_finished=MagicMock(),
            lbl_current_doc=MagicMock(),
        )
        panel.sync_manager.can_save_path.return_value = True
        panel.controller.allows_intentional_empty_save.return_value = False
        panel.wpm.write_text_file.return_value = False

        WritingModeWidget.manual_save(panel)

        self.assertTrue(panel.is_dirty_left)
        editor.document().setModified.assert_not_called()
        panel.sync_manager.upload_content_async.assert_not_called()

    def test_manual_save_ignores_clean_open_editors(self):
        left_editor = MagicMock()
        right_editor = MagicMock()
        left_editor.document().isModified.return_value = False
        right_editor.document().isModified.return_value = False
        panel = SimpleNamespace(
            current_loaded_file_left="메인/원고/1권/001화.txt",
            current_loaded_file_right="메인/원고/1권/002화.txt",
            left_editor=left_editor,
            right_editor=right_editor,
            is_dirty_left=False,
            is_dirty_right=False,
            wpm=MagicMock(),
            sync_manager=MagicMock(),
            controller=MagicMock(),
            pm=SimpleNamespace(current_project="서버 작품"),
            on_sync_finished=MagicMock(),
            lbl_current_doc=MagicMock(),
            lbl_r_doc=MagicMock(),
            right_editor_container=MagicMock(),
        )
        panel.sync_manager.can_save_path.return_value = True

        WritingModeWidget.manual_save(panel)

        panel.wpm.write_text_file.assert_not_called()
        panel.sync_manager.upload_content_async.assert_not_called()

    def test_editor_margin_refresh_preserves_clean_document_state(self):
        left_editor = SmartTextEdit()
        right_editor = SmartTextEdit()
        left_editor.setPlainText("왼쪽 원고")
        right_editor.setPlainText("오른쪽 원고")
        left_editor.document().setModified(False)
        right_editor.document().setModified(False)
        left_changed = MagicMock()
        right_changed = MagicMock()
        left_editor.textChanged.connect(left_changed)
        right_editor.textChanged.connect(right_changed)
        panel = SimpleNamespace(
            left_editor=left_editor,
            right_editor=right_editor,
            pad_h=40,
            pad_v=25,
        )

        WritingModeWidget.apply_editor_margins(panel)

        self.assertFalse(left_editor.document().isModified())
        self.assertFalse(right_editor.document().isModified())
        left_changed.assert_not_called()
        right_changed.assert_not_called()

    def test_editor_resize_preserves_clean_document_state(self):
        editor = SmartTextEdit()
        editor.setPlainText("크기 변경 중인 원고")
        editor.document().setModified(False)
        changed = MagicMock()
        editor.textChanged.connect(changed)
        event = QResizeEvent(QSize(640, 480), QSize(600, 440))

        editor.resizeEvent(event)

        self.assertFalse(editor.document().isModified())
        changed.assert_not_called()


class WritingShutdownFlushTestCase(unittest.TestCase):
    def test_empty_v2_queue_is_already_flushed(self):
        target = SimpleNamespace(
            is_v2_enabled=True,
            _v2_context={"local_key": "project-key"},
            _v2_store=SimpleNamespace(
                counts=lambda _key: {
                    "pending": 0,
                    "inflight": 0,
                    "conflict": 0,
                    "total": 0,
                }
            ),
        )

        self.assertTrue(SyncManager.flush_pending_syncs(target, 10))

    def test_offline_v2_queue_remains_durable_instead_of_claiming_synced(self):
        target = SimpleNamespace(
            is_v2_enabled=True,
            _v2_context={"local_key": "project-key"},
            _v2_store=SimpleNamespace(
                counts=lambda _key: {
                    "pending": 1,
                    "inflight": 0,
                    "conflict": 0,
                    "total": 1,
                }
            ),
            supabase=None,
        )

        self.assertFalse(SyncManager.flush_pending_syncs(target, 10))

    def test_close_warning_says_local_is_safe_when_server_flush_is_pending(self):
        flush = MagicMock(return_value=False)
        panel = SimpleNamespace(
            writing_mode=SimpleNamespace(
                sync_manager=SimpleNamespace(flush_pending_syncs=flush)
            )
        )

        with patch("mode_assistant.QMessageBox.information") as information:
            result = AssistantModeWidget._flush_writing_sync_before_close(panel)

        self.assertFalse(result)
        flush.assert_called_once_with()
        information.assert_called_once()
        self.assertIn("다음 실행", information.call_args.args[2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
