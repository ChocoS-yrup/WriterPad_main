from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTextEdit, QLabel, QSplitter, QTextBrowser,
    QStackedWidget, QComboBox, QMenu, QGraphicsDropShadowEffect, QScrollArea, QFrame, QMessageBox,
    QSizePolicy, QCheckBox, QDialog, QListWidget, QLineEdit, QRadioButton, QSpinBox, QButtonGroup,
    QFontDialog, QTabWidget, QListWidgetItem, QGridLayout, QTabBar
)
from PyQt6.QtGui import QFont, QTextCursor, QGuiApplication, QTextDocument
from PyQt6.QtCore import pyqtSignal, Qt, QSettings, QTimer, QThread


class DigitLabel(QLabel):
    """마우스 휠 및 클릭으로 조작이 가능한 숫자(0~9) 표시 라벨"""
    valueChanged = pyqtSignal(int)
    deltaScrolled = pyqtSignal(int)

    def __init__(self, value=0):
        super().__init__(str(value))
        self.setObjectName("DigitLabel")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(40, 50)
        # 마우스를 올렸을 때 클릭 가능하다는 것을 알려주기 위해 커서 변경
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.value = value

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta > 0:
            self.deltaScrolled.emit(1)
        elif delta < 0:
            self.deltaScrolled.emit(-1)

    def set_value(self, val):
        self.value = val
        self.setText(str(self.value))
        self.valueChanged.emit(self.value)

    def mousePressEvent(self, event):
        """좌클릭 시 0~9 목록이 나오는 메뉴 팝업"""
        if event.button() == Qt.MouseButton.LeftButton:
            menu = QMenu(self)
            for i in range(10):
                action = menu.addAction(str(i))
                action.triggered.connect(lambda checked, val=i: self.set_value(val))
            menu.exec(event.globalPosition().toPoint())


class SmartChapterSelector(QWidget):
    """천, 백, 십, 일의 자리를 휠로 조작하는 스마트 콤보박스"""
    chapterChanged = pyqtSignal(int) # 화수가 로드(Go)될 때 발생하는 시그널

    def __init__(self):
        super().__init__()
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        self.btn_prev = QPushButton("<")
        self.btn_prev.setObjectName("DarkButton")
        self.btn_prev.setFixedWidth(40)
        self.btn_prev.clicked.connect(self.decrement_and_go)

        # 4자리 숫자 (0001)
        self.digits = [DigitLabel(0), DigitLabel(0), DigitLabel(0), DigitLabel(1)]

        digit_layout = QHBoxLayout()
        digit_layout.setSpacing(5)
        for i, d in enumerate(self.digits):
            digit_layout.addWidget(d)
            d.deltaScrolled.connect(lambda delta, idx=i: self.handle_digit_scroll(idx, delta))
            d.valueChanged.connect(self.on_digit_value_changed)

        self.btn_next = QPushButton(">")
        self.btn_next.setObjectName("DarkButton")
        self.btn_next.setFixedWidth(40)
        self.btn_next.clicked.connect(self.increment_and_go)

        layout.addWidget(QLabel("현재 화수:"))
        layout.addWidget(self.btn_prev)
        layout.addLayout(digit_layout)
        layout.addWidget(self.btn_next)

        self.setLayout(layout)

    def handle_digit_scroll(self, digit_idx, delta):
        multiplier = 10 ** (3 - digit_idx)
        new_val = self.get_value() + (delta * multiplier)
        self.set_value(new_val)
        self.chapterChanged.emit(self.get_value())

    def on_digit_value_changed(self, val):
        self.chapterChanged.emit(self.get_value())

    def get_value(self):
        val = 0
        for i, d in enumerate(self.digits):
            val += d.value * (10 ** (3 - i))
        return val

    def set_value(self, val):
        val = max(1, min(9999, val)) # 1~9999 사이 유지
        v_str = f"{val:04d}"
        for i, d in enumerate(self.digits):
            d.value = int(v_str[i])
            d.setText(v_str[i])

    def increment_and_go(self):
        """오른쪽 꺾쇠 누르면 1 증가 후 즉시 로드"""
        self.set_value(self.get_value() + 1)
        self.chapterChanged.emit(self.get_value())

    def decrement_and_go(self):
        """왼쪽 꺾쇠 누르면 1 감소 후 즉시 로드"""
        self.set_value(self.get_value() - 1)
        self.chapterChanged.emit(self.get_value())

    def get_current_chapter_string(self):
        """현재 화면에 표시된 4자리 숫자를 문자열로 반환 (예: '0001')"""
        return "".join([str(d.value) for d in self.digits])
