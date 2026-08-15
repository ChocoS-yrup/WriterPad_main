import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QTextCursor, QInputMethodEvent

from text_editor import SmartTextEdit


class EllipsisInputTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.editor = SmartTextEdit()
        self.editor.show()
        self.editor.setFocus()

    def tearDown(self):
        self.editor.close()

    def test_direct_three_periods_become_single_midline_ellipsis(self):
        QTest.keyClicks(self.editor, "...")

        self.assertEqual(self.editor.toPlainText(), "⋯")
        self.assertEqual(len(self.editor.toPlainText()), 1)

    def test_midline_ellipsis_keeps_the_same_line_height_as_regular_text(self):
        self.editor.setPlainText("가나다")
        normal_height = self.editor.document().documentLayout().blockBoundingRect(
            self.editor.document().firstBlock()
        ).height()

        self.editor.setPlainText("가⋯다")
        ellipsis_height = self.editor.document().documentLayout().blockBoundingRect(
            self.editor.document().firstBlock()
        ).height()

        self.assertEqual(self.editor.toPlainText(), "가⋯다")
        self.assertEqual(ellipsis_height, normal_height)

    def test_midline_ellipsis_uses_visual_outline_without_larger_font(self):
        QTest.keyClicks(self.editor, "...")

        cursor = self.editor.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(1, QTextCursor.MoveMode.KeepAnchor)
        char_format = cursor.charFormat()

        self.assertTrue(
            char_format.hasProperty(self.editor._ELLIPSIS_FORMAT_PROPERTY)
        )
        self.assertGreater(char_format.textOutline().widthF(), 0)
        self.assertEqual(char_format.fontPointSize(), 0)

    def test_loaded_midline_ellipsis_restores_visual_format(self):
        self.editor.setPlainText("가⋯다")

        cursor = self.editor.textCursor()
        cursor.setPosition(1)
        cursor.setPosition(2, QTextCursor.MoveMode.KeepAnchor)

        self.assertGreater(cursor.charFormat().textOutline().widthF(), 0)

    def test_text_after_converted_ellipsis_does_not_inherit_outline(self):
        QTest.keyClicks(self.editor, "...a")

        cursor = self.editor.textCursor()
        cursor.setPosition(1)
        cursor.setPosition(2, QTextCursor.MoveMode.KeepAnchor)

        self.assertEqual(self.editor.toPlainText(), "⋯a")
        self.assertEqual(
            cursor.charFormat().textOutline().style(),
            Qt.PenStyle.NoPen,
        )

    def test_backspace_and_undo_handle_converted_ellipsis_as_one_action(self):
        QTest.keyClicks(self.editor, "...")
        QTest.keyClick(self.editor, Qt.Key.Key_Backspace)
        self.assertEqual(self.editor.toPlainText(), "")

        self.editor.undo()
        self.assertEqual(self.editor.toPlainText(), "⋯")
        self.editor.undo()
        self.assertEqual(self.editor.toPlainText(), "")

    def test_pasted_periods_are_not_converted(self):
        QApplication.clipboard().setText("...")

        self.editor.paste()

        self.assertEqual(self.editor.toPlainText(), "...")

    def test_ime_commit_does_not_apply_direct_input_conversion(self):
        event = QInputMethodEvent()
        event.setCommitString("...")

        self.editor.inputMethodEvent(event)

        self.assertEqual(self.editor.toPlainText(), "...")

    def test_existing_periods_are_not_rewritten_by_other_edits(self):
        self.editor.setPlainText("기존 ... 및 …")
        self.editor.moveCursor(QTextCursor.MoveOperation.End)
        QTest.keyClicks(self.editor, "a")

        self.assertEqual(self.editor.toPlainText(), "기존 ... 및 …a")

    def test_read_only_editor_rejects_smart_pair_and_scene_separator_input(self):
        self.editor.setReadOnly(True)

        QTest.keyClicks(self.editor, "'\"([{*")

        self.assertEqual(self.editor.toPlainText(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
