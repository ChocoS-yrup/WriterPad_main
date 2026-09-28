"""Offline Windows user-data envelope around the unchanged shared v1 package.

No Qt, credentials, sync store, server configuration or application startup.
All publication uses a new sibling directory; existing destinations are refused.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

from project_backup_adapter_v1 import backup_project, restore_project, verify_restored
from project_backup_v1 import BackupFormatError, read_manifest
from project_creation_v1 import audit, prepare_open, SYNC_INTERNAL_ROOTS
from project_identity_v1 import read_identity
from project_paths import validate_local_project_name

FORMAT = "writerpad-windows-user-data-1"
INDEX = "windows-manifest.json"
DATA = "windows-data"
PORTABLE = "portable-v1"
USER_TREES = {"메인", "AI", "백업", "집필모드"}
USER_FILES = {"설정.json", "cost_history.json"}
IDENTITY = ".writerpad/identity-v1.json"
SCOPE_TEXT = (
    "디스크에 저장된 AI·집필 원고, 응답 이력, 휴지통, 자동저장 사본, 작품 설정과 비용 이력. "
    "로그인·서버 연결·송신 대기열·실행 기록·앱 전역 설정·미저장 편집 내용은 제외합니다."
)


class ArchiveError(BackupFormatError):
    pass


def _safe_relative(name):
    if not isinstance(name, str) or not name or "\\" in name:
        raise ArchiveError("백업의 상대 경로가 올바르지 않습니다.")
    for part in name.split("/"):
        validate_local_project_name(part)
    return name


def _included(name):
    parts = name.split("/")
    if name == IDENTITY or name in USER_FILES:
        return True
    if parts[0] not in USER_TREES:
        return False
    if parts[0] == "집필모드" and len(parts) > 1 and parts[1] in SYNC_INTERNAL_ROOTS:
        return False
    # Temporary writes are not user data. A change during capture is detected
    # by the before/after inventory, including these excluded entries.
    return not any(p.startswith(".") or p.endswith(".tmp") for p in parts)


def _no_links(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ArchiveError("연결 또는 재분석 지점은 백업·복원할 수 없습니다.")
    return info


def _checked_root(path):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            _no_links(part)
    return path


def _digest(path):
    value = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            value.update(chunk)
    return {"bytes": size, "sha256": value.hexdigest()}


def _inventory(root, *, user_scope=False):
    root = _checked_root(root)
    if not root.is_dir():
        raise ArchiveError("읽을 작품 또는 백업 폴더를 찾을 수 없습니다.")
    files, directories, excluded, signatures = {}, [], [], {}
    for current, dirs, names in os.walk(root):
        for name in sorted(dirs + names):
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            _safe_relative(relative)
            info = _no_links(path)
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise ArchiveError("일반 파일이나 폴더만 보관할 수 있습니다.")
            signatures[relative] = (info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            include = not user_scope or _included(relative)
            if not include:
                excluded.append(relative)
                # Traverse the identity container only, never credential/queue
                # containers or other unknown roots.
                if name in dirs and relative != ".writerpad":
                    dirs.remove(name)
                continue
            if name in dirs:
                directories.append(relative)
            else:
                files[relative] = _digest(path)
    return {"files": files, "directories": sorted(directories), "excluded": sorted(excluded)}, signatures


def _write_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _copy_inventory(source, target, listing):
    target.mkdir(parents=True, exist_ok=True)
    for name in listing["directories"]:
        (target / name).mkdir(parents=True, exist_ok=True)
    for name, expected in listing["files"].items():
        original, dest = source / name, target / name
        _no_links(original)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with original.open("rb") as reader, dest.open("xb") as writer:
            shutil.copyfileobj(reader, writer, 1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        if _digest(dest) != expected:
            raise ArchiveError("복사 중 내용이 달라졌습니다. 원본 작업을 멈춘 뒤 다시 백업하세요.")


def _destination(source, destination):
    source, destination = _checked_root(source), _checked_root(destination)
    if source == destination or source in destination.parents or destination in source.parents:
        raise ArchiveError("원본과 분리된 새 목적지를 선택하세요.")
    if destination.exists():
        raise ArchiveError("목적지가 이미 존재합니다. 새 폴더 이름을 선택하세요.")
    if not destination.parent.is_dir():
        raise ArchiveError("목적지의 상위 폴더를 먼저 선택하세요.")
    return source, destination


def _publish(staging, destination):
    if os.path.lexists(destination):
        raise ArchiveError("목적지가 이미 존재합니다. 덮어쓰지 않았습니다.")
    # Windows rename refuses even an existing empty directory.
    os.rename(staging, destination)


def create_archive(project, destination, *, preservation=False):
    """Read-only stable capture; optional raw preservation when identity is broken."""
    source, destination = _destination(project, destination)
    before, signature = _inventory(source, user_scope=True)
    if not before["files"]:
        raise ArchiveError("보관할 작품 자료를 찾을 수 없습니다.")
    if not preservation and any(audit(str(source)).values()):
        raise ArchiveError("작품 구조나 진행 중 작업을 확인해야 합니다. 독립 복구의 자료 사본 보존을 사용하세요.")
    # The private staging parent is the only tree cleaned up on any failure.
    with tempfile.TemporaryDirectory(prefix=".writerpad-archive-", dir=destination.parent) as temp:
        staging = Path(temp) / "complete"
        staging.mkdir()
        snapshot = staging / DATA
        _copy_inventory(source, snapshot, before)
        portable = False
        if not preservation:
            backup_project(str(snapshot), str(staging / PORTABLE))
            portable = True
        after, after_signature = _inventory(source, user_scope=True)
        if before != after or signature != after_signature:
            raise ArchiveError("백업 중 원본이 변경되었습니다. 저장·편집을 마친 뒤 다시 백업하세요.")
        files, _ = _inventory(staging)
        _write_json(staging / INDEX, {
            "format": FORMAT, "portable_v1": portable,
            "scope": SCOPE_TEXT, "source_title": source.name,
            "excluded": before["excluded"],
            "files": files["files"], "directories": files["directories"],
        })
        result = verify_archive(staging)
        _publish(staging, destination)
    return result


def verify_archive(package):
    """Verify exact inventory and hashes before offering any restoration."""
    package = _checked_root(package)
    actual, _ = _inventory(package)
    try:
        manifest = json.loads((package / INDEX).read_text(encoding="utf-8"))
        if manifest["format"] != FORMAT or type(manifest["portable_v1"]) is not bool:
            raise ArchiveError("지원하지 않는 Windows 백업 형식입니다.")
        expected = manifest["files"]
        directories = manifest["directories"]
        if not isinstance(expected, dict) or not isinstance(directories, list):
            raise ArchiveError("백업 목록 형식이 올바르지 않습니다.")
        names = list(expected) + directories
        if len(names) != len({n.casefold() for n in names}):
            raise ArchiveError("백업 경로가 중복됩니다.")
        for name in names:
            _safe_relative(name)
            if name == DATA or name == DATA + "/.writerpad":
                continue
            if name.startswith(DATA + "/") and _included(name[len(DATA) + 1:]):
                continue
            if manifest["portable_v1"] and (name == PORTABLE or name.startswith(PORTABLE + "/")):
                continue
            raise ArchiveError("백업에 허용되지 않은 데이터 경로가 있습니다.")
        actual["files"].pop(INDEX)
        if actual["files"] != expected or actual["directories"] != sorted(directories):
            raise ArchiveError("백업 파일 목록·크기·해시가 일치하지 않습니다.")
        if manifest["portable_v1"]:
            core = read_manifest(str(package / PORTABLE))
            identity = read_identity(str(package / DATA))
            if core["project"] != {**identity["project"], "order": 0}:
                raise ArchiveError("공통 백업과 Windows 자료의 작품이 다릅니다.")
            fields = ("uuid", "kind", "parent_uuid", "path", "title", "order")
            structure = lambda nodes: {
                node["uuid"]: {field: node[field] for field in fields}
                for node in nodes
            }
            if structure(core["nodes"]) != structure(identity["nodes"]):
                raise ArchiveError("공통 백업과 Windows 자료의 구조가 다릅니다.")
            verify_restored(str(package / DATA), core)
            if any(audit(str(package / DATA)).values()):
                raise ArchiveError("보관한 원고와 작품 구조가 일치하지 않습니다.")
            for node in core["nodes"]:
                if node["kind"] == "document":
                    if _digest(package / PORTABLE / "workspace" / node["uuid"]) != {
                        "bytes": node["bytes"], "sha256": node["sha256"]
                    }:
                        raise ArchiveError("공통 백업의 본문 해시가 일치하지 않습니다.")
        return manifest
    except (KeyError, TypeError, ValueError) as error:
        raise ArchiveError("백업 manifest를 해석할 수 없습니다.") from error


def restore_archive(package, destination):
    """Restore to a NEW standalone root, never the active workspace or sync DB."""
    source, destination = _destination(package, destination)
    manifest = verify_archive(source)
    with tempfile.TemporaryDirectory(prefix=".writerpad-restore-", dir=destination.parent) as temp:
        staging = Path(temp) / "complete"
        workspace = staging / "작품목록"
        workspace.mkdir(parents=True)
        title = validate_local_project_name(manifest["source_title"])
        project = workspace / title
        if manifest["portable_v1"]:
            restore_project(str(source / PORTABLE), str(workspace), title)
        else:
            project.mkdir()
        data = source / DATA
        listing, _ = _inventory(data, user_scope=True)
        # Reuse the adapter's UUID tree, and then restore platform user data.
        # Files already materialized by the adapter are verified, not overwritten.
        for name in listing["directories"]:
            (project / name).mkdir(parents=True, exist_ok=True)
        for name, expected in listing["files"].items():
            if name == IDENTITY and manifest["portable_v1"]:
                continue
            target = project / name
            if target.exists():
                if _digest(target) != expected:
                    raise ArchiveError("복원된 원고와 보관한 자료가 다릅니다.")
                continue
            _copy_inventory(data, project, {"directories": [], "files": {name: expected}})
        status = "preserved_only"
        if manifest["portable_v1"]:
            verdict = prepare_open(str(project))
            if verdict["status"] != "ok":
                raise ArchiveError("복원 작품의 열기 검증에 실패했습니다.")
            verify_restored(str(project), read_manifest(str(source / PORTABLE)))
            status = "openable"
        # Verification is repeated only across this copy boundary: it catches
        # a source archive changed while restoration was in progress.
        if verify_archive(source) != manifest:
            raise ArchiveError("복원 중 백업이 변경되었습니다.")
        _write_json(staging / "config.json", {"last_project": title, "startup_mode": "assistant"})
        _write_json(staging / "restore-report.json", {
            "format": FORMAT, "status": status, "project": title,
            "sync_state_restored": False,
            "notice": "독립 복원 사본입니다. 기존 실행환경에 합치거나 서버에 연결하지 마세요.",
        })
        _publish(staging, destination)
    return {"status": status, "root": str(destination), "project": str(destination / "작품목록" / title)}


def locate_projects(root):
    """List project paths without initializing settings, identity or databases."""
    root = _checked_root(root)
    workspace = root / "작품목록"
    if not workspace.is_dir():
        return []
    _checked_root(workspace)
    return [str(p) for p in sorted(workspace.iterdir()) if p.is_dir() and not p.name.startswith(".")]
