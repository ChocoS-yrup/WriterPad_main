"""Dedicated candidate startup: no ordinary window, sync manager or recovery."""
import hashlib
import os
from pathlib import Path
import sys

from PyQt6.QtCore import QThread, pyqtSignal, Qt, QTimer, QLockFile, QEvent
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QPushButton, QTextEdit

from body_validation_transport import ForegroundTicket
from general_validation_boundary import GeneralValidationDenied
from general_validation_plan import PROJECT_NAME, STAGING_URL, PATH, PLAN_ID


def available_action(journals):
    """Read only our fixed stage receipts; never resume an incomplete attempt."""
    import json
    from general_validation_control import pending_controls
    if pending_controls(journals):
        return 'restore'
    next_action = 'prepare'
    stages = (('prepare','create'),('create','finish'),('finish',None))
    for index,(action,next_name) in enumerate(stages):
        path = Path(journals)/(PLAN_ID+'-'+action+'.jsonl')
        if not path.exists():
            if any((Path(journals)/(PLAN_ID+'-'+later+'.jsonl')).exists() for later,_ in stages[index+1:]):
                return None
            return next_action
        try:
            if json.loads(path.read_text('utf-8').splitlines()[-1]).get('event') != 'completed':
                return None
        except (ValueError,OSError,IndexError):
            return None
        next_action = next_name
    return None

INSTALL_ROOT = Path(r'D:\안티그래비티\scratch\집필프로그램')


def runtime_appdata(*, editor=False):
    from general_validation_build import GENERAL_VALIDATION_ONLY
    from general_editor_build import GENERAL_EDITOR_ONLY
    from general_validation_service import require
    from body_validation_service import DEVICE_ID
    require((GENERAL_EDITOR_ONLY if editor else GENERAL_VALIDATION_ONLY) and getattr(sys,'frozen',False), 'GENERAL_CANDIDATE_REQUIRED')
    require(Path(sys.executable).parent.resolve() == INSTALL_ROOT.resolve(), 'INSTALLATION_PATH_CHANGED')
    require(not any(os.environ.get(k) for k in ('ANTIGRAVITY_PROFILE','ANTIGRAVITY_ROOT_DIR',
        'ANTIGRAVITY_APP_DATA_DIR','ANTIGRAVITY_FORCE_PROJECT_ID','ANTIGRAVITY_INSTANCE_KEY')), 'RUNTIME_OVERRIDE_REFUSED')
    appdata = Path(os.environ['LOCALAPPDATA'])/'AntigravityWriter'
    device_file = appdata/'.device_id'
    require(device_file.read_text('utf-8').strip() == DEVICE_ID, 'DEVICE_CHANGED')
    return appdata


def restore_controls_only(*, editor=False):
    from general_validation_control import restore_pending_controls
    from sync_v2_store import SyncV2Store
    appdata = runtime_appdata(editor=editor)
    store = SyncV2Store.open_existing_current_schema(str(appdata/'sync_v2.sqlite3'))
    key = store.local_key_for(str(INSTALL_ROOT/'작품목록'/PROJECT_NAME/'집필모드'))
    if editor:
        from general_editor_plan import PLAN_ID as editor_plan
        restore_pending_controls(store,key,appdata/'general-editor-20260913',plan_id=editor_plan)
    else:
        restore_pending_controls(store,key,appdata/'general-validation-20260912')


def open_service(ticket, *, editor=False):
    from general_validation_transport import GeneralTransport
    from general_validation_service import GeneralValidationService, require
    from general_validation_control import pending_controls
    from body_validation_service import DEVICE_ID
    from cloud_config import load_cloud_client_config
    from sync_manager import SyncManager
    from sync_v2_store import SyncV2Store
    from project_manager_writing import WritingProjectManager
    from security_manager import SecurityManager
    appdata = runtime_appdata(editor=editor)
    device_file = appdata/'.device_id'
    require(not pending_controls(appdata/'general-validation-20260912'), 'CONTROL_RESTORE_REQUIRED')
    if editor:
        from general_editor_plan import PLAN_ID as editor_plan
        require(not pending_controls(appdata/'general-editor-20260913',plan_id=editor_plan), 'CONTROL_RESTORE_REQUIRED')
    config = load_cloud_client_config(Path(sys._MEIPASS))
    require(config.is_ready and config.url == STAGING_URL, 'STAGING_REQUIRED')
    ticket.check()
    transport = GeneralTransport(ticket)
    client = None
    try:
        # Static factory only. It may refresh the saved login; no SyncManager exists.
        client = SyncManager.create_supabase_client(config,http_transport=transport,preserve_rejected_session=True)
        ticket.check()
        require(client is not None and getattr(client,'_antigravity_authenticated',False), 'SAVED_LOGIN_REQUIRED')
        user, session = client.auth.get_user().user, client.auth.get_session()
        require(session is not None and user is not None and str(user.id) == str(session.user.id), 'AUTH_SESSION_CHANGED')
        ticket.check()
        transport.bind_verified()
        token_sha = hashlib.sha256(session.access_token.encode()).digest()
        def session_current():
            ticket.check()
            access,_ = SecurityManager.get_supabase_session()
            return (hashlib.sha256(access.encode()).digest() == token_sha
                    and device_file.read_text('utf-8').strip() == DEVICE_ID)
        store = SyncV2Store.open_existing_current_schema(str(appdata/'sync_v2.sqlite3'))
        # Neither initialize_project nor create_detached: both may modify files.
        wpm = object.__new__(WritingProjectManager)
        wpm.current_project = PROJECT_NAME
        wpm.writing_root_path = str(INSTALL_ROOT/'작품목록'/PROJECT_NAME/'집필모드')
        journals = appdata/('general-editor-20260913' if editor else 'general-validation-20260912')
        journals.mkdir(exist_ok=True)
        service_class = GeneralValidationService
        if editor:
            from general_editor_service import EditorService
            service_class = EditorService
        return service_class(store=store,wpm=wpm,client=client,transport=transport,ticket=ticket,
            journal_dir=journals,device_id=DEVICE_ID,account_id=str(user.id),access_token=session.access_token,
            session_current=session_current)
    except Exception:
        if client:
            SyncManager._close_supabase_client(client)
        else:
            transport.close()
        SyncManager.release_auth_lease()
        raise


class ValidationWorker(QThread):
    outcome = pyqtSignal(bool,str)

    def __init__(self,action,ticket,parent=None):
        super().__init__(parent)
        self.action,self.ticket = action,ticket

    def run(self):
        service = None
        success,text = False,''
        try:
            if self.action == 'restore':
                restore_controls_only()
                self.outcome.emit(True,'중단된 실행의 관문을 복원했습니다. 송수신하지 않았습니다.\n창을 종료하고 실행 기록을 전달해 주세요.')
                return
            service = open_service(self.ticket)
            result = service.run(self.action)
            self.ticket.check()
            text = ('인증과 서버·로컬 기준 대조를 마쳤습니다. 송신하지 않았습니다.' if self.action == 'prepare'
                else f"revision {result['revision']} / {result['bytes']} bytes\nSHA-256 {result['sha256']}")
            success = True
        except Exception as error:
            from bidirectional_sync_scope import ScopeDenied
            code = str(error) if isinstance(error,(GeneralValidationDenied,ScopeDenied)) else type(error).__name__
            text = '작업을 멈췄습니다. 다시 누르지 말고 실행 기록을 전달해 주세요.\n'+code
        finally:
            if service:
                from sync_manager import SyncManager
                try:
                    SyncManager._close_supabase_client(service.client)
                finally:
                    SyncManager.release_auth_lease()
            if self.action != 'restore' or text:
                self.outcome.emit(success,text)


class ValidationWindow(QWidget):
    def __init__(self,available='prepare'):
        super().__init__()
        self.worker = self.ticket = None
        self.closing = False
        self.last_action,self.last_success = None,False
        self.setWindowTitle('일반 본문 양방향 검증 — Staging')
        self.resize(760,460)
        layout = QVBoxLayout(self)
        label = QLabel(PROJECT_NAME+'\n'+PATH)
        label.setWordWrap(True)
        layout.addWidget(label)
        self.buttons = {}
        for action,title in (('prepare','1. 인증·서버·로컬 기준 확인'),
                             ('create','2. Windows 본문 생성·송신 1회'),
                             ('finish','3. iPad 송신 완료 후 받기·Windows 송신 1회'),
                             ('restore','중단된 실행의 관문만 복원 — 송수신 없음')):
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False,a=action:self.start_action(a))
            self.buttons[action] = button
            layout.addWidget(button)
        for action,button in self.buttons.items():
            button.setEnabled(action == available)
        self.buttons['restore'].setVisible(available == 'restore')
        self.stop_button = QPushButton('작업 중지')
        self.stop_button.clicked.connect(self.cancel)
        self.stop_button.setEnabled(False)
        layout.addWidget(self.stop_button)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlainText('아직 서버에 연결하지 않았습니다. 승인된 단계의 버튼만 사용하세요.\n'
            'Windows 생성·송신 완료 뒤 iPad에서 수신·송신을 진행합니다.\n'
            '작업 중 창을 벗어나거나 최소화하면 이 작업의 권한이 종료됩니다.')
        layout.addWidget(self.log)

    def start_action(self,action):
        if self.worker or QApplication.applicationState() != Qt.ApplicationState.ApplicationActive:
            return
        if action not in self.buttons or not self.buttons[action].isEnabled():
            return
        for button in self.buttons.values():
            button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.last_action,self.last_success = action,False
        self.ticket = ForegroundTicket()
        self.worker = ValidationWorker(action,self.ticket,self)
        self.worker.outcome.connect(self.show_outcome)
        self.worker.finished.connect(self.finish_action)
        self.log.append('지정 작품을 확인하고 있습니다. 이 창을 유지해 주세요.')
        self.worker.start()

    def show_outcome(self,success,text):
        self.last_success = success
        self.log.append(text)
        if success and self.last_action == 'create':
            self.log.append('본문 revision 1과 순서 revision 2, 관문 복원을 확인했습니다. 이제 iPad 단계를 진행하세요.')
        elif success and self.last_action == 'finish':
            self.log.append('Windows revision 3 송신과 관문 복원을 확인했습니다. 이제 iPad에서 최종 수신하세요.')

    def finish_action(self):
        self.worker.deleteLater()
        self.worker = None
        self.stop_button.setEnabled(False)
        if not self.last_success:
            # A failed restore never enables transmission. Only local undo may
            # be retried; durable stage reservations continue to block writes.
            appdata = Path(os.environ.get('LOCALAPPDATA',''))/'AntigravityWriter'
            if available_action(appdata/'general-validation-20260912') == 'restore':
                self.buttons['restore'].setVisible(True)
                self.buttons['restore'].setEnabled(True)
        if self.last_success:
            next_action = {'prepare':'create','create':'finish'}.get(self.last_action)
            if next_action:
                self.buttons[next_action].setEnabled(True)
        if self.closing:
            self.close()

    def cancel(self):
        if self.ticket:
            self.ticket.cancel()

    def application_state_changed(self,state):
        if state != Qt.ApplicationState.ApplicationActive:
            self.cancel()

    def changeEvent(self,event):
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            self.cancel()
        super().changeEvent(event)

    def closeEvent(self,event):
        self.cancel()
        if self.worker:
            self.closing = True
            event.ignore()
        else:
            event.accept()


def run_general_validation_app(argv):
    app = QApplication([argv[0]])
    lock = None
    smoke = '--general-validation-startup-smoke-test' in argv
    available = 'prepare'
    if not smoke:
        appdata = Path(os.environ['LOCALAPPDATA'])/'AntigravityWriter'
        if not appdata.is_dir():
            return 2
        lock = QLockFile(str(appdata/'general-validation-window.lock'))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
            return 3
        available = available_action(appdata/'general-validation-20260912')
    window = ValidationWindow(available)
    app.applicationStateChanged.connect(window.application_state_changed)
    if smoke:
        QTimer.singleShot(100,window.close)
    window.show()
    result = app.exec()
    if lock:
        lock.unlock()
    return result
