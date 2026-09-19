"""Synthetic UUIDs, temporary real stores/trees, real pull apply, no network."""
import copy
import json
import os
import socket
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.qt_app import APP
from tests import test_handshake_stability as lifecycle
from tests.test_sync_contract_stage8 import supported_handshake, access_token_with_subject
from project_creation_v1 import create_project, create_item, writing_root
from project_identity_v1 import read_identity
from project_manager_writing import WritingProjectManager
from sync_v2_store import SyncV2Store
from sync_contract import CANONICAL_CONTRACT_SHA256, SERVER_CAPABILITIES
from handshake_lifecycle import ContractDispatchPaused
import general_test_gate as gate


class GeneralTestGateTests(unittest.TestCase):
    def setUp(self):
        blocked = patch.object(socket.socket, "connect", side_effect=AssertionError("LIVE NETWORK FORBIDDEN"))
        blocked.start(); self.addCleanup(blocked.stop)
        self.case = lifecycle.HandshakeStabilityTests()
        self.case.setUp(); self.addCleanup(self.case.doCleanups)
        self.manager, self.store, self.client = self.case.manager, self.case.store, self.case.client
        self.workspace = Path(self.case.fixture.temp.name) / "synthetic"
        self.workspace.mkdir()
        self.name = "합성 준비 시험"
        identity = create_project(str(self.workspace), self.name)
        self.root = self.workspace / self.name
        create_item(str(self.root), identity["nodes"][0]["uuid"], "추가 시험", True)
        identity = read_identity(str(self.root))
        self.project_id = identity["project"]["uuid"]
        self.folders = [{"id": n["uuid"], "parent": n["parent_uuid"], "name": n["title"],
                         "revision": 1, "deleted": False} for n in identity["nodes"]]
        self.orders = [{"id": str(uuid.uuid4()), "parent": parent, "revision": 1,
                        "children": [n["uuid"] for n in sorted(identity["nodes"], key=lambda n:n["order"])
                                     if n["parent_uuid"] == parent]}
                       for parent in [None] + [n["uuid"] for n in identity["nodes"]]]
        self.assertEqual(len(self.folders), 11)
        self.assertEqual(len(self.orders), 12)
        active = patch.multiple(gate, PROJECT_ID=self.project_id, PROJECT_NAME=self.name,
                                STAGING_URL="https://synthetic.invalid", EXPECTED_FOLDERS=self.folders,
                                EXPECTED_ORDERS=self.orders)
        active.start(); self.addCleanup(active.stop)
        manager = self.manager
        manager._v2_context = dict(self.store.configure_project(writing_root(str(self.root)), self.name, self.project_id))
        manager._v2_context["writer_device_id"] = manager._v2_device_id
        # Start at LEGACY/0, just like the reported Windows disk checkpoint.
        manager._v2_wpm = WritingProjectManager.create_detached(str(self.workspace), self.name, writing_root(str(self.root)))
        self.client.supabase_url = gate.STAGING_URL
        self.client.reply = supported_handshake(self.project_id, project_sync_mode="ID_BASED", migration_epoch=1)
        self.workers = []
        for name, value in {"_cloud_config": SimpleNamespace(url=gate.STAGING_URL),
                            "_v2_pull_worker": None, "_v2_pull_worker_identity": None,
                            "_active_backups": 0, "_server_action_workers": [], "_retry_queue": {},
                            "_retry_active_key": None, "_review_execution_busy": False,
                            "_general_test_ticket": None}.items():
            active = patch.object(manager, name, value, create=True)
            active.start(); self.addCleanup(active.stop)
        manager._forget_contract_handshake()
        manager._accept_structure_authority("contract")
        manager.mark_project_server_state(self.project_id, "active")
        self.coordinator = manager._current_pull_coordinator()
        self.coordinator.update(pulling=False, pull_pending=False, baseline_validated=False)
        self.folder_rows = [{"folder_id": n["uuid"], "project_id":self.project_id, "parent_folder_id": n["parent_uuid"],
                             "name":n["title"], "revision":1, "is_deleted":False}
                            for n in identity["nodes"]]
        self.order_rows = [{"tree_order_id":n["id"], "project_id":self.project_id, "parent_folder_id":n["parent"],
                            "children":n["children"], "revision":1} for n in self.orders]
        for name, options in (
            ("request_contract_handshake_async", {"return_value":False}),
            ("_start_worker", {"side_effect":self.workers.append}),
            ("_record_sync_success", {}),
            ("_fetch_v2_project_documents", {"return_value":[]}),
            ("_fetch_v2_project_folders", {"side_effect":lambda *a, **k:copy.deepcopy(self.folder_rows)}),
            ("_fetch_v2_project_tree_orders", {"side_effect":lambda *a, **k:copy.deepcopy(self.order_rows)}),
            ("_fetch_v2_project_folder_versions", {"return_value":[]}),
        ):
            active = patch.object(manager, name, **options)
            active.start(); self.addCleanup(active.stop)
        self.addCleanup(self.flush)

    def flush(self):
        self.manager._shutting_down = True
        APP.processEvents()

    def prepare(self):
        self.ticket = gate.begin(self.manager)
        gate.handshake(self.manager, self.ticket)
        gate.start_pull(self.manager, self.ticket)
        self.finish_pull()
        return gate.observation(self.manager, self.ticket)

    def finish_pull(self):
        worker = self.workers[-1]
        worker.run()  # real V2PullWorker and connected production foreground apply
        worker.finished.emit()

    def test_fresh_pull_and_activation_keep_writes_held(self):
        other = gate._gates(self.manager)
        before = self.prepare()
        self.assertFalse(before["gate_open"])
        self.assertEqual(before["received_uuid_orders"], 12)
        self.assertTrue(gate.writes_held(self.manager))
        calls = len(self.client.calls)
        result = gate.activate(self.manager, self.ticket)
        self.assertGreater(len(self.client.calls), calls)
        self.assertTrue(result["gate_open"])
        self.assertTrue(result["contract_path_selected"])
        self.assertTrue(result["outbound_send_held"])
        self.assertTrue(result["other_project_gates_unchanged"])
        self.assertIsNone(result["outbound_test_requests"])
        self.assertEqual({k:v for k,v in gate._gates(self.manager).items() if k in other and k != self.manager._v2_context["local_key"]},
                         {k:v for k,v in other.items() if k != self.manager._v2_context["local_key"]})
        self.assertTrue(all(name in {"get_sync_handshake", "get_project_status"} for name,_ in self.client.calls))

    def test_hold_is_durable_and_scoped_even_with_captured_context(self):
        gate.begin(self.manager)
        context = self.manager._contract_dispatch_context()
        reopened = SyncV2Store(self.store.db_path)
        restarted = SimpleNamespace(_v2_context=dict(self.manager._v2_context), _v2_store=reopened)
        self.assertTrue(gate.writes_held(restarted))
        self.manager._v2_context = {**self.manager._v2_context, "project_id":str(uuid.uuid4())}
        self.assertFalse(gate.writes_held(self.manager))
        self.assertTrue(gate.writes_held(self.manager, context))
        with self.assertRaises(ContractDispatchPaused): self.manager._check_contract_dispatch({}, context)

    def test_old_baseline_cannot_substitute_for_new_pull(self):
        self.ticket = gate.begin(self.manager)
        gate.handshake(self.manager, self.ticket)
        self.coordinator["baseline_validated"] = True
        with self.assertRaises(gate.PreparationError): gate.observation(self.manager, self.ticket)
        with self.assertRaises(gate.PreparationError): gate.activate(self.manager, self.ticket)
        self.assertFalse(self.manager.contract_path_enabled())

    def test_failed_pull_does_not_arm_gate_or_clear_hold(self):
        self.ticket = gate.begin(self.manager)
        gate.handshake(self.manager, self.ticket)
        gate.start_pull(self.manager, self.ticket)
        self.workers[-1].resultReady.emit(False, "synthetic failure")
        self.workers[-1].finished.emit()
        with self.assertRaises(gate.PreparationError): gate.observation(self.manager, self.ticket)
        self.assertTrue(gate.writes_held(self.manager))
        self.assertFalse(self.manager.contract_path_enabled())

    def test_order_revision_mismatch_blocks_readiness(self):
        self.order_rows[0]["revision"] = 2
        with self.assertRaises(gate.PreparationError): self.prepare()
        self.assertFalse(self.manager.contract_path_enabled())

    def test_wrong_target_and_endpoint_are_refused_before_network(self):
        for attr, value in (("_v2_context", {**self.manager._v2_context, "project_id":str(uuid.uuid4())}),
                            ("_cloud_config", SimpleNamespace(url="https://different.invalid"))):
            with self.subTest(attr=attr), patch.object(self.manager, attr, value):
                with self.assertRaises(gate.PreparationError): gate.begin(self.manager)
        self.assertEqual(self.client.calls, [])
        self.assertFalse(gate.writes_held(self.manager))

    def test_pending_or_active_work_refused_before_network(self):
        for attr, value in (("_active_server_syncs", 1), ("_server_action_workers", [object()]),
                            ("_retry_queue", {"fake":object()}), ("_review_execution_busy", True)):
            with self.subTest(attr=attr), patch.object(self.manager, attr, value):
                with self.assertRaises(gate.PreparationError): gate.begin(self.manager)
        self.assertEqual(self.client.calls, [])

    def test_account_change_during_activation_never_opens_gate(self):
        self.prepare()
        original = self.client._answer
        def changed():
            result = original()
            self.client._antigravity_access_token = access_token_with_subject(str(uuid.uuid4()))
            return result
        with patch.object(self.client, "_answer", side_effect=changed):
            with self.assertRaises(Exception): gate.activate(self.manager, self.ticket)
        self.assertFalse(self.manager.contract_path_enabled())
        self.assertTrue(gate.writes_held(self.manager))

    def test_queue_change_at_final_handshake_blocks_gate(self):
        self.prepare()
        original = self.client._answer
        def changed():
            result = original()
            self.manager._retry_queue["synthetic"] = object()
            return result
        with patch.object(self.client, "_answer", side_effect=changed):
            with self.assertRaises(gate.PreparationError): gate.activate(self.manager, self.ticket)
        self.assertFalse(self.manager.contract_path_enabled())

    def test_send_and_lease_boundaries_issue_no_rpc(self):
        self.prepare(); gate.activate(self.manager, self.ticket)
        self.client.calls.clear()
        context = self.manager._contract_dispatch_context()
        self.assertFalse(self.manager.retry_pending_syncs())
        self.assertIsNone(self.manager._launch_v2_operation("synthetic"))
        result = self.manager._process_v2_operation("synthetic", dispatch_context=context)
        self.assertEqual(result["kind"], "paused")
        with self.assertRaises(ContractDispatchPaused): self.manager._check_contract_dispatch({}, context)
        with self.assertRaises(ContractDispatchPaused): self.manager._ensure_remote_project(self.client)
        with self.assertRaises(ContractDispatchPaused): self.manager._acquire_v2_lease(str(uuid.uuid4()))
        self.assertFalse(self.manager._release_v2_lease(str(uuid.uuid4())))
        self.manager.heartbeat_lock(self.name, "synthetic.txt", "synthetic")
        self.assertFalse(self.manager.release_lock(self.name, "synthetic.txt", "synthetic"))
        action = Mock()
        self.assertIsNone(self.manager._start_server_action(action))
        action.assert_not_called()
        self.assertEqual(self.client.calls, [])

    def test_completed_server_checkpoint_is_accepted_without_intermediate_migrating(self):
        # A separate empty DB reproduces a Windows client that missed the
        # transition. Keep this diagnostic separate from success fixtures.
        legacy = SyncV2Store(str(self.workspace / "legacy.sqlite3"))
        with patch.object(self.manager, "_v2_store", legacy), patch.object(self.manager, "_v2_context",
                dict(legacy.configure_project(writing_root(str(self.root)), self.name, self.project_id))):
            ticket = gate.begin(self.manager)
            gate.handshake(self.manager, ticket)
            row = legacy.get_project(self.manager._v2_context["local_key"])
            self.assertEqual((row["project_sync_mode"], row["migration_epoch"]), ("ID_BASED", 1))
            self.assertFalse(row["contract_path_enabled"])
            self.assertTrue(gate.writes_held(self.manager))
            self.assertFalse(self.manager._current_pull_coordinator()["baseline_validated"])
            with legacy._reader() as connection:
                records = connection.execute("SELECT old_mode, new_mode FROM sync_server_checkpoint_observations").fetchall()
                self.assertEqual([tuple(r) for r in records], [("LEGACY", "ID_BASED")])

    def test_changed_other_project_gate_invalidates_report(self):
        self.prepare()
        other_key = self.case.fixture.context["local_key"]
        self.store.set_contract_path_enabled(other_key, True)
        with self.assertRaises(gate.PreparationError): gate.observation(self.manager, self.ticket)
        self.assertFalse(self.manager.contract_path_enabled())

    def test_hold_creation_failure_performs_no_network_or_gate_write(self):
        with patch.object(Path, "open", side_effect=OSError("synthetic disk full")):
            with self.assertRaises(OSError): gate.begin(self.manager)
        self.assertEqual(self.client.calls, [])
        self.assertFalse(self.manager.contract_path_enabled())

    def test_stale_context_after_pull_start_is_not_accepted(self):
        ticket = gate.begin(self.manager); gate.handshake(self.manager, ticket)
        gate.start_pull(self.manager, ticket)
        self.manager._v2_context_generation += 1
        try:
            self.finish_pull()
            with self.assertRaises(gate.PreparationError): gate.observation(self.manager, ticket)
            self.assertFalse(self.manager.contract_path_enabled())
        finally:
            self.manager._v2_context_generation -= 1
            self.manager._v2_pull_worker = None

    def test_c9_blocked_authority_refuses_activation(self):
        self.prepare()
        self.manager._block_structure_authority("synthetic")
        with self.assertRaises(gate.PreparationError): gate.activate(self.manager, self.ticket)
        self.assertFalse(self.manager.contract_path_enabled())


if __name__ == "__main__":
    unittest.main()
