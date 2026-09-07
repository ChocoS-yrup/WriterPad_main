import os

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTextEdit, QLabel, QSplitter, QTextBrowser,
    QStackedWidget, QComboBox, QMenu, QGraphicsDropShadowEffect, QScrollArea, QFrame, QMessageBox,
    QSizePolicy, QCheckBox, QDialog, QListWidget, QLineEdit, QRadioButton, QSpinBox, QButtonGroup,
    QFontDialog, QTabWidget, QListWidgetItem, QGridLayout, QTabBar, QFileDialog
)
from PyQt6.QtGui import QFont, QTextCursor, QGuiApplication, QTextDocument
from PyQt6.QtCore import (
    pyqtSignal, Qt, QSettings, QTimer, QThread, QSignalBlocker,
)

from app_config import get_saved_font, save_font_to_json
from cloud_config import classify_cloud_error
from llm_provider import load_model_catalog, resolve_model_selection
from project_manager import startup_mode_from_config


class SupabaseLoginWorker(QThread):
    resultReady = pyqtSignal(bool, str)

    def __init__(self, email, password):
        super().__init__()
        self.email = email
        self.password = password

    def run(self):
        from sync_manager import SyncManager
        success, message = SyncManager().sign_in(self.email, self.password)
        self.resultReady.emit(success, message)


class SupabaseLogoutWorker(QThread):
    resultReady = pyqtSignal(bool, str)

    def run(self):
        try:
            from sync_manager import SyncManager

            SyncManager().sign_out()
            self.resultReady.emit(True, "")
        except Exception as error:
            self.resultReady.emit(False, classify_cloud_error(error).message)


class SettingsPanel(QWidget):
    fontChanged = pyqtSignal(object)
    traySettingChanged = pyqtSignal(bool)
    typewriterToggled = pyqtSignal(str, bool)
    extractRequested = pyqtSignal(bool, int, int, str)
    modelRefreshRequested = pyqtSignal()
    _TYPEWRITER_CHECKBOX_ATTRS = {
        "요약": "chk_tw_summary",
        "초안": "chk_tw_draft",
        "평가": "chk_tw_eval",
        "완성본": "chk_tw_completed",
        "집필모드": "chk_tw_writing",
    }

    def __init__(self, pm):
        super().__init__()
        self.pm = pm
        from security_manager import SecurityManager

        self.setObjectName("SettingsPanel")
        self.setStyleSheet("""
            QWidget#SettingsPanel, QWidget#SettingsPage {
                background-color: #181b22;
                color: #e5e7eb;
                font-family: 'Malgun Gothic';
            }
            QScrollArea#SettingsScroll {
                background: transparent;
                border: none;
            }
            QFrame#SettingsCard {
                background-color: #20242d;
                border: 1px solid #343b49;
                border-radius: 12px;
            }
            QLabel#SectionTitle {
                color: #f3f4f6;
                font-size: 18px;
                font-weight: 700;
                border: none;
                background: transparent;
            }
            QLabel#SectionDescription {
                color: #9ca3af;
                font-size: 13px;
                font-weight: 400;
                border: none;
                background: transparent;
            }
            QLabel {
                color: #e5e7eb;
                font-size: 14px;
                font-weight: 500;
            }
            QCheckBox, QRadioButton {
                color: #e5e7eb;
                background-color: transparent;
                border: none;
                font-size: 14px;
                font-weight: 600;
                spacing: 8px;
                padding: 4px 2px;
            }
            QCheckBox::indicator, QRadioButton::indicator {
                width: 18px;
                height: 18px;
            }
            QLineEdit, QSpinBox, QComboBox {
                min-height: 38px;
                background-color: #171a20;
                border: 1px solid #3d4554;
                border-radius: 7px;
                padding: 0 11px;
                color: #f9fafb;
                font-size: 14px;
                selection-background-color: #2f6df6;
            }
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
                border: 1px solid #4f83ff;
            }
            QTextEdit {
                background-color: #171a20;
                color: #e5e7eb;
                border: 1px solid #3d4554;
                border-radius: 8px;
                padding: 12px;
                font-size: 14px;
                font-weight: 400;
            }
            QPushButton {
                min-height: 38px;
                padding: 0 18px;
                color: #f9fafb;
                background-color: #2d3440;
                border: 1px solid #424b5c;
                border-radius: 7px;
                font-size: 14px;
                font-weight: 700;
            }
            QPushButton:hover { background-color: #394252; }
            QPushButton:pressed { background-color: #252b35; }
            QPushButton:disabled { color: #6b7280; background-color: #232730; }
            QPushButton[primary="true"] {
                background-color: #2f6df6;
                border-color: #2f6df6;
            }
            QPushButton[primary="true"]:hover { background-color: #407cff; }
            QTabWidget#MainSettingsTabs::pane {
                border: none;
                border-top: 1px solid #343b49;
                background-color: #181b22;
                top: -1px;
            }
            QTabWidget#MainSettingsTabs > QTabBar::tab {
                background-color: transparent;
                color: #9ca3af;
                border: none;
                border-bottom: 3px solid transparent;
                padding: 13px 20px;
                margin-right: 4px;
                font-size: 15px;
                font-weight: 700;
            }
            QTabWidget#MainSettingsTabs > QTabBar::tab:selected {
                color: #ffffff;
                border-bottom-color: #3b74ff;
                background-color: #20242d;
            }
            QTabWidget#MainSettingsTabs > QTabBar::tab:hover:!selected {
                color: #d1d5db;
                background-color: #20242d;
            }
            QTabWidget#PromptTabs::pane {
                border: 1px solid #343b49;
                border-radius: 8px;
                background-color: #171a20;
                top: -1px;
            }
            QTabWidget#PromptTabs > QTabBar::tab {
                color: #9ca3af;
                background-color: #171a20;
                border: 1px solid #343b49;
                padding: 9px 16px;
                font-size: 13px;
                font-weight: 700;
            }
            QTabWidget#PromptTabs > QTabBar::tab:selected {
                color: #ffffff;
                background-color: #2f6df6;
                border-color: #2f6df6;
            }
            QLabel#CostSummary {
                color: #f3f4f6;
                background-color: #171a20;
                border: 1px solid #343b49;
                border-radius: 8px;
                padding: 8px;
                font-size: 14px;
                font-weight: 700;
            }
            QScrollBar:vertical {
                background: #181b22;
                width: 10px;
                margin: 2px;
            }
            QScrollBar::handle:vertical {
                background: #465064;
                border-radius: 4px;
                min-height: 32px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
        """)

        self.main_tabs = QTabWidget()
        self.main_tabs.setObjectName("MainSettingsTabs")

        # 탭 1: 프로그램 설정
        tab_program, prog_layout = self._create_scroll_page()

        startup_card, startup_layout = self._create_card(
            "시작 화면",
            "프로그램을 실행했을 때 처음 표시할 모드를 선택합니다. 다음 실행부터 적용됩니다.",
        )
        self.combo_startup_mode = QComboBox()
        self.combo_startup_mode.setMaximumWidth(280)
        self.combo_startup_mode.addItem("AI 어시스턴트 모드", "assistant")
        self.combo_startup_mode.addItem("집필 모드", "writing")
        startup_mode = startup_mode_from_config(self.pm.global_config)
        startup_index = self.combo_startup_mode.findData(startup_mode)
        self.combo_startup_mode.setCurrentIndex(
            startup_index if startup_index >= 0 else 0
        )
        self.combo_startup_mode.currentIndexChanged.connect(
            self.save_startup_mode
        )
        startup_layout.addWidget(
            self.combo_startup_mode, 0, Qt.AlignmentFlag.AlignLeft
        )
        prog_layout.addWidget(startup_card)

        font_card, font_layout = self._create_card(
            "에디터 글꼴", "집필 화면과 보조 화면에 사용할 기본 글꼴을 선택합니다."
        )
        self.btn_font = QPushButton("🔤 에디터 폰트 변경")
        self.btn_font.setMaximumWidth(260)
        self.btn_font.clicked.connect(self.change_font)
        font_layout.addWidget(self.btn_font, 0, Qt.AlignmentFlag.AlignLeft)
        prog_layout.addWidget(font_card)

        tray_card, tray_layout = self._create_card(
            "창 닫기 동작", "닫기(X) 버튼을 눌렀을 때 프로그램을 종료할지 선택합니다."
        )
        self.chk_tray = QCheckBox("닫기 버튼을 누르면 시스템 트레이로 최소화")
        self.chk_tray.setChecked(self.pm.global_config.get("minimize_to_tray", False))
        self.chk_tray.stateChanged.connect(self.toggle_tray)
        tray_layout.addWidget(self.chk_tray)
        prog_layout.addWidget(tray_card)

        tw_card, tw_card_layout = self._create_card(
            "타자기 스크롤", "입력 중인 줄을 화면 중앙에 유지할 화면을 선택합니다."
        )
        tw_layout = QGridLayout()
        tw_layout.setHorizontalSpacing(22)
        tw_layout.setVerticalSpacing(8)
        self.chk_tw_summary = QCheckBox("요약 탭")
        self.chk_tw_draft = QCheckBox("초안 탭")
        self.chk_tw_eval = QCheckBox("평가 탭")
        self.chk_tw_completed = QCheckBox("완성본 탭")
        self.chk_tw_writing = QCheckBox("집필 모드")
        self.chk_tw_summary.setChecked(self.pm.global_config.get("tw_summary", False))
        self.chk_tw_draft.setChecked(self.pm.global_config.get("tw_draft", False))
        self.chk_tw_eval.setChecked(self.pm.global_config.get("tw_eval", False))
        self.chk_tw_completed.setChecked(self.pm.global_config.get("tw_completed", False))
        self.chk_tw_writing.setChecked(self.pm.global_config.get("tw_writing", False))
        for index, checkbox in enumerate([
            self.chk_tw_summary, self.chk_tw_draft, self.chk_tw_eval,
            self.chk_tw_completed, self.chk_tw_writing,
        ]):
            tw_layout.addWidget(checkbox, index // 3, index % 3)
        self.chk_tw_summary.toggled.connect(lambda checked: self.typewriterToggled.emit("요약", checked))
        self.chk_tw_draft.toggled.connect(lambda checked: self.typewriterToggled.emit("초안", checked))
        self.chk_tw_eval.toggled.connect(lambda checked: self.typewriterToggled.emit("평가", checked))
        self.chk_tw_completed.toggled.connect(lambda checked: self.typewriterToggled.emit("완성본", checked))
        self.chk_tw_writing.toggled.connect(lambda checked: self.typewriterToggled.emit("집필모드", checked))
        tw_card_layout.addLayout(tw_layout)
        prog_layout.addWidget(tw_card)

        # 탭 2: 클라우드 계정
        tab_cloud, cloud_layout = self._create_scroll_page()
        account_card, account_layout = self._create_card(
            "클라우드 동기화 계정",
            "로그인하면 여러 기기에서 작품을 안전하게 동기화할 수 있습니다.",
        )
        self.lbl_supabase_status = QLabel()
        self.lbl_supabase_status.setObjectName("CloudAccountStatus")
        self.lbl_supabase_status.setWordWrap(True)
        account_layout.addWidget(self.lbl_supabase_status)

        sync_layout = QGridLayout()
        sync_layout.setHorizontalSpacing(12)
        sync_layout.setVerticalSpacing(12)
        sync_layout.setColumnStretch(1, 1)
        self.edit_supabase_email = QLineEdit()
        self.edit_supabase_email.setPlaceholderText("이메일")
        self.edit_supabase_password = QLineEdit()
        self.edit_supabase_password.setPlaceholderText("비밀번호 (저장되지 않음)")
        self.edit_supabase_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.btn_supabase_login = QPushButton("동기화 로그인")
        self.btn_supabase_login.setProperty("primary", True)
        self.btn_supabase_login.setMinimumWidth(150)
        self.btn_supabase_login.clicked.connect(self.login_supabase)
        self.btn_supabase_logout = QPushButton("계정 로그아웃")
        self.btn_supabase_logout.setMinimumWidth(140)
        self.btn_supabase_logout.clicked.connect(self.logout_supabase)
        sync_layout.addWidget(QLabel("이메일"), 0, 0)
        sync_layout.addWidget(self.edit_supabase_email, 0, 1)
        sync_layout.addWidget(QLabel("비밀번호"), 1, 0)
        sync_layout.addWidget(self.edit_supabase_password, 1, 1)
        account_actions = QHBoxLayout()
        account_actions.setSpacing(10)
        account_actions.addStretch()
        account_actions.addWidget(self.btn_supabase_login)
        account_actions.addWidget(self.btn_supabase_logout)
        sync_layout.addLayout(account_actions, 2, 1)
        account_layout.addLayout(sync_layout)
        cloud_layout.addWidget(account_card)

        security_card, security_layout = self._create_card(
            "로그인 정보 보호",
            "비밀번호는 저장하지 않습니다. 로그인 후 발급된 세션만 Windows 자격 증명 저장소에 A/B 프로필별로 보관합니다.",
        )
        session_note = QLabel(
            "• 입력한 비밀번호는 로그인 요청 직후 메모리에서 지워집니다.\n"
            "• 자동 로그인 중에는 계정 이메일과 로그인 상태를 화면에 표시합니다.\n"
            "• 인터넷이 끊겨도 로컬 저장과 SQLite 재시도 큐는 계속 동작합니다."
        )
        session_note.setWordWrap(True)
        session_note.setStyleSheet("color: #cbd5e1;")
        security_layout.addWidget(session_note)
        cloud_layout.addWidget(security_card)

        diagnostics_card, diagnostics_layout = self._create_card(
            "동기화 진단",
            "서버 요청 없이 이 컴퓨터에 기록된 최근 동기화 상태만 확인합니다.",
        )
        self.lbl_sync_diagnostics = QLabel()
        self.lbl_sync_diagnostics.setObjectName("CloudAccountStatus")
        self.lbl_sync_diagnostics.setWordWrap(True)
        self.lbl_sync_diagnostics.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        diagnostics_layout.addWidget(self.lbl_sync_diagnostics)
        diagnostic_actions = QHBoxLayout()
        self.lbl_diagnostics_copy_status = QLabel("")
        self.lbl_diagnostics_copy_status.setStyleSheet("color: #86efac;")
        self.btn_refresh_sync_diagnostics = QPushButton("진단 정보 새로고침")
        self.btn_copy_sync_diagnostics = QPushButton("진단 정보 복사")
        self.btn_refresh_sync_diagnostics.clicked.connect(
            self.refresh_sync_diagnostics
        )
        self.btn_copy_sync_diagnostics.clicked.connect(
            self.copy_sync_diagnostics
        )
        diagnostic_actions.addWidget(self.lbl_diagnostics_copy_status)
        diagnostic_actions.addStretch()
        diagnostic_actions.addWidget(self.btn_refresh_sync_diagnostics)
        diagnostic_actions.addWidget(self.btn_copy_sync_diagnostics)
        diagnostics_layout.addLayout(diagnostic_actions)
        cloud_layout.addWidget(diagnostics_card)
        review_card, review_layout = self._create_card(
            "역방향 계약 검토 · 최종검증03",
            "원고 아래 빈 3권과 1권 → 2권 → 3권 순서의 검토 요청을 보관합니다. "
            "준비·내보내기는 관문을 닫은 상태에서 사용하며 전송하지 않습니다. "
            "실제 송신은 별도로 승인한 고정 배치 1회만 실행합니다.",
        )
        self.lbl_contract_review = QLabel("최종검증03의 일반 수신이 완료된 뒤 준비하세요.")
        self.lbl_contract_review.setWordWrap(True)
        review_layout.addWidget(self.lbl_contract_review)
        review_actions = QHBoxLayout()
        self.btn_prepare_contract_review = QPushButton("3권 검토 요청 준비")
        self.btn_export_contract_review = QPushButton("저장된 요청 내보내기")
        self.btn_prepare_contract_review.clicked.connect(self.prepare_contract_review)
        self.btn_export_contract_review.clicked.connect(self.export_contract_review)
        self.btn_send_contract_review = QPushButton("검토 배치 1회 송신")
        self.btn_send_contract_review.clicked.connect(self.send_contract_review)
        review_actions.addWidget(self.btn_prepare_contract_review)
        review_actions.addWidget(self.btn_export_contract_review)
        review_actions.addWidget(self.btn_send_contract_review)
        review_layout.addLayout(review_actions)
        readiness_actions = QHBoxLayout()
        self.btn_read_contract_readiness = QPushButton('준비 상태 조회 · 송신 없음')
        self.btn_export_contract_readiness = QPushButton('조회 결과 내보내기')
        self.btn_read_contract_readiness.clicked.connect(self.read_contract_readiness)
        self.btn_export_contract_readiness.clicked.connect(self.export_contract_readiness)
        readiness_actions.addWidget(self.btn_read_contract_readiness)
        readiness_actions.addWidget(self.btn_export_contract_readiness)
        review_layout.addLayout(readiness_actions)
        self.lbl_contract_readiness = QLabel('준비 상태 조회는 실행 기록이 있어도 사용할 수 있습니다. 송신하지 않습니다.')
        self.lbl_contract_readiness.setWordWrap(True)
        review_layout.addWidget(self.lbl_contract_readiness)
        recovery_read_actions = QHBoxLayout()
        self.btn_recovery_server_read = QPushButton('서버 원장 조회 · 회차 생성 없음')
        self.btn_recovery_server_export = QPushButton('원장 조회 결과 내보내기')
        self.btn_recovery_server_read.clicked.connect(self.read_recovery_server)
        self.btn_recovery_server_export.clicked.connect(self.export_recovery_server)
        recovery_read_actions.addWidget(self.btn_recovery_server_read)
        recovery_read_actions.addWidget(self.btn_recovery_server_export)
        review_layout.addLayout(recovery_read_actions)
        self.lbl_recovery_server_read = QLabel('현재 로그인으로 원장만 조회합니다. 복구 회차를 만들지 않습니다.')
        self.lbl_recovery_server_read.setWordWrap(True)
        review_layout.addWidget(self.lbl_recovery_server_read)
        self.btn_http_zero_recovery = QPushButton('HTTP 0 중단 · 별도 복구 승인')
        self.btn_http_zero_recovery.clicked.connect(self.send_http_zero_recovery)
        review_layout.addWidget(self.btn_http_zero_recovery)
        resume_actions = QHBoxLayout()
        self.btn_post_coordination_read = QPushButton('조정 후 원장 조회 · 회차 생성 없음')
        self.btn_post_coordination_export = QPushButton('조정 후 원장 결과 내보내기')
        self.btn_post_coordination_read.clicked.connect(lambda:self.read_recovery_server(post_coordination=True))
        self.btn_post_coordination_export.clicked.connect(lambda:self.export_recovery_server(post_coordination=True))
        resume_actions.addWidget(self.btn_post_coordination_read)
        resume_actions.addWidget(self.btn_post_coordination_export)
        review_layout.addLayout(resume_actions)
        self.btn_post_coordination_resume = QPushButton('조정 후 고정 요청 · 추가 1회 승인')
        self.btn_post_coordination_resume.clicked.connect(self.send_post_coordination_resume)
        review_layout.addWidget(self.btn_post_coordination_resume)
        cloud_layout.addWidget(review_card)
        # Visibility only: this opt-in never grants execution or opens a gate.
        review_card.setObjectName('ContractReviewDiagnosticsCard')
        review_card.setVisible(os.environ.get('WRITERPAD_CONTRACT_REVIEW_UI') == '1')
        cloud_layout.addStretch()
        self.refresh_supabase_account_status()
        self.refresh_sync_diagnostics()

        extract_card, extract_card_layout = self._create_card(
            "완성본 내보내기", "완성본을 원하는 범위와 파일 형식으로 저장합니다."
        )
        ext_layout = QVBoxLayout()
        ext_layout.setSpacing(10)
        range_layout = QHBoxLayout()
        self.radio_all = QRadioButton("전체 추출 (1화 ~ 최신화)")
        self.radio_all.setChecked(True)
        self.radio_partial = QRadioButton("부분 추출")
        self.range_group = QButtonGroup(self)
        self.range_group.addButton(self.radio_all)
        self.range_group.addButton(self.radio_partial)
        range_layout.addWidget(self.radio_all)
        range_layout.addWidget(self.radio_partial)
        range_layout.addStretch()
        ext_layout.addLayout(range_layout)
        spin_layout = QHBoxLayout()
        self.spin_start = QSpinBox()
        self.spin_start.setRange(1, 999)
        self.spin_start.setEnabled(False)
        self.spin_end = QSpinBox()
        self.spin_end.setRange(1, 999)
        self.spin_end.setEnabled(False)
        spin_layout.addWidget(QLabel("시작 화수:"))
        spin_layout.addWidget(self.spin_start)
        spin_layout.addWidget(QLabel("끝 화수:"))
        spin_layout.addWidget(self.spin_end)
        spin_layout.addStretch()
        ext_layout.addLayout(spin_layout)
        self.radio_partial.toggled.connect(self.spin_start.setEnabled)
        self.radio_partial.toggled.connect(self.spin_end.setEnabled)
        fmt_layout = QHBoxLayout()
        self.radio_txt = QRadioButton(".txt 로 추출")
        self.radio_txt.setChecked(True)
        self.radio_pdf = QRadioButton(".pdf 로 추출")
        self.fmt_group = QButtonGroup(self)
        self.fmt_group.addButton(self.radio_txt)
        self.fmt_group.addButton(self.radio_pdf)
        fmt_layout.addWidget(self.radio_txt)
        fmt_layout.addWidget(self.radio_pdf)
        fmt_layout.addStretch()
        ext_layout.addLayout(fmt_layout)
        self.btn_extract = QPushButton("완성본 추출하기")
        self.btn_extract.setProperty("primary", True)
        self.btn_extract.setMaximumWidth(260)
        self.btn_extract.clicked.connect(self.on_extract_clicked)
        ext_layout.addWidget(self.btn_extract, 0, Qt.AlignmentFlag.AlignLeft)
        extract_card_layout.addLayout(ext_layout)
        prog_layout.addWidget(extract_card)
        prog_layout.addStretch()

        # 탭 3: 프롬프트 설정
        tab_prompt = QWidget()
        tab_prompt.setObjectName("SettingsPage")
        prompt_layout = QVBoxLayout(tab_prompt)
        prompt_layout.setContentsMargins(18, 18, 18, 18)
        prompt_layout.setSpacing(14)
        prompt_card, prompt_card_layout = self._create_card(
            "AI 프롬프트",
            "작품별로 초안·평가·요약에 사용할 시스템 프롬프트를 관리합니다.",
        )
        self.prompt_tabs = QTabWidget()
        self.prompt_tabs.setObjectName("PromptTabs")
        self.edit_prompt_draft = QTextEdit()
        self.edit_prompt_draft.setPlaceholderText("초안 생성용 시스템 프롬프트를 입력하세요...")
        self.edit_prompt_draft.setPlainText(self.pm.get_project_setting("prompt_draft", self.pm.global_config.get("prompt_draft", "")))
        self.edit_prompt_eval = QTextEdit()
        self.edit_prompt_eval.setPlaceholderText("평가용 시스템 프롬프트를 입력하세요...")
        self.edit_prompt_eval.setPlainText(self.pm.get_project_setting("prompt_eval", self.pm.global_config.get("prompt_eval", "")))
        self.edit_prompt_summary = QTextEdit()
        self.edit_prompt_summary.setPlaceholderText("요약용 시스템 프롬프트를 입력하세요...")
        self.edit_prompt_summary.setPlainText(self.pm.get_project_setting("prompt_summary", self.pm.global_config.get("prompt_summary", "")))
        self.prompt_tabs.addTab(self.edit_prompt_draft, "초안 프롬프트")
        self.prompt_tabs.addTab(self.edit_prompt_eval, "평가 프롬프트")
        self.prompt_tabs.addTab(self.edit_prompt_summary, "요약 프롬프트")
        self.btn_global_search = QPushButton("🔍 전체 텍스트 검색 (Ctrl+Shift+F)")
        self.btn_global_search.setMinimumWidth(220)
        self.btn_save_prompts = QPushButton("💾 프롬프트 저장")
        self.btn_save_prompts.setProperty("primary", True)
        self.btn_save_prompts.setMinimumWidth(220)
        self.btn_save_prompts.clicked.connect(self.save_prompts)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_global_search)
        btn_layout.addWidget(self.btn_save_prompts)
        prompt_card_layout.addWidget(self.prompt_tabs, 1)
        prompt_card_layout.addLayout(btn_layout)
        prompt_layout.addWidget(prompt_card, 1)

        # 탭 4: 비용 모니터링 및 API 키 설정
        tab_api, api_outer_layout = self._create_scroll_page()

        dashboard_card, dashboard_layout = self._create_card(
            "API 사용 비용", "모든 프로젝트의 사용 비용을 기간별로 확인합니다."
        )

        # 요약 라벨들
        self.lbl_cost_total = QLabel("전체 누적: $0.00")
        self.lbl_cost_year = QLabel("올해 누적: $0.00")
        self.lbl_cost_month = QLabel("이번 달 누적: $0.00")
        self.lbl_cost_today = QLabel("오늘 누적: $0.00")

        for lbl in [self.lbl_cost_total, self.lbl_cost_year, self.lbl_cost_month, self.lbl_cost_today]:
            lbl.setObjectName("CostSummary")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setMinimumHeight(52)

        summary_layout = QGridLayout()
        summary_layout.setHorizontalSpacing(10)
        summary_layout.setVerticalSpacing(10)
        summary_layout.addWidget(self.lbl_cost_total, 0, 0)
        summary_layout.addWidget(self.lbl_cost_year, 0, 1)
        summary_layout.addWidget(self.lbl_cost_month, 1, 0)
        summary_layout.addWidget(self.lbl_cost_today, 1, 1)
        dashboard_layout.addLayout(summary_layout)

        # 컨트롤 영역 (작은 탭 형태)
        ctrl_layout = QHBoxLayout()
        self.tab_period = QTabBar()
        self.tab_period.addTab("일별 요약 (이번 주)")
        self.tab_period.addTab("월별 요약 (이번 달)")
        self.tab_period.addTab("연별 요약 (올해)")
        self.tab_period.setDrawBase(False)
        self.tab_period.setStyleSheet("""
            QTabBar { border: none; background: transparent; }
            QTabBar::tab { color: #9ca3af; font-size: 13px; font-weight: 700; padding: 9px 14px; border-radius: 6px; background-color: #171a20; border: 1px solid #3a3f4c; margin-right: 5px; }
            QTabBar::tab:selected { background-color: #2f6df6; color: white; border: 1px solid #2f6df6; }
            QTabBar::tab:hover:!selected { color: #e5e7eb; background-color: #2d3440; }
        """)
        self.tab_period.currentChanged.connect(lambda idx: self.refresh_dashboard())

        btn_refresh = QPushButton("🔄 새로고침")
        btn_refresh.setMinimumWidth(150)
        btn_refresh.clicked.connect(self.refresh_dashboard)

        ctrl_layout.addWidget(self.tab_period)
        ctrl_layout.addStretch()
        ctrl_layout.addWidget(btn_refresh)
        dashboard_layout.addLayout(ctrl_layout)

        from chart_components import BarChartWidget
        self.chart_widget = BarChartWidget()
        self.chart_widget.setMinimumHeight(250)
        dashboard_layout.addWidget(self.chart_widget)
        api_outer_layout.addWidget(dashboard_card)

        api_key_card, api_key_card_layout = self._create_card(
            "API 키", "제공자별 API 키는 Windows 자격 증명 저장소에 암호화해 보관합니다."
        )
        self.api_inputs = {}
        api_layout = QGridLayout()
        api_layout.setHorizontalSpacing(10)
        api_layout.setVerticalSpacing(10)
        api_layout.setColumnStretch(1, 1)
        for i, provider in enumerate(["Gemini", "Claude", "OpenAI"]):
            api_layout.addWidget(QLabel(f"{provider} API 키:"), i, 0)
            le_key = QLineEdit()
            le_key.setEchoMode(QLineEdit.EchoMode.Password)
            le_key.setText(SecurityManager.get_api_key(provider))
            self.api_inputs[provider] = le_key
            btn_toggle = QPushButton("👁️")
            btn_toggle.setFixedWidth(44)
            btn_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_toggle.clicked.connect(lambda checked, le=le_key: le.setEchoMode(
                QLineEdit.EchoMode.Normal if le.echoMode() == QLineEdit.EchoMode.Password else QLineEdit.EchoMode.Password
            ))
            api_layout.addWidget(le_key, i, 1)
            api_layout.addWidget(btn_toggle, i, 2)
        btn_save_api = QPushButton("API 키 저장")
        btn_save_api.setProperty("primary", True)
        btn_save_api.setMaximumWidth(180)
        btn_save_api.clicked.connect(self.save_api_keys)
        api_layout.addWidget(btn_save_api, 3, 1, 1, 2, Qt.AlignmentFlag.AlignRight)
        api_key_card_layout.addLayout(api_layout)
        api_outer_layout.addWidget(api_key_card)

        model_card, model_card_layout = self._create_card(
            "사용 가능 모델", "저장된 API 키로 제공자별 모델 목록을 다시 불러옵니다."
        )
        self.lbl_model_refresh_status = QLabel("API 키를 저장한 뒤, 상단 또는 여기에서 모델 목록을 새로고침하세요.")
        self.lbl_model_refresh_status.setWordWrap(True)
        self.btn_refresh_models = QPushButton("🔄 제공자별 사용 가능 모델 조회")
        self.btn_refresh_models.setMaximumWidth(320)
        self.btn_refresh_models.clicked.connect(self.modelRefreshRequested.emit)
        model_card_layout.addWidget(self.lbl_model_refresh_status)
        model_card_layout.addWidget(self.btn_refresh_models, 0, Qt.AlignmentFlag.AlignLeft)
        api_outer_layout.addWidget(model_card)

        api_outer_layout.addStretch()

        self.main_tabs.addTab(tab_program, "프로그램 설정")
        self.main_tabs.addTab(tab_cloud, "클라우드 계정")
        self.main_tabs.addTab(tab_prompt, "프롬프트 설정")
        self.main_tabs.addTab(tab_api, "API · 비용")
        self.main_tabs.currentChanged.connect(self._on_settings_tab_changed)

        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.main_tabs)
        self.setLayout(main_layout)

        # 대시보드 초기화 로드
        self.refresh_dashboard()

    def _create_scroll_page(self):
        page = QWidget()
        page.setObjectName("SettingsPage")
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(18, 18, 18, 18)
        page_layout.setSpacing(14)

        scroll = QScrollArea()
        scroll.setObjectName("SettingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        return scroll, page_layout

    def _create_card(self, title, description=""):
        card = QFrame()
        card.setObjectName("SettingsCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(12)

        title_label = QLabel(title)
        title_label.setObjectName("SectionTitle")
        card_layout.addWidget(title_label)
        if description:
            description_label = QLabel(description)
            description_label.setObjectName("SectionDescription")
            description_label.setWordWrap(True)
            card_layout.addWidget(description_label)
        return card, card_layout

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.refresh_sync_diagnostics)

    def _on_settings_tab_changed(self, index):
        if index == 1:
            self.refresh_supabase_account_status()
            self.refresh_sync_diagnostics()

    @staticmethod
    def _diagnostic_display_text(snapshot):
        snapshot = dict(snapshot or {})
        state_labels = {
            "saved": "동기화 완료",
            "backup": "로컬 복구본 생성 중",
            "syncing": "서버 전송 중",
            "offline": "오프라인 · 서버 전송 대기",
            "auth_required": "로그인 필요",
            "conflict": "문서 충돌",
            "lease": "다른 기기에서 편집 중",
            "failed": "서버 전송 대기 · 재시도 필요",
            "project_trashed": "서버 휴지통 · 동기화 중지",
            "project_purged": "서버 영구 삭제 · 로컬 사본",
        }
        login_state = str(snapshot.get("login_state") or "알 수 없음")
        state = str(snapshot.get("sync_state") or "")
        pending_count = max(0, int(snapshot.get("pending_count") or 0))
        success_at = str(snapshot.get("last_success_at") or "기록 없음")
        failure_at = str(snapshot.get("last_failure_at") or "")
        failure_reason = str(snapshot.get("last_failure_reason") or "기록 없음")
        if failure_at:
            failure_reason = f"{failure_reason} ({failure_at})"
        dropped = max(0, int(snapshot.get("dropped_log_count") or 0))
        lines = [
            f"로그인 상태: {login_state}",
            f"현재 상태: {state_labels.get(state, state or '알 수 없음')}",
            f"서버 대기 문서: {pending_count}건",
            f"마지막 동기화 성공: {success_at}",
            f"최근 동기화 실패: {failure_reason}",
        ]
        if dropped:
            lines.append(f"기록하지 못한 진단 로그: {dropped}건")
        return "\n".join(lines)

    def refresh_sync_diagnostics(self):
        try:
            from sync_manager import SyncManager

            snapshot = SyncManager().diagnostic_snapshot()
            text = SettingsPanel._diagnostic_display_text(snapshot)
        except Exception:
            text = (
                "로그인 상태: 알 수 없음\n"
                "현재 상태: 진단 정보를 읽을 수 없음\n"
                "서버 대기 문서: 알 수 없음\n"
                "마지막 동기화 성공: 기록 없음\n"
                "최근 동기화 실패: 기록 없음"
            )
        self.lbl_sync_diagnostics.setText(text)
        return text

    def read_contract_readiness(self):
        from sync_manager import SyncManager
        from contract_readiness_diagnostics import format_readiness
        try:
            report = SyncManager().inspect_reviewed_contract_readiness()
            self.lbl_contract_readiness.setText(format_readiness(report))
            return report
        except Exception:
            self.lbl_contract_readiness.setText('준비 상태를 조회하지 못했습니다. 송신하지 않았습니다.')

    def export_contract_readiness(self):
        from sync_manager import SyncManager
        path, _ = QFileDialog.getSaveFileName(self, '관찰한 준비 상태 내보내기',
                                            'windows-contract-readiness.json', 'JSON (*.json)')
        if not path:
            return
        try:
            report = SyncManager().export_reviewed_readiness(path)
            self.lbl_contract_readiness.setText('기존 관찰 결과를 내보냈습니다. 새 조회나 송신은 하지 않았습니다.')
            return report
        except Exception:
            self.lbl_contract_readiness.setText('내보내지 못했습니다. 먼저 상태를 조회하고 작품 밖에 JSON으로 저장하세요.')

    def read_recovery_server(self, *, post_coordination=False):
        from sync_manager import SyncManager
        from contract_recovery_readonly import format_recovery_read
        manager = SyncManager()
        button = self.btn_post_coordination_read if post_coordination else self.btn_recovery_server_read
        try:
            worker = manager.launch_recovery_server_readonly(post_coordination=True) if post_coordination else manager.launch_recovery_server_readonly()
            button.setEnabled(False)
            self.lbl_recovery_server_read.setText('현재 로그인으로 서버 원장을 조회 중입니다. 복구 회차를 만들지 않습니다.')
            worker.resultReady.connect(lambda report:self.lbl_recovery_server_read.setText(format_recovery_read(report)))
            worker.finished.connect(lambda:button.setEnabled(True))
            manager._start_worker(worker)
        except Exception:
            button.setEnabled(True)
            self.lbl_recovery_server_read.setText('원장 조회를 시작하지 못했습니다. 실행 중인 조회와 로그인·작품 상태를 확인하세요.')

    def export_recovery_server(self, *, post_coordination=False):
        from sync_manager import SyncManager
        path, _ = QFileDialog.getSaveFileName(self, '로그인 원장 조회 결과 내보내기',
                                            'windows-post-coordination-readonly.json' if post_coordination else 'windows-recovery-readonly.json', 'JSON (*.json)')
        if not path:
            return
        try:
            manager = SyncManager()
            export = manager.export_post_coordination_server_readonly if post_coordination else manager.export_recovery_server_readonly
            report = export(path)
            self.lbl_recovery_server_read.setText('관찰한 원장 조회 결과를 내보냈습니다. 복구 회차·송신·새 조회 없음.')
            return report
        except Exception:
            self.lbl_recovery_server_read.setText('내보내지 못했습니다. 먼저 원장을 조회하고 작품 밖에 JSON으로 저장하세요.')

    def send_http_zero_recovery(self):
        from sync_manager import SyncManager
        from reviewed_contract_sender import REVIEWED_BATCH, REVIEWED_REQUEST_SHA256, now
        from contract_http_zero_recovery import (APPROVAL_KIND, ORIGINAL_EXECUTION_ROWS_SHA256,
                                                ORIGINAL_PREPARATION_ROWS_SHA256)
        import uuid
        manager = SyncManager()
        try:
            report = manager.inspect_reviewed_contract_readiness()
            if report['stale'] or not report['http_zero_recovery']['local_candidate']:
                self.lbl_contract_readiness.setText('별도 복구 후보가 아닙니다. 기존 기록을 확인하세요.')
                return
            answer = QMessageBox.question(
                self, 'HTTP 0 중단 요청의 별도 복구 승인',
                '최종검증03: 빈 3권 생성과 원고 순서 갱신의 원요청을 사용합니다.\n'
                '기존 stopped 기록은 보존하고 별도 복구 회차 한 건을 만듭니다.\n'
                '이전 승인과 준비 조건 충족은 새 실행 승인이 아닙니다.\n'
                '서버 원장·권한 및 실행 조건 검사를 통과한 경우에만 1회 송신합니다.\n'
                '검사에서 멈추어도 복구 회차를 다시 만들거나 자동 재전송하지 않습니다.\n'
                '종료 시 원래 작품의 관문을 닫습니다.\n\n'
                f'배치: {REVIEWED_BATCH}\n요청 SHA256: {REVIEWED_REQUEST_SHA256}\n\n'
                '별도 실행 범위가 확정됐고 양쪽 편집을 멈췄습니까? 이 복구 1회를 새로 승인합니까?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            envelope = manager.reverse_contract_review()
            approval = {'kind':APPROVAL_KIND, 'approval_id':str(uuid.uuid4()), 'batch_id':REVIEWED_BATCH,
                        'request_sha256':REVIEWED_REQUEST_SHA256, 'original_execution_sha256':ORIGINAL_EXECUTION_ROWS_SHA256,
                        'original_preparation_sha256':ORIGINAL_PREPARATION_ROWS_SHA256,
                        'account_marker':envelope['account_marker'], 'approved_at':now(), 'manual_once':True}
            worker = manager.launch_http_zero_recovery_once(approval=approval)
            self.btn_http_zero_recovery.setEnabled(False)
            self.lbl_contract_readiness.setText('별도 복구 회차의 실행 조건을 확인 중입니다.')
            def result(success, state):
                if success:
                    message = ('별도 복구 영수증을 저장하고 관문을 닫았습니다: ' + state
                               + '\n구조는 일반 수신에서 확인하세요. 재전송하지 마세요.')
                else:
                    message = ('복구가 중단됐습니다: ' + state
                               + '\n재전송하지 말고 준비 상태를 조회·내보내 주세요.')
                self.lbl_contract_readiness.setText(message)
            worker.resultReady.connect(result)
            manager._start_worker(worker)
        except Exception as error:
            self.lbl_contract_readiness.setText('복구를 시작하지 못했습니다. ' + self._contract_review_error(error))

    def send_post_coordination_resume(self):
        from sync_manager import SyncManager
        from contract_post_coordination_resume import new_approval, execution_build_sha256
        manager = SyncManager()
        try:
            report = manager.inspect_reviewed_contract_readiness()
            if (report['stale'] or not report['observation']['all_conditions_met']
                    or not report['post_coordination_resume']['local_candidate']):
                self.lbl_contract_readiness.setText('추가 1회 경로의 준비 조건이 충족되지 않았습니다. 회차를 만들지 않았습니다.')
                return
            build = execution_build_sha256()
            answer = QMessageBox.question(self, '조정 후 고정 요청의 추가 1회 승인',
                '최종검증03의 빈 3권 생성과 원고 순서 갱신을 같은 요청으로 실행합니다.\n'
                '원본과 두 stopped 기록을 보존하고 이 정책의 추가 회차를 최대 1개 만듭니다.\n'
                '새 회차 생성 뒤 중단되거나 응답이 유실돼도 재시도하지 않습니다.\n'
                '검사 통과 시 관문을 일시 사용해 HTTP 최대 1회 송신하고 종료 시 닫습니다.\n'
                f'실행 설치 SHA256: {build}\n\n'
                '별도 실행 범위가 확정됐고 양쪽 편집을 멈췄습니까? 이 추가 1회를 새로 승인합니까?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            # Only the explicit Yes creates a fresh approval ID. It is not a DB claim.
            approval = new_approval(manager.reverse_contract_review())
            worker = manager.launch_post_coordination_resume_once(approval=approval)
            self.btn_post_coordination_resume.setEnabled(False)
            self.lbl_contract_readiness.setText('추가 1회 경로의 실행 조건을 확인 중입니다.')
            worker.resultReady.connect(lambda success,state:self.lbl_contract_readiness.setText(
                ('추가 회차 결과: ' if success else '추가 회차 중단: ') + state
                + '\n재전송하지 말고 준비 상태를 조회·내보내 주세요.'))
            manager._start_worker(worker)
        except Exception as error:
            self.lbl_contract_readiness.setText('추가 실행을 시작하지 못했습니다. ' + self._contract_review_error(error))

    def send_contract_review(self):
        from sync_manager import SyncManager
        from reviewed_contract_sender import REVIEWED_BATCH, REVIEWED_REQUEST_SHA256
        manager = SyncManager()
        try:
            envelope = manager.reverse_contract_review()
            if (envelope['request']['batch']['batch_id'], envelope['request_sha256']) != (REVIEWED_BATCH, REVIEWED_REQUEST_SHA256):
                self.lbl_contract_review.setText('양쪽에서 검토한 고정 요청과 다릅니다. 송신하지 않았습니다.')
                return
            answer = QMessageBox.question(
                self, '고정 검토 배치의 실제 송신',
                '별도로 승인한 실제 실행을 시작합니다.\n'
                '최종검증03: 빈 3권 생성 + 원고 순서 갱신, 고정 요청 1회입니다.\n'
                '검사 통과 후 관문을 일시적으로 사용하며 종료 시 닫습니다.\n'
                '응답 유실이나 재시작 시 자동으로 다시 보내지 않습니다.\n\n'
                f'배치: {REVIEWED_BATCH}\n요청 SHA256: {REVIEWED_REQUEST_SHA256}\n\n'
                '양쪽 편집을 멈췄고 이 실제 송신을 승인합니까?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            worker = manager.launch_reviewed_contract_once(REVIEWED_BATCH, REVIEWED_REQUEST_SHA256, approved=True)
            self.btn_send_contract_review.setEnabled(False)
            self.lbl_contract_review.setText('고정 요청의 실행 조건을 확인 중입니다.')
            def result(success, state):
                if success and state == 'committed':
                    text = '서버 성공 영수증을 저장하고 관문을 닫았습니다. 구조는 일반 수신에서 확인하세요.'
                elif success and state == 'rejected':
                    text = '서버 거절 영수증을 저장하고 관문을 닫았습니다. 재전송하지 마세요.'
                elif state == 'REVIEWED_EXECUTION_ALREADY_STARTED':
                    text = '이미 실행을 시작한 요청입니다. 재전송하지 않고 기존 실행 결과를 확인하세요.'
                else:
                    text = '실행을 중단했습니다. 재전송하지 말고 관문과 읽기 전용 결과를 확인하세요. (' + state + ')'
                self.lbl_contract_review.setText(text)
            worker.resultReady.connect(result)
            manager._start_worker(worker)
        except Exception as error:
            self.lbl_contract_review.setText('송신을 시작하지 못했습니다. ' + self._contract_review_error(error))

    def prepare_contract_review(self):
        from sync_manager import SyncManager
        try:
            envelope = SyncManager().prepare_reverse_contract_review()
            self.lbl_contract_review.setText(
                "검토 요청을 보관했습니다. 전송되지 않습니다.\n"
                + "배치: " + envelope['request']['batch']['batch_id']
                + "\n요청 SHA256: " + envelope['request_sha256']
            )
            return envelope
        except Exception as error:
            self.lbl_contract_review.setText(self._contract_review_error(error))

    @staticmethod
    def _contract_review_error(error):
        messages = {
            'CONTRACT_PREPARATION_WRONG_PROJECT': '최종검증03을 먼저 여세요.',
            'CONTRACT_PREPARATION_GATE_OPEN': '계약 관문이 닫힌 상태에서만 준비할 수 있습니다.',
            'CONTRACT_PREPARATION_MISSING': '저장된 요청이 없습니다. 먼저 검토 요청을 준비하세요.',
            'CONTRACT_PREPARATION_NOT_READY': '일반 수신과 계약 확인이 완료된 뒤 다시 준비하세요.',
            'CONTRACT_PREPARATION_QUEUE_NOT_EMPTY': '미완료 작업이나 계약 배치가 있어 준비하지 않았습니다.',
            'CONTRACT_PREPARATION_BASELINE_CHANGED': '검토 기준과 현재 상태가 다릅니다. 기존 요청은 보존됩니다.',
            'CONTRACT_PREPARATION_IDENTITY_REQUIRED': '현재 작품의 로그인 계정과 기기 정보를 확인할 수 없습니다.',
            'CONTRACT_PREPARATION_EXPORT_LOCATION': '작품 폴더 밖에 JSON 파일로 저장하세요.',
        }
        return messages.get(getattr(error, 'code', ''), '검토 요청을 처리하지 못했습니다. 전송은 하지 않았습니다.')

    def export_contract_review(self):
        from sync_manager import SyncManager
        try:
            # Resolve before prompting; cancelled export never prepares or reissues.
            SyncManager().reverse_contract_review()
            path, _ = QFileDialog.getSaveFileName(
                self, '검토 요청 내보내기', 'windows-reverse-prepared-request.json', 'JSON (*.json)'
            )
            if not path:
                return None
            envelope = SyncManager().export_reverse_contract_review(path)
            self.lbl_contract_review.setText('저장된 원본 요청을 내보냈습니다. 현재 서버 상태나 전송 승인을 뜻하지 않습니다.')
            return envelope
        except Exception as error:
            self.lbl_contract_review.setText(self._contract_review_error(error))

    def copy_sync_diagnostics(self):
        try:
            from sync_manager import SyncManager

            report = SyncManager().diagnostic_report()
        except Exception:
            report = "작가님 힘내세요 · 동기화 진단\n진단 정보를 읽을 수 없습니다."
        QApplication.clipboard().setText(report)
        self.lbl_diagnostics_copy_status.setText("민감정보를 제외하고 복사했습니다.")
        QTimer.singleShot(
            2500, lambda: self.lbl_diagnostics_copy_status.setText("")
        )
        return report

    def on_extract_clicked(self):
        is_full = self.radio_all.isChecked()
        start = self.spin_start.value()
        end = self.spin_end.value()
        fmt = "txt" if self.radio_txt.isChecked() else "pdf"
        self.extractRequested.emit(is_full, start, end, fmt)

    def save_prompts(self):
        self.pm.set_project_setting("prompt_draft", self.edit_prompt_draft.toPlainText())
        self.pm.set_project_setting("prompt_eval", self.edit_prompt_eval.toPlainText())
        self.pm.set_project_setting("prompt_summary", self.edit_prompt_summary.toPlainText())
        QMessageBox.information(self, "저장 완료", "AI 프롬프트 설정이 저장되었습니다.")

    def set_chk_tray(self, value):
        self.chk_tray.setChecked(value)

    def set_typewriter_checked(self, step_name, enabled):
        checkbox_name = self._TYPEWRITER_CHECKBOX_ATTRS.get(step_name)
        checkbox = getattr(self, checkbox_name, None) if checkbox_name else None
        if checkbox is None:
            return
        blocker = QSignalBlocker(checkbox)
        checkbox.setChecked(bool(enabled))
        del blocker

    def save_startup_mode(self, index):
        startup_mode = self.combo_startup_mode.itemData(index)
        if startup_mode not in {"assistant", "writing"}:
            startup_mode = "assistant"
        self.pm.global_config["startup_mode"] = startup_mode
        self.pm.save_global_config()

    def change_font(self):
        saved_font = get_saved_font()
        font, ok = QFontDialog.getFont(saved_font, self, "에디터 폰트 설정")
        if ok:
            save_font_to_json(font)
            self.fontChanged.emit(font)

    def toggle_tray(self, state):
        enabled = (state == 2)
        self.pm.global_config["minimize_to_tray"] = enabled
        self.pm.save_global_config()
        self.traySettingChanged.emit(enabled)

    def login_supabase(self):
        from sync_manager import SyncManager

        manager = SyncManager()
        config_state, config_message = manager.cloud_configuration_status()
        if config_state != "ready":
            self.edit_supabase_password.clear()
            self.lbl_supabase_status.setText(config_message)
            self._style_cloud_status(
                "disconnected"
                if config_state in ("disabled", "credential_busy")
                else "error"
            )
            self.btn_supabase_login.setEnabled(False)
            self.btn_supabase_logout.setEnabled(False)
            return
        email = self.edit_supabase_email.text().strip()
        password = self.edit_supabase_password.text()
        if not email or not password:
            self.lbl_supabase_status.setText("이메일과 비밀번호를 입력해주세요.")
            self._style_cloud_status("error")
            return
        self.btn_supabase_login.setEnabled(False)
        self.btn_supabase_logout.setEnabled(False)
        self.lbl_supabase_status.setText("로그인 중...")
        self._style_cloud_status("working")
        self._supabase_login_worker = SupabaseLoginWorker(email, password)

        def finished(success, message):
            self.btn_supabase_login.setEnabled(True)
            self.edit_supabase_password.clear()
            if success:
                self.refresh_supabase_account_status(message)
                self.refresh_sync_diagnostics()
                from sync_manager import SyncManager
                manager = SyncManager()
                QTimer.singleShot(0, manager._publish_sync_state)
                QTimer.singleShot(0, manager.retry_pending_syncs)
            else:
                self.lbl_supabase_status.setText(f"로그인 실패: {message}")
                self._style_cloud_status("error")
                try:
                    from sync_manager import SyncManager

                    self.btn_supabase_logout.setEnabled(
                        bool(SyncManager().authenticated_email())
                    )
                except Exception:
                    self.btn_supabase_logout.setEnabled(False)

        self._supabase_login_worker.resultReady.connect(finished)
        self._supabase_login_worker.start()

    def logout_supabase(self):
        self.btn_supabase_login.setEnabled(False)
        self.btn_supabase_logout.setEnabled(False)
        self.lbl_supabase_status.setText("로그아웃 중...")
        self._style_cloud_status("working")
        self._supabase_logout_worker = SupabaseLogoutWorker()

        def finished(success, message):
            self.btn_supabase_login.setEnabled(True)
            self.edit_supabase_password.clear()
            if success:
                self.refresh_supabase_account_status("")
                self.refresh_sync_diagnostics()
                from sync_manager import SyncManager

                manager = SyncManager()
                QTimer.singleShot(0, manager._publish_sync_state)
            else:
                self.lbl_supabase_status.setText(f"로그아웃 실패: {message}")
                self._style_cloud_status("error")
                self.btn_supabase_logout.setEnabled(True)

        self._supabase_logout_worker.resultReady.connect(finished)
        self._supabase_logout_worker.start()

    def refresh_supabase_account_status(self, email=None):
        if email is None:
            try:
                from sync_manager import SyncManager

                manager = SyncManager()
                config_state, config_message = manager.cloud_configuration_status()
                if config_state != "ready":
                    self.lbl_supabase_status.setText(config_message)
                    SettingsPanel._style_cloud_status(
                        self,
                        "disconnected"
                if config_state in ("disabled", "credential_busy")
                else "error",
                    )
                    self.btn_supabase_login.setText("동기화 로그인")
                    self.btn_supabase_login.setEnabled(False)
                    logout_button = getattr(self, "btn_supabase_logout", None)
                    if logout_button is not None:
                        logout_button.setEnabled(False)
                    return
                email = manager.authenticated_email()
            except Exception:
                email = ""
        self.btn_supabase_login.setEnabled(True)
        email = (email or "").strip()
        if email:
            self.lbl_supabase_status.setText(
                f"자동 로그인됨: {email}\n비밀번호는 저장하지 않고 Windows 로그인 세션만 사용합니다."
            )
            SettingsPanel._style_cloud_status(self, "connected")
            self.btn_supabase_login.setText("계정 변경")
            logout_button = getattr(self, "btn_supabase_logout", None)
            if logout_button is not None:
                logout_button.setEnabled(True)
        else:
            self.lbl_supabase_status.setText(
                "클라우드 동기화 계정에 로그인이 되어있지 않습니다.\n"
                "설정탭 / 클라우드 계정 로그인을 확인해주세요."
            )
            SettingsPanel._style_cloud_status(self, "disconnected")
            self.btn_supabase_login.setText("동기화 로그인")
            logout_button = getattr(self, "btn_supabase_logout", None)
            if logout_button is not None:
                logout_button.setEnabled(False)

    def _style_cloud_status(self, state):
        colors = {
            "connected": ("#86efac", "#173326", "#28543d"),
            "disconnected": ("#fdba74", "#3a291b", "#654326"),
            "working": ("#93c5fd", "#172d46", "#294d73"),
            "error": ("#fca5a5", "#421f25", "#6f3039"),
        }
        color, background, border = colors.get(state, colors["disconnected"])
        self.lbl_supabase_status.setStyleSheet(
            f"color: {color}; background-color: {background}; border: 1px solid {border}; "
            "border-radius: 8px; padding: 12px; font-size: 14px; font-weight: 700;"
        )


    def refresh_dashboard(self):
        try:
            logs = self.pm.get_aggregated_cost_history()
        except:
            logs = []

        from datetime import datetime, timedelta
        import calendar

        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        this_month_str = now.strftime("%Y-%m")
        this_year_str = now.strftime("%Y")

        total_cost = sum(item.get("cost_usd", 0.0) for item in logs)
        year_cost = sum(item.get("cost_usd", 0.0) for item in logs if item.get("timestamp", "").startswith(this_year_str))
        month_cost = sum(item.get("cost_usd", 0.0) for item in logs if item.get("timestamp", "").startswith(this_month_str))
        today_cost = sum(item.get("cost_usd", 0.0) for item in logs if item.get("timestamp", "").startswith(today_str))

        self.lbl_cost_total.setText(f"전체 누적: ${total_cost:.4f}")
        self.lbl_cost_year.setText(f"올해 누적: ${year_cost:.4f}")
        self.lbl_cost_month.setText(f"이번 달 누적: ${month_cost:.4f}")
        self.lbl_cost_today.setText(f"오늘 누적: ${today_cost:.4f}")

        # 차트 데이터 생성
        mode_idx = 0
        if hasattr(self, 'tab_period'):
            mode_idx = self.tab_period.currentIndex()
        mode = ["일별", "월별", "연별"][mode_idx]
        chart_data = []

        if "일별" in mode:
            # 이번 주 월요일 ~ 일요일
            monday = now - timedelta(days=now.weekday())
            days = ["월", "화", "수", "목", "금", "토", "일"]
            for i in range(7):
                target_date = monday + timedelta(days=i)
                target_str = target_date.strftime("%Y-%m-%d")

                # 아직 오지 않은 날짜면 비용 0으로 표시 (빈 툴팁)
                day_logs = [item for item in logs if item.get("timestamp", "").startswith(target_str)]
                cost = sum(item.get("cost_usd", 0.0) for item in day_logs)

                details = f"[{target_str} ({days[i]})]\n"
                if target_date > now:
                    details = f"{days[i]}요일 (예정)"
                elif not day_logs:
                    details += "결제 내역 없음"
                else:
                    models = {}
                    for item in day_logs:
                        m = item.get("model_name", "Unknown")
                        models[m] = models.get(m, 0.0) + item.get("cost_usd", 0.0)
                    for m, c in models.items():
                        details += f"- {m}: ${c:.4f}\n"
                    details += f"총액: ${cost:.4f}"

                chart_data.append({"label": days[i], "cost": cost, "details": details})

        elif "월별" in mode:
            # 4주차 (간단히 1주차~4주차, 남은 일수는 4주차에 편입)
            weeks = ["1주차", "2주차", "3주차", "4주차"]
            week_costs = [0.0] * 4
            week_details = [{} for _ in range(4)] # list of model dicts

            for item in logs:
                ts = item.get("timestamp", "")
                if ts.startswith(this_month_str):
                    try:
                        day = int(ts[8:10])
                        w_idx = (day - 1) // 7
                        if w_idx > 3: w_idx = 3 # 29일 이상은 4주차로 병합

                        cost = item.get("cost_usd", 0.0)
                        week_costs[w_idx] += cost
                        m = item.get("model_name", "Unknown")
                        week_details[w_idx][m] = week_details[w_idx].get(m, 0.0) + cost
                    except:
                        pass

            for i in range(4):
                cost = week_costs[i]
                details = f"[{this_month_str} {weeks[i]}]\n"
                if cost == 0:
                    details += "결제 내역 없음"
                else:
                    for m, c in week_details[i].items():
                        details += f"- {m}: ${c:.4f}\n"
                    details += f"총액: ${cost:.4f}"
                chart_data.append({"label": weeks[i], "cost": cost, "details": details})

        elif "연별" in mode:
            # 1월 ~ 12월
            for m_idx in range(1, 13):
                target_month = f"{this_year_str}-{m_idx:02d}"
                month_logs = [item for item in logs if item.get("timestamp", "").startswith(target_month)]
                cost = sum(item.get("cost_usd", 0.0) for item in month_logs)

                details = f"[{this_year_str}년 {m_idx}월]\n"
                if not month_logs:
                    details += "결제 내역 없음"
                else:
                    models = {}
                    for item in month_logs:
                        m = item.get("model_name", "Unknown")
                        models[m] = models.get(m, 0.0) + item.get("cost_usd", 0.0)
                    for m, c in models.items():
                        details += f"- {m}: ${c:.4f}\n"
                    details += f"총액: ${cost:.4f}"

                chart_data.append({"label": f"{m_idx}월", "cost": cost, "details": details})

        self.chart_widget.set_data(chart_data)

    def save_api_keys(self):
        from security_manager import SecurityManager
        for provider, le_key in self.api_inputs.items():
            key = le_key.text().strip()
            SecurityManager.save_api_key(provider, key)
        QMessageBox.information(self, "저장 완료", "API 키가 안전하게 로컬에 암호화되어 저장되었습니다.")

    def set_model_refresh_status(self, message):
        self.lbl_model_refresh_status.setText(message)
