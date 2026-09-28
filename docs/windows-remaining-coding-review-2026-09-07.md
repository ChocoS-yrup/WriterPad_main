# iPad 회신 대조 후 Windows 잔여 코딩 결과

2026-09-07. 범위는 Windows 잔여 코딩과 격리 검증이다. 앱 설치·실제 통신·관문 변경·prod 전환·커밋·푸시·병합은 하지 않았다. 기존 원본·stopped·성공·영수증 기록을 읽거나 수정하는 시험도 수행하지 않았다.

## 입력 대조

- Windows HEAD와 브랜치는 `0244d8263cadea893e11c1ee2e7b5dad9c0f4be4`, `feat/contract-handshake-closed-gate`다. 시작 시 추적 변경은 없었으며 기존 untracked 자료를 보존했다.
- 받은 파일은 `ipad-remaining-development-handoff-20260907-v2.zip`, SHA-256 `3405b81d4a4a28eb9c5076abf32914d63cb1d5b3850972f592f31827a492d01a`다.
- ZIP CRC, manifest 8개 파일의 크기·SHA-256, 경로 이탈·중복 부재를 확인했다. 내부 소스 보존 ZIP의 CRC도 확인했다. 별도 첨부 md는 ZIP 안 md와 bytes가 일치한다. 첨부 스크립트·Swift 패치는 실행하거나 적용하지 않았다.
- iPad 검토는 Windows 발신 ZIP23 `8a2846bb…6f3f`와 같은 HEAD를 기준으로 한다. iPad가 제출 CI metadata를 대조한 결과와 현재 원격 CI를 직접 조회한 결과는 구분했다. 이번에 GitHub·기기·서버를 재조회하지 않았다.
- iPad 백업 우선 구현·XCTest·Release 준비 지시는 iPad 작업 인계다. Windows의 승인 범위에 Swift 수정·앱 설치·배포를 추가하지 않았다. 양쪽 대조 결과 Windows R1→R2/R3의 범위가 일치한다.

## 한정된 완료 조건과 결과

| 항목 | 대조 결과 | 이번 결과 |
|---|---|---|
| 기존 원고 자동저장·취소/늦은 응답 귀속·두 고정 계약 | 양쪽이 완료 범위를 인정 | 기존 구현 유지. 계약/설치 재실행 없음 |
| R1 새 AI 응답의 비용·이력 저장 실패 | Windows의 별도 결함으로 합의 | 재현→수정→오류 주입/복사/재저장/원래 회차/종료 보호 검증 완료 |
| R2 작품 백업 사용자 기능과 Windows 자료 범위 | 기존 v1 core/adapter 재사용, Windows 부가 자료는 별도 범위 | 정상 identity-v1 작품의 UI 생성·검증·독립 복원, 저장된 Windows 사용자 자료 포함 검증 완료 |
| R3 Qt와 독립된 복구 | Windows 담당 | Python 표준 라이브러리 소스 도구와 콘솔 진입점. Qt 없는 실행에서 사본 보존·검증·복원 확인 |
| R4 일반 새 계약 동기화 | Windows 구현/검사는 있음. iPad 일반 경로 확장 필요 | 아래 작업표 대조 완료. 새 Windows 결함이 특정되지 않아 sync 코드는 수정·재시험하지 않음 |
| R5 prod | 양쪽 Staging 개발 완료 뒤 별도 단계 | 계속 보류 |

이 표의 R1~R3 완료는 **소스와 격리 환경** 기준이다. 현재 설치본의 기능 완료나 실제 Windows↔iPad 전체 사용 완료로 확대하지 않는다. R2는 명시한 저장 영역을 대상으로 하며 legacy 자동 승격·임의 사용자 파일/전역 설정 전체 복제·iPad의 Windows 추가 자료 직접 복원은 포함하지 않는다.

## R1 변경

수정 전 합성 호출에서 비용 기록 OSError가 전파되고 응답 이력 저장·화면 표시가 모두 호출되지 않음을 확인했다. 실제 원고 삭제가 발생했다는 뜻은 아니다.

`assistant_workflow.py`에서 비용과 응답 이력을 독립 처리한다. 이력 저장 오류를 숨기지 않고 원래 요청 문맥과 생성 결과를 `ai_response_recovery.py`의 별도 창에 보존한다. 전체 복사·다른 파일로 저장을 제공하며 취소/실패 시 결과를 유지한다. 새 회차·새 요청이 이전 실패 결과를 덮지 않는다.

`project_manager.py`에서 AI 이력과 비용 파일은 임시 파일→flush/fsync→교체로 기록한다. 잘못된 기존 비용 JSON을 빈 이력으로 덮지 않는다. AI 이력 이름 충돌을 피한다. 비용 오류가 나도 새 AI 호출이나 비용 기록 재시도를 자동 수행하지 않는다.

`main.py`와 `mode_assistant.py`는 미저장 AI 응답 확인을 종료 시 설정 쓰기보다 먼저 수행한다. 같은 디스크 오류로 설정 쓰기도 실패할 때 새 응답 확인에 도달하지 못하는 경계를 막는다. 기존 원고 자동저장 알고리즘이나 취소 문맥 구현은 다시 만들지 않았다.

## R2/R3 변경

- `project_archive.py`: 공통 v1을 수정하지 않는 Windows 바깥 묶음. 파일 목록·크기·SHA-256, 복사 전후 변화, 원고/identity 일치, 경로 이탈·junction, 기존 목적지 충돌을 검사한다. 사적인 임시 디렉터리에서 완성한 뒤 새 목적지에 공개한다.
- `project_archive_dialog.py`, `project_dialogs.py`: 프로젝트 선택 화면의 **작품 백업·복구**. 파일 선택 취소는 무작업이며, 백그라운드 작업 중 중복 실행·창 파괴를 막는다. 외부 목적지 공개가 성공하기 전에는 완료로 표시하지 않는다.
- `writerpad_recovery.py`, `writerpad-recovery.cmd`: 주 앱·Qt·로그인 없이 위치 조회, 자료 사본 보존, 검증, 새 독립 루트 복원. 손상 identity는 자료 보존 경로에서 bytes 그대로 남긴다. 이 경우 `preserved_only`이며 구조 복구 완료로 표시하지 않는다.
- 정상 복원은 기존 adapter의 UUID·부모·순서를 유지하고, AI 원고·응답 이력·설정·비용·휴지통 원위치·기존 백업 사본을 더한다. 임시 별도 작업공간에서 `prepare_open=ok`와 실제 detached `WritingProjectManager`의 원고 읽기를 확인했다. 인증·송신 큐·소모된 검토 요청은 복원하지 않는다.

포함/제외 목록과 사용법은 [사용 안내](windows-local-backup-recovery-guide-2026-09-07.md)를 따른다. 독립 복구 ZIP은 Python 소스 묶음이며 EXE 빌드나 설치 패키지가 아니다. Python 3.11이 동작해야 한다. 실제 손상 원본·기기 데이터로 시험하지 않았다.

## R4 동일 작업표

Windows 근거는 현재 소스와 기존 제출 CI 34109016242의 stage8 151개·writing_data 41개 등이다. 아래 이름은 기존 검사 연결이며 이번 실행 결과가 아니다. iPad 칸은 받은 인계/감사 검토의 보고 범위다. 전체 iPad 저장소를 새로 검사한 것은 아니다.

| 작업 | Windows 구현·기존 격리 근거 | iPad 회신과 남은 실사용 근거 |
|---|---|---|
| 문서 생성 | `sync_v2_store` enqueue create, stage8 `test_contract_document_commit_uses_exact_wire_and_applies_complete_result` | 일반 RPC는 기존 `commit_document`; 새 일반 `document_commit` 연결 잔여. 양쪽 일반 생성 실사용 미확인 |
| 문서 수정 | enqueue update 분기, 문서 wire/response 검증 | 새 일반 경로 연결 잔여. 모든 일반 수정의 양쪽 완료 근거는 미확인 |
| 빈 본문 | 위 정확한 wire 검사에서 빈 내용 create, 문서 conformance vectors | 새 일반 빈 본문 경로의 양쪽 실사용 미확인 |
| 문서 삭제 | enqueue delete 분기·문서 계약 sender | iPad 새 계약 소속 연결 잔여. 문서 삭제 한 칸의 양쪽 완료를 확대 판정하지 않음 |
| 문서 복원 | enqueue restore 분기·문서 계약 sender | iPad 연결 잔여. 문서 복원 한 칸의 양쪽 완료 미확인 |
| 폴더 생성 | stage8 `test_folder_create_delete_restore_are_atomic_lifecycle_batches` | 현재 새 폴더 1개+순서 1개 제한 경로. 빈 2권/3권 두 고정 사례만 양쪽 완료 |
| 폴더 이름 변경 | `test_contract_folder_rename_and_order_share_one_atomic_batch` | 일반 mutation 연결 잔여. 일반 실사용 미확인 |
| 폴더 이동 | `test_combined_rename_move_supersedes_predecessor_once` | 일반 연결 잔여. 일반 실사용 미확인 |
| 폴더 삭제 | lifecycle·nonempty rollback/commit 검사 | 일반 연결 잔여. 일반 실사용 미확인 |
| 폴더 복원 | lifecycle·nonempty restore 검사 | 일반 연결 잔여. 일반 실사용 미확인 |
| 순서 | `test_contract_ui_tree_order_queues_atomic_batch_not_hidden_document`, writing_data UI-to-queue | 두 고정 계약의 순서 근거는 유지. 임의 작업 전체로 확대하지 않음 |
| 충돌·rebase | `test_intent_is_immutable_and_rebase_creates_successor`, 거절/부분응답 검사 | 자동 dispatcher/사용자 충돌 해결 연결 잔여. 모든 동시 편집의 양쪽 실사용 근거 미확인 |
| 오프라인 | `test_repeated_offline_tree_order_supersedes_without_mutating_original` | 새 일반 자동 경로 연결 잔여. 실제 양쪽 오프라인 수렴은 별도 |
| 재시작·응답 유실 | `test_restart_recovery_is_append_only_and_reuses_operation_id`, `test_contract_document_response_loss_reuses_recorded_result` | 새 일반 dispatcher와 재시작/불확정 응답 연결 잔여. 실제 양쪽 일반 작업 확인은 별도 |

관문을 열지 않은 상태에서 새 Windows 결함을 특정할 근거가 없으므로 이 빈 실사용 칸을 채우기 위한 통신이나 포괄 재시험은 하지 않았다. iPad 계약 lock 0.2.0과 서버 검증기의 0.3.0 불일치는 보고된 의존성으로 남긴다. pin 상향·검사 완화·서버 변경은 하지 않았다.

## 실행한 검증과 보존 근거

- 새 실패 경계·확장 자료/복구·UI 검사 21개를 추가했다.
- 최종 묶음: 새 21개 + 변경한 프로젝트 선택 화면의 기존 1개 + 종료 보호에 영향을 받는 기존 1개 = **23/23 통과**, 실패/오류/skip 0.
- R1 최초 수정 뒤 기존 AI 안전성 24개도 변경 영향 검사로 24/24 통과했다. 최종 묶음의 기존 종료 검사 1개와 겹치므로 합산하지 않는다. 종료 보호를 추가한 뒤에는 해당 기존 검사와 새 경계만 다시 확인했다.
- 기존 v1 core/adapter, 고정 계약, 전체 CI 1198개, 앱 빌드·설치·실기기 검사는 반복하지 않았다.
- 새 복구 CLI는 `python -S`로 Qt와 사이트 패키지가 없는 별도 프로세스에서 실행했다. 새 원고/폴더/journal은 전부 합성 임시 작업공간 안에서만 만들었다. 실제 계약 회차나 운영 기록은 만들지 않았다.
- `git diff --check` 통과. HEAD는 그대로이며 변경은 미커밋 상태다. 기존 문서/증거 일괄 스테이징 없음.

근거 폴더: `_evidence/ipad-remaining-development-review-20260907/`. `verification.json`, `r1-before.json`, `final-targeted-tests.json/.log` 및 최종 소스 해시/전달 패키지 검증 기록을 보존한다. 관문 “닫힘”은 기존 마지막 관측을 유지한다는 뜻이며 새 라이브 관측이라고 보고하지 않는다.

## 사용자 다음 행동

1. **지금 앱 조작은 필요 없다.** 현재 설치본과 Staging을 유지한다. 설치/실통신을 위한 새 승인 요청은 이번 범위에 넣지 않는다.
2. iPad 담당자에게 전달할 때는 `windows-remaining-coding-review-20260907.zip` 한 묶음을 사용하면 된다. Windows R1~R3의 소스·격리 검증 결과와 별도 Windows 백업 범위를 참고하고, iPad의 백업 생성·시작 실패 처리·일반 새 계약 연결을 계속하면 된다. 단순 수신 확인을 위한 추가 왕복은 필요 없다. 이번 작업에서 iPad에 직접 전송하지 않았다.
3. 양쪽 개발이 끝난 뒤에만 구체적인 설치/일반 작업 Staging 실사용 범위를 따로 정한다. 지금 설치·실제 송신·관문 개방·prod 전환을 실행하지 않는다.
