"""Keep failed AI history writes recoverable without another API request."""
import os
import tempfile
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout,
)


class AIResponseRecoveryDialog(QDialog):
    def __init__(self, request, text, failures, history_saved, parent=None):
        super().__init__(parent)
        self.request = request
        self.response_text = text
        self.saved = history_saved
        self.discarded = False
        self.setWindowTitle("AI 응답 보존 · 저장 오류")
        self.resize(680, 500)
        layout = QVBoxLayout(self)
        self.notice = QLabel(
            f"{request.project_name} · {request.chapter}화 · {request.step_name}\n"
            + ", ".join(failures) + " 저장에 실패했습니다.\n"
            "생성 결과는 아래에 보존했습니다. 복사하거나 다른 파일로 저장하세요.\n"
            "비용 이력은 자동으로 재기록하지 않습니다."
        )
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.editor = QPlainTextEdit()
        self.editor.setReadOnly(True)
        self.editor.setPlainText(text)
        layout.addWidget(self.editor)
        row = QHBoxLayout()
        self.copy_button = QPushButton("전체 복사")
        self.copy_button.clicked.connect(self.copy_response)
        self.save_button = QPushButton("다른 파일로 저장")
        self.save_button.clicked.connect(self.save_response)
        row.addWidget(self.copy_button)
        row.addWidget(self.save_button)
        layout.addLayout(row)

    def copy_response(self):
        QApplication.clipboard().setText(self.response_text)

    def save_response(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "AI 응답 사본 저장",
            f"{self.request.chapter:03d}화_{self.request.step_name}_AI응답.md",
            "Markdown (*.md);;텍스트 (*.txt)",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if not path:
            return
        temporary = None
        try:
            target = Path(path)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=target.parent, delete=False
            ) as handle:
                temporary = handle.name
                handle.write(self.response_text)
                handle.flush()
                os.fsync(handle.fileno())
            # Publish complete bytes without ever replacing an existing file.
            os.link(temporary, target)
            self.saved = True
            self.notice.setText("AI 응답 사본을 저장했습니다. 비용 이력은 별도 확인이 필요합니다.")
        except (OSError, UnicodeError):
            QMessageBox.warning(self, "저장 실패", "기존 파일은 덮어쓰지 않습니다. 다른 이름이나 위치를 선택하세요. 응답은 이 창에 남아 있습니다.")
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def can_discard(self):
        if self.saved or self.discarded:
            return True
        answer = QMessageBox.question(
            self, "저장되지 않은 AI 응답",
            "이 응답은 아직 파일에 저장되지 않았습니다. 저장하지 않고 닫으시겠습니까?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        self.discarded = answer == QMessageBox.StandardButton.Yes
        return self.discarded

    def reject(self):
        if self.can_discard():
            super().reject()

    def closeEvent(self, event):
        if self.can_discard():
            event.accept()
        else:
            event.ignore()
