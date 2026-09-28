"""Per-send status and queue-transition authorization; no live server access."""
import copy
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from handshake_lifecycle import ContractDispatchPaused
from sync_v2_store import SyncV2Store
from tests import test_contract_followup as base
from tests import test_sync_contract_stage8 as fixtures


class ContractSendPolicyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = base.ContractFollowupTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.manager = self.fixture.manager
        self.store = self.fixture.store
        self.client = self.fixture.client
        self.status_reply = {"project_id": fixtures.PROJECT_ID, "state": "active"}
        self.calls = []

    def batch(self):
        return self.fixture.case.make_batch()

    def install(self, request, before_status=None, before_write=None):
        def rpc(name, params):
            def execute():
                self.calls.append(name)
                if name == "get_project_status":
                    if before_status:
                        before_status()
                    if isinstance(self.status_reply, Exception):
                        raise self.status_reply
                    return SimpleNamespace(data=self.status_reply)
                if before_write:
                    before_write()
                response = (fixtures.ContractStoreTests._document_success(request)
                            if name == "document_commit"
                            else self.fixture.case.success_response(request))
                return SimpleNamespace(data=response)
            return SimpleNamespace(execute=execute)
        return patch.object(self.client, "rpc", side_effect=rpc)

    def test_each_uncached_send_reads_status_before_write(self):
        for _ in range(2):
            request = self.batch()
            with self.install(request):
                self.manager._process_contract_structure_batch(request["batch"]["batch_id"])
        self.assertEqual(self.calls, ["get_project_status", "atomic_structure_commit"] * 2)

    def test_invalid_status_never_falls_back_or_writes(self):
        for reply in ({"state": "active"}, {"project_id": fixtures.OTHER_PROJECT_ID, "state": "active"},
                      {"project_id": 123, "state": "active"}, [], None,
                      {"project_id": fixtures.PROJECT_ID, "state": "unknown"},
                      TimeoutError("synthetic offline"), RuntimeError("PROJECT_NOT_FOUND")):
            with self.subTest(reply=type(reply).__name__):
                request = self.batch()
                self.status_reply = reply
                self.calls.clear()
                with self.install(request), self.assertRaises(Exception):
                    self.manager._process_contract_structure_batch(request["batch"]["batch_id"])
                self.assertEqual(self.calls, ["get_project_status"])
                self.assertEqual(self.store.structure_batch_request(request["batch"]["batch_id"]), request)

    def test_server_inactive_blocks_without_using_cached_active(self):
        for state in ("trashed", "purged"):
            self.fixture.allow_structure()
            request = self.batch()
            self.status_reply = {"project_id": fixtures.PROJECT_ID, "state": state}
            self.calls.clear()
            with self.install(request), self.assertRaises(Exception):
                self.manager._process_contract_structure_batch(request["batch"]["batch_id"])
            self.assertEqual(self.calls, ["get_project_status"])

    def test_closure_during_status_read_preserves_batch(self):
        request = self.batch()
        with self.install(request, before_status=self.manager.disable_contract_path), self.assertRaises(ContractDispatchPaused):
            self.manager._process_contract_structure_batch(request["batch"]["batch_id"])
        self.assertEqual(self.calls, ["get_project_status"])
        self.assertEqual(self.store.structure_batch_request(request["batch"]["batch_id"]), request)

    def test_block_then_resolve_invalidates_old_preparation_at_both_boundaries(self):
        for boundary in ("status", "rpc"):
            for state in ("blocked", "conflict"):
                with self.subTest(boundary=boundary, state=state):
                    request = self.batch()
                    original = copy.deepcopy(request)
                    blocker = self.store.enqueue(self.manager._v2_context, boundary + state + ".txt", "")
                    def transition():
                        if state == "blocked":
                            self.store.mark_blocked(blocker["operation_id"], "INVALID_ARGUMENT")
                        else:
                            self.store.mark_conflict(blocker["operation_id"], 1, boundary + state + ".txt", "", "")
                        self.store.mark_retry(blocker["operation_id"], "synthetic resolved")
                    writes = []
                    def rpc(name, params):
                        if name == "get_project_status":
                            if boundary == "status":
                                transition()
                            return SimpleNamespace(execute=lambda: SimpleNamespace(data=self.status_reply))
                        if boundary == "rpc":
                            transition()
                        return SimpleNamespace(execute=lambda: (writes.append(name) or SimpleNamespace(data=self.fixture.case.success_response(request))))
                    with patch.object(self.client, "rpc", side_effect=rpc), self.assertRaises(ContractDispatchPaused):
                        self.manager._process_contract_structure_batch(request["batch"]["batch_id"])
                    self.assertEqual(writes, [])
                    self.assertEqual(self.store.structure_batch_request(request["batch"]["batch_id"]), original)
                    # A new preparation may reuse the exact batch after resolution.
                    with self.install(request):
                        self.assertTrue(self.manager._process_contract_structure_batch(request["batch"]["batch_id"])["applied"])

    def test_pending_enqueue_does_not_invalidate_preparation(self):
        request = self.batch()
        with self.install(request, before_status=lambda: self.store.enqueue(self.manager._v2_context, "new-local.txt", "")):
            result = self.manager._process_contract_structure_batch(request["batch"]["batch_id"])
        self.assertTrue(result["applied"])
        self.assertEqual(self.calls, ["get_project_status", "atomic_structure_commit"])

    def test_document_commit_also_requires_fresh_status(self):
        operation, request = self.fixture.case.make_document_batch()
        with self.install(request):
            result = self.manager._process_v2_operation(operation["operation_id"])
        self.assertEqual(result["kind"], "committed")
        self.assertEqual(self.calls, ["get_project_status", "document_commit"])

    def test_journal_stamp_covers_structure_events_and_resolution_across_store_instances(self):
        request = self.batch()
        operation_id = request["ordered_intents"][0]["operation_id"]
        key = self.manager._v2_context["local_key"]
        other_reader = SyncV2Store(self.store.db_path)
        original = other_reader.contract_queue_authority_stamp(key)
        self.store.mark_blocked(operation_id, "INVALID_ARGUMENT")
        blocked = other_reader.contract_queue_authority_stamp(key)
        self.assertNotEqual(original, blocked)
        self.store.mark_retry(operation_id, "synthetic resolved")
        self.assertNotEqual(blocked, other_reader.contract_queue_authority_stamp(key))
        self.assertEqual(self.store.structure_batch_request(request["batch"]["batch_id"]), request)

    def test_cached_receipt_needs_no_additional_status_read_or_write(self):
        request = self.batch()
        with self.install(request):
            first = self.manager._process_contract_structure_batch(request["batch"]["batch_id"])
            second = self.manager._process_contract_structure_batch(request["batch"]["batch_id"])
        self.assertEqual(first, second)
        self.assertEqual(self.calls, ["get_project_status", "atomic_structure_commit"])

    def test_repeated_notifications_do_not_duplicate_status_preparation(self):
        request = self.batch()
        entered, release = threading.Event(), threading.Event()
        results = []
        def pause():
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test wait")
        with self.install(request, before_status=pause):
            thread = threading.Thread(target=lambda: results.append(self.manager._process_contract_structure_batch(request["batch"]["batch_id"])))
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                for _ in range(5):
                    with self.assertRaises(ContractDispatchPaused):
                        self.manager._process_contract_structure_batch(request["batch"]["batch_id"])
            finally:
                release.set()
                thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.calls, ["get_project_status", "atomic_structure_commit"])
        self.assertTrue(results[0]["applied"])


if __name__ == "__main__":
    unittest.main()
