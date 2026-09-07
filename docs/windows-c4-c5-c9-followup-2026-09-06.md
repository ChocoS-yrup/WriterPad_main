# Windows C4·C5·C9 보완 결과 — iPad 전달용

2026-09-06 KST. **Windows 로컬 코드 수정과 회귀 검증 완료.** 실제 서버 계약 전송이나 설치본 검증 완료를 뜻하지 않는다. iPad 측 보완 결과와 다시 대조한 뒤 다음 시험을 결정한다.

## 기준과 변경 범위

- 저장소 / 브랜치: `ChocoS-yrup/WriterPad_main` / `feat/contract-handshake-closed-gate`
- 기준 HEAD: `a3eaa8b97dc9769ad313ac3fe579d0b1443849a9`
- 현재 결과: 위 HEAD에 대한 **미커밋 작업 트리**. 새 수정 커밋이나 새 설치 빌드는 아직 없다.
- 계약: version `0.2.0`, protocol `3`
- digest: `416c1b99edb9bda694731dee4b25688d9d82d1f32610aa23ddfda571ec3c7670`
- 근거 회신: `ipad-final-comparison-followup-2026-09-06.md`, iPad 기준 코드 `bb40d22164f34371f9ad7f70b9cddf208a692f83`

이번 변경 파일:

| 파일 | 변경 |
|---|---|
| `contract_transport.py` 신규 | 계약 RPC별 HTTP 상태 보존, SDK 내부 재시도 방지, 명시적 거절 분류 |
| `handshake_lifecycle.py` | 응답 작품 확인, 오류 원인 검사, 구조·작품·대기열의 최종 승인 검사 |
| `sync_contract.py` | C4 UUID 문자열·목록 엄격성 |
| `sync_manager.py` | 구조/작품 상태 변경 세대, 늦은 작품 상태 응답 거절 |
| `tests/test_contract_followup.py` 신규 | 안전 요구를 검증하는 상시 회귀 테스트 17개 |
| `tests/test_handshake_stability.py` | 기존 전송 시험의 초기 조건에 확인된 구조/작품 상태 명시 |
| `tests/test_sync_contract_stage8.py` | 기존 정상 전송 시험 2개의 초기 구조/작품 확인 조건 명시 |

기존 테스트의 성공 조건이나 차단 기대를 완화하지 않았다. 정상 전송 테스트에서 핸드셰이크만으로 구조 기준까지 확인됐다고 가정하던 초기 조건을 구분했다.

기존 미커밋 집필 수정인 `mode_writing.py`, `tests/test_editor_view_state.py`, `tests/test_sync_state.py`, `tests/test_remote_editor_cursor.py`는 추가 수정 없이 보존했다. 기존 AI 수정도 보존했다. 계약 파일·lock·pin·의존성 버전은 변경하지 않았다.

## 수정 전후

| 항목 | 수정 전 재현 | 수정 후 회귀 결과 |
|---|---|---|
| C4 project_id 누락 | supported 응답 승인 | 필수 UUID 문자열 및 요청 작품 일치 검사, 누락/다른 작품 거절 |
| C4 protocol/capability 목록 | 중복 protocol, 0 포함 protocol, 중복 capability 승인 | 중복·0 이하 protocol, bool 등 잘못된 형, 빈 목록/빈 capability 거절 |
| C5 JSON 429/500/503 | SDK가 상태 코드를 버려 재시도 완료 기록으로 고착. 성공 응답 준비 후에도 요청 수 1→1 | 실제 SDK 오류 변환을 통과한 상태를 보존. 기한 전 요청 1회 유지, 기한 후 1→2회로 복구, 반복 알림에도 추가 요청 없음 |
| C5 확정된 거절 | 감싼 오류의 과거 timeout이 거절보다 먼저 읽힐 수 있음 | 원인 전체에서 명시적 거절을 먼저 확인. 400/401/403 및 500 안의 계약 거절을 통신 복구 루프로 바꾸지 않음 |
| C9 구조 기준 변경 | 인증 준비 중 구조 BLOCKED가 돼도 모의 execute 1회 | 인증 준비·RPC 구성·worker 대기 이후에도 UNKNOWN/BLOCKED이면 모의 execute 0회 |
| C9 작품 상태 변경 | 최종 계약 검사에 작품 활성 상태 확인 없음 | 현재 문맥에서 확인된 active가 필요. trashed/purged 전이 및 전이 후 active 복귀까지 이전 준비 요청은 차단 |
| C9 늦은 상태 응답 | 상태 조회 응답이 현재 작품 문맥으로 반영될 여지 | 요청의 작품/인증/저장소/클라이언트 문맥과 변경 세대 확인. 이전 인증 응답 및 새 휴지통 상태보다 늦은 active 응답을 승인에 사용하지 않음 |
| C9 저장소의 blocked/conflict | 최종 승인 검사에 포함되지 않음 | 실제 임시 DB에 차단/충돌을 만든 후 최종 전송 0회, 대상 계약 요청 보존 |
| 이미 전송 시작한 요청 | 영수증 보존이 필요 | 시작 뒤 구조/작품이 차단돼도 검증된 영수증은 원래 저장소에 보존 |

신규 시험을 처음 추가했을 때 11개 시험 메서드의 하위 조건에서 24개 실패를 확인했다. 보완 및 범위 추가 후 최종 신규 시험은 17개이며 모두 통과했다. 이번 시험은 iPad의 현상 재현용 시험과 달리 **잘못된 입력 거절과 전송 0회**를 통과 조건으로 사용한다.

## 양쪽이 따를 동작

### C4 응답 승인

`project_id`는 UUID 문자열이며 요청 작품과 같아야 한다. protocol 목록은 비어 있지 않은 양의 정수 목록이고 bool·중복을 허용하지 않는다. capability 목록은 중복·빈 문자열을 허용하지 않으며 필수 서버 capability를 포함해야 한다. 기존 필수 3개 필드와 version/digest/protocol pin 검사는 유지한다. iPad 검사 완화나 계약 digest 변경은 필요하지 않다.

### C5 HTTP 실패와 재시도

Windows는 현재 고정한 `supabase/postgrest 2.31.0`의 RPC 빌더와 오류 변환을 그대로 사용하면서, **각 요청의 복사본**에서 HTTP 상태를 보존한다. 다른 RPC나 공유 SDK 클라이언트를 전역으로 변경하지 않는다. 새 계약 전송은 항상 수명주기 검사로 돌아오도록 이 복사본의 SDK 자동 재시도를 끈다.

JSON 형식의 일시적 429/5xx는 2→4→8→16→32→60초, 이후 60초 상한의 기존 핸드셰이크 재시도로 들어간다. 확정된 인증/권한/계약 거절과 잘못된 응답은 같은 통신 재시도 루프로 넣지 않는다. 실패를 감싼 예외에 과거 timeout이 있어도 명시적 거절이 우선한다.

실제 SDK와 `httpx.MockTransport`를 연결한 회귀 검사로 확인했다. 계약 쓰기 503 뒤에는 숨겨진 즉시 재전송이 없으며, 관문을 닫으면 재시도 전송은 0회다. 임시 시험 관문을 다시 승인한 뒤 보낸 요청 바이트도 최초 요청과 같았다.

### C9 구조·작품 승인

구조 기준은 기존 UNKNOWN / LEGACY 또는 CONTRACT 허용 / BLOCKED 상태를 사용한다. 실제 구조 조회와 적용 판단 경로가 이를 갱신한다. 계약 쓰기는 현재 작품·인증 세대와 일치하는 허용 상태만 사용한다.

작품 상태는 기존 `get_project_status` 및 호환 조회 경로에서 확인하되 요청 당시 작품·인증·저장소·클라이언트 문맥에 귀속한다. 디스크에 남은 active 또는 기본 active 값만으로 계약 쓰기를 승인하지 않는다. 대기 중 새로운 구조/작품 상태 변경이 있으면 이전 active 응답이 이를 덮어쓰지 못한다.

구조/작품 전송 승인 세대는 핸드셰이크 캐시 수명과 별도로 관리한다. UNKNOWN/BLOCKED/trashed 같은 상태를 거쳐 다시 허용되더라도 이미 준비 중이던 요청은 이전 세대이므로 시작하지 않는다. 재개할 때는 **같은 저장 배치를 새 문맥으로 다시 검사**한다.

최종 전송 조건은 다음과 같다.

1. 현재 계약 관문, 인증, 작품 연결, 핸드셰이크, 계약 메타데이터가 유효함.
2. 현재 문맥의 구조 기준이 확인돼 있으며 작품이 active로 확인됨.
3. 준비를 시작한 이후 구조/작품 승인 세대가 바뀌지 않음.
4. 해당 작품 대기열에 blocked/conflict가 없음. pending/inflight/retry 작업이 있다는 사실만으로 거절하지 않음.
5. 종료/강제 오프라인/동일 배치 중복 전송 상태가 아님.

이 검사는 저장 배치 선택, worker 실행, 인증 대기 이후, RPC 구성 이후의 전송 시작 예약까지 적용된다. 구조 `atomic_structure_commit`과 계약 원고 `document_commit`이 같은 최종 경계를 사용한다. 해당 조건을 전체 legacy 동기화의 새 전역 차단 조건으로 추가하지 않았다.

단순히 ‘모두 동기화 완료’나 `lastPullSucceeded`를 승인 조건으로 쓰지 않는다. 로컬 저장으로 대기 배치가 생겨도 이미 확인된 구조 기준은 사용할 수 있다. 원고 열기·집필·로컬 저장에서 서버 응답을 기다리는 동작을 추가하지 않았다.

닫힘/무효화는 operation ID, batch ID, payload 재생성 사유가 아니다. 시작 예약은 서버 수신·적용 증거가 아니며, 예약 뒤 닫힘은 서버 취소/롤백 완료를 뜻하지 않는다. 이미 시작한 요청의 유효한 영수증과 결과 불명 시 동일 요청 재시도 규칙은 유지한다.

## 최종 검증

```powershell
python -B -X faulthandler -m unittest tests.test_contract_followup tests.test_network_recovery tests.test_handshake_stability tests.test_sync_contract_stage8 tests.test_sync_state tests.test_sync_v2 tests.test_sync_resilience tests.test_sync_diagnostics tests.test_module_boundaries tests.test_startup_mode tests.test_shutdown_budget tests.test_writing_autosave tests.test_assistant_safety tests.test_app_config_paths tests.test_editor_view_state tests.test_remote_editor_cursor tests.test_typewriter_mode -q
```

**최종 소스: 754개 실행 / 통과 753개 / 실패 0개 / 건너뜀 1개**, 90.565초. 건너뜀은 기존 Windows 심볼릭 링크 생성 권한 관련 시험이다. 신규 17개에 건너뜀은 없다. 집필 커서·여백·자동저장·AI 안전성 관련 기존 회귀도 포함했다.

로그: `_evidence/c4-c5-c9-final-full-suite.txt`. 앞선 214개/215개 부분 실행은 최종 754개와 중복되므로 합산하지 않는다. 테스트 출력의 통신/디스크 실패 문구는 실패 주입 시험에서 나온 것이며 unittest 결과는 위와 같다. 임시 디렉터리 정리 ResourceWarning도 있었지만 테스트 실패는 없었다.

`git diff --check` 통과. 제품/테스트 파일별 최종 SHA-256은 `_evidence/windows-c4-c5-c9-followup-20260906/verification.json`에 기록한다. 새 커밋이 없으므로 HEAD만으로 이번 소스 상태를 식별해서는 안 된다.

## 남은 조건과 사용자 다음 행동

- 실제 작품 관문, 서버 설정, allowlist, 계약 파일·digest, 프로젝트 mode/epoch를 변경하지 않았다. 실제 서버 쓰기 시험도 없었다. 관문 조작과 서버 응답은 임시 테스트 저장소·대역 안에서만 사용했다.
- 커밋·푸시·재빌드·실행 파일 교체는 하지 않았다. **설치된 앱은 이번 수정 전 실행 파일이다.**
- iPad C9 보완 및 복구 시각 진단 결과가 아직 필요하다. Windows 수정만으로 양쪽 C1–C12 완료를 선언하지 않는다.
- 클라이언트가 아직 관측하지 못한 다른 기기의 서버 상태 변경은 로컬 세대 검사로 막을 수 없다. 실제 쓰기 시점의 서버 활성 상태/권한 정책은 별도의 읽기 확인과 Staging 시험으로 검증해야 한다.
- iPad 최초 계약 시험 지원 범위는 여전히 새 폴더 1개 + tree_order 1개, Debug 수동 1배치다. 원고 계약 자동 동기화·이름 변경·이동·삭제·복원 범위 확대를 이번 결과에 포함하지 않는다.
- SDK 업그레이드 시 `contract_transport.py`의 요청 복사/HTTP 보존 회귀를 다시 실행해야 한다. 이번 작업은 버전을 올리지 않았다.

**사용자가 지금 할 일:** 이 문서를 iPad 측 작업에 전달하고, iPad의 C9 및 복구 진단 수정 결과를 받아 이 작업에 첨부한다. 현재 앱에서 추가 조작이나 통신 차단 시험은 필요 없다. 실제 계약 관문은 닫힘을 유지한다. 양쪽 결과를 대조한 다음 커밋·푸시 및 새 빌드 설치 검증을 진행하고, 실제 계약 전송 시험은 그 이후 별도 지정된 Staging 작품에서 결정한다.

참조: SDK 사용 형태는 [Supabase Python RPC 공식 문서](https://supabase.com/docs/reference/python/rpc)를 확인했고, 상태 소실과 요청 복사 동작은 현재 설치된 2.31.0 소스 및 모의 HTTP 시험으로 검증했다. 이 문서에는 비밀키·토큰·원고 본문을 포함하지 않는다.
