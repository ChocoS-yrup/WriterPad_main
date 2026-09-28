import json
import os
import sys

from PyQt6.QtGui import QFont
from runtime_profile import root_dir


# Match ProjectManager's storage root. Launchers and shortcuts may start the
# executable in a read-only directory unrelated to the installed application.
_settings_root = root_dir(
    os.path.dirname(sys.executable)
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__))
)
FONT_CONFIG_FILE = os.path.join(_settings_root, "fonts.json")

def get_saved_font():
    if os.path.exists(FONT_CONFIG_FILE):
        try:
            with open(FONT_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "font_family" in data:
                    font = QFont(data["font_family"], data.get("font_size", 14))
                    font.setBold(data.get("font_bold", False))
                    font.setItalic(data.get("font_italic", False))
                    return font
        except Exception:
            pass
    return QFont("Malgun Gothic", 14, QFont.Weight.Bold)

def save_font_to_json(font):
    data = {}
    if os.path.exists(FONT_CONFIG_FILE):
        try:
            with open(FONT_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    data["font_family"] = font.family()
    data["font_size"] = font.pointSize()
    data["font_bold"] = font.bold()
    data["font_italic"] = font.italic()
    with open(FONT_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

CONFIG_FILE = os.path.join(_settings_root, "config.json")
DEFAULT_CONFIG = {
    "model_summary": "Gemini 3.1 Pro",
    "model_draft": "Claude Opus 4.8",
    "model_eval": "Gemini 3.1 Pro",
    "prompt_summary": "당신은 훌륭한 웹소설 요약 AI입니다. 다음 내용을 요약해주세요.",
    "prompt_draft": "당신은 천재 웹소설 작가입니다. 주어진 요약과 플롯을 바탕으로 흥미진진한 초안을 작성해주세요.",
    "prompt_eval": "당신은 냉철한 웹소설 편집자입니다. 다음 원문을 평가하고 다음 전개 아이디어 3가지를 제시해주세요."
}

def get_config():
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=4)
        return DEFAULT_CONFIG.copy()

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            merged = {**DEFAULT_CONFIG, **data}

        # 기존 파일에 없는 기본 설정값이 있다면 업데이트하여 다시 저장
        if set(data.keys()) != set(merged.keys()):
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(merged, f, ensure_ascii=False, indent=4)

        return merged
    except Exception:
        pass
    return DEFAULT_CONFIG.copy()

def save_config(key, value):
    config = get_config()
    config[key] = value
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=4)
