"""Multi-document writing UI without the global SyncManager dispatcher."""
import threading
import time
from contextlib import contextmanager

from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTreeWidget, QTreeWidgetItem, QLabel, QPushButton, QCheckBox, QInputDialog, QDialog,
    QTextEdit, QTabWidget)

from integrated_editor_plan import PROJECT_NAME
from integrated_editor_store import require
from integrated_editor_sync import AutoSync
from text_editor import SmartTextEdit


MESSAGES = {
    'PROTECTED_NODE': '기존 보존 항목입니다. 새 시험 문서에서 편집하세요.',
    'CONFLICT_REVIEW_REQUIRED': '충돌이 보존되었습니다. 충돌 내용을 확인하세요.',
    'EXECUTION_LIMIT_REACHED': '승인된 요청량 또는 실행 시간이 끝났습니다.',
    'EMPTY_AUTO_LIMIT_REACHED': '자동 수신 허용량을 모두 사용해 자동 동기화를 껐습니다.',
    'AUTOMATIC_POLICY_REQUIRED': '자동 수신 한도 정책이 없어 자동 동기화를 시작할 수 없습니다.',
    'AUTOMATIC_COUNTER_INVALID': '자동 수신 사용량을 확인할 수 없어 자동 동기화를 중단했습니다.',
    'WRITE_OUTSIDE_SCOPE': '이 항목은 현재 동기화 실행 범위에 포함되지 않았습니다.',
    'AUTHORITY_ENDED': '동기화 실행 조건이 변경되어 멈췄습니다. 편집 내용은 보존됩니다.',
    'RECEIPT_NOT_FOUND_OR_AMBIGUOUS': '서버 반영 결과를 확정하지 못했습니다. 재전송하지 않고 보존합니다.',
    'NAME_COLLISION': '같은 위치에 같은 이름의 항목이 있습니다.',
    'SAVE_DRAFT_BEFORE_STRUCTURE_CHANGE': '초안을 먼저 저장한 뒤 삭제·복원을 진행하세요.',
    'EXTERNAL_FILE_CHANGE_PRESERVED': '외부에서 바뀐 파일이 있어 덮어쓰지 않았습니다.',
}


class SyncWorker(QThread):
    outcome = pyqtSignal(bool, str)

    def __init__(self, call, parent):
        super().__init__(parent)
        self.call = call

    def run(self):
        try:
            self.outcome.emit(True, self.call())
        except Exception as error:
            # Never put an HTTP exception URL, token or manuscript in a status label.
            code = str(error) if str(error) in MESSAGES else type(error).__name__
            self.outcome.emit(False, code)


class BoundaryTextEdit(SmartTextEdit):
    """Label input sources locally without changing SmartTextEdit behavior."""
    input_source = 'text_change'

    @contextmanager
    def input_boundary(self, source):
        previous = self.input_source
        self.input_source = source
        try:
            yield
        finally:
            self.input_source = previous

    def insertFromMimeData(self, source):
        with self.input_boundary('paste'):
            super().insertFromMimeData(source)

    def keyPressEvent(self, event):
        source = 'enter' if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) else 'key'
        with self.input_boundary(source):
            super().keyPressEvent(event)

    def inputMethodEvent(self, event):
        with self.input_boundary('input_method'):
            super().inputMethodEvent(event)


class IntegratedWritingWidget(QWidget):
    def __init__(self, store, *, network_factory=None):
        super().__init__()
        self.store, self.network_factory = store, network_factory
        self.network = self.scheduler = self.worker = None
        self.active_id, self.loading, self.closing = None, False, False
        self.draft_persistence_failed = False
        self.foreground = threading.Event()
        if QApplication.instance().applicationState() == Qt.ApplicationState.ApplicationActive:
            self.foreground.set()
        self.last_message = '로컬 집필 준비 · 서버 연결 전'
        self.setWindowTitle('집필 — ' + PROJECT_NAME + ' — Staging')
        self.resize(1150, 800)
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        layout.addLayout(bar)
        self.buttons = {}
        for key, title, callback in (
            ('save', '저장', lambda: self.save(source='save_button')), ('document', '새 문서', lambda: self.create('document')),
            ('folder', '새 폴더', lambda: self.create('folder')), ('rename', '이름 변경', self.rename),
            ('move', '이동', self.move), ('up', '위로', lambda: self.reorder(-1)),
            ('down', '아래로', lambda: self.reorder(1)), ('delete', '삭제', lambda: self.lifecycle(True)),
            ('restore', '복원', lambda: self.lifecycle(False)), ('conflict', '충돌 보기', self.show_conflicts)):
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, fn=callback: self.perform(fn))
            bar.addWidget(button)
            self.buttons[key] = button
        netbar = QHBoxLayout()
        layout.addLayout(netbar)
        self.autosave = QCheckBox('자동저장')
        self.autosave.setChecked(True)
        self.autosync = QCheckBox('자동 동기화')
        self.autosync.toggled.connect(self.auto_changed)
        netbar.addWidget(self.autosave)
        netbar.addWidget(self.autosync)
        for key, title, callback in (
            ('connect', '동기화 연결', self.connect_network),
            ('sync', '저장된 변경 동기화', lambda: self.start_sync(False)),
            ('receive', '수신', lambda: self.start_sync(False, True))):
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, fn=callback: self.perform(fn))
            netbar.addWidget(button)
            self.buttons[key] = button
        splitter = QSplitter()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel('문서·폴더')
        self.tree.setDragDropMode(QTreeWidget.DragDropMode.NoDragDrop)
        self.tree.currentItemChanged.connect(self.selection_changed)
        self.editor = BoundaryTextEdit()
        self.editor.textChanged.connect(self.text_changed)
        self.editor.compositionChanged.connect(self.text_changed)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.editor)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.idle_timer = QTimer(self)
        self.idle_timer.setSingleShot(True)
        self.idle_timer.setInterval(800)
        self.idle_timer.timeout.connect(lambda: self.perform(lambda: self.save(source='autosave_idle')))
        self.sync_timer = QTimer(self)
        self.sync_timer.setInterval(1000)
        self.sync_timer.timeout.connect(lambda: self.start_sync(True))
        self.sync_timer.start()
        self.save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self.save_shortcut.activated.connect(lambda: self.perform(lambda: self.save(source='save_shortcut')))
        QApplication.instance().applicationStateChanged.connect(self.application_state)
        self.rebuild_tree()
        self.autosync.setChecked(store.state().get('automatic_enabled', False))
        QTimer.singleShot(0, self.resume_approved_execution)
        self.refresh()

    def perform(self, callback):
        try:
            callback()
        except Exception as error:
            self.last_message = MESSAGES.get(str(error), '작업을 완료하지 못했습니다. 현재 내용을 보존했습니다.')
        self.refresh()

    def selected_key(self):
        item = self.tree.currentItem()
        require(item is not None, 'SELECT_NODE')
        return item.data(0, Qt.ItemDataRole.UserRole)

    def preserve(self, *, source='ui_preserve'):
        if self.active_id and not self.editor.isReadOnly():
            try:
                self.store.preserve_draft(self.active_id, self.editor.text_with_pending_input_method(), source=source)
            except Exception:
                self.draft_persistence_failed = True
                raise
            self.draft_persistence_failed = False

    def automatic_draft_wait_reason(self):
        # UI scheduling only: a direct backend cycle cannot inspect an editor's
        # in-memory text, IME or failed persistence. Never save to clear a guard.
        if getattr(self.editor, '_is_composing', False) or self.editor.has_pending_input_method():
            return '한글 조합이 끝날 때까지 자동 동기화를 기다립니다.'
        if self.draft_persistence_failed:
            return '초안 기록 실패가 해결될 때까지 자동 동기화를 기다립니다.'
        state = self.store.state()
        if self.active_id and not self.editor.isReadOnly():
            if self.editor.text_with_pending_input_method() != state['nodes'][self.active_id]['local']['content']:
                return '미저장 초안이 있어 자동 동기화를 기다립니다.'
        # Include retained drafts in other tabs/documents and after restart.
        if any(row['local']['kind'] == 'document' and not row['local']['deleted']
               and row['draft'] != row['local']['content'] for row in state['nodes'].values()):
            return '미저장 초안이 있어 자동 동기화를 기다립니다.'
        return None

    def text_changed(self):
        if self.loading or self.editor.isReadOnly():
            return
        try:
            self.preserve(source=self.editor.input_source)
        except Exception:
            self.idle_timer.stop()
            self.last_message = '초안을 기록하지 못했습니다. 이 창을 유지하고 다시 저장하세요.'
            self.refresh()
            return
        self.idle_timer.stop()
        if self.autosave.isChecked() and not getattr(self.editor, '_is_composing', False):
            self.idle_timer.start()
        self.refresh()

    def save(self, *, source='explicit'):
        if self.worker or self.active_id is None or self.editor.isReadOnly():
            return
        require(not getattr(self.editor, '_is_composing', False), 'FINISH_COMPOSITION')
        self.idle_timer.stop()
        self.preserve()
        self.store.save(self.active_id, source=source)
        self.editor.document().setModified(False)
        self.last_message = '로컬 저장 완료'

    def selection_changed(self, current, previous):
        if self.loading:
            return
        try:
            require(not self.editor.has_pending_input_method(), 'FINISH_COMPOSITION')
            self.idle_timer.stop()
            self.preserve()
            if self.active_id and self.autosave.isChecked() and not self.editor.isReadOnly():
                self.store.save(self.active_id, source='selection_change')
            self.load_selected()
        except Exception:
            self.loading = True
            self.tree.setCurrentItem(previous)
            self.loading = False
            self.last_message = '초안을 보존하지 못해 문서 전환을 중단했습니다.'
        self.refresh()

    def load_selected(self):
        key = self.selected_key()
        row = self.store.state()['nodes'][key]
        self.loading = True
        try:
            self.active_id = key if row['local']['kind'] == 'document' and not row['local']['deleted'] else None
            text = self.store.select(key) if self.active_id else ''
            self.editor.setPlainText(text)
            self.editor.setReadOnly(self.active_id is None or row['protected'])
        finally:
            self.loading = False

    def rebuild_tree(self, select=None):
        state = self.store.state()
        selected = select or self.active_id or state['selected']
        self.loading = True
        self.tree.clear()
        items = {}
        def add(parent_id, parent_item):
            candidates = [k for k, r in state['nodes'].items() if r['local']['parent'] == parent_id]
            order = next((r['local']['children'] for r in state['orders'].values()
                          if r['local']['parent'] == parent_id), [])
            candidates.sort(key=lambda k: (order.index(k) if k in order else len(order), state['nodes'][k]['local']['name']))
            for key in candidates:
                node = state['nodes'][key]['local']
                item = QTreeWidgetItem(parent_item, [node['name'] + (' · 삭제됨' if node['deleted'] else '')])
                item.setData(0, Qt.ItemDataRole.UserRole, key)
                items[key] = item
                if node['kind'] == 'folder':
                    add(key, item)
                    item.setExpanded(True)
        add(None, self.tree)
        if selected in items:
            self.tree.setCurrentItem(items[selected])
        elif items:
            self.tree.setCurrentItem(next(iter(items.values())))
        self.loading = False
        if items:
            self.load_selected()

    def parent_for_create(self):
        item = self.tree.currentItem()
        if item is None:
            return None
        node = self.store.state()['nodes'][self.selected_key()]['local']
        return node['id'] if node['kind'] == 'folder' else node['parent']

    def create(self, kind):
        name, ok = QInputDialog.getText(self, '새 문서' if kind == 'document' else '새 폴더', '이름')
        if ok:
            self.preserve()
            key = self.store.create(kind, self.parent_for_create(), name)
            self.rebuild_tree(key)

    def rename(self):
        key = self.selected_key()
        node = self.store.state()['nodes'][key]['local']
        name, ok = QInputDialog.getText(self, '이름 변경', '이름', text=node['name'])
        if ok:
            self.preserve()
            self.store.relocate(key, parent=node['parent'], name=name)
            self.rebuild_tree(key)

    def move(self):
        key = self.selected_key()
        nodes = self.store.state()['nodes']
        choices = [None] + [k for k, r in nodes.items() if r['local']['kind'] == 'folder' and not r['local']['deleted']]
        def label(k):
            if k is None:
                return '작품 최상위'
            node = nodes[k]['local']
            return label(node['parent']) + ' / ' + node['name']
        labels = [label(k) for k in choices]
        selected, ok = QInputDialog.getItem(self, '이동', '대상 폴더', labels, editable=False)
        if ok:
            self.preserve()
            self.store.relocate(key, parent=choices[labels.index(selected)], name=nodes[key]['local']['name'])
            self.rebuild_tree(key)

    def reorder(self, step):
        key = self.selected_key()
        state = self.store.state()
        parent = state['nodes'][key]['local']['parent']
        children = list(next(r['local']['children'] for r in state['orders'].values() if r['local']['parent'] == parent))
        index = children.index(key)
        if 0 <= index + step < len(children):
            self.preserve()
            children[index], children[index + step] = children[index + step], children[index]
            self.store.reorder(parent, children)
            self.rebuild_tree(key)

    def lifecycle(self, deleted):
        key = self.selected_key()
        self.preserve()
        self.store.lifecycle(key, deleted)
        self.rebuild_tree(key)

    def connect_network(self):
        require(self.worker is None and self.network_factory is not None, 'NO_EXECUTION_SCOPE')
        if self.network:
            self.network.close()
        self.network = self.network_factory(self.store, self.foreground.is_set)
        self.scheduler = AutoSync(self.network, clock=time.monotonic)
        self.scheduler.enabled = self.autosync.isChecked()
        with self.store.change('execution_selected') as state:
            state['active_run'] = self.network.run_id
        self.last_message = '동기화 실행 범위 연결됨'

    def auto_changed(self, checked):
        state = self.store.state()
        run_id = state.get('active_run')
        reason = self.store.automatic_stop_reason(run_id) if run_id in state['runs'] else None
        if checked and reason:
            checked = False
            self.last_message = MESSAGES[reason]
        self.autosync.blockSignals(True)
        self.autosync.setChecked(checked)
        self.autosync.blockSignals(False)
        if state.get('automatic_enabled', False) != checked:
            with self.store.change('automatic_preference') as state:
                state['automatic_enabled'] = checked
        if self.scheduler:
            self.scheduler.enabled = checked
        if hasattr(self, 'status'):
            self.refresh()

    def resume_approved_execution(self):
        if self.closing:
            return
        state = self.store.state()
        run_id = state.get('active_run')
        if not self.foreground.is_set() or not self.network_factory or not state.get('automatic_enabled') or not run_id:
            return
        def resume():
            reason = self.store.automatic_stop_reason(run_id)
            require(reason is None, reason)
            self.store.check_run(run_id)
            self.connect_network()
            if self.network.run_id != run_id:
                self.network.close()
                self.network = self.scheduler = None
                require(False, 'EXECUTION_SCOPE_CHANGED')
        self.perform(resume)

    def start_sync(self, automatic, receive_only=False):
        if self.worker or not self.network or not self.foreground.is_set() or self.closing:
            return
        if self.editor.has_pending_input_method():
            return
        if automatic and (not self.autosync.isChecked() or not self.scheduler.enabled
                          or time.monotonic() < self.scheduler.next_at):
            return
        try:
            if automatic:
                reason = self.automatic_draft_wait_reason()
                if reason:
                    self.last_message = reason
                    self.refresh()
                    return
            self.preserve()
        except Exception:
            self.last_message = '초안 기록 실패로 동기화를 시작하지 않았습니다.'
            self.refresh()
            return
        call = self.scheduler.tick if automatic else lambda: self.network.cycle(receive_only=receive_only)
        self.idle_timer.stop()
        self.worker = SyncWorker(call, self)
        self.worker.outcome.connect(self.sync_outcome)
        self.worker.finished.connect(self.sync_finished)
        self.worker.start()
        self.refresh()

    def sync_outcome(self, ok, value):
        self.last_message = ({'sent': '변경 송신 완료', 'received': '수신 완료', 'recovered': '송신 결과 복구 완료',
            'conflict': '충돌 보존됨', 'blocked': '동기화 보류됨', 'retry_wait': '연결 복구 대기',
            'busy': '다른 동기화 진행 중', 'idle': '동기화 대기'}.get(value, value) if ok
            else MESSAGES.get(value, '동기화를 완료하지 못했습니다. 저장 내용과 요청을 보존했습니다.'))
        # Editing can continue during I/O. Reload the latest durable draft, never the sent snapshot.
        self.rebuild_tree()
        self.refresh()

    def sync_finished(self):
        self.worker.deleteLater()
        self.worker = None
        if self.active_id:
            self.editor.setReadOnly(self.store.state()['nodes'][self.active_id]['protected'])
        self.refresh()
        if self.closing:
            self.close()

    def application_state(self, state):
        if state == Qt.ApplicationState.ApplicationActive:
            self.foreground.set()
        else:
            self.foreground.clear()
        if self.scheduler:
            self.scheduler.foreground = self.foreground.is_set()
        if self.foreground.is_set() and (self.network is None or self.scheduler.last_error == 'AUTHORITY_ENDED'):
            QTimer.singleShot(0, self.resume_approved_execution)

    def refresh(self):
        s = self.store.state()
        run_id = s.get('active_run')
        reason = self.store.automatic_stop_reason(run_id) if run_id in s['runs'] else None
        if reason:
            self.autosync.blockSignals(True)
            self.autosync.setChecked(False)
            self.autosync.blockSignals(False)
            if self.scheduler:
                self.scheduler.enabled = False
            if s.get('automatic_enabled'):
                with self.store.change('automatic_disabled') as state:
                    state['automatic_enabled'] = False
        pending = sum(j['status'] != 'completed' for j in s['jobs'])
        notice = ' · ' + MESSAGES[reason] if reason and self.last_message != MESSAGES[reason] else ''
        self.status.setText(f'{self.last_message}{notice} · 대기 작업 {pending} · 충돌 {len(s["conflicts"])}')
        for key in ('connect', 'sync', 'receive'):
            self.buttons[key].setEnabled(self.worker is None and (key == 'connect' or self.network is not None))
        self.tree.setEnabled(self.worker is None)
        for key in ('save', 'document', 'folder', 'rename', 'move', 'up', 'down', 'delete', 'restore'):
            self.buttons[key].setEnabled(self.worker is None)
        if self.worker:
            self.editor.setReadOnly(True)

    def show_conflicts(self):
        records = self.store.state()['conflicts']
        dialog = QDialog(self)
        dialog.setWindowTitle('보존된 충돌')
        dialog.resize(850, 550)
        layout = QVBoxLayout(dialog)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        for field, title in (('base', '기준'), ('local', '로컬'), ('remote', '서버')):
            view = QTextEdit()
            view.setReadOnly(True)
            parts = []
            for record in records:
                for detail in record.get('details', [record]):
                    node = detail.get(field)
                    if node is None:
                        parts.append('아직 확인되지 않은 항목')
                    else:
                        parts.append(node.get('name', '정렬') + '\n' + node.get('content', '구조 변경'))
                    if field == 'local' and detail.get('draft') is not None:
                        parts.append('보존된 초안\n' + detail['draft'])
            view.setPlainText('\n\n'.join(parts) or '보존된 충돌이 없습니다.')
            tabs.addTab(view, title)
        dialog.exec()

    def closeEvent(self, event):
        self.idle_timer.stop()
        self.sync_timer.stop()
        try:
            self.preserve()
        except Exception:
            self.last_message = '초안을 보존하지 못했습니다. 창을 유지합니다.'
            event.ignore()
            self.refresh()
            return
        self.closing = True
        self.foreground.clear()
        if self.worker:
            self.closing = True
            event.ignore()
            return
        if self.network:
            self.network.close()
        event.accept()
