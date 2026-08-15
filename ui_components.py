"""기존 UI import 경로를 유지하는 호환성 모듈.

실제 구현은 역할별 모듈에 있으며, 기존 호출부는 이 모듈을 계속 사용할 수 있다.
"""

from ai_panel import AIPanelWidget, AIPanelWidgetBase, ChatBubble, ChatInputEdit
from app_config import get_config, get_saved_font, save_config, save_font_to_json
from chapter_selector import DigitLabel, SmartChapterSelector
from editor_panel import EditorPanel
from model_selector import ModelDiscoveryWorker, ModelSelector
from project_dialogs import ProjectManagementDialog, ProjectSelectionDialog
from settings_panel import SettingsPanel
from text_editor import SmartTextEdit

__all__ = [
    "AIPanelWidget",
    "AIPanelWidgetBase",
    "ChatBubble",
    "ChatInputEdit",
    "DigitLabel",
    "EditorPanel",
    "ModelDiscoveryWorker",
    "ModelSelector",
    "ProjectManagementDialog",
    "ProjectSelectionDialog",
    "SettingsPanel",
    "SmartChapterSelector",
    "SmartTextEdit",
    "get_config",
    "get_saved_font",
    "save_config",
    "save_font_to_json",
]
