# Windows 별도 복구 중단 결과 — iPad 전달용

2026-09-07 KST. 대상은 최종검증03의 빈 3권 생성과 기존 원고 순서 갱신이다. 승인된 별도 복구 회차 1개는 생성됐으나 `CONTRACT_PREPARATION_NOT_READY`로 중단됐다. **쓰기 HTTP 0회, 복구 영수증 0개, 현재 Windows 관문 15개 모두 닫힘**을 확인했다. 이번 단계에서는 읽기 전용 검사와 근거 보존만 수행했다.

## 실제 관찰

| 시점(KST) | 결과 |
|---|---|
| 02:56:35.200527 | 실행 전 readiness2: 준비 조건 10개 모두 참, 관문 닫힘, 복구 회차 없음 |
| 02:58:33.188660 | 새 로컬 복구 회차 생성 |
| 02:58:33.648661 | `initial_remote_before` 단계에서 `authority_allowed=false` 관찰 |
| 02:58:33.653660 | 복구 회차 stopped, 쓰기 HTTP 0회 |
| 02:58:33.670660 | `gate_closed` 기록 |
| 02:58:49.730024 | 실행 후 readiness3: 준비 조건 10개 모두 참, 관문 닫힘, 이미 중단된 복구 회차 존재 |
| 03:00:48.393592 | 서버 SELECT: 배치·작업·시도·결과 각 0건, 3권 없음, 기존 순서 revision 1 |

실패 순간 `authority.accepted=false`, `authority.allowed=false`였으며 나머지 9개 준비 조건은 모두 참이었다. 계정/작품 식별 일치, 활성 작품, 작품 컨텍스트 일치는 참이었고 active_server_work_count는 0이었다. 이는 당시 구조 기준이 허용된 상태로 수락되지 않았다는 뜻이다. 서버 계정 권한 부족이나 원고 데이터 불일치를 입증하는 결과는 아니다.

실행 후 JSON의 모든 조건이 참인 것은 약 16초 뒤의 새 관찰이다. 앞선 실패 순간을 부정하거나 복구 회차 재사용을 허용하지 않는다. `already_attempted=false` 역시 쓰기 HTTP 시도가 없었다는 뜻이며, 회차가 남아 있다는 뜻이 아니다. `local_candidate=false`, `execution_authorized=false`다.

## 보존 및 서버 확인

- 원본 준비 행 전체 SHA256: `1f887f310b33ef2805335ddc2c2d36ab049a7e010ea4192a6e517f0aeb54cda4`.
- 원본 stopped/HTTP 0 실행 행 전체 SHA256: `e18cf5940e6e222f74c2a2733471a22a28071452edad1e99fdb08db4b00588e4`.
- 두 해시 및 원요청 내보내기 bytes가 실행 전과 일치한다. 기존 폴더·문서 메타데이터·순서·큐 ID와 이벤트도 일치한다. 원고 본문은 읽지 않았다.
- 변경된 선별 상태는 별도 복구 회차 1개, 복구 이벤트 4개, 진단 3개뿐이다. 복구 영수증은 0개다. schema 8011 유지, contract_batches 0개, Windows 관문 열림 0/15개다.
- 서버 확인은 Supabase 커넥터의 SELECT만 사용했다. Windows 로그인 RPC 증명을 새로 만들거나 복구 함수를 실행하지 않았다. SQL과 원 응답을 첨부했다. 고정 batch 및 두 operation ID를 포함한 조회에서 원장 4종이 모두 0건이었다.
- 새 폴더 ID의 서버 행은 없다. 기존 tree_order는 revision 1, children `[1권, 2권]`이다. 이번 두 작업은 서버에 반영되지 않았다.
- 설치 EXE SHA256은 검토·설치본 `76e6e5e4ede5c1c47fbedf13c8ce033db19829a454c83645e125e79ab77b8e5e`와 일치한다. 이번 결과 확인 중 제품 수정·빌드·설치·기존 시험 반복은 하지 않았다.

## 코드 대조와 미확정 범위

`handshake_lifecycle.py:214`의 구조 기준 관찰은 `_v2_structure_authority`가 contract 또는 legacy일 때 accepted를 참으로 판단한다. 저장된 진단에는 원래 enum 값과 변경 호출자가 없으므로 unknown/blocked/미설정 중 어느 상태였는지는 확정할 수 없다.

`reviewed_contract_sender.py:140`의 초기 원격 검사에는 핸드셰이크·작품 상태·로그인 계정/역할 조회 후 로컬 기준 검사가 포함된다. 이번 실패 관찰은 이 단계에 기록됐다. 이벤트에는 `initial_remote_after`, `ledger_verified`, `gate_before`, `http_attempt_durable`, `response_received`가 없다. 코드 순서와 함께 보면 관문 개방 및 쓰기 HTTP 시도 전 중단이며, 이번 회차의 복구 원장 RPC 검사 완료도 확인되지 않는다. **쓰기 HTTP 0회는 읽기 네트워크 호출도 0회였다는 뜻이 아니다.**

`sync_manager.py:2140`에는 구조 기준을 UNKNOWN으로 되돌리는 선택 시작 경로가 있고, pull 시작 경로(`sync_manager.py:9825`)에서도 호출한다. 이 경로와 실행 전후 구조 기준 변화의 동시성은 다음 검토 후보지만, 실제 실패 때 이 호출이 원인이었다는 추적 기록은 없다. 이번 진단을 9월 6일 최초 중단의 원인으로 소급해 단정하지 않는다.

## 고정 식별자

- Staging: `mhpnszcorfzrvhyondxr`.
- 작품: `1bd47431-0773-482c-8eb5-ac9e2952b6f4`.
- Batch: `6fd8c11b-5b74-4219-aa0b-a5d408ca8505`.
- Request SHA256: `bbc9571cb222b35b268c7eaf5a823cc54f7d9e5f9199736e511562a7fe458045`.
- 소모된 복구 회차: `http0-074b0dc9-007c-4f0e-8516-a84804850e77`.
- 빈 3권 folder/create: `77627d68-8388-4d6c-9363-ba6537aac1f0`, operation `7998be14-c48d-49e0-84b0-08030070d312`, base 0.
- 기존 원고 tree_order/reorder: `68c1a7b5-0dda-49ba-bc9a-42e92ce2b758`, operation `f55e8fab-ea38-468e-a4fe-fbe2cf9ad127`, base 1.

## 다음 사용자 행동

`windows-recovery-stop-result-20260907.zip`을 iPad 측으로 보내고, 이번 실패 순간의 `authority_allowed=false`와 서버 무반영 결과에 대한 검증 회신을 요청한다. Windows 실행 경로의 구조 기준 변경 시점과 추가 진단에 필요한 최소 범위를 함께 검토할 자료다.

양쪽 관문을 계속 닫아 둔다. 복구/송신 버튼을 다시 누르지 않는다. 소모된 회차나 원본 실행을 초기화·삭제하거나 요청을 재발급하지 않는다. 추가 회차·재전송은 이번 승인 범위에 없다. 회신을 받아 대조하기 전에는 실제 실행을 진행하지 않는다.

근거 ZIP에는 사용자 JSON 원본·사용자가 제공한 화면 이미지·실행 전후 선별 상태·중단 이벤트·서버 SELECT와 결과·설치 기록·관련 소스와 검증 스크립트·SHA256 manifest를 포함한다. 전체 DB, 원고 본문, 로그인 토큰, 원시 owner token은 포함하지 않는다.
