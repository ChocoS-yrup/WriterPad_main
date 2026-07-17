import os
import re

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QSplitter, QTreeWidget, QTreeWidgetItem, QTextEdit, QLabel,
    QComboBox, QToolButton, QFrame, QMenu, QMessageBox,
    QLineEdit, QDialog, QListWidget, QListWidgetItem,
    QToolBar, QSizePolicy, QTextBrowser, QTabWidget, QFileDialog
)
from PyQt6.QtGui import QAction, QShortcut, QKeySequence, QPixmap, QPainter, QIcon, QFont
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QThread


class WritingExtractionMixin:
    def _get_all_chapter_files(self):
        import os
        import re
        manuscript_path = os.path.join(self.wpm.writing_root_path, "메인", "원고")
        chapter_files = []
        pattern = re.compile(r"^(\d+)화")

        if os.path.exists(manuscript_path):
            for root, dirs, files in os.walk(manuscript_path):
                for file in files:
                    if file.endswith(".txt"):
                        match = pattern.match(file)
                        if match:
                            chapter_num = int(match.group(1))
                            chapter_files.append((chapter_num, os.path.join(root, file), file))

        chapter_files.sort(key=lambda x: x[0])
        return chapter_files

    def _extract_chapters_to_file(self, chapters, check_length=False):
        import os
        from PyQt6.QtWidgets import QMessageBox

        if not chapters:
            QMessageBox.information(self, "추출", "추출할 챕터가 없습니다.")
            return None, [], []

        output_lines = []
        included_chapters = []
        for ch_num, path, filename in chapters:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except Exception as e:
                reply = QMessageBox.question(
                    self,
                    "파일 읽기 오류",
                    f"'{filename}' 파일을 읽는 도중 오류가 발생했습니다.\n계속 진행하시겠습니까?\n\n상세 오류: {e}",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.No:
                    return None, [], []
                continue

            if check_length:
                # 공백 제외 글자수 계산
                stripped_content = "".join(content.split())
                if len(stripped_content) < 300:
                    break

            title = os.path.splitext(filename)[0]
            output_lines.append(f"[{title}]\n\n{content}\n")
            included_chapters.append(ch_num)

        if not output_lines:
            QMessageBox.information(self, "추출", "조건에 맞는 추출 내용이 없습니다.")
            return None, [], []

        return "\n".join(output_lines), chapters, included_chapters

    def extract_all_chapters(self):
        chapters = self._get_all_chapter_files()
        result_text, _, included = self._extract_chapters_to_file(chapters, check_length=True)
        if not result_text: return

        from PyQt6.QtWidgets import QFileDialog, QMessageBox

        project_name = getattr(self.pm, 'current_project', '프로젝트')
        if not project_name:
            project_name = '프로젝트'

        if included:
            start_num = included[0]
            end_num = included[-1]
            default_name = f"{project_name}({start_num}~{end_num}화).txt"
        else:
            default_name = f"{project_name}(전체추출).txt"

        path, _ = QFileDialog.getSaveFileName(self, "추출 파일 저장", default_name, "Text Files (*.txt)")
        if path:
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(result_text)
                QMessageBox.information(self, "성공", f"파일이 성공적으로 추출되었습니다.\n{path}")
            except Exception as e:
                QMessageBox.critical(self, "오류", f"파일 저장 중 오류가 발생했습니다.\n{e}")

    def extract_partial_chapters(self):
        all_chapters = self._get_all_chapter_files()
        available_ch_nums = [c[0] for c in all_chapters] if all_chapters else None

        dialog = PartialExtractionDialog(self, available_chapters=available_ch_nums)
        if dialog.exec():
            n, m = dialog.get_range()
            if n > m:
                n, m = m, n

            filtered = [c for c in all_chapters if n <= c[0] <= m]

            result_text, original_filtered, included = self._extract_chapters_to_file(filtered, check_length=False)
            if not result_text: return

            from PyQt6.QtWidgets import QFileDialog, QMessageBox

            project_name = getattr(self.pm, 'current_project', '프로젝트')
            if not project_name:
                project_name = '프로젝트'
            default_name = f"{project_name}({n}~{m}화).txt"

            path, _ = QFileDialog.getSaveFileName(self, "추출 파일 저장", default_name, "Text Files (*.txt)")
            if path:
                try:
                    with open(path, 'w', encoding='utf-8') as f:
                        f.write(result_text)

                    # 누락 화수 확인 로직
                    requested_set = set(range(n, m + 1))
                    included_set = set(included)
                    missing_set = requested_set - included_set

                    msg = f"파일이 성공적으로 추출되었습니다.\n\n[결과 요약]\n- 지정 범위: {n}화 ~ {m}화\n- 실제 포함: 총 {len(included)}개 화"
                    if missing_set:
                        missing_sorted = sorted(list(missing_set))

                        # 연속된 숫자를 그룹화하는 로직 (예: 26~50화)
                        def get_ranges(nums):
                            if not nums: return ""
                            ranges = []
                            start = nums[0]
                            prev = nums[0]
                            for x in nums[1:]:
                                if x == prev + 1:
                                    prev = x
                                else:
                                    ranges.append((start, prev))
                                    start = x
                                    prev = x
                            ranges.append((start, prev))

                            range_strs = []
                            for s, e in ranges:
                                if s == e:
                                    range_strs.append(f"{s}화")
                                else:
                                    range_strs.append(f"{s}화~{e}화")
                            return ", ".join(range_strs)

                        msg += f"\n- 제외(존재하지 않음): {get_ranges(missing_sorted)}"

                    QMessageBox.information(self, "성공", msg)
                except Exception as e:
                    QMessageBox.critical(self, "오류", f"파일 저장 중 오류가 발생했습니다.\n{e}")



class PartialExtractionDialog(QDialog):
    def __init__(self, parent=None, available_chapters=None):
        super().__init__(parent)
        self.setWindowTitle("부분 추출")
        self.setFixedSize(500, 240)

        from PyQt6.QtWidgets import QVBoxLayout, QHBoxLayout, QComboBox, QLabel, QPushButton
        from PyQt6.QtGui import QFont

        layout = QVBoxLayout(self)

        font = QFont()
        font.setPointSize(16)

        hlayout = QHBoxLayout()
        self.start_combo = QComboBox()
        self.start_combo.setFont(font)
        self.end_combo = QComboBox()
        self.end_combo.setFont(font)

        if available_chapters:
            for ch in available_chapters:
                self.start_combo.addItem(f"{ch}화", ch)
                self.end_combo.addItem(f"{ch}화", ch)
        else:
            for i in range(1, 1000):
                self.start_combo.addItem(f"{i}화", i)
                self.end_combo.addItem(f"{i}화", i)

        if self.end_combo.count() > 0:
            self.end_combo.setCurrentIndex(self.end_combo.count() - 1)

        lbl_start = QLabel("시작 화:")
        lbl_start.setFont(font)
        lbl_end = QLabel("끝 화:")
        lbl_end.setFont(font)

        hlayout.addWidget(lbl_start)
        hlayout.addWidget(self.start_combo)
        hlayout.addWidget(lbl_end)
        hlayout.addWidget(self.end_combo)

        layout.addLayout(hlayout)

        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("추출")
        ok_btn.setFont(font)
        ok_btn.setMinimumHeight(50)
        ok_btn.clicked.connect(self.accept)

        cancel_btn = QPushButton("취소")
        cancel_btn.setFont(font)
        cancel_btn.setMinimumHeight(50)
        cancel_btn.clicked.connect(self.reject)

        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def get_range(self):
        return self.start_combo.currentData(), self.end_combo.currentData()
