"""Repeatable before/after resource measurements for long-running use."""

import gc
import json
import tempfile
import time
import tracemalloc
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer

from sync_manager import SaveWorker, SyncManager
from sync_v2_store import SyncV2Store
from writing_controller import WritingController


class _NoopSyncManager:
    is_v2_enabled = False

    def run_retention_async(self, _wpm):
        return None


class _ManualSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback, *_args):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self.callbacks):
            callback(*args)


class _ManualAutoSaveWorker:
    def __init__(self, *_args, **_kwargs):
        self.resultReady = _ManualSignal()
        self.finished = _ManualSignal()
        self.deleted = False

    def deleteLater(self):
        self.deleted = True


def _controller(active_paths):
    return WritingController(
        SimpleNamespace(),
        _NoopSyncManager(),
        SimpleNamespace(current_project="계측 작품"),
        "device",
        lambda: list(active_paths),
        lambda _path: None,
    )


def measure_snapshot_cache():
    active_paths = []
    controller = _controller(active_paths)
    tracemalloc.start()
    gc.collect()
    before = tracemalloc.get_traced_memory()[0]
    for index in range(720):
        path = f"메인/원고/{index:04d}.txt"
        active_paths[:] = [path]
        controller.notify_file_opened(
            path, f"{index:06d}:" + ("가" * 32768)
        )
    gc.collect()
    after, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    controller.stop_timers()
    return {
        "cached_documents": len(controller.last_snapshot_contents),
        "retained_bytes": max(0, after - before),
        "peak_bytes": max(0, peak - before),
    }


def measure_status_db_work(manager):
    with tempfile.TemporaryDirectory() as temp_dir:
        store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
        context = store.configure_project(
            str(Path(temp_dir, "작품", "집필모드")), "계측 작품"
        )
        calls = 0
        original_connect = store._connect

        def counted_connect():
            nonlocal calls
            calls += 1
            return original_connect()

        store._connect = counted_connect
        manager._v2_store = store
        manager._v2_context = context
        manager._v2_device_id = "device"
        manager._retry_queue.clear()
        manager._active_server_syncs = 0
        manager._active_backups = 0
        started = time.perf_counter()
        for _ in range(1000):
            manager._publish_sync_state()
        elapsed = time.perf_counter() - started
        return {
            "iterations": 1000,
            "sqlite_connections": calls,
            "elapsed_ms": round(elapsed * 1000, 2),
        }


class _FakeResponse:
    data = []


class _FakeQuery:
    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def upsert(self, *_args, **_kwargs):
        return self

    def execute(self):
        return _FakeResponse()


class _FakeHttpClient:
    def __init__(self, counters):
        self.counters = counters

    def close(self):
        self.counters["closed"] += 1


class _FakeSupabase:
    def __init__(self, counters):
        self._antigravity_httpx_client = _FakeHttpClient(counters)

    def table(self, _name):
        return _FakeQuery()


def measure_legacy_http_clients():
    counters = {"created": 0, "closed": 0}

    def create_client():
        counters["created"] += 1
        return _FakeSupabase(counters)

    with patch.object(SyncManager, "create_supabase_client", create_client):
        for index in range(200):
            worker = SaveWorker(
                None,
                None,
                "계측 작품",
                f"메인/원고/{index:04d}.txt",
                "본문",
                force_overwrite=True,
            )
            worker.run()
    return counters


def _wait_until(predicate, timeout_ms=10000):
    loop = QEventLoop()
    deadline = time.monotonic() + (timeout_ms / 1000)

    def poll():
        if predicate() or time.monotonic() >= deadline:
            loop.quit()
        else:
            QTimer.singleShot(5, poll)

    QTimer.singleShot(0, poll)
    loop.exec()


def _wait_until_stable(predicate, stable_ms=250, timeout_ms=30000):
    loop = QEventLoop()
    deadline = time.monotonic() + (timeout_ms / 1000)
    stable_since = [None]

    def poll():
        now = time.monotonic()
        if predicate():
            if stable_since[0] is None:
                stable_since[0] = now
            if now - stable_since[0] >= stable_ms / 1000:
                loop.quit()
                return
        else:
            stable_since[0] = None
        if now >= deadline:
            loop.quit()
        else:
            QTimer.singleShot(5, poll)

    QTimer.singleShot(0, poll)
    loop.exec()


def measure_finished_worker_retention(manager):
    with tempfile.TemporaryDirectory() as temp_dir:
        wpm = SimpleNamespace(
            workspace_dir=temp_dir,
            current_project="계측 작품",
        )
        manager._v2_store = None
        manager._v2_context = None
        manager._v2_device_id = None
        with patch("sync_manager.AutoSaveWorker", _ManualAutoSaveWorker), patch.object(
            manager, "_start_worker"
        ):
            workers = [
                manager.upload_autosave_async(
                    wpm, f"메인/원고/{index:04d}.txt", "백업 본문"
                )
                for index in range(120)
            ]
            for worker in workers:
                worker.resultReady.emit(True, "")
                worker.finished.emit()
        QCoreApplication.processEvents()
        retained_immediately = len(manager._autosave_workers)
        wait_loop = QEventLoop()
        QTimer.singleShot(2200, wait_loop.quit)
        wait_loop.exec()
        return {
            "completed_workers": len(workers),
            "retained_immediately": retained_immediately,
            "retained_after_2_2s": len(manager._autosave_workers),
        }


def main():
    app = QCoreApplication.instance() or QCoreApplication([])
    manager = SyncManager()
    manager.supabase = None
    report = {
        "snapshot_cache": measure_snapshot_cache(),
        "status_db_work": measure_status_db_work(manager),
        "legacy_http_clients": measure_legacy_http_clients(),
        "finished_worker_retention": measure_finished_worker_retention(manager),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    manager.wait_all_workers()
    manager._diagnostics.flush()
    app.processEvents()


if __name__ == "__main__":
    main()
