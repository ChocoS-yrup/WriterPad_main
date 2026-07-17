from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTextEdit, QLabel, QSplitter, QTextBrowser,
    QStackedWidget, QComboBox, QMenu, QGraphicsDropShadowEffect, QScrollArea, QFrame, QMessageBox,
    QSizePolicy, QCheckBox, QDialog, QListWidget, QLineEdit, QRadioButton, QSpinBox, QButtonGroup,
    QFontDialog, QTabWidget, QListWidgetItem, QGridLayout, QTabBar
)
from PyQt6.QtGui import QFont, QTextCursor, QGuiApplication, QTextDocument
from PyQt6.QtCore import pyqtSignal, Qt, QSettings, QTimer, QThread

from app_config import get_saved_font


class ChatInputEdit(QTextEdit):
    returnPressed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(50)
        self.setStyleSheet("""
            QTextEdit {
                border: 1px solid #dcdcdc;
                border-radius: 5px;
                padding: 5px;
                background-color: white;
            }
        """)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.returnPressed.emit()
                event.accept()
        else:
            super().keyPressEvent(event)



class AIPanelWidgetBase(QWidget):
    """AI 결과물을 띄워놓고 피드백을 주고받을 수 있는 도킹 패널"""
    feedbackRequested = pyqtSignal(str)
    applyRequested = pyqtSignal(str)
    closeRequested = pyqtSignal()
    stopRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.step_name = ""
        self.setObjectName("AIPanelWidget")

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.loading_timer = QTimer(self)
        self.loading_timer.setInterval(1000)
        self.loading_timer.timeout.connect(self.update_loading_animation)
        self.loading_bubble = None
        self.loading_dots = 0

        # 상단 스플리터 (좌: 에디터, 우: 채팅)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 좌측: 결과 텍스트 에디터 및 옵션
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.result_editor = QTextBrowser()
        self.result_editor.setOpenExternalLinks(True)
        self.result_editor.setFont(get_saved_font())
        left_layout.addWidget(self.result_editor)

        splitter.addWidget(left_widget)

        # 우측: 채팅 영역
        chat_widget = QWidget()
        chat_layout = QVBoxLayout(chat_widget)
        chat_layout.setContentsMargins(0, 0, 0, 0)

        self.chat_scroll = QScrollArea()
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setObjectName("ChatScroll")
        self.chat_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.chat_content = QWidget()
        self.chat_content.setStyleSheet("background: transparent;")
        self.chat_vbox = QVBoxLayout(self.chat_content)
        self.chat_vbox.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.chat_vbox.setContentsMargins(10, 10, 10, 10)
        self.chat_vbox.setSpacing(10)

        self.chat_scroll.setWidget(self.chat_content)

        input_layout = QHBoxLayout()
        self.chat_input = ChatInputEdit()
        self.chat_input.setObjectName("ChatInput")
        self.chat_input.setPlaceholderText("예: 말투를 조금 더 자연스럽게 고쳐줘...")
        self.chat_input.returnPressed.connect(self.send_feedback)

        self.btn_send = QPushButton("전송")
        self.btn_send.setObjectName("SendButton")
        self.btn_send.clicked.connect(self.send_feedback)

        input_layout.addWidget(self.chat_input)
        input_layout.addWidget(self.btn_send)

        chat_layout.addWidget(self.chat_scroll)
        chat_layout.addLayout(input_layout)

        self.chat_widget = chat_widget
        splitter.addWidget(self.chat_widget)
        splitter.setSizes([400, 300]) # 비율 조정

        main_layout.addWidget(splitter)

        # 하단: 버튼 영역
        bottom_layout = QHBoxLayout()

        self.btn_toggle_chat = QPushButton("💬 채팅창 닫기")
        self.btn_toggle_chat.setObjectName("DarkButton")
        self.btn_toggle_chat.setCheckable(True)
        self.btn_toggle_chat.clicked.connect(self.toggle_chat)

        bottom_layout.addWidget(self.btn_toggle_chat)
        bottom_layout.addStretch()

        self.btn_apply_selected = QPushButton("선택한 영역 적용")
        self.btn_apply_selected.setObjectName("ApplySelectedButton")
        self.btn_apply_selected.clicked.connect(self.apply_selected_result)

        self.btn_apply = QPushButton("에디터에 적용")
        self.btn_apply.setObjectName("ApplyButton")
        self.btn_apply.clicked.connect(self.apply_result)

        self.btn_cancel = QPushButton("취소")
        self.btn_cancel.setObjectName("CancelButton")
        self.btn_cancel.clicked.connect(self.closeRequested.emit)

        bottom_layout.addWidget(self.btn_apply_selected)
        bottom_layout.addSpacing(5)
        bottom_layout.addWidget(self.btn_apply)
        bottom_layout.addSpacing(5)
        bottom_layout.addWidget(self.btn_cancel)

        main_layout.addLayout(bottom_layout)

    def update_loading_animation(self):
        if self.loading_bubble:
            self.loading_dots = (self.loading_dots % 3) + 1
            dots = "." * self.loading_dots
            self.loading_bubble.set_text(f"AI가 문맥을 분석하는 중입니다{dots}")

    def request_stop(self):
        self.stopRequested.emit()

    def start_loading_animation(self):
        self.btn_send.setEnabled(True)
        self.btn_send.setText("중지")
        self.btn_send.setStyleSheet("background-color: #d9534f; color: white; font-weight: bold;")
        try:
            self.btn_send.clicked.disconnect()
        except TypeError:
            pass
        self.btn_send.clicked.connect(self.request_stop)

        self.chat_input.setEnabled(False)
        self.chat_input.setStyleSheet("background-color: #3b3b3b; color: #888888;")
        self.loading_dots = 0
        self.loading_bubble = self.append_chat("AI", "AI가 문맥을 분석하는 중입니다.")
        self.loading_timer.start()

    def stop_loading_animation(self):
        self.loading_timer.stop()
        if self.loading_bubble:
            self.chat_vbox.removeWidget(self.loading_bubble)
            self.loading_bubble.deleteLater()
            self.loading_bubble = None
        self.btn_send.setEnabled(True)
        self.btn_send.setText("전송")
        self.btn_send.setStyleSheet("")
        try:
            self.btn_send.clicked.disconnect()
        except TypeError:
            pass
        self.btn_send.clicked.connect(self.send_feedback)

        self.chat_input.setEnabled(True)
        self.chat_input.setStyleSheet("")

    def toggle_chat(self, checked):
        if checked:
            self.chat_widget.hide()
            self.btn_toggle_chat.setText("💬 채팅창 열기")
        else:
            self.chat_widget.show()
            self.btn_toggle_chat.setText("💬 채팅창 닫기")

    def init_session(self, step_name, system_prompt="", user_text="", is_generation=True):
        self.step_name = step_name
        self.result_editor.clear()

        self.chat_session = []
        if system_prompt:
            self.chat_session.append({"role": "system", "content": system_prompt})
        if user_text:
            self.chat_session.append({"role": "user", "content": user_text})

        # 이전 채팅 내역 지우기
        while self.chat_vbox.count():
            item = self.chat_vbox.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        self.chat_input.clear()

        if is_generation:
            self.start_loading_animation()
        else:
            self.append_chat("AI", f"[{step_name}] 단계의 텍스트와 관련하여 무엇이든 물어보세요.")
            self.btn_send.setEnabled(True)
            self.chat_input.setEnabled(True)

        self.btn_apply.setEnabled(False)
        self.btn_apply_selected.setEnabled(False)

    def append_chat(self, sender, text):
        is_me = (sender != "AI")
        bubble = ChatBubble(text, is_me)
        self.chat_vbox.addWidget(bubble)
        # 스크롤 최하단으로 이동
        QTimer.singleShot(50, lambda: self.chat_scroll.verticalScrollBar().setValue(self.chat_scroll.verticalScrollBar().maximum()))
        return bubble

    def send_feedback(self):
        feedback = self.chat_input.toPlainText().strip()
        if not feedback: return

        self.append_chat("나", feedback)
        self.chat_input.clear()

        # 세션에 유저 입력 추가
        if hasattr(self, 'chat_session'):
            self.chat_session.append({"role": "user", "content": feedback})

        self.start_loading_animation()
        self.feedbackRequested.emit(feedback)

    def update_result(self, new_text, msg, is_error=False):
        import markdown
        self.stop_loading_animation()

        if new_text:
            html_text = markdown.markdown(new_text, extensions=['fenced_code', 'tables'])
            if self.result_editor.toPlainText().strip():
                cursor = self.result_editor.textCursor()
                cursor.movePosition(cursor.MoveOperation.End)
                cursor.insertHtml("<br><br><hr><br><br>" + html_text)
                self.result_editor.setTextCursor(cursor)
            else:
                self.result_editor.setHtml(html_text)

            # 세션에 AI 응답 추가
            if hasattr(self, 'chat_session'):
                self.chat_session.append({"role": "assistant", "content": new_text})

        self.append_chat("AI", msg)

        # 에러 여부에 따라 적용 버튼 활성화/비활성화 처리
        if not is_error:
            self.btn_apply.setEnabled(True)
            self.btn_apply_selected.setEnabled(True)

    def apply_result(self):
        final_text = self.result_editor.toPlainText()
        self.applyRequested.emit(final_text)

    def apply_selected_result(self):
        cursor = self.result_editor.textCursor()
        if cursor.hasSelection():
            selected_text = cursor.selectedText().replace('\u2029', '\n')
            self.applyRequested.emit(selected_text)

class ChatBubble(QWidget):
    """iMessage 스타일의 채팅 말풍선 위젯"""
    def __init__(self, text, is_me=False):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.lbl = QLabel(text)
        self.lbl.setWordWrap(True)
        self.lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        if is_me:
            self.lbl.setStyleSheet("""
                background-color: #0b81ff;
                color: white;
                border-radius: 15px;
                padding: 10px 15px;
                font-family: 'Malgun Gothic';
                font-size: 14px;
            """)
            layout.addStretch()
            layout.addWidget(self.lbl)
        else:
            self.lbl.setStyleSheet("""
                background-color: #e5e5ea;
                color: black;
                border-radius: 15px;
                padding: 10px 15px;
                font-family: 'Malgun Gothic';
                font-size: 14px;
            """)
            layout.addWidget(self.lbl)
            layout.addStretch()

    def set_text(self, text):
        self.lbl.setText(text)

class AIPanelWidget(QWidget):
    closeRequested = pyqtSignal()
    stopRequested = pyqtSignal()
    applyRequested = pyqtSignal(str)
    feedbackRequested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.stack = QStackedWidget(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.addWidget(self.stack)

        self.panels = {
            "초안": AIPanelWidgetBase(self),
            "완성본": AIPanelWidgetBase(self),
            "평가": AIPanelWidgetBase(self),
            "요약": AIPanelWidgetBase(self)
        }
        for name, panel in self.panels.items():
            panel.step_name = name
            panel.closeRequested.connect(self.closeRequested)
            panel.applyRequested.connect(self.applyRequested)
            panel.feedbackRequested.connect(self.feedbackRequested)
            panel.stopRequested.connect(self.stopRequested)
            self.stack.addWidget(panel)

        self.current_panel = self.panels["초안"]
        self.step_name = "초안"

    @property
    def chat_session(self):
        return self.current_panel.chat_session

    @property
    def pending_raw_texts(self):
        return getattr(self.current_panel, 'pending_raw_texts', "")

    @pending_raw_texts.setter
    def pending_raw_texts(self, value):
        self.current_panel.pending_raw_texts = value

    @property
    def is_final_confirm_mode(self):
        return getattr(self.current_panel, 'is_final_confirm_mode', False)

    @is_final_confirm_mode.setter
    def is_final_confirm_mode(self, value):
        self.current_panel.is_final_confirm_mode = value

    def init_session(self, step_name, system_prompt="", user_text="", is_generation=True):
        if step_name in self.panels:
            self.current_panel = self.panels[step_name]
            self.step_name = step_name
            self.stack.setCurrentWidget(self.current_panel)

            if is_generation or not getattr(self.current_panel, 'chat_session', None):
                self.current_panel.init_session(step_name, system_prompt, user_text, is_generation)

    def update_result(self, *args, **kwargs):
        self.current_panel.update_result(*args, **kwargs)

    def append_chat(self, *args, **kwargs):
        return self.current_panel.append_chat(*args, **kwargs)

    def start_loading_animation(self):
        self.current_panel.start_loading_animation()

    def stop_loading_animation(self):
        self.current_panel.stop_loading_animation()
