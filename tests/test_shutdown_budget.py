"""Stage 4: application shutdown must finish inside one bounded budget."""

import queue
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from PyQt6.QtCore import QTimer

from tests.qt_app import APP

from mode_assistant import AssistantModeWidget
from mode_writing import WritingModeWidget
from sync_diagnostics import SyncDiagnosticLog
from sync_manager import SHUTDOWN_BUDGET_MS, SyncManager
from writing_controller import WritingController


class _FakeWorker:
    """Stands in for a QThread so tests never depend on real thread timing."""

    def __init__(self, running=True, finishes=True):
        self._running = running
        self._finishes = finishes
        self.wait_calls = []
        self.terminate = MagicMock()

    def isRunning(self):
        return self._running

    def wait(self, timeout_ms=None):
        self.wait_calls.append(timeout_ms)
        if self._finishes:
            self._running = False
            return True
        return False


class ShutdownBudgetTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = APP

    def setUp(self):
        self.manager = SyncManager()
        self.previous = (
            self.manager.supabase,
            self.manager.cloud_config_state,
            self.manager._last_cloud_error_kind,
            self.manager._v2_store,
            self.manager._v2_context,
            self.manager._v2_device_id,
        )
        self.manager.reset_shutdown_state()
        self.manager._shutdown_timer_stoppers = []
        for name in (
            "_workers",
            "_bulk_workers",
            "_history_workers",
            "_autosave_workers",
            "_retention_workers",
            "_rename_workers",
            "_v2_workers",
            "_server_action_workers",
        ):
            getattr(self.manager, name).clear()
        self.manager.active_workers.clear()
        self.manager._last_cloud_error_kind = ""

    def tearDown(self):
        (
            self.manager.supabase,
            self.manager.cloud_config_state,
            self.manager._last_cloud_error_kind,
            self.manager._v2_store,
            self.manager._v2_context,
            self.manager._v2_device_id,
        ) = self.previous
        self.manager.reset_shutdown_state()
        self.manager._shutdown_timer_stoppers = []
        for name in (
            "_workers",
            "_bulk_workers",
            "_history_workers",
            "_autosave_workers",
            "_retention_workers",
            "_rename_workers",
            "_v2_workers",
            "_server_action_workers",
        ):
            getattr(self.manager, name).clear()
        self.manager.active_workers.clear()

    def _enable_stuck_v2_queue(self):
        """Cloud looks usable but the operation queue never drains."""
        self.manager.cloud_config_state = "ready"
        self.manager.supabase = SimpleNamespace()
        self.manager._v2_device_id = "device-1"
        self.manager._v2_context = {
            "local_key": "project-local",
            "project_id": "project-1",
            "server_state": "active",
        }
        self.manager._v2_store = SimpleNamespace(
            counts=lambda _key: {
                "pending": 1,
                "inflight": 0,
                "conflict": 0,
                "total": 1,
            },
            next_ready_operation=lambda _key: None,
            next_ready_structure_batch=lambda _key: None,
        )

    # --- budget ---------------------------------------------------------

    def test_flush_stays_within_budget_when_queue_never_drains(self):
        self._enable_stuck_v2_queue()

        started = time.monotonic()
        completed = self.manager.flush_pending_syncs()
        elapsed_ms = (time.monotonic() - started) * 1000

        self.assertFalse(completed)
        self.assertLess(elapsed_ms, SHUTDOWN_BUDGET_MS * 1.5)

    def test_second_begin_shutdown_does_not_extend_the_deadline(self):
        first = self.manager.begin_shutdown(budget_ms=400)
        again = self.manager.begin_shutdown(budget_ms=60000)

        self.assertEqual(first, again)
        self.assertLessEqual(self.manager.shutdown_remaining_ms(), 400)

    def test_flush_shares_the_open_budget_instead_of_adding_its_own(self):
        self._enable_stuck_v2_queue()
        self.manager.begin_shutdown(budget_ms=300)

        started = time.monotonic()
        self.manager.flush_pending_syncs()
        elapsed_ms = (time.monotonic() - started) * 1000

        self.assertLess(elapsed_ms, SHUTDOWN_BUDGET_MS)

    def test_remaining_budget_is_none_before_shutdown_starts(self):
        self.assertIsNone(self.manager.shutdown_remaining_ms())

    # --- periodic work stops before the nested flush loop ----------------

    def test_begin_shutdown_stops_every_registered_periodic_timer(self):
        stop_pull = MagicMock()
        stop_controller_timers = MagicMock()
        self.manager.register_shutdown_timer_stopper(stop_pull)
        self.manager.register_shutdown_timer_stopper(stop_controller_timers)
        self.manager._v2_retry_timer.start(60000)

        self.manager.begin_shutdown()

        stop_pull.assert_called_once_with()
        stop_controller_timers.assert_called_once_with()
        self.assertFalse(self.manager._v2_retry_timer.isActive())
        self.assertIsNone(self.manager._v2_retry_context)

    def test_flush_event_loop_does_not_revive_periodic_timers(self):
        self._enable_stuck_v2_queue()
        periodic = QTimer()
        periodic.setInterval(10)
        periodic.start()
        self.manager.register_shutdown_timer_stopper(periodic.stop)
        self.manager.begin_shutdown(budget_ms=200)
        self.assertFalse(periodic.isActive())

        # flush 는 중첩 이벤트 루프를 돌린다. 멈춘 타이머가 되살아나면 안 된다.
        self.manager.flush_pending_syncs()

        self.assertFalse(periodic.isActive())
        self.assertFalse(self.manager._v2_retry_timer.isActive())

    def test_registering_the_same_stopper_twice_keeps_one_entry(self):
        stopper = MagicMock()
        self.manager.register_shutdown_timer_stopper(stopper)
        self.manager.register_shutdown_timer_stopper(stopper)

        self.manager.begin_shutdown()

        stopper.assert_called_once_with()

    # --- known-bad cloud config skips the remote attempt ----------------

    def test_confirmed_dns_failure_skips_the_remote_flush_immediately(self):
        self._enable_stuck_v2_queue()
        store = MagicMock()
        self.manager._v2_store = store
        self.manager._last_cloud_error_kind = "dns"

        started = time.monotonic()
        completed = self.manager.flush_pending_syncs()
        elapsed_ms = (time.monotonic() - started) * 1000

        self.assertFalse(completed)
        self.assertLess(elapsed_ms, 250)
        store.counts.assert_not_called()

    def test_unconfigured_cloud_skips_the_remote_flush_immediately(self):
        self._enable_stuck_v2_queue()
        store = MagicMock()
        self.manager._v2_store = store
        self.manager.cloud_config_state = "invalid"

        self.assertTrue(self.manager.flush_pending_syncs())
        store.counts.assert_not_called()

    def test_pending_operations_are_left_queued_for_the_next_run(self):
        self._enable_stuck_v2_queue()
        store = self.manager._v2_store

        self.manager.flush_pending_syncs()

        # 큐를 비우거나 버리는 경로가 없어야 다음 실행에서 복구된다.
        self.assertFalse(hasattr(store, "cleared"))
        self.assertEqual(store.counts("project-local")["pending"], 1)

    # --- worker draining ------------------------------------------------

    def test_wait_all_workers_waits_for_each_worker_only_once(self):
        worker = _FakeWorker()
        self.manager._workers.append(worker)
        self.manager._v2_workers.append(worker)
        self.manager.active_workers.add(worker)
        self.manager.begin_shutdown(budget_ms=500)

        self.assertTrue(self.manager.wait_all_workers())
        self.assertEqual(len(worker.wait_calls), 1)
        self.assertGreater(worker.wait_calls[0], 0)

    def test_wait_all_workers_stays_unbounded_outside_shutdown(self):
        worker = _FakeWorker()
        self.manager._workers.append(worker)

        self.assertTrue(self.manager.wait_all_workers())
        self.assertEqual(worker.wait_calls, [None])

    def test_wait_all_workers_reports_workers_that_missed_the_deadline(self):
        stuck = _FakeWorker(finishes=False)
        self.manager._workers.append(stuck)
        self.manager.begin_shutdown(budget_ms=50)

        self.assertFalse(self.manager.wait_all_workers())

    def test_shutdown_never_terminates_a_running_worker(self):
        stuck = _FakeWorker(finishes=False)
        self.manager._workers.append(stuck)
        self.manager.begin_shutdown(budget_ms=50)

        self.assertFalse(self.manager.shutdown())

        stuck.terminate.assert_not_called()

    def test_shutdown_keeps_the_http_pool_open_while_a_worker_runs(self):
        stuck = _FakeWorker(finishes=False)
        self.manager._workers.append(stuck)
        http_client = MagicMock()
        self.manager.supabase = SimpleNamespace(
            _antigravity_httpx_client=http_client
        )
        self.manager.begin_shutdown(budget_ms=50)

        self.manager.shutdown()

        http_client.close.assert_not_called()

    def test_shutdown_closes_the_http_pool_once_every_worker_finished(self):
        http_client = MagicMock()
        self.manager.supabase = SimpleNamespace(
            _antigravity_httpx_client=http_client
        )

        self.assertTrue(self.manager.shutdown())

        http_client.close.assert_called_once_with()

    def test_shutdown_blocks_new_server_actions(self):
        self.manager.shutdown()

        self.assertIsNone(self.manager._start_server_action(lambda: None))

    def test_shutdown_bounds_the_diagnostic_log_flush(self):
        self.manager.begin_shutdown(budget_ms=50)
        with patch.object(self.manager._diagnostics, "flush") as flush:
            self.manager.shutdown()

        self.assertEqual(len(flush.call_args_list), 1)
        self.assertIsNotNone(flush.call_args.kwargs.get("timeout_ms"))

    # --- controller side ------------------------------------------------

    def _controller(self, sync_manager, active_paths=()):
        timer = MagicMock()
        timer.isActive.return_value = False
        with patch("writing_controller.QTimer", return_value=timer):
            return WritingController(
                MagicMock(),
                sync_manager,
                SimpleNamespace(current_project="작품"),
                "session-1",
                lambda: list(active_paths),
                lambda _path: "",
            )

    def test_controller_wait_stops_timers_and_bounds_the_wait(self):
        sync_manager = MagicMock()
        sync_manager.shutdown_remaining_ms.return_value = 400
        controller = self._controller(sync_manager)
        worker = _FakeWorker()
        controller._lock_workers = [worker]

        self.assertTrue(controller.wait_all_workers())

        self.assertFalse(controller._timers_started)
        self.assertEqual(len(worker.wait_calls), 1)
        self.assertGreater(worker.wait_calls[0], 0)
        controller.idle_timer.stop.assert_called()

    def test_release_all_locks_is_skipped_once_the_budget_is_gone(self):
        sync_manager = MagicMock()
        sync_manager.cloud_network_enabled = True
        sync_manager.is_v2_enabled = True
        sync_manager.shutdown_remaining_ms.return_value = 0
        controller = self._controller(sync_manager, ["메인/원고/1권/001화.txt"])
        controller.locked_paths.add("메인/원고/1권/001화.txt")

        controller.release_all_locks()

        sync_manager.release_lock_async.assert_not_called()
        self.assertEqual(controller.locked_paths, set())

    def test_release_all_locks_is_skipped_when_cloud_is_unusable(self):
        sync_manager = MagicMock()
        sync_manager.cloud_network_enabled = False
        controller = self._controller(sync_manager, ["메인/원고/1권/001화.txt"])
        controller.locked_paths.add("메인/원고/1권/001화.txt")

        controller.release_all_locks()

        sync_manager.release_lock_async.assert_not_called()
        self.assertEqual(controller.locked_paths, set())

    def test_release_all_locks_still_runs_inside_the_budget(self):
        sync_manager = MagicMock()
        sync_manager.cloud_network_enabled = True
        sync_manager.is_v2_enabled = True
        sync_manager.shutdown_remaining_ms.return_value = 900
        path = "메인/원고/1권/001화.txt"
        controller = self._controller(sync_manager, [path])
        controller.locked_paths.add(path)

        controller.release_all_locks()

        sync_manager.release_lock_async.assert_called_once_with(
            "작품", path, "session-1"
        )

    # --- close ordering -------------------------------------------------

    def test_close_persists_local_view_state_before_the_remote_flush(self):
        order = MagicMock()
        sync_manager = SimpleNamespace(
            cloud_network_enabled=True,
            begin_shutdown=order.begin_shutdown,
            flush_pending_syncs=MagicMock(return_value=True),
        )
        order.attach_mock(sync_manager.flush_pending_syncs, "flush")
        writing_mode = SimpleNamespace(
            sync_manager=sync_manager,
            persist_editor_view_states=order.persist,
        )
        panel = SimpleNamespace(writing_mode=writing_mode)

        self.assertTrue(
            AssistantModeWidget._flush_writing_sync_before_close(panel)
        )

        self.assertEqual(
            order.mock_calls,
            [call.begin_shutdown(), call.persist(), call.flush()],
        )

    def test_writing_mode_stops_its_remote_pull_timer_on_shutdown(self):
        timer = MagicMock()
        widget = SimpleNamespace(remote_pull_timer=timer)

        WritingModeWidget._stop_remote_pull_timer(widget)

        timer.stop.assert_called_once_with()

    def test_stopping_the_remote_pull_timer_is_safe_before_it_exists(self):
        WritingModeWidget._stop_remote_pull_timer(SimpleNamespace())


class DiagnosticFlushBudgetTestCase(unittest.TestCase):
    def test_flush_gives_up_when_the_writer_stalls(self):
        with tempfile.TemporaryDirectory() as directory:
            log = SyncDiagnosticLog(directory=directory)
            log._queue = queue.Queue()
            log._queue.put({"event": "sync_state"})

            started = time.monotonic()
            drained = log.flush(timeout_ms=80)
            elapsed_ms = (time.monotonic() - started) * 1000

            self.assertFalse(drained)
            self.assertLess(elapsed_ms, 1000)

    def test_flush_reports_success_once_the_queue_is_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            log = SyncDiagnosticLog(directory=directory)
            log.record("sync_state", state="saved")

            self.assertTrue(log.flush(timeout_ms=2000))


if __name__ == "__main__":
    unittest.main()
