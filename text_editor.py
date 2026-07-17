from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTextEdit, QLabel, QSplitter, QTextBrowser,
    QStackedWidget, QComboBox, QMenu, QGraphicsDropShadowEffect, QScrollArea, QFrame, QMessageBox,
    QSizePolicy, QCheckBox, QDialog, QListWidget, QLineEdit, QRadioButton, QSpinBox, QButtonGroup,
    QFontDialog, QTabWidget, QListWidgetItem, QGridLayout, QTabBar
)
from PyQt6.QtGui import QFont, QTextCursor, QGuiApplication, QTextDocument
from PyQt6.QtCore import pyqtSignal, Qt, QSettings, QTimer, QThread


class SmartTextEdit(QTextEdit):
    """따옴표 자동완성을 지원하는 텍스트 에디터"""
    _last_windows_ime_open = None
    _force_korean_on_first_activation = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.typewriter_enabled = False
        self._custom_placeholder = ""
        self._is_composing = False
        self._input_activation_serial = 0
        self._startup_ime_guard_active = False
        self._startup_ime_key_buffer = []
        self._startup_ime_replay_scheduled = False
        self.cursorPositionChanged.connect(self.keep_cursor_centered)
        self.textChanged.connect(self.on_text_changed)

    def focusOutEvent(self, event):
        self._remember_windows_ime_state()
        super().focusOutEvent(event)

    def activate_input_method(self):
        """고정 지연 없이 포커스 완료 시점에 데스크톱 IME 문맥을 활성화한다."""
        self._input_activation_serial += 1
        serial = self._input_activation_serial
        self._startup_ime_guard_active = bool(
            SmartTextEdit._force_korean_on_first_activation
            or SmartTextEdit._last_windows_ime_open is True
        )
        self._startup_ime_key_buffer.clear()
        self._startup_ime_replay_scheduled = False
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self._refresh_input_method(serial)
        QTimer.singleShot(0, lambda: self._refresh_input_method(serial))

    def _refresh_input_method(self, serial):
        if serial != self._input_activation_serial or not self.hasFocus() or self.isReadOnly():
            return
        self._restore_windows_ime_state()
        input_method = QGuiApplication.inputMethod()
        input_method.update(
            Qt.InputMethodQuery.ImEnabled
            | Qt.InputMethodQuery.ImHints
            | Qt.InputMethodQuery.ImCursorRectangle
        )

    @staticmethod
    def _windows_ime_api():
        """Windows에서만 IMM API를 반환하고 다른 환경에서는 조용히 비활성화한다."""
        import sys

        if sys.platform != "win32":
            return None
        try:
            import ctypes
            from ctypes import wintypes

            imm32 = ctypes.WinDLL("imm32", use_last_error=True)
            imm32.ImmGetContext.argtypes = [wintypes.HWND]
            imm32.ImmGetContext.restype = wintypes.HANDLE
            imm32.ImmReleaseContext.argtypes = [wintypes.HWND, wintypes.HANDLE]
            imm32.ImmReleaseContext.restype = wintypes.BOOL
            imm32.ImmGetOpenStatus.argtypes = [wintypes.HANDLE]
            imm32.ImmGetOpenStatus.restype = wintypes.BOOL
            imm32.ImmSetOpenStatus.argtypes = [wintypes.HANDLE, wintypes.BOOL]
            imm32.ImmSetOpenStatus.restype = wintypes.BOOL
            imm32.ImmGetDefaultIMEWnd.argtypes = [wintypes.HWND]
            imm32.ImmGetDefaultIMEWnd.restype = wintypes.HWND
            return imm32
        except (AttributeError, OSError):
            return None

    def _windows_ime_hwnd(self):
        """IME 문맥이 연결된 실제 포커스 창 핸들을 안전하게 반환한다."""
        import sys

        if sys.platform != "win32":
            return None
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.GetFocus.argtypes = []
            user32.GetFocus.restype = wintypes.HWND
            focused_hwnd = user32.GetFocus()
            if focused_hwnd:
                return int(focused_hwnd)
        except (AttributeError, OSError, TypeError, ValueError):
            pass

        top_level = self.window()
        if top_level is None:
            return None
        try:
            window_id = top_level.winId()
            return int(window_id) if window_id is not None else None
        except (RuntimeError, TypeError, ValueError):
            return None

    @staticmethod
    def _synchronize_windows_ime_window(imm32, hwnd, open_status):
        """대기 중인 키보다 먼저 반영되도록 기본 IME 창에 동기 명령을 보낸다."""
        try:
            import ctypes
            from ctypes import wintypes

            ime_hwnd = imm32.ImmGetDefaultIMEWnd(hwnd)
            if not ime_hwnd:
                return False
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.SendMessageW.argtypes = [
                wintypes.HWND,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            ]
            user32.SendMessageW.restype = wintypes.LPARAM
            user32.SendMessageW(ime_hwnd, 0x0283, 0x0006, int(bool(open_status)))
            return True
        except (AttributeError, OSError, TypeError, ValueError):
            return False

    @classmethod
    def force_startup_korean_for_widget(cls, widget):
        """메인 창이 표시되는 즉시 한글 IME를 열되 최초 입력 보호 상태는 유지한다."""
        imm32 = cls._windows_ime_api()
        if imm32 is None or widget is None:
            return False
        try:
            window_id = widget.winId()
            hwnd = int(window_id) if window_id is not None else None
        except (RuntimeError, TypeError, ValueError):
            return False
        if hwnd is None:
            return False

        context = imm32.ImmGetContext(hwnd)
        if not context:
            return False
        try:
            applied = bool(imm32.ImmSetOpenStatus(context, True))
            synchronized = cls._synchronize_windows_ime_window(imm32, hwnd, True)
            if applied or synchronized:
                cls._last_windows_ime_open = True
                return True
            return False
        finally:
            imm32.ImmReleaseContext(hwnd, context)

    @classmethod
    def _complete_startup_korean_mode(cls):
        cls._force_korean_on_first_activation = False
        cls._last_windows_ime_open = True

    @staticmethod
    def _send_windows_virtual_keys(buffered_keys):
        """보류한 최초 물리 키를 현재 Windows 입력 스트림에 순서대로 되돌린다."""
        import sys

        if sys.platform != "win32" or not buffered_keys:
            return False
        try:
            import ctypes
            from ctypes import wintypes

            ULONG_PTR = wintypes.WPARAM

            class MOUSEINPUT(ctypes.Structure):
                _fields_ = [
                    ("dx", wintypes.LONG),
                    ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR),
                ]

            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [
                    ("wVk", wintypes.WORD),
                    ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR),
                ]

            class HARDWAREINPUT(ctypes.Structure):
                _fields_ = [
                    ("uMsg", wintypes.DWORD),
                    ("wParamL", wintypes.WORD),
                    ("wParamH", wintypes.WORD),
                ]

            class INPUTUNION(ctypes.Union):
                _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

            class INPUT(ctypes.Structure):
                _anonymous_ = ("value",)
                _fields_ = [("type", wintypes.DWORD), ("value", INPUTUNION)]

            inputs = []
            for virtual_key, shift_required, _text in buffered_keys:
                if shift_required:
                    inputs.append(INPUT(type=1, ki=KEYBDINPUT(wVk=0x10)))
                inputs.append(INPUT(type=1, ki=KEYBDINPUT(wVk=virtual_key)))
                inputs.append(INPUT(type=1, ki=KEYBDINPUT(wVk=virtual_key, dwFlags=0x0002)))
                if shift_required:
                    inputs.append(INPUT(type=1, ki=KEYBDINPUT(wVk=0x10, dwFlags=0x0002)))

            input_array = (INPUT * len(inputs))(*inputs)
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
            user32.SendInput.restype = wintypes.UINT
            return user32.SendInput(len(inputs), input_array, ctypes.sizeof(INPUT)) == len(inputs)
        except (AttributeError, OSError, TypeError, ValueError):
            return False

    def _guard_startup_ime_key(self, event):
        if not self._startup_ime_guard_active:
            return False

        import sys

        char = event.text()
        blocked_modifiers = (
            Qt.KeyboardModifier.ControlModifier
            | Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.MetaModifier
        )
        if (
            sys.platform != "win32"
            or len(char) != 1
            or not char.isascii()
            or not char.isalpha()
            or event.modifiers() & blocked_modifiers
        ):
            return False

        virtual_key = int(event.nativeVirtualKey() or event.key())
        if not 0x41 <= virtual_key <= 0x5A:
            return False

        shift_required = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        self._startup_ime_key_buffer.append((virtual_key, shift_required, char))
        self._restore_windows_ime_state()
        event.accept()

        if not self._startup_ime_replay_scheduled:
            self._startup_ime_replay_scheduled = True
            serial = self._input_activation_serial
            QTimer.singleShot(0, lambda: self._replay_startup_ime_keys(serial))
        return True

    def _replay_startup_ime_keys(self, serial):
        if serial != self._input_activation_serial:
            return
        self._startup_ime_replay_scheduled = False
        buffered_keys = self._startup_ime_key_buffer[:]
        self._startup_ime_key_buffer.clear()
        self._startup_ime_guard_active = False
        if not buffered_keys:
            return
        SmartTextEdit._complete_startup_korean_mode()
        if self.hasFocus() and not self.isReadOnly() and self._send_windows_virtual_keys(buffered_keys):
            return
        if self.hasFocus() and not self.isReadOnly():
            self.insertPlainText("".join(item[2] for item in buffered_keys))

    def _remember_windows_ime_state(self):
        imm32 = self._windows_ime_api()
        hwnd = self._windows_ime_hwnd()
        if imm32 is None or hwnd is None:
            return
        context = imm32.ImmGetContext(hwnd)
        if not context:
            return
        try:
            SmartTextEdit._last_windows_ime_open = bool(imm32.ImmGetOpenStatus(context))
        finally:
            imm32.ImmReleaseContext(hwnd, context)

    def _restore_windows_ime_state(self):
        force_korean = SmartTextEdit._force_korean_on_first_activation
        open_status = True if force_korean else SmartTextEdit._last_windows_ime_open
        imm32 = self._windows_ime_api()
        hwnd = self._windows_ime_hwnd()
        if imm32 is None or open_status is None or hwnd is None:
            return
        context = imm32.ImmGetContext(hwnd)
        if not context:
            return
        try:
            applied = bool(imm32.ImmSetOpenStatus(context, open_status))
            synchronized = self._synchronize_windows_ime_window(imm32, hwnd, open_status)
            if force_korean and (applied or synchronized):
                SmartTextEdit._last_windows_ime_open = True
        finally:
            imm32.ImmReleaseContext(hwnd, context)

    def setPlaceholderText(self, text):
        self._custom_placeholder = text
        self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)
        # 여백을 무시하는 기본 placeholder 대신, 문서 여백을 계산하여 직접 그립니다.
        # 또한 조합 중(IME)일 때(_is_composing)는 placeholder를 숨깁니다.
        if self.document().isEmpty() and self._custom_placeholder and not self._is_composing:
            from PyQt6.QtGui import QPainter, QColor
            from PyQt6.QtCore import Qt
            painter = QPainter(self.viewport())
            painter.setPen(QColor("#a0a0a0"))

            root_fmt = self.document().rootFrame().frameFormat()
            left_margin = root_fmt.leftMargin()
            top_margin = root_fmt.topMargin()

            # Qt 기본 여백 4px 추가 보정
            rect = self.viewport().rect()
            rect.adjust(int(left_margin) + 4, int(top_margin) + 4, 0, 0)

            painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, self._custom_placeholder)

    def on_text_changed(self):
        cursor = self.textCursor()
        cursor_pos = cursor.position()

        # 사후 텍스트 치환 (... -> …)
        if cursor_pos >= 3:
            cursor.setPosition(cursor_pos - 3, QTextCursor.MoveMode.KeepAnchor)
            if cursor.selectedText() == "...":
                self.blockSignals(True)
                cursor.insertText("…")
                self.setTextCursor(cursor)
                self.blockSignals(False)
                return
            cursor.setPosition(cursor_pos)

        # 사후 텍스트 치환 (ㄴㄴ -> 「」)
        if cursor_pos >= 2:
            cursor.setPosition(cursor_pos - 2, QTextCursor.MoveMode.KeepAnchor)
            if cursor.selectedText() == "ㄴㄴ":
                self.blockSignals(True)
                cursor.insertText("「」")
                cursor.movePosition(QTextCursor.MoveOperation.Left)
                self.setTextCursor(cursor)
                self.blockSignals(False)
                return

            cursor.setPosition(cursor_pos - 2, QTextCursor.MoveMode.KeepAnchor)
            if cursor.selectedText() == "ㄱㄱ":
                self.blockSignals(True)
                cursor.insertText("『』")
                cursor.movePosition(QTextCursor.MoveOperation.Left)
                self.setTextCursor(cursor)
                self.blockSignals(False)
                return
            cursor.setPosition(cursor_pos)

    def inputMethodEvent(self, event):
        if (
            self._startup_ime_guard_active
            and not self._startup_ime_key_buffer
            and event.preeditString()
        ):
            self._startup_ime_guard_active = False
            SmartTextEdit._complete_startup_korean_mode()
        super().inputMethodEvent(event)

        preedit = event.preeditString()

        # IME 조합 상태 추적 (안내 문구를 가리기 위함)
        is_composing_now = bool(preedit)
        if self._is_composing != is_composing_now:
            self._is_composing = is_composing_now
            self.viewport().update()
        if preedit in ("ㄴ", "ㄱ"):
            cursor = self.textCursor()
            cursor_pos = cursor.position()
            if cursor_pos >= 1:
                cursor.setPosition(cursor_pos - 1, QTextCursor.MoveMode.KeepAnchor)
                if cursor.selectedText() == preedit:
                    # 블록 시그널을 걸어서 reset() 도중에 textChanged가 두 번 치환하는 것을 방지
                    self.blockSignals(True)
                    QGuiApplication.inputMethod().reset()

                    cursor = self.textCursor()
                    pos = cursor.position()

                    target_char = "「」" if preedit == "ㄴ" else "『』"
                    target_str = preedit * 2

                    cursor.setPosition(max(0, pos - 2), QTextCursor.MoveMode.KeepAnchor)
                    if cursor.selectedText() == target_str:
                        cursor.insertText(target_char)
                    else:
                        cursor.setPosition(max(0, pos - 1), QTextCursor.MoveMode.KeepAnchor)
                        if cursor.selectedText() == preedit:
                            cursor.insertText(target_char)

                    cursor.movePosition(QTextCursor.MoveOperation.Left)
                    self.setTextCursor(cursor)
                    self.blockSignals(False)
                    return

    def keep_cursor_centered(self):
        # 드래그/Shift 선택 중에는 Qt의 기본 자동 스크롤에 중앙 정렬 이동이
        # 겹치지 않게 한다. 선택이 끝난 뒤 일반 입력 시에는 다시 동작한다.
        if not self.typewriter_enabled or self.textCursor().hasSelection():
            return

        cursor_rect = self.cursorRect()
        viewport_height = self.viewport().height()
        center_y = viewport_height / 2

        # 커서가 화면 중간보다 아래로 내려가면 스크롤을 이동시킴
        if cursor_rect.center().y() > center_y:
            diff = cursor_rect.center().y() - center_y
            scroll_bar = self.verticalScrollBar()
            scroll_bar.setValue(int(scroll_bar.value() + diff))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 타자기 모드에서 마지막 줄까지 부드럽게 스크롤 되도록 하단 여백 추가
        doc = self.document()
        root_frame = doc.rootFrame()
        fmt = root_frame.frameFormat()
        fmt.setBottomMargin(self.viewport().height() / 2)
        root_frame.setFrameFormat(fmt)

    def keyPressEvent(self, event):
        if self._guard_startup_ime_key(event):
            return

        # 엔터 키 처리: 바로 다음 문자가 닫는 따옴표/괄호면, 넘어가서 줄바꿈
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            cursor = self.textCursor()
            cursor_pos = cursor.position()
            cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor)
            next_char = cursor.selectedText()
            cursor.setPosition(cursor_pos)

            if next_char in ("'", '"', ']', '}', ')', '」', '』'):
                cursor.movePosition(QTextCursor.MoveOperation.Right)
                self.setTextCursor(cursor)
                super().keyPressEvent(event)
                return

        if event.key() == Qt.Key.Key_Backspace:
            cursor = self.textCursor()
            if not cursor.hasSelection():
                cursor_pos = cursor.position()

                # 장면 전환 기호 통째로 지우기
                if cursor_pos >= 10:
                    cursor.setPosition(cursor_pos - 10, QTextCursor.MoveMode.KeepAnchor)
                    if cursor.selectedText().replace('\u2029', '\n') == "\n\n * * *\n\n":
                        cursor.removeSelectedText()
                        return
                    cursor.setPosition(cursor_pos)

                if cursor_pos > 0:
                    cursor.movePosition(QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor)
                    prev_char = cursor.selectedText()
                    cursor.setPosition(cursor_pos)

                    cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor)
                    next_char = cursor.selectedText()
                    cursor.setPosition(cursor_pos)

                    pairs = [("'", "'"), ('"', '"'), ('[', ']'), ('{', '}'), ('(', ')'), ('「', '」'), ('『', '』')]
                    if (prev_char, next_char) in pairs:
                        cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor)
                        cursor.removeSelectedText()

            super().keyPressEvent(event)
            return

        if event.key() == Qt.Key.Key_Delete:
            cursor = self.textCursor()
            if not cursor.hasSelection():
                cursor_pos = cursor.position()
                cursor.setPosition(cursor_pos + 10, QTextCursor.MoveMode.KeepAnchor)
                if cursor.selectedText().replace('\u2029', '\n') == "\n\n * * *\n\n":
                    cursor.removeSelectedText()
                    return
                cursor.setPosition(cursor_pos)

        char = event.text()

        if char == "*":
            cursor = self.textCursor()
            cursor.insertText("\n\n * * *\n\n")
            return

        # 닫는 괄호/따옴표를 입력할 때, 바로 다음 문자가 동일하면 건너뛰기
        if char in ("'", '"', ']', '}', ')', '」', '』'):
            cursor = self.textCursor()
            cursor_pos = cursor.position()
            cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor)
            next_char = cursor.selectedText()
            cursor.setPosition(cursor_pos)
            if next_char == char:
                cursor.movePosition(QTextCursor.MoveOperation.Right)
                self.setTextCursor(cursor)
                return

        pair_map = {
            "'": "'",
            '"': '"',
            '[': ']',
            '{': '}',
            '(': ')'
        }

        if char in pair_map:
            close_char = pair_map[char]
            cursor = self.textCursor()
            if cursor.hasSelection():
                text = cursor.selectedText()
                cursor.insertText(f"{char}{text}{close_char}")
                return

            super().keyPressEvent(event)
            cursor = self.textCursor()
            cursor.insertText(close_char)
            cursor.movePosition(QTextCursor.MoveOperation.Left)
            self.setTextCursor(cursor)
            return

        super().keyPressEvent(event)
