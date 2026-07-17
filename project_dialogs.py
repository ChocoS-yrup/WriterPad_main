from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTextEdit, QLabel, QSplitter, QTextBrowser,
    QStackedWidget, QComboBox, QMenu, QGraphicsDropShadowEffect, QScrollArea, QFrame, QMessageBox,
    QSizePolicy, QCheckBox, QDialog, QListWidget, QLineEdit, QRadioButton, QSpinBox, QButtonGroup,
    QFontDialog, QTabWidget, QListWidgetItem, QGridLayout, QTabBar
)
from PyQt6.QtGui import QFont, QTextCursor, QGuiApplication, QTextDocument
from PyQt6.QtCore import pyqtSignal, Qt, QSettings, QTimer, QThread


class ProjectSelectionDialog(QDialog):
    def __init__(self, pm, parent=None):
        super().__init__(parent)
        self.pm = pm
        self.selected_project = None
        self.setWindowTitle("프로젝트 선택")
        self.setFixedSize(600, 400)
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

        top_layout.addWidget(lbl)
        top_layout.addStretch()
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
        self.accept()

    def on_create(self):
        name = self.input_new.text().strip()
        if not name:
            QMessageBox.warning(self, "경고", "새 프로젝트명을 입력하세요.")
            return
        if name in self.pm.get_all_projects():
            QMessageBox.warning(self, "경고", "이미 존재하는 프로젝트명입니다.")
            return
        self.selected_project = name
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


class ProjectManagementDialog(QDialog):
    def __init__(self, pm, parent=None):
        super().__init__(parent)
        self.pm = pm
        self.setWindowTitle("프로젝트 관리")
        self.setFixedSize(600, 400)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Window)

        layout = QVBoxLayout()
        lbl = QLabel("프로젝트 이름 변경 및 삭제를 할 수 있습니다:")
        layout.addWidget(lbl)

        self.list_widget = QListWidget()
        font = QFont("Malgun Gothic", 12)
        self.list_widget.setFont(font)
        self.refresh_list()
        layout.addWidget(self.list_widget)

        btn_layout = QHBoxLayout()
        btn_rename = QPushButton("이름 변경")
        btn_rename.setMinimumHeight(35)
        btn_delete = QPushButton("삭제")
        btn_delete.setMinimumHeight(35)
        btn_delete.setStyleSheet("background-color: #dc3545; color: white; font-weight: bold;")

        btn_rename.clicked.connect(self.on_rename)
        btn_delete.clicked.connect(self.on_delete)

        btn_layout.addWidget(btn_rename)
        btn_layout.addWidget(btn_delete)
        layout.addLayout(btn_layout)

        self.setLayout(layout)

    def refresh_list(self):
        self.list_widget.clear()
        for p in self.pm.get_all_projects():
            self.list_widget.addItem(p)

    def on_rename(self):
        item = self.list_widget.currentItem()
        if not item:
            QMessageBox.warning(self, "경고", "이름을 변경할 프로젝트를 선택하세요.")
            return

        old_name = item.text()
        from PyQt6.QtWidgets import QInputDialog
        new_name, ok = QInputDialog.getText(self, "프로젝트 이름 변경", "새 프로젝트 이름을 입력하세요:", text=old_name)

        if ok and new_name and new_name != old_name:
            success, msg = self.pm.rename_project(old_name, new_name)
            if success:
                QMessageBox.information(self, "성공", "프로젝트 이름이 변경되었습니다.")
                self.refresh_list()
            else:
                QMessageBox.warning(self, "실패", msg)

    def on_delete(self):
        item = self.list_widget.currentItem()
        if not item:
            QMessageBox.warning(self, "경고", "삭제할 프로젝트를 선택하세요.")
            return

        project_name = item.text()

        # 1차 경고
        reply1 = QMessageBox.question(
            self, "삭제 확인",
            f"정말 '{project_name}' 프로젝트를 삭제하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply1 != QMessageBox.StandardButton.Yes:
            return

        # 2차 경고
        msg2 = QMessageBox(self)
        msg2.setIcon(QMessageBox.Icon.Critical)
        msg2.setWindowTitle("최종 삭제 확인")
        msg2.setText(f"삭제된 프로젝트는 복구할 수 없습니다.\n진짜로 '{project_name}'을(를) 지울까요?")
        btn_yes = msg2.addButton("예, 완전히 삭제합니다", QMessageBox.ButtonRole.DestructiveRole)
        btn_no = msg2.addButton("아니오, 취소합니다", QMessageBox.ButtonRole.RejectRole)
        msg2.exec()

        if msg2.clickedButton() == btn_yes:
            success, msg = self.pm.delete_project(project_name)
            if success:
                QMessageBox.information(self, "삭제 완료", "프로젝트가 삭제되었습니다.")
                self.refresh_list()
            else:
                QMessageBox.warning(self, "삭제 실패", msg)
