# Windows 완료 범위 대조 — 현재 HEAD 0244d82

2026-09-07. 요청한 9개 항목을 현재 소스·이미 실행된 검사·설치 근거에 대조했다. 제품 코드를 수정하거나 앱을 실행하지 않았고, 시험·빌드·설치·실제 송신을 반복하지 않았다. 서버·로컬 DB를 새로 조회하거나 관문을 변경하지 않았다.

**판정: 고정 계약과 여러 안전 장치는 완료됐지만, 요청한 범위 전체를 제품 완성으로 판정할 수는 없다.** 새로 부족한 부분은 AI 응답 기록 실패 경계, 작품 전체 백업의 제품 연결, 앱과 독립된 복구 진입점이다. 일반 작업 전체의 새 계약 상시 사용은 구현 근거와 양쪽 실사용 완료 근거를 구분해야 한다.

## 1. 기준과 검사·설치 연결

- HEAD: `0244d8263cadea893e11c1ee2e7b5dad9c0f4be4`, 브랜치 `feat/contract-handshake-closed-gate`, [Draft PR #9](https://github.com/ChocoS-yrup/WriterPad_main/pull/9). 푸시 완료, 병합 미실행.
- [현재 HEAD의 CI 34109016242](https://github.com/ChocoS-yrup/WriterPad_main/actions/runs/34109016242): **success**. 1198개 시험, 827.349초, 계약 검증·Windows 빌드·패키지 Qt 시작·EXE 해시 기록 모두 통과. 완료 시각 2026-09-07 19:15:51 KST. 이번에는 이미 끝난 실행의 결과와 로그만 내려받았다.
- 최초 CI 34107230857의 테스트 fixture 오류는 0244d82로 보완·푸시됐고 최신 CI에서 해소됐다. 이전 실패를 현재 실패로 취급하지 않는다.
- 설치본: `D:\안티그래비티\scratch\집필프로그램\작가님 힘내세요.exe`, SHA-256 `cd7ccebf125864b63ac8a9552585c44221e1086d0e6fc3e2617c6fa89a12fd89`. 현재 파일 해시도 대조했다.
- 설치 제품 소스 기준은 7f77ac0. 이후 HEAD까지 차이는 `tests/test_cloud_config.py` 하나뿐이다. 이번 감사 대상 제품 소스 12개가 설치 빌드 입력 해시와 일치했다. 기존 51개 모듈 패키지 비교 및 정상 시작·설정 화면 사용자 확인을 연결했다.
- CI EXE는 56,100,863 bytes, SHA-256 `92fd7a63cddc7a19c53d84975357897d64a1b73f334be35e136aba5d029dc06b`. 현재 개인 Staging 설치본과 동일 바이너리라고 주장하지 않는다. CI는 기기별 release 설정을 포함하지 않는다.
- 설치 일치는 ‘검토한 코드가 들어 있다’는 증거다. 아래 각 기능의 완료 판정에는 별도의 해당 검사와 실사용 근거를 연결한다.

상태 의미: **완료**는 명시한 범위의 구현·검사 근거 확보, **일부 구현**은 기능의 일부 단계만 구현/제품 연결, **미구현**은 요청한 사용자 기능의 실행 경로 없음, **확인 필요**는 구현과 일부 검사는 있으나 요청한 전체 범위의 완료 판정에 근거가 부족함이다.

## 2. 요청 항목별 판정

| 요청 항목 | 판정 | 현재 확보한 범위 / 실제 남은 부분 |
|---|---|---|
| 고정 계약 준비·송신·영수증·실제 수신 | **완료** | 빈 2권/3권·원고 순서의 두 고정 사례. 일반 작업 전체 완료로 확대하지 않음. |
| 원본·중단 기록 보존, 관문 닫힘 | **완료** | 기존 원본·두 stopped·committed 기록과 영수증 보존, 마지막 설치 후 관측에서 관문 0/15. 이번에는 라이브 상태를 재관측하지 않음. |
| 기능 커밋·새 설치·시험 카드 숨김 | **완료** | 기능·패키징·테스트 보완 커밋 모두 원격 반영, 설치·사용자 화면 확인, 현재 HEAD CI 통과. |
| AI 저장 실패 시 원고 보존 | **일부 구현** | **기존 원고 자동저장 실패 보호는 완료.** 새 AI 응답을 보여주기 전 비용/응답 이력 기록 실패까지 포괄하는 경로는 완료 근거가 없고 정적 공백을 발견함. |
| AI 취소 후 늦은 응답의 회차 귀속 | **완료** | 요청 ID·작품·회차 고정, 취소 무효화, 늦은 결과/오류 차단, 새 요청 오염 방지 및 해당 검사 통과. |
| 일반 문서·구조 작업 전체의 새 계약 자동 동기화 | **확인 필요** | 문서·구조 배치·자동 dispatcher·수신·복구 경로 및 격리 검사는 존재. 일반 작업 전체를 양쪽 앱의 실제 자동 사용으로 완료했다는 근거는 부족함. |
| 작품 전체 외부 백업·별도 환경 복원 | **일부 구현** | v1 패키지 core와 프로젝트 adapter, 별도 임시 workspace 복원·UUID/bytes·열기 가능성 검사 완료. 사용자 백업/복원 UI와 작품 전체 범위의 연결·설치본 검증은 미완. |
| 앱 시작 실패 시 데이터 접근·복구 | **미구현** | **앱과 독립된 사용자용 복구 진입점 기준.** 원고 TXT 접근과 복원 library는 있지만, 앱/Qt가 시작되지 않을 때 실행할 독립 복구 도구·안내 동선은 확인되지 않음. |
| Production 연결·최종 실사용 검증 | **일부 구현** | 설정 기반 클라우드 연결 코드와 prod 읽기 대조는 존재. 실제 prod용 구성·로그인·양쪽 사용 확인은 미실행이며 사용자 결정으로 보류. 현재 Staging 개발 완료를 막는 결함으로 취급하지 않음. |

## 3. 완료 항목의 구체적 근거

### 고정 계약·기록 보존·설치

- 구현: [contract_preparation.py](../contract_preparation.py), [reviewed_contract_sender.py](../reviewed_contract_sender.py), [contract_post_coordination_resume.py](../contract_post_coordination_resume.py), [sync_v2_store.py](../sync_v2_store.py).
- 현재 CI에서 준비 13개, reviewed sender 27개, 추가 1회 정책 30개, pull/review 조정 13개 명명된 시험을 확인했다. 서로 다른 시점의 전체 검사 수를 합산하지 않는다.
- 실제 수신 완료: [두 고정 계약 완료 보고](windows-ipad-fixed-contract-complete-2026-09-07.md). 역방향 성공은 committed / HTTP 1 / 이벤트 24 / 영수증 1. 소모된 요청을 다시 실행할 필요가 없다.
- 설치·보존: [새 설치 완료 보고](windows-release-installed-2026-09-07.md), `_evidence/windows-release-install-20260907/final-verification.json`.
- 카드 숨김: `settings_panel.py:436`의 objectName과 다음 줄의 명시적 환경변수 가시성 조건. 일반 진단은 유지된다. 이전 5개 Qt 가시성 검사와 사용자 확인을 재사용했다.

### AI 원고 자동저장 보호 — 이 부분은 이미 완료

- `project_manager.py:251`의 `save_chapter_text`: 같은 디렉터리 임시 파일에 쓰고 flush/fsync한 뒤 원고 파일을 교체한다. 실패할 때 기존 파일을 먼저 비우지 않는다.
- `mode_assistant.py:459`의 `autosave_panel`: OSError/UnicodeError에 편집 내용을 유지하고 dirty 상태와 재시도 타이머를 보존한다.
- `mode_assistant.py:495`의 `on_chapter_changed`: 저장 실패 시 다음 회차로 바꾸지 않는다. 회차는 화면 전역 선택값이 아니라 패널에 귀속된다.
- 현재 CI의 `tests/test_assistant_safety.py`에서 `test_failed_autosave_retains_dirty_text_and_retries`, `test_save_failure_prevents_chapter_switch_and_keeps_editor`, `test_fsync_failure_preserves_existing_bytes_and_cleans_temporary_file`, `test_replace_failure_preserves_existing_bytes`, `test_encoding_failure_preserves_existing_bytes` 통과.
- 위 제품 소스는 설치 입력 해시와 같다. AI 안전성 모듈의 24개 명명된 검사는 현재 HEAD CI에서 확인됐다. 이 기존 수정이나 검사를 반복할 이유가 없다.

### AI 취소와 회차 귀속 — 완료

- `assistant_runtime.py:165`의 불변 `AIRequestContext`에 요청 ID·작품명/경로·회차·단계·모델을 고정한다. Worker는 메시지를 복사하고 결과를 보내기 전 취소 상태를 확인한다.
- `assistant_workflow.py:22`에서 요청 문맥을 worker에 연결한다. `:68`의 취소는 활성 요청을 무효화해 UI 큐에 이미 들어온 응답도 제외한다.
- `assistant_workflow.py:441`은 활성 요청과 작품이 일치하는 결과만 처리하고 `request.chapter`로 저장한다. 현재 선택된 다른 회차로 귀속시키지 않는다.
- 현재 CI에서 `test_cancel_discards_real_thread_result_already_queued_for_ui`, `test_cancel_and_switch_ignores_late_result_and_error`, `test_old_result_cannot_finish_or_pollute_new_request`, `test_response_uses_original_chapter_and_selected_model`, `test_response_never_writes_into_another_project`, feedback·요약 취소 검사 통과. 일부는 실제 QThread와 가짜 API로 큐 전달을 확인한다. 실제 유료 AI 호출은 아니다.
- 설치 입력에 같은 `assistant_runtime.py`, `assistant_workflow.py`, `mode_assistant.py`, `project_manager.py`가 포함된 근거를 연결했다.

## 4. 실제 남은 부분과 완료 조건

### R1. AI 응답을 받은 뒤 부가 기록이 실패하는 경계

**새로 확인한 정적 공백이며, 위 자동저장 수정의 반복이 아니다.** `assistant_workflow.py:441`에서 활성 요청을 지운 뒤 `log_api_cost` → `save_ai_response` → 화면 결과 표시 순서로 처리한다. `project_manager.py:351`의 비용 JSON 쓰기는 I/O 오류가 전파될 수 있다. 이 경우 이후 AI 응답 저장·화면 표시까지 도달하지 않는 호출 순서다. `project_manager.py:185`의 AI 이력은 디렉터리 생성이 try 밖에 있고, 파일 쓰기 실패는 출력만 남기며 사용자가 저장 실패를 확인·복구하는 경로가 분명하지 않다.

이번에는 오류를 주입하거나 앱을 실행하지 않았다. 기존 디스크 원고가 삭제된다는 뜻도 아니다. **‘새로 생성된 응답까지 저장 오류에 안전하다’는 전체 판정은 할 수 없다는 뜻**이다.

남은 일: 이 새 경계만 격리 재현해 영향을 확정하고, 비용/이력 저장 실패가 생성된 응답의 표시·복사·재저장을 막지 않도록 오류 처리와 결과 보존 방식을 정한다. 완료 조건은 비용 기록 실패와 AI 응답 저장 실패 각각에서 원고·생성 결과·올바른 회차 귀속을 유지하고 실패를 사용자에게 알려 주는 것이다. 기존 원고 자동저장/취소 시험을 다시 처음부터 재구현하지 않는다.

### R2. 작품 전체 외부 백업을 사용 가능한 기능으로 연결

현재 코드가 없는 상태는 아니다. `project_backup_v1.py:165/:290`에 패키지 생성·복원, `project_backup_adapter_v1.py:78/:144`에 실제 identity-v1을 이용한 백업과 열 수 있는 프로젝트 복원이 있다. `project_creation_v1.py:1061`의 복구 journal 처리도 연결돼 있다.

현재 CI에서 adapter 6개 시험은 별도 임시 workspace에 UUID·부모·순서·원고 bytes·휴지통 문서를 복원하고 `prepare_open` 정상 판정, 중간 중단 복원을 확인한다. v1 패키지 시험 1개와 과거 A2 core 8개도 통과했다. 따라서 ‘백업 core만 있고 실제 프로젝트 adapter가 없다’는 오래된 설명은 정확하지 않다.

그러나 추적 제품 소스에서 `backup_project`/adapter `restore_project`를 호출하는 사용자 UI·명령 진입점을 찾지 못했다. 현재 패키지는 집필모드의 identity nodes를 대상으로 하므로 AI 모드 원고·AI 응답 이력·작품 설정 등 프로젝트 나머지 데이터까지 모두 백업한다고 할 수 없다. 기존 폴더 내 자동저장 복사본과 작품 전체 독립 백업도 서로 다르다. 오래된 `independent-project-backup-recovery.md`의 A2 `files/` 형식과 현재 v1 `workspace/<uuid>` 형식은 혼동하지 않는다.

남은 일: 사용자가 외부 위치를 선택해 백업·검증·복원할 진입점을 연결하고, ‘작품 전체’에 포함할 실제 사용자 데이터 범위를 완성한다. 기존 core·adapter를 재사용한다. 완료 조건은 원본/기존 목적지 불변, 포함 데이터 목록·해시 검증, 별도 프로필/환경에 복원 후 작품 열기까지이며 이미 통과한 합성 core 왕복을 같은 조건으로 반복하는 것이 아니다. 운영 세션·송신 대기열을 무조건 복제해 재전송시키는 복원은 목표로 삼지 않는다.

### R3. 앱이 시작되지 않을 때의 독립 복구 경로

`main.py`는 시작 시 Qt를 import한다. `--qt-import-smoke-test`도 Qt 시작 검사이며 복구 기능이 아니다. 현재 시작 모드 시험 10개는 정상 Qt 환경의 탭/문서 복원 등을 검사한다. 앱 실행 불가 상태에서 원고를 내보내는 시험은 아니다.

로컬 TXT와 백업 library가 있다는 것은 기반이다. 일반 사용자가 앱 없이 저장 위치를 확인하고 안전한 사본을 만들거나 외부 백업을 복원할 독립 실행 경로는 현재 추적 소스·패키징에서 확인되지 않았다. 일부 프로젝트 journal 복구는 정상 앱/프로젝트 초기화 경로에 의존한다.

남은 일: 주 앱 UI가 실패해도 실행 가능한 복구 도구/명령과 사용 안내를 제공한다. 데이터 원본을 수정하기 전에 사본 확보·패키지 검증을 수행하도록 하고, 앱 시작 실패를 가정한 별도 환경에서 이 경로만 확인한다. ‘원고가 TXT라 탐색기로 읽을 수 있다’는 사실을 사용자 복구 기능 완성으로 대신하지 않는다.

### R4. 일반 새 계약 자동 동기화의 양쪽 완료 범위 확정

`sync_manager.py:2607/:3082`의 구조 enqueue, `:3613` 부근의 일반 retry dispatcher, `:10289`의 atomic structure 전송, `:11351/:11446`의 이름/위치·폴더 생명주기 처리 및 문서 계약 전송이 있다. `tests/test_sync_contract_stage8.py`에는 문서·폴더 생성/이름/이동/삭제/복원/순서/중복응답/거절 검사 등이 있고 현재 CI에서 151개 명명된 시험이 통과했다. `test_writing_data`의 UI-to-queue 관련 시험도 포함돼 있다.

이 상태를 ‘일반 작업 자동 경로 미구현’으로 단정하지 않는다. 반대로 두 고정 빈 폴더 실제 성공만으로 일반 편집 전체·양쪽 재실행·충돌·오프라인까지 현재 앱에서 완료했다고 확대하지도 않는다.

남은 일: Windows와 iPad의 동일한 작업 목록에 구현·격리 검사·실기기 근거를 연결하고 실제 비어 있는 칸만 정한다. 문서 create/update/empty/delete/restore, 폴더 create/rename/move/delete/restore, 순서, 동시에 수정/중단 후 복구의 기존 증거를 재사용한다. 이름별 실제 미구현이 확인되면 그 항목만 수정한다. 관문은 현재 지시대로 닫고, 이번 감사에서 자동 송신 사용을 새로 허용하지 않는다.

### R5. prod는 개발 완료 이후의 별도 단계

사용자는 양쪽 서버의 자료가 모두 테스트용이며, Staging에서 양쪽 개발을 완료한 다음 서울 prod로 옮기기로 했다. prod는 이미 재개됐다. 읽기 대조에서 prod에 계정/작품이 없고 서버 정의 차이가 있는 것까지 확인했다. 실제 설정 변경·계정 준비·기기 연결·일반 사용 확인은 보류다. 지금 전체 테스트 자료와 계정 UUID를 반드시 이전해야 한다는 요구를 새로 만들지 않는다. 양쪽 prod 확인 전 Staging을 비활성화하지 않는다.

## 5. 다음 행동과 전달 자료

Windows에서 우선 처리할 구체 항목은 **R1 AI 응답 기록 실패 경계 → R2/R3 백업·독립 복구 연결**이다. R4는 iPad의 같은 항목별 근거와 대조해 실제 빈 범위를 정한다. 새 기능 요구를 끝없이 늘리거나 완료한 고정 계약·오프라인 검사·설치 확인을 되풀이하지 않는다.

사용자가 할 일: 이 보고서와 같은 이름의 근거 ZIP을 iPad 측에 전달하고, 같은 항목에서 iPad의 구현/검사 상태와 ‘작품 전체 백업’ 포함 범위를 대조하도록 요청한다. 앱 조작은 필요 없다. 이 감사 요청 자체로 위 남은 기능을 구현하거나 실제 관문을 열지는 않았다.

근거 위치: `D:\안티그래비티\scratch\작가님 힘내세요\_evidence\windows-completion-audit-20260907\`. `ci-current-head.json/.log`, `ci-relevant-tests.log`, `test-index.json`, `source-install-link.json`, `code-anchors.json`, `verification.json`을 보존했다. 근거 ZIP에는 선별 소스·시험·기존 설치/실수신 요약도 포함한다. 라이브 DB·원고·인증 정보·EXE는 포함하지 않는다.
