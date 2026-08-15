import re
import unicodedata


MANUSCRIPT_ROOT_PATH = "메인/원고"

ROOT_STORAGE_NAMES = {
    "📚 원고": "원고",
    "원고": "원고",
    "👤 캐릭터": "캐릭터",
    "캐릭터": "캐릭터",
    "📖 설정집": "설정집",
    "설정집": "설정집",
    "📝 메모장": "메모장",
    "메모장": "메모장",
    # Canonical is 스토리 플롯, matching iPad. 플롯 is the legacy Windows name and
    # stays readable so existing folders and imports still resolve.
    "🗺️ 스토리 플롯": "스토리 플롯",
    "🗺️ 메인 스토리 틀": "스토리 플롯",
    "스토리 플롯": "스토리 플롯",
    "플롯": "스토리 플롯",
    "🌊 흐름 정리": "흐름정리",
    "흐름 정리": "흐름정리",
    "흐름정리": "흐름정리",
    "🔍 복선": "복선",
    "복선": "복선",
    "📌 장소": "장소",
    "장소": "장소",
    "🗑️ 휴지통": "휴지통",
    "휴지통": "휴지통",
}


def canonical_root_storage_name(value):
    """Map every platform/display alias to one shared root storage name."""
    normalized = unicodedata.normalize("NFC", str(value or ""))
    return ROOT_STORAGE_NAMES.get(normalized, normalized)


def canonical_root_children(child_names):
    """Normalize logical root children without hiding invalid duplicates."""
    return [
        canonical_root_storage_name(value)
        for value in (child_names or [])
        if str(value or "")
    ]


def canonical_tree_parent_path(parent_path):
    """Normalize a tree-order parent whose first child is a fixed app root."""
    normalized = unicodedata.normalize(
        "NFC", str(parent_path or "").replace("\\", "/")
    )
    if normalized == "<root>":
        return normalized
    parts = normalized.split("/")
    if len(parts) >= 2 and parts[0] == "메인":
        parts[1] = canonical_root_storage_name(parts[1])
    return "/".join(parts)


def _natural_key(value):
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", value)
    )


def canonical_manuscript_children(parent_path, child_names):
    """Return fixed volume/chapter order, or None for a freely ordered parent."""
    parent_path = unicodedata.normalize(
        "NFC", str(parent_path or "").replace("\\", "/")
    )
    names = [unicodedata.normalize("NFC", str(name)) for name in child_names]
    if parent_path == MANUSCRIPT_ROOT_PATH:
        def volume_key(name):
            match = re.fullmatch(r"(\d+)권", name)
            if match:
                return (0, int(match.group(1)), _natural_key(name))
            return (1, 0, _natural_key(name))

        return sorted(names, key=volume_key)

    if re.fullmatch(r"메인/원고/\d+권", parent_path):
        def chapter_key(name):
            match = re.match(r"(\d+)화(?:\s|\.|$)", name)
            if match:
                return (0, int(match.group(1)), _natural_key(name))
            return (1, 0, _natural_key(name))

        return sorted(names, key=chapter_key)
    return None


def is_fixed_manuscript_parent(parent_path):
    return canonical_manuscript_children(parent_path, []) is not None
