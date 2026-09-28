"""PR #50 transition compatibility; disposable SQLite and mocked server rows only."""
import copy
import os
import sqlite3
import tempfile
import unittest
import unicodedata
import uuid
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

from project_manager_writing import WritingProjectManager
from sync_contract import CANONICAL_CONTRACT_SHA256, SERVER_CAPABILITIES, SyncContractError, normalize_storage_name
from sync_manager import SyncManager
from sync_v2_store import SERVER_ROOT_ORDER_PATH, SyncV2Store


class TransitionCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"ANTIGRAVITY_APP_DATA_DIR": self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.wpm = WritingProjectManager()
        self.wpm.workspace_dir = self.temp.name
        self.wpm.current_project = "PR50 fixture"
        self.wpm.writing_root_path = str(Path(self.temp.name, "project", "writing"))
        Path(self.wpm.writing_root_path).mkdir(parents=True)
        self.wpm.project_settings = {"tree_order": {}}
        self.wpm.save_settings = lambda: True
        self.db = str(Path(self.temp.name, "sync.sqlite3"))
        self.store = SyncV2Store(self.db)
        self.context = self.store.configure_project(self.wpm.writing_root_path, "PR50 fixture", str(uuid.uuid4()))
        self.local_key = self.context["local_key"]
        with patch.object(SyncManager, "_instance", None), patch.object(SyncManager, "init_supabase"):
            self.manager = SyncManager()
        self.manager._v2_store = self.store
        self.manager._v2_context = self.context
        self.manager._v2_device_id = str(uuid.uuid4())
        self.manager._v2_wpm = self.wpm
        self.manager._diagnostics = MagicMock()
        self.manager._v2_protected_paths_provider = lambda: set()
        self.manager._v2_active_paths_provider = lambda: []
        self.main = self.folder("메인")
        self.sub = self.folder("메인/메모장", self.main["folder_id"])
        self.store.replace_folder_snapshots(self.local_key, [self.main, self.sub])

    @staticmethod
    def folder(path, parent=None, **overrides):
        return {"folder_id": str(uuid.uuid4()), "parent_folder_id": parent,
                "local_path": path, "name": path.rsplit("/", 1)[-1],
                "revision": 1, "is_deleted": False, **overrides}

    def activate(self):
        for mode in ("MIGRATING", "ID_BASED"):
            self.store.activate_contract_project(
                self.local_key, project_sync_mode=mode, migration_epoch=1,
                server_protocol_version=3, server_contract_sha256=CANONICAL_CONTRACT_SHA256,
                server_capabilities=SERVER_CAPABILITIES,
            )

    def legacy(self, *, deleted=False, root=False):
        path = "메인/문서.txt" if root else "메인/메모장/문서.txt"
        local_path = "메인/휴지통/문서.txt" if deleted else path
        remote = {
            "project_id": self.context["project_id"], "document_id": str(uuid.uuid4()),
            "relative_path": path, "content": "보존할 본문\n", "revision": 7,
            "is_deleted": deleted, "parent_folder_id": self.main["folder_id"] if root else self.sub["folder_id"],
            "name": "문서.txt", "storage_name_key": "\\x" + normalize_storage_name("문서.txt").utf8.hex(),
            "structure_revision": 1,
        }
        self.store.apply_remote_snapshot(self.context, remote["document_id"], path,
                                         remote["content"], 7, is_deleted=deleted, local_path=local_path)
        self.assertTrue(self.wpm.write_text_file(local_path, remote["content"]))
        return remote

    def row(self, remote):
        return self.store.get_document_by_id(remote["document_id"])

    def dump(self):
        with closing(sqlite3.connect(self.db)) as connection:
            return list(connection.iterdump())

    def pull(self, remote, **kwargs):
        return self.manager._apply_v2_remote_documents([remote], strict=True, **kwargs)

    def order(self, parent=None, children=None, revision=4):
        return {"tree_order_id": str(uuid.uuid4()), "parent_folder_id": parent,
                "children": children or [], "revision": revision}

    def test_general_equal_revision_initializes_only_four_columns_and_allows_edit(self):
        remote = self.legacy()
        remote["current_version_id"] = str(uuid.uuid4())
        snapshot_before = copy.deepcopy(remote)
        history_before = [line for line in self.dump() if not line.startswith('INSERT INTO "sync_documents"')]
        before = self.row(remote)
        self.assertTrue(all(before[key] is None for key in ("name", "parent_folder_id", "storage_name_key", "structure_revision")))
        self.pull(remote)
        after = self.row(remote)
        for key in before:
            if key not in {"name", "parent_folder_id", "storage_name_key", "structure_revision"}:
                self.assertEqual(after[key], before[key], key)
        self.assertEqual(after["structure_revision"], 1)
        self.assertEqual(after["parent_folder_id"], self.sub["folder_id"])
        self.assertEqual(after["name"], "문서.txt")
        self.assertEqual(after["storage_name_key"], normalize_storage_name("문서.txt").normalized)
        self.assertEqual(remote, snapshot_before)
        self.assertEqual([line for line in self.dump() if not line.startswith('INSERT INTO "sync_documents"')], history_before)
        self.activate()
        operation = self.store.enqueue(self.context, remote["relative_path"], "편집한 내용")
        self.assertEqual(operation["intent_kind"], "update")
        self.assertEqual(operation["base_revision"], 7)
        self.assertEqual(operation["provenance_kind"], "CONTRACT_BATCH")

    def test_active_open_equal_revision_receives_metadata_and_refresh(self):
        remote = self.legacy()
        self.manager._v2_active_paths_provider = lambda: [remote["relative_path"]]
        changes = self.pull(remote)
        self.assertEqual(self.row(remote)["structure_revision"], 1)
        self.assertEqual(changes[0]["kind"], "remote_refresh")

    def test_delete_request_after_initialization(self):
        remote = self.legacy()
        self.pull(remote)
        self.activate()
        operation = self.store.enqueue(self.context, remote["relative_path"], remote["content"], is_deleted=True)
        self.assertEqual(operation["intent_kind"], "delete")
        self.assertEqual(operation["provenance_kind"], "CONTRACT_BATCH")

    def test_tombstone_initialization_preserves_history_and_allows_restore(self):
        remote = self.legacy(deleted=True)
        before = self.row(remote)
        self.pull(remote)
        self.assertEqual(self.row(remote)["local_path"], before["local_path"])
        self.assertEqual(self.row(remote)["is_deleted"], 1)
        self.assertEqual(self.row(remote)["revision"], 7)
        self.activate()
        operation = self.store.enqueue(self.context, before["local_path"], remote["content"], relative_path=remote["relative_path"])
        self.assertEqual(operation["intent_kind"], "restore")
        self.assertEqual(operation["provenance_kind"], "CONTRACT_BATCH")

    def test_pending_local_operation_preserves_every_row(self):
        remote = self.legacy()
        self.store.enqueue(self.context, remote["relative_path"], "미전송 본문")
        before = self.dump()
        self.pull(remote)
        self.assertEqual(self.dump(), before)

    def test_protected_active_editor_preserves_every_row_and_disk(self):
        remote = self.legacy()
        self.wpm.write_text_file(remote["relative_path"], "아직 저장 중")
        self.manager._v2_active_paths_provider = lambda: [remote["relative_path"]]
        self.manager._v2_protected_paths_provider = lambda: {remote["relative_path"]}
        before = self.dump()
        self.assertEqual(self.pull(remote), [])
        self.assertEqual(self.dump(), before)
        self.assertEqual(self.wpm.read_text_file(remote["relative_path"]), "아직 저장 중")

    def test_unqueued_local_content_is_not_overwritten_by_active_refresh(self):
        remote = self.legacy()
        self.manager._v2_active_paths_provider = lambda: [remote["relative_path"]]
        self.wpm.write_text_file(remote["relative_path"], "로컬 작업")
        before = self.dump()
        self.assertEqual(self.pull(remote), [])
        self.assertEqual(self.dump(), before)
        self.assertEqual(self.wpm.read_text_file(remote["relative_path"]), "로컬 작업")

    def test_protection_provider_failure_defers_initialization(self):
        remote = self.legacy()
        self.manager._v2_protected_paths_provider = MagicMock(side_effect=RuntimeError("editor unavailable"))
        before = self.dump()
        self.assertEqual(self.pull(remote), [])
        self.assertEqual(self.dump(), before)

    def test_inflight_and_blocked_operations_defer_initialization(self):
        remote = self.legacy()
        operation = self.store.enqueue(self.context, remote["relative_path"], "미전송 본문")
        self.store.mark_attempt(operation["operation_id"])
        before = self.dump()
        self.pull(remote)
        self.assertEqual(self.dump(), before)
        self.store.mark_blocked(operation["operation_id"], "PATH_CONFLICT")
        before = self.dump()
        self.pull(remote)
        self.assertEqual(self.dump(), before)

    def test_pending_folder_rename_defers_initialization(self):
        remote = self.legacy()
        self.store.record_folder_rename_intent(self.local_key, "메인/메모장", "메인/다른이름")
        before = self.dump()
        self.pull(remote)
        self.assertEqual(self.dump(), before)

    def test_pending_contract_folder_operation_defers_initialization(self):
        remote = self.legacy()
        self.activate()
        self.manager.queue_contract_structure_intents([{
            "entity_kind": "folder", "entity_id": self.sub["folder_id"],
            "intent_kind": "rename", "base_revision": 1, "payload": {"name": "변경"},
        }], retry=False)
        before = self.dump()
        self.pull(remote)
        self.assertEqual(self.dump(), before)

    def test_local_conflict_state_is_preserved(self):
        remote = self.legacy()
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute("UPDATE sync_documents SET sync_state = 'conflict', conflict_local = ? WHERE document_id = ?",
                               ("보존할 충돌본", remote["document_id"]))
            connection.commit()
        before = self.dump()
        self.pull(remote)
        self.assertEqual(self.dump(), before)

    def test_incomplete_snapshot_and_partial_local_structure_are_rejected(self):
        remote = self.legacy()
        before = self.dump()
        incomplete = dict(remote)
        del incomplete["parent_folder_id"]
        with self.assertRaises(SyncContractError):
            self.store.apply_equal_revision_structure(self.context, incomplete)
        self.assertEqual(self.dump(), before)
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute("UPDATE sync_documents SET name = ? WHERE document_id = ?", (remote["name"], remote["document_id"]))
            connection.commit()
        with self.assertRaises(SyncContractError):
            self.pull(remote)

    def test_persisted_legacy_row_initializes_after_store_reopen(self):
        remote = self.legacy()
        self.store = SyncV2Store(self.db)
        self.manager._v2_store = self.store
        self.pull(remote)
        self.assertEqual(self.row(remote)["structure_revision"], 1)

    def test_non_strict_mismatch_marks_pull_blocked(self):
        remote = self.legacy()
        self.manager._apply_v2_remote_documents([{**remote, "project_id": str(uuid.uuid4())}])
        self.assertTrue(self.manager._v2_last_pull_apply_blocked)
        self.assertIsNone(self.row(remote)["structure_revision"])

    def test_mismatched_proofs_are_rejected_without_any_database_changes(self):
        remote = self.legacy()
        mutations = [
            {"project_id": str(uuid.uuid4())}, {"project_id": None},
            {"document_id": str(uuid.uuid4())}, {"content": "다른 본문"},
            {"revision": 8}, {"is_deleted": True}, {"relative_path": "메인/다른.txt"},
            {"_server_relative_path": "메인/다른.txt"}, {"parent_folder_id": self.main["folder_id"]},
            {"parent_folder_id": None}, {"name": "다른.txt"}, {"storage_name_key": None},
            {"storage_name_key": "\\x00"}, {"structure_revision": 0},
        ]
        before = self.dump()
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises((SyncContractError, ValueError)):
                self.store.apply_equal_revision_structure(self.context, {**remote, **mutation})
            self.assertEqual(self.dump(), before)

    def test_foreign_local_document_and_parent_are_rejected(self):
        remote = self.legacy()
        other = self.store.configure_project(str(Path(self.temp.name, "other")), "Other", str(uuid.uuid4()))
        before = self.dump()
        with self.assertRaises(SyncContractError):
            self.store.apply_equal_revision_structure(other, {**remote, "project_id": other["project_id"]})
        self.assertEqual(self.dump(), before)
        foreign = self.folder("메인/메모장")
        self.store.replace_folder_snapshots(other["local_key"], [foreign])
        with self.assertRaises(SyncContractError):
            self.store.apply_equal_revision_structure(self.context, {**remote, "parent_folder_id": foreign["folder_id"]})

    def test_lower_structure_revision_rejected_and_replay_idempotent(self):
        remote = self.legacy()
        self.pull(remote)
        before = self.dump()
        self.pull(remote)
        self.assertEqual(self.dump(), before)
        self.pull({**remote, "structure_revision": 3})
        newer = self.dump()
        with self.assertRaises(SyncContractError):
            self.pull(remote)
        self.assertEqual(self.dump(), newer)
        self.assertEqual(self.row(remote)["structure_revision"], 3)

    def test_changed_parent_identity_cannot_be_overwritten_at_equal_body_revision(self):
        remote = self.legacy()
        self.pull(remote)
        replacement = self.folder(self.sub["local_path"], self.main["folder_id"])
        self.store.replace_folder_snapshots(self.local_key, [self.main, replacement])
        before = self.dump()
        with self.assertRaises(SyncContractError):
            self.pull({**remote, "parent_folder_id": replacement["folder_id"], "structure_revision": 2})
        self.assertEqual(self.dump(), before)

    def test_main_inbound_ui_and_outbound_reuse_transition_order(self):
        remote = self.legacy(root=True)
        snapshot = self.order(self.main["folder_id"], [remote["document_id"], self.sub["folder_id"]])
        self.wpm.project_settings["tree_order"] = {"메인": ["오래된 값"]}
        self.pull(remote, tree_order_rows=[snapshot])
        self.assertEqual(self.wpm.project_settings["tree_order"], {"<root>": ["문서.txt", "메모장"]})
        durable = self.store.get_tree_order(self.local_key, "<root>")
        self.assertEqual(durable["tree_order_id"], snapshot["tree_order_id"])
        self.assertIsNone(self.store.get_tree_order(self.local_key, "메인"))
        self.activate()
        request = self.manager.record_tree_order({"<root>": ["메모장", "문서.txt"]}, retry=False)
        intent = request["ordered_intents"][0]
        self.assertEqual(intent["entity_id"], snapshot["tree_order_id"])
        self.assertEqual(intent["base_revision"], 4)
        self.assertEqual(intent["payload"]["parent_folder_id"], self.main["folder_id"])
        self.assertEqual(intent["payload"]["children"], [self.sub["folder_id"], remote["document_id"]])

    def test_old_durable_main_path_alias_reuses_id(self):
        snapshot = self.order(self.main["folder_id"], [self.sub["folder_id"]])
        self.store.replace_tree_order_snapshots(self.local_key, [snapshot])
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute("UPDATE sync_tree_orders SET parent_path = '메인'")
            connection.commit()
        intent = self.manager._contract_tree_order_intents({"<root>": ["메모장"]})[0]
        self.assertEqual(intent["entity_id"], snapshot["tree_order_id"])
        self.assertEqual(intent["base_revision"], 4)

    def test_committed_reorder_receipt_keeps_canonical_root_and_reuses_new_revision(self):
        snapshot = self.order(self.main["folder_id"], [self.sub["folder_id"]])
        self.store.replace_tree_order_snapshots(self.local_key, [snapshot])
        self.activate()
        request = self.manager.record_tree_order({"<root>": ["메모장"]}, retry=False)
        intent = request["ordered_intents"][0]
        batch = request["batch"]
        response = {
            "kind": "atomic_structure_commit_success", "batch_id": batch["batch_id"],
            "batch_payload_sha256": batch["batch_payload_sha256"],
            "status": "committed", "applied": True,
            "results": [{"sequence": intent["sequence"], "operation_id": intent["operation_id"],
                         "entity_id": intent["entity_id"], "result_revision": 5}],
        }
        self.store.mark_structure_batch_attempt(batch["batch_id"])
        self.store.record_structure_batch_response(batch["batch_id"], response)
        before = self.dump()
        self.store.record_structure_batch_response(batch["batch_id"], response)
        self.assertEqual(self.dump(), before)
        durable = self.store.get_tree_order(self.local_key, "<root>")
        self.assertEqual(durable["revision"], 5)
        self.assertEqual(durable["tree_order_id"], snapshot["tree_order_id"])
        self.assertIsNone(self.store.get_tree_order(self.local_key, "메인"))
        next_intent = self.manager._contract_tree_order_intents({"<root>": ["메모장"]})[0]
        self.assertEqual(next_intent["base_revision"], 5)
        self.assertEqual(next_intent["entity_id"], snapshot["tree_order_id"])

    def test_physical_null_parent_order_stays_separate(self):
        physical = self.order(None, [self.main["folder_id"]])
        binder = self.order(self.main["folder_id"], [self.sub["folder_id"]])
        self.store.replace_tree_order_snapshots(self.local_key, [physical, binder])
        self.assertEqual(self.manager._contract_tree_order_from_snapshots([physical, binder]), {"<root>": ["메모장"]})
        self.assertEqual(self.store.get_tree_order(self.local_key, SERVER_ROOT_ORDER_PATH)["tree_order_id"], physical["tree_order_id"])
        intent = self.manager._contract_tree_order_intents({"<root>": ["메모장"]})[0]
        self.assertEqual(intent["entity_id"], binder["tree_order_id"])
        self.assertEqual(intent["payload"]["parent_folder_id"], self.main["folder_id"])

    def test_missing_main_fails_closed(self):
        self.store.replace_folder_snapshots(self.local_key, [self.sub])
        with self.assertRaises(SyncContractError):
            self.manager._contract_tree_order_intents({"<root>": []})
        with self.assertRaises(SyncContractError):
            self.store.replace_tree_order_snapshots(self.local_key, [self.order(self.main["folder_id"])])

    def test_ambiguous_main_fails_closed(self):
        # Simulate a damaged older projection containing canonically equivalent
        # top-level names at different paths. Do not pick the exact-path row.
        alias = self.folder("다른경로")
        self.store.replace_folder_snapshots(self.local_key, [self.main, self.sub, alias])
        with closing(sqlite3.connect(self.db)) as connection:
            connection.execute("UPDATE sync_folders SET name = ?, storage_name_key = NULL WHERE folder_id = ?",
                               (unicodedata.normalize("NFD", "메인"), alias["folder_id"]))
            connection.commit()
        with self.assertRaises(SyncContractError):
            self.manager._contract_tree_order_intents({"<root>": []})
        with self.assertRaises(SyncContractError):
            self.store.replace_tree_order_snapshots(self.local_key, [self.order(self.main["folder_id"])])

    def test_named_nested_main_is_not_binder_main(self):
        nested = self.folder("메인/메모장/메인", self.sub["folder_id"])
        self.store.replace_folder_snapshots(self.local_key, [self.main, self.sub, nested])
        self.assertEqual(self.store.binder_main_folder(self.local_key)["folder_id"], self.main["folder_id"])

    def test_unproven_or_wrong_parent_main_is_rejected(self):
        for assignment in ("revision = 0", "parent_folder_id = '00000000-0000-4000-8000-000000000001'"):
            with self.subTest(assignment=assignment):
                with closing(sqlite3.connect(self.db)) as connection:
                    connection.execute("UPDATE sync_folders SET " + assignment + " WHERE folder_id = ?", (self.main["folder_id"],))
                    connection.commit()
                with self.assertRaises(SyncContractError):
                    self.manager._contract_tree_order_intents({"<root>": []})

    def test_outbound_deleted_or_cross_parent_document_is_rejected(self):
        remote = self.legacy(root=True)
        self.pull(remote)
        for assignment in ("is_deleted = 1", "is_deleted = 0, parent_folder_id = NULL"):
            with closing(sqlite3.connect(self.db)) as connection:
                connection.execute("UPDATE sync_documents SET " + assignment + " WHERE document_id = ?", (remote["document_id"],))
                connection.commit()
            with self.subTest(assignment=assignment), self.assertRaises(SyncContractError):
                self.manager._contract_tree_order_intents({"<root>": ["문서.txt"]})

    def test_new_binder_order_never_uses_null_parent(self):
        physical = self.order(None, [self.main["folder_id"]])
        self.store.replace_tree_order_snapshots(self.local_key, [physical])
        intent = self.manager._contract_tree_order_intents({"<root>": ["메모장"]})[0]
        self.assertNotEqual(intent["entity_id"], physical["tree_order_id"])
        self.assertEqual(intent["base_revision"], 0)
        self.assertEqual(intent["payload"]["parent_folder_id"], self.main["folder_id"])

    def test_subfolder_order_still_round_trips(self):
        remote = self.legacy()
        self.pull(remote)
        snapshot = self.order(self.sub["folder_id"], [remote["document_id"]])
        self.store.replace_tree_order_snapshots(self.local_key, [snapshot])
        self.assertEqual(self.manager._contract_tree_order_from_snapshots([snapshot]), {"메인/메모장": ["문서.txt"]})
        intent = self.manager._contract_tree_order_intents({"메인/메모장": ["문서.txt"]})[0]
        self.assertEqual(intent["entity_id"], snapshot["tree_order_id"])

    def test_invalid_children_parent_project_and_identity_preserve_order(self):
        remote = self.legacy(root=True)
        self.pull(remote)
        snapshot = self.order(self.main["folder_id"], [self.sub["folder_id"]])
        self.store.replace_tree_order_snapshots(self.local_key, [snapshot])
        deleted = self.folder("메인/削除", self.main["folder_id"], is_deleted=True)
        self.store.replace_folder_snapshots(self.local_key, [self.main, self.sub, deleted])
        other = self.store.configure_project(str(Path(self.temp.name, "other")), "Other", str(uuid.uuid4()))
        foreign = self.folder("메인/외부")
        self.store.replace_folder_snapshots(other["local_key"], [foreign])
        bad_rows = [
            {**snapshot, "revision": 5, "children": [str(uuid.uuid4())]},
            {**snapshot, "revision": 5, "children": [deleted["folder_id"]]},
            {**snapshot, "revision": 5, "children": [foreign["folder_id"]]},
            {**snapshot, "revision": 5, "children": [self.main["folder_id"]]},
            {**snapshot, "revision": 5, "children": [self.sub["folder_id"]] * 2},
            {**snapshot, "project_id": other["project_id"]},
            {**snapshot, "parent_folder_id": foreign["folder_id"]},
            {**snapshot, "tree_order_id": str(uuid.uuid4())},
        ]
        before = self.dump()
        for bad in bad_rows:
            with self.subTest(snapshot=bad), self.assertRaises(SyncContractError):
                self.store.replace_tree_order_snapshots(self.local_key, [bad])
            self.assertEqual(self.dump(), before)

    def test_order_idempotency_and_stale_snapshot_cannot_regress_durable_or_ui(self):
        snapshot = self.order(self.main["folder_id"], [self.sub["folder_id"]])
        self.store.replace_tree_order_snapshots(self.local_key, [snapshot])
        self.manager._apply_contract_tree_order_snapshots([snapshot])
        before = self.dump()
        self.store.replace_tree_order_snapshots(self.local_key, [snapshot])
        self.assertIsNone(self.manager._apply_contract_tree_order_snapshots([snapshot]))
        self.assertEqual(self.dump(), before)
        ui = copy.deepcopy(self.wpm.project_settings)
        for bad in ({**snapshot, "revision": 3}, {**snapshot, "children": []}):
            with self.subTest(snapshot=bad), self.assertRaises(SyncContractError):
                self.manager._apply_v2_remote_documents([], strict=True, tree_order_rows=[bad])
            self.assertEqual(self.dump(), before)
            self.assertEqual(self.wpm.project_settings, ui)


if __name__ == "__main__":
    unittest.main()
