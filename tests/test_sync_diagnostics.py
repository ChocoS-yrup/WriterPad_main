import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from settings_panel import SettingsPanel
from sync_diagnostics import (
    SyncDiagnosticLog,
    format_diagnostic_report,
    safe_failure_reason,
    sanitize_sensitive_text,
)
from sync_manager import _MeasuredReentrantLock


class _FakeLabel:
    def __init__(self):
        self.text = ""

    def setText(self, value):
        self.text = value


class SyncDiagnosticPrivacyTestCase(unittest.TestCase):

    def test_structure_lock_reports_only_outermost_wait_and_hold(self):
        observed = []
        lock = _MeasuredReentrantLock(
            lambda phase, elapsed_ms: observed.append((phase, elapsed_ms))
        )

        with lock:
            with lock:
                pass

        self.assertEqual([phase for phase, _ in observed], ["wait", "hold"])
        self.assertTrue(all(elapsed_ms >= 0 for _, elapsed_ms in observed))

    def test_sensitive_credentials_are_redacted_from_copied_text(self):
        jwt = "eyJabcdefghijk.abcdefghijk.abcdefghijk"
        source = (
            f"password=my-password access_token=access-secret "
            f"refresh_token=refresh-secret Authorization=Bearer {jwt}"
        )

        sanitized = sanitize_sensitive_text(source)

        for secret in ("my-password", "access-secret", "refresh-secret", jwt):
            self.assertNotIn(secret, sanitized)
        self.assertIn("[REDACTED]", sanitized)

    def test_failure_log_keeps_category_but_never_payload_or_manuscript(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log = SyncDiagnosticLog(temp_dir, max_bytes=4096, backup_count=1)
            manuscript = "범인은 오래된 성문 뒤에 숨어 있었다"
            token = "eyJabcdefghijk.abcdefghijk.abcdefghijk"

            log.record(
                "sync_failure",
                detail=f"network timeout password=secret {token} content={manuscript}",
                state="offline",
                pending_count=3,
            )
            log.flush()
            content = Path(log.path).read_text(encoding="utf-8")

            self.assertIn("네트워크 연결 오류", content)
            self.assertNotIn("secret", content)
            self.assertNotIn(token, content)
            self.assertNotIn(manuscript, content)

    def test_diagnostic_report_contains_only_safe_summary_fields(self):
        report = format_diagnostic_report({
            "login_state": "로그인됨 password=hidden",
            "sync_state": "offline",
            "pending_count": 2,
            "last_success_at": "2026-08-02T10:00:00+09:00",
            "last_failure_at": "2026-08-02T10:01:00+09:00",
            "last_failure_reason": "JWT expired access_token=hidden-token",
            "dropped_log_count": 0,
        })

        self.assertIn("서버 대기 문서: 2건", report)
        self.assertIn("클라우드 로그인 필요", report)
        self.assertNotIn("hidden", report)
        self.assertNotIn("access_token=hidden-token", report)
        self.assertIn("원고 본문은 포함되지 않습니다", report)

    def test_safe_failure_reason_never_returns_unknown_raw_error(self):
        raw = "unexpected payload: 사용자의 원고 문장 전체"

        self.assertEqual(safe_failure_reason(raw), "서버 요청 처리 오류")
        self.assertNotIn("원고 문장", safe_failure_reason(raw))


class SyncDiagnosticRotationTestCase(unittest.TestCase):
    def test_log_rotation_limits_file_size_and_backup_count(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log = SyncDiagnosticLog(
                temp_dir, max_bytes=512, backup_count=2, queue_size=128
            )
            for index in range(300):
                log.record(
                    "sync_failure",
                    detail="network timeout",
                    state="offline",
                    pending_count=index % 7,
                )
            log.flush()

            files = sorted(Path(temp_dir).glob("sync-diagnostics.log*"))
            self.assertLessEqual(len(files), 3)
            self.assertTrue(files)
            self.assertTrue(all(path.stat().st_size <= 700 for path in files))

    def test_long_running_burst_is_non_blocking_and_storage_stays_bounded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            log = SyncDiagnosticLog(
                temp_dir, max_bytes=512, backup_count=2, queue_size=16
            )
            accepted = 0
            for index in range(10000):
                accepted += bool(log.record(
                    "sync_state", state="syncing", pending_count=index % 5
                ))
            log.flush()

            files = list(Path(temp_dir).glob("sync-diagnostics.log*"))
            self.assertLessEqual(len(files), 3)
            self.assertLessEqual(sum(path.stat().st_size for path in files), 2100)
            self.assertGreater(accepted, 0)
            self.assertGreaterEqual(log.summary()["dropped_log_count"], 0)


class SyncDiagnosticSettingsTestCase(unittest.TestCase):
    def test_settings_summary_shows_required_user_fields(self):
        text = SettingsPanel._diagnostic_display_text({
            "login_state": "로그인됨",
            "sync_state": "offline",
            "pending_count": 4,
            "last_success_at": "2026-08-02T10:00:00+09:00",
            "last_failure_at": "2026-08-02T10:01:00+09:00",
            "last_failure_reason": "네트워크 연결 오류",
            "dropped_log_count": 0,
        })

        self.assertIn("로그인 상태: 로그인됨", text)
        self.assertIn("서버 대기 문서: 4건", text)
        self.assertIn("마지막 동기화 성공", text)
        self.assertIn("최근 동기화 실패: 네트워크 연결 오류", text)

    def test_copy_button_uses_sanitized_manager_report(self):
        label = _FakeLabel()
        clipboard = MagicMock()
        target = SimpleNamespace(lbl_diagnostics_copy_status=label)
        manager = MagicMock()
        manager.diagnostic_report.return_value = (
            "작가님 힘내세요 · 동기화 진단\n민감정보와 원고 본문은 포함되지 않습니다."
        )

        with patch("sync_manager.SyncManager", return_value=manager), patch(
            "settings_panel.QApplication.clipboard", return_value=clipboard
        ), patch("settings_panel.QTimer.singleShot"):
            report = SettingsPanel.copy_sync_diagnostics(target)

        clipboard.setText.assert_called_once_with(report)
        self.assertIn("민감정보를 제외", label.text)


if __name__ == "__main__":
    unittest.main()
