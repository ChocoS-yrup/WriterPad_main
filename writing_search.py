import os

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QVBoxLayout,
)


class GlobalSearchWorker(QThread):
    finished = pyqtSignal(list)
    progress = pyqtSignal(str)

    def __init__(self, root_path, keyword):
        super().__init__()
        self.root_path = root_path
        self.keyword = keyword

    def run(self):
        results = []
        try:
            for root, dirs, files in os.walk(self.root_path):
                if "백업" in root.replace("\\", "/"):
                    continue
                for file in files:
                    if file.endswith(".txt"):
                        full_path = os.path.join(root, file)
                        rel_path = os.path.relpath(full_path, self.root_path).replace("\\", "/")
                        self.progress.emit(f"검색 중: {rel_path}")
                        try:
                            with open(full_path, "r", encoding="utf-8") as f:
                                content = f.read()
                            lines = content.split('\n')
                            for i, line in enumerate(lines):
                                if self.keyword in line:
                                    snippet = line.strip()
                                    if len(snippet) > 50:
                                        idx = snippet.find(self.keyword)
                                        start = max(0, idx - 20)
                                        snippet = "..." + snippet[start:start+50] + "..."
                                    results.append((rel_path, i+1, snippet))
                        except:
                            pass
        except Exception as e:
            pass
        self.finished.emit(results)

class GlobalSearchDialog(QDialog):
    def __init__(self, root_path, parent=None):
        super().__init__(parent)
        self.root_path = root_path
        self.selected_path = None
        self.init_ui()
        self.setWindowTitle("전체 검색")
        self.resize(600, 400)

    def init_ui(self):
        layout = QVBoxLayout(self)

        search_layout = QHBoxLayout()
        self.input_keyword = QLineEdit()
        self.input_keyword.setPlaceholderText("검색어를 입력하세요...")
        self.input_keyword.returnPressed.connect(self.do_search)
        self.btn_search = QPushButton("검색")
        self.btn_search.clicked.connect(self.do_search)
        search_layout.addWidget(self.input_keyword)
        search_layout.addWidget(self.btn_search)

        self.lbl_status = QLabel("대기 중...")

        self.list_results = QListWidget()
        self.list_results.itemDoubleClicked.connect(self.on_item_activated)
        self.list_results.itemActivated.connect(self.on_item_activated)

        layout.addLayout(search_layout)
        layout.addWidget(self.lbl_status)
        layout.addWidget(self.list_results)

    def do_search(self):
        keyword = self.input_keyword.text().strip()
        if not keyword: return

        if hasattr(self, 'worker') and self.worker.isRunning():
            return

        self.list_results.clear()
        if hasattr(self, 'btn_search'):
            self.btn_search.setEnabled(False)
        self.input_keyword.setEnabled(False)

        self.worker = GlobalSearchWorker(self.root_path, keyword)
        self.worker.progress.connect(self.lbl_status.setText)
        self.worker.finished.connect(self.on_search_finished)
        self.worker.start()

    def on_search_finished(self, results):
        if hasattr(self, 'btn_search'):
            self.btn_search.setEnabled(True)
        self.input_keyword.setEnabled(True)

        if not results:
            self.lbl_status.setText("검색 결과가 없습니다.")
            return

        self.lbl_status.setText(f"총 {len(results)}건의 결과를 찾았습니다. (더블클릭하여 이동)")
        for rel_path, line_num, snippet in results:
            item = QListWidgetItem(f"[{rel_path} : {line_num}줄] {snippet}")
            item.setData(Qt.ItemDataRole.UserRole, rel_path)
            self.list_results.addItem(item)

    def on_item_activated(self, item):
        self.selected_path = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def get_selected_path(self):
        return self.selected_path

    def get_search_keyword(self):
        return self.input_keyword.text().strip()

    def accept(self):
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.worker.terminate()
            self.worker.wait()
        super().accept()

    def reject(self):
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.worker.terminate()
            self.worker.wait()
        super().reject()

    def closeEvent(self, event):
        if hasattr(self, 'worker') and self.worker.isRunning():
            self.worker.terminate()
            self.worker.wait()

        if hasattr(self, 'sync_manager') and self.sync_manager:
            self.sync_manager.wait_all_workers()

        event.accept()



class LocalSearchDialog(QDialog):
    def __init__(self, mode_widget, parent=None):
        super().__init__(parent)
        self.mode_widget = mode_widget
        self.setWindowTitle("에디터 내부 검색")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Tool)
        self.setModal(False)
        self.resize(400, 70)
        self.setStyleSheet("""
            QDialog { border: 2px solid #555555; background-color: #2b2d36; }
            QLineEdit { border: 1px solid #000000; padding: 4px; background-color: #222222; color: #ffffff; }
        """)
        self.init_ui()

    def init_ui(self):
        layout = QHBoxLayout(self)
        self.input_keyword = QLineEdit()
        self.input_keyword.setPlaceholderText("검색어를 입력하세요...")
        self.input_keyword.returnPressed.connect(self.do_search_next)

        btn_prev = QPushButton("이전 찾기")
        btn_prev.clicked.connect(self.do_search_prev)

        btn_next = QPushButton("다음 찾기")
        btn_next.clicked.connect(self.do_search_next)
        btn_next.setDefault(True)  # 엔터키 입력 시 기본 작동

        layout.addWidget(QLabel("검색:"))
        layout.addWidget(self.input_keyword)
        layout.addWidget(btn_prev)
        layout.addWidget(btn_next)

    def do_search_next(self):
        self._do_search(forward=True)

    def do_search_prev(self):
        self._do_search(forward=False)

    def _do_search(self, forward):
        keyword = self.input_keyword.text().strip()
        if not keyword: return

        editor = self.mode_widget.active_editor
        if not editor: return

        from PyQt6.QtGui import QTextDocument
        options = QTextDocument.FindFlag(0)
        if not forward:
            options |= QTextDocument.FindFlag.FindBackward

        found = editor.find(keyword, options)
        if not found:
            op = editor.textCursor().MoveOperation.Start if forward else editor.textCursor().MoveOperation.End
            editor.moveCursor(op)
            found = editor.find(keyword, options)
            if not found:
                QMessageBox.information(self, "검색", "더 이상 일치하는 내용이 없습니다.")
