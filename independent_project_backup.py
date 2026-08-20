"""Fail-closed whole-project backup and isolated restore core.

This module deliberately has no UI, sync, Supabase, or live-project wiring.  A
caller supplies the project/folder/document identities it already owns, and the
store proves that those identities describe the UTF-8 files being packaged.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Literal


FORMAT_NAME = "writerpad-independent-project-backup"
FORMAT_VERSION = 1
MANIFEST_FILE_NAME = "manifest.json"
CONTENT_DIRECTORY_NAME = "files"
RESTORED_IDENTITY_MANIFEST_FILE_NAME = "writerpad-project-manifest.json"
DEFAULT_RETENTION_DAYS = 30


class IndependentProjectBackupError(Exception):
    """A stable code plus optional path for a rejected backup operation."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = str(detail)
        super().__init__(f"{code}: {self.detail}" if self.detail else code)

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, IndependentProjectBackupError)
            and self.code == other.code
            and self.detail == other.detail
        )


@dataclass(frozen=True)
class ProjectIdentity:
    project_id: uuid.UUID
    name: str
    created_at: datetime
    modified_at: datetime


@dataclass(frozen=True)
class BackupEntry:
    entry_id: uuid.UUID
    project_id: uuid.UUID
    kind: Literal["folder", "text"]
    parent_id: uuid.UUID | None
    relative_path: str
    user_order: int
    modified_at: datetime
    content_sha256: str | None = None


@dataclass(frozen=True)
class BackupReceipt:
    package_path: Path
    manifest: dict


@dataclass(frozen=True)
class RestoreReceipt:
    restored_workspace_path: Path
    identity_manifest_path: Path
    manifest: dict


@dataclass(frozen=True)
class BackupInventoryItem:
    package_path: Path
    created_at: datetime
    is_pinned: bool = False


def retention_candidates(
    items: Iterable[BackupInventoryItem],
    *,
    now: datetime,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> list[BackupInventoryItem]:
    """Return deletion candidates without deleting or mutating any package."""

    if retention_days <= 0:
        return []
    cutoff = _aware_utc(now) - timedelta(days=retention_days)
    return sorted(
        (
            item
            for item in items
            if not item.is_pinned and _aware_utc(item.created_at) < cutoff
        ),
        key=lambda item: (_aware_utc(item.created_at), str(item.package_path)),
    )


class IndependentProjectBackupStore:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], uuid.UUID] | None = None,
    ):
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._uuid_factory = uuid_factory or uuid.uuid4

    def create_backup(
        self,
        *,
        source_workspace: os.PathLike[str] | str,
        project: ProjectIdentity,
        entries: Iterable[BackupEntry],
        package_path: os.PathLike[str] | str,
    ) -> BackupReceipt:
        source = _existing_directory(source_workspace)
        _reject_symlink_components(source, source)
        destination = _new_destination(package_path)
        if _is_contained(destination, source):
            raise IndependentProjectBackupError(
                "DESTINATION_INSIDE_WORKSPACE", str(destination)
            )

        normalized_entries = _validate_entries(project, entries)
        backup_id = self._uuid_factory()
        if not isinstance(backup_id, uuid.UUID):
            raise IndependentProjectBackupError("INVALID_BACKUP_ID", backup_id)
        staging = destination.parent / (
            f".{destination.name}.partial-{str(backup_id).lower()}"
        )
        if os.path.lexists(staging):
            raise IndependentProjectBackupError("DESTINATION_EXISTS", staging)

        created_staging = False
        try:
            (staging / CONTENT_DIRECTORY_NAME).mkdir(parents=True, exist_ok=False)
            created_staging = True
            manifest_entries: list[dict] = []
            for entry in normalized_entries:
                source_item = _contained_item(source, entry.relative_path)
                _reject_symlink_components(source_item, source)
                package_item = staging / CONTENT_DIRECTORY_NAME / Path(
                    *PurePosixPath(entry.relative_path).parts
                )
                if entry.kind == "folder":
                    _require_type(source_item, "folder")
                    package_item.mkdir(parents=True, exist_ok=True)
                    content = None
                else:
                    _require_type(source_item, "text")
                    data = source_item.read_bytes()
                    _require_utf8(data, entry.relative_path)
                    digest = hashlib.sha256(data).hexdigest()
                    package_item.parent.mkdir(parents=True, exist_ok=True)
                    _write_new_bytes(package_item, data)
                    content = {
                        "package_path": (
                            f"{CONTENT_DIRECTORY_NAME}/{entry.relative_path}"
                        ),
                        "utf8_byte_count": len(data),
                        "sha256": digest,
                    }
                manifest_entries.append(_entry_manifest(entry, content))

            manifest = {
                "format": FORMAT_NAME,
                "format_version": FORMAT_VERSION,
                "backup_id": str(backup_id).lower(),
                "created_at": _iso8601(self._clock()),
                "project": {
                    "project_id": str(project.project_id).lower(),
                    "name": project.name,
                    "created_at": _iso8601(project.created_at),
                    "modified_at": _iso8601(project.modified_at),
                },
                "entries": manifest_entries,
            }
            _write_new_bytes(staging / MANIFEST_FILE_NAME, _json_bytes(manifest))
            self.verify_backup(staging)
            if os.path.lexists(destination):
                raise IndependentProjectBackupError(
                    "DESTINATION_EXISTS", str(destination)
                )
            os.rename(staging, destination)
            created_staging = False
            verified = self.verify_backup(destination)
            return BackupReceipt(destination, verified)
        except Exception:
            if created_staging and os.path.lexists(staging):
                _remove_owned_directory(staging)
            raise

    def verify_backup(
        self, package_path: os.PathLike[str] | str
    ) -> dict:
        root = _existing_directory(package_path)
        _reject_symlink_components(root, root)
        manifest_path = root / MANIFEST_FILE_NAME
        if not manifest_path.exists():
            raise IndependentProjectBackupError("MANIFEST_MISSING")
        _require_type(manifest_path, "text")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IndependentProjectBackupError("MANIFEST_INVALID", exc) from exc

        project, entries = _parse_manifest(manifest)
        _validate_entries(project, entries)
        expected_files = {MANIFEST_FILE_NAME}
        expected_directories = {CONTENT_DIRECTORY_NAME}
        for entry, raw_entry in zip(entries, manifest["entries"]):
            item = root / CONTENT_DIRECTORY_NAME / Path(
                *PurePosixPath(entry.relative_path).parts
            )
            _reject_symlink_components(item, root)
            relative_package_path = (
                f"{CONTENT_DIRECTORY_NAME}/{entry.relative_path}"
            )
            if entry.kind == "folder":
                _require_type(item, "folder")
                expected_directories.add(relative_package_path)
                continue
            _require_type(item, "text")
            content = raw_entry["content"]
            if content["package_path"] != relative_package_path:
                raise IndependentProjectBackupError(
                    "MANIFEST_INVALID", entry.relative_path
                )
            data = item.read_bytes()
            _require_utf8(data, entry.relative_path)
            digest = hashlib.sha256(data).hexdigest()
            if (
                content["utf8_byte_count"] != len(data)
                or content["sha256"] != digest
            ):
                raise IndependentProjectBackupError(
                    "CONTENT_HASH_MISMATCH", entry.relative_path
                )
            expected_files.add(relative_package_path)

        actual_files, actual_directories = _inventory(root)
        unexpected_files = sorted(actual_files - expected_files)
        missing_files = sorted(expected_files - actual_files)
        unexpected_directories = sorted(actual_directories - expected_directories)
        missing_directories = sorted(expected_directories - actual_directories)
        if unexpected_files or unexpected_directories:
            detail = (unexpected_files + unexpected_directories)[0]
            raise IndependentProjectBackupError("UNEXPECTED_PACKAGE_ITEM", detail)
        if missing_files or missing_directories:
            detail = (missing_files + missing_directories)[0]
            raise IndependentProjectBackupError("PACKAGE_ITEM_MISSING", detail)
        return manifest

    def restore_verified_backup(
        self,
        *,
        package_path: os.PathLike[str] | str,
        restored_workspace: os.PathLike[str] | str,
    ) -> RestoreReceipt:
        package = _existing_directory(package_path)
        manifest = self.verify_backup(package)
        destination = _new_destination(restored_workspace)
        if _is_contained(destination, package):
            raise IndependentProjectBackupError(
                "DESTINATION_INSIDE_PACKAGE", str(destination)
            )
        backup_id = uuid.UUID(manifest["backup_id"])
        staging = destination.parent / (
            f".{destination.name}.restore-{str(backup_id).lower()}"
        )
        if os.path.lexists(staging):
            raise IndependentProjectBackupError("DESTINATION_EXISTS", staging)

        created_staging = False
        try:
            staging.mkdir(parents=False, exist_ok=False)
            created_staging = True
            for raw_entry in manifest["entries"]:
                entry = _parse_entry(raw_entry)
                target = staging / Path(*PurePosixPath(entry.relative_path).parts)
                if entry.kind == "folder":
                    target.mkdir(parents=True, exist_ok=True)
            for raw_entry in manifest["entries"]:
                entry = _parse_entry(raw_entry)
                if entry.kind != "text":
                    continue
                source = package / raw_entry["content"]["package_path"]
                target = staging / Path(*PurePosixPath(entry.relative_path).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                _write_new_bytes(target, source.read_bytes())
            _write_new_bytes(
                staging / RESTORED_IDENTITY_MANIFEST_FILE_NAME,
                _json_bytes(manifest),
            )
            self._verify_restored_workspace(staging, manifest)
            if os.path.lexists(destination):
                raise IndependentProjectBackupError(
                    "DESTINATION_EXISTS", str(destination)
                )
            os.rename(staging, destination)
            created_staging = False
            self._verify_restored_workspace(destination, manifest)
            return RestoreReceipt(
                destination,
                destination / RESTORED_IDENTITY_MANIFEST_FILE_NAME,
                manifest,
            )
        except Exception:
            if created_staging and os.path.lexists(staging):
                _remove_owned_directory(staging)
            raise

    def _verify_restored_workspace(self, root: Path, manifest: dict) -> None:
        root = _existing_directory(root)
        _reject_symlink_components(root, root)
        identity_path = root / RESTORED_IDENTITY_MANIFEST_FILE_NAME
        _require_type(identity_path, "text")
        try:
            restored_manifest = json.loads(identity_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IndependentProjectBackupError(
                "RESTORE_VERIFICATION_FAILED", exc
            ) from exc
        if restored_manifest != manifest:
            raise IndependentProjectBackupError(
                "RESTORE_VERIFICATION_FAILED", "identity manifest mismatch"
            )

        expected_files = {RESTORED_IDENTITY_MANIFEST_FILE_NAME}
        expected_directories: set[str] = set()
        for raw_entry in manifest["entries"]:
            entry = _parse_entry(raw_entry)
            item = root / Path(*PurePosixPath(entry.relative_path).parts)
            _reject_symlink_components(item, root)
            if entry.kind == "folder":
                _require_type(item, "folder")
                expected_directories.add(entry.relative_path)
                continue
            _require_type(item, "text")
            data = item.read_bytes()
            content = raw_entry["content"]
            try:
                _require_utf8(data, entry.relative_path)
            except IndependentProjectBackupError as exc:
                raise IndependentProjectBackupError(
                    "RESTORE_VERIFICATION_FAILED", exc.detail
                ) from exc
            if (
                len(data) != content["utf8_byte_count"]
                or hashlib.sha256(data).hexdigest() != content["sha256"]
            ):
                raise IndependentProjectBackupError(
                    "RESTORE_VERIFICATION_FAILED", entry.relative_path
                )
            expected_files.add(entry.relative_path)

        actual_files, actual_directories = _inventory(root)
        if actual_files != expected_files or actual_directories != expected_directories:
            differences = sorted(
                actual_files.symmetric_difference(expected_files)
                | actual_directories.symmetric_difference(expected_directories)
            )
            raise IndependentProjectBackupError(
                "RESTORE_VERIFICATION_FAILED",
                differences[0] if differences else "inventory mismatch",
            )


def _entry_manifest(entry: BackupEntry, content: dict | None) -> dict:
    return {
        "entry_id": str(entry.entry_id).lower(),
        "project_id": str(entry.project_id).lower(),
        "kind": entry.kind,
        "parent_id": str(entry.parent_id).lower() if entry.parent_id else None,
        "relative_path": entry.relative_path,
        "user_order": entry.user_order,
        "modified_at": _iso8601(entry.modified_at),
        "content": content,
    }


def _parse_manifest(manifest: object) -> tuple[ProjectIdentity, list[BackupEntry]]:
    if not isinstance(manifest, dict):
        raise IndependentProjectBackupError("MANIFEST_INVALID", "root")
    if manifest.get("format") != FORMAT_NAME or manifest.get("format_version") != 1:
        raise IndependentProjectBackupError("UNSUPPORTED_FORMAT")
    try:
        uuid.UUID(str(manifest["backup_id"]))
        _parse_datetime(manifest["created_at"])
        raw_project = manifest["project"]
        if not isinstance(raw_project, dict):
            raise TypeError("project")
        project = ProjectIdentity(
            project_id=uuid.UUID(str(raw_project["project_id"])),
            name=str(raw_project["name"]),
            created_at=_parse_datetime(raw_project["created_at"]),
            modified_at=_parse_datetime(raw_project["modified_at"]),
        )
        raw_entries = manifest["entries"]
        if not isinstance(raw_entries, list):
            raise TypeError("entries")
        entries = [_parse_entry(value) for value in raw_entries]
    except (KeyError, TypeError, ValueError) as exc:
        raise IndependentProjectBackupError("MANIFEST_INVALID", exc) from exc
    return project, entries


def _parse_entry(raw_entry: object) -> BackupEntry:
    if not isinstance(raw_entry, dict):
        raise IndependentProjectBackupError("MANIFEST_INVALID", "entry")
    try:
        kind = raw_entry["kind"]
        if kind not in {"folder", "text"}:
            raise ValueError("kind")
        content = raw_entry["content"]
        if kind == "folder" and content is not None:
            raise ValueError("folder content")
        if kind == "text":
            if not isinstance(content, dict):
                raise ValueError("text content")
            if not isinstance(content["utf8_byte_count"], int):
                raise ValueError("utf8_byte_count")
            if content["utf8_byte_count"] < 0:
                raise ValueError("utf8_byte_count")
            _validate_sha256(content["sha256"])
            if not isinstance(content["package_path"], str):
                raise ValueError("package_path")
        return BackupEntry(
            entry_id=uuid.UUID(str(raw_entry["entry_id"])),
            project_id=uuid.UUID(str(raw_entry["project_id"])),
            kind=kind,
            parent_id=(
                uuid.UUID(str(raw_entry["parent_id"]))
                if raw_entry["parent_id"] is not None
                else None
            ),
            relative_path=str(raw_entry["relative_path"]),
            user_order=int(raw_entry["user_order"]),
            modified_at=_parse_datetime(raw_entry["modified_at"]),
            content_sha256=(content["sha256"] if content else None),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise IndependentProjectBackupError("MANIFEST_INVALID", exc) from exc


def _validate_entries(
    project: ProjectIdentity, entries: Iterable[BackupEntry]
) -> list[BackupEntry]:
    if not isinstance(project.project_id, uuid.UUID) or not project.name.strip():
        raise IndependentProjectBackupError("INVALID_PROJECT")
    _aware_utc(project.created_at)
    _aware_utc(project.modified_at)
    normalized = sorted(
        list(entries), key=lambda entry: (entry.relative_path, str(entry.entry_id))
    )
    by_id: dict[uuid.UUID, BackupEntry] = {}
    seen_paths: set[str] = set()
    for entry in normalized:
        if not isinstance(entry.entry_id, uuid.UUID):
            raise IndependentProjectBackupError("INVALID_ENTRY_ID", entry.entry_id)
        if entry.entry_id in by_id:
            raise IndependentProjectBackupError("DUPLICATE_ENTRY_ID", entry.entry_id)
        if entry.project_id != project.project_id:
            raise IndependentProjectBackupError("PROJECT_ID_MISMATCH", entry.entry_id)
        if entry.kind not in {"folder", "text"}:
            raise IndependentProjectBackupError("INVALID_ENTRY_KIND", entry.kind)
        _validate_relative_path(entry.relative_path)
        path_key = entry.relative_path.casefold()
        if path_key in seen_paths:
            raise IndependentProjectBackupError(
                "DUPLICATE_RELATIVE_PATH", entry.relative_path
            )
        if entry.user_order < 0:
            raise IndependentProjectBackupError("INVALID_USER_ORDER", entry.relative_path)
        _aware_utc(entry.modified_at)
        if entry.kind == "folder" and entry.content_sha256 is not None:
            raise IndependentProjectBackupError(
                "FOLDER_HAS_CONTENT_HASH", entry.relative_path
            )
        if entry.content_sha256 is not None:
            _validate_sha256(entry.content_sha256)
        by_id[entry.entry_id] = entry
        seen_paths.add(path_key)

    for entry in normalized:
        path_parent = str(PurePosixPath(entry.relative_path).parent)
        path_parent = "" if path_parent == "." else path_parent
        if entry.parent_id is None:
            if path_parent:
                raise IndependentProjectBackupError(
                    "PARENT_PATH_MISMATCH", entry.relative_path
                )
            continue
        parent = by_id.get(entry.parent_id)
        if parent is None:
            raise IndependentProjectBackupError("PARENT_MISSING", entry.parent_id)
        if parent.kind != "folder":
            raise IndependentProjectBackupError("PARENT_NOT_FOLDER", entry.parent_id)
        if path_parent != parent.relative_path:
            raise IndependentProjectBackupError(
                "PARENT_PATH_MISMATCH", entry.relative_path
            )
    return normalized


def _validate_relative_path(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or "\\" in value
        or "\0" in value
    ):
        raise IndependentProjectBackupError("INVALID_RELATIVE_PATH", value)
    parts = value.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise IndependentProjectBackupError("INVALID_RELATIVE_PATH", value)


def _validate_sha256(value: object) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or value.lower() != value
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise IndependentProjectBackupError("INVALID_SHA256", value)


def _existing_directory(value: os.PathLike[str] | str) -> Path:
    path = Path(value).absolute()
    if not os.path.lexists(path):
        raise IndependentProjectBackupError("DIRECTORY_MISSING", path)
    _require_type(path, "folder")
    return path.resolve(strict=True)


def _new_destination(value: os.PathLike[str] | str) -> Path:
    requested = Path(value).absolute()
    if not requested.name:
        raise IndependentProjectBackupError("INVALID_DESTINATION", requested)
    parent = _existing_directory(requested.parent)
    destination = parent / requested.name
    if os.path.lexists(destination):
        raise IndependentProjectBackupError("DESTINATION_EXISTS", destination)
    return destination


def _contained_item(root: Path, relative_path: str) -> Path:
    _validate_relative_path(relative_path)
    item = root / Path(*PurePosixPath(relative_path).parts)
    if not _is_contained(item, root):
        raise IndependentProjectBackupError("INVALID_RELATIVE_PATH", relative_path)
    if not os.path.lexists(item):
        raise IndependentProjectBackupError("SOURCE_MISSING", relative_path)
    return item


def _is_contained(candidate: Path, root: Path) -> bool:
    candidate_key = os.path.normcase(str(candidate.resolve(strict=False)))
    root_key = os.path.normcase(str(root.resolve(strict=False)))
    try:
        return os.path.commonpath([candidate_key, root_key]) == root_key
    except ValueError:
        return False


def _reject_symlink_components(item: Path, root: Path) -> None:
    root = root.absolute()
    item = item.absolute()
    try:
        relative = item.relative_to(root)
    except ValueError as exc:
        raise IndependentProjectBackupError("INVALID_RELATIVE_PATH", item) from exc
    current = root
    paths = [root]
    for part in relative.parts:
        current = current / part
        paths.append(current)
    for path in paths:
        if os.path.lexists(path) and stat.S_ISLNK(os.lstat(path).st_mode):
            raise IndependentProjectBackupError("SYMLINK_NOT_ALLOWED", path)


def _require_type(path: Path, expected: Literal["folder", "text"]) -> None:
    if not os.path.lexists(path):
        raise IndependentProjectBackupError("SOURCE_MISSING", path)
    mode = os.lstat(path).st_mode
    if stat.S_ISLNK(mode):
        raise IndependentProjectBackupError("SYMLINK_NOT_ALLOWED", path)
    matches = stat.S_ISDIR(mode) if expected == "folder" else stat.S_ISREG(mode)
    if not matches:
        raise IndependentProjectBackupError("SOURCE_TYPE_MISMATCH", path)


def _require_utf8(data: bytes, detail: str) -> None:
    try:
        data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise IndependentProjectBackupError("INVALID_UTF8", detail) from exc


def _inventory(root: Path) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    directories: set[str] = set()
    for current_root, directory_names, file_names in os.walk(
        root, topdown=True, followlinks=False
    ):
        current = Path(current_root)
        for name in directory_names:
            item = current / name
            if stat.S_ISLNK(os.lstat(item).st_mode):
                raise IndependentProjectBackupError("SYMLINK_NOT_ALLOWED", item)
            directories.add(item.relative_to(root).as_posix())
        for name in file_names:
            item = current / name
            _require_type(item, "text")
            files.add(item.relative_to(root).as_posix())
    return files, directories


def _write_new_bytes(path: Path, data: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise IndependentProjectBackupError("DESTINATION_EXISTS", path) from exc


def _remove_owned_directory(path: Path) -> None:
    if stat.S_ISLNK(os.lstat(path).st_mode):
        raise IndependentProjectBackupError("SYMLINK_NOT_ALLOWED", path)
    shutil.rmtree(path)


def _json_bytes(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")


def _aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise IndependentProjectBackupError("INVALID_DATETIME", value)
    return value.astimezone(timezone.utc)


def _iso8601(value: datetime) -> str:
    return _aware_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("datetime")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _aware_utc(parsed)
