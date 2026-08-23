import base64
import contextlib
import io
import json
import socket
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cloud_config import classify_cloud_error
from sync_manager import SyncManager
from writing_controller import WritingController


class _Keyring:
    """A stand-in credential store. The real one is never touched here."""

    def __init__(self, access="access-stored", refresh="refresh-stored"):
        self.access = access
        self.refresh = refresh
        self.clears = 0

    def get_supabase_session(self):
        return (self.access, self.refresh)

    def clear_supabase_session(self):
        self.clears += 1
        self.access = ""
        self.refresh = ""

    def save_supabase_session(self, access, refresh):
        self.access = access
        self.refresh = refresh

    @property
    def present(self):
        return bool(self.access and self.refresh)


def _http_error(status_code, message):
    error = RuntimeError(message)
    error.status_code = status_code
    return error


def _access_token(tag):
    """A token shaped like the real one. Its claims are never what orders it."""
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": 1000}).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"header.{payload}.{tag}"


def _session(tag):
    return SimpleNamespace(
        access_token=_access_token(tag),
        refresh_token=f"refresh-{tag}",
        user=SimpleNamespace(email="writer@example.com"),
    )


class StoredSessionLineageTestCase(unittest.TestCase):
    """One credential slot, several clients, each rotating on its own clock.

    Whichever write lands last wins the slot. Ordering those writes by anything
    the token says about time does not work: the server issues every access
    token with the same lifetime, so two rotations a moment apart carry an
    identical expiry and the later write takes the tie regardless of which
    session is live. A client may replace exactly the token it was handed, and
    nothing else.
    """

    def setUp(self):
        self.generation = SyncManager._session_generation

    def tearDown(self):
        SyncManager._session_generation = self.generation

    def _persist(self, stored, incoming, **kwargs):
        keyring = _Keyring(access=stored[0], refresh=stored[1])
        with patch("security_manager.SecurityManager", keyring):
            written = SyncManager._persist_supabase_session(incoming, **kwargs)
        return written, keyring

    def test_two_rotations_sharing_an_expiry_are_still_ordered(self):
        """The case an expiry comparison cannot see, and the one that bit us."""
        self.assertEqual(
            _access_token("t1").split(".")[1], _access_token("t2").split(".")[1]
        )
        written, keyring = self._persist(
            ("access-current", "refresh-t2"),
            _session("t1"),
            expected_previous="refresh-t0",
        )
        self.assertFalse(written)
        self.assertEqual(keyring.refresh, "refresh-t2")

    def test_a_client_may_replace_exactly_the_token_it_was_given(self):
        written, keyring = self._persist(
            ("access-current", "refresh-t1"),
            _session("t2"),
            expected_previous="refresh-t1",
        )
        self.assertTrue(written)
        self.assertEqual(keyring.refresh, "refresh-t2")

    def test_the_same_rotation_arriving_twice_is_idempotent(self):
        written, keyring = self._persist(
            ("access-current", "refresh-t2"),
            _session("t2"),
            expected_previous="refresh-t1",
        )
        self.assertTrue(written)
        self.assertEqual(keyring.refresh, "refresh-t2")

    def test_callbacks_arriving_newest_first_leave_the_newest_stored(self):
        """Reverse the order and the newest still has to be what survives."""
        keyring = _Keyring(access="access-0", refresh="refresh-t0")
        deliveries = [
            (_session("t3"), "refresh-t2"),
            (_session("t2"), "refresh-t1"),
            (_session("t1"), "refresh-t0"),
        ]
        with patch("security_manager.SecurityManager", keyring):
            # The newest lands first because its predecessor is not what is
            # stored; then the older ones arrive and must all be refused.
            SyncManager._persist_supabase_session(
                deliveries[0][0], expected_previous="refresh-t0"
            )
            for session, previous in deliveries[1:]:
                SyncManager._persist_supabase_session(
                    session, expected_previous=previous
                )
        self.assertEqual(keyring.refresh, "refresh-t3")

    def test_a_client_from_before_a_new_sign_in_cannot_write(self):
        stale_generation = SyncManager._session_generation
        SyncManager._session_generation += 1
        written, keyring = self._persist(
            ("access-current", "refresh-new-login"),
            _session("stale"),
            generation=stale_generation,
        )
        self.assertFalse(written)
        self.assertEqual(keyring.refresh, "refresh-new-login")

    def test_a_client_from_the_current_generation_may_write(self):
        written, keyring = self._persist(
            ("access-current", "refresh-t1"),
            _session("t2"),
            expected_previous="refresh-t1",
            generation=SyncManager._session_generation,
        )
        self.assertTrue(written)
        self.assertEqual(keyring.refresh, "refresh-t2")

    def test_the_first_write_into_an_empty_store_lands(self):
        written, keyring = self._persist(
            ("", ""), _session("first"), expected_previous="refresh-whatever"
        )
        self.assertTrue(written)
        self.assertEqual(keyring.refresh, "refresh-first")

    def test_a_sign_in_with_no_predecessor_outranks_what_is_stored(self):
        written, keyring = self._persist(
            ("access-current", "refresh-someone-elses"), _session("fresh-login")
        )
        self.assertTrue(written)
        self.assertEqual(keyring.refresh, "refresh-fresh-login")

    def test_a_session_missing_either_half_is_not_written(self):
        for label, session in (
            ("no access", SimpleNamespace(access_token="", refresh_token="r")),
            ("no refresh", SimpleNamespace(access_token="a", refresh_token="")),
        ):
            with self.subTest(session=label):
                written, keyring = self._persist(
                    ("access-current", "refresh-keep"), session
                )
                self.assertFalse(written)
                self.assertEqual(keyring.refresh, "refresh-keep")

    def test_shutdown_waits_between_the_server_answering_and_the_write(self):
        """The gap the store lock cannot see, and the one that loses tokens.

        The server retires the old token the moment it answers. Everything up to
        the new one being written down is a window where closing leaves a spent
        token behind, so shutdown has to wait on the exchange, not on the write.
        """
        answered = threading.Event()
        release = threading.Event()
        observed = {}

        def exchange():
            with SyncManager._session_exchange_in_flight():
                # The server has answered and retired the previous token here.
                answered.set()
                release.wait(2.0)
                # The write would happen here.

        worker = threading.Thread(target=exchange)
        worker.start()
        self.assertTrue(answered.wait(1.0))
        observed["while_in_flight"] = SyncManager.await_session_writes(
            timeout_ms=100
        )
        release.set()
        worker.join()
        observed["after_it_landed"] = SyncManager.await_session_writes(
            timeout_ms=500
        )

        self.assertEqual(observed["while_in_flight"], False)
        self.assertEqual(observed["after_it_landed"], True)

    def test_shutdown_returns_at_once_when_nothing_is_in_flight(self):
        self.assertTrue(SyncManager.await_session_writes(timeout_ms=50))

    def test_overlapping_exchanges_all_have_to_finish(self):
        release = threading.Event()
        started = threading.Semaphore(0)

        def exchange():
            with SyncManager._session_exchange_in_flight():
                started.release()
                release.wait(2.0)

        workers = [threading.Thread(target=exchange) for _ in range(3)]
        for worker in workers:
            worker.start()
        for _ in workers:
            self.assertTrue(started.acquire(timeout=1.0))

        self.assertFalse(SyncManager.await_session_writes(timeout_ms=100))
        release.set()
        for worker in workers:
            worker.join()
        self.assertTrue(SyncManager.await_session_writes(timeout_ms=500))

    def test_persisting_from_inside_a_restore_does_not_deadlock(self):
        """The restore holds the lock while the session it produced is stored."""
        keyring = _Keyring(access="access-current", refresh="refresh-t1")
        with patch("security_manager.SecurityManager", keyring):
            with SyncManager._session_restore_lock:
                written = SyncManager._persist_supabase_session(
                    _session("t2"), expected_previous="refresh-t1"
                )
        self.assertTrue(written)
        self.assertEqual(keyring.refresh, "refresh-t2")


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


class SessionRestoreTestCase(unittest.TestCase):
    """A stored session outlives everything except the server refusing it.

    Deleting it on any failed restore turns a dropped connection into a logout
    the writer has to undo by hand, and it takes the refresh token along, so
    nothing can recover on its own afterwards.
    """

    def setUp(self):
        self.keyring = _Keyring()

    def _discard(self, error, offered=("access-stored", "refresh-stored")):
        with patch("security_manager.SecurityManager", self.keyring):
            return SyncManager._discard_rejected_session(
                classify_cloud_error(error), offered[0], offered[1]
            )

    def test_transient_failures_keep_the_session(self):
        transient = {
            "dns": socket.gaierror("getaddrinfo failed"),
            "timeout": TimeoutError("request timed out"),
            "server_500": _http_error(500, "server error"),
            "server_502": _http_error(502, "bad gateway"),
            "server_503": _http_error(503, "service unavailable"),
            "connection_reset": ConnectionResetError("connection reset by peer"),
            "unrecognized": RuntimeError("something else entirely"),
        }
        for label, error in transient.items():
            with self.subTest(failure=label):
                self.keyring = _Keyring()
                self.assertFalse(self._discard(error))
                self.assertEqual(self.keyring.clears, 0)
                self.assertTrue(self.keyring.present)

    def test_a_refusal_from_the_server_discards_the_session(self):
        refusals = {
            "400": _http_error(400, "Invalid Refresh Token"),
            "401": _http_error(401, "invalid credentials"),
            "message_only": RuntimeError("Invalid login credentials"),
        }
        for label, error in refusals.items():
            with self.subTest(refusal=label):
                self.keyring = _Keyring()
                self.assertTrue(self._discard(error))
                self.assertEqual(self.keyring.clears, 1)
                self.assertFalse(self.keyring.present)

    def test_a_refusal_of_an_already_rotated_token_keeps_the_session(self):
        """Two clients starting together is the ordinary case, not a logout.

        Refresh tokens rotate on use. The second restore is told its token is
        invalid, and it is, but only because the first one already exchanged it
        for the pair now sitting in the store.
        """
        self.keyring = _Keyring(access="access-rotated", refresh="refresh-rotated")
        discarded = self._discard(
            _http_error(400, "Invalid Refresh Token: Already Used"),
            offered=("access-stored", "refresh-stored"),
        )
        self.assertFalse(discarded)
        self.assertEqual(self.keyring.clears, 0)
        self.assertEqual(
            (self.keyring.access, self.keyring.refresh),
            ("access-rotated", "refresh-rotated"),
        )

    def test_a_credential_store_that_cannot_be_read_discards_nothing(self):
        class _Unreadable(_Keyring):
            def get_supabase_session(self):
                raise OSError("credential store unavailable")

        self.keyring = _Unreadable()
        self.assertFalse(self._discard(_http_error(401, "invalid credentials")))
        self.assertEqual(self.keyring.clears, 0)


class SingleRefreshAuthorityTestCase(unittest.TestCase):
    """One credential, several clients, and only one of them may rotate it.

    Five clients refreshing on five schedules against one credential means every
    rotation retires the token the other four are holding, and each of them then
    discovers it is signed out. Ordering the writes keeps the store correct; it
    does not stop the clients from invalidating each other.
    """

    def setUp(self):
        self.manager = SyncManager()
        self.manager._auth_retry_blocked = False
        self.rotations = []

    def _client(self, held, stored_access="access-current"):
        rotations = self.rotations

        def refresh_session():
            rotations.append("refresh")
            return SimpleNamespace(session=SimpleNamespace(
                access_token="access-rotated", refresh_token="refresh-rotated",
                user=SimpleNamespace(email="writer@example.com"),
            ))

        def set_session(access, refresh):
            rotations.append("adopt")
            return SimpleNamespace(session=SimpleNamespace(
                access_token=access, refresh_token=refresh,
                user=SimpleNamespace(email="writer@example.com"),
            ))

        client = SimpleNamespace(
            auth=SimpleNamespace(
                refresh_session=refresh_session,
                set_session=set_session,
                get_session=lambda: SimpleNamespace(session=None),
            ),
            _antigravity_authenticated=False,
            _antigravity_refresh_token=held,
            _antigravity_session_generation=SyncManager._session_generation,
        )
        return client

    def test_a_worker_takes_the_session_somebody_else_refreshed(self):
        """Adopting costs one local exchange; refreshing costs everyone else."""
        keyring = _Keyring(access="access-newer", refresh="refresh-newer")
        client = self._client(held="refresh-mine")
        with patch("security_manager.SecurityManager", keyring):
            self.manager.ensure_session_valid(client, force_refresh=True)

        self.assertEqual(self.rotations, ["adopt"])
        self.assertTrue(client._antigravity_authenticated)
        self.assertEqual(client._antigravity_refresh_token, "refresh-newer")
        # Nothing was rotated, so the store still holds what it held.
        self.assertEqual(keyring.refresh, "refresh-newer")

    def test_a_worker_already_holding_the_stored_session_refreshes(self):
        """Nothing to adopt means this really is the client that has to rotate."""
        keyring = _Keyring(access="access-current", refresh="refresh-mine")
        client = self._client(held="refresh-mine")
        with patch("security_manager.SecurityManager", keyring):
            self.manager.ensure_session_valid(client, force_refresh=True)

        self.assertEqual(self.rotations, ["refresh"])
        self.assertEqual(keyring.refresh, "refresh-rotated")
        self.assertEqual(client._antigravity_refresh_token, "refresh-rotated")

    def test_no_client_refreshes_on_a_timer_of_its_own(self):
        """A timer inside the library is neither serialized nor waited for."""
        captured = {}

        def client_options(**kwargs):
            captured.update(kwargs)
            return None

        supabase = SimpleNamespace(
            create_client=lambda *a, **k: SimpleNamespace(
                auth=SimpleNamespace(
                    set_session=MagicMock(side_effect=TimeoutError("timed out")),
                    on_auth_state_change=MagicMock(),
                )
            ),
            ClientOptions=client_options,
        )
        config = SimpleNamespace(
            is_ready=True, url="https://example.invalid", publishable_key="pk"
        )
        with patch("security_manager.SecurityManager", _Keyring()),                 patch.dict("sys.modules", {"supabase": supabase}):
            SyncManager.create_supabase_client(config)

        self.assertIs(captured.get("auto_refresh_token"), False)


class SessionRestoreThroughTheClientTestCase(unittest.TestCase):
    """The same rule, exercised through create_supabase_client itself."""

    def setUp(self):
        self.keyring = _Keyring()

    @contextlib.contextmanager
    def _supabase(self, auth):
        """Patch what create_supabase_client reaches, once, on this thread.

        Applying the patches inside each worker would have them installing and
        removing one another's mocks, which is a different race than the one
        under test.
        """
        config = SimpleNamespace(
            is_ready=True, url="https://example.invalid", publishable_key="pk"
        )

        def persist(session, expected_previous=None, generation=None):
            self.keyring.save_supabase_session(
                session.access_token, session.refresh_token
            )
            return True

        supabase = SimpleNamespace(
            create_client=lambda *args, **kwargs: SimpleNamespace(auth=auth),
            ClientOptions=lambda **kwargs: None,
        )
        with patch("security_manager.SecurityManager", self.keyring), \
                patch.object(
                    SyncManager, "_persist_supabase_session", staticmethod(persist)
                ), \
                patch.dict("sys.modules", {"supabase": supabase}):
            yield lambda: SyncManager.create_supabase_client(config)

    def test_a_timeout_leaves_the_writer_signed_in(self):
        auth = SimpleNamespace(
            set_session=MagicMock(side_effect=TimeoutError("timed out")),
            on_auth_state_change=MagicMock(),
        )
        printed = io.StringIO()
        with self._supabase(auth) as create:
            with contextlib.redirect_stdout(printed):
                client = create()

        self.assertIsNotNone(client)
        self.assertFalse(client._antigravity_authenticated)
        self.assertEqual(client._antigravity_restore_error_kind, "timeout")
        # The session is still there, so the next attempt can recover by itself.
        self.assertEqual(self.keyring.clears, 0)
        self.assertTrue(self.keyring.present)
        # And a kept session that could not be restored says so, or somebody is
        # left looking at a signed-out app with nothing to read.
        self.assertIn("timeout", printed.getvalue())
        self.assertIn("kept", printed.getvalue())

    def test_a_refused_token_signs_the_writer_out(self):
        auth = SimpleNamespace(
            set_session=MagicMock(side_effect=_http_error(401, "invalid credentials")),
            on_auth_state_change=MagicMock(),
        )
        with self._supabase(auth) as create:
            client = create()

        self.assertFalse(client._antigravity_authenticated)
        self.assertEqual(client._antigravity_restore_error_kind, "authentication")
        self.assertEqual(self.keyring.clears, 1)
        self.assertFalse(self.keyring.present)

    def test_a_restored_session_is_persisted_and_reported(self):
        session = SimpleNamespace(
            access_token="access-new", refresh_token="refresh-new",
            user=SimpleNamespace(email="writer@example.com"),
        )
        auth = SimpleNamespace(
            set_session=MagicMock(return_value=SimpleNamespace(session=session)),
            on_auth_state_change=MagicMock(),
        )
        with self._supabase(auth) as create:
            client = create()

        self.assertTrue(client._antigravity_authenticated)
        self.assertEqual(client._antigravity_email, "writer@example.com")
        self.assertEqual(
            (self.keyring.access, self.keyring.refresh),
            ("access-new", "refresh-new"),
        )
        self.assertEqual(self.keyring.clears, 0)

    def test_clients_starting_together_do_not_race_each_other_out(self):
        """Four restores at once, against a server that rotates on every use.

        Unserialized, they all read the same refresh token, all spend it, and
        every one after the first is refused and signs the writer out.
        """
        # What the server will still accept. It is not the credential store:
        # the exchange rotates this the instant it happens, well before the new
        # pair has been written down anywhere.
        accepted = {"refresh": self.keyring.refresh}
        exchanges = []
        held = threading.Event()

        def set_session(access, refresh):
            if refresh != accepted["refresh"]:
                raise _http_error(400, "Invalid Refresh Token: Already Used")
            index = len(exchanges) + 1
            accepted["refresh"] = f"refresh-{index}"
            exchanges.append(refresh)
            if not held.is_set():
                # Hold the winner inside the exchange long enough that an
                # unserialized second caller reads the spent token and is
                # refused.
                held.set()
                time.sleep(0.05)
            return SimpleNamespace(session=SimpleNamespace(
                access_token=f"access-{index}", refresh_token=f"refresh-{index}",
                user=SimpleNamespace(email="writer@example.com"),
            ))

        auth = SimpleNamespace(
            set_session=set_session, on_auth_state_change=MagicMock()
        )
        results = []
        with self._supabase(auth) as create:
            threads = [
                threading.Thread(target=lambda: results.append(create()))
                for _ in range(4)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(len(results), 4)
        for index, client in enumerate(results):
            with self.subTest(client=index):
                self.assertTrue(client._antigravity_authenticated)
        self.assertEqual(len(exchanges), 4)
        self.assertEqual(self.keyring.clears, 0)
        self.assertTrue(self.keyring.present)


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
        self.manager.reset_shutdown_state()

    def tearDown(self):
        (
            self.manager.supabase,
            self.manager._auth_retry_blocked,
            self.manager._auth_refresh_generation,
            self.manager._shutting_down,
        ) = self.previous
        # 싱글턴이므로 종료 예산을 남겨두면 이후 테스트의 worker wait 이 0ms 가 된다.
        self.manager.reset_shutdown_state()

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
        timer = MagicMock()
        timer.isActive.return_value = False
        with patch("writing_controller.QTimer", return_value=timer):
            controller = WritingController(
                wpm,
                sync_manager,
                SimpleNamespace(current_project="작품"),
                "device",
                lambda: [path],
                lambda requested: (
                    "안전하게 저장된 본문" if requested == path else None
                ),
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
