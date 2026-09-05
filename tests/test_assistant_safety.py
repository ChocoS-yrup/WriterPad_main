"""AI mode regression checks. Only temporary manuscripts and fake API calls."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import QEvent, QMutex, QObject, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QInputMethodEvent, QTextCursor
from PyQt6.QtTest import QTest
from tests.qt_app import APP
from mode_assistant import AssistantModeWidget
from project_manager import ProjectManager
from assistant_runtime import AIGenerationWorker


class FakeGenerationWorker(QObject):
    resultReady = pyqtSignal(str, str, int, int)
    error = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, step_name, messages, selected_model, **kwargs):
        super().__init__(kwargs.get("parent"))
        self.step_name = step_name
        self.selected_model = selected_model
        self.caching = kwargs.get("use_context_caching", False)
        self.running = False
        self.requestInterruption = MagicMock()

    def start(self):
        self.running = True

    def isRunning(self):
        return self.running

    def complete(self, text="생성 결과"):
        self.resultReady.emit(self.step_name, text, 10, 20)
        self.running = False
        self.finished.emit()


class AssistantSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {"ANTIGRAVITY_ROOT_DIR": self.temp.name})
        env.start()
        self.addCleanup(env.stop)
        def select(widget):
            widget.pm.set_current_project("합성 작품")
        with patch.object(AssistantModeWidget, "_select_project", select), patch.object(
            AssistantModeWidget, "init_tray_icon"
        ), patch("security_manager.SecurityManager.get_api_key", return_value=""):
            self.widget = AssistantModeWidget()
        QTest.qWait(100)  # finish the production startup tab restore
        self.addCleanup(self.dispose_widget)

    def dispose_widget(self):
        for panel in self.widget.left_panels[:4] + self.widget.right_panels[:4]:
            panel.autosave_timer.stop()
        self.widget.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_real_idle_timer_saves_to_panels_chapter_not_global_selection(self):
        panel = self.widget.left_panels[0]
        panel.current_chapter = self.widget.right_panels[0].current_chapter = 7
        self.widget.current_chapter = 2
        self.assertEqual(panel.autosave_timer.interval(), 5000)
        panel.text_edit.setPlainText("7화의 초안")
        QTest.qWait(5200)
        self.assertEqual(self.widget.pm.load_chapter_text("초안", 7), "7화의 초안")
        self.assertEqual(self.widget.pm.load_chapter_text("초안", 2), "")
        self.assertFalse(panel.text_edit.document().isModified())

    def test_failed_autosave_retains_dirty_text_and_retries(self):
        panel = self.widget.left_panels[0]
        panel.text_edit.setPlainText("저장 재시도할 원고")
        panel.text_edit.document().setModified(True)
        with patch.object(self.widget.pm, "save_chapter_text", side_effect=OSError("disk full")):
            panel.trigger_autosave()
        self.assertTrue(panel.text_edit.document().isModified())
        self.assertTrue(panel.autosave_timer.isActive())
        panel.trigger_autosave()
        self.assertEqual(self.widget.pm.load_chapter_text("초안", 1), "저장 재시도할 원고")
        self.assertFalse(panel.text_edit.document().isModified())

    def test_empty_document_autosaves_after_user_deletion(self):
        panel = self.widget.left_panels[0]
        panel.text_edit.setPlainText("삭제 전 원고")
        panel.trigger_autosave()
        self.assertEqual(self.widget.pm.load_chapter_text("초안", 1), "삭제 전 원고")
        panel.text_edit.clear()
        panel.trigger_autosave()
        self.assertEqual(self.widget.pm.load_chapter_text("초안", 1), "")

    def test_right_hand_panel_saves_its_own_chapter(self):
        panel = self.widget.right_panels[2]
        panel.current_chapter = self.widget.left_panels[2].current_chapter = 9
        self.widget.current_chapter = 1
        panel.text_edit.setPlainText("9화 평가")
        panel.trigger_autosave()
        self.assertEqual(self.widget.pm.load_chapter_text("평가", 9), "9화 평가")
        self.assertEqual(self.widget.pm.load_chapter_text("평가", 1), "")

    def test_save_failure_prevents_chapter_switch_and_keeps_editor(self):
        panel = self.widget.left_panels[0]
        panel.text_edit.setPlainText("저장하지 못한 원고")
        with patch.object(self.widget.pm, "save_chapter_text", side_effect=OSError("disk full")):
            self.widget.on_chapter_changed(2)
        self.assertEqual(self.widget.current_chapter, 1)
        self.assertEqual(panel.current_chapter, 1)
        self.assertEqual(panel.text_edit.toPlainText(), "저장하지 못한 원고")
        self.assertTrue(panel.text_edit.document().isModified())
        self.assertTrue(panel.autosave_timer.isActive())

    def test_real_thread_delivers_result_and_releases_worker(self):
        provider = MagicMock()
        provider.generate.return_value = ("실제 스레드 결과", 10, 20)
        self.widget.show()
        with patch("llm_provider.LLMFactory.get_provider", return_value=provider):
            self.widget.handle_ai_generation("초안")
            worker = self.widget.ai_worker
            self.assertTrue(worker.wait(2000))
            QTest.qWait(100)
        self.assertFalse(self.widget.is_working)
        self.assertEqual(self.widget._ai_workers, [])
        self.assertEqual(self.widget.ai_panel.current_panel.result_editor.toPlainText(), "실제 스레드 결과")

    def test_cancel_discards_real_thread_result_already_queued_for_ui(self):
        provider = MagicMock()
        provider.generate.return_value = ("이미 도착한 이전 응답", 10, 20)
        with patch("llm_provider.LLMFactory.get_provider", return_value=provider):
            self.widget.handle_ai_generation("초안")
            self.assertTrue(self.widget.ai_worker.wait(2000))
            # Do not process the queued result until after cancellation.
            self.widget.hide_ai_panel()
            self.widget.on_chapter_changed(2)
            with patch.object(self.widget.pm, "save_ai_response") as save:
                QTest.qWait(100)
            save.assert_not_called()
        self.assertEqual(self.widget._ai_workers, [])

    def test_cancelled_worker_is_kept_alive_and_blocks_shutdown_until_finished(self):
        worker = self.start_request()
        self.widget.hide_ai_panel()
        event = QCloseEvent()
        with patch("mode_assistant.QMessageBox.warning") as warning:
            self.widget.closeEvent(event)
        self.assertFalse(event.isAccepted())
        warning.assert_called_once()
        self.assertIn(worker, self.widget._ai_workers)
        worker.complete()
        self.assertFalse(self.widget.has_running_ai_workers())

    def test_chapter_switch_does_not_replay_old_timer_into_new_document(self):
        panel = self.widget.left_panels[0]
        panel.autosave_timer.setInterval(20)
        self.widget.right_panels[0].autosave_timer.setInterval(20)
        panel.text_edit.setPlainText("첫 화의 내용")
        self.widget.on_chapter_changed(2)
        QTest.qWait(80)
        self.assertEqual(self.widget.pm.load_chapter_text("초안", 1), "첫 화의 내용")
        self.assertFalse(Path(self.widget.pm.get_text_file_path("초안", 2)).exists())

    def test_korean_preedit_is_saved_without_committing_or_duplicating_it(self):
        panel = self.widget.left_panels[0]
        panel.text_edit.setPlainText("본문")
        panel.text_edit.moveCursor(QTextCursor.MoveOperation.End)
        APP.sendEvent(panel.text_edit, QInputMethodEvent("한", []))
        panel.trigger_autosave()
        self.assertEqual(self.widget.pm.load_chapter_text("초안", 1), "본문한")
        self.assertTrue(panel.text_edit.has_pending_input_method())
        self.assertTrue(panel.text_edit.document().isModified())
        event = QInputMethodEvent()
        event.setCommitString("한")
        APP.sendEvent(panel.text_edit, event)
        panel.trigger_autosave()
        self.assertEqual(self.widget.pm.load_chapter_text("초안", 1), "본문한")
        self.assertFalse(panel.text_edit.document().isModified())

    def start_request(self, *, feedback=False):
        with patch("assistant_workflow.AIGenerationWorker", FakeGenerationWorker):
            if feedback:
                self.widget.handle_ai_feedback("교정해주세요")
                return self.widget.chat_worker
            self.widget.handle_ai_generation("초안")
            return self.widget.ai_worker

    def test_cancel_and_switch_ignores_late_result_and_error(self):
        worker = self.start_request()
        self.widget.hide_ai_panel()
        worker.requestInterruption.assert_called_once()
        self.assertFalse(self.widget.is_working)
        self.assertTrue(self.widget.has_running_ai_workers())
        self.assertTrue(self.widget.chapter_selector.isEnabled())
        self.widget.on_chapter_changed(2)
        with patch.object(self.widget.pm, "save_ai_response") as save, patch.object(
            self.widget.pm, "log_api_cost"
        ) as cost, patch.object(self.widget.ai_panel, "update_result") as update, patch(
            "assistant_workflow.QMessageBox.critical"
        ) as error:
            worker.error.emit("late error")
            worker.complete("1화 요청의 늦은 결과")
        save.assert_not_called()
        cost.assert_not_called()
        update.assert_not_called()
        error.assert_not_called()
        self.assertFalse(self.widget.has_running_ai_workers())

    def test_old_result_cannot_finish_or_pollute_new_request(self):
        old = self.start_request()
        self.widget.hide_ai_panel()
        self.widget.on_chapter_changed(2)
        new = self.start_request()
        with patch.object(self.widget.pm, "save_ai_response") as save:
            old.complete("old")
            self.assertTrue(self.widget.is_working)
            save.assert_not_called()
            new.complete("2화 결과")
            save.assert_called_once_with("초안", 2, "2화 결과")
        self.assertFalse(self.widget.is_working)

    def test_response_uses_original_chapter_and_selected_model(self):
        worker = self.start_request()
        original_model = worker.selected_model
        # Simulate a programmatic selection change while a callback is queued.
        self.widget.current_chapter = 8
        with patch.object(self.widget.pm, "save_ai_response") as save, patch.object(
            self.widget.pm, "log_api_cost"
        ) as cost:
            worker.complete("1화 결과")
        save.assert_called_once_with("초안", 1, "1화 결과")
        cost.assert_called_once_with("초안", original_model, 10, 20)

    def test_response_never_writes_into_another_project(self):
        worker = self.start_request()
        self.widget.pm.set_current_project("다른 합성 작품")
        with patch.object(self.widget.pm, "save_ai_response") as save, patch.object(
            self.widget.pm, "log_api_cost"
        ) as cost:
            worker.complete()
        save.assert_not_called()
        cost.assert_not_called()
        self.assertFalse(self.widget.is_working)

    def test_feedback_request_is_busy_and_cancellation_invalidates_it(self):
        self.widget.handle_ai_open("초안")
        worker = self.start_request(feedback=True)
        self.assertTrue(self.widget.is_working)
        self.widget.hide_ai_panel()
        with patch.object(self.widget.pm, "save_ai_response") as save:
            worker.complete()
        save.assert_not_called()

    def test_error_clears_busy_state_and_allows_retry(self):
        worker = self.start_request()
        with patch("assistant_workflow.QMessageBox.critical"):
            worker.error.emit("synthetic network failure")
        self.assertFalse(self.widget.is_working)
        worker.running = False
        worker.finished.emit()
        next_worker = self.start_request()
        next_worker.complete("재시도 성공")
        self.assertFalse(self.widget.is_working)

    def test_opening_same_step_in_another_chapter_resets_old_chat_and_result(self):
        self.widget.handle_ai_open("초안")
        self.widget.ai_panel.update_result("1화 결과", "완료")
        self.widget.hide_ai_panel()
        self.widget.on_chapter_changed(2)
        self.widget.handle_ai_open("초안")
        self.assertEqual(self.widget.ai_panel.current_panel.result_editor.toPlainText(), "")
        self.assertNotIn("1화 결과", str(self.widget.ai_panel.chat_session))

    def test_final_confirm_summary_requests_use_the_same_cancellation_path(self):
        self.widget.left_panels[3].text_edit.setPlainText("1화 요약 보존")
        for chapter in (26, 27):
            with self.subTest(chapter=chapter):
                self.widget.current_chapter = chapter
                with patch("assistant_workflow.AIGenerationWorker", FakeGenerationWorker):
                    self.widget.handle_final_confirm()
                worker = self.widget.ai_worker
                self.assertEqual(worker.request_context.chapter, chapter)
                self.assertEqual(worker.step_name, "요약")
                self.assertEqual(worker.caching, chapter == 26)
                self.assertTrue(self.widget.is_working)
                self.assertTrue(self.widget.ai_panel.is_final_confirm_mode)
                self.assertIn(f"[{chapter}화 원문]", self.widget.ai_panel.pending_raw_texts)
                self.widget.hide_ai_panel()
                with patch.object(self.widget.pm, "save_ai_response") as save:
                    worker.complete()
                save.assert_not_called()
        self.assertEqual(self.widget.pm.load_chapter_text("요약", 1), "1화 요약 보존")
        self.assertNotEqual(self.widget.pm.load_chapter_text("요약", 26), "1화 요약 보존")


class AssistantAtomicSaveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = ProjectManager.__new__(ProjectManager)
        self.manager.mutex = QMutex()
        self.manager.project_path = self.temp.name
        Path(self.temp.name, "메인", "초안").mkdir(parents=True)
        self.manager.save_chapter_text("초안", 1, "기존 원고")

    def test_fsync_failure_preserves_existing_bytes_and_cleans_temporary_file(self):
        path = Path(self.manager.get_text_file_path("초안", 1))
        before = path.read_bytes()
        with patch("os.fsync", side_effect=OSError("synthetic write failure")):
            with self.assertRaises(OSError):
                self.manager.save_chapter_text("초안", 1, "새 원고")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_replace_failure_preserves_existing_bytes(self):
        path = Path(self.manager.get_text_file_path("초안", 1))
        before = path.read_bytes()
        with patch("os.replace", side_effect=PermissionError("synthetic locked file")):
            with self.assertRaises(PermissionError):
                self.manager.save_chapter_text("초안", 1, "새 원고")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_encoding_failure_preserves_existing_bytes(self):
        path = Path(self.manager.get_text_file_path("초안", 1))
        before = path.read_bytes()
        with self.assertRaises(UnicodeEncodeError):
            self.manager.save_chapter_text("초안", 1, "invalid surrogate: \ud800")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_saving_without_project_reports_failure(self):
        self.manager.project_path = None
        with self.assertRaises(OSError):
            self.manager.save_chapter_text("초안", 1, "원고")


class GenerationWorkerSafetyTests(unittest.TestCase):
    def test_messages_are_frozen_at_request_creation(self):
        messages = [{"role": "user", "content": "1화"}]
        worker = AIGenerationWorker("초안", messages, "dummy")
        messages[0]["content"] = "2화"
        messages.append({"role": "user", "content": "다른 요청"})
        self.assertEqual(worker.messages, [{"role": "user", "content": "1화"}])

    def test_cancellation_after_provider_returns_emits_no_result(self):
        worker = AIGenerationWorker("초안", [], "dummy")
        results = []
        worker.resultReady.connect(lambda *args: results.append(args))
        provider = MagicMock()
        provider.generate.return_value = ("늦은 결과", 1, 2)
        with patch("llm_provider.LLMFactory.get_provider", return_value=provider), patch.object(
            worker, "isInterruptionRequested", side_effect=[False, True]
        ):
            worker.run()
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
