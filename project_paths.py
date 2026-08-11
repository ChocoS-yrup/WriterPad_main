import ntpath
import os
import unicodedata
from dataclasses import dataclass


INVALID_PROJECT_NAME = "invalid_project_name"
PROJECT_NAME_CONFLICT = "project_name_conflict"
WORKSPACE_ESCAPE = "workspace_escape"
IMPORT_MARKER_FILENAME = ".server-project-import.json"


_ERROR_MESSAGES = {
    INVALID_PROJECT_NAME: "프로젝트 이름을 Windows 폴더 이름으로 안전하게 사용할 수 없습니다.",
    PROJECT_NAME_CONFLICT: "같은 이름의 로컬 프로젝트 또는 파일이 이미 존재합니다.",
    WORKSPACE_ESCAPE: "작품목록 폴더 바깥의 경로는 사용할 수 없습니다.",
}

_INVALID_WINDOWS_CHARACTERS = set('<>:"/\\|?*')
_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "CLOCK$",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
MAX_LOCAL_ENTRY_UTF16_UNITS = 255


class LocalProjectPathError(ValueError):
    def __init__(self, code):
        if code not in _ERROR_MESSAGES:
            code = INVALID_PROJECT_NAME
        self.code = code
        self.user_message = _ERROR_MESSAGES[code]
        super().__init__(self.user_message)


@dataclass(frozen=True)
class LocalProjectDestination:
    project_name: str
    project_path: str
    writing_root_path: str


def validate_local_project_name(project_name):
    if not isinstance(project_name, str) or not project_name:
        raise LocalProjectPathError(INVALID_PROJECT_NAME)
    if project_name != project_name.strip():
        raise LocalProjectPathError(INVALID_PROJECT_NAME)
    if project_name in {".", ".."}:
        raise LocalProjectPathError(INVALID_PROJECT_NAME)
    if project_name[-1] == "." or project_name[-1].isspace():
        raise LocalProjectPathError(INVALID_PROJECT_NAME)
    if os.path.isabs(project_name) or ntpath.isabs(project_name):
        raise LocalProjectPathError(INVALID_PROJECT_NAME)
    drive, _ = ntpath.splitdrive(project_name)
    if drive:
        raise LocalProjectPathError(INVALID_PROJECT_NAME)
    if any(character in _INVALID_WINDOWS_CHARACTERS for character in project_name):
        raise LocalProjectPathError(INVALID_PROJECT_NAME)
    if any(ord(character) < 32 or ord(character) == 127 for character in project_name):
        raise LocalProjectPathError(INVALID_PROJECT_NAME)

    device_name = project_name.split(".", 1)[0].upper()
    if device_name in _RESERVED_WINDOWS_NAMES:
        raise LocalProjectPathError(INVALID_PROJECT_NAME)
    return project_name


def normalize_local_entry_name(entry_name):
    """Trim a trailing blank and validate one cross-device file component."""
    if not isinstance(entry_name, str):
        raise LocalProjectPathError(INVALID_PROJECT_NAME)
    normalized = unicodedata.normalize("NFC", entry_name.rstrip())
    validate_local_project_name(normalized)
    if len(normalized.encode("utf-16-le")) // 2 > MAX_LOCAL_ENTRY_UTF16_UNITS:
        raise LocalProjectPathError(INVALID_PROJECT_NAME)
    return normalized


def resolve_local_project_destination(workspace_dir, project_name, require_available=True):
    project_name = validate_local_project_name(project_name)
    workspace_path = os.path.abspath(workspace_dir or "")
    project_path = os.path.abspath(os.path.join(workspace_path, project_name))
    try:
        inside_workspace = (
            os.path.commonpath([workspace_path, project_path]) == workspace_path
        )
    except ValueError:
        inside_workspace = False
    if not inside_workspace or project_path == workspace_path:
        raise LocalProjectPathError(WORKSPACE_ESCAPE)
    if require_available and os.path.lexists(project_path):
        raise LocalProjectPathError(PROJECT_NAME_CONFLICT)
    return LocalProjectDestination(
        project_name=project_name,
        project_path=project_path,
        writing_root_path=os.path.join(project_path, "집필모드"),
    )
