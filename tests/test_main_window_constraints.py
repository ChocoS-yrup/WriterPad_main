import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtWidgets import QApplication, QWidget

from main import MIN_MAIN_WINDOW_HEIGHT, MIN_MAIN_WINDOW_WIDTH, MainWindow
from project_dialogs import ProjectSelectionDialog


class _ProjectManagerStub:
    def __init__(self):
        self.current_project = "크기 제한 테스트"
        self.global_config = {}

    def save_global_config(self):
        pass


class _AssistantModeStub(QWidget):
    switchModeRequested = pyqtSignal()
    sendToWritingModeRequested = pyqtSignal(str)
    typewriterModeToggled = pyqtSignal(str, bool)

    def __init__(self):
        super().__init__()
        self.pm = _ProjectManagerStub()


class _WritingModeStub(QWidget):
    switchModeRequested = pyqtSignal()
    sendToAssistantRequested = pyqtSignal(str)

    def __init__(self, _pm):
        super().__init__()
        self.active_editor = None
        self.left_editor = SimpleNamespace(append=lambda _text: None)


class MainWindowConstraintTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_window_cannot_shrink_below_readable_height(self):
        with patch("main.AssistantModeWidget", _AssistantModeStub), patch(
            "main.WritingModeWidget", _WritingModeStub
        ):
            window = MainWindow()
            window.resize(300, 300)

        self.assertEqual(window.minimumWidth(), MIN_MAIN_WINDOW_WIDTH)
        self.assertEqual(window.minimumHeight(), MIN_MAIN_WINDOW_HEIGHT)
        self.assertGreaterEqual(window.width(), MIN_MAIN_WINDOW_WIDTH)
        self.assertGreaterEqual(window.height(), MIN_MAIN_WINDOW_HEIGHT)
        window.close()

    def test_project_selection_dialog_stays_above_other_windows_on_startup(self):
        project_manager = SimpleNamespace(
            global_config={},
            get_all_projects=lambda: [],
        )
        dialog = ProjectSelectionDialog(project_manager)

        self.assertTrue(
            dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
        )
        dialog.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
