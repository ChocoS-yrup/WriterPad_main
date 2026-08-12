"""Resolve a document sync conflict without leaving the app.

The three conflict artifacts already written to 백업/충돌 are shown as tabs.
Whichever tab is active when the user confirms becomes the document body, and
the ordinary save path clears the conflict from there.
"""

import os

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt


CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")

MERGED_LABEL = "3방향 병합 충돌"
LOCAL_LABEL = "내 로컬 편집본"
REMOTE_LABEL = "서버 최신본"
REPORT_LABEL = "차이점 비교"


def has_conflict_markers(text):
    body = str(text or "")
    return all(marker in body for marker in ("<<<<<<<", ">>>>>>>"))


def _newest_artifact(wpm, relative_path, label):
    """Return the newest 백업/충돌 artifact for this document, if any.

    Conflict files are timestamped, and the writer may have edited one by hand
    before opening this dialog, so the folder wins over the stored snapshot.
    """
    root = getattr(wpm, "writing_root_path", "") or ""
    folder = os.path.join(root, "백업", "충돌")
    if not os.path.isdir(folder):
        return None
    base, ext = os.path.splitext(os.path.basename(relative_path))
    prefix = f"{base} ({label} "
    candidates = []
    for name in os.listdir(folder):
        if not name.startswith(prefix):
            continue
        full = os.path.join(folder, name)
        try:
            candidates.append((os.path.getmtime(full), full))
        except OSError:
            continue
    if not candidates:
        return None
    newest = max(candidates)[1]
    try:
        with open(newest, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def build_conflict_view(wpm, document, report_builder=None):
    """Merge stored conflict snapshots with anything edited in 백업/충돌."""
    path = document.get("local_path") or document.get("path") or ""
    merged = document.get("conflict_merged") or ""
    local = document.get("conflict_local") or ""
    remote = document.get("conflict_remote") or ""
    base = document.get("conflict_base") or ""

    if wpm is not None:
        for label, key in (
            (MERGED_LABEL, "merged"),
            (LOCAL_LABEL, "local"),
            (REMOTE_LABEL, "remote"),
        ):
            edited = _newest_artifact(wpm, path, label)
            if edited is not None:
                if key == "merged":
                    merged = edited
                elif key == "local":
                    local = edited
                else:
                    remote = edited

    # 세 버전은 직접 고쳐둘 수 있으니 폴더가 우선이지만, 차이점 비교는 파생
    # 결과물이다. 충돌 당시 저장된 파일을 그대로 쓰면 예전 형식이 계속 보이고
    # 손으로 고친 로컬본도 반영되지 않는다. 항상 지금 내용으로 다시 만든다.
    report = ""
    if callable(report_builder):
        try:
            report = report_builder(base, local, remote)
        except Exception:
            report = ""
    if not report and wpm is not None:
        report = _newest_artifact(wpm, path, REPORT_LABEL) or ""

    return {
        "path": path,
        "merged": merged,
        "local": local,
        "remote": remote,
        "report": report,
    }


class ConflictResolutionDialog(QDialog):
    """Pick one of the three conflict versions, editing it first if needed."""

    def __init__(self, views, parent=None):
        super().__init__(parent)
        self.setWindowTitle("충돌 문서 확인")
        self.setObjectName("ConflictDialog")
        self.setModal(True)
        self.resize(980, 680)
        self._views = list(views)
        self._editors = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.document_selector = QComboBox()
        for view in self._views:
            self.document_selector.addItem(
                os.path.basename(view["path"]) or view["path"], view["path"]
            )
        selector_row = QHBoxLayout()
        selector_row.addWidget(QLabel("충돌 문서"))
        selector_row.addWidget(self.document_selector, 1)
        layout.addLayout(selector_row)
        self.document_selector.setVisible(len(self._views) > 1)
        selector_row.itemAt(0).widget().setVisible(len(self._views) > 1)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self._merged_editor = QPlainTextEdit()
        self._report_view = QPlainTextEdit()
        self._report_view.setReadOnly(True)
        splitter = QSplitter(Qt.Orientation.Vertical)
        merged_page = QWidget()
        merged_layout = QVBoxLayout(merged_page)
        merged_layout.setContentsMargins(12, 12, 12, 6)
        merged_layout.setSpacing(6)
        hint = QLabel("충돌 마커를 정리한 뒤 선택하세요. 이 탭은 편집할 수 있습니다.")
        hint.setObjectName("ConflictHint")
        merged_layout.addWidget(hint)
        merged_layout.addWidget(self._merged_editor)
        splitter.addWidget(merged_page)
        report_page = QWidget()
        report_layout = QVBoxLayout(report_page)
        report_layout.setContentsMargins(12, 6, 12, 12)
        report_layout.setSpacing(6)
        report_title = QLabel(REPORT_LABEL)
        report_title.setObjectName("ConflictSectionLabel")
        report_layout.addWidget(report_title)
        report_layout.addWidget(self._report_view)
        splitter.addWidget(report_page)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self.tabs.addTab(splitter, MERGED_LABEL)
        self._editors[0] = self._merged_editor

        for index, (label, key) in enumerate(
            ((LOCAL_LABEL, "local"), (REMOTE_LABEL, "remote")), start=1
        ):
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(12, 12, 12, 12)
            editor = QPlainTextEdit()
            page_layout.addWidget(editor)
            self.tabs.addTab(page, label)
            self._editors[index] = editor
            setattr(self, f"_{key}_editor", editor)

        self.status = QLabel("")
        self.status.setObjectName("ConflictWarning")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QDialogButtonBox()
        self.apply_button = buttons.addButton(
            "활성화된 탭 선택", QDialogButtonBox.ButtonRole.AcceptRole
        )
        close_button = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        close_button.setObjectName("DarkButton")
        close_button.setText("닫기")
        buttons.accepted.connect(self._confirm)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.document_selector.currentIndexChanged.connect(self._load_document)
        self.tabs.currentChanged.connect(self._update_status)
        self._load_document(0)

    # --- state ---------------------------------------------------------

    def _current_view(self):
        index = max(0, self.document_selector.currentIndex())
        return self._views[index] if self._views else None

    def _load_document(self, _index):
        view = self._current_view()
        if view is None:
            return
        self._merged_editor.setPlainText(view["merged"])
        self._local_editor.setPlainText(view["local"])
        self._remote_editor.setPlainText(view["remote"])
        self._report_view.setPlainText(
            view["report"] or "차이점 비교 내용을 찾지 못했습니다."
        )
        self._update_status()

    def _update_status(self, *_args):
        content = self.selected_content()
        if not content.strip():
            self.status.setText(
                f"'{self.selected_label()}' 는 빈 문서입니다. "
                "그대로 적용하면 원고 내용이 사라집니다."
            )
        elif has_conflict_markers(content):
            self.status.setText(
                "선택한 내용에 충돌 마커(<<<<<<<, >>>>>>>)가 남아 있습니다. "
                "그대로 적용하면 원고에 그대로 들어갑니다."
            )
        else:
            self.status.setText("")

    def selected_document_path(self):
        view = self._current_view()
        return view["path"] if view else ""

    def selected_label(self):
        return self.tabs.tabText(self.tabs.currentIndex())

    def selected_content(self):
        editor = self._editors.get(self.tabs.currentIndex())
        return editor.toPlainText() if editor is not None else ""

    # --- confirm -------------------------------------------------------

    def _confirm(self):
        content = self.selected_content()
        if not content.strip():
            answer = QMessageBox.warning(
                self,
                "빈 문서를 선택했습니다",
                f"'{self.selected_label()}' 는 내용이 비어 있습니다.\n"
                "적용하면 이 원고의 본문이 사라집니다.\n\n"
                "그래도 적용할까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.accept()
            return
        if has_conflict_markers(content):
            answer = QMessageBox.warning(
                self,
                "충돌 마커가 남아 있습니다",
                "선택한 내용에 <<<<<<< / >>>>>>> 표시가 남아 있습니다.\n"
                "그대로 적용하면 원고 본문에 그대로 들어갑니다.\n\n"
                "그래도 적용할까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.accept()
