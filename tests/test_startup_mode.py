import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QWidget

from main import MainWindow
from mode_writing import WritingModeWidget
from project_manager import ProjectManager, startup_mode_from_config
from settings_panel import SettingsPanel


class _AssistantModeStub(QWidget):
    switchModeRequested = pyqtSignal()
    sendToWritingModeRequested = pyqtSignal(str)
    typewriterModeToggled = pyqtSignal(str, bool)

    def __init__(self, pm):
        super().__init__()
        self.pm = pm


class _WritingModeStub(QWidget):
    switchModeRequested = pyqtSignal()
    sendToAssistantRequested = pyqtSignal(str)
    editorSessionRestored = pyqtSignal()

    def __init__(self, pm):
        super().__init__()
        self.pm = pm
        self.active_editor = None
        self.left_editor = SimpleNamespace(append=lambda _text: None)
        self._saved_files_loaded = False
        self.restore_runs = 0
        self.activate_calls = 0
        self.restored_file = None
        self.events = []
        QTimer.singleShot(0, self._restore_saved_editor)

    def _restore_saved_editor(self):
        if self._saved_files_loaded:
            return
        self.restore_runs += 1
        if self.pm.current_project:
            self.restored_file = self.pm.global_config.get(
                "writing_last_left_file"
            )
        self._saved_files_loaded = True
        self.events.append("editor-restored")
        self.editorSessionRestored.emit()

    def activate_current_editor_input(self):
        self.activate_calls += 1
        self.events.append("editor-activated")


class _ComboStub:
    def __init__(self, value):
        self.value = value

    def itemData(self, _index):
        return self.value


class StartupModeTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.env = patch.dict(
            os.environ,
            {"ANTIGRAVITY_ROOT_DIR": self.temp_dir.name},
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def _manager(self):
        manager = ProjectManager()
        manager.current_project = manager.global_config.get("last_project") or None
        return manager

    def _save_config(self, startup_mode=..., project="테스트 작품", last_file=None):
        manager = self._manager()
        if startup_mode is ...:
            manager.global_config.pop("startup_mode", None)
        else:
            manager.global_config["startup_mode"] = startup_mode
        manager.global_config["last_project"] = project or ""
        if last_file is None:
            manager.global_config.pop("writing_last_left_file", None)
        else:
            manager.global_config["writing_last_left_file"] = last_file
        manager.save_global_config()

    def _new_window(self):
        def assistant_factory():
            return _AssistantModeStub(self._manager())

        with patch("main.AssistantModeWidget", assistant_factory), patch(
            "main.WritingModeWidget", _WritingModeStub
        ):
            window = MainWindow()
        self.addCleanup(window.close)
        return window

    def _process_startup_restore(self):
        self.app.processEvents()

    def test_writing_save_then_recreated_window_restores_writing(self):
        self._save_config("assistant")
        first_window = self._new_window()
        self._process_startup_restore()

        target = SimpleNamespace(
            pm=first_window.assistant_mode.pm,
            combo_startup_mode=_ComboStub("writing"),
        )
        SettingsPanel.save_startup_mode(target, 0)
        first_window.close()

        restarted = self._new_window()
        self.assertIs(restarted.mode_stack.currentWidget(), restarted.assistant_mode)
        self._process_startup_restore()

        self.assertIs(restarted.mode_stack.currentWidget(), restarted.writing_mode)
        self.assertEqual(restarted.writing_mode.activate_calls, 1)

    def test_assistant_save_then_recreated_window_selects_assistant(self):
        self._save_config("writing")
        first_window = self._new_window()
        self._process_startup_restore()

        target = SimpleNamespace(
            pm=first_window.assistant_mode.pm,
            combo_startup_mode=_ComboStub("assistant"),
        )
        SettingsPanel.save_startup_mode(target, 0)
        first_window.close()

        restarted = self._new_window()
        self._process_startup_restore()

        self.assertIs(restarted.mode_stack.currentWidget(), restarted.assistant_mode)
        self.assertEqual(restarted.writing_mode.activate_calls, 0)

    def test_missing_null_wrong_types_and_unknown_values_fall_back(self):
        invalid_values = (..., None, 7, ["writing"], "unknown")

        for value in invalid_values:
            with self.subTest(value=value):
                self._save_config(value)
                window = self._new_window()
                self._process_startup_restore()
                self.assertIs(
                    window.mode_stack.currentWidget(), window.assistant_mode
                )
                self.assertEqual(window.writing_mode.activate_calls, 0)
                window.close()

    def test_writing_waits_for_editor_restore_then_activates_once(self):
        last_file = "메인/원고/마지막 문서.txt"
        self._save_config("writing", last_file=last_file)
        window = self._new_window()

        self.assertIs(window.mode_stack.currentWidget(), window.assistant_mode)
        self.assertEqual(window.writing_mode.activate_calls, 0)
        self._process_startup_restore()

        self.assertEqual(window.writing_mode.restored_file, last_file)
        self.assertEqual(
            window.writing_mode.events,
            ["editor-restored", "editor-activated"],
        )
        self.assertEqual(window.writing_mode.activate_calls, 1)
        window.writing_mode.editorSessionRestored.emit()
        self.assertEqual(window.writing_mode.activate_calls, 1)
        self.assertEqual(window.writing_mode.restore_runs, 1)

    def test_writing_start_without_project_is_safe(self):
        self._save_config(
            "writing",
            project=None,
            last_file="메인/원고/이전 프로젝트.txt",
        )
        window = self._new_window()
        self._process_startup_restore()

        self.assertIsNone(window.assistant_mode.pm.current_project)
        self.assertIsNone(window.writing_mode.restored_file)
        self.assertIs(window.mode_stack.currentWidget(), window.writing_mode)
        self.assertEqual(window.writing_mode.activate_calls, 1)

    def test_five_consecutive_restarts_each_switch_once(self):
        self._save_config("writing", last_file="메인/원고/연속 실행.txt")

        for restart in range(5):
            with self.subTest(restart=restart):
                window = self._new_window()
                self._process_startup_restore()
                self.assertIs(
                    window.mode_stack.currentWidget(), window.writing_mode
                )
                self.assertEqual(window.writing_mode.restore_runs, 1)
                self.assertEqual(window.writing_mode.activate_calls, 1)
                window.close()

    def test_mode_resolution_never_mutates_config(self):
        config = {"startup_mode": ["writing"]}

        self.assertEqual(startup_mode_from_config(config), "assistant")
        self.assertEqual(config, {"startup_mode": ["writing"]})

    def test_startup_selection_does_not_reload_or_save_config(self):
        manager = SimpleNamespace(
            current_project="테스트 작품",
            global_config={"startup_mode": "assistant"},
            load_global_config=MagicMock(),
            save_global_config=MagicMock(),
        )

        with patch(
            "main.AssistantModeWidget",
            lambda: _AssistantModeStub(manager),
        ), patch("main.WritingModeWidget", _WritingModeStub):
            window = MainWindow()
        self.addCleanup(window.close)
        self._process_startup_restore()

        manager.load_global_config.assert_not_called()
        manager.save_global_config.assert_not_called()


class WritingSessionRestoreTestCase(unittest.TestCase):
    def _target(self, root, config):
        return SimpleNamespace(
            _saved_files_loaded=False,
            pm=SimpleNamespace(global_config=config),
            wpm=SimpleNamespace(writing_root_path=root),
            _initial_last_active="left",
            left_editor=object(),
            right_editor=object(),
            btn_toggle_split=SimpleNamespace(isChecked=lambda: False),
            set_active_editor=MagicMock(),
            _open_file_by_path=MagicMock(),
            update_editor_statistics=MagicMock(),
            _refresh_storage_status_for_editor_state=MagicMock(),
            editorSessionRestored=SimpleNamespace(emit=MagicMock()),
        )

    def test_last_document_restore_and_completion_signal_run_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            relative_path = "메인/원고/마지막 문서.txt"
            document = Path(temp_dir, *relative_path.split("/"))
            document.parent.mkdir(parents=True)
            document.write_text("test", encoding="utf-8")
            target = self._target(
                temp_dir,
                {"writing_last_left_file": relative_path},
            )

            WritingModeWidget.load_saved_files(target)
            WritingModeWidget.load_saved_files(target)

        target._open_file_by_path.assert_called_once_with(relative_path)
        target.editorSessionRestored.emit.assert_called_once_with()
        self.assertTrue(target._saved_files_loaded)

    def test_missing_project_skips_stale_document_without_error(self):
        target = self._target(
            None,
            {"writing_last_left_file": "메인/원고/이전 프로젝트.txt"},
        )

        WritingModeWidget.load_saved_files(target)

        target._open_file_by_path.assert_not_called()
        target.editorSessionRestored.emit.assert_called_once_with()


if __name__ == "__main__":
    unittest.main(verbosity=2)
