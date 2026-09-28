# Windows 일반 수신·검토 준비 조정 결과 — iPad 전달용

2026-09-07 KST. 사용자 지시에 따라 소스의 최소 조정·진단과 격리 검증을 완료했다. **제품 설치·새 실제 복구 회차·실제 송신·관문 개방은 수행하지 않았다.** 설치된 앱에는 이번 코드가 아직 반영되지 않았다.

## 문제와 변경 후 동작

이전 소스에서는 검토 송신 준비 중에도 일반 수신이 시작돼 구조 기준을 unknown으로 바꿀 수 있었다. iPad 격리 근거를 Windows의 변경 전 소스와 대조해 같은 결과를 확인했다. 이번 조정은 그 코드상 충돌 경로를 막는다. 02:58:33 실제 중단 당시의 호출 순서를 복원하거나 그 원인이 확정됐다고 주장하지 않는다.

| 상황 | 변경 후 동작 |
|---|---|
| 일반 수신이 먼저 진입 | 같은 `_contract_lock`에서 coordinator를 예약한다. worker 생성 전에도 검토 준비가 진행 중 수신을 감지하고 claim 전에 `CONTRACT_PREPARATION_NOT_READY`로 멈춘다. 실행·복구 회차를 소비하지 않는다. |
| 검토 준비가 먼저 진입 | 같은 lock 아래 설정된 `_review_execution_busy`를 보고 새 수신을 보류한다. 이 경로에서는 비동기 핸드셰이크 요청도 시작하지 않는다. |
| 원격 검사 중 수신 요청 반복 | pending 요청과 명시적 manual/baseline/retry 옵션을 합쳐 보존한다. 수신으로 authority나 write epoch를 바꾸지 않는다. |
| 검토 준비 종료·오류 | busy를 해제하고 queued Qt 신호를 보낸다. UI slot과 실제 pull 진입에서 작품/계정 세대 및 coordinator가 그대로인지 다시 확인한 뒤 보류된 수신을 재개한다. 네트워크 대기 중 공통 lock을 잡지 않는다. |
| 작품/계정 변경·종료 | 오래된 재개 신호는 버린다. 다른 세대의 pending 요청을 소비하지 않는다. 종료 중에는 수신을 재개하지 않는다. |
| 늦은 worker 결과·완료 | 해당 worker와 세대 및 수신별 메모리 예약 토큰을 확인한다. 오래된 완료 신호가 새 수신의 pulling 표시를 해제하지 못한다. |
| worker 생성/시작 오류 | 현재 예약만 해제하고 pending 및 기준 재검사 의도를 보존한다. unknown을 허용 상태로 강제 치환하지 않는다. |
| blocked 또는 실제 revision 차이 | 기존 거절을 유지한다. 일반 보류 수신은 blocked를 해제하지 않으며, 실제 높은 revision은 쓰기 전에 중단된다. 기존 명시적 수신 재검사 동작은 유지한다. |

변경한 제품 파일은 `sync_manager.py`, `reviewed_contract_sender.py`, `contract_readiness_diagnostics.py`, `network_recovery.py` 네 개다. 새 시험은 `tests/test_pull_review_coordination.py`다. `network_recovery.py`의 변경은 기존 구조 기준 전환에 고정 진단 사유를 붙이는 한 줄이며 로그인/RPC 계약은 바꾸지 않았다.

## 진단

기존 **준비 상태 조회 · 송신 없음** 및 JSON 내보내기에 `observation.coordination`을 추가했다. 실제 복구 버튼을 누르지 않고 읽을 수 있다. 준비 조건은 기존 10개에 `pull_idle`을 추가해 **11개**가 된다. 기존 관문·권한·기준 조건을 삭제하거나 완화하지 않았다.

- 허용된 authority 상태: contract, legacy, unknown, blocked, unset, unrecognized.
- 고정 변경 사유: initial/configure/release/selection/pull_start/pull_retry/pull_start_failed/accepted/blocked/sign_in/session_recovery.
- 전환 순번과 작품/인증 세대, 관찰 시점의 작품/인증 세대, write epoch.
- 현재 coordinator의 pulling/pull_pending, worker 존재/현재 세대 여부, 검토 준비 활성 여부.

전환 메타데이터와 관찰은 같은 contract lock을 사용한다. 순번은 메모리상의 전환 기록 호출마다 증가하며 DB 회차 번호가 아니다. 본문·파일 경로·JWT·owner token·원시 오류·예약 토큰을 진단에 넣지 않는다. 영속 진단은 허용 필드만 다시 선별한다. 이전 형식의 진단 레코드는 다시 쓰지 않는다. DB 스키마 변경은 없다.

## 검증

필요한 검사 **142개 통과**: 새 경계 시험 13개와 기존 영향 회귀 129개. 검사 명령과 개별 결과는 `tests.log`에 있다. 이후 같은 새 시험 중 내보내기 검사를 실제 JSON 파일 대조까지 강화하고 해당 1개만 다시 실행해 통과했다(`export-test.log`). 완료한 전체 검사를 반복하지 않았다.

새 검사는 실제 SyncManager/coordinator, 실제 SQLite 임시 저장소, Qt QObject 신호 및 queued UI slot을 사용했다. worker 경계는 테스트 대역으로 제한했고, 두 경합 검사는 Python 스레드와 이벤트를 사용해 worker 구성 전 예약 구간 및 원격 조회 대기 구간을 제어했다. 원격 조회를 기다리는 동안 다른 스레드가 contract lock을 획득하는 것도 확인했다. RPC는 합성 클라이언트만 사용했다. 실제 V2PullWorker의 네트워크 실행이나 두 기기 종단 검증은 하지 않았다.

검사 범위는 수신 우선/검토 우선, 원격 조회 사이 수신 도착, 합치기/재개, 오류/생성 실패, 종료, 작품/인증 세대 변경, 늦은 결과/중복 완료 신호, blocked 보존, 높은 revision 차이, 진단 선별·읽기·내보내기다. 기존 reviewed sender, HTTP 0 복구, 독립 읽기 조회, C9 영향 검사, 네트워크 복구 및 일반 업로드 후 수신 coordinator 회귀를 포함했다.

변경 파일 구문 검사와 diff 공백 검사도 통과했다. 기존 `handshake_lifecycle.py`와 `contract_http_zero_recovery.py`는 이전 제출본과 바이트가 같다. C9·영속 HTTP 시도·원본/복구의 중복 송신 금지 규칙을 변경하지 않았다.

## 실제 데이터·설치 보존

작업 전과 후 실제 DB를 `mode=ro&immutable=1`로 선별 조회했다. 각 조회는 WAL 부재와 조회 중 DB 크기/수정 시각 불변을 확인했고 제품 store를 초기화하지 않았다. 원고 본문·전체 DB를 읽지 않았다. 03:56:37 KST 최종 대조 결과:

- 선별 protected_state 전체가 작업 전과 같다. 폴더/문서 메타데이터·순서·큐 ID 및 이벤트, 관문, 진단/복구 건수 모두 같다.
- 원본 요청 bytes와 준비 전체 행 해시 `1f887f310b33ef2805335ddc2c2d36ab049a7e010ea4192a6e517f0aeb54cda4` 유지.
- 최초 stopped 실행 전체 행 해시 `e18cf5940e6e222f74c2a2733471a22a28071452edad1e99fdb08db4b00588e4` 유지, 쓰기 HTTP 0회.
- 별도 회차 `http0-074b0dc9-007c-4f0e-8516-a84804850e77`의 선별 열과 이벤트 전체가 작업 전과 같다. stopped/HTTP 0, 회차 1개·이벤트 4개·영수증 0개 유지. 진단 3개 유지.
- Windows 관문 열림 **0/15**, schema **8011**, contract_batches 0개 유지.
- 설치 EXE SHA256 `76e6e5e4ede5c1c47fbedf13c8ce033db19829a454c83645e125e79ab77b8e5e`, 79,300,754 bytes 유지. 빌드/설치를 수행하지 않았다.
- 실제 서버·로그인 RPC를 호출하지 않았다. 서버 무반영 결론은 이전 중단 결과의 읽기 전용 서버 근거를 참조하며 새 서버 검사를 수행한 것으로 표현하지 않는다.

고정 batch `6fd8c11b-5b74-4219-aa0b-a5d408ca8505`, request SHA256 `bbc9571cb222b35b268c7eaf5a823cc54f7d9e5f9199736e511562a7fe458045`를 유지한다. 빈 3권 생성/base 0과 기존 원고 순서 갱신/base 1의 요청을 재발급하지 않았다. 이미 소비된 회차를 되살리거나 추가 실행 경로를 만들지 않았다. 향후 재개 정책이 필요하면 별도 설계 검토가 필요하다.

## 다음 사용자 행동

**`windows-pull-review-coordination-20260907.zip`을 iPad 측으로 보내고 패치·진단 11개 조건·격리 검사 결과의 대조 회신을 요청해 주세요.** iPad 대조 후 필요한 설치와 비송신 관찰 범위를 정하면 된다. 이번 전달은 설치나 다음 실제 실행의 승인이 아니다.

양쪽 관문을 계속 닫고 앱에서 복구/송신 버튼을 다시 누르지 않는다. 3권 수동 생성·회차 초기화도 하지 않는다. 지금 사용자가 앱에서 추가로 할 조작은 없다.

ZIP에는 이번 기준 대비 패치, 변경 전 제품 파일 4개와 변경 후 파일 5개, 전체 파일 해시, 검사 로그, 실제 데이터·설치 전후 선별 근거, iPad 수신 문서와 격리 재현 결과를 포함한다. 실행 파일·전체 DB·원고 본문·인증 자료는 포함하지 않는다.
