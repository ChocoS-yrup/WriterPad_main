"""Validation-only startup shell. Importing/painting it opens no sync database."""
import os
from pathlib import Path
import sys

from PyQt6.QtCore import QThread, pyqtSignal, Qt, QTimer, QLockFile
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QPushButton, QTextEdit

from body_validation_transport import ForegroundTicket
from bidirectional_sync_scope import ENDPOINT, PROJECT_NAME, ScopeDenied

INSTALL_ROOT = Path(r"D:\안티그래비티\scratch\집필프로그램")


def open_service(ticket):
    """Only the explicit foreground button can reach credentials or the store."""
    from body_validation_build import BODY_VALIDATION_ONLY
    from body_validation_transport import BodyHTTPTransport
    from body_validation_service import BodyValidationService, DEVICE_ID, require
    from cloud_config import load_cloud_client_config
    from sync_manager import SyncManager
    from sync_v2_store import SyncV2Store
    from project_manager_writing import WritingProjectManager
    require(BODY_VALIDATION_ONLY and getattr(sys, "frozen", False), "VALIDATION_CANDIDATE_REQUIRED")
    require(Path(sys.executable).parent.resolve() == INSTALL_ROOT.resolve(), "APP_INSTALLATION_PATH_CHANGED")
    require(not any(os.environ.get(k) for k in ("ANTIGRAVITY_PROFILE", "ANTIGRAVITY_ROOT_DIR",
        "ANTIGRAVITY_APP_DATA_DIR", "ANTIGRAVITY_FORCE_PROJECT_ID", "ANTIGRAVITY_INSTANCE_KEY")), "RUNTIME_OVERRIDE_REFUSED")
    appdata = Path(os.environ["LOCALAPPDATA"]) / "AntigravityWriter"
    require((appdata / ".device_id").read_text("utf-8").strip() == DEVICE_ID, "DEVICE_CHANGED")
    config = load_cloud_client_config(Path(sys._MEIPASS))
    require(config.is_ready and config.url == ENDPOINT, "STAGING_CONFIG_REQUIRED")
    ticket.check()
    transport = BodyHTTPTransport(ticket)
    client = None
    try:
        client = SyncManager.create_supabase_client(config, http_transport=transport,
                                                   preserve_rejected_session=True)
        ticket.check()
        require(client is not None and getattr(client, "_antigravity_authenticated", False), "AUTH_SESSION_REQUIRED")
        # get_user validates with Auth; get_session merely retrieves the exact
        # token already retained by this client. No decoded JWT grants authority.
        user = client.auth.get_user().user
        session = client.auth.get_session()
        ticket.check()
        require(session is not None and str(session.user.id) == str(user.id), "AUTH_SESSION_CHANGED")
        transport.bind_verified_session(str(user.id), session.access_token)
        store = SyncV2Store.open_existing_current_schema(str(appdata / "sync_v2.sqlite3"))
        wpm = WritingProjectManager()
        wpm.current_project = PROJECT_NAME
        wpm.writing_root_path = str(INSTALL_ROOT / "작품목록" / PROJECT_NAME / "집필모드")
        # No initialize_project/create_detached: those may materialize folders.
        journal_dir = appdata / "body-validation-20260911"
        journal_dir.mkdir(exist_ok=True)
        service = BodyValidationService(store=store, wpm=wpm, client=client, transport=transport,
            ticket=ticket, journal_dir=journal_dir, device_id=DEVICE_ID)
        return service
    except Exception:
        if client:
            SyncManager._close_supabase_client(client)
        else:
            transport.close()
        SyncManager.release_auth_lease()
        raise


class ValidationWorker(QThread):
    outcome = pyqtSignal(bool, str)

    def __init__(self, action, ticket, parent=None):
        super().__init__(parent)
        self.action, self.ticket = action, ticket

    def run(self):
        service = None
        try:
            service = open_service(self.ticket)
            result = service.send_once() if self.action == "send" else service.receive_once()
            self.ticket.check()
            self.outcome.emit(True, f"revision {result['revision']} / {result['bytes']} bytes\nSHA-256 {result['sha256']}")
        except Exception as error:
            code = str(error) if isinstance(error, ScopeDenied) else type(error).__name__
            self.outcome.emit(False, "작업을 멈췄습니다. 다시 누르지 말고 실행 기록을 전달해 주세요.\n" + code)
        finally:
            if service:
                from sync_manager import SyncManager
                SyncManager._close_supabase_client(service.client)
                SyncManager.release_auth_lease()


class ValidationWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.ticket = None
        self.closing = False
        self.last_action = None
        self.last_success = False
        self.setWindowTitle("본문 양방향 검증 — Staging")
        self.resize(760, 460)
        layout = QVBoxLayout(self)
        label = QLabel(PROJECT_NAME + "\n메인/원고/1권/1화.txt\n일반 자동 송신은 잠겨 있습니다.")
        label.setWordWrap(True)
        layout.addWidget(label)
        self.send = QPushButton("1. Windows 검증 문장 추가·송신 1회")
        self.receive = QPushButton("2. iPad 송신 완료 후 최종 본문 받기")
        self.stop_button = QPushButton("작업 중지")
        self.stop_button.setEnabled(False)
        for button in (self.send, self.receive, self.stop_button):
            layout.addWidget(button)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlainText("아직 서버에 연결하지 않았습니다. 승인된 순서의 버튼만 사용하세요.\n"
            "Windows 송신 완료 뒤 iPad에서 수신·송신을 진행합니다.\n"
            "작업 중 창을 벗어나거나 최소화하면 이 작업의 권한이 종료됩니다.")
        layout.addWidget(self.log)
        self.send.clicked.connect(lambda: self.start_action("send"))
        self.receive.clicked.connect(lambda: self.start_action("receive"))
        self.stop_button.clicked.connect(self.cancel)

    def start_action(self, action):
        if self.worker or QApplication.applicationState() != Qt.ApplicationState.ApplicationActive:
            return
        self.send.setEnabled(False)
        self.receive.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.last_action, self.last_success = action, False
        self.ticket = ForegroundTicket()
        self.worker = ValidationWorker(action, self.ticket, self)
        self.worker.outcome.connect(self.show_outcome)
        self.worker.finished.connect(self.finish_action)
        self.log.append("지정 본문을 확인하고 있습니다. 이 창을 유지해 주세요.")
        self.worker.start()

    def show_outcome(self, success, text):
        self.last_success = success
        self.log.append(text)
        if success:
            self.log.append("Windows 송신과 lease 종료를 확인했습니다. 이제 iPad 단계를 진행하세요."
                            if self.last_action == "send" else "최종 5줄을 받았습니다. 종료 후 오프라인 관찰 단계로 진행하세요.")

    def finish_action(self):
        self.worker.deleteLater()
        self.worker = None
        self.stop_button.setEnabled(False)
        # No send retry on this window; durable reserve additionally prevents
        # replay after restart, including crashes between disk save and enqueue.
        self.receive.setEnabled(self.last_success and self.last_action == "send")
        if self.closing:
            self.close()

    def cancel(self):
        if self.ticket:
            self.ticket.cancel()

    def application_state_changed(self, state):
        if state != Qt.ApplicationState.ApplicationActive:
            self.cancel()

    def closeEvent(self, event):
        self.cancel()
        if self.worker:
            self.closing = True
            self.log.append("응답과 중지 기록을 정리한 뒤 종료합니다.")
            event.ignore()
        else:
            event.accept()


def run_body_validation_app(argv):
    app = QApplication([argv[0]])
    window = ValidationWindow()
    app.applicationStateChanged.connect(window.application_state_changed)
    lock = None
    if "--body-validation-startup-smoke-test" in argv:
        # No credentials, store, installation files, or real appdata touched.
        QTimer.singleShot(100, window.close)
    else:
        appdata = Path(os.environ["LOCALAPPDATA"]) / "AntigravityWriter"
        if not appdata.is_dir():
            return 2
        lock = QLockFile(str(appdata / "body-validation-window.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            return 3
    window.show()
    result = app.exec()
    if lock:
        lock.unlock()
    return result
