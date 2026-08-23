import copy
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import uuid
from contextlib import closing, redirect_stdout
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
    read_handshake_compatibility,
    require_server_compatibility,
    safe_trace,
    validate_atomic_structure_response,
    validate_document_commit_response,
)
from sync_manager import SyncManager
from sync_v2_store import STAGE8_USER_VERSION, SyncV2Store
from unicode15_casefold import (
    MAPPING_COUNT,
    MAPPING_SHA256,
    SOURCE_COMMIT,
    SOURCE_PATH,
    UNICODE_VERSION,
    frozen_casefold,
    mapping_sha256,
    validate_frozen_table,
)


PROJECT_ID = "00000000-0000-4000-8000-000000000201"
DEVICE_ID = "60000000-0000-4000-8000-000000000201"
BATCH_ID = "10000000-0000-4000-8000-000000000201"
OTHER_PROJECT_ID = "00000000-0000-4000-8000-000000000202"


LIVE_PROJECT_ID = "01c1b72f-34fb-4fd4-abec-cbe49bb1b3a2"

# Recorded from staging on 2026-08-23, before the 0.2.0 allowlist row was
# switched on.
LIVE_INACTIVE_REPLY = (
    '{"supported":false,"project_id":"01c1b72f-34fb-4fd4-abec-cbe49bb1b3a2",'
    '"migration_epoch":0,"contract_version":null,"project_sync_mode":"LEGACY",'
    '"server_capabilities":[],"server_contract_sha256":null,'
    '"server_protocol_version":null,"canonical_contract_sha256":null,'
    '"supported_protocol_versions":[]}'
)

# Recorded from the same project after the row was switched on. Note the mode:
# an allowlisted contract does not move a project off LEGACY, and epoch stays 0.
LIVE_ACTIVE_REPLY = (
    '{"supported":true,"project_id":"01c1b72f-34fb-4fd4-abec-cbe49bb1b3a2",'
    '"migration_epoch":0,"contract_version":"0.2.0",'
    '"project_sync_mode":"LEGACY","server_capabilities":'
    '["atomic_structure_commit","contract_allowlist_validation",'
    '"project_mode_migration_lock","folder_tombstones","id_tree_validation",'
    '"legacy_epoch_zero_adapter","storage_name_v1","document_commit_v1"],'
    '"server_contract_sha256":'
    '"416c1b99edb9bda694731dee4b25688d9d82d1f32610aa23ddfda571ec3c7670",'
    '"server_protocol_version":3,"canonical_contract_sha256":'
    '"416c1b99edb9bda694731dee4b25688d9d82d1f32610aa23ddfda571ec3c7670",'
    '"supported_protocol_versions":[3]}'
)


def live_reply(raw, project_id=PROJECT_ID, **overrides):
    """One of the recorded replies, readdressed to the fixture's project."""
    handshake = json.loads(raw)
    handshake["project_id"] = project_id
    handshake.update(overrides)
    return handshake


def supported_handshake(project_id=PROJECT_ID, **overrides):
    """The reply the server sends with the 0.2.0 allowlist row switched on."""
    return live_reply(LIVE_ACTIVE_REPLY, project_id, **overrides)


def unsupported_handshake(project_id=PROJECT_ID, **overrides):
    """The reply the server sends with no allowlist row for this client."""
    return live_reply(LIVE_INACTIVE_REPLY, project_id, **overrides)


def arm_contract_handshake(manager, outcome="supported"):
    """Leave behind the reading a fresh handshake would have recorded."""
    manager._contract_handshake = {
        "generation": manager._v2_context_generation,
        "project_id": manager._v2_context["project_id"],
        "identity": manager._contract_identity(),
        "contract_sha256": CANONICAL_CONTRACT_SHA256,
        "observed_at": "",
        "outcome": outcome,
    }
    manager._contract_handshake_attempt = manager._v2_context_generation


class _HandshakeClient:
    """A Supabase stand-in that answers get_sync_handshake and counts calls."""

    def __init__(self, reply, email="writer@example.invalid"):
        self.reply = reply
        self.calls = []
        # ``ensure_session_valid`` leaves a client with no auth alone, which is
        # what keeps these cases off the token refresh path entirely.
        self.auth = None
        self._antigravity_email = email
        self._antigravity_authenticated = True

    def rpc(self, name, params):
        self.calls.append((name, dict(params)))
        return SimpleNamespace(execute=self._answer)

    def _answer(self):
        if isinstance(self.reply, Exception):
            raise self.reply
        return SimpleNamespace(data=self.reply)


def contract_root():
    configured = os.environ.get("WRITERPAD_SYNC_CONTRACT_DIR")
    candidates = [
        Path(configured) if configured else None,
        # The released checkout is vendored in this repository so the contract
        # tests keep running no matter where the working tree sits.
        Path(__file__).resolve().parents[1] / "sync-contract",
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

    def test_frozen_unicode15_casefold_table_integrity(self):
        self.assertEqual(UNICODE_VERSION, "15.0.0")
        self.assertEqual(
            SOURCE_COMMIT,
            "1fbc31bee3e36d46e86e8723936b5c2c7b71081f",
        )
        self.assertEqual(
            SOURCE_PATH,
            "supabase/migrations/"
            "20260811010000_sync_contract_0_1_0_foundation.sql",
        )
        self.assertEqual(MAPPING_COUNT, 1530)
        self.assertEqual(
            MAPPING_SHA256,
            "2a17566332a6a1e32afbfd431f9c73a7f30caa22fb4ce881c4e35ebc2b7f2284",
        )
        self.assertEqual(mapping_sha256(), MAPPING_SHA256)
        validate_frozen_table()

    def test_frozen_unicode15_casefold_representative_values(self):
        cases = (
            ("ABC", "abc"),
            ("Straße", "strasse"),
            ("\u0130", "i\u0307"),
            ("\u13a0", "\u13a0"),
            ("\uab70", "\u13a0"),
            ("\u1c80", "\u0432"),
            ("\u1c88", "\ua64b"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(frozen_casefold(source), expected)
                self.assertEqual(normalize_storage_name(source).normalized, expected)

    def test_frozen_casefold_preserves_previous_runtime_output_for_all_scalars(self):
        for codepoint in range(sys.maxunicode + 1):
            character = chr(codepoint)
            if frozen_casefold(character) != character.casefold():
                self.fail(f"case-fold mismatch for U+{codepoint:06X}")

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
        self.store.set_contract_path_enabled(self.context["local_key"], True)
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
        arm_contract_handshake(manager)
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

    def test_a_pull_between_delete_and_restore_keeps_the_row_it_needs(self):
        """서버가 폴더를 tombstone 한 뒤 pull 이 와도 복원 intent 를 만들 수 있어야 한다.

        폴더 projection 은 서버의 live 행을 받아 통째로 교체한다. 삭제된 폴더는
        live 집합에 없으므로, 그 행을 지워버리면 revision 이 함께 사라진다.
        계약은 create 가 아닌 intent 에 revision 1 이상을 요구하므로, revision 을
        잃으면 복원 intent 를 만들 수 없다 — 그리고 아무 말 없이 빈 목록이 된다.
        """
        manager = self._contract_manager()
        parent = self._folder_snapshot("메인/메모장")
        created_path = "메인/메모장/새 폴더"
        created = self._folder_snapshot(
            created_path, parent_id=parent["folder_id"], revision=4
        )
        self.store.replace_folder_snapshots(
            self.context["local_key"], [parent, created]
        )
        folder_id = created["folder_id"]

        delete_operations = manager.record_tombstone(
            created_path, "메인/휴지통/새 폴더", retry=False
        )
        manager.queue_contract_path_change_with_order(
            delete_operations, {"메인/메모장": []}, retry=False
        )
        self.assertTrue(self.store.get_folder_by_id(folder_id)["is_deleted"])

        # 여기서 pull 이 한 번 돈다. 서버는 그 폴더를 live 로 주지 않는다.
        self.store.replace_folder_snapshots(
            self.context["local_key"], [parent]
        )
        retired = self.store.get_folder_by_id(folder_id)
        self.assertIsNotNone(retired, "삭제된 폴더의 행이 사라졌다")
        self.assertTrue(retired["is_deleted"])
        self.assertEqual(retired["revision"], 4, "복원이 걸 revision 이 사라졌다")
        self.assertEqual(retired["local_path"], created_path)

        restore_operations = manager.record_restore(
            "메인/휴지통/새 폴더",
            created_path,
            original_rel_path=created_path,
            retry=False,
        )
        restore_request = manager.queue_contract_path_change_with_order(
            restore_operations,
            {"메인/메모장": ["새 폴더"]},
            retry=False,
        )
        intents = restore_request["ordered_intents"]
        self.assertIn("restore", [item["intent_kind"] for item in intents])
        restore_intent = next(
            item for item in intents if item["intent_kind"] == "restore"
        )
        self.assertEqual(restore_intent["entity_id"], folder_id)
        # create 가 아닌 intent 는 revision 1 이상이어야 한다.
        self.assertGreaterEqual(restore_intent["base_revision"], 1)
        self.assertEqual(restore_intent["base_revision"], 4)

    def test_a_retired_row_only_moves_when_a_live_folder_claims_its_path(self):
        """은퇴한 행은 자기 경로를 지킨다. 그 경로를 새 폴더가 가져갈 때만 비켜난다."""
        parent = self._folder_snapshot("메인/메모장")
        contested = "메인/메모장/같은 자리"
        quiet = "메인/메모장/조용한 자리"
        first = self._folder_snapshot(
            contested, parent_id=parent["folder_id"], revision=2
        )
        second = self._folder_snapshot(
            quiet, parent_id=parent["folder_id"], revision=3
        )
        self.store.replace_folder_snapshots(
            self.context["local_key"], [parent, first, second]
        )

        # 서버가 둘 다 내리고, 그중 한 자리를 새 폴더가 차지한다.
        replacement = self._folder_snapshot(
            contested, parent_id=parent["folder_id"], revision=1
        )
        self.store.replace_folder_snapshots(
            self.context["local_key"], [parent, replacement]
        )

        moved = self.store.get_folder_by_id(first["folder_id"])
        stayed = self.store.get_folder_by_id(second["folder_id"])
        self.assertTrue(moved["is_deleted"])
        self.assertTrue(stayed["is_deleted"])
        self.assertEqual(moved["revision"], 2)
        self.assertEqual(stayed["revision"], 3)
        self.assertEqual(
            stayed["local_path"], quiet, "다투지 않은 행이 자리를 잃었다"
        )
        self.assertNotEqual(moved["local_path"], contested)
        # 그 자리는 새 폴더가 live 로 가진다.
        live = self.store.get_folder_by_path(self.context["local_key"], contested)
        self.assertEqual(live["folder_id"], replacement["folder_id"])

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

    def _contract_lifecycle_with_documents(self, *, deleted):
        manager = self._contract_manager()
        parent = self._folder_snapshot("메인/메모장")
        original_path = "메인/메모장/원고 묶음"
        folder = self._folder_snapshot(
            original_path, parent_id=parent["folder_id"]
        )
        folder["is_deleted"] = deleted
        self.store.replace_folder_snapshots(
            self.context["local_key"], [parent, folder]
        )
        self.store.replace_tree_order_snapshots(
            self.context["local_key"], [{
                "tree_order_id": str(uuid.uuid4()),
                "parent_folder_id": parent["folder_id"],
                "children": [] if deleted else [folder["folder_id"]],
                "revision": 1,
            }]
        )
        source_root = (
            "메인/휴지통/원고 묶음" if deleted else original_path
        )
        document_ids = []
        document_names = []
        for name in ("첫째.txt", "둘째.txt"):
            local_path = f"{source_root}/{name}"
            document = self.store.ensure_document(
                self.context["local_key"], local_path, f"{name} 내용"
            )
            document_ids.append(document["document_id"])
            document_names.append(name)
        with closing(sqlite3.connect(self.db_path)) as connection:
            for document_id, name in zip(document_ids, document_names):
                connection.execute(
                    "UPDATE sync_documents SET revision = 1, "
                    "structure_revision = 1, parent_folder_id = ?, "
                    "storage_name_key = ?, is_deleted = ?, server_path = "
                    "replace(local_path, '메인/휴지통/원고 묶음', "
                    "'메인/메모장/원고 묶음') WHERE document_id = ?",
                    (
                        folder["folder_id"],
                        normalize_storage_name(name).normalized,
                        int(deleted),
                        document_id,
                    ),
                )
            connection.commit()
        manager._v2_wpm.read_text_file = lambda path: f"{path} 내용"
        manager._v2_wpm.update_trash_metadata = lambda *args, **kwargs: True
        manager._v2_wpm.relocate_trash_item = lambda path: path + "-relocated"
        return manager, parent, folder, document_ids

    def _assert_lifecycle_sqlite_rollback(
        self, manager, operations, tree_order, document_ids, expected_paths,
    ):
        with closing(sqlite3.connect(self.db_path)) as connection:
            before_operations = connection.execute(
                "SELECT count(*) FROM sync_operations"
            ).fetchone()[0]
            before_batches = connection.execute(
                "SELECT count(*) FROM sync_contract_batches"
            ).fetchone()[0]
        real_enqueue = self.store.enqueue
        call_count = 0

        def fail_after_first_document(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("injected lifecycle batch failure")
            return real_enqueue(*args, **kwargs)

        with patch.object(
            self.store, "enqueue", side_effect=fail_after_first_document
        ):
            with self.assertRaisesRegex(
                RuntimeError, "injected lifecycle batch failure"
            ):
                manager.queue_contract_path_change_with_order(
                    operations, tree_order, retry=False
                )

        self.assertEqual(call_count, 2)
        self.assertEqual(
            [
                self.store.get_document_by_id(document_id)["local_path"]
                for document_id in document_ids
            ],
            expected_paths,
        )
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM sync_operations"
                ).fetchone()[0],
                before_operations,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM sync_contract_batches"
                ).fetchone()[0],
                before_batches,
            )

    def test_nonempty_folder_delete_batch_failure_rolls_back_sqlite(self):
        manager, _parent, folder, document_ids = (
            self._contract_lifecycle_with_documents(deleted=False)
        )
        original_path = folder["local_path"]
        trash_path = "메인/휴지통/원고 묶음"
        operations = manager.record_tombstone(
            original_path, trash_path, retry=False
        )

        self._assert_lifecycle_sqlite_rollback(
            manager,
            operations,
            {"메인/메모장": []},
            document_ids,
            [f"{original_path}/첫째.txt", f"{original_path}/둘째.txt"],
        )
        stored_folder = self.store.get_folder_by_id(folder["folder_id"])
        self.assertEqual(stored_folder["local_path"], original_path)
        self.assertFalse(stored_folder["is_deleted"])

    def test_nonempty_folder_restore_batch_failure_rolls_back_sqlite(self):
        manager, _parent, folder, document_ids = (
            self._contract_lifecycle_with_documents(deleted=True)
        )
        trash_path = "메인/휴지통/원고 묶음"
        restored_path = "메인/메모장/복원 원고"
        operations = manager.record_restore(
            trash_path,
            restored_path,
            original_rel_path=folder["local_path"],
            retry=False,
        )

        self._assert_lifecycle_sqlite_rollback(
            manager,
            operations,
            {"메인/메모장": ["복원 원고"]},
            document_ids,
            [f"{trash_path}/첫째.txt", f"{trash_path}/둘째.txt"],
        )
        stored_folder = self.store.get_folder_by_id(folder["folder_id"])
        self.assertEqual(stored_folder["local_path"], folder["local_path"])
        self.assertTrue(stored_folder["is_deleted"])

    def test_nonempty_folder_delete_and_restore_commit_all_sqlite_projections(self):
        manager, _parent, folder, document_ids = (
            self._contract_lifecycle_with_documents(deleted=False)
        )
        original_path = folder["local_path"]
        trash_path = "메인/휴지통/원고 묶음"
        delete_operations = manager.record_tombstone(
            original_path, trash_path, retry=False
        )
        manager.queue_contract_path_change_with_order(
            delete_operations, {"메인/메모장": []}, retry=False
        )

        self.assertTrue(
            self.store.get_folder_by_id(folder["folder_id"])["is_deleted"]
        )
        self.assertEqual(
            [
                self.store.get_document_by_id(document_id)["local_path"]
                for document_id in document_ids
            ],
            [f"{trash_path}/첫째.txt", f"{trash_path}/둘째.txt"],
        )
        restored_path = "메인/메모장/복원 원고"
        restore_operations = manager.record_restore(
            trash_path,
            restored_path,
            original_rel_path=original_path,
            retry=False,
        )
        manager.queue_contract_path_change_with_order(
            restore_operations,
            {"메인/메모장": ["복원 원고"]},
            retry=False,
        )

        restored = self.store.get_folder_by_id(folder["folder_id"])
        self.assertEqual(restored["local_path"], restored_path)
        self.assertFalse(restored["is_deleted"])
        self.assertEqual(
            [
                self.store.get_document_by_id(document_id)["local_path"]
                for document_id in document_ids
            ],
            [f"{restored_path}/첫째.txt", f"{restored_path}/둘째.txt"],
        )
        with closing(sqlite3.connect(self.db_path)) as connection:
            rows = connection.execute(
                "SELECT document_id, is_deleted FROM sync_operations "
                "ORDER BY queue_id"
            ).fetchall()
        self.assertEqual(len(rows), 4)
        self.assertEqual([row[1] for row in rows], [1, 1, 0, 0])
        self.assertEqual({row[0] for row in rows}, set(document_ids))

    def test_compatible_legacy_uses_protocol_three_without_promotion(self):
        project = self.store.activate_contract_project(
            self.context["local_key"],
            project_sync_mode="LEGACY",
            migration_epoch=0,
            server_protocol_version=3,
            server_contract_sha256=CANONICAL_CONTRACT_SHA256,
            server_capabilities=SERVER_CAPABILITIES,
        )
        self.store.set_contract_path_enabled(self.context["local_key"], True)
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
        arm_contract_handshake(manager)
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
        self.assertEqual(self.store.operation_state_divergences(), [])
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


class ContractHandshakeGateTests(unittest.TestCase):
    """The handshake reports; only a local decision opens the write path."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SyncV2Store(str(Path(self.temp.name) / "sync.sqlite3"))
        self.context = self.store.configure_project(
            str(Path(self.temp.name) / "writing"), "Handshake", PROJECT_ID
        )
        self.manager = SyncManager()
        self.manager._v2_store = self.store
        self.manager._v2_context = dict(self.context)
        self.manager._v2_device_id = DEVICE_ID
        self.manager._v2_wpm = SimpleNamespace(
            writing_root_path=str(Path(self.temp.name) / "writing"),
            read_text_file=lambda _path: None,
            project_settings={"tree_order": {}},
            save_settings=lambda: True,
        )
        Path(self.manager._v2_wpm.writing_root_path).mkdir(
            parents=True, exist_ok=True
        )
        # One manager serves the whole process, so a reading left by an earlier
        # case would otherwise arm this one.
        self.manager._forget_contract_handshake()
        self.manager._contract_handshake_error = ""

    def tearDown(self):
        self.manager._forget_contract_handshake()
        self.manager.supabase = None
        self.manager._v2_context = None
        self.manager._v2_store = None
        self.manager._v2_device_id = None
        self.manager._v2_wpm = None
        self.temp.cleanup()

    def _attach(self, reply):
        client = _HandshakeClient(reply)
        self.manager.supabase = client
        return client

    def _project(self):
        return self.store.get_project(self.context["local_key"])

    def test_supported_handshake_alone_does_not_open_the_contract_path(self):
        """The gate is the whole point: server assent is not local consent."""
        self._attach(supported_handshake())
        reading = self.manager.perform_contract_handshake()

        self.assertEqual(reading["outcome"], "supported")
        self.assertTrue(self.manager.contract_handshake_is_fresh())
        # The server state was recorded in full ...
        project = self._project()
        self.assertEqual(project["project_sync_mode"], "LEGACY")
        self.assertEqual(project["migration_epoch"], 0)
        self.assertEqual(project["server_protocol_version"], 3)
        self.assertEqual(
            project["active_contract_sha256"], CANONICAL_CONTRACT_SHA256
        )
        # ... and the write path is still the legacy one.
        self.assertFalse(project["contract_path_enabled"])
        self.assertFalse(self.manager.contract_path_enabled())
        self.assertFalse(self.manager._uses_contract_structure())

    def test_open_gate_with_a_fresh_handshake_turns_the_path_on(self):
        client = self._attach(supported_handshake())
        project = self.manager.enable_contract_path()

        self.assertTrue(project["contract_path_enabled"])
        self.assertTrue(project["contract_path_enabled_at"])
        self.assertTrue(self.manager._uses_contract_structure())
        self.assertEqual(client.calls[-1][0], "get_sync_handshake")
        self.assertEqual(client.calls[-1][1], {
            "p_project_id": PROJECT_ID,
            "p_contract_sha256": CANONICAL_CONTRACT_SHA256,
        })

    def test_activation_takes_its_own_handshake_and_never_a_stored_one(self):
        """A positive from earlier in the session cannot stand in for now."""
        self._attach(supported_handshake())
        self.manager.perform_contract_handshake()
        self.assertTrue(self.manager.contract_handshake_is_fresh())

        # The server withdrew support between the reading and the activation.
        client = self._attach(unsupported_handshake())
        with self.assertRaises(SyncContractError) as raised:
            self.manager.enable_contract_path()

        self.assertEqual(raised.exception.code, "CONTRACT_NOT_ALLOWED")
        self.assertEqual(client.calls[-1][0], "get_sync_handshake")
        self.assertFalse(self.manager.contract_path_enabled())
        self.assertFalse(self.manager._uses_contract_structure())

    def test_open_gate_without_a_fresh_reading_keeps_the_path_closed(self):
        """Both halves are required, in the other direction too."""
        self.store.set_contract_path_enabled(self.context["local_key"], True)
        self.store.activate_contract_project(
            self.context["local_key"],
            project_sync_mode="MIGRATING",
            migration_epoch=1,
            server_protocol_version=3,
            server_contract_sha256=CANONICAL_CONTRACT_SHA256,
            server_capabilities=SERVER_CAPABILITIES,
        )
        self.assertTrue(self.manager.contract_path_enabled())
        self.assertFalse(self.manager.contract_handshake_is_fresh())
        self.assertFalse(self.manager._uses_contract_structure())

    def test_unsupported_reply_stores_nothing_and_arms_nothing(self):
        self._attach(unsupported_handshake())
        reading = self.manager.perform_contract_handshake()

        self.assertEqual(reading["outcome"], "unsupported")
        self.assertFalse(self.manager.contract_handshake_is_fresh())
        project = self._project()
        self.assertEqual(project["project_sync_mode"], "LEGACY")
        self.assertEqual(project["migration_epoch"], 0)
        self.assertIsNone(project["server_protocol_version"])
        self.assertIsNone(project["active_contract_sha256"])
        self.assertIsNone(project["server_capabilities_json"])
        self.assertFalse(self.manager._uses_contract_structure())

    def test_digest_protocol_and_capability_faults_are_each_refused(self):
        cases = (
            # A server on a different contract, answering consistently about it.
            ({"server_contract_sha256": "0" * 64,
              "canonical_contract_sha256": "0" * 64,
              "contract_version": None}, "CONTRACT_DIGEST_MISMATCH"),
            # A server that has not reached protocol 3 yet.
            ({"server_protocol_version": 2,
              "supported_protocol_versions": [2]}, "PROTOCOL_TOO_OLD"),
            ({"server_capabilities": list(SERVER_CAPABILITIES[:-1])},
             "CAPABILITY_MISMATCH"),
            # LEGACY is the mode the live reply carries, and it owns epoch 0.
            ({"migration_epoch": 1}, "STALE_MIGRATION_EPOCH"),
        )
        for overrides, expected in cases:
            with self.subTest(overrides=sorted(overrides)):
                self.manager._forget_contract_handshake()
                self._attach(supported_handshake(**overrides))
                with self.assertRaises(SyncContractError) as raised:
                    self.manager.perform_contract_handshake()
                self.assertEqual(raised.exception.code, expected)
                self.assertFalse(self.manager.contract_handshake_is_fresh())
                self.assertFalse(self._project()["contract_path_enabled"])
                self.assertEqual(
                    self._project()["project_sync_mode"], "LEGACY"
                )

    def test_malformed_compatibility_fields_normalize_to_invalid_argument(self):
        """A null protocol version is the shape the live reply already has."""
        cases = (
            ("server_protocol_version", None),
            ("server_protocol_version", ""),
            ("server_protocol_version", "3"),
            ("server_protocol_version", True),
            ("server_contract_sha256", None),
            ("server_contract_sha256", CANONICAL_CONTRACT_SHA256.upper()),
            ("server_contract_sha256", CANONICAL_CONTRACT_SHA256[:32]),
            ("server_capabilities", None),
            ("server_capabilities", "atomic_structure_commit"),
            ("server_capabilities", [1, 2, 3]),
            ("project_sync_mode", None),
            ("project_sync_mode", "ID_BASED_V2"),
            ("migration_epoch", None),
            ("migration_epoch", "1"),
            ("migration_epoch", -1),
            ("supported_protocol_versions", 3),
            ("supported_protocol_versions", "3"),
            ("supported_protocol_versions", [None]),
            ("supported_protocol_versions", ["3"]),
            ("supported_protocol_versions", [True]),
        )
        for field, value in cases:
            with self.subTest(field=field, value=repr(value)):
                self.manager._forget_contract_handshake()
                self._attach(supported_handshake(**{field: value}))
                with self.assertRaises(SyncContractError) as raised:
                    self.manager.perform_contract_handshake()
                self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
                self.assertFalse(self.manager.contract_handshake_is_fresh())
                self.assertFalse(self.manager._uses_contract_structure())

    def test_require_server_compatibility_never_raises_a_bare_type_error(self):
        """The contract error is the only thing callers have to catch."""
        common = {
            "project_sync_mode": "MIGRATING",
            "migration_epoch": 1,
            "server_contract_sha256": CANONICAL_CONTRACT_SHA256,
            "server_capabilities": SERVER_CAPABILITIES,
        }
        for value in (None, "", "three", [], {}, 3.5):
            with self.subTest(value=repr(value)):
                with self.assertRaises(SyncContractError) as raised:
                    require_server_compatibility(
                        server_protocol_version=value, **common
                    )
                self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        for value in (None, "folders", 7):
            with self.subTest(capabilities=repr(value)):
                candidate = dict(common, server_capabilities=value)
                with self.assertRaises(SyncContractError):
                    require_server_compatibility(
                        server_protocol_version=3, **candidate
                    )

    def test_read_handshake_compatibility_rejects_a_non_mapping(self):
        for reply in (None, [], "supported", 3):
            with self.subTest(reply=repr(reply)):
                with self.assertRaises(SyncContractError) as raised:
                    read_handshake_compatibility(reply)
                self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")

    def test_a_reply_about_another_project_is_refused(self):
        self._attach(supported_handshake(project_id=OTHER_PROJECT_ID))
        with self.assertRaises(SyncContractError) as raised:
            self.manager.perform_contract_handshake()
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(self._project()["project_sync_mode"], "LEGACY")

    def test_server_and_network_failures_leave_the_legacy_path_working(self):
        failures = (
            RuntimeError("FORBIDDEN"),
            RuntimeError("INVALID_ARGUMENT"),
            RuntimeError("NETWORK_UNAVAILABLE"),
            RuntimeError("permission denied for function get_sync_handshake"),
        )
        for failure in failures:
            with self.subTest(failure=str(failure)):
                self.manager._forget_contract_handshake()
                self.manager._contract_handshake_attempt = None
                self._attach(failure)
                # The quiet, once-per-project call swallows every one of these.
                self.assertIsNone(self.manager._ensure_contract_handshake())
                self.assertFalse(self.manager.contract_handshake_is_fresh())
                self.assertFalse(self.manager._uses_contract_structure())
                # And the legacy queue still takes work.
                operation = self.store.enqueue(
                    self.manager._v2_context,
                    "failure.txt",
                    str(failure),
                    relative_path="failure.txt",
                )
                self.assertEqual(operation["status"], "pending")
                self.assertEqual(operation["relative_path"], "failure.txt")

    def test_a_withdrawn_answer_disarms_the_session(self):
        self._attach(supported_handshake())
        self.manager.enable_contract_path()
        self.assertTrue(self.manager._uses_contract_structure())

        # Any later call that comes back with one of the withdrawing codes
        # drops the reading, and the gate alone cannot hold the path open.
        with self.assertRaises(RuntimeError):
            self.manager._call_with_session(
                lambda: (_ for _ in ()).throw(RuntimeError("FORBIDDEN")),
                self.manager.supabase,
            )
        self.assertFalse(self.manager.contract_handshake_is_fresh())
        self.assertTrue(self.manager.contract_path_enabled())
        self.assertFalse(self.manager._uses_contract_structure())

    def test_signing_in_as_somebody_else_drops_the_reading(self):
        self._attach(supported_handshake())
        self.manager.enable_contract_path()
        self.assertTrue(self.manager._uses_contract_structure())

        self.manager.supabase._antigravity_email = "other@example.invalid"
        self.assertFalse(self.manager.contract_handshake_is_fresh())
        self.assertFalse(self.manager._uses_contract_structure())

    def test_releasing_the_project_drops_the_reading(self):
        self._attach(supported_handshake())
        self.manager.enable_contract_path()
        self.assertTrue(self.manager._uses_contract_structure())

        self.manager.release_v2()
        self.assertIsNone(self.manager._contract_handshake)
        self.assertFalse(self.manager.contract_handshake_is_fresh())

    def test_the_handshake_is_asked_once_per_opened_project(self):
        client = self._attach(supported_handshake())
        for _ in range(4):
            self.manager._ensure_contract_handshake()
        self.assertEqual(len(client.calls), 1)

        # A new generation is a new project binding, and that asks again.
        self.manager._v2_context_generation += 1
        self.manager._ensure_contract_handshake()
        self.assertEqual(len(client.calls), 2)

    def test_closing_the_gate_by_hand_stops_using_the_contract_path(self):
        self._attach(supported_handshake())
        self.manager.enable_contract_path()
        self.assertTrue(self.manager._uses_contract_structure())

        project = self.manager.disable_contract_path()
        self.assertFalse(project["contract_path_enabled"])
        self.assertIsNone(project["contract_path_enabled_at"])
        self.assertFalse(self.manager._uses_contract_structure())
        # The server reading it was carrying stays on the row as the last
        # observation. It is diagnostic now, and it opens nothing.
        self.assertEqual(project["server_protocol_version"], 3)
        self.assertEqual(
            project["active_contract_sha256"], CANONICAL_CONTRACT_SHA256
        )

    def test_the_recorded_active_reply_is_read_exactly_as_the_server_sent_it(self):
        """The live answer is LEGACY at epoch 0, and it must stay that way."""
        self._attach(live_reply(LIVE_ACTIVE_REPLY))
        reading = self.manager.perform_contract_handshake()

        self.assertEqual(reading["outcome"], "supported")
        self.assertEqual(reading["project_sync_mode"], "LEGACY")
        self.assertEqual(reading["migration_epoch"], 0)
        project = self._project()
        # An allowlisted contract does not promote the project. Mode and epoch
        # are exactly where they were, and only the server facts were written.
        self.assertEqual(project["project_sync_mode"], "LEGACY")
        self.assertEqual(project["migration_epoch"], 0)
        self.assertEqual(project["server_protocol_version"], 3)
        self.assertEqual(
            project["active_contract_sha256"], CANONICAL_CONTRACT_SHA256
        )
        self.assertEqual(
            json.loads(project["server_capabilities_json"]),
            sorted(SERVER_CAPABILITIES),
        )
        # And the write path is still closed, which is the whole arrangement.
        self.assertFalse(project["contract_path_enabled"])
        self.assertFalse(self.manager._uses_contract_structure())

    def test_the_recorded_inactive_reply_writes_nothing(self):
        self._attach(live_reply(LIVE_INACTIVE_REPLY))
        reading = self.manager.perform_contract_handshake()

        self.assertEqual(reading["outcome"], "unsupported")
        project = self._project()
        self.assertEqual(project["project_sync_mode"], "LEGACY")
        self.assertIsNone(project["server_protocol_version"])
        self.assertIsNone(project["active_contract_sha256"])
        self.assertFalse(self.manager._uses_contract_structure())

    def test_the_recorded_active_reply_opens_the_path_once_the_gate_is_open(self):
        self._attach(live_reply(LIVE_ACTIVE_REPLY))
        project = self.manager.enable_contract_path()

        self.assertTrue(project["contract_path_enabled"])
        self.assertEqual(project["project_sync_mode"], "LEGACY")
        self.assertEqual(project["migration_epoch"], 0)
        # LEGACY at epoch 0 is a contract-capable state through the epoch-zero
        # adapter, so the path is genuinely live without any promotion.
        self.assertTrue(self.manager._uses_contract_structure())

    def test_a_server_that_has_dropped_this_protocol_is_refused(self):
        """server_protocol_version is a ceiling; the set is the real answer."""
        self._attach(supported_handshake(
            server_protocol_version=4, supported_protocol_versions=[4]
        ))
        with self.assertRaises(SyncContractError) as raised:
            self.manager.perform_contract_handshake()

        self.assertEqual(raised.exception.code, "PROTOCOL_TOO_OLD")
        self.assertFalse(self.manager.contract_handshake_is_fresh())
        self.assertEqual(self._project()["project_sync_mode"], "LEGACY")
        self.assertIsNone(self._project()["server_protocol_version"])

    def test_a_server_that_still_accepts_this_protocol_is_taken(self):
        self._attach(supported_handshake(
            server_protocol_version=4, supported_protocol_versions=[3, 4]
        ))
        reading = self.manager.perform_contract_handshake()

        self.assertEqual(reading["outcome"], "supported")
        self.assertEqual(self._project()["server_protocol_version"], 4)

    def test_a_scalar_outside_the_servers_own_set_is_refused(self):
        self._attach(supported_handshake(
            server_protocol_version=9, supported_protocol_versions=[3]
        ))
        with self.assertRaises(SyncContractError) as raised:
            self.manager.perform_contract_handshake()
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")

    def test_a_reply_that_disagrees_with_itself_about_the_contract_is_refused(self):
        cases = (
            {"canonical_contract_sha256": "0" * 64},
            {"contract_version": "0.3.0"},
            {"contract_version": "0.1.0"},
        )
        for overrides in cases:
            with self.subTest(overrides=sorted(overrides)):
                self.manager._forget_contract_handshake()
                self._attach(supported_handshake(**overrides))
                with self.assertRaises(SyncContractError) as raised:
                    self.manager.perform_contract_handshake()
                self.assertEqual(
                    raised.exception.code, "CONTRACT_DIGEST_MISMATCH"
                )
                self.assertFalse(self.manager._uses_contract_structure())
                self.assertEqual(
                    self._project()["project_sync_mode"], "LEGACY"
                )

    def test_an_absent_protocol_set_falls_back_to_the_scalar_alone(self):
        """The field is not in the contract's required five; missing is allowed."""
        reply = supported_handshake()
        reply.pop("supported_protocol_versions")
        self._attach(reply)
        self.assertEqual(
            self.manager.perform_contract_handshake()["outcome"], "supported"
        )

    def _server_moved_the_project_to_migrating(self):
        """Exactly what a handshake records once the server promotes a project.

        The gate is never touched here. Recording server state is all a
        handshake is allowed to do.
        """
        return self.store.activate_contract_project(
            self.context["local_key"],
            project_sync_mode="MIGRATING",
            migration_epoch=1,
            server_protocol_version=3,
            server_contract_sha256=CANONICAL_CONTRACT_SHA256,
            server_capabilities=SERVER_CAPABILITIES,
        )

    def test_a_promoted_project_still_writes_legacy_while_the_gate_is_closed(self):
        """The write shape follows the gate, not the mode the server reported.

        project_sync_mode is the server's to move. If a document write read it
        alone, the server could put this client on contract-native commits by
        promoting the project, and the gate would never be consulted.
        """
        self._server_moved_the_project_to_migrating()
        self.assertFalse(self.store.contract_path_enabled(self.context["local_key"]))

        operation = self.store.enqueue(
            self.manager._v2_context, "가.txt", "본문", relative_path="가.txt"
        )
        self.assertEqual(operation["provenance_kind"], "LEGACY_EPOCH_0")
        self.assertEqual(operation["sync_protocol_version"], 2)
        self.assertIsNone(operation["batch_id"])
        self.assertIsNone(operation["contract_version"])
        self.assertIsNone(operation["canonical_contract_sha256"])

    def test_a_closed_gate_limits_the_write_and_never_edits_the_observation(self):
        """Holding a write back is not the same as rewriting what was observed.

        The mode and epoch on the project row are the server's answer. They stay
        exactly as the handshake recorded them, and remain readable for
        diagnosis, while the gate governs only the shape of the write.
        """
        self._server_moved_the_project_to_migrating()
        before = self._project()

        for name in ("가.txt", "나.txt", "다.txt"):
            self.store.enqueue(
                self.manager._v2_context, name, "본문", relative_path=name
            )

        after = self._project()
        for column in (
            "project_sync_mode", "migration_epoch", "server_protocol_version",
            "active_contract_sha256", "server_capabilities_json",
        ):
            with self.subTest(column=column):
                self.assertEqual(after[column], before[column])
        # The observation is intact and still says MIGRATING ...
        self.assertEqual(after["project_sync_mode"], "MIGRATING")
        self.assertEqual(after["migration_epoch"], 1)
        # ... while the gate it does not control is still shut.
        self.assertFalse(after["contract_path_enabled"])

    def test_a_promoted_project_writes_contract_batches_once_the_gate_opens(self):
        self._server_moved_the_project_to_migrating()
        self.store.set_contract_path_enabled(self.context["local_key"], True)

        operation = self.store.enqueue(
            self.manager._v2_context, "나.txt", "본문", relative_path="나.txt"
        )
        self.assertEqual(operation["provenance_kind"], "CONTRACT_BATCH")
        self.assertEqual(operation["sync_protocol_version"], 3)
        self.assertTrue(operation["batch_id"])
        self.assertEqual(operation["contract_version"], CONTRACT_VERSION)
        self.assertEqual(
            operation["canonical_contract_sha256"], CANONICAL_CONTRACT_SHA256
        )

    def test_a_structure_batch_is_refused_while_the_gate_is_closed(self):
        """The store checks the gate too, so a forgetful caller cannot skip it."""
        self._server_moved_the_project_to_migrating()
        intents = [{
            "entity_kind": "folder",
            "entity_id": str(uuid.uuid4()),
            "intent_kind": "create",
            "base_revision": 0,
            "payload": {"name": "메모장"},
        }]
        with self.assertRaises(SyncContractError) as raised:
            self.store.create_structure_batch(
                self.manager._v2_context, DEVICE_ID, intents
            )
        self.assertEqual(raised.exception.code, "CONTRACT_NOT_ALLOWED")

        self.store.set_contract_path_enabled(self.context["local_key"], True)
        request = self.store.create_structure_batch(
            self.manager._v2_context, DEVICE_ID, intents
        )
        self.assertEqual(request["project_sync_mode"], "MIGRATING")

    def test_the_gate_defaults_to_closed_on_a_fresh_project(self):
        self.assertFalse(self._project()["contract_path_enabled"])
        self.assertIsNone(self._project()["contract_path_enabled_at"])
        self.assertFalse(self.manager.contract_path_enabled())
        self.assertFalse(self.manager._uses_contract_structure())


def _preflight_module():
    """Load the preflight script, which lives outside any package."""
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "contract_path_preflight.py"
    spec = importlib.util.spec_from_file_location("contract_path_preflight", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ContractPreflightVerdictTests(unittest.TestCase):
    """The preflight decides STOP or proceed, so its verdicts are load-bearing."""

    @classmethod
    def setUpClass(cls):
        cls.preflight = _preflight_module()

    def _verdict(self, reply, project_id=LIVE_PROJECT_ID):
        stream = io.StringIO()
        with redirect_stdout(stream):
            self.preflight.print_handshake(project_id, reply)
        lines = [
            line.split("=", 1)[1]
            for line in stream.getvalue().splitlines()
            if line.startswith("verdict=")
        ]
        self.assertEqual(len(lines), 1, stream.getvalue())
        return lines[0]

    def test_the_recorded_active_reply_clears_the_preflight(self):
        verdict = self._verdict(json.loads(LIVE_ACTIVE_REPLY))
        self.assertNotIn("STOP", verdict)
        self.assertIn("LEGACY/0", verdict)

    def test_the_recorded_inactive_reply_offers_nothing_to_open(self):
        verdict = self._verdict(json.loads(LIVE_INACTIVE_REPLY))
        self.assertNotIn("STOP", verdict)
        self.assertIn("does not support", verdict)

    def test_a_promoted_project_stops_the_preflight(self):
        """A promotion is a separate approval, so seeing one has to halt this."""
        for mode, epoch in (("MIGRATING", 1), ("ID_BASED", 1), ("MIGRATING", 2)):
            with self.subTest(mode=mode, epoch=epoch):
                reply = json.loads(LIVE_ACTIVE_REPLY)
                reply["project_sync_mode"] = mode
                reply["migration_epoch"] = epoch
                verdict = self._verdict(reply)
                self.assertIn("STOP", verdict)
                self.assertIn(f"{mode}/{epoch}", verdict)

    def test_literals_that_do_not_match_the_pin_stop_the_preflight(self):
        cases = (
            ({"server_protocol_version": 4,
              "supported_protocol_versions": [4]}, "PROTOCOL_TOO_OLD"),
            ({"canonical_contract_sha256": "0" * 64},
             "CONTRACT_DIGEST_MISMATCH"),
            ({"contract_version": "0.3.0"}, "CONTRACT_DIGEST_MISMATCH"),
            ({"server_contract_sha256": "0" * 64,
              "canonical_contract_sha256": "0" * 64,
              "contract_version": None}, "CONTRACT_DIGEST_MISMATCH"),
            ({"server_capabilities": list(SERVER_CAPABILITIES[:-1])},
             "CAPABILITY_MISMATCH"),
        )
        for overrides, expected in cases:
            with self.subTest(overrides=sorted(overrides)):
                reply = json.loads(LIVE_ACTIVE_REPLY)
                reply.update(overrides)
                verdict = self._verdict(reply)
                self.assertIn("STOP", verdict)
                self.assertIn(expected, verdict)

    def test_a_reply_about_another_project_stops_the_preflight(self):
        reply = json.loads(LIVE_ACTIVE_REPLY)
        reply["project_id"] = OTHER_PROJECT_ID
        self.assertIn("STOP", self._verdict(reply))

    def test_an_unreadable_reply_stops_the_preflight(self):
        for reply in (None, [], "supported", 3):
            with self.subTest(reply=repr(reply)):
                self.assertIn("STOP", self._verdict(reply))

    def test_an_empty_project_row_is_not_reported_as_a_bad_server_answer(self):
        """Nothing recorded yet is a different thing from a refused handshake."""
        self.assertEqual(
            self.preflight.stored_compatibility({
                "project_sync_mode": "LEGACY",
                "migration_epoch": 0,
                "server_protocol_version": None,
                "active_contract_sha256": None,
                "server_capabilities_json": None,
            }),
            "NOT RECORDED",
        )
        self.assertEqual(
            self.preflight.stored_compatibility({
                "project_sync_mode": "LEGACY",
                "migration_epoch": 0,
                "server_protocol_version": 3,
                "active_contract_sha256": CANONICAL_CONTRACT_SHA256,
                "server_capabilities_json": json.dumps(list(SERVER_CAPABILITIES)),
            }),
            "PASS",
        )

    def test_the_preflight_prints_nothing_a_cp949_console_cannot_render(self):
        """It is run from a Windows console, where a stray dash aborts the run."""
        path = (
            Path(__file__).resolve().parents[1]
            / "scripts" / "contract_path_preflight.py"
        )
        source = path.read_text(encoding="utf-8")
        offenders = sorted({character for character in source if ord(character) > 127})
        self.assertEqual(offenders, [])


class _AuthenticatedClient(_HandshakeClient):
    """A stand-in that create_supabase_client can hand back, ready to call."""

    def rpc(self, name, params):
        self.calls.append((name, dict(params)))
        reply = self.reply
        if isinstance(reply, dict):
            # The real server answers about whichever project was asked for.
            reply = dict(reply, project_id=params["p_project_id"])
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=reply))


class CredentialLockCheckTests(unittest.TestCase):
    """The mode that exists so the lock can be tested without a run that might
    reach the server if the test fails."""

    @classmethod
    def setUpClass(cls):
        cls.preflight = _preflight_module()

    def _run(self, lease_result):
        released = []
        stream = io.StringIO()
        with patch.object(
            SyncManager, "acquire_auth_lease", staticmethod(lambda: lease_result)
        ), patch.object(
            SyncManager, "release_auth_lease",
            staticmethod(lambda: released.append(True)),
        ), patch.object(
            self.preflight, "run_handshakes"
        ) as handshakes, patch.object(
            self.preflight, "run_application_handshake"
        ) as application, patch.object(
            self.preflight, "read_projects"
        ) as read_projects, patch.object(
            sys, "argv", ["contract_path_preflight.py", "--credential-lock-check"]
        ):
            with redirect_stdout(stream):
                status = self.preflight.main()
        return status, stream.getvalue(), handshakes, application, read_projects, released

    def test_a_free_lock_is_reported_and_handed_straight_back(self):
        status, output, handshakes, application, _read, released = self._run(True)

        self.assertEqual(status, 0)
        self.assertIn("acquired=true", output)
        self.assertIn("the credential is free", output)
        # Reporting is all it does. Holding on would make the check itself the
        # thing that blocks the next run.
        self.assertEqual(released, [True])
        handshakes.assert_not_called()
        application.assert_not_called()

    def test_a_held_lock_stops_with_a_non_zero_exit(self):
        status, output, handshakes, application, _read, _released = self._run(False)

        self.assertEqual(status, 1)
        self.assertIn("acquired=false", output)
        self.assertIn("STOP", output)
        handshakes.assert_not_called()
        application.assert_not_called()

    def test_a_lock_that_cannot_be_taken_at_all_stops_too(self):
        status, output, _h, _a, _read, _released = self._run(None)

        self.assertEqual(status, 1)
        self.assertIn("unavailable", output)
        self.assertIn("STOP", output)

    def test_it_reaches_no_network_and_not_even_the_database(self):
        """Whatever the answer, nothing that could send a request is entered."""
        for lease_result in (True, False, None):
            with self.subTest(lease=repr(lease_result)):
                _status, output, handshakes, application, read_projects, _r = (
                    self._run(lease_result)
                )
                handshakes.assert_not_called()
                application.assert_not_called()
                read_projects.assert_not_called()
                self.assertIn("network_used=false", output)


class ContractApplicationHandshakeTests(unittest.TestCase):
    """The end-to-end check: the real client path, driven against a copy.

    The RPC preflight proves what the server answers. These prove the wiring:
    that perform_contract_handshake records what it should, and that the gate
    holds afterwards against a write.
    """

    @classmethod
    def setUpClass(cls):
        cls.preflight = _preflight_module()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.live = Path(self.temp.name) / "sync_v2.sqlite3"
        store = SyncV2Store(str(self.live))
        store.configure_project(
            str(Path(self.temp.name) / "writing"), "Preflight", PROJECT_ID
        )

    def tearDown(self):
        manager = SyncManager()
        manager.release_v2()
        manager.supabase = None
        manager._v2_store = None
        manager._v2_device_id = None
        self.temp.cleanup()

    def _run(self, reply):
        client = _AuthenticatedClient(reply)
        digest_before = self.preflight.file_sha256(self.live)
        stream = io.StringIO()
        with patch.object(
            SyncManager, "create_supabase_client", staticmethod(lambda config=None: client)
        ):
            with redirect_stdout(stream):
                status = self.preflight.run_application_handshake(self.live, "")
        output = stream.getvalue()
        # Whatever else happened, the live database is not what was worked on.
        self.assertEqual(self.preflight.file_sha256(self.live), digest_before)
        return status, output, client

    @staticmethod
    def _checks(output):
        return dict(
            line.split("=", 1)
            for line in output.splitlines()
            if "=PASS" in line or "=FAIL" in line.split("=", 1)[-1][:4]
        )

    @staticmethod
    def _value(output, key):
        for line in output.splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1]
        return None

    def test_the_recorded_active_reply_runs_the_wiring_and_the_gate_holds(self):
        status, output, client = self._run(json.loads(LIVE_ACTIVE_REPLY))

        self.assertEqual(status, 0, output)
        self.assertEqual(client.calls[0][0], "get_sync_handshake")
        self.assertEqual(
            client.calls[0][1]["p_contract_sha256"], CANONICAL_CONTRACT_SHA256
        )
        # The real client call ran and recorded a supported reading ...
        self.assertEqual(self._value(output, "outcome"), "supported")
        self.assertEqual(self._value(output, "handshake_is_fresh"), "true")
        self.assertEqual(
            self._value(output, "observed_project_sync_mode"), "LEGACY"
        )
        self.assertEqual(self._value(output, "observed_migration_epoch"), "0")
        # ... and the write path is still shut behind it.
        self.assertEqual(self._value(output, "uses_contract_structure"), "false")
        self.assertNotIn("=FAIL", output)
        self.assertIn("wiring held", self._value(output, "verdict"))

    def test_the_observation_is_recorded_but_the_gate_is_not_touched(self):
        _status, output, _client = self._run(json.loads(LIVE_ACTIVE_REPLY))

        # The server facts land on the row ...
        self.assertIn("project.1.server_protocol_version", output)
        self.assertIn("project.1.active_contract_sha256", output)
        self.assertIn("project.1.server_capabilities_json", output)
        # ... and the two gate columns are not among what changed.
        self.assertNotIn("project.1.contract_path_enabled=", output)
        self.assertNotIn("project.1.contract_path_enabled_at=", output)

    def test_the_handshake_queues_no_work_of_any_kind(self):
        _status, output, _client = self._run(json.loads(LIVE_ACTIVE_REPLY))
        for counter in (
            "operations_total", "operations_protocol_3",
            "operations_with_batch_id", "operations_contract_batch",
            "contract_batches",
        ):
            with self.subTest(counter=counter):
                self.assertIn(f"handshake_queued_nothing.{counter}=PASS", output)

    def test_a_write_through_the_closed_gate_still_comes_out_legacy(self):
        """The probe is the part that proves the stored state opens nothing."""
        _status, output, _client = self._run(json.loads(LIVE_ACTIVE_REPLY))

        self.assertEqual(self._value(output, "probe.1.provenance_kind"), "LEGACY_EPOCH_0")
        self.assertEqual(self._value(output, "probe.1.sync_protocol_version"), "2")
        self.assertEqual(self._value(output, "probe.1.batch_id"), "(none)")
        self.assertEqual(self._value(output, "probe.1.contract_version"), "(none)")
        self.assertIn("probe_write_is_legacy[1]=PASS", output)
        self.assertIn("probe_created_no_contract_batch=PASS", output)
        self.assertIn("probe_created_no_protocol_3_operation=PASS", output)

    def test_a_promoted_project_stops_the_run_but_the_gate_still_holds(self):
        """Two things at once: the promotion halts it, and nothing leaked."""
        reply = json.loads(LIVE_ACTIVE_REPLY)
        reply["project_sync_mode"] = "MIGRATING"
        reply["migration_epoch"] = 1
        status, output, _client = self._run(reply)

        self.assertEqual(status, 1)
        self.assertIn("observed_mode_is_legacy_epoch_0[1]=FAIL", output)
        self.assertIn("STOP", self._value(output, "verdict"))
        # The observation was recorded honestly, exactly as the server sent it.
        self.assertIn("project.1.project_sync_mode='LEGACY' -> 'MIGRATING'", output)
        # And the gate held anyway, which is the fix this run exists to prove.
        self.assertEqual(self._value(output, "uses_contract_structure"), "false")
        self.assertIn("gate_still_closed[1]=PASS", output)
        self.assertIn("probe_write_is_legacy[1]=PASS", output)
        self.assertEqual(self._value(output, "probe.1.provenance_kind"), "LEGACY_EPOCH_0")

    def test_the_recorded_inactive_reply_records_nothing_and_passes(self):
        status, output, _client = self._run(json.loads(LIVE_INACTIVE_REPLY))

        self.assertEqual(status, 0, output)
        self.assertEqual(self._value(output, "outcome"), "unsupported")
        self.assertEqual(self._value(output, "handshake_is_fresh"), "false")
        self.assertEqual(self._value(output, "project.1.changed"), "(nothing)")
        self.assertIn("nothing to activate", self._value(output, "verdict"))

    def test_a_server_that_dropped_this_protocol_stops_the_run(self):
        reply = json.loads(LIVE_ACTIVE_REPLY)
        reply["server_protocol_version"] = 4
        reply["supported_protocol_versions"] = [4]
        status, output, _client = self._run(reply)

        self.assertEqual(status, 1)
        self.assertIn("handshake_completed[1]=FAIL PROTOCOL_TOO_OLD", output)
        self.assertEqual(self._value(output, "project.1.changed"), "(nothing)")

    def _unauthenticated_run(self, stored_session, restore_error_kind=""):
        """A client that came back signed out, over a stand-in credential store.

        The real store is never read here: whether a session happens to be
        saved on this machine must not decide what these cases assert.
        """
        client = _AuthenticatedClient(json.loads(LIVE_ACTIVE_REPLY))
        client._antigravity_authenticated = False
        if restore_error_kind:
            client._antigravity_restore_error_kind = restore_error_kind
        keyring = SimpleNamespace(
            get_supabase_session=lambda: (
                ("access", "refresh") if stored_session else ("", "")
            ),
        )
        stream = io.StringIO()
        with patch("security_manager.SecurityManager", keyring), patch.object(
            SyncManager, "create_supabase_client",
            staticmethod(lambda config=None: client),
        ):
            with redirect_stdout(stream):
                status = self.preflight.run_application_handshake(self.live, "")
        return status, stream.getvalue(), client

    def test_nobody_signed_in_stops_the_run(self):
        status, output, client = self._unauthenticated_run(stored_session=False)

        self.assertEqual(status, 1)
        self.assertIn("NO STORED SESSION", output)
        self.assertIn("stored_session_present=false", output)
        self.assertEqual(client.calls, [])

    def test_a_kept_session_that_would_not_restore_stops_the_run_and_says_why(self):
        """Signed out because the restore failed is a different thing entirely.

        The session is still saved, so this is a bad minute rather than a
        logout, and the reason has to reach whoever is reading.
        """
        status, output, client = self._unauthenticated_run(
            stored_session=True, restore_error_kind="timeout"
        )

        self.assertEqual(status, 1)
        self.assertIn("RESTORE FAILED", output)
        self.assertIn("stored_session_present=true", output)
        self.assertIn("restore_error_kind=timeout", output)
        self.assertEqual(client.calls, [])

    def test_the_run_prints_nothing_that_looks_like_a_credential(self):
        _status, output, _client = self._run(json.loads(LIVE_ACTIVE_REPLY))
        lowered = output.lower()
        for marker in (
            "access_token", "refresh_token", "apikey", "api_key", "bearer",
            "authorization", "eyj", "@", "http://", "https://",
        ):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, lowered)


if __name__ == "__main__":
    unittest.main()
