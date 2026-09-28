"""C4/C5/C9 safety regressions: synthetic projects and HTTP MockTransport only."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from postgrest import SyncPostgrestClient

from handshake_lifecycle import ContractDispatchPaused
from sync_contract import SyncContractError, read_handshake_compatibility
from sync_manager import STRUCTURE_AUTHORITY_LEGACY
from tests import test_handshake_stability as lifecycle
from tests import test_sync_contract_stage8 as fixtures


class ContractFollowupTests(unittest.TestCase):
    def setUp(self):
        self.case = lifecycle.HandshakeStabilityTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.manager = self.case.manager
        self.client = self.case.client
        self.store = self.case.store
        self.allow_structure()

    def allow_structure(self):
        self.manager._accept_structure_authority(STRUCTURE_AUTHORITY_LEGACY)
        self.manager.mark_project_server_state(fixtures.PROJECT_ID, "active")

    def attach_http(self, handler):
        http = httpx.Client(transport=httpx.MockTransport(handler))
        sdk = SyncPostgrestClient("https://example.invalid/rest/v1", http_client=http)
        self.addCleanup(http.close)
        self.client.rpc = sdk.rpc
        return sdk

    def test_project_id_is_required_and_must_identify_request(self):
        for value in (None, "", "not-a-uuid", False, fixtures.OTHER_PROJECT_ID):
            with self.subTest(value=value):
                reply = fixtures.supported_handshake()
                if value is None:
                    del reply["project_id"]
                else:
                    reply["project_id"] = value
                self.client.reply = reply
                self.manager._forget_contract_handshake()
                with self.assertRaises(SyncContractError):
                    self.manager.perform_contract_handshake()
                self.assertFalse(self.manager.contract_handshake_is_fresh())

    def test_protocol_and_capability_lists_reject_malformed_members(self):
        for field, value in (
            ("supported_protocol_versions", [3, 3]),
            ("supported_protocol_versions", [0, 3]),
            ("supported_protocol_versions", [-1, 3]),
            ("supported_protocol_versions", [True, 3]),
            ("supported_protocol_versions", []),
            ("server_capabilities", fixtures.supported_handshake()["server_capabilities"] * 2),
            ("server_capabilities", fixtures.supported_handshake()["server_capabilities"] + [""]),
        ):
            with self.subTest(field=field, value=value):
                reply = fixtures.supported_handshake(**{field: value})
                with self.assertRaises(SyncContractError):
                    read_handshake_compatibility(reply)

    def test_project_uuid_requires_a_json_string(self):
        # UUID(str(value)) alone could coerce a 32-digit JSON number to a UUID.
        reply = fixtures.supported_handshake(project_id=12345678123456781234567812345678)
        # The fixture normalizes project IDs for wire replies; inject the raw type.
        reply["project_id"] = 12345678123456781234567812345678
        with self.assertRaises(SyncContractError):
            read_handshake_compatibility(reply)

    def test_valid_response_and_required_pins_are_preserved(self):
        read_handshake_compatibility(fixtures.supported_handshake())
        for field in ("contract_version", "canonical_contract_sha256", "supported_protocol_versions"):
            reply = fixtures.supported_handshake()
            del reply[field]
            with self.subTest(field=field), self.assertRaises(SyncContractError):
                read_handshake_compatibility(reply)

    def test_real_sdk_http_failures_retry_after_deadline_without_duplicate_queries(self):
        for status in (429, 500, 503):
            with self.subTest(status=status):
                calls = []
                def handler(request):
                    calls.append(request)
                    if len(calls) == 1:
                        return httpx.Response(status, json={"message": "temporary service failure", "code": "PGRST000", "details": None, "hint": None})
                    return httpx.Response(200, json=fixtures.supported_handshake())
                self.attach_http(handler)
                self.manager._forget_contract_handshake()
                self.manager._ensure_contract_handshake()
                for _ in range(20):
                    self.manager._ensure_contract_handshake()
                self.assertEqual(len(calls), 1)
                self.case.now += 65
                for _ in range(20):
                    self.manager._ensure_contract_handshake()
                self.assertEqual(len(calls), 2)
                self.assertTrue(self.manager.contract_handshake_is_fresh())
                self.assertFalse(self.manager.contract_path_enabled())

    def test_real_sdk_refusals_are_not_transient_even_inside_500(self):
        for status, message in ((400, "invalid"), (401, "invalid"), (403, "invalid"),
                                (500, "CONTRACT_NOT_ALLOWED"), (500, "CONTRACT_DIGEST_MISMATCH")):
            with self.subTest(status=status, message=message):
                calls = []
                def handler(request):
                    calls.append(request)
                    return httpx.Response(status, json={"message": message, "code": "P0001", "details": None, "hint": None})
                self.attach_http(handler)
                self.manager._auth_retry_blocked = False
                self.client._antigravity_authenticated = True
                self.manager._forget_contract_handshake()
                self.manager._ensure_contract_handshake()
                self.case.now += 120
                for _ in range(20):
                    self.manager._ensure_contract_handshake()
                self.assertEqual(len(calls), 1)
                self.assertFalse(self.manager.contract_handshake_is_fresh())

    def test_wrapped_explicit_refusal_wins_over_older_timeout(self):
        refusal = SyncContractError("FORBIDDEN")
        refusal.__cause__ = TimeoutError()
        outer = RuntimeError("wrapper")
        outer.__cause__ = refusal
        self.assertFalse(self.manager._transient_handshake_error(outer))

    def change_authority(self, change):
        if change == "unknown":
            self.manager._begin_structure_authority_selection()
        elif change == "blocked":
            self.manager._block_structure_authority("INVALID_TREE_ORDER_RESPONSE")
        elif change == "blocked_then_allowed":
            self.manager._block_structure_authority("INVALID_TREE_ORDER_RESPONSE")
            self.allow_structure()
        elif change == "trashed_then_active":
            self.manager.mark_project_server_state(fixtures.PROJECT_ID, "trashed")
            self.allow_structure()
        else:
            self.manager.mark_project_server_state(fixtures.PROJECT_ID, change)

    def test_structure_and_project_changes_during_preparation_start_no_transport(self):
        for boundary in ("auth", "rpc"):
            for change in ("unknown", "blocked", "trashed", "purged", "blocked_then_allowed", "trashed_then_active"):
                with self.subTest(boundary=boundary, change=change):
                    self.allow_structure()
                    request = self.case.make_batch()
                    original = copy.deepcopy(request)
                    context = self.manager._contract_dispatch_context()
                    execute = Mock(return_value=SimpleNamespace(data=self.case.success_response(request)))
                    def auth(_client):
                        if boundary == "auth":
                            self.change_authority(change)
                    def rpc(name, _params):
                        if name == "get_project_status":
                            return SimpleNamespace(execute=lambda: SimpleNamespace(data={
                                "project_id": fixtures.PROJECT_ID, "state": "active"}))
                        if boundary == "rpc":
                            self.change_authority(change)
                        return SimpleNamespace(execute=execute)
                    with patch.object(self.manager, "ensure_session_valid", side_effect=auth), patch.object(self.client, "rpc", side_effect=rpc):
                        with self.assertRaises(ContractDispatchPaused):
                            self.manager._process_contract_structure_batch(request["batch"]["batch_id"], context)
                    execute.assert_not_called()
                    self.assertEqual(self.store.structure_batch_request(request["batch"]["batch_id"]), original)

    def test_worker_waiting_for_structure_decision_does_not_send(self):
        request = self.case.make_batch()
        workers = []
        with patch.object(self.manager, "_start_worker", side_effect=workers.append):
            self.manager._launch_contract_structure_batch(request["batch"]["batch_id"])
        self.manager._begin_structure_authority_selection()
        self.client.calls.clear()
        workers[0].run()
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.store.structure_batch_request(request["batch"]["batch_id"]), request)

    def test_unknown_structure_blocks_but_pending_local_work_does_not(self):
        request = self.case.make_batch()
        self.manager._begin_structure_authority_selection()
        self.assertFalse(self.manager._contract_request_ready(request))
        self.allow_structure()
        self.assertTrue(self.manager._contract_request_ready(request))
        # "Fully synced" is not the contract authority: this batch is pending.
        self.assertIsNotNone(self.store.next_ready_structure_batch(self.manager._v2_context["local_key"]))

    def test_document_uses_same_structure_boundary(self):
        operation, request = self.case.make_document_batch()
        execute = Mock()
        with patch.object(self.manager, "ensure_session_valid", side_effect=lambda *_args, **_kwargs: self.change_authority("blocked")), patch.object(self.client, "rpc", return_value=SimpleNamespace(execute=execute)):
            result = self.manager._process_v2_operation(operation["operation_id"])
        self.assertEqual(result["kind"], "paused")
        execute.assert_not_called()
        self.assertEqual(self.store.structure_batch_request(operation["batch_id"]), request)

    def test_started_response_is_kept_after_structure_or_project_blocks(self):
        request = self.case.make_batch()
        self.store.mark_structure_batch_attempt(request["batch"]["batch_id"])
        def answer():
            self.change_authority("blocked")
            self.change_authority("trashed")
            return SimpleNamespace(data=self.case.success_response(request))
        with patch.object(self.client, "_answer", side_effect=answer):
            receipt = self.manager._process_contract_structure_batch(request["batch"]["batch_id"])
        self.assertTrue(receipt["applied"])
        self.assertEqual(self.store.document_batch_response(request["batch"]["batch_id"]), receipt)

    def test_unobserved_project_state_cannot_authorize_contract_write(self):
        request = self.case.make_batch()
        self.manager._contract_project_state_context = None
        self.assertFalse(self.manager._contract_request_ready(request))
        self.client.reply = {"state": "active"}
        self.assertEqual(self.manager._fetch_v2_project_status(), "active")
        self.assertTrue(self.manager._contract_request_ready(request))

    def test_old_project_status_response_cannot_approve_new_authentication(self):
        request = self.case.make_batch()
        previous = self.manager._contract_project_state_context
        def reply():
            self.manager._auth_context_generation += 1
            return SimpleNamespace(data={"state": "active"})
        with patch.object(self.client, "rpc", return_value=SimpleNamespace(execute=reply)):
            self.manager._fetch_v2_project_status()
        self.assertEqual(self.manager._contract_project_state_context, previous)
        self.assertFalse(self.manager._contract_request_ready(request))

    def test_late_active_status_does_not_undo_newer_project_trash(self):
        request = self.case.make_batch()
        def reply():
            self.manager.mark_project_server_state(fixtures.PROJECT_ID, "trashed")
            return SimpleNamespace(data={"state": "active"})
        with patch.object(self.client, "rpc", return_value=SimpleNamespace(execute=reply)):
            self.manager._fetch_v2_project_status()
        self.assertEqual(self.manager._current_project_server_state(), "trashed")
        self.assertFalse(self.manager._contract_request_ready(request))

    def test_real_blocked_or_conflict_queue_during_preparation_preserves_request(self):
        for state in ("blocked", "conflict"):
            with self.subTest(state=state):
                self.allow_structure()
                request = self.case.make_batch()
                operation = self.store.enqueue(self.manager._v2_context, state + ".txt", "")
                execute = Mock()
                def prepare(name, _params):
                    if name == "get_project_status":
                        return SimpleNamespace(execute=lambda: SimpleNamespace(data={
                            "project_id": fixtures.PROJECT_ID, "state": "active"}))
                    if state == "blocked":
                        self.store.mark_blocked(operation["operation_id"], "INVALID_ARGUMENT")
                    else:
                        self.store.mark_conflict(operation["operation_id"], 1, state + ".txt", "", "")
                    return SimpleNamespace(execute=execute)
                with patch.object(self.client, "rpc", side_effect=prepare):
                    with self.assertRaises(ContractDispatchPaused):
                        self.manager._process_contract_structure_batch(request["batch"]["batch_id"])
                execute.assert_not_called()
                self.assertEqual(self.store.structure_batch_request(request["batch"]["batch_id"]), request)
                # Resolve this synthetic blocker before testing the next state.
                self.store.mark_retry(operation["operation_id"], "test resolved")

    def test_real_sdk_contract_failure_has_no_hidden_retry_and_reuses_payload(self):
        request = self.case.make_batch()
        sent = []
        def handler(http_request):
            if http_request.url.path.endswith("/get_project_status"):
                return httpx.Response(200, json={"project_id": fixtures.PROJECT_ID, "state": "active"})
            sent.append(http_request.content)
            if len(sent) == 1:
                return httpx.Response(503, json={"code": "PGRST000", "message": "temporary failure", "details": None, "hint": None})
            return httpx.Response(200, json=self.case.success_response(request))
        sdk = self.attach_http(handler)
        with self.assertRaises(Exception) as raised:
            self.manager._process_contract_structure_batch(request["batch"]["batch_id"])
        self.assertEqual(getattr(raised.exception, "status_code", None), 503)
        self.assertEqual(len(sent), 1)
        self.assertTrue(sdk.rpc("get_sync_handshake", {}).request.retry_enabled)
        self.manager.disable_contract_path()
        with self.assertRaises(ContractDispatchPaused):
            self.manager._process_contract_structure_batch(request["batch"]["batch_id"])
        self.assertEqual(len(sent), 1)
        # Reauthorize only this temporary project, preserving its queued JSON.
        self.client.rpc = fixtures._HandshakeClient(fixtures.supported_handshake()).rpc
        self.manager.enable_contract_path()
        self.client.rpc = sdk.rpc
        receipt = self.manager._process_contract_structure_batch(request["batch"]["batch_id"])
        self.assertTrue(receipt["applied"])
        self.assertEqual(sent[0], sent[1])


if __name__ == "__main__":
    unittest.main()
