"""Offline reproduction of the deployed network-block/reconnect scenario."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tests import test_handshake_stability as stability
from tests import test_sync_resilience as resilience
from sync_manager import SyncManager, V2PullWorker, STRUCTURE_AUTHORITY_BLOCKED, STRUCTURE_AUTHORITY_LEGACY
from handshake_lifecycle import ContractDispatchPaused


class Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in self.callbacks:
            callback(*args)


class Pull:
    def __init__(self, manager, project_id=None):
        self.resultReady, self.finished = Signal(), Signal()

    def isRunning(self):
        return True

    def deleteLater(self):
        pass


class NetworkRecoveryTests(unittest.TestCase):
    def setUp(self):
        stability.HandshakeStabilityTests.setUp(self)
        self.manager._session_recovery_ticket = None
        self.manager._contract_probe_pending = None
        self.manager._v2_last_pull_apply_blocked = False
        for name in ('request_contract_handshake_async', '_start_worker'):
            mocked = patch.object(self.manager, name)
            mocked.start()
            self.addCleanup(mocked.stop)

    def start_pull(self):
        with patch('sync_manager.V2PullWorker', Pull), patch.object(
            self.manager, '_start_worker'
        ), patch.object(self.manager, 'request_contract_handshake_async'):
            return self.manager.pull_remote_changes_async()

    def test_timeout_retries_same_project_after_delay(self):
        self.assertTrue(self.start_pull())
        worker = self.manager._v2_pull_worker
        worker.resultReady.emit(False, TimeoutError('synthetic'))
        worker.finished.emit()
        self.assertNotEqual(self.manager._v2_structure_authority, STRUCTURE_AUTHORITY_BLOCKED)
        self.assertFalse(self.manager._structure_authority_allows_dispatch())
        self.assertFalse(self.start_pull())
        self.now += 3
        self.assertTrue(self.start_pull())
        self.assertFalse(self.start_pull())

    def test_invalid_structure_remains_blocked(self):
        self.assertTrue(self.start_pull())
        worker = self.manager._v2_pull_worker
        worker.resultReady.emit(False, 'INVALID_TREE_ORDER_RESPONSE')
        worker.finished.emit()
        self.now += 120
        self.assertFalse(self.start_pull())
        self.assertEqual(self.manager._v2_structure_authority, STRUCTURE_AUTHORITY_BLOCKED)

    def test_pending_restore_is_network_wait_not_auth_required(self):
        self.client.auth = SimpleNamespace(get_session=lambda: None)
        self.client._antigravity_restore_pending = True
        self.client._antigravity_authenticated = False
        with self.assertRaisesRegex(RuntimeError, 'NETWORK_UNAVAILABLE'):
            self.manager.ensure_session_valid()
        self.assertFalse(self.manager._auth_retry_blocked)

    def test_wrapped_transport_error_is_retryable(self):
        wrapped = RuntimeError('auth transport wrapper')
        wrapped.__cause__ = ConnectionError('synthetic')
        self.assertTrue(SyncManager._transient_handshake_error(wrapped))

    def test_http_refusal_does_not_retry_an_attached_timeout(self):
        error = RuntimeError('synthetic refusal')
        error.status_code = 403
        error.__cause__ = TimeoutError()
        self.assertFalse(SyncManager._transient_handshake_error(error))

    def test_worker_preserves_transport_exception(self):
        error = TimeoutError('synthetic')
        worker = V2PullWorker(self.manager)
        results = []
        worker.resultReady.connect(lambda ok, result: results.append((ok, result)))
        with patch.object(self.manager, '_ensure_contract_handshake', side_effect=error):
            worker.run()
        self.assertEqual(results, [(False, error)])

    def test_recovery_preserves_closed_gate_batch_and_payload(self):
        request = stability.HandshakeStabilityTests.make_batch(self)
        self.manager.disable_contract_path()
        self.assertTrue(self.start_pull())
        worker = self.manager._v2_pull_worker
        worker.resultReady.emit(False, ConnectionError())
        worker.finished.emit()
        with patch.object(self.manager, '_launch_contract_structure_batch') as launch:
            self.manager.retry_pending_syncs()
            launch.assert_not_called()
        self.now += 65
        self.assertTrue(self.start_pull())
        self.assertEqual(self.store.structure_batch_request(request['batch']['batch_id']), request)
        self.assertFalse(self.fixture._project()['contract_path_enabled'])

    def test_late_pull_after_account_change_is_ignored(self):
        self.assertTrue(self.start_pull())
        worker = self.manager._v2_pull_worker
        self.client._antigravity_access_token = stability.fixtures.access_token_with_subject(
            stability.fixtures.OTHER_PROJECT_ID
        )
        with patch.object(self.manager, '_block_structure_authority') as block:
            worker.resultReady.emit(False, 'INVALID_TREE_ORDER_RESPONSE')
            block.assert_not_called()

    def test_project_change_discards_network_backoff(self):
        self.assertTrue(self.start_pull())
        worker = self.manager._v2_pull_worker
        worker.resultReady.emit(False, TimeoutError())
        worker.finished.emit()
        self.manager._v2_context_generation += 1
        self.assertTrue(self.start_pull())

    def test_successful_baseline_clears_network_wait(self):
        self.assertTrue(self.start_pull())
        worker = self.manager._v2_pull_worker
        worker.resultReady.emit(False, TimeoutError())
        worker.finished.emit()
        self.now += 3
        self.assertTrue(self.start_pull())
        with patch.object(self.manager, '_apply_v2_remote_documents', return_value=[]), patch.object(
            self.manager, '_identity_audit_is_clean', return_value=True
        ), patch.object(self.manager, '_recover_untracked_local_files_after_pull', return_value=0):
            self.manager._v2_pull_worker.resultReady.emit(True, {
                'documents': [], 'folders': [], 'tree_orders': [], 'folder_versions': [],
                'structure_authority': {'kind': STRUCTURE_AUTHORITY_LEGACY, 'folder_paths': []},
            })
        self.assertFalse(self.manager._current_pull_coordinator().get('network_retry_after'))
        self.assertTrue(self.manager._structure_authority_allows_dispatch())

    def prepare_restore(self):
        self.client._antigravity_restore_pending = True
        self.client._antigravity_authenticated = False
        self.actions = []
        start = patch.object(self.manager, '_start_server_action', side_effect=lambda action, callback: (
            self.actions.append((action, callback)) or object()
        ))
        start.start()
        self.addCleanup(start.stop)
        self.session = SimpleNamespace(
            access_token=stability.fixtures.access_token_with_subject(stability.fixtures.DEFAULT_SUBJECT),
            refresh_token='synthetic-new', user=SimpleNamespace(email='synthetic@example.invalid'),
        )
        self.restored_auth = SimpleNamespace(
            set_session=MagicMock(return_value=SimpleNamespace(session=self.session)),
            on_auth_state_change=MagicMock(),
        )
        self.restored_clients = []
        def create(*args, **kwargs):
            self.assertFalse(kwargs['restore_session'])
            fresh = SimpleNamespace(
                auth=self.restored_auth, _antigravity_session_generation=SyncManager._session_generation,
                _antigravity_httpx_client=SimpleNamespace(close=MagicMock()),
            )
            self.restored_clients.append(fresh)
            return fresh
        for target, kwargs in (
            ('create_supabase_client', {'side_effect': create}),
            ('_persist_supabase_session', {'return_value': True}),
        ):
            mocked = patch.object(self.manager, target, **kwargs)
            mocked.start()
            self.addCleanup(mocked.stop)
        credentials = patch('security_manager.SecurityManager.get_supabase_session',
                            return_value=('synthetic-access', 'synthetic-refresh'))
        credentials.start()
        self.addCleanup(credentials.stop)

    def finish_restore(self):
        action, callback = self.actions[-1]
        try:
            result = action()
        except Exception as error:
            callback(False, error)
        else:
            callback(True, result)

    def test_restore_backoff_singleflight_and_same_project_recovery(self):
        self.prepare_restore()
        self.restored_auth.set_session.side_effect = TimeoutError()
        self.assertTrue(self.manager.request_session_recovery_async())
        self.restored_auth.set_session.assert_not_called()  # UI entry does no I/O
        for _ in range(20):
            self.assertFalse(self.manager.request_session_recovery_async())
        self.finish_restore()
        self.assertFalse(self.manager._auth_retry_blocked)
        for _ in range(20):
            self.assertFalse(self.manager.request_session_recovery_async())
        self.assertEqual(self.restored_auth.set_session.call_count, 1)
        self.now += 3
        self.restored_auth.set_session.side_effect = None
        self.assertTrue(self.manager.request_session_recovery_async())
        with patch.object(self.manager, 'pull_remote_changes_async') as pull:
            self.finish_restore()
            pull.assert_called_once_with(reason='baseline')
        self.assertIsNot(self.manager.supabase, self.client)
        self.assertTrue(self.manager.supabase._antigravity_authenticated)
        self.assertFalse(self.manager._structure_authority_allows_dispatch())
        self.assertFalse(self.manager.request_session_recovery_async())
        self.assertFalse(self.fixture._project()['contract_path_enabled'])

    def test_restore_refusal_requires_login_without_automatic_loop(self):
        self.prepare_restore()
        error = RuntimeError('synthetic refusal')
        error.status = 401
        self.restored_auth.set_session.side_effect = error
        self.manager.request_session_recovery_async()
        self.finish_restore()
        self.now += 120
        self.assertTrue(self.manager._auth_retry_blocked)
        self.assertFalse(self.manager.request_session_recovery_async())

    def test_queued_restore_cancelled_by_logout_before_io(self):
        self.prepare_restore()
        self.manager.request_session_recovery_async()
        with patch('security_manager.SecurityManager.clear_supabase_session'):
            self.manager.sign_out()
        self.finish_restore()
        self.restored_auth.set_session.assert_not_called()
        self.assertTrue(self.manager._auth_retry_blocked)

    def test_late_restore_does_not_resurrect_logout_or_replace_project(self):
        for logout in (False, True):
            with self.subTest(logout=logout):
                self.manager._auth_retry_blocked = False
                self.prepare_restore()
                def late(*_):
                    if logout:
                        with patch('security_manager.SecurityManager.clear_supabase_session'):
                            self.manager.sign_out()
                    else:
                        self.manager._v2_context_generation += 1
                    return SimpleNamespace(session=self.session)
                self.restored_auth.set_session.side_effect = late
                self.manager.request_session_recovery_async()
                self.finish_restore()
                self.assertIs(self.manager.supabase, self.client)
                self.manager._persist_supabase_session.assert_not_called()
                self.restored_clients[-1]._antigravity_httpx_client.close.assert_called_once()

    def test_completed_restore_is_discarded_if_account_changes_before_callback(self):
        self.prepare_restore()
        self.manager.request_session_recovery_async()
        action, callback = self.actions[-1]
        restored = action()
        stability.HandshakeStabilityTests.sign_in_fake(self, stability.fixtures.OTHER_PROJECT_ID)
        callback(True, restored)
        self.assertIs(self.manager.supabase, self.client)
        restored._antigravity_httpx_client.close.assert_called_once()

    def test_factory_timeout_preserves_recovery_intent(self):
        fixture = resilience.SessionRestoreThroughTheClientTestCase()
        fixture.setUp()
        auth = SimpleNamespace(set_session=MagicMock(side_effect=TimeoutError()),
                               on_auth_state_change=MagicMock())
        with fixture._supabase(auth) as create:
            client = create()
            try:
                self.assertTrue(client._antigravity_restore_pending)
                self.assertFalse(client._antigravity_authenticated)
                self.assertEqual(fixture.keyring.clears, 0)
                auth.set_session.reset_mock()
                isolated = create(restore_session=False)
                auth.set_session.assert_not_called()
                self.manager._close_supabase_client(isolated)
            finally:
                self.manager._close_supabase_client(client)


if __name__ == '__main__':
    unittest.main()
