# Windows → iPad: 회차 없는 실제 로그인 원장 조회 결과

2026-09-07 KST. **독립 읽기 경로 구현·설치 후 사용자가 현재 앱 로그인으로 원장 조회를 수행해 내보냈다. 고정 요청의 11필드 증명이 일치하고 네 count는 모두 0이다. 원본 stopped/HTTP 0과 닫힌 관문, 복구 회차 0개도 보존됐다. 실제 복구 송신은 하지 않았다.**

## 실제 로그인 조회

원본 파일: `windows-recovery-readonly.json`.
SHA256: `4031245dd6ac3771a4d7aa3721ad9415a5a076170f3973d7dd0a8f11846bde09`.

| 항목 | 결과 |
|---|---|
| 앱 조회 시작/종료 | 2026-09-07 02:38:55.216101 / 02:38:55.659977 KST |
| 서버 checked_at | 2026-09-07 02:38:55.816814 KST |
| 새 nonce | `587e2669-cb34-4f6b-bd09-cd3d1a5353b2` |
| authorized / complete | true / true |
| batches / operations / attempts / results | 0 / 0 / 0 / 0 |
| 계정 marker | `bbca16c03e5b9711`, 원본과 일치 |
| error_code | null |
| stale / context_changed_since_observation | false / false |
| original_verified / recovery_round_present | true / false |
| execution_authorized | false |

작품 `1bd47431-0773-482c-8eb5-ac9e2952b6f4`, batch `6fd8c11b-5b74-4219-aa0b-a5d408ca8505`, request SHA256 `bbc9571cb222b35b268c7eaf5a823cc54f7d9e5f9199736e511562a7fe458045` 및 두 operation ID의 순서가 원본 준비 요청과 같다. nonce는 iPad의 이전 DB 역할 기반 조회 증명과 다르며 조회 시각도 그 이후다. 서버와 클라이언트의 시각은 원문 그대로 보존했으며 기존 검사기의 허용 시간 범위 안이다.

이 결과는 사용자가 설치된 앱의 새 독립 읽기 버튼으로 수행해 내보낸 관찰이다. 관리자 증명을 주입하거나 저장된 토큰을 외부에서 갱신한 결과가 아니다. 이번 대조 도구는 파일·메타데이터만 읽었으며 서버 RPC를 대신 재호출하지 않았다. proof는 해당 조회 시점의 결과이고 재사용 가능한 실행 허가나 송신 예약이 아니다.

## 새 읽기 경로와 검증

이전 Windows 복구 전달 ZIP `5aba1a62b5577b620fb81fb6e4144af0f5d1af3debd2c5bcbb7bf190b86c589b`를 기준으로 최소 연결을 추가했다. 변경 소스 5개 및 증분 패치·변경 전 파일 3개를 포함한다.

- `contract_http_zero_recovery.py`: DB 쓰기 없는 공통 `read_server_ledger`를 분리했다. 독립 조회는 유효한 nonzero count도 관찰할 수 있지만 기존 복구 adapter는 계속 nonzero를 거절하고 0건 통과 후에만 기존 진행 사건을 기록한다.
- `contract_recovery_readonly.py`: 원본/기존 stopped 행·회차 없음·닫힌 관문을 확인하고 앱 현재 클라이언트의 캡처한 JWT로 `auth.get_user(jwt=...)`와 원장 RPC만 호출한다. get_session·토큰 갱신·claim·DB 쓰기·관문 setter를 사용하지 않는다. 전후 연결/계정/JWT/클라이언트/쓰기 epoch/관문 변화는 stale로 처리한다. 중복 읽기와 복구 sender의 동시 진입을 차단한다.
- `reviewed_contract_sender.py`: 독립 읽기 mixin을 연결하고 기존 관찰 내보내기를 공통 함수로 분리했다. 복구 송신 조건을 완화하지 않았다.
- `settings_panel.py`: `서버 원장 조회 · 회차 생성 없음`, `원장 조회 결과 내보내기`를 추가했다. 승인 창이나 송신 버튼을 조회 수단으로 사용하지 않는다. 결과는 메모리에서 작품 밖 JSON으로 내보낸다. 원시 예외 문구·JWT·refresh token을 결과에 담지 않는다.
- `tests/test_contract_recovery_readonly.py`: 새 경계 13개를 추가했다. 영향을 받는 복구 31개와 기존 진단 9개를 포함해 **53개 / 실패 0 / OK**. 이전 139개 전체 회귀를 반복하거나 합산하지 않았다.

검증은 합성 임시 DB·파일·클라이언트를 사용했다. DB 쓰기·관문 setter·세션 갱신을 금지한 상태의 성공, 권한/식별 불일치·오류, nonzero 관찰, 조회 전후 연결/JWT 변경, 열린 관문 거절, 중복 읽기/송신 차단, 기존 회차 거절, 새 nonce, 캐시 내보내기/경로 제한/취소를 확인했다. 이번 실제 결과 대조에서 제품 수정이나 회귀 재실행은 하지 않았다.

## 설치 및 실제 DB 보존

2026-09-07 02:37:45 KST 앱 종료 확인 후 설치했다. 설치 SHA256은 `76e6e5e4ede5c1c47fbedf13c8ce033db19829a454c83645e125e79ab77b8e5e`, 79,300,754 bytes다. 새 모듈·버튼·schema 8011 패키지 포함과 테스트한 소스 해시를 확인했다. 빌드본과 설치본의 프로젝트/인증 초기화 전 Qt smoke는 모두 종료 코드 0이었다. 이전 `a4ab309d…` 설치본을 백업했다.

설치 직후 DB는 8010이었다. 사용자 앱 시작 및 조회 후 DB는 **8011**이며 복구용 테이블 3개가 존재하고 모든 행 수가 **0**이다. 설치 전과 조회 후를 비교한 결과 schema_version 외의 선별 protected_state 전체가 동일하다.

- 원본 preparation 1개, 기존 실행 1개: **stopped / HTTP 0 / 영수증 없음**.
- 원본 준비 행 목록 SHA256: `1f887f310b33ef2805335ddc2c2d36ab049a7e010ea4192a6e517f0aeb54cda4`.
- 기존 실행 행 목록 SHA256: `e18cf5940e6e222f74c2a2733471a22a28071452edad1e99fdb08db4b00588e4`.
- 열린 관문 **0 / 15개**.
- 복구 회차/진행 사건/영수증 각각 **0개**. 기존 구조·원고 메타데이터·큐·진단 기록도 보존됐다.
- 원요청 export SHA256: `83e58abdd93e23064bd2ca90fab18c0b19a09ff4f49a71cc22c20f79c8947bbc`, bytes 보존.

DB는 제품 저장소 객체를 초기화하지 않고 immutable 읽기 전용으로 확인했다. WAL 없음과 읽기 전후 크기·수정 시각 불변을 검사했다. 전체 DB·원고 본문·토큰은 읽거나 전달하지 않았다. 행 해시는 전체 원본 행의 선별 JSON 목록 해시이며 전체 DB 해시가 아니다.

결과 대조 시점에는 앱 프로세스가 없어 실행 중인 프로세스 경로 대조는 수행하지 못했다. 설치된 EXE 파일은 다시 해시해 일치함을 확인했다. 이를 현재 실행 중인 이미지의 재추출 검증으로 표현하지 않는다. installation.json의 `actual_login_read_performed=false`는 설치 직후 기록이며, 이후의 실제 조회 결과는 이번 원본 JSON과 verification.json에 별도로 보존했다.

## 다음 단계와 사용자 행동

이번 단계는 현재 로그인으로 읽기 API가 동작하고 원본을 보존함을 확인한 것이다. 모든 현재 송신 조건/전체 로컬·원격 기준/큐/handshake를 새로 검사한 것은 아니다. 과거 `CONTRACT_PREPARATION_NOT_READY`의 실패 조건도 여전히 미상이다.

**사용자는 이 결과·근거 ZIP을 iPad 측에 보내고, 설치·독립 읽기·원본 보존 결과를 대조한 다음 단계 범위의 회신을 받아 주세요.** 지금 앱 추가 조작이나 복구 버튼 사용은 필요 없다. 실제 복구 1회는 별도 새 승인과 직전 검사를 요구하며, 이번 구현·설치·조회 승인을 재사용하지 않는다. 원본/기존 실행 삭제·요청 재발급·3권 수동 생성·송신·재전송은 하지 않는다.

근거: `_evidence/windows-recovery-readonly-20260907/`와 `_evidence/windows-recovery-readonly-result-20260907/`. 제출한 iPad 배포 ZIP도 원문으로 포함한다. EXE와 실제 DB는 ZIP에 넣지 않았다.
