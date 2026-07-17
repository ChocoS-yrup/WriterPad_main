import sys
import os
from PyQt6.QtWidgets import QApplication, QMainWindow
from PyQt6.QtGui import QIcon, QFont
from PyQt6.QtCore import Qt

from mode_writing import WritingModeWidget
from project_manager import ProjectManager
from ui_components import get_saved_font

class TestWritingMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("QMainWindow { background-color: #1b1d24; }")
        
        # 프로젝트 매니저 초기화
        self.pm = ProjectManager()
        
        # 기존 프로젝트가 있으면 첫 번째 프로젝트를 로드, 없으면 TestProject 생성 및 로드
        projects = self.pm.get_all_projects()
        if projects:
            self.pm.set_current_project(projects[0])
        else:
            self.pm.set_current_project("TestProject")
            
        project_name = self.pm.current_project
        self.setWindowTitle(f"웹소설 집필모드 (테스트) - [{project_name}]")
        self.setMinimumSize(1000, 700)
        
        # 전역 폰트 설정
        app_font = QFont("Malgun Gothic", 10, QFont.Weight.Bold)
        QApplication.setFont(app_font)
        
        # 집필 모드 위젯 초기화
        self.writing_mode = WritingModeWidget(self.pm)
        
        # 모드 스위칭 시그널 연결 (테스트용이므로 print로 대체)
        self.writing_mode.switchModeRequested.connect(self.dummy_switch_mode)
        self.writing_mode.sendToAssistantRequested.connect(self.dummy_send_to_assistant)
        
        self.setCentralWidget(self.writing_mode)
        
    def dummy_switch_mode(self):
        print("어시스턴트 모드로 전환 요청됨 (테스트 환경에서는 동작하지 않음)")

    def dummy_send_to_assistant(self, text):
        print(f"어시스턴트로 전송 요청됨:\n{text}\n(테스트 환경에서는 동작하지 않음)")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # 기본 경로 설정
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    
    # 아이콘 설정
    icon_path = os.path.join(base_path, "Antigravity_AI_Writer_App.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
        
    # 스타일시트 설정
    style_path = os.path.join(base_path, "style.qss")
    try:
        with open(style_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())
    except Exception as e:
        print(f"스타일시트 로드 실패: {e}")
    
    # 설정된 폰트 로드 및 적용
    font = get_saved_font()
    app.setFont(font)
    
    window = TestWritingMainWindow()
    window.show()
    
    sys.exit(app.exec())
