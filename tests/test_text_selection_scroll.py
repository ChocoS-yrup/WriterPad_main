import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from text_editor import SmartTextEdit


class TextSelectionScrollTestCase(unittest.TestCase):
    def test_typewriter_centering_is_paused_while_text_is_selected(self):
        cursor = SimpleNamespace(hasSelection=lambda: True)
        editor = SimpleNamespace(
            typewriter_enabled=True,
            textCursor=lambda: cursor,
            cursorRect=MagicMock(),
        )

        SmartTextEdit.keep_cursor_centered(editor)

        editor.cursorRect.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
