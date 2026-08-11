import copy
import json
import os
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path

from sync_contract import (
    CANONICAL_CONTRACT_BYTES,
    CANONICAL_CONTRACT_SHA256,
    CONTRACT_CONTENT_COMMIT,
    CONTRACT_GIT_COMMIT,
    CONTRACT_VERSION,
    SERVER_CAPABILITIES,
    SyncContractError,
    build_atomic_structure_request,
    normalize_storage_name,
    require_server_compatibility,
    safe_trace,
    validate_atomic_structure_response,
)
from sync_manager import SyncManager
from sync_v2_store import STAGE8_USER_VERSION, SyncV2Store


PROJECT_ID = "00000000-0000-4000-8000-000000000201"
DEVICE_ID = "60000000-0000-4000-8000-000000000201"
BATCH_ID = "10000000-0000-4000-8000-000000000201"


def contract_root():
    configured = os.environ.get("WRITERPAD_SYNC_CONTRACT_DIR")
    candidates = [
        Path(configured) if configured else None,
        Path(__file__).resolve().parents[2] / "_stage7_writerpad" / "sync-contract",
    ]
    for candidate in candidates:
        if candidate and (candidate / "protocol.json").is_file():
            return candidate
    raise unittest.SkipTest("released sync-contract checkout is not available")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class ContractPrimitiveTests(unittest.TestCase):
    def test_release_pin_is_exact(self):
        self.assertEqual(CONTRACT_VERSION, "0.1.0")
        self.assertEqual(CONTRACT_GIT_COMMIT, "45d18cff62cc48e29d0e6efcfc634fec96150198")
        self.assertEqual(CONTRACT_CONTENT_COMMIT, "7f05f32dd385ce0e1922b88d688742fca2a503fa")
        self.assertEqual(CANONICAL_CONTRACT_BYTES, 19473)
        self.assertEqual(
            CANONICAL_CONTRACT_SHA256,
            "fae86b4e6385ee37fbeb99f9256194ec319b64bfda92974ce90a3eb70d2e7a46",
        )

    def test_all_storage_name_vectors(self):
        vectors = load_json(
            contract_root() / "conformance_vectors" / "storage-name-v1.json"
        )["vectors"]
        self.assertEqual(len(vectors), 15)
        for vector in vectors:
            with self.subTest(vector=vector["vector_id"]):
                if vector["valid"]:
                    actual = normalize_storage_name(vector["input"])
                    self.assertEqual(actual.normalized, vector["normalized"])
                    self.assertEqual(actual.utf8_hex, vector["utf8_hex"])
                else:
                    with self.assertRaises(SyncContractError) as raised:
                        normalize_storage_name(vector["input"])
                    self.assertEqual(raised.exception.code, vector["error_code"])

    def test_all_atomic_wire_cases_and_exact_payload_hash(self):
        cases = load_json(
            contract_root() / "conformance_vectors" / "atomic-structure-commit.json"
        )["cases"]
        self.assertEqual(len(cases), 4)
        first = cases[0]
        sources = [
            {
                "operation_id": intent["operation_id"],
                "entity_kind": intent["entity_kind"],
                "entity_id": intent["entity_id"],
                "intent_kind": intent["intent_kind"],
                "base_revision": intent["base_revision"],
                "payload": intent["payload"],
            }
            for intent in first["request"]["ordered_intents"]
        ]
        request = build_atomic_structure_request(
            project_id=PROJECT_ID,
            project_sync_mode="ID_BASED",
            migration_epoch=1,
            writer_device_id=DEVICE_ID,
            ordered_intents=sources,
            batch_id=BATCH_ID,
            client_build_id="conformance-0.1.0",
        )
        self.assertEqual(request, first["request"])
        self.assertEqual(
            request["batch"]["batch_payload_sha256"],
            "0fd7de3e5329659757e0a391f3cfed43faac11a750ad13698301bfda7499e62c",
        )
        validate_atomic_structure_response(request, cases[0]["response"])
        validate_atomic_structure_response(request, cases[1]["response"])
        validate_atomic_structure_response(request, cases[2]["response"])
        validate_atomic_structure_response(cases[3]["request"], cases[3]["response"])

    def test_all_transition_vectors_are_the_pinned_release(self):
        vectors = [load_json(path) for path in sorted((contract_root() / "test_vectors").glob("*.json"))]
        self.assertEqual(len(vectors), 12)
        self.assertEqual(
            [item["vector_id"] for item in vectors],
            [f"TV-{index:03d}" for index in range(1, 13)],
        )
        for vector in vectors:
            self.assertEqual(vector["contract_version"], CONTRACT_VERSION)
            self.assertLessEqual(vector["minimum_protocol_version"], 3)
            self.assertTrue(vector["invariants"])

    def test_capability_and_digest_checks_fail_closed(self):
        common = {
            "project_sync_mode": "MIGRATING",
            "migration_epoch": 1,
            "server_protocol_version": 3,
            "server_contract_sha256": CANONICAL_CONTRACT_SHA256,
            "server_capabilities": SERVER_CAPABILITIES,
        }
        require_server_compatibility(**common)
        for field, bad_value, expected in (
            ("server_protocol_version", 2, "PROTOCOL_TOO_OLD"),
            ("server_contract_sha256", "0" * 64, "CONTRACT_DIGEST_MISMATCH"),
            ("server_capabilities", SERVER_CAPABILITIES[:-1], "CAPABILITY_MISMATCH"),
        ):
            candidate = dict(common)
            candidate[field] = bad_value
            with self.subTest(field=field), self.assertRaises(SyncContractError) as raised:
                require_server_compatibility(**candidate)
            self.assertEqual(raised.exception.code, expected)

    def test_partial_success_response_is_rejected(self):
        case = load_json(
            contract_root() / "conformance_vectors" / "atomic-structure-commit.json"
        )["cases"][0]
        partial = copy.deepcopy(case["response"])
        partial["results"].pop()
        with self.assertRaises(SyncContractError) as raised:
            validate_atomic_structure_response(case["request"], partial)
        self.assertEqual(raised.exception.code, "PARTIAL_BATCH_RESPONSE")

    def test_atomic_request_rejects_invalid_storage_name_before_queueing(self):
        with self.assertRaises(SyncContractError) as raised:
            build_atomic_structure_request(
                project_id=PROJECT_ID,
                project_sync_mode="ID_BASED",
                migration_epoch=1,
                writer_device_id=DEVICE_ID,
                ordered_intents=[{
                    "entity_kind": "folder",
                    "entity_id": str(uuid.uuid4()),
                    "intent_kind": "create",
                    "base_revision": 0,
                    "payload": {"name": "CON.txt"},
                }],
            )
        self.assertEqual(raised.exception.code, "STORAGE_NAME_RESERVED")

    def test_diagnostics_drop_content_tokens_and_urls(self):
        trace = safe_trace(
            "dispatch",
            operation_id=str(uuid.uuid4()),
            error_code="NETWORK_ERROR",
            content="비밀 문서 본문",
            access_token="secret",
            endpoint="https://private.invalid",
        )
        rendered = json.dumps(trace, ensure_ascii=False)
        self.assertNotIn("비밀 문서 본문", rendered)
        self.assertNotIn("secret", rendered)
        self.assertNotIn("private.invalid", rendered)


class ContractStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / "sync.sqlite3")
        self.store = SyncV2Store(self.db_path)
        self.context = self.store.configure_project(
            str(Path(self.temp.name) / "writing"), "Stage 8", PROJECT_ID
        )

    def tearDown(self):
        self.temp.cleanup()

    def _activate_id_based(self):
        self.store.activate_contract_project(
            self.context["local_key"],
            project_sync_mode="MIGRATING",
            migration_epoch=1,
            server_protocol_version=3,
            server_contract_sha256=CANONICAL_CONTRACT_SHA256,
            server_capabilities=SERVER_CAPABILITIES,
        )
        return self.store.activate_contract_project(
            self.context["local_key"],
            project_sync_mode="ID_BASED",
            migration_epoch=1,
            server_protocol_version=3,
            server_contract_sha256=CANONICAL_CONTRACT_SHA256,
            server_capabilities=SERVER_CAPABILITIES,
        )

    def _vector_intents(self):
        request = load_json(
            contract_root() / "conformance_vectors" / "atomic-structure-commit.json"
        )["cases"][0]["request"]
        return [
            {
                "operation_id": item["operation_id"],
                "entity_kind": item["entity_kind"],
                "entity_id": item["entity_id"],
                "intent_kind": item["intent_kind"],
                "base_revision": item["base_revision"],
                "payload": item["payload"],
            }
            for item in request["ordered_intents"]
        ]

    def test_new_database_is_legacy_epoch_zero_and_user_version_is_additive(self):
        project = self.store.get_project(self.context["local_key"])
        self.assertEqual(project["project_sync_mode"], "LEGACY")
        self.assertEqual(project["migration_epoch"], 0)
        self.assertIsNone(project["active_contract_sha256"])
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], STAGE8_USER_VERSION)
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_project_is_never_automatically_promoted(self):
        self.store.enqueue(self.context, "메인/원고/001화.txt", "offline")
        reopened = SyncV2Store(self.db_path)
        project = reopened.get_project(self.context["local_key"])
        self.assertEqual((project["project_sync_mode"], project["migration_epoch"]), ("LEGACY", 0))

    def test_restart_recovery_is_append_only_and_reuses_operation_id(self):
        operation = self.store.enqueue(self.context, "메인/원고/001화.txt", "offline")
        self.store.mark_attempt(operation["operation_id"])
        reopened = SyncV2Store(self.db_path)
        recovered = reopened.next_ready_operation(self.context["local_key"])
        self.assertEqual(recovered["operation_id"], operation["operation_id"])
        self.assertEqual(recovered["status"], "retry_wait")
        self.assertEqual(reopened.operation_attempts(operation["operation_id"])[0]["outcome"], "transport_unknown")
        self.assertEqual(
            [item["event_type"] for item in reopened.operation_events(operation["operation_id"])],
            ["enqueued", "dispatch_started", "retry_scheduled"],
        )

    def test_intent_is_immutable_and_rebase_creates_successor(self):
        original = self.store.enqueue(self.context, "메인/원고/001화.txt", "local")
        successor = self.store.rebase_clean_merge(
            original["operation_id"], 2, "remote", "merged"
        )
        unchanged = self.store.operation(original["operation_id"])
        self.assertEqual(unchanged["content"], "local")
        self.assertEqual(unchanged["base_revision"], 0)
        self.assertEqual(unchanged["status"], "cancelled")
        self.assertNotEqual(successor["operation_id"], original["operation_id"])
        self.assertEqual(successor["supersedes_operation_id"], original["operation_id"])
        self.assertEqual((successor["base_revision"], successor["content"]), (2, "merged"))
        with closing(sqlite3.connect(self.db_path)) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE sync_operations SET content = 'mutated' WHERE operation_id = ?",
                    (original["operation_id"],),
                )

    def test_cancellation_is_idempotent_and_terminal_safe(self):
        first = self.store.enqueue(self.context, "first.txt", "first")
        event_id = str(uuid.uuid4())
        self.assertEqual(self.store.cancel_operation(first["operation_id"], event_id)["status"], "cancelled")
        self.assertEqual(self.store.cancel_operation(first["operation_id"], event_id)["status"], "already_cancelled")
        self.assertEqual(self.store.cancel_operation(first["operation_id"], str(uuid.uuid4()))["status"], "already_cancelled")
        self.assertEqual(
            [event["event_type"] for event in self.store.operation_events(first["operation_id"])].count("cancel_requested"),
            1,
        )
        second = self.store.enqueue(self.context, "second.txt", "second")
        self.store.mark_attempt(second["operation_id"])
        self.store.mark_success(second["operation_id"], {"revision": 1})
        with self.assertRaises(SyncContractError) as raised:
            self.store.cancel_operation(second["operation_id"], str(uuid.uuid4()))
        self.assertEqual(raised.exception.code, "OPERATION_TERMINAL")

    def test_atomic_batch_partial_response_changes_nothing(self):
        self._activate_id_based()
        request = self.store.create_structure_batch(
            self.context, DEVICE_ID, self._vector_intents(), batch_id=BATCH_ID
        )
        self.store.mark_structure_batch_attempt(BATCH_ID)
        response = load_json(
            contract_root() / "conformance_vectors" / "atomic-structure-commit.json"
        )["cases"][0]["response"]
        partial = copy.deepcopy(response)
        partial["results"].pop()
        with self.assertRaises(SyncContractError) as raised:
            self.store.record_structure_batch_response(BATCH_ID, partial)
        self.assertEqual(raised.exception.code, "PARTIAL_BATCH_RESPONSE")
        for intent in request["ordered_intents"]:
            self.assertEqual(self.store.operation(intent["operation_id"])["status"], "inflight")
            self.assertEqual(self.store.operation_attempts(intent["operation_id"]), [])
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sync_contract_batch_results").fetchone()[0], 0)

    def test_atomic_batch_commit_and_identical_replay_are_deterministic(self):
        self._activate_id_based()
        request = self.store.create_structure_batch(
            self.context, DEVICE_ID, self._vector_intents(), batch_id=BATCH_ID
        )
        same = self.store.create_structure_batch(
            self.context, DEVICE_ID, self._vector_intents(), batch_id=BATCH_ID
        )
        self.assertEqual(same, request)
        self.store.mark_structure_batch_attempt(BATCH_ID)
        response = load_json(
            contract_root() / "conformance_vectors" / "atomic-structure-commit.json"
        )["cases"][0]["response"]
        self.store.record_structure_batch_response(BATCH_ID, response)
        self.assertEqual(self.store.record_structure_batch_response(BATCH_ID, response), response)
        for intent in request["ordered_intents"]:
            self.assertEqual(self.store.operation(intent["operation_id"])["status"], "completed")
            self.assertEqual(len(self.store.operation_attempts(intent["operation_id"])), 1)

    def test_manager_sends_exact_p_request_after_atomic_attempt_begins(self):
        self._activate_id_based()
        request = self.store.create_structure_batch(
            self.context, DEVICE_ID, self._vector_intents(), batch_id=BATCH_ID
        )
        self.store.mark_structure_batch_attempt(BATCH_ID)
        response = load_json(
            contract_root() / "conformance_vectors" / "atomic-structure-commit.json"
        )["cases"][0]["response"]

        class RpcCall:
            def execute(inner_self):
                return type("Response", (), {"data": response})()

        class Client:
            def __init__(inner_self):
                inner_self.calls = []

            def rpc(inner_self, name, params):
                inner_self.calls.append((name, params))
                return RpcCall()

        manager = SyncManager()
        previous = (
            manager._v2_store,
            manager._v2_context,
            manager._v2_device_id,
            manager.supabase,
        )
        try:
            manager._v2_store = self.store
            manager._v2_context = dict(self.context)
            manager._v2_device_id = DEVICE_ID
            manager.supabase = Client()
            result = manager._process_contract_structure_batch(BATCH_ID)

            self.assertEqual(result, response)
            self.assertEqual(
                manager.supabase.calls,
                [("atomic_structure_commit", {"p_request": request})],
            )
        finally:
            (
                manager._v2_store,
                manager._v2_context,
                manager._v2_device_id,
                manager.supabase,
            ) = previous

    def test_structure_rebase_uses_new_batch_and_preserves_original(self):
        self._activate_id_based()
        first_request = self.store.create_structure_batch(
            self.context, DEVICE_ID, self._vector_intents(), batch_id=BATCH_ID
        )
        original = first_request["ordered_intents"][0]
        successor_id = str(uuid.uuid4())
        next_batch_id = str(uuid.uuid4())
        next_request = self.store.create_structure_batch(
            self.context,
            DEVICE_ID,
            [{
                "operation_id": successor_id,
                "entity_kind": original["entity_kind"],
                "entity_id": original["entity_id"],
                "intent_kind": original["intent_kind"],
                "base_revision": 2,
                "payload": {"name": "Rebased"},
                "supersedes_operation_id": original["operation_id"],
            }],
            batch_id=next_batch_id,
        )
        self.assertNotEqual(next_request["batch"]["batch_id"], BATCH_ID)
        self.assertEqual(self.store.operation(original["operation_id"])["status"], "cancelled")
        successor = self.store.operation(successor_id)
        self.assertEqual(successor["supersedes_operation_id"], original["operation_id"])
        self.assertEqual(successor["payload"], {"name": "Rebased"})

    def test_stored_diagnostic_contains_metadata_only(self):
        trace = self.store.record_diagnostic(
            self.context["local_key"],
            "rpc_failure",
            project_id=PROJECT_ID,
            error_code="NETWORK_ERROR",
            content="private manuscript",
            access_token="secret",
        )
        self.assertNotIn("content", trace)
        self.assertNotIn("access_token", trace)
        with closing(sqlite3.connect(self.db_path)) as connection:
            stored = connection.execute("SELECT metadata_json FROM sync_contract_diagnostics").fetchone()[0]
        self.assertNotIn("private manuscript", stored)
        self.assertNotIn("secret", stored)


class LegacyMigrationTests(unittest.TestCase):
    def test_legacy_snapshot_is_classified_without_invented_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = str(Path(temp) / "legacy.sqlite3")
            project_id = str(uuid.uuid4())
            document_id = str(uuid.uuid4())
            operation_id = str(uuid.uuid4())
            now = "2026-08-11T00:00:00+00:00"
            with closing(sqlite3.connect(db_path)) as connection:
                connection.executescript(
                    """
                    CREATE TABLE sync_projects (
                        local_key TEXT PRIMARY KEY, project_id TEXT NOT NULL UNIQUE,
                        project_name TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE sync_documents (
                        document_id TEXT PRIMARY KEY, local_key TEXT NOT NULL,
                        local_path TEXT NOT NULL, server_path TEXT NOT NULL,
                        revision INTEGER NOT NULL DEFAULT 0, base_content TEXT NOT NULL DEFAULT '',
                        base_hash TEXT NOT NULL DEFAULT '', is_deleted INTEGER NOT NULL DEFAULT 0,
                        sync_state TEXT NOT NULL DEFAULT 'local', last_error TEXT NOT NULL DEFAULT '',
                        conflict_base TEXT, conflict_local TEXT, conflict_remote TEXT,
                        conflict_merged TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                        UNIQUE(local_key, local_path)
                    );
                    CREATE TABLE sync_operations (
                        queue_id INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT NOT NULL UNIQUE,
                        local_key TEXT NOT NULL, project_id TEXT NOT NULL, document_id TEXT NOT NULL,
                        local_path TEXT NOT NULL, relative_path TEXT NOT NULL, base_revision INTEGER,
                        base_content TEXT NOT NULL, content TEXT NOT NULL, is_deleted INTEGER NOT NULL DEFAULT 0,
                        status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                        last_error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    """
                )
                connection.execute("INSERT INTO sync_projects VALUES (?, ?, ?, ?, ?)", ("legacy-root", project_id, "Legacy", now, now))
                connection.execute(
                    "INSERT INTO sync_documents VALUES (?, ?, ?, ?, 0, '', '', 0, 'local', '', NULL, NULL, NULL, NULL, ?, ?)",
                    (document_id, "legacy-root", "draft.txt", "draft.txt", now, now),
                )
                connection.execute(
                    "INSERT INTO sync_operations VALUES (NULL, ?, ?, ?, ?, ?, ?, 0, '', ?, 0, 'pending', 3, '', ?, ?)",
                    (operation_id, "legacy-root", project_id, document_id, "draft.txt", "draft.txt", "legacy body", now, now),
                )
                connection.commit()
            migrated = SyncV2Store(db_path)
            project = migrated.get_project("legacy-root")
            operation = migrated.operation(operation_id)
            self.assertEqual((project["project_sync_mode"], project["migration_epoch"]), ("LEGACY", 0))
            self.assertIsNone(project["active_contract_sha256"])
            self.assertEqual(operation["provenance_kind"], "LEGACY_EPOCH_0")
            self.assertEqual(operation["sync_protocol_version"], 2)
            self.assertIsNone(operation["contract_version"])
            self.assertIsNone(operation["batch_id"])
            self.assertEqual(operation["legacy_attempt_count"], 3)
            self.assertEqual(migrated.operation_attempts(operation_id), [])
            self.assertEqual(migrated.operation_events(operation_id)[0]["event_type"], "enqueued")
            with closing(sqlite3.connect(db_path)) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], STAGE8_USER_VERSION)
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")


if __name__ == "__main__":
    unittest.main()
