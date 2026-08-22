import copy
import os
import json
import re
import stat
import sys
import threading
import time
import unicodedata
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from PyQt6.QtCore import (
    QObject, QThread, pyqtSignal, pyqtSlot, QMutex, QMutexLocker, QTimer,
    QMetaObject, Qt,
)

from datetime import datetime

from cloud_config import (
    CLOUD_INVALID_MESSAGE,
    classify_cloud_error,
    load_cloud_client_config,
)
from runtime_profile import is_forced_offline
from sync_contract import SyncContractError, require_server_compatibility
from binder_order import (
    ROOT_STORAGE_NAMES,
    canonical_manuscript_children,
    canonical_root_children,
    canonical_tree_parent_path,
    is_fixed_manuscript_parent,
)
from project_paths import LocalProjectPathError, validate_local_project_name
from sync_diagnostics import SyncDiagnosticLog, format_diagnostic_report
from sync_v2_store import SyncV2Store
from three_way_merge import three_way_merge


# commit_folder refuses these for a reason that no amount of waiting changes:
# a name someone else holds, a parent that is not there, a tree that cannot
# exist. Sending them again on a timer would spin forever, and because folder
# work rides inside the tree-order commit it would take binder order down with
# it. They are reported once and stepped over.
PERMANENT_FOLDER_ERROR_CODES = frozenset({
    "PARENT_FOLDER_NOT_FOUND",
    "FOLDER_NAME_CONFLICT",
    "FOLDER_ALREADY_EXISTS",
    "FOLDER_NOT_FOUND",
    "FOLDER_NOT_EMPTY",
    "FOLDER_CYCLE",
    "INVALID_ARGUMENT",
})
TREE_ORDER_DOCUMENT_PATH = "__antigravity__/tree-order.json"
TRASH_PURGE_DOCUMENT_PATH = "__antigravity__/trash-purge.json"
LEASE_CONFLICT_RETRY_DELAYS_MS = (3000, 5000, 10000, 30000)
NETWORK_RETRY_DELAYS_MS = (5000, 15000, 30000, 60000)
# 종료는 하나의 예산 안에서만 원격 작업을 시도한다. 예산이 끝나면 새 요청을
# 만들지 않고 다음 실행으로 미룬다.
SHUTDOWN_BUDGET_MS = 5000
# remote flush 가 예산을 다 쓰면 lease 해제와 worker 정리에 쓸 시간이 없다.
# 나머지는 종료 후반부를 위해 남긴다.
SHUTDOWN_FLUSH_BUDGET_MS = 3500
# 예산이 끝나도 실행 중인 QThread 는 강제 종료하지 않는다. HTTP timeout(5초)이
# 끝날 때까지만 더 기다려 스레드 파괴로 인한 종료 크래시를 막는다.
SHUTDOWN_GRACE_MS = 6000
MAX_WINDOWS_COMPONENT_UTF16_UNITS = 255
MAX_WINDOWS_DIRECTORY_PATH = 247
TREE_ROOT_STORAGE_NAMES = ROOT_STORAGE_NAMES


class RemoteRenameSkipped(Exception):
    """A prevalidated remote rename failed its final mutable checks.

    Raised from inside the identity transaction so the journal is dropped and
    identity stays exactly where the unmoved directory is.
    """


def supabase_config_dir():
    """Return the directory that holds public Supabase client settings."""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def load_or_create_device_id():
    from runtime_profile import app_data_dir

    data_dir = app_data_dir()
    os.makedirs(data_dir, exist_ok=True)
    device_id_path = os.path.join(data_dir, ".device_id")
    device_id = ""
    try:
        with open(device_id_path, "r", encoding="utf-8") as device_file:
            device_id = str(uuid.UUID(device_file.read().strip()))
    except (FileNotFoundError, OSError, TypeError, ValueError):
        device_id = str(uuid.uuid4())
        temp_path = device_id_path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as device_file:
            device_file.write(device_id)
            device_file.flush()
            os.fsync(device_file.fileno())
        os.replace(temp_path, device_id_path)
    return device_id


def is_internal_sync_path(relative_path):
    path = str(relative_path or "").replace("\\", "/").strip("/")
    return path == TREE_ORDER_DOCUMENT_PATH or path.startswith("__antigravity__/")


def is_live_document_path(relative_path):
    """Return whether a local text path represents a live cloud document."""
    path = str(relative_path or "").replace("\\", "/").strip("/")
    if not path:
        return False
    if is_internal_sync_path(path):
        return False
    return not (
        path == "백업"
        or path.startswith("백업/")
        or path == "메인/휴지통"
        or path.startswith("메인/휴지통/")
        or path == "휴지통"
        or path.startswith("휴지통/")
    )


class LockWorker(QThread):
    # Keep QThread.finished intact. Emitting a custom signal with the same name
    # before run() actually returns can schedule deleteLater() while the native
    # thread is still running, which makes Qt terminate the whole process.
    resultReady = pyqtSignal(bool, str, object) # success, msg, server_updated_at
    
    def __init__(self, sync_manager, project_name, relative_path, session_id):
        super().__init__()
        self.sync_manager = sync_manager
        self.project_name = project_name
        self.relative_path = relative_path
        self.session_id = session_id
    def run(self):
        owns_client = False
        try:
            # v2 must reuse the profile's one authenticated client. Creating a
            # client per file-open can rotate the same refresh token concurrently.
            owns_client = not self.sync_manager.is_v2_enabled
            self.supabase = (
                SyncManager.create_supabase_client()
                if owns_client else self.sync_manager.supabase
            )
            success, msg = self.sync_manager.check_and_acquire_lock(self.project_name, self.relative_path, self.session_id, client=self.supabase)
            server_updated_at = None
            if success:
                server_updated_at = self.sync_manager.get_file_updated_at(self.project_name, self.relative_path, client=self.supabase)
            self.resultReady.emit(success, msg, server_updated_at)
        except Exception as e:
            self.resultReady.emit(False, str(e), None)
        finally:
            if owns_client:
                SyncManager._close_supabase_client(
                    getattr(self, "supabase", None)
                )

class SaveWorker(QThread):
    resultReady = pyqtSignal(bool, str, str, object) # success(bool), error_msg(str), rel_path(str), new_updated_at(str|None)
    conflict_detected = pyqtSignal(str, str, str, str) # project, rel_path, local_content, server_content
    
    def __init__(self, supabase_client, wpm, project_name, relative_path, content, local_updated_at=None, force_overwrite=False):
        super().__init__()
        self.supabase = supabase_client
        self.wpm = wpm
        self.project_name = project_name
        self.relative_path = relative_path
        self.content = content
        self.local_updated_at = local_updated_at
        self.force_overwrite = force_overwrite

    def run(self):
        try:
            self.supabase = SyncManager.create_supabase_client()
            new_updated_at = None
            # 1. 로컬 저장 (백그라운드 스레드에서 I/O)
            if self.wpm and self.relative_path:
                if not self.wpm.write_text_file(self.relative_path, self.content):
                    raise OSError("로컬 파일 저장에 실패했습니다.")
                
            # 2. 클라우드 동기화
            if not self.supabase:
                self.resultReady.emit(False, "서버 연결 없음", self.relative_path, None)
                return

            if not self.force_overwrite:
                # 충돌 확인
                resp = self.supabase.table("writing_contents").select("updated_at, content").eq("project_name", self.project_name).eq("relative_path", self.relative_path).execute()
                if resp.data:
                    server_updated_at = resp.data[0].get("updated_at")
                    if self.local_updated_at is None or server_updated_at != self.local_updated_at:
                        server_content = resp.data[0].get("content", "")
                        self.conflict_detected.emit(self.project_name, self.relative_path, self.content, server_content)
                        self.resultReady.emit(True, "충돌 감지", self.relative_path, server_updated_at)
                        return

            resp = self.supabase.table("writing_contents").upsert({
                "project_name": self.project_name,
                "relative_path": self.relative_path,
                "content": self.content
            }).execute()
            new_updated_at = resp.data[0].get("updated_at") if resp.data else None
            
            self.resultReady.emit(True, "", self.relative_path, new_updated_at)
        except Exception as e:
            self.resultReady.emit(False, str(e), self.relative_path, None)
        finally:
            SyncManager._close_supabase_client(getattr(self, "supabase", None))

class BulkSaveWorker(QThread):
    resultReady = pyqtSignal(bool, str) # success, error_msg
    
    def __init__(self, supabase_client, wpm, project_name):
        super().__init__()
        self.supabase = supabase_client
        self.wpm = wpm
        self.project_name = project_name

    def run(self):
        try:
            self.supabase = SyncManager.create_supabase_client()
            if not self.wpm or not self.wpm.writing_root_path:
                self.resultReady.emit(False, "집필모드 경로를 찾을 수 없습니다.")
                return

            if not self.supabase:
                self.resultReady.emit(True, "")
                return
                
            data_list = []
            import os
            for root, _, files in os.walk(self.wpm.writing_root_path):
                if "백업" in root.replace("\\", "/"):
                    continue
                for file in files:
                    if file.endswith(".txt"):
                        full_path = os.path.join(root, file)
                        rel_path = os.path.relpath(full_path, self.wpm.writing_root_path).replace("\\", "/")
                        try:
                            with open(full_path, "r", encoding="utf-8") as f:
                                content = f.read()
                            data_list.append({
                                "project_name": self.project_name,
                                "relative_path": rel_path,
                                "content": content
                            })
                        except Exception as e:
                            print(f"BulkSaveWorker file read error ({rel_path}): {e}")

            if data_list:
                # 100개씩 나눠서 업로드 (Supabase payload limit 대비)
                chunk_size = 100
                for i in range(0, len(data_list), chunk_size):
                    chunk = data_list[i:i+chunk_size]
                    self.supabase.table("writing_contents").upsert(chunk).execute()
                    
            self.resultReady.emit(True, "")
        except Exception as e:
            self.resultReady.emit(False, str(e))
        finally:
            SyncManager._close_supabase_client(getattr(self, "supabase", None))

class BackupWorker(QThread):
    resultReady = pyqtSignal(bool, str) # success, error_msg
    
    def __init__(self, supabase_client, wpm, project_name, relative_path, content):
        super().__init__()
        self.supabase = supabase_client
        self.wpm = wpm
        self.project_name = project_name
        self.relative_path = relative_path
        self.content = content

    def run(self):
        try:
            self.supabase = SyncManager.create_supabase_client()
            # 1. 로컬 정기 백업 저장
            if self.wpm and self.relative_path:
                now = datetime.now()
                bucket_minute = (now.minute // 5) * 5
                timestamp = f"{now.strftime('%Y%m%d_%H')}{bucket_minute:02d}"
                base_name = os.path.basename(self.relative_path).replace(".txt", "")
                
                # 화별 폴더(base_name) 분리
                abs_backup_path = os.path.join(self.wpm.workspace_dir, self.wpm.current_project, "집필모드", "백업", "자동저장", base_name, f"{base_name}_{timestamp}.txt")
                os.makedirs(os.path.dirname(abs_backup_path), exist_ok=True)
                with open(abs_backup_path, "w", encoding="utf-8") as f:
                    f.write(self.content)
            
            # 2. 클라우드 히스토리 백업
            if self.supabase:
                self.supabase.table("writing_history").insert({
                    "project_name": self.project_name,
                    "relative_path": self.relative_path,
                    "content": self.content
                }).execute()
            
            self.resultReady.emit(True, "")
        except Exception as e:
            self.resultReady.emit(False, str(e))
        finally:
            SyncManager._close_supabase_client(getattr(self, "supabase", None))

class AutoSaveWorker(QThread):
    resultReady = pyqtSignal(bool, str)
    
    def __init__(self, wpm, relative_path, content):
        super().__init__()
        self.wpm = wpm
        self.relative_path = relative_path
        self.content = content

    def run(self):
        try:
            if self.wpm and self.relative_path:
                from datetime import datetime
                now = datetime.now()
                bucket_minute = (now.minute // 5) * 5
                timestamp = f"{now.strftime('%Y%m%d_%H')}{bucket_minute:02d}"
                base_name = os.path.basename(self.relative_path).replace(".txt", "")
                
                # 화별 폴더(base_name) 분리
                abs_backup_path = os.path.join(self.wpm.workspace_dir, self.wpm.current_project, "집필모드", "백업", "자동저장", base_name, f"{base_name}_{timestamp}.txt")
                os.makedirs(os.path.dirname(abs_backup_path), exist_ok=True)
                with open(abs_backup_path, "w", encoding="utf-8") as f:
                    f.write(self.content)
            self.resultReady.emit(True, "")
        except Exception as e:
            self.resultReady.emit(False, str(e))

class RenameWorker(QThread):
    resultReady = pyqtSignal(bool, str)
    
    def __init__(self, supabase_client, project_name, old_rel_path, new_rel_path):
        super().__init__()
        self.supabase = supabase_client
        self.project_name = project_name
        self.old_rel_path = old_rel_path
        self.new_rel_path = new_rel_path

    def run(self):
        try:
            self.supabase = SyncManager.create_supabase_client()
            if self.supabase:
                self.supabase.table("writing_contents") \
                    .update({"relative_path": self.new_rel_path}) \
                    .eq("project_name", self.project_name) \
                    .eq("relative_path", self.old_rel_path) \
                    .execute()
                
            self.resultReady.emit(True, "")
        except Exception as e:
            self.resultReady.emit(False, str(e))
        finally:
            SyncManager._close_supabase_client(getattr(self, "supabase", None))


class V2QueueWorker(QThread):
    resultReady = pyqtSignal(bool, str, object)

    def __init__(self, sync_manager, operation_id):
        super().__init__()
        self.sync_manager = sync_manager
        self.operation_id = operation_id
        self.supabase = sync_manager.supabase

    def run(self):
        try:
            result = self.sync_manager._process_v2_operation(self.operation_id)
            kind = result.get("kind")
            self.resultReady.emit(
                kind in {"committed", "auto_merged", "conflict", "blocked"},
                result.get("error", ""),
                result,
            )
        except Exception as e:
            self.resultReady.emit(False, str(e), {"kind": "retry", "error": str(e)})


class V2StructureBatchWorker(QThread):
    resultReady = pyqtSignal(bool, str, object)

    def __init__(self, sync_manager, batch_id):
        super().__init__()
        self.sync_manager = sync_manager
        self.batch_id = batch_id

    def run(self):
        try:
            result = self.sync_manager._process_contract_structure_batch(self.batch_id)
            self.resultReady.emit(
                result.get("kind") in {
                    "atomic_structure_commit_success",
                    "atomic_structure_commit_failure",
                },
                result.get("error_message", ""),
                result,
            )
        except Exception as error:
            self.resultReady.emit(
                False, str(error),
                {"kind": "retry", "error_message": str(error)},
            )


class V2PullWorker(QThread):
    resultReady = pyqtSignal(bool, object)

    def __init__(self, sync_manager, project_id=None):
        super().__init__()
        self.sync_manager = sync_manager
        # The manager is shared and its context can be swapped while this
        # request is in the air. Ask about the project this pull was started
        # for, not whichever one is open when the reply is being built.
        self.project_id = project_id

    def run(self):
        try:
            project_id = self.project_id
            documents = self.sync_manager._fetch_v2_project_documents(
                project_id=project_id
            )
            folders = self.sync_manager._fetch_v2_project_folders(
                self.sync_manager.supabase, project_id=project_id
            )
            folder_versions = (
                self.sync_manager._fetch_v2_project_folder_versions(
                    self.sync_manager.supabase, project_id=project_id
                )
                if self.sync_manager._needs_folder_history(folders)
                else []
            )
            tree_orders = (
                self.sync_manager._fetch_v2_project_tree_orders(
                    self.sync_manager.supabase, project_id=project_id
                )
                if self.sync_manager._uses_contract_structure()
                else []
            )
            self.resultReady.emit(True, {
                "documents": documents,
                "folders": folders,
                "folder_versions": folder_versions,
                "tree_orders": tree_orders,
            })
        except Exception as error:
            self.resultReady.emit(False, str(error))


class ServerActionWorker(QThread):
    """Run a small server-only callable without blocking the editor thread."""

    resultReady = pyqtSignal(bool, object)

    def __init__(self, action):
        super().__init__()
        self.action = action

    def run(self):
        try:
            self.resultReady.emit(True, self.action())
        except Exception as error:
            self.resultReady.emit(False, error)

class SyncManager(QObject):
    syncStateChanged = pyqtSignal(str, str, int)  # state, detail, pending retry count
    conflictDetected = pyqtSignal(object)
    autoMergeApplied = pyqtSignal(object)
    remoteDocumentsApplied = pyqtSignal(object)

    _instance = None
    _mutex = QMutex()

    def __new__(cls, *args, **kwargs):
        with QMutexLocker(cls._mutex):
            if cls._instance is None:
                cls._instance = super(SyncManager, cls).__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        super().__init__()
        self._initialized = True
        self.active_workers = set()
        self._retry_queue = {}
        self._retry_active_key = None
        self._active_server_syncs = 0
        self._active_backups = 0
        self._last_sync_error = ""
        self._last_failure_offline = False
        self.current_sync_state = "saved"
        self._v2_store = None
        self._v2_context = None
        self._v2_context_generation = 0
        self._v2_wpm = None
        self._v2_device_id = None
        self._v2_worker = None
        self._v2_structure_worker = None
        self._v2_workers = []
        self._v2_callbacks = {}
        self._v2_conflict_callbacks = {}
        self._v2_leases = {}
        self._v2_pull_worker = None
        self._v2_protected_paths_provider = None
        self._v2_active_paths_provider = None
        self._v2_retry_timer = QTimer(self)
        self._v2_retry_timer.setSingleShot(True)
        self._v2_retry_timer.timeout.connect(self._run_scheduled_v2_retry)
        self._v2_retry_context = None
        self._v2_lease_retry_operation_id = None
        self._v2_lease_retry_attempt = 0
        self._v2_network_retry_attempts = {}
        self._server_action_workers = []
        self._heartbeat_worker = None
        self._workers = []
        self._bulk_workers = []
        self._history_workers = []
        self._autosave_workers = []
        self._autosave_workers_by_path = {}
        self._autosave_followups = {}
        self._autosave_ready_followups = []
        self._autosave_followup_lock = threading.Lock()
        self._retention_workers = []
        self._rename_workers = []
        self._retention_worker = None
        self._session_refresh_lock = threading.Lock()
        self._structure_mutation_gate = threading.RLock()
        self._local_structure_generation = 0
        self._v2_untracked_recovery_paths = set()
        self._v2_last_pull_apply_blocked = False
        self._v2_identity_apply_failed = False
        self._v2_identity_uuid_conflicts = []
        self._auth_refresh_generation = 0
        self._auth_retry_blocked = False
        self._shutting_down = False
        self._draining = False
        self._shutdown_deadline = None
        self._shutdown_timer_stoppers = []
        self._diagnostics = SyncDiagnosticLog()
        self._last_diagnostic_state_signature = None
        self._cloud_config = None
        self.cloud_config_state = "disabled"
        self.cloud_config_message = ""
        self._last_cloud_error_kind = ""
        
        self.supabase = None
        self.init_supabase()

    def _v2_pull_identity(self):
        """What a pull is for. A reply only counts while all of it still holds.

        The generation alone would catch a release, and the ids alone would
        catch a swap that happens to reuse the generation. Together they also
        catch the case that fooled the first attempt: released, then another
        project attached before the old reply arrived.
        """
        context = self._v2_context or {}
        return (
            self._v2_context_generation,
            str(context.get("project_id") or ""),
            str(context.get("local_key") or ""),
        )

    def release_v2(self):
        """Serve no project at all until something valid is attached.

        One manager serves every project this process opens, so leaving the
        previous one attached is how a refused project ends up publishing into
        the project the writer opened before it. Dropping the context is what
        makes ``is_v2_enabled`` false, and an in-flight pull that lands
        afterwards finds nothing to apply.
        """
        self._cancel_scheduled_v2_retry(reset_backoff=True)
        self._v2_context = None
        # Every request already in the air belongs to the generation that is
        # ending here. None of them may land on whatever is attached next.
        self._v2_context_generation += 1
        self._v2_wpm = None
        self._v2_untracked_recovery_paths = set()
        self._v2_last_pull_apply_blocked = False
        self._v2_identity_apply_failed = False
        self._v2_identity_uuid_conflicts = []

    def configure_v2(
        self,
        wpm,
        project_name,
        device_id,
        store=None,
        project_id=None,
        recover_local_changes=True,
    ):
        """Attach the current Windows writing project to the durable v2 store.

        A project that could not be opened has no writing root, and this
        manager is shared by the whole process. Attaching one anyway used to
        register the working directory as a project; refusing without letting
        go would be worse still, because the binder would show the refused
        project while every publish still addressed the last one that opened.
        So the refusal releases first and changes nothing else.
        """
        from runtime_profile import forced_project_id
        writing_root_path = getattr(wpm, "writing_root_path", None)
        if not str(writing_root_path or "").strip():
            self.release_v2()
            raise ValueError(
                "열 수 없는 프로젝트는 동기화에 연결하지 않습니다."
            )
        self._cancel_scheduled_v2_retry(reset_backoff=True)
        self._v2_store = store or self._v2_store or SyncV2Store()
        self._v2_wpm = wpm
        self._v2_device_id = str(device_id)
        local_key = self._v2_store.local_key_for(writing_root_path)
        project_was_configured = self._v2_store.get_project(local_key) is not None
        selected_project_id = project_id or forced_project_id() or None
        self._v2_context = self._v2_store.configure_project(
            wpm.writing_root_path, project_name, selected_project_id
        )
        self._v2_context["writer_device_id"] = self._v2_device_id
        # A new project is a new generation, even when the one before it was
        # released first: a reply from the old one must not pass for this one.
        self._v2_context_generation += 1
        deterministic_ids = bool(forced_project_id() and not project_id)
        root = getattr(wpm, "writing_root_path", None)
        self._v2_untracked_recovery_paths = set()
        if recover_local_changes and root and os.path.isdir(root):
            for current_root, dirs, files in os.walk(root):
                relative_root = os.path.relpath(current_root, root).replace("\\", "/")
                if relative_root != "." and not is_live_document_path(relative_root):
                    dirs[:] = []
                    continue
                dirs[:] = [
                    name for name in dirs
                    if is_live_document_path(
                        name if relative_root == "." else f"{relative_root}/{name}"
                    )
                ]
                for filename in files:
                    if filename.endswith(".txt"):
                        full_path = os.path.join(current_root, filename)
                        relative_path = os.path.relpath(full_path, root).replace("\\", "/")
                        try:
                            with open(full_path, "r", encoding="utf-8") as source:
                                content = source.read()
                        except OSError:
                            content = ""
                        document_id = None
                        if deterministic_ids:
                            document_id = str(uuid.uuid5(
                                uuid.UUID(self._v2_context["project_id"]), relative_path
                            ))
                        existing = self._v2_store.get_document(
                            self._v2_context["local_key"], relative_path
                        )
                        if project_was_configured and existing is None:
                            # Do not assign a new UUID until a successful remote
                            # pull proves that this path has no server identity.
                            # Tree-order can materialize empty placeholders on
                            # another device before their document rows arrive.
                            self._v2_untracked_recovery_paths.add(relative_path)
                            continue
                        if (
                            project_was_configured
                            and existing is not None
                            and int(existing.get("revision") or 0) == 0
                            and not self._v2_store.has_active_operations(
                                existing["document_id"]
                            )
                        ):
                            self._v2_untracked_recovery_paths.add(relative_path)
                            continue
                        self._v2_store.ensure_document(
                            self._v2_context["local_key"], relative_path, content, document_id
                        )
                        # A file created just before a forced shutdown can exist on disk
                        # without ever reaching the queue. On an already configured project,
                        # newly discovered files are creation recovery work, not initial import.

        tree_order = getattr(wpm, "project_settings", {}).get("tree_order")
        tree_document = self._v2_store.get_document(
            self._v2_context["local_key"], TREE_ORDER_DOCUMENT_PATH
        )
        if (
            recover_local_changes
            and project_was_configured
            and isinstance(tree_order, dict)
            and tree_order
            and tree_document is None
        ):
            self.record_tree_order(tree_order, retry=False)
        purge_state = getattr(wpm, "project_settings", {}).get(
            "trash_purged_revisions"
        )
        if (
            recover_local_changes
            and project_was_configured
            and isinstance(purge_state, dict)
            and purge_state
            and self._v2_store.get_document(
                self._v2_context["local_key"], TRASH_PURGE_DOCUMENT_PATH
            ) is None
        ):
            self.record_trash_purge([], retry=False)
        self._release_ready_tree_order_barrier()
        self._publish_sync_state()
        return dict(self._v2_context)

    @staticmethod
    def _normalized_tree_order(tree_order):
        if not isinstance(tree_order, dict):
            return {}
        normalized = {}
        for parent_path, child_names in tree_order.items():
            parent_path = canonical_tree_parent_path(parent_path)
            if (
                parent_path == "메인/휴지통"
                or parent_path.startswith("메인/휴지통/")
                or not isinstance(child_names, list)
            ):
                continue
            clean_names = [str(name) for name in child_names if str(name)]
            if parent_path == "<root>":
                clean_names = canonical_root_children(clean_names)
            fixed_order = canonical_manuscript_children(
                parent_path, clean_names
            )
            if fixed_order is not None:
                clean_names = fixed_order
            existing_names = normalized.get(parent_path)
            if existing_names is None:
                normalized[parent_path] = clean_names
                continue
            existing_keys = {
                unicodedata.normalize("NFC", name).casefold()
                for name in existing_names
            }
            for name in clean_names:
                name_key = unicodedata.normalize("NFC", name).casefold()
                if name_key in existing_keys:
                    continue
                existing_keys.add(name_key)
                existing_names.append(name)
        return normalized

    @classmethod
    def _tree_order_content(cls, tree_order):
        normalized_order = cls._normalized_tree_order(tree_order)
        # Parent keys are explicit folder evidence. Keep that evidence beside
        # the legacy string lists so a receiver never has to guess whether an
        # unresolved leaf is an empty folder or a document commit in flight.
        folder_paths = sorted(
            parent_path
            for parent_path in normalized_order
            if parent_path != "<root>"
            and parent_path != "메인/휴지통"
            and not parent_path.startswith("메인/휴지통/")
        )
        folder_path_keys = {
            cls._tree_path_comparison_key(path) for path in folder_paths
        }
        fixed_root_keys = {
            cls._tree_path_comparison_key(f"메인/{name}")
            for name in set(TREE_ROOT_STORAGE_NAMES.values())
        }
        has_unclassified_leaf = any(
            cls._tree_path_comparison_key(
                cls._tree_order_child_path(parent_path, child_name)
            ) not in folder_path_keys | fixed_root_keys
            and not str(child_name).casefold().endswith(".txt")
            for parent_path, child_names in normalized_order.items()
            for child_name in child_names
        )
        payload = {
            "version": 1,
            "tree_order": normalized_order,
        }
        if not has_unclassified_leaf:
            payload["folder_paths"] = folder_paths
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _normalized_trash_purges(value):
        if not isinstance(value, dict):
            return {}
        normalized = {}
        for document_id, revision in value.items():
            try:
                normalized[str(uuid.UUID(str(document_id)))] = max(0, int(revision))
            except (TypeError, ValueError):
                continue
        return normalized

    @classmethod
    def _trash_purge_content(cls, purges, empty_generation=None):
        return json.dumps(
            {
                "version": 1,
                "purged_revisions": cls._normalized_trash_purges(purges),
                "empty_generation": str(empty_generation or ""),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def record_trash_purge(self, trash_items, empty_all=False, retry=True):
        """Synchronize permanent trash removal without deleting server history."""
        if not self.is_v2_enabled:
            return None
        purges = self._normalized_trash_purges(
            self._v2_wpm.project_settings.get("trash_purged_revisions", {})
        )
        affected_ids = set()
        for item in trash_items or []:
            document = None
            document_id = item.get("document_id") if isinstance(item, dict) else None
            if document_id:
                document = self._v2_store.get_document_by_id(document_id)
            if document is None and isinstance(item, dict) and item.get("trash_path"):
                document = self._v2_store.get_document(
                    self._v2_context["local_key"], item["trash_path"]
                )
            if not document or not document.get("is_deleted"):
                continue
            document_id = document["document_id"]
            revision = int(document.get("revision") or 0)
            if revision <= 0:
                continue
            purges[document_id] = max(purges.get(document_id, 0), revision)
            affected_ids.add(document_id)

        generation = self._v2_wpm.project_settings.get("trash_empty_generation", "")
        if empty_all:
            generation = str(uuid.uuid4())
            self._v2_wpm.project_settings["trash_empty_generation"] = generation
        self._v2_wpm.project_settings["trash_purged_revisions"] = purges
        self._v2_wpm.save_settings()

        for document_id in affected_ids:
            self._v2_store.relocate_deleted_document(
                document_id, f"__antigravity__/purged/{document_id}"
            )

        document_id = str(uuid.uuid5(
            uuid.UUID(self._v2_context["project_id"]), TRASH_PURGE_DOCUMENT_PATH
        ))
        if self._v2_store.get_document(
            self._v2_context["local_key"], TRASH_PURGE_DOCUMENT_PATH
        ) is None:
            self._v2_store.ensure_document(
                self._v2_context["local_key"],
                TRASH_PURGE_DOCUMENT_PATH,
                "",
                document_id,
            )
        operation = self._v2_store.enqueue(
            self._v2_context,
            TRASH_PURGE_DOCUMENT_PATH,
            self._trash_purge_content(purges, generation),
            relative_path=TRASH_PURGE_DOCUMENT_PATH,
        )
        self._publish_sync_state()
        if retry:
            self.retry_pending_syncs()
        return operation

    @property
    def local_structure_generation(self):
        with self._structure_mutation_gate:
            return self._local_structure_generation

    @contextmanager
    def local_structure_mutation(self):
        """Serialize local filesystem/tree/queue changes with remote tree apply."""
        with self._structure_mutation_gate:
            self._local_structure_generation += 1
            try:
                yield self._local_structure_generation
            finally:
                self._local_structure_generation += 1

    def record_structure_recovery(
        self, old_rel_path, new_rel_path, error_code="RECOVERY_FAILED"
    ):
        if not self.is_v2_enabled:
            return None
        recovery_id = self._v2_store.record_structure_recovery(
            self._v2_context["local_key"],
            old_rel_path,
            new_rel_path,
            error_code,
        )
        self._publish_sync_state()
        return recovery_id

    def _uses_contract_structure(self):
        if not self.is_v2_enabled:
            return False
        project = self._v2_store.get_project(self._v2_context["local_key"])
        if not project:
            return False
        try:
            require_server_compatibility(
                project_sync_mode=project["project_sync_mode"],
                migration_epoch=int(project["migration_epoch"] or 0),
                server_protocol_version=int(project["server_protocol_version"] or 0),
                server_contract_sha256=project["active_contract_sha256"] or "",
                server_capabilities=json.loads(
                    project["server_capabilities_json"] or "[]"
                ),
            )
        except (SyncContractError, TypeError, ValueError):
            return False
        return True

    @staticmethod
    def _path_before_local_change(path, aliases):
        path = str(path or "").replace("\\", "/").strip("/")
        for new_prefix, old_prefix in aliases or []:
            if path == new_prefix:
                return old_prefix
            if path.startswith(new_prefix + "/"):
                return old_prefix + path[len(new_prefix):]
        return path

    def _folder_id_for_path(
        self, path, *, required=True, aliases=None, pending_folders=None
    ):
        path = self._safe_relative_path(path) if path else ""
        if not path or path == "<root>":
            return None
        pending = (pending_folders or {}).get(path)
        if pending:
            return pending["folder_id"]
        lookup_path = self._path_before_local_change(path, aliases)
        folder = self._v2_store.get_folder_by_path(
            self._v2_context["local_key"], lookup_path
        )
        if folder is None and required:
            raise SyncContractError("CONTRACT_STRUCTURE_IDS_REQUIRED")
        return folder["folder_id"] if folder else None

    def _contract_tree_order_intents(
        self, tree_order, *, aliases=None, pending_folders=None
    ):
        intents = []
        local_key = self._v2_context["local_key"]
        project_uuid = uuid.UUID(self._v2_context["project_id"])
        for raw_parent, raw_children in self._normalized_tree_order(tree_order).items():
            parent_path = canonical_tree_parent_path(raw_parent)
            parent_folder_id = self._folder_id_for_path(
                "" if parent_path == "<root>" else parent_path,
                aliases=aliases,
                pending_folders=pending_folders,
            )
            children = []
            for child_name in raw_children:
                child_path = (
                    f"메인/{child_name}"
                    if parent_path == "<root>"
                    else f"{parent_path}/{child_name}"
                )
                pending = (pending_folders or {}).get(child_path)
                if pending:
                    children.append(pending["folder_id"])
                    continue
                lookup_path = self._path_before_local_change(
                    child_path, aliases
                )
                folder = self._v2_store.get_folder_by_path(
                    local_key, lookup_path
                )
                if folder:
                    children.append(folder["folder_id"])
                    continue
                document = self._v2_store.get_document(local_key, lookup_path)
                if document:
                    children.append(document["document_id"])
                    continue
                raise SyncContractError("TREE_REFERENCE_NOT_FOUND")
            existing = self._v2_store.get_tree_order(
                local_key,
                self._path_before_local_change(parent_path, aliases),
            )
            tree_order_id = (
                existing["tree_order_id"] if existing else
                str(uuid.uuid5(
                    project_uuid, f"tree-order:{parent_folder_id or 'root'}"
                ))
            )
            intent = {
                "entity_kind": "tree_order",
                "entity_id": tree_order_id,
                "intent_kind": "reorder",
                "base_revision": int(existing["revision"] if existing else 0),
                "payload": {"children": children},
            }
            if parent_folder_id:
                intent["payload"]["parent_folder_id"] = parent_folder_id
            previous = self._v2_store.latest_active_structure_operation(
                tree_order_id
            )
            if previous:
                intent["supersedes_operation_id"] = previous["operation_id"]
            intents.append(intent)
        return intents

    def queue_contract_structure_intents(self, intents, retry=True):
        if not intents:
            return None
        request = self.queue_atomic_structure_batch(intents, retry=False)
        if retry:
            self.retry_pending_syncs()
        return request

    def queue_contract_path_change_with_order(
        self, path_operations, tree_order, retry=True
    ):
        intents = []
        path_changes = []
        document_changes = []
        aliases = []
        pending_folders = {}
        for operation in path_operations or []:
            intents.extend(operation.get("contract_structure_intents") or [])
            document_changes.extend(
                operation.get("contract_document_changes") or []
            )
            change = operation.get("contract_path_change")
            if change:
                path_changes.append(change)
                old_path = change.get("old_path")
                new_path = change.get("new_path")
                if old_path and new_path and old_path != new_path:
                    aliases.append((new_path, old_path))
                pending = change.get("pending_folder")
                if pending:
                    pending_folders[pending["local_path"]] = pending
                for pending in change.get("pending_folders") or []:
                    pending_folders[pending["local_path"]] = pending
        intents.extend(self._contract_tree_order_intents(
            tree_order,
            aliases=aliases,
            pending_folders=pending_folders,
        ))
        if not intents:
            if document_changes:
                raise SyncContractError("CONTRACT_STRUCTURE_IDS_REQUIRED")
            return None
        request = self._v2_store.create_structure_batch_with_path_changes(
            self._v2_context,
            self._v2_device_id,
            intents,
            path_changes,
            document_changes=document_changes,
        )
        self._publish_sync_state()
        if retry:
            self.retry_pending_syncs()
        return request

    def record_tree_order(self, tree_order, retry=True):
        """Persist project binder order as one hidden revisioned v2 document."""
        if not self.is_v2_enabled:
            return None
        if self._uses_contract_structure():
            return self.queue_contract_structure_intents(
                self._contract_tree_order_intents(tree_order), retry=retry
            )
        with self._structure_mutation_gate:
            content = self._tree_order_content(tree_order)
            document_id = str(uuid.uuid5(
                uuid.UUID(self._v2_context["project_id"]), TREE_ORDER_DOCUMENT_PATH
            ))
            document = self._v2_store.get_document(
                self._v2_context["local_key"], TREE_ORDER_DOCUMENT_PATH
            )
            if document is None:
                document = self._v2_store.ensure_document(
                    self._v2_context["local_key"],
                    TREE_ORDER_DOCUMENT_PATH,
                    "",
                    document_id,
                )
            pending_folder_renames = (
                self._v2_store.pending_folder_rename_intents(
                    self._v2_context["local_key"]
                )
            )
            if (
                document.get("base_content") == content
                and not self._v2_store.has_active_operations(document["document_id"])
                and not pending_folder_renames
            ):
                return None
            operation = self._v2_store.enqueue(
                self._v2_context,
                TREE_ORDER_DOCUMENT_PATH,
                content,
                relative_path=TREE_ORDER_DOCUMENT_PATH,
            )
        self._publish_sync_state()
        if retry:
            self.retry_pending_syncs()
        return operation

    def record_folder_rename_intent(self, old_path, new_path):
        """Persist proof that a folder rename came from a local user action."""
        if not self.is_v2_enabled:
            return None
        if self._uses_contract_structure():
            return None
        old_path = self._safe_relative_path(old_path)
        new_path = self._safe_relative_path(new_path)
        root = os.path.abspath(self._v2_wpm.writing_root_path)
        new_full = os.path.abspath(os.path.join(root, new_path))
        if (
            os.path.commonpath([root, new_full]) != root
            or not os.path.isdir(new_full)
            or self._is_reparse_path(new_full)
        ):
            raise ValueError("INVALID_LOCAL_FOLDER_RENAME_INTENT")
        with self._structure_mutation_gate:
            return self._v2_store.record_folder_rename_intent(
                self._v2_context["local_key"], old_path, new_path
            )

    def record_created_document(self, relative_path, retry=True):
        """Durably queue one newly created local document and return its operation."""
        if not self.is_v2_enabled or not is_live_document_path(relative_path):
            return None
        with self._structure_mutation_gate:
            content = self._v2_wpm.read_text_file(relative_path)
            if content is None:
                raise OSError("LOCAL_DOCUMENT_READ_FAILED")
            document = self._v2_store.get_document(
                self._v2_context["local_key"], relative_path
            )
            if document is None:
                self._v2_store.ensure_document(
                    self._v2_context["local_key"], relative_path, content
                )
            operation = self._v2_store.enqueue(
                self._v2_context,
                relative_path,
                content,
                relative_path=relative_path,
            )
        self._publish_sync_state()
        if retry:
            self.retry_pending_syncs()
        return operation

    def activate_contract_project(
        self,
        *,
        project_sync_mode,
        migration_epoch,
        server_protocol_version,
        server_contract_sha256,
        server_capabilities,
    ):
        """Apply an explicitly verified server project state; never auto-promote."""
        if not self.is_v2_enabled:
            raise RuntimeError("v2 project is not configured")
        project = self._v2_store.activate_contract_project(
            self._v2_context["local_key"],
            project_sync_mode=project_sync_mode,
            migration_epoch=migration_epoch,
            server_protocol_version=server_protocol_version,
            server_contract_sha256=server_contract_sha256,
            server_capabilities=server_capabilities,
        )
        self._v2_context.update({
            "project_sync_mode": project["project_sync_mode"],
            "migration_epoch": int(project["migration_epoch"] or 0),
        })
        return project

    def queue_atomic_structure_batch(self, ordered_intents, retry=True):
        if not self.is_v2_enabled:
            raise RuntimeError("v2 project is not configured")
        request = self._v2_store.create_structure_batch(
            self._v2_context,
            self._v2_device_id,
            ordered_intents,
        )
        self._publish_sync_state()
        if retry:
            self.retry_pending_syncs()
        return request
    def defer_tree_order_until_operations(self, tree_order, operations):
        """Persist a tree snapshot that cannot enqueue before document success."""
        if not self.is_v2_enabled:
            return None
        operation_ids = [
            operation.get("operation_id")
            for operation in (operations or [])
            if isinstance(operation, dict) and operation.get("operation_id")
        ]
        if not operation_ids:
            # Nothing is in flight for the order to wait behind — an empty
            # folder came back, or every document was already settled. The
            # order still has to be recorded, so commit it now rather than
            # raising and taking the whole restore down with it.
            return self.record_tree_order(tree_order, retry=False)
        with self._structure_mutation_gate:
            barrier = self._v2_store.defer_tree_order(
                self._v2_context,
                self._tree_order_content(tree_order),
                operation_ids,
            )
        self._publish_sync_state()
        return barrier

    def _release_ready_tree_order_barrier(self):
        if not self.is_v2_enabled:
            return None
        with self._structure_mutation_gate:
            barrier = self._v2_store.ready_tree_order_barrier(
                self._v2_context["local_key"]
            )
            if barrier is None:
                return None
            tree_order = self._tree_order_from_content(
                barrier["tree_order_content"]
            )
            if tree_order is None:
                raise RuntimeError("INVALID_TREE_ORDER_BARRIER")
            operation = self.record_tree_order(tree_order, retry=False)
            self._v2_store.complete_tree_order_barrier(barrier["barrier_id"])
            return operation

    @property
    def is_v2_enabled(self):
        return bool(self._v2_store and self._v2_context and self._v2_device_id)

    def can_save_path(self, relative_path):
        """Reject late editor saves for a path that has already been tombstoned."""
        if not self.is_v2_enabled:
            return True
        if not is_live_document_path(relative_path):
            return False
        existing = self._v2_store.get_document(
            self._v2_context["local_key"], relative_path
        )
        if existing is not None:
            return True
        return not self._v2_store.has_tombstone_for_server_path(
            self._v2_context["local_key"], relative_path
        )

    def would_erase_nonempty_document(self, relative_path, content):
        """Return whether an empty save would erase a non-empty synced base."""
        if content != "" or not self.is_v2_enabled or not relative_path:
            return False
        document = self._v2_store.get_document(
            self._v2_context["local_key"], relative_path
        )
        return bool(
            document
            and not document.get("is_deleted")
            and (
                document.get("base_content")
                or self._v2_store.has_nonempty_active_content(
                    document["document_id"]
                )
            )
        )

    def report_empty_content_guard(self, relative_path):
        detail = (
            f"{relative_path}: 기존 내용이 있는 문서가 빈 상태가 되어 "
            "자동저장을 중단했습니다. Ctrl+S로 전체 삭제 여부를 확인해주세요."
        )
        self._set_sync_state("empty_guard", detail)
        return detail

    def report_server_queue_failure(self, relative_path, error):
        """Report a cloud-queue failure without treating the local save as lost."""
        detail = (
            f"{relative_path}: 로컬 원고는 저장됐지만 서버 전송 대기열에 "
            f"등록하지 못했습니다. ({error})"
        )
        self._last_sync_error = detail
        self._last_failure_offline = self._is_connectivity_error(str(error))
        self._set_sync_state(
            "offline" if self._last_failure_offline else "failed", detail
        )
        return detail
        
    def _start_worker(self, worker):
        self.active_workers.add(worker)
        worker.finished.connect(lambda *args, w=worker: self.active_workers.discard(w))
        worker.start()
        return worker

    def _start_server_action(self, action, callback=None):
        if self._shutting_down:
            return None
        worker = ServerActionWorker(action)
        self._server_action_workers.append(worker)
        if callback:
            worker.resultReady.connect(callback)

        def cleanup_worker():
            if worker in self._server_action_workers:
                self._server_action_workers.remove(worker)
            if self._heartbeat_worker is worker:
                self._heartbeat_worker = None
            worker.deleteLater()

        worker.finished.connect(cleanup_worker)
        return self._start_worker(worker)

    def heartbeat_locks_async(
        self, project_name, relative_paths, session_id, client=None
    ):
        """Renew all active leases in one background worker per heartbeat tick."""
        if self._heartbeat_worker is not None:
            try:
                if self._heartbeat_worker.isRunning():
                    return self._heartbeat_worker
            except RuntimeError:
                self._heartbeat_worker = None
        paths = tuple(dict.fromkeys(path for path in relative_paths if path))
        if not paths:
            return None

        def heartbeat_all():
            for path in paths:
                self.heartbeat_lock(project_name, path, session_id, client=client)
            return len(paths)

        worker = self._start_server_action(heartbeat_all)
        self._heartbeat_worker = worker
        return worker

    def release_lock_async(
        self, project_name, relative_path, session_id, client=None
    ):
        if not relative_path:
            return None
        return self._start_server_action(
            lambda: self.release_lock(
                project_name, relative_path, session_id, client=client
            )
        )

    @property
    def pending_retry_count(self):
        persistent = 0
        if self.is_v2_enabled:
            counts = self._v2_store.counts(self._v2_context["local_key"])
            persistent = counts.get("documents", counts["total"])
        return len(self._retry_queue) + persistent

    def _absent_project_state(self):
        """Decide what a missing server row means for this project.

        Absent is not purged. Before the first commit lands there is no row
        to find, and ensure_project — the thing that creates it — only runs
        inside dispatch. Reading that absence as a purge stops dispatch, so
        the row never appears and the project stays stuck for good. Only a
        project this device has already committed can have gone missing.
        """
        store = getattr(self, "_v2_store", None)
        context = getattr(self, "_v2_context", None)
        if not store or not context:
            return "active"
        checker = getattr(store, "has_server_acknowledged_commit", None)
        if not callable(checker):
            return "active"
        return "purged" if checker(context["local_key"]) else "active"

    def _current_project_server_state(self):
        if not self.is_v2_enabled:
            return "active"
        return str(
            self._v2_context.get("server_state") or "active"
        )

    def mark_project_server_state(self, project_id, state):
        if state not in {"active", "trashed", "purged"}:
            return False
        if self._v2_store is not None:
            setter = getattr(
                self._v2_store, "set_project_server_state", None
            )
            if callable(setter):
                setter(project_id, state)
        if (
            self._v2_context
            and self._v2_context.get("project_id") == str(project_id)
        ):
            self._v2_context["server_state"] = state
            if state in {"trashed", "purged"}:
                # Project trash removes leases on the server. Do not issue
                # release RPCs after access has already been revoked.
                self._v2_leases.clear()
                self._cancel_scheduled_v2_retry(reset_backoff=True)
            self._publish_sync_state()
        return True

    def _set_sync_state(self, state, detail="", pending_count=None):
        self.current_sync_state = state
        if pending_count is None:
            pending_count = self.pending_retry_count
        pending_count = max(0, int(pending_count or 0))
        signature = (str(state), str(detail), int(pending_count))
        if signature != self._last_diagnostic_state_signature:
            self._last_diagnostic_state_signature = signature
            if state in {"offline", "failed", "auth_required", "conflict", "lease"}:
                self._diagnostics.record(
                    "sync_failure",
                    detail=detail or state,
                    state=state,
                    pending_count=pending_count,
                )
            else:
                self._diagnostics.record(
                    "sync_state", state=state, pending_count=pending_count
                )
        self.syncStateChanged.emit(state, detail, pending_count)

    def _record_sync_success(self):
        # 전송이 한 번이라도 성공했다면 직전 실패 사유는 더 이상 현재 상태가
        # 아니다. 남겨두면 큐가 다시 돌고 있는데도 옛 오류가 계속 표시된다.
        self._last_sync_error = ""
        self._last_failure_offline = False
        self._auth_retry_blocked = False
        self._diagnostics.record(
            "sync_success",
            state="saved",
            pending_count=self.pending_retry_count,
        )

    def diagnostic_snapshot(self):
        """Return a local-only, non-sensitive snapshot for the settings panel."""
        summary = self._diagnostics.summary()
        if self.authenticated_email():
            login_state = "로그인됨"
        elif self._auth_retry_blocked:
            login_state = "로그인 필요"
        else:
            login_state = "로그아웃"
        return {
            **summary,
            "login_state": login_state,
            "pending_count": self.pending_retry_count,
            "sync_state": self.current_sync_state,
        }

    def diagnostic_report(self):
        return format_diagnostic_report(self.diagnostic_snapshot())

    def _publish_sync_state(self):
        v2_counts = None
        if self.is_v2_enabled:
            v2_counts = self._v2_store.counts(
                self._v2_context["local_key"]
            )
        persistent_count = (
            v2_counts.get("documents", v2_counts["total"])
            if v2_counts is not None else 0
        )
        pending_count = len(self._retry_queue) + persistent_count

        def publish(state, detail):
            self._set_sync_state(
                state, detail, pending_count=pending_count
            )

        server_state = self._current_project_server_state()
        if server_state == "trashed":
            publish(
                "project_trashed",
                "서버 휴지통에 있는 작품입니다. 동기화를 중지하고 로컬 원고만 보존합니다.",
            )
        elif server_state == "purged":
            publish(
                "project_purged",
                "서버에서 영구 삭제된 작품입니다. 기존 UUID의 동기화를 영구 중지합니다.",
            )
        elif self._auth_retry_blocked:
            publish(
                "auth_required",
                self._last_sync_error or (
                    "클라우드 로그인이 필요합니다. 로컬 저장은 계속되며, "
                    "다시 로그인하면 대기 작업이 이어서 전송됩니다."
                ),
            )
        elif self._active_server_syncs > 0:
            publish("syncing", "서버에 변경 내용을 올리는 중입니다.")
        elif v2_counts is not None and v2_counts["conflict"]:
            count = v2_counts["conflict"]
            publish("conflict", f"자동 병합할 수 없는 문서 충돌이 {count}건 있습니다.")
        elif v2_counts is not None and v2_counts["total"]:
            detail = self._v2_store.latest_error(self._v2_context["local_key"])
            if "LEASE_CONFLICT" in detail:
                publish(
                    "lease",
                    "다른 기기에서 이 문서를 편집 중입니다. 그 기기에서 문서를 닫은 뒤 다시 시도하세요.",
                )
                return
            if "AUTH_REQUIRED" in detail or "AUTH_EXPIRED" in detail:
                # 로그인 문제를 오프라인으로 부르면 "인터넷을 확인하세요" 라는
                # 엉뚱한 안내가 나간다. 필요한 행동은 재로그인이다.
                publish(
                    "auth_required",
                    "클라우드 로그인 세션이 만료됐습니다. 다시 로그인하면 "
                    "대기 작업이 이어서 전송됩니다.",
                )
                return
            # 오류 근거가 없으면 연결 문제라고 단정하지 않는다. 전송을 기다리는
            # 상태이므로 재시도 버튼이 있는 failed 로 알린다.
            offline = bool(detail) and self._is_connectivity_error(detail)
            publish(
                "offline" if offline else "failed",
                detail or "서버 전송을 기다리는 로컬 변경 사항이 있습니다.",
            )
        elif self._retry_queue:
            pending = list(self._retry_queue.values())
            state = "offline" if any(item.get("_retry_offline", False) for item in pending) else "failed"
            detail = next((item.get("_retry_error") for item in pending if item.get("_retry_error")), self._last_sync_error)
            publish(state, detail)
        elif self._active_backups > 0:
            publish("backup", "로컬 자동백업을 만드는 중입니다.")
        else:
            publish("saved", "현재 로컬 변경 사항이 저장되어 있습니다.")

    @staticmethod
    def _is_connectivity_error(error_msg):
        message = (error_msg or "").lower()
        markers = (
            "서버 연결 없음", "offline", "network", "connection", "disconnected",
            "테스트 오프라인",
            "timeout", "timed out", "dns", "unreachable", "refused", "winerror",
            "temporarily unavailable", "name or service",
        )
        return any(marker in message for marker in markers)

    @staticmethod
    def _v2_follow_up_delay_ms(
        kind, error_message="", lease_attempt=1, network_attempt=1
    ):
        if kind in {"committed", "auto_merged", "conflict"}:
            return 0
        if kind == "retry" and "LEASE_CONFLICT" in (error_message or ""):
            attempt_index = max(0, int(lease_attempt or 1) - 1)
            return LEASE_CONFLICT_RETRY_DELAYS_MS[
                min(attempt_index, len(LEASE_CONFLICT_RETRY_DELAYS_MS) - 1)
            ]
        if kind == "retry" and SyncManager._is_connectivity_error(error_message):
            attempt_index = max(0, int(network_attempt or 1) - 1)
            return NETWORK_RETRY_DELAYS_MS[
                min(attempt_index, len(NETWORK_RETRY_DELAYS_MS) - 1)
            ]
        return None

    def _cancel_scheduled_v2_retry(self, reset_backoff=False):
        self._v2_retry_timer.stop()
        self._v2_retry_context = None
        if reset_backoff:
            self._v2_lease_retry_operation_id = None
            self._v2_lease_retry_attempt = 0
            self._v2_network_retry_attempts.clear()

    def _next_lease_retry_attempt(self, operation_id):
        operation_id = str(operation_id or "")
        if operation_id != self._v2_lease_retry_operation_id:
            self._v2_lease_retry_operation_id = operation_id
            self._v2_lease_retry_attempt = 0
        self._v2_lease_retry_attempt += 1
        return self._v2_lease_retry_attempt

    def _reset_lease_retry_backoff(self, operation_id=None):
        if (
            operation_id is not None
            and str(operation_id) != self._v2_lease_retry_operation_id
        ):
            return
        self._v2_lease_retry_operation_id = None
        self._v2_lease_retry_attempt = 0

    def _schedule_v2_retry(self, delay_ms):
        context = self._v2_context or {}
        local_key = context.get("local_key")
        project_id = context.get("project_id")
        if (
            not local_key
            or not project_id
            or self._current_project_server_state() != "active"
        ):
            return False

        retry_context = (str(local_key), str(project_id))
        delay_ms = max(0, int(delay_ms))
        if (
            self._v2_retry_timer.isActive()
            and self._v2_retry_context == retry_context
        ):
            remaining_ms = self._v2_retry_timer.remainingTime()
            if 0 <= remaining_ms <= delay_ms:
                return False

        self._v2_retry_timer.stop()
        self._v2_retry_context = retry_context
        self._v2_retry_timer.start(delay_ms)
        return True

    def _run_scheduled_v2_retry(self):
        expected_context = self._v2_retry_context
        self._v2_retry_context = None
        current_context = self._v2_context or {}
        current_identity = (
            str(current_context.get("local_key") or ""),
            str(current_context.get("project_id") or ""),
        )
        if not expected_context or current_identity != expected_context:
            return False
        if (
            self._current_project_server_state() != "active"
            or is_forced_offline()
            or not self.supabase
        ):
            return False
        return self.retry_pending_syncs()

    def _queue_retry(self, key, payload, error_msg, offline=False):
        # 같은 파일은 가장 최신 내용 하나만 보관해 오래된 원고가 재전송되지 않게 한다.
        payload["_retry_error"] = error_msg or "서버 동기화에 실패했습니다."
        payload["_retry_offline"] = bool(offline)
        self._retry_queue[key] = payload
        self._last_sync_error = payload["_retry_error"]
        self._last_failure_offline = bool(offline)

    def retry_pending_syncs(self, manual=False):
        """다른 서버 요청이 성공한 뒤 대기 중인 항목을 한 건씩 다시 전송한다."""
        if self._auth_retry_blocked or self._shutting_down:
            self._publish_sync_state()
            return False
        if self._v2_retry_timer.isActive():
            if not manual:
                return False
            self._cancel_scheduled_v2_retry(reset_backoff=False)
        if self.is_v2_enabled:
            if self._current_project_server_state() != "active":
                self._publish_sync_state()
                return False
            if (
                self._v2_worker is not None
                or self._v2_structure_worker is not None
                or self._active_server_syncs > 0
            ):
                return False
            next_structure_batch = getattr(
                self._v2_store, "next_ready_structure_batch", None
            )
            structure_request = (
                next_structure_batch(self._v2_context["local_key"])
                if callable(next_structure_batch)
                else None
            )
            if structure_request:
                self._launch_contract_structure_batch(
                    structure_request["batch"]["batch_id"]
                )
                return True
            operation = self._v2_store.next_ready_operation(self._v2_context["local_key"])
            if operation is None:
                # 앞선 작업이 사라져 고아가 된 연쇄 편집이 있으면 되살린다.
                # 그대로 두면 대기 건수만 표시된 채 큐가 영원히 멈춘다.
                recover = getattr(
                    self._v2_store, "recover_stranded_operations", None
                )
                if callable(recover) and recover(self._v2_context["local_key"]):
                    operation = self._v2_store.next_ready_operation(
                        self._v2_context["local_key"]
                    )
            if operation:
                self._launch_v2_operation(operation)
                return True
        if self._retry_active_key is not None or self._active_server_syncs > 0 or not self._retry_queue:
            return False

        key, payload = next(iter(self._retry_queue.items()))
        self._retry_active_key = key
        kind = payload["kind"]
        if kind == "content":
            self._launch_content_upload(payload, key, is_retry=True)
        elif kind == "bulk":
            self._launch_bulk_upload(payload, key, is_retry=True)
        elif kind == "history":
            self._launch_history_upload(payload, key, is_retry=True)
        else:
            self._retry_queue.pop(key, None)
            self._retry_active_key = None
            self._publish_sync_state()
            return False
        return True

    def register_shutdown_timer_stopper(self, stopper):
        """Register a callable that stops periodic work when shutdown begins."""
        if callable(stopper) and stopper not in self._shutdown_timer_stoppers:
            self._shutdown_timer_stoppers.append(stopper)
        return stopper

    def begin_shutdown(self, budget_ms=None):
        """Freeze periodic work and open one bounded shutdown window.

        Idempotent: the first call fixes the deadline so every later shutdown
        step shares the same budget instead of adding its own timeout.
        """
        if self._shutdown_deadline is None:
            budget = SHUTDOWN_BUDGET_MS if budget_ms is None else budget_ms
            self._shutdown_deadline = time.monotonic() + max(0, int(budget)) / 1000.0
        self._draining = True
        try:
            self._v2_retry_timer.stop()
        except RuntimeError:
            pass
        self._v2_retry_context = None
        for stopper in list(self._shutdown_timer_stoppers):
            try:
                stopper()
            except (RuntimeError, TypeError):
                pass
        return self._shutdown_deadline

    def reset_shutdown_state(self):
        """Reopen normal operation on the reused singleton (long-lived tests)."""
        self._shutting_down = False
        self._draining = False
        self._shutdown_deadline = None

    def shutdown_remaining_ms(self):
        """Milliseconds left in the shutdown budget, or None if not shutting down."""
        if self._shutdown_deadline is None:
            return None
        return max(0, int(round((self._shutdown_deadline - time.monotonic()) * 1000)))

    def _shutdown_budget_exhausted(self):
        remaining = self.shutdown_remaining_ms()
        return remaining is not None and remaining <= 0

    def remote_calls_are_pointless(self):
        """True when a further server request can only burn the shutdown budget."""
        if not getattr(self, "cloud_network_enabled", True):
            return True
        # 이름 해석이 이미 실패했다면 재시도해도 timeout 만 소모한다.
        return getattr(self, "_last_cloud_error_kind", "") == "dns"

    def flush_pending_syncs(self, timeout_ms=None):
        """Give durable Sync V2 operations a bounded chance to finish before exit."""
        # 주기 타이머를 먼저 멈춰야 아래 중첩 이벤트 루프가 도는 동안 새 워커가
        # 생기지 않는다.
        begin_shutdown = getattr(self, "begin_shutdown", None)
        if callable(begin_shutdown):
            begin_shutdown()
        if timeout_ms is None:
            remaining = getattr(self, "shutdown_remaining_ms", None)
            timeout_ms = remaining() if callable(remaining) else None
            if timeout_ms is None:
                timeout_ms = SHUTDOWN_BUDGET_MS
            timeout_ms = min(timeout_ms, SHUTDOWN_FLUSH_BUDGET_MS)
        if not getattr(self, "cloud_network_enabled", True):
            return True
        pointless = getattr(self, "remote_calls_are_pointless", None)
        if callable(pointless) and pointless():
            return False
        if not self.is_v2_enabled:
            return True
        server_state = str(
            (getattr(self, "_v2_context", None) or {}).get(
                "server_state", "active"
            )
        )
        if server_state != "active":
            return True
        local_key = self._v2_context["local_key"]

        def current_counts():
            return self._v2_store.counts(local_key)

        counts = current_counts()
        if counts["conflict"]:
            return False
        if not counts["pending"] and not counts["inflight"]:
            return True
        if is_forced_offline() or not self.supabase:
            return False

        from PyQt6.QtCore import QEventLoop, QTimer

        loop = QEventLoop()
        poll_timer = QTimer()
        poll_timer.setInterval(100)
        timeout_timer = QTimer()
        timeout_timer.setSingleShot(True)
        completed = {"value": False}

        def check_state():
            counts_now = current_counts()
            if counts_now["conflict"]:
                loop.quit()
                return
            v2_worker_running = any(
                worker.isRunning()
                for worker in list(getattr(self, "_v2_workers", []))
            )
            if (
                not counts_now["pending"]
                and not counts_now["inflight"]
                and not v2_worker_running
            ):
                completed["value"] = True
                loop.quit()
                return
            if self._v2_worker is None and self._active_server_syncs == 0:
                self.retry_pending_syncs()

        poll_timer.timeout.connect(check_state)
        timeout_timer.timeout.connect(loop.quit)
        poll_timer.start()
        timeout_timer.start(max(1, int(timeout_ms)))
        check_state()
        if not completed["value"]:
            loop.exec()
        poll_timer.stop()
        timeout_timer.stop()
        return completed["value"]

    def _complete_server_attempt(self, key, payload, success, error_msg, worker, is_retry):
        self._active_server_syncs = max(0, self._active_server_syncs - 1)
        server_success = bool(success and getattr(worker, "supabase", None) is not None)
        effective_error = error_msg or ("" if server_success else "서버 연결 없음")

        if server_success:
            self._retry_queue.pop(key, None)
            self._last_sync_error = ""
            self._last_failure_offline = False
            self._record_sync_success()
        else:
            self._queue_retry(
                key,
                payload,
                effective_error,
                offline=getattr(worker, "supabase", None) is None or self._is_connectivity_error(effective_error),
            )

        if is_retry and self._retry_active_key == key:
            self._retry_active_key = None
        self._publish_sync_state()
        return server_success, effective_error
        
    @staticmethod
    def create_supabase_client(config=None):
        config = config or load_cloud_client_config(supabase_config_dir())
        if not config.is_ready:
            return None
        custom_httpx_client = None
        try:
            from supabase import create_client, ClientOptions
            import httpx

            custom_httpx_client = httpx.Client(
                timeout=5.0,
                limits=httpx.Limits(max_keepalive_connections=5),
            )
            options = ClientOptions(httpx_client=custom_httpx_client)
            client = create_client(
                config.url,
                config.publishable_key,
                options=options,
            )
            client._antigravity_httpx_client = custom_httpx_client

            try:
                from security_manager import SecurityManager
                access_token, refresh_token = SecurityManager.get_supabase_session()
            except Exception:
                access_token, refresh_token = "", ""

            authenticated = False
            try:
                auth_response = None
                if access_token and refresh_token:
                    auth_response = client.auth.set_session(access_token, refresh_token)
                session = getattr(auth_response, "session", None) if auth_response else None
                if session:
                    SyncManager._persist_supabase_session(session)
                    authenticated = True
                    user = getattr(session, "user", None)
                    client._antigravity_email = getattr(user, "email", "") or ""
            except Exception as auth_error:
                classified = classify_cloud_error(auth_error)
                client._antigravity_restore_error_kind = classified.kind
                try:
                    from security_manager import SecurityManager
                    SecurityManager.clear_supabase_session()
                except Exception:
                    pass

            client._antigravity_authenticated = authenticated

            try:
                def persist_auth_event(event, session):
                    event_name = str(getattr(event, "value", event) or "").upper()
                    if session and event_name in {
                        "SIGNED_IN", "TOKEN_REFRESHED", "USER_UPDATED"
                    }:
                        SyncManager._persist_supabase_session(session)
                        client._antigravity_authenticated = True
                        user = getattr(session, "user", None)
                        client._antigravity_email = getattr(user, "email", "") or ""

                client.auth.on_auth_state_change(persist_auth_event)
                client._antigravity_auth_callback = persist_auth_event
            except Exception:
                pass
            return client
        except Exception:
            if custom_httpx_client is not None:
                try:
                    custom_httpx_client.close()
                except Exception:
                    pass
            return None

    @staticmethod
    def _persist_supabase_session(session):
        access_token = getattr(session, "access_token", "")
        refresh_token = getattr(session, "refresh_token", "")
        if not (access_token and refresh_token):
            return False
        try:
            from security_manager import SecurityManager
            SecurityManager.save_supabase_session(access_token, refresh_token)
            return True
        except Exception as error:
            # Keep the valid in-memory session alive even if Windows Credential
            # Manager is temporarily unavailable. The next auth event retries it.
            print("Supabase session persistence unavailable.")
            return False

    @staticmethod
    def _session_from_response(response):
        return getattr(response, "session", None) or response

    def _mark_auth_required(self, error=None):
        self._auth_retry_blocked = True
        # 로그인 만료는 오프라인이 아니다. offline 로 표시하면 네트워크를
        # 확인하라는 안내가 나가고 정작 필요한 재로그인은 안내되지 않는다.
        self._last_failure_offline = False
        self._last_sync_error = (
            "AUTH_REQUIRED: 클라우드 로그인 세션을 갱신하지 못했습니다. "
            "설정 탭에서 다시 로그인하면 로컬 대기 작업이 이어서 전송됩니다."
        )
        if self.supabase:
            try:
                self.supabase._antigravity_authenticated = False
            except Exception:
                pass
        if error:
            self._last_cloud_error_kind = classify_cloud_error(error).kind
            print("Supabase session recovery paused.")

    def ensure_session_valid(self, client=None, force_refresh=False):
        """Validate one shared session and serialize token refresh attempts."""
        client = client or self.supabase
        if not client:
            raise RuntimeError("AUTH_REQUIRED")
        auth = getattr(client, "auth", None)
        if auth is None:
            return True
        if self._auth_retry_blocked and not force_refresh:
            raise RuntimeError("AUTH_REQUIRED")

        observed_generation = self._auth_refresh_generation
        with self._session_refresh_lock:
            try:
                if force_refresh and observed_generation == self._auth_refresh_generation:
                    response = auth.refresh_session()
                    self._auth_refresh_generation += 1
                else:
                    response = auth.get_session()
                session = self._session_from_response(response)
                if not getattr(session, "access_token", "") or not getattr(
                    session, "refresh_token", ""
                ):
                    raise RuntimeError("AUTH_REQUIRED")
                self._persist_supabase_session(session)
                client._antigravity_authenticated = True
                self._auth_retry_blocked = False
                return True
            except Exception as error:
                self._mark_auth_required(error)
                raise RuntimeError("AUTH_REQUIRED") from error

    def _call_with_session(self, action, client=None):
        """Execute one server action with at most one token recovery retry."""
        client = client or self.supabase
        self.ensure_session_valid(client)
        try:
            return action()
        except Exception as error:
            if self._stable_error_code(error) != "AUTH_EXPIRED":
                raise
        self.ensure_session_valid(client, force_refresh=True)
        try:
            return action()
        except Exception as error:
            if self._stable_error_code(error) in {"AUTH_EXPIRED", "AUTH_REQUIRED"}:
                self._mark_auth_required(error)
                raise RuntimeError("AUTH_REQUIRED") from error
            raise

    @staticmethod
    def _close_supabase_client(client):
        http_client = getattr(client, "_antigravity_httpx_client", None)
        if http_client is not None:
            try:
                http_client.close()
            except Exception:
                pass

    def init_supabase(self):
        old_client = self.supabase
        self._cloud_config = load_cloud_client_config(supabase_config_dir())
        self.cloud_config_state = self._cloud_config.state
        self.cloud_config_message = (
            "" if self._cloud_config.is_ready else self._cloud_config.user_message
        )
        self.supabase = (
            self.create_supabase_client(self._cloud_config)
            if self._cloud_config.is_ready
            else None
        )
        if old_client is not None and old_client is not self.supabase:
            self._close_supabase_client(old_client)
        if self._cloud_config.is_ready and not self.supabase:
            self.cloud_config_state = "invalid"
            self.cloud_config_message = CLOUD_INVALID_MESSAGE

    @property
    def cloud_network_enabled(self):
        return self.cloud_config_state == "ready" and self.supabase is not None

    def cloud_configuration_status(self):
        return self.cloud_config_state, self.cloud_config_message

    def sign_in(self, email, password):
        if not email or not password:
            return False, "이메일과 비밀번호를 입력해주세요."
        if not self.cloud_network_enabled:
            self.init_supabase()
        if not self.cloud_network_enabled:
            return False, self.cloud_config_message or CLOUD_INVALID_MESSAGE
        try:
            response = self.supabase.auth.sign_in_with_password({
                "email": email.strip(),
                "password": password,
            })
            session = getattr(response, "session", None)
            if not session:
                return False, "로그인 세션을 받지 못했습니다."
            self._persist_supabase_session(session)
            try:
                self.supabase._antigravity_authenticated = True
            except Exception:
                pass
            self._auth_retry_blocked = False
            self._last_sync_error = ""
            self._last_failure_offline = False
            self._publish_sync_state()
            QTimer.singleShot(0, self.retry_pending_syncs)
            user = getattr(response, "user", None)
            signed_in_email = getattr(user, "email", None) or email.strip()
            self.supabase._antigravity_email = signed_in_email
            return True, signed_in_email
        except Exception as error:
            classified = classify_cloud_error(error)
            self._last_cloud_error_kind = classified.kind
            return False, classified.message

    def sign_out(self):
        try:
            if self.supabase:
                self.supabase.auth.sign_out()
        except Exception:
            pass
        from security_manager import SecurityManager
        SecurityManager.clear_supabase_session()
        try:
            if self.supabase:
                self.supabase._antigravity_authenticated = False
                self.supabase._antigravity_email = ""
        except Exception:
            pass
        self._auth_retry_blocked = True
        self._publish_sync_state()

    def authenticated_email(self):
        if not self.supabase or not getattr(
            self.supabase, "_antigravity_authenticated", True
        ):
            return ""
        return getattr(self.supabase, "_antigravity_email", "") or ""

    @staticmethod
    def _response_data(response):
        data = getattr(response, "data", response)
        if isinstance(data, list) and len(data) == 1:
            return data[0]
        return data

    @staticmethod
    def _stable_error_code(error):
        message = str(error)
        lowered = message.lower()
        if any(marker in lowered for marker in (
            "jwt expired", "pgrst303", "invalid jwt", "token has expired",
            "refresh token", "refresh_token", "authsessionmissing",
        )):
            return "AUTH_EXPIRED"
        if "permission denied for function" in lowered:
            return "SERVER_RPC_PERMISSION_DENIED"
        for code in (
            "AUTH_REQUIRED", "FORBIDDEN", "INVALID_ARGUMENT",
            "PROJECT_TRASHED", "PROJECT_PURGED", "PROJECT_NOT_FOUND",
            "DOCUMENT_NOT_FOUND", "DOCUMENT_ALREADY_EXISTS",
            # PARENT_FOLDER_NOT_FOUND must stay ahead of FOLDER_NOT_FOUND:
            # this matches by substring, and the shorter one is inside it.
            "PARENT_FOLDER_NOT_FOUND", "FOLDER_NAME_CONFLICT",
            "FOLDER_ALREADY_EXISTS", "FOLDER_NOT_FOUND",
            "FOLDER_NOT_EMPTY", "FOLDER_CYCLE",
            "REVISION_CONFLICT", "OPERATION_ID_REUSED", "LEASE_REQUIRED",
            "LEASE_CONFLICT", "LEASE_EXPIRED", "PATH_CONFLICT",
            "CONTRACT_NOT_ALLOWED", "CONTRACT_DIGEST_MISMATCH",
            "PROTOCOL_TOO_OLD", "CAPABILITY_MISMATCH",
            "PROJECT_MIGRATING", "MIGRATION_LOCKED",
            "STALE_MIGRATION_EPOCH", "BATCH_ID_REUSED",
            "CONTENT_SIZE_MISMATCH", "CONTENT_DIGEST_MISMATCH",
            "STRUCTURE_REVISION_CONFLICT", "PARTIAL_BATCH_RESPONSE",
            "REMOTE_DOCUMENT_SNAPSHOT_INCOMPLETE", "REMOTE_PATH_CONFLICT",
            "INVALID_REMOTE_PATH", "REMOTE_SNAPSHOT_APPLY_FAILED",
        ):
            if code in message:
                return code
        return ""

    def _ensure_remote_project(self, client):
        response = client.rpc("ensure_project", {
            "p_project_id": self._v2_context["project_id"],
            "p_name": (
                self._v2_context.get("server_name")
                or self._v2_context["project_name"]
            ),
        }).execute()
        return self._response_data(response)

    def _fetch_v2_project_status(self, require_connection=False):
        if not self.is_v2_enabled or is_forced_offline() or not self.supabase:
            if require_connection:
                raise RuntimeError("NETWORK_UNAVAILABLE")
            return self._current_project_server_state()
        project_id = self._v2_context["project_id"]
        try:
            response = self._call_with_session(
                lambda: self.supabase.rpc(
                    "get_project_status",
                    {"p_project_id": project_id},
                ).execute(),
                self.supabase,
            )
            data = self._response_data(response) or {}
        except Exception as status_error:
            status_code = self._stable_error_code(status_error)
            if status_code in {"AUTH_EXPIRED", "AUTH_REQUIRED"}:
                raise
            if status_code == "PROJECT_NOT_FOUND":
                # The RPC answered. It is deployed, so the compatibility
                # path below has nothing left to discover: the server
                # simply holds no row for this project.
                data = {"state": self._absent_project_state()}
            else:
                # Compatibility path for a server that has the project-trash
                # migration but has not deployed get_project_status yet.
                trashed_response = self._call_with_session(
                    lambda: self.supabase.rpc(
                        "list_trashed_projects", {}
                    ).execute(),
                    self.supabase,
                )
                trashed_rows = getattr(trashed_response, "data", None)
                if not isinstance(trashed_rows, list):
                    raise RuntimeError("INVALID_RESPONSE") from None
                if any(
                    str(row.get("project_id") or "") == project_id
                    for row in trashed_rows if isinstance(row, dict)
                ):
                    data = {"state": "trashed"}
                else:
                    active_response = self._call_with_session(
                        lambda: self.supabase.table("projects")
                        .select("project_id")
                        .eq("project_id", project_id)
                        .limit(1)
                        .execute(),
                        self.supabase,
                    )
                    active_rows = getattr(active_response, "data", None)
                    if not isinstance(active_rows, list):
                        raise RuntimeError("INVALID_RESPONSE") from None
                    data = {
                        "state": (
                            "active" if active_rows
                            else self._absent_project_state()
                        )
                    }
        state = str(data.get("state") or "")
        if state not in {"active", "trashed", "purged"}:
            raise RuntimeError("INVALID_RESPONSE")
        self.mark_project_server_state(
            self._v2_context["project_id"], state
        )
        return state

    def _acquire_v2_lease(
        self, document_id, client=None, session_checked=False
    ):
        client = client or self.supabase
        if not client:
            raise RuntimeError("서버 연결 없음")
        action = lambda: client.rpc("acquire_edit_lease", {
                "p_document_id": document_id,
                "p_device_id": self._v2_device_id,
                "p_ttl_seconds": 90,
            }).execute()
        response = action() if session_checked else self._call_with_session(
            action, client
        )
        data = self._response_data(response) or {}
        token = data.get("lease_token")
        if not token:
            raise RuntimeError("LEASE_REQUIRED")
        self._v2_leases[document_id] = token
        return data

    def _fetch_remote_document(self, document_id, client=None):
        client = client or self.supabase
        response = (
            client.table("documents")
            .select(
                "document_id,relative_path,content,revision,is_deleted,deleted_at,updated_at"
            )
            .eq("document_id", document_id)
            .limit(1)
            .execute()
        )
        rows = getattr(response, "data", None) or []
        return rows[0] if rows else None

    def set_remote_protected_paths_provider(self, provider):
        self._v2_protected_paths_provider = provider

    def set_active_document_paths_provider(self, provider):
        self._v2_active_paths_provider = provider

    def _active_v2_paths(self):
        if not self._v2_active_paths_provider:
            return set()
        try:
            return {
                str(path or "").replace("\\", "/")
                for path in self._v2_active_paths_provider()
                if path
            }
        except (AttributeError, RuntimeError, TypeError):
            return set()

    def _release_v2_lease(self, document_id, client=None):
        token = self._v2_leases.get(document_id)
        if not token:
            return True
        if is_forced_offline():
            self._v2_leases.pop(document_id, None)
            return True
        supabase = client or self.supabase
        if not supabase:
            if self._v2_leases.get(document_id) == token:
                self._v2_leases.pop(document_id, None)
            return True
        try:
            self._call_with_session(
                lambda: supabase.rpc("release_edit_lease", {
                    "p_document_id": document_id,
                    "p_device_id": self._v2_device_id,
                    "p_lease_token": token,
                }).execute(),
                supabase,
            )
            if self._v2_leases.get(document_id) == token:
                self._v2_leases.pop(document_id, None)
            return True
        except Exception as error:
            print(f"Failed to release v2 edit lease: {error}")
            # Do not keep heartbeating a lease that we failed to release.
            # The server-side TTL can then expire naturally and another device
            # can continue editing instead of being blocked indefinitely.
            if self._v2_leases.get(document_id) == token:
                self._v2_leases.pop(document_id, None)
            return False

    def _acquire_v2_lease_async(self, document_id, client=None):
        return self._start_server_action(
            lambda: self._acquire_v2_lease(document_id, client=client)
        )

    def _release_v2_lease_async(self, document_id, client=None):
        return self._start_server_action(
            lambda: self._release_v2_lease(document_id, client=client)
        )

    def _finalize_v2_operation_lease(self, kind, operation):
        if not operation or kind == "auto_merged":
            return
        document_id = operation.get("document_id")
        if not document_id:
            return
        local_path = str(operation.get("local_path") or "").replace("\\", "/")
        is_active = local_path in self._active_v2_paths()

        # 새 문서는 첫 commit 때 lease_token 없이 생성된다. 생성 직후에도
        # 사용자가 편집 중이면 즉시 lease를 획득해 기존 문서와 동일하게
        # heartbeat를 유지하고 다른 기기의 덮어쓰기를 막는다.
        if (
            kind == "committed"
            and not operation.get("is_deleted")
            and is_active
            and document_id not in self._v2_leases
        ):
            self._acquire_v2_lease_async(document_id)
            return

        if document_id not in self._v2_leases:
            return

        # 활성 편집 문서는 저장이 끝나도 lease를 유지한다. 문서를 닫거나
        # 다른 문서로 이동했거나 삭제/충돌이 발생한 경우에만 해제한다.
        must_release = (
            kind in {"conflict", "project_disabled"}
            or bool(operation.get("is_deleted"))
            or not is_active
        )
        if must_release:
            self._release_v2_lease_async(document_id)

    def _pull_project_id(self, project_id=None):
        """The project a request is about: the one it froze, or the one open."""
        return str(project_id or (self._v2_context or {}).get("project_id") or "")

    def _fetch_v2_project_documents(
        self, require_connection=False, check_project_status=True,
        project_id=None,
    ):
        if not self.is_v2_enabled or is_forced_offline() or not self.supabase:
            if require_connection:
                raise RuntimeError("NETWORK_UNAVAILABLE")
            return []
        if check_project_status:
            state = self._fetch_v2_project_status(
                require_connection=require_connection
            )
            if state == "trashed":
                raise RuntimeError("PROJECT_TRASHED")
            if state == "purged":
                raise RuntimeError("PROJECT_PURGED")
        response = self._call_with_session(
            lambda: self.supabase.table("documents")
            .select(
                "document_id,relative_path,content,revision,is_deleted,deleted_at,"
                "parent_folder_id,name,structure_revision,updated_at"
            )
            .eq("project_id", self._pull_project_id(project_id))
            .execute(),
            self.supabase,
        )
        data = getattr(response, "data", None)
        if data is None:
            if require_connection:
                raise RuntimeError("INVALID_RESPONSE")
            return []
        if not isinstance(data, list):
            raise RuntimeError("INVALID_RESPONSE")
        return data

    def _fetch_v2_project_folders(self, client, project_id=None):
        """Read the stable folder projection used by newer iPad clients."""
        response = self._call_with_session(
            lambda: client.table("folders")
            .select(
                "folder_id,parent_folder_id,name,revision,is_deleted,updated_at"
            )
            .eq("project_id", self._pull_project_id(project_id))
            .execute(),
            client,
        )
        data = getattr(response, "data", None)
        if not isinstance(data, list):
            raise RuntimeError("INVALID_FOLDER_RESPONSE")
        return data

    def _fetch_v2_project_folder_versions(self, client, project_id=None):
        """Read folder history needed to bootstrap an exact rename identity."""
        response = self._call_with_session(
            lambda: client.table("folder_versions")
            .select(
                "folder_id,parent_folder_id,name,revision,is_deleted,operation_kind,created_at"
            )
            .eq("project_id", self._pull_project_id(project_id))
            .execute(),
            client,
        )
        data = getattr(response, "data", None)
        if not isinstance(data, list):
            raise RuntimeError("INVALID_FOLDER_VERSION_RESPONSE")
        return data

    def _fetch_v2_project_tree_orders(self, client, project_id=None):
        response = self._call_with_session(
            lambda: client.table("tree_orders")
            .select(
                "tree_order_id,parent_folder_id,children,revision,updated_at"
            )
            .eq("project_id", self._pull_project_id(project_id))
            .execute(),
            client,
        )
        data = getattr(response, "data", None)
        if not isinstance(data, list):
            raise RuntimeError("INVALID_TREE_ORDER_RESPONSE")
        return data

    def _needs_folder_history(self, folder_rows):
        """Fetch history only to bootstrap a renamed ID not yet known locally."""
        if not self.is_v2_enabled:
            return False
        stored_ids = {
            str(item["folder_id"])
            for item in self._v2_store.list_folders(
                self._v2_context["local_key"]
            )
        }
        return any(
            isinstance(row, dict)
            and row.get("folder_id")
            and str(row["folder_id"]) not in stored_ids
            and int(row.get("revision") or 0) > 1
            for row in (folder_rows or [])
        )

    def _recover_untracked_local_files_after_pull(self, remote_documents):
        """Adopt remote UUIDs first, then queue only proven local-only content."""
        candidates = set(self._v2_untracked_recovery_paths or set())
        if not candidates or not self.is_v2_enabled or self._v2_wpm is None:
            return 0

        remote_identities = {}
        for remote in remote_documents or []:
            if bool(remote.get("is_deleted")):
                continue
            try:
                remote_path = self._safe_relative_path(remote.get("relative_path"))
            except ValueError:
                continue
            if is_live_document_path(remote_path):
                remote_identities[
                    self._tree_path_comparison_key(remote_path)
                ] = str(remote.get("document_id") or "")

        queued_count = 0
        unresolved = set()
        with self._structure_mutation_gate:
            for relative_path in sorted(candidates):
                document = self._v2_store.get_document(
                    self._v2_context["local_key"], relative_path
                )
                remote_id = remote_identities.get(
                    self._tree_path_comparison_key(relative_path)
                )
                if remote_id:
                    if document and str(document["document_id"]) == remote_id:
                        # The pull durably adopted the server document_id.
                        continue
                    unresolved.add(relative_path)
                    continue
                full_path = os.path.abspath(os.path.join(
                    self._v2_wpm.writing_root_path, relative_path
                ))
                root = os.path.abspath(self._v2_wpm.writing_root_path)
                try:
                    if (
                        os.path.commonpath([root, full_path]) != root
                        or not os.path.isfile(full_path)
                        or self._is_reparse_path(full_path)
                    ):
                        continue
                except (OSError, ValueError):
                    continue
                content = self._v2_wpm.read_text_file(relative_path)
                if content is None:
                    continue
                if content == "":
                    # Empty files represented by tree-order need no document UUID.
                    continue
                if document is None:
                    self._v2_store.ensure_document(
                        self._v2_context["local_key"], relative_path, content
                    )
                self._v2_store.enqueue(
                    self._v2_context,
                    relative_path,
                    content,
                    relative_path=relative_path,
                )
                queued_count += 1
            self._v2_untracked_recovery_paths = unresolved

        if unresolved:
            detail = (
                "같은 서버 경로에 다른 문서 UUID가 있어 자동 신규 등록을 "
                "중단했습니다. 로컬 파일은 변경하지 않았습니다."
            )
            self._set_sync_state("conflict", detail)
        elif queued_count:
            self._publish_sync_state()
        return queued_count

    @classmethod
    def _tree_order_from_content(cls, content):
        try:
            payload = json.loads(content or "{}")
        except (TypeError, json.JSONDecodeError):
            return None
        tree_order = payload.get("tree_order") if isinstance(payload, dict) else None
        if not isinstance(tree_order, dict):
            return None
        return cls._normalized_tree_order(tree_order)

    @classmethod
    def _infer_outbound_empty_folder_rename(cls, base_content, content):
        """Return one exact same-position rename between adjacent tree snapshots."""
        previous_order = cls._tree_order_from_content(base_content)
        current_order = cls._tree_order_from_content(content)
        if previous_order is None or current_order is None or previous_order == current_order:
            return None

        differing_parents = []
        for parent_path in set(previous_order).intersection(current_order):
            old_children = previous_order[parent_path]
            new_children = current_order[parent_path]
            if old_children == new_children:
                continue
            if len(old_children) != len(new_children):
                return None
            changed_indexes = [
                index
                for index, (old_name, new_name) in enumerate(
                    zip(old_children, new_children)
                )
                if old_name != new_name
            ]
            if len(changed_indexes) != 1:
                return None
            differing_parents.append((parent_path, changed_indexes[0]))
        if len(differing_parents) != 1:
            return None

        parent_path, index = differing_parents[0]
        if parent_path == "메인/휴지통" or parent_path.startswith("메인/휴지통/"):
            return None
        old_name = previous_order[parent_path][index]
        new_name = current_order[parent_path][index]
        old_path = cls._tree_order_child_path(parent_path, old_name)
        new_path = cls._tree_order_child_path(parent_path, new_name)
        if cls._tree_path_comparison_key(old_path) == cls._tree_path_comparison_key(
            new_path
        ):
            return None

        old_has_entry = old_path in previous_order
        new_has_entry = new_path in current_order
        if old_has_entry != new_has_entry:
            return None
        if old_has_entry and (
            previous_order[old_path] != [] or current_order[new_path] != []
        ):
            return None
        if any(key.startswith(old_path + "/") for key in previous_order) or any(
            key.startswith(new_path + "/") for key in current_order
        ):
            return None

        transformed = copy.deepcopy(previous_order)
        transformed[parent_path][index] = new_name
        if old_has_entry:
            transformed.pop(old_path)
            transformed[new_path] = []
        if transformed != current_order:
            return None
        return {
            "old_relative_path": old_path,
            "new_relative_path": new_path,
            "new_name": new_name,
        }

    @classmethod
    def _folder_rows_with_tree_paths(cls, rows):
        """Resolve each live folder_id to one validated Windows tree path."""
        live_rows = {
            str(row.get("folder_id")): row
            for row in (rows or [])
            if isinstance(row, dict)
            and row.get("folder_id")
            and not row.get("is_deleted")
        }
        cached_paths = {}

        def row_path(folder_id, visiting=None):
            if folder_id in cached_paths:
                return cached_paths[folder_id]
            row = live_rows.get(folder_id)
            if row is None:
                return None
            visiting = set(visiting or ())
            if folder_id in visiting:
                return None
            visiting.add(folder_id)
            try:
                name = unicodedata.normalize("NFC", str(row.get("name") or ""))
                cls._validate_tree_order_component(name)
            except (TypeError, ValueError):
                return None
            parent_id = row.get("parent_folder_id")
            if parent_id:
                parent_path = row_path(str(parent_id), visiting)
                if not parent_path:
                    return None
                storage_name = (
                    TREE_ROOT_STORAGE_NAMES.get(name, name)
                    if parent_path == "메인"
                    else name
                )
                path = f"{parent_path}/{storage_name}"
            else:
                path = name
            cached_paths[folder_id] = path
            return path

        resolved = {}
        for folder_id, row in live_rows.items():
            path = row_path(folder_id)
            if not path:
                continue
            if path != "메인" and not path.startswith("메인/"):
                path = f"메인/{path}"
            resolved[folder_id] = {**row, "local_path": path}
        return resolved

    @classmethod
    def _folder_rows_by_tree_path(cls, rows, relative_path):
        """Resolve a tree path without guessing between duplicate folder identities."""
        wanted_key = cls._tree_path_comparison_key(relative_path)
        matches = []
        for row in cls._folder_rows_with_tree_paths(rows).values():
            if cls._tree_path_comparison_key(row["local_path"]) == wanted_key:
                matches.append(row)
        return matches

    def _publishable_identity_folders(self):
        """Every identity folder, shallowest first, with its wanted server state.

        Identity is the only authority here: a node is there only after a
        journalled creation transaction issued its UUID. Where the node sits now
        says what the server should hold — a folder under 휴지통 belongs there as
        a tombstone, anything else as a live row. 휴지통 itself is a live folder,
        matching iPad.
        """
        from project_creation_v1 import identity_folder_nodes

        root = getattr(self._v2_wpm, "writing_root_path", None)
        return [
            {
                **node,
                "wants_deleted": node["legacy_path"].startswith("메인/휴지통/"),
            }
            for node in identity_folder_nodes(root)
        ]

    def _commit_folder_state(self, operation, client, folder_id, params):
        """Send one commit_folder, or report and step over a refusal that stands.

        Returns ``None`` when the server refused for a reason a retry cannot
        change. Anything else — a network failure, an expired session — is
        raised so the tree-order operation retries the whole thing as before.
        """
        try:
            response = client.rpc("commit_folder", {
                "p_folder_id": folder_id,
                "p_project_id": operation["project_id"],
                "p_operation_id": str(uuid.uuid5(
                    uuid.UUID(str(operation["operation_id"])),
                    f"folder-{params['intent']}:{folder_id}",
                )),
                "p_device_id": self._v2_device_id,
                "p_base_revision": params["base_revision"],
                "p_parent_folder_id": params["parent_folder_id"],
                "p_name": params["name"],
                "p_is_deleted": params["is_deleted"],
            }).execute()
        except Exception as error:
            code = self._stable_error_code(error)
            if code not in PERMANENT_FOLDER_ERROR_CODES:
                raise
            self._report_folder_block(operation, folder_id, code)
            return None
        result = self._response_data(response) or {}
        if str(result.get("folder_id")) != folder_id or "revision" not in result:
            raise RuntimeError("INVALID_FOLDER_COMMIT_RESPONSE")
        return result

    def _report_folder_block(self, operation, folder_id, reason):
        """Report a folder this client may not touch. Never repair it here."""
        # A blocked folder stays blocked until someone reconciles the two
        # identities, so this is one standing report, not one per dispatch. The
        # operation id is deliberately left out: it would make every retry look
        # like a new finding.
        self._v2_store.record_diagnostic(
            self._v2_context["local_key"],
            "folder_create_blocked",
            dedupe=True,
            entity_id=folder_id,
            error_code=reason,
            project_id=operation["project_id"],
            rpc_name="commit_folder",
        )

    def _commit_outbound_folder_lifecycle(self, operation, client):
        """Make the server's folder rows agree with identity before tree-order.

        Identity says where a folder is, so it also says what the server should
        hold: a row under 휴지통 belongs there as a tombstone, anything else as a
        live row. Two directions, two orders. Deletions run deepest first
        because the server refuses to tombstone a folder that still has a live
        child. Creations and restores run shallowest first because a folder
        needs a live parent.

        Only identity may authorize a change, and only over rows that already
        carry the very same UUID. A folder whose name is held by a different
        identity — an imported project whose folders were issued elsewhere — is
        reported and left exactly as it is. This client never renames,
        re-parents or deletes another identity's folder to make room.
        """
        empty = {"created": [], "restored": [], "deleted": [], "blocked": []}
        if not self.is_v2_enabled or self._v2_wpm is None:
            return empty
        candidates = self._publishable_identity_folders()
        if not candidates:
            return empty

        live_by_id = {}
        deleted_by_id = {}
        live_by_slot = {}
        for row in self._fetch_v2_project_folders(client) or []:
            if not isinstance(row, dict):
                continue
            folder_id = str(row.get("folder_id") or "")
            if not folder_id:
                continue
            if row.get("is_deleted"):
                deleted_by_id[folder_id] = row
                continue
            live_by_id[folder_id] = row
            live_by_slot[self._folder_slot(
                row.get("parent_folder_id"), row.get("name")
            )] = folder_id

        result = {key: list(value) for key, value in empty.items()}

        # 휴지통 으로 간 폴더부터, 깊은 것에서 얕은 것 순으로 내린다.
        for node in sorted(
            (node for node in candidates if node["wants_deleted"]),
            key=lambda node: node["legacy_path"].count("/"),
            reverse=True,
        ):
            folder_id = str(node["uuid"])
            row = live_by_id.get(folder_id)
            if row is None:
                # Never published, so there is nothing to tombstone. This is
                # also what keeps a first upload from trying to create a folder
                # that is already deleted, which the server refuses outright.
                continue
            still_live_child = any(
                str(other.get("parent_folder_id") or "") == folder_id
                for other in live_by_id.values()
            )
            if still_live_child:
                result["blocked"].append(node["legacy_path"])
                self._report_folder_block(
                    operation, folder_id, "CHILD_FOLDER_STILL_LIVE"
                )
                continue
            committed = self._commit_folder_state(operation, client, folder_id, {
                "intent": "delete",
                "base_revision": int(row.get("revision") or 0),
                # The server row, not identity, describes where this folder
                # still hangs. Identity already moved it under 휴지통, which has
                # no bearing on the parent the server must validate.
                "parent_folder_id": row.get("parent_folder_id"),
                "name": row.get("name"),
                "is_deleted": True,
            })
            if committed is None:
                # Reported and left as it is. Its ancestors stay live, which is
                # what the server already believes, so the rest of the pass
                # still describes a tree the server can accept.
                result["blocked"].append(node["legacy_path"])
                continue
            live_by_slot.pop(
                self._folder_slot(row.get("parent_folder_id"), row.get("name")),
                None,
            )
            del live_by_id[folder_id]
            deleted_by_id[folder_id] = {**row, **committed, "is_deleted": True}
            result["deleted"].append(node["legacy_path"])

        local_key = self._v2_context["local_key"]
        for node in (node for node in candidates if not node["wants_deleted"]):
            folder_id = str(node["uuid"])
            if folder_id in live_by_id:
                continue
            parent_uuid = node["parent_uuid"]
            parent_uuid = str(parent_uuid) if parent_uuid else None
            name = node["legacy_path"].rsplit("/", 1)[-1]
            slot = self._folder_slot(parent_uuid, name)
            reason = None
            if parent_uuid is not None and parent_uuid not in live_by_id:
                reason = "PARENT_NOT_PUBLISHED"
            elif slot in live_by_slot:
                reason = "FOLDER_NAME_TAKEN"
            if reason:
                result["blocked"].append(node["legacy_path"])
                self._report_folder_block(operation, folder_id, reason)
                continue

            restored = deleted_by_id.get(folder_id)
            committed = self._commit_folder_state(operation, client, folder_id, {
                "intent": "restore" if restored else "create",
                "base_revision": (
                    int(restored.get("revision") or 0) if restored else 0
                ),
                "parent_folder_id": parent_uuid,
                "name": name,
                "is_deleted": False,
            })
            if committed is None:
                # Children of a folder that never landed are skipped by the
                # PARENT_NOT_PUBLISHED guard on the next turn of this same loop.
                result["blocked"].append(node["legacy_path"])
                continue
            live_by_id[folder_id] = {**committed, "is_deleted": False}
            live_by_slot[slot] = folder_id
            deleted_by_id.pop(folder_id, None)
            self._v2_store.ensure_local_folder(
                local_key,
                node["legacy_path"],
                folder_id=folder_id,
                parent_folder_id=parent_uuid,
            )
            result["restored" if restored else "created"].append(
                node["legacy_path"]
            )

        return result

    @staticmethod
    def _folder_slot(parent_folder_id, name):
        """Key one live sibling slot the way the server's unique index does."""
        return (
            str(parent_folder_id or ""),
            unicodedata.normalize("NFC", str(name or "")).casefold(),
        )

    def _commit_outbound_folder_rename(self, operation, client):
        """Mirror a conservative Windows empty-folder rename to stable folder_id."""
        intent = self._infer_outbound_empty_folder_rename(
            operation.get("base_content"), operation.get("content")
        )
        if self._v2_wpm is None:
            return None
        local_key = operation.get("local_key") or self._v2_context["local_key"]
        rename_intent = None
        if intent is not None:
            rename_intent = self._v2_store.pending_folder_rename_intent(
                local_key,
                intent["old_relative_path"],
                intent["new_relative_path"],
            )
        else:
            # Recovery case: folder identity still has the old name, while a
            # prior tree-order snapshot already contains the desired new name.
            # An explicit durable user intent is the only authority to finish
            # that folder RPC; an ordinary reconstructed tree snapshot cannot.
            tree_order = self._tree_order_from_content(operation.get("content"))
            candidates = []
            if tree_order is not None:
                for candidate in self._v2_store.pending_folder_rename_intents(
                    local_key
                ):
                    old_path = candidate["old_path"]
                    new_path = candidate["new_path"]
                    old_parent, old_name = os.path.split(old_path)
                    new_parent, new_name = os.path.split(new_path)
                    sibling_key = "<root>" if old_parent == "메인" else old_parent
                    siblings = tree_order.get(sibling_key)
                    if (
                        old_parent == new_parent
                        and old_name
                        and new_name
                        and isinstance(siblings, list)
                        and siblings.count(old_name) == 0
                        and siblings.count(new_name) == 1
                        and old_path not in tree_order
                    ):
                        candidates.append((candidate, {
                            "old_relative_path": old_path,
                            "new_relative_path": new_path,
                            "new_name": new_name,
                        }))
            if len(candidates) == 1:
                rename_intent, intent = candidates[0]
        if rename_intent is None:
            # Tree refreshes, remote merges and startup reconstruction also
            # enqueue tree-order. They must never be promoted to a folder RPC
            # without durable proof of a local user rename.
            return None

        root = os.path.abspath(self._v2_wpm.writing_root_path)
        new_full_path = os.path.abspath(
            os.path.join(root, intent["new_relative_path"])
        )
        try:
            if (
                os.path.commonpath([root, new_full_path]) != root
                or not os.path.isdir(new_full_path)
                or self._is_reparse_path(new_full_path)
            ):
                return None
            with os.scandir(new_full_path) as entries:
                if next(entries, None) is not None:
                    return None
        except (OSError, ValueError):
            return None

        rows = self._fetch_v2_project_folders(client)
        old_matches = self._folder_rows_by_tree_path(
            rows, intent["old_relative_path"]
        )
        if len(old_matches) != 1:
            # A prior replay may already have changed the projection. In that
            # case the desired identity is present and no second revision is needed.
            new_matches = self._folder_rows_by_tree_path(
                rows, intent["new_relative_path"]
            )
            if len(old_matches) == 0 and len(new_matches) == 1:
                self._v2_store.complete_folder_rename_intent(
                    rename_intent["intent_id"]
                )
                return {"status": "already_applied", **new_matches[0]}
            # Legacy Windows-only folders have no stable server identity. Keep
            # tree-order compatibility, but never invent or pair an identity.
            return None

        folder = old_matches[0]
        try:
            folder_namespace = uuid.UUID(str(operation["operation_id"]))
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("INVALID_FOLDER_OPERATION")
        folder_operation_id = str(uuid.uuid5(
            folder_namespace,
            f"folder-rename:{folder['folder_id']}:{intent['new_name']}",
        ))
        try:
            response = client.rpc("commit_folder", {
                "p_folder_id": folder["folder_id"],
                "p_project_id": operation["project_id"],
                "p_base_revision": int(folder.get("revision") or 0),
                "p_operation_id": folder_operation_id,
                "p_device_id": self._v2_device_id,
                "p_parent_folder_id": folder.get("parent_folder_id"),
                "p_name": intent["new_name"],
                "p_is_deleted": False,
            }).execute()
        except Exception as error:
            code = self._stable_error_code(error)
            if code not in PERMANENT_FOLDER_ERROR_CODES:
                raise
            # The intent stays pending on purpose. It is the writer's rename,
            # and dropping it here would lose that silently; it simply stops
            # taking the tree-order commit down with it every time.
            self._report_folder_block(
                operation, str(folder["folder_id"]), code
            )
            return None
        result = self._response_data(response) or {}
        if "revision" not in result or str(result.get("folder_id")) != str(
            folder["folder_id"]
        ):
            raise RuntimeError("INVALID_FOLDER_COMMIT_RESPONSE")
        self._v2_store.complete_folder_rename_intent(
            rename_intent["intent_id"]
        )
        return result

    @staticmethod
    def _safe_relative_path(path):
        normalized = unicodedata.normalize(
            "NFC", (path or "").replace("\\", "/").strip("/")
        )
        parts = [part for part in normalized.split("/") if part]
        if not parts or any(part in {".", ".."} for part in parts):
            raise ValueError("INVALID_REMOTE_PATH")
        return "/".join(parts)

    @staticmethod
    def _tree_path_comparison_key(path):
        return "/".join(
            unicodedata.normalize("NFC", part).casefold()
            for part in str(path or "").replace("\\", "/").split("/")
        )

    @staticmethod
    def _is_reparse_path(path):
        if os.path.islink(path):
            return True
        is_junction = getattr(os.path, "isjunction", None)
        if callable(is_junction) and is_junction(path):
            return True
        try:
            attributes = getattr(os.lstat(path), "st_file_attributes", 0)
        except OSError:
            return False
        return bool(
            attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )

    @staticmethod
    def _validate_tree_order_component(name):
        if not isinstance(name, str):
            raise ValueError("INVALID_REMOTE_PATH")
        name = unicodedata.normalize("NFC", name)
        try:
            validate_local_project_name(name)
        except LocalProjectPathError as error:
            raise ValueError("INVALID_REMOTE_PATH") from error
        if name == "__antigravity__":
            raise ValueError("INVALID_REMOTE_PATH")
        if len(name.encode("utf-16-le")) // 2 > MAX_WINDOWS_COMPONENT_UTF16_UNITS:
            raise ValueError("INVALID_REMOTE_PATH")
        return name

    @classmethod
    def _validate_tree_order_parent(cls, parent_path):
        if not isinstance(parent_path, str):
            raise ValueError("INVALID_REMOTE_PATH")
        if parent_path == "<root>":
            return parent_path
        normalized = canonical_tree_parent_path(parent_path)
        if (
            not normalized
            or normalized.startswith("/")
            or normalized.endswith("/")
            or "//" in normalized
        ):
            raise ValueError("INVALID_REMOTE_PATH")
        parts = normalized.split("/")
        if parts[0] != "메인":
            raise ValueError("INVALID_REMOTE_PATH")
        return "/".join(cls._validate_tree_order_component(part) for part in parts)

    @classmethod
    def _validated_remote_tree_order(cls, tree_order):
        validated = {}
        parent_keys = set()
        for raw_parent, raw_children in tree_order.items():
            parent = cls._validate_tree_order_parent(raw_parent)
            parent_key = cls._tree_path_comparison_key(parent)
            if parent_key in parent_keys:
                raise FileExistsError("REMOTE_PATH_CONFLICT")
            parent_keys.add(parent_key)
            if not isinstance(raw_children, list):
                raise ValueError("INVALID_REMOTE_PATH")

            children = []
            child_keys = set()
            for raw_child in raw_children:
                child = cls._validate_tree_order_component(raw_child)
                storage_name = (
                    TREE_ROOT_STORAGE_NAMES.get(child, child)
                    if parent == "<root>"
                    else child
                )
                child_key = unicodedata.normalize("NFC", storage_name).casefold()
                if child_key in child_keys:
                    raise FileExistsError("REMOTE_PATH_CONFLICT")
                child_keys.add(child_key)
                children.append(storage_name)
            fixed_order = canonical_manuscript_children(parent, children)
            if fixed_order is not None:
                children = fixed_order
            validated[parent] = children
        return validated

    @classmethod
    def _validated_remote_tree_folder_paths(cls, raw_paths, tree_order):
        """Validate additive explicit folder evidence in a tree-order payload."""
        if raw_paths is None:
            return None
        if not isinstance(raw_paths, list):
            raise ValueError("INVALID_REMOTE_PATH")
        validated = set()
        for raw_path in raw_paths:
            path = cls._validate_tree_order_parent(raw_path)
            if (
                path == "<root>"
                or path == "메인/휴지통"
                or path.startswith("메인/휴지통/")
                or path not in tree_order
            ):
                raise ValueError("INVALID_REMOTE_PATH")
            key = cls._tree_path_comparison_key(path)
            if key in validated:
                raise FileExistsError("REMOTE_PATH_CONFLICT")
            validated.add(key)
        return validated

    @classmethod
    def _tree_order_child_path(cls, parent_path, child_name):
        storage_name = (
            TREE_ROOT_STORAGE_NAMES.get(child_name, child_name)
            if parent_path == "<root>"
            else child_name
        )
        if parent_path == "<root>":
            return f"메인/{storage_name}"
        return f"{parent_path}/{storage_name}"

    @classmethod
    def _safe_existing_tree_directory(cls, root, relative_path):
        full_path = os.path.abspath(os.path.join(root, relative_path))
        if not os.path.lexists(full_path):
            return False
        if cls._is_reparse_path(full_path) or not os.path.isdir(full_path):
            raise FileExistsError("REMOTE_PATH_CONFLICT")
        real_root = os.path.realpath(root)
        real_path = os.path.realpath(full_path)
        try:
            if os.path.commonpath([real_root, real_path]) != real_root:
                raise FileExistsError("REMOTE_PATH_CONFLICT")
        except ValueError as error:
            raise FileExistsError("REMOTE_PATH_CONFLICT") from error
        return True

    @classmethod
    def _build_remote_tree_folder_plan(
        cls,
        writing_root,
        tree_order,
        remote_live_document_paths,
        remote_folder_paths=None,
        explicit_tree_folder_paths=None,
        has_remote_folder_projection=False,
    ):
        root = os.path.abspath(writing_root)
        if not cls._safe_existing_tree_directory(root, ""):
            raise ValueError("INVALID_REMOTE_PATH")

        live_paths_by_key = {}
        for raw_path in remote_live_document_paths or set():
            path = cls._safe_relative_path(raw_path)
            key = cls._tree_path_comparison_key(path)
            previous = live_paths_by_key.get(key)
            if previous is not None and previous != path:
                raise FileExistsError("REMOTE_PATH_CONFLICT")
            live_paths_by_key[key] = path
        live_path_keys = set(live_paths_by_key)
        folder_path_keys = {
            cls._tree_path_comparison_key(cls._safe_relative_path(path))
            for path in (remote_folder_paths or set())
        }
        explicit_folder_keys = set(explicit_tree_folder_paths or set())
        tree_parent_folder_keys = {
            cls._tree_path_comparison_key(parent_path)
            for parent_path in tree_order
            if parent_path != "<root>"
        }
        fixed_root_path_keys = {
            cls._tree_path_comparison_key(f"메인/{storage_name}")
            for storage_name in TREE_ROOT_STORAGE_NAMES.values()
        }
        candidate_paths = {}
        document_paths = {}

        def add_candidate(relative_path):
            relative_path = cls._validate_tree_order_parent(relative_path)
            if relative_path == "<root>":
                return
            parts = relative_path.split("/")
            for depth in range(1, len(parts) + 1):
                candidate = "/".join(parts[:depth])
                if candidate == "메인/휴지통" or candidate.startswith(
                    "메인/휴지통/"
                ):
                    return
                key = cls._tree_path_comparison_key(candidate)
                previous = candidate_paths.get(key)
                if previous is not None and previous != candidate:
                    raise FileExistsError("REMOTE_PATH_CONFLICT")
                candidate_paths[key] = candidate

        for parent_path in tree_order:
            if parent_path != "<root>":
                add_candidate(parent_path)
            else:
                add_candidate("메인")
        for parent_path, child_names in tree_order.items():
            for child_name in child_names:
                child_path = cls._tree_order_child_path(parent_path, child_name)
                child_key = cls._tree_path_comparison_key(child_path)
                is_confirmed_folder = (
                    child_key in folder_path_keys
                    or child_key in explicit_folder_keys
                    or child_key in tree_parent_folder_keys
                    or child_key in fixed_root_path_keys
                )
                if child_key in live_path_keys:
                    if is_confirmed_folder:
                        raise FileExistsError("REMOTE_PATH_CONFLICT")
                    document_paths[child_key] = live_paths_by_key[child_key]
                    continue

                # iPad tree-order stores a document's display name without the
                # Windows storage suffix, while the documents projection keeps
                # the real ``.txt`` path.  Match only that one exact same-parent
                # alias.  Stable folder evidence wins neither side: a folder and
                # a document with the same binder label is ambiguous and must be
                # resolved instead of guessed.
                alias_document_path = None
                if not str(child_name).casefold().endswith(".txt"):
                    alias_key = cls._tree_path_comparison_key(f"{child_path}.txt")
                    alias_document_path = live_paths_by_key.get(alias_key)
                if alias_document_path is not None:
                    if is_confirmed_folder:
                        raise FileExistsError("REMOTE_PATH_CONFLICT")
                    document_paths[alias_key] = alias_document_path
                    continue

                if is_confirmed_folder or (
                    explicit_tree_folder_paths is None
                    and not has_remote_folder_projection
                    and not str(child_name).casefold().endswith(".txt")
                ):
                    add_candidate(child_path)
                    continue
                # Folder projections and modern Windows payloads are
                # authoritative. An unclassified leaf is normally a document
                # whose commit has not reached the projection yet; never turn
                # it into a real directory, regardless of its extension.
                raise RuntimeError("REMOTE_DOCUMENT_SNAPSHOT_INCOMPLETE")

        if set(candidate_paths).intersection(live_path_keys):
            raise FileExistsError("REMOTE_PATH_CONFLICT")

        plan = []
        planned_keys = set(candidate_paths)
        for relative_path in sorted(
            candidate_paths.values(), key=lambda path: (path.count("/"), path.casefold())
        ):
            full_path = os.path.abspath(os.path.join(root, relative_path))
            try:
                if os.path.commonpath([root, full_path]) != root:
                    raise ValueError("INVALID_REMOTE_PATH")
            except ValueError as error:
                raise ValueError("INVALID_REMOTE_PATH") from error
            if (
                len(full_path.encode("utf-16-le")) // 2
                > MAX_WINDOWS_DIRECTORY_PATH
            ):
                raise ValueError("INVALID_REMOTE_PATH")

            parent_relative = relative_path.rpartition("/")[0]
            parent_full = root if not parent_relative else os.path.join(root, parent_relative)
            parent_key = cls._tree_path_comparison_key(parent_relative)
            parent_exists = cls._safe_existing_tree_directory(root, parent_relative)
            if not parent_exists and parent_key not in planned_keys:
                raise FileExistsError("REMOTE_PATH_CONFLICT")

            existing_name = None
            if parent_exists:
                requested_name = relative_path.rpartition("/")[2]
                requested_key = unicodedata.normalize("NFC", requested_name).casefold()
                matches = [
                    entry
                    for entry in os.listdir(parent_full)
                    if unicodedata.normalize("NFC", entry).casefold() == requested_key
                ]
                if len(matches) > 1 or (matches and matches[0] != requested_name):
                    raise FileExistsError("REMOTE_PATH_CONFLICT")
                existing_name = matches[0] if matches else None

            exists = existing_name is not None or os.path.lexists(full_path)
            if exists:
                cls._safe_existing_tree_directory(root, relative_path)
            plan.append({
                "relative_path": relative_path,
                "full_path": full_path,
                "exists": exists,
            })

        for relative_path in document_paths.values():
            full_path = os.path.abspath(os.path.join(root, relative_path))
            if (
                len(full_path.encode("utf-16-le")) // 2
                > MAX_WINDOWS_DIRECTORY_PATH
            ):
                raise ValueError("INVALID_REMOTE_PATH")
            parent_relative = relative_path.rpartition("/")[0]
            parent_full = os.path.join(root, parent_relative)
            if not os.path.lexists(parent_full):
                continue
            cls._safe_existing_tree_directory(root, parent_relative)
            requested_name = relative_path.rpartition("/")[2]
            requested_key = unicodedata.normalize("NFC", requested_name).casefold()
            matches = [
                entry
                for entry in os.listdir(parent_full)
                if unicodedata.normalize("NFC", entry).casefold() == requested_key
            ]
            if len(matches) > 1 or (matches and matches[0] != requested_name):
                raise FileExistsError("REMOTE_PATH_CONFLICT")
            if matches:
                existing_path = os.path.join(parent_full, matches[0])
                if cls._is_reparse_path(existing_path) or not os.path.isfile(
                    existing_path
                ):
                    raise FileExistsError("REMOTE_PATH_CONFLICT")
        return plan

    @classmethod
    def _rollback_remote_tree_folders(cls, root, created_paths):
        root = os.path.abspath(root)
        for full_path in reversed(created_paths):
            try:
                if (
                    os.path.commonpath([root, os.path.abspath(full_path)]) == root
                    and os.path.isdir(full_path)
                    and not cls._is_reparse_path(full_path)
                ):
                    os.rmdir(full_path)
            except (OSError, ValueError):
                pass

    @classmethod
    def _build_remote_empty_folder_rename_plan(
        cls,
        writing_root,
        previous_order,
        remote_order,
        remote_live_document_paths,
    ):
        """Infer one unambiguous empty-folder rename from adjacent tree snapshots."""
        if not isinstance(previous_order, dict) or previous_order == remote_order:
            return []

        fixed_root_storage_names = set(TREE_ROOT_STORAGE_NAMES.values())

        def comparable_children(parent_path, children):
            if parent_path != "<root>":
                return list(children)
            # Root labels and presence differ between clients (for example,
            # ``플롯`` vs ``스토리 플롯`` and an omitted ``휴지통``).  They are
            # fixed application nodes, not user structure mutations.  Compare
            # only custom root entries when inferring a custom-folder rename.
            return [
                name for name in children
                if TREE_ROOT_STORAGE_NAMES.get(name, name)
                not in fixed_root_storage_names
            ]

        def equivalent_except_optional_empty_leaves(left, right):
            for key in set(left).union(right):
                if key == "<root>":
                    if comparable_children(
                        key, left.get(key, [])
                    ) != comparable_children(key, right.get(key, [])):
                        return False
                    continue
                left_present = key in left
                right_present = key in right
                if left_present and right_present:
                    if left[key] != right[key]:
                        return False
                    continue
                present_value = left[key] if left_present else right[key]
                if present_value != []:
                    return False
            return True

        differing_parents = []
        for parent_path in set(previous_order).intersection(remote_order):
            old_children = comparable_children(
                parent_path, previous_order[parent_path]
            )
            new_children = comparable_children(
                parent_path, remote_order[parent_path]
            )
            if old_children == new_children:
                continue
            if len(old_children) != len(new_children):
                return []
            changed_indexes = [
                index
                for index, (old_name, new_name) in enumerate(
                    zip(old_children, new_children)
                )
                if old_name != new_name
            ]
            if len(changed_indexes) != 1:
                return []
            index = changed_indexes[0]
            differing_parents.append((
                parent_path, old_children[index], new_children[index]
            ))
        if len(differing_parents) != 1:
            return []

        parent_path, old_name, new_name = differing_parents[0]
        if parent_path == "메인/휴지통" or parent_path.startswith("메인/휴지통/"):
            return []
        old_path = cls._tree_order_child_path(parent_path, old_name)
        new_path = cls._tree_order_child_path(parent_path, new_name)
        old_key = cls._tree_path_comparison_key(old_path)
        new_key = cls._tree_path_comparison_key(new_path)
        if old_key == new_key:
            # Case-only and normalization-only renames are deliberately not
            # guessed on Windows because they require a temporary path.
            return []

        live_keys = {
            cls._tree_path_comparison_key(path)
            for path in (remote_live_document_paths or set())
        }
        if new_key in live_keys:
            return []

        old_has_entry = old_path in previous_order
        new_has_entry = new_path in remote_order
        # Empty leaf folders may be represented either by just their name in
        # the parent list or by an additional ``folder/path: []`` entry.  iPad
        # legitimately omits that redundant empty-list key, while Windows
        # usually persists it.  Treat either representation as equivalent, but
        # only when every present leaf entry is explicitly empty.
        if old_has_entry and previous_order[old_path] != []:
            return []
        if new_has_entry and remote_order[new_path] != []:
            return []
        if any(
            key.startswith(old_path + "/") for key in previous_order
        ) or any(key.startswith(new_path + "/") for key in remote_order):
            return []

        transformed = copy.deepcopy(previous_order)
        old_indexes = [
            index for index, name in enumerate(transformed[parent_path])
            if name == old_name
        ]
        if len(old_indexes) != 1:
            return []
        transformed[parent_path][old_indexes[0]] = new_name
        if old_has_entry:
            transformed.pop(old_path)
        if new_has_entry:
            transformed[new_path] = []
        if not equivalent_except_optional_empty_leaves(
            transformed, remote_order
        ):
            # Any second addition, removal, reorder or rename makes pairing
            # ambiguous, so retain the conservative mkdir-only behavior.
            return []

        root = os.path.abspath(writing_root)
        old_full = os.path.abspath(os.path.join(root, old_path))
        new_full = os.path.abspath(os.path.join(root, new_path))
        try:
            if (
                os.path.commonpath([root, old_full]) != root
                or os.path.commonpath([root, new_full]) != root
                or len(old_full.encode("utf-16-le")) // 2
                > MAX_WINDOWS_DIRECTORY_PATH
                or len(new_full.encode("utf-16-le")) // 2
                > MAX_WINDOWS_DIRECTORY_PATH
                or not cls._safe_existing_tree_directory(root, old_path)
                or not cls._safe_existing_tree_directory(
                    root, old_path.rpartition("/")[0]
                )
                or os.path.lexists(new_full)
            ):
                return []
            with os.scandir(old_full) as entries:
                if next(entries, None) is not None:
                    return []
        except (OSError, ValueError, FileExistsError):
            return []

        return [{
            "old_relative_path": old_path,
            "new_relative_path": new_path,
            "old_full_path": old_full,
            "new_full_path": new_full,
        }]

    @classmethod
    def _apply_remote_empty_folder_rename(cls, writing_root, item):
        """Apply a prevalidated rename after repeating all mutable checks."""
        root = os.path.abspath(writing_root)
        old_path = item["old_relative_path"]
        old_full = item["old_full_path"]
        new_full = item["new_full_path"]
        try:
            if (
                not cls._safe_existing_tree_directory(root, old_path)
                or os.path.lexists(new_full)
            ):
                return False
            with os.scandir(old_full) as entries:
                if next(entries, None) is not None:
                    return False
            os.rename(old_full, new_full)
            return True
        except (OSError, ValueError, FileExistsError):
            return False

    def _rollback_remote_empty_folder_renames(self, writing_root, renamed_items):
        """Put the renamed empty directories back, identity along with them.

        The undo is the same prevalidated rename read backwards, so a directory
        that stopped being safe to move in the meantime is left alone.
        """
        for item in reversed(renamed_items or ()):
            try:
                self._relocate_remote_empty_folder({
                    "old_relative_path": item["new_relative_path"],
                    "new_relative_path": item["old_relative_path"],
                    "old_full_path": item["new_full_path"],
                    "new_full_path": item["old_full_path"],
                })
            except Exception:
                # A rollback runs inside a raise. It reports nothing of its own.
                pass

    def _save_remote_tree_order_settings(self, tree_order):
        self._v2_wpm.project_settings["tree_order"] = tree_order
        if self._v2_wpm.save_settings() is False:
            raise OSError("REMOTE_DOCUMENT_WRITE_FAILED")

    @classmethod
    def _preserve_equivalent_tree_parent_orders(
        cls, preferred_order, remote_order
    ):
        """Keep preferred sibling order only where both snapshots name the same entries."""
        result = copy.deepcopy(remote_order)
        for parent_path, remote_children in remote_order.items():
            if is_fixed_manuscript_parent(parent_path):
                continue
            preferred_children = preferred_order.get(parent_path)
            if not isinstance(preferred_children, list):
                continue
            preferred_keys = [
                cls._tree_path_comparison_key(
                    cls._tree_order_child_path(parent_path, name)
                )
                for name in preferred_children
            ]
            remote_keys = [
                cls._tree_path_comparison_key(
                    cls._tree_order_child_path(parent_path, name)
                )
                for name in remote_children
            ]
            if (
                len(preferred_keys) == len(set(preferred_keys))
                and len(remote_keys) == len(set(remote_keys))
                and set(preferred_keys) == set(remote_keys)
            ):
                result[parent_path] = copy.deepcopy(preferred_children)
        return result

    @classmethod
    def _stable_tree_order_for_remote_document_renames(
        cls, local_order, remote_order, document_changes
    ):
        """Preserve sibling order around proven same-parent UUID renames.

        Some legacy clients rebuild the whole tree-order document from an
        unordered document collection while renaming one document.  The stable
        document UUID proves the rename, but not an unrelated reorder.  Only in
        that narrow case do we substitute the new leaf at the old position and
        retain other equivalent parent lists.
        """
        if not isinstance(local_order, dict) or not document_changes:
            return None
        try:
            preferred_order = cls._validated_remote_tree_order(local_order)
        except (TypeError, ValueError, OSError, FileExistsError):
            return None

        applied_rename = False
        for change in document_changes:
            if change.get("is_deleted"):
                continue
            old_path = change.get("old_local_path")
            new_path = change.get("new_local_path")
            if not old_path or not new_path:
                continue
            try:
                old_path = cls._safe_relative_path(old_path)
                new_path = cls._safe_relative_path(new_path)
            except ValueError:
                continue
            if old_path == new_path:
                continue
            old_parent, _, old_name = old_path.rpartition("/")
            new_parent, _, new_name = new_path.rpartition("/")
            if not old_parent or old_parent != new_parent:
                continue
            children = preferred_order.get(old_parent)
            if not isinstance(children, list):
                continue
            old_key = cls._tree_path_comparison_key(old_path)
            new_key = cls._tree_path_comparison_key(new_path)
            old_indexes = [
                index
                for index, name in enumerate(children)
                if cls._tree_path_comparison_key(
                    cls._tree_order_child_path(old_parent, name)
                ) == old_key
            ]
            if len(old_indexes) != 1 or any(
                cls._tree_path_comparison_key(
                    cls._tree_order_child_path(old_parent, name)
                ) == new_key
                for name in children
            ):
                continue
            children[old_indexes[0]] = new_name
            applied_rename = True

        if not applied_rename:
            return None
        return cls._preserve_equivalent_tree_parent_orders(
            preferred_order, remote_order
        )

    def _merge_existing_orders_omitted_by_remote(
        self, local_order, remote_order
    ):
        """Retain or derive orders for existing directories omitted by a legacy snapshot."""
        if not isinstance(local_order, dict):
            local_order = {}
        try:
            validated_local = self._validated_remote_tree_order(local_order)
        except (TypeError, ValueError, OSError, FileExistsError):
            validated_local = {}
        merged = copy.deepcopy(remote_order)
        root = os.path.abspath(self._v2_wpm.writing_root_path)

        def natural_key(name):
            return tuple(
                (0, int(part)) if part.isdigit() else (1, part.casefold())
                for part in re.split(r"(\d+)", name)
            )

        def existing_directory(relative_path):
            try:
                full_path = os.path.abspath(os.path.join(root, relative_path))
                return bool(
                    os.path.commonpath([root, full_path]) == root
                    and os.path.isdir(full_path)
                    and not self._is_reparse_path(full_path)
                )
            except (OSError, ValueError):
                return False

        pending = {
            parent_path
            for parent_path in validated_local
            if parent_path != "<root>"
            and parent_path not in remote_order
            and parent_path != "메인/휴지통"
            and not parent_path.startswith("메인/휴지통/")
        }
        for parent_path, child_names in remote_order.items():
            for child_name in child_names:
                child_path = self._tree_order_child_path(parent_path, child_name)
                if (
                    child_path not in remote_order
                    and child_path != "메인/휴지통"
                    and not child_path.startswith("메인/휴지통/")
                    and existing_directory(child_path)
                ):
                    pending.add(child_path)

        visited = set()
        while pending:
            parent_path = min(pending, key=lambda path: (path.count("/"), path))
            pending.remove(parent_path)
            if parent_path in visited or parent_path in remote_order:
                continue
            visited.add(parent_path)
            if not existing_directory(parent_path):
                continue
            full_parent = os.path.join(root, parent_path)
            try:
                disk_names = [
                    self._validate_tree_order_component(name)
                    for name in os.listdir(full_parent)
                    if name and not name.endswith(".tmp")
                ]
            except (OSError, ValueError):
                continue
            if not disk_names and parent_path not in validated_local:
                # Do not manufacture a new empty-list key on the second replay
                # merely because the first application materialized an empty
                # folder named by its parent list.
                continue
            disk_by_key = {}
            collision = False
            for name in disk_names:
                key = unicodedata.normalize("NFC", name).casefold()
                if key in disk_by_key:
                    collision = True
                    break
                disk_by_key[key] = name
            if collision:
                continue

            canonical_volume = re.fullmatch(
                r"메인/원고/(\d+)권", parent_path
            )
            expected_chapters = None
            if canonical_volume:
                volume_number = int(canonical_volume.group(1))
                first_chapter = (volume_number - 1) * 25 + 1
                expected_chapters = [
                    f"{number:03d}화.txt"
                    for number in range(first_chapter, first_chapter + 25)
                ]
                if {
                    unicodedata.normalize("NFC", name).casefold()
                    for name in expected_chapters
                } != set(disk_by_key):
                    expected_chapters = None

            if expected_chapters is not None:
                # This is the exact untouched 25-file set created by add_volume,
                # while the server snapshot contains no order for the volume.
                # A successful user drag would have published an explicit parent
                # list, so this narrow fallback safely repairs enumerator order.
                ordered = [
                    disk_by_key[
                        unicodedata.normalize("NFC", name).casefold()
                    ]
                    for name in expected_chapters
                ]
            else:
                ordered = []
                for saved_name in validated_local.get(parent_path, []):
                    key = unicodedata.normalize("NFC", saved_name).casefold()
                    disk_name = disk_by_key.pop(key, None)
                    if disk_name is not None:
                        ordered.append(disk_name)
                remaining = sorted(
                    disk_by_key.values(),
                    key=lambda name: (
                        not os.path.isdir(os.path.join(full_parent, name)),
                        natural_key(name),
                    ),
                )
                ordered.extend(remaining)
            merged[parent_path] = ordered

            for child_name in ordered:
                child_path = self._tree_order_child_path(
                    parent_path, child_name
                )
                if (
                    child_path not in remote_order
                    and child_path not in visited
                    and existing_directory(child_path)
                ):
                    pending.add(child_path)
        return merged

    def _apply_remote_tree_order_document(
        self,
        document_id,
        content,
        revision,
        is_deleted=False,
        remote_live_document_paths=None,
        remote_document_changes=None,
        remote_folder_paths=None,
        has_remote_folder_projection=False,
        remote_folder_ids=None,
    ):
        planned_generation = self.local_structure_generation
        if is_deleted:
            return None
        try:
            payload = json.loads(content or "{}")
        except (TypeError, json.JSONDecodeError):
            return None
        if payload.get("version") != 1:
            return None
        raw_order = payload.get("tree_order")
        if not isinstance(raw_order, dict) or not raw_order:
            return None
        remote_order = self._validated_remote_tree_order(raw_order)
        remote_order = {
            key: value
            for key, value in remote_order.items()
            if key != "메인/휴지통" and not key.startswith("메인/휴지통/")
        }
        if not remote_order:
            return None
        explicit_tree_folder_paths = self._validated_remote_tree_folder_paths(
            payload.get("folder_paths"), remote_order
        )
        with self._structure_mutation_gate:
            if self._local_structure_generation != planned_generation:
                return None
            display_order = self._stable_tree_order_for_remote_document_renames(
                getattr(self._v2_wpm, "project_settings", {}).get(
                    "tree_order", {}
                ),
                remote_order,
                remote_document_changes,
            )
            return self._apply_remote_tree_order_document_locked(
                document_id,
                content,
                revision,
                remote_order,
                remote_live_document_paths=remote_live_document_paths,
                display_order=display_order,
                remote_folder_paths=remote_folder_paths,
                explicit_tree_folder_paths=explicit_tree_folder_paths,
                has_remote_folder_projection=has_remote_folder_projection,
                remote_folder_ids=remote_folder_ids,
            )

    def _apply_remote_tree_order_document_locked(
        self,
        document_id,
        content,
        revision,
        remote_order,
        remote_live_document_paths=None,
        display_order=None,
        remote_folder_paths=None,
        explicit_tree_folder_paths=None,
        has_remote_folder_projection=False,
        remote_folder_ids=None,
    ):
        folder_plan = self._build_remote_tree_folder_plan(
            self._v2_wpm.writing_root_path,
            remote_order,
            remote_live_document_paths,
            remote_folder_paths,
            explicit_tree_folder_paths,
            has_remote_folder_projection,
        )

        existing = self._v2_store.get_document_by_id(document_id)
        existing_revision = int((existing or {}).get("revision") or 0)
        has_local_tree_operations = bool(
            existing and self._v2_store.has_active_operations(document_id)
        )
        baseline_already_applied = bool(
            existing
            and revision == existing_revision
            and existing.get("base_content") == content
            and not has_local_tree_operations
        )
        if existing and revision <= existing_revision and not baseline_already_applied:
            return None

        local_order = getattr(self._v2_wpm, "project_settings", {}).get(
            "tree_order", {}
        )
        effective_order = display_order or remote_order
        if (
            baseline_already_applied
            and display_order is None
            and isinstance(local_order, dict)
        ):
            try:
                validated_local_order = self._validated_remote_tree_order(
                    local_order
                )
                effective_order = self._preserve_equivalent_tree_parent_orders(
                    validated_local_order, remote_order
                )
            except (TypeError, ValueError, OSError, FileExistsError):
                effective_order = remote_order
        effective_order = self._merge_existing_orders_omitted_by_remote(
            local_order, effective_order
        )
        merged_order = dict(effective_order)
        if isinstance(local_order, dict):
            merged_order.update({
                key: copy.deepcopy(value)
                for key, value in local_order.items()
                if key == "메인/휴지통" or key.startswith("메인/휴지통/")
            })
        if (
            baseline_already_applied
            and local_order == merged_order
            and all(item["exists"] for item in folder_plan)
        ):
            return None

        had_tree_order = "tree_order" in self._v2_wpm.project_settings
        previous_tree_order = copy.deepcopy(local_order)
        rename_plan = []
        if (
            existing
            and revision > existing_revision
            and not has_local_tree_operations
            and isinstance(local_order, dict)
        ):
            try:
                previous_payload = json.loads(existing.get("base_content") or "{}")
                previous_raw_order = previous_payload.get("tree_order")
                if (
                    previous_payload.get("version") != 1
                    or not isinstance(previous_raw_order, dict)
                    or not previous_raw_order
                ):
                    raise ValueError("INVALID_REMOTE_PATH")
                previous_remote_order = self._validated_remote_tree_order(
                    previous_raw_order
                )
                previous_remote_order = {
                    key: value
                    for key, value in previous_remote_order.items()
                    if key != "메인/휴지통"
                    and not key.startswith("메인/휴지통/")
                }
                local_remote_order = {
                    key: copy.deepcopy(value)
                    for key, value in local_order.items()
                    if key != "메인/휴지통"
                    and not key.startswith("메인/휴지통/")
                }
                if (
                    local_remote_order == previous_remote_order
                ):
                    rename_plan = self._build_remote_empty_folder_rename_plan(
                        self._v2_wpm.writing_root_path,
                        previous_remote_order,
                        remote_order,
                        remote_live_document_paths,
                    )
            except (TypeError, ValueError, OSError, json.JSONDecodeError):
                rename_plan = []
        # The compatibility merge above preserves local parent entries that a
        # legacy snapshot omits.  Once a rename is proven, however, retaining
        # the old empty leaf key would leave settings pointing at the previous
        # name even though the directory itself is renamed.  Mirror the remote
        # representation exactly for the proven pair.
        for item in rename_plan:
            old_path = item["old_relative_path"]
            new_path = item["new_relative_path"]
            merged_order.pop(old_path, None)
            if new_path in remote_order:
                merged_order[new_path] = copy.deepcopy(remote_order[new_path])
        created_paths = []
        renamed_items = []
        adopted_nodes = []
        settings_save_attempted = False
        try:
            for item in rename_plan:
                if self._relocate_remote_empty_folder(item):
                    renamed_items.append(item)
            adopted_nodes = self._adopt_remote_tree_folders(
                folder_plan, renamed_items, remote_folder_ids
            )
            # Adoption issues the ids before the directories exist, so the
            # directories it creates roll back with everything else this
            # snapshot made.
            adopted_paths = {node["legacy_path"] for node in adopted_nodes}
            created_paths.extend(
                item["full_path"]
                for item in folder_plan
                if not item["exists"] and item["relative_path"] in adopted_paths
            )
            for item in folder_plan:
                if item["exists"] or any(
                    item["full_path"] == renamed["new_full_path"]
                    for renamed in renamed_items
                ):
                    continue
                try:
                    os.mkdir(item["full_path"])
                    created_paths.append(item["full_path"])
                except FileExistsError:
                    if not self._safe_existing_tree_directory(
                        self._v2_wpm.writing_root_path, item["relative_path"]
                    ):
                        raise

            settings_save_attempted = True
            self._save_remote_tree_order_settings(merged_order)
            if not baseline_already_applied:
                applied = self._v2_store.apply_remote_snapshot(
                    self._v2_context,
                    document_id,
                    TREE_ORDER_DOCUMENT_PATH,
                    content,
                    revision,
                    is_deleted=False,
                    local_path=TREE_ORDER_DOCUMENT_PATH,
                )
                if not applied.get("applied"):
                    raise RuntimeError(
                        "REMOTE_SNAPSHOT_APPLY_FAILED:"
                        + str(applied.get("reason") or "unknown")
                    )
        except Exception:
            if had_tree_order:
                self._v2_wpm.project_settings["tree_order"] = previous_tree_order
            else:
                self._v2_wpm.project_settings.pop("tree_order", None)
            if settings_save_attempted:
                try:
                    self._v2_wpm.save_settings()
                except Exception:
                    pass
            try:
                self._release_adopted_identity(
                    adopted_nodes,
                    lambda: self._rollback_remote_tree_folders(
                        self._v2_wpm.writing_root_path, created_paths
                    ),
                )
            except Exception:
                # The journal this left behind is the recovery path. Never let
                # it replace the failure that started the rollback.
                pass
            self._rollback_remote_empty_folder_renames(
                self._v2_wpm.writing_root_path, renamed_items
            )
            raise

        return {
            "kind": "tree_order",
            "document_id": document_id,
            "old_local_path": TREE_ORDER_DOCUMENT_PATH,
            "new_local_path": TREE_ORDER_DOCUMENT_PATH,
            "remote_path": TREE_ORDER_DOCUMENT_PATH,
            "content": content,
            "revision": revision,
            "is_deleted": False,
        }

    def _apply_remote_trash_purge_document(
        self, document_id, content, revision, is_deleted=False
    ):
        if is_deleted:
            return None
        try:
            payload = json.loads(content or "{}")
        except (TypeError, json.JSONDecodeError):
            return None
        remote_purges = self._normalized_trash_purges(
            payload.get("purged_revisions")
        )
        empty_generation = str(payload.get("empty_generation") or "")
        applied = self._v2_store.apply_remote_snapshot(
            self._v2_context,
            document_id,
            TRASH_PURGE_DOCUMENT_PATH,
            content,
            revision,
            is_deleted=False,
            local_path=TRASH_PURGE_DOCUMENT_PATH,
        )
        if not applied.get("applied"):
            return None

        local_purges = self._normalized_trash_purges(
            self._v2_wpm.project_settings.get("trash_purged_revisions", {})
        )
        for purged_id, purged_revision in remote_purges.items():
            local_purges[purged_id] = max(
                local_purges.get(purged_id, 0), purged_revision
            )

        previous_generation = str(
            self._v2_wpm.project_settings.get("trash_empty_generation", "")
        )
        if empty_generation and empty_generation != previous_generation:
            self._v2_wpm.empty_trash()
            self._v2_wpm.project_settings["trash_empty_generation"] = empty_generation

        self._v2_wpm.project_settings["trash_purged_revisions"] = local_purges
        self._v2_wpm.save_settings()

        for document in self._v2_store.list_documents(
            self._v2_context["local_key"]
        ):
            purged_revision = local_purges.get(document["document_id"], 0)
            if not document.get("is_deleted") or purged_revision < int(
                document.get("revision") or 0
            ):
                continue
            local_path = str(document.get("local_path") or "")
            if local_path.startswith("메인/휴지통/"):
                self._v2_wpm.delete_from_trash(local_path)
            self._v2_store.relocate_deleted_document(
                document["document_id"],
                f"__antigravity__/purged/{document['document_id']}",
            )

        return {
            "kind": "trash_purge",
            "document_id": document_id,
            "old_local_path": TRASH_PURGE_DOCUMENT_PATH,
            "new_local_path": TRASH_PURGE_DOCUMENT_PATH,
            "remote_path": TRASH_PURGE_DOCUMENT_PATH,
            "content": content,
            "revision": revision,
            "is_deleted": False,
        }

    @classmethod
    def _remote_tree_folder_paths(cls, remote_documents, live_document_paths):
        """Return the folders named by the newest usable remote tree snapshot."""
        tree_rows = [
            item for item in (remote_documents or [])
            if not item.get("is_deleted")
            and str(item.get("relative_path") or "").replace("\\", "/")
            == TREE_ORDER_DOCUMENT_PATH
        ]
        if not tree_rows:
            return None
        tree_row = max(tree_rows, key=lambda item: int(item.get("revision") or 0))
        try:
            payload = json.loads(tree_row.get("content") or "{}")
            if payload.get("version") != 1:
                return None
            tree_order = cls._validated_remote_tree_order(payload.get("tree_order"))
        except (TypeError, ValueError, OSError, json.JSONDecodeError):
            return None

        live_keys = {
            cls._tree_path_comparison_key(path)
            for path in (live_document_paths or set())
        }
        folders = set()
        for parent_path, child_names in tree_order.items():
            if parent_path != "<root>":
                folders.add(parent_path)
            for child_name in child_names:
                child_path = cls._tree_order_child_path(parent_path, child_name)
                if cls._tree_path_comparison_key(child_path) not in live_keys:
                    folders.add(child_path)
        return folders

    @staticmethod
    def _folder_move_prefixes(old_path, new_path):
        """Infer a renamed directory from one stable document's path change."""
        old_parts = str(old_path or "").split("/")
        new_parts = str(new_path or "").split("/")
        common_suffix = 0
        for old_part, new_part in zip(reversed(old_parts), reversed(new_parts)):
            if old_part != new_part:
                break
            common_suffix += 1
        # An unchanged leaf filename proves this is a parent-folder change, not
        # merely a document rename.
        if common_suffix < 1:
            return None
        old_prefix = "/".join(old_parts[:-common_suffix])
        new_prefix = "/".join(new_parts[:-common_suffix])
        if (
            not old_prefix
            or not new_prefix
            or old_prefix == new_prefix
            or old_prefix == "메인"
            or new_prefix == "메인"
        ):
            return None
        return old_prefix, new_prefix

    def _apply_remote_folder_identities(
        self,
        folder_rows,
        folder_versions,
        remote_documents,
        protected_paths,
    ):
        """Apply exact folder_id renames before any child document is moved.

        A pull can observe document commits before the matching tree-order commit.
        In that partial state we defer the whole pull.  Once both projections agree,
        the directory is renamed once, preserving every tracked and untracked byte.
        """
        if not folder_rows:
            return {"blocked": False, "changes": []}
        resolved = self._folder_rows_with_tree_paths(folder_rows)
        live_document_paths = set()
        for item in remote_documents or []:
            if item.get("is_deleted"):
                continue
            try:
                path = self._safe_relative_path(item.get("relative_path"))
            except ValueError:
                continue
            if is_live_document_path(path):
                live_document_paths.add(path)
        remote_folders = self._remote_tree_folder_paths(
            remote_documents,
            live_document_paths,
        )
        if remote_folders is None:
            return {"blocked": True, "changes": []}
        remote_folder_keys = {
            self._tree_path_comparison_key(path) for path in remote_folders
        }
        fixed_root_keys = {
            self._tree_path_comparison_key(f"메인/{storage_name}")
            for storage_name in set(TREE_ROOT_STORAGE_NAMES.values())
        }
        stored = {
            str(item["folder_id"]): item
            for item in self._v2_store.list_folders(
                self._v2_context["local_key"]
            )
        }
        # ``folders`` is the stable identity projection. If tree-order omits a
        # user-created identified folder, the two server projections are from
        # different structural moments. Fixed application roots are exempt:
        # iPad intentionally omits some of them (notably trash), and presents
        # aliases such as ``스토리 플롯`` for the Windows ``플롯`` directory.
        # If tree-order is one step ahead of an otherwise unchanged stored
        # folder row, defer only tree-order. Other folder IDs and documents can
        # still be applied safely. A new/changed folder row missing from the
        # tree remains a hard partial-snapshot block.
        defer_tree_order = False
        for folder_id, item in resolved.items():
            item_key = self._tree_path_comparison_key(item["local_path"])
            if (
                item["local_path"] == "메인"
                or item_key in remote_folder_keys
                or item_key in fixed_root_keys
            ):
                continue
            previous = stored.get(folder_id)
            if (
                previous
                and int(previous.get("revision") or 0)
                == int(item.get("revision") or 0)
                and self._tree_path_comparison_key(previous.get("local_path"))
                == item_key
            ):
                defer_tree_order = True
                continue
            return {"blocked": True, "changes": []}
        versions_by_id = {}
        for version in folder_versions or []:
            if not isinstance(version, dict) or not version.get("folder_id"):
                continue
            versions_by_id.setdefault(str(version["folder_id"]), []).append(
                version
            )
        for versions in versions_by_id.values():
            versions.sort(key=lambda item: int(item.get("revision") or 0))

        local_documents = self._v2_store.list_documents(
            self._v2_context["local_key"]
        )
        remote_by_id = {
            str(item.get("document_id") or ""): item
            for item in (remote_documents or [])
        }
        # Dirty/protected editors must block a directory move. Clean active
        # editors are path-remapped by the UI after the exact folder-ID change;
        # blocking them here would leave every inbound nonempty rename pending
        # for as long as the document remained open.
        guarded_paths = set(protected_paths or set())
        tree_document = self._v2_store.get_document(
            self._v2_context["local_key"], TREE_ORDER_DOCUMENT_PATH
        )
        tree_has_local_work = bool(
            tree_document
            and self._v2_store.has_active_operations(
                tree_document["document_id"]
            )
        )

        candidates = []
        for folder_id, current in resolved.items():
            current_revision = int(current.get("revision") or 0)
            new_path = current["local_path"]
            old_path = None
            old_revision = 0
            previous = stored.get(folder_id)
            if previous and int(previous.get("revision") or 0) < current_revision:
                old_path = self._safe_relative_path(previous.get("local_path"))
                old_revision = int(previous.get("revision") or 0)
            elif previous is None:
                history = [
                    item for item in versions_by_id.get(folder_id, [])
                    if 0 < int(item.get("revision") or 0) < current_revision
                    and not item.get("is_deleted")
                ]
                if history:
                    prior = history[-1]
                    # History bootstrapping is limited to a same-parent rename.
                    # A cross-parent move needs a previously persisted path.
                    if str(prior.get("parent_folder_id") or "") == str(
                        current.get("parent_folder_id") or ""
                    ):
                        parent_path = new_path.rpartition("/")[0]
                        try:
                            prior_name = unicodedata.normalize(
                                "NFC", str(prior.get("name") or "")
                            )
                            self._validate_tree_order_component(prior_name)
                        except (TypeError, ValueError):
                            prior_name = ""
                        if prior_name and parent_path:
                            old_path = f"{parent_path}/{prior_name}"
                            old_revision = int(prior.get("revision") or 0)
            if not old_path or old_path == new_path:
                continue
            if self._tree_path_comparison_key(old_path) == self._tree_path_comparison_key(
                new_path
            ):
                # Case-only/NFC-only Windows renames require a separate two-step
                # transaction and are intentionally not inferred here.
                continue
            candidates.append({
                "folder_id": folder_id,
                "old_path": old_path,
                "new_path": new_path,
                "old_revision": old_revision,
                "new_revision": current_revision,
            })

        # A parent rename already carries every descendant directory with it.
        actions = []
        for candidate in sorted(
            candidates, key=lambda item: item["old_path"].count("/")
        ):
            if any(
                candidate["old_path"].startswith(parent["old_path"] + "/")
                and candidate["new_path"].startswith(parent["new_path"] + "/")
                for parent in actions
            ):
                continue
            actions.append(candidate)

        root = os.path.abspath(self._v2_wpm.writing_root_path)
        planned = []
        for action in actions:
            old_path = action["old_path"]
            new_path = action["new_path"]
            old_key = self._tree_path_comparison_key(old_path)
            new_key = self._tree_path_comparison_key(new_path)
            # A folders row can arrive before its tree-order document. Never let
            # child document rows split the directory during that partial pull.
            if old_key in remote_folder_keys or new_key not in remote_folder_keys:
                return {"blocked": True, "changes": []}
            if tree_has_local_work:
                return {"blocked": True, "changes": []}
            members = [
                item for item in local_documents
                if not item.get("is_deleted")
                and str(item.get("local_path") or "").startswith(old_path + "/")
                and is_live_document_path(item.get("local_path"))
            ]
            for member in members:
                member_path = self._safe_relative_path(member["local_path"])
                remote = remote_by_id.get(str(member["document_id"]))
                try:
                    remote_path = self._safe_relative_path(
                        (remote or {}).get("relative_path")
                    )
                except ValueError:
                    remote_path = ""
                if remote_path.startswith(old_path + "/"):
                    remote_path = new_path + remote_path[len(old_path):]
                if (
                    not remote
                    or remote.get("is_deleted")
                    or not remote_path.startswith(new_path + "/")
                    or self._v2_store.has_active_operations(member["document_id"])
                ):
                    return {"blocked": True, "changes": []}

            old_full = os.path.abspath(os.path.join(root, old_path))
            new_full = os.path.abspath(os.path.join(root, new_path))
            new_parent = new_path.rpartition("/")[0]
            try:
                if (
                    os.path.commonpath([root, old_full]) != root
                    or os.path.commonpath([root, new_full]) != root
                    or not self._safe_existing_tree_directory(root, new_parent)
                ):
                    return {"blocked": True, "changes": []}
                old_exists = os.path.lexists(old_full)
                new_exists = os.path.lexists(new_full)
                if old_exists and not self._safe_existing_tree_directory(
                    root, old_path
                ):
                    return {"blocked": True, "changes": []}
                if new_exists and not self._safe_existing_tree_directory(
                    root, new_path
                ):
                    return {"blocked": True, "changes": []}
                if old_exists and new_exists:
                    with os.scandir(old_full) as entries:
                        if next(entries, None) is not None:
                            return {"blocked": True, "changes": []}
                    action_kind = "remove_empty_source"
                elif old_exists:
                    action_kind = "rename"
                else:
                    action_kind = "already_applied"
                if action_kind == "already_applied":
                    guarded_prefixes = ()
                elif action_kind == "remove_empty_source":
                    guarded_prefixes = (old_path,)
                else:
                    guarded_prefixes = (old_path, new_path)
                if any(
                    path == prefix or path.startswith(prefix + "/")
                    for path in guarded_paths
                    for prefix in guarded_prefixes
                ):
                    return {"blocked": True, "changes": []}
                if self._v2_store.move_destination_conflicts(
                    self._v2_context["local_key"], old_path, new_path
                ):
                    return {"blocked": True, "changes": []}
                planned.append({**action, "kind": action_kind})
            except (OSError, ValueError):
                return {"blocked": True, "changes": []}

        applied_actions = []
        moved_document_prefixes = []
        try:
            for action in planned:
                old_full = os.path.join(root, action["old_path"])
                new_full = os.path.join(root, action["new_path"])
                if action["kind"] == "rename":
                    self._relocate_remote_identity(
                        action["old_path"],
                        action["new_path"],
                        lambda old_full=old_full, new_full=new_full: os.rename(
                            old_full, new_full
                        ),
                    )
                elif action["kind"] == "remove_empty_source":
                    # The directory is already at the new path. Only a leftover
                    # empty source is dropped, and identity follows it if it is
                    # the node that still names the old path.
                    self._relocate_remote_identity(
                        action["old_path"],
                        action["new_path"],
                        lambda old_full=old_full: os.rmdir(old_full),
                    )
                applied_actions.append(action)
                self._v2_store.move_local_path(
                    self._v2_context["local_key"],
                    action["old_path"],
                    action["new_path"],
                )
                moved_document_prefixes.append(action)
            self._v2_store.replace_folder_snapshots(
                self._v2_context["local_key"],
                list(resolved.values()),
            )
        except Exception:
            for action in reversed(moved_document_prefixes):
                try:
                    self._v2_store.move_local_path(
                        self._v2_context["local_key"],
                        action["new_path"],
                        action["old_path"],
                    )
                except Exception:
                    pass
            for action in reversed(applied_actions):
                old_full = os.path.join(root, action["old_path"])
                new_full = os.path.join(root, action["new_path"])
                try:
                    if action["kind"] == "rename" and not os.path.lexists(old_full):
                        self._relocate_remote_identity(
                            action["new_path"],
                            action["old_path"],
                            lambda: os.rename(new_full, old_full),
                        )
                    elif action["kind"] == "remove_empty_source" and not os.path.lexists(old_full):
                        os.mkdir(old_full)
                except Exception:
                    pass
            raise

        return {
            "blocked": False,
            "defer_tree_order": defer_tree_order,
            "changes": [
                {
                    "kind": "folder_identity_rename",
                    "folder_id": action["folder_id"],
                    "old_local_path": action["old_path"],
                    "new_local_path": action["new_path"],
                    "revision": action["new_revision"],
                }
                for action in planned
                if action["kind"] != "already_applied"
            ],
            "path_projections": [
                {
                    "old_path": action["old_path"],
                    "new_path": action["new_path"],
                }
                for action in planned
            ],
        }

    def _project_remote_documents_through_folder_identities(
        self, remote_documents, path_projections
    ):
        """Project stale document paths through exact folder-ID renames.

        ``commit_folder`` changes the stable folder projection independently
        from child ``commit_document`` calls.  Preserve the raw server path in
        metadata while applying the authoritative folder prefix locally.
        """
        projections = []
        for item in path_projections or []:
            try:
                old_path = self._safe_relative_path(item.get("old_path"))
                new_path = self._safe_relative_path(item.get("new_path"))
            except (AttributeError, ValueError):
                continue
            if old_path and new_path and old_path != new_path:
                projections.append((old_path, new_path))
        projections.sort(key=lambda pair: pair[0].count("/"), reverse=True)

        projected_documents = []
        for remote in remote_documents or []:
            if not isinstance(remote, dict) or remote.get("is_deleted"):
                projected_documents.append(remote)
                continue
            try:
                raw_path = self._safe_relative_path(remote.get("relative_path"))
            except ValueError:
                projected_documents.append(remote)
                continue
            if not is_live_document_path(raw_path):
                projected_documents.append(remote)
                continue

            projected_path = raw_path
            local_document = None
            try:
                local_document = self._v2_store.get_document_by_id(
                    str(uuid.UUID(str(remote.get("document_id"))))
                )
            except (TypeError, ValueError):
                pass
            if local_document:
                try:
                    server_path = self._safe_relative_path(
                        local_document.get("server_path")
                    )
                    local_path = self._safe_relative_path(
                        local_document.get("local_path")
                    )
                    stored_projection = self._folder_move_prefixes(
                        server_path, local_path
                    )
                except ValueError:
                    stored_projection = None
                if stored_projection:
                    old_prefix, new_prefix = stored_projection
                    if projected_path.startswith(old_prefix + "/"):
                        projected_path = (
                            new_prefix + projected_path[len(old_prefix):]
                        )

            if projected_path == raw_path:
                for old_prefix, new_prefix in projections:
                    if projected_path.startswith(old_prefix + "/"):
                        projected_path = (
                            new_prefix + projected_path[len(old_prefix):]
                        )
                        break

            if projected_path == raw_path:
                projected_documents.append(remote)
            else:
                projected_documents.append({
                    **remote,
                    "relative_path": projected_path,
                    "_server_relative_path": raw_path,
                })
        return projected_documents

    def _move_remote_renamed_folders(
        self,
        remote_documents,
        remote_live_document_paths,
        protected_paths,
    ):
        """Move a complete clean folder once before applying its document rows.

        Folder identity is represented indirectly by tree order. Stable document
        IDs let us distinguish a real folder rename from an unrelated delete and
        create. We only move when every tracked live document below the old folder
        maps to the same new prefix and the remote tree confirms the old folder is
        gone. Otherwise the existing per-document conflict-safe path is retained.
        """
        from project_creation_v1 import CreationError
        from project_identity_v1 import IdentityError

        remote_folders = self._remote_tree_folder_paths(
            remote_documents, remote_live_document_paths
        )
        if remote_folders is None:
            return []
        remote_folder_keys = {
            self._tree_path_comparison_key(path) for path in remote_folders
        }
        remote_by_id = {
            str(item.get("document_id") or ""): item
            for item in (remote_documents or [])
        }
        local_documents = self._v2_store.list_documents(
            self._v2_context["local_key"]
        )
        local_by_id = {
            str(item.get("document_id") or ""): item
            for item in local_documents
        }

        proposed = {}
        for document_id, remote in remote_by_id.items():
            local = local_by_id.get(document_id)
            if not local or local.get("is_deleted") or remote.get("is_deleted"):
                continue
            try:
                old_path = self._safe_relative_path(local.get("local_path"))
                new_path = self._safe_relative_path(remote.get("relative_path"))
            except ValueError:
                continue
            if old_path == new_path:
                continue
            prefixes = self._folder_move_prefixes(old_path, new_path)
            if prefixes:
                proposed.setdefault(prefixes[0], set()).add(prefixes[1])

        root = os.path.abspath(self._v2_wpm.writing_root_path)
        moved = []
        for old_prefix in sorted(proposed, key=lambda path: path.count("/")):
            destinations = proposed[old_prefix]
            if len(destinations) != 1:
                continue
            new_prefix = next(iter(destinations))
            old_key = self._tree_path_comparison_key(old_prefix)
            new_key = self._tree_path_comparison_key(new_prefix)
            if old_key in remote_folder_keys or new_key not in remote_folder_keys:
                continue
            if any(
                old_prefix == previous_old
                or old_prefix.startswith(previous_old + "/")
                for previous_old, _ in moved
            ):
                continue

            members = [
                item for item in local_documents
                if not item.get("is_deleted")
                and str(item.get("local_path") or "").startswith(old_prefix + "/")
                and is_live_document_path(item.get("local_path"))
            ]
            if not members:
                continue
            complete_mapping = True
            for member in members:
                remote = remote_by_id.get(str(member.get("document_id") or ""))
                try:
                    member_path = self._safe_relative_path(
                        member.get("local_path")
                    )
                    remote_path = self._safe_relative_path(
                        (remote or {}).get("relative_path")
                    )
                except ValueError:
                    complete_mapping = False
                    break
                expected_path = new_prefix + member_path[len(old_prefix):]
                if (
                    not remote
                    or remote.get("is_deleted")
                    or remote_path != expected_path
                    or self._v2_store.has_active_operations(member["document_id"])
                    or member_path in protected_paths
                    or expected_path in protected_paths
                ):
                    complete_mapping = False
                    break
            if not complete_mapping:
                continue

            old_full = os.path.abspath(os.path.join(root, old_prefix))
            new_full = os.path.abspath(os.path.join(root, new_prefix))
            new_parent = new_prefix.rpartition("/")[0]
            try:
                if (
                    os.path.commonpath([root, old_full]) != root
                    or os.path.commonpath([root, new_full]) != root
                    or not self._safe_existing_tree_directory(root, old_prefix)
                    or not self._safe_existing_tree_directory(root, new_parent)
                    or os.path.lexists(new_full)
                ):
                    continue
            except (ValueError, FileExistsError):
                continue
            try:
                self._relocate_remote_identity(
                    old_prefix,
                    new_prefix,
                    lambda: os.rename(old_full, new_full),
                )
            except (OSError, ValueError, CreationError, IdentityError):
                continue
            moved.append((old_prefix, new_prefix))
        return moved

    def _contract_tree_order_from_snapshots(self, snapshots):
        """Resolve server UUID ordering into the path/name order used by the UI."""
        order = {}
        local_key = self._v2_context["local_key"]
        for snapshot in snapshots or []:
            parent_id = snapshot.get("parent_folder_id")
            if parent_id:
                parent = self._v2_store.get_folder_by_id(parent_id)
                if parent is None or parent.get("local_key") != local_key:
                    raise SyncContractError("TREE_REFERENCE_NOT_FOUND")
                parent_path = parent["local_path"]
            else:
                parent_path = "<root>"
            child_names = []
            for child_id in snapshot.get("children") or []:
                child = self._v2_store.get_folder_by_id(child_id)
                if child is None:
                    child = self._v2_store.get_document_by_id(child_id)
                if child is None or child.get("local_key") != local_key:
                    raise SyncContractError("TREE_REFERENCE_NOT_FOUND")
                child_path = self._safe_relative_path(child["local_path"])
                actual_parent = (
                    child_path.rsplit("/", 1)[0] if "/" in child_path else "<root>"
                )
                expected_parent = "메인" if parent_path == "<root>" else parent_path
                if actual_parent != expected_parent:
                    raise SyncContractError("TREE_PARENT_MISMATCH")
                child_names.append(child_path.rsplit("/", 1)[-1])
            order[parent_path] = child_names
        return self._validated_remote_tree_order(order) if order else {}

    def _apply_contract_tree_order_snapshots(self, snapshots):
        if not snapshots:
            return None
        remote_order = self._contract_tree_order_from_snapshots(snapshots)
        local_order = getattr(self._v2_wpm, "project_settings", {}).get(
            "tree_order", {}
        )
        merged_order = copy.deepcopy(remote_order)
        if isinstance(local_order, dict):
            merged_order.update({
                key: copy.deepcopy(value)
                for key, value in local_order.items()
                if key == "메인/휴지통" or key.startswith("메인/휴지통/")
            })
        if local_order == merged_order:
            return None
        self._save_remote_tree_order_settings(merged_order)
        return {
            "kind": "tree_order",
            "revision": max(int(item.get("revision") or 0) for item in snapshots),
            "tree_order_ids": [item["tree_order_id"] for item in snapshots],
        }

    def _apply_v2_remote_documents(
        self,
        remote_documents,
        strict=False,
        folder_rows=None,
        folder_versions=None,
        tree_order_rows=None,
    ):
        if (
            not self.is_v2_enabled
            or not self._v2_wpm
            or not getattr(self._v2_wpm, "writing_root_path", None)
        ):
            # Without a root there is nowhere to apply anything, and every path
            # below starts by making one absolute.
            return []
        from project_creation_v1 import CreationError
        from project_identity_v1 import IdentityError

        self._v2_last_pull_apply_blocked = False
        self._v2_identity_apply_failed = False
        self._v2_identity_uuid_conflicts = []
        try:
            protected = set(
                (self._v2_protected_paths_provider or (lambda: set()))() or set()
            )
        except Exception:
            protected = set()
        protected = {
            unicodedata.normalize("NFC", path.replace("\\", "/"))
            for path in protected
            if path
        }
        active_paths = {
            unicodedata.normalize("NFC", path)
            for path in self._active_v2_paths()
            if path
        }
        changes = []
        root = os.path.abspath(self._v2_wpm.writing_root_path)
        defer_contract_tree_order = bool(
            tree_order_rows is not None
            and self._v2_store.has_active_structure_kind(
                self._v2_context["local_key"], "tree_order"
            )
        )
        # The folder projection is written before identity can follow it, so
        # the rows it replaces are kept until this apply is known to have
        # landed. A blocked pull must not leave the database describing folders
        # identity never heard of.
        previous_folder_snapshots = (
            self._v2_store.list_folders(self._v2_context["local_key"])
            if folder_rows is not None
            else None
        )

        try:
            with self._structure_mutation_gate:
                identity_result = self._apply_remote_folder_identities(
                    folder_rows,
                    folder_versions,
                    remote_documents,
                    protected,
                )
                if (
                    not identity_result.get("blocked")
                    and tree_order_rows is not None
                    and not defer_contract_tree_order
                ):
                    self._v2_store.replace_tree_order_snapshots(
                        self._v2_context["local_key"], tree_order_rows or []
                    )
        except Exception as error:
            if strict:
                raise
            print(f"Failed to apply remote folder identities: {error}")
            self._set_sync_state(
                "conflict",
                "폴더 정체성 충돌로 원격 구조 적용을 보류했습니다. "
                "로컬 파일은 변경하지 않았습니다.",
            )
            self._v2_last_pull_apply_blocked = True
            return []
        if identity_result["blocked"]:
            self._v2_last_pull_apply_blocked = True
            return []
        defer_tree_order = bool(identity_result.get("defer_tree_order"))
        changes.extend(identity_result["changes"])
        remote_documents = self._project_remote_documents_through_folder_identities(
            remote_documents,
            identity_result.get("path_projections", ()),
        )
        resolved_folder_rows = self._folder_rows_with_tree_paths(folder_rows)
        remote_folder_paths = {
            item["local_path"] for item in resolved_folder_rows.values()
        }
        # Identity adopts the peer's folder id for the paths this pull creates,
        # so the server's proof of who a directory is has to reach that far.
        remote_folder_ids = {
            self._tree_path_comparison_key(item["local_path"]): folder_id
            for folder_id, item in resolved_folder_rows.items()
        }

        remote_live_document_paths = set()
        for remote in remote_documents or []:
            if bool(remote.get("is_deleted")):
                continue
            try:
                remote_path = self._safe_relative_path(remote.get("relative_path"))
            except ValueError:
                continue
            if is_live_document_path(remote_path):
                remote_live_document_paths.add(remote_path)

        self._move_remote_renamed_folders(
            remote_documents,
            remote_live_document_paths,
            protected,
        )

        def full_path(relative_path):
            candidate = os.path.abspath(os.path.join(root, relative_path))
            if os.path.commonpath([root, candidate]) != root:
                raise ValueError("INVALID_REMOTE_PATH")
            return candidate

        # Tombstones vacate reused paths first. Live documents then create their
        # parent paths before tree-order materializes entries with no document row.
        ordered_remote_documents = sorted(
            remote_documents or [],
            key=lambda item: (
                0
                if str(item.get("relative_path") or "").replace("\\", "/")
                == TRASH_PURGE_DOCUMENT_PATH
                else (
                    1
                    if bool(item.get("is_deleted"))
                    else (
                        3
                        if str(item.get("relative_path") or "").replace("\\", "/")
                        == TREE_ORDER_DOCUMENT_PATH
                        else 2
                    )
                ),
                int(item.get("revision") or 0),
            ),
        )
        for remote in ordered_remote_documents:
            remote_path = ""
            revision = 0
            try:
                document_id = str(uuid.UUID(str(remote.get("document_id"))))
                remote_path = self._safe_relative_path(remote.get("relative_path"))
                server_relative_path = self._safe_relative_path(
                    remote.get("_server_relative_path") or remote_path
                )
                revision = int(remote.get("revision") or 0)
                content = remote.get("content") or ""
                is_deleted = bool(remote.get("is_deleted"))
                deleted_at = remote.get("deleted_at") or remote.get("updated_at")
                if remote_path == TREE_ORDER_DOCUMENT_PATH:
                    if defer_tree_order:
                        # A tree snapshot may lead one folder row during a rapid
                        # multi-rename batch. Never materialize that unconfirmed
                        # path, but do not withhold unrelated confirmed changes.
                        continue
                    change = self._apply_remote_tree_order_document(
                        document_id,
                        content,
                        revision,
                        is_deleted,
                        remote_live_document_paths,
                        changes,
                        remote_folder_paths,
                        folder_rows is not None,
                        remote_folder_ids=remote_folder_ids,
                    )
                    if change:
                        changes.append(change)
                        self._diagnostics.record(
                            "sync_tree_applied",
                            state=f"revision={revision}",
                            pending_count=self.pending_retry_count,
                        )
                    continue
                if remote_path == TRASH_PURGE_DOCUMENT_PATH:
                    change = self._apply_remote_trash_purge_document(
                        document_id, content, revision, is_deleted
                    )
                    if change:
                        changes.append(change)
                    continue
                if not is_deleted and not is_live_document_path(remote_path):
                    continue
                path_document = self._v2_store.get_document(
                    self._v2_context["local_key"], remote_path
                )
                if (
                    path_document
                    and str(path_document.get("document_id")) != document_id
                ):
                    adopted = False
                    if (
                        not is_deleted
                        and remote_path not in protected
                        and remote_path not in active_paths
                    ):
                        local_full_path = full_path(remote_path)
                        try:
                            local_bytes_match = (
                                os.path.isfile(local_full_path)
                                and not self._is_reparse_path(local_full_path)
                                and self._v2_wpm.read_text_file(remote_path) == content
                            )
                        except (OSError, ValueError):
                            local_bytes_match = False
                        if local_bytes_match:
                            adopted = self._v2_store.adopt_pristine_document_identity(
                                self._v2_context["local_key"],
                                remote_path,
                                document_id,
                            )
                    if not adopted:
                        if strict:
                            raise RuntimeError("DOCUMENT_UUID_CONFLICT")
                        continue
                document = self._v2_store.get_document_by_id(document_id)
                old_path = document.get("local_path") if document else None
                canonical_old_path = (
                    self._safe_relative_path(old_path) if old_path else None
                )
                repair_unicode_path = bool(
                    document
                    and not is_deleted
                    and old_path != remote_path
                    and canonical_old_path == remote_path
                    and revision == int(document.get("revision") or 0)
                )
                purged_revision = self._normalized_trash_purges(
                    self._v2_wpm.project_settings.get(
                        "trash_purged_revisions", {}
                    )
                ).get(document_id, 0)
                if is_deleted and purged_revision >= revision:
                    if self._v2_store.has_active_operations(document_id):
                        continue
                    if old_path and str(old_path).startswith("메인/휴지통/"):
                        self._v2_wpm.delete_from_trash(old_path)
                    virtual_path = f"__antigravity__/purged/{document_id}"
                    if document and revision <= int(document.get("revision") or 0):
                        applied = self._v2_store.relocate_deleted_document(
                            document_id, virtual_path
                        )
                    else:
                        applied = self._v2_store.apply_remote_snapshot(
                            self._v2_context,
                            document_id,
                            server_relative_path,
                            content,
                            revision,
                            is_deleted=True,
                            local_path=virtual_path,
                        )
                    if applied.get("applied"):
                        changes.append({
                            "kind": "purged_tombstone",
                            "document_id": document_id,
                            "old_local_path": old_path,
                            "new_local_path": virtual_path,
                            "remote_path": remote_path,
                            "content": content,
                            "revision": revision,
                            "is_deleted": True,
                        })
                    continue

                tombstone_copy_missing = bool(
                    document
                    and is_deleted
                    and document.get("is_deleted")
                    and str(old_path or "").startswith("메인/휴지통/")
                    and not os.path.exists(full_path(old_path))
                )
                repair_tombstone_location = bool(
                    document
                    and is_deleted
                    and document.get("is_deleted")
                    and (
                        not str(old_path or "").startswith("메인/휴지통/")
                        or tombstone_copy_missing
                    )
                )
                repair_live_copy = bool(
                    document
                    and not is_deleted
                    and not document.get("is_deleted")
                    and revision == int(document.get("revision") or 0)
                    and old_path == remote_path
                    and not os.path.exists(full_path(old_path))
                )

                # Another Windows instance can update the shared durable store
                # and disk before this visible instance handles the same pull.
                # In that case the revision is equal, but the clean open editor
                # still needs a refresh notification.
                equal_revision_active_refresh = bool(
                    document
                    and not is_deleted
                    and revision == int(document.get("revision") or 0)
                    and old_path == remote_path
                    and remote_path in active_paths
                )
                if equal_revision_active_refresh:
                    if self._v2_store.has_active_operations(document_id):
                        continue
                    if remote_path in protected:
                        continue
                    if self._v2_wpm.read_text_file(remote_path) != content:
                        if not self._v2_wpm.write_text_file(remote_path, content):
                            if strict:
                                raise OSError("REMOTE_DOCUMENT_WRITE_FAILED")
                            continue
                    changes.append({
                        "kind": "remote_refresh",
                        "document_id": document_id,
                        "old_local_path": old_path,
                        "new_local_path": remote_path,
                        "remote_path": remote_path,
                        "content": content,
                        "revision": revision,
                        "is_deleted": False,
                    })
                    continue

                if revision <= 0 or (
                    document
                    and revision <= document["revision"]
                    and not repair_tombstone_location
                    and not repair_live_copy
                    and not repair_unicode_path
                ):
                    if strict and revision <= 0:
                        raise ValueError("INVALID_REMOTE_REVISION")
                    continue
                if self._v2_store.has_active_operations(document_id):
                    if strict:
                        raise RuntimeError("REMOTE_DOCUMENT_HAS_LOCAL_OPERATIONS")
                    continue
                if remote_path in protected or (old_path and old_path in protected):
                    if strict:
                        raise RuntimeError("REMOTE_DOCUMENT_PATH_IS_PROTECTED")
                    continue

                local_path = old_path or remote_path
                renamed_from = None
                renamed_to = None
                created_tombstone_path = None
                duplicate_unicode_path = None

                if is_deleted:
                    if repair_tombstone_location:
                        # The live path may already belong to a replacement UUID.
                        # Preserve the old tombstone separately without moving it.
                        local_path = self._v2_wpm.materialize_remote_tombstone(
                            remote_path, content, deleted_at, document_id
                        )
                        created_tombstone_path = local_path
                        renamed_from, renamed_to = old_path, local_path
                    elif old_path and not old_path.startswith("메인/휴지통/"):
                        old_full = full_path(old_path)
                        if os.path.exists(old_full):
                            os.makedirs(full_path("메인/휴지통"), exist_ok=True)
                            local_path = self._v2_wpm.move_to_trash(
                                old_path, deleted_at, document_id
                            )
                            renamed_from, renamed_to = old_path, local_path
                        else:
                            local_path = self._v2_wpm.materialize_remote_tombstone(
                                remote_path, content, deleted_at, document_id
                            )
                            created_tombstone_path = local_path
                            renamed_from, renamed_to = old_path, local_path
                    elif old_path and old_path.startswith("메인/휴지통/"):
                        local_path = old_path
                        if not os.path.exists(full_path(local_path)):
                            local_path = self._v2_wpm.materialize_remote_tombstone(
                                remote_path, content, deleted_at, document_id
                            )
                            created_tombstone_path = local_path
                        elif not self._v2_wpm.write_text_file(local_path, content):
                            if strict:
                                raise OSError("REMOTE_DOCUMENT_WRITE_FAILED")
                            continue
                    else:
                        known_path = self._identity_live_document_path(
                            document_id
                        )
                        if known_path and os.path.exists(full_path(known_path)):
                            os.makedirs(full_path("메인/휴지통"), exist_ok=True)
                            local_path = self._v2_wpm.move_to_trash(
                                known_path, deleted_at, document_id
                            )
                            renamed_from, renamed_to = known_path, local_path
                        else:
                            local_path = self._v2_wpm.materialize_remote_tombstone(
                                remote_path, content, deleted_at, document_id
                            )
                            created_tombstone_path = local_path
                    self._settle_remote_identity(
                        old_path, local_path, document_id, remote_folder_ids
                    )
                    if not self._v2_wpm.write_text_file(local_path, content):
                        if created_tombstone_path:
                            self._v2_wpm.delete_from_trash(created_tombstone_path)
                        if strict:
                            raise OSError("REMOTE_DOCUMENT_WRITE_FAILED")
                        continue
                else:
                    local_path = remote_path
                    old_full = full_path(old_path) if old_path else None
                    new_full = full_path(local_path)
                    if old_path and old_path != local_path and old_full and os.path.exists(old_full):
                        if os.path.exists(new_full):
                            if not repair_unicode_path:
                                if strict:
                                    raise FileExistsError("REMOTE_PATH_CONFLICT")
                                continue
                            try:
                                with open(old_full, "r", encoding="utf-8") as source:
                                    old_content = source.read()
                                with open(new_full, "r", encoding="utf-8") as source:
                                    new_content = source.read()
                            except OSError:
                                if strict:
                                    raise
                                continue
                            if old_content != content or new_content != content:
                                if strict:
                                    raise FileExistsError("REMOTE_PATH_CONFLICT")
                                continue
                            duplicate_unicode_path = old_path
                        else:
                            os.makedirs(os.path.dirname(new_full), exist_ok=True)
                            self._adopt_remote_identity(
                                self._remote_identity_ancestor_entries(
                                    local_path, remote_folder_ids
                                )
                            )
                            self._relocate_remote_identity(
                                old_path,
                                local_path,
                                lambda: os.rename(old_full, new_full),
                            )
                            renamed_from, renamed_to = old_path, local_path
                    elif repair_unicode_path and os.path.exists(new_full):
                        try:
                            with open(new_full, "r", encoding="utf-8") as source:
                                new_content = source.read()
                        except OSError:
                            if strict:
                                raise
                            continue
                        if new_content != content:
                            if strict:
                                raise FileExistsError("REMOTE_PATH_CONFLICT")
                            continue
                    elif not old_path and os.path.exists(new_full):
                        try:
                            with open(new_full, "r", encoding="utf-8") as source:
                                if source.read() != content:
                                    if strict:
                                        raise FileExistsError(
                                            "REMOTE_PATH_CONFLICT"
                                        )
                                    continue
                        except OSError:
                            if strict:
                                raise
                            continue

                    adopted = self._settle_remote_identity(
                        old_path, local_path, document_id, remote_folder_ids
                    )
                    if not self._v2_wpm.write_text_file(local_path, content):
                        self._release_adopted_document(adopted, local_path)
                        if renamed_from and renamed_to:
                            try:
                                self._relocate_remote_identity(
                                    renamed_to,
                                    renamed_from,
                                    lambda: os.rename(
                                        full_path(renamed_to),
                                        full_path(renamed_from),
                                    ),
                                )
                            except Exception:
                                pass
                        if strict:
                            raise OSError("REMOTE_DOCUMENT_WRITE_FAILED")
                        continue

                if repair_unicode_path:
                    applied = self._v2_store.repair_clean_document_path(
                        document_id, local_path, remote_path
                    )
                elif repair_live_copy:
                    applied = {
                        "applied": True,
                        "reason": "restored_missing_local_copy",
                    }
                elif (
                    repair_tombstone_location
                    and revision <= int(document.get("revision") or 0)
                ):
                    applied = self._v2_store.relocate_deleted_document(
                        document_id, local_path
                    )
                else:
                    applied = self._v2_store.apply_remote_snapshot(
                        self._v2_context,
                        document_id,
                        server_relative_path,
                        content,
                        revision,
                        is_deleted=is_deleted,
                        local_path=local_path,
                        parent_folder_id=remote.get("parent_folder_id"),
                        name=remote.get("name"),
                        structure_revision=remote.get("structure_revision"),
                    )
                if not applied.get("applied"):
                    if created_tombstone_path:
                        try:
                            self._v2_wpm.delete_from_trash(created_tombstone_path)
                        except Exception:
                            pass
                    if renamed_from and renamed_to:
                        try:
                            self._relocate_remote_identity(
                                renamed_to,
                                renamed_from,
                                lambda: os.rename(
                                    full_path(renamed_to),
                                    full_path(renamed_from),
                                ),
                            )
                        except Exception:
                            pass
                    if strict:
                        raise RuntimeError(
                            "REMOTE_SNAPSHOT_APPLY_FAILED:"
                            + str(applied.get("reason") or "unknown")
                        )
                    continue
                if duplicate_unicode_path:
                    try:
                        os.remove(full_path(duplicate_unicode_path))
                    except OSError:
                        pass
                if repair_unicode_path:
                    self._prune_empty_unicode_path_parents(
                        root, full_path(old_path)
                    )
                if is_deleted:
                    self._v2_wpm.update_trash_metadata(
                        local_path, deleted_at, document_id
                    )
                changes.append({
                    "document_id": document_id,
                    "old_local_path": old_path,
                    "new_local_path": local_path,
                    "remote_path": remote_path,
                    "content": content,
                    "revision": revision,
                    "is_deleted": is_deleted,
                })
            except Exception as error:
                if strict:
                    raise
                if remote_path == TREE_ORDER_DOCUMENT_PATH:
                    code = self._stable_error_code(error) or type(error).__name__
                    self._diagnostics.record(
                        "sync_tree_deferred",
                        state=f"revision={revision};reason={code}",
                        pending_count=self.pending_retry_count,
                    )
                if self._v2_identity_apply_failed or isinstance(
                    error, (CreationError, IdentityError)
                ):
                    # Identity is not per-document work that can be skipped and
                    # still add up to a finished pull. The folder projection is
                    # already stored, so a pull that fails to name what it
                    # stored has to say so instead of reporting nothing.
                    self._block_pull_with_conflict(error)
                print(f"Failed to apply remote v2 document: {error}")
        if tree_order_rows is not None and not defer_contract_tree_order:
            try:
                contract_tree_change = self._apply_contract_tree_order_snapshots(
                    tree_order_rows
                )
                if contract_tree_change:
                    changes.append(contract_tree_change)
            except Exception as error:
                if strict:
                    raise
                code = self._stable_error_code(error) or type(error).__name__
                self._diagnostics.record(
                    "sync_tree_deferred",
                    state=f"contract;reason={code}",
                    pending_count=self.pending_retry_count,
                )
        # Last, because the documents inside a folder deleted elsewhere have
        # just been tombstoned into 휴지통 on their own.
        changes.extend(
            self._apply_remote_folder_tombstones(folder_rows, protected)
        )
        if self._v2_identity_apply_failed:
            # A deferred partial snapshot legitimately leaves the projection
            # ahead of the tree; an identity failure leaves it describing
            # folders nothing here can name.
            self._restore_folder_snapshots(
                previous_folder_snapshots, identity_result
            )
        return changes

    def _relocate_remote_empty_folder(self, item):
        """Rename one proven empty directory, taking its identity node along."""
        def apply_filesystem():
            if not self._apply_remote_empty_folder_rename(
                self._v2_wpm.writing_root_path, item
            ):
                raise RemoteRenameSkipped(item["old_relative_path"])
            return True

        try:
            self._relocate_remote_identity(
                item["old_relative_path"],
                item["new_relative_path"],
                apply_filesystem,
            )
        except RemoteRenameSkipped:
            return False
        return True

    def _adopt_remote_tree_folders(self, folder_plan, renamed_items, folder_ids):
        """Record every directory this tree snapshot is about to materialize.

        The server's folder id is used wherever the pull carries one. A folder
        the peer has no stable id for — a legacy tree-order-only entry — gets a
        local id, exactly as it would had the writer created it here; the
        outbound side then publishes that id rather than inventing a second one.

        Directories that already exist are recorded too. They are in the tree
        the open check audits either way, so leaving them unnamed is the same
        divergence, only discovered later.
        """
        renamed_paths = {item["new_full_path"] for item in renamed_items or ()}
        ids = folder_ids or {}
        entries = [
            {
                "legacy_path": item["relative_path"],
                "kind": "folder",
                "uuid": ids.get(
                    self._tree_path_comparison_key(item["relative_path"])
                ),
            }
            for item in folder_plan
            if item["full_path"] not in renamed_paths
        ]
        return self._adopt_remote_identity(entries)

    def _record_identity_diagnostic(self, event, **metadata):
        """Record an identity finding where it survives being looked for.

        The rotating log is read by nobody after 256KB. These are the states a
        verification run and a recovery both have to see, so they go in the
        database beside the other structure diagnostics — deduplicated, because
        a standing divergence is re-found on every single pull.
        """
        store = self._v2_store
        context = self._v2_context or {}
        if store is None or not context.get("local_key"):
            return
        try:
            store.record_diagnostic(
                context["local_key"],
                event,
                dedupe=True,
                project_id=context.get("project_id"),
                **metadata,
            )
        except Exception:
            # Instrumentation never decides whether a pull may finish.
            pass

    def _identity_conflict_detail(self):
        """Say which kind of divergence stopped this pull being recorded.

        Neither message may promise that nothing changed. Both are found by
        auditing what the apply already did, and by then a folder or a document
        the snapshot brought can be on disk.
        """
        if self._v2_identity_uuid_conflicts:
            return (
                "같은 폴더를 서로 다른 UUID로 가리키고 있습니다. "
                "일부 원격 변경이 로컬에 반영되었을 수 있습니다. "
                "복구하기 전까지 동기화를 완료로 기록하지 않습니다."
            )
        return (
            "원격 구조를 적용한 뒤 identity와 파일 트리가 어긋났습니다. "
            "일부 원격 변경이 로컬에 반영되었을 수 있습니다. "
            "동기화를 완료로 기록하지 않았습니다."
        )

    def _block_pull_with_conflict(self, error):
        """Refuse to finish a pull whose structure or identity did not apply."""
        self._v2_last_pull_apply_blocked = True
        self._v2_identity_apply_failed = True
        self._record_identity_diagnostic(
            "identity_apply_blocked",
            error_code=self._stable_error_code(error) or type(error).__name__,
        )
        self._set_sync_state(
            "conflict",
            "원격 구조를 identity 에 기록하지 못해 동기화를 완료로 "
            "기록하지 않았습니다. 다음 동기화에서 다시 시도합니다.",
        )

    def _restore_folder_snapshots(self, previous, identity_result):
        """Take back the folder rows this apply could not name — and only those.

        A snapshot is not all-or-nothing: an exact folder-id rename can land
        while a new folder beside it fails to be adopted. The renamed directory
        is on disk under its new name, so its new row is what the tree shows
        and must stay. The failed folder exists nowhere but in the database,
        and is the row that has to go.
        """
        if previous is None:
            return
        local_key = self._v2_context["local_key"]
        moved = [
            (
                self._tree_path_comparison_key(change.get("old_local_path")),
                change.get("new_local_path"),
            )
            for change in (identity_result or {}).get("changes") or ()
            if change.get("kind") == "folder_identity_rename"
            and change.get("old_local_path")
        ]

        def followed_a_landed_rename(row):
            key = self._tree_path_comparison_key(row.get("local_path"))
            return any(
                key == old_key or key.startswith(old_key + "/")
                for old_key, _new_path in moved
            )

        try:
            current = {
                str(row["folder_id"]): row
                for row in self._v2_store.list_folders(local_key)
            }
            restored = [
                current[str(row["folder_id"])]
                if followed_a_landed_rename(row) and str(row["folder_id"]) in current
                else row
                for row in previous
            ]
            self._v2_store.replace_folder_snapshots(local_key, restored)
        except Exception as error:
            self._record_identity_diagnostic(
                "folder_projection_restore_failed",
                error_code=self._stable_error_code(error) or type(error).__name__,
            )

    def _identity_uuid_divergences(self):
        """Rows whose id disagrees with the identity node at the same path.

        The path audit cannot see this: both sides name the same folder, and
        only the ids differ. That is the shape a pull leaves behind when it
        adopts nothing and the projection is written anyway, and it is what
        makes the next outbound publish re-issue an id the server already has.
        """
        from project_creation_v1 import read_identity

        project_root = self._identity_project_root()
        if not project_root:
            return []
        nodes = read_identity(project_root)["nodes"]
        by_path = {node["legacy_path"]: node for node in nodes}
        by_uuid = {node["uuid"]: node for node in nodes}

        local_key = self._v2_context["local_key"]
        rows = [
            (str(row.get("folder_id")), row)
            for row in self._v2_store.list_folders(local_key)
        ]
        rows.extend(
            (str(row.get("document_id")), row)
            for row in self._v2_store.list_documents(local_key)
        )
        divergences = []
        for entity_id, row in rows:
            if row.get("is_deleted"):
                continue
            try:
                local_path = self._safe_relative_path(row.get("local_path"))
            except ValueError:
                continue
            if not local_path or is_internal_sync_path(local_path):
                continue
            at_path = by_path.get(local_path)
            if at_path is not None and at_path["uuid"] != entity_id:
                divergences.append(local_path)
                continue
            at_uuid = by_uuid.get(entity_id)
            if at_uuid is not None and at_uuid["legacy_path"] != local_path:
                divergences.append(local_path)
        return sorted(set(divergences))

    def _identity_project_root(self):
        """The project directory whose identity file this pull must follow."""
        root = getattr(self._v2_wpm, "writing_root_path", None)
        if not root:
            return None
        from project_creation_v1 import identity_path

        project_root = os.path.dirname(os.path.abspath(root))
        if not os.path.exists(identity_path(project_root)):
            # A legacy tree has no identity of record yet. Importing it is an
            # explicit user action, never a side effect of a pull.
            return None
        return project_root

    def _identity_call(self, action):
        """Run one identity transaction, remembering that a failure was one.

        What went wrong matters less than where: a full disk raises ``OSError``
        and a refused adoption raises ``CreationError``, and either way this
        pull has stored rows it could not name. The exception is re-raised
        unchanged so callers still see the real error.
        """
        try:
            return action()
        except RemoteRenameSkipped:
            raise
        except BaseException as error:
            self._v2_identity_apply_failed = True
            raise

    def _adopt_remote_identity(self, entries):
        """Record the peer's ids for paths this pull is about to materialize.

        A path identity already knows keeps its recorded id, so a snapshot that
        claims a different one is noted rather than applied: the id is what
        makes two items the same item, and only a recovery that has both sides
        in front of it may change one.
        """
        from project_creation_v1 import adopt_remote_nodes, identity_uuid_conflicts

        project_root = self._identity_project_root()
        if not project_root or not entries:
            return []
        return self._identity_call(lambda: self._adopt_checked_identity(
            project_root, entries, identity_uuid_conflicts, adopt_remote_nodes
        ))

    def _adopt_checked_identity(
        self, project_root, entries, find_conflicts, adopt
    ):
        conflicts = find_conflicts(project_root, entries)
        if conflicts:
            self._note_identity_uuid_conflicts(conflicts)
        return adopt(project_root, entries)

    def _note_identity_uuid_conflicts(self, conflicts):
        """Keep a snapshot's disagreeing ids where the success gate can see them."""
        known = {item["legacy_path"] for item in self._v2_identity_uuid_conflicts}
        for conflict in conflicts:
            if conflict["legacy_path"] in known:
                continue
            self._v2_identity_uuid_conflicts.append(conflict)
            # The path is deliberately not recorded: diagnostics carry ids and
            # states, never anything a manuscript tree is named with. The two
            # ids are enough to find both sides.
            self._record_identity_diagnostic(
                "identity_uuid_conflict",
                entity_id=conflict["recorded"],
                state=f"proven={conflict['proven']}",
                error_code="IDENTITY_UUID_CONFLICT",
            )

    def _remote_identity_ancestor_entries(self, relative_path, folder_ids=None):
        """The folders above ``relative_path``, under the peer's ids where known.

        A document cannot be recorded under a folder that has no UUID, and the
        folders a pull creates arrive in the same snapshot, so the ancestors are
        always offered for adoption first.
        """
        parts = str(relative_path or "").split("/")
        ids = folder_ids or {}
        return [
            {
                "legacy_path": "/".join(parts[:depth]),
                "kind": "folder",
                "uuid": ids.get(
                    self._tree_path_comparison_key("/".join(parts[:depth]))
                ),
            }
            for depth in range(1, len(parts))
        ]

    def _adopt_remote_identity_path(
        self, relative_path, kind, entity_uuid, folder_ids=None
    ):
        """Record one remote path, and any ancestor identity does not know yet."""
        entries = self._remote_identity_ancestor_entries(
            relative_path, folder_ids
        )
        entries.append({
            "legacy_path": relative_path,
            "kind": kind,
            "uuid": entity_uuid,
        })
        return self._adopt_remote_identity(entries)

    def _release_adopted_identity(self, nodes, apply_filesystem):
        """Give back ids adopted for a snapshot that did not land.

        Identity is given back first and the entries follow only once that
        write has landed. A failed release therefore keeps both, which still
        opens, instead of deleting directories that identity would go on
        naming.
        """
        from project_creation_v1 import release_adopted_nodes

        project_root = self._identity_project_root()
        if not project_root or not nodes:
            return apply_filesystem()
        return self._identity_call(
            lambda: release_adopted_nodes(project_root, nodes, apply_filesystem)
        )

    def _identity_live_document_path(self, entity_uuid):
        """Where identity says a document is when the sync store has no row.

        Identity is the local authority for a document's location, so a remote
        delete for a file this device created but never published moves that
        file into 휴지통 instead of writing a second copy beside it. The copy
        was the file identity could not name afterwards.
        """
        project_root = self._identity_project_root()
        if not project_root:
            return None
        node = self._identity_node_by_uuid(project_root, entity_uuid)
        if node is None or node["kind"] != "document":
            return None
        legacy_path = node["legacy_path"]
        return legacy_path if is_live_document_path(legacy_path) else None

    def _release_adopted_document(self, adopted, local_path):
        """Give back the id and the placeholder of a document that never landed.

        Adoption issues the id before the bytes exist, so a write that fails
        would otherwise leave an empty file that the next attempt reads as a
        conflicting copy of the same path.
        """
        node = next(
            (
                item for item in adopted or ()
                if item["kind"] == "document"
                and item["legacy_path"] == local_path
            ),
            None,
        )
        if node is None:
            return
        target = os.path.join(
            os.path.abspath(self._v2_wpm.writing_root_path),
            local_path.replace("/", os.sep),
        )

        def remove_placeholder():
            if os.path.isfile(target) and os.path.getsize(target) == 0:
                os.unlink(target)

        try:
            self._release_adopted_identity([node], remove_placeholder)
        except Exception:
            pass

    def _identity_node_by_uuid(self, project_root, entity_uuid):
        from project_creation_v1 import read_identity

        if not entity_uuid:
            return None
        for node in read_identity(project_root)["nodes"]:
            if node["uuid"] == str(entity_uuid):
                return node
        return None

    def _relocate_remote_identity(self, old_path, new_path, apply_filesystem):
        """Move a remote-driven path and take its identity node with it."""
        from project_creation_v1 import relocate_path

        project_root = self._identity_project_root()
        if not project_root:
            return apply_filesystem()
        return self._identity_call(
            lambda: relocate_path(
                project_root, old_path, new_path, apply_filesystem
            )
        )

    def _settle_remote_identity(
        self, old_path, new_path, entity_uuid, folder_ids=None
    ):
        """Name a document this pull has already written to ``new_path``.

        The bytes can land before identity can follow them — a preserved trash
        copy, a path repaired in place — so the node is moved, or recorded for
        the first time, afterwards. A node that already names the path is left
        exactly as it is, which keeps a repeated pull free.

        The node only follows when its own file is gone. A copy taken beside a
        file that is still there is a new node, never a move that would leave
        the original unnamed.
        """
        from project_creation_v1 import node_for_path

        project_root = self._identity_project_root()
        if not project_root or not new_path:
            return []
        if node_for_path(project_root, new_path) is not None:
            return []
        node = node_for_path(project_root, old_path) if old_path else None
        if node is None:
            node = self._identity_node_by_uuid(project_root, entity_uuid)
        root = os.path.abspath(self._v2_wpm.writing_root_path)
        if node is not None and not os.path.lexists(
            os.path.join(root, node["legacy_path"].replace("/", os.sep))
        ):
            self._relocate_remote_identity(
                node["legacy_path"], new_path, lambda: None
            )
            return []
        return self._adopt_remote_identity_path(
            new_path, "document", entity_uuid, folder_ids
        )

    def _identity_audit_is_clean(self):
        """Whether identity and the file tree still agree after an apply.

        A snapshot that materialized a file identity does not name opens as a
        blocked project on the next launch. Calling that pull applied would
        hide the divergence until then, so it is reported and retried instead.
        """
        from project_creation_v1 import CreationError, audit
        from project_identity_v1 import IdentityError

        project_root = self._identity_project_root()
        if not project_root:
            return True
        if self._v2_identity_uuid_conflicts:
            return False
        try:
            report = audit(project_root)
        except (IdentityError, CreationError, OSError) as error:
            self._record_identity_diagnostic(
                "identity_audit_failed",
                error_code=self._stable_error_code(error) or type(error).__name__,
            )
            return False
        try:
            crossed = self._identity_uuid_divergences()
        except Exception as error:
            # An audit that cannot run is not a clean audit.
            self._record_identity_diagnostic(
                "identity_audit_failed",
                error_code=self._stable_error_code(error) or type(error).__name__,
            )
            return False
        if crossed:
            self._record_identity_diagnostic(
                "identity_uuid_divergence",
                state=f"paths={len(crossed)}",
                error_code="IDENTITY_UUID_DIVERGENCE",
            )
            return False
        if not any(report.values()):
            return True
        self._record_identity_diagnostic(
            "identity_tree_divergence",
            state=(
                f"missing_on_disk={len(report['missing_on_disk'])};"
                f"missing_in_identity={len(report['missing_in_identity'])};"
                f"pending_journals={len(report['pending_journals'])}"
            ),
            error_code="IDENTITY_TREE_DIVERGENCE",
        )
        return False

    def _identity_folder_by_uuid(self, folder_uuid):
        from project_creation_v1 import identity_folder_nodes

        root = getattr(self._v2_wpm, "writing_root_path", None)
        for node in identity_folder_nodes(root):
            if str(node["uuid"]) == str(folder_uuid):
                return node
        return None

    def _apply_remote_folder_tombstones(self, folder_rows, protected):
        """Follow a folder another device deleted, without removing one byte.

        Windows read the folder projection only for live rows, so a folder
        deleted on iPad was simply invisible here: its documents arrived as
        tombstones and moved themselves into 휴지통, and the directory they
        came out of stayed in the binder, empty. That is the mirror of the
        Windows-side gap that left an empty folder on iPad.

        The folder is moved to 휴지통, never deleted, so anything still inside
        it — an untracked file, a document that has not synced yet — survives
        and stays reachable. Identity follows the move, so the UUID is intact
        and this client will not then re-publish the folder as live.
        """
        deleted_ids = [
            str(row.get("folder_id"))
            for row in (folder_rows or [])
            if isinstance(row, dict)
            and row.get("is_deleted")
            and row.get("folder_id")
        ]
        if self._v2_wpm is None or not (deleted_ids or folder_rows):
            return []

        root = os.path.abspath(self._v2_wpm.writing_root_path)
        protected = set(protected or ())
        changes = []
        # Shallowest first: moving a parent takes its children with it, and
        # identity rewrites their paths, so the children resolve as done.
        ordered = sorted(
            deleted_ids,
            key=lambda folder_id: len(
                (self._identity_folder_by_uuid(folder_id) or {})
                .get("legacy_path", "")
                .split("/")
            ),
        )
        for folder_id in ordered:
            node = self._identity_folder_by_uuid(folder_id)
            if node is None:
                continue
            local_path = node["legacy_path"]
            if local_path == "메인/휴지통" or local_path.startswith("메인/휴지통/"):
                continue
            if not self._folder_move_is_safe(local_path, protected, root):
                continue
            trash_path = self._v2_wpm.move_to_trash(local_path)
            self._v2_store.move_local_path(
                self._v2_context["local_key"], local_path, trash_path
            )
            changes.append({
                "kind": "folder_tombstone",
                "old_local_path": local_path,
                "new_local_path": trash_path,
                "entity_id": folder_id,
            })

        changes.extend(
            self._apply_remote_folder_restores(folder_rows, protected, root)
        )
        return changes

    def _folder_move_is_safe(self, local_path, protected, root):
        """Whether this client may move one folder on the writer's behalf."""
        if local_path in protected or any(
            path.startswith(local_path + "/") for path in protected
        ):
            return False
        if any(
            (
                document["local_path"] == local_path
                or document["local_path"].startswith(local_path + "/")
            )
            and self._v2_store.has_active_operations(document["document_id"])
            for document in self._v2_store.list_documents(
                self._v2_context["local_key"]
            )
        ):
            # Unsent local edits still address the old path. Leave the folder
            # alone and let the next pull settle it.
            return False
        full_path = os.path.abspath(
            os.path.join(root, local_path.replace("/", os.sep))
        )
        try:
            return (
                os.path.commonpath([root, full_path]) == root
                and os.path.isdir(full_path)
                and not self._is_reparse_path(full_path)
            )
        except (OSError, ValueError):
            return False

    def _apply_remote_folder_restores(self, folder_rows, protected, root):
        """Follow a folder another device pulled back out of the trash.

        Without this the two clients undo each other. The outbound pass reads
        identity, sees the node still under 휴지통 and publishes a delete, so a
        restore performed on iPad would quietly disappear again.

        The server row says where the folder belongs, so the parent comes from
        ``parent_folder_id`` rather than the local trash index, which only
        records where the folder was when this device last saw it. A target
        that is already occupied is left alone and reported.
        """
        restored = []
        for row in folder_rows or []:
            if not isinstance(row, dict) or row.get("is_deleted"):
                continue
            folder_id = str(row.get("folder_id") or "")
            node = self._identity_folder_by_uuid(folder_id) if folder_id else None
            if node is None:
                continue
            trash_path = node["legacy_path"]
            if not trash_path.startswith("메인/휴지통/"):
                continue

            parent_id = row.get("parent_folder_id")
            parent_node = (
                self._identity_folder_by_uuid(parent_id) if parent_id else None
            )
            if parent_node is None:
                continue
            parent_path = parent_node["legacy_path"]
            if parent_path.startswith("메인/휴지통"):
                continue
            name = unicodedata.normalize("NFC", str(row.get("name") or ""))
            if not name:
                continue
            target_path = f"{parent_path}/{name}"
            if not self._folder_move_is_safe(trash_path, protected, root):
                continue
            if os.path.exists(os.path.abspath(
                os.path.join(root, target_path.replace("/", os.sep))
            )):
                self._v2_store.record_diagnostic(
                    self._v2_context["local_key"],
                    "folder_restore_blocked",
                    dedupe=True,
                    entity_id=folder_id,
                    error_code="RESTORE_TARGET_TAKEN",
                    project_id=self._v2_context["project_id"],
                )
                continue

            landed = self._v2_wpm.restore_from_trash(trash_path, parent_path)
            self._v2_store.move_local_path(
                self._v2_context["local_key"], trash_path, landed
            )
            restored.append({
                "kind": "folder_restore",
                "old_local_path": trash_path,
                "new_local_path": landed,
                "entity_id": folder_id,
            })
        return restored

    @staticmethod
    def _prune_empty_unicode_path_parents(root, old_file_path):
        """Remove only empty legacy NFD directories left by a path repair."""
        root = os.path.abspath(root)
        current = os.path.dirname(os.path.abspath(old_file_path))
        while current != root:
            try:
                if os.path.commonpath([root, current]) != root:
                    break
                os.rmdir(current)
            except OSError:
                break
            current = os.path.dirname(current)

    def pull_remote_changes_async(self):
        if not self.is_v2_enabled or is_forced_offline() or not self.supabase:
            return False
        if self._v2_pull_worker is not None:
            try:
                if self._v2_pull_worker.isRunning():
                    return False
            except RuntimeError:
                self._v2_pull_worker = None

        started_for = self._v2_pull_identity()
        worker = V2PullWorker(self, project_id=started_for[1])
        self._v2_pull_worker = worker

        def handle_result(success, payload):
            if not self.is_v2_enabled or self._v2_pull_identity() != started_for:
                # This reply belongs to the project that was open when the
                # request went out. Releasing, or opening another project, ends
                # that generation: nothing in the reply may be applied to
                # whatever is attached now.
                return
            if success:
                if isinstance(payload, dict):
                    documents = payload.get("documents") or []
                    folder_rows = payload.get("folders") or []
                    folder_versions = payload.get("folder_versions") or []
                    tree_order_rows = payload.get("tree_orders") or []
                else:
                    # Compatibility with an already-running/legacy worker.
                    documents = payload
                    folder_rows = []
                    folder_versions = []
                    tree_order_rows = []
                changes = self._apply_v2_remote_documents(
                    documents,
                    folder_rows=folder_rows,
                    folder_versions=folder_versions,
                    tree_order_rows=tree_order_rows,
                )
                if self._v2_last_pull_apply_blocked:
                    return
                # A structure pull is audited whether or not it reported a
                # change: the apply that quietly skipped its identity work is
                # exactly the one that reports nothing.
                audited = bool(changes or folder_rows or tree_order_rows)
                if audited and not self._identity_audit_is_clean():
                    # Something landed that identity cannot name. Reporting the
                    # pull as applied would hide that until the next launch
                    # refuses to open the project.
                    self._v2_last_pull_apply_blocked = True
                    self._set_sync_state(
                        "conflict", self._identity_conflict_detail()
                    )
                    return
                recovered_count = self._recover_untracked_local_files_after_pull(
                    documents
                )
                self._record_sync_success()
                if changes:
                    self.remoteDocumentsApplied.emit(changes)
                if recovered_count:
                    self._schedule_v2_retry(0)
            else:
                code = self._stable_error_code(payload)
                if code == "PROJECT_TRASHED":
                    self.mark_project_server_state(
                        self._v2_context["project_id"], "trashed"
                    )
                elif code == "PROJECT_PURGED":
                    self.mark_project_server_state(
                        self._v2_context["project_id"], "purged"
                    )
                elif code == "PROJECT_NOT_FOUND":
                    # A project still waiting for its first commit is
                    # absent for an ordinary reason. Only one this device
                    # already committed has actually gone missing.
                    self.mark_project_server_state(
                        self._v2_context["project_id"],
                        self._absent_project_state(),
                    )
                print(f"Failed to pull v2 documents: {payload}")

        def handle_finished():
            if self._v2_pull_worker is worker:
                self._v2_pull_worker = None

        worker.resultReady.connect(handle_result)
        worker.finished.connect(handle_finished)
        worker.finished.connect(worker.deleteLater)
        self._start_worker(worker)
        return True

    def _process_contract_structure_batch(self, batch_id):
        if is_forced_offline():
            raise RuntimeError("테스트 오프라인 모드")
        if not self.supabase:
            raise RuntimeError("서버 연결 없음")
        request = self._v2_store.structure_batch_request(batch_id)
        if (
            not request
            or request.get("batch", {}).get("batch_id") != batch_id
            or request.get("project_id") != self._v2_context["project_id"]
        ):
            raise RuntimeError("BATCH_NOT_READY")
        response = self.supabase.rpc(
            "atomic_structure_commit", {"p_request": request}
        ).execute()
        result = self._response_data(response)
        return self._v2_store.record_structure_batch_response(batch_id, result)

    def _launch_contract_structure_batch(self, batch_id):
        operation_ids = self._v2_store.mark_structure_batch_attempt(batch_id)
        worker = V2StructureBatchWorker(self, batch_id)
        self._v2_structure_worker = worker
        self._active_server_syncs += 1
        self._publish_sync_state()

        def handle_result(success, error_message, payload):
            self._active_server_syncs = max(0, self._active_server_syncs - 1)
            self._v2_structure_worker = None
            if not success:
                self._v2_store.mark_structure_batch_retry(batch_id, error_message)
                self._v2_store.record_diagnostic(
                    self._v2_context["local_key"],
                    "structure_batch_retry",
                    batch_id=batch_id,
                    error_code=self._stable_error_code(error_message) or "CLIENT_ERROR",
                )
                self._last_sync_error = error_message
                self._last_failure_offline = self._is_connectivity_error(error_message)
            else:
                self._last_sync_error = ""
                self._last_failure_offline = False
                self._v2_store.record_diagnostic(
                    self._v2_context["local_key"],
                    "structure_batch_result",
                    batch_id=batch_id,
                    state="completed" if payload.get("applied") else "blocked",
                )
            self._publish_sync_state()
            if success and payload.get("applied"):
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, self.retry_pending_syncs)

        worker.resultReady.connect(handle_result)
        worker.finished.connect(worker.deleteLater)
        self._start_worker(worker)
        return {"worker": worker, "operation_ids": operation_ids}

    def _process_v2_operation(self, operation_id):
        operation = self._v2_store.operation(operation_id)
        if not operation:
            return {"kind": "retry", "error": "대기 작업을 찾을 수 없습니다."}
        is_contract_document = (
            operation.get("provenance_kind") == "CONTRACT_BATCH"
        )

        def contract_result(response):
            if response.get("kind") == "document_commit_success":
                wire_result = response["results"][0]
                return {
                    "kind": "committed",
                    "result": {
                        "revision": wire_result["result_revision"],
                        "content_hash": wire_result["content_sha256"],
                        "status": response["status"],
                        "structure_revision": wire_result["structure_revision"],
                        "parent_folder_id": wire_result["parent_folder_id"],
                        "name": wire_result["name"],
                    },
                    "operation": operation,
                }
            error_code = response["error"]["code"]
            if error_code in {"REVISION_CONFLICT", "DOCUMENT_ALREADY_EXISTS"}:
                raise RuntimeError(error_code)
            self._v2_store.mark_blocked(operation_id, error_code)
            return {
                "kind": "blocked", "error": error_code,
                "operation": operation,
            }

        if is_forced_offline():
            error = "테스트 오프라인 모드"
            return {"kind": "retry", "error": error, "operation": operation}
        client = self.supabase
        if not client:
            error = "서버 연결 없음"
            return {"kind": "retry", "error": error, "operation": operation}

        try:
            for auth_attempt in range(2):
                try:
                    self.ensure_session_valid(
                        client, force_refresh=bool(auth_attempt)
                    )
                    self._ensure_remote_project(client)
                    if is_contract_document:
                        cached = self._v2_store.document_batch_response(
                            operation["batch_id"]
                        )
                        if cached:
                            return contract_result(cached)
                        request = self._v2_store.structure_batch_request(
                            operation["batch_id"]
                        )
                        if not request:
                            raise RuntimeError("CONTRACT_BATCH_NOT_FOUND")
                        response = client.rpc(
                            "document_commit", {"p_request": request}
                        ).execute()
                        wire_response = self._response_data(response) or {}
                        wire_response = self._v2_store.record_document_batch_response(
                            operation["batch_id"], wire_response
                        )
                        return contract_result(wire_response)

                    if operation["relative_path"] == TREE_ORDER_DOCUMENT_PATH:
                        # Folder state settles first: a rename needs its folder,
                        # and a child needs its parent, to already be there.
                        self._commit_outbound_folder_lifecycle(operation, client)
                        self._commit_outbound_folder_rename(operation, client)
                    lease_token = None
                    if operation["base_revision"] > 0:
                        lease_token = self._acquire_v2_lease(
                            operation["document_id"], client, session_checked=True
                        ).get("lease_token")
                    response = client.rpc("commit_document", {
                        "p_document_id": operation["document_id"],
                        "p_project_id": operation["project_id"],
                        "p_base_revision": operation["base_revision"],
                        "p_operation_id": operation["operation_id"],
                        "p_device_id": self._v2_device_id,
                        "p_relative_path": operation["relative_path"],
                        "p_content": operation["content"],
                        "p_is_deleted": bool(operation["is_deleted"]),
                        "p_lease_token": lease_token,
                    }).execute()
                    result = self._response_data(response) or {}
                    if "revision" not in result:
                        raise RuntimeError(
                            "commit_document 응답에 revision이 없습니다."
                        )
                    return {
                        "kind": "committed",
                        "result": result,
                        "operation": operation,
                    }
                except Exception as auth_error:
                    if (
                        auth_attempt == 0
                        and self._stable_error_code(auth_error) in {
                            "AUTH_EXPIRED", "AUTH_REQUIRED"
                        }
                    ):
                        continue
                    raise
        except Exception as error:
            code = self._stable_error_code(error)
            if code in {"AUTH_EXPIRED", "AUTH_REQUIRED"}:
                self._mark_auth_required(error)
                message = self._last_sync_error
                self._v2_store.mark_retry(operation_id, message)
                return {
                    "kind": "auth_required",
                    "error": message,
                    "operation": operation,
                }
            if code in {
                "PROJECT_TRASHED", "PROJECT_PURGED", "PROJECT_NOT_FOUND"
            }:
                state = (
                    "trashed" if code == "PROJECT_TRASHED" else "purged"
                )
                self.mark_project_server_state(
                    operation["project_id"], state
                )
                self._v2_store.mark_retry(operation_id, code)
                return {
                    "kind": "project_disabled",
                    "error": code,
                    "operation": operation,
                }
            if code in {"REVISION_CONFLICT", "DOCUMENT_ALREADY_EXISTS"}:
                remote = self._fetch_remote_document(operation["document_id"], client)
                if not remote:
                    message = "REVISION_CONFLICT: 서버 문서를 읽을 수 없습니다."
                    return {"kind": "retry", "error": message, "operation": operation}
                if operation["relative_path"] == TREE_ORDER_DOCUMENT_PATH:
                    # Binder order is one project preference snapshot. A later local
                    # reorder rebases onto the newest server revision and becomes the
                    # next atomic update instead of creating a manuscript conflict.
                    self._v2_store.rebase_clean_merge(
                        operation_id,
                        remote["revision"],
                        remote.get("content", ""),
                        operation["content"],
                    )
                    return {
                        "kind": "auto_merged",
                        "operation": operation,
                        "remote": remote,
                        "merged_content": operation["content"],
                        "old_local_path": TREE_ORDER_DOCUMENT_PATH,
                        "new_local_path": TREE_ORDER_DOCUMENT_PATH,
                    }
                if operation["relative_path"] == TRASH_PURGE_DOCUMENT_PATH:
                    try:
                        local_payload = json.loads(operation.get("content") or "{}")
                    except (TypeError, json.JSONDecodeError):
                        local_payload = {}
                    try:
                        remote_payload = json.loads(remote.get("content") or "{}")
                    except (TypeError, json.JSONDecodeError):
                        remote_payload = {}
                    merged_purges = self._normalized_trash_purges(
                        remote_payload.get("purged_revisions")
                    )
                    for purged_id, purged_revision in self._normalized_trash_purges(
                        local_payload.get("purged_revisions")
                    ).items():
                        merged_purges[purged_id] = max(
                            merged_purges.get(purged_id, 0), purged_revision
                        )
                    generation = (
                        local_payload.get("empty_generation")
                        or remote_payload.get("empty_generation")
                        or ""
                    )
                    merged_content = self._trash_purge_content(
                        merged_purges, generation
                    )
                    self._v2_store.rebase_clean_merge(
                        operation_id,
                        remote["revision"],
                        remote.get("content", ""),
                        merged_content,
                    )
                    return {
                        "kind": "auto_merged",
                        "operation": operation,
                        "remote": remote,
                        "merged_content": merged_content,
                        "old_local_path": TRASH_PURGE_DOCUMENT_PATH,
                        "new_local_path": TRASH_PURGE_DOCUMENT_PATH,
                    }
                latest_local_content = operation["content"]
                if self._v2_wpm:
                    disk_content = self._v2_wpm.read_text_file(operation["local_path"])
                    if disk_content is not None:
                        latest_local_content = disk_content
                merge = three_way_merge(
                    operation["base_content"], latest_local_content, remote.get("content", "")
                )
                if not merge.has_conflicts:
                    old_local_path = operation["local_path"]
                    new_local_path = old_local_path
                    document = self._v2_store.get_document_by_id(operation["document_id"])
                    remote_path = remote.get("relative_path", operation["relative_path"])
                    local_path_was_changed = bool(
                        document and operation["relative_path"] != document.get("server_path")
                    )
                    if remote_path != operation["relative_path"] and not local_path_was_changed:
                        new_local_path = remote_path
                        if self._v2_wpm and old_local_path != new_local_path:
                            old_full = os.path.join(self._v2_wpm.writing_root_path, old_local_path)
                            new_full = os.path.join(self._v2_wpm.writing_root_path, new_local_path)
                            os.makedirs(os.path.dirname(new_full), exist_ok=True)
                            if os.path.exists(new_full):
                                raise RuntimeError("PATH_CONFLICT: 원격 이름을 적용할 위치에 파일이 이미 있습니다.")
                            if os.path.exists(old_full):
                                os.rename(old_full, new_full)
                        self._v2_store.move_local_path(
                            self._v2_context["local_key"], old_local_path, new_local_path
                        )
                    self._v2_store.rebase_clean_merge(
                        operation_id, remote["revision"], remote.get("content", ""), merge.content,
                        remote_path=new_local_path if new_local_path != old_local_path else None,
                    )
                    if self._v2_wpm:
                        self._v2_wpm.write_text_file(new_local_path, merge.content)
                    return {
                        "kind": "auto_merged",
                        "operation": operation,
                        "remote": remote,
                        "merged_content": merge.content,
                        "old_local_path": old_local_path,
                        "new_local_path": new_local_path,
                    }
                self._v2_store.mark_conflict(
                    operation_id,
                    remote["revision"],
                    remote.get("relative_path", operation["relative_path"]),
                    remote.get("content", ""),
                    merge.content,
                    latest_local_content,
                )
                return {
                    "kind": "conflict",
                    "operation": operation,
                    "remote": remote,
                    "base_content": operation["base_content"],
                    "local_content": latest_local_content,
                    "merged_content": merge.content,
                    "conflict_count": merge.conflict_count,
                    "error": "REVISION_CONFLICT",
                }

            message = code or str(error)
            return {"kind": "retry", "error": message, "operation": operation}

    def _launch_v2_operation(self, operation):
        # A manual retry or another successful operation may start before a
        # scheduled lease retry fires. Cancel that reservation so only one
        # retry chain can exist at a time.
        self._cancel_scheduled_v2_retry(reset_backoff=False)
        self._v2_store.mark_attempt(operation["operation_id"])
        worker = V2QueueWorker(self, operation["operation_id"])
        self._v2_worker = worker
        self._v2_workers.append(worker)
        self._active_server_syncs += 1
        self._publish_sync_state()

        def handle_finished(success, error_message, payload):
            self._active_server_syncs = max(0, self._active_server_syncs - 1)
            self._v2_worker = None
            kind = (payload or {}).get("kind", "retry")
            original = (payload or {}).get("operation") or operation
            callback = self._v2_callbacks.pop(operation["operation_id"], None)
            conflict_callback = self._v2_conflict_callbacks.pop(operation["operation_id"], None)

            if kind == "committed":
                result = payload["result"]
                self._v2_store.mark_success(operation["operation_id"], result)
                self._release_ready_tree_order_barrier()
                if original.get("is_deleted"):
                    if self._v2_wpm:
                        self._v2_wpm.update_trash_metadata(
                            original.get("local_path"),
                            result.get("committed_at"),
                            original.get("document_id"),
                        )
                self._last_sync_error = ""
                self._last_failure_offline = False
                self._record_sync_success()
                if callback:
                    callback(True, "", original["local_path"], result["revision"])
            elif kind == "auto_merged":
                self.autoMergeApplied.emit(payload)
                if conflict_callback:
                    conflict_callback(payload)
            elif kind == "conflict":
                self.conflictDetected.emit(payload)
                if conflict_callback:
                    conflict_callback(payload)
                if callback:
                    callback(False, "REVISION_CONFLICT", original["local_path"], None)
            elif kind == "blocked":
                self._last_sync_error = payload.get("error", "CONTRACT_NOT_ALLOWED")
                self._last_failure_offline = False
                if callback:
                    callback(False, self._last_sync_error, original["local_path"], None)
            elif kind == "project_disabled":
                self._last_sync_error = payload.get("error", "")
                if callback:
                    callback(
                        False,
                        self._last_sync_error,
                        original["local_path"],
                        None,
                    )
            else:
                self._last_sync_error = error_message or payload.get("error", "")
                self._last_failure_offline = self._is_connectivity_error(self._last_sync_error)
                self._v2_store.mark_retry(operation["operation_id"], self._last_sync_error)
                if callback:
                    callback(False, self._last_sync_error, original["local_path"], None)

            self._finalize_v2_operation_lease(kind, original)
            self._publish_sync_state()
            operation_id = original.get("operation_id") or operation["operation_id"]
            if kind == "retry" and "LEASE_CONFLICT" in self._last_sync_error:
                lease_attempt = self._next_lease_retry_attempt(operation_id)
            else:
                self._reset_lease_retry_backoff(operation_id)
                lease_attempt = 1
            if kind == "retry" and self._is_connectivity_error(
                self._last_sync_error
            ):
                network_attempt = self._v2_network_retry_attempts.get(
                    operation_id, 0
                ) + 1
                self._v2_network_retry_attempts[operation_id] = network_attempt
            else:
                self._v2_network_retry_attempts.pop(operation_id, None)
                network_attempt = 1
            follow_up_delay = self._v2_follow_up_delay_ms(
                kind,
                self._last_sync_error,
                lease_attempt,
                network_attempt,
            )
            if follow_up_delay is not None:
                self._schedule_v2_retry(follow_up_delay)

        # resultReady can arrive just before QThread.run() returns. Keep the worker
        # alive until QThread's native finished signal confirms the thread stopped.
        worker.resultReady.connect(handle_finished)

        def cleanup_worker():
            if worker in self._v2_workers:
                self._v2_workers.remove(worker)
            worker.deleteLater()

        worker.finished.connect(cleanup_worker)
        self._start_worker(worker)
        return worker

    def check_and_acquire_lock(self, project_name, relative_path, session_id, client=None):
        """
        Check if the file is locked by another session.
        If not, acquire the lock for the current session.
        Returns: (success(bool), message(str))
        """
        if self.is_v2_enabled:
            server_state = self._current_project_server_state()
            if server_state in {"trashed", "purged"}:
                return (
                    True,
                    "서버 동기화가 중지된 작품입니다. 변경 내용은 로컬에만 저장됩니다.",
                )
            if not is_live_document_path(relative_path):
                return False, "휴지통 문서는 읽기 전용입니다. 복원한 뒤 편집해주세요."
            if not self.can_save_path(relative_path):
                return False, "이미 삭제된 문서입니다. 새 문서를 만들어 이름을 지정해주세요."
            document = self._v2_store.ensure_document(
                self._v2_context["local_key"], relative_path
            )
            if is_forced_offline():
                return True, "테스트 오프라인 상태로 편집합니다. 저장 내용은 재시도 큐에 보관됩니다."
            if document["revision"] == 0:
                return True, "새 로컬 문서입니다. 첫 저장 때 서버에 등록됩니다."
            supabase = client or self.supabase
            if not supabase:
                return True, "오프라인 상태로 편집합니다. 저장 내용은 재시도 큐에 보관됩니다."
            if getattr(supabase, "_antigravity_authenticated", None) is False:
                return (
                    False,
                    "클라우드 동기화 계정에 로그인이 되어있지 않습니다.\n"
                    "설정탭 / 클라우드 계정 로그인을 확인해주세요.",
                )
            try:
                self._acquire_v2_lease(document["document_id"], supabase)
                return True, "Lock acquired."
            except Exception as error:
                code = self._stable_error_code(error)
                if code == "LEASE_CONFLICT":
                    return False, "다른 기기에서 이미 편집 중인 문서입니다."
                if code == "SERVER_RPC_PERMISSION_DENIED":
                    return (
                        False,
                        "서버 동기화 권한 설정이 완료되지 않았습니다. "
                        "Supabase V2 RPC 권한을 적용한 뒤 문서를 다시 열어주세요.",
                    )
                if self._is_connectivity_error(str(error)):
                    return True, "오프라인 상태로 편집합니다. 저장 내용은 재시도 큐에 보관됩니다."
                return False, code or str(error)

        supabase = client or self.supabase
        if not supabase:
            return True, "Mock mode: Lock acquired."
            
        for attempt in range(2):
            try:
                # Query the editor_locks table
                resp = supabase.table("editor_locks").select("*").eq("project_name", project_name).eq("relative_path", relative_path).execute()
                
                if resp.data:
                    lock_info = resp.data[0]
                    locked_by = lock_info.get("locked_by")
                    locked_at_str = lock_info.get("locked_at")
                    
                    if locked_by and locked_by != session_id:
                        import datetime as dt
                        is_dead = False
                        if locked_at_str:
                            try:
                                locked_at = dt.datetime.fromisoformat(locked_at_str.replace("Z", "+00:00"))
                                now = dt.datetime.now(dt.timezone.utc)
                                if (now - locked_at).total_seconds() > 60:
                                    is_dead = True
                            except Exception as e:
                                print(f"Error parsing lock locked_at: {e}")
                        
                        if not is_dead:
                            return False, "다른 기기에서 이미 편집 중인 파일입니다."
                        else:
                            print(f"Dead lock 감지됨 ({relative_path}). 강제로 락을 뺏어옵니다.")
                
                # Acquire lock
                import datetime as dt
                supabase.table("editor_locks").upsert({
                    "project_name": project_name,
                    "relative_path": relative_path,
                    "locked_by": session_id,
                    "locked_at": dt.datetime.now(dt.timezone.utc).isoformat()
                }).execute()
                
                if attempt == 1:
                    print("✅ 서버 통신선 자동 재연결 및 복구 성공!")
                
                return True, "Lock acquired."
                
            except Exception as e:
                if attempt == 0:
                    # 유휴 커넥션(Keep-Alive) 만료로 인한 연결 끊김일 수 있으므로 클라이언트 재초기화 후 재시도
                    print(f"⚠️ 서버 유휴 만료 감지됨 (에러: {e}). 즉시 통신선 재연결을 시도합니다...")
                    self.init_supabase()
                else:
                    # 두 번째에도 실패하면 조용히 오프라인 모드로 넘김 (콘솔 도배 방지)
                    print(f"❌ 오프라인 모드 전환 (서버 통신 완전 실패): {e}")
                    return True, "오프라인 상태로 편집을 허용합니다."

    def heartbeat_lock(self, project_name, relative_path, session_id, client=None):
        if self.is_v2_enabled:
            if is_forced_offline():
                return
            document = self._v2_store.get_document(self._v2_context["local_key"], relative_path)
            if not document or document["revision"] == 0:
                return
            token = self._v2_leases.get(document["document_id"])
            supabase = client or self.supabase
            if not token or not supabase:
                return
            try:
                self._call_with_session(
                    lambda: supabase.rpc("renew_edit_lease", {
                        "p_document_id": document["document_id"],
                        "p_device_id": self._v2_device_id,
                        "p_lease_token": token,
                        "p_ttl_seconds": 90,
                    }).execute(),
                    supabase,
                )
            except Exception as error:
                print(f"Failed to renew v2 edit lease: {error}")
            return

        supabase = client or self.supabase
        if not supabase:
            return
        try:
            # Trigger updates updated_at automatically, but we explicitly update locked_at
            import datetime as dt
            supabase.table("editor_locks").upsert({
                "project_name": project_name,
                "relative_path": relative_path,
                "locked_by": session_id,
                "locked_at": dt.datetime.now(dt.timezone.utc).isoformat()
            }).execute()
        except Exception as e:
            print(f"Failed to heartbeat lock: {e}")

    def release_lock(self, project_name, relative_path, session_id, client=None):
        if self.is_v2_enabled:
            document = self._v2_store.get_document(self._v2_context["local_key"], relative_path)
            if not document:
                return True
            return self._release_v2_lease(document["document_id"], client=client)

        supabase = client or self.supabase
        if not supabase:
            return True
            
        try:
            supabase.table("editor_locks").delete().eq("project_name", project_name).eq("relative_path", relative_path).eq("locked_by", session_id).execute()
            return True
        except Exception as e:
            print(f"Failed to release lock: {e}")
            return False

    def get_file_updated_at(self, project_name, relative_path, client=None):
        if self.is_v2_enabled:
            if not is_live_document_path(relative_path):
                return 0
            document = self._v2_store.get_document(self._v2_context["local_key"], relative_path)
            return document["revision"] if document else 0

        supabase = client or self.supabase
        if not supabase:
            return None
        try:
            resp = supabase.table("writing_contents").select("updated_at").eq("project_name", project_name).eq("relative_path", relative_path).execute()
            if resp.data:
                return resp.data[0].get("updated_at")
        except Exception as e:
            print(f"Failed to fetch updated_at for {relative_path}: {e}")
        return None

    def upload_content_async(self, wpm, project_name, relative_path, content, callback=None, local_updated_at=None, force_overwrite=False, conflict_callback=None):
        if self.is_v2_enabled:
            if not is_live_document_path(relative_path):
                if callback:
                    callback(
                        True,
                        "휴지통 문서는 클라우드 저장 대상에서 제외됩니다.",
                        relative_path,
                        None,
                    )
                return None
            if not self.can_save_path(relative_path):
                if callback:
                    callback(
                        True,
                        "삭제된 문서의 늦은 저장을 무시했습니다.",
                        relative_path,
                        None,
                    )
                return None
            if (
                not force_overwrite
                and self.would_erase_nonempty_document(relative_path, content)
            ):
                error = self.report_empty_content_guard(relative_path)
                if callback:
                    callback(False, error, relative_path, None)
                return None
            if wpm and relative_path and not wpm.write_text_file(relative_path, content):
                if callback:
                    callback(False, "로컬 파일 저장에 실패했습니다.", relative_path, None)
                return None
            recovery_keys = {
                self._tree_path_comparison_key(path)
                for path in (self._v2_untracked_recovery_paths or set())
            }
            if self._tree_path_comparison_key(relative_path) in recovery_keys:
                message = (
                    "서버 문서 UUID 확인이 끝날 때까지 업로드를 보류했습니다. "
                    "로컬 파일은 저장되었습니다."
                )
                if callback:
                    callback(False, message, relative_path, None)
                self.pull_remote_changes_async()
                return None
            document = self._v2_store.get_document(
                self._v2_context["local_key"], relative_path
            )
            if (
                document
                and int(document.get("revision") or 0) > 0
                and not document.get("is_deleted")
                and document.get("server_path") == relative_path
                and document.get("base_content") == content
                and not self._v2_store.has_active_operations(document["document_id"])
            ):
                if callback:
                    callback(True, "", relative_path, document["revision"])
                self._publish_sync_state()
                return None
            operation = self._v2_store.enqueue(
                self._v2_context, relative_path, content, relative_path=relative_path
            )
            if callback:
                self._v2_callbacks[operation["operation_id"]] = callback
            if conflict_callback:
                self._v2_conflict_callbacks[operation["operation_id"]] = conflict_callback
            self._publish_sync_state()
            self.retry_pending_syncs()
            return self._v2_worker

        key = ("content", project_name, relative_path)
        payload = {
            "kind": "content",
            "wpm": wpm,
            "project_name": project_name,
            "relative_path": relative_path,
            "content": content,
            "callback": callback,
            "local_updated_at": local_updated_at,
            "force_overwrite": force_overwrite,
            "conflict_callback": conflict_callback,
        }
        return self._launch_content_upload(payload, key, is_retry=False)

    def _launch_content_upload(self, payload, key, is_retry=False):
        worker = SaveWorker(
            self.supabase,
            None if is_retry else payload["wpm"],
            payload["project_name"],
            payload["relative_path"],
            payload["content"],
            payload["local_updated_at"],
            payload["force_overwrite"],
        )
        self._workers.append(worker)

        def cleanup_worker():
            if worker in self._workers:
                self._workers.remove(worker)

        def handle_finished(success, error_msg, rel_path, new_updated_at):
            server_success, effective_error = self._complete_server_attempt(
                key, payload, success, error_msg, worker, is_retry
            )
            callback = payload.get("callback")
            if callback:
                callback(server_success, effective_error, rel_path, new_updated_at)
            if server_success:
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, self.retry_pending_syncs)

        conflict_callback = payload.get("conflict_callback")
        if conflict_callback:
            worker.conflict_detected.connect(conflict_callback)

        self._active_server_syncs += 1
        self._publish_sync_state()
        worker.resultReady.connect(handle_finished)
        worker.finished.connect(cleanup_worker)
        worker.finished.connect(worker.deleteLater)
        self._start_worker(worker)

        return worker

    def upload_all_content_async(self, wpm, project_name, callback=None):
        if self.is_v2_enabled:
            queued = 0
            root = getattr(wpm, "writing_root_path", None)
            if root and os.path.isdir(root):
                for current_root, dirs, files in os.walk(root):
                    relative_root = os.path.relpath(current_root, root).replace("\\", "/")
                    if relative_root != "." and not is_live_document_path(relative_root):
                        dirs[:] = []
                        continue
                    dirs[:] = [
                        name for name in dirs
                        if is_live_document_path(
                            name if relative_root == "." else f"{relative_root}/{name}"
                        )
                    ]
                    for filename in files:
                        if not filename.endswith(".txt"):
                            continue
                        full_path = os.path.join(current_root, filename)
                        relative_path = os.path.relpath(full_path, root).replace("\\", "/")
                        try:
                            with open(full_path, "r", encoding="utf-8") as file:
                                content = file.read()
                            self._v2_store.enqueue(
                                self._v2_context, relative_path, content, relative_path
                            )
                            queued += 1
                        except OSError as error:
                            print(f"v2 bulk queue error ({relative_path}): {error}")
            self._publish_sync_state()
            self.retry_pending_syncs()
            if callback:
                callback(True, "" if queued else "저장할 문서가 없습니다.")
            return self._v2_worker

        key = ("bulk", project_name)
        payload = {
            "kind": "bulk",
            "wpm": wpm,
            "writing_root_path": getattr(wpm, "writing_root_path", None),
            "project_name": project_name,
            "callback": callback,
        }
        return self._launch_bulk_upload(payload, key, is_retry=False)

    def _launch_bulk_upload(self, payload, key, is_retry=False):
        bulk_source = SimpleNamespace(writing_root_path=payload["writing_root_path"])
        worker = BulkSaveWorker(self.supabase, bulk_source, payload["project_name"])
        self._bulk_workers.append(worker)

        def cleanup_worker():
            if worker in self._bulk_workers:
                self._bulk_workers.remove(worker)

        def handle_finished(success, error_msg):
            server_success, effective_error = self._complete_server_attempt(
                key, payload, success, error_msg, worker, is_retry
            )
            callback = payload.get("callback")
            if callback:
                callback(server_success, effective_error)
            if server_success:
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, self.retry_pending_syncs)

        self._active_server_syncs += 1
        self._publish_sync_state()
        worker.resultReady.connect(handle_finished)
        worker.finished.connect(cleanup_worker)
        worker.finished.connect(worker.deleteLater)
        self._start_worker(worker)

        return worker

    def upload_history_async(self, wpm, project_name, relative_path, content, callback=None):
        if self.is_v2_enabled:
            return self.upload_autosave_async(wpm, relative_path, content, callback)

        key = ("history", project_name, relative_path)
        payload = {
            "kind": "history",
            "wpm": wpm,
            "project_name": project_name,
            "relative_path": relative_path,
            "content": content,
            "callback": callback,
        }
        return self._launch_history_upload(payload, key, is_retry=False)

    def _launch_history_upload(self, payload, key, is_retry=False):
        worker = BackupWorker(
            self.supabase,
            None if is_retry else payload["wpm"],
            payload["project_name"],
            payload["relative_path"],
            payload["content"],
        )
        self._history_workers.append(worker)

        def cleanup_worker():
            if worker in self._history_workers:
                self._history_workers.remove(worker)

        def handle_finished(success, error_msg):
            if is_retry:
                server_success, effective_error = self._complete_server_attempt(
                    key, payload, success, error_msg, worker, is_retry=True
                )
            else:
                self._active_backups = max(0, self._active_backups - 1)
                server_success = bool(success and getattr(worker, "supabase", None) is not None)
                effective_error = error_msg or ("" if server_success else "서버 연결 없음")
                if server_success:
                    self._retry_queue.pop(key, None)
                    self._last_sync_error = ""
                    self._last_failure_offline = False
                else:
                    self._queue_retry(
                        key,
                        payload,
                        effective_error,
                        offline=getattr(worker, "supabase", None) is None or self._is_connectivity_error(effective_error),
                    )
                self._publish_sync_state()

            callback = payload.get("callback")
            if callback:
                callback(server_success, effective_error)
            if server_success:
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, self.retry_pending_syncs)

        if is_retry:
            self._active_server_syncs += 1
        else:
            self._active_backups += 1
        self._publish_sync_state()
        worker.resultReady.connect(handle_finished)
        worker.finished.connect(cleanup_worker)
        worker.finished.connect(worker.deleteLater)
        self._start_worker(worker)

        return worker

    def upload_autosave_async(self, wpm, relative_path, content, callback=None):
        if self._shutting_down:
            return None
        worker_key = (id(wpm), str(relative_path or ""))
        existing = self._autosave_workers_by_path.get(worker_key)
        if existing is not None:
            pending = self._autosave_followups.get(worker_key)
            callbacks = list(pending[3]) if pending else []
            if callback:
                callbacks.append(callback)
            self._autosave_followups[worker_key] = (
                wpm, relative_path, content, callbacks
            )
            return existing

        worker = AutoSaveWorker(wpm, relative_path, content)
        self._autosave_workers.append(worker)
        self._autosave_workers_by_path[worker_key] = worker
        
        def cleanup_worker():
            if worker in self._autosave_workers:
                self._autosave_workers.remove(worker)
            if self._autosave_workers_by_path.get(worker_key) is worker:
                self._autosave_workers_by_path.pop(worker_key, None)
            followup = self._autosave_followups.pop(worker_key, None)
            if followup and not self._shutting_down:
                with self._autosave_followup_lock:
                    self._autosave_ready_followups.append(followup)
                QMetaObject.invokeMethod(
                    self,
                    "_drain_autosave_followups",
                    Qt.ConnectionType.QueuedConnection,
                )
                
        def handle_finished(success, error_msg):
            self._active_backups = max(0, self._active_backups - 1)
            if success:
                self._publish_sync_state()
            else:
                self._set_sync_state("failed", f"자동백업 실패: {error_msg}")
            if callback:
                callback(success, error_msg)

        self._active_backups += 1
        self._publish_sync_state()
        worker.resultReady.connect(handle_finished)
        worker.finished.connect(
            cleanup_worker,
            Qt.ConnectionType.DirectConnection,
        )
        worker.finished.connect(worker.deleteLater)
        self._start_worker(worker)
        
        return worker

    def _start_autosave_followup(
        self, wpm, relative_path, content, callbacks
    ):
        if self._shutting_down:
            return None

        def notify_callbacks(success, error_msg):
            for pending_callback in callbacks or []:
                pending_callback(success, error_msg)

        return self.upload_autosave_async(
            wpm,
            relative_path,
            content,
            notify_callbacks if callbacks else None,
        )

    @pyqtSlot()
    def _drain_autosave_followups(self):
        while True:
            with self._autosave_followup_lock:
                if not self._autosave_ready_followups:
                    return
                followup = self._autosave_ready_followups.pop(0)
            self._start_autosave_followup(*followup)

    def rename_item_async(self, project_name, old_rel_path, new_rel_path, callback=None):
        if self.is_v2_enabled:
            try:
                self.record_path_change(old_rel_path, new_rel_path)
                if callback:
                    callback(True, "")
            except Exception as error:
                if callback:
                    callback(False, str(error))
            return self._v2_worker

        worker = RenameWorker(self.supabase, project_name, old_rel_path, new_rel_path)
        self._rename_workers.append(worker)

        def cleanup_worker():
            if worker in self._rename_workers:
                self._rename_workers.remove(worker)

        if callback:
            worker.resultReady.connect(callback)
        worker.finished.connect(cleanup_worker)
        worker.finished.connect(worker.deleteLater)
        self._start_worker(worker)
        return worker

    def _contract_path_change_intents(self, old_rel_path, new_rel_path):
        local_key = self._v2_context["local_key"]
        old_rel_path = self._safe_relative_path(old_rel_path)
        new_rel_path = self._safe_relative_path(new_rel_path)
        root = self._v2_wpm.writing_root_path
        new_full_path = os.path.join(root, new_rel_path)
        old_parent = old_rel_path.rsplit("/", 1)[0] if "/" in old_rel_path else ""
        new_parent = new_rel_path.rsplit("/", 1)[0] if "/" in new_rel_path else ""
        old_name = old_rel_path.rsplit("/", 1)[-1]
        new_name = new_rel_path.rsplit("/", 1)[-1]
        intents = []
        pending_folder = None

        folder = self._v2_store.get_folder_by_path(local_key, old_rel_path)
        if folder is None and os.path.isdir(new_full_path):
            parent_folder_id = self._folder_id_for_path(
                "" if new_parent in {"", "메인"} else new_parent
            )
            folder = {
                "folder_id": str(uuid.uuid4()),
                "local_path": new_rel_path,
                "parent_folder_id": parent_folder_id,
                "revision": 0,
            }
            pending_folder = dict(folder)
        if folder is not None:
            parent_folder_id = self._folder_id_for_path(
                "" if new_parent in {"", "메인"} else new_parent
            )
            revision = int(folder.get("revision") or 0)
            if revision == 0:
                intents.append({
                    "entity_kind": "folder",
                    "entity_id": folder["folder_id"],
                    "intent_kind": "create",
                    "base_revision": 0,
                    "payload": {
                        "name": new_name,
                        "parent_folder_id": parent_folder_id,
                    },
                })
            else:
                if old_name != new_name:
                    intents.append({
                        "entity_kind": "folder",
                        "entity_id": folder["folder_id"],
                        "intent_kind": "rename",
                        "base_revision": revision,
                        "payload": {"name": new_name},
                    })
                    revision += 1
                old_parent_id = folder.get("parent_folder_id")
                if old_parent != new_parent or old_parent_id != parent_folder_id:
                    intents.append({
                        "entity_kind": "folder",
                        "entity_id": folder["folder_id"],
                        "intent_kind": "move",
                        "base_revision": revision,
                        "payload": {"parent_folder_id": parent_folder_id},
                    })
        else:
            document = self._v2_store.get_document(local_key, old_rel_path)
            if document is not None and int(document.get("revision") or 0) > 0:
                revision = int(document.get("structure_revision") or 1)
                if old_name != new_name:
                    intents.append({
                        "entity_kind": "document",
                        "entity_id": document["document_id"],
                        "intent_kind": "rename",
                        "base_revision": revision,
                        "payload": {"name": new_name},
                    })
                    revision += 1
                new_parent_id = self._folder_id_for_path(new_parent)
                if document.get("parent_folder_id") != new_parent_id:
                    intents.append({
                        "entity_kind": "document",
                        "entity_id": document["document_id"],
                        "intent_kind": "move",
                        "base_revision": revision,
                        "payload": {"parent_folder_id": new_parent_id},
                    })

        superseded_entities = set()
        for intent in intents:
            entity_id = intent["entity_id"]
            if entity_id not in superseded_entities:
                previous = self._v2_store.latest_active_structure_operation(
                    entity_id
                )
                if previous:
                    intent["supersedes_operation_id"] = previous["operation_id"]
                superseded_entities.add(entity_id)
        return intents, pending_folder

    def _contract_folder_lifecycle_plan(
        self, old_rel_path, new_rel_path, intent_kind
    ):
        old_rel_path = self._safe_relative_path(old_rel_path)
        new_rel_path = self._safe_relative_path(new_rel_path)
        folders = [
            folder for folder in self._v2_store.list_folders(
                self._v2_context["local_key"]
            )
            if folder["local_path"] == old_rel_path
            or folder["local_path"].startswith(old_rel_path + "/")
        ]
        if intent_kind == "delete":
            folders = [folder for folder in folders if not folder["is_deleted"]]
            folders.sort(key=lambda item: item["local_path"].count("/"), reverse=True)
        else:
            folders = [folder for folder in folders if folder["is_deleted"]]
            folders.sort(key=lambda item: item["local_path"].count("/"))
        if not folders:
            return None

        intents = []
        updates = []
        pending_folders = []
        for folder in folders:
            suffix = folder["local_path"][len(old_rel_path):]
            updated_path = new_rel_path + suffix
            parent_id = folder.get("parent_folder_id")
            payload = {}
            if intent_kind == "restore":
                if not suffix:
                    parent_path = (
                        new_rel_path.rsplit("/", 1)[0]
                        if "/" in new_rel_path else ""
                    )
                    parent_id = self._folder_id_for_path(
                        "" if parent_path in {"", "메인"} else parent_path
                    )
                payload = {
                    "name": updated_path.rsplit("/", 1)[-1],
                    "parent_folder_id": parent_id,
                }
                pending_folders.append({
                    "folder_id": folder["folder_id"],
                    "local_path": updated_path,
                    "parent_folder_id": parent_id,
                })
            intent = {
                "entity_kind": "folder",
                "entity_id": folder["folder_id"],
                "intent_kind": intent_kind,
                "base_revision": int(folder["revision"]),
                "payload": payload,
            }
            previous = self._v2_store.latest_active_structure_operation(
                folder["folder_id"]
            )
            if previous:
                intent["supersedes_operation_id"] = previous["operation_id"]
            intents.append(intent)
            updates.append({
                "folder_id": folder["folder_id"],
                "local_path": updated_path,
                "parent_folder_id": parent_id,
                "is_deleted": intent_kind == "delete",
            })
        return {
            "contract_structure_intents": intents,
            "contract_path_change": {
                "old_path": old_rel_path,
                "new_path": old_rel_path,
                "folder_updates": updates,
                "pending_folders": pending_folders,
            },
        }

    def record_path_change(self, old_rel_path, new_rel_path, retry=True):
        if not self.is_v2_enabled:
            return []
        if not is_live_document_path(old_rel_path) or not is_live_document_path(new_rel_path):
            return []
        with self._structure_mutation_gate:
            contract_plan = (
                self._contract_path_change_intents(old_rel_path, new_rel_path)
                if self._uses_contract_structure() else None
            )
            if contract_plan is not None:
                contract_intents, pending_folder = contract_plan
                return [{
                    "contract_structure_intents": contract_intents,
                    "contract_path_change": {
                        "old_path": self._safe_relative_path(old_rel_path),
                        "new_path": self._safe_relative_path(new_rel_path),
                        "pending_folder": pending_folder,
                    },
                }]
            moved = self._v2_store.move_local_path(
                self._v2_context["local_key"], old_rel_path, new_rel_path
            )
            root = self._v2_wpm.writing_root_path
            new_full_path = os.path.join(root, new_rel_path)
            if (
                os.path.isfile(new_full_path)
                and new_full_path.endswith(".txt")
                and not moved
            ):
                self._v2_store.ensure_document(
                    self._v2_context["local_key"], new_rel_path
                )
                document = self._v2_store.get_document(
                    self._v2_context["local_key"], new_rel_path
                )
                moved = [{**document, "local_path": new_rel_path}]
            elif os.path.isdir(new_full_path):
                for current_root, dirs, files in os.walk(new_full_path):
                    relative_root = os.path.relpath(current_root, root).replace(
                        "\\", "/"
                    )
                    if not is_live_document_path(relative_root):
                        dirs[:] = []
                        continue
                    dirs[:] = [
                        name for name in dirs
                        if is_live_document_path(f"{relative_root}/{name}")
                    ]
                    for filename in files:
                        if filename.endswith(".txt"):
                            local_path = os.path.relpath(
                                os.path.join(current_root, filename), root
                            ).replace("\\", "/")
                            if not self._v2_store.get_document(
                                self._v2_context["local_key"], local_path
                            ):
                                document = self._v2_store.ensure_document(
                                    self._v2_context["local_key"], local_path
                                )
                                moved.append({**document, "local_path": local_path})

            operations = []
            for document in moved:
                local_path = document["local_path"]
                content = self._v2_wpm.read_text_file(local_path)
                if content is not None:
                    operations.append(self._v2_store.enqueue(
                        self._v2_context,
                        local_path,
                        content,
                        relative_path=local_path,
                    ))
        self._publish_sync_state()
        if retry:
            self.retry_pending_syncs()
        return operations

    def record_tombstone(self, old_rel_path, trash_rel_path, retry=True):
        if not self.is_v2_enabled:
            return []
        with self._structure_mutation_gate:
            folder_operation = None
            contract_structure = self._uses_contract_structure()
            if contract_structure:
                folder_operation = self._contract_folder_lifecycle_plan(
                    old_rel_path, old_rel_path, "delete"
                ) or {
                    "contract_structure_intents": [],
                    "contract_path_change": None,
                }
            # A previously deleted UUID may still reserve the same local trash path
            # even when its physical copy was removed. Relocate the new copy before
            # updating SQLite so repeated delete/restore cycles cannot violate the
            # UNIQUE(local_key, local_path) constraint.
            for _ in range(100):
                conflicts = self._v2_store.move_destination_conflicts(
                    self._v2_context["local_key"], old_rel_path, trash_rel_path
                )
                if not conflicts:
                    break
                trash_rel_path = self._v2_wpm.relocate_trash_item(trash_rel_path)
            else:
                raise RuntimeError("휴지통 문서 경로를 안전하게 확보하지 못했습니다.")

            source_documents = [
                document for document in self._v2_store.list_documents(
                    self._v2_context["local_key"]
                )
                if document["local_path"] == old_rel_path
                or document["local_path"].startswith(old_rel_path + "/")
            ]
            if contract_structure:
                document_changes = []
                for document in source_documents:
                    suffix = document["local_path"][len(old_rel_path):]
                    new_local_path = trash_rel_path + suffix
                    content = self._v2_wpm.read_text_file(new_local_path)
                    if content is None:
                        continue
                    document_changes.append({
                        "document_id": document["document_id"],
                        "old_local_path": document["local_path"],
                        "new_local_path": new_local_path,
                        "relative_path": document["server_path"],
                        "content": content,
                        "is_deleted": True,
                        "enqueue": not (
                            document["revision"] == 0
                            and not self._v2_store.has_active_operations(
                                document["document_id"]
                            )
                        ),
                    })
                folder_operation["contract_document_changes"] = document_changes
                moved = [{
                    **document,
                    "old_local_path": document["local_path"],
                    "local_path": trash_rel_path
                    + document["local_path"][len(old_rel_path):],
                } for document in source_documents]
            else:
                moved = self._v2_store.move_local_path(
                    self._v2_context["local_key"], old_rel_path, trash_rel_path
                )
                for document in moved:
                    # Keep a dependent tombstone behind an unfinished create.
                    if (
                        document["revision"] == 0
                        and not self._v2_store.has_active_operations(
                            document["document_id"]
                        )
                    ):
                        continue
                    content = self._v2_wpm.read_text_file(document["local_path"])
                    if content is not None:
                        self._v2_store.enqueue(
                            self._v2_context,
                            document["local_path"],
                            content,
                            relative_path=document["server_path"],
                            is_deleted=True,
                        )
            if moved:
                self._v2_wpm.update_trash_metadata(
                    trash_rel_path,
                    document_id=min(
                        str(document.get("document_id") or "") for document in moved
                    ),
                )
        self._publish_sync_state()
        if retry:
            self.retry_pending_syncs()
        return ([folder_operation] if folder_operation else []) + moved

    def record_restore(
        self, trash_rel_path, restored_rel_path,
        original_rel_path=None, retry=True,
    ):
        if not self.is_v2_enabled:
            return []
        with self._structure_mutation_gate:
            folder_operation = None
            contract_structure = self._uses_contract_structure()
            if contract_structure and not original_rel_path:
                raise SyncContractError("CONTRACT_STRUCTURE_IDS_REQUIRED")
            if contract_structure and original_rel_path:
                folder_operation = self._contract_folder_lifecycle_plan(
                    original_rel_path, restored_rel_path, "restore"
                ) or {
                    "contract_structure_intents": [],
                    "contract_path_change": None,
                }
            source_documents = [
                document for document in self._v2_store.list_documents(
                    self._v2_context["local_key"]
                )
                if document["local_path"] == trash_rel_path
                or document["local_path"].startswith(trash_rel_path + "/")
            ]
            if contract_structure and folder_operation is not None:
                document_changes = []
                for document in source_documents:
                    suffix = document["local_path"][len(trash_rel_path):]
                    new_local_path = restored_rel_path + suffix
                    content = self._v2_wpm.read_text_file(new_local_path)
                    if content is None:
                        continue
                    document_changes.append({
                        "document_id": document["document_id"],
                        "old_local_path": document["local_path"],
                        "new_local_path": new_local_path,
                        "relative_path": new_local_path,
                        "content": content,
                        "is_deleted": False,
                    })
                folder_operation["contract_document_changes"] = document_changes
                moved = [{
                    **document,
                    "old_local_path": document["local_path"],
                    "local_path": restored_rel_path
                    + document["local_path"][len(trash_rel_path):],
                } for document in source_documents]
            else:
                moved = self._v2_store.move_local_path(
                    self._v2_context["local_key"], trash_rel_path, restored_rel_path
                )
                for document in moved:
                    content = self._v2_wpm.read_text_file(document["local_path"])
                    if content is not None:
                        operation = self._v2_store.enqueue(
                            self._v2_context,
                            document["local_path"],
                            content,
                            relative_path=document["local_path"],
                            is_deleted=False,
                        )
                        # The binder holds its tree order back until these
                        # land, and it needs the operation id to know what
                        # it is waiting for. The moved row alone identifies
                        # the document but not the work in flight.
                        document["operation_id"] = operation["operation_id"]
        self._publish_sync_state()
        if retry:
            self.retry_pending_syncs()
        return ([folder_operation] if folder_operation else []) + moved

    def run_retention_async(self, wpm, callback=None):
        if self._retention_worker is not None:
            try:
                if self._retention_worker.isRunning():
                    return self._retention_worker
            except RuntimeError:
                self._retention_worker = None

        worker = RetentionWorker(wpm)
        self._retention_workers.append(worker)
        self._retention_worker = worker
        
        def cleanup_worker():
            if worker in self._retention_workers:
                self._retention_workers.remove(worker)
            if self._retention_worker is worker:
                self._retention_worker = None
                
        if callback:
            worker.resultReady.connect(callback)
        worker.finished.connect(
            cleanup_worker,
            Qt.ConnectionType.DirectConnection,
        )
        worker.finished.connect(worker.deleteLater)
        self._start_worker(worker)
        
        return worker

    def _unique_workers(self):
        """Collect every tracked worker once, keeping a strong reference."""
        lists = [
            getattr(self, '_workers', []),
            getattr(self, '_bulk_workers', []),
            getattr(self, '_history_workers', []),
            getattr(self, '_autosave_workers', []),
            getattr(self, '_retention_workers', []),
            getattr(self, '_rename_workers', []),
            getattr(self, '_lock_workers', []),
            getattr(self, '_v2_workers', []),
            getattr(self, '_server_action_workers', []),
            list(getattr(self, 'active_workers', set())),
        ]
        seen = set()
        workers = []
        for worker_list in lists:
            for worker in list(worker_list):
                if id(worker) in seen:
                    continue
                seen.add(id(worker))
                workers.append(worker)
        return workers

    def wait_all_workers(self, timeout_ms=None):
        """Wait for tracked threads, never terminating them.

        Returns True when every worker finished. With no timeout and no open
        shutdown window the wait stays unbounded, as long-running callers rely
        on that.
        """
        if timeout_ms is None:
            timeout_ms = self.shutdown_remaining_ms()
        deadline = (
            None
            if timeout_ms is None
            else time.monotonic() + max(0, int(timeout_ms)) / 1000.0
        )
        drained = True
        for worker in self._unique_workers():
            try:
                if not worker.isRunning():
                    continue
                if deadline is None:
                    worker.wait()
                    continue
                remaining = int(round((deadline - time.monotonic()) * 1000))
                if remaining <= 0 or not worker.wait(remaining):
                    drained = False
            except RuntimeError:
                pass
        return drained

    def shutdown(self):
        """Stop retries, drain background work in budget, and close HTTP pools."""
        self.begin_shutdown()
        self._shutting_down = True
        self._autosave_followups.clear()
        with self._autosave_followup_lock:
            self._autosave_ready_followups.clear()
        drained = self.wait_all_workers()
        if not drained:
            # 예산이 끝나도 스레드를 terminate() 하지 않는다. 실행 중인 QThread 를
            # 파괴하면 종료가 크래시로 끝나므로 in-flight HTTP timeout 만큼만 더
            # 기다린 뒤 남은 작업은 다음 실행으로 넘긴다.
            drained = self.wait_all_workers(SHUTDOWN_GRACE_MS)
        self._diagnostics.flush(timeout_ms=SHUTDOWN_GRACE_MS)
        if drained and self.supabase is not None:
            # 워커가 아직 client 를 쓰고 있을 수 있으므로 완전히 비었을 때만 닫는다.
            self._close_supabase_client(self.supabase)
        return drained

class RetentionWorker(QThread):
    resultReady = pyqtSignal(bool, str)
    
    def __init__(self, wpm):
        super().__init__()
        self.wpm = wpm

    def run(self):
        try:
            import os, re
            from datetime import datetime, timedelta
            
            if not self.wpm or not self.wpm.current_project:
                self.resultReady.emit(False, "No project")
                return
                
            backup_dir = os.path.join(self.wpm.workspace_dir, self.wpm.current_project, "집필모드", "백업", "자동저장")
            if not os.path.exists(backup_dir):
                self.resultReady.emit(True, "No backup dir")
                return
                
            now = datetime.now()
            cutoff_1h = now - timedelta(hours=1)
            cutoff_24h = now - timedelta(hours=24)
            
            deleted_count = 0
            
            for doc_dir in os.listdir(backup_dir):
                doc_path = os.path.join(backup_dir, doc_dir)
                if not os.path.isdir(doc_path): continue
                
                files = []
                pattern = re.compile(r'.*?_(\d{8}_\d{4})\.txt$')
                for f in os.listdir(doc_path):
                    if not f.endswith('.txt'): continue
                    m = pattern.match(f)
                    if m:
                        try:
                            dt = datetime.strptime(m.group(1), '%Y%m%d_%H%M')
                            files.append((f, dt, os.path.join(doc_path, f)))
                        except:
                            pass
                            
                keep_files = set()
                group_1h_24h = {}
                group_over_24h = {}
                
                for f, dt, path in files:
                    if dt > cutoff_1h:
                        keep_files.add(f)
                    elif dt > cutoff_24h:
                        key = dt.strftime('%Y%m%d_%H')
                        if key not in group_1h_24h or dt > group_1h_24h[key][1]:
                            group_1h_24h[key] = (f, dt)
                    else:
                        key = dt.strftime('%Y%m%d')
                        if key not in group_over_24h or dt > group_over_24h[key][1]:
                            group_over_24h[key] = (f, dt)
                            
                for f, dt in group_1h_24h.values():
                    keep_files.add(f)
                for f, dt in group_over_24h.values():
                    keep_files.add(f)
                    
                for f, dt, path in files:
                    if f not in keep_files:
                        try:
                            os.remove(path)
                            deleted_count += 1
                        except:
                            pass
                            
            print(f"[RetentionWorker] Deleted {deleted_count} old auto-save files.")
            self.resultReady.emit(True, str(deleted_count))
        except Exception as e:
            print(f"[RetentionWorker] Error: {e}")
            self.resultReady.emit(False, str(e))
