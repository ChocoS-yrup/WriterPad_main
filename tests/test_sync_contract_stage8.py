import copy
import json
import os
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sync_contract import (
    CANONICAL_CONTRACT_BYTES,
    CANONICAL_CONTRACT_SHA256,
    CONTRACT_CONTENT_COMMIT,
    CONTRACT_GIT_COMMIT,
    CONTRACT_VERSION,
    SERVER_CAPABILITIES,
    SyncContractError,
    build_atomic_structure_request,
    build_document_commit_request,
    normalize_storage_name,
    require_server_compatibility,
    safe_trace,
    validate_atomic_structure_response,
    validate_document_commit_response,
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
        self.assertEqual(CONTRACT_VERSION, "0.2.0")
        self.assertEqual(CONTRACT_GIT_COMMIT, "fcd99b7098b9a04bd93c585d89b16588aa482530")
        self.assertEqual(CONTRACT_CONTENT_COMMIT, "7bcb5d25c5376b02469666df7318b90b456ffee6")
        self.assertEqual(CANONICAL_CONTRACT_BYTES, 23256)
        self.assertEqual(
            CANONICAL_CONTRACT_SHA256,
            "416c1b99edb9bda694731dee4b25688d9d82d1f32610aa23ddfda571ec3c7670",
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
            client_build_id="conformance-0.2.0",
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

    def test_all_document_wire_cases_and_exact_payload_hash(self):
        cases = load_json(
            contract_root() / "conformance_vectors" / "document-commit.json"
        )["cases"]
        self.assertEqual(len(cases), 7)
        first_request = cases[0]["request"]
        intent = first_request["ordered_intents"][0]
        payload = intent["payload"]
        request = build_document_commit_request(
            project_id=first_request["project_id"],
            project_sync_mode=first_request["project_sync_mode"],
            migration_epoch=first_request["migration_epoch"],
            writer_device_id=first_request["batch"]["writer_device_id"],
            document_id=intent["document_id"],
            intent_kind=intent["intent_kind"],
            base_revision=intent["base_revision"],
            parent_folder_id=payload["parent_folder_id"],
            name=payload["name"],
            content=payload["content"],
            is_deleted=payload["is_deleted"],
            structure_revision=payload["structure_revision"],
            operation_id=intent["operation_id"],
            batch_id=first_request["batch"]["batch_id"],
            client_build_id="conformance-0.2.0",
        )
        self.assertEqual(request, first_request)
        self.assertEqual(
            request["batch"]["batch_payload_sha256"],
            "e2571bcb8611ea13c058753470871eb61fb6117d8dd8adbd36688eac0cd5d34d",
        )
        requests = {
            case["case_id"]: case["request"]
            for case in cases
            if "request" in case
        }
        for case in cases:
            source_id = case.get("request_from") or case.get("replay_of")
            source_request = case.get("request") or requests[source_id]
            with self.subTest(case=case["case_id"]):
                validate_document_commit_response(source_request, case["response"])

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

    def test_server_compatibility_rejects_invalid_mode_epoch_pairs(self):
        common = {
            "server_protocol_version": 3,
            "server_contract_sha256": CANONICAL_CONTRACT_SHA256,
            "server_capabilities": SERVER_CAPABILITIES,
        }
        for mode, epoch in (
            ("LEGACY", 1),
            ("MIGRATING", 0),
            ("ID_BASED", 0),
            ("MIGRATING", -1),
        ):
            with self.subTest(mode=mode, epoch=epoch), self.assertRaises(SyncContractError) as raised:
                require_server_compatibility(
                    project_sync_mode=mode,
                    migration_epoch=epoch,
                    **common,
                )
            self.assertEqual(raised.exception.code, "STALE_MIGRATION_EPOCH")

    def test_document_failure_requires_nonempty_string_message(self):
        cases = load_json(
            contract_root() / "conformance_vectors" / "document-commit.json"
        )["cases"]
        case = next(
            item for item in cases
            if item["response"]["kind"] == "document_commit_failure"
            and "request" in item
        )
        request = case["request"]
        for message in (None, "", 123):
            response = copy.deepcopy(case["response"])
            response["error"]["message"] = message
            with self.subTest(message=message), self.assertRaises(SyncContractError) as raised:
                validate_document_commit_response(request, response)
            self.assertEqual(raised.exception.code, "INVALID_DOCUMENT_RESPONSE")

        missing = copy.deepcopy(case["response"])
        del missing["error"]["message"]
        with self.assertRaises(SyncContractError) as raised:
            validate_document_commit_response(request, missing)
        self.assertEqual(raised.exception.code, "INVALID_DOCUMENT_RESPONSE")

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

    def _contract_manager(self):
        project = self._activate_id_based()
        manager = SyncManager()
        manager._v2_store = self.store
        manager._v2_context = {**self.context, **project}
        manager._v2_device_id = DEVICE_ID
        manager._v2_wpm = SimpleNamespace(
            writing_root_path=str(Path(self.temp.name) / "writing"),
            read_text_file=lambda _path: None,
            project_settings={"tree_order": {}},
            save_settings=lambda: True,
        )
        Path(manager._v2_wpm.writing_root_path).mkdir(parents=True, exist_ok=True)
        return manager

    def _folder_snapshot(self, path, *, parent_id=None, revision=1):
        return {
            "folder_id": str(uuid.uuid4()),
            "parent_folder_id": parent_id,
            "local_path": path,
            "name": path.rsplit("/", 1)[-1],
            "revision": revision,
            "is_deleted": False,
        }

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

    @staticmethod
    def _document_success(request, status="committed", revision=1):
        intent = request["ordered_intents"][0]
        payload = intent["payload"]
        return {
            "kind": "document_commit_success",
            "batch_id": request["batch"]["batch_id"],
            "batch_payload_sha256": request["batch"]["batch_payload_sha256"],
            "status": status,
            "applied": True,
            "results": [{
                "sequence": 1,
                "operation_id": intent["operation_id"],
                "document_id": intent["document_id"],
                "result_revision": revision,
                "structure_revision": payload["structure_revision"],
                "parent_folder_id": payload["parent_folder_id"],
                "name": payload["name"],
                "content_sha256": payload["content_sha256"],
                "content_byte_count": payload["content_byte_count"],
                "is_deleted": payload["is_deleted"],
            }],
        }

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

    def test_contract_ui_tree_order_queues_atomic_batch_not_hidden_document(self):
        manager = self._contract_manager()
        parent = self._folder_snapshot("메인/메모장")
        child = self._folder_snapshot(
            "메인/메모장/장면", parent_id=parent["folder_id"]
        )
        self.store.replace_folder_snapshots(
            self.context["local_key"], [parent, child]
        )

        request = manager.record_tree_order(
            {"메인/메모장": ["장면"]}, retry=False
        )

        self.assertEqual(request["kind"], "atomic_structure_commit_request")
        self.assertEqual(
            [item["entity_kind"] for item in request["ordered_intents"]],
            ["tree_order"],
        )
        self.assertEqual(
            request["ordered_intents"][0]["payload"],
            {
                "parent_folder_id": parent["folder_id"],
                "children": [child["folder_id"]],
            },
        )
        self.assertIsNone(
            self.store.get_document(
                self.context["local_key"], "__antigravity__/tree-order.json"
            )
        )

    def test_contract_folder_rename_and_order_share_one_atomic_batch(self):
        manager = self._contract_manager()
        parent = self._folder_snapshot("메인/메모장")
        child = self._folder_snapshot(
            "메인/메모장/옛 이름", parent_id=parent["folder_id"], revision=4
        )
        self.store.replace_folder_snapshots(
            self.context["local_key"], [parent, child]
        )
        old_path = Path(manager._v2_wpm.writing_root_path, child["local_path"])
        new_path = old_path.with_name("새 이름")
        old_path.mkdir(parents=True)
        old_path.rename(new_path)

        operations = manager.record_path_change(
            child["local_path"], "메인/메모장/새 이름", retry=False
        )
        request = manager.queue_contract_path_change_with_order(
            operations, {"메인/메모장": ["새 이름"]}, retry=False
        )

        self.assertEqual(
            [item["entity_kind"] for item in request["ordered_intents"]],
            ["folder", "tree_order"],
        )
        rename, order = request["ordered_intents"]
        self.assertEqual((rename["intent_kind"], rename["base_revision"]), ("rename", 4))
        self.assertEqual(rename["entity_id"], child["folder_id"])
        self.assertEqual(order["payload"]["children"], [child["folder_id"]])
        self.assertEqual(
            self.store.get_folder_by_id(child["folder_id"])["local_path"],
            "메인/메모장/새 이름",
        )

    def test_contract_path_batch_rolls_back_batch_and_snapshot_together(self):
        manager = self._contract_manager()
        parent = self._folder_snapshot("메인/메모장")
        child = self._folder_snapshot(
            "메인/메모장/이전", parent_id=parent["folder_id"], revision=3
        )
        self.store.replace_folder_snapshots(
            self.context["local_key"], [parent, child]
        )
        old_path = Path(manager._v2_wpm.writing_root_path, child["local_path"])
        new_path = old_path.with_name("이후")
        old_path.mkdir(parents=True)
        old_path.rename(new_path)
        operations = manager.record_path_change(
            child["local_path"], "메인/메모장/이후", retry=False
        )

        with patch.object(
            self.store, "move_folder_paths",
            side_effect=RuntimeError("injected snapshot failure"),
        ), self.assertRaises(RuntimeError):
            manager.queue_contract_path_change_with_order(
                operations, {"메인/메모장": ["이후"]}, retry=False
            )

        self.assertIsNotNone(
            self.store.get_folder_by_path(
                self.context["local_key"], child["local_path"]
            )
        )
        self.assertIsNone(
            self.store.get_folder_by_path(
                self.context["local_key"], "메인/메모장/이후"
            )
        )
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM sync_contract_batches"
                ).fetchone()[0],
                0,
            )

    def test_combined_rename_move_supersedes_predecessor_once(self):
        manager = self._contract_manager()
        source = self._folder_snapshot("메인/메모장/원본")
        target = self._folder_snapshot("메인/메모장/대상")
        child = self._folder_snapshot(
            "메인/메모장/원본/이전",
            parent_id=source["folder_id"],
            revision=5,
        )
        self.store.replace_folder_snapshots(
            self.context["local_key"], [source, target, child]
        )
        predecessor = self.store.create_structure_batch(
            self.context,
            DEVICE_ID,
            [{
                "entity_kind": "folder",
                "entity_id": child["folder_id"],
                "intent_kind": "rename",
                "base_revision": 5,
                "payload": {"name": "대기 중"},
            }],
        )["ordered_intents"][0]
        old_path = Path(manager._v2_wpm.writing_root_path, child["local_path"])
        new_rel_path = "메인/메모장/대상/이후"
        new_path = Path(manager._v2_wpm.writing_root_path, new_rel_path)
        old_path.mkdir(parents=True)
        new_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.rename(new_path)

        operations = manager.record_path_change(
            child["local_path"], new_rel_path, retry=False
        )
        request = manager.queue_contract_path_change_with_order(
            operations,
            {"메인/메모장/대상": ["이후"]},
            retry=False,
        )
        rename, move, _order = request["ordered_intents"]
        self.assertEqual(
            rename["supersedes_operation_id"], predecessor["operation_id"]
        )
        self.assertNotIn("supersedes_operation_id", move)
        self.assertEqual(
            self.store.operation(predecessor["operation_id"])["status"],
            "cancelled",
        )

    def test_folder_create_delete_restore_are_atomic_lifecycle_batches(self):
        manager = self._contract_manager()
        parent = self._folder_snapshot("메인/메모장")
        self.store.replace_folder_snapshots(
            self.context["local_key"], [parent]
        )
        created_path = "메인/메모장/새 폴더"
        Path(
            manager._v2_wpm.writing_root_path, created_path
        ).mkdir(parents=True)
        create_operations = manager.record_path_change(
            created_path, created_path, retry=False
        )
        create_request = manager.queue_contract_path_change_with_order(
            create_operations,
            {"메인/메모장": ["새 폴더"]},
            retry=False,
        )
        self.assertEqual(
            [item["intent_kind"] for item in create_request["ordered_intents"]],
            ["create", "reorder"],
        )
        created = self.store.get_folder_by_path(
            self.context["local_key"], created_path
        )
        self.assertIsNotNone(created)

        # Simulate the server-proven revision used by later lifecycle intents.
        snapshots = [parent, {
            "folder_id": created["folder_id"],
            "parent_folder_id": parent["folder_id"],
            "local_path": created_path,
            "name": "새 폴더",
            "revision": 1,
            "is_deleted": False,
        }]
        self.store.replace_folder_snapshots(
            self.context["local_key"], snapshots
        )
        delete_operations = manager.record_tombstone(
            created_path, "메인/휴지통/새 폴더", retry=False
        )
        delete_request = manager.queue_contract_path_change_with_order(
            delete_operations,
            {"메인/메모장": []},
            retry=False,
        )
        self.assertEqual(
            [item["intent_kind"] for item in delete_request["ordered_intents"]],
            ["delete", "reorder"],
        )
        self.assertTrue(
            self.store.get_folder_by_id(created["folder_id"])["is_deleted"]
        )

        restored_path = "메인/메모장/복원 폴더"
        restore_operations = manager.record_restore(
            "메인/휴지통/새 폴더",
            restored_path,
            original_rel_path=created_path,
            retry=False,
        )
        restore_request = manager.queue_contract_path_change_with_order(
            restore_operations,
            {"메인/메모장": ["복원 폴더"]},
            retry=False,
        )
        self.assertEqual(
            [item["intent_kind"] for item in restore_request["ordered_intents"]],
            ["restore", "reorder"],
        )
        restored = self.store.get_folder_by_id(created["folder_id"])
        self.assertEqual(restored["local_path"], restored_path)
        self.assertFalse(restored["is_deleted"])

    def test_compatible_legacy_uses_protocol_three_without_promotion(self):
        project = self.store.activate_contract_project(
            self.context["local_key"],
            project_sync_mode="LEGACY",
            migration_epoch=0,
            server_protocol_version=3,
            server_contract_sha256=CANONICAL_CONTRACT_SHA256,
            server_capabilities=SERVER_CAPABILITIES,
        )
        manager = SyncManager()
        manager._v2_store = self.store
        manager._v2_context = {**self.context, **project}
        manager._v2_device_id = DEVICE_ID
        manager._v2_wpm = SimpleNamespace(
            writing_root_path=str(Path(self.temp.name) / "writing"),
            read_text_file=lambda _path: None,
            project_settings={"tree_order": {}},
            save_settings=lambda: True,
        )
        parent = self._folder_snapshot("메인/메모장")
        self.store.replace_folder_snapshots(
            self.context["local_key"], [parent]
        )

        request = manager.record_tree_order(
            {"메인/메모장": []}, retry=False
        )

        self.assertEqual(request["project_sync_mode"], "LEGACY")
        self.assertEqual(request["migration_epoch"], 0)
        stored = self.store.get_project(self.context["local_key"])
        self.assertEqual(
            (stored["project_sync_mode"], stored["migration_epoch"]),
            ("LEGACY", 0),
        )

    def test_contract_document_move_and_order_share_one_atomic_batch(self):
        manager = self._contract_manager()
        source = self._folder_snapshot("메인/메모장/원본")
        target = self._folder_snapshot("메인/메모장/대상")
        self.store.replace_folder_snapshots(
            self.context["local_key"], [source, target]
        )
        document_id = str(uuid.uuid4())
        old_path = "메인/메모장/원본/초고.txt"
        new_path = "메인/메모장/대상/완성.txt"
        self.store.apply_remote_snapshot(
            self.context,
            document_id,
            old_path,
            "body",
            revision=3,
            parent_folder_id=source["folder_id"],
            name="초고.txt",
            structure_revision=7,
        )
        physical_old = Path(manager._v2_wpm.writing_root_path, old_path)
        physical_new = Path(manager._v2_wpm.writing_root_path, new_path)
        physical_old.parent.mkdir(parents=True)
        physical_old.write_text("body", encoding="utf-8")
        physical_new.parent.mkdir(parents=True)
        physical_old.rename(physical_new)

        operations = manager.record_path_change(old_path, new_path, retry=False)
        request = manager.queue_contract_path_change_with_order(
            operations,
            {"메인/메모장/대상": ["완성.txt"]},
            retry=False,
        )

        self.assertEqual(
            [(item["entity_kind"], item["intent_kind"]) for item in request["ordered_intents"]],
            [("document", "rename"), ("document", "move"), ("tree_order", "reorder")],
        )
        rename, move, order = request["ordered_intents"]
        self.assertEqual((rename["base_revision"], move["base_revision"]), (7, 8))
        self.assertEqual({rename["entity_id"], move["entity_id"]}, {document_id})
        self.assertEqual(move["payload"]["parent_folder_id"], target["folder_id"])
        self.assertEqual(order["payload"]["children"], [document_id])

    def test_repeated_offline_tree_order_supersedes_without_mutating_original(self):
        manager = self._contract_manager()
        parent = self._folder_snapshot("메인/메모장")
        first = self._folder_snapshot(
            "메인/메모장/첫째", parent_id=parent["folder_id"]
        )
        second = self._folder_snapshot(
            "메인/메모장/둘째", parent_id=parent["folder_id"]
        )
        self.store.replace_folder_snapshots(
            self.context["local_key"], [parent, first, second]
        )
        first_request = manager.record_tree_order(
            {"메인/메모장": ["첫째", "둘째"]}, retry=False
        )
        first_intent = copy.deepcopy(first_request["ordered_intents"][0])

        second_request = manager.record_tree_order(
            {"메인/메모장": ["둘째", "첫째"]}, retry=False
        )
        second_intent = second_request["ordered_intents"][0]

        self.assertNotEqual(
            first_request["batch"]["batch_id"], second_request["batch"]["batch_id"]
        )
        self.assertNotEqual(first_intent["operation_id"], second_intent["operation_id"])
        self.assertEqual(
            second_intent["supersedes_operation_id"], first_intent["operation_id"]
        )
        self.assertEqual(
            self.store.structure_batch_request(first_request["batch"]["batch_id"])["ordered_intents"][0],
            first_intent,
        )
        self.assertEqual(
            self.store.operation(first_intent["operation_id"])["status"], "cancelled"
        )

    def test_contract_tree_order_pull_resolves_ids_and_preserves_local_trash(self):
        manager = self._contract_manager()
        parent = self._folder_snapshot("메인/메모장")
        folder = self._folder_snapshot(
            "메인/메모장/장면", parent_id=parent["folder_id"]
        )
        self.store.replace_folder_snapshots(
            self.context["local_key"], [parent, folder]
        )
        document_id = str(uuid.uuid4())
        self.store.apply_remote_snapshot(
            self.context,
            document_id,
            "메인/메모장/초고.txt",
            "body",
            revision=2,
            parent_folder_id=parent["folder_id"],
            name="초고.txt",
            structure_revision=3,
        )
        manager._v2_wpm.project_settings["tree_order"] = {
            "메인/휴지통": ["로컬 보관본.txt"]
        }
        snapshots = [{
            "tree_order_id": str(uuid.uuid4()),
            "parent_folder_id": parent["folder_id"],
            "children": [folder["folder_id"], document_id],
            "revision": 5,
        }]

        change = manager._apply_contract_tree_order_snapshots(snapshots)

        self.assertEqual(change["kind"], "tree_order")
        self.assertEqual(change["revision"], 5)
        self.assertEqual(
            manager._v2_wpm.project_settings["tree_order"],
            {
                "메인/메모장": ["장면", "초고.txt"],
                "메인/휴지통": ["로컬 보관본.txt"],
            },
        )

    def test_pending_contract_tree_order_is_detected_before_remote_projection(self):
        manager = self._contract_manager()
        parent = self._folder_snapshot("메인/메모장")
        child = self._folder_snapshot(
            "메인/메모장/장면", parent_id=parent["folder_id"]
        )
        self.store.replace_folder_snapshots(
            self.context["local_key"], [parent, child]
        )
        manager.record_tree_order({"메인/메모장": ["장면"]}, retry=False)

        self.assertTrue(
            self.store.has_active_structure_kind(
                self.context["local_key"], "tree_order"
            )
        )
    def test_mode_epoch_transition_and_sqlite_pair_constraints(self):
        common = {
            "server_protocol_version": 3,
            "server_contract_sha256": CANONICAL_CONTRACT_SHA256,
            "server_capabilities": SERVER_CAPABILITIES,
        }
        for mode, epoch, expected in (
            ("LEGACY", 1, "STALE_MIGRATION_EPOCH"),
            ("MIGRATING", 0, "STALE_MIGRATION_EPOCH"),
            ("MIGRATING", 2, "STALE_MIGRATION_EPOCH"),
            ("ID_BASED", 1, "INVALID_PROJECT_MODE_TRANSITION"),
        ):
            with self.subTest(mode=mode, epoch=epoch), self.assertRaises(SyncContractError) as raised:
                self.store.activate_contract_project(
                    self.context["local_key"],
                    project_sync_mode=mode,
                    migration_epoch=epoch,
                    **common,
                )
            self.assertEqual(raised.exception.code, expected)

        migrating = self.store.activate_contract_project(
            self.context["local_key"],
            project_sync_mode="MIGRATING",
            migration_epoch=1,
            **common,
        )
        self.assertEqual((migrating["project_sync_mode"], migrating["migration_epoch"]), ("MIGRATING", 1))

        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                "UPDATE sync_projects SET project_sync_mode = ?, migration_epoch = ? WHERE local_key = ?",
                ("MIGRATING", 1, self.context["local_key"]),
            )
            for mode, epoch in (
                ("MIGRATING", 2),
                ("ID_BASED", 2),
                ("LEGACY", 0),
            ):
                with self.subTest(
                    sql_from="MIGRATING/1", sql_mode=mode, sql_epoch=epoch
                ), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "UPDATE sync_projects SET project_sync_mode = ?, migration_epoch = ? WHERE local_key = ?",
                        (mode, epoch, self.context["local_key"]),
                    )

        for mode, epoch in (("MIGRATING", 2), ("ID_BASED", 2)):
            with self.subTest(mode=mode, epoch=epoch), self.assertRaises(SyncContractError) as raised:
                self.store.activate_contract_project(
                    self.context["local_key"],
                    project_sync_mode=mode,
                    migration_epoch=epoch,
                    **common,
                )
            self.assertEqual(raised.exception.code, "STALE_MIGRATION_EPOCH")

        completed = self.store.activate_contract_project(
            self.context["local_key"],
            project_sync_mode="ID_BASED",
            migration_epoch=1,
            **common,
        )
        self.assertEqual((completed["project_sync_mode"], completed["migration_epoch"]), ("ID_BASED", 1))

        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute(
                "UPDATE sync_projects SET project_sync_mode = ?, migration_epoch = ? WHERE local_key = ?",
                ("ID_BASED", 1, self.context["local_key"]),
            )
            for mode, epoch in (
                ("ID_BASED", 2),
                ("MIGRATING", 1),
                ("LEGACY", 0),
                ("LEGACY", 1),
                ("MIGRATING", 0),
                ("ID_BASED", 0),
            ):
                with self.subTest(
                    sql_from="ID_BASED/1", sql_mode=mode, sql_epoch=epoch
                ), self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "UPDATE sync_projects SET project_sync_mode = ?, migration_epoch = ? WHERE local_key = ?",
                        (mode, epoch, self.context["local_key"]),
                    )
            pair = connection.execute(
                "SELECT project_sync_mode, migration_epoch FROM sync_projects WHERE local_key = ?",
                (self.context["local_key"],),
            ).fetchone()
            self.assertEqual(pair, ("ID_BASED", 1))

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

    def test_contract_document_commit_uses_exact_wire_and_applies_complete_result(self):
        self._activate_id_based()
        self.context["writer_device_id"] = DEVICE_ID
        operation = self.store.enqueue(
            self.context, "Empty.md", "", relative_path="Empty.md"
        )
        request = self.store.structure_batch_request(operation["batch_id"])
        self.assertEqual(request["kind"], "document_commit_request")
        self.assertEqual(request["ordered_intents"][0]["intent_kind"], "create")
        self.assertEqual(request["ordered_intents"][0]["payload"]["content"], "")
        self.assertIn("document_commit_v1", request["batch"]["client_capabilities"])
        response = self._document_success(request)

        class RpcCall:
            def __init__(inner_self, data):
                inner_self.data = data

            def execute(inner_self):
                return type("Response", (), {"data": inner_self.data})()

        class Client:
            def __init__(inner_self):
                inner_self.calls = []

            def rpc(inner_self, name, params):
                inner_self.calls.append((name, params))
                return RpcCall({"project_id": PROJECT_ID} if name == "ensure_project" else response)

        manager = SyncManager()
        manager._v2_store = self.store
        manager._v2_context = dict(self.context)
        manager._v2_device_id = DEVICE_ID
        manager.supabase = Client()
        self.store.mark_attempt(operation["operation_id"])
        result = manager._process_v2_operation(operation["operation_id"])
        self.assertEqual(result["kind"], "committed")
        self.assertEqual(
            manager.supabase.calls[-1],
            ("document_commit", {"p_request": request}),
        )
        self.store.mark_success(operation["operation_id"], result["result"])
        completed = self.store.operation(operation["operation_id"])
        document = self.store.get_document_by_id(operation["document_id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual((document["revision"], document["structure_revision"]), (1, 1))

    def test_contract_document_response_loss_reuses_recorded_result(self):
        self._activate_id_based()
        self.context["writer_device_id"] = DEVICE_ID
        operation = self.store.enqueue(self.context, "Draft.md", "body")
        request = self.store.structure_batch_request(operation["batch_id"])
        response = self._document_success(request)
        self.store.mark_attempt(operation["operation_id"])
        self.store.record_document_batch_response(operation["batch_id"], response)

        reopened = SyncV2Store(self.db_path)
        recovered = reopened.next_ready_operation(self.context["local_key"])
        self.assertEqual(recovered["operation_id"], operation["operation_id"])

        class RpcCall:
            def execute(inner_self):
                return type("Response", (), {"data": {"project_id": PROJECT_ID}})()

        class Client:
            def __init__(inner_self):
                inner_self.calls = []

            def rpc(inner_self, name, params):
                inner_self.calls.append((name, params))
                if name == "document_commit":
                    raise AssertionError("recorded response must prevent duplicate apply")
                return RpcCall()

        manager = SyncManager()
        manager._v2_store = reopened
        manager._v2_context = dict(self.context)
        manager._v2_device_id = DEVICE_ID
        manager.supabase = Client()
        reopened.mark_attempt(operation["operation_id"])
        result = manager._process_v2_operation(operation["operation_id"])
        self.assertEqual(result["kind"], "committed")
        self.assertEqual([name for name, _ in manager.supabase.calls], ["ensure_project"])

    def test_contract_document_partial_response_is_not_recorded_or_applied(self):
        self._activate_id_based()
        self.context["writer_device_id"] = DEVICE_ID
        operation = self.store.enqueue(self.context, "Draft.md", "body")
        request = self.store.structure_batch_request(operation["batch_id"])
        partial = self._document_success(request)
        partial["results"] = []
        self.store.mark_attempt(operation["operation_id"])
        with self.assertRaises(SyncContractError) as raised:
            self.store.record_document_batch_response(operation["batch_id"], partial)
        self.assertEqual(raised.exception.code, "PARTIAL_BATCH_RESPONSE")
        self.assertIsNone(self.store.document_batch_response(operation["batch_id"]))
        self.assertEqual(self.store.operation(operation["operation_id"])["status"], "inflight")

    def test_contract_document_committed_then_replayed_response_is_equivalent(self):
        self._activate_id_based()
        self.context["writer_device_id"] = DEVICE_ID
        operation = self.store.enqueue(self.context, "Draft.md", "body")
        request = self.store.structure_batch_request(operation["batch_id"])
        committed = self._document_success(request)
        replayed = self._document_success(request, status="replayed")
        self.assertEqual(
            self.store.record_document_batch_response(
                operation["batch_id"], committed
            ),
            committed,
        )
        self.assertEqual(
            self.store.record_document_batch_response(
                operation["batch_id"], replayed
            ),
            committed,
        )

    def test_contract_document_requires_server_proven_folder_identity(self):
        self._activate_id_based()
        self.context["writer_device_id"] = DEVICE_ID
        with self.assertRaises(SyncContractError) as raised:
            self.store.enqueue(self.context, "메인/원고/Draft.md", "body")
        self.assertEqual(raised.exception.code, "CONTRACT_STRUCTURE_IDS_REQUIRED")
        folder_id = str(uuid.uuid4())
        self.store.replace_folder_snapshots(self.context["local_key"], [{
            "folder_id": folder_id,
            "parent_folder_id": None,
            "local_path": "메인/원고",
            "name": "원고",
            "revision": 1,
            "is_deleted": False,
        }])
        operation = self.store.enqueue(
            self.context, "메인/원고/Draft.md", "body"
        )
        request = self.store.structure_batch_request(operation["batch_id"])
        self.assertEqual(
            request["ordered_intents"][0]["payload"]["parent_folder_id"],
            folder_id,
        )

    def test_contract_document_rejects_normalized_sibling_collision(self):
        self._activate_id_based()
        self.context["writer_device_id"] = DEVICE_ID
        self.store.enqueue(self.context, "Ｆｏｏ.md", "first")
        with self.assertRaises(SyncContractError) as raised:
            self.store.enqueue(self.context, "foo.MD", "second")
        self.assertEqual(raised.exception.code, "PATH_CONFLICT")

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
