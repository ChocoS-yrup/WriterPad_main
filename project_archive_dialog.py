"""Local backup entry from the project picker, before any project is opened."""
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QFileDialog, QLabel, QMessageBox, QPushButton, QVBoxLayout,
)

from project_archive import create_archive, verify_archive, restore_archive, SCOPE_TEXT


class ArchiveWorker(QThread):
    resultReady = pyqtSignal(bool, object)

    def __init__(self, action, args, parent=None):
        super().__init__(parent)
        self.action, self.args = action, args

    def run(self):
        try:
            self.resultReady.emit(True, self.action(*self.args))
        except Exception as error:
            self.resultReady.emit(False, str(error))


class ProjectArchiveDialog(QDialog):
    def __init__(self, project=None, parent=None):
        super().__init__(parent)
        self.project = project
        self.worker = None
        self.result = None
        self.setWindowTitle("작품 백업·복구")
        self.resize(590, 330)
        layout = QVBoxLayout(self)
        self.notice = QLabel(
            SCOPE_TEXT + "\n\n백업은 폴더 묶음으로 보관합니다. 다른 창에서 편집 중이라면 먼저 저장하세요. "
            "복원은 새 독립 폴더에만 수행하며 현재 작품을 바꾸지 않습니다."
        )
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.create_button = QPushButton("선택 작품 백업 만들기")
        self.create_button.setEnabled(bool(project))
        self.create_button.clicked.connect(self.create_backup)
        self.verify_button = QPushButton("백업 폴더 검증")
        self.verify_button.clicked.connect(self.verify_backup)
        self.restore_button = QPushButton("새 독립 폴더에 복원")
        self.restore_button.clicked.connect(self.restore_backup)
        self.buttons = (self.create_button, self.verify_button, self.restore_button)
        for button in self.buttons:
            layout.addWidget(button)

    def _new_destination(self, caption, name):
        parent = QFileDialog.getExistingDirectory(self, "보관할 상위 폴더 선택")
        if not parent:
            return ""
        from PyQt6.QtWidgets import QInputDialog
        title, ok = QInputDialog.getText(self, caption, "새 폴더 이름", text=name)
        if not ok:
            return ""
        from project_paths import validate_local_project_name
        try:
            validate_local_project_name(title)
        except ValueError:
            QMessageBox.warning(self, "폴더 이름", "사용할 수 있는 새 폴더 이름을 입력하세요.")
            return ""
        return str(Path(parent) / title)

    def create_backup(self):
        if not self.project or self.worker:
            return
        target = self._new_destination(
            "백업 폴더", Path(self.project).name + datetime.now().strftime("_백업_%Y%m%d_%H%M%S")
        )
        if target:
            self._start(create_archive, (self.project, target), "백업")

    def verify_backup(self):
        package = QFileDialog.getExistingDirectory(self, "검증할 백업 폴더 선택")
        if package:
            self._start(verify_archive, (package,), "검증")

    def restore_backup(self):
        package = QFileDialog.getExistingDirectory(self, "복원할 백업 폴더 선택")
        if not package:
            return
        target = self._new_destination("독립 복원 폴더", "작품_독립복원")
        if target:
            self._start(restore_archive, (package, target), "복원")

    def _start(self, action, args, label):
        if self.worker:
            return
        self.result = None
        self.action_label = label
        self.notice.setText(label + " 중입니다. 완료할 때까지 원본을 편집하지 마세요.")
        for button in self.buttons:
            button.setEnabled(False)
        self.worker = ArchiveWorker(action, args, self)
        self.worker.resultReady.connect(self._result)
        self.worker.finished.connect(self._finished)
        self.worker.start()

    def _result(self, success, result):
        self.result = (success, result)

    def _finished(self):
        self.worker.deleteLater()
        self.worker = None
        for button in self.buttons:
            button.setEnabled(button != self.create_button or bool(self.project))
        if self.result and self.result[0]:
            self.notice.setText(self.action_label + " 완료. 파일 목록·내용 해시를 확인했습니다.\n" + SCOPE_TEXT)
            if self.action_label == "복원":
                self.notice.setText(
                    "독립 복원 완료: " + self.result[1]["root"]
                    + "\n현재 앱에는 연결하지 않았습니다. 원고는 복원 폴더에서 확인할 수 있습니다."
                )
        else:
            reason = self.result[1] if self.result else "작업이 중단되었습니다."
            self.notice.setText("완료하지 못했습니다. 원본과 기존 목적지는 보존했습니다.\n" + reason)

    def reject(self):
        if not self.worker:
            super().reject()

    def closeEvent(self, event):
        if self.worker:
            event.ignore()
        else:
            event.accept()
