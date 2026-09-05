"""Offline lifecycle/dispatch regression tests; no credentials or live projects."""
import copy
import threading
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from tests.qt_app import APP
from tests import test_sync_contract_stage8 as fixtures
from sync_manager import SyncManager, STRUCTURE_AUTHORITY_LEGACY
from sync_contract import SyncContractError, read_handshake_compatibility
from handshake_lifecycle import ContractDispatchPaused


class HandshakeStabilityTests(unittest.TestCase):
    def setUp(self):
        session_generation = SyncManager._session_generation
        self.addCleanup(setattr, SyncManager, "_session_generation", session_generation)
        self.fixture = fixtures.ContractHandshakeGateTests()
        with patch.object(SyncManager, "init_supabase"):
            self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.manager = self.fixture.manager
        self.manager._v2_context["writer_device_id"] = self.manager._v2_device_id
        self.store = self.fixture.store
        self.client = self.fixture._attach(fixtures.supported_handshake())
        self.manager._auth_retry_blocked = False
        self.manager._shutting_down = False
        self.manager._v2_worker = None
        self.manager._v2_structure_worker = None
        self.manager._active_server_syncs = 0
        self.manager._cancel_scheduled_v2_retry(reset_backoff=True)
        self.now = 100.0
        clock = patch("sync_manager.time.monotonic", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)

    def make_batch(self):
        self.manager.enable_contract_path()
        return self.manager.queue_atomic_structure_batch([{
            "entity_kind": "folder", "entity_id": str(uuid.uuid4()),
            "intent_kind": "create", "base_revision": 0,
            "payload": {"name": "synthetic", "parent_folder_id": None},
        }], retry=False)

    def test_closed_gate_preserves_queued_batch_without_dispatch(self):
        request = self.make_batch()
        self.manager.disable_contract_path()
        self.manager._accept_structure_authority(STRUCTURE_AUTHORITY_LEGACY)
        with patch.object(self.manager, "_current_project_server_state", return_value="active"), patch.object(
            self.manager, "_launch_contract_structure_batch"
        ) as launch:
            self.manager.retry_pending_syncs()
        launch.assert_not_called()
        self.assertEqual(self.store.structure_batch_request(request["batch"]["batch_id"]), request)

    def test_invalid_handshake_starts_no_rpc(self):
        request = self.make_batch()
        self.manager._forget_contract_handshake()
        self.client.calls.clear()
        try:
            self.manager._process_contract_structure_batch(request["batch"]["batch_id"])
        except SyncContractError:
            pass
        self.assertEqual(self.client.calls, [])

    def test_account_change_requeries_in_same_project(self):
        self.manager._ensure_contract_handshake()
        self.client._antigravity_access_token = fixtures.access_token_with_subject(fixtures.OTHER_PROJECT_ID)
        self.manager._ensure_contract_handshake()
        self.assertEqual(len(self.client.calls), 2)
        self.assertTrue(self.manager.contract_handshake_is_fresh())

    def test_transient_failure_recovers_with_bounded_retry(self):
        self.client.reply = TimeoutError("synthetic timeout")
        self.manager._ensure_contract_handshake()
        self.client.reply = fixtures.supported_handshake()
        for _ in range(20):
            self.manager._ensure_contract_handshake()
        self.assertEqual(len(self.client.calls), 1)
        self.now += 65
        self.manager._ensure_contract_handshake()
        self.assertEqual(len(self.client.calls), 2)
        self.assertTrue(self.manager.contract_handshake_is_fresh())

    def test_late_account_response_is_not_rebound_to_new_account(self):
        def answer():
            self.client._antigravity_access_token = fixtures.access_token_with_subject(fixtures.OTHER_PROJECT_ID)
            return SimpleNamespace(data=fixtures.supported_handshake())
        with patch.object(self.client, "_answer", side_effect=answer):
            self.manager._ensure_contract_handshake()
        self.assertFalse(self.manager.contract_handshake_is_fresh())
        self.assertIsNone(self.fixture._project()["contract_validated_at"])

    def test_missing_contract_fields_are_rejected(self):
        for key in ("contract_version", "canonical_contract_sha256", "supported_protocol_versions"):
            with self.subTest(key=key):
                reply = fixtures.supported_handshake()
                del reply[key]
                with self.assertRaises(SyncContractError):
                    read_handshake_compatibility(reply)

    def success_response(self, request):
        return {
            "kind": "atomic_structure_commit_success", "status": "committed",
            "applied": True, "batch_id": request["batch"]["batch_id"],
            "batch_payload_sha256": request["batch"]["batch_payload_sha256"],
            "results": [{"sequence": i["sequence"], "operation_id": i["operation_id"],
                         "entity_id": i["entity_id"], "result_revision": 1}
                        for i in request["ordered_intents"]],
        }

    def test_preparation_changes_prevent_transport(self):
        for change in ("gate", "account", "project", "client", "digest", "epoch", "logout"):
            with self.subTest(change=change):
                self.manager._forget_contract_handshake()
                self.client = self.fixture._attach(fixtures.supported_handshake())
                request = self.make_batch()
                context = self.manager._contract_dispatch_context()
                original_context = dict(self.manager._v2_context)
                def mutate(_client):
                    if change == "gate":
                        self.manager.disable_contract_path()
                    elif change == "account":
                        self.client._antigravity_access_token = fixtures.access_token_with_subject(fixtures.OTHER_PROJECT_ID)
                    elif change == "project":
                        self.manager._v2_context_generation += 1
                    elif change == "client":
                        self.fixture._attach(fixtures.supported_handshake())
                    elif change == "digest":
                        self.manager._contract_handshake["contract_sha256"] = "0" * 64
                    elif change == "epoch":
                        request["migration_epoch"] += 1
                    elif change == "logout":
                        self.client._antigravity_authenticated = False
                self.client.calls.clear()
                with patch.object(self.manager, "ensure_session_valid", side_effect=mutate):
                    with self.assertRaises(ContractDispatchPaused):
                        self.manager._send_contract_request("atomic_structure_commit", request, context)
                self.assertEqual(self.client.calls, [])
                self.manager._v2_context = original_context

    def test_state_change_during_rpc_construction_prevents_execute(self):
        request = self.make_batch()
        execute = unittest.mock.Mock()
        def prepare(*_):
            self.manager.disable_contract_path()
            return SimpleNamespace(execute=execute)
        with patch.object(self.client, "rpc", side_effect=prepare):
            with self.assertRaises(ContractDispatchPaused):
                self.manager._process_contract_structure_batch(request["batch"]["batch_id"])
        execute.assert_not_called()

    def test_worker_waiting_to_start_cannot_rebind_to_new_project(self):
        request = self.make_batch()
        workers = []
        with patch.object(self.manager, "_start_worker", side_effect=workers.append):
            self.manager._launch_contract_structure_batch(request["batch"]["batch_id"])
        self.manager._v2_context_generation += 1
        self.client.calls.clear()
        workers[0].run()
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.store.structure_batch_request(request["batch"]["batch_id"]), request)
        self.assertEqual(self.store.operation(request["ordered_intents"][0]["operation_id"])["status"], "retry_wait")

    def test_repeated_retry_notifications_start_one_worker(self):
        request = self.make_batch()
        self.manager._accept_structure_authority(STRUCTURE_AUTHORITY_LEGACY)
        workers = []
        with patch.object(self.manager, "_current_project_server_state", return_value="active"), patch.object(
            self.manager, "_start_worker", side_effect=workers.append
        ):
            for _ in range(20):
                self.manager.retry_pending_syncs()
        self.assertEqual(len(workers), 1)
        self.manager.disable_contract_path()
        workers[0].run()
        self.assertEqual(self.store.structure_batch_request(request["batch"]["batch_id"]), request)

    def test_inflight_response_is_recorded_after_gate_closes(self):
        request = self.make_batch()
        response = self.success_response(request)
        def answer():
            self.manager.disable_contract_path()
            return SimpleNamespace(data=response)
        self.store.mark_structure_batch_attempt(request["batch"]["batch_id"])
        with patch.object(self.client, "_answer", side_effect=answer):
            result = self.manager._process_contract_structure_batch(request["batch"]["batch_id"])
        self.assertTrue(result["applied"])
        self.assertFalse(self.manager.contract_path_enabled())
        self.assertEqual(self.store.operation(request["ordered_intents"][0]["operation_id"])["status"], "completed")

    def test_late_structure_receipt_does_not_publish_new_project_state(self):
        request = self.make_batch()
        response = self.success_response(request)
        workers = []
        with patch.object(self.manager, "_start_worker", side_effect=workers.append):
            self.manager._launch_contract_structure_batch(request["batch"]["batch_id"])
        def answer():
            self.manager.release_v2()
            return SimpleNamespace(data=response)
        with patch.object(self.client, "_answer", side_effect=answer), patch.object(
            self.manager, "_publish_sync_state"
        ) as publish:
            workers[0].run()
        publish.assert_not_called()
        self.assertEqual(self.store.document_batch_response(request["batch"]["batch_id"]), response)

    def test_duplicate_handshake_notifications_are_nonblocking(self):
        entered, release = threading.Event(), threading.Event()
        def answer():
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test synchronization timeout")
            return SimpleNamespace(data=fixtures.supported_handshake())
        with patch.object(self.client, "_answer", side_effect=answer):
            thread = threading.Thread(target=self.manager._ensure_contract_handshake)
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                for _ in range(20):
                    self.manager._ensure_contract_handshake()
                # Local queueing remains available while network is delayed.
                operation = self.store.enqueue(self.fixture.context, "synthetic.txt", "")
                self.assertEqual(operation["status"], "pending")
                self.assertEqual(len(self.client.calls), 1)
            finally:
                release.set()
                thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(self.manager.contract_handshake_is_fresh())

    def test_late_project_response_leaves_new_attempt_available(self):
        def answer():
            self.manager._v2_context_generation += 1
            return SimpleNamespace(data=fixtures.supported_handshake())
        with patch.object(self.client, "_answer", side_effect=answer):
            self.manager._ensure_contract_handshake()
        self.assertIsNone(self.fixture._project()["contract_validated_at"])
        self.manager._ensure_contract_handshake()
        self.assertEqual(len(self.client.calls), 2)
        self.assertTrue(self.manager.contract_handshake_is_fresh())

    def test_permanent_refusal_is_not_retried_on_timer(self):
        for refusal in (RuntimeError("FORBIDDEN"), SyncContractError("CONTRACT_DIGEST_MISMATCH")):
            with self.subTest(refusal=str(refusal)):
                self.manager._forget_contract_handshake()
                self.client.calls.clear()
                self.client.reply = refusal
                for _ in range(10):
                    self.manager._ensure_contract_handshake()
                    self.now += 100
                self.assertEqual(len(self.client.calls), 1)

    def test_retry_delay_is_bounded_and_reset_by_project_switch(self):
        self.client.reply = ConnectionError("synthetic offline")
        for _ in range(10):
            self.manager._ensure_contract_handshake()
            self.assertGreater(self.manager._contract_retry_after, self.now)
            self.assertLessEqual(self.manager._contract_retry_after - self.now, 60)
            self.now += 65
        self.client.reply = fixtures.supported_handshake()
        self.manager._v2_context_generation += 1
        self.manager._ensure_contract_handshake()
        self.assertTrue(self.manager.contract_handshake_is_fresh())

    def sign_in_fake(self, subject):
        session = SimpleNamespace(access_token=fixtures.access_token_with_subject(subject), refresh_token="synthetic")
        auth = unittest.mock.Mock()
        auth.sign_in_with_password.return_value = SimpleNamespace(session=session, user=None)
        self.client.auth = auth
        with patch.object(SyncManager, "cloud_network_enabled", new_callable=unittest.mock.PropertyMock, return_value=True), patch.object(
            self.manager, "_persist_supabase_session"
        ), patch("sync_manager.QTimer.singleShot"):
            success, _ = self.manager.sign_in("synthetic@example.invalid", "synthetic")
        self.client.auth = None
        self.assertTrue(success)

    def test_same_account_relogin_and_direct_account_change_requery(self):
        self.manager._ensure_contract_handshake()
        for subject in (fixtures.DEFAULT_SUBJECT, fixtures.OTHER_PROJECT_ID):
            self.sign_in_fake(subject)
            self.assertFalse(self.manager.contract_handshake_is_fresh())
            self.manager._ensure_contract_handshake()
            self.assertTrue(self.manager.contract_handshake_is_fresh())
        self.assertEqual(len(self.client.calls), 3)

    def test_logout_and_auth_expiry_clear_attempts(self):
        for expire in (False, True):
            self.manager._ensure_contract_handshake()
            with patch("security_manager.SecurityManager.clear_supabase_session"):
                if expire:
                    self.manager._mark_auth_required()
                else:
                    self.manager.sign_out()
            before = len(self.client.calls)
            self.manager._ensure_contract_handshake()
            self.assertEqual(len(self.client.calls), before)
            self.assertIsNone(self.manager._contract_handshake_attempt)
            self.sign_in_fake(fixtures.DEFAULT_SUBJECT)
            self.manager._ensure_contract_handshake()
            self.assertEqual(len(self.client.calls), before + 1)

    def make_document_batch(self):
        # Only this test's temporary database is promoted to exercise the
        # already-existing contract document path. Real project flags stay out.
        for mode in ("MIGRATING", "ID_BASED"):
            self.client.reply = fixtures.supported_handshake(project_sync_mode=mode, migration_epoch=1)
            self.manager.perform_contract_handshake()
        self.manager.enable_contract_path()
        operation = self.store.enqueue(self.manager._v2_context, "synthetic.txt", "")
        return operation, self.store.structure_batch_request(operation["batch_id"])

    def test_auth_rpc_expiry_clears_cache_until_relogin(self):
        self.client.reply = RuntimeError("JWT expired")
        self.manager._ensure_contract_handshake()
        self.assertTrue(self.manager._auth_retry_blocked)
        self.assertIsNone(self.manager._contract_handshake_attempt)
        self.manager._ensure_contract_handshake()
        self.assertEqual(len(self.client.calls), 1)
        self.sign_in_fake(fixtures.DEFAULT_SUBJECT)
        self.client.reply = fixtures.supported_handshake()
        self.manager._ensure_contract_handshake()
        self.assertTrue(self.manager.contract_handshake_is_fresh())

    def test_auth_preparation_timeout_remains_retryable(self):
        self.client.auth = SimpleNamespace(get_session=unittest.mock.Mock(side_effect=TimeoutError()))
        self.manager._ensure_contract_handshake()
        self.assertFalse(self.manager._auth_retry_blocked)
        self.assertIsNone(self.manager._contract_handshake_attempt)
        self.client.auth = None
        self.now += 65
        self.manager._ensure_contract_handshake()
        self.assertTrue(self.manager.contract_handshake_is_fresh())

    def test_late_auth_preparation_cannot_overwrite_relogin(self):
        for fails in (False, True):
            with self.subTest(fails=fails):
                self.manager._forget_contract_handshake()
                old_session = SimpleNamespace(
                    access_token=fixtures.access_token_with_subject(fixtures.DEFAULT_SUBJECT),
                    refresh_token="synthetic",
                )
                def answer():
                    self.sign_in_fake(fixtures.OTHER_PROJECT_ID)
                    if fails:
                        raise RuntimeError("JWT expired")
                    return old_session
                self.client.auth = SimpleNamespace(get_session=answer)
                with patch.object(self.manager, "_persist_supabase_session") as persist:
                    self.manager._ensure_contract_handshake()
                persist.assert_not_called()
                self.assertFalse(self.manager._auth_retry_blocked)
                self.assertEqual(self.client._antigravity_access_token,
                                 fixtures.access_token_with_subject(fixtures.OTHER_PROJECT_ID))
                self.manager._ensure_contract_handshake()
                self.assertTrue(self.manager.contract_handshake_is_fresh())

    def test_missing_fields_are_rejected_by_automatic_lifecycle(self):
        for field in ("contract_version", "canonical_contract_sha256", "supported_protocol_versions"):
            with self.subTest(field=field):
                self.manager._forget_contract_handshake()
                self.client.reply = fixtures.supported_handshake()
                del self.client.reply[field]
                self.manager._ensure_contract_handshake()
                self.assertFalse(self.manager.contract_handshake_is_fresh())
                self.assertIsNone(self.fixture._project()["contract_validated_at"])

    def test_logout_during_retry_wait_cancels_old_attempt(self):
        self.client.reply = ConnectionError("synthetic offline")
        self.manager._ensure_contract_handshake()
        with patch("security_manager.SecurityManager.clear_supabase_session"):
            self.manager.sign_out()
        self.now += 65
        self.manager._ensure_contract_handshake()
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.manager._contract_retry_after, 0)
        self.assertIsNone(self.manager._contract_retry_key)

    def test_document_gate_and_invalid_handshake_prevent_all_rpcs(self):
        operation, request = self.make_document_batch()
        for close_gate in (False, True):
            self.manager._forget_contract_handshake()
            if close_gate:
                self.manager.disable_contract_path()
            self.client.calls.clear()
            result = self.manager._process_v2_operation(operation["operation_id"])
            self.assertEqual(result["kind"], "paused")
            self.assertEqual(self.client.calls, [])
            self.assertEqual(self.store.structure_batch_request(operation["batch_id"]), request)

    def test_periodic_pull_retries_handshake_even_when_queue_blocks_pull(self):
        self.make_batch()
        self.manager._forget_contract_handshake()
        self.client.reply = TimeoutError("synthetic timeout")
        self.manager._ensure_contract_handshake()
        self.client.reply = fixtures.supported_handshake()
        self.now += 65
        actions = []
        def start(action, callback):
            actions.append((action, callback))
            return object()
        with patch.object(self.manager, "_start_server_action", side_effect=start), patch.object(
            self.manager, "_ordinary_pull_gate_is_open", return_value=False
        ):
            for _ in range(20):
                self.assertFalse(self.manager.pull_remote_changes_async(reason="general"))
        self.assertEqual(len(actions), 1)
        actions[0][0]()
        actions[0][1]()
        self.assertTrue(self.manager.contract_handshake_is_fresh())
        self.assertIsNone(self.manager._contract_probe_pending)

    def test_scheduled_probe_is_cancelled_on_logout_or_project_switch(self):
        for logout in (False, True):
            with self.subTest(logout=logout):
                self.sign_in_fake(fixtures.DEFAULT_SUBJECT)
                actions = []
                def start(action, callback):
                    actions.append((action, callback))
                    return object()
                with patch.object(self.manager, "_start_server_action", side_effect=start):
                    self.assertTrue(self.manager.request_contract_handshake_async())
                if logout:
                    with patch("security_manager.SecurityManager.clear_supabase_session"):
                        self.manager.sign_out()
                else:
                    self.manager._v2_context_generation += 1
                self.client.calls.clear()
                actions[0][0]()
                actions[0][1]()
                self.assertEqual(self.client.calls, [])
                self.assertIsNone(self.manager._contract_probe_pending)

    def test_document_preparation_account_change_prevents_transport(self):
        operation, request = self.make_document_batch()
        def change(_client, **_):
            self.client._antigravity_access_token = fixtures.access_token_with_subject(fixtures.OTHER_PROJECT_ID)
        self.client.calls.clear()
        with patch.object(self.manager, "ensure_session_valid", side_effect=change):
            result = self.manager._process_v2_operation(operation["operation_id"])
        self.assertEqual(result["kind"], "paused")
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.store.structure_batch_request(operation["batch_id"]), request)

    def test_late_document_success_records_original_receipt_without_ui_callback(self):
        operation, request = self.make_document_batch()
        response = fixtures.ContractStoreTests._document_success(request)
        workers = []
        callback = unittest.mock.Mock()
        self.manager._v2_callbacks[operation["operation_id"]] = callback
        with patch.object(self.manager, "_start_worker", side_effect=workers.append):
            self.manager._launch_v2_operation(operation)
        def answer():
            self.manager.release_v2()
            return SimpleNamespace(data=response)
        with patch.object(self.client, "_answer", side_effect=answer), patch.object(
            self.manager, "_publish_sync_state"
        ) as publish:
            workers[0].run()
        publish.assert_not_called()
        callback.assert_not_called()
        self.assertEqual(self.store.document_batch_response(operation["batch_id"]), response)
        self.assertEqual(self.store.operation(operation["operation_id"])["status"], "completed")
        self.manager._v2_workers.remove(workers[0])

    def test_repeated_recovery_does_not_duplicate_inflight_transport(self):
        request = self.make_batch()
        self.store.mark_structure_batch_attempt(request["batch"]["batch_id"])
        entered, release = threading.Event(), threading.Event()
        result = []
        def answer():
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test synchronization timeout")
            return SimpleNamespace(data=self.success_response(request))
        self.client.calls.clear()
        with patch.object(self.client, "_answer", side_effect=answer):
            thread = threading.Thread(target=lambda: result.append(
                self.manager._process_contract_structure_batch(request["batch"]["batch_id"])
            ))
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                for _ in range(10):
                    with self.assertRaises(ContractDispatchPaused):
                        self.manager._process_contract_structure_batch(request["batch"]["batch_id"])
                self.assertEqual(len(self.client.calls), 1)
            finally:
                release.set()
                thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(result[0]["applied"])
        self.manager._process_contract_structure_batch(request["batch"]["batch_id"])
        self.assertEqual(len(self.client.calls), 1)

    def test_late_handshake_after_same_account_relogin_is_ignored(self):
        def answer():
            self.sign_in_fake(fixtures.DEFAULT_SUBJECT)
            return SimpleNamespace(data=fixtures.supported_handshake())
        with patch.object(self.client, "_answer", side_effect=answer):
            self.manager._ensure_contract_handshake()
        self.assertFalse(self.manager.contract_handshake_is_fresh())
        self.assertIsNone(self.fixture._project()["contract_validated_at"])
        self.manager._ensure_contract_handshake()
        self.assertEqual(len(self.client.calls), 2)

    def test_late_unsupported_reply_does_not_suppress_new_project_probe(self):
        def answer():
            self.manager._v2_context_generation += 1
            return SimpleNamespace(data=fixtures.unsupported_handshake())
        with patch.object(self.client, "_answer", side_effect=answer):
            self.manager._ensure_contract_handshake()
        self.assertIsNone(self.manager._contract_handshake)
        self.manager._ensure_contract_handshake()
        self.assertEqual(len(self.client.calls), 2)

    def test_invalid_batch_contract_metadata_starts_no_rpc(self):
        request = self.make_batch()
        for field, value in (("contract_version", "wrong"), ("sync_protocol_version", 2),
                             ("canonical_contract_sha256", "0" * 64), ("client_capabilities", [])):
            with self.subTest(field=field):
                changed = copy.deepcopy(request)
                changed["batch"][field] = value
                self.client.calls.clear()
                with self.assertRaises(ContractDispatchPaused):
                    self.manager._send_contract_request("atomic_structure_commit", changed,
                                                       self.manager._contract_dispatch_context())
                self.assertEqual(self.client.calls, [])


if __name__ == "__main__":
    unittest.main()
