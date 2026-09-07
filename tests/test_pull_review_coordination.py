"""Actual manager/coordinator and Qt signals, temporary stores, no live network."""
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from PyQt6.QtCore import QObject, pyqtSignal
from tests.qt_app import APP
from tests import test_contract_http_zero_recovery as fixtures
from sync_contract import SyncContractError
from contract_readiness_diagnostics import coordination_snapshot


class Pull(QObject):
    resultReady = pyqtSignal(bool, object)
    finished = pyqtSignal()

    def __init__(self, manager, project_id=None):
        super().__init__()
        self.project_id = project_id

    def isRunning(self):
        return True


class PullReviewCoordinationTests(unittest.TestCase):
    def setUp(self):
        self.case = fixtures.HttpZeroRecoveryTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.manager, self.store = self.case.manager, self.case.store
        self.coordinator = self.manager._current_pull_coordinator()
        self.coordinator.update(baseline_validated=True, pull_pending=False, pulling=False)
        self.workers = []
        for target, name, options in (
            (self.manager, '_v2_pull_worker', {'new':None}),
            (self.manager, '_v2_pull_worker_identity', {'new':None}),
            (self.manager, 'request_contract_handshake_async', {'return_value':False}),
            (self.manager, '_start_worker', {'side_effect': self.workers.append}),
            (self.manager, '_record_sync_success', {}),
        ):
            active = patch.object(target, name, **options)
            active.start(); self.addCleanup(active.stop)
        active = patch('sync_manager.V2PullWorker', Pull)
        active.start(); self.addCleanup(active.stop)
        self.addCleanup(self.flush)

    def flush(self):
        self.manager._shutting_down = True
        APP.processEvents()

    def queue_pull(self, **kwargs):
        return self.manager.pull_remote_changes_async(**kwargs)

    def test_pull_first_refuses_both_claims_without_consuming_recovery(self):
        self.assertTrue(self.queue_pull())
        before = self.case.originals()
        with patch.object(self.store, 'claim_http_zero_recovery') as recovery_claim, patch.object(self.store, 'claim_reviewed_execution') as original_claim:
            for send in (self.case.send, self.case.case.send):
                with self.assertRaises(SyncContractError) as caught:
                    send()
                self.assertEqual(caught.exception.code, 'CONTRACT_PREPARATION_NOT_READY')
                self.assertIn('pull_idle', caught.exception.readiness_observation['failed_conditions'])
            recovery_claim.assert_not_called(); original_claim.assert_not_called()
        self.assertEqual(self.case.originals(), before)
        with self.store._reader() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM sync_reviewed_recoveries').fetchone()[0], 0)
        self.assertFalse(self.manager.contract_path_enabled())

    def test_reservation_blocks_claim_even_before_worker_is_constructed(self):
        entered, release = threading.Event(), threading.Event()
        def hold(*args, **kwargs):
            entered.set(); self.assertTrue(release.wait(5)); return False
        with patch.object(self.manager, '_start_reserved_pull', side_effect=hold), ThreadPoolExecutor(max_workers=1) as pool:
            task = pool.submit(self.queue_pull)
            try:
                self.assertTrue(entered.wait(5))
                self.assertIsNone(self.manager._v2_pull_worker)
                with patch.object(self.store, 'claim_http_zero_recovery') as claim:
                    with self.assertRaises(SyncContractError): self.case.send()
                    claim.assert_not_called()
            finally:
                release.set()
            self.assertFalse(task.result(timeout=5))

    def test_review_remote_wait_defers_coalesces_and_resumes_on_qt_slot(self):
        entered, release = threading.Event(), threading.Event()
        def remote_read(name):
            if name == 'projects' and not entered.is_set():
                entered.set(); self.assertTrue(release.wait(5))
        self.case.case.on_read = remote_read
        with ThreadPoolExecutor(max_workers=1) as pool:
            task = pool.submit(self.case.send)
            try:
                self.assertTrue(entered.wait(5))
                self.assertTrue(self.manager._contract_lock.acquire(timeout=1))
                self.manager._contract_lock.release()
                before = coordination_snapshot(self.manager)
                for _ in range(5): self.assertFalse(self.queue_pull(reason='general'))
                self.assertEqual(coordination_snapshot(self.manager)['write_epoch'], before['write_epoch'])
                self.assertEqual(coordination_snapshot(self.manager)['authority_state'], before['authority_state'])
                self.assertEqual(self.workers, [])
                self.manager.request_contract_handshake_async.assert_not_called()
            finally:
                release.set()
            self.assertTrue(task.result(timeout=5)['applied'])
        self.assertFalse(self.manager._review_execution_busy)
        APP.processEvents()
        self.assertEqual(len(self.workers), 1)
        self.assertTrue(self.coordinator['pulling'])
        self.assertFalse(self.coordinator['pull_pending'])
        self.assertEqual(coordination_snapshot(self.manager)['authority_reason'], 'pull_start')
        self.assertEqual(len(self.case.case.writes), 1)  # synthetic RPC only

    def test_error_releases_preparation_and_preserves_pending_pull(self):
        def fail(name):
            if name == 'projects':
                self.assertFalse(self.queue_pull())
                raise TimeoutError('synthetic read failure')
        self.case.case.on_read = fail
        with self.assertRaises(TimeoutError): self.case.send()
        self.assertFalse(self.manager._review_execution_busy)
        self.assertFalse(self.manager.contract_path_enabled())
        APP.processEvents()
        self.assertEqual(len(self.workers), 1)
        self.assertEqual(self.case.case.writes, [])
        with self.assertRaises(SyncContractError): self.case.send()

    def test_stale_release_ticket_never_starts_new_project_or_account(self):
        for attr in ('_v2_context_generation', '_auth_context_generation'):
            with self.subTest(attr=attr):
                self.coordinator['pull_pending'] = True
                with self.manager._contract_lock: self.manager._queue_pull_after_review()
                setattr(self.manager, attr, getattr(self.manager, attr)+1)
                new = self.manager._current_pull_coordinator()
                new['pull_pending'] = True
                APP.processEvents()
                self.assertEqual(self.workers, [])
                self.assertTrue(new['pull_pending'])
                self.coordinator = new

    def test_shutdown_release_retains_pending_without_start(self):
        self.coordinator['pull_pending'] = True
        with self.manager._contract_lock: self.manager._queue_pull_after_review()
        self.manager._shutting_down = True
        APP.processEvents()
        self.assertEqual(self.workers, [])
        self.assertTrue(self.coordinator['pull_pending'])

    def test_deferred_manual_baseline_and_retry_flags_are_merged(self):
        self.manager._review_execution_busy = True
        try:
            self.assertFalse(self.queue_pull(manual=True, reason='baseline', retry_pending_after_pull=True))
            self.assertFalse(self.queue_pull(reason='general'))
        finally: self.manager._review_execution_busy = False
        with patch.object(self.manager, '_start_reserved_pull', return_value=True) as start:
            self.assertTrue(self.queue_pull())
            self.assertEqual(start.call_args.args[-3:], (True, True, 'baseline'))
        self.assertNotIn('review_deferred_manual', self.coordinator)

    def test_constructor_failure_clears_reservation_keeps_baseline_unaccepted(self):
        with patch('sync_manager.V2PullWorker', side_effect=RuntimeError('synthetic start error')):
            with self.assertRaises(RuntimeError): self.queue_pull()
        self.assertFalse(self.coordinator['pulling'])
        self.assertTrue(self.coordinator['pull_pending'])
        self.assertIsNone(self.manager._v2_pull_worker)
        obs = coordination_snapshot(self.manager)
        self.assertEqual(obs['authority_state'], 'unknown')
        self.assertEqual(obs['authority_reason'], 'pull_start_failed')
        self.assertFalse(self.coordinator['baseline_validated'])
        self.assertTrue(self.queue_pull())
        self.assertEqual(len(self.workers), 1)

    def test_late_result_and_finished_signals_cannot_accept_another_generation(self):
        self.assertTrue(self.queue_pull(retry_pending_after_pull=True))
        worker = self.workers[0]
        self.manager._auth_context_generation += 1
        new = self.manager._current_pull_coordinator()
        self.manager._block_structure_authority('INVALID_TREE_ORDER_RESPONSE')
        before = coordination_snapshot(self.manager)
        with patch.object(self.manager, 'retry_pending_syncs') as retry:
            worker.resultReady.emit(True, {'structure_authority':{'kind':'legacy'}, 'background_apply':{'kind':'unchanged'}})
            worker.finished.emit()
            retry.assert_not_called()
        self.assertEqual(coordination_snapshot(self.manager)['authority_state'], 'blocked')
        self.assertEqual(coordination_snapshot(self.manager)['write_epoch'], before['write_epoch'])
        self.assertTrue(new['pull_pending'])
        self.assertEqual(len(self.workers), 1)

    def test_old_finished_signal_cannot_release_a_new_pull_reservation(self):
        self.assertTrue(self.queue_pull())
        worker = self.workers[0]
        worker.resultReady.emit(True, {'structure_authority':{'kind':'legacy'},
                                      'background_apply':{'kind':'unchanged'}})
        worker.finished.emit()
        self.assertFalse(self.coordinator['pulling'])
        self.assertTrue(self.manager._contract_authority_observation()['allowed'])
        self.assertTrue(self.queue_pull())
        newer = self.workers[-1]
        worker.finished.emit()
        self.assertTrue(self.coordinator['pulling'])
        self.assertIs(self.manager._v2_pull_worker, newer)

    def test_blocked_state_is_not_overridden_by_deferred_ordinary_pull(self):
        self.manager._review_execution_busy = True
        try: self.assertFalse(self.queue_pull())
        finally: self.manager._review_execution_busy = False
        self.manager._block_structure_authority('INVALID_TREE_ORDER_RESPONSE')
        with self.manager._contract_lock: self.manager._queue_pull_after_review()
        APP.processEvents()
        self.assertEqual(self.workers, [])
        self.assertEqual(coordination_snapshot(self.manager)['authority_state'], 'blocked')
        self.assertTrue(self.coordinator['pull_pending'])

    def test_real_higher_revision_still_stops_before_synthetic_write(self):
        self.case.case.remote['tree_orders'][0]['revision'] += 1
        with self.assertRaises(SyncContractError): self.case.send()
        self.assertEqual(self.case.case.writes, [])
        self.assertFalse(self.manager.contract_path_enabled())
        self.assertEqual(self.case.originals(), self.case.original)

    def test_readonly_diagnostics_export_and_failure_record_are_sanitized(self):
        self.manager._block_structure_authority('PRIVATE_PATH_OR_TOKEN')
        self.manager._authority_transition_reason = 'PRIVATE_PATH_OR_TOKEN'
        before = self.case.originals()
        with patch.object(self.manager, 'perform_contract_handshake', side_effect=AssertionError('no RPC')):
            report = self.manager.inspect_reviewed_contract_readiness()
            destination = self.case.case.fixture.root.parent.parent / 'coordination-readiness.json'
            try:
                self.manager.export_reviewed_readiness(str(destination))
                exported = json.loads(destination.read_text(encoding='utf-8'))
                self.assertEqual(exported['observation']['coordination'], report['observation']['coordination'])
            finally:
                if destination.exists(): destination.unlink()
        obs = report['observation']
        self.assertEqual(obs['coordination']['authority_state'], 'blocked')
        self.assertEqual(obs['coordination']['authority_reason'], 'initial')
        self.assertGreater(obs['coordination']['transition_sequence'], 0)
        self.assertFalse(report['execution_authorized'])
        obs['coordination']['raw_exception'] = 'PRIVATE_PATH_OR_TOKEN'
        self.store.record_reviewed_observation(self.case.key, self.case.batch, 'test_observation', obs)
        raw = json.dumps(self.store.diagnostics(self.case.key, limit=100))
        self.assertNotIn('PRIVATE_PATH_OR_TOKEN', raw)
        self.assertIn('coordination', raw)
        self.assertEqual(self.case.originals(), before)


if __name__ == '__main__': unittest.main()
