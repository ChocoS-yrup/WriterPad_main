import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sync_manager import SyncManager
from writing_controller import WritingController


class _Auth:
    def __init__(self):
        self.session = SimpleNamespace(
            access_token="access-1",
            refresh_token="refresh-1",
            user=SimpleNamespace(email="writer@example.com"),
        )
        self.get_calls = 0
        self.refresh_calls = 0

    def get_session(self):
        self.get_calls += 1
        return self.session

    def refresh_session(self):
        self.refresh_calls += 1
        self.session = SimpleNamespace(
            access_token=f"access-{self.refresh_calls + 1}",
            refresh_token=f"refresh-{self.refresh_calls + 1}",
            user=SimpleNamespace(email="writer@example.com"),
        )
        return SimpleNamespace(session=self.session)


class _Client:
    def __init__(self, auth=None):
        self.auth = auth or _Auth()
        self._antigravity_authenticated = True


class SyncResilienceTestCase(unittest.TestCase):
    def setUp(self):
        self.manager = SyncManager()
        self.previous = (
            self.manager.supabase,
            self.manager._auth_retry_blocked,
            self.manager._auth_refresh_generation,
            self.manager._shutting_down,
        )
        self.manager._auth_retry_blocked = False
        self.manager._auth_refresh_generation = 0
        self.manager._shutting_down = False

    def tearDown(self):
        (
            self.manager.supabase,
            self.manager._auth_retry_blocked,
            self.manager._auth_refresh_generation,
            self.manager._shutting_down,
        ) = self.previous

    def test_expired_jwt_refreshes_once_persists_tokens_and_retries_call(self):
        client = _Client()
        attempts = []

        def action():
            attempts.append("call")
            if len(attempts) == 1:
                raise RuntimeError("PGRST303: JWT expired")
            return "ok"

        with patch.object(
            SyncManager, "_persist_supabase_session", return_value=True
        ) as persist:
            result = self.manager._call_with_session(action, client)

        self.assertEqual(result, "ok")
        self.assertEqual(attempts, ["call", "call"])
        self.assertEqual(client.auth.refresh_calls, 1)
        self.assertGreaterEqual(persist.call_count, 2)
        self.assertFalse(self.manager._auth_retry_blocked)

    def test_second_auth_failure_opens_circuit_and_stops_queue_retry(self):
        client = _Client()

        with patch.object(
            SyncManager, "_persist_supabase_session", return_value=True
        ), self.assertRaisesRegex(RuntimeError, "AUTH_REQUIRED"):
            self.manager._call_with_session(
                lambda: (_ for _ in ()).throw(RuntimeError("JWT expired")),
                client,
            )

        self.assertTrue(self.manager._auth_retry_blocked)
        with patch.object(self.manager, "_publish_sync_state") as publish:
            self.assertFalse(self.manager.retry_pending_syncs())
        publish.assert_called_once_with()

    def test_manual_retry_does_not_bypass_login_required_circuit(self):
        self.manager._auth_retry_blocked = True
        self.manager._v2_retry_timer.start(60000)

        with patch.object(self.manager, "_publish_sync_state") as publish, \
                patch.object(self.manager, "_launch_v2_operation") as launch:
            self.assertFalse(self.manager.retry_pending_syncs(manual=True))

        publish.assert_called_once_with()
        launch.assert_not_called()

    def test_concurrent_forced_refresh_is_single_flight(self):
        entered_refresh = threading.Event()
        release_refresh = threading.Event()

        class BlockingAuth(_Auth):
            def refresh_session(self):
                self.refresh_calls += 1
                entered_refresh.set()
                release_refresh.wait(2)
                return SimpleNamespace(session=self.session)

        client = _Client(BlockingAuth())
        errors = []

        def refresh():
            try:
                self.manager.ensure_session_valid(client, force_refresh=True)
            except Exception as error:
                errors.append(error)

        with patch.object(
            SyncManager, "_persist_supabase_session", return_value=True
        ):
            first = threading.Thread(target=refresh)
            second = threading.Thread(target=refresh)
            first.start()
            self.assertTrue(entered_refresh.wait(1))
            second.start()
            release_refresh.set()
            first.join(2)
            second.join(2)

        self.assertEqual(errors, [])
        self.assertEqual(client.auth.refresh_calls, 1)
        self.assertEqual(client.auth.get_calls, 1)

    def test_cloud_queue_failure_does_not_reopen_local_dirty_state(self):
        path = "메인/원고/1권/015화.txt"
        wpm = MagicMock()
        wpm.write_text_file.return_value = True
        sync_manager = MagicMock()
        sync_manager.can_save_path.return_value = True
        sync_manager.would_erase_nonempty_document.return_value = False
        sync_manager.upload_content_async.side_effect = RuntimeError("queue busy")
        persisted = MagicMock()
        controller = WritingController(
            wpm,
            sync_manager,
            SimpleNamespace(current_project="작품"),
            "device",
            lambda: [path],
            lambda requested: "안전하게 저장된 본문" if requested == path else None,
            persisted,
        )
        controller.pending_autosave_paths.add(path)

        controller.sync_file()

        wpm.write_text_file.assert_called_once_with(path, "안전하게 저장된 본문")
        persisted.assert_called_once_with(path, "안전하게 저장된 본문", True)
        sync_manager.report_server_queue_failure.assert_called_once()
        self.assertNotIn(path, controller.pending_autosave_paths)
        self.assertFalse(controller.idle_timer.isActive())

    def test_shutdown_closes_shared_http_pool_after_workers(self):
        http_client = MagicMock()
        client = SimpleNamespace(_antigravity_httpx_client=http_client)
        self.manager.supabase = client

        self.manager.shutdown()

        self.assertTrue(self.manager._shutting_down)
        self.assertFalse(self.manager._v2_retry_timer.isActive())
        http_client.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
