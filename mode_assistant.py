import sys, os, json, subprocess, time
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QMessageBox, QSystemTrayIcon, 
    QMenu, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QCheckBox, QFrame, QStackedWidget, QStatusBar, QLabel, QSplitter
)
from ui_components import (
    EditorPanel, SettingsPanel, AIPanelWidget, ModelSelector,
    SmartChapterSelector, get_saved_font, ProjectSelectionDialog
)
from search_components import LocalSearchBar, GlobalSearchDialog
from PyQt6.QtGui import QIcon, QAction, QFont, QShortcut, QKeySequence, QTextCursor
from PyQt6.QtCore import Qt, QSettings, QTimer, QThread, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from project_manager import ProjectManager

from assistant_runtime import AIGenerationWorker, AutoCloseMessageBox, FileSaveWorker, SingleApplication
from assistant_workflow import AssistantWorkflowMixin


class AssistantModeWidget(AssistantWorkflowMixin, QWidget):
    switchModeRequested = pyqtSignal()
    sendToWritingModeRequested = pyqtSignal(str)
    typewriterModeToggled = pyqtSignal(str, bool)
    _TYPEWRITER_CONFIG_KEYS = {
        "요약": "tw_summary",
        "초안": "tw_draft",
        "평가": "tw_eval",
        "완성본": "tw_completed",
        "집필모드": "tw_writing",
    }
    def __init__(self):
        super().__init__()
        self.pm = ProjectManager()

        forced_project = os.environ.get("ANTIGRAVITY_AUTO_PROJECT", "").strip()
        if forced_project and forced_project in self.pm.get_all_projects():
            self.pm.set_current_project(forced_project)
        else:
            self._select_project()

        self.current_chapter = self.pm.get_project_setting("current_chapter", 1)
        self.is_working = False
        
        self.minimize_to_tray_enabled = self.pm.global_config.get("minimize_to_tray", False)
        
        self.save_workers = []
        
        self.init_ui()
        
        # 각 패널의 화수를 개별적으로 초기화
        for i in range(4):
            ch = self.pm.get_project_setting(f"chapter_tab_{i}", 1)
            if hasattr(self.left_panels[i], 'current_chapter'):
                self.left_panels[i].current_chapter = ch
            if hasattr(self.right_panels[i], 'current_chapter'):
                self.right_panels[i].current_chapter = ch
                
                
        self.apply_global_font(get_saved_font())
        
        # 이전 활성 탭 복구 (UI가 먼저 그려지도록 약간 지연)
        saved_tab_index = self.pm.get_project_setting("current_tab_index", 0)
        QTimer.singleShot(50, lambda: self.switch_tab(saved_tab_index, force=True))
        self.update_status_bar()
        self.init_tray_icon()

    def _select_project(self):

        # 프로젝트 선택 로직
        # 다이얼로그가 독립적인 작업표시줄 아이콘을 갖도록 parent를 지정하지 않음
        dlg = ProjectSelectionDialog(self.pm)

        last_proj = self.pm.global_config.get("last_project")
        if last_proj:
            # 리스트 위젯에서 마지막 프로젝트를 미리 선택해줌
            items = dlg.list_widget.findItems(last_proj, Qt.MatchFlag.MatchExactly)
            if items:
                dlg.list_widget.setCurrentItem(items[0])

        if dlg.exec():
            if dlg.selected_project:
                self.pm.set_current_project(dlg.selected_project)
            else:
                sys.exit(0)
        else:
            sys.exit(0)
        
    def init_ui(self):
        main_layout = QVBoxLayout(self)
        
        # --- 상단: 타이틀, 화수 선택, 모델 선택 ---
        header_layout = QVBoxLayout()
        header_layout.setContentsMargins(10, 5, 10, 0)
        header_layout.setSpacing(5)
        
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        self.toggle_btn = QPushButton("📝 집필 모드로 전환")
        self.toggle_btn.setMinimumHeight(40)
        self.toggle_btn.clicked.connect(self.switchModeRequested.emit)
        self.chapter_selector = SmartChapterSelector()
        self.chapter_selector.chapterChanged.connect(self.on_chapter_changed)
        # 로드된 화수를 콤보박스에 표시
        self.chapter_selector.set_value(self.current_chapter)
        
        self.btn_save = QPushButton("💾 저장 (Ctrl+S)")
        self.btn_save.setObjectName("DarkButton")
        self.btn_save.setMinimumHeight(40)
        self.btn_save.clicked.connect(self.manual_save)
        
        shortcut_save = QShortcut(QKeySequence("Ctrl+S"), self)
        shortcut_save.activated.connect(self.manual_save)
        
        shortcut_local_search = QShortcut(QKeySequence("Ctrl+F"), self)
        shortcut_local_search.activated.connect(self.show_local_search)
        
        shortcut_global_search = QShortcut(QKeySequence("Ctrl+Shift+F"), self)
        shortcut_global_search.activated.connect(self.show_global_search)
        
        top_row.addWidget(self.toggle_btn)
        top_row.addStretch()
        top_row.addWidget(self.btn_save)
        top_row.addSpacing(10)
        top_row.addWidget(self.chapter_selector)
        
        bottom_row = QHBoxLayout()
        self.model_selector = ModelSelector(self.pm)
        bottom_row.addStretch()
        bottom_row.addWidget(self.model_selector)
        
        header_layout.addLayout(top_row)
        header_layout.addLayout(bottom_row)
        
        main_layout.addLayout(header_layout)
        main_layout.addSpacing(5)
        
        # --- 중앙: 사이드바(QListWidget) + 에디터(QStackedWidget) ---
        center_layout = QHBoxLayout()
        center_layout.setContentsMargins(10, 0, 10, 10)
        
        self.sidebar = QFrame()
        self.sidebar.setObjectName("SidebarFrame")
        self.sidebar.setFixedWidth(180)
        
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(10, 10, 10, 10)
        sidebar_layout.setSpacing(8)
        
        self.btn_summary = QPushButton("요약")
        self.btn_draft = QPushButton("초안")
        self.btn_eval = QPushButton("평가")
        self.btn_completed = QPushButton("완성본")
        self.btn_settings = QPushButton("설정")
        
        self.sidebar_btns = [self.btn_draft, self.btn_completed, self.btn_eval, self.btn_summary, self.btn_settings]
        
        tab_font = QFont("Malgun Gothic", 20, QFont.Weight.Bold)
        for i, btn in enumerate(self.sidebar_btns):
            btn.setObjectName("SidebarButton")
            btn.setCheckable(True)
            btn.setFont(tab_font)
            btn.setMinimumHeight(75)
            btn.clicked.connect(lambda checked, idx=i: self.switch_tab(idx))
            
        sidebar_layout.addWidget(self.btn_draft)
        sidebar_layout.addWidget(self.btn_completed)
        sidebar_layout.addWidget(self.btn_eval)
        sidebar_layout.addSpacing(83) # 탭 하나 분량 띄우기
        sidebar_layout.addWidget(self.btn_summary)
        sidebar_layout.addStretch()
        
        self.btn_split = QPushButton("좌우 분할 모드")
        self.btn_split.setCheckable(True)
        self.btn_split.setObjectName("DarkButton")
        self.btn_split.setMinimumHeight(50)
        self.btn_split.clicked.connect(self.toggle_split_mode)
        sidebar_layout.addWidget(self.btn_split)
        
        sidebar_layout.addWidget(self.btn_settings)
        
        self.editor_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.editor_splitter.setChildrenCollapsible(False)
        
        self.left_stack = QStackedWidget()
        self.right_stack = QStackedWidget()
        
        self.editor_splitter.addWidget(self.left_stack)
        self.editor_splitter.addWidget(self.right_stack)
        
        self.right_stack.hide()
        self.is_split_mode = False
        self.last_focused_side = "left"
        
        def create_panels():
            return [
                EditorPanel("초안", self.pm, "초안 내용을 입력하거나 AI 결과를 확인하세요..."),
                EditorPanel("완성본", self.pm, "최종 완성본 내용을 입력하세요..."),
                EditorPanel("평가", self.pm, "평가 내용을 입력하거나 AI 결과를 확인하세요..."),
                EditorPanel("요약", self.pm, "요약 내용을 입력하거나 AI 결과를 확인하세요..."),
                SettingsPanel(self.pm)
            ]
            
        self.left_panels = create_panels()
        self.right_panels = create_panels()
        
        # 텍스트 동기화
        for i in range(4):
            self.right_panels[i].text_edit.setDocument(self.left_panels[i].text_edit.document())
            
        for i, p_list in enumerate([self.left_panels, self.right_panels]):
            stack = self.left_stack if i == 0 else self.right_stack
            for j, p in enumerate(p_list):
                stack.addWidget(p)
                if j < 4: # EditorPanel
                    p.saveRequested.connect(self.save_content)
                    p.aiGenerationRequested.connect(self.handle_ai_generation)
                    p.aiOpenRequested.connect(self.handle_ai_open)
                    p.text_edit.textChanged.connect(self.update_status_bar)
                    p.openFolderRequested.connect(self.open_backup_folder)
                    p.finalConfirmRequested.connect(self.handle_final_confirm)
                    p.sendToWritingModeRequested.connect(self.sendToWritingModeRequested.emit)
            
            settings = p_list[4]
            settings.fontChanged.connect(self.apply_global_font)
            settings.traySettingChanged.connect(self.set_minimize_to_tray)
            settings.typewriterToggled.connect(self.set_typewriter_mode)
            settings.extractRequested.connect(self.handle_extraction)
            settings.btn_global_search.clicked.connect(self.show_global_search)
            settings.modelRefreshRequested.connect(self.model_selector.refresh_account_models)
            self.model_selector.refreshStateChanged.connect(settings.set_model_refresh_status)
            
        self._restore_typewriter_modes()
        QApplication.instance().focusChanged.connect(self.on_focus_changed)
        # --- 상태 표시줄 (Status Bar) ---
        self.status_bar = QStatusBar()
        main_layout.addWidget(self.status_bar)
        
        self.switch_tab(0, force=True)
        center_layout.addWidget(self.sidebar)
        center_layout.addWidget(self.editor_splitter, stretch=1)
        
        # 메인 레이아웃 조립 (스플리터 적용)
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        
        left_container = QWidget()
        left_container.setLayout(center_layout)
        
        self.ai_panel = AIPanelWidget(self)
        self.ai_panel.hide()
        
        self.ai_panel.closeRequested.connect(self.hide_ai_panel)
        self.ai_panel.applyRequested.connect(self.apply_ai_result)
        self.ai_panel.feedbackRequested.connect(self.handle_ai_feedback)
        self.ai_panel.stopRequested.connect(self.stop_ai_generation)
        
        self.main_splitter.addWidget(left_container)
        self.main_splitter.addWidget(self.ai_panel)
        self.main_splitter.setSizes([700, 300]) # 기본 비율
        
        main_layout.addWidget(self.main_splitter, stretch=1)
        
    def switch_tab(self, index, force=False):
        stack = self.left_stack if self.last_focused_side == "left" else self.right_stack
        
        try:
            current_index = stack.currentIndex()
        except Exception:
            current_index = -1
            
        if current_index == index and not force:
            if hasattr(self, 'sidebar_btns'):
                self.sidebar_btns[index].setChecked(True)
            return
            
        if hasattr(self, 'sidebar_btns') and current_index != index and current_index != -1:
            old_panel = self.get_active_panel()
            
            if old_panel:
                text = old_panel.text_edit.toPlainText()
                if text.strip():
                    timestamp = time.strftime("%Y%m%d_%H%M%S")
                    chapter_str = f"{old_panel.current_chapter:03d}화"
                    filename = f"{old_panel.step_name}_{chapter_str}_{timestamp}.txt"
                    filepath = os.path.join("백업", "pre_transition", filename)
                    
                    worker = FileSaveWorker(filepath, text)
                    self.save_workers.append(worker)
                    worker.finished.connect(lambda w=worker: self.save_workers.remove(w) if w in self.save_workers else None)
                    worker.start()
                    
                self.sync_internal_storage(old_panel)
                old_panel.text_edit.document().setModified(False)

        # 설정 탭(4번) 진입 시 분할 모드 임시 해제 로직
        if index == 4:
            self.btn_split.setEnabled(False)  # 설정 탭에서는 분할 모드 버튼 비활성화
            if getattr(self, 'is_split_mode', False):
                self.was_split_mode = True
                self.was_left_idx = self.left_stack.currentIndex()
                self.was_right_idx = self.right_stack.currentIndex()
                self.was_focused_side = self.last_focused_side
                
                self.is_split_mode = False
                self.right_stack.hide()
                self.btn_split.blockSignals(True)
                self.btn_split.setChecked(False)
                self.btn_split.blockSignals(False)
                for p in self.left_panels[:4] + self.right_panels[:4]:
                    p.set_split_mode(False)
                    
            self.last_focused_side = "left"
            stack = self.left_stack
        else:
            self.btn_split.setEnabled(True)  # 다른 탭에서는 다시 활성화
            # 설정 탭에서 다른 곳으로 돌아올 때 분할 모드 복구 로직
            if getattr(self, 'was_split_mode', False):
                self.was_split_mode = False
                self.is_split_mode = True
                
                self.left_stack.setCurrentIndex(self.was_left_idx)
                self.right_stack.setCurrentIndex(self.was_right_idx)
                self.last_focused_side = self.was_focused_side
                
                self.right_stack.show()
                self.btn_split.blockSignals(True)
                self.btn_split.setChecked(True)
                self.btn_split.blockSignals(False)
                for p in self.left_panels[:4] + self.right_panels[:4]:
                    p.set_split_mode(True)
                    
                stack = self.left_stack if self.last_focused_side == "left" else self.right_stack

        stack.setCurrentIndex(index)
        self.update_sidebar_buttons()
            
        new_panel = self.get_active_panel()
        if new_panel:
            self.current_chapter = new_panel.current_chapter
            if hasattr(self, 'chapter_selector'):
                self.chapter_selector.blockSignals(True)
                self.chapter_selector.set_value(self.current_chapter)
                self.chapter_selector.blockSignals(False)
                
            self.load_content_for_panel(new_panel)
            self.update_status_bar()
            self.update_panel_styles()
            
            # 탭 변경 시 즉각 저장
            if hasattr(self, 'pm'):
                self.pm.set_project_setting("current_tab_index", index)

    def on_focus_changed(self, old, new):
        if hasattr(self, 'left_panels') and hasattr(self, 'right_panels'):
            for p in self.left_panels[:4]:
                if new == p.text_edit:
                    self.last_focused_side = "left"
                    self.current_chapter = p.current_chapter
                    if hasattr(self, 'chapter_selector'):
                        self.chapter_selector.blockSignals(True)
                        self.chapter_selector.set_value(self.current_chapter)
                        self.chapter_selector.blockSignals(False)
                    self.update_sidebar_buttons()
                    self.update_panel_styles()
                    return
            for p in self.right_panels[:4]:
                if new == p.text_edit:
                    self.last_focused_side = "right"
                    self.current_chapter = p.current_chapter
                    if hasattr(self, 'chapter_selector'):
                        self.chapter_selector.blockSignals(True)
                        self.chapter_selector.set_value(self.current_chapter)
                        self.chapter_selector.blockSignals(False)
                    self.update_sidebar_buttons()
                    self.update_panel_styles()
                    return

    def update_sidebar_buttons(self):
        if not hasattr(self, 'sidebar_btns'): return
        stack = self.left_stack if self.last_focused_side == "left" else self.right_stack
        idx = stack.currentIndex()
        for i, btn in enumerate(self.sidebar_btns):
            btn.setChecked(i == idx)

    def update_panel_styles(self):
        active_style = "QTextEdit { border: 2px solid #2a64f6; }"
        inactive_style = ""
        for p in self.left_panels[:4]:
            p.text_edit.setStyleSheet(active_style if self.last_focused_side == "left" and self.is_split_mode else inactive_style)
        for p in self.right_panels[:4]:
            p.text_edit.setStyleSheet(active_style if self.last_focused_side == "right" and self.is_split_mode else inactive_style)

    def toggle_split_mode(self, checked):
        if checked and hasattr(self, 'ai_panel') and self.ai_panel.isVisible():
            QMessageBox.warning(self, "안내", "AI 패널이 닫혀있어야 분할 모드를 사용할 수 있습니다.")
            self.btn_split.blockSignals(True)
            self.btn_split.setChecked(False)
            self.btn_split.blockSignals(False)
            return False

        self.is_split_mode = checked
        if checked:
            left_idx = self.left_stack.currentIndex()
            if left_idx in [0, 1]:  # 초안(0) or 완성본(1)
                self.switch_tab(1)
                right_idx = 0
            elif left_idx == 2:  # 평가(2)
                self.switch_tab(1)
                right_idx = 2
            else:
                right_idx = 0 if left_idx != 0 else 3
                
            self.right_stack.setCurrentIndex(right_idx)
            
            right_panel = self.right_panels[right_idx] if right_idx < 4 else None
            if right_panel:
                right_panel.current_chapter = self.current_chapter
                self.load_content_for_panel(right_panel)
                
            self.right_stack.show()
            self.last_focused_side = "right"
        else:
            self.right_stack.hide()
            self.last_focused_side = "left"
            
        for p in self.left_panels[:4] + self.right_panels[:4]:
            p.set_split_mode(checked)
            
        self.update_sidebar_buttons()
        self.update_panel_styles()
        
        return True

    def get_active_panel(self):
        if not hasattr(self, 'left_panels'): return None
        if self.is_split_mode and self.last_focused_side == "right":
            idx = self.right_stack.currentIndex()
            panels = self.right_panels
        else:
            idx = self.left_stack.currentIndex()
            panels = self.left_panels
            
        if idx < 4: return panels[idx]
        return None

    def sync_internal_storage(self, panel):
        if not panel: return
        text = panel.text_edit.toPlainText()
        self.pm.save_chapter_text(panel.step_name, panel.current_chapter, text)

    def load_content_for_panel(self, panel):
        if not panel: return
        loaded_text = self.pm.load_chapter_text(panel.step_name, panel.current_chapter)
                
        panel.text_edit.blockSignals(True)
        panel.text_edit.setPlainText(loaded_text)
        panel.text_edit.blockSignals(False)
        panel.text_edit.document().setModified(False)
        panel.update_count()
    def on_chapter_changed(self, chapter):
        if hasattr(self, 'ai_panel') and self.ai_panel.isVisible():
            QMessageBox.warning(self, "안내", "AI 패널이 열려있을 때는 화수를 이동할 수 없습니다.")
            if hasattr(self, 'chapter_selector'):
                self.chapter_selector.blockSignals(True)
                self.chapter_selector.set_value(self.current_chapter)
                self.chapter_selector.blockSignals(False)
            return

        panel = self.get_active_panel()
        if panel:
            self.sync_internal_storage(panel)
            panel.current_chapter = chapter
            
            # 현재 탭 인덱스를 확인하여 좌/우 패널 모두 화수 업데이트 및 개별 저장
            idx = -1
            if self.is_split_mode and self.last_focused_side == "right":
                idx = self.right_stack.currentIndex()
            else:
                idx = self.left_stack.currentIndex()
                
            if idx != -1 and idx < 4:
                self.left_panels[idx].current_chapter = chapter
                self.right_panels[idx].current_chapter = chapter
                self.pm.set_project_setting(f"chapter_tab_{idx}", chapter)
                
            self.current_chapter = chapter
            self.pm.set_project_setting("current_chapter", chapter)
        
        # 이전처럼 글로벌 변수에도 저장해둠
        if hasattr(self, 'settings'):
            self.settings.setValue("current_chapter", self.current_chapter)
        
        if panel:
            self.load_content_for_panel(panel)
            panel.text_edit.setFocus()
            cursor = panel.text_edit.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            panel.text_edit.setTextCursor(cursor)
            
        self.update_status_bar()
        
    def calculate_max_continuous_chapter(self, step_name):
        """현재 탭(step_name)의 1화부터 끊기지 않고 20자 초과 작성된 최대 화수 계산"""
        current_panel = self.get_active_panel()
        max_ch = 0
        
        for ch in range(1, 10000):
            # 현재 화면에서 직접 타이핑 중인 화수인지 확인 (저장 전 실시간 반영용)
            if current_panel and current_panel.step_name == step_name and self.current_chapter == ch:
                text = current_panel.text_edit.toPlainText().strip()
            else:
                text = self.pm.load_chapter_text(step_name, ch).strip()
                
            if len(text) > 20:
                max_ch = ch
            else:
                break
                
        return max_ch

    def update_status_bar(self):
        status_text = "대기 중" if not self.is_working else "작업 중..."
        
        panel = self.get_active_panel()
        max_ch_text = ""
        if panel:
            max_ch = self.calculate_max_continuous_chapter(panel.step_name)
            if max_ch > 0:
                max_ch_text = f" (현재 {panel.step_name} 최대 {max_ch}화까지 작성 됨)"
                
        session_cost = self.pm.session_cost if hasattr(self.pm, 'session_cost') else 0.0
        self.status_bar.showMessage(
            f"현재 화수: {self.current_chapter:03d}화{max_ch_text} | "
            f"진행 상태: {status_text} | "
            f"이번 세션 누적 비용: ${session_cost:.4f}"
        )
        
    def init_tray_icon(self):
        self.tray_icon = QSystemTrayIcon(self)
        
        import os, sys
        from PyQt6.QtGui import QIcon
        if getattr(sys, 'frozen', False):
            base_path = sys._MEIPASS
        else:
            base_path = os.path.dirname(os.path.abspath(__file__))

        icon_path = os.path.join(base_path, "app_icon.ico")
        if os.path.exists(icon_path):
            icon = QIcon(icon_path)
        else:
            icon = self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon)

        self.tray_icon.setIcon(icon)
        
        # 트레이 우클릭 메뉴
        tray_menu = QMenu()
        show_action = QAction("열기", self)
        show_action.triggered.connect(self.showNormal)
        
        quit_action = QAction("종료", self)
        quit_action.triggered.connect(self.force_quit)
        
        tray_menu.addAction(show_action)
        tray_menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.tray_icon_activated)
        
        # 시스템 트레이 아이콘은 항상 표시
        self.tray_icon.show()
        
    def tray_icon_activated(self, reason):
        """트레이 아이콘 클릭 시 행동 정의"""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick or reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.showNormal()
            self.activateWindow()

    def showEvent(self, event):
        super().showEvent(event)
        try:
            import ctypes
            imm32 = ctypes.windll.imm32
            user32 = ctypes.windll.user32
            
            hwnd = int(self.winId())
            himc = imm32.ImmGetContext(hwnd)
            if himc:
                conversion = ctypes.c_uint32()
                sentence = ctypes.c_uint32()
                imm32.ImmGetConversionStatus(himc, ctypes.byref(conversion), ctypes.byref(sentence))
                
                # IME_CMODE_HANGUL is 1
                if not (conversion.value & 1):
                    imm32.ImmSetConversionStatus(himc, conversion.value | 1, sentence.value)
                    
                imm32.ImmReleaseContext(hwnd, himc)
        except Exception as e:
            print("한글 모드 자동 전환 실패:", e)

    def restore_window_state(self):
        settings = QSettings("HitomiKkeora", "WebNovelAssistant")
        geom = settings.value("geometry")
        if geom:
            self.restoreGeometry(geom)
        else:
            self.resize(1100, 800)

    def save_window_state(self):
        settings = QSettings("HitomiKkeora", "WebNovelAssistant")
        settings.setValue("geometry", self.saveGeometry())
        self.pm.set_project_setting("current_chapter", self.current_chapter)
            
    def set_minimize_to_tray(self, enabled):
        self.minimize_to_tray_enabled = enabled
        
    def apply_global_font(self, font):
        for p in self.left_panels[:4] + self.right_panels[:4]:
            p.text_edit.setFont(font)
            
    def show_local_search(self):
        panel = self.get_active_panel()
        if panel and hasattr(panel, 'search_bar'):
            panel.search_bar.show_and_focus()
            
    def show_global_search(self):
        dlg = GlobalSearchDialog(self.pm, self)
        dlg.resultSelected.connect(self.goto_search_result)
        dlg.exec()
        
    def goto_search_result(self, step, chapter, index, keyword):
        # 1. 챕터 변경
        if self.chapter_selector.get_value() != chapter:
            self.chapter_selector.set_value(chapter)
            self.on_chapter_changed(chapter)
            
        # 2. 탭 전환
        step_idx = {"초안": 0, "완성본": 1, "평가": 2, "요약": 3}.get(step, 0)
        self.switch_tab(step_idx)
        
        # 3. 하이라이트 (포커스 주고 위치 이동)
        panel = self.get_active_panel()
        if panel:
            cursor = panel.text_edit.textCursor()
            cursor.setPosition(index)
            cursor.setPosition(index + len(keyword), QTextCursor.MoveMode.KeepAnchor)
            panel.text_edit.setTextCursor(cursor)
            panel.text_edit.setFocus()
            
    def set_typewriter_mode(self, step_name, enabled):
        self._apply_typewriter_mode(
            step_name,
            enabled,
            persist=True,
            notify=True,
        )

    def _restore_typewriter_modes(self):
        for step_name in ("요약", "초안", "평가", "완성본"):
            config_key = self._TYPEWRITER_CONFIG_KEYS[step_name]
            self._apply_typewriter_mode(
                step_name,
                bool(self.pm.global_config.get(config_key, False)),
                persist=False,
                notify=False,
            )

    def _apply_typewriter_mode(
        self, step_name, enabled, *, persist, notify
    ):
        config_key = self._TYPEWRITER_CONFIG_KEYS.get(step_name)
        if config_key is None:
            return
        enabled = bool(enabled)
        # The paired editors share a QTextDocument. Apply the hidden right view
        # first so the initially visible left viewport owns the final margin.
        for p in self.right_panels[:4] + self.left_panels[:4]:
            if p.step_name == step_name:
                p.set_typewriter_mode(enabled)

        for settings in (self.left_panels[4], self.right_panels[4]):
            settings.set_typewriter_checked(step_name, enabled)

        if persist:
            self.pm.global_config[config_key] = enabled
            self.pm.save_global_config()
        if notify:
            self.typewriterModeToggled.emit(step_name, enabled)
        
    def open_backup_folder(self, step_name):
        """해당 단계의 메인 폴더를 엽니다."""
        if not getattr(self, 'pm', None) or not self.pm.project_path:
            QMessageBox.warning(self, "오류", "프로젝트가 선택되지 않았습니다.")
            return
            
        target_dir = os.path.join(self.pm.project_path, "메인", step_name)
        
        os.makedirs(target_dir, exist_ok=True)
        if sys.platform == 'win32':
            os.startfile(target_dir)
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', target_dir])
        else:
            subprocess.Popen(['xdg-open', target_dir])
            
    def save_content(self, step_name, text):
        """버튼을 통한 수동 저장 처리 (메인 폴더)"""
        chapter = self.current_chapter
        
        try:
            self.pm.save_chapter_text(step_name, chapter, text, is_backup=False)
            
            # 저장 경로 가져오기 (표시용)
            file_path = self.pm.get_text_file_path(step_name, chapter, is_backup=False)
            
            msg_box = AutoCloseMessageBox(
                "저장 완료", 
                f"[{chapter:03d}화 - {step_name} 단계]\n\n텍스트가 메인 폴더에 안전하게 저장되었습니다.\n저장 경로: {file_path}", 
                timeout=3, 
                parent=self
            )
            msg_box.exec()
            
            panel = self.get_active_panel()
            if panel and panel.step_name == step_name:
                panel.text_edit.document().setModified(False)
        except Exception as e:
            QMessageBox.critical(self, "저장 오류", f"파일 저장 중 오류가 발생했습니다:\n{e}")
        
    def manual_save(self):
        panel = self.get_active_panel()
        if panel:
            text = panel.text_edit.toPlainText()
            self.pm.save_chapter_text(panel.step_name, panel.current_chapter, text, is_backup=False)
            panel.text_edit.document().setModified(False)
            self.status_bar.showMessage(f"[{panel.step_name}] 메인 폴더에 수동 저장되었습니다.", 2000)

    def check_unsaved_changes(self, is_final_quit=False):
        """저장되지 않은 변경사항이 있는지 확인하고 처리. 진행(True) / 취소(False) 반환"""
        has_unsaved = False
        panels = self.left_panels[:4] if hasattr(self, 'left_panels') else []
        for i, p in enumerate(panels):
            if p.text_edit.document().isModified():
                saved_text = self.pm.load_chapter_text(p.step_name, p.current_chapter) or ""
                current_text = p.text_edit.toPlainText()
                if current_text.replace('\\r\\n', '\\n').replace('\\r', '').strip() != saved_text.replace('\\r\\n', '\\n').replace('\\r', '').strip():
                    print(f"[DEBUG] check_unsaved_changes: Assistant panel {i} '{p.step_name}' is really modified")
                    has_unsaved = True
                    break
                else:
                    # 내용이 동일하다면 오탐지된 것이므로 플래그 초기화
                    p.text_edit.document().setModified(False)
                
        # 집필 모드도 함께 검사
        writing_mode_needs_save = False
        if hasattr(self, 'writing_mode'):
            wm = self.writing_mode
            if getattr(wm, 'left_editor', None) and wm.left_editor.document().isModified():
                if wm.current_loaded_file_left:
                    c_txt = wm.left_editor.toPlainText().replace('\\r\\n', '\\n').replace('\\r', '').strip()
                    s_txt = (wm.wpm.read_text_file(wm.current_loaded_file_left) or "").replace('\\r\\n', '\\n').replace('\\r', '').strip()
                    if c_txt != s_txt:
                        has_unsaved = True
                        writing_mode_needs_save = True
                    else:
                        wm.left_editor.document().setModified(False)
                elif wm.left_editor.toPlainText().strip():
                    has_unsaved = True
                    writing_mode_needs_save = True
            if getattr(wm, 'right_editor', None) and wm.right_editor.document().isModified():
                if wm.current_loaded_file_right:
                    c_txt = wm.right_editor.toPlainText().replace('\\r\\n', '\\n').replace('\\r', '').strip()
                    s_txt = (wm.wpm.read_text_file(wm.current_loaded_file_right) or "").replace('\\r\\n', '\\n').replace('\\r', '').strip()
                    if c_txt != s_txt:
                        has_unsaved = True
                        writing_mode_needs_save = True
                    else:
                        wm.right_editor.document().setModified(False)
                elif wm.right_editor.toPlainText().strip():
                    has_unsaved = True
                    writing_mode_needs_save = True
                
        if not has_unsaved:
            if is_final_quit:
                self._flush_writing_sync_before_close()
            return True
            
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("저장되지 않은 변경사항")
        msg_box.setText("저장 되지 않은 내용이 있습니다. 저장하시겠습니까?")
        msg_box.setIcon(QMessageBox.Icon.Warning)
        
        btn_save = msg_box.addButton("저장", QMessageBox.ButtonRole.AcceptRole)
        btn_no = msg_box.addButton("아니요", QMessageBox.ButtonRole.DestructiveRole)
        btn_cancel = msg_box.addButton("취소", QMessageBox.ButtonRole.RejectRole)
        
        msg_box.exec()
        
        if msg_box.clickedButton() == btn_save:
            for p in panels:
                if p.text_edit.document().isModified():
                    self.sync_internal_storage(p)
                    p.text_edit.document().setModified(False)
                    
            if writing_mode_needs_save and hasattr(self, 'writing_mode'):
                if hasattr(self.writing_mode, 'manual_save'):
                    worker = self.writing_mode.manual_save()
                    if worker:
                        from PyQt6.QtCore import QEventLoop
                        loop = QEventLoop()
                        worker.finished.connect(loop.quit)
                        loop.exec()
            if is_final_quit:
                self._flush_writing_sync_before_close()
                    
            self.status_bar.showMessage("모든 변경사항이 저장되었습니다.", 2000)
            return True
        elif msg_box.clickedButton() == btn_no:
            if is_final_quit:
                confirm_box = QMessageBox(self)
                confirm_box.setWindowTitle("경고")
                confirm_box.setText("정말 저장하지 않고 종료하시겠습니까?\n작성 중인 내용이 날아갈 수 있습니다.")
                confirm_box.setIcon(QMessageBox.Icon.Critical)
                yes_btn = confirm_box.addButton("예 (저장 안 함)", QMessageBox.ButtonRole.DestructiveRole)
                no_btn = confirm_box.addButton("아니오 (돌아가기)", QMessageBox.ButtonRole.RejectRole)
                confirm_box.exec()
                if confirm_box.clickedButton() == yes_btn:
                    return True
                else:
                    return False
            return True
        else:
            return False
            
        return True

    def _flush_writing_sync_before_close(self):
        writing_mode = getattr(self, "writing_mode", None)
        sync_manager = getattr(writing_mode, "sync_manager", None)
        if getattr(sync_manager, "cloud_network_enabled", None) is False:
            return True
        flush = getattr(sync_manager, "flush_pending_syncs", None)
        if not callable(flush):
            return True
        if flush():
            return True
        QMessageBox.information(
            self,
            "로컬 저장 완료",
            "원고는 로컬과 동기화 대기열에 안전하게 저장되었습니다.\n"
            "서버 전송은 완료되지 않아 다음 실행 때 자동으로 재시도합니다.",
        )
        return False

    def closeEvent(self, event):
        """메인 윈도우의 닫기(X) 버튼을 눌렀을 때 호출되는 이벤트"""
        # 종료 전 현재 화수 및 탭 인덱스 저장
        self.pm.set_project_setting("current_chapter", self.current_chapter)
        
        if hasattr(self, 'sidebar_btns'):
            for i, btn in enumerate(self.sidebar_btns):
                if btn.isChecked():
                    self.pm.set_project_setting("current_tab_index", i)
                    break
        
        if self.is_working:
            QMessageBox.warning(
                self, 
                "사용 중", 
                "사용 중입니다. (현재 작업이 완료된 후 종료할 수 있습니다)"
            )
            event.ignore() # 종료 차단
            return
            
        # 6-1. 시스템 트레이(작업 표시줄) 최소화 옵션 적용
        if self.minimize_to_tray_enabled:
            event.ignore() # 완전 종료는 차단
            self.hide()    # 창만 숨김
        else:
            # 6-2. 트레이 옵션이 꺼져있을 때 완벽한 프로세스 종료
            if not self.check_unsaved_changes(is_final_quit=True):
                event.ignore()
                return
            self.save_window_state()
            self.tray_icon.hide()
            self.tray_icon.setParent(None)
            from PyQt6.QtWidgets import QApplication
            QApplication.processEvents()
            event.accept()
            QApplication.quit()
            
    def force_quit(self):
        """프로세스를 완전히 종료하는 메서드"""
        # 트레이의 '종료' 버튼을 눌렀을 때도 작업 중이면 종료 차단
        if self.is_working:
            self.showNormal()
            self.activateWindow()
            QMessageBox.warning(
                self, 
                "사용 중", 
                "사용 중입니다. (현재 작업이 완료된 후 종료할 수 있습니다)"
            )
            return
            
        if not self.check_unsaved_changes(is_final_quit=True):
            return
            
        self.save_window_state()
            
        # 트레이 아이콘 숨김 및 앱 완전 종료
        self.tray_icon.hide()
        self.tray_icon.setParent(None)
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()
        QApplication.quit()
        # 주의: sys.exit(0)은 QApplication.quit() 이후 이벤트 루프가 끝난 뒤 메인문에서 실행됨.

if __name__ == "__main__":
    app_id = "WebNovelAssistant_Unique_ID_2026"
    app = SingleApplication(app_id, sys.argv)
    
    # PyInstaller 임시 폴더 대응
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    style_path = os.path.join(base_path, "style.qss")

    try:
        with open(style_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())
    except Exception as e:
        print(f"스타일시트 로드 실패: {e}")
        
    # 6-3. 단일 실행 보장 로직
    if app.is_running():
        print("프로그램이 이미 실행 중입니다. 기존 창을 최상단으로 띄웁니다.")
        app.wake_up_server()
        sys.exit(0) # 새 인스턴스는 즉시 종료
        
    window = MainWindow()
    app.set_activation_window(window)
    window.show()
    
    # 메인 이벤트 루프 실행 후, 종료 시 완벽하게 프로세스 해제
    sys.exit(app.exec())
