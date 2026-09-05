import json
import os
import subprocess
import time
from uuid import uuid4

from PyQt6.QtCore import Qt, QSettings, QTimer, QThread, pyqtSignal
from PyQt6.QtGui import QIcon, QAction, QFont, QShortcut, QKeySequence, QTextCursor
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QMessageBox, QSystemTrayIcon,
    QMenu, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QCheckBox, QFrame,
    QStackedWidget, QStatusBar, QLabel, QSplitter,
)

from assistant_runtime import AIGenerationWorker, AIRequestContext


class AssistantWorkflowMixin:
    def _ai_session_key(self, step_name):
        return (self.pm.project_path, self.current_chapter, step_name)

    def _start_ai_request(self, step_name, selected_model, *, feedback=False, use_context_caching=False):
        request = AIRequestContext(
            str(uuid4()), self.pm.current_project, self.pm.project_path,
            self.current_chapter, step_name, selected_model, feedback,
        )
        worker = AIGenerationWorker(
            step_name, self.ai_panel.chat_session, selected_model,
            use_context_caching=use_context_caching, parent=self,
        )
        worker.request_context = request
        if not hasattr(self, "_ai_workers"):
            self._ai_workers = []
        self._ai_workers.append(worker)
        self._active_ai_request = request
        self._ai_session_context = self._ai_session_key(step_name)
        self.is_working = True
        self.sidebar.setEnabled(False)
        self.chapter_selector.setEnabled(False)
        self.update_status_bar()
        worker.resultReady.connect(self._on_ai_request_result)
        worker.error.connect(self._on_ai_request_error)
        worker.finished.connect(self._on_ai_worker_finished)
        if feedback:
            self.chat_worker = worker
        else:
            self.ai_worker = worker
        worker.start()

    def _on_ai_request_result(self, step_name, text, in_tok, out_tok):
        self.on_ai_finished(
            step_name, text, in_tok, out_tok,
            request=self.sender().request_context,
        )

    def _on_ai_request_error(self, message):
        self.on_ai_error(message, request=self.sender().request_context)

    def _on_ai_worker_finished(self):
        worker = self.sender()
        if worker in self._ai_workers:
            self._ai_workers.remove(worker)
        worker.deleteLater()

    def has_running_ai_workers(self):
        return any(worker.isRunning() for worker in getattr(self, "_ai_workers", ()))

    def _cancel_ai_request(self):
        request = getattr(self, "_active_ai_request", None)
        self._active_ai_request = None  # invalidate even already-queued callbacks
        for worker in getattr(self, "_ai_workers", ()):
            if worker.request_context == request:
                worker.requestInterruption()
        self.is_working = False
        return request is not None

    def handle_ai_open(self, step_name):
        """AI 세션을 초기화(또는 기존 세션 유지)하고 창만 엽니다."""
        if self.is_working:
            QMessageBox.warning(self, "작업 중", "이미 AI 작업이 진행 중입니다. 잠시만 기다려주세요.")
            return

        # 시스템 프롬프트를 미리 로드
        system_prompt = ""
        if step_name == "초안":
            system_prompt = self.pm.get_project_setting("prompt_draft", self.pm.global_config.get("prompt_draft", ""))
        elif step_name == "평가":
            system_prompt = self.pm.get_project_setting("prompt_eval", self.pm.global_config.get("prompt_eval", ""))
        elif step_name == "요약":
            system_prompt = self.pm.get_project_setting("prompt_summary", self.pm.global_config.get("prompt_summary", ""))

        session_key = self._ai_session_key(step_name)
        if getattr(self, "_ai_session_context", None) != session_key:
            self.ai_panel.init_session(step_name, system_prompt, "", is_generation=False, reset=True)
        self._ai_session_context = session_key

        self.ai_panel.show()

        # 패널이 열려 있는 동안 탭 전환 비활성화
        self.sidebar.setEnabled(False)
        if hasattr(self, 'chapter_selector'):
            self.chapter_selector.setEnabled(False)

    def handle_ai_generation(self, step_name, custom_context=None):
        if self.is_working:
            QMessageBox.warning(self, "작업 중", "이미 AI 작업이 진행 중입니다. 잠시만 기다려주세요.")
            return

        original_step = step_name
        context = custom_context

        # [신규 기능] 완성본에서 AI 작성 클릭 시 -> 완성본 내용을 바탕으로 평가 탭으로 강제 이동
        if original_step == "완성본":
            source_chapter = self.current_chapter
            completed_text = self.get_active_panel().text_edit.toPlainText()
            context = completed_text
            step_name = "평가"

            # 평가 탭으로 안전하게 전환
            self.switch_tab(2)

            # 전환된 평가 탭의 화수를 완성본의 화수(source_chapter)로 강제 동기화
            if hasattr(self, 'chapter_selector'):
                self.chapter_selector.set_value(source_chapter)
            self.on_chapter_changed(source_chapter)

        if original_step == "초안":
            # [기능 추가] 초안 작성 시 완성본 탭(인덱스 1)의 화수도 현재 화수로 자동 동기화
            completed = self.left_panels[1]
            if completed.current_chapter != self.current_chapter:
                if not self.sync_internal_storage(completed):
                    return
                completed.current_chapter = self.current_chapter
                self.right_panels[1].current_chapter = self.current_chapter
                self.load_content_for_panel(completed)
            self.pm.set_project_setting("chapter_tab_1", self.current_chapter)

            if context is None:
                context = self.gather_context("초안", self.current_chapter, 0)

            # 에디터에 작성된 초안 텍스트를 가져와서 덧붙임
            editor_text = self.get_active_panel().text_edit.toPlainText().strip()
            if editor_text:
                context = f"{context}\n\n[이번 화 작성 플롯 및 지시사항]\n{editor_text}"

            system_prompt = self.pm.get_project_setting("prompt_draft", self.pm.global_config.get("prompt_draft", ""))
            self.ai_panel.init_session("초안", system_prompt, context)

        elif original_step == "완성본":
            system_prompt = self.pm.get_project_setting("prompt_eval", self.pm.global_config.get("prompt_eval", ""))

            char_count = len(context)
            user_text = f"{source_chapter}화 원고는 공백포함 {char_count}글자로 작성되었음. 다음은 {source_chapter}화 완성본 내용입니다.\n\n[완성본 원문]\n{context}"
            self.ai_panel.init_session("평가", system_prompt, user_text)

        elif original_step in ["평가", "요약"]:
            if context:
                # 내부 로직(예: 최종 확정)에서 명시적으로 컨텍스트를 주입한 경우, 세션을 초기화하고 생성 시작
                system_prompt = self.pm.get_project_setting("prompt_summary", self.pm.global_config.get("prompt_summary", ""))
                self.ai_panel.init_session(original_step, system_prompt, context)
            else:
                # 사용자가 수동으로 버튼을 눌렀을 경우, 워커 실행 없이 채팅창만 염
                self.handle_ai_open(step_name)
                return

        # 2. 상태 업데이트 및 작업 시작
        self.is_working = True
        self.update_status_bar()

        # 패널 열기 및 잠금
        self.sidebar.setEnabled(False)
        if hasattr(self, 'chapter_selector'):
            self.chapter_selector.setEnabled(False)

        self.ai_panel.show()

        # 탭(step_name)에 맞는 AI 모델 선택
        model_mapping = {
            "초안": "model_draft",
            "평가": "model_eval",
            "요약": "model_summary"
        }
        model_key = model_mapping.get(step_name, "model_eval")
        selected_model = self.pm.get_project_setting(model_key, self.pm.global_config.get(model_key, "Gemini 3.1 Pro"))

        self._start_ai_request(step_name, selected_model)

    def gather_context(self, step_name, current_chapter, max_summary_ch=None):
        """초안 탭 등에서 직전 화수(N-1)의 요약 탭 텍스트를 실제 파일에서 불러옵니다."""
        if current_chapter <= 1:
            return "이전 화수 요약이 없습니다. (1화)"

        # 1. N-1화 요약 파일 로드 (여기에는 N-6까지의 AI 요약본과 N-5~N-1의 원문이 포함되어 있음)
        prev_summary = self.pm.load_chapter_text("요약", current_chapter - 1)
        if not prev_summary.strip():
            return f"[{current_chapter-1}화 요약본 파일이 비어 있습니다.]"

        print(f"[{step_name}] {current_chapter-1}화 요약본 실제 데이터 로드 완료")
        return prev_summary

    def stop_ai_generation(self):
        if self._cancel_ai_request():
            self.ai_panel.stop_loading_animation()
            self.ai_panel.append_chat("AI", "사용자에 의해 생성이 중단되었습니다.")
            self.update_status_bar()

    def hide_ai_panel(self):
        self._cancel_ai_request()
        self.ai_panel.stop_loading_animation()
        self.ai_panel.is_final_confirm_mode = False
        self.ai_panel.pending_raw_texts = ""
        self.ai_panel.hide()
        self.sidebar.setEnabled(True) # 탭 이동 버튼 잠금 해제
        if hasattr(self, 'chapter_selector'):
            self.chapter_selector.setEnabled(True)
        self.is_working = False
        self.update_status_bar()

    def handle_final_confirm(self):
        """평가 탭에서 최종 확정 버튼 클릭 시 호출됨"""
        if self.is_working:
            return
        self.hide_ai_panel()
        chapter = self.current_chapter

        summary_panel = self.left_panels[3]
        if summary_panel.current_chapter != chapter:
            if not self.sync_internal_storage(summary_panel):
                return
            summary_panel.current_chapter = chapter
            self.right_panels[3].current_chapter = chapter
            self.load_content_for_panel(summary_panel)

        # 1. 완성본 텍스트 가져오기 (양쪽 패널 중 완성본 찾기)
        completed_text = ""

        # 에디터에 띄워져있는 해당 화수의 완성본 내용 우선 사용
        for p in self.left_panels[:4] + self.right_panels[:4]:
            if p.step_name == "완성본" and p.current_chapter == chapter:
                completed_text = p.text_edit.toPlainText()
                break

        if not completed_text:
            completed_text = self.pm.load_chapter_text("완성본", chapter)

        if chapter <= 25:
            # [1구간] 25화 이하: 1화부터 현재 화수까지 완성본을 단순 취합하여 요약 탭에 저장
            accumulated_text = []
            for i in range(1, chapter + 1):
                if i == chapter:
                    raw_text = completed_text
                else:
                    raw_text = self.pm.load_chapter_text("완성본", i)

                accumulated_text.append(f"[{i}화]\n\n{raw_text}")

            final_summary_text = "\n\n".join(accumulated_text)

            self.pm.save_chapter_text("요약", chapter, final_summary_text)

            # 요약 탭 화면 갱신
            for p in self.left_panels[:4] + self.right_panels[:4]:
                if p.step_name == "요약" and p.current_chapter == chapter:
                    p.text_edit.setPlainText(final_summary_text)
                    p.trigger_autosave()

            # 분할 모드 해제 및 요약 탭 이동
            if self.is_split_mode:
                self.btn_split.blockSignals(True)
                self.btn_split.setChecked(False)
                self.btn_split.blockSignals(False)
                self.toggle_split_mode(False)

            # 요약 탭의 화수도 확정된 현재 화수로 동기화
            self.left_panels[3].current_chapter = chapter
            self.right_panels[3].current_chapter = chapter
            self.pm.set_project_setting("chapter_tab_3", chapter)

            self.switch_tab(3)

            QMessageBox.information(self, "최종 확정", f"1화부터 {chapter}화까지의 완성본이 요약 탭으로 취합되었습니다.")

        elif chapter == 26:
            # [2구간] 26화: 1~21화 원문을 One-Shot으로 요약 후(컨텍스트 캐싱), 22~26화 원문을 병합
            raw_texts_1_21 = []
            for i in range(1, 22):
                text = self.pm.load_chapter_text("완성본", i)
                raw_texts_1_21.append(f"[{i}화]\n{text}")

            # 전체를 하나의 거대한 텍스트로 결합
            combined_1_21 = "\n\n".join(raw_texts_1_21)

            # 22~26화 원문 준비
            recent_raws = []
            for i in range(22, 27):
                if i == chapter:
                    raw_text = completed_text
                else:
                    raw_text = self.pm.load_chapter_text("완성본", i)
                recent_raws.append(f"[{i}화 원문]\n{raw_text}\n")

            combined_recent_raws = "\n".join(recent_raws)

            # 요약 탭 이동 설정
            if self.is_split_mode:
                self.btn_split.blockSignals(True)
                self.btn_split.setChecked(False)
                self.btn_split.blockSignals(False)
                self.toggle_split_mode(False)

            self.left_panels[3].current_chapter = chapter
            self.right_panels[3].current_chapter = chapter
            self.pm.set_project_setting("chapter_tab_3", chapter)
            self.switch_tab(3)

            # AI 패널 초기화 및 워커 실행
            prompt_summary = self.pm.get_project_setting("prompt_summary")
            user_text = f"다음 1~21화 전체 원문을 쪼개지 말고 한 번에 요약해주세요.\n\n{combined_1_21}"

            self.ai_panel.init_session("요약", system_prompt=prompt_summary, user_text=user_text)
            self.ai_panel.pending_raw_texts = combined_recent_raws
            self.ai_panel.is_final_confirm_mode = True
            self.ai_panel.show()
            self.ai_panel.append_chat("AI", "1~21화 One-Shot 컨텍스트 캐싱 요약을 백그라운드에서 진행 중입니다. 잠시만 기다려주세요...")

            # AIGenerationWorker 사용 (Context Caching 활성화)
            summary_model = self.pm.get_project_setting("model_summary", self.pm.global_config.get("model_summary", "Gemini 3.1 Pro"))
            self._start_ai_request("요약", summary_model, use_context_caching=True)

        else:
            # [3구간] 27화 이상: 정밀 롤링 방식 (이전 요약본 + 밀려난 1개 원문) -> AI 요약 -> 최근 5개 원문 병합

            # 1. 직전 화수(N-1) 요약본 파일에서 하단의 5개 원문을 제외한 순수 요약본(1~N-6화 요약본)만 추출
            n_minus_1_summary_full = self.pm.load_chapter_text("요약", chapter - 1)
            if "--- 원문 ---" in n_minus_1_summary_full:
                prev_summary = n_minus_1_summary_full.split("--- 원문 ---")[0].strip()
            else:
                prev_summary = n_minus_1_summary_full.strip()

            # 2. N-5 원문
            shifted_raw = self.pm.load_chapter_text("완성본", chapter - 5)

            # 3. N-4 ~ N 최근 원문 5개
            recent_raws = []
            for i in range(chapter-4, chapter+1):
                if i == chapter:
                    raw_text = completed_text
                else:
                    raw_text = self.pm.load_chapter_text("완성본", i)
                recent_raws.append(f"[{i}화 원문]\n{raw_text}\n")

            combined_recent_raws = "\n".join(recent_raws)

            # 요약 탭 이동
            if self.is_split_mode:
                self.btn_split.blockSignals(True)
                self.btn_split.setChecked(False)
                self.btn_split.blockSignals(False)
                self.toggle_split_mode(False)

            self.left_panels[3].current_chapter = chapter
            self.right_panels[3].current_chapter = chapter
            self.pm.set_project_setting("chapter_tab_3", chapter)
            self.switch_tab(3)

            # AI 패널 초기화 및 AIGenerationWorker 실행
            prompt_summary = self.pm.get_project_setting("prompt_summary")
            summary_model = self.pm.get_project_setting("model_summary", self.pm.global_config.get("model_summary", "Gemini 3.1 Pro"))

            user_text = f"[이전 요약(1~{chapter-6}화)]\n{prev_summary}\n\n[추가 원문({chapter-5}화)]\n{shifted_raw}"

            self.ai_panel.init_session("요약", system_prompt=prompt_summary, user_text=user_text)
            self.ai_panel.pending_raw_texts = combined_recent_raws
            self.ai_panel.is_final_confirm_mode = True
            self.ai_panel.show()
            self.ai_panel.append_chat("AI", f"1~{chapter-5}화 롤링 요약을 백그라운드에서 진행 중입니다. 잠시만 기다려주세요...")

            self._start_ai_request("요약", summary_model)

    def apply_ai_result(self, final_text):
        if (
            self.is_working
            or getattr(self, "_ai_session_context", None)
            != self._ai_session_key(self.ai_panel.step_name)
        ):
            return
        target_panel = None
        left_p = self.left_panels[self.left_stack.currentIndex()]
        right_p = self.right_panels[self.right_stack.currentIndex()] if self.is_split_mode else None

        if left_p.step_name == self.ai_panel.step_name:
            target_panel = left_p
        elif right_p and right_p.step_name == self.ai_panel.step_name:
            target_panel = right_p

        if target_panel and target_panel.current_chapter != self.current_chapter:
            return
        if target_panel:
            if getattr(self.ai_panel, "is_final_confirm_mode", False):
                full_text = final_text + "\n\n--- 원문 ---\n\n" + getattr(self.ai_panel, "pending_raw_texts", "")
                target_panel.text_edit.setPlainText(full_text)
                target_panel.trigger_autosave()
                self.ai_panel.is_final_confirm_mode = False
                self.ai_panel.pending_raw_texts = ""
            else:
                cursor = target_panel.text_edit.textCursor()
                cursor.movePosition(cursor.MoveOperation.End)
                cursor.insertText("\n\n" + final_text)
                target_panel.text_edit.setTextCursor(cursor)
                target_panel.trigger_autosave()

        if target_panel and target_panel.step_name != "평가":
            self.hide_ai_panel()

    def perform_transition_save(self, text, side, step_name):
        from datetime import datetime
        chapter = self.current_chapter

        # 1. 내부 저장 (메인 폴더)
        self.pm.save_chapter_text(step_name, chapter, text)

        # 2. 전환 직전 텍스트 백업 (백업/전환직전 폴더)
        self.pm.save_chapter_text(step_name, chapter, text, is_backup=True, backup_type="전환직전")



    def handle_ai_feedback(self, feedback_text):
        if self.is_working:
            return
        step_name = self.ai_panel.step_name
        model_mapping = {
            "초안": "model_draft",
            "평가": "model_eval",
            "요약": "model_summary"
        }
        model_key = model_mapping.get(step_name, "model_eval")
        selected_model = self.pm.get_project_setting(model_key, self.pm.global_config.get(model_key, "Gemini 3.1 Pro"))

        self._start_ai_request(step_name, selected_model, feedback=True)

    def on_ai_finished(self, step_name, generated_text, in_tok=0, out_tok=0, *, request=None):
        if request is None or request != getattr(self, "_active_ai_request", None):
            return
        self._active_ai_request = None
        self.is_working = False
        if (self.pm.current_project, self.pm.project_path) != (request.project_name, request.project_path):
            self.update_status_bar()
            return
        self.pm.log_api_cost(request.step_name, request.model, in_tok, out_tok)
        self.pm.save_ai_response(request.step_name, request.chapter, generated_text)
        self.update_status_bar()

        if (
            self.ai_panel.isVisible()
            and self._ai_session_key(step_name) == self._ai_session_context
            and self.current_chapter == request.chapter
        ):
            if request.feedback:
                msg = "요청하신 대로 텍스트를 수정했습니다."
            elif step_name in ["완성본", "평가"]:
                msg = "작성된 내용을 확인해보세요. 수정하고 싶은 부분이 있다면 말씀해 주세요."
            else:
                msg = "작성된 초안을 확인해보세요. 수정하고 싶은 부분이 있다면 말씀해 주세요."
            self.ai_panel.update_result(generated_text, msg)

    def handle_extraction(self, is_full, start, end, fmt):
        if is_full:
            start = 1
            max_ch = 1
            # 완성본 폴더의 파일들을 보고 가장 큰 숫자를 찾음
            path = os.path.join(self.pm.project_path, "메인", "완성본")
            if os.path.exists(path):
                for f in os.listdir(path):
                    if f.endswith("화.txt"):
                        try:
                            ch = int(f.replace("화.txt", ""))
                            if ch > max_ch:
                                max_ch = ch
                        except: pass
            end = max_ch

        if start > end:
            QMessageBox.warning(self, "경고", "시작 화수가 끝 화수보다 클 수 없습니다.")
            return

        accumulated_text = []
        for i in range(start, end + 1):
            raw_text = self.pm.load_chapter_text("완성본", i)
            if raw_text.strip():
                accumulated_text.append(f"[{i}화]\n\n{raw_text}")

        if not accumulated_text:
            QMessageBox.warning(self, "경고", "추출할 내용이 없습니다.")
            return

        final_text = "\n\n".join(accumulated_text)
        project_name = self.pm.current_project
        if is_full:
            filename = f"[{project_name}_1-{end}화].{fmt}"
        else:
            filename = f"[{project_name}_{start}-{end}화].{fmt}"

        # 저장은 [scratch\작가님 힘내세요\메인] 폴더에
        export_dir = os.path.join(self.pm.workspace_dir, project_name, "메인")
        os.makedirs(export_dir, exist_ok=True)
        export_path = os.path.join(export_dir, filename)

        if fmt == "txt":
            try:
                with open(export_path, "w", encoding="utf-8") as f:
                    f.write(final_text)
                QMessageBox.information(self, "완료", f"TXT 파일 추출 성공:\n{export_path}")
            except Exception as e:
                QMessageBox.critical(self, "오류", f"TXT 저장 실패:\n{e}")
        else:
            # pdf 추출
            from PyQt6.QtGui import QTextDocument, QPdfWriter, QPageSize
            try:
                doc = QTextDocument()
                doc.setPlainText(final_text)
                pdf = QPdfWriter(export_path)
                pdf.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
                doc.print(pdf)
                QMessageBox.information(self, "완료", f"PDF 파일 추출 성공:\n{export_path}")
            except Exception as e:
                QMessageBox.critical(self, "오류", f"PDF 저장 실패:\n{e}")

    def on_ai_error(self, err_msg, *, request=None):
        if request is None or request != getattr(self, "_active_ai_request", None):
            return
        self._active_ai_request = None
        self.is_working = False
        if (self.pm.current_project, self.pm.project_path) != (request.project_name, request.project_path):
            self.update_status_bar()
            return
        if self.ai_panel.isVisible():
            self.ai_panel.update_result("", f"AI 생성 중 오류가 발생했습니다:\n{err_msg}", is_error=True)
        self.update_status_bar()
        QMessageBox.critical(self, "AI 오류", f"AI 생성 중 오류가 발생했습니다:\n{err_msg}")
