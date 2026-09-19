# Windows–iPad C1–C12 최종 대조 회신

검토일: 2026-09-06 KST. 코드와 보관된 검증 자료를 읽고, Windows에서는 격리된 임시 저장소와 모의 HTTP로 추가 검사했다.

**판정: 핵심 수명주기 규칙은 상당 부분 일치하지만, C1–C12 전체 일치 또는 실서버 계약 전송 시험 준비 완료로 확정할 수 없다. Windows에서 C5·C9의 남은 결함을 재현했고, C4 응답 형식 검증에도 차이가 있다. 실제 관문은 계속 닫아 두고 아래 선행 조건을 먼저 충족해야 한다.**

## 1. 대조 기준과 증거의 범위

| 구분 | 기준 |
|---|---|
| Windows 저장소·브랜치 | `ChocoS-yrup/WriterPad_main` / `feat/contract-handshake-closed-gate` |
| Windows 현재 HEAD | `a3eaa8b97dc9769ad313ac3fe579d0b1443849a9` |
| Windows 안정화 코드 커밋 | `d92d26a92f1e5e1b2ada690853e33a9103796078` |
| iPad 저장소·브랜치 | `ChocoS-yrup/Writerpad` / `codex/ipad-unified-contract-canary-integration` |
| iPad 대조 커밋 | `bb40d22164f34371f9ad7f70b9cddf208a692f83` |
| 양쪽 계약 pin | version `0.2.0`, protocol `3` |
| canonical digest | `416c1b99edb9bda694731dee4b25688d9d82d1f32610aa23ddfda571ec3c7670` |
| 기존 실사용 검증 환경 | WriterPad Staging. 이번에는 실서버 설정을 재조회하거나 변경하지 않음 |

Windows의 기존 미커밋 수정 `mode_writing.py`, `tests/test_editor_view_state.py`, `tests/test_sync_state.py`, `tests/test_remote_editor_cursor.py`를 보존했다. 커서·여백 수정은 이번 핸드셰이크 대조에서 변경하지 않았다. AI 코드도 변경하지 않았다.

iPad는 기존 사전 점검 문서가 아니라 `bb40d22`의 `Docs/ipad-handshake-stability-implementation-2026-09-06.md`, 검증 JSON, 실제 Swift 소스와 테스트를 대조했다. 부모 커밋과 `bb40d22` 사이 `WriterPad`·`WriterPadTests` diff의 SHA-256이 보고서의 `d55c4dfc9aac9a8ca6fce676b90d5a2327a8b509a02374c14bc89a44bad4f609`와 일치했다. 따라서 보관된 시험 요약의 소스 상태와 이 커밋을 연결할 수 있다.

iPad 검증 JSON은 **총 946개 / 통과 945개 / 실패 0개 / 건너뜀 1개**다. 실행은 07:22:37–07:25:40 KST, `Scripts/run_tests.sh`, iPad Pro 13-inch (M5) Simulator 26.5였다. 이 Windows 환경에서 Xcode 시험을 재실행하거나 원본 xcresult 전체를 직접 검증한 것은 아니다.

사용자가 이후 확인한 실기기 결과인 오프라인 저장 → 자동 동기화 복구 → 재실행 후 원고 보존도 반영했다. 이전 문서의 “실기기 미검증”을 그대로 현재 상태로 인용하지 않는다. 다만 그 사용자 확인만으로 계약 RPC 전송·서버 멱등성·모든 계정/백그라운드 경계 시험까지 완료됐다고 확대하지 않는다.

## 2. C1–C12 대조표

‘일치’는 검토한 코드·회귀 시험 범위의 의미다. 실서버 계약 쓰기 성공을 뜻하지 않는다.

| 규칙 | 판정 | 대조 결과 |
|---|---|---|
| C1 사용자·작품·연결 세대에 귀속 | 일치 | Windows는 저장소/클라이언트/작품/사용자와 무효화 세대로 문맥을 구분한다. iPad는 account/local/server project, 인증·binding epoch, pin을 사용한다. 같은 계정 재로그인도 새 인증 수명이다. |
| C2 캐시·시도·재시도 수명 | 일치 | 로그아웃·작품/연결 변경에서 캐시와 재시도/완료 억제 상태를 정리한다. 이전 실제 호출의 슬롯은 반환 때까지 유지하고 결과를 무효화한다. 디스크의 과거 성공 시각만으로 재시작 후 전송하지 않는다. |
| C3 이전 응답 무시 | 일치 | 성공·실패·unsupported를 요청 세대와 비교한다. iPad의 합류 요청 및 늦은 계약 거절에도 세대 검사가 추가됐다. |
| C4 응답 형식·계약 pin | **차이 있음** | 필수 3개 필드 누락 거절과 version/digest/protocol pin은 일치한다. Windows는 project_id 누락, 중복/0 포함 protocol 목록, 중복 capability를 허용하지만 iPad는 거절한다. 아래 R3 참고. |
| C5 일시적 실패 분류 | **Windows 수정 필요** | timeout/연결 오류의 재시도는 일치한다. 다만 현재 Windows PostgREST SDK를 거친 일부 JSON 429/5xx 오류에서 HTTP 상태가 소실되어 영구 조회 시도로 기록된다. iPad는 계약 HTTP 응답 상태를 직접 보존한다. 아래 R1 참고. |
| C6 제한된 지연 재시도 | 일치, C5에 종속 | 핸드셰이크 지연은 양쪽 모두 2→4→8→16→32→60초, 이후 60초 상한이다. 실패가 C5에서 일시적 오류로 분류돼야 이 경로에 들어간다. 실제 전체 복구 시간의 60초 상한은 아니다. |
| C7 중복 억제 | 일치, 구현 방식 다름 | Windows는 복원 SDK 클라이언트를 분리하고, iPad는 실제 SDK 인증 교환을 직렬화한다. 핸드셰이크·pull·같은 배치/작품 sender의 동시 중복을 억제한다. 모바일 실기기의 모든 중단 경계를 이번에 독립 재검증한 것은 아니다. |
| C8 집필 비차단·복구 읽기 | 일치, 범위 제한 | 로컬 집필/저장을 네트워크 대기와 분리하고, pending 원고가 있어도 복구용 기준 조회가 가능하다. 양쪽 일반 동기화 복구 증거가 있으나 계약 배치의 자동 복구 증거로 대체할 수 없다. |
| C9 전송 직전 재검사 | **전체 일치 보류** | gate/인증/작품/핸드셰이크 변경 검사는 양쪽에 있다. Windows 최종 경계에서 구조 차단 상태 재검사가 빠진 경우를 재현했다. iPad의 구조 기준/작품 상태 변경을 포함한 추가 경계 시험도 필요하다. 아래 R2 참고. |
| C10 차단 중 불변 요청 보존 | 일치, iPad 지원 범위 한정 | 보관된 batch/operation ID·payload를 닫힌 관문 때문에 재생성하지 않는다. iPad canary queue도 claim 후 차단 시 원래 JSON을 유지한다. iPad의 미구현 작업 종류까지 검증된 것은 아니다. |
| C11 시작·불명·적용 구분 | 일치, 실서버 미검증 | 로컬 시작 예약과 서버 적용을 구분한다. 검증한 늦은 영수증은 원래 저장소에 반영하고 새 화면 완료 표시를 억제한다. 응답 유실 시 같은 요청으로 멱등 처리를 사용한다. 실제 계약 서버 영수증은 이번 증거에 없다. |
| C12 자동 관문 개방 금지 | 일치 | 자동 조회 성공은 관문을 열지 않는다. 명시적 개방에도 새 조회가 필요하고, 대기 중 닫힘이 늦은 성공보다 우선한다. 진행 중인 조회 때문에 즉시 열지 못할 수 있으나 닫힘을 유지한다. |

주요 소스: Windows `handshake_lifecycle.py`, `network_recovery.py`, `sync_manager.py`, `sync_contract.py`; iPad `SyncV2Handshake.swift`, `SupabaseAuthService.swift`, `SupabaseProjectBindingService.swift`, `SyncV2ContractStructure.swift`, `SyncV2Store.swift`, `SyncSettingsView.swift`.

## 3. 남은 결함과 차이

### R1. Windows: SDK 오류 변환 후 일시적 HTTP 실패가 재시도를 멈춤

- 위치: `handshake_lifecycle.py:52` 오류 분류, `:147` 조회 실패 처리. 설치된 `postgrest 2.31.0`의 `SyncRPCFilterRequestBuilder.execute`에서 JSON 오류를 `APIError`로 변환하는 경로.
- 재현 환경: `supabase 2.31.0`, `postgrest 2.31.0`, `httpx 0.28.1`. 저장소 요구사항의 Supabase 버전과 일치한다.
- 실제 네트워크 함수를 대역으로 바꾸고 JSON 형식의 429/500/503 응답을 현재 SDK 변환에 통과시켰다. 세 경우 모두 상태 속성이 없는 `APIError`가 됐고 Windows가 재시도 불가로 판정했다.
- 같은 작품의 최초 503 이후 성공 응답을 준비하고 가상 시각을 120초 진행시킨 뒤 복구 조회를 20회 요청해도 호출 수는 **1→1**, 유효한 핸드셰이크는 **없음**이었다.
- 필요한 보완: HTTP 상태와 명시적 서버 거절 코드를 함께 보존해 분류할 것. 429/일시적 5xx는 재시도하되 확정된 인증·권한·계약 거절은 반복 통신 재시도로 바꾸지 않을 것. 단순 TimeoutError 대역 외에 **실제 SDK 오류 변환을 거치는** 회귀 시험이 필요하다.
- iPad는 `SyncV2ContractHTTPClient`와 `LiveSyncV2HandshakeTransport.classify`에서 이 HTTP 상태 보존을 구현했다.

### R2. Windows: 전송 준비 중 구조가 차단돼도 마지막 경계를 통과

- 위치: `handshake_lifecycle.py:242` `_check_contract_dispatch`; `:272` `_send_contract_request`; `sync_manager.py:10153` `_process_contract_structure_batch`.
- 일반 큐 진입점은 `sync_manager.py:3577`에서 구조 기준을 확인한다. 그러나 최종 `_check_contract_dispatch`는 `_uses_contract_structure`를 확인할 뿐 `_structure_authority_allows_dispatch`를 다시 검사하지 않는다.
- 재현: 격리된 작품에서 유효한 핸드셰이크와 열린 시험 관문으로 배치 생성 → 인증 준비 대역에서 `_block_structure_authority` 호출 → 실제 배치 처리 함수 계속 실행.
- 결과: 구조 전송 허용 **False**인데 모의 transport `execute`가 **1회** 호출됐다. ID/payload는 보존됐다. 실제 서버로 보내지는 않았다.
- 이는 통제된 경계 재현이다. 실제 사용자 세션에서 같은 순서가 발생했다거나 서버 데이터가 손상됐다는 증거는 아니다.
- 필요한 보완: 전송 시작 예약까지 구조 기준·작품 상태의 유효성을 보장하고, 대기 중 UNKNOWN/BLOCKED/비활성 작품 상태가 되면 아직 시작하지 않은 쓰기를 0회로 유지하는 회귀 시험을 추가할 것. 이미 시작한 요청의 영수증 처리 규칙은 유지할 것.
- **iPad도 추가 확인 필요:** `SyncV2ContractStructureSender.sendNext`의 마지막 authorize는 gate/auth/binding/handshake/scene/global-sync 세대를 검사한다. `SyncV2ProjectUploadPullCoordinator.beginUploadDrain`은 pull/upload permit 중복을 막지만 그 자체로 `lastPullSucceeded`나 blocked phase를 승인 조건으로 검사하지 않는다. 최신 경계 테스트의 6개 변경은 gate/auth/binding/handshake/scene/global-sync이며 구조 기준 차단과 작품 서버 상태 변경은 포함하지 않는다. 이 추가 경계의 0회 시험 또는 해당 상태가 발생할 수 없음을 보장하는 다른 경로의 근거를 회신받기 전에는 C9 전체 일치로 확정하지 않는다. Swift 실행 재현은 이번 환경에서 하지 않았다.

### R3. 응답 형식의 거절 범위와 문서 갱신

| 입력 | Windows 현재 모의 검사 | iPad bb40d22 소스 |
|---|---|---|
| project_id 없음 | supported 승인 | Decodable 필수 UUID라 거절 |
| supported_protocol_versions = [3, 3] | 승인 | 중복 거절 |
| supported_protocol_versions = [0, 3] | 승인 | 0 이하 거절 |
| 필수 capability를 포함하되 중복 항목 추가 | 승인 | 중복 거절 |
| contract_version / canonical_contract_sha256 / supported_protocol_versions 누락 | 거절 | 거절 |

앞의 4개는 Windows 임시 검사로 직접 확인했고 iPad는 `readHandshakeCompatibility` 및 응답 타입을 확인했다. 정상 서버 응답에서 문제가 발생했다는 뜻은 아니다. 요청 작품을 확인하는 project_id를 필수로 하고, 양쪽 모두 양의 정수·중복 없는 목록을 받도록 맞추는 것을 제안한다. 이 정책 합의와 회귀 검사는 계약 파일/digest를 바꾸는 작업과 분리한다.

iPad 저장소의 `sync-contract/handshake-envelope.md`에는 아직 “Windows가 필수 3개 필드 누락을 통과시킨다”는 **수정 전 설명**이 남아 있다. Windows는 이미 이를 거절하므로 문서 정정 대상이다. `bb40d22`의 새 구현 보고서와 이 오래된 설명을 혼용하면 안 된다.

### R4. 계약 쓰기 지원 범위와 자동 전송이 다름

- Windows에는 `atomic_structure_commit`과 계약 `document_commit`의 저장 배치 처리 및 자동 큐 배출 경로가 있다. 이것이 모든 원고/폴더 작업의 계약 전환 완료를 뜻하지는 않는다.
- iPad 계약 배치 생성은 **새 폴더 1개 + 해당 부모의 tree_order 1개**만 지원한다. 부모/순서의 기존 서버 revision이 필요하다. 계약 sender는 Debug 설정 화면의 **수동 1배치 전송**이다.
- iPad의 계약 원고 `document_commit`, 이름 변경·이동·삭제·복원·복합 구조 변경 연결은 아직 범위 밖이다. 일반 원고 동기화는 기존 경로다.
- 따라서 지금 합의 가능한 최초 공동 계약 시험은 작은 폴더 생성 canary이며, 양쪽 원고의 계약 자동 동기화 전체 시험은 아니다. 폴더 이동 등의 범위를 이번 검토에서 확대하지 않았다.

### R5. iPad의 30초~2분 복구 관측

4회 중 1회의 지연과 복구 후 보존 성공은 사용자 실기기 관측으로 기록한다. 이것만으로 결함이라고 단정하거나 정상 시간으로 확정하지 않는다.

양쪽의 2→60초 규칙은 핸드셰이크 재시도 대기다. iPad에는 별도로 인증 복원 12초, 인증 갱신 재시도 30초, pull 15초, 일반 동기화 주기 90초 등의 설정이 있다. 진행 중인 물리 요청이 끝날 때까지 슬롯도 유지한다. 이 수치를 더해 이번 지연의 원인이라고 주장할 수는 없다.

추가 실기기 자료는 **통신 복구 시각 → 인증 준비 완료 → 핸드셰이크 요청/응답 → 기준 pull 완료 → 대기 배치 처리 → 완료 표시**의 시각, 앱 전경/배경 상태, 재시도 횟수만 있으면 된다. 토큰·이메일·원고 본문은 필요 없다. 네트워크가 회복됐는데 자동 조회가 다시 시작되지 않는 경우와, 기한/진행 중 호출을 기다리는 경우를 구분해야 한다.

## 4. 계약 전송 시험의 선행 조건

다음은 향후 시험을 위한 조건이며 이번 작업에서 관문 개방이나 서버 변경을 실행하라는 지시가 아니다.

1. **로컬 보완과 회귀 검사:** Windows R1·R2를 수정·검증하고 C4 거절 범위를 합의한다. iPad에서 R2의 추가 구조/작품 상태 경계를 확인한다. 수정된 소스 commit, 앱 빌드 식별자와 테스트 결과를 고정한다.
2. **공통 시험 범위 고정:** 최초 시험을 iPad가 지원하는 폴더 1개 생성 + tree_order 1개로 제한한다. 일반 원고 저장과 계약 document_commit을 혼동하지 않는다. iPad는 수동 전송, Windows는 자동 큐 배출이 가능하므로 관문 개방 순간의 대기 배치 목록을 미리 확정한다.
3. **별도 Staging 시험 작품 준비:** 실제 집필 작품 대신 허용된 시험 작품과 백업을 사용한다. 같은 서버 작품 ID, 올바른 사용자 소유 연결, 각 기기의 고유 device ID, 유효한 부모/순서 revision을 양쪽에서 읽기로 확인한다. 시험과 무관한 대기·충돌·기존 legacy 구조 작업이 섞이지 않아야 한다. 기존 배치를 삭제해서 조건을 맞추지 않는다.
4. **서버 준비 상태는 먼저 읽기 확인:** version `0.2.0`, 위 digest, protocol `3` 포함 지원 목록, 필수 capabilities, 현재 mode/epoch, RPC 배포본과 allowlist 상태가 시험을 허용하는지 확인한다. 클라이언트가 추측해 mode/epoch를 전환하거나 digest를 바꾸지 않는다. 조건이 안 맞으면 별도 서버 변경 계획/승인 전까지 시험을 보류한다. 최초의 오래된 handshake migration만으로 서버 호환성이 확보됐다고 가정하지 않는다.
5. **닫힌 관문 검사를 먼저 확정:** 새로 생성한 것과 저장된 pending/retry 배치 모두 gate=False/무효 handshake에서 계약 쓰기 RPC 0회여야 한다. 인증·작품·binding·구조 기준이 전송 준비 중 바뀌는 경우에도 동일해야 한다. 읽기 handshake는 허용한다.
6. **실제 쓰기는 별도 승인 뒤:** 지정된 시험 작품과 지정된 배치에 대해서만 새 handshake를 확인하고 필요한 로컬 관문을 명시적으로 연다. 서버 설정 변경이 필요하다면 별도 범위를 확정한다. prod 전환은 포함하지 않는다.
7. **성공 증거와 응답 유실 시험:** 서버 환경, 양쪽 빌드, batch/operation ID, 요청 hash, 송신/응답/서버 적용 시각, 검증된 receipt, 결과 revision을 기록한다. 반대 기기 pull 및 재실행 후 구조 보존도 확인한다. 응답 유실 재전송은 현재 관문 검사를 통과한 동일 ID/payload로 하며 서버 적용 1회를 확인한다. ‘동기화 완료’ 문구만으로 계약 쓰기 성공을 판정하지 않는다.
8. **시험 종료 확인:** 관문을 닫고 남은 배치 상태와 영수증을 확인한다. 이미 서버에 적용된 변경은 관문 닫힘으로 취소됐다고 표시하지 않는다.

## 5. 이번 검증 결과와 변경 범위

직접 실행한 기존 Windows 회귀 검사:

```powershell
python -B -m unittest tests.test_handshake_stability tests.test_network_recovery -q
```

**48개 실행 / 통과 48 / 실패 0 / 건너뜀 0.** 이 결과는 새로운 R1·R2 경계가 안전하다는 뜻이 아니다. 기존 시험이 해당 SDK 변환/구조 상태 전이를 포함하지 않았다는 것이 이번 추가 검사의 발견이다.

추가 읽기 검토용 모의 재현:

```powershell
python -B _evidence/windows-ipad-final-review-20260906/review_probes.py
```

결과 파일: `_evidence/windows-ipad-final-review-20260906/probe-results.json`. 이는 차이를 측정하는 검토 스크립트이며 48개 정식 회귀 테스트에 합산하지 않는다. R1·R2는 요구 동작을 충족하지 못한 결과다. iPad 전체 시험 수는 위의 보관된 보고 수치이며 Windows에서 새로 실행한 수치가 아니다.

이번에 만든 것은 이 회신 문서, 검토용 iPad 복제본, 모의 검사 스크립트와 결과뿐이다. 제품 코드·기존 테스트·원고·실제 작품 관문·서버 설정·allowlist·계약 파일·digest·프로젝트 mode/epoch는 변경하지 않았다. 실제 서버 쓰기, 커밋, 푸시, 재빌드, 실행 파일 교체도 수행하지 않았다. 시험의 관문 조작은 격리된 임시 테스트 저장소에만 적용했다.

**iPad 측에 요청할 회신:** C4 형식 엄격성 합의, C9의 구조 차단/작품 비활성 경계에 대한 근거 또는 테스트, 30초~2분 지연 사례의 단계별 시각, 최초 canary의 수동 전송 범위와 빌드 식별자를 제공해 달라. Windows는 R1·R2 보완 후 같은 기준으로 다시 결과를 전달해야 한다.
