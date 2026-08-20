import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QElapsedTimer, QEventLoop
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QStackedWidget,
    QWidget,
)

from editor_panel import EditorPanel
from main import MainWindow
from mode_assistant import AssistantModeWidget
from mode_writing import WritingModeWidget
from settings_panel import SettingsPanel
from text_editor import SmartTextEdit


def process_until(predicate, timeout_ms=1000):
    timer = QElapsedTimer()
    timer.start()
    while timer.elapsed() < timeout_ms:
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)
        if predicate():
            return True
    return bool(predicate())


class _PanelStub:
    def __init__(self, step_name):
        self.step_name = step_name
        self.text_edit = SmartTextEdit()

    def set_typewriter_mode(self, enabled):
        EditorPanel.set_typewriter_mode(self, enabled)


class _SettingsStub:
    _TYPEWRITER_CHECKBOX_ATTRS = SettingsPanel._TYPEWRITER_CHECKBOX_ATTRS

    def __init__(self):
        self.chk_tw_summary = QCheckBox()
        self.chk_tw_draft = QCheckBox()
        self.chk_tw_eval = QCheckBox()
        self.chk_tw_completed = QCheckBox()
        self.chk_tw_writing = QCheckBox()

    def set_typewriter_checked(self, step_name, enabled):
        SettingsPanel.set_typewriter_checked(self, step_name, enabled)


class _WritingModeStub(QWidget):
    def __init__(self):
        super().__init__()
        self.activate_calls = 0

    def activate_current_editor_input(self):
        self.activate_calls += 1


class TypewriterModeTestCase(unittest.TestCase):
    STEPS = ("초안", "완성본", "평가", "요약")

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def _editor(self, width=480, height=280):
        editor = SmartTextEdit()
        editor.resize(width, height)
        editor.show()
        self.addCleanup(editor.close)
        self.assertTrue(process_until(lambda: editor.viewport().height() > 0))
        return editor

    def _assistant_target(self, config):
        left = [_PanelStub(step) for step in self.STEPS]
        right = [_PanelStub(step) for step in self.STEPS]
        left_settings = _SettingsStub()
        right_settings = _SettingsStub()
        manager = SimpleNamespace(
            global_config=dict(config),
            save_global_config=MagicMock(),
        )
        target = SimpleNamespace(
            pm=manager,
            left_panels=left + [left_settings],
            right_panels=right + [right_settings],
            _TYPEWRITER_CONFIG_KEYS=AssistantModeWidget._TYPEWRITER_CONFIG_KEYS,
            typewriterModeToggled=SimpleNamespace(emit=MagicMock()),
        )
        target._apply_typewriter_mode = MethodType(
            AssistantModeWidget._apply_typewriter_mode,
            target,
        )
        return target

    def test_common_setter_updates_state_margin_and_view(self):
        editor = self._editor()
        editor.document().setModified(False)

        editor.set_typewriter_mode(True, base_bottom_margin=18)
        enabled_margin = (
            editor.document().rootFrame().frameFormat().bottomMargin()
        )

        self.assertTrue(editor.typewriter_enabled)
        self.assertAlmostEqual(
            enabled_margin,
            editor.viewport().height() / 2,
            delta=1.0,
        )
        self.assertFalse(editor.document().isModified())

        editor.set_typewriter_mode(False)
        disabled_margin = (
            editor.document().rootFrame().frameFormat().bottomMargin()
        )
        self.assertFalse(editor.typewriter_enabled)
        self.assertAlmostEqual(disabled_margin, 18.0, delta=0.1)

    def test_ai_four_tabs_restore_for_left_and_right_editors(self):
        config = {
            "tw_summary": True,
            "tw_draft": False,
            "tw_eval": True,
            "tw_completed": False,
        }
        target = self._assistant_target(config)

        AssistantModeWidget._restore_typewriter_modes(target)

        expected = {
            "요약": True,
            "초안": False,
            "평가": True,
            "완성본": False,
        }
        for panels in (target.left_panels, target.right_panels):
            for panel in panels[:4]:
                self.assertEqual(
                    panel.text_edit.typewriter_enabled,
                    expected[panel.step_name],
                )
        for settings in (target.left_panels[4], target.right_panels[4]):
            for step_name, checkbox_name in settings._TYPEWRITER_CHECKBOX_ATTRS.items():
                if step_name != "집필모드":
                    self.assertEqual(
                        getattr(settings, checkbox_name).isChecked(),
                        expected[step_name],
                    )
        target.pm.save_global_config.assert_not_called()
        target.typewriterModeToggled.emit.assert_not_called()

    def test_settings_panels_synchronize_without_recursive_toggle(self):
        target = self._assistant_target({})
        toggled = []
        for settings in (target.left_panels[4], target.right_panels[4]):
            settings.chk_tw_draft.toggled.connect(toggled.append)

        AssistantModeWidget._apply_typewriter_mode(
            target,
            "초안",
            True,
            persist=True,
            notify=True,
        )

        self.assertTrue(target.left_panels[0].text_edit.typewriter_enabled)
        self.assertTrue(target.right_panels[0].text_edit.typewriter_enabled)
        self.assertTrue(target.left_panels[4].chk_tw_draft.isChecked())
        self.assertTrue(target.right_panels[4].chk_tw_draft.isChecked())
        self.assertEqual(toggled, [])
        target.pm.save_global_config.assert_called_once_with()
        target.typewriterModeToggled.emit.assert_called_once_with("초안", True)

    def test_writing_left_and_right_use_common_setter(self):
        target = SimpleNamespace(
            pm=SimpleNamespace(global_config={"tw_writing": True}),
            left_editor=self._editor(),
            right_editor=self._editor(),
            pad_v=27,
        )

        WritingModeWidget.update_typewriter_setting(target)

        for editor in (target.left_editor, target.right_editor):
            self.assertTrue(editor.typewriter_enabled)
            editor.set_typewriter_mode(False)
            margin = editor.document().rootFrame().frameFormat().bottomMargin()
            self.assertAlmostEqual(margin, 27.0, delta=0.1)

    def test_mode_switches_do_not_change_editor_signal_receivers(self):
        editor = self._editor()
        stack = QStackedWidget()
        assistant_mode = QWidget()
        writing_mode = _WritingModeStub()
        stack.addWidget(assistant_mode)
        stack.addWidget(writing_mode)
        target = SimpleNamespace(
            mode_stack=stack,
            assistant_mode=assistant_mode,
            writing_mode=writing_mode,
        )
        self.addCleanup(stack.close)
        before = (
            editor.receivers(editor.cursorPositionChanged),
            editor.receivers(editor.textChanged),
        )

        for _ in range(20):
            MainWindow.switch_to_writing(target)
            MainWindow.switch_to_assistant(target)

        after = (
            editor.receivers(editor.cursorPositionChanged),
            editor.receivers(editor.textChanged),
        )
        self.assertEqual(after, before)

    def test_long_document_last_line_scrolls_in_qt_event_loop(self):
        editor = self._editor(height=240)
        editor.setPlainText("\n".join(f"line {index}" for index in range(160)))
        editor.set_typewriter_mode(True)
        cursor = editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        editor.setTextCursor(cursor)

        self.assertTrue(
            process_until(lambda: editor.verticalScrollBar().value() > 0)
        )
        cursor_center = editor.cursorRect().center().y()
        self.assertLessEqual(cursor_center, editor.viewport().height() / 2 + 8)

    def test_multiline_paste_scrolls_and_selection_does_not_recenter(self):
        editor = self._editor(height=220)
        editor.set_typewriter_mode(True)
        cursor = editor.textCursor()
        cursor.insertText("\n".join(f"pasted {index}" for index in range(120)))
        editor.setTextCursor(cursor)
        self.assertTrue(
            process_until(lambda: editor.verticalScrollBar().value() > 0)
        )

        selection = editor.textCursor()
        selection.setPosition(max(0, selection.position() - 30))
        selection.movePosition(
            QTextCursor.MoveOperation.End,
            QTextCursor.MoveMode.KeepAnchor,
        )
        editor.setTextCursor(selection)
        selected_scroll = editor.verticalScrollBar().value()
        editor.keep_cursor_centered()
        QApplication.processEvents()
        self.assertEqual(editor.verticalScrollBar().value(), selected_scroll)

    def test_split_editors_scroll_independently_with_shared_document(self):
        container = QWidget()
        layout = QHBoxLayout(container)
        left = SmartTextEdit()
        right = SmartTextEdit()
        right.setDocument(left.document())
        layout.addWidget(left)
        layout.addWidget(right)
        container.resize(900, 260)
        container.show()
        self.addCleanup(container.close)
        left.setPlainText("\n".join(f"shared {index}" for index in range(150)))
        left.set_typewriter_mode(True)
        right.set_typewriter_mode(True)
        for editor in (left, right):
            cursor = editor.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            editor.setTextCursor(cursor)

        self.assertTrue(
            process_until(
                lambda: left.verticalScrollBar().value() > 0
                and right.verticalScrollBar().value() > 0
            )
        )


class RealAssistantConstructionTestCase(unittest.TestCase):
    """Exercise the real widget tree without replacing its panels with stubs."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        project = "타자기 회귀 작품"
        (root / "작품목록" / project).mkdir(parents=True)
        (root / "config.json").write_text(
            json.dumps(
                {
                    "last_project": project,
                    "tw_summary": True,
                    "tw_draft": True,
                    "tw_eval": True,
                    "tw_completed": True,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        env = patch.dict(
            os.environ,
            {
                "ANTIGRAVITY_ROOT_DIR": str(root),
                "ANTIGRAVITY_APP_DATA_DIR": str(root / "appdata"),
                "ANTIGRAVITY_AUTO_PROJECT": project,
            },
            clear=False,
        )
        env.start()
        self.addCleanup(env.stop)

    def test_assistant_mode_builds_with_saved_typewriter_settings(self):
        # This widget owns application-wide tray and shutdown behavior. Exercise
        # its real tree in a process that can follow the same lifetime as the app
        # instead of leaking its deferred Qt teardown into unrelated widget tests.
        child = """
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication
from mode_assistant import AssistantModeWidget

app = QApplication.instance() or QApplication([])
widget = AssistantModeWidget()
restored = [
    panel.text_edit.typewriter_enabled
    for panel in widget.left_panels[:4] + widget.right_panels[:4]
]
assert len(restored) == 8
assert all(restored)
widget.close()
app.processEvents()
"""
        completed = subprocess.run(
            [sys.executable, "-X", "faulthandler", "-c", child],
            cwd=Path(__file__).resolve().parents[1],
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"assistant construction subprocess failed\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )

    def test_reloading_a_document_restores_the_typewriter_margin(self):
        """setPlainText 는 rootFrame 서식을 기본값으로 되돌린다.

        탭 전환과 화수 로드마다 일어나므로, 여백이 살아나지 않으면 스크롤
        여유가 0이 되어 커서가 뷰포트 맨 아래에 닿아야만 스크롤된다.
        """
        editor = SmartTextEdit()
        editor.resize(400, 300)
        editor.show()
        self.addCleanup(editor.close)
        self.addCleanup(editor.deleteLater)
        self.assertTrue(process_until(lambda: editor.viewport().height() > 0))
        editor.set_typewriter_mode(True)
        expected = editor.viewport().height() / 2
        self.assertAlmostEqual(
            editor.document().rootFrame().frameFormat().bottomMargin(),
            expected,
            delta=1,
        )

        # 문서를 새로 채우면 여백이 기본값으로 초기화된다.
        editor.setPlainText("새로 불러온 원고")
        cursor = editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        editor.setTextCursor(cursor)

        self.assertTrue(
            process_until(
                lambda: abs(
                    editor.document().rootFrame().frameFormat().bottomMargin()
                    - editor.viewport().height() / 2
                )
                <= 1
            )
        )

    def test_typewriter_scroll_starts_at_the_viewport_centre(self):
        editor = SmartTextEdit()
        editor.resize(480, 300)
        editor.show()
        self.addCleanup(editor.close)
        self.addCleanup(editor.deleteLater)
        self.assertTrue(process_until(lambda: editor.viewport().height() > 0))
        editor.set_typewriter_mode(True)
        editor.setPlainText("")
        self.assertTrue(process_until(lambda: editor.verticalScrollBar().value() == 0))

        centre = editor.viewport().height() / 2
        cursor_y_at_first_scroll = None
        for index in range(60):
            cursor = editor.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            editor.setTextCursor(cursor)
            editor.insertPlainText(f"line {index}\n")
            process_until(lambda: True, timeout_ms=10)
            if editor.verticalScrollBar().value() > 0:
                cursor_y_at_first_scroll = editor.cursorRect().center().y()
                break

        self.assertIsNotNone(cursor_y_at_first_scroll)
        # 뷰포트 맨 아래가 아니라 중앙에서 스크롤이 시작돼야 한다.
        self.assertLess(abs(cursor_y_at_first_scroll - centre), centre * 0.35)

    def test_hidden_editor_defers_document_layout_until_shown(self):
        editor = SmartTextEdit()
        self.addCleanup(editor.deleteLater)
        base_margin = editor.document().rootFrame().frameFormat().bottomMargin()

        editor.set_typewriter_mode(True)

        # 숨어 있는 동안에는 문서를 건드리지 않는다.
        self.assertTrue(editor.typewriter_enabled)
        self.assertEqual(
            editor.document().rootFrame().frameFormat().bottomMargin(),
            base_margin,
        )

        editor.resize(400, 300)
        editor.show()
        self.addCleanup(editor.close)
        self.assertTrue(
            process_until(
                lambda: editor.document().rootFrame().frameFormat().bottomMargin()
                > base_margin
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
