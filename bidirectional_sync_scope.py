"""Offline specification for the proposed LEGACY body round trip.

This module has no network, DB, UI or installation entry point. It does NOT
unlock either application. Candidate integrations must enforce these decisions
at enqueue/claim and the final HTTP boundary before any live use is approved.
"""
from copy import deepcopy
from hashlib import sha256
import json
from threading import RLock
from uuid import UUID

PROJECT_ID = "9a78c51c-7de9-43a8-be54-d25a22d08a28"
DOCUMENT_ID = "502cdbe7-814c-42f8-8ed4-81c40cd94902"
PROJECT_NAME = "본문수신검증 20260911"
PATH = "메인/원고/1권/1화.txt"
ENDPOINT = "https://mhpnszcorfzrvhyondxr.supabase.co"
BASE = "본문 수신 검증 20260911\n이 문서는 동기화 시험용 합성 원고입니다.\n끝.\n"
WINDOWS = BASE + "Windows 양방향 검증 20260911\n"
IPAD = WINDOWS + "iPad 양방향 검증 20260911\n"
FOLDER_BASELINE_SHA = "1957a74d4d8460035a7e8f082e7dc55e2ba1675c247eb5eda16c5f66077254ae"
CONTROL_ID = "ef6e1de1-a3d0-5959-96be-58f87a683cc0"
CONTROL_SHA = "1cfe9438a3ff000c3af3b5593f973554b2966e9fd8c29e704e73143fda27b5be"

def content_sha(text):
    return sha256(text.encode("utf-8")).hexdigest()

def proposed_plan():
    return {
        "status": "preparation_only_not_authorized_not_installed",
        "project_id": PROJECT_ID, "document_id": DOCUMENT_ID, "relative_path": PATH,
        "endpoint": ENDPOINT, "mode": "LEGACY", "epoch": 0,
        "settings_row": "absent at last observation; stop if changed",
        "aligned_to_ipad_proposal": "ipad-staging-body-bidirectional-20260911; preparation alignment only",
        "lease_acquire_requested_ttl_seconds": {"windows": 90, "ipad": 60},
        "preserved_server_commit_lease_extension_seconds": 90,
        "server_lease_definition_freshly_verified": False,
        "directions": [{"sender": sender, "base_revision": rev, "result_revision": rev + 1,
            "before": before, "after": after, "before_sha256": content_sha(before),
            "after_sha256": content_sha(after), "after_bytes": len(after.encode("utf-8")),
            "commit_attempt_limit": 1, "operation_id": "bind newly enqueued product UUID before dispatch; never reuse historical upload ID"}
            for sender, rev, before, after in [("windows", 1, BASE, WINDOWS), ("ipad", 2, WINDOWS, IPAD)]],
        "success_does_not_prove": "ID_BASED general automatic synchronization",
        "runtime_integration_complete": False,
    }

class ScopeDenied(ValueError):
    pass

def validate_readback(snapshot, *, sender):
    """Validate an exported server readback, never open a real SQLite database.

    This checks data shape only, not freshness, provenance, credentials, device
    state or user approval. An execution coordinator must independently prove
    those. Last observed evidence must never be labelled a fresh readback.
    """
    if sender not in {"windows", "ipad", "windows_receive"}:
        raise ScopeDenied("UNKNOWN_DIRECTION")
    if (snapshot.get("project_uuid") != PROJECT_ID or snapshot.get("endpoint") != ENDPOINT
            or snapshot.get("project_sync_mode") != "LEGACY" or type(snapshot.get("migration_epoch")) is not int
            or snapshot.get("migration_epoch") != 0 or type(snapshot.get("project_sync_settings_rows")) is not int
            or snapshot.get("project_sync_settings_rows") != 0):
        raise ScopeDenied("READBACK_CONTEXT_MISMATCH")
    folders = snapshot.get("folders")
    documents = snapshot.get("documents")
    if not isinstance(folders, list) or len(folders) != 11 or not isinstance(documents, list) or len(documents) != 2 or snapshot.get("tree_orders") != []:
        raise ScopeDenied("READBACK_ENTITY_SET_MISMATCH")
    fields = ("folder_id", "parent_folder_id", "name", "revision", "is_deleted")
    try:
        rows = sorted([{key: row[key] for key in fields} for row in folders], key=lambda row: row["folder_id"])
        actual = sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    except (KeyError, TypeError):
        raise ScopeDenied("INVALID_FOLDER_READBACK") from None
    if actual != FOLDER_BASELINE_SHA:
        raise ScopeDenied("FOLDER_BASELINE_CHANGED")
    by_id = {row.get("document_id"): row for row in documents}
    if set(by_id) != {DOCUMENT_ID, CONTROL_ID}:
        raise ScopeDenied("DOCUMENT_SET_CHANGED")
    body, revision = {"windows": (BASE, 1), "ipad": (WINDOWS, 2),
                      "windows_receive": (IPAD, 3)}[sender]
    for doc_id, expected_path, expected_revision, expected_sha in [
            (DOCUMENT_ID, PATH, revision, content_sha(body)),
            (CONTROL_ID, "__antigravity__/tree-order.json", 1, CONTROL_SHA)]:
        row = by_id[doc_id]
        if (row.get("project_id") != PROJECT_ID or row.get("relative_path") != expected_path
                or type(row.get("revision")) is not int or row.get("revision") != expected_revision
                or row.get("is_deleted") is not False or row.get("deleted_at") is not None
                or any(row.get(k) is not None for k in ("parent_folder_id", "name", "structure_revision"))
                or not isinstance(row.get("content"), str) or content_sha(row["content"]) != expected_sha):
            raise ScopeDenied("DOCUMENT_BASELINE_CHANGED")
    return True

class BodyRoundTripScope:
    """Fail-closed reference state machine; no persisted grant is accepted.

    New process/object starts locked. Each instance permits one operation only;
    an unknown commit response consumes the attempt and requires receipt review.
    The one lease cleanup is still possible after a successful lease response.
    """
    def __init__(self, sender, *, account_id, device_id):
        if sender not in {"windows", "ipad"}:
            raise ScopeDenied("UNKNOWN_DIRECTION")
        UUID(account_id); UUID(device_id)
        self.sender = sender
        self.account_id, self.device_id = account_id, device_id
        self.before, self.after, self.revision = (BASE, WINDOWS, 1) if sender == "windows" else (WINDOWS, IPAD, 2)
        self._lock = RLock()
        self._operation = None
        self._armed_once = False
        self._counts = {}
        self._lease = None
        self._commit_outcome = None
        self._stopped = False

    def arm(self, operation, *, endpoint, account_id, mode, epoch, target_active_ids):
        with self._lock:
            if self._armed_once or self._stopped:
                raise ScopeDenied("NEW_SCOPE_REQUIRED")
            if endpoint != ENDPOINT or account_id != self.account_id or mode != "LEGACY" or type(epoch) is not int or epoch != 0:
                raise ScopeDenied("CONTEXT_MISMATCH")
            expected = {"project_id": PROJECT_ID, "document_id": DOCUMENT_ID, "relative_path": PATH,
                        "local_path": PATH, "content": self.after, "base_content": self.before,
                        "base_revision": self.revision, "is_deleted": False}
            if (any(operation.get(key) != value for key, value in expected.items())
                    or type(operation.get("base_revision")) is not int or operation.get("is_deleted") not in (False, 0)):
                raise ScopeDenied("OPERATION_MISMATCH")
            if operation.get("provenance_kind") != "LEGACY_EPOCH_0":
                raise ScopeDenied("PROTOCOL_MISMATCH")
            operation_id = operation.get("operation_id")
            try:
                UUID(operation_id)
            except (ValueError, TypeError, AttributeError):
                raise ScopeDenied("INVALID_OPERATION_ID") from None
            if list(target_active_ids) != [operation_id]:
                raise ScopeDenied("TARGET_QUEUE_NOT_EXACT")
            self._operation = deepcopy(operation)
            self._armed_once = True

    def consume_rpc(self, name, params, *, endpoint, account_id):
        """Consume just before invoking transport, not when constructing a request.

        Return is a decision only. The caller must not perform auth retries,
        redirects or SDK retries below this boundary without rechecking here.
        """
        with self._lock:
            if self._stopped or not self._operation or endpoint != ENDPOINT or account_id != self.account_id:
                raise ScopeDenied("LOCKED_OR_CONTEXT_CHANGED")
            expected = None
            if name == "ensure_project" and self.sender == "windows" and not self._counts.get("commit_document"):
                expected = {"p_project_id": PROJECT_ID, "p_name": PROJECT_NAME}
            elif name == "acquire_edit_lease" and not self._counts.get("commit_document"):
                expected = {"p_document_id": DOCUMENT_ID, "p_device_id": self.device_id, "p_ttl_seconds": 90 if self.sender == "windows" else 60}
            elif name == "commit_document" and self._lease:
                expected = {"p_document_id": DOCUMENT_ID, "p_project_id": PROJECT_ID,
                    "p_base_revision": self.revision, "p_operation_id": self._operation["operation_id"],
                    "p_device_id": self.device_id, "p_relative_path": PATH, "p_content": self.after,
                    "p_is_deleted": False, "p_lease_token": self._lease}
            elif name == "release_edit_lease" and self._lease and (self.sender == "windows" or self._commit_outcome == "confirmed"):
                expected = {"p_document_id": DOCUMENT_ID, "p_device_id": self.device_id, "p_lease_token": self._lease}
            try:
                exact_payload = json.dumps(params, sort_keys=True, allow_nan=False) == json.dumps(expected, sort_keys=True, allow_nan=False)
            except (TypeError, ValueError):
                exact_payload = False
            if expected is None or not exact_payload or self._counts.get(name, 0):
                raise ScopeDenied("RPC_OUTSIDE_SCOPE_OR_ALREADY_ATTEMPTED")
            # Consume before transport: exceptions/timeouts must not restore this budget.
            self._counts[name] = 1
            if name == "commit_document":
                self._commit_outcome = "unknown"
            if name == "release_edit_lease":
                self._lease = None

    def record_lease(self, token):
        with self._lock:
            if self._stopped or self._counts.get("acquire_edit_lease") != 1 or self._lease is not None or self._counts.get("release_edit_lease"):
                raise ScopeDenied("LEASE_NOT_EXPECTED")
            UUID(token)
            self._lease = token

    def record_receipt(self, *, operation_id, revision, content_hash):
        with self._lock:
            if (self._stopped or not self._operation or self._counts.get("commit_document") != 1
                    or operation_id != self._operation["operation_id"] or type(revision) is not int
                    or revision != self.revision + 1 or content_hash != content_sha(self.after)):
                raise ScopeDenied("RECEIPT_MISMATCH")
            self._commit_outcome = "confirmed"

    def stop(self):
        with self._lock:
            self._stopped = True
            self._lease = None

    def observation(self):
        with self._lock:
            return {"sender": self.sender, "armed": self._armed_once and not self._stopped,
                    "rpc_attempts": dict(self._counts), "commit_outcome": self._commit_outcome,
                    "automatic_retry_allowed": False, "restart_grant_allowed": False,
                    "is_runtime_application_guard": False}
