from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass(frozen=True)
class MergeResult:
    content: str
    has_conflicts: bool
    conflict_count: int


@dataclass(frozen=True)
class _Change:
    start: int
    end: int
    replacement: tuple
    side: str


def _changes(base_lines, changed_lines, side):
    matcher = SequenceMatcher(a=base_lines, b=changed_lines, autojunk=False)
    result = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            result.append(_Change(i1, i2, tuple(changed_lines[j1:j2]), side))
    return result


def _overlaps(first, second):
    if first.start == first.end and second.start == second.end:
        return first.start == second.start
    if first.start == first.end:
        return second.start < first.start < second.end
    if second.start == second.end:
        return first.start < second.start < first.end
    return max(first.start, second.start) < min(first.end, second.end)


def _clusters(changes):
    remaining = list(changes)
    clusters = []
    while remaining:
        cluster = [remaining.pop(0)]
        expanded = True
        while expanded:
            expanded = False
            for candidate in list(remaining):
                if any(_overlaps(candidate, item) for item in cluster):
                    cluster.append(candidate)
                    remaining.remove(candidate)
                    expanded = True
        clusters.append(sorted(cluster, key=lambda item: (item.start, item.end, item.side)))
    return sorted(
        clusters,
        key=lambda group: (
            min(item.start for item in group),
            min(item.end for item in group),
        ),
    )


def _apply_segment(base_lines, start, end, changes):
    cursor = start
    output = []
    for change in sorted(changes, key=lambda item: (item.start, item.end)):
        output.extend(base_lines[cursor:change.start])
        output.extend(change.replacement)
        cursor = change.end
    output.extend(base_lines[cursor:end])
    return output


def _ensure_newline(lines):
    value = "".join(lines)
    if value and not value.endswith("\n"):
        value += "\n"
    return value


def _line_tokens(value):
    """Normalize editor text into independent line tokens for line-based merging."""
    return [line + "\n" for line in value.splitlines()]




SECTION_RULE = "=" * 9
EMPTY_LINE_LABEL = "(빈 줄)"
MISSING_LINE_LABEL = "(없음)"


def _section(title, body):
    text = str(body or "")
    if not text.strip():
        text = "(내용 없음)"
    return f"{SECTION_RULE}\n\n{title}\n\n{text.rstrip()}\n\n"


def _shown(line):
    if line is None:
        return MISSING_LINE_LABEL
    return line if line.strip() else EMPTY_LINE_LABEL


def _line_differences(local, remote):
    """List the lines where the local and server versions disagree."""
    local_lines = str(local or "").splitlines()
    remote_lines = str(remote or "").splitlines()
    matcher = SequenceMatcher(None, local_lines, remote_lines, autojunk=False)
    blocks = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        span = max(i2 - i1, j2 - j1)
        for offset in range(span):
            local_line = local_lines[i1 + offset] if i1 + offset < i2 else None
            remote_line = remote_lines[j1 + offset] if j1 + offset < j2 else None
            blocks.append((i1 + offset + 1, local_line, remote_line))
    return blocks


def build_conflict_report(base, local, remote):
    """Plain-language comparison. No +/-/@@ diff syntax to decode."""
    report = (
        "3방향 병합 충돌 차이점 비교\n\n"
        "참고용입니다. 원고와 서버 최신본은 자동으로 덮어쓰지 않았습니다.\n\n"
        + _section("바꾸기 전 원본", base)
        + _section("서버 최신본", remote)
        + f"{SECTION_RULE}\n\n차이점 비교\n\n"
    )
    differences = _line_differences(local, remote)
    if not differences:
        return report + "로컬과 서버 내용이 같습니다.\n"
    lines = []
    single = len(differences) == 1
    for line_number, local_line, remote_line in differences:
        if not single:
            lines.append(f"{line_number}번째 줄")
        lines.append(f"로컬 : {_shown(local_line)}")
        lines.append(f"서버 : {_shown(remote_line)}")
        lines.append("")
    return report + "\n".join(lines).rstrip() + "\n"


def three_way_merge(base, local, remote):
    """Line-based diff3 merge with explicit markers for overlapping edits."""
    if local == remote:
        return MergeResult(local, False, 0)
    if local == base:
        return MergeResult(remote, False, 0)
    if remote == base:
        return MergeResult(local, False, 0)

    base_lines = _line_tokens(base)
    local_lines = _line_tokens(local)
    remote_lines = _line_tokens(remote)
    changes = _changes(base_lines, local_lines, "local") + _changes(base_lines, remote_lines, "remote")

    output = []
    cursor = 0
    conflicts = 0
    for cluster in _clusters(changes):
        start = min(change.start for change in cluster)
        end = max(change.end for change in cluster)
        output.extend(base_lines[cursor:start])
        local_changes = [change for change in cluster if change.side == "local"]
        remote_changes = [change for change in cluster if change.side == "remote"]

        if local_changes and remote_changes:
            local_segment = _apply_segment(base_lines, start, end, local_changes)
            remote_segment = _apply_segment(base_lines, start, end, remote_changes)
            if local_segment == remote_segment:
                output.extend(local_segment)
            else:
                conflicts += 1
                output.append("<<<<<<< 내 로컬 편집본\n")
                output.append(_ensure_newline(local_segment))
                output.append("||||||| 마지막 공통본\n")
                output.append(_ensure_newline(base_lines[start:end]))
                output.append("=======\n")
                output.append(_ensure_newline(remote_segment))
                output.append(">>>>>>> 서버 최신본\n")
        else:
            output.extend(
                _apply_segment(base_lines, start, end, local_changes or remote_changes)
            )
        cursor = end

    output.extend(base_lines[cursor:])
    return MergeResult("".join(output), conflicts > 0, conflicts)
