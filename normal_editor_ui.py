"""The product writing UI with an injected single-document local session.

Reuses its binder, editor, Ctrl+S handler and WritingController debounce. It does
not initialize the ordinary project selector, singleton SyncManager or timers.
"""
from types import SimpleNamespace
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QEvent
from PyQt6.QtWidgets import QWidget, QPushButton, QLabel, QTreeWidgetItem, QAbstractItemView, QApplication, QCheckBox

from mode_writing import WritingModeWidget
from writing_controller import WritingController
from general_editor_ui import EditorTicket, EditorWindow, close_service
from general_editor_service import error_code
import normal_editor_plan as plan


class NormalWorker(QThread):
    outcome = pyqtSignal(bool,object)

    def __init__(self, action, local, factory, ticket, closer, parent):
        super().__init__(parent)
        self.action,self.local,self.factory,self.ticket,self.closer = action,local,factory,ticket,closer

    def run(self):
        service = None
        try:
            service = self.factory(self.ticket,self.local)
            self.outcome.emit(True,getattr(service,self.action)())
        except Exception as error:
            self.outcome.emit(False,error)
        finally:
            if service is not None:self.closer(service)


class NormalWritingWidget(WritingModeWidget):
    def __init__(self, local, *, network_factory, closer=close_service, is_active=None):
        QWidget.__init__(self)
        self.local,self.network_factory,self.closer = local,network_factory,closer
        self.foreground_override = is_active
        self.is_active = is_active or (lambda:QApplication.applicationState()==Qt.ApplicationState.ApplicationActive)
        self.worker = self.ticket = None
        self.saving,self.loading,self.closing = False,True,False
        self.persistence_failed = False
        self.last_error = ''
        self.pm = SimpleNamespace(current_project=plan.PROJECT_NAME,global_config={})
        self._initial_last_active,self._initial_split_mode_enabled = 'left',False
        self.current_loaded_file_left,self.current_loaded_file_right = plan.PATH,None
        self.is_dirty_left,self.is_dirty_right = False,False
        self.loaded_versions = {}
        self.wpm = SimpleNamespace(write_text_file=local.write)
        self.sync_manager = SimpleNamespace(can_save_path=lambda p:p==plan.PATH,
            would_erase_nonempty_document=lambda p,t:t=='',report_empty_content_guard=lambda p:'빈 본문 초안 보존됨',
            upload_content_async=self.enqueue,upload_autosave_async=lambda *a,**k:None,
            report_server_queue_failure=self.queue_failed,is_v2_enabled=True)
        self.controller = WritingController(self.wpm,self.sync_manager,self.pm,'normal-editor',
            lambda:[plan.PATH],lambda path:WritingModeWidget._editor_text_for_background_save(self.left_editor),
            self.on_idle_autosave_persisted)
        WritingModeWidget.init_ui(self)
        self.setWindowTitle('집필 — '+plan.PROJECT_NAME+' — Staging')
        self.resize(1100,800)
        self.top_toolbar.addWidget(QLabel('지정 문서 · 수동 송수신'))
        self.autosave_toggle = QCheckBox('자동저장')
        self.autosave_toggle.setChecked(True)
        self.autosave_toggle.toggled.connect(self.autosave_changed)
        self.top_toolbar.addWidget(self.autosave_toggle)
        self.network_buttons = {}
        for action,title in [('send','저장된 변경 송신'),('receive','서버 변경 수신'),('recover','송신 결과 확인')]:
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False,a=action:self.start_network(a))
            self.top_toolbar.addWidget(button)
            self.network_buttons[action] = button
        self.recover_button = QPushButton('로컬 저장·대기열 복구')
        self.recover_button.clicked.connect(self.recover_local)
        self.top_toolbar.addWidget(self.recover_button)
        self.result_label = QLabel('서버 연결 대기')
        self.result_label.setWordWrap(True)
        self.layout().addWidget(self.result_label)
        # Keep the familiar UI, while every unrelated action remains inert.
        for name in ('toggle_btn','btn_toggle_split','btn_global_search','btn_padding','btn_open_backup',
                     'btn_open_conflict','btn_send_to_assistant','btn_save_right'):
            getattr(self,name).setEnabled(False)
        self.rename_shortcut.setEnabled(False)
        self.binder_tree.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self.binder_tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.binder_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.binder_tree.blockSignals(True)
        parent = QTreeWidgetItem(self.binder_tree,['원고'])
        item = QTreeWidgetItem(parent,[plan.NAME])
        item.setData(0,Qt.ItemDataRole.UserRole,plan.PATH)
        parent.setExpanded(True)
        self.binder_tree.setCurrentItem(item)
        self.binder_tree.blockSignals(False)
        self.left_editor.setPlainText(local.draft())
        self.left_editor.setReadOnly(False)
        self.left_editor.document().setModified(local.draft()!=local.expected_disk)
        self.is_dirty_left = local.draft()!=local.expected_disk
        self.lbl_current_doc.setText(plan.NAME)
        self.controller.accept_persisted_snapshot(plan.PATH,local.expected_disk)
        self.loading = False
        self.refresh_status()
        QApplication.instance().applicationStateChanged.connect(self.application_state_changed)

    def set_active_editor(self,editor):
        self.active_editor = editor  # No global preference writes in this scope.

    def _save_binder_width(self):pass
    def persist_editor_view_states(self):pass
    def _snap_editor_splitter_to_center(self,*args):pass
    def save_tree_state(self,*args):pass
    def save_tree_order(self,*args):pass
    def on_tree_item_expanded(self,*args):pass
    def on_tree_current_item_changed(self,*args):pass
    def on_tree_item_changed(self,*args):pass
    def on_tree_editor_closed(self,*args):pass
    def handle_item_moved(self,*args):pass
    def start_rename_item(self):pass
    def show_tree_context_menu(self,*args):pass
    def show_global_search(self):pass
    def show_local_search(self):pass
    def schedule_editor_metadata_refresh(self,*args):pass
    def _flush_editor_metadata_updates(self):pass
    def _show_storage_status_details(self):self.refresh_status()
    def update_tree_icon(self,*args):pass
    def on_sync_finished(self,*args):pass

    def on_editor_text_changed(self):
        if self.loading or self.saving or self.worker:return
        self.is_dirty_left = True
        try:
            self.local.preserve_draft(self.left_editor.toPlainText())
            if not self.autosave_toggle.isChecked() or self.left_editor.toPlainText()=='' or self._editor_is_composing(self.left_editor):
                self.controller.idle_timer.stop()
            else:
                self.controller.notify_text_changed(plan.PATH,user_initiated=False)
            self.persistence_failed = False
        except Exception as error:
            self.persistence_failed = True
            self.last_error = error_code(error)
        self.refresh_status()

    def autosave_changed(self,enabled):
        self.controller.idle_timer.stop()
        if enabled and not self.worker and self.is_dirty_left:
            self.on_editor_text_changed()

    def enqueue(self,*args,**kwargs):
        try:
            self.local.enqueue_saved()
        except Exception as error:
            self.queue_failed(plan.PATH,error)
        self.refresh_status()

    def queue_failed(self,path,error):
        self.last_error = error_code(error)
        self.refresh_status()

    def manual_save(self):
        if self.worker or self.saving:return
        self.saving = True
        try:
            self.last_error = ''
            if self.left_editor.toPlainText()=='':
                self.local.preserve_draft('')
                raise ValueError('empty draft')
            WritingModeWidget.manual_save(self)
        except Exception as error:
            self.last_error = error_code(error)
        finally:
            self.saving = False
            self.refresh_status()

    def on_idle_autosave_persisted(self,path,content,success):
        if success:
            self.is_dirty_left = False
            self.left_editor.document().setModified(False)
        self.refresh_status()

    def refresh_status(self):
        if not hasattr(self,'network_buttons'):return
        try:status = self.local.status()
        except Exception as error:status = error_code(error)
        self.lbl_storage_status.setText(status)
        for button in self.network_buttons.values():
            button.setEnabled(not self.worker and not self.persistence_failed)
        self.recover_button.setEnabled(not self.worker)
        self.autosave_toggle.setEnabled(not self.worker)
        if self.last_error:self.result_label.setText(self.last_error)

    def recover_local(self):
        try:
            self.local.recover_local()
            self.last_error = ''
            self.result_label.setText('로컬 복구 완료. 송신하지 않았습니다.')
        except Exception as error:self.last_error = error_code(error)
        self.refresh_status()

    def start_network(self,action):
        if self.worker or self.persistence_failed or not self.is_active():return
        if self._editor_is_composing(self.left_editor):
            self.last_error = '글자 입력을 마친 뒤 다시 실행하세요.'
            self.refresh_status(); return
        try:
            self.local.preserve_draft(self.left_editor.toPlainText())
            self.local.check()
            if action=='send':self.local.enqueue_saved()
        except Exception as error:
            self.last_error = error_code(error); self.refresh_status(); return
        self.controller.idle_timer.stop()
        self.left_editor.setReadOnly(True)
        self.ticket = EditorTicket(EditorWindow.foreground_check(self))
        self.worker = NormalWorker(action,self.local,self.network_factory,self.ticket,self.closer,self)
        self.worker.outcome.connect(self.network_outcome)
        self.worker.finished.connect(self.network_finished)
        self.last_error = ''
        self.refresh_status()
        self.worker.start()

    def network_outcome(self,success,value):
        if success:
            self.result_label.setText(value.get('note') or f"revision {value['revision']} / {value['bytes']} bytes\nSHA-256 {value['sha256']}")
            self.loading = True
            self.left_editor.setPlainText(self.local.draft())
            self.loading = False
            self.controller.accept_persisted_snapshot(plan.PATH,self.local.expected_disk)
        else:self.last_error = error_code(value)
        self.refresh_status()

    def network_finished(self):
        self.worker.deleteLater(); self.worker = None
        self.ticket.cancel(); self.ticket = None
        self.left_editor.setReadOnly(False)
        self.refresh_status()
        if self.closing:self.close()

    def application_state_changed(self,state):
        if state!=Qt.ApplicationState.ApplicationActive and self.ticket:self.ticket.cancel()

    def changeEvent(self,event):
        if event.type()==QEvent.Type.WindowStateChange and self.isMinimized() and self.ticket:self.ticket.cancel()
        QWidget.changeEvent(self,event)

    def closeEvent(self,event):
        self.controller.stop_timers()
        if self.ticket:self.ticket.cancel()
        try:self.local.preserve_draft(self.left_editor.toPlainText())
        except Exception as error:
            self.persistence_failed = True
            self.last_error = error_code(error)
            self.refresh_status(); event.ignore(); return
        if self.worker:
            self.closing = True; event.ignore()
        else:event.accept()
