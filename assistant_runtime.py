import os
from copy import deepcopy
from dataclasses import dataclass

from PyQt6.QtCore import QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication, QMessageBox


class FileSaveWorker(QThread):
    finished = pyqtSignal()

    def __init__(self, filepath, content, mutex=None):
        super().__init__()
        self.filepath = filepath
        self.content = content
        self.mutex = mutex

    def run(self):
        try:
            from PyQt6.QtCore import QMutexLocker

            if self.mutex:
                locker = QMutexLocker(self.mutex)
                with open(self.filepath, "w", encoding="utf-8") as f:
                    f.write(self.content)
                del locker # 명시적으로 락 해제
            else:
                with open(self.filepath, "w", encoding="utf-8") as f:
                    f.write(self.content)

            self.prune_old_backups()
        except Exception as e:
            print(f"Save error: {e}")
        self.finished.emit()

    def prune_old_backups(self):
        dirname = os.path.dirname(self.filepath)
        if "백업" not in os.path.normpath(dirname).split(os.sep):
            return

        files = [os.path.join(dirname, f) for f in os.listdir(dirname) if f.endswith(".txt")]
        if not files: return

        file_stats = []
        for f in files:
            try:
                mtime = os.path.getmtime(f)
                file_stats.append((f, mtime))
            except Exception:
                pass

        file_stats.sort(key=lambda x: x[1])
        if not file_stats: return

        newest_file = file_stats[-1][0]

        intervals = {}
        for f, mtime in file_stats:
            interval_idx = int(mtime) // 300
            if interval_idx not in intervals:
                intervals[interval_idx] = []
            intervals[interval_idx].append((f, mtime))

        files_to_delete = []
        for idx, file_list in intervals.items():
            to_keep = file_list[-1][0]
            for f, mtime in file_list:
                if f != to_keep and f != newest_file:
                    files_to_delete.append(f)

        for f in files_to_delete:
            try:
                os.remove(f)
            except Exception:
                pass

class AutoCloseMessageBox(QMessageBox):
    """3초 뒤 자동으로 닫히는 메시지 박스"""
    def __init__(self, title, text, timeout=3, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setText(text)
        self.timeout = timeout
        self.time_left = timeout
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_timer)
        self.timer.start(1000)
        self.setStandardButtons(QMessageBox.StandardButton.Ok)
        self.update_text()

    def update_text(self):
        btn = self.button(QMessageBox.StandardButton.Ok)
        if btn:
            btn.setText(f"확인 ({self.time_left}초)")

    def update_timer(self):
        self.time_left -= 1
        if self.time_left <= 0:
            self.timer.stop()
            self.accept()
        else:
            self.update_text()

class SingleApplication(QApplication):
    """단일 실행 보장 및 기존 창 활성화를 위한 Application 클래스"""
    def __init__(self, id, *argv):
        super().__init__(*argv)
        self._id = id
        self._activation_window = None

        # 다른 인스턴스가 실행 중인지 확인 (소켓 연결 시도)
        self._out_socket = QLocalSocket()
        self._out_socket.connectToServer(self._id)
        self._is_running = self._out_socket.waitForConnected(500)

        self._in_socket = None
        self._server = None

        # 실행 중인 인스턴스가 없다면 로컬 서버(소켓) 오픈
        if not self._is_running:
            self._out_socket.close()
            self._server = QLocalServer(self)
            self._server.removeServer(self._id)
            self._server.listen(self._id)
            self._server.newConnection.connect(self._on_new_connection)

    def is_running(self):
        return self._is_running

    def set_activation_window(self, window):
        self._activation_window = window

    def _on_new_connection(self):
        # 다른 인스턴스가 실행을 시도할 때 호출됨
        self._in_socket = self._server.nextPendingConnection()
        self._in_socket.readyRead.connect(self._on_ready_read)

    def _on_ready_read(self):
        self._in_socket.readAll()
        # 기존 창을 최상단으로 끌어올림 (윈도우 환경 최적화)
        if self._activation_window:
            # 최소화되어 있다면 풀고 활성화 상태로 변경
            self._activation_window.setWindowState(
                (self._activation_window.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive
            )

            # Windows의 포커스 탈취 방지 정책을 우회하기 위해 일시적으로 '항상 위' 속성 부여
            self._activation_window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            self._activation_window.show()
            self._activation_window.raise_()
            self._activation_window.activateWindow()

            # 다시 '항상 위' 속성 해제 (정상 상태 복귀)
            self._activation_window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
            self._activation_window.show()

    def wake_up_server(self):
        # 실행 중인 서버(기존 창)로 신호를 보내서 깨움
        if self._is_running and self._out_socket.state() == QLocalSocket.LocalSocketState.ConnectedState:
            self._out_socket.write(b"WAKEUP")
            self._out_socket.waitForBytesWritten(500)

@dataclass(frozen=True)
class AIRequestContext:
    request_id: str
    project_name: str
    project_path: str
    chapter: int
    step_name: str
    model: str
    feedback: bool = False


class AIGenerationWorker(QThread):
    """AI API를 호출하고 비동기로 결과를 받아오는 워커 스레드"""
    # Keep QThread.finished for actual thread lifetime/cleanup.
    resultReady = pyqtSignal(str, str, int, int)
    error = pyqtSignal(str)

    def __init__(self, step_name, messages, selected_model, use_context_caching=False, parent=None):
        super().__init__(parent)
        self.step_name = step_name
        self.messages = deepcopy(messages)
        self.selected_model = selected_model
        self.use_context_caching = use_context_caching

    def run(self):
        from llm_provider import LLMFactory
        try:
            if self.isInterruptionRequested():
                return
            provider = LLMFactory.get_provider(self.selected_model)
            res = provider.generate(self.messages, use_context_caching=self.use_context_caching)
            if self.isInterruptionRequested():
                return
            if isinstance(res, tuple) and len(res) >= 3:
                result_text, in_tok, out_tok = res[0], res[1], res[2]
            else:
                result_text, in_tok, out_tok = str(res), 0, 0
            self.resultReady.emit(self.step_name, result_text, in_tok or 0, out_tok or 0)
        except Exception as e:
            if not self.isInterruptionRequested():
                self.error.emit(str(e))
