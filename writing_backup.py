import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)


class HistoryViewerDialog(QDialog):
    def __init__(self, writing_manager, rel_path, parent=None):
        super().__init__(parent)
        self.wpm = writing_manager
        self.rel_path = rel_path
        self.selected_backup_path = None
        self.init_ui()
        self.setWindowTitle(f"이전 버전 복원: {os.path.basename(rel_path)}")
        self.resize(960, 650)
        self.load_history()

    def init_ui(self):
        layout = QHBoxLayout(self)

        left_layout = QVBoxLayout()
        self.list_history = QListWidget()
        self.list_history.setFixedWidth(230)
        self.list_history.itemSelectionChanged.connect(self.on_history_selected)
        left_layout.addWidget(QLabel("자동저장 이력 (최신순)"))
        left_layout.addWidget(self.list_history)

        right_layout = QVBoxLayout()
        self.tabs = QTabWidget()

        self.preview_editor = QTextBrowser()
        self.preview_editor.setStyleSheet("QTextBrowser { font-family: 'Malgun Gothic'; font-size: 12pt; padding: 10px; }")
        self.tabs.addTab(self.preview_editor, "선택 시점 미리보기")

        diff_widget = QWidget()
        diff_layout = QVBoxLayout(diff_widget)
        diff_layout.setContentsMargins(0, 0, 0, 0)
        self.diff_summary = QLabel("이력을 선택하면 현재본과 비교합니다.")
        self.diff_editor = QTextBrowser()
        self.diff_editor.setStyleSheet("QTextBrowser { font-family: Consolas, monospace; font-size: 10pt; padding: 10px; }")
        diff_layout.addWidget(self.diff_summary)
        diff_layout.addWidget(self.diff_editor)
        self.tabs.addTab(diff_widget, "현재본과 차이")
        right_layout.addWidget(self.tabs)

        btn_layout = QHBoxLayout()
        self.btn_restore = QPushButton("이 버전으로 복원하기")
        self.btn_restore.setEnabled(False)
        self.btn_restore.setStyleSheet("background-color: #2a64f6; color: white; font-weight: bold; padding: 10px;")
        self.btn_restore.clicked.connect(self.do_restore)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_restore)
        right_layout.addLayout(btn_layout)

        layout.addLayout(left_layout)
        layout.addLayout(right_layout)

    def load_history(self):
        for history in self.wpm.list_backup_history(self.rel_path):
            size_kb = max(1, (history["size"] + 1023) // 1024)
            item = QListWidgetItem(f"{history['display_time']}  ·  {size_kb}KB")
            item.setData(Qt.ItemDataRole.UserRole, history["path"])
            self.list_history.addItem(item)
        if self.list_history.count() == 0:
            self.preview_editor.setPlainText("이 원고의 자동저장 이력이 없습니다.")
        else:
            self.list_history.setCurrentRow(0)

    def on_history_selected(self):
        items = self.list_history.selectedItems()
        if not items: return
        file_path = items[0].data(Qt.ItemDataRole.UserRole)
        try:
            comparison = self.wpm.compare_with_backup(self.rel_path, file_path)
            self.preview_editor.setPlainText(comparison["backup_content"])
            self.diff_editor.setPlainText(comparison["diff"])
            self.diff_summary.setText(
                f"선택한 백업 기준: 추가 {comparison['additions']}줄 · 삭제 {comparison['deletions']}줄"
            )
            self.selected_backup_path = file_path
            self.btn_restore.setEnabled(True)
        except Exception as e:
            self.selected_backup_path = None
            self.btn_restore.setEnabled(False)
            self.preview_editor.setPlainText(f"파일을 읽을 수 없습니다: {e}")
            self.diff_editor.clear()

    def do_restore(self):
        items = self.list_history.selectedItems()
        if not items:
            QMessageBox.warning(self, "경고", "복원할 버전을 선택해주세요.")
            return

        reply = QMessageBox.question(
            self,
            "복원 확인",
            "현재 내용을 선택한 시점으로 되돌립니다.\n현재본은 먼저 '백업/복원전'에 안전하게 보관됩니다.\n진행하시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.accept()



    def get_selected_backup_path(self):
        return self.selected_backup_path
