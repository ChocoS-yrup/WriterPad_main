import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from project_paths import (
    PROJECT_NAME_CONFLICT,
    LocalProjectPathError,
    resolve_local_project_destination,
)


AUTH_REQUIRED = "auth_required"
NETWORK_UNAVAILABLE = "network_unavailable"
ACCESS_DENIED = "access_denied"
INVALID_RESPONSE = "invalid_response"
INVALID_PROJECT = "invalid_project"
CURRENT_PROJECT_OPEN = "current_project_open"
PENDING_SYNC = "pending_sync"
LOCAL_NAME_CONFLICT = "local_name_conflict"
LOCAL_STORAGE_ERROR = "local_storage_error"
SERVER_OPERATION_FAILED = "server_operation_failed"
SERVER_PROJECT_PURGED = "server_project_purged"
SERVER_PROJECT_MISSING = "server_project_missing"

TRASH_METADATA_SUFFIX = ".project-trash.json"

_ERROR_MESSAGES = {
    AUTH_REQUIRED: "서버 작품을 삭제하거나 복원하려면 Supabase 로그인이 필요합니다.",
    NETWORK_UNAVAILABLE: "서버에 연결할 수 없습니다. 네트워크 연결을 확인해주세요.",
    ACCESS_DENIED: "이 서버 작품을 삭제하거나 복원할 권한이 없습니다.",
    INVALID_RESPONSE: "서버 작품 휴지통 응답이 올바르지 않습니다.",
    INVALID_PROJECT: "작품 휴지통 항목이 올바르지 않습니다.",
    CURRENT_PROJECT_OPEN: "현재 열려 있는 작품은 닫은 뒤 삭제해주세요.",
    PENDING_SYNC: "아직 서버에 반영되지 않은 변경 사항이 있습니다. 동기화를 완료한 뒤 삭제해주세요.",
    LOCAL_NAME_CONFLICT: "같은 이름의 로컬 작품이 있어 복원할 수 없습니다.",
    LOCAL_STORAGE_ERROR: "로컬 작품 휴지통을 안전하게 변경하지 못했습니다.",
    SERVER_OPERATION_FAILED: "서버 작품 휴지통 작업을 완료하지 못했습니다.",
    SERVER_PROJECT_PURGED: "서버에서 이미 영구 삭제된 작품입니다.",
    SERVER_PROJECT_MISSING: "서버에서 이 작품을 찾을 수 없습니다.",
}


class ProjectTrashError(RuntimeError):
    def __init__(self, code):
        if code not in _ERROR_MESSAGES:
            code = SERVER_OPERATION_FAILED
        self.code = code
        self.user_message = _ERROR_MESSAGES[code]
        super().__init__(self.user_message)


@dataclass(frozen=True)
class TrashedProject:
    entry_id: str
    project_name: str
    project_id: str = ""
    trashed_at: str = ""
    local_available: bool = False
    server_available: bool = False

    @property
    def is_server_project(self):
        return bool(self.project_id)


class ProjectTrashService:
    """Coordinate local project retention with the server-side project trash."""

    def __init__(
        self,
        project_manager,
        store,
        supabase_client=None,
        trash_root=None,
        authenticated_call=None,
    ):
        self.pm = project_manager
        self.store = store
        self.supabase = supabase_client
        self.workspace_dir = os.path.abspath(self.pm.workspace_dir)
        self.trash_root = os.path.abspath(
            trash_root
            or os.path.join(os.path.dirname(self.workspace_dir), "작품휴지통")
        )
        self.data_dir = os.path.join(self.trash_root, "data")
        self.index_dir = os.path.join(self.trash_root, "index")
        self.last_server_error = ""
        self.authenticated_call = authenticated_call

    @staticmethod
    def _utc_now():
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize_uuid(value):
        try:
            return str(uuid.UUID(str(value)))
        except (AttributeError, TypeError, ValueError):
            raise ProjectTrashError(INVALID_PROJECT) from None

    def _ensure_local_trash(self):
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.index_dir, exist_ok=True)

    def _entry_data_path(self, entry_id):
        entry_id = self._normalize_uuid(entry_id)
        path = os.path.abspath(os.path.join(self.data_dir, entry_id))
        if os.path.commonpath([self.data_dir, path]) != self.data_dir:
            raise ProjectTrashError(INVALID_PROJECT)
        return path

    def _metadata_path(self, entry_id):
        entry_id = self._normalize_uuid(entry_id)
        path = os.path.abspath(
            os.path.join(self.index_dir, entry_id + TRASH_METADATA_SUFFIX)
        )
        if os.path.commonpath([self.index_dir, path]) != self.index_dir:
            raise ProjectTrashError(INVALID_PROJECT)
        return path

    def _write_metadata(self, metadata):
        self._ensure_local_trash()
        path = self._metadata_path(metadata["entry_id"])
        temp_path = path + "." + str(uuid.uuid4()) + ".tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as file:
                json.dump(metadata, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, path)
        except OSError:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass
            raise ProjectTrashError(LOCAL_STORAGE_ERROR) from None

    def _read_metadata(self, entry_id):
        try:
            path = self._metadata_path(entry_id)
            with open(path, "r", encoding="utf-8") as file:
                metadata = json.load(file)
            if (
                not isinstance(metadata, dict)
                or self._normalize_uuid(metadata.get("entry_id")) != entry_id
                or not isinstance(metadata.get("project_name"), str)
                or not metadata["project_name"]
            ):
                raise ProjectTrashError(INVALID_PROJECT)
            project_id = metadata.get("project_id") or ""
            if project_id:
                metadata["project_id"] = self._normalize_uuid(project_id)
            return metadata
        except ProjectTrashError:
            raise
        except (json.JSONDecodeError, OSError, TypeError):
            raise ProjectTrashError(INVALID_PROJECT) from None

    def _remove_metadata(self, entry_id):
        try:
            path = self._metadata_path(entry_id)
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            raise ProjectTrashError(LOCAL_STORAGE_ERROR) from None

    def _binding_for_project(self, project_name):
        writing_root = os.path.join(
            self.workspace_dir, project_name, "집필모드"
        )
        local_key = self.store.local_key_for(writing_root)
        return self.store.get_project(local_key)

    def _require_authenticated_client(self):
        if self.supabase is None:
            raise ProjectTrashError(NETWORK_UNAVAILABLE)
        if getattr(self.supabase, "_antigravity_authenticated", None) is False:
            raise ProjectTrashError(AUTH_REQUIRED)

    @staticmethod
    def _classify_server_error(error):
        message = str(error or "").lower()
        status = getattr(error, "status_code", None)
        code = str(getattr(error, "code", "") or "").lower()
        combined = message + " " + code
        if status == 401 or any(marker in combined for marker in (
            "auth_required", "invalid jwt", "jwt expired", "not authenticated",
        )):
            return AUTH_REQUIRED
        if status == 403 or any(marker in combined for marker in (
            "forbidden", "permission denied", "42501",
        )):
            return ACCESS_DENIED
        if "project_purged" in combined:
            return SERVER_PROJECT_PURGED
        if "project_not_found" in combined:
            return SERVER_PROJECT_MISSING
        if any(marker in combined for marker in (
            "network", "connection", "timeout", "timed out", "dns",
            "unreachable", "refused", "winerror", "temporarily unavailable",
        )):
            return NETWORK_UNAVAILABLE
        return SERVER_OPERATION_FAILED

    def _rpc(self, name, params):
        self._require_authenticated_client()
        try:
            action = lambda: self.supabase.rpc(name, params).execute()
            response = (
                self.authenticated_call(action, self.supabase)
                if self.authenticated_call
                else action()
            )
        except Exception as error:
            raise ProjectTrashError(
                self._classify_server_error(error)
            ) from None
        if response is None or not hasattr(response, "data"):
            raise ProjectTrashError(INVALID_RESPONSE)
        return response.data

    @staticmethod
    def _validate_server_rows(rows):
        if not isinstance(rows, list):
            raise ProjectTrashError(INVALID_RESPONSE)
        validated = []
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ProjectTrashError(INVALID_RESPONSE)
            try:
                project_id = str(uuid.UUID(str(row["project_id"])))
                name = row["name"]
                trashed_at = row["trashed_at"]
            except (KeyError, TypeError, ValueError, AttributeError):
                raise ProjectTrashError(INVALID_RESPONSE) from None
            if (
                project_id in seen
                or not isinstance(name, str)
                or not name
                or not isinstance(trashed_at, str)
                or not trashed_at
            ):
                raise ProjectTrashError(INVALID_RESPONSE)
            seen.add(project_id)
            validated.append((project_id, name, trashed_at))
        return validated

    def _local_entries(self):
        entries = {}
        if not os.path.isdir(self.index_dir):
            return entries
        for filename in os.listdir(self.index_dir):
            if not filename.endswith(TRASH_METADATA_SUFFIX):
                continue
            entry_id = filename[:-len(TRASH_METADATA_SUFFIX)]
            try:
                entry_id = self._normalize_uuid(entry_id)
                metadata = self._read_metadata(entry_id)
                data_path = self._entry_data_path(entry_id)
            except ProjectTrashError:
                continue
            if not os.path.isdir(data_path):
                continue
            project_id = metadata.get("project_id") or ""
            entries[entry_id] = TrashedProject(
                entry_id=entry_id,
                project_name=metadata["project_name"],
                project_id=project_id,
                trashed_at=str(metadata.get("trashed_at") or ""),
                local_available=True,
                server_available=False,
            )
        return entries

    def list_projects(self):
        entries = self._local_entries()
        self.last_server_error = ""
        if self.supabase is not None:
            try:
                rows = self._validate_server_rows(
                    self._rpc("list_trashed_projects", {})
                )
                for project_id, name, trashed_at in rows:
                    local = entries.get(project_id)
                    entries[project_id] = TrashedProject(
                        entry_id=project_id,
                        project_name=(
                            local.project_name if local else name
                        ),
                        project_id=project_id,
                        trashed_at=trashed_at,
                        local_available=bool(local and local.local_available),
                        server_available=True,
                    )
            except ProjectTrashError as error:
                self.last_server_error = error.user_message
        return sorted(
            sorted(
                entries.values(),
                key=lambda entry: (
                    entry.project_name.casefold(),
                    entry.project_name,
                    entry.entry_id,
                ),
            ),
            key=lambda entry: entry.trashed_at,
            reverse=True,
        )

    def _deactivate_project_config(self, project_name):
        order = self.pm.global_config.setdefault("project_order", [])
        self.pm.global_config["project_order"] = [
            name for name in order if name != project_name
        ]
        if self.pm.global_config.get("last_project") == project_name:
            self.pm.global_config["last_project"] = ""
        self.pm.save_global_config()

    def _restore_project_config(self, project_name):
        order = self.pm.global_config.setdefault("project_order", [])
        if project_name not in order:
            order.append(project_name)
        self.pm.save_global_config()

    def trash_project(self, project_name):
        try:
            destination = resolve_local_project_destination(
                self.workspace_dir, project_name, require_available=False
            )
        except LocalProjectPathError:
            raise ProjectTrashError(INVALID_PROJECT) from None
        if getattr(self.pm, "current_project", None) == project_name:
            raise ProjectTrashError(CURRENT_PROJECT_OPEN)
        if not os.path.isdir(destination.project_path):
            raise ProjectTrashError(INVALID_PROJECT)

        binding = self._binding_for_project(project_name)
        bound_project_id = (
            self._normalize_uuid(binding["project_id"]) if binding else ""
        )
        # A permanently deleted server UUID can never be reused or restored.
        # Keep the remaining local files recoverable as a local-only trash
        # entry while preserving the append-only local provenance binding.
        server_was_purged = bool(
            binding and binding.get("server_state") == "purged"
        )
        if binding and not server_was_purged:
            counts = self.store.counts(binding["local_key"])
            if counts["total"]:
                raise ProjectTrashError(PENDING_SYNC)
        project_id = "" if server_was_purged else bound_project_id
        entry_id = project_id or str(uuid.uuid4())
        data_path = self._entry_data_path(entry_id)
        if os.path.lexists(data_path):
            raise ProjectTrashError(LOCAL_STORAGE_ERROR)

        trashed_at = self._utc_now()
        metadata = {
            "schema_version": 1,
            "entry_id": entry_id,
            "project_name": project_name,
            "project_id": project_id,
            "trashed_at": trashed_at,
            "state": "preparing",
        }
        self._write_metadata(metadata)

        if project_id:
            try:
                data = self._rpc(
                    "trash_project", {"p_project_id": project_id}
                )
            except ProjectTrashError as error:
                if error.code in {
                    SERVER_PROJECT_PURGED,
                    SERVER_PROJECT_MISSING,
                }:
                    # The local cache can lag behind a purge performed on
                    # another device. The server data is already gone, so the
                    # only safe remaining action is a recoverable local move.
                    server_was_purged = True
                    project_id = ""
                    metadata["project_id"] = ""
                    self._write_metadata(metadata)
                else:
                    try:
                        self._remove_metadata(entry_id)
                    except ProjectTrashError:
                        pass
                    raise
            if project_id and (
                not isinstance(data, dict) or data.get("status") != "trashed"
            ):
                try:
                    self._remove_metadata(entry_id)
                except ProjectTrashError:
                    pass
                raise ProjectTrashError(INVALID_RESPONSE)
            if project_id:
                trashed_at = str(data.get("trashed_at") or trashed_at)
                metadata["trashed_at"] = trashed_at
                metadata["state"] = "server_trashed"
                self._write_metadata(metadata)
                self.store.set_project_server_state(project_id, "trashed")

        try:
            os.replace(destination.project_path, data_path)
        except OSError:
            # If the server RPC already succeeded, keep the journal so the
            # exact same operation can be retried without losing local data.
            raise ProjectTrashError(LOCAL_STORAGE_ERROR) from None

        metadata["state"] = "trashed"
        try:
            self._write_metadata(metadata)
            self._deactivate_project_config(project_name)
        except ProjectTrashError:
            # The project is already safe in the trash even if metadata/config
            # finalization needs a later retry.
            raise
        if server_was_purged and bound_project_id:
            self.store.set_project_server_state(bound_project_id, "purged")
        return TrashedProject(
            entry_id=entry_id,
            project_name=project_name,
            project_id=project_id,
            trashed_at=trashed_at,
            local_available=True,
            server_available=bool(project_id),
        )

    def trash_server_project(self, project_id, project_name=""):
        project_id = self._normalize_uuid(project_id)
        data = self._rpc(
            "trash_project", {"p_project_id": project_id}
        )
        if not isinstance(data, dict) or data.get("status") != "trashed":
            raise ProjectTrashError(INVALID_RESPONSE)
        name = str(data.get("name") or project_name or "").strip()
        trashed_at = str(data.get("trashed_at") or "")
        if not name or not trashed_at:
            raise ProjectTrashError(INVALID_RESPONSE)
        self.store.set_project_server_state(project_id, "trashed")
        return TrashedProject(
            entry_id=project_id,
            project_name=name,
            project_id=project_id,
            trashed_at=trashed_at,
            local_available=False,
            server_available=True,
        )

    def restore_project(self, entry):
        if not isinstance(entry, TrashedProject):
            raise ProjectTrashError(INVALID_PROJECT)
        entry_id = self._normalize_uuid(entry.entry_id)
        metadata = None
        data_path = self._entry_data_path(entry_id)
        destination = None
        if entry.local_available:
            metadata = self._read_metadata(entry_id)
            try:
                destination = resolve_local_project_destination(
                    self.workspace_dir, metadata["project_name"]
                )
            except LocalProjectPathError as error:
                if error.code == PROJECT_NAME_CONFLICT:
                    raise ProjectTrashError(LOCAL_NAME_CONFLICT) from None
                raise ProjectTrashError(INVALID_PROJECT) from None
            if not os.path.isdir(data_path):
                raise ProjectTrashError(INVALID_PROJECT)

        if entry.project_id:
            data = self._rpc(
                "restore_project", {"p_project_id": entry.project_id}
            )
            if not isinstance(data, dict) or data.get("status") != "active":
                raise ProjectTrashError(INVALID_RESPONSE)
            self.store.set_project_server_state(entry.project_id, "active")

        if destination is not None:
            try:
                os.replace(data_path, destination.project_path)
            except OSError:
                raise ProjectTrashError(LOCAL_STORAGE_ERROR) from None
            self._remove_metadata(entry_id)
            self._restore_project_config(metadata["project_name"])
        return entry.project_name

    def purge_project(self, entry):
        if not isinstance(entry, TrashedProject):
            raise ProjectTrashError(INVALID_PROJECT)
        entry_id = self._normalize_uuid(entry.entry_id)
        data_path = self._entry_data_path(entry_id)

        if entry.project_id:
            data = self._rpc(
                "purge_project", {"p_project_id": entry.project_id}
            )
            if not isinstance(data, dict) or data.get("status") != "purged":
                raise ProjectTrashError(INVALID_RESPONSE)
            self.store.set_project_server_state(entry.project_id, "purged")

        try:
            if os.path.isdir(data_path):
                shutil.rmtree(data_path)
            self._remove_metadata(entry_id)
        except ProjectTrashError:
            raise
        except OSError:
            raise ProjectTrashError(LOCAL_STORAGE_ERROR) from None

        return True
