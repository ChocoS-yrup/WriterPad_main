"""Explicit product flow for one LEGACY synthetic manuscript, with no dispatcher.

The UI passes a fresh foreground ticket per action. This module reuses the
product file writer and durable enqueue/attempt/success/snapshot APIs. It never
configures a project, recovers a queue, repairs identity or opens a global gate.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from bidirectional_sync_scope import (BASE, WINDOWS, IPAD, PROJECT_ID, PROJECT_NAME,
    DOCUMENT_ID, PATH, ENDPOINT, CONTROL_ID, CONTROL_SHA, BodyRoundTripScope,
    ScopeDenied, content_sha, validate_readback)

DEVICE_ID = "1d6bf0ad-86ff-43ce-a84a-d220134312e8"
PARENT_ID = "1de12e60-f998-48b9-aae9-7675b4b42fb9"


def require(condition, code):
    if not condition:
        raise ScopeDenied(code)


def legacy_body_order_matches(local_order, wire_order):
    """Account only for Windows' empty trash entry omitted from LEGACY wire.

    Do not call the broader wire normalizer here: it also drops nonempty trash
    descendants and normalizes names/orders, which could hide genuine drift.
    Work on a copy and keep the saved settings and control document unchanged.
    """
    if not isinstance(local_order, dict) or not isinstance(wire_order, dict):
        return False
    comparable = dict(local_order)
    if comparable.get("메인/휴지통") == [] and "메인/휴지통" not in wire_order:
        del comparable["메인/휴지통"]
    return comparable == wire_order


class RunJournal:
    """Append-only evidence, never an execution grant. Existing attempts stop."""
    def __init__(self, path, ticket):
        self.path, self.ticket = Path(path), ticket
        self.handle = None

    def reserve(self):
        self.ticket.check()
        self.handle = self.path.open("x", encoding="utf-8", newline="\n")
        self.append("reserved", {"run_id": self.ticket.run_id})

    def append(self, event, details=None):
        require(self.handle is not None, "JOURNAL_NOT_RESERVED")
        row = {"event": event, "at_utc": datetime.now(timezone.utc).isoformat(), **(details or {})}
        self.handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        self.handle.flush()
        os.fsync(self.handle.fileno())

    def close(self):
        if self.handle:
            self.handle.close()
            self.handle = None


class BodySnapshot:
    """Freeze before validation; no live client is retained by the accepted data."""
    def __init__(self, rows, *, stage, account_id):
        frozen = deepcopy(rows)
        projects = frozen["projects"]
        require(len(projects) == 1 and projects[0]["project_id"] == PROJECT_ID
                and projects[0]["owner_id"] == account_id and projects[0]["name"] == PROJECT_NAME
                and projects[0].get("deleted_at") is None, "REMOTE_PROJECT_CHANGED")
        require(frozen["project_sync_settings"] == [], "REMOTE_MODE_CHANGED")
        for table in ("documents", "folders"):
            require(isinstance(frozen[table], list), "REMOTE_ROWS_INVALID")
            for row in frozen[table]:
                fields = ("document_id", "project_id", "relative_path", "content", "revision", "is_deleted",
                          "deleted_at", "parent_folder_id", "name", "structure_revision") if table == "documents" else (
                          "folder_id", "project_id", "parent_folder_id", "name", "revision", "is_deleted", "deleted_at")
                require(all(k in row for k in fields) and row["project_id"] == PROJECT_ID, "REMOTE_FIELDS_MISSING")
                require(type(row["revision"]) is int and row["is_deleted"] is False
                        and row["deleted_at"] is None, "REMOTE_ROW_CHANGED")
        snapshot = dict(frozen, project_uuid=PROJECT_ID, endpoint=ENDPOINT,
                        project_sync_mode="LEGACY", migration_epoch=0, project_sync_settings_rows=0)
        validate_readback(snapshot, sender={1: "windows", 2: "ipad", 3: "windows_receive"}[stage])
        self._encoded = json.dumps(frozen, ensure_ascii=False, sort_keys=True)

    def rows(self):
        return json.loads(self._encoded)

    @classmethod
    def capture(cls, client, *, stage, account_id, ticket):
        rows = {}
        for table in ("projects", "project_sync_settings", "documents", "folders", "tree_orders"):
            ticket.check()
            rows[table] = client.table(table).select("*").eq("project_id", PROJECT_ID).execute().data
        ticket.check()
        return cls(rows, stage=stage, account_id=account_id)


class BodyValidationService:
    def __init__(self, *, store, wpm, client, transport, ticket, journal_dir, device_id):
        self.store, self.wpm, self.client = store, wpm, client
        self.transport, self.ticket = transport, ticket
        self.account_id = transport.account_id
        self.journal_dir = Path(journal_dir)
        self.device_id = device_id
        require(device_id == DEVICE_ID and self.account_id, "LOCAL_DEVICE_OR_ACCOUNT_CHANGED")
        self.context = store.get_project(store.local_key_for(wpm.writing_root_path))
        require(self.context is not None, "LOCAL_BINDING_MISSING")
        self.context = dict(self.context, writer_device_id=device_id)

    def _active_ids(self, connection=None):
        if connection is None:
            with self.store._reader() as conn:
                return self._active_ids(conn)
        from sync_v2_store import CONTRACT_ACTIVE_STATES
        result = []
        for table in ("sync_operations", "sync_structure_operations"):
            for row in connection.execute("SELECT operation_id FROM " + table + " WHERE local_key = ?",
                                          (self.context["local_key"],)):
                if self.store._derived_state(connection, row[0]) in CONTRACT_ACTIVE_STATES:
                    result.append(row[0])
        return result

    def _check_local(self, snapshot, revision, content, *, expected_active=(), disk_content=None):
        self.ticket.check()
        project = self.store.get_project(self.context["local_key"])
        require(project and project["project_id"] == PROJECT_ID
                and project["project_name"] == PROJECT_NAME and project["project_sync_mode"] == "LEGACY"
                and project["migration_epoch"] == 0 and not project["contract_path_enabled"]
                and project["server_state"] == "active", "LOCAL_BINDING_CHANGED")
        require(self._active_ids() == list(expected_active), "LOCAL_QUEUE_NOT_EXACT")
        require(not self.store.pending_folder_delete_intents(self.context["local_key"])
                and not self.store.pending_folder_rename_intents(self.context["local_key"]), "LOCAL_STRUCTURE_PENDING")
        docs = self.store.list_documents(self.context["local_key"])
        by_id = {d["document_id"]: d for d in docs}
        require(len(docs) == 2 and set(by_id) == {DOCUMENT_ID, CONTROL_ID}, "LOCAL_DOCUMENT_SET_CHANGED")
        doc, control = by_id[DOCUMENT_ID], by_id[CONTROL_ID]
        require(doc["local_path"] == doc["server_path"] == PATH and doc["revision"] == revision
                and doc["base_content"] == content and doc["base_hash"] == content_sha(content)
                and not doc["is_deleted"], "LOCAL_BASELINE_CHANGED")
        require(control["revision"] == 1 and not control["is_deleted"]
                and control["local_path"] == control["server_path"] == "__antigravity__/tree-order.json"
                and content_sha(control["base_content"]) == CONTROL_SHA, "LOCAL_CONTROL_CHANGED")
        root = Path(self.wpm.writing_root_path).absolute()
        require(root.resolve() == root and (root / PATH).read_bytes() == (content if disk_content is None else disk_content).encode("utf-8"), "LOCAL_FILE_CHANGED")
        from project_identity_v1 import read_identity
        identity = read_identity(str(root.parent))
        require(identity["project"]["uuid"] == PROJECT_ID, "LOCAL_IDENTITY_CHANGED")
        nodes = identity["nodes"]
        docs_identity = [n for n in nodes if n["kind"] == "document"]
        require(len(docs_identity) == 1 and docs_identity[0]["uuid"] == DOCUMENT_ID
                and docs_identity[0]["legacy_path"] == PATH and docs_identity[0]["parent_uuid"] == PARENT_ID,
                "LOCAL_DOCUMENT_IDENTITY_CHANGED")
        folder_rows = snapshot.rows()["folders"]
        remote = {r["folder_id"]: r for r in folder_rows}
        local = [n for n in nodes if n["kind"] == "folder"]
        require(len(local) == 11 and {n["uuid"] for n in local} == set(remote), "LOCAL_FOLDER_SET_CHANGED")
        def folder_path(folder_id):
            row = remote[folder_id]
            return (folder_path(row["parent_folder_id"]) + "/" if row["parent_folder_id"] else "") + row["name"]
        for node in local:
            row = remote[node["uuid"]]
            path = root / folder_path(node["uuid"])
            require(node["parent_uuid"] == row["parent_folder_id"]
                    and node["legacy_path"] == folder_path(node["uuid"])
                    and path.is_dir() and path.resolve() == path, "LOCAL_FOLDER_IDENTITY_CHANGED")
        stored_folders = self.store.list_folders(self.context["local_key"])
        require(len(stored_folders) == 11, "LOCAL_FOLDER_PROJECTION_CHANGED")
        for row in stored_folders:
            expected = remote.get(row["folder_id"])
            require(expected and row["local_path"] == folder_path(row["folder_id"])
                    and all(row[k] == expected[k] for k in ("parent_folder_id", "name", "revision", "is_deleted")),
                    "LOCAL_FOLDER_PROJECTION_CHANGED")
        settings = json.loads((root / "설정.json").read_text(encoding="utf-8"))
        expected_order = json.loads(control["base_content"])["tree_order"]
        require(legacy_body_order_matches(settings.get("tree_order"), expected_order), "LOCAL_ORDER_CHANGED")
        by_uuid = {n["uuid"]: n for n in nodes}
        for node in nodes:
            parent_path = by_uuid[node["parent_uuid"]]["legacy_path"] if node["parent_uuid"] else "<root>"
            expected_names = expected_order.get(parent_path, [])
            siblings = sorted((n for n in nodes if n["parent_uuid"] == node["parent_uuid"]), key=lambda n: n["order"])
            require([n["legacy_path"].rsplit("/", 1)[-1] for n in siblings] == expected_names, "LOCAL_IDENTITY_ORDER_CHANGED")
        with self.store._reader() as conn:
            require(conn.execute("SELECT count(*) FROM sync_tree_orders WHERE local_key = ?",
                                 (self.context["local_key"],)).fetchone()[0] == 0, "LOCAL_UUID_ORDER_CHANGED")

    def _rpc(self, name, params):
        self.ticket.check()
        data = self.client.rpc(name, params).execute().data
        self.ticket.check()
        return data[0] if isinstance(data, list) and len(data) == 1 else data

    def send_once(self):
        require(not (self.journal_dir / "send.jsonl").exists(), "PRIOR_SEND_REQUIRES_REVIEW")
        snapshot = BodySnapshot.capture(self.client, stage=1, account_id=self.account_id, ticket=self.ticket)
        self._check_local(snapshot, 1, BASE)
        journal = RunJournal(self.journal_dir / "send.jsonl", self.ticket)
        journal.reserve()
        self.transport.persist_attempt = journal.append
        lease = None
        lease_released = False
        try:
            self.wpm.compare_write_text_file(PATH, BASE.encode(), WINDOWS,
                guard=lambda: self._check_local(snapshot, 1, BASE))
            journal.append("local_saved", {"bytes": 127, "sha256": content_sha(WINDOWS)})
            # Check and enqueue while holding the existing store transaction;
            # never select/drain/recover any earlier operation.
            with self.store._transaction() as conn:
                self.ticket.check()
                require(self._active_ids(conn) == [], "LOCAL_QUEUE_CHANGED_BEFORE_ENQUEUE")
                project = conn.execute("SELECT * FROM sync_projects WHERE local_key=?", (self.context["local_key"],)).fetchone()
                require(project["project_id"] == PROJECT_ID and project["project_sync_mode"] == "LEGACY"
                        and project["migration_epoch"] == 0 and not project["contract_path_enabled"], "LOCAL_MODE_CHANGED_BEFORE_ENQUEUE")
                operation = self.store.enqueue(self.context, PATH, WINDOWS, relative_path=PATH)
            scope = BodyRoundTripScope("windows", account_id=self.account_id, device_id=self.device_id)
            scope.arm(operation, endpoint=ENDPOINT, account_id=self.account_id, mode="LEGACY", epoch=0,
                      target_active_ids=self._active_ids())
            self.transport.scope = scope
            def check_final_rpc(name):
                if name == "release_edit_lease":
                    self._check_local(snapshot, 2, WINDOWS)
                else:
                    self._check_local(snapshot, 1, BASE,
                                      expected_active=[operation["operation_id"]], disk_content=WINDOWS)
                    current = self.store.operation(operation["operation_id"])
                    dispatches = [e for e in self.store.operation_events(operation["operation_id"])
                                  if e["event_type"] == "dispatch_started"]
                    # The store counts completed attempt receipts; the current
                    # inflight attempt is represented by its dispatch event.
                    require(current["status"] == "inflight" and current["attempts"] == 0 and len(dispatches) == 1
                            and all(current[k] == operation[k] for k in ("project_id", "document_id", "relative_path",
                                "local_path", "base_revision", "base_content", "content", "is_deleted", "provenance_kind")),
                            "LOCAL_OPERATION_CHANGED_BEFORE_HTTP")
            self.transport.validate_local_rpc = check_final_rpc
            journal.append("enqueued", {"operation_id": operation["operation_id"]})
            self.ticket.check()
            self.store.mark_attempt(operation["operation_id"])
            result = self._rpc("ensure_project", {"p_project_id": PROJECT_ID, "p_name": PROJECT_NAME})
            require(result.get("project_id") == PROJECT_ID, "ENSURE_RECEIPT_CHANGED")
            result = self._rpc("acquire_edit_lease", {"p_document_id": DOCUMENT_ID,
                "p_device_id": self.device_id, "p_ttl_seconds": 90})
            require(result.get("document_id") == DOCUMENT_ID and result.get("device_id") == self.device_id,
                    "LEASE_RECEIPT_CHANGED")
            lease = result["lease_token"]
            scope.record_lease(lease)
            journal.append("lease_acquired", {"expires_at": result.get("expires_at")})
            result = self._rpc("commit_document", {"p_document_id": DOCUMENT_ID, "p_project_id": PROJECT_ID,
                "p_base_revision": 1, "p_operation_id": operation["operation_id"], "p_device_id": self.device_id,
                "p_relative_path": PATH, "p_content": WINDOWS, "p_is_deleted": False, "p_lease_token": lease})
            require(result.get("status") == "committed", "COMMIT_RECEIPT_CHANGED")
            scope.record_receipt(operation_id=operation["operation_id"], revision=result.get("revision"),
                                 content_hash=result.get("content_hash"))
            # mark_success changes only this operation/document. Preserve the
            # existing local structural metadata in this LEGACY body-only flow.
            local = self.store.get_document_by_id(DOCUMENT_ID)
            receipt = {k: result[k] for k in ("revision", "content_hash", "status")}
            receipt.update({k: local[k] for k in ("parent_folder_id", "name", "structure_revision")})
            self.ticket.check()
            self.store.mark_success(operation["operation_id"], receipt)
            journal.append("commit_confirmed", {"operation_id": operation["operation_id"], **receipt})
            released = self._rpc("release_edit_lease", {"p_document_id": DOCUMENT_ID,
                "p_device_id": self.device_id, "p_lease_token": lease})
            lease = None
            require(released is True, "LEASE_RELEASE_NOT_CONFIRMED")
            lease_released = True
            journal.append("lease_released")
            after = BodySnapshot.capture(self.client, stage=2, account_id=self.account_id, ticket=self.ticket)
            self._check_local(after, 2, WINDOWS)
            journal.append("send_complete", {"revision": 2, "bytes": 127, "sha256": content_sha(WINDOWS)})
            return {"revision": 2, "bytes": 127, "sha256": content_sha(WINDOWS)}
        except Exception as error:
            # Do not clear inflight, infer a failed commit, rebase, retry, or
            # release with an invalidated foreground ticket. Preserve the lease
            # evidence for expiry/receipt review if normal cleanup did not finish.
            # A lost acquire reply can leave a server lease even though no
            # token reached this process. Do not report that case as cleaned up.
            acquire_attempted = any(e["method"] == "POST" and e["path"] == "/rest/v1/rpc/acquire_edit_lease"
                                    for e in self.transport.events)
            journal.append("stopped", {"error_type": type(error).__name__,
                                      "lease_may_remain": acquire_attempted and not lease_released})
            raise
        finally:
            self.transport.scope = None
            self.transport.validate_local_rpc = None
            self.transport.persist_attempt = None
            journal.close()

    def receive_once(self):
        send_rows = [json.loads(line) for line in (self.journal_dir / "send.jsonl").read_text("utf-8").splitlines()]
        require(send_rows[-1]["event"] == "send_complete", "SEND_AND_LEASE_NOT_CONFIRMED")
        require(not (self.journal_dir / "receive.jsonl").exists(), "PRIOR_RECEIVE_REQUIRES_REVIEW")
        snapshot = BodySnapshot.capture(self.client, stage=3, account_id=self.account_id, ticket=self.ticket)
        self._check_local(snapshot, 2, WINDOWS)
        journal = RunJournal(self.journal_dir / "receive.jsonl", self.ticket)
        journal.reserve()
        try:
            # The product receives precisely the validated immutable capture;
            # there is no hydration/refetch between inspection and application.
            body = next(r for r in snapshot.rows()["documents"] if r["document_id"] == DOCUMENT_ID)
            self.wpm.compare_write_text_file(PATH, WINDOWS.encode(), body["content"],
                guard=lambda: self._check_local(snapshot, 2, WINDOWS))
            journal.append("local_saved", {"bytes": 158, "sha256": content_sha(body["content"])})
            self._check_local(snapshot, 2, WINDOWS, disk_content=IPAD)
            applied = self.store.apply_remote_snapshot(self.context, DOCUMENT_ID, PATH, body["content"], body["revision"])
            require(applied.get("applied") is True, "RECEIVE_BASELINE_NOT_APPLIED")
            self._check_local(snapshot, 3, IPAD)
            journal.append("receive_complete", {"revision": 3, "bytes": 158, "sha256": content_sha(IPAD)})
            return {"revision": 3, "bytes": 158, "sha256": content_sha(IPAD)}
        except Exception as error:
            journal.append("stopped", {"error_type": type(error).__name__})
            raise
        finally:
            journal.close()
