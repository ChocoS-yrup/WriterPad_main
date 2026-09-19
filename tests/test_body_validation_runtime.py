"""Candidate runtime checks: synthetic SQLite/files and real SDK over fake HTTP."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from supabase import create_client, ClientOptions

from bidirectional_sync_scope import BASE, WINDOWS, IPAD, PATH, PROJECT_ID, PROJECT_NAME, DOCUMENT_ID, CONTROL_ID, ENDPOINT, ScopeDenied, content_sha
from body_validation_transport import BodyHTTPTransport, ForegroundTicket
from body_validation_service import BodyValidationService, BodySnapshot, DEVICE_ID
from project_manager_writing import WritingProjectManager
from sync_v2_store import SyncV2Store

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "10000000-0000-4000-8000-000000000001"
TOKEN = "isolated-access-token"
LEASE = "10000000-0000-4000-8000-000000000003"


def fixture():
    baseline = json.loads((ROOT / "_evidence/windows-control-document-repair-20260911/target-baseline.json").read_text("utf-8-sig"))
    for row in baseline["folders"]:
        row["project_id"] = PROJECT_ID
    return {"projects": [{"project_id": PROJECT_ID, "owner_id": ACCOUNT, "name": PROJECT_NAME, "deleted_at": None}],
            "project_sync_settings": [], **{k: baseline[k] for k in ("documents", "folders", "tree_orders")}}


class FakeServer:
    def __init__(self):
        self.rows = fixture()
        self.calls = []
        self.failure = None
        self.on_request = None

    def body(self):
        return next(d for d in self.rows["documents"] if d["document_id"] == DOCUMENT_ID)

    def __call__(self, request):
        path = request.url.path.rsplit("/", 1)[-1]
        self.calls.append((request.method, path))
        if self.on_request:
            self.on_request(path)
        if request.method == "GET":
            return httpx.Response(200, json=copy.deepcopy(self.rows[path]))
        payload = json.loads(request.content)
        if path == "ensure_project":
            result = {"project_id": PROJECT_ID}
        elif path == "acquire_edit_lease":
            if self.failure == "acquire_timeout":
                raise httpx.ReadTimeout("synthetic lost acquire response")
            result = {"document_id": DOCUMENT_ID, "device_id": DEVICE_ID,
                      "lease_token": LEASE, "expires_at": "2026-09-11T23:00:00Z"}
        elif path == "commit_document":
            if self.failure == "conflict":
                return httpx.Response(409, json={"code": "P0001", "message": "REVISION_CONFLICT", "details": "", "hint": ""})
            self.body().update(content=payload["p_content"], revision=2)
            if self.failure == "timeout":
                raise httpx.ReadTimeout("synthetic lost response")
            result = {"revision": 2, "content_hash": content_sha(WINDOWS), "status": "committed"}
            if self.failure == "wrong_hash":
                result["content_hash"] = "bad"
        elif path == "release_edit_lease":
            result = self.failure != "release_false"
        else:
            raise AssertionError("Unexpected fake HTTP: " + path)
        return httpx.Response(200, json=result)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.writing = self.root / PROJECT_NAME / "집필모드"
        self.writing.mkdir(parents=True)
        self.journals = self.root / "journals"
        self.journals.mkdir()
        self.server = FakeServer()
        self.rows = self.server.rows
        self.folder_map = {r["folder_id"]: r for r in self.rows["folders"]}
        control = next(d for d in self.rows["documents"] if d["document_id"] == CONTROL_ID)
        order = json.loads(control["content"])["tree_order"]
        def path(fid):
            row = self.folder_map[fid]
            return (path(row["parent_folder_id"]) + "/" if row["parent_folder_id"] else "") + row["name"]
        nodes = []
        for index, row in enumerate(self.rows["folders"]):
            rel = path(row["folder_id"])
            (self.writing / rel).mkdir(parents=True, exist_ok=True)
            nodes.append({"uuid": row["folder_id"], "kind": "folder", "parent_uuid": row["parent_folder_id"],
                "legacy_path": rel, "path": rel, "title": row["name"],
                "order": order[rel.rsplit("/", 1)[0] if "/" in rel else "<root>"].index(row["name"])})
        nodes.append({"uuid": DOCUMENT_ID, "kind": "document", "parent_uuid": "1de12e60-f998-48b9-aae9-7675b4b42fb9",
            "legacy_path": PATH, "path": PATH, "title": "1화", "order": 0})
        identity_dir = self.writing.parent / ".writerpad"
        identity_dir.mkdir()
        (identity_dir / "identity-v1.json").write_text(json.dumps({"format_version": 1, "project": {"uuid": PROJECT_ID}, "nodes": nodes}, ensure_ascii=False), "utf-8")
        (self.writing / PATH).write_bytes(BASE.encode())
        control = next(d for d in self.rows["documents"] if d["document_id"] == CONTROL_ID)
        local_order = copy.deepcopy(json.loads(control["content"])["tree_order"])
        # Real Windows settings retain the empty trash entry. The LEGACY wire
        # normalizer omits it; copying wire JSON here concealed that distinction.
        local_order["메인/휴지통"] = []
        (self.writing / "설정.json").write_text(json.dumps({"tree_order": local_order}, ensure_ascii=False), "utf-8")
        self.store = SyncV2Store(str(self.root / "sync.sqlite3"))
        self.context = self.store.configure_project(str(self.writing), PROJECT_NAME, PROJECT_ID)
        for d in self.rows["documents"]:
            self.store.ensure_document(self.context["local_key"], d["relative_path"], "", d["document_id"])
            self.store.apply_remote_snapshot(self.context, d["document_id"], d["relative_path"], d["content"], 1)
        self.store.replace_folder_snapshots(self.context["local_key"], [dict(r, local_path=path(r["folder_id"])) for r in self.rows["folders"]])
        self.other = self.store.configure_project(str(self.root / "other" / "집필모드"), "일반 시험", "d8f50b5f-ae0e-42f8-9296-5d5885a5b304")
        self.other_op = self.store.enqueue(self.other, "other.txt", "leave this queued")
        with self.store._transaction() as conn:
            conn.execute("UPDATE sync_projects SET project_sync_mode='MIGRATING', migration_epoch=1 WHERE local_key=?", (self.other["local_key"],))
            conn.execute("UPDATE sync_projects SET project_sync_mode='ID_BASED', migration_epoch=1 WHERE local_key=?", (self.other["local_key"],))
        self.other_before = self.store.operation(self.other_op["operation_id"])
        self.wpm = object.__new__(WritingProjectManager)
        self.wpm.writing_root_path = str(self.writing)
        self.clients = []
        self.service = self.new_service()

    def tearDown(self):
        for client in self.clients:
            client._test_http.close()
        self.tmp.cleanup()

    def new_service(self, *, clock=None):
        ticket = ForegroundTicket(**({"clock": clock} if clock else {}))
        guard = BodyHTTPTransport(ticket, inner=httpx.MockTransport(self.server))
        http = httpx.Client(transport=guard, follow_redirects=False, trust_env=False)
        client = create_client(ENDPOINT, "isolated-publishable-key", options=ClientOptions(httpx_client=http, auto_refresh_token=False))
        client.postgrest.auth(TOKEN)
        client._test_http = http
        self.clients.append(client)
        guard.bind_verified_session(ACCOUNT, TOKEN)
        return BodyValidationService(store=self.store, wpm=self.wpm, client=client, transport=guard,
            ticket=ticket, journal_dir=self.journals, device_id=DEVICE_ID)

    def db_dump(self):
        with self.store._reader() as conn:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            return {t: [tuple(r) for r in conn.execute('SELECT * FROM "' + t + '" ORDER BY rowid')]
                    for t in tables if t != "sqlite_sequence"}

    def files(self):
        return {p.relative_to(self.writing.parent).as_posix(): p.read_bytes()
                for p in self.writing.parent.rglob("*") if p.is_file()}

    def test_round_trip_real_store_writer_sdk_and_transport_preserve_other_work(self):
        before = self.db_dump()
        files = self.files()
        result = self.service.send_once()
        self.assertEqual(result["bytes"], 127)
        self.assertEqual((self.writing / PATH).read_bytes(), WINDOWS.encode())
        self.assertEqual(self.store.operation(self.other_op["operation_id"]), self.other_before)
        self.server.body().update(content=IPAD, revision=3)
        received = self.new_service().receive_once()
        self.assertEqual(received["bytes"], 158)
        self.assertEqual((self.writing / PATH).read_bytes(), IPAD.encode())
        self.assertEqual(self.store.get_document_by_id(DOCUMENT_ID)["revision"], 3)
        self.assertEqual(self.store.operation(self.other_op["operation_id"]), self.other_before)
        for table, rows in before.items():
            if table not in {"sync_documents", "sync_operations", "sync_operation_events", "sync_operation_attempts"}:
                self.assertEqual(self.db_dump()[table], rows, table)
        after_files = self.files()
        for name, data in files.items():
            if name != "집필모드/" + PATH:
                self.assertEqual(after_files[name], data, name)
        self.assertEqual([p for method, p in self.server.calls if method == "POST"],
                         ["ensure_project", "acquire_edit_lease", "commit_document", "release_edit_lease"])

    def test_restart_after_send_cannot_send_again(self):
        self.service.send_once()
        calls = len(self.server.calls)
        with self.assertRaises(ScopeDenied):
            self.new_service().send_once()
        self.assertEqual(len(self.server.calls), calls)

    def test_unknown_response_keeps_inflight_no_cleanup_or_replay(self):
        self.server.failure = "timeout"
        with self.assertRaises(Exception):
            self.service.send_once()
        op = self.service._active_ids()[0]
        self.assertEqual(self.store.operation(op)["status"], "inflight")
        self.assertEqual(self.server.body()["revision"], 2)
        with self.assertRaises(ScopeDenied):
            self.new_service().send_once()
        self.assertEqual([p for m, p in self.server.calls if m == "POST"].count("commit_document"), 1)
        self.assertNotIn(("POST", "release_edit_lease"), self.server.calls)

    def test_conflict_has_no_rebase_no_new_operation_no_retry(self):
        self.server.failure = "conflict"
        with self.assertRaises(Exception):
            self.service.send_once()
        active = self.service._active_ids()
        self.assertEqual(len(active), 1)
        self.assertEqual(self.store.operation(active[0])["status"], "inflight")
        self.assertEqual(len([p for m, p in self.server.calls if m == "POST"]), 3)

    def test_wrong_receipt_does_not_complete_operation(self):
        self.server.failure = "wrong_hash"
        with self.assertRaises(ScopeDenied):
            self.service.send_once()
        self.assertEqual(len(self.service._active_ids()), 1)
        self.assertEqual(self.store.get_document_by_id(DOCUMENT_ID)["revision"], 1)

    def test_failed_release_blocks_next_direction(self):
        self.server.failure = "release_false"
        with self.assertRaises(ScopeDenied):
            self.service.send_once()
        with self.assertRaises(ScopeDenied):
            self.new_service().receive_once()

    def test_server_snapshot_drift_before_send_preserves_all_local_rows_and_bytes(self):
        before, files = self.db_dump(), self.files()
        cases = []
        r = fixture(); r["documents"][0]["content"] += "x"; cases.append(r)
        r = fixture(); r["documents"].append(copy.deepcopy(r["documents"][0])); cases.append(r)
        r = fixture(); r["folders"][0]["name"] += "x"; cases.append(r)
        r = fixture(); r["project_sync_settings"] = [{"project_sync_mode": "ID_BASED"}]; cases.append(r)
        r = fixture(); r["projects"][0]["owner_id"] = str(uuid4()); cases.append(r)
        r = fixture(); r["documents"][0].pop("structure_revision"); cases.append(r)
        r = fixture(); r["tree_orders"] = [{}]; cases.append(r)
        for rows in cases:
            self.server.rows = rows
            with self.assertRaises(ScopeDenied):
                self.new_service().send_once()
            self.assertEqual(self.db_dump(), before)
            self.assertEqual(self.files(), files)
        self.assertFalse(list(self.journals.iterdir()))
        self.assertFalse(any(m == "POST" for m, _ in self.server.calls))

    def test_target_pending_operation_rejected_without_cancelling_or_claiming_it(self):
        self.store.enqueue(self.context, PATH, "prior user change")
        before, files = self.db_dump(), self.files()
        with self.assertRaises(ScopeDenied):
            self.service.send_once()
        self.assertEqual(self.db_dump(), before)
        self.assertEqual(self.files(), files)

    def test_local_mode_or_binding_changed_rejected_before_file_write(self):
        with self.store._transaction() as conn:
            conn.execute("UPDATE sync_projects SET project_sync_mode='MIGRATING', migration_epoch=1 WHERE local_key=?", (self.context["local_key"],))
        before, files = self.db_dump(), self.files()
        with self.assertRaises(ScopeDenied):
            self.service.send_once()
        self.assertEqual(self.db_dump(), before)
        self.assertEqual(self.files(), files)

    def test_cancel_before_action_makes_no_http_or_local_mutation(self):
        before, files = self.db_dump(), self.files()
        self.service.ticket.cancel()
        with self.assertRaises(ScopeDenied):
            self.service.send_once()
        self.assertFalse(self.server.calls)
        self.assertEqual(self.db_dump(), before)
        self.assertEqual(self.files(), files)

    def test_cancel_during_commit_response_leaves_inflight(self):
        self.server.on_request = lambda path: self.service.ticket.cancel() if path == "commit_document" else None
        with self.assertRaises(ScopeDenied):
            self.service.send_once()
        self.assertEqual(self.store.operation(self.service._active_ids()[0])["status"], "inflight")
        self.assertNotIn(("POST", "release_edit_lease"), self.server.calls)

    def test_grant_expiry_between_queries_stops_without_local_writes(self):
        now = [0.0]
        service = self.new_service(clock=lambda: now[0])
        before, files = self.db_dump(), self.files()
        self.server.on_request = lambda path: now.__setitem__(0, 301.0)
        with self.assertRaises(ScopeDenied):
            service.send_once()
        self.assertEqual(len(self.server.calls), 1)
        self.assertEqual(self.db_dump(), before)
        self.assertEqual(self.files(), files)

    def test_receive_drift_rejected_before_any_file_or_row_apply(self):
        self.service.send_once()
        self.server.body().update(content=IPAD, revision=3)
        self.server.rows["folders"].pop()
        before, files = self.db_dump(), self.files()
        with self.assertRaises(ScopeDenied):
            self.new_service().receive_once()
        self.assertEqual(self.db_dump(), before)
        self.assertEqual(self.files(), files)

    def test_receive_uses_frozen_content_if_live_rows_change_after_capture(self):
        self.service.send_once()
        self.server.body().update(content=IPAD, revision=3)
        service = self.new_service()
        check = service._check_local
        def mutate_after_validation(*args, **kwargs):
            check(*args, **kwargs)
            self.server.body()["content"] = "changed after capture"
        with patch.object(service, "_check_local", side_effect=mutate_after_validation):
            service.receive_once()
        self.assertEqual((self.writing / PATH).read_bytes(), IPAD.encode())
        self.assertEqual([p for m, p in self.server.calls if m == "GET"].count("documents"), 3)

    def test_compare_save_preserves_changed_content_and_recovery_temp(self):
        target = self.writing / PATH
        target.write_bytes(b"changed")
        recovery = Path(str(target) + ".tmp")
        recovery.write_bytes(b"preserve")
        with self.assertRaises(ValueError):
            self.wpm.compare_write_text_file(PATH, BASE.encode(), WINDOWS, guard=lambda: None)
        self.assertEqual(target.read_bytes(), b"changed")
        self.assertEqual(recovery.read_bytes(), b"preserve")

    def test_save_then_enqueue_failure_is_durable_and_not_replayed(self):
        with patch.object(self.store, "enqueue", side_effect=OSError("synthetic enqueue failure")):
            with self.assertRaises(OSError):
                self.service.send_once()
        self.assertEqual((self.writing / PATH).read_bytes(), WINDOWS.encode())
        with self.assertRaises(ScopeDenied):
            self.new_service().send_once()
        self.assertFalse(any(m == "POST" for m, _ in self.server.calls))

    def test_final_http_rejects_other_project_protocol_method_query_schema_and_session(self):
        guard = self.service.transport
        requests = [
            httpx.Request("POST", ENDPOINT + "/rest/v1/rpc/atomic_structure_commit", json={}),
            httpx.Request("POST", ENDPOINT + "/rest/v1/rpc/commit_folder", json={}),
            httpx.Request("PATCH", ENDPOINT + "/rest/v1/documents", json={}),
            httpx.Request("GET", ENDPOINT + "/rest/v1/documents", params={"select": "*"}),
            httpx.Request("GET", ENDPOINT + "/rest/v1/documents", params={"select": "*", "project_id": "eq.wrong"}),
            httpx.Request("GET", "https://example.invalid/rest/v1/documents"),
            httpx.Request("GET", ENDPOINT + "/rest/v1/documents", params={"select": "*", "project_id": "eq." + PROJECT_ID}, headers={"accept-profile": "private"}),
            httpx.Request("GET", ENDPOINT + "/auth/v1/user"),
        ]
        for request in requests:
            request.headers["authorization"] = "Bearer " + TOKEN
            with self.assertRaises(ScopeDenied):
                guard.handle_request(request)
        request = httpx.Request("GET", ENDPOINT + "/rest/v1/documents", params={"select": "*", "project_id": "eq." + PROJECT_ID}, headers={"authorization": "Bearer changed"})
        with self.assertRaises(ScopeDenied):
            guard.handle_request(request)
        self.assertFalse(self.server.calls)

    def test_final_http_disallows_redirect_followup_and_replayed_commit(self):
        self.service.send_once()
        self.service.transport.scope = None
        with self.assertRaises(ScopeDenied):
            self.service.client.rpc("commit_document", {}).execute()
        with self.assertRaises(ScopeDenied):
            self.service.transport.handle_request(httpx.Request("GET", "http://mhpnszcorfzrvhyondxr.supabase.co/rest/v1/documents"))

    def test_existing_store_attachment_does_not_upgrade_or_recover(self):
        before = self.db_dump()
        attached = SyncV2Store.open_existing_current_schema(self.store.db_path)
        self.assertEqual(attached.operation(self.other_op["operation_id"]), self.other_before)
        self.assertEqual(self.db_dump(), before)
        with self.assertRaises(ValueError):
            SyncV2Store.open_existing_current_schema(str(self.root / "missing.sqlite3"))

    def test_snapshot_returns_detached_copies(self):
        snapshot = BodySnapshot(self.rows, stage=1, account_id=ACCOUNT)
        self.rows["documents"][0]["content"] = "later"
        returned = snapshot.rows()
        returned["documents"][0]["content"] = "mutated"
        self.assertEqual(snapshot.rows()["documents"][0]["content"], BASE)

    def test_order_comparison_accepts_only_empty_trash_representation_without_mutation(self):
        from body_validation_service import legacy_body_order_matches
        control = next(d for d in self.rows["documents"] if d["document_id"] == CONTROL_ID)
        wire = json.loads(control["content"])["tree_order"]
        local = {**copy.deepcopy(wire), "메인/휴지통": []}
        before = copy.deepcopy(local)
        self.assertTrue(legacy_body_order_matches(local, wire))
        self.assertTrue(legacy_body_order_matches(wire, wire))
        self.assertEqual(local, before)
        cases = [None, [], {**local, "메인/휴지통": ["deleted.txt"]}, {**local, "메인/휴지통": None},
                 {**local, "메인/휴지통": {}}, {**local, "메인/휴지통/하위": []}, {**local, "unknown": []}]
        r = copy.deepcopy(local); r["메인"].reverse(); cases.append(r)
        r = copy.deepcopy(local); del r["메인/메모장"]; cases.append(r)
        r = copy.deepcopy(local); r["메인/원고/1권"] = ["2화.txt"]; cases.append(r)
        for changed in cases:
            with self.subTest(changed=changed):
                self.assertFalse(legacy_body_order_matches(changed, wire))

    def test_nonempty_trash_or_actual_order_change_stops_before_local_save_and_rpc(self):
        path = self.writing / "설정.json"
        baseline = json.loads(path.read_text("utf-8"))
        variants = []
        r = copy.deepcopy(baseline); r["tree_order"]["메인/휴지통"] = ["deleted.txt"]; variants.append(r)
        r = copy.deepcopy(baseline); r["tree_order"]["메인"].reverse(); variants.append(r)
        r = copy.deepcopy(baseline); r["tree_order"]["메인/휴지통/child"] = []; variants.append(r)
        for settings in variants:
            path.write_text(json.dumps(settings, ensure_ascii=False), "utf-8")
            before_db, before_files = self.db_dump(), self.files()
            with self.assertRaisesRegex(ScopeDenied, "LOCAL_ORDER_CHANGED"):
                self.new_service().send_once()
            self.assertEqual(self.db_dump(), before_db)
            self.assertEqual(self.files(), before_files)
            self.assertFalse(list(self.journals.iterdir()))
        self.assertFalse(any(method == "POST" for method, _ in self.server.calls))

    def test_context_change_after_lease_is_rejected_at_final_http(self):
        def change_mode(path):
            if path == "acquire_edit_lease":
                with self.store._transaction() as conn:
                    conn.execute("UPDATE sync_projects SET project_sync_mode='MIGRATING', migration_epoch=1 WHERE local_key=?", (self.context["local_key"],))
        self.server.on_request = change_mode
        with self.assertRaises(ScopeDenied):
            self.service.send_once()
        self.assertNotIn(("POST", "commit_document"), self.server.calls)
        self.assertEqual((self.writing / PATH).read_bytes(), WINDOWS.encode())

    def test_local_edit_after_lease_is_preserved_and_not_sent(self):
        def edit(path):
            if path == "acquire_edit_lease":
                (self.writing / PATH).write_bytes(b"external edit")
        self.server.on_request = edit
        with self.assertRaises(ScopeDenied):
            self.service.send_once()
        self.assertNotIn(("POST", "commit_document"), self.server.calls)
        self.assertEqual((self.writing / PATH).read_bytes(), b"external edit")

    def test_receive_metadata_failure_keeps_saved_file_and_durable_stop(self):
        self.service.send_once()
        self.server.body().update(content=IPAD, revision=3)
        service = self.new_service()
        with patch.object(self.store, "apply_remote_snapshot", side_effect=OSError("synthetic metadata error")):
            with self.assertRaises(OSError):
                service.receive_once()
        self.assertEqual((self.writing / PATH).read_bytes(), IPAD.encode())
        self.assertEqual(self.store.get_document_by_id(DOCUMENT_ID)["revision"], 2)
        with self.assertRaises(ScopeDenied):
            self.new_service().receive_once()

    def test_product_client_factory_uses_final_transport_without_restoring_credentials(self):
        from types import SimpleNamespace
        from sync_manager import SyncManager
        from security_manager import SecurityManager
        guard = BodyHTTPTransport(ForegroundTicket(), inner=httpx.MockTransport(self.server))
        config = SimpleNamespace(is_ready=True, url=ENDPOINT, publishable_key="isolated-publishable-key")
        with patch.object(SyncManager, "acquire_auth_lease", return_value=True), \
             patch.object(SecurityManager, "get_supabase_session", side_effect=AssertionError("No credential reads")):
            client = SyncManager.create_supabase_client(config, restore_session=False,
                http_transport=guard, preserve_rejected_session=True)
        self.assertIsNotNone(client)
        self.assertIs(client._antigravity_httpx_client._transport, guard)
        self.assertFalse(client._antigravity_httpx_client.follow_redirects)
        with self.assertRaises(ScopeDenied):
            client.table("documents").select("*").execute()
        self.assertFalse(self.server.calls)
        SyncManager._close_supabase_client(client)

    def test_compared_save_cancellation_at_final_replace_preserves_original(self):
        count = [0]
        def cancel_second_check():
            count[0] += 1
            if count[0] == 2:
                raise ScopeDenied("CANCELLED")
        with self.assertRaises(ScopeDenied):
            self.wpm.compare_write_text_file(PATH, BASE.encode(), WINDOWS, guard=cancel_second_check)
        self.assertEqual((self.writing / PATH).read_bytes(), BASE.encode())
        self.assertFalse(list((self.writing / PATH).parent.glob("*.scope-*")))

    def test_unknown_acquire_records_possible_lease_without_fabricating_token(self):
        self.server.failure = "acquire_timeout"
        with self.assertRaises(Exception):
            self.service.send_once()
        rows = [json.loads(line) for line in (self.journals / "send.jsonl").read_text("utf-8").splitlines()]
        self.assertTrue(rows[-1]["lease_may_remain"])
        self.assertEqual(rows[-1]["event"], "stopped")
        self.assertNotIn(("POST", "commit_document"), self.server.calls)

    def test_sdk_duplicate_attempt_stopped_at_transport_before_second_wire_call(self):
        rejected = []
        def repeat(path):
            if path == "commit_document":
                op = self.store.operation(self.service._active_ids()[0])
                payload = {"p_document_id": DOCUMENT_ID, "p_project_id": PROJECT_ID, "p_base_revision": 1,
                    "p_operation_id": op["operation_id"], "p_device_id": DEVICE_ID, "p_relative_path": PATH,
                    "p_content": WINDOWS, "p_is_deleted": False, "p_lease_token": LEASE}
                try:
                    self.service.client.rpc("commit_document", payload).execute()
                except ScopeDenied:
                    rejected.append(True)
        self.server.on_request = repeat
        self.service.send_once()
        self.assertEqual(rejected, [True])
        self.assertEqual(self.server.calls.count(("POST", "commit_document")), 1)


if __name__ == "__main__":
    unittest.main()
