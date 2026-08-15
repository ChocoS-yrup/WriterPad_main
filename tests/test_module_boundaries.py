import tempfile
import unittest
from pathlib import Path

import mode_assistant
import mode_writing
import ui_components
from ai_panel import AIPanelWidget, AIPanelWidgetBase, ChatBubble, ChatInputEdit
from app_config import get_config, get_saved_font, save_config, save_font_to_json
from assistant_runtime import AIGenerationWorker, AutoCloseMessageBox, FileSaveWorker, SingleApplication
from assistant_workflow import AssistantWorkflowMixin
from chapter_selector import DigitLabel, SmartChapterSelector
from editor_panel import EditorPanel
from model_selector import ModelDiscoveryWorker, ModelSelector
from project_dialogs import ProjectManagementDialog, ProjectSelectionDialog
from settings_panel import SettingsPanel
from text_editor import SmartTextEdit
from writing_backup import HistoryViewerDialog
from writing_extraction import PartialExtractionDialog, WritingExtractionMixin
from writing_search import GlobalSearchDialog, GlobalSearchWorker, LocalSearchDialog
from writing_tree import WritingTreeMixin


class ModuleBoundaryTestCase(unittest.TestCase):
    """기존 import 경로를 유지하면서 구현 클래스가 역할별 모듈에 있는지 확인한다."""

    def test_ui_components_remains_a_compatible_facade(self):
        expected_exports = {
            "AIPanelWidget": AIPanelWidget,
            "AIPanelWidgetBase": AIPanelWidgetBase,
            "ChatBubble": ChatBubble,
            "ChatInputEdit": ChatInputEdit,
            "DigitLabel": DigitLabel,
            "SmartChapterSelector": SmartChapterSelector,
            "EditorPanel": EditorPanel,
            "ModelDiscoveryWorker": ModelDiscoveryWorker,
            "ModelSelector": ModelSelector,
            "ProjectManagementDialog": ProjectManagementDialog,
            "ProjectSelectionDialog": ProjectSelectionDialog,
            "SettingsPanel": SettingsPanel,
            "SmartTextEdit": SmartTextEdit,
            "get_config": get_config,
            "get_saved_font": get_saved_font,
            "save_config": save_config,
            "save_font_to_json": save_font_to_json,
        }
        for name, implementation in expected_exports.items():
            self.assertIs(getattr(ui_components, name), implementation, name)

    def test_mode_modules_reexport_moved_runtime_and_dialog_classes(self):
        self.assertIs(mode_assistant.FileSaveWorker, FileSaveWorker)
        self.assertIs(mode_assistant.AutoCloseMessageBox, AutoCloseMessageBox)
        self.assertIs(mode_assistant.SingleApplication, SingleApplication)
        self.assertIs(mode_assistant.AIGenerationWorker, AIGenerationWorker)
        self.assertIs(mode_writing.GlobalSearchDialog, GlobalSearchDialog)
        self.assertIs(mode_writing.LocalSearchDialog, LocalSearchDialog)
        self.assertIs(mode_writing.HistoryViewerDialog, HistoryViewerDialog)

    def test_large_mode_widgets_delegate_feature_groups_to_mixins(self):
        self.assertTrue(issubclass(mode_writing.WritingModeWidget, WritingTreeMixin))
        self.assertTrue(issubclass(mode_writing.WritingModeWidget, WritingExtractionMixin))
        self.assertEqual(mode_writing.WritingModeWidget.load_tree_data.__module__, "writing_tree")
        self.assertEqual(mode_writing.WritingModeWidget.extract_all_chapters.__module__, "writing_extraction")
        self.assertIs(mode_writing.PartialExtractionDialog, PartialExtractionDialog)
        self.assertTrue(issubclass(mode_assistant.AssistantModeWidget, AssistantWorkflowMixin))
        self.assertEqual(mode_assistant.AssistantModeWidget.handle_ai_generation.__module__, "assistant_workflow")

    def test_file_save_worker_still_writes_content_after_extraction(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir, "saved.txt")
            worker = FileSaveWorker(str(target), "분리 후 저장 내용")

            worker.run()

            self.assertEqual(target.read_text(encoding="utf-8"), "분리 후 저장 내용")

    def test_global_search_worker_still_skips_backup_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manuscript = root / "메인" / "원고"
            backup = root / "백업" / "자동저장"
            manuscript.mkdir(parents=True)
            backup.mkdir(parents=True)
            (manuscript / "001화.txt").write_text("찾을 문장", encoding="utf-8")
            (backup / "001화_백업.txt").write_text("찾을 문장", encoding="utf-8")
            emitted = []
            worker = GlobalSearchWorker(str(root), "찾을")
            worker.finished.connect(emitted.append)

            worker.run()

            self.assertEqual(len(emitted), 1)
            self.assertEqual(len(emitted[0]), 1)
            self.assertTrue(emitted[0][0][0].endswith("001화.txt"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
