# Windows 계약 전송 공통 정책 3개 보완 결과

2026-09-06 KST. **남은 세 조건의 Windows 구현과 로컬 회귀 검증을 완료했다.** iPad 구현 보고서와 정책을 맞춘 결과이며, 아직 받지 못한 iPad 소스 diff의 독립 검증이나 실서버 계약 전송 완료를 뜻하지 않는다.

기준 HEAD는 `a3eaa8b97dc9769ad313ac3fe579d0b1443849a9`, 브랜치는 `feat/contract-handshake-closed-gate`다. 이전 C4·C5·C9 보완 위의 미커밋 작업 트리이며 새 커밋/설치 빌드는 없다. 계약 version `0.2.0`, protocol `3`, digest `416c1b99edb9bda694731dee4b25688d9d82d1f32610aa23ddfda571ec3c7670`은 유지했다.

## 1. 실제 구현

### 계약 전송마다 새 작품 상태 확인

계약 구조 `atomic_structure_commit`과 계약 원고 `document_commit`의 공통 전송 함수에서, 기존 관문·구조·인증·작품 조건을 통과한 뒤 `get_project_status`를 새로 읽는다. 인증 준비 이후, 상태 응답 수신 이후, 쓰기 RPC 구성 이후에 원래 문맥과 승인 상태를 다시 검사한다.

현재 문맥의 기존 active 기록은 새 읽기를 대신할 수 없다. 계약 전용 읽기에서는 일반 동기화의 호환 조회나 기본 active로 우회하지 않는다. 통신 실패·잘못된 응답·다른 작품·trashed/purged일 때 계약 쓰기는 시작하지 않는다. 이미 유효한 완료 영수증이 저장된 배치는 전송할 필요가 없으므로 읽기/쓰기를 반복하지 않는다.

작품 상태를 기다리는 준비 슬롯과 실제 쓰기를 시작한 슬롯을 구분한다. 같은 배치에 복구 알림이 반복돼도 진행 중인 상태 조회와 쓰기를 중복으로 시작하지 않는다. 네트워크 대기는 계약 잠금 밖, 기존 worker에서 진행한다.

### 상태 응답의 작품 ID 검사

응답은 객체이고 `project_id`는 유효한 UUID 문자열이어야 한다. 요청 작품 UUID와 일치하며 `state=active`인 경우만 다음 쓰기 준비로 진행한다. 누락·다른 UUID·숫자형 ID·미지 상태를 승인하지 않는다. 이는 핸드셰이크의 C4 검사에 더해 작품 상태 응답에도 적용한 조건이다.

일반 동기화의 기존 `get_project_status` 호환 경로는 변경하지 않았다. 계약 전용 전송 함수의 검사를 통해 정책을 분리했다.

### 차단 후 해소된 대기열도 이전 승인 무효화

SQLite의 기존 `sync_operation_events` 이력에서 blocked/conflict 진입과 그 상태에서 벗어난 사건을 읽어 작품별 이력 지문을 계산한다. 원고 작업과 구조 작업을 모두 포함한다. 이 지문을 전송 준비 때 잡고 전송 직전까지 다시 비교한다.

따라서 상태가 허용 → 차단 → 허용으로 돌아와 현재 차단 건수가 0이어도, 옛 준비 요청은 차단된다. 같은 저장 배치를 새 문맥으로 다시 준비하면 재검사 후 보낼 수 있다. 보통의 로컬 enqueue/attempt/retry는 차단 이력을 만들지 않으므로 정상 저장이 스스로 승인을 무효화하지 않는다.

이력은 저장소의 실제 차단/충돌/해소 경로에서 기록된 사건을 사용한다. 테스트 전용 스위치를 추가하지 않았다. 다른 저장소 인스턴스로 읽어도 전이를 확인한다. 로컬 DB 스키마·계약 wire 형식은 변경하지 않았고, 이 지문을 batch payload나 계약 digest에 넣지 않는다.

## 2. 수정 전후와 회귀 시험

| 조건 | 수정 전 | 수정 후 |
|---|---|---|
| 실제 계약 쓰기 전 작품 상태 조회 | 기존 상태 재사용 가능 | 새 상태 읽기 → 최종 검사 → 쓰기 순서 확인 |
| 상태 ID 누락·불일치·잘못된 형 | active 수락 가능 | 상태 읽기 1회, 쓰기 0회, 우회 조회 없음 |
| 상태 통신 실패·누락 작품·비활성 | 새 읽기를 강제하지 않음 | 쓰기 0회, 배치 보존 |
| 상태 조회 중 관문 닫힘 | 새 상태 조회 경계가 없었음 | 읽기 응답 뒤 쓰기 0회 |
| 조회/쓰기 구성 중 blocked 또는 conflict 후 해소 | 모의 쓰기 1회 | 모의 쓰기 0회, 새 준비에서 같은 배치로 복구 가능 |
| 정상 로컬 enqueue | 정상 전송 가능 | 그대로 전송 가능 |
| 상태 읽기 중 복구 알림 반복 | 전용 준비 슬롯 없음 | 읽기 1회 + 쓰기 1회만 실행 |
| 이미 저장된 완료 영수증 | 재사용 | 추가 읽기/쓰기 없이 재사용 |

`tests/test_contract_send_policy.py`의 신규 10개 시험은 모두 안전 요구를 통과 조건으로 한다. 최초 8개 재현 시험에서는 하위 조건 18개 실패를 확인했고, 수정 후 통과했다. 구조 작업의 차단/해소 이력과 완료 영수증 재사용 검사를 추가했다.

이전 정상 전송 테스트의 대역에도 `get_project_status` 응답을 추가했다. 읽기 호출과 쓰기 호출을 구분하도록 기대 호출 목록을 갱신했으며 쓰기 0회 요구는 유지했다. 이미 전송을 시작한 요청의 유효한 영수증 보존, 이전 계정/작품 완료 표시 억제, ID/payload 보존도 기존 회귀로 재확인했다.

## 3. 최종 검사

```powershell
python -B -X faulthandler -m unittest tests.test_contract_send_policy tests.test_contract_followup tests.test_network_recovery tests.test_handshake_stability tests.test_sync_contract_stage8 tests.test_sync_state tests.test_sync_v2 tests.test_sync_resilience tests.test_sync_diagnostics tests.test_module_boundaries tests.test_startup_mode tests.test_shutdown_budget tests.test_writing_autosave tests.test_assistant_safety tests.test_app_config_paths tests.test_editor_view_state tests.test_remote_editor_cursor tests.test_typewriter_mode -q
```

**764개 실행 / 통과 763 / 실패 0 / 건너뜀 1**, 104.859초. 건너뜀은 기존 Windows 심볼릭 링크 권한 관련 시험이며 신규 10개에는 건너뜀이 없다. 임시 디렉터리 정리 ResourceWarning이 있었지만 테스트 실패는 없었다. 기존 집필 커서·여백·자동저장·AI 안전성 검사도 포함했다.

- 최초 재현 로그: `_evidence/contract-send-policy-before.txt`
- 최종 전체 로그: `_evidence/contract-send-policy-full-suite.txt`
- 최종 소스 식별: `_evidence/windows-contract-send-policy-20260906/verification.json`
- `git diff --check` 통과. 부분 실행 수치는 전체 검사와 중복되므로 합산하지 않는다.

이번 추가 변경은 `handshake_lifecycle.py`, `sync_v2_store.py`, 신규 `tests/test_contract_send_policy.py` 및 기존 전송 관련 테스트 3개(`test_contract_followup.py`, `test_handshake_stability.py`, `test_sync_contract_stage8.py`)다. 기존 미커밋 AI·집필 작업과 이전 C4·C5·C9 수정은 보존했다.

## 4. 변경하지 않은 사항과 다음 행동

실제 작품 관문·서버 설정·allowlist·계약 파일·digest·프로젝트 mode/epoch는 변경하지 않았다. 실제 서버 쓰기도 없었다. 관문 및 응답 조작은 격리된 테스트 저장소/대역에서만 했다. 커밋·푸시·재빌드·설치는 하지 않았으므로 **설치된 실행 파일에는 이번 수정이 반영되지 않았다.**

작품/구조가 이미 비활성·차단으로 알려져 있다면 기존 복원/기준 재조회 경로로 먼저 그 조건을 해소해야 한다. 이번 상태 읽기가 기존 차단 조건을 무조건 우회해 전송을 시작하게 하지는 않는다. 실제 쓰기 시점에 다른 기기가 바꾼 서버 상태는 서버의 트랜잭션 검사로 보장해야 하며, 그 실서버 증거는 아직 별도 검증 대상이다.

**사용자가 지금 할 일:** 이 문서를 iPad 측에 전달하고, 요청한 iPad 제품·테스트 diff와 검증 JSON을 받으면 이 작업에 첨부한다. 해당 소스 대조가 끝난 뒤 양쪽 커밋/빌드를 고정하고 닫힌 관문에서 설치본·복구 진단을 검증한다. 현재 앱에서 조작하거나 통신 장애 시험을 반복할 필요는 없다. 실제 관문은 계속 닫아 둔다.
