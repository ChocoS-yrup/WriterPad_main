"""Opt-in UI bound to the manager used by the live writing view."""
import json
import os
from pathlib import Path

from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QMessageBox, QFileDialog

import general_test_gate as preparation


class GateWorker(QThread):
    resultReady = pyqtSignal(bool, object)

    def __init__(self, action, manager, ticket, parent):
        super().__init__(parent)
        self.action, self.manager, self.ticket = action, manager, ticket

    def run(self):
        try:
            self.resultReady.emit(True, self.action(self.manager, self.ticket))
        except Exception as error:
            message = str(error) if isinstance(error, preparation.PreparationError) else "연결 확인에 실패했습니다. 송신 보류를 유지합니다."
            self.resultReady.emit(False, message)


class GeneralTestGateCard(QFrame):
    def __init__(self, pm, parent=None):
        super().__init__(parent)
        self.pm = pm
        self.manager = None
        self.ticket = None
        self.worker = None
        self.waiting_for_pull = False
        self.report = None
        self.release_requested = False
        self.setObjectName("GeneralTestGateCard")
        self.setVisible(os.environ.get("WRITERPAD_GENERAL_TEST_GATE_UI") == "1")
        layout = QVBoxLayout(self)
        title = QLabel("일반동기화 검증 20260910 · 준비 확인")
        layout.addWidget(title)
        self.status = QLabel("지정된 Staging 시험 작품만 확인합니다. 준비 확인은 서버를 읽고 구조를 수신합니다. 관문을 열어도 송신 보류는 유지됩니다.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.prepare_button = QPushButton("준비 확인·수신 · 원고 송신 없음")
        self.activate_button = QPushButton("시험 관문 열기 · 송신 보류 유지")
        self.export_button = QPushButton("현재 확인 결과 내보내기")
        self.release_button = QPushButton("시험 송신 보류 해제 · 새 수신 후 확인")
        self.reprepare_button = QPushButton("새 시험 준비로 돌아가기 · 송신 보류")
        for button, action in ((self.prepare_button, self.prepare), (self.activate_button, self.activate),
                               (self.release_button, self.release_hold), (self.reprepare_button, self.reprepare),
                               (self.export_button, self.export)):
            button.clicked.connect(action)
            layout.addWidget(button)
        self.poll = QTimer(self)
        self.poll.setInterval(150)
        self.poll.timeout.connect(self._poll_pull)
        self._buttons()

    @property
    def busy(self):
        return self.worker is not None or self.waiting_for_pull

    def bind_manager(self, manager):
        if self.busy:
            raise RuntimeError("preparation is running")
        self.manager = manager
        self.ticket = None
        self.report = None
        self._buttons()

    def _bound(self):
        if self.manager is None:
            raise preparation.PreparationError("집필 화면의 연결이 아직 준비되지 않았습니다.")
        root = getattr(getattr(self.manager, "_v2_wpm", None), "writing_root_path", None)
        selected = getattr(self.pm, "project_path", None)
        if not root or not selected or Path(root).absolute().parent != Path(selected).absolute():
            raise preparation.PreparationError("현재 화면과 동기화 작품이 다릅니다.")
        return self.manager

    def _buttons(self):
        released = bool(self.report) and not self.report.get("outbound_send_held", True)
        self.prepare_button.setEnabled(self.manager is not None and not self.busy and not released)
        self.activate_button.setEnabled(bool(self.report) and not self.report.get("gate_open", False) and not self.busy)
        self.release_button.setEnabled(bool(self.report) and self.report.get("gate_open", False) and not released and not self.busy)
        self.reprepare_button.setEnabled(self.manager is not None and not self.busy)
        self.export_button.setEnabled(bool(self.report) and not self.busy)

    def _failure(self, message):
        self.report = None
        self.release_requested = False
        self.waiting_for_pull = False
        self.poll.stop()
        self.status.setText(str(message))
        self._buttons()

    def prepare(self):
        if self.busy:
            return
        self.report = None
        try:
            manager = self._bound()
            self.ticket = preparation.begin(manager)
            self.ticket["view_guard"] = self._bound
            self._start(preparation.handshake)
        except Exception as error:
            self._failure(str(error) if isinstance(error, preparation.PreparationError) else "준비를 시작하지 못했습니다. 원고 송신이나 관문 변경을 실행하지 않았습니다.")

    def _start(self, action):
        self.worker_result = None
        self.worker_action = action
        self.worker = GateWorker(action, self.manager, self.ticket, self)
        self.worker.resultReady.connect(self._result)
        self.worker.finished.connect(self._finished)
        self.status.setText("현재 앱 연결을 확인하고 있습니다. 원고 송신 보류를 유지합니다.")
        self._buttons()
        self.worker.start()

    def _result(self, success, result):
        self.worker_result = (success, result)

    def _finished(self):
        self.worker.wait()
        self.worker.deleteLater()
        self.worker = None
        if not self.worker_result or not self.worker_result[0]:
            self._failure(self.worker_result[1] if self.worker_result else "확인이 중단됐습니다. 송신 보류를 유지합니다.")
            return
        try:
            self._bound()
            if self.worker_action in (preparation.handshake, preparation.prepare_release):
                preparation.start_pull(self.manager, self.ticket)
                self.waiting_for_pull = True
                self.status.setText("현재 앱에서 UUID 정렬표와 구조를 수신하고 있습니다. 원고 송신은 보류합니다.")
                self.poll.start()
                self._buttons()
            else:
                self._show_report(preparation.current_observation(self.manager, self.ticket))
        except Exception as error:
            if self.worker_action is preparation.release:
                self._failure("보류 해제는 완료됐지만 현재 화면 상태를 다시 확인해야 합니다. ‘새 시험 준비로 돌아가기’로 다시 보류할 수 있습니다.")
            else:
                self._failure(str(error) if isinstance(error, preparation.PreparationError) else "수신 또는 상태 확인에 실패했습니다. 송신 보류를 유지합니다.")

    def _poll_pull(self):
        try:
            self._bound()
            preparation._validate(self.manager, self.ticket)
            coordinator = self.manager._current_pull_coordinator()
            if coordinator["pulling"] or self.manager._v2_pull_worker is not None:
                return
            self.waiting_for_pull = False
            self.poll.stop()
            report = preparation.observation(self.manager, self.ticket)
            if self.release_requested:
                self.release_requested = False
                self._start(preparation.release)
            else:
                self._show_report(report)
        except Exception as error:
            self._failure(str(error) if isinstance(error, preparation.PreparationError) else "수신 결과를 확인하지 못했습니다. 송신 보류를 유지합니다.")

    def _show_report(self, report):
        self.report = report
        if not report.get("outbound_send_held", True):
            self.status.setText("시험 작품의 송신 보류를 해제했습니다. 해제 자체는 원고·폴더를 보내지 않습니다. 이후 편집은 일반 동기화 경로를 사용합니다. 다음 실험은 ‘새 시험 준비로 돌아가기’에서 시작하세요.")
            self._buttons()
            return
        self.status.setText(
            ("시험 관문이 열렸습니다. " if report["gate_open"] else "준비 확인이 완료됐습니다. 관문은 닫혀 있습니다. ")
            + "정렬표 12개·구조·C9·미완료 0건 확인. 송신 보류는 앱 재시작 후에도 유지됩니다. 실제 원고 송신은 별도 단계입니다."
        )
        self._buttons()

    def activate(self):
        if self.busy or not self.report:
            return
        answer = QMessageBox.question(
            self, "시험 작품 관문", "일반동기화 검증 20260910의 새 계약 관문을 여시겠습니까?\n새 핸드셰이크로 다시 확인합니다. 원고·폴더 송신 보류는 유지됩니다.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._bound()
            self._start(preparation.activate)
        except preparation.PreparationError as error:
            self._failure(error)

    def export(self):
        if self.busy or not self.report:
            return
        path, _ = QFileDialog.getSaveFileName(self, "현재 확인 결과 저장", "windows-general-test-gate-observation.json", "JSON (*.json)", options=QFileDialog.Option.DontConfirmOverwrite)
        if not path:
            return
        try:
            self._bound()
            report = preparation.current_observation(self.manager, self.ticket)
            # Existing reports are evidence, never overwrite them.
            with open(path, "x", encoding="utf-8") as handle:
                json.dump(report, handle, ensure_ascii=False, indent=2)
            self.status.setText("현재 앱의 확인 결과를 저장했습니다. 새 송신은 하지 않았습니다.")
        except Exception:
            self._failure("내보내지 못했습니다. 작품·연결 상태와 새 파일 이름을 확인하세요. 이 내보내기는 송신 보류 상태를 변경하지 않습니다.")

    def release_hold(self):
        if self.busy or not self.report or not self.report.get("outbound_send_held", True):
            return
        answer = QMessageBox.question(self, "시험 송신 보류 해제",
            "지정 시험 작품의 보류를 해제하시겠습니까?\n새 연결·수신·관문·대기열을 확인한 뒤 해제합니다. 이후 편집은 서버로 동기화될 수 있습니다.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._bound()
            self.release_requested = True
            self._start(preparation.prepare_release)
        except preparation.PreparationError as error:
            self._failure(error)

    def reprepare(self):
        if self.busy:
            return
        answer = QMessageBox.question(self, "새 시험 준비",
            "송신을 보류하고 시험 관문을 닫겠습니까?\n원고·폴더는 그대로 보존합니다. 초기 빈 구조와 다르면 준비 검사는 중단됩니다.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.ticket = preparation.reprepare(self._bound())
            self.ticket["view_guard"] = self._bound
            self.report = None
            self._start(preparation.handshake)
        except Exception as error:
            self._failure(str(error) if isinstance(error, preparation.PreparationError) else "재준비를 시작하지 못했습니다. 보류 상태를 확인하세요.")
