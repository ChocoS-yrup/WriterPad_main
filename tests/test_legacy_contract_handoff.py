"""Offline regression for a protocol-2 create, rename, and content chain."""

import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sync_contract import CANONICAL_CONTRACT_SHA256, SERVER_CAPABILITIES, SyncContractError
from sync_manager import SyncManager
from sync_v2_store import SyncV2Store


class LegacyContractHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SyncV2Store(str(Path(self.temp.name) / "isolated.sqlite3"))
        self.project_id = str(uuid.uuid4())
        self.device_id = str(uuid.uuid4())
        self.context = self.store.configure_project(
            str(Path(self.temp.name) / "writing"), "synthetic", self.project_id
        )
        self.first = self.store.enqueue(self.context, "old.txt", "")
        self.store.move_local_path(self.context["local_key"], "old.txt", "new.txt")
        self.second = self.store.enqueue(self.context, "new.txt", "")
        self.third = self.store.enqueue(self.context, "new.txt", "x" * 26)
        self.store.mark_attempt(self.first["operation_id"])
        self.store.mark_retry(self.first["operation_id"], "PROTOCOL_TOO_OLD")

    def tearDown(self):
        self.temp.cleanup()

    def activate(self):
        self.store.set_contract_path_enabled(self.context["local_key"], True)
        self.store.activate_contract_project(
            self.context["local_key"], project_sync_mode="LEGACY",
            migration_epoch=0, server_protocol_version=3,
            server_contract_sha256=CANONICAL_CONTRACT_SHA256,
            server_capabilities=SERVER_CAPABILITIES,
        )

    @staticmethod
    def document_success(request, revision):
        intent = request["ordered_intents"][0]
        payload = intent["payload"]
        return {
            "kind": "document_commit_success",
            "batch_id": request["batch"]["batch_id"],
            "batch_payload_sha256": request["batch"]["batch_payload_sha256"],
            "status": "committed", "applied": True,
            "results": [{
                "sequence": 1, "operation_id": intent["operation_id"],
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

    def test_handoff_requires_explicit_gate_and_server_absence(self):
        self.assertIsNone(self.store.next_ready_operation(self.context["local_key"]))
        with self.assertRaises(SyncContractError):
            self.store.prepare_rejected_legacy_create(
                self.first["operation_id"], writer_device_id=self.device_id,
                server_absence_verified=True,
            )
        self.activate()
        with self.assertRaises(SyncContractError):
            self.store.prepare_rejected_legacy_create(
                self.first["operation_id"], writer_device_id=self.device_id,
            )
        self.assertEqual(self.store.operation(self.first["operation_id"])["status"], "retry_wait")

    def test_three_original_jobs_continue_in_order_with_immutable_links(self):
        self.activate()
        created = self.store.prepare_rejected_legacy_create(
            self.first["operation_id"], writer_device_id=self.device_id,
            server_absence_verified=True,
        )
        self.assertEqual(created["provenance_kind"], "CONTRACT_BATCH")
        self.assertEqual(created["sync_protocol_version"], 3)
        self.assertEqual(created["document_id"], self.first["document_id"])
        self.assertEqual(created["supersedes_operation_id"], self.first["operation_id"])
        self.assertNotEqual(created["operation_id"], self.first["operation_id"])
        create_request = self.store.structure_batch_request(created["batch_id"])
        self.assertEqual(create_request["project_sync_mode"], "LEGACY")
        self.assertEqual(create_request["ordered_intents"][0]["intent_kind"], "create")
        self.assertNotIn("supersedes_operation_id", create_request["ordered_intents"][0])
        self.store.mark_attempt(created["operation_id"])
        create_response = self.document_success(create_request, 1)
        self.store.record_document_batch_response(created["batch_id"], create_response)
        self.store.mark_success(created["operation_id"], {
            "revision": 1,
            "content_hash": create_response["results"][0]["content_sha256"],
            "structure_revision": 1,
            "parent_folder_id": None,
            "name": "old.txt",
        })

        rename_request = self.store.next_ready_structure_batch(self.context["local_key"])
        self.assertIsNotNone(rename_request)
        rename = rename_request["ordered_intents"][0]
        self.assertEqual(rename["entity_kind"], "document")
        self.assertEqual(rename["entity_id"], self.first["document_id"])
        self.assertEqual(rename["intent_kind"], "rename")
        self.assertEqual(rename["base_revision"], 1)
        self.assertNotIn("supersedes_operation_id", rename)
        self.assertEqual(
            self.store.operation(rename["operation_id"])["supersedes_operation_id"],
            self.second["operation_id"],
        )
        self.assertIsNone(self.store.next_ready_operation(self.context["local_key"]))
        self.assertEqual(self.store.recover_stranded_operations(self.context["local_key"]), 0)
        self.store.mark_structure_batch_attempt(rename_request["batch"]["batch_id"])
        rename_response = {
            "kind": "atomic_structure_commit_success",
            "batch_id": rename_request["batch"]["batch_id"],
            "batch_payload_sha256": rename_request["batch"]["batch_payload_sha256"],
            "status": "committed", "applied": True,
            "results": [{
                "sequence": 1, "operation_id": rename["operation_id"],
                "entity_id": rename["entity_id"], "result_revision": 2,
            }],
        }
        self.store.record_structure_batch_response(
            rename_request["batch"]["batch_id"], rename_response
        )
        self.store.record_structure_batch_response(
            rename_request["batch"]["batch_id"], rename_response
        )

        updated = self.store.next_ready_operation(self.context["local_key"])
        self.assertEqual(updated["provenance_kind"], "CONTRACT_BATCH")
        self.assertEqual(updated["supersedes_operation_id"], self.third["operation_id"])
        self.assertEqual(updated["document_id"], self.first["document_id"])
        self.assertEqual(updated["content"], self.third["content"])
        update_request = self.store.structure_batch_request(updated["batch_id"])
        update_intent = update_request["ordered_intents"][0]
        self.assertEqual(update_intent["intent_kind"], "update")
        self.assertEqual(update_intent["base_revision"], 1)
        self.assertEqual(update_intent["payload"]["structure_revision"], 2)
        self.assertEqual(update_intent["payload"]["name"], "new.txt")
        self.assertNotIn("supersedes_operation_id", update_intent)
        self.store.mark_attempt(updated["operation_id"])
        update_response = self.document_success(update_request, 2)
        self.store.record_document_batch_response(updated["batch_id"], update_response)
        self.store.mark_success(updated["operation_id"], {
            "revision": 2,
            "content_hash": update_response["results"][0]["content_sha256"],
            "structure_revision": 2,
            "parent_folder_id": None,
            "name": "new.txt",
        })
        self.assertIsNone(self.store.next_ready_operation(self.context["local_key"]))
        self.assertIsNone(self.store.next_ready_structure_batch(self.context["local_key"]))
        original = [self.store.operation(item["operation_id"]) for item in (
            self.first, self.second, self.third
        )]
        self.assertEqual([item["content"] for item in original], ["", "", "x" * 26])
        self.assertEqual([item["operation_id"] for item in original], [
            self.first["operation_id"], self.second["operation_id"],
            self.third["operation_id"],
        ])

    def test_manager_requires_read_only_server_absence_before_opening_gate(self):
        self.store.activate_contract_project(
            self.context["local_key"], project_sync_mode="LEGACY",
            migration_epoch=0, server_protocol_version=3,
            server_contract_sha256=CANONICAL_CONTRACT_SHA256,
            server_capabilities=SERVER_CAPABILITIES,
        )

        class ReadOnlyTable:
            def __init__(self, rows):
                self.rows = rows

            def select(self, _columns):
                return self

            def eq(self, _column, _value):
                return self

            def in_(self, _column, _values):
                return self

            def limit(self, _count):
                return self

            def execute(self):
                return SimpleNamespace(data=self.rows)

        class ReadOnlyClient:
            def __init__(self, document_rows):
                self.document_rows = document_rows
                self.calls = []

            def rpc(self, name, _params):
                self.calls.append(name)
                if name != "get_project_status":
                    raise AssertionError("unexpected RPC")
                return SimpleNamespace(execute=lambda: SimpleNamespace(data={
                    "project_id": self_project_id, "state": "active",
                }))

            def table(self, name):
                self.calls.append(name)
                if name == "documents":
                    return ReadOnlyTable(self.document_rows)
                if name == "sync_operations":
                    return ReadOnlyTable([])
                if name == "folders":
                    return ReadOnlyTable([])
                raise AssertionError("unexpected table")

        self_project_id = self.project_id
        reading = {"outcome": "supported", "observed_at": "2026-09-29T00:00:00+00:00",
                   "context_key": ("stable",)}
        compatibility = {
            "project_sync_mode": "ID_BASED", "migration_epoch": 1,
            "server_protocol_version": 3,
            "server_contract_sha256": CANONICAL_CONTRACT_SHA256,
            "server_capabilities": SERVER_CAPABILITIES,
        }

        def handshake(**kwargs):
            kwargs["_checkpoint_acceptor"](compatibility, reading)
            return reading

        manager = SyncManager()
        manager._v2_store = self.store
        manager._v2_context = dict(self.context)
        manager._v2_device_id = self.device_id
        manager._v2_worker = None
        manager._v2_structure_worker = None
        try:
            with (
                patch("sync_manager.general_test_writes_held", return_value=False),
                patch.object(manager, "_contract_context_key", return_value=("stable",)),
                patch.object(manager, "perform_contract_handshake", side_effect=handshake),
                patch.object(manager, "_contract_handshake_reading", return_value=reading),
                patch.object(manager, "ensure_session_valid"),
                patch.object(manager, "_begin_structure_authority_selection"),
                patch.object(manager, "_current_pull_coordinator", return_value={}),
                patch.object(manager, "_publish_sync_state"),
                patch.object(manager, "pull_remote_changes_async", return_value=True) as pull,
                patch.object(manager, "retry_pending_syncs", return_value=False),
            ):
                manager.supabase = ReadOnlyClient([{"document_id": self.first["document_id"]}])
                with self.assertRaises(SyncContractError):
                    manager.recover_rejected_legacy_queue()
                self.assertFalse(self.store.contract_path_enabled(self.context["local_key"]))
                self.assertEqual(manager.supabase.calls, [
                    "get_project_status", "documents", "sync_operations",
                ])
                missing_parent = {
                    **self.store.rejected_legacy_create_candidate(self.context["local_key"]),
                    "parent_folder_ids": [str(uuid.uuid4())],
                }
                manager.supabase = ReadOnlyClient([])
                with patch.object(
                    self.store, "rejected_legacy_create_candidate",
                    return_value=missing_parent,
                ):
                    with self.assertRaises(SyncContractError):
                        manager.recover_rejected_legacy_queue()
                self.assertFalse(self.store.contract_path_enabled(self.context["local_key"]))
                self.assertEqual(manager.supabase.calls, [
                    "get_project_status", "documents", "sync_operations", "folders",
                ])
                manager.supabase = ReadOnlyClient([])
                observations = 0

                def drifting_handshake(**kwargs):
                    nonlocal observations
                    observations += 1
                    observed = dict(compatibility)
                    if observations == 2:
                        observed.update(project_sync_mode="LEGACY", migration_epoch=0)
                    kwargs["_checkpoint_acceptor"](observed, reading)
                    return reading

                with patch.object(manager, "perform_contract_handshake",
                                  side_effect=drifting_handshake):
                    with self.assertRaises(SyncContractError):
                        manager.recover_rejected_legacy_queue()
                self.assertEqual(observations, 2)
                self.assertFalse(self.store.contract_path_enabled(self.context["local_key"]))
                self.assertEqual(self.store.get_project(self.context["local_key"])["project_sync_mode"],
                                 "LEGACY")
                manager.supabase = ReadOnlyClient([])
                with patch.object(
                    self.store, "prepare_rejected_legacy_create",
                    side_effect=SyncContractError("LEGACY_CHAIN_UNSAFE"),
                ):
                    with self.assertRaises(SyncContractError):
                        manager.recover_rejected_legacy_queue()
                rolled_back = self.store.get_project(self.context["local_key"])
                self.assertEqual(
                    (rolled_back["project_sync_mode"], rolled_back["migration_epoch"],
                     rolled_back["contract_path_enabled"]), ("LEGACY", 0, 0),
                )
                self.assertEqual(
                    [self.store.operation(item["operation_id"])["status"]
                     for item in (self.first, self.second, self.third)],
                    ["retry_wait", "pending", "pending"],
                )
                with self.store._reader() as connection:
                    self.assertEqual(connection.execute(
                        "SELECT count(*) FROM sync_server_checkpoint_observations"
                    ).fetchone()[0], 0)
                manager.supabase = ReadOnlyClient([])
                result = manager.recover_rejected_legacy_queue()
                self.assertTrue(self.store.contract_path_enabled(self.context["local_key"]))
                self.assertEqual(
                    (self.store.get_project(self.context["local_key"])["project_sync_mode"],
                     self.store.get_project(self.context["local_key"])["migration_epoch"]),
                    ("ID_BASED", 1),
                )
                self.assertTrue(result["successor_operation_id"])
                self.assertTrue(result["dispatch_started"])
                pull.assert_called_once_with(
                    manual=True, retry_pending_after_pull=True, reason="baseline"
                )
                successor = self.store.operation(result["successor_operation_id"])
                self.assertEqual(successor["document_id"], self.first["document_id"])
                self.assertEqual(successor["supersedes_operation_id"], self.first["operation_id"])
                request = self.store.structure_batch_request(successor["batch_id"])
                self.assertEqual((request["project_sync_mode"], request["migration_epoch"]),
                                 ("ID_BASED", 1))
                self.assertEqual(manager.supabase.calls, [
                    "get_project_status", "documents", "sync_operations",
                ])
                with self.store._reader() as connection:
                    self.assertEqual(connection.execute(
                        "SELECT count(*) FROM sync_server_checkpoint_observations"
                    ).fetchone()[0], 1)
        finally:
            manager.supabase = None
            manager._v2_context = None
            manager._v2_store = None
            manager._v2_device_id = None

    def test_rejected_rename_cannot_release_following_content(self):
        self.activate()
        created = self.store.prepare_rejected_legacy_create(
            self.first["operation_id"], writer_device_id=self.device_id,
            server_absence_verified=True,
        )
        self.store.mark_attempt(created["operation_id"])
        self.store.mark_success(created["operation_id"], {
            "revision": 1, "structure_revision": 1,
            "parent_folder_id": None, "name": "old.txt",
        })
        rename_request = self.store.next_ready_structure_batch(self.context["local_key"])
        self.store.mark_structure_batch_attempt(rename_request["batch"]["batch_id"])
        refusal = {
            "kind": "atomic_structure_commit_failure",
            "batch_id": rename_request["batch"]["batch_id"],
            "batch_payload_sha256": rename_request["batch"]["batch_payload_sha256"],
            "status": "rejected", "applied": False, "results": [],
            "error": {
                "code": "PATH_CONFLICT", "message": "PATH_CONFLICT",
                "failed_sequence": 1,
            },
        }
        self.store.record_structure_batch_response(
            rename_request["batch"]["batch_id"], refusal
        )
        self.assertEqual(self.store.recover_stranded_operations(self.context["local_key"]), 0)
        self.assertIsNone(self.store.next_ready_operation(self.context["local_key"]))
        self.assertEqual(self.store.operation(self.third["operation_id"])["status"], "pending")

    def test_uncertain_legacy_attempt_blocks_conversion(self):
        self.activate()
        self.store.mark_attempt(self.first["operation_id"])
        self.store.mark_retry(self.first["operation_id"], "NETWORK_UNAVAILABLE")
        with self.assertRaises(SyncContractError) as raised:
            self.store.prepare_rejected_legacy_create(
                self.first["operation_id"], writer_device_id=self.device_id,
                server_absence_verified=True,
            )
        self.assertEqual(raised.exception.code, "LEGACY_RESULT_UNCERTAIN")
        self.assertEqual(self.store.operation(self.first["operation_id"])["status"], "retry_wait")


if __name__ == "__main__":
    unittest.main()
