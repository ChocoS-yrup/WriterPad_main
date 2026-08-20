import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mode_writing import WritingModeWidget
from project_manager_writing import WritingProjectManager
from settings_panel import SettingsPanel
from sync_manager import SaveWorker, SyncManager
from sync_v2_store import SyncV2Store


class SyncManagerStateTestCase(unittest.TestCase):
    def setUp(self):
        self.manager = SyncManager()
        self.previous_v2_state = (
            self.manager._v2_store,
            self.manager._v2_context,
            self.manager._v2_wpm,
            dict(self.manager._v2_leases),
        )
        self.manager._v2_store = None
        self.manager._v2_context = None
        self.manager._v2_wpm = None
        self.manager._v2_leases = {}
        self.manager._retry_queue = {}
        self.manager._retry_active_key = None
        self.manager._active_server_syncs = 0
        self.manager._active_backups = 0
        self.manager._last_sync_error = ""
        self.manager._last_failure_offline = False
        self.states = []
        self.manager.syncStateChanged.connect(self._record_state)

    def tearDown(self):
        self.manager.syncStateChanged.disconnect(self._record_state)
        self.manager._retry_queue = {}
        self.manager._retry_active_key = None
        self.manager._active_server_syncs = 0
        self.manager._active_backups = 0
        (
            self.manager._v2_store,
            self.manager._v2_context,
            self.manager._v2_wpm,
            previous_leases,
        ) = self.previous_v2_state
        self.manager._v2_leases = previous_leases

    def _record_state(self, state, detail, pending_count):
        self.states.append((state, detail, pending_count))

    def test_state_priority_covers_backup_sync_offline_failure_and_saved(self):
        self.manager._active_backups = 1
        self.manager._publish_sync_state()
        self.assertEqual(self.states[-1][0], "backup")

        self.manager._active_server_syncs = 1
        self.manager._publish_sync_state()
        self.assertEqual(self.states[-1][0], "syncing")

        self.manager._active_server_syncs = 0
        self.manager._active_backups = 0
        retry_key = ("content", "작품", "001화.txt")
        self.manager._queue_retry(retry_key, {"kind": "content"}, "connection timeout", offline=True)
        self.manager._publish_sync_state()
        self.assertEqual(self.states[-1][0], "offline")

        self.manager._retry_queue[retry_key]["_retry_offline"] = False
        self.manager._publish_sync_state()
        self.assertEqual(self.states[-1][0], "failed")

        self.manager._retry_queue.clear()
        self.manager._publish_sync_state()
        self.assertEqual(self.states[-1][0], "saved")

    def test_missing_rpc_grant_shows_actionable_message_instead_of_raw_json(self):
        path = "메인/원고/1권/006화.txt"
        document = {"document_id": "document-id", "revision": 1}
        self.manager._v2_store = MagicMock()
        self.manager._v2_store.get_document.return_value = document
        self.manager._v2_store.ensure_document.return_value = document
        self.manager._v2_store.has_tombstone_for_server_path.return_value = False
        self.manager._v2_context = {"local_key": "project-key"}
        self.manager._v2_device_id = "device-id"
        raw_error = {
            "message": "permission denied for function acquire_edit_lease",
            "code": "42501",
            "hint": None,
            "details": None,
        }

        with patch.object(
            self.manager, "_acquire_v2_lease", side_effect=RuntimeError(raw_error)
        ):
            success, message = self.manager.check_and_acquire_lock(
                "서버 작품", path, "session-id", client=object()
            )

        self.assertFalse(success)
        self.assertIn("서버 동기화 권한 설정", message)
        self.assertNotIn("42501", message)
        self.assertNotIn("permission denied", message)

    def test_logged_out_client_shows_cloud_login_guidance_before_lease_rpc(self):
        path = "메인/원고/1권/006화.txt"
        document = {"document_id": "document-id", "revision": 1}
        self.manager._v2_store = MagicMock()
        self.manager._v2_store.ensure_document.return_value = document
        self.manager._v2_context = {"local_key": "project-key"}
        self.manager._v2_device_id = "device-id"
        client = SimpleNamespace(_antigravity_authenticated=False)

        with patch.object(self.manager, "_acquire_v2_lease") as acquire:
            success, message = self.manager.check_and_acquire_lock(
                "서버 작품", path, "session-id", client=client
            )

        self.assertFalse(success)
        self.assertEqual(
            message,
            "클라우드 동기화 계정에 로그인이 되어있지 않습니다.\n"
            "설정탭 / 클라우드 계정 로그인을 확인해주세요.",
        )
        acquire.assert_not_called()

    def test_retry_queue_keeps_only_the_latest_content_for_each_file(self):
        key = ("content", "작품", "메인/원고/1권/001화.txt")
        self.manager._queue_retry(key, {"kind": "content", "content": "이전 내용"}, "timeout", offline=True)
        self.manager._queue_retry(key, {"kind": "content", "content": "최신 내용"}, "timeout", offline=True)

        self.assertEqual(self.manager.pending_retry_count, 1)
        self.assertEqual(self.manager._retry_queue[key]["content"], "최신 내용")

    def _publish_with_v2_error(self, error, pending=1):
        old = (
            self.manager._v2_store,
            self.manager._v2_context,
            self.manager._v2_device_id,
        )
        try:
            self.manager._v2_store = SimpleNamespace(
                counts=lambda _key: {
                    "pending": pending, "inflight": 0, "conflict": 0,
                    "total": pending,
                },
                latest_error=lambda _key: error,
            )
            self.manager._v2_context = {"local_key": "A"}
            self.manager._v2_device_id = "device-a"
            self.manager._publish_sync_state()
        finally:
            (
                self.manager._v2_store,
                self.manager._v2_context,
                self.manager._v2_device_id,
            ) = old
        return self.states[-1]

    def test_expired_login_asks_for_relogin_instead_of_a_network_check(self):
        state, detail, _pending = self._publish_with_v2_error("AUTH_REQUIRED")

        self.assertEqual(state, "auth_required")
        guidance = WritingModeWidget._storage_status_guidance(
            state, detail, 1, 0, False
        )
        self.assertIn("로그인", guidance["action"])
        self.assertNotIn("인터넷", guidance["action"])

    def test_queued_work_without_an_error_is_not_reported_as_offline(self):
        state, _detail, _pending = self._publish_with_v2_error("")

        self.assertEqual(state, "failed")
        guidance = WritingModeWidget._storage_status_guidance(
            state, "", 1, 0, False
        )
        self.assertEqual(guidance["action_code"], "retry")
        self.assertNotIn("인터넷", guidance["action"])

    def test_connectivity_error_still_reports_offline(self):
        state, _detail, _pending = self._publish_with_v2_error("connection timeout")

        self.assertEqual(state, "offline")

    def test_success_clears_a_previous_failure_reason(self):
        self.manager._last_sync_error = "AUTH_REQUIRED: 세션 갱신 실패"
        self.manager._last_failure_offline = True
        self.manager._auth_retry_blocked = True

        self.manager._record_sync_success()

        self.assertEqual(self.manager._last_sync_error, "")
        self.assertFalse(self.manager._last_failure_offline)
        self.assertFalse(self.manager._auth_retry_blocked)

    def test_auth_failure_is_not_recorded_as_offline(self):
        try:
            self.manager._mark_auth_required()
            self.assertTrue(self.manager._auth_retry_blocked)
            self.assertFalse(self.manager._last_failure_offline)
        finally:
            self.manager._auth_retry_blocked = False
            self.manager._last_sync_error = ""

    def test_persistent_lease_conflict_has_a_distinct_user_state(self):
        old_store = self.manager._v2_store
        old_context = self.manager._v2_context
        old_device = self.manager._v2_device_id
        try:
            self.manager._v2_store = SimpleNamespace(
                counts=lambda _key: {
                    "pending": 1, "inflight": 0, "conflict": 0, "total": 1
                },
                latest_error=lambda _key: "LEASE_CONFLICT",
            )
            self.manager._v2_context = {"local_key": "B"}
            self.manager._v2_device_id = "device-b"

            self.manager._publish_sync_state()

            self.assertEqual(self.states[-1][0], "lease")
            self.assertIn("다른 기기", self.states[-1][1])
        finally:
            self.manager._v2_store = old_store
            self.manager._v2_context = old_context
            self.manager._v2_device_id = old_device

    def test_retry_dispatches_one_queued_item_at_a_time(self):
        key = ("content", "작품", "001화.txt")
        payload = {"kind": "content", "content": "재시도 내용"}
        self.manager._retry_queue[key] = payload

        with patch.object(self.manager, "_launch_content_upload") as launch:
            self.assertTrue(self.manager.retry_pending_syncs())

        launch.assert_called_once_with(payload, key, is_retry=True)
        self.assertEqual(self.manager._retry_active_key, key)
        self.assertFalse(self.manager.retry_pending_syncs())

    def test_failed_attempt_is_queued_and_later_success_removes_it(self):
        key = ("content", "작품", "001화.txt")
        payload = {"kind": "content", "content": "보존할 내용"}

        self.manager._active_server_syncs = 1
        success, _ = self.manager._complete_server_attempt(
            key, payload, False, "connection timeout", SimpleNamespace(supabase=None), is_retry=False
        )
        self.assertFalse(success)
        self.assertIn(key, self.manager._retry_queue)
        self.assertEqual(self.manager.current_sync_state, "offline")

        self.manager._active_server_syncs = 1
        success, _ = self.manager._complete_server_attempt(
            key, payload, True, "", SimpleNamespace(supabase=object()), is_retry=False
        )
        self.assertTrue(success)
        self.assertNotIn(key, self.manager._retry_queue)
        self.assertEqual(self.manager.current_sync_state, "saved")

    def test_offline_save_worker_still_saves_locally_and_reports_server_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wpm = WritingProjectManager()
            wpm.workspace_dir = temp_dir
            wpm.current_project = "테스트 작품"
            wpm.writing_root_path = str(Path(temp_dir, "테스트 작품", "집필모드"))
            relative_path = "메인/원고/1권/001화.txt"
            result = []

            worker = SaveWorker(None, wpm, "테스트 작품", relative_path, "로컬에는 반드시 남을 내용")
            worker.resultReady.connect(lambda *args: result.append(args))
            with patch.object(SyncManager, "create_supabase_client", return_value=None):
                worker.run()

            self.assertEqual(wpm.read_text_file(relative_path), "로컬에는 반드시 남을 내용")
            self.assertEqual(result[0][0], False)
            self.assertIn("서버 연결 없음", result[0][1])

    def test_failed_session_restore_is_not_reported_as_logged_in(self):
        stale_session = SimpleNamespace(
            user=SimpleNamespace(email="stale@example.com")
        )
        target = SimpleNamespace(
            supabase=SimpleNamespace(
                _antigravity_authenticated=False,
                auth=SimpleNamespace(get_session=lambda: stale_session),
            )
        )

        self.assertEqual(SyncManager.authenticated_email(target), "")

    def test_frozen_build_reads_bundled_supabase_configuration(self):
        import sync_manager

        with patch.object(sync_manager.sys, "_MEIPASS", "C:/bundle", create=True):
            self.assertEqual(sync_manager.supabase_config_dir(), "C:/bundle")

    def test_windows_build_includes_public_supabase_configuration(self):
        spec = Path("Antigravity_AI_Writer.spec").read_text(encoding="utf-8")

        self.assertNotIn(".env", spec)
        self.assertIn("config_source = 'release_cloud_config.json'", spec)
        self.assertIn("assert_release_config_buildable(config_source)", spec)
        self.assertIn("(config_source, '.')", spec)
        self.assertIn("if Path(config_source).is_file() else []", spec)
        source = Path("sync_manager.py").read_text(encoding="utf-8")
        self.assertNotIn("SUPABASE_EMAIL", source)
        self.assertNotIn("SUPABASE_PASSWORD", source)
        self.assertNotIn("SUPABASE_ACCESS_TOKEN", source)
        self.assertNotIn("SUPABASE_REFRESH_TOKEN", source)

    def test_trashed_project_status_stops_retry_and_preserves_queue(self):
        class _Rpc:
            def execute(self):
                return SimpleNamespace(data={"state": "trashed"})

        class _Client:
            def rpc(self, name, params):
                self.last_call = (name, params)
                return _Rpc()

        with tempfile.TemporaryDirectory() as temp_dir:
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            project_id = "f6e37e0a-9f93-40d5-860e-3a5c81c61961"
            context = store.configure_project(
                str(Path(temp_dir, "작품", "집필모드")),
                "작품",
                project_id,
            )
            store.enqueue(
                context, "메인/원고/001화.txt", "보존할 로컬 원고"
            )
            self.manager._v2_store = store
            self.manager._v2_context = context
            self.manager._v2_wpm = SimpleNamespace()
            self.manager._v2_device_id = "device"
            self.manager.supabase = _Client()

            with self.assertRaisesRegex(RuntimeError, "PROJECT_TRASHED"):
                self.manager._fetch_v2_project_documents()

            self.assertEqual(
                self.manager._v2_context["server_state"], "trashed"
            )
            self.assertEqual(
                store.get_project_by_id(project_id)["server_state"],
                "trashed",
            )
            self.assertFalse(self.manager.retry_pending_syncs())
            self.assertEqual(store.counts(context["local_key"])["pending"], 1)
            self.assertEqual(
                self.manager.current_sync_state, "project_trashed"
            )

    def test_project_status_falls_back_to_existing_trash_rpc(self):
        project_id = "0b49107c-0807-486e-bd7d-693f97ceddb4"

        class _Rpc:
            def __init__(self, name):
                self.name = name

            def execute(self):
                if self.name == "get_project_status":
                    raise RuntimeError("function does not exist")
                return SimpleNamespace(data=[{
                    "project_id": project_id,
                    "name": "휴지통 작품",
                    "trashed_at": "2026-07-29T10:00:00Z",
                }])

        class _Client:
            def rpc(self, name, params):
                return _Rpc(name)

        with tempfile.TemporaryDirectory() as temp_dir:
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            context = store.configure_project(
                str(Path(temp_dir, "작품", "집필모드")),
                "작품",
                project_id,
            )
            self.manager._v2_store = store
            self.manager._v2_context = context
            self.manager._v2_wpm = SimpleNamespace()
            self.manager._v2_device_id = "device"
            self.manager.supabase = _Client()

            self.assertEqual(
                self.manager._fetch_v2_project_status(), "trashed"
            )
            self.assertEqual(
                self.manager._v2_context["server_state"], "trashed"
            )

    def test_project_the_server_never_saw_is_not_read_as_purged(self):
        """A project waiting for its first commit simply has no server row.

        ensure_project is what creates that row, and it only runs inside
        dispatch. Calling the absence a purge stops dispatch, so the row
        never appears and the project can never recover on its own.
        """
        project_id = "1f2a4c86-3c0f-4b21-9a5e-0f6b7d2c9481"

        class _Rpc:
            def __init__(self, name):
                self.name = name

            def execute(self):
                if self.name == "get_project_status":
                    raise RuntimeError("P0001: PROJECT_NOT_FOUND")
                return SimpleNamespace(data=[])

        # No table(): the RPC has answered, so reaching the compatibility
        # path would be a mistake and this client makes that mistake loud.
        class _Client:
            def rpc(self, name, params):
                return _Rpc(name)

        with tempfile.TemporaryDirectory() as temp_dir:
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            context = store.configure_project(
                str(Path(temp_dir, "작품", "집필모드")),
                "작품",
                project_id,
            )
            store.enqueue(context, "메인/원고/001화.txt", "첫 회차")
            self.manager._v2_store = store
            self.manager._v2_context = context
            self.manager._v2_wpm = SimpleNamespace()
            self.manager._v2_device_id = "device"
            self.manager.supabase = _Client()

            self.assertEqual(
                self.manager._fetch_v2_project_status(), "active"
            )
            self.assertEqual(
                self.manager._v2_context["server_state"], "active"
            )
            self.assertEqual(
                store.get_project_by_id(project_id)["server_state"], "active"
            )
            self.assertNotEqual(
                self.manager.current_sync_state, "project_purged"
            )

    def test_project_that_vanished_after_a_commit_is_still_purged(self):
        """Absence only means a purge once the server has accepted a commit."""
        project_id = "6d0be1a4-70cc-4a1f-8c33-2b9c5f1e77a2"

        class _Rpc:
            def __init__(self, name):
                self.name = name

            def execute(self):
                if self.name == "get_project_status":
                    raise RuntimeError("P0001: PROJECT_NOT_FOUND")
                return SimpleNamespace(data=[])

        # No table(): the RPC has answered, so reaching the compatibility
        # path would be a mistake and this client makes that mistake loud.
        class _Client:
            def rpc(self, name, params):
                return _Rpc(name)

        with tempfile.TemporaryDirectory() as temp_dir:
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            context = store.configure_project(
                str(Path(temp_dir, "작품", "집필모드")),
                "작품",
                project_id,
            )
            operation = store.enqueue(
                context, "메인/원고/001화.txt", "서버가 받아준 원고"
            )
            store.mark_attempt(operation["operation_id"])
            store.mark_success(operation["operation_id"], {"revision": 1})

            self.manager._v2_store = store
            self.manager._v2_context = context
            self.manager._v2_wpm = SimpleNamespace()
            self.manager._v2_device_id = "device"
            self.manager.supabase = _Client()

            self.assertEqual(
                self.manager._fetch_v2_project_status(), "purged"
            )
            self.assertEqual(
                self.manager._v2_context["server_state"], "purged"
            )

    def test_missing_status_rpc_does_not_purge_a_project_never_uploaded(self):
        """The compatibility path must not read an absent row as a purge."""
        project_id = "b3c9f1d2-5e47-4a08-9f61-8d20c4a7be35"

        class _Rpc:
            def __init__(self, name):
                self.name = name

            def execute(self):
                if self.name == "get_project_status":
                    raise RuntimeError("function does not exist")
                return SimpleNamespace(data=[])

        class _Table:
            def select(self, *args, **kwargs):
                return self

            def eq(self, *args, **kwargs):
                return self

            def limit(self, *args, **kwargs):
                return self

            def execute(self):
                return SimpleNamespace(data=[])

        class _Client:
            def rpc(self, name, params):
                return _Rpc(name)

            def table(self, name):
                return _Table()

        with tempfile.TemporaryDirectory() as temp_dir:
            store = SyncV2Store(str(Path(temp_dir, "sync.sqlite3")))
            context = store.configure_project(
                str(Path(temp_dir, "작품", "집필모드")),
                "작품",
                project_id,
            )
            store.enqueue(context, "메인/원고/001화.txt", "첫 회차")
            self.manager._v2_store = store
            self.manager._v2_context = context
            self.manager._v2_wpm = SimpleNamespace()
            self.manager._v2_device_id = "device"
            self.manager.supabase = _Client()

            self.assertEqual(
                self.manager._fetch_v2_project_status(), "active"
            )
            self.assertEqual(
                store.get_project_by_id(project_id)["server_state"], "active"
            )


class _FakeLabel:
    def __init__(self):
        self.text = ""
        self.style = ""
        self.tooltip = ""
        self.enabled = True

    def setText(self, value):
        self.text = value

    def setStyleSheet(self, value):
        self.style = value

    def setToolTip(self, value):
        self.tooltip = value

    def setEnabled(self, value):
        self.enabled = bool(value)


class StorageStatusLabelTestCase(unittest.TestCase):
    def test_all_user_visible_status_labels(self):
        target = SimpleNamespace(lbl_storage_status=_FakeLabel())
        expected = {
            "saved": "로컬 저장 완료",
            "backup": "복구본 생성 중",
            "syncing": "서버 전송 중",
            "offline": "로컬 저장 완료",
            "auth_required": "로그인 필요",
            "lease": "다른 기기 편집 중",
            "failed": "서버 전송 대기",
            "conflict": "충돌",
            "project_trashed": "서버 휴지통 · 동기화 중지",
            "project_purged": "서버 영구 삭제 · 로컬 사본",
        }

        for state, label in expected.items():
            WritingModeWidget.update_storage_status(
                target,
                state,
                "상세 상태",
                1 if state in {
                    "offline", "auth_required", "lease", "failed", "conflict",
                    "project_trashed", "project_purged",
                } else 0,
            )
            self.assertIn(label, target.lbl_storage_status.text)

        self.assertIn("원인과 해결 방법", target.lbl_storage_status.tooltip)
        WritingModeWidget.update_storage_status(
            target, "failed", "상세 상태", 1
        )
        self.assertIn("다시 시도", target.lbl_storage_status.tooltip)

    def test_storage_status_makes_restored_cloud_login_visible(self):
        target = SimpleNamespace(
            lbl_storage_status=_FakeLabel(),
            sync_manager=SimpleNamespace(
                authenticated_email=lambda: "writer@example.com"
            ),
        )

        WritingModeWidget.update_storage_status(target, "saved", "", 0)

        self.assertEqual("● 동기화 완료", target.lbl_storage_status.text)
        self.assertIn("writer@example.com", target.lbl_storage_status.tooltip)

    def test_dirty_editor_shows_one_background_save_waiting(self):
        target = SimpleNamespace(
            lbl_storage_status=_FakeLabel(),
            is_dirty_left=True,
            is_dirty_right=False,
            sync_manager=SimpleNamespace(authenticated_email=lambda: ""),
        )

        WritingModeWidget.update_storage_status(target, "saved", "", 0)

        self.assertIn("로컬 저장 대기 1건", target.lbl_storage_status.text)
        self.assertNotIn("수정 중", target.lbl_storage_status.text)
        self.assertNotIn("저장 완료", target.lbl_storage_status.text)
        self.assertIn("입력을 잠시 멈추", target.lbl_storage_status.tooltip)
        self.assertIn("Ctrl+S", target.lbl_storage_status.tooltip)

    def test_empty_document_guard_is_not_hidden_by_generic_pending_text(self):
        target = SimpleNamespace(
            lbl_storage_status=_FakeLabel(),
            is_dirty_left=True,
            is_dirty_right=False,
            sync_manager=SimpleNamespace(authenticated_email=lambda: ""),
        )

        WritingModeWidget.update_storage_status(
            target,
            "empty_guard",
            "기존 내용이 있는 문서의 자동저장을 중단했습니다.",
            0,
        )

        self.assertIn("전체 삭제 확인 필요", target.lbl_storage_status.text)
        self.assertNotIn("로컬 저장 대기 1건", target.lbl_storage_status.text)

    def test_dirty_editor_keeps_important_offline_context_visible(self):
        target = SimpleNamespace(
            lbl_storage_status=_FakeLabel(),
            is_dirty_left=False,
            is_dirty_right=True,
            sync_manager=SimpleNamespace(authenticated_email=lambda: ""),
        )

        WritingModeWidget.update_storage_status(
            target, "offline", "서버 연결 없음", 1
        )

        self.assertEqual(
            target.lbl_storage_status.text,
            "● 로컬 저장 대기 1건 · 오프라인",
        )

    def test_clean_editor_shows_local_save_and_one_server_document(self):
        target = SimpleNamespace(
            lbl_storage_status=_FakeLabel(),
            is_dirty_left=False,
            is_dirty_right=False,
            sync_manager=SimpleNamespace(authenticated_email=lambda: ""),
        )

        WritingModeWidget.update_storage_status(
            target, "syncing", "서버에 변경 내용을 올리는 중입니다.", 1
        )

        self.assertEqual(
            target.lbl_storage_status.text,
            "● 로컬 저장 완료 · 서버 전송 중 1건",
        )

    def test_guidance_distinguishes_seven_primary_storage_states(self):
        cases = {
            "saved": ("동기화 완료", "모두 반영"),
            "syncing": ("서버 전송 중", "로컬 저장은 완료"),
            "failed": ("서버 전송 대기", "로컬에 저장"),
            "offline": ("오프라인", "로컬에 저장"),
            "auth_required": ("로그인 필요", "로컬에 저장"),
            "conflict": ("충돌", "로컬 원고는 보존"),
        }
        for state, (title_text, safe_text) in cases.items():
            guidance = WritingModeWidget._storage_status_guidance(
                state,
                "상세 원인",
                1 if state != "saved" else 0,
                0,
                state == "saved",
            )
            self.assertIn(title_text, guidance["title"])
            self.assertIn(safe_text, guidance["summary"])
            self.assertTrue(guidance["action"])

        local_pending = WritingModeWidget._storage_status_guidance(
            "saved", "", 0, 2, True
        )
        self.assertEqual(local_pending["title"], "로컬 저장 대기")
        self.assertIn("2건", local_pending["summary"])
        self.assertEqual(local_pending["action_code"], "manual_save")

        offline = WritingModeWidget._storage_status_guidance(
            "offline", "인터넷 연결 없음", 1, 0, True
        )
        login_required = WritingModeWidget._storage_status_guidance(
            "auth_required", "JWT expired", 1, 0, False
        )
        self.assertEqual(offline["action_code"], "retry")
        self.assertEqual(login_required["action_code"], "")
        self.assertIn("다시 로그인", login_required["action"])

    def test_status_detail_dialog_runs_retry_only_after_user_selects_it(self):
        retry = MagicMock()
        target = SimpleNamespace(
            _storage_state="failed",
            _storage_detail="서버 응답 오류",
            _storage_pending_count=1,
            _storage_editor_dirty_count=0,
            _storage_account_email="writer@example.com",
            _retry_storage_sync=retry,
        )
        action_button = object()
        with patch("mode_writing.QMessageBox") as message_box:
            box = message_box.return_value
            box.addButton.side_effect = [action_button, object()]
            box.clickedButton.return_value = action_button

            WritingModeWidget._show_storage_status_details(target)

        self.assertIn("원인", box.setInformativeText.call_args.args[0])
        self.assertIn("다음 할 일", box.setInformativeText.call_args.args[0])
        retry.assert_called_once_with()

    def test_settings_panel_labels_automatic_login_without_showing_password(self):
        target = SimpleNamespace(
            lbl_supabase_status=_FakeLabel(),
            btn_supabase_login=_FakeLabel(),
            btn_supabase_logout=_FakeLabel(),
        )

        SettingsPanel.refresh_supabase_account_status(target, "writer@example.com")

        self.assertIn("자동 로그인됨: writer@example.com", target.lbl_supabase_status.text)
        self.assertIn("비밀번호는 저장하지 않고", target.lbl_supabase_status.text)
        self.assertEqual(target.btn_supabase_login.text, "계정 변경")
        self.assertTrue(target.btn_supabase_logout.enabled)

    def test_settings_panel_logged_out_message_uses_cloud_login_guidance(self):
        target = SimpleNamespace(
            lbl_supabase_status=_FakeLabel(),
            btn_supabase_login=_FakeLabel(),
            btn_supabase_logout=_FakeLabel(),
        )

        SettingsPanel.refresh_supabase_account_status(target, "")

        self.assertEqual(
            target.lbl_supabase_status.text,
            "클라우드 동기화 계정에 로그인이 되어있지 않습니다.\n"
            "설정탭 / 클라우드 계정 로그인을 확인해주세요.",
        )
        self.assertEqual(target.btn_supabase_login.text, "동기화 로그인")
        self.assertFalse(target.btn_supabase_logout.enabled)

    def test_settings_panel_explicitly_disables_unconfigured_cloud(self):
        target = SimpleNamespace(
            lbl_supabase_status=_FakeLabel(),
            btn_supabase_login=_FakeLabel(),
            btn_supabase_logout=_FakeLabel(),
        )
        manager = SimpleNamespace(
            cloud_configuration_status=lambda: (
                "disabled",
                "이 빌드는 클라우드 동기화가 구성되지 않았습니다.",
            )
        )

        with patch("sync_manager.SyncManager", return_value=manager):
            SettingsPanel.refresh_supabase_account_status(target)

        self.assertEqual(
            target.lbl_supabase_status.text,
            "이 빌드는 클라우드 동기화가 구성되지 않았습니다.",
        )
        self.assertFalse(target.btn_supabase_login.enabled)
        self.assertFalse(target.btn_supabase_logout.enabled)

    def test_compact_status_button_retries_only_when_items_are_pending(self):
        calls = []
        target = SimpleNamespace(
            _storage_pending_count=0,
            sync_manager=SimpleNamespace(
                retry_pending_syncs=lambda **kwargs: calls.append(kwargs)
            ),
        )

        WritingModeWidget._retry_storage_sync(target)
        self.assertEqual(calls, [])

        target._storage_pending_count = 2
        WritingModeWidget._retry_storage_sync(target)
        self.assertEqual(calls, [{"manual": True}])

    def test_conflict_status_retries_independent_queue_before_opening_folder(self):
        retry = MagicMock(return_value=True)
        target = SimpleNamespace(
            _storage_state="conflict",
            _storage_pending_count=3,
            sync_manager=SimpleNamespace(retry_pending_syncs=retry),
            open_conflict_folder=MagicMock(),
        )

        WritingModeWidget._retry_storage_sync(target)

        retry.assert_called_once_with(manual=True)
        target.open_conflict_folder.assert_not_called()

        retry.reset_mock()
        retry.return_value = False
        WritingModeWidget._retry_storage_sync(target)

        retry.assert_called_once_with(manual=True)
        target.open_conflict_folder.assert_called_once_with()

    def test_successful_conflict_resolution_restores_document_label(self):
        target = SimpleNamespace(
            loaded_versions={},
            current_loaded_file_left="메인/메모장/해결본.txt",
            current_loaded_file_right=None,
            lbl_current_doc=_FakeLabel(),
            lbl_r_doc=_FakeLabel(),
        )
        target.lbl_current_doc.text = "해결본.txt (충돌 해결 필요)"

        WritingModeWidget.on_sync_finished(
            target, True, "", "메인/메모장/해결본.txt", 5
        )

        self.assertEqual(target.lbl_current_doc.text, "해결본.txt")
        self.assertEqual(
            target.loaded_versions["메인/메모장/해결본.txt"], 5
        )

    def test_remote_refresh_updates_clean_open_editor_and_renamed_path(self):
        editor = MagicMock()
        target = SimpleNamespace(
            controller=MagicMock(),
            loaded_versions={"메인/메모장/예전.txt": 1},
            current_loaded_file_left="메인/메모장/예전.txt",
            current_loaded_file_right=None,
            left_editor=editor,
            right_editor=MagicMock(),
            lbl_current_doc=_FakeLabel(),
            lbl_r_doc=_FakeLabel(),
            is_dirty_left=False,
            is_dirty_right=False,
            load_tree_data=MagicMock(),
            _schedule_remote_tree_refresh=MagicMock(),
            _refresh_storage_status_for_editor_state=MagicMock(),
        )
        editor.toPlainText.return_value = "현재 화면의 이전 내용"

        WritingModeWidget.on_remote_documents_applied(target, [{
            "old_local_path": "메인/메모장/예전.txt",
            "new_local_path": "메인/메모장/새이름.txt",
            "content": "다른 Windows에서 저장한 내용",
            "revision": 7,
            "is_deleted": False,
        }])

        self.assertEqual(target.current_loaded_file_left, "메인/메모장/새이름.txt")
        editor.setPlainText.assert_called_once_with("다른 Windows에서 저장한 내용")
        self.assertEqual(target.lbl_current_doc.text, "새이름.txt")
        self.assertEqual(target.loaded_versions["메인/메모장/새이름.txt"], 7)
        target.controller.rename_path.assert_called_once()
        target.controller.accept_remote_snapshot.assert_called_once_with(
            "메인/메모장/새이름.txt", "다른 Windows에서 저장한 내용"
        )
        target._schedule_remote_tree_refresh.assert_called_once()
        target._refresh_storage_status_for_editor_state.assert_called_once()
        target.load_tree_data.assert_not_called()

    def test_remote_folder_identity_rename_remaps_clean_open_child_path(self):
        target = SimpleNamespace(
            controller=MagicMock(),
            loaded_versions={
                "메인/옛 폴더/문서.txt": 6,
                "메인/다른 폴더/유지.txt": 2,
            },
            current_loaded_file_left="메인/옛 폴더/문서.txt",
            current_loaded_file_right=None,
            left_editor=MagicMock(),
            right_editor=MagicMock(),
            lbl_current_doc=_FakeLabel(),
            lbl_r_doc=_FakeLabel(),
            is_dirty_left=False,
            is_dirty_right=False,
            _schedule_remote_tree_refresh=MagicMock(),
            _refresh_storage_status_for_editor_state=MagicMock(),
        )

        WritingModeWidget.on_remote_documents_applied(target, [{
            "kind": "folder_identity_rename",
            "old_local_path": "메인/옛 폴더",
            "new_local_path": "메인/새 폴더",
            "revision": 4,
            "is_deleted": False,
        }])

        self.assertEqual(
            target.current_loaded_file_left, "메인/새 폴더/문서.txt"
        )
        self.assertEqual(target.loaded_versions, {
            "메인/새 폴더/문서.txt": 6,
            "메인/다른 폴더/유지.txt": 2,
        })
        target.controller.rename_path.assert_called_once_with(
            "메인/옛 폴더", "메인/새 폴더"
        )
        target.left_editor.setPlainText.assert_not_called()
        target._schedule_remote_tree_refresh.assert_called_once()

    def test_dirty_open_editor_is_reported_as_remote_pull_protected(self):
        left_editor = MagicMock()
        left_editor.document.return_value.isModified.return_value = False
        right_editor = MagicMock()
        right_editor.document.return_value.isModified.return_value = True
        target = SimpleNamespace(
            current_loaded_file_left="메인/메모장/왼쪽.txt",
            current_loaded_file_right="메인/메모장/오른쪽.txt",
            is_dirty_left=True,
            is_dirty_right=False,
            left_editor=left_editor,
            right_editor=right_editor,
        )

        protected = WritingModeWidget.get_remote_sync_protected_paths(target)

        self.assertEqual(protected, {
            "메인/메모장/왼쪽.txt",
            "메인/메모장/오른쪽.txt",
        })


if __name__ == "__main__":
    unittest.main(verbosity=2)
