import os

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTextEdit, QLabel, QSplitter, QTextBrowser,
    QStackedWidget, QComboBox, QMenu, QGraphicsDropShadowEffect, QScrollArea, QFrame, QMessageBox,
    QSizePolicy, QCheckBox, QDialog, QListWidget, QLineEdit, QRadioButton, QSpinBox, QButtonGroup,
    QFontDialog, QTabWidget, QListWidgetItem, QGridLayout, QTabBar, QInputDialog
)
from PyQt6.QtGui import (
    QDesktopServices, QFont, QTextCursor, QGuiApplication, QTextDocument
)
from PyQt6.QtCore import pyqtSignal, Qt, QSettings, QSize, QTimer, QThread, QUrl
from datetime import datetime


def _format_server_timestamp(value):
    if not value:
        return "알 수 없음"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone()
        return parsed.strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(value)


class ServerProjectListRow(QWidget):
    def __init__(self, project, state, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        heading = QHBoxLayout()
        heading.setSpacing(8)
        title = QLabel(project.name)
        title.setStyleSheet("font-size: 12pt; font-weight: 600;")
        heading.addWidget(title, 1)

        state_label = QLabel(state)
        state_colors = {
            "가져옴": ("#7f8c8d", "#252a2e"),
            "가져오기 재개": ("#ffb74d", "#3b3022"),
            "가져올 수 있음": ("#66d9ef", "#20343a"),
            "다른 실행환경에 있음": ("#c4b5fd", "#30294a"),
            "로컬 파일 없음": ("#fca5a5", "#451f24"),
        }
        foreground, background = state_colors.get(
            state, ("#e0e0e0", "#252a2e")
        )
        state_label.setStyleSheet(
            "QLabel {"
            f"color: {foreground}; background: {background};"
            "border-radius: 8px; padding: 2px 8px; font-weight: 600;"
            "}"
        )
        heading.addWidget(state_label)
        layout.addLayout(heading)

        updated = _format_server_timestamp(project.updated_at)
        details = QLabel(
            f"최근 수정  {updated}    ·    ID  {project.masked_project_id}"
        )
        details.setStyleSheet("color: #aeb4ba; font-size: 9pt;")
        layout.addWidget(details)


class ServerProjectCatalogWorker(QThread):
    resultReady = pyqtSignal(bool, object)

    def __init__(self, service):
        super().__init__()
        self.service = service

    def run(self):
        try:
            self.resultReady.emit(True, self.service.list_projects())
        except Exception as error:
            message = getattr(
                error,
                "user_message",
                "서버 작품 목록을 조회하지 못했습니다.",
            )
            self.resultReady.emit(False, message)


class ServerProjectImportWorker(QThread):
    resultReady = pyqtSignal(bool, object)

    def __init__(self, service, project_id, server_name, local_project_name):
        super().__init__()
        self.service = service
        self.project_id = project_id
        self.server_name = server_name
        self.local_project_name = local_project_name

    def run(self):
        try:
            result = self.service.import_project(
                self.project_id,
                self.server_name,
                self.local_project_name,
            )
            self.resultReady.emit(True, result)
        except Exception as error:
            message = getattr(
                error,
                "user_message",
                "서버 작품을 가져오지 못했습니다.",
            )
            self.resultReady.emit(False, message)


class ServerProjectActionWorker(QThread):
    resultReady = pyqtSignal(bool, object)

    def __init__(self, service, action, project):
        super().__init__()
        self.service = service
        self.action = action
        self.project = project

    def run(self):
        try:
            if self.action != "trash":
                raise RuntimeError("알 수 없는 서버 작품 작업입니다.")
            result = self.service.trash_server_project(
                self.project.project_id, self.project.name
            )
            self.resultReady.emit(True, result)
        except Exception as error:
            message = getattr(
                error,
                "user_message",
                "서버 작품을 휴지통으로 이동하지 못했습니다.",
            )
            self.resultReady.emit(False, message)


class ProjectTrashWorker(QThread):
    resultReady = pyqtSignal(str, bool, object)

    def __init__(self, service, action, payload=None):
        super().__init__()
        self.service = service
        self.action = action
        self.payload = payload

    def run(self):
        try:
            if self.action == "list":
                result = self.service.list_projects()
            elif self.action == "trash":
                result = self.service.trash_project(self.payload)
            elif self.action == "restore":
                result = self.service.restore_project(self.payload)
            elif self.action == "purge":
                result = self.service.purge_project(self.payload)
            else:
                raise RuntimeError("알 수 없는 작품 휴지통 작업입니다.")
            self.resultReady.emit(self.action, True, result)
        except Exception as error:
            message = getattr(
                error,
                "user_message",
                "작품 휴지통 작업을 완료하지 못했습니다.",
            )
            self.resultReady.emit(self.action, False, message)


class ServerProjectImportDialog(QDialog):
    def __init__(
        self,
        pm,
        parent=None,
        sync_manager=None,
        store=None,
        device_id=None,
        auto_refresh=True,
    ):
        super().__init__(parent)
        from server_project_import import (
            ServerProjectCatalogService,
            ServerProjectImportService,
        )
        from sync_manager import SyncManager, load_or_create_device_id
        from sync_v2_store import SyncV2Store
        from project_trash import ProjectTrashService

        self.pm = pm
        self.sync_manager = sync_manager or SyncManager()
        self.store = store or SyncV2Store()
        self.device_id = device_id or load_or_create_device_id()
        self.catalog_service = ServerProjectCatalogService(
            self.sync_manager.supabase,
            self.store,
            self.pm.workspace_dir,
            authenticated_call=getattr(
                self.sync_manager, "_call_with_session", None
            ),
        )
        self.import_service = ServerProjectImportService(
            self.sync_manager,
            self.store,
            self.pm.workspace_dir,
            self.device_id,
        )
        self.trash_service = ProjectTrashService(
            self.pm,
            self.store,
            self.sync_manager.supabase,
            authenticated_call=getattr(
                self.sync_manager, "_call_with_session", None
            ),
        )
        self.imported_project_name = None
        self._worker = None
        self._worker_action = None
        self._worker_result = None
        self._close_when_idle = False
        self._accept_after_worker = False

        self.setWindowTitle("서버 작품 관리")
        self.setFixedSize(860, 590)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Window)

        layout = QVBoxLayout(self)
        description = QLabel(
            "서버 작품을 가져오거나 휴지통으로 이동할 수 있습니다. "
            "서버에서 삭제해도 다른 위치의 로컬 원고는 안전하게 보존됩니다."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        self.status_label = QLabel("서버 작품 목록을 새로고침해주세요.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.list_widget = QListWidget()
        self.list_widget.setFont(QFont("Malgun Gothic", 11))
        self.list_widget.setSpacing(5)
        self.list_widget.setStyleSheet(
            "QListWidget { border: 1px solid #3a3f45; padding: 5px; }"
            "QListWidget::item { border-bottom: 1px solid #343940; }"
            "QListWidget::item:selected { background: #263d4a; }"
        )
        self.list_widget.currentItemChanged.connect(
            self._on_project_selection_changed
        )
        layout.addWidget(self.list_widget)

        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("로컬 작품명:"))
        self.local_name_input = QLineEdit()
        self.local_name_input.setPlaceholderText(
            "이름 충돌 시 다른 로컬 작품명을 입력하세요"
        )
        name_layout.addWidget(self.local_name_input)
        layout.addLayout(name_layout)

        button_layout = QHBoxLayout()
        self.refresh_button = QPushButton("새로고침")
        self.open_folder_button = QPushButton("폴더 열기")
        self.server_trash_button = QPushButton("서버 휴지통으로 이동")
        self.server_trash_button.setStyleSheet(
            "background-color: #c47f00; color: white; font-weight: bold;"
        )
        self.open_trash_button = QPushButton("🗑 서버 휴지통")
        self.import_button = QPushButton("선택 작품 가져오기")
        self.cancel_button = QPushButton("취소")
        self.refresh_button.clicked.connect(self.refresh_server_projects)
        self.open_folder_button.clicked.connect(self.open_selected_folder)
        self.server_trash_button.clicked.connect(
            self.trash_selected_server_project
        )
        self.open_trash_button.clicked.connect(self.open_server_trash)
        self.import_button.clicked.connect(self.import_selected_project)
        self.cancel_button.clicked.connect(self._request_close)
        button_layout.addWidget(self.refresh_button)
        button_layout.addWidget(self.open_folder_button)
        button_layout.addWidget(self.open_trash_button)
        button_layout.addStretch(1)
        button_layout.addWidget(self.server_trash_button)
        button_layout.addWidget(self.import_button)
        button_layout.addWidget(self.cancel_button)
        layout.addLayout(button_layout)

        self.import_button.setEnabled(False)
        self.open_folder_button.setEnabled(False)
        self.server_trash_button.setEnabled(False)
        if auto_refresh:
            QTimer.singleShot(0, self.refresh_server_projects)

    @property
    def is_busy(self):
        return self._worker is not None

    def refresh_server_projects(self):
        if self.is_busy:
            return False
        worker = ServerProjectCatalogWorker(self.catalog_service)
        self.status_label.setText("서버 작품 목록을 불러오는 중입니다…")
        self._start_worker(worker, "catalog")
        return True

    def import_selected_project(self):
        if self.is_busy:
            return False
        item = self.list_widget.currentItem()
        project = (
            item.data(Qt.ItemDataRole.UserRole)
            if item is not None else None
        )
        if project is None or not project.can_import:
            QMessageBox.warning(
                self, "가져오기", "가져올 서버 작품을 선택해주세요."
            )
            return False
        local_name = self.local_name_input.text()
        if not local_name.strip():
            QMessageBox.warning(
                self, "가져오기", "사용할 로컬 작품명을 입력해주세요."
            )
            return False

        worker = ServerProjectImportWorker(
            self.import_service,
            project.project_id,
            project.name,
            local_name,
        )
        self.status_label.setText(
            f"'{project.name}' 작품의 문서를 가져오는 중입니다…"
        )
        self._start_worker(worker, "import")
        return True

    def open_selected_folder(self):
        project = self._selected_project()
        local_path = getattr(project, "local_path", "") if project else ""
        if not local_path or not os.path.isdir(local_path):
            QMessageBox.warning(
                self, "폴더 열기", "연결된 로컬 작품 폴더를 찾을 수 없습니다."
            )
            return False
        return QDesktopServices.openUrl(QUrl.fromLocalFile(local_path))

    def trash_selected_server_project(self):
        if self.is_busy:
            return False
        project = self._selected_project()
        if project is None:
            QMessageBox.warning(
                self, "서버 휴지통", "삭제할 서버 작품을 선택해주세요."
            )
            return False
        reply = QMessageBox.question(
            self,
            "서버 휴지통으로 이동",
            (
                f"'{project.name}' 작품을 서버 휴지통으로 이동하시겠습니까?\n\n"
                "모든 기기에서 이 작품의 서버 동기화가 중지됩니다. "
                "현재 컴퓨터와 다른 실행환경의 로컬 원고는 삭제하지 않습니다."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return False
        worker = ServerProjectActionWorker(
            self.trash_service, "trash", project
        )
        self.status_label.setText(
            f"'{project.name}' 작품을 서버 휴지통으로 이동하는 중입니다…"
        )
        self._start_worker(worker, "trash")
        return True

    def open_server_trash(self):
        if self.is_busy:
            return False
        dialog = ProjectTrashDialog(
            self.trash_service, self, auto_refresh=True
        )
        dialog.exec()
        self.refresh_server_projects()
        return True

    def _selected_project(self):
        item = self.list_widget.currentItem()
        return (
            item.data(Qt.ItemDataRole.UserRole)
            if item is not None else None
        )

    def _start_worker(self, worker, action):
        self._worker = worker
        self._worker_action = action
        self._worker_result = None
        self._set_busy(True)
        worker.resultReady.connect(self._record_worker_result)
        worker.finished.connect(self._finish_worker)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _record_worker_result(self, success, payload):
        self._worker_result = (bool(success), payload)

    def _finish_worker(self):
        action = self._worker_action
        result = self._worker_result
        self._worker = None
        self._worker_action = None
        self._worker_result = None
        self._set_busy(False)

        if result is None:
            success, payload = False, "작업 결과를 받지 못했습니다."
        else:
            success, payload = result
        if action == "catalog":
            self._finish_catalog(success, payload)
        elif action == "import":
            self._finish_import(success, payload)
        elif action == "trash":
            self._finish_server_trash(success, payload)

        if self._close_when_idle:
            self.reject()
        elif self._accept_after_worker:
            self.accept()

    def _finish_catalog(self, success, payload):
        if not success:
            self.list_widget.clear()
            self.status_label.setText(str(payload))
            return
        self._populate_projects(payload)

    def _populate_projects(self, projects):
        from server_project_import import (
            LOCAL_CURRENT,
            LOCAL_MISSING,
            LOCAL_OTHER,
        )

        self.list_widget.clear()
        for project in projects:
            if project.import_incomplete:
                state = "가져오기 재개"
            elif project.local_state == LOCAL_OTHER:
                state = "다른 실행환경에 있음"
            elif project.local_state == LOCAL_MISSING:
                state = "로컬 파일 없음"
            elif (
                project.local_state == LOCAL_CURRENT
                or project.already_imported
            ):
                state = "가져옴"
            else:
                state = "가져올 수 있음"
            updated = _format_server_timestamp(project.updated_at)
            accessible_text = (
                f"{project.name}  [{state}]\n"
                f"ID {project.masked_project_id} · 최근 수정 {updated}"
            )
            item = QListWidgetItem()
            item.setData(
                Qt.ItemDataRole.AccessibleTextRole, accessible_text
            )
            item.setSizeHint(QSize(0, 68))
            item.setData(Qt.ItemDataRole.UserRole, project)
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(
                item, ServerProjectListRow(project, state, self.list_widget)
            )

        if not projects:
            self.status_label.setText("가져올 수 있는 서버 작품이 없습니다.")
            self.import_button.setEnabled(False)
            return
        self.status_label.setText(
            f"접근 가능한 서버 작품 {len(projects)}개를 불러왔습니다."
        )
        self.list_widget.setCurrentRow(0)

    def _finish_import(self, success, payload):
        if not success:
            self.status_label.setText(str(payload))
            QMessageBox.warning(self, "서버 작품 가져오기 실패", str(payload))
            return
        self.imported_project_name = payload.local_project_name
        document_count = getattr(payload, "document_count", None)
        if document_count == 0:
            message = (
                f"'{payload.local_project_name}' 작품을 가져왔지만, "
                "현재 서버에 동기화된 문서가 0개입니다.\n\n"
                "빈 작품으로 열리며 iPad에서 문서 동기화가 완료되면 "
                "Windows가 자동으로 내려받습니다."
            )
            self.status_label.setText(message.replace("\n\n", " "))
            QMessageBox.information(
                self,
                "서버 문서 없음",
                message,
            )
        else:
            self.status_label.setText(
                f"'{payload.local_project_name}' 작품을 가져왔습니다."
            )
        self._accept_after_worker = True

    def _finish_server_trash(self, success, payload):
        if not success:
            self.status_label.setText(str(payload))
            QMessageBox.warning(
                self, "서버 휴지통 이동 실패", str(payload)
            )
            return
        project_id = getattr(payload, "project_id", "")
        mark_state = getattr(
            self.sync_manager, "mark_project_server_state", None
        )
        if callable(mark_state) and project_id:
            mark_state(project_id, "trashed")
        self.status_label.setText(
            f"'{payload.project_name}' 작품을 서버 휴지통으로 이동했습니다."
        )
        self.refresh_server_projects()

    def _on_project_selection_changed(self, current, previous):
        del previous
        project = (
            current.data(Qt.ItemDataRole.UserRole)
            if current is not None else None
        )
        if project is None:
            self.import_button.setEnabled(False)
            self.open_folder_button.setEnabled(False)
            self.server_trash_button.setEnabled(False)
            return
        suggested_name = project.local_project_name or project.name
        self.local_name_input.setText(suggested_name)
        self.import_button.setText(
            "누락 작품 다시 가져오기"
            if getattr(project, "local_state", "") == "missing"
            else (
                "가져오기 재개"
                if project.import_incomplete
                else "선택 작품 가져오기"
            )
        )
        can_import = project.can_import and not self.is_busy
        self.import_button.setEnabled(can_import)
        self.local_name_input.setEnabled(
            can_import and getattr(project, "local_state", "") != "missing"
        )
        self.open_folder_button.setEnabled(
            not self.is_busy
            and bool(getattr(project, "local_path", ""))
            and os.path.isdir(project.local_path)
        )
        self.server_trash_button.setEnabled(not self.is_busy)

    def _set_busy(self, busy):
        self.refresh_button.setEnabled(not busy)
        self.list_widget.setEnabled(not busy)
        self.open_trash_button.setEnabled(not busy)
        if busy:
            self.local_name_input.setEnabled(False)
            self.import_button.setEnabled(False)
            self.open_folder_button.setEnabled(False)
            self.server_trash_button.setEnabled(False)
        else:
            self._on_project_selection_changed(
                self.list_widget.currentItem(), None
            )
        if not busy:
            self.cancel_button.setEnabled(True)
            self.cancel_button.setText("취소")

    def _request_close(self):
        if self.is_busy:
            self._close_when_idle = True
            self.cancel_button.setEnabled(False)
            self.cancel_button.setText("닫기 대기 중…")
            self.status_label.setText(
                "현재 작업이 안전하게 끝나면 창을 닫습니다."
            )
            return
        self.reject()

    def closeEvent(self, event):
        if self.is_busy:
            self._request_close()
            event.ignore()
            return
        super().closeEvent(event)


class ProjectSelectionDialog(QDialog):
    def __init__(self, pm, parent=None):
        super().__init__(parent)
        self.pm = pm
        self.selected_project = None
        # Creating and opening are separate paths: only creation issues UUIDs.
        self.is_new_project = False
        self.setWindowTitle("프로젝트 선택")
        self.setFixedSize(720, 430)
        # This is shown before the main window is visible. Keep it above any
        # existing windows so the first required action is never hidden.
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
        )

        layout = QVBoxLayout()

        top_layout = QHBoxLayout()
        lbl = QLabel("열어볼 프로젝트를 선택하거나 새 프로젝트를 생성하세요:")
        self.btn_manage = QPushButton("⚙️ 관리")
        self.btn_manage.setMinimumSize(90, 35)
        self.btn_manage.setAutoDefault(False)
        self.btn_manage.clicked.connect(self.open_management)
        self.btn_server_import = QPushButton("☁ 서버 작품 관리")
        self.btn_server_import.setMinimumSize(150, 35)
        self.btn_server_import.setAutoDefault(False)
        self.btn_server_import.clicked.connect(self.open_server_import)

        top_layout.addWidget(lbl)
        top_layout.addStretch()
        top_layout.addWidget(self.btn_server_import)
        top_layout.addWidget(self.btn_manage)
        layout.addLayout(top_layout)

        self.list_widget = QListWidget()
        font = QFont("Malgun Gothic", 13, QFont.Weight.Bold)
        self.list_widget.setFont(font)

        self.list_widget.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.list_widget.model().rowsMoved.connect(self.on_order_changed)
        self.list_widget.itemDoubleClicked.connect(self.on_open)

        self.refresh_list()
        layout.addWidget(self.list_widget)

        self.input_new = QLineEdit()
        self.input_new.setPlaceholderText("새 프로젝트명 입력")
        layout.addWidget(self.input_new)

        btn_layout = QHBoxLayout()
        self.btn_open = QPushButton("선택 프로젝트 열기")
        self.btn_open.setMinimumHeight(40)
        self.btn_open.setDefault(True)
        self.btn_open.setAutoDefault(True)
        btn_create = QPushButton("새 프로젝트 생성")
        btn_create.setMinimumHeight(40)
        self.btn_open.clicked.connect(self.on_open)
        btn_create.clicked.connect(self.on_create)

        btn_layout.addWidget(self.btn_open)
        btn_layout.addWidget(btn_create)
        layout.addLayout(btn_layout)

        self.setLayout(layout)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._bring_to_front)

    def _bring_to_front(self):
        self.raise_()
        self.activateWindow()

    def refresh_list(self):
        self.list_widget.clear()
        last_project = self.pm.global_config.get("last_project", "")
        for p in self.pm.get_all_projects():
            item = QListWidgetItem(p)
            self.list_widget.addItem(item)
            if p == last_project:
                self.list_widget.setCurrentItem(item)

        # 만약 마지막 프로젝트가 없거나 삭제되어서 선택이 안 됐다면 첫 번째 항목 선택
        if self.list_widget.count() > 0 and not self.list_widget.currentItem():
            self.list_widget.setCurrentRow(0)

        self.list_widget.setFocus()

    def on_open(self):
        item = self.list_widget.currentItem()
        if not item:
            QMessageBox.warning(self, "경고", "열어볼 프로젝트를 선택하세요.")
            return
        self.selected_project = item.text()
        self.is_new_project = False
        self.accept()

    def on_create(self):
        name = self.input_new.text()
        if not name.strip():
            QMessageBox.warning(self, "경고", "새 프로젝트명을 입력하세요.")
            return
        from project_paths import (
            LocalProjectPathError,
            resolve_local_project_destination,
        )
        try:
            destination = resolve_local_project_destination(
                self.pm.workspace_dir, name
            )
        except LocalProjectPathError as error:
            QMessageBox.warning(self, "경고", error.user_message)
            return
        self.selected_project = destination.project_name
        self.is_new_project = True
        self.accept()

    def on_order_changed(self, parent, start, end, destination, row):
        ordered = []
        for i in range(self.list_widget.count()):
            ordered.append(self.list_widget.item(i).text())
        self.pm.save_project_order(ordered)

    def open_management(self):
        dialog = ProjectManagementDialog(self.pm, self)
        dialog.exec()
        self.refresh_list()

    def open_server_import(self):
        dialog = ServerProjectImportDialog(self.pm, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        imported_name = dialog.imported_project_name
        self.refresh_list()
        if imported_name:
            items = self.list_widget.findItems(
                imported_name, Qt.MatchFlag.MatchExactly
            )
            if items:
                self.list_widget.setCurrentItem(items[0])


class ProjectManagementDialog(QDialog):
    def __init__(self, pm, parent=None, trash_service=None):
        super().__init__(parent)
        if trash_service is None:
            from project_trash import ProjectTrashService
            from sync_manager import SyncManager
            from sync_v2_store import SyncV2Store

            sync_manager = SyncManager()
            trash_service = ProjectTrashService(
                pm,
                SyncV2Store(),
                sync_manager.supabase,
                authenticated_call=sync_manager._call_with_session,
            )

        self.pm = pm
        self.trash_service = trash_service
        self._worker = None
        self._worker_result = None
        self._close_when_idle = False
        self.setWindowTitle("프로젝트 관리")
        self.setFixedSize(680, 460)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Window)

        layout = QVBoxLayout(self)
        lbl = QLabel(
            "작품을 끌어 놓거나, 선택 후 ▲/▼ 버튼으로 순서를 바꿀 수 있습니다. "
            "프로젝트 이름을 변경하거나 휴지통으로 이동할 수 있습니다. "
            "휴지통으로 이동한 서버 작품은 다른 기기의 작품 목록에서도 숨겨집니다."
        )
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        self.list_widget = QListWidget()
        font = QFont("Malgun Gothic", 12)
        self.list_widget.setFont(font)
        self.list_widget.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.list_widget.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list_widget.setDropIndicatorShown(True)
        self.list_widget.model().rowsMoved.connect(self.on_order_changed)
        self.list_widget.currentRowChanged.connect(
            self._update_order_buttons
        )

        self.move_up_button = QPushButton("▲")
        self.move_up_button.setObjectName("DarkButton")
        self.move_up_button.setFixedSize(44, 44)
        self.move_up_button.setToolTip("선택한 작품을 위로 이동")
        self.move_up_button.setAccessibleName("선택한 작품을 위로 이동")
        self.move_up_button.setAutoDefault(False)
        self.move_up_button.clicked.connect(
            lambda: self.move_selected_project(-1)
        )

        self.move_down_button = QPushButton("▼")
        self.move_down_button.setObjectName("DarkButton")
        self.move_down_button.setFixedSize(44, 44)
        self.move_down_button.setToolTip("선택한 작품을 아래로 이동")
        self.move_down_button.setAccessibleName("선택한 작품을 아래로 이동")
        self.move_down_button.setAutoDefault(False)
        self.move_down_button.clicked.connect(
            lambda: self.move_selected_project(1)
        )

        list_layout = QHBoxLayout()
        list_layout.addWidget(self.list_widget, 1)
        order_button_layout = QVBoxLayout()
        order_button_layout.addStretch()
        order_button_layout.addWidget(self.move_up_button)
        order_button_layout.addWidget(self.move_down_button)
        order_button_layout.addStretch()
        list_layout.addLayout(order_button_layout)
        layout.addLayout(list_layout)
        self.refresh_list()

        self.status_label = QLabel("작품을 선택해주세요.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        btn_layout = QHBoxLayout()
        self.rename_button = QPushButton("이름 변경")
        self.rename_button.setMinimumHeight(35)
        self.trash_button = QPushButton("휴지통으로 이동")
        self.trash_button.setMinimumHeight(35)
        self.trash_button.setStyleSheet(
            "background-color: #c47f00; color: white; font-weight: bold;"
        )
        self.open_trash_button = QPushButton("🗑 휴지통")
        self.open_trash_button.setMinimumHeight(35)

        self.rename_button.clicked.connect(self.on_rename)
        self.trash_button.clicked.connect(self.on_delete)
        self.open_trash_button.clicked.connect(self.open_trash)

        btn_layout.addWidget(self.rename_button)
        btn_layout.addWidget(self.trash_button)
        btn_layout.addStretch()
        btn_layout.addWidget(self.open_trash_button)
        layout.addLayout(btn_layout)

    @property
    def is_busy(self):
        return self._worker is not None

    def refresh_list(self):
        selected_name = (
            self.list_widget.currentItem().text()
            if self.list_widget.currentItem() is not None
            else ""
        )
        self.list_widget.clear()
        for p in self.pm.get_all_projects():
            item = QListWidgetItem(p)
            self.list_widget.addItem(item)
            if p == selected_name:
                self.list_widget.setCurrentItem(item)
        if self.list_widget.count() and self.list_widget.currentItem() is None:
            self.list_widget.setCurrentRow(0)
        self._update_order_buttons()

    def _current_project_order(self):
        return [
            self.list_widget.item(row).text()
            for row in range(self.list_widget.count())
        ]

    def _save_current_project_order(self):
        self.pm.save_project_order(self._current_project_order())

    def on_order_changed(self, *args):
        del args
        self._save_current_project_order()
        self._update_order_buttons()

    def move_selected_project(self, offset):
        if self.is_busy or offset not in (-1, 1):
            return False

        current_row = self.list_widget.currentRow()
        target_row = current_row + offset
        if (
            current_row < 0
            or target_row < 0
            or target_row >= self.list_widget.count()
        ):
            self._update_order_buttons()
            return False

        item = self.list_widget.takeItem(current_row)
        self.list_widget.insertItem(target_row, item)
        self.list_widget.setCurrentItem(item)
        self._save_current_project_order()
        self._update_order_buttons()
        return True

    def _update_order_buttons(self, *_args):
        if not hasattr(self, "move_up_button"):
            return
        current_row = self.list_widget.currentRow()
        can_reorder = not self.is_busy and current_row >= 0
        self.move_up_button.setEnabled(can_reorder and current_row > 0)
        self.move_down_button.setEnabled(
            can_reorder and current_row < self.list_widget.count() - 1
        )

    def on_rename(self):
        if self.is_busy:
            return
        item = self.list_widget.currentItem()
        if not item:
            QMessageBox.warning(self, "경고", "이름을 변경할 프로젝트를 선택하세요.")
            return

        old_name = item.text()
        new_name, ok = QInputDialog.getText(self, "프로젝트 이름 변경", "새 프로젝트 이름을 입력하세요:", text=old_name)

        if ok and new_name and new_name != old_name:
            success, msg = self.pm.rename_project(old_name, new_name)
            if success:
                QMessageBox.information(self, "성공", "프로젝트 이름이 변경되었습니다.")
                self.refresh_list()
            else:
                QMessageBox.warning(self, "실패", msg)

    def on_delete(self):
        if self.is_busy:
            return False
        item = self.list_widget.currentItem()
        if not item:
            QMessageBox.warning(
                self, "경고", "휴지통으로 이동할 프로젝트를 선택하세요."
            )
            return False

        project_name = item.text()
        reply = QMessageBox.question(
            self,
            "휴지통으로 이동",
            (
                f"'{project_name}' 작품을 휴지통으로 이동하시겠습니까?\n\n"
                "서버와 동기화된 작품은 iPad 등 다른 기기의 작품 목록에서도 "
                "숨겨집니다. 휴지통에서 복원할 수 있습니다."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return False
        return self._start_trash(project_name)

    def _start_trash(self, project_name):
        if self.is_busy:
            return False
        self.status_label.setText(
            f"'{project_name}' 작품을 안전하게 휴지통으로 이동하는 중입니다…"
        )
        worker = ProjectTrashWorker(
            self.trash_service, "trash", project_name
        )
        self._start_worker(worker)
        return True

    def _start_worker(self, worker):
        self._worker = worker
        self._worker_result = None
        self._set_busy(True)
        worker.resultReady.connect(self._record_worker_result)
        worker.finished.connect(self._finish_worker)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _record_worker_result(self, action, success, payload):
        self._worker_result = (action, bool(success), payload)

    def _finish_worker(self):
        result = self._worker_result
        self._worker = None
        self._worker_result = None
        self._set_busy(False)

        if result is None:
            action, success, payload = (
                "trash", False, "작업 결과를 받지 못했습니다."
            )
        else:
            action, success, payload = result

        if action == "trash" and success:
            project_name = payload.project_name
            self.refresh_list()
            self.status_label.setText(
                f"'{project_name}' 작품을 휴지통으로 이동했습니다."
            )
            QMessageBox.information(
                self,
                "휴지통 이동 완료",
                (
                    f"'{project_name}' 작품을 휴지통으로 이동했습니다.\n"
                    "필요하면 관리창의 휴지통에서 복원할 수 있습니다."
                ),
            )
        elif not success:
            self.status_label.setText(str(payload))
            QMessageBox.warning(self, "휴지통 이동 실패", str(payload))

        if self._close_when_idle:
            self.reject()

    def _set_busy(self, busy):
        self.list_widget.setEnabled(not busy)
        self.rename_button.setEnabled(not busy)
        self.trash_button.setEnabled(not busy)
        self.open_trash_button.setEnabled(not busy)
        self._update_order_buttons()

    def open_trash(self):
        if self.is_busy:
            return
        dialog = ProjectTrashDialog(
            self.trash_service, self, auto_refresh=True
        )
        dialog.exec()
        self.refresh_list()

    def closeEvent(self, event):
        if self.is_busy:
            self._close_when_idle = True
            self.status_label.setText(
                "현재 작업이 안전하게 끝나면 창을 닫습니다."
            )
            event.ignore()
            return
        super().closeEvent(event)


class ProjectTrashListRow(QWidget):
    def __init__(self, entry, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        heading = QHBoxLayout()
        title = QLabel(entry.project_name)
        title.setStyleSheet("font-size: 12pt; font-weight: 600;")
        heading.addWidget(title, 1)

        if entry.server_available and entry.local_available:
            location = "서버 · 로컬"
        elif entry.server_available:
            location = "서버"
        else:
            location = "로컬"
        location_label = QLabel(location)
        location_label.setStyleSheet(
            "QLabel { color: #ffcc80; background: #3b3022;"
            "border-radius: 8px; padding: 2px 8px; font-weight: 600; }"
        )
        heading.addWidget(location_label)
        layout.addLayout(heading)

        details = f"삭제 시각  {_format_server_timestamp(entry.trashed_at)}"
        if entry.project_id:
            project_id = entry.project_id
            masked_id = (
                f"{project_id[:8]}…{project_id[-4:]}"
                if len(project_id) > 13 else project_id
            )
            details += f"    ·    ID  {masked_id}"
        detail_label = QLabel(details)
        detail_label.setStyleSheet("color: #aeb4ba; font-size: 9pt;")
        layout.addWidget(detail_label)


class ProjectTrashDialog(QDialog):
    def __init__(self, trash_service, parent=None, auto_refresh=True):
        super().__init__(parent)
        self.trash_service = trash_service
        self._worker = None
        self._worker_result = None
        self._close_when_idle = False
        self._notice_after_refresh = ""

        self.setWindowTitle("프로젝트 휴지통")
        self.setFixedSize(720, 500)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Window)

        layout = QVBoxLayout(self)
        description = QLabel(
            "휴지통의 작품은 복원할 수 있습니다. 영구 삭제하면 서버와 "
            "이 컴퓨터에서 완전히 제거되며 되돌릴 수 없습니다."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        self.status_label = QLabel("휴지통을 새로고침해주세요.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.list_widget = QListWidget()
        self.list_widget.setFont(QFont("Malgun Gothic", 11))
        self.list_widget.setSpacing(5)
        self.list_widget.setStyleSheet(
            "QListWidget { border: 1px solid #3a3f45; padding: 5px; }"
            "QListWidget::item { border-bottom: 1px solid #343940; }"
            "QListWidget::item:selected { background: #263d4a; }"
        )
        self.list_widget.currentItemChanged.connect(
            self._on_selection_changed
        )
        layout.addWidget(self.list_widget)

        button_layout = QHBoxLayout()
        self.refresh_button = QPushButton("새로고침")
        self.restore_button = QPushButton("복원")
        self.purge_button = QPushButton("영구 삭제")
        self.close_button = QPushButton("닫기")
        self.purge_button.setStyleSheet(
            "background-color: #dc3545; color: white; font-weight: bold;"
        )
        self.refresh_button.clicked.connect(self.refresh_trash)
        self.restore_button.clicked.connect(self.restore_selected)
        self.purge_button.clicked.connect(self.purge_selected)
        self.close_button.clicked.connect(self._request_close)
        button_layout.addWidget(self.refresh_button)
        button_layout.addStretch()
        button_layout.addWidget(self.restore_button)
        button_layout.addWidget(self.purge_button)
        button_layout.addWidget(self.close_button)
        layout.addLayout(button_layout)

        self.restore_button.setEnabled(False)
        self.purge_button.setEnabled(False)
        if auto_refresh:
            QTimer.singleShot(0, self.refresh_trash)

    @property
    def is_busy(self):
        return self._worker is not None

    def refresh_trash(self):
        if self.is_busy:
            return False
        self.status_label.setText("프로젝트 휴지통을 불러오는 중입니다…")
        self._start_worker(ProjectTrashWorker(
            self.trash_service, "list"
        ))
        return True

    def restore_selected(self):
        if self.is_busy:
            return False
        entry = self._selected_entry()
        if entry is None:
            QMessageBox.warning(
                self, "복원", "복원할 작품을 선택해주세요."
            )
            return False
        reply = QMessageBox.question(
            self,
            "작품 복원",
            f"'{entry.project_name}' 작품을 다시 작품 목록으로 복원하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return False
        return self._start_restore(entry)

    def _start_restore(self, entry):
        if self.is_busy:
            return False
        self.status_label.setText(
            f"'{entry.project_name}' 작품을 복원하는 중입니다…"
        )
        self._start_worker(ProjectTrashWorker(
            self.trash_service, "restore", entry
        ))
        return True

    def purge_selected(self):
        if self.is_busy:
            return False
        entry = self._selected_entry()
        if entry is None:
            QMessageBox.warning(
                self, "영구 삭제", "영구 삭제할 작품을 선택해주세요."
            )
            return False
        typed_name, ok = QInputDialog.getText(
            self,
            "영구 삭제 확인",
            (
                "이 작업은 되돌릴 수 없습니다.\n"
                f"계속하려면 작품명 '{entry.project_name}'을(를) "
                "정확히 입력하세요:"
            ),
        )
        if not ok:
            return False
        if not self._permanent_delete_name_matches(
            entry.project_name, typed_name
        ):
            QMessageBox.warning(
                self,
                "영구 삭제 취소",
                "입력한 작품명이 일치하지 않아 영구 삭제하지 않았습니다.",
            )
            return False
        return self._start_purge(entry)

    @staticmethod
    def _permanent_delete_name_matches(expected, typed):
        return typed == expected

    def _start_purge(self, entry):
        if self.is_busy:
            return False
        self.status_label.setText(
            f"'{entry.project_name}' 작품을 영구 삭제하는 중입니다…"
        )
        self._start_worker(ProjectTrashWorker(
            self.trash_service, "purge", entry
        ))
        return True

    def _selected_entry(self):
        item = self.list_widget.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _start_worker(self, worker):
        self._worker = worker
        self._worker_result = None
        self._set_busy(True)
        worker.resultReady.connect(self._record_worker_result)
        worker.finished.connect(self._finish_worker)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _record_worker_result(self, action, success, payload):
        self._worker_result = (action, bool(success), payload)

    def _finish_worker(self):
        result = self._worker_result
        self._worker = None
        self._worker_result = None
        self._set_busy(False)

        if result is None:
            action, success, payload = (
                "list", False, "작업 결과를 받지 못했습니다."
            )
        else:
            action, success, payload = result

        if self._close_when_idle:
            self.reject()
            return
        if not success:
            self.status_label.setText(str(payload))
            QMessageBox.warning(self, "프로젝트 휴지통", str(payload))
            return
        if action == "list":
            self._populate_entries(payload)
            return

        entry_name = (
            payload if action == "restore" else "선택한 작품"
        )
        if action == "restore":
            self._notice_after_refresh = (
                f"'{entry_name}' 작품을 복원했습니다."
            )
        else:
            self._notice_after_refresh = "선택한 작품을 영구 삭제했습니다."
        self.refresh_trash()

    def _populate_entries(self, entries):
        self.list_widget.clear()
        for entry in entries:
            if entry.server_available and entry.local_available:
                location = "서버 · 로컬"
            elif entry.server_available:
                location = "서버"
            else:
                location = "로컬"
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setData(
                Qt.ItemDataRole.AccessibleTextRole,
                (
                    f"{entry.project_name} [{location}] "
                    f"삭제 시각 {_format_server_timestamp(entry.trashed_at)}"
                ),
            )
            item.setSizeHint(QSize(0, 68))
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(
                item, ProjectTrashListRow(entry, self.list_widget)
            )

        server_warning = getattr(
            self.trash_service, "last_server_error", ""
        )
        if self._notice_after_refresh:
            message = self._notice_after_refresh
            self._notice_after_refresh = ""
        elif entries:
            message = f"휴지통에 작품 {len(entries)}개가 있습니다."
        else:
            message = "휴지통이 비어 있습니다."
        if server_warning:
            message += f"  서버 목록 경고: {server_warning}"
        self.status_label.setText(message)
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        else:
            self._on_selection_changed(None, None)

    def _on_selection_changed(self, current, previous):
        del previous
        has_selection = current is not None and not self.is_busy
        self.restore_button.setEnabled(has_selection)
        self.purge_button.setEnabled(has_selection)

    def _set_busy(self, busy):
        self.refresh_button.setEnabled(not busy)
        self.list_widget.setEnabled(not busy)
        has_selection = self.list_widget.currentItem() is not None
        self.restore_button.setEnabled(not busy and has_selection)
        self.purge_button.setEnabled(not busy and has_selection)
        if not busy:
            self.close_button.setEnabled(True)
            self.close_button.setText("닫기")

    def _request_close(self):
        if self.is_busy:
            self._close_when_idle = True
            self.close_button.setEnabled(False)
            self.close_button.setText("닫기 대기 중…")
            self.status_label.setText(
                "현재 작업이 안전하게 끝나면 창을 닫습니다."
            )
            return
        self.reject()

    def closeEvent(self, event):
        if self.is_busy:
            self._request_close()
            event.ignore()
            return
        super().closeEvent(event)
