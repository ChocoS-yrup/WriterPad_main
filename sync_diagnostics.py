import json
import os
import queue
import re
import threading
from datetime import datetime

from runtime_profile import app_data_dir


DEFAULT_MAX_LOG_BYTES = 256 * 1024
DEFAULT_BACKUP_COUNT = 3
DEFAULT_QUEUE_SIZE = 512


_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(access[_ -]?token|refresh[_ -]?token|password|passwd|"
    r"api[_ -]?key|authorization)\b\s*[:=]\s*([^\s,;]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_JWT_TOKEN = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)


def sanitize_sensitive_text(value, limit=500):
    """Remove credentials from text that may be copied or persisted."""
    text = str(value or "")
    text = _SENSITIVE_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    text = _BEARER_TOKEN.sub("Bearer [REDACTED]", text)
    text = _JWT_TOKEN.sub("[REDACTED_JWT]", text)
    text = text.replace("\r", " ").replace("\n", " ").strip()
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


def safe_failure_reason(value):
    """Return a useful category without retaining request payload or manuscript."""
    text = sanitize_sensitive_text(value).lower()
    categories = (
        (("auth_required", "auth_expired", "jwt expired", "pgrst303", "로그인 필요"), "클라우드 로그인 필요"),
        (("lease_conflict", "다른 기기"), "다른 기기에서 편집 중"),
        (("revision_conflict", "document_already_exists", "문서 충돌", "충돌"), "문서 충돌"),
        (("project_trashed", "서버 휴지통"), "서버 휴지통 작품"),
        (("project_purged", "project_not_found", "서버 작품을 찾을 수 없음"), "서버 작품을 찾을 수 없음"),
        (("permission denied", "forbidden", "42501", "권한 설정 오류"), "서버 권한 설정 오류"),
        ((
            "network", "connection", "timeout", "timed out", "dns",
            "unreachable", "refused", "winerror", "offline", "서버 연결 없음",
            "네트워크 연결 오류",
        ), "네트워크 연결 오류"),
        (("path_conflict", "문서 경로 충돌"), "문서 경로 충돌"),
        (("empty", "빈 상태"), "빈 문서 자동저장 확인 필요"),
    )
    for markers, reason in categories:
        if any(marker in text for marker in markers):
            return reason
    return "서버 요청 처리 오류" if text else "기록 없음"


class SyncDiagnosticLog:
    """Bounded asynchronous JSONL log for non-sensitive sync events."""

    def __init__(
        self,
        directory=None,
        max_bytes=DEFAULT_MAX_LOG_BYTES,
        backup_count=DEFAULT_BACKUP_COUNT,
        queue_size=DEFAULT_QUEUE_SIZE,
    ):
        self.directory = os.path.abspath(
            directory or os.path.join(app_data_dir(), "diagnostics")
        )
        self.path = os.path.join(self.directory, "sync-diagnostics.log")
        self.max_bytes = max(256, int(max_bytes))
        self.backup_count = max(0, int(backup_count))
        self._queue = queue.Queue(maxsize=max(8, int(queue_size)))
        self._dropped_count = 0
        self._cache_lock = threading.Lock()
        self._last_success_at = ""
        self._last_failure_at = ""
        self._last_failure_reason = ""
        self._load_summary()
        self._thread = threading.Thread(
            target=self._writer_loop,
            name="sync-diagnostic-writer",
            daemon=True,
        )
        self._thread.start()

    @staticmethod
    def _now():
        return datetime.now().astimezone().isoformat(timespec="seconds")

    def _log_paths_oldest_first(self):
        paths = [f"{self.path}.{index}" for index in range(self.backup_count, 0, -1)]
        paths.append(self.path)
        return paths

    def _load_summary(self):
        for path in self._log_paths_oldest_first():
            try:
                with open(path, "r", encoding="utf-8") as source:
                    for line in source:
                        try:
                            event = json.loads(line)
                        except (TypeError, json.JSONDecodeError):
                            continue
                        if event.get("event") == "sync_success":
                            self._last_success_at = str(event.get("time") or "")
                        elif event.get("event") == "sync_failure":
                            self._last_failure_at = str(event.get("time") or "")
                            self._last_failure_reason = str(event.get("reason") or "")
            except OSError:
                continue

    def record(self, event, detail="", state="", pending_count=0):
        event = str(event or "sync_state")
        timestamp = self._now()
        reason = safe_failure_reason(detail) if event == "sync_failure" else ""
        entry = {
            "time": timestamp,
            "event": event,
            "state": sanitize_sensitive_text(state, limit=64),
            "pending_count": max(0, int(pending_count or 0)),
        }
        if reason:
            entry["reason"] = reason
        with self._cache_lock:
            if event == "sync_success":
                self._last_success_at = timestamp
            elif event == "sync_failure":
                self._last_failure_at = timestamp
                self._last_failure_reason = reason
        try:
            self._queue.put_nowait(entry)
            return True
        except queue.Full:
            with self._cache_lock:
                self._dropped_count += 1
            return False

    def summary(self):
        with self._cache_lock:
            return {
                "last_success_at": self._last_success_at,
                "last_failure_at": self._last_failure_at,
                "last_failure_reason": self._last_failure_reason,
                "dropped_log_count": self._dropped_count,
                "log_path": self.path,
            }

    def flush(self):
        self._queue.join()

    def _rotate(self):
        if self.backup_count <= 0:
            try:
                os.remove(self.path)
            except FileNotFoundError:
                pass
            return
        oldest = f"{self.path}.{self.backup_count}"
        try:
            os.remove(oldest)
        except FileNotFoundError:
            pass
        for index in range(self.backup_count - 1, 0, -1):
            source = f"{self.path}.{index}"
            destination = f"{self.path}.{index + 1}"
            try:
                os.replace(source, destination)
            except FileNotFoundError:
                pass
        try:
            os.replace(self.path, f"{self.path}.1")
        except FileNotFoundError:
            pass

    def _write_entry(self, entry):
        os.makedirs(self.directory, exist_ok=True)
        encoded = (
            json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        try:
            current_size = os.path.getsize(self.path)
        except OSError:
            current_size = 0
        if current_size and current_size + len(encoded) > self.max_bytes:
            self._rotate()
        with open(self.path, "ab") as target:
            target.write(encoded)

    def _writer_loop(self):
        while True:
            entry = self._queue.get()
            try:
                if entry is None:
                    return
                self._write_entry(entry)
            except OSError:
                with self._cache_lock:
                    self._dropped_count += 1
            finally:
                self._queue.task_done()


def format_diagnostic_report(snapshot):
    snapshot = dict(snapshot or {})
    login_state = sanitize_sensitive_text(snapshot.get("login_state"), 80) or "알 수 없음"
    pending_count = max(0, int(snapshot.get("pending_count") or 0))
    last_success = sanitize_sensitive_text(snapshot.get("last_success_at"), 80) or "기록 없음"
    failure_reason = safe_failure_reason(snapshot.get("last_failure_reason"))
    last_failure = sanitize_sensitive_text(snapshot.get("last_failure_at"), 80) or "기록 없음"
    sync_state = sanitize_sensitive_text(snapshot.get("sync_state"), 80) or "알 수 없음"
    dropped = max(0, int(snapshot.get("dropped_log_count") or 0))
    return "\n".join((
        "작가님 힘내세요 · 동기화 진단",
        f"로그인 상태: {login_state}",
        f"현재 동기화 상태: {sync_state}",
        f"서버 대기 문서: {pending_count}건",
        f"마지막 성공 시각: {last_success}",
        f"최근 실패 시각: {last_failure}",
        f"최근 실패 원인: {failure_reason}",
        f"누락된 진단 로그: {dropped}건",
        "민감정보와 원고 본문은 포함되지 않습니다.",
    ))
