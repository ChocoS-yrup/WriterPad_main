import gc
import tempfile
import threading
import time
import tracemalloc
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import QEventLoop, QThread, QTimer, pyqtSignal

from sync_manager import (
    AutoSaveWorker,
    BackupWorker,
    BulkSaveWorker,
    RenameWorker,
    RetentionWorker,
    SaveWorker,
    SyncManager,
)
from sync_v2_store import SyncV2Store
from tests.resource_probe import (
    measure_finished_worker_retention,
    measure_legacy_http_clients,
    measure_snapshot_cache,
    measure_status_db_work,
)
from tests.qt_app import APP
from writing_controller import WritingController


def _wait_until(predicate, timeout_ms=10000):
    loop = QEventLoop()
    deadline = time.monotonic() + timeout_ms / 1000

    def poll():
        if predicate() or time.monotonic() >= deadline:
            loop.quit()
        else:
            QTimer.singleShot(5, poll)

    QTimer.singleShot(0, poll)
    loop.exec()
    return predicate()


class _SlowResultWorker(QThread):
    resultReady = pyqtSignal(bool, str)
    started_count = 0
    started_event = threading.Event()
    release_event = threading.Event()

    def __init__(self, *_args, **_kwargs):
        super().__init__()

    def run(self):
        type(self).started_count += 1
        type(self).started_event.set()
        type(self).release_event.wait(3)
        self.resultReady.emit(True, "")


class _BlockingAutoSaveWorker(QThread):
    resultReady = pyqtSignal(bool, str)
    instances = []
    started_events = [threading.Event(), threading.Event()]
    release_events = [threading.Event(), threading.Event()]

    def __init__(self, _wpm, _path, content):
        super().__init__()
        self.content = content
        self.worker_index = len(type(self).instances)
        type(self).instances.append(self)

    def run(self):
        index = self.worker_index
        type(self).started_events[index].set()
        type(self).release_events[index].wait(5)
        self.resultReady.emit(True, "")


class LongRunResourceTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Keep a real QApplication alive in a module for the full process.
        # A QCoreApplication cannot be upgraded later; widget tests otherwise
        # terminate the interpreter without a Python traceback on Windows CI.
        cls.app = APP

    def setUp(self):
        self.manager = SyncManager()
        self.manager._shutting_down = False
        self.manager._v2_store = None
        self.manager._v2_context = None
        self.manager._v2_device_id = None
        self.manager._workers.clear()
        self.manager._bulk_workers.clear()
        self.manager._history_workers.clear()
        self.manager._autosave_workers.clear()
        self.manager._autosave_workers_by_path.clear()
        self.manager._autosave_followups.clear()
        self.manager._autosave_ready_followups.clear()
        self.manager._retention_workers.clear()
        self.manager._rename_workers.clear()
        self.manager.active_workers.clear()
        self.manager._retention_worker = None
        self.manager._heartbeat_worker = None

    def tearDown(self):
        _SlowResultWorker.release_event.set()
        self.manager.wait_all_workers()
        self.app.processEvents()
        self.manager._v2_store = None
        self.manager._v2_context = None
        self.manager._v2_device_id = None

    def test_six_hour_document_switch_cache_stays_bounded(self):
        result = measure_snapshot_cache()

        self.assertLessEqual(result["cached_documents"], 2)
        self.assertLess(result["retained_bytes"], 1024 * 1024)

    def test_repeated_status_updates_use_one_sqlite_snapshot_each(self):
        result = measure_status_db_work(self.manager)

        self.assertEqual(result["iterations"], 1000)
        self.assertEqual(result["sqlite_connections"], 1000)

    def test_legacy_http_client_is_closed_after_every_worker(self):
        result = measure_legacy_http_clients()

        self.assertEqual(result, {"created": 200, "closed": 200})

    def test_result_signals_do_not_override_native_thread_finished(self):
        for worker_type in (
            SaveWorker,
            BulkSaveWorker,
            BackupWorker,
            AutoSaveWorker,
            RenameWorker,
            RetentionWorker,
        ):
            self.assertNotIn("finished", worker_type.__dict__)
            self.assertIn("resultReady", worker_type.__dict__)

    def test_completed_workers_are_removed_without_delayed_retention(self):
        result = measure_finished_worker_retention(self.manager)

        self.assertEqual(result["completed_workers"], 120)
        self.assertEqual(result["retained_immediately"], 0)
        self.assertEqual(result["retained_after_2_2s"], 0)

    def test_repeated_same_document_backups_coalesce_to_active_and_latest(self):
        class ManualSignal:
            def __init__(self):
                self.callbacks = []

            def connect(self, callback, *_args):
                self.callbacks.append(callback)

            def emit(self, *args):
                for callback in list(self.callbacks):
                    callback(*args)

        class ManualWorker:
            instances = []

            def __init__(self, _wpm, _path, content):
                self.content = content
                self.resultReady = ManualSignal()
                self.finished = ManualSignal()
                self.deleted = False
                type(self).instances.append(self)

            def deleteLater(self):
                self.deleted = True

        wpm = SimpleNamespace()
        path = "메인/원고/장시간.txt"

        with patch("sync_manager.AutoSaveWorker", ManualWorker), patch.object(
            self.manager, "_start_worker"
        ):
            first = self.manager.upload_autosave_async(wpm, path, "본문-0")
            returned = [
                self.manager.upload_autosave_async(
                    wpm, path, f"본문-{index}"
                )
                for index in range(1, 720)
            ]
            self.assertTrue(all(worker is first for worker in returned))
            self.assertEqual(len(self.manager._autosave_workers), 1)
            self.assertEqual(len(self.manager._autosave_followups), 1)
            self.assertEqual(
                next(iter(self.manager._autosave_followups.values()))[2],
                "본문-719",
            )

            first.resultReady.emit(True, "")
            first.finished.emit()
            self.manager._drain_autosave_followups()
            self.assertEqual(len(ManualWorker.instances), 2)
            followup = ManualWorker.instances[-1]
            self.assertEqual(followup.content, "본문-719")
            self.assertIs(
                self.manager._autosave_workers_by_path[
                    (id(wpm), path)
                ],
                followup,
            )

            followup.resultReady.emit(True, "")
            followup.finished.emit()

        self.assertEqual(len(ManualWorker.instances), 2)
        self.assertTrue(all(worker.deleted for worker in ManualWorker.instances))
        self.assertEqual(self.manager._autosave_workers, [])
        self.assertEqual(self.manager._autosave_workers_by_path, {})
        self.assertEqual(self.manager._autosave_followups, {})

    def test_real_qthread_same_document_backup_runs_active_then_latest(self):
        _BlockingAutoSaveWorker.instances.clear()
        for event in (
            *_BlockingAutoSaveWorker.started_events,
            *_BlockingAutoSaveWorker.release_events,
        ):
            event.clear()

        def release_test_workers():
            for event in _BlockingAutoSaveWorker.release_events:
                event.set()
            for worker in list(_BlockingAutoSaveWorker.instances):
                try:
                    worker.wait(5000)
                except RuntimeError:
                    pass

        self.addCleanup(release_test_workers)
        wpm = SimpleNamespace()
        path = "메인/원고/실제스레드.txt"

        with patch("sync_manager.AutoSaveWorker", _BlockingAutoSaveWorker):
            first = self.manager.upload_autosave_async(wpm, path, "본문-0")
            self.assertTrue(
                _BlockingAutoSaveWorker.started_events[0].wait(2)
            )
            returned = [
                self.manager.upload_autosave_async(
                    wpm, path, f"본문-{index}"
                )
                for index in range(1, 100)
            ]
            self.assertTrue(all(worker is first for worker in returned))
            self.assertEqual(len(self.manager._autosave_workers), 1)

            _BlockingAutoSaveWorker.release_events[0].set()
            self.assertTrue(first.wait(5000))
            self.assertTrue(_wait_until(
                lambda: len(_BlockingAutoSaveWorker.instances) == 2
                and _BlockingAutoSaveWorker.started_events[1].is_set(),
                timeout_ms=5000,
            ))
            followup = _BlockingAutoSaveWorker.instances[1]
            self.assertEqual(followup.content, "본문-99")
            self.assertIs(
                self.manager._autosave_workers_by_path[(id(wpm), path)],
                followup,
            )

            _BlockingAutoSaveWorker.release_events[1].set()
            self.assertTrue(followup.wait(5000))
            self.assertTrue(_wait_until(
                lambda: not self.manager._autosave_workers
                and not self.manager._autosave_workers_by_path
                and not self.manager._autosave_followups,
                timeout_ms=5000,
            ))

    def test_retention_and_heartbeat_workers_do_not_duplicate(self):
        _SlowResultWorker.started_count = 0
        _SlowResultWorker.started_event.clear()
        _SlowResultWorker.release_event.clear()
        wpm = SimpleNamespace()

        with patch("sync_manager.RetentionWorker", _SlowResultWorker):
            first = self.manager.run_retention_async(wpm)
            self.assertTrue(_SlowResultWorker.started_event.wait(1))
            repeated = [
                self.manager.run_retention_async(wpm) for _ in range(24)
            ]
            self.assertTrue(all(worker is first for worker in repeated))
            self.assertEqual(len(self.manager._retention_workers), 1)
            _SlowResultWorker.release_event.set()
            self.assertTrue(_wait_until(
                lambda: not self.manager._retention_workers
            ))

        heartbeat = MagicMock()
        heartbeat.isRunning.return_value = True
        self.manager._heartbeat_worker = heartbeat
        with patch.object(self.manager, "_start_server_action") as start:
            returned = [
                self.manager.heartbeat_locks_async(
                    "작품", ["메인/원고/장시간.txt"], "device"
                )
                for _ in range(720)
            ]
        self.assertTrue(all(worker is heartbeat for worker in returned))
        start.assert_not_called()

    def test_periodic_timers_start_only_once(self):
        sync_manager = MagicMock()
        controller = WritingController(
            SimpleNamespace(),
            sync_manager,
            SimpleNamespace(current_project="장시간 작품"),
            "device",
            lambda: [],
            lambda _path: None,
        )

        self.assertTrue(controller.start_timers())
        for _ in range(720):
            self.assertFalse(controller.start_timers())

        self.assertEqual(sync_manager.run_retention_async.call_count, 1)
        self.assertEqual(len(controller.findChildren(QTimer)), 4)
        controller.stop_timers()
        self.assertFalse(any(
            timer.isActive() for timer in controller.findChildren(QTimer)
        ))

    def test_offline_repeated_save_keeps_immutable_durable_intents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            context = store.configure_project(
                str(Path(temp_dir, "작품", "집필모드")), "오프라인 작품"
            )
            tracemalloc.start()
            operation_ids = []
            for index in range(720):
                operation = store.enqueue(
                    context,
                    "메인/원고/장시간.txt",
                    f"최신 본문 {index}\n" + ("가" * 8192),
                )
                operation_ids.append(operation["operation_id"])
            gc.collect()
            retained, _peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            counts = store.counts(context["local_key"])
            latest = store.operation(operation_ids[-1])

        self.assertEqual(counts["documents"], 1)
        self.assertEqual(counts["total"], 720)
        self.assertEqual(len(set(operation_ids)), 720)
        self.assertTrue(latest["content"].startswith("최신 본문 719"))
        self.assertLess(retained, 4 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
