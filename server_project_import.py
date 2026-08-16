import json
import os
import threading
import uuid
from dataclasses import dataclass

from project_paths import (
    IMPORT_MARKER_FILENAME,
    PROJECT_NAME_CONFLICT,
    LocalProjectPathError,
    resolve_local_project_destination,
)


AUTH_REQUIRED = "auth_required"
NETWORK_UNAVAILABLE = "network_unavailable"
ACCESS_DENIED = "access_denied"
INVALID_RESPONSE = "invalid_response"
IMPORT_ALREADY_COMPLETE = "import_already_complete"
IMPORT_IN_PROGRESS = "import_in_progress"
PROJECT_BINDING_CONFLICT = "project_binding_conflict"
LOCAL_STORAGE_ERROR = "local_storage_error"
SNAPSHOT_PULL_FAILED = "snapshot_pull_failed"
SNAPSHOT_APPLY_FAILED = "snapshot_apply_failed"
PENDING_LOCAL_CHANGES = "pending_local_changes"

LOCAL_AVAILABLE = "available"
LOCAL_CURRENT = "current"
LOCAL_OTHER = "other"
LOCAL_MISSING = "missing"


_ERROR_MESSAGES = {
    AUTH_REQUIRED: "서버 작품을 조회하려면 Supabase 로그인이 필요합니다.",
    NETWORK_UNAVAILABLE: "서버에 연결할 수 없습니다. 네트워크 연결을 확인해주세요.",
    ACCESS_DENIED: "서버 작품 목록을 조회할 권한이 없습니다.",
    INVALID_RESPONSE: "서버 작품 목록 응답이 올바르지 않습니다.",
    IMPORT_ALREADY_COMPLETE: "이미 가져온 서버 작품입니다.",
    IMPORT_IN_PROGRESS: "이 서버 작품을 이미 가져오는 중입니다.",
    PROJECT_BINDING_CONFLICT: "서버 작품 UUID와 로컬 작품 연결이 충돌합니다.",
    LOCAL_STORAGE_ERROR: "로컬 작품 또는 동기화 상태를 저장하지 못했습니다.",
    SNAPSHOT_PULL_FAILED: "서버 작품의 문서 snapshot을 가져오지 못했습니다.",
    SNAPSHOT_APPLY_FAILED: "서버 작품의 문서를 로컬에 안전하게 적용하지 못했습니다.",
    PENDING_LOCAL_CHANGES: (
        "로컬 폴더는 없지만 서버에 보내지 못한 변경 기록이 남아 있습니다. "
        "동기화 상태를 정리하기 전에는 다시 가져올 수 없습니다."
    ),
}


class ServerProjectCatalogError(RuntimeError):
    """A stable, user-safe failure raised while reading the server project catalog."""

    def __init__(self, code):
        if code not in _ERROR_MESSAGES:
            code = INVALID_RESPONSE
        self.code = code
        self.user_message = _ERROR_MESSAGES[code]
        super().__init__(self.user_message)


class ServerProjectImportError(RuntimeError):
    def __init__(self, code):
        if code not in _ERROR_MESSAGES:
            code = LOCAL_STORAGE_ERROR
        self.code = code
        self.user_message = _ERROR_MESSAGES[code]
        super().__init__(self.user_message)


@dataclass(frozen=True)
class ServerProject:
    project_id: str
    name: str
    created_at: str
    updated_at: str
    already_imported: bool
    local_project_name: str = ""
    import_incomplete: bool = False
    local_state: str = LOCAL_AVAILABLE
    local_path: str = ""

    @property
    def masked_project_id(self):
        return f"{self.project_id[:8]}…{self.project_id[-4:]}"

    @property
    def can_import(self):
        if self.local_state == LOCAL_OTHER:
            return False
        return (
            self.import_incomplete
            or self.local_state in {LOCAL_AVAILABLE, LOCAL_MISSING}
            or not self.already_imported
        )


@dataclass(frozen=True)
class ServerProjectImportResult:
    project_id: str
    local_project_name: str
    writing_root_path: str
    document_count: int
    applied_change_count: int
    resumed: bool


class ServerProjectCatalogService:
    """Read the RLS-filtered Supabase project catalog and compare local bindings."""

    PROJECT_FIELDS = "project_id,name,created_at,updated_at"

    def __init__(
        self, supabase_client, store, workspace_dir=None, authenticated_call=None
    ):
        self.supabase = supabase_client
        self.store = store
        self.workspace_dir = (
            os.path.normcase(os.path.abspath(workspace_dir))
            if workspace_dir else ""
        )
        self.authenticated_call = authenticated_call

    def _binding_local_state(self, binding):
        if binding is None:
            return LOCAL_AVAILABLE, ""
        writing_root = os.path.abspath(binding.get("local_key") or "")
        project_path = os.path.dirname(writing_root)
        if not os.path.isdir(writing_root):
            return LOCAL_MISSING, project_path
        if not self.workspace_dir:
            return LOCAL_CURRENT, project_path
        project_workspace = os.path.normcase(os.path.dirname(project_path))
        if project_workspace == self.workspace_dir:
            return LOCAL_CURRENT, project_path
        return LOCAL_OTHER, project_path

    def list_projects(self):
        self._require_authenticated_client()
        try:
            action = lambda: (
                self.supabase.table("projects")
                .select(self.PROJECT_FIELDS)
                .execute()
            )
            response = (
                self.authenticated_call(action, self.supabase)
                if self.authenticated_call
                else action()
            )
        except Exception as error:
            raise ServerProjectCatalogError(
                self._classify_query_error(error)
            ) from None

        rows = self._validated_rows(response)
        projects = []
        for row in rows:
            binding = self.store.get_project_by_id(row["project_id"])
            journal = self.store.get_project_import(row["project_id"])
            import_incomplete = bool(
                journal and journal.get("state") != "complete"
            )
            local_state, local_path = self._binding_local_state(binding)
            projects.append(ServerProject(
                project_id=row["project_id"],
                name=row["name"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                already_imported=bool(
                    binding is not None
                    and local_state in {LOCAL_CURRENT, LOCAL_OTHER}
                    and not import_incomplete
                ),
                local_project_name=binding["project_name"] if binding else "",
                import_incomplete=import_incomplete,
                local_state=local_state,
                local_path=local_path,
            ))
        return sorted(
            projects,
            key=lambda project: (
                project.name.casefold(),
                project.name,
                project.project_id,
            ),
        )

    def _require_authenticated_client(self):
        if self.supabase is None:
            raise ServerProjectCatalogError(NETWORK_UNAVAILABLE)
        if getattr(self.supabase, "_antigravity_authenticated", None) is False:
            raise ServerProjectCatalogError(AUTH_REQUIRED)

    @classmethod
    def _validated_rows(cls, response):
        if response is None or not hasattr(response, "data"):
            raise ServerProjectCatalogError(INVALID_RESPONSE)
        rows = response.data
        if not isinstance(rows, list):
            raise ServerProjectCatalogError(INVALID_RESPONSE)

        validated = []
        seen_ids = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ServerProjectCatalogError(INVALID_RESPONSE)
            if set(("project_id", "name", "created_at", "updated_at")) - set(row):
                raise ServerProjectCatalogError(INVALID_RESPONSE)
            try:
                project_id = str(uuid.UUID(str(row["project_id"])))
            except (AttributeError, TypeError, ValueError):
                raise ServerProjectCatalogError(INVALID_RESPONSE) from None
            name = row["name"]
            created_at = row["created_at"]
            updated_at = row["updated_at"]
            if (
                not isinstance(name, str)
                or not name.strip()
                or not isinstance(created_at, str)
                or not created_at
                or not isinstance(updated_at, str)
                or not updated_at
                or project_id in seen_ids
            ):
                raise ServerProjectCatalogError(INVALID_RESPONSE)
            seen_ids.add(project_id)
            validated.append({
                "project_id": project_id,
                "name": name,
                "created_at": created_at,
                "updated_at": updated_at,
            })
        return validated

    @staticmethod
    def _classify_query_error(error):
        message = str(error or "").lower()
        status = getattr(error, "status_code", None)
        code = str(getattr(error, "code", "") or "").lower()

        if status == 401 or any(marker in message or marker in code for marker in (
            "auth_required",
            "invalid jwt",
            "jwt expired",
            "not authenticated",
            "authentication required",
        )):
            return AUTH_REQUIRED
        if status == 403 or any(marker in message or marker in code for marker in (
            "forbidden",
            "permission denied",
            "row-level security",
            "row level security",
            "42501",
        )):
            return ACCESS_DENIED
        if any(marker in message or marker in code for marker in (
            "network",
            "connection",
            "disconnected",
            "timeout",
            "timed out",
            "dns",
            "unreachable",
            "refused",
            "winerror",
            "temporarily unavailable",
            "name or service",
        )):
            return NETWORK_UNAVAILABLE
        return INVALID_RESPONSE


class ServerProjectImportService:
    """Bind and materialize one explicitly selected server project without writes."""

    _locks_guard = threading.Lock()
    _project_locks = {}

    def __init__(self, sync_manager, store, workspace_dir, device_id):
        self.sync_manager = sync_manager
        self.store = store
        self.workspace_dir = os.path.abspath(workspace_dir)
        self.device_id = str(uuid.UUID(str(device_id)))

    def import_project(self, project_id, server_name, local_project_name=None):
        try:
            project_id = str(uuid.UUID(str(project_id)))
        except (AttributeError, TypeError, ValueError):
            raise ServerProjectImportError(INVALID_RESPONSE) from None
        local_project_name = (
            server_name if local_project_name is None else local_project_name
        )
        catalog = ServerProjectCatalogService(
            self.sync_manager.supabase,
            self.store,
            authenticated_call=self.sync_manager._call_with_session,
        )
        catalog._require_authenticated_client()

        with self._locks_guard:
            project_lock = self._project_locks.setdefault(
                project_id, threading.Lock()
            )
        if not project_lock.acquire(blocking=False):
            raise ServerProjectImportError(IMPORT_IN_PROGRESS)

        destination = None
        journal_started = False
        phase = "prepare"
        previous_sync_context = None
        try:
            destination, recovering_missing = self._prepare_destination(
                project_id, local_project_name
            )
            from project_creation_v1 import initialize_existing_project
            from project_manager_writing import WritingProjectManager

            # Importing a server project creates a local one, so the standard
            # folders must come from the journalled transaction that issues
            # their UUIDs. The destination is already reserved with an import
            # marker in it, and resuming keeps the ids the journal recorded.
            initialize_existing_project(
                self.workspace_dir, destination.project_name
            )

            wpm = WritingProjectManager.create_detached(
                self.workspace_dir,
                destination.project_name,
                destination.writing_root_path,
            )
            phase = "binding"
            context = self.store.begin_project_import(
                destination.writing_root_path,
                destination.project_name,
                project_id,
                server_name=server_name,
                reset_complete=recovering_missing,
            )
            journal_started = True
            if (
                context["previous_state"] == "complete"
                and not recovering_missing
            ):
                self._write_marker(destination, project_id, "complete")
                raise ServerProjectImportError(IMPORT_ALREADY_COMPLETE)
            resumed = recovering_missing or context["previous_state"] in {
                "preparing", "pulling", "failed"
            }
            self.store.set_project_import_state(project_id, "pulling")
            self._write_marker(destination, project_id, "pulling")

            previous_sync_context = self._capture_sync_context()
            self.sync_manager.configure_v2(
                wpm,
                destination.project_name,
                self.device_id,
                store=self.store,
                project_id=project_id,
                recover_local_changes=False,
            )
            self.sync_manager.set_remote_protected_paths_provider(None)
            self.sync_manager.set_active_document_paths_provider(None)

            phase = "pull"
            remote_documents = self.sync_manager._fetch_v2_project_documents(
                require_connection=True,
                check_project_status=False,
            )
            phase = "apply"
            changes = self.sync_manager._apply_v2_remote_documents(
                remote_documents, strict=True
            )

            phase = "identity"
            self._adopt_server_identity(destination, project_id, wpm)

            phase = "complete"
            self.store.set_project_import_state(project_id, "complete")
            try:
                self._write_marker(destination, project_id, "complete")
            except Exception:
                self.store.set_project_import_state(
                    project_id, "failed", _ERROR_MESSAGES[LOCAL_STORAGE_ERROR]
                )
                raise
            return ServerProjectImportResult(
                project_id=project_id,
                local_project_name=destination.project_name,
                writing_root_path=destination.writing_root_path,
                document_count=len(remote_documents),
                applied_change_count=len(changes),
                resumed=resumed,
            )
        except (LocalProjectPathError, ServerProjectCatalogError):
            raise
        except ServerProjectImportError:
            raise
        except Exception as error:
            code = self._failure_code(phase, error)
            if journal_started:
                try:
                    self.store.set_project_import_state(
                        project_id, "failed", _ERROR_MESSAGES[code]
                    )
                except Exception:
                    pass
            if destination is not None:
                try:
                    self._write_marker(destination, project_id, "failed")
                except Exception:
                    pass
            raise ServerProjectImportError(code) from None
        finally:
            if previous_sync_context is not None:
                self._restore_sync_context(previous_sync_context)
            project_lock.release()

    def _adopt_server_identity(self, destination, project_id, wpm):
        """Give the imported project the ids the server already holds.

        Without this the project cannot open: the pull writes documents that
        identity has never heard of, and opening audits identity against the
        file tree. The folders are just as wrong — they were minted here while
        the server already had the same folders under the other device's ids —
        so the folder publisher would report a name clash forever.
        """
        from project_creation_v1 import adopt_server_identity

        local_key = self.store.local_key_for(destination.writing_root_path)
        folder_rows = []
        try:
            client = self.sync_manager.supabase
            if client is not None:
                folder_rows = (
                    self.sync_manager._fetch_v2_project_folders(client) or []
                )
        except Exception:
            # A server without a folder projection still imports; those folders
            # simply keep the ids this client issued.
            folder_rows = []

        resolved = self.sync_manager._folder_rows_with_tree_paths(folder_rows)
        sync_rows = {
            "local_key": local_key,
            "projects": [{"project_id": project_id, "local_key": local_key}],
            "folders": [
                {"folder_id": row["folder_id"], "local_path": row["local_path"]}
                for row in resolved.values()
            ],
            "documents": [
                {
                    "document_id": document["document_id"],
                    "local_path": document["local_path"],
                }
                for document in self.store.list_documents(local_key)
                if not document["is_deleted"]
            ],
        }
        adopt_server_identity(
            os.path.dirname(destination.writing_root_path),
            sync_rows,
            destination.project_name,
            order_hint=(getattr(wpm, "project_settings", None) or {}).get(
                "tree_order"
            ),
        )

    def _prepare_destination(self, project_id, local_project_name):
        destination = resolve_local_project_destination(
            self.workspace_dir, local_project_name, require_available=False
        )
        binding = self.store.get_project_by_id(project_id)
        recovering_missing = bool(
            binding is not None
            and not os.path.isdir(binding.get("local_key") or "")
        )
        if binding is not None:
            journal = self.store.get_project_import(project_id)
            if (
                not recovering_missing
                and (journal is None or journal.get("state") == "complete")
            ):
                raise ServerProjectImportError(IMPORT_ALREADY_COMPLETE)
            expected_key = self.store.local_key_for(
                destination.writing_root_path
            )
            if binding["local_key"] != expected_key:
                raise ServerProjectImportError(PROJECT_BINDING_CONFLICT)
            if recovering_missing:
                counts = self.store.counts(binding["local_key"])
                if counts["total"]:
                    raise ServerProjectImportError(PENDING_LOCAL_CHANGES)

        if os.path.lexists(destination.project_path):
            if not os.path.isdir(destination.project_path):
                raise LocalProjectPathError(PROJECT_NAME_CONFLICT)
            marker = self._read_marker(destination)
            marker_matches = marker.get("project_id") == project_id
            binding_matches = binding is not None
            if not marker_matches and not binding_matches:
                raise LocalProjectPathError(PROJECT_NAME_CONFLICT)
        else:
            os.mkdir(destination.project_path)
            try:
                self._write_marker(destination, project_id, "preparing")
            except Exception:
                try:
                    os.rmdir(destination.project_path)
                except OSError:
                    pass
                raise

        if os.path.lexists(destination.writing_root_path):
            if not os.path.isdir(destination.writing_root_path):
                raise LocalProjectPathError(PROJECT_NAME_CONFLICT)
        else:
            os.mkdir(destination.writing_root_path)
        self._write_marker(destination, project_id, "preparing")
        return destination, recovering_missing

    @staticmethod
    def _marker_path(destination):
        return os.path.join(destination.project_path, IMPORT_MARKER_FILENAME)

    def _read_marker(self, destination):
        try:
            with open(
                self._marker_path(destination), "r", encoding="utf-8"
            ) as marker_file:
                marker = json.load(marker_file)
            return marker if isinstance(marker, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _write_marker(self, destination, project_id, state):
        marker_path = self._marker_path(destination)
        temp_path = marker_path + ".tmp"
        payload = {
            "version": 1,
            "project_id": project_id,
            "local_project_name": destination.project_name,
            "state": state,
        }
        with open(temp_path, "w", encoding="utf-8") as marker_file:
            json.dump(payload, marker_file, ensure_ascii=False, indent=2)
            marker_file.flush()
            os.fsync(marker_file.fileno())
        os.replace(temp_path, marker_path)

    def _capture_sync_context(self):
        return (
            self.sync_manager._v2_store,
            self.sync_manager._v2_context,
            self.sync_manager._v2_wpm,
            self.sync_manager._v2_device_id,
            self.sync_manager._v2_protected_paths_provider,
            self.sync_manager._v2_active_paths_provider,
        )

    def _restore_sync_context(self, previous):
        (
            self.sync_manager._v2_store,
            self.sync_manager._v2_context,
            self.sync_manager._v2_wpm,
            self.sync_manager._v2_device_id,
            self.sync_manager._v2_protected_paths_provider,
            self.sync_manager._v2_active_paths_provider,
        ) = previous

    @staticmethod
    def _failure_code(phase, error):
        if phase == "pull":
            classified = ServerProjectCatalogService._classify_query_error(error)
            if classified in {
                AUTH_REQUIRED, NETWORK_UNAVAILABLE, ACCESS_DENIED
            }:
                return classified
            return SNAPSHOT_PULL_FAILED
        if phase == "apply":
            return SNAPSHOT_APPLY_FAILED
        return LOCAL_STORAGE_ERROR
