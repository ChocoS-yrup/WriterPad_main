from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTextEdit, QLabel, QSplitter, QTextBrowser,
    QStackedWidget, QComboBox, QMenu, QGraphicsDropShadowEffect, QScrollArea, QFrame, QMessageBox,
    QSizePolicy, QCheckBox, QDialog, QListWidget, QLineEdit, QRadioButton, QSpinBox, QButtonGroup,
    QFontDialog, QTabWidget, QListWidgetItem, QGridLayout, QTabBar
)
from PyQt6.QtGui import (
    QFont, QTextCursor, QGuiApplication, QTextDocument, QTextCharFormat,
    QTextFormat, QPen,
)
from PyQt6.QtCore import (
    pyqtSignal, Qt, QSettings, QTimer, QThread, QEventLoop,
)


class SmartTextEdit(QTextEdit):
    """따옴표 자동완성을 지원하는 텍스트 에디터"""
    compositionChanged = pyqtSignal()
    _last_windows_ime_open = None
    _force_korean_on_first_activation = True
    _ELLIPSIS_FORMAT_PROPERTY = int(QTextFormat.Property.UserProperty) + 1

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.typewriter_enabled = False
        self._typewriter_base_bottom_margin = (
            self.document().rootFrame().frameFormat().bottomMargin()
        )
        self._typewriter_align_timer = QTimer(self)
        self._typewriter_align_timer.setSingleShot(True)
        self._typewriter_align_timer.timeout.connect(self.keep_cursor_centered)
        self._custom_placeholder = ""
        self._is_composing = False
        self._ime_preedit_text = ""
        self._input_activation_serial = 0
        self._startup_ime_guard_active = False
        self._startup_ime_key_buffer = []
        self._startup_ime_replay_scheduled = False
        self._ellipsis_start = None
        self._ellipsis_count = 0
        self.cursorPositionChanged.connect(self.keep_cursor_centered)
        self.textChanged.connect(self.on_text_changed)

    def focusOutEvent(self, event):
        self._finish_pending_ellipsis_edit()
        self._remember_windows_ime_state()
        super().focusOutEvent(event)

    def has_pending_input_method(self):
        """Return whether Windows is still displaying an uncommitted preedit."""
        return bool(self._is_composing and self._ime_preedit_text)

    def text_with_pending_input_method(self):
        """Return the document text with the visible IME preedit included.

        Windows draws the syllable being composed at the caret, but it is not
        part of QTextDocument until the IME commits it. A background save
        reads this instead of forcing that commit: rebuilding the string
        sends nothing to the IME, injects no cursor keys and runs no nested
        event loop, so it cannot collide with the writer's next keystroke.

        Autosave rewrites the whole file, so a syllable the writer goes on to
        change or cancel is corrected by the following save.
        """
        text = self.toPlainText()
        if not self.has_pending_input_method():
            return text
        # Block separators occupy one cursor position each and toPlainText
        # renders them as one newline, so the two offsets stay aligned.
        position = max(0, min(int(self.textCursor().position()), len(text)))
        return text[:position] + self._ime_preedit_text + text[position:]

    def commit_pending_input_method(self):
        """Commit the focused editor's visible IME preedit before persistence."""
        if (
            self.isReadOnly()
            or not self.hasFocus()
            or not self._is_composing
            or not self._ime_preedit_text
        ):
            return False

        # Let the native IME own the commit. Inserting the cached preedit here
        # can race with the real Windows commit event and duplicate the final
        # character. A zero-net cursor nudge mirrors the user's Right/Left
        # action, which closes the boxed composition without adding text.
        input_method = QGuiApplication.inputMethod()
        if input_method is None:
            return False
        input_method.commit()
        self._nudge_cursor_for_input_commit()

        # Windows may post the commit event after QInputMethod.commit()
        # returns. Give that native event a short event-loop turn before the
        # caller reads toPlainText().
        if self._is_composing:
            loop = QEventLoop()

            def finish_when_committed():
                if not self._is_composing:
                    loop.quit()

            self.compositionChanged.connect(finish_when_committed)
            QTimer.singleShot(60, loop.quit)
            try:
                loop.exec()
            finally:
                try:
                    self.compositionChanged.disconnect(
                        finish_when_committed
                    )
                except (RuntimeError, TypeError):
                    pass
        return True

    def _nudge_cursor_for_input_commit(self):
        """Move away and back without changing the final caret position."""
        cursor = self.textCursor()
        if cursor.hasSelection():
            return False
        position = cursor.position()
        last_position = max(0, self.document().characterCount() - 1)
        if position < last_position:
            virtual_keys = (0x27, 0x25)  # Right, Left
        else:
            virtual_keys = (0x25, 0x27)  # Left, Right

        # On Windows, pass the same navigation keys through the native input
        # stream so the Korean IME sees them just as it sees physical arrows.
        import sys
        if sys.platform == "win32" and self._send_windows_virtual_keys(
            [(key, False, "") for key in virtual_keys]
        ):
            return True

        moved = QTextCursor(cursor)

        if position < last_position:
            if not moved.movePosition(QTextCursor.MoveOperation.Right):
                return False
            self.setTextCursor(moved)
            moved.movePosition(QTextCursor.MoveOperation.Left)
        elif position > 0:
            if not moved.movePosition(QTextCursor.MoveOperation.Left):
                return False
            self.setTextCursor(moved)
            moved.movePosition(QTextCursor.MoveOperation.Right)
        else:
            return False

        self.setTextCursor(moved)
        return moved.position() == position

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

    def setPlainText(self, text):
        super().setPlainText(text)
        self._apply_ellipsis_display_formats()

    def setText(self, text):
        super().setText(text)
        self._apply_ellipsis_display_formats()

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
        if self.isReadOnly():
            # The custom IME shortcuts below insert text through QTextCursor,
            # so they must not bypass QTextEdit's read-only protection.
            super().inputMethodEvent(event)
            return

        # IME 조합으로 들어오는 텍스트는 직접 키 입력 말줄임표 규칙의 대상이 아니다.
        self._finish_pending_ellipsis_edit()
        if (
            self._startup_ime_guard_active
            and not self._startup_ime_key_buffer
            and event.preeditString()
        ):
            self._startup_ime_guard_active = False
            SmartTextEdit._complete_startup_korean_mode()
        previous_preedit = self._ime_preedit_text
        super().inputMethodEvent(event)
        preedit = event.preeditString()
        self._ime_preedit_text = preedit

        # IME 조합 상태 추적 (안내 문구를 가리기 위함)
        is_composing_now = bool(preedit)
        if self._is_composing != is_composing_now:
            self._is_composing = is_composing_now
            self.viewport().update()
        if preedit != previous_preedit:
            # A preedit character is visible but is not yet part of
            # QTextDocument, so QTextEdit may not emit textChanged for it.
            # Treat the composition itself as an edit to restart autosave.
            self.compositionChanged.emit()
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
        # 아직 표시되지 않은 뷰는 문서 레이아웃이 없다. 특히 좌·우 에디터가 하나의
        # QTextDocument 를 공유한 채 생성 중일 때 cursorRect() 를 만지면 Qt 가
        # 죽는다. 표시된 뒤 resizeEvent 가 정렬을 이어받는다.
        if not self.isVisible():
            return
        self._ensure_typewriter_margin()

        cursor_rect = self.cursorRect()
        viewport_height = self.viewport().height()
        center_y = viewport_height / 2

        # 커서가 화면 중간보다 아래로 내려가면 스크롤을 이동시킴
        if cursor_rect.center().y() > center_y:
            diff = cursor_rect.center().y() - center_y
            scroll_bar = self.verticalScrollBar()
            scroll_bar.setValue(int(scroll_bar.value() + diff))

    def set_typewriter_mode(self, enabled, base_bottom_margin=None):
        """Apply typewriter state and its document/view layout atomically."""
        if base_bottom_margin is not None:
            try:
                self._typewriter_base_bottom_margin = max(
                    0.0, float(base_bottom_margin)
                )
            except (TypeError, ValueError):
                pass
        self.typewriter_enabled = bool(enabled)
        self._refresh_typewriter_layout(align_cursor=self.typewriter_enabled)

    def _expected_bottom_margin(self):
        if self.typewriter_enabled:
            return self.viewport().height() / 2
        return self._typewriter_base_bottom_margin

    def _ensure_typewriter_margin(self):
        """문서가 새로 채워지면 rootFrame 서식이 기본값으로 돌아간다.

        setPlainText 는 탭 전환과 화수 로드마다 호출되므로 타자기 하단 여백이
        조용히 사라진다. 여백이 없으면 스크롤 여유가 0이라 중앙 정렬이 아예
        걸리지 않고, 커서가 뷰포트 맨 아래에 닿아야 Qt 기본 스크롤만 동작한다.
        """
        if not self.isVisible():
            return
        current = self.document().rootFrame().frameFormat().bottomMargin()
        if abs(current - self._expected_bottom_margin()) > 0.5:
            self._refresh_typewriter_layout(from_view_event=True)

    def _refresh_typewriter_layout(self, align_cursor=False, from_view_event=False):
        # 생성 도중에는 문서 레이아웃을 건드리지 않는다. AI 모드의 좌·우
        # 에디터는 하나의 QTextDocument 를 공유한 채 만들어지는데, 뷰가 생기기
        # 전에 rootFrame 의 frameFormat 을 바꾸면 Qt 가 죽는다.
        # show/resize 이벤트는 뷰가 실제로 존재한다는 뜻이므로 그대로 적용한다.
        # (showEvent 시점의 isVisible() 은 아직 False 라 이 구분이 필요하다.)
        if not from_view_event and not self.isVisible():
            return
        doc = self.document()
        was_modified = doc.isModified()
        signals_were_blocked = self.blockSignals(True)
        try:
            root_frame = doc.rootFrame()
            fmt = root_frame.frameFormat()
            fmt.setBottomMargin(self._expected_bottom_margin())
            root_frame.setFrameFormat(fmt)
            doc.setModified(was_modified)
        finally:
            self.blockSignals(signals_were_blocked)
        self.viewport().update()
        if align_cursor and not self.textCursor().hasSelection():
            self.keep_cursor_centered()
            self._typewriter_align_timer.start(0)
        else:
            self._typewriter_align_timer.stop()

    def showEvent(self, event):
        super().showEvent(event)
        # 생성 중 미뤄둔 타자기 여백을 뷰가 실제로 생긴 시점에 적용한다.
        self._refresh_typewriter_layout(
            align_cursor=self.typewriter_enabled, from_view_event=True
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_typewriter_layout(
            align_cursor=self.typewriter_enabled, from_view_event=True
        )

    @staticmethod
    def _is_direct_period_event(event):
        blocked_modifiers = (
            Qt.KeyboardModifier.ControlModifier
            | Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.MetaModifier
        )
        return event.text() == "." and not (event.modifiers() & blocked_modifiers)

    def _finish_pending_ellipsis_edit(self):
        self._ellipsis_start = None
        self._ellipsis_count = 0

    def _pending_ellipsis_matches_cursor(self, cursor):
        if self._ellipsis_start is None or cursor.hasSelection():
            return False
        if cursor.position() != self._ellipsis_start + self._ellipsis_count:
            return False
        probe = QTextCursor(cursor)
        probe.setPosition(self._ellipsis_start, QTextCursor.MoveMode.KeepAnchor)
        return probe.selectedText() == "." * self._ellipsis_count

    def _ellipsis_display_format(self, base_format):
        """줄 높이는 유지하면서 가운데 말줄임표의 점만 시각적으로 키운다."""
        ellipsis_format = QTextCharFormat(base_format)
        brush = base_format.foreground()
        if brush.style() == Qt.BrushStyle.NoBrush:
            color = self.palette().text().color()
        else:
            color = brush.color()

        point_size = base_format.fontPointSize()
        if point_size <= 0:
            point_size = self.currentFont().pointSizeF()
        if point_size <= 0:
            point_size = 14.0

        # 외곽선은 글자의 advance/ascender/descender를 바꾸지 않으므로
        # 폰트 크기를 올릴 때처럼 해당 줄 전체가 내려앉지 않는다.
        outline_width = max(0.75, min(1.35, point_size / 14.0))
        outline = QPen(color, outline_width)
        outline.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        ellipsis_format.setTextOutline(outline)
        ellipsis_format.setProperty(self._ELLIPSIS_FORMAT_PROPERTY, True)
        return ellipsis_format

    def _apply_ellipsis_display_formats(self):
        """파일에서 다시 연 문서의 말줄임표에도 표시 전용 서식을 복원한다."""
        document = self.document()
        undo_enabled = document.isUndoRedoEnabled()
        signals_were_blocked = self.blockSignals(True)
        if undo_enabled:
            document.setUndoRedoEnabled(False)
        try:
            match = document.find("⋯")
            while not match.isNull():
                base_format = match.charFormat()
                if not base_format.hasProperty(self._ELLIPSIS_FORMAT_PROPERTY):
                    match.setCharFormat(self._ellipsis_display_format(base_format))
                match = document.find("⋯", match)
        finally:
            if undo_enabled:
                document.setUndoRedoEnabled(True)
            self.blockSignals(signals_were_blocked)

    def _insert_direct_period(self):
        cursor = self.textCursor()
        if self._pending_ellipsis_matches_cursor(cursor):
            # 이전 점 입력의 Undo 블록에 연결하되, 매 입력마다 닫아서
            # 화면에 점이 즉시 그려지도록 한다.
            cursor.joinPreviousEditBlock()
            cursor.insertText(".")
            self._ellipsis_count += 1
            if self._ellipsis_count == 3:
                cursor.setPosition(self._ellipsis_start)
                cursor.setPosition(
                    self._ellipsis_start + 3,
                    QTextCursor.MoveMode.KeepAnchor,
                )
                base_format = QTextCharFormat(cursor.charFormat())
                cursor.insertText("⋯", self._ellipsis_display_format(base_format))
                cursor.setCharFormat(base_format)
                cursor.endEditBlock()
                self._finish_pending_ellipsis_edit()
            else:
                cursor.endEditBlock()
            self.setTextCursor(cursor)
            return

        self._finish_pending_ellipsis_edit()
        cursor = self.textCursor()
        if cursor.hasSelection():
            cursor.removeSelectedText()
        self._ellipsis_start = cursor.position()
        cursor.beginEditBlock()
        cursor.insertText(".")
        cursor.endEditBlock()
        self._ellipsis_count = 1
        self.setTextCursor(cursor)

    def mousePressEvent(self, event):
        self._finish_pending_ellipsis_edit()
        super().mousePressEvent(event)

    def insertFromMimeData(self, source):
        # 붙여넣기는 원문을 보존하며 말줄임표 치환을 적용하지 않는다.
        self._finish_pending_ellipsis_edit()
        super().insertFromMimeData(source)

    def keyPressEvent(self, event):
        if self.isReadOnly():
            # Smart pairs and scene separators insert directly with QTextCursor.
            # Delegate first so a document-less/read-only editor stays immutable.
            super().keyPressEvent(event)
            return

        if not self._is_direct_period_event(event) or self._is_composing:
            self._finish_pending_ellipsis_edit()

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

        if self._is_direct_period_event(event) and not self._is_composing and not self.isReadOnly():
            self._insert_direct_period()
            return

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
