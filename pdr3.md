

---

## [추가 작업 스펙 정리] 미적용 기능 단계별 구현 계획

앞서 확인한 미적용 사항들을 효율적이고 안전하게 구현하기 위해, 4개의 작은 단계(Step)로 나누어 작업할 수 있도록 정리했습니다.

### Step 1: Claude & OpenAI 공식 라이브러리 연동 (llm_provider.py)
- [ ] `anthropic` 라이브러리 설치 및 `ClaudeProvider` 신규 구현
  - 모델명: `claude-opus-4-8`
  - `max_tokens`: 65000
  - 시스템 프롬프트(system)와 유저 메시지(messages) 분리 파싱 로직 필수 적용
- [ ] `openai` 라이브러리 설치 및 `OpenAIProvider` 신규 구현
  - 모델명: `gpt-4o`

### Step 2: 에러 통합 및 Factory 라우팅 강화 (llm_provider.py)
- [ ] `LLMProvider` 부모 클래스 혹은 유틸에 `_translate_error(e)` 헬퍼 메서드 신설 (영어 에러를 한국어로 치환)
- [ ] Gemini, Claude, OpenAI 3개의 Provider 내의 `try-except`에서 발생하는 모든 에러를 `_translate_error`를 거쳐 `RuntimeError`로 던지도록 통일
- [ ] `LLMFactory.get_provider` 고도화
  - UI가 넘겨주는 문자열(예: 'Claude 3.5 Sonnet')을 파싱해 정확한 클래스로 분기
  - `SecurityManager`에서 각 모델별 API 키를 불러와 주입하고, 키가 없으면 명시적 에러 발생

### Step 3: 마크다운 렌더링 UI 적용 (ui_components.py)
- [ ] `markdown` 라이브러리 설치
- [ ] `AIPanelWidgetBase`의 기존 텍스트 결과 영역(`QTextEdit` 또는 `QLabel`)을 `QTextBrowser`로 교체
- [ ] `update_result` 메서드에 마크다운 변환 로직 추가 (`markdown.markdown(text)`)
- [ ] 변환된 HTML 문자열을 `QTextBrowser`에 렌더링하여 가독성 개선

### Step 4: 에러 UI 팝업 안내 처리 (main.py)
- [ ] `AIGenerationWorker` 통신 중 발생하는 `RuntimeError` 처리 보강
- [ ] 에러 발생 시 UI 패널 내 텍스트 출력을 넘어, 사용자에게 명시적으로 안내되는 팝업창(`QMessageBox.critical` 등)을 띄우도록 `error` 시그널 핸들러(`on_ai_error`) 수정
