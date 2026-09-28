import inspect
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QInputMethodEvent, QKeyEvent
from PyQt6.QtWidgets import QApplication

import main
from mode_writing import WritingModeWidget
from text_editor import SmartTextEdit


class InputMethodActivationTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.previous_open = SmartTextEdit._last_windows_ime_open
        self.previous_force = SmartTextEdit._force_korean_on_first_activation
        self.editor = SmartTextEdit()
        self.editor.setReadOnly(False)

    def tearDown(self):
        self.editor.close()
        self.app.processEvents()
        SmartTextEdit._last_windows_ime_open = self.previous_open
        SmartTextEdit._force_korean_on_first_activation = self.previous_force

    def test_activation_refreshes_ime_now_and_on_next_event_loop_turn(self):
        with (
            patch.object(self.editor, "_refresh_input_method") as refresh,
            patch("text_editor.QTimer.singleShot") as single_shot,
        ):
            self.editor.activate_input_method()

            serial = self.editor._input_activation_serial
            refresh.assert_called_once_with(serial)
            single_shot.assert_called_once()
            delay, callback = single_shot.call_args.args
            self.assertEqual(delay, 0)

            callback()
            self.assertEqual(refresh.call_args_list[-1].args, (serial,))
            self.assertEqual(refresh.call_count, 2)

    def test_first_activation_forces_korean_then_preserves_user_english_mode(self):
        class FakeImm32:
            def __init__(self):
                self.open_status = False
                self.status_calls = []
                self.release_calls = []

            def ImmGetContext(self, hwnd):
                return 321

            def ImmGetOpenStatus(self, context):
                return self.open_status

            def ImmSetOpenStatus(self, context, status):
                self.status_calls.append((context, status))
                self.open_status = bool(status)
                return True

            def ImmReleaseContext(self, hwnd, context):
                self.release_calls.append((hwnd, context))
                return True

        fake_imm32 = FakeImm32()
        previous_open = SmartTextEdit._last_windows_ime_open
        previous_force = SmartTextEdit._force_korean_on_first_activation
        SmartTextEdit._last_windows_ime_open = False
        SmartTextEdit._force_korean_on_first_activation = True
        try:
            with (
                patch.object(self.editor, "_windows_ime_api", return_value=fake_imm32),
                patch.object(self.editor, "_windows_ime_hwnd", return_value=77),
                patch.object(
                    self.editor,
                    "_synchronize_windows_ime_window",
                    return_value=True,
                ) as synchronize,
            ):
                self.editor._restore_windows_ime_state()

                self.assertTrue(SmartTextEdit._force_korean_on_first_activation)
                self.assertTrue(SmartTextEdit._last_windows_ime_open)

                SmartTextEdit._complete_startup_korean_mode()
                fake_imm32.open_status = False
                self.editor._remember_windows_ime_state()
                self.editor._restore_windows_ime_state()
        finally:
            SmartTextEdit._last_windows_ime_open = previous_open
            SmartTextEdit._force_korean_on_first_activation = previous_force

        self.assertEqual(fake_imm32.status_calls, [(321, True), (321, False)])
        self.assertEqual(fake_imm32.release_calls, [(77, 321), (77, 321), (77, 321)])
        self.assertEqual(
            [call.args[2] for call in synchronize.call_args_list],
            [True, False],
        )

    def test_program_show_forces_korean_without_consuming_first_input_guard(self):
        class FakeImm32:
            def __init__(self):
                self.status_calls = []

            def ImmGetContext(self, hwnd):
                return 321

            def ImmSetOpenStatus(self, context, status):
                self.status_calls.append((context, status))
                return True

            def ImmReleaseContext(self, hwnd, context):
                return True

        fake_imm32 = FakeImm32()
        widget = SimpleNamespace(winId=lambda: 77)
        SmartTextEdit._force_korean_on_first_activation = True
        SmartTextEdit._last_windows_ime_open = False

        with (
            patch.object(SmartTextEdit, "_windows_ime_api", return_value=fake_imm32),
            patch.object(SmartTextEdit, "_synchronize_windows_ime_window", return_value=True),
        ):
            applied = SmartTextEdit.force_startup_korean_for_widget(widget)

        self.assertTrue(applied)
        self.assertEqual(fake_imm32.status_calls, [(321, True)])
        self.assertTrue(SmartTextEdit._last_windows_ime_open)
        self.assertTrue(SmartTextEdit._force_korean_on_first_activation)

    def test_real_ime_text_completes_the_startup_guard(self):
        SmartTextEdit._force_korean_on_first_activation = True
        self.editor._startup_ime_guard_active = True

        committed = QInputMethodEvent()
        committed.setCommitString("ㅇ")
        self.editor.inputMethodEvent(committed)

        self.assertTrue(self.editor._startup_ime_guard_active)
        self.assertTrue(SmartTextEdit._force_korean_on_first_activation)

        self.editor.inputMethodEvent(QInputMethodEvent("ㅇ", []))

        self.assertFalse(self.editor._startup_ime_guard_active)
        self.assertFalse(SmartTextEdit._force_korean_on_first_activation)

    def test_background_save_text_carries_the_preedit_at_the_caret(self):
        self.editor.setPlainText("바다")
        cursor = self.editor.textCursor()
        cursor.setPosition(1)
        self.editor.setTextCursor(cursor)

        self.editor.inputMethodEvent(QInputMethodEvent("ㄷ", []))

        self.assertTrue(self.editor.has_pending_input_method())
        # The preedit is drawn but is not part of the document.
        self.assertEqual(self.editor.toPlainText(), "바다")
        self.assertEqual(self.editor.text_with_pending_input_method(), "바ㄷ다")

    def test_background_save_text_matches_document_with_no_preedit(self):
        self.editor.setPlainText("조합이 끝난 문장")

        self.assertFalse(self.editor.has_pending_input_method())
        self.assertEqual(
            self.editor.text_with_pending_input_method(), "조합이 끝난 문장"
        )

    def test_reading_the_preedit_never_signals_the_input_method(self):
        from PyQt6.QtGui import QTextCursor

        self.editor.setPlainText("바")
        self.editor.moveCursor(QTextCursor.MoveOperation.End)
        self.editor.inputMethodEvent(QInputMethodEvent("ㄷ", []))

        with patch.object(
            self.editor, "commit_pending_input_method"
        ) as commit, patch.object(
            self.editor, "_send_windows_virtual_keys"
        ) as send_keys:
            text = WritingModeWidget._editor_text_for_background_save(
                self.editor
            )

        self.assertEqual(text, "바ㄷ")
        commit.assert_not_called()
        send_keys.assert_not_called()

    def test_first_raw_english_key_is_replayed_after_ime_activation(self):
        event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_D,
            Qt.KeyboardModifier.NoModifier,
            0x20,
            0x44,
            0,
            "d",
            False,
            1,
        )
        repeated_event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_D,
            Qt.KeyboardModifier.NoModifier,
            0x20,
            0x44,
            0,
            "d",
            True,
            2,
        )
        SmartTextEdit._force_korean_on_first_activation = True
        self.editor._startup_ime_guard_active = True

        with (
            patch.object(self.editor, "_restore_windows_ime_state") as restore,
            patch.object(self.editor, "hasFocus", return_value=True),
            patch.object(self.editor, "_send_windows_virtual_keys", return_value=True) as replay,
            patch("text_editor.QTimer.singleShot") as single_shot,
        ):
            self.editor.keyPressEvent(event)
            self.editor.keyPressEvent(repeated_event)

            self.assertEqual(self.editor.toPlainText(), "")
            self.assertTrue(event.isAccepted())
            self.assertTrue(repeated_event.isAccepted())
            self.assertEqual(restore.call_count, 2)
            single_shot.assert_called_once()
            delay, callback = single_shot.call_args.args
            self.assertEqual(delay, 0)

            callback()

        replay.assert_called_once_with([(0x44, False, "d"), (0x44, False, "d")])
        self.assertFalse(self.editor._startup_ime_guard_active)
        self.assertFalse(SmartTextEdit._force_korean_on_first_activation)
        self.assertEqual(self.editor._startup_ime_key_buffer, [])

    def test_each_korean_chapter_transition_is_guarded_but_english_is_not(self):
        SmartTextEdit._force_korean_on_first_activation = False
        SmartTextEdit._last_windows_ime_open = True
        with (
            patch.object(self.editor, "_refresh_input_method"),
            patch("text_editor.QTimer.singleShot"),
        ):
            self.editor.activate_input_method()

        self.assertTrue(self.editor._startup_ime_guard_active)

        SmartTextEdit._last_windows_ime_open = False
        with (
            patch.object(self.editor, "_refresh_input_method"),
            patch("text_editor.QTimer.singleShot"),
        ):
            self.editor.activate_input_method()

        self.assertFalse(self.editor._startup_ime_guard_active)
        self.assertEqual(self.editor._startup_ime_key_buffer, [])

    def test_windows_key_replay_builds_a_serial_input_batch(self):
        class FakeSendInput:
            def __init__(self):
                self.argtypes = None
                self.restype = None
                self.count = 0
                self.virtual_keys = []

            def __call__(self, count, inputs, input_size):
                self.count = count
                self.virtual_keys = [inputs[index].ki.wVk for index in range(count)]
                return count

        send_input = FakeSendInput()
        fake_user32 = type("FakeUser32", (), {"SendInput": send_input})()
        with patch("ctypes.WinDLL", return_value=fake_user32):
            success = self.editor._send_windows_virtual_keys(
                [(0x44, False, "d"), (0x52, True, "R")]
            )

        self.assertTrue(success)
        self.assertEqual(send_input.count, 6)
        self.assertEqual(send_input.virtual_keys, [0x44, 0x44, 0x10, 0x52, 0x52, 0x10])

    def test_missing_native_window_handle_does_not_abort_file_loading(self):
        class FailingImm32:
            def ImmGetContext(self, hwnd):
                raise AssertionError("창 핸들이 없으면 IME 문맥을 조회하면 안 됩니다.")

        with (
            patch.object(self.editor, "_windows_ime_api", return_value=FailingImm32()),
            patch.object(self.editor, "_windows_ime_hwnd", return_value=None),
        ):
            self.editor._remember_windows_ime_state()
            self.editor._restore_windows_ime_state()

    def test_file_open_and_session_restore_have_no_fixed_focus_delay(self):
        open_source = inspect.getsource(WritingModeWidget._open_file_by_path)
        restore_source = inspect.getsource(WritingModeWidget.load_saved_files)
        switch_source = inspect.getsource(main.MainWindow.switch_to_writing)
        window_event_source = inspect.getsource(main.MainWindow.event)
        main_source = Path(main.__file__).read_text(encoding="utf-8")

        self.assertIn("editor.activate_input_method()", open_source)
        self.assertIn("self.isVisible()", open_source)
        self.assertIn("activate_current_editor_input()", switch_source)
        self.assertIn("QEvent.Type.WindowActivate", window_event_source)
        self.assertIn("force_startup_korean_for_widget(self)", window_event_source)
        self.assertLess(
            main_source.index("window.show()"),
            main_source.index("SmartTextEdit.force_startup_korean_for_widget(window)"),
        )
        self.assertNotIn("singleShot(500", open_source)
        self.assertNotIn("singleShot(500", restore_source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
