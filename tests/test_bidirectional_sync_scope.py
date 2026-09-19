"""New proposed-scope tests only. No live DB, credentials, application or server."""
import copy
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bidirectional_sync_scope import (BodyRoundTripScope, ScopeDenied, BASE, WINDOWS, IPAD,
    PROJECT_ID, PROJECT_NAME, DOCUMENT_ID, ENDPOINT, PATH, content_sha)

ACCOUNT = "10000000-0000-4000-8000-000000000001"
DEVICE = "10000000-0000-4000-8000-000000000002"
LEASE = "10000000-0000-4000-8000-000000000003"

def operation(sender="windows"):
    return {"operation_id": str(uuid.uuid4()), "project_id": PROJECT_ID, "document_id": DOCUMENT_ID,
        "relative_path": PATH, "local_path": PATH, "content": WINDOWS if sender == "windows" else IPAD,
        "base_content": BASE if sender == "windows" else WINDOWS,
        "base_revision": 1 if sender == "windows" else 2, "is_deleted": False,
        "provenance_kind": "LEGACY_EPOCH_0"}

def arm(scope, op, **changes):
    values = dict(endpoint=ENDPOINT, account_id=ACCOUNT, mode="LEGACY", epoch=0,
                  target_active_ids=[op["operation_id"]])
    values.update(changes)
    scope.arm(op, **values)

def rpc(scope, name, params, **changes):
    context = dict(endpoint=ENDPOINT, account_id=ACCOUNT)
    context.update(changes)
    scope.consume_rpc(name, params, **context)

def lease(scope):
    rpc(scope, "acquire_edit_lease", {"p_document_id": DOCUMENT_ID, "p_device_id": DEVICE,
                                     "p_ttl_seconds": 90 if scope.sender == "windows" else 60})
    scope.record_lease(LEASE)

def commit(op):
    return {"p_document_id": DOCUMENT_ID, "p_project_id": PROJECT_ID, "p_base_revision": op["base_revision"],
        "p_operation_id": op["operation_id"], "p_device_id": DEVICE, "p_relative_path": PATH,
        "p_content": op["content"], "p_is_deleted": False, "p_lease_token": LEASE}

class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.scope = BodyRoundTripScope("windows", account_id=ACCOUNT, device_id=DEVICE)
        self.op = operation()

    def test_both_directions_exact_revision_hash_and_single_commit(self):
        for sender in ("windows", "ipad"):
            scope = BodyRoundTripScope(sender, account_id=ACCOUNT, device_id=DEVICE)
            op = operation(sender); arm(scope, op); lease(scope)
            rpc(scope, "commit_document", commit(op))
            scope.record_receipt(operation_id=op["operation_id"], revision=op["base_revision"] + 1, content_hash=content_sha(op["content"]))
            self.assertEqual(scope.observation()["commit_outcome"], "confirmed")
            with self.assertRaises(ScopeDenied): rpc(scope, "commit_document", commit(op))

    def test_unarmed_and_new_process_cannot_send(self):
        arm(self.scope, self.op)
        fresh = BodyRoundTripScope("windows", account_id=ACCOUNT, device_id=DEVICE)
        with self.assertRaises(ScopeDenied): rpc(fresh, "commit_document", commit(self.op))

    def test_wrong_project_document_path_content_revision_and_contract_rejected(self):
        for key, value in [("project_id", "d8f50b5f-ae0e-42f8-9296-5d5885a5b304"),
                ("document_id", str(uuid.uuid4())), ("relative_path", "__antigravity__/tree-order.json"),
                ("local_path", "elsewhere"), ("content", WINDOWS + "extra"), ("base_content", ""),
                ("base_revision", 2), ("base_revision", True), ("is_deleted", True),
                ("provenance_kind", "CONTRACT_BATCH")]:
            with self.subTest(key=key, value=value):
                op = {**self.op, key: value}
                with self.assertRaises(ScopeDenied): arm(self.scope, op)

    def test_wrong_mode_account_epoch_endpoint_and_queue_rejected(self):
        for change in [dict(mode="ID_BASED"), dict(epoch=1), dict(epoch=False),
                dict(account_id=str(uuid.uuid4())), dict(endpoint="https://example.invalid"),
                dict(target_active_ids=[]), dict(target_active_ids=[self.op["operation_id"], "old"] )]:
            with self.assertRaises(ScopeDenied): arm(self.scope, self.op, **change)

    def test_rpc_scope_checks_extra_parameters_and_context_at_execution(self):
        arm(self.scope, self.op); lease(self.scope)
        for params in [{**commit(self.op), "extra": 1}, {**commit(self.op), "p_document_id": str(uuid.uuid4())},
                       {**commit(self.op), "p_base_revision": True}, {**commit(self.op), "p_is_deleted": 0}]:
            with self.assertRaises(ScopeDenied): rpc(self.scope, "commit_document", params)
        for change in [dict(account_id=str(uuid.uuid4())), dict(endpoint=ENDPOINT + "/")]:
            with self.assertRaises(ScopeDenied): rpc(self.scope, "commit_document", commit(self.op), **change)
        self.assertNotIn("commit_document", self.scope.observation()["rpc_attempts"])

    def test_unknown_response_consumes_budget_but_allows_one_lease_cleanup(self):
        arm(self.scope, self.op); lease(self.scope)
        rpc(self.scope, "commit_document", commit(self.op))  # network exception would follow
        self.assertEqual(self.scope.observation()["commit_outcome"], "unknown")
        with self.assertRaises(ScopeDenied): rpc(self.scope, "commit_document", commit(self.op))
        params = {"p_document_id": DOCUMENT_ID, "p_device_id": DEVICE, "p_lease_token": LEASE}
        rpc(self.scope, "release_edit_lease", params)
        with self.assertRaises(ScopeDenied): rpc(self.scope, "release_edit_lease", params)

    def test_wrong_receipt_does_not_confirm_or_rearm(self):
        arm(self.scope, self.op); lease(self.scope); rpc(self.scope, "commit_document", commit(self.op))
        for change in [dict(operation_id=str(uuid.uuid4())), dict(revision=9), dict(content_hash="wrong")]:
            receipt = dict(operation_id=self.op["operation_id"], revision=2, content_hash=content_sha(WINDOWS)); receipt.update(change)
            with self.assertRaises(ScopeDenied): self.scope.record_receipt(**receipt)
        with self.assertRaises(ScopeDenied): arm(self.scope, self.op)
        self.assertEqual(self.scope.observation()["commit_outcome"], "unknown")

    def test_folder_history_structure_migration_heartbeat_and_old_queue_denied(self):
        arm(self.scope, self.op)
        for name in ["commit_folder", "document_commit", "structure_batch", "begin_project_sync_migration",
                     "renew_edit_lease", "upload_history"]:
            with self.assertRaises(ScopeDenied): rpc(self.scope, name, {})
        self.assertEqual(self.scope.observation()["rpc_attempts"], {})

    def test_stop_invalidates_existing_grant_and_late_response(self):
        arm(self.scope, self.op); lease(self.scope); self.scope.stop()
        with self.assertRaises(ScopeDenied): rpc(self.scope, "commit_document", commit(self.op))
        with self.assertRaises(ScopeDenied): self.scope.record_lease(LEASE)
        with self.assertRaises(ScopeDenied): arm(self.scope, self.op)

    def test_operation_mutation_does_not_expand_bound_payload(self):
        arm(self.scope, self.op); lease(self.scope)
        self.op["content"] += "later edit"
        with self.assertRaises(ScopeDenied): rpc(self.scope, "commit_document", commit(self.op))

    def test_concurrent_duplicate_commit_has_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        arm(self.scope, self.op); lease(self.scope)
        def send(_):
            try: rpc(self.scope, "commit_document", commit(self.op)); return True
            except ScopeDenied: return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(send, range(8))), 1)

    def test_ipad_proposal_bytes_ttl_and_unknown_response_cleanup_match_candidate(self):
        self.assertEqual((len(WINDOWS.encode()), content_sha(WINDOWS)),
                         (127, "4cf361c4a26d342dbe712fec07f5ff7ccd3610605b471a87f5abc5250e8ca15f"))
        self.assertEqual((len(IPAD.encode()), content_sha(IPAD)),
                         (158, "eee3691fbe8e6805a9d74b56dfd1d5cb9df53ad0c01e6325069cb091e68ff161"))
        scope = BodyRoundTripScope("ipad", account_id=ACCOUNT, device_id=DEVICE)
        op = operation("ipad"); arm(scope, op)
        with self.assertRaises(ScopeDenied):
            rpc(scope, "acquire_edit_lease", {"p_document_id": DOCUMENT_ID, "p_device_id": DEVICE, "p_ttl_seconds": 90})
        lease(scope); rpc(scope, "commit_document", commit(op))
        params = {"p_document_id": DOCUMENT_ID, "p_device_id": DEVICE, "p_lease_token": LEASE}
        with self.assertRaises(ScopeDenied): rpc(scope, "release_edit_lease", params)
        scope.record_receipt(operation_id=op["operation_id"], revision=3, content_hash=content_sha(IPAD))
        rpc(scope, "release_edit_lease", params)

class ProductPathTests(unittest.TestCase):
    """Existing Windows store/manager methods, synthetic storage + fake RPC only.

    The policy is explicitly injected in this fake client. This test is NOT
    evidence that the installed EXE has a new runtime network restriction.
    """
    def test_windows_enqueue_legacy_parent_read_commit_and_reverse_baseline(self):
        from PyQt6.QtWidgets import QApplication
        from sync_manager import SyncManager
        from sync_v2_store import SyncV2Store
        app = QApplication.instance() or QApplication([])
        baseline = json.loads((Path(__file__).parents[1] / "_evidence/windows-control-document-repair-20260911/target-baseline.json").read_text(encoding="utf-8-sig"))
        with tempfile.TemporaryDirectory() as tmp:
            store = SyncV2Store(str(Path(tmp) / "synthetic.sqlite3"))
            ctx = store.configure_project(str(Path(tmp) / "synthetic-project" / "writing"), PROJECT_NAME, PROJECT_ID)
            store.apply_remote_snapshot(ctx, DOCUMENT_ID, PATH, BASE, 1)
            scope = BodyRoundTripScope("windows", account_id=ACCOUNT, device_id=DEVICE)
            calls = []
            class Query:
                def select(self, *args): return self
                def eq(self, key, value):
                    if key == "project_id": self_project = value; assert self_project == PROJECT_ID
                    return self
                def order(self, *args): return self
                def range(self, *args): return self
                def execute(self):
                    calls.append(("read_folders", {}))
                    return SimpleNamespace(data=copy.deepcopy(baseline["folders"]))
            class Client:
                def table(self, name):
                    assert name == "folders"
                    return Query()
                def rpc(self, name, params):
                    def execute():
                        rpc(scope, name, params)
                        calls.append((name, copy.deepcopy(params)))
                        if name == "acquire_edit_lease":
                            scope.record_lease(LEASE)
                            return SimpleNamespace(data={"lease_token": LEASE})
                        if name == "commit_document":
                            return SimpleNamespace(data={"revision": 2, "content_hash": content_sha(WINDOWS)})
                        return SimpleNamespace(data={})
                    return SimpleNamespace(execute=execute)
            manager = SyncManager()
            # This test is run in a fresh isolated process; no real manager exists.
            manager._v2_store, manager._v2_context = store, ctx
            manager._v2_device_id, manager.supabase = DEVICE, Client()
            manager._v2_wpm = SimpleNamespace(write_text_file=lambda path, text: True)
            rows = {r["folder_id"]: r for r in baseline["folders"]}
            def folder_path(r):
                return folder_path(rows[r["parent_folder_id"]]) + "/" + r["name"] if r["parent_folder_id"] else r["name"]
            candidates = [{"uuid": r["folder_id"], "parent_uuid": r["parent_folder_id"],
                "legacy_path": folder_path(r), "wants_deleted": False} for r in rows.values()]
            # Model the filesystem identity adapter only; parent publication and
            # its server projection comparison execute their actual product code.
            with patch.object(manager, "_publishable_identity_folders", return_value=candidates), \
                 patch.object(manager, "ensure_session_valid"), \
                 patch.object(manager, "retry_pending_syncs"), \
                 patch.object(manager, "_publish_sync_state"), \
                 patch("sync_manager.is_forced_offline", return_value=False):
                manager.upload_content_async(manager._v2_wpm, PROJECT_NAME, PATH, WINDOWS)
                op = store.next_ready_operation(ctx["local_key"])
                self.assertEqual(op["provenance_kind"], "LEGACY_EPOCH_0")
                arm(scope, op)
                result = manager._process_v2_operation(op["operation_id"])
                self.assertEqual(result["kind"], "committed", result)
                store.mark_success(op["operation_id"], result["result"])
                scope.record_receipt(operation_id=op["operation_id"], revision=2, content_hash=content_sha(WINDOWS))
                # Emulate the remote iPad revision-3 snapshot at the real store
                # apply boundary. This is not an iPad dispatcher/device test.
                applied = store.apply_remote_snapshot(ctx, DOCUMENT_ID, PATH, IPAD, 3)
                self.assertTrue(applied["applied"])
                final = store.get_document(ctx["local_key"], PATH)
                self.assertEqual((final["revision"], final["base_content"]), (3, IPAD))
                self.assertEqual([name for name, _ in calls], ["ensure_project", "read_folders", "acquire_edit_lease", "commit_document"])
                self.assertEqual(calls[-1][1], commit(op))
            manager._v2_store = manager._v2_context = manager._v2_wpm = None
            manager.supabase = None
            del app

class ReadbackTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = json.loads((Path(__file__).parents[1] / "_evidence/windows-control-document-repair-20260911/target-baseline.json").read_text(encoding="utf-8-sig"))
        # Explicit synthetic mode annotations: historical target JSON does not
        # itself contain these fields and is not a fresh live preflight.
        self.snapshot.update(project_sync_mode="LEGACY", migration_epoch=0, project_sync_settings_rows=0)

    def test_both_baselines_validate_without_trusting_supplied_content_hash(self):
        from bidirectional_sync_scope import validate_readback
        self.assertTrue(validate_readback(self.snapshot, sender="windows"))
        doc = next(row for row in self.snapshot["documents"] if row["document_id"] == DOCUMENT_ID)
        doc.update(content=WINDOWS, revision=2)
        self.assertTrue(validate_readback(self.snapshot, sender="ipad"))
        doc["content"] += "unapproved edit"
        with self.assertRaises(ScopeDenied): validate_readback(self.snapshot, sender="ipad")

    def test_mode_folder_control_nullable_field_and_extra_document_drift_stop(self):
        from bidirectional_sync_scope import validate_readback
        cases = []
        s = copy.deepcopy(self.snapshot); s["project_sync_mode"] = "ID_BASED"; cases.append(s)
        s = copy.deepcopy(self.snapshot); s["migration_epoch"] = False; cases.append(s)
        s = copy.deepcopy(self.snapshot); s["project_sync_settings_rows"] = 1; cases.append(s)
        s = copy.deepcopy(self.snapshot); s["folders"][0]["name"] += "changed"; cases.append(s)
        s = copy.deepcopy(self.snapshot); s["folders"].pop(); cases.append(s)
        s = copy.deepcopy(self.snapshot); s["documents"][1]["revision"] = 2; cases.append(s)
        s = copy.deepcopy(self.snapshot); s["documents"][0]["parent_folder_id"] = str(uuid.uuid4()); cases.append(s)
        s = copy.deepcopy(self.snapshot); s["documents"].append(copy.deepcopy(s["documents"][0])); cases.append(s)
        for snapshot in cases:
            with self.assertRaises(ScopeDenied): validate_readback(snapshot, sender="windows")
