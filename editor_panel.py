from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTextEdit, QLabel, QSplitter, QTextBrowser,
    QStackedWidget, QComboBox, QMenu, QGraphicsDropShadowEffect, QScrollArea, QFrame, QMessageBox,
    QSizePolicy, QCheckBox, QDialog, QListWidget, QLineEdit, QRadioButton, QSpinBox, QButtonGroup,
    QFontDialog, QTabWidget, QListWidgetItem, QGridLayout, QTabBar
)
from PyQt6.QtGui import QFont, QTextCursor, QGuiApplication, QTextDocument
from PyQt6.QtCore import pyqtSignal, Qt, QSettings, QTimer, QThread

from app_config import get_saved_font
from search_components import LocalSearchBar
from text_editor import SmartTextEdit


class EditorPanel(QWidget):
    """글을 작성하거나 결과를 확인하는 텍스트 영역 및 하단 유틸리티 위젯"""
    saveRequested = pyqtSignal(str, str) # 단계명(step_name), 에디터 내용(text)
    openFolderRequested = pyqtSignal(str) # 단계명
    autoSaveRequested = pyqtSignal(str, str) # 오토세이브 시그널
    aiGenerationRequested = pyqtSignal(str) # AI API 호출 시그널
    aiOpenRequested = pyqtSignal(str) # AI 창 열기 호출 시그널
    finalConfirmRequested = pyqtSignal() # 최종 확정 시그널
    sendToWritingModeRequested = pyqtSignal(str) # 집필 모드로 보내기 시그널

    def __init__(self, step_name, pm, placeholder_text=""):
        super().__init__()
        self.step_name = step_name
        self.pm = pm
        self.current_chapter = 1  # 탭별 개별 화수 기억
        self.settings = QSettings("HitomiKkeora", "WebNovelAssistant")
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        # 텍스트 에디터
        self.text_edit = SmartTextEdit()
        self.text_edit.setPlaceholderText(placeholder_text)
        self.text_edit.textChanged.connect(self.on_text_changed)

        # 검색바 (기본 숨김)
        self.search_bar = LocalSearchBar(self.text_edit)

        # 오토세이브 타이머 설정
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(5000)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.timeout.connect(self.trigger_autosave)
        self.text_edit.compositionChanged.connect(self.on_text_changed)

        # 폰트 불러오기 및 적용
        self.text_edit.setFont(get_saved_font())

        # AI 호출 플로팅 버튼
        self.btn_ai = QPushButton("✨ AI 작성", self.text_edit)
        self.btn_ai.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ai.setStyleSheet("""
            QPushButton {
                background-color: #3b3b3b;
                color: #ffffff;
                font-weight: bold;
                border-radius: 8px;
                padding: 8px 15px;
            }
            QPushButton:hover {
                background-color: #555555;
            }
        """)
        self.btn_ai.clicked.connect(self.request_ai_generation)

        # AI 창 열기 플로팅 버튼
        self.btn_ai_open = QPushButton("💬 AI 창", self.text_edit)
        self.btn_ai_open.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ai_open.setStyleSheet(self.btn_ai.styleSheet())
        self.btn_ai_open.clicked.connect(self.request_ai_open)

        # 탭 이름에 따라 보여줄 버튼 결정
        if self.step_name in ["요약", "평가"]:
            self.btn_ai.hide()
            self.btn_ai_open.show()
        else:
            self.btn_ai.show()
            self.btn_ai_open.show()

        self.lbl_tab_name = QLabel(f"{self.step_name}", self.text_edit)
        self.lbl_tab_name.setStyleSheet("color: #777777; font-size: 13px; font-weight: bold; background: transparent;")
        self.lbl_tab_name.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)

        # 하단 툴바
        bottom_layout = QHBoxLayout()

        self.lbl_count = QLabel("현재 글자 수 (공백 포함: 0 / 공백 제외: 0)")
        self.lbl_count.setStyleSheet("color: gray; font-weight: bold; font-size: 15px;")

        self.btn_copy = QPushButton("📋 복사")
        self.btn_copy.setMinimumWidth(80)
        self.btn_copy.clicked.connect(self.copy_text)

        self.btn_open_folder = QPushButton("📁 폴더 열기")
        self.btn_open_folder.setMinimumWidth(120)
        self.btn_open_folder.clicked.connect(self.request_open_folder)

        self.btn_save = QPushButton("저장")
        self.btn_save.setMinimumWidth(80)
        self.btn_save.setObjectName("DarkButton")
        self.btn_save.clicked.connect(self.request_save)

        self.btn_final_confirm = QPushButton("✅ 확정")
        self.btn_final_confirm.setMinimumWidth(80)
        self.btn_final_confirm.setStyleSheet("""
            QPushButton {
                background-color: #00d6ac;
                color: #1b1d24;
                font-weight: bold;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #00e6ba;
            }
        """)
        self.btn_final_confirm.clicked.connect(self.request_final_confirm)
        if self.step_name != "평가":
            self.btn_final_confirm.hide()

        self.btn_send_to_writing = QPushButton("🚀 집필 모드로 보내기")
        self.btn_send_to_writing.setMinimumWidth(150)
        self.btn_send_to_writing.setStyleSheet("""
            QPushButton {
                background-color: #8b5cf6;
                color: #ffffff;
                font-weight: bold;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #7c3aed;
            }
        """)
        self.btn_send_to_writing.clicked.connect(self.request_send_to_writing)

        bottom_layout.addWidget(self.lbl_count)
        bottom_layout.addStretch()
        bottom_layout.addWidget(self.btn_send_to_writing)
        bottom_layout.addWidget(self.btn_copy)
        bottom_layout.addWidget(self.btn_open_folder)
        bottom_layout.addWidget(self.btn_save)
        bottom_layout.addWidget(self.btn_final_confirm)

        layout.addWidget(self.search_bar)
        layout.addWidget(self.text_edit)
        layout.addLayout(bottom_layout)

        self.setLayout(layout)

    def set_typewriter_mode(self, enabled):
        self.text_edit.set_typewriter_mode(enabled)

    def request_ai_generation(self):
        self.aiGenerationRequested.emit(self.step_name)

    def request_ai_open(self):
        self.aiOpenRequested.emit(self.step_name)

    def request_open_folder(self):
        self.openFolderRequested.emit(self.step_name)

    def request_final_confirm(self):
        self.finalConfirmRequested.emit()

    def request_send_to_writing(self):
        cursor = self.text_edit.textCursor()
        text = cursor.selectedText().replace('\u2029', '\n')
        if not text:
            text = self.text_edit.toPlainText()
        if text:
            self.sendToWritingModeRequested.emit(text)

    def update_count(self):
        text = self.text_edit.toPlainText()
        char_count = len(text)
        no_space_count = len(text.replace(" ", "").replace("\n", "").replace("\t", ""))
        self.lbl_count.setText(f"현재 글자 수 (공백 포함: {char_count:,} / 공백 제외: {no_space_count:,})")

    def on_text_changed(self):
        self.update_count()
        self.autosave_timer.start() # 입력이 발생하면 타이머 (재)시작

    def trigger_autosave(self):
        self.autoSaveRequested.emit(
            self.step_name, self.text_edit.text_with_pending_input_method()
        )

    def request_save(self):
        """저장 버튼 클릭 시 시그널 발생"""
        self.saveRequested.emit(self.step_name, self.text_edit.toPlainText())

    def resizeEvent(self, event):
        if event:
            super().resizeEvent(event)
        # 텍스트 에디터 우측 하단에 AI 버튼 고정
        if hasattr(self, 'btn_ai') and hasattr(self, 'btn_ai_open') and hasattr(self, 'text_edit'):
            tw = self.text_edit.width()
            th = self.text_edit.height()

            bw_ai = self.btn_ai.sizeHint().width() if not self.btn_ai.isHidden() else 0
            bw_open = self.btn_ai_open.sizeHint().width() if not self.btn_ai_open.isHidden() else 0
            bh = max(self.btn_ai.sizeHint().height(), self.btn_ai_open.sizeHint().height())

            spacing = 10
            total_w = bw_ai + (spacing if (bw_ai and bw_open) else 0) + bw_open

            start_x = tw - total_w - 35
            btn_y = th - bh - 45

            if bw_ai > 0:
                self.btn_ai.resize(bw_ai, bh)
                self.btn_ai.move(start_x, btn_y)
            if bw_open > 0:
                self.btn_ai_open.resize(bw_open, bh)
                self.btn_ai_open.move(start_x + bw_ai + (spacing if bw_ai > 0 else 0), btn_y)

            if hasattr(self, 'lbl_tab_name'):
                self.lbl_tab_name.adjustSize()
                lw = self.lbl_tab_name.width()
                lh = self.lbl_tab_name.height()
                self.lbl_tab_name.move(tw - lw - 35, btn_y + bh + 4)

    def set_split_mode(self, is_split):
        if is_split:
            self.btn_ai.setText(f"{self.step_name}")
            self.btn_ai.setEnabled(False)
            self.btn_ai.setStyleSheet("""
                QPushButton {
                    background-color: #2b2b2b;
                    color: #777777;
                    font-weight: bold;
                    border-radius: 8px;
                    padding: 8px 15px;
                    border: 1px solid #3b3b3b;
                }
            """)
            if hasattr(self, 'btn_ai_open'):
                self.btn_ai_open.setEnabled(False)
                self.btn_ai_open.setStyleSheet(self.btn_ai.styleSheet())
            if hasattr(self, 'lbl_tab_name'):
                self.lbl_tab_name.hide()
            if hasattr(self, 'btn_final_confirm'):
                self.btn_final_confirm.setEnabled(False)
                self.btn_final_confirm.setStyleSheet("""
                    QPushButton {
                        background-color: #2b2b2b;
                        color: #777777;
                        font-weight: bold;
                        border-radius: 8px;
                        padding: 8px 15px;
                        border: 1px solid #3b3b3b;
                    }
                """)
        else:
            self.btn_ai.setText("✨ AI 작성")
            self.btn_ai.setEnabled(True)
            self.btn_ai.setStyleSheet("""
                QPushButton {
                    background-color: #3b3b3b;
                    color: #ffffff;
                    font-weight: bold;
                    border-radius: 8px;
                    padding: 8px 15px;
                }
                QPushButton:hover {
                    background-color: #555555;
                }
                QPushButton:pressed {
                    background-color: #2b2b2b;
                }
            """)
            if hasattr(self, 'btn_ai_open'):
                self.btn_ai_open.setEnabled(True)
                self.btn_ai_open.setStyleSheet(self.btn_ai.styleSheet())
            if hasattr(self, 'lbl_tab_name'):
                self.lbl_tab_name.show()
            if hasattr(self, 'btn_final_confirm'):
                self.btn_final_confirm.setEnabled(True)
                self.btn_final_confirm.setStyleSheet("""
                    QPushButton {
                        background-color: #1a73e8;
                        color: white;
                        font-weight: bold;
                        border-radius: 8px;
                        padding: 8px 15px;
                    }
                    QPushButton:hover {
                        background-color: #1557b0;
                    }
                    QPushButton:pressed {
                        background-color: #0d3b7a;
                    }
                """)

        self.resizeEvent(None)

    def request_open_folder(self):
        """폴더 열기 버튼 클릭 시 시그널 발생"""
        self.openFolderRequested.emit(self.step_name)

    def request_ai_generation(self):
        """AI 버튼 클릭 시 시그널 발생"""
        self.aiGenerationRequested.emit(self.step_name)

    def copy_text(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self.text_edit.toPlainText())
