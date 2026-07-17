from PyQt6.QtWidgets import QFrame, QLineEdit, QPushButton, QHBoxLayout, QLabel, QDialog, QVBoxLayout, QListWidget, QListWidgetItem, QApplication, QMessageBox
from PyQt6.QtGui import QTextCursor, QTextDocument
from PyQt6.QtCore import Qt, pyqtSignal, QTimer

class SearchAlertPopup(QMessageBox):
    def __init__(self, text, timeout=3, parent=None):
        super().__init__(parent)
        self.setWindowTitle("알림")
        self.setText(text)
        self.time_left = timeout
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_timer)
        self.timer.start(1000)
        self.setStandardButtons(QMessageBox.StandardButton.Ok)
        self.update_text()
        self.setStyleSheet("QMessageBox { background-color: #2b2b2b; color: white; } QLabel { color: white; } QPushButton { background-color: #444; color: white; padding: 5px; }")
        
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



class LocalSearchBar(QFrame):
    def __init__(self, text_edit):
        super().__init__()
        self.text_edit = text_edit
        self.setStyleSheet("""
            QFrame { background-color: #2b2b2b; border-bottom: 1px solid #444; }
            QLineEdit { font-size: 16px; padding: 5px; background-color: #1e1e1e; color: white; border: 1px solid #555; }
            QPushButton { font-size: 14px; padding: 5px 15px; background-color: #333; color: white; border: none; }
            QPushButton:hover { background-color: #444; }
        """)
        
        layout = QHBoxLayout()
        layout.setContentsMargins(10, 5, 10, 5)
        
        self.input_search = QLineEdit()
        self.input_search.setPlaceholderText("부분 검색어 입력...")
        self.input_search.returnPressed.connect(self.find_next)
        
        self.btn_prev = QPushButton("이전")
        self.btn_prev.clicked.connect(self.find_prev)
        
        self.btn_next = QPushButton("다음")
        self.btn_next.clicked.connect(self.find_next)
        
        self.btn_close = QPushButton("❌")
        self.btn_close.setFixedWidth(40)
        self.btn_close.clicked.connect(self.hide)
        
        layout.addWidget(QLabel("🔍 부분 검색:"))
        layout.addWidget(self.input_search)
        layout.addWidget(self.btn_prev)
        layout.addWidget(self.btn_next)
        layout.addWidget(self.btn_close)
        self.setLayout(layout)
        self.hide()
        
    def find_next(self):
        query = self.input_search.text()
        if not query: return
        found = self.text_edit.find(query)
        if not found:
            cursor = self.text_edit.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            self.text_edit.setTextCursor(cursor)
            found2 = self.text_edit.find(query)
            if not found2:
                SearchAlertPopup("검색 결과가 없습니다.", parent=self).exec()
            
    def find_prev(self):
        query = self.input_search.text()
        if not query: return
        found = self.text_edit.find(query, QTextDocument.FindFlag.FindBackward)
        if not found:
            cursor = self.text_edit.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.text_edit.setTextCursor(cursor)
            found2 = self.text_edit.find(query, QTextDocument.FindFlag.FindBackward)
            if not found2:
                SearchAlertPopup("검색 결과가 없습니다.", parent=self).exec()
            
    def show_and_focus(self):
        self.show()
        self.input_search.setFocus()
        self.input_search.selectAll()

class GlobalSearchDialog(QDialog):
    resultSelected = pyqtSignal(str, int, int, str)
    
    def __init__(self, pm, parent=None):
        super().__init__(parent)
        self.pm = pm
        self.setWindowTitle("전체 텍스트 검색 (모든 탭 / 화수)")
        self.resize(800, 600)
        
        self.setStyleSheet("""
            QDialog { background-color: #1e1e1e; }
            QLabel { color: white; font-size: 16px; font-weight: bold; }
            QLineEdit { font-size: 18px; padding: 10px; background-color: #2b2b2b; color: white; border: 1px solid #555; }
            QPushButton { font-size: 16px; padding: 10px 20px; background-color: #2962ff; color: white; border: none; }
            QPushButton:hover { background-color: #0039cb; }
            QListWidget { background-color: #2b2b2b; color: white; font-size: 16px; padding: 5px; }
            QListWidget::item { padding: 10px; border-bottom: 1px solid #444; }
            QListWidget::item:selected { background-color: #00e5ff; color: black; }
        """)
        
        layout = QVBoxLayout()
        
        top_layout = QHBoxLayout()
        self.input_search = QLineEdit()
        self.input_search.setPlaceholderText("검색어를 입력하세요...")
        self.input_search.returnPressed.connect(self.perform_search)
        
        self.btn_search = QPushButton("검색")
        self.btn_search.clicked.connect(self.perform_search)
        
        top_layout.addWidget(self.input_search)
        top_layout.addWidget(self.btn_search)
        
        self.list_results = QListWidget()
        self.list_results.itemDoubleClicked.connect(self.on_item_double_clicked)
        
        self.lbl_status = QLabel("검색어를 입력 후 엔터나 검색 버튼을 누르세요.")
        self.lbl_status.setStyleSheet("color: #888; font-size: 14px; font-weight: normal;")
        
        layout.addLayout(top_layout)
        layout.addWidget(self.lbl_status)
        layout.addWidget(self.list_results)
        self.setLayout(layout)
        
    def perform_search(self):
        if getattr(self, 'is_searching', False): return
        self.is_searching = True
        try:
            keyword = self.input_search.text().strip()
            if not keyword:
                return
            
            self.list_results.clear()
            self.lbl_status.setText("검색 중...")
            QApplication.processEvents()
            
            results = self.pm.search_all_chapters(keyword)
            
            if not results:
                self.lbl_status.setText(f"'{keyword}'에 대한 검색 결과가 없습니다.")
                SearchAlertPopup(f"'{keyword}'에 대한 검색 결과가 없습니다.", parent=self).exec()
                return
                
            self.lbl_status.setText(f"총 {len(results)}개의 결과를 찾았습니다.")
            for res in results:
                item_text = f"[{res['step']}] {res['chapter']}화 : {res['snippet']}"
                item = QListWidgetItem(item_text)
                item.setData(Qt.ItemDataRole.UserRole, res)
                self.list_results.addItem(item)
        finally:
            self.is_searching = False
            
    def on_item_double_clicked(self, item):
        res = item.data(Qt.ItemDataRole.UserRole)
        keyword = self.input_search.text().strip()
        self.resultSelected.emit(res['step'], res['chapter'], res['index'], keyword)
        self.accept()
