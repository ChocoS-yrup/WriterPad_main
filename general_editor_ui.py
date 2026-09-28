"""One real manuscript editor; no project switching or ordinary startup timers."""
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from PyQt6.QtCore import QThread, pyqtSignal, Qt, QTimer, QLockFile, QEvent
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QPushButton, QTextEdit

from body_validation_transport import ForegroundTicket
from general_validation_ui import open_service, restore_controls_only
from general_editor_service import available_action, error_code
import general_editor_plan as plan
from text_editor import SmartTextEdit
from writing_controller import WritingController
from mode_writing import WritingModeWidget


class EditorTicket(ForegroundTicket):
    def __init__(self,foreground):
        self.foreground = foreground
        super().__init__()

    def check(self):
        super().check()
        if not self.foreground():
            self.cancel()
            from bidirectional_sync_scope import ScopeDenied
            raise ScopeDenied('EDITOR_WINDOW_NO_LONGER_FOREGROUND')


def close_service(service):
    if service is not None:
        from sync_manager import SyncManager
        try:
            SyncManager._close_supabase_client(service.client)
        finally:
            SyncManager.release_auth_lease()


class EditorWorker(QThread):
    outcome = pyqtSignal(bool,object)

    def __init__(self,action,ticket,factory,closer,parent=None):
        super().__init__(parent)
        self.action,self.ticket,self.factory,self.closer = action,ticket,factory,closer

    def run(self):
        service = None
        try:
            if self.action=='restore':
                restore_controls_only(editor=True)
                self.outcome.emit(True,{'restored':True})
                return
            service = self.factory(self.ticket)
            stage,action = self.action.split('_')
            if action=='prepare':
                text = service.prepare(stage)
                self.ticket.check()
                self.outcome.emit(True,{'service':service,'text':text,'stage':stage})
                service = None  # Ownership transfers to the editor until local save/cancel.
            else:
                result = service.send(stage)
                self.outcome.emit(True,{'result':result})
        except Exception as error:
            self.outcome.emit(False,error)
        finally:
            self.closer(service)


class EditorWindow(QWidget):
    def __init__(self,journals,*,factory=None,closer=close_service,is_active=None):
        super().__init__()
        self.journals = Path(journals)
        self.factory = factory or (lambda ticket:open_service(ticket,editor=True))
        self.closer = closer
        self.foreground_override = is_active
        self.is_active = is_active or (lambda:QApplication.applicationState()==Qt.ApplicationState.ApplicationActive)
        self.worker = self.ticket = self.service = self.controller = None
        self.stage = None
        self.closing = self.saving = self.cancelled = False
        self.baseline_text = ''
        self.setWindowTitle('일반 편집·저장 검증 — Staging')
        self.resize(800,690)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(plan.PROJECT_NAME+'\n'+plan.PATH))
        self.instruction = QLabel('아직 서버에 연결하지 않았습니다. 승인된 단계의 버튼만 사용하세요.')
        self.instruction.setWordWrap(True)
        layout.addWidget(self.instruction)
        self.editor = SmartTextEdit()
        self.editor.setReadOnly(True)
        self.editor.textChanged.connect(self.text_changed)
        layout.addWidget(self.editor)
        self.buttons = {}
        for action,title in (
            ('manual_prepare','1. 인증·기준 확인 후 편집 시작'),
            ('manual_save','2. 편집 내용 수동 저장 — Ctrl+S'),
            ('manual_send','3. 저장된 Windows 변경 송신 1회'),
            ('auto_prepare','4. iPad 송신 완료 후 수신·자동저장 편집 시작'),
            ('auto_send','5. 자동저장된 Windows 변경 송신 1회'),
            ('restore','중단된 실행의 관문만 복원 — 송수신 없음')):
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False,a=action:self.start(a))
            self.buttons[action] = button
            layout.addWidget(button)
        self.shortcut = QShortcut(QKeySequence.StandardKey.Save,self)
        self.shortcut.activated.connect(lambda:self.start('manual_save'))
        self.stop_button = QPushButton('작업 중지')
        self.stop_button.clicked.connect(self.cancel)
        layout.addWidget(self.stop_button)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log)
        self.expiry_timer = QTimer(self)
        self.expiry_timer.setInterval(250)
        self.expiry_timer.timeout.connect(self.check_expiry)
        self.refresh()

    def refresh(self):
        action = available_action(self.journals)
        for name,button in self.buttons.items():
            button.setEnabled(not self.worker and not self.service and name==action)
        self.buttons['restore'].setVisible(action=='restore')
        if action=='done': self.instruction.setText('Windows revision 6 송신 완료. iPad 최종 수신 후 양측 앱을 종료하세요.')
        if action=='review': self.instruction.setText('중단 기록이 있습니다. 다시 실행하지 말고 기록을 전달하세요.')

    def start(self,action):
        if not self.is_active() or self.worker or self.saving or not self.buttons[action].isEnabled(): return
        if action=='manual_save':
            self.save_local('manual')
            return
        try:
            self.preserve_draft()
        except OSError as error:
            self.show_error(error)
            return
        for button in self.buttons.values(): button.setEnabled(False)
        self.cancelled = False
        self.ticket = EditorTicket(self.foreground_check())
        self.worker = EditorWorker(action,self.ticket,self.factory,self.closer,self)
        self.worker.outcome.connect(self.show_outcome)
        self.worker.finished.connect(self.worker_finished)
        self.log.append('지정 작품의 계정·기준을 확인하고 있습니다. 창을 유지해 주세요.')
        self.worker.start()

    def foreground_check(self):
        if self.foreground_override is not None:
            return self.foreground_override
        if os.name=='nt':
            # Check the OS directly even while a synchronous editor save is busy.
            # This uses window identity only; it never captures pixels.
            import ctypes
            from ctypes import wintypes
            get_foreground = ctypes.WinDLL('user32',use_last_error=True).GetForegroundWindow
            get_foreground.restype = wintypes.HWND
            expected = int(self.winId())
            return lambda:int(get_foreground() or 0)==expected
        return self.is_active

    def show_outcome(self,success,value):
        if not success:
            self.show_error(value)
            return
        if 'service' in value:
            if self.cancelled or self.closing or not self.is_active():
                value['service'].ticket.cancel()
                self.closer(value['service'])
                return
            self.attach_editor(value['service'],value['stage'],value['text'])
        elif 'result' in value:
            result = value['result']
            self.log.append(f"revision {result['revision']} / {result['bytes']} bytes\nSHA-256 {result['sha256']}")
        else:
            self.log.append('관문 복원 완료. 송수신은 재개하지 않았습니다.')

    def worker_finished(self):
        self.worker.deleteLater()
        self.worker = None
        if self.service:
            self.buttons['manual_save'].setEnabled(self.stage=='manual')
        else:
            self.refresh()
        if self.closing:self.close()

    def attach_editor(self,service,stage,text):
        self.service,self.ticket,self.stage = service,service.ticket,stage
        self.baseline_text = text
        self.editor.blockSignals(True)
        self.editor.setPlainText(text)
        self.editor.document().setModified(False)
        self.editor.blockSignals(False)
        self.editor.setReadOnly(False)
        self.editor.setFocus()
        self.controller = WritingController(None,None,SimpleNamespace(current_project=plan.PROJECT_NAME),'editor-only',
            lambda:[plan.PATH],lambda path:WritingModeWidget._editor_text_for_background_save(self.editor))
        self.controller.accept_persisted_snapshot(plan.PATH,text)
        # Keep the product debounce timer and save method; scope the whole save transaction.
        self.controller.idle_timer.timeout.disconnect()
        self.controller.idle_timer.timeout.connect(self.save_auto)
        line = plan.WINDOWS_STAGES[stage].content[len(text):].rstrip('\n')
        self.instruction.setText('본문 끝에 다음 한 줄과 줄바꿈을 추가하세요: '+line+
            (' — Ctrl+S로 저장합니다.' if stage=='manual' else ' — 입력을 멈추면 자동저장합니다.'))
        self.buttons['manual_save'].setEnabled(stage=='manual')
        self.expiry_timer.start()

    def text_changed(self):
        if not self.service or self.editor.isReadOnly() or self.saving:return
        if self.stage=='auto':
            # ID_BASED requires no editor lease. The scoped queue grants save authority.
            self.controller.notify_text_changed(plan.PATH,user_initiated=False)

    def save_auto(self):
        if self.stage=='auto' and self.editor.toPlainText()==plan.AUTO:
            self.save_local('auto')

    def save_local(self,stage):
        if self.saving or not self.service or not self.is_active() or self.cancelled:return
        self.saving = True
        service = self.service
        try:
            text = (WritingModeWidget._editor_text_for_save(self.editor) if stage=='manual'
                    else WritingModeWidget._editor_text_for_background_save(self.editor))
            def callback(files,manager):
                self.controller.wpm,self.controller.sync_manager = files,manager
                if stage=='manual':
                    panel = SimpleNamespace(current_loaded_file_left=plan.PATH,current_loaded_file_right=None,
                        is_dirty_left=True,is_dirty_right=False,left_editor=self.editor,wpm=files,sync_manager=manager,
                        pm=SimpleNamespace(current_project=plan.PROJECT_NAME),controller=self.controller,
                        on_sync_finished=lambda *a:None,lbl_current_doc=QLabel(plan.NAME,self))
                    WritingModeWidget.manual_save(panel)
                else:
                    self.controller.pending_autosave_paths.add(plan.PATH)
                    self.controller.sync_file()
            operation = service.save(stage,text,callback)
            self.baseline_text = text
            self.editor.document().setModified(False)
            self.log.append('본문 로컬 저장·계약 대기열 등록·관문 복원 완료. 아직 송신하지 않았습니다.')
            self.log.append('operation '+operation['operation_id'])
        except Exception as error:
            self.show_error(error)
        finally:
            self.saving = False
            self.release_editor()
            self.refresh()

    def release_editor(self):
        self.expiry_timer.stop()
        self.editor.setReadOnly(True)
        if self.controller:
            self.controller.idle_timer.stop()
            self.controller.deleteLater()
            self.controller = None
        if self.service:
            self.service.ticket.cancel()
            self.closer(self.service)
            self.service = None
        self.buttons['manual_save'].setEnabled(False)

    def show_error(self,error):
        code = error_code(error)
        self.log.append('작업을 멈췄습니다. 저장·송신 기록을 보존하고 다음 단계로 넘어가지 마세요.\n'+code)

    def preserve_draft(self):
        text = self.editor.toPlainText()
        if text and text!=self.baseline_text and self.journals.is_dir():
            # Separate evidence; never overwrite manuscript, queue or earlier drafts.
            with (self.journals/(plan.PLAN_ID+'-draft-'+str(uuid4())+'.txt')).open('xb') as stream:
                stream.write(text.encode('utf-8'))
                stream.flush()
                os.fsync(stream.fileno())
            self.baseline_text = text

    def cancel(self):
        self.cancelled = True
        if self.ticket:self.ticket.cancel()
        if not self.worker and not self.saving:
            try:
                self.preserve_draft()
            except OSError as error:
                self.show_error(error)
            finally:
                self.release_editor()
                self.refresh()

    def check_expiry(self):
        try:
            if self.ticket:self.ticket.check()
        except Exception as error:
            self.show_error(error)
            self.cancel()

    def application_state_changed(self,state):
        if state!=Qt.ApplicationState.ApplicationActive:self.cancel()

    def changeEvent(self,event):
        if event.type()==QEvent.Type.WindowStateChange and self.isMinimized():self.cancel()
        super().changeEvent(event)

    def closeEvent(self,event):
        self.cancel()
        if self.worker or self.saving:
            self.closing = True
            event.ignore()
        else:
            try:self.preserve_draft()
            except OSError as error:
                self.show_error(error)
                event.ignore()
                return
            event.accept()


def run_general_editor_app(argv):
    app = QApplication([argv[0]])
    smoke = '--general-editor-startup-smoke-test' in argv
    lock = None
    if smoke:
        import tempfile
        scratch = tempfile.TemporaryDirectory()
        journals = Path(scratch.name)
    else:
        from general_validation_ui import runtime_appdata
        appdata = runtime_appdata(editor=True)
        lock = QLockFile(str(appdata/'general-validation-window.lock'))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):return 3
        journals = appdata/'general-editor-20260913'
    window = EditorWindow(journals)
    app.applicationStateChanged.connect(window.application_state_changed)
    if smoke:QTimer.singleShot(100,window.close)
    window.show()
    result = app.exec()
    if lock:lock.unlock()
    if smoke:scratch.cleanup()
    return result
