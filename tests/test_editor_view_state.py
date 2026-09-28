import os
import json
import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QApplication, QTextEdit

from mode_writing import WritingModeWidget
from text_editor import SmartTextEdit


class EditorViewStateTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.left = SmartTextEdit()
        self.right = SmartTextEdit()
        for editor in (self.left, self.right):
            editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
            editor.resize(320, 120)
            editor.show()
        self.pm = SimpleNamespace(
            current_project="커서 기억 작품",
            global_config={},
            save_global_config=MagicMock(),
        )
        self.panel = SimpleNamespace(
            pm=self.pm,
            left_editor=self.left,
            right_editor=self.right,
            current_loaded_file_left="메인/원고/왼쪽.txt",
            current_loaded_file_right="메인/원고/오른쪽.txt",
            _EDITOR_VIEW_STATE_KEY=WritingModeWidget._EDITOR_VIEW_STATE_KEY,
            _EDITOR_VIEW_STATE_LIMIT=WritingModeWidget._EDITOR_VIEW_STATE_LIMIT,
        )
        for method_name in (
            "_remember_editor_view_state",
            "_saved_editor_view_state",
            "_restore_editor_view_state",
            "persist_editor_view_states",
        ):
            setattr(
                self.panel,
                method_name,
                MethodType(getattr(WritingModeWidget, method_name), self.panel),
            )
        self.app.processEvents()

    def tearDown(self):
        self.left.close()
        self.right.close()
        self.app.processEvents()

    def _remember(self, editor, rel_path=None):
        return WritingModeWidget._remember_editor_view_state(
            self.panel, editor, rel_path
        )

    def _restore(self, editor, rel_path):
        return WritingModeWidget._restore_editor_view_state(
            self.panel, editor, rel_path
        )

    def test_left_and_right_positions_are_saved_independently(self):
        text = "\n".join(f"{index:03d}번째 줄 " + ("긴 문장 " * 20) for index in range(200))
        self.left.setPlainText(text)
        self.right.setPlainText(text)
        left_cursor = self.left.textCursor()
        left_cursor.setPosition(137)
        self.left.setTextCursor(left_cursor)
        right_cursor = self.right.textCursor()
        right_cursor.setPosition(731)
        self.right.setTextCursor(right_cursor)
        self.left.verticalScrollBar().setValue(240)
        self.right.verticalScrollBar().setValue(520)
        self.left.horizontalScrollBar().setValue(35)
        self.right.horizontalScrollBar().setValue(75)

        self.assertTrue(self._remember(self.left))
        self.assertTrue(self._remember(self.right))

        states = self.pm.global_config[WritingModeWidget._EDITOR_VIEW_STATE_KEY][
            "커서 기억 작품"
        ]
        self.assertEqual(states["left"]["메인/원고/왼쪽.txt"]["cursor"], 137)
        self.assertEqual(states["right"]["메인/원고/오른쪽.txt"]["cursor"], 731)
        self.assertEqual(
            states["left"]["메인/원고/왼쪽.txt"]["vertical_scroll"], 240
        )
        self.assertEqual(
            states["right"]["메인/원고/오른쪽.txt"]["horizontal_scroll"], 75
        )

    def test_restore_clamps_cursor_and_restores_scroll_after_layout(self):
        text = "\n".join(f"{index:03d}번째 줄 " + ("긴 문장 " * 20) for index in range(200))
        self.left.setPlainText(text)
        cursor = self.left.textCursor()
        cursor.setPosition(650)
        self.left.setTextCursor(cursor)
        self.left.verticalScrollBar().setValue(420)
        self.left.horizontalScrollBar().setValue(60)
        self._remember(self.left)

        self.left.setPlainText(text)
        self.left.moveCursor(QTextCursor.MoveOperation.Start)
        self.left.verticalScrollBar().setValue(0)
        self.left.horizontalScrollBar().setValue(0)

        self.assertTrue(self._restore(self.left, "메인/원고/왼쪽.txt"))
        self.app.processEvents()

        self.assertEqual(self.left.textCursor().position(), 650)
        self.assertEqual(self.left.verticalScrollBar().value(), 420)
        self.assertEqual(self.left.horizontalScrollBar().value(), 60)

        short_path = "메인/원고/짧은문서.txt"
        self.panel.current_loaded_file_left = short_path
        states = self.pm.global_config[WritingModeWidget._EDITOR_VIEW_STATE_KEY][
            "커서 기억 작품"
        ]["left"]
        self.left.setPlainText("짧은 문서")
        self._remember(self.left)
        states[short_path].update({
            "cursor": 9999,
            "vertical_scroll": 9999,
            "horizontal_scroll": 9999,
        })

        self.assertTrue(self._restore(self.left, short_path))
        self.app.processEvents()
        self.assertEqual(self.left.textCursor().position(), len("짧은 문서"))
        self.assertLessEqual(
            self.left.verticalScrollBar().value(),
            self.left.verticalScrollBar().maximum(),
        )

    def test_state_is_scoped_by_project_and_document_path(self):
        self.left.setPlainText("첫 번째 작품")
        cursor = self.left.textCursor()
        cursor.setPosition(3)
        self.left.setTextCursor(cursor)
        self._remember(self.left)

        self.pm.current_project = "다른 작품"
        self.assertFalse(self._restore(self.left, "메인/원고/왼쪽.txt"))

        self.pm.current_project = "커서 기억 작품"
        self.assertFalse(self._restore(self.left, "메인/원고/없는문서.txt"))

    def test_persist_saves_both_panes_in_one_config_write(self):
        self.left.setPlainText("왼쪽 문서")
        self.right.setPlainText("오른쪽 문서")

        WritingModeWidget.persist_editor_view_states(self.panel)

        self.pm.save_global_config.assert_called_once_with()
        states = self.pm.global_config[WritingModeWidget._EDITOR_VIEW_STATE_KEY][
            "커서 기억 작품"
        ]
        self.assertIn("메인/원고/왼쪽.txt", states["left"])
        self.assertIn("메인/원고/오른쪽.txt", states["right"])

    def _open_synced_file(self, side, old_text, new_text, *, legacy=False):
        editor = self.left if side == 'left' else self.right
        path = '메인/원고/동기화.txt'
        setattr(self.panel, 'current_loaded_file_' + side, path)
        editor.setPlainText(old_text)
        editor.moveCursor(QTextCursor.MoveOperation.Start)
        self._remember(editor)
        state = self.panel._saved_editor_view_state(editor, path)
        if legacy:
            state.pop('content_sha256', None)
        # Persisted settings must also work across restart, not just in memory.
        self.pm.global_config = json.loads(json.dumps(self.pm.global_config))
        setattr(self.panel, 'current_loaded_file_' + side, '메인/원고/다른문서.txt')
        editor.setPlainText('Windows에서 확인 중인 다른 문서')
        self.panel.active_editor = editor
        self.panel.is_dirty_left = self.panel.is_dirty_right = False
        self.panel.controller = MagicMock()
        self.panel.lbl_current_doc = MagicMock()
        self.panel.lbl_r_doc = MagicMock()
        self.panel.update_typewriter_setting = MagicMock()
        self.panel.apply_editor_margins = MagicMock()
        self.panel.isVisible = lambda: False
        with tempfile.TemporaryDirectory() as root:
            target = Path(root, path)
            target.parent.mkdir(parents=True)
            target.write_text(new_text, encoding='utf-8')
            self.panel.wpm = SimpleNamespace(
                writing_root_path=root,
                read_text_file=lambda relative: Path(root, relative).read_text(encoding='utf-8'),
            )
            WritingModeWidget._open_file_by_path(self.panel, path)
            self.app.processEvents()
        self.assertEqual(editor.toPlainText(), new_text)
        self.assertFalse(editor.document().isModified())
        return editor

    def test_unopened_synced_document_opens_at_end_in_either_pane(self):
        for side in ('left', 'right'):
            with self.subTest(side=side):
                editor = self._open_synced_file(side, '이전 원고', 'iPad에서 추가한 원고\n마지막 문장 😀')
                self.assertTrue(editor.textCursor().atEnd())

    def test_same_length_remote_edit_invalidates_old_position(self):
        editor = self._open_synced_file('left', '가나다라마', '가나다바마')
        self.assertTrue(editor.textCursor().atEnd())

    def test_unchanged_document_keeps_saved_position_when_reopened(self):
        editor = self._open_synced_file('left', '변하지 않은 원고', '변하지 않은 원고')
        self.assertEqual(editor.textCursor().position(), 0)

    def test_legacy_position_without_content_identity_opens_at_end(self):
        editor = self._open_synced_file('left', '예전 내용', '새로운 내용', legacy=True)
        self.assertTrue(editor.textCursor().atEnd())

    def test_saved_cursor_after_emoji_uses_qt_character_positions(self):
        self.left.setPlainText('😀😀😀 끝')
        self.left.moveCursor(QTextCursor.MoveOperation.End)
        self._remember(self.left)
        self.left.moveCursor(QTextCursor.MoveOperation.Start)
        self.assertTrue(self._restore(self.left, self.panel.current_loaded_file_left))
        self.assertTrue(self.left.textCursor().atEnd())

    def test_deferred_scroll_restore_ignores_replaced_document(self):
        self.left.setPlainText('이전 문서')
        self._remember(self.left)
        with patch('mode_writing.QTimer.singleShot') as deferred:
            self.assertTrue(self._restore(self.left, self.panel.current_loaded_file_left))
        callback = deferred.call_args.args[1]
        self.left.setPlainText('동기화 후 다른 내용')
        with patch.object(self.left.verticalScrollBar(), 'setValue') as scroll:
            callback()
            scroll.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
