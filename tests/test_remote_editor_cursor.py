"""Remote manuscript updates should leave the writer ready to append."""
import unittest
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock

from tests.qt_app import APP
from mode_writing import WritingModeWidget
from text_editor import SmartTextEdit


class RemoteEditorCursorTests(unittest.TestCase):
    def setUp(self):
        self.path = 'main/manuscript/chapter.txt'
        self.left = SmartTextEdit()
        self.right = SmartTextEdit()
        self.addCleanup(self.left.deleteLater)
        self.addCleanup(self.right.deleteLater)
        self.panel = SimpleNamespace(
            controller=MagicMock(), loaded_versions={},
            current_loaded_file_left=self.path,
            current_loaded_file_right=self.path,
            left_editor=self.left, right_editor=self.right,
            lbl_current_doc=MagicMock(), lbl_r_doc=MagicMock(),
            is_dirty_left=False, is_dirty_right=False,
            pad_h=73, pad_v=31,
            _schedule_remote_tree_refresh=MagicMock(),
            _refresh_storage_status_for_editor_state=MagicMock(),
        )
        self.panel.apply_editor_margins = MethodType(
            WritingModeWidget.apply_editor_margins, self.panel
        )

    def apply(self, content):
        WritingModeWidget.on_remote_documents_applied(self.panel, [{
            'old_local_path': self.path, 'new_local_path': self.path,
            'content': content, 'revision': 2, 'is_deleted': False,
        }])

    def test_updated_manuscript_places_both_cursors_at_end_without_edit_signal(self):
        remote = 'iPad에서 쓴 원고\n마지막 문장입니다. ✍️😀'
        changed = MagicMock()
        for editor in (self.left, self.right):
            editor.setPlainText('이전 원고')
            editor.textChanged.connect(changed)
        self.apply(remote)
        for editor in (self.left, self.right):
            self.assertEqual(editor.toPlainText(), remote)
            self.assertTrue(editor.textCursor().atEnd())
            self.assertFalse(editor.document().isModified())
            self.assertFalse(editor.signalsBlocked())
        changed.assert_not_called()

    def test_unchanged_refresh_keeps_cursor_and_selection(self):
        from PyQt6.QtGui import QTextCursor
        for editor in (self.left, self.right):
            editor.setPlainText('기존 문장 수정 중')
            cursor = editor.textCursor()
            cursor.setPosition(2)
            cursor.setPosition(5, QTextCursor.MoveMode.KeepAnchor)
            editor.setTextCursor(cursor)
        self.apply('기존 문장 수정 중')
        for editor in (self.left, self.right):
            self.assertEqual(editor.textCursor().position(), 5)
            self.assertEqual(editor.textCursor().anchor(), 2)

    def test_empty_remote_document_has_valid_end_cursor(self):
        self.left.setPlainText('이전 문장')
        self.apply('')
        self.assertEqual(self.left.textCursor().position(), 0)
        self.assertTrue(self.left.textCursor().atEnd())

    def test_remote_update_restores_custom_margins_and_typewriter_layout(self):
        for typewriter in (False, True):
            with self.subTest(typewriter=typewriter):
                changed = MagicMock()
                for editor in (self.left, self.right):
                    editor.resize(500, 400)
                    editor.show()
                    editor.setPlainText('동기화 이전')
                    editor.set_typewriter_mode(typewriter)
                APP.processEvents()
                self.panel.apply_editor_margins()
                for editor in (self.left, self.right):
                    editor.textChanged.connect(changed)
                self.apply('동기화로 변경된 문장\n마지막 줄')
                APP.processEvents()
                for editor in (self.left, self.right):
                    fmt = editor.document().rootFrame().frameFormat()
                    self.assertEqual(fmt.leftMargin(), 73)
                    self.assertEqual(fmt.rightMargin(), 73)
                    self.assertEqual(fmt.topMargin(), 31)
                    expected_bottom = editor.viewport().height() / 2 if typewriter else 31
                    self.assertAlmostEqual(fmt.bottomMargin(), expected_bottom)
                    self.assertTrue(editor.textCursor().atEnd())
                    self.assertFalse(editor.document().isModified())
                    editor.textChanged.disconnect(changed)
                    editor.hide()
                changed.assert_not_called()

    def test_remote_update_does_not_change_other_pane_view(self):
        self.panel.current_loaded_file_right = 'other.txt'
        self.right.setPlainText('다른 창에서 수정 중인 문장')
        cursor = self.right.textCursor()
        cursor.setPosition(4)
        self.right.setTextCursor(cursor)
        self.right.document().setModified(True)
        original_format = self.right.document().rootFrame().frameFormat()
        self.apply('동기화된 문장')
        self.assertEqual(self.right.textCursor().position(), 4)
        self.assertTrue(self.right.document().isModified())
        self.assertEqual(self.right.document().rootFrame().frameFormat(), original_format)


if __name__ == '__main__':
    unittest.main()
