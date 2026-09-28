# Windows 실제 조회 실행 준비안

2026-09-14. 이번 사용자 요청은 **문서 작성만**이다. 기존 수집기·설정·세션 관련 소스와 기존 문서를 읽고 이 준비안만 추가했다. 구현, 코드 실행/재검사, collect/inspect, 실제 조회, 자격증명 읽기, 로그인 갱신, 설치, 관문/hold 변경은 하지 않았다. 아래 호출부·실행 범위·폴더는 후속 작업의 설계이며 아직 생성하거나 승인한 실행물이 아니다.

**결정할 다음 단계는 Windows 독립 호출부의 오프라인 구현과 영향 검사 한 묶음이다.** 수집기 구현은 이미 있지만 실제 세션·전송·새 범위를 안전하게 연결하는 호출부는 없다. 구현과 검사가 끝나기 전에는 실제 조회 승인을 요청하지 않는다. iPad 문서 대조는 종료됐으므로 같은 명세를 다시 대조하도록 보내지 않는다.

**1. 현재 기준과 보존 범위**

- `isolated_read_collector.py`의 Q1~Q7, scope, journal, observation 형식을 유지한다. 기존 수집기 고유 21건 결과와 Windows 편집기 최종 53건은 완료 기록이며 재실행 대상으로 삼지 않는다.
- `docs/windows-minimal-read-preparation-2026-09-14.md`의 요청·count 조건을 따른다. 그 문서의 “수집기 구현 필요”, “iPad 준비안 요청”은 당시 단계의 설명이며 현재 다음 행동으로 반복하지 않는다.
- `docs/windows-observation-mapping-spec-reply-2026-09-14.md`의 해석을 iPad가 수용했다. 합성 reference 대조·교차 플랫폼 체인 해시는 미검증, 후보 profile은 수동 참조, raw 없는 항목은 내용 독립 검증 미완료로 유지한다.
- 실조회 비교에는 원본 파일이 존재하는 과거 17 node / 15 order 자료를 사용한다. 합성 예제를 재생성하거나 합성 reference 해시를 이 자료에 붙이지 않는다. 과거 자료와 현재 서버의 차이는 정지·보고할 결과이며 비교 입력을 최신값으로 바꿔 통과시키지 않는다.
- 기존 미커밋 변경, 설치물, 본문, iPad 충돌/미송신 18·19바이트 원본 2개, 기존 증거를 보존한다. 종료 run `6a7a9c7d-982a-4fcd-90e6-3b4140504860` 및 그 사용량·만료는 재사용·재계산·초기화하지 않는다.

**2. 필요한 호출부의 구체적 범위 — 아직 구현하지 않음**

후속 구현 파일명 제안은 `isolated_read_launcher.py`, 검사는 `tests/test_isolated_read_launcher.py`다. 제품 앱 시작·UI·자동 동기화 경로에 연결하지 않는 별도 진입점으로 만든다. import, 도움말, 기본 실행에는 HTTP나 자격증명 접근을 넣지 않는다. 실제 실행은 검토된 명세 파일을 명시적으로 지정하는 단일 진입점으로 제한한다. 실행 가능한 CLI와 명령은 구현 완료 후 제시한다.

| 호출부 구성 | 수행할 일 | 기존 수집기와의 경계 |
| --- | --- | --- |
| 실행 명세 읽기 | 고정 결과 root, scope 원본 해시, 코드·참조 파일, 설정 위치, 계정 profile, 시작 가능 시각·절대 만료를 고정 | 기존 scope에 임의 필드를 넣지 않고 별도 호출 명세에서 결합 |
| 중복 실행 차단 | 고정 namespace에서 run_id를 원자적으로 1회 점유하고 이미 점유된 run은 거부 | collect는 자기 결과 디렉터리만 중복 검사하므로 호출 전 실패까지 기록할 외부 점유가 필요 |
| 자격증명 경계 | 지정된 Windows 사용자·profile의 access token 한 항목만 읽음. 원본 변경 금지 | refresh token/SDK 세션 복원 경로는 사용하지 않음 |
| 전송 경계 | 같은 endpoint·Q1~Q7 순서/메서드/경로/params/body만 통과시키는 async transport wrapper | collect의 전송 전 attempt 예약이 기본 HTTP 사용량 근거 |
| 요청 전 조건 확인 | 승인 명세·참조/설정 결합, 토큰 변경/만료, 남은 시간, 기존 관문 적용 여부를 확인 | 거부 시 그 요청을 전송하지 않고 중단. 이미 예약된 attempt는 반환하지 않음 |
| 실제 전송 | 명시적으로 만든 HTTPX async transport, 인증서 검증 유지, 재시도 0, proxy/환경 우회 설정 없음 | redirect·추가 페이지·SDK 자동 요청 없이 collect 1회만 호출 |
| 종료 처리 | 신규 결과만 보존하고 짧은 상태·예약 횟수·중단 코드·폴더 위치를 보고 | 완료/실패/취소 뒤 다시 collect 호출하지 않음 |

호출부에는 `SyncManager.create_supabase_client`, `auth.set_session`, `get_session`, `ensure_session_valid`, `network_factory`, 앱/store/materialize/accept_snapshot을 넣지 않는다. 소스상 기존 SDK 복원 경로에는 refresh token 읽기, set_session, 세션 저장·실패 처리 등이 있으므로 이번 제한에 맞지 않는다. `SecurityManager.get_supabase_session()`도 refresh token까지 읽기 때문에 사용하지 않는다.

HTTPX transport와 자격증명 공급부는 분리해 오프라인 검사에서는 둘 다 가짜 구현을 주입할 수 있게 한다. 실제 전송 경계의 조건은 구현 시 기존 설치 HTTPX 소스와 대조하고, 신규 패키지 설치나 업그레이드 없이 구현한다. 현재 문서에서 라이브 전송 동작을 검증했다고 주장하지 않는다.

**3. 결과 폴더와 원본 입력**

고정 결과 namespace 제안:

```text
D:\안티그래비티\scratch\작가님 힘내세요\_evidence\windows-independent-live-read\
    attempts\<새 run_id>\
        launch-manifest.json
        scope-input.json
        reference\before-metadata.json
        reference\before-orders.json
        launcher-journal.jsonl
    runs\<같은 새 run_id>\
        scope.json
        journal.jsonl
        Q1.body ... Q7.body
        observation.json
```

이번에는 위 폴더를 만들지 않았다. `runs`가 collect에 넘길 results_root이고, collect가 그 아래 `<run_id>`를 직접 생성한다. 호출부가 해당 run 폴더를 미리 만들면 collect가 거부하므로 사전 점유·원본 입력 보존은 별도 attempts 아래에서 수행하도록 설계한다.

attempts의 run 폴더는 배타적으로 생성하며, 어느 단계에서 멈췄든 같은 run으로 다시 들어가지 않는다. runs에만 폴더가 있는 모순 상태도 거부한다. 점유 실패 때는 기존 폴더 안에 실패 기록조차 덧붙이지 않는다. 새 실행이 필요하면 이유를 보고한 뒤 별도 새 승인 범위를 정한다. 결과 root를 바꿔 동일 범위로 재시도하는 우회는 허용하지 않는다.

호출부의 launcher-journal은 시작 전 점유/중단과 입력 연결을 기록할 별도 감사 자료다. 기존 collector journal 이벤트 형식을 바꾸거나 실행 전 실패를 Q1 응답으로 꾸미지 않는다. 추가 기록의 필드·부분 기록 해석은 후속 구현에서 함께 정하고 검사한다. 영구 점유는 예산 환급·재개를 허용하는 장치가 아니다.

읽을 원본 입력은 아래 두 파일로 고정한다.

```text
D:\안티그래비티\scratch\작가님 힘내세요\output\windows-ipad-isolated-target-reply-20260913\before-metadata.json
D:\안티그래비티\scratch\작가님 힘내세요\output\windows-ipad-isolated-target-reply-20260913\before-orders.json
```

기존 기록의 SHA-256은 각각 다음과 같다. 이번에는 재계산하지 않았다.

```text
metadata 079df5ba95deda09744f7632fe6f6ead2fbe7cbc2c994a0ce02d9ff2f5b132a9
orders   9a51f220ab631ef58ddf033b63dd8da89df38f48dffc095df87a712cb3e95d6a
```

후속 실제 실행에서는 두 파일의 정확한 바이트를 한 번 읽어 해시를 확인하고, 신규 attempts/reference에 그대로 보존한 바이트를 collect에 넘긴다. JSON 재인코딩, DB에서 재추출, 본문 보정은 하지 않는다. 원본을 보존하지 못하면 HTTP 전에 중단한다. 추가 보존의 대상은 이 실조회 입력이며 미보존 합성 입력을 복구하는 작업이 아니다.

경로는 승인된 절대 경로 안에 한정하고 symlink/reparse point·기존 파일 덮어쓰기·다른 결과 root를 거부한다. 실제 raw는 비공개 원본으로 보관하며 자동 ZIP 생성·외부 전달·앱 반영은 하지 않는다. 최대 응답 body는 수집기 기준 요청당 64 MiB이며 디스크 기록 실패도 정지 조건이다. 기존 DB/WAL/SHM과 원고 폴더를 열거나 새 사본으로 대체할 이유가 없다.

**4. 새 실행 범위와 7회·180초 한도**

현재 scope가 요구하는 값은 아래와 같다. 빈칸을 채운 실제 scope JSON, 실행 ID, 절대 만료 시각은 이번에 발급하지 않는다.

| scope 필드 | 값/결정 시점 |
| --- | --- |
| format | windows-isolated-read-scope-v1 |
| run_id | 호출부 준비 완료 후 승인 검토용으로 새 UUID 1개 고정. 종료 run/합성 예제 run은 거부 |
| endpoint | https://mhpnszcorfzrvhyondxr.supabase.co |
| account_id | e487c6ea-1c2b-4a90-821e-91e8547106de |
| project_id | d8f50b5f-ae0e-42f8-9296-5d5885a5b304 |
| max_requests | 정수 7 |
| max_seconds | 숫자 180 |
| expires_at | 실제 승인안에 KST·UTC·epoch 초로 함께 표시할 절대 시각 E |
| reference_sha256 | 위 실제 보유 reference 두 파일의 해시 |

별도 호출 명세는 시작 가능 시각 S와 절대 만료 E를 고정한다. 기본 실행 창은 **E = S + 180초**로 제안한다. 사용자가 검토·승인하기 전에 S/E와 명령을 구체적으로 보여 준다. 승인이 늦어져 실행 창을 놓치면 자동으로 시각을 이동하지 않는다. 새 창이 필요하다는 사실을 보고한다.

실제 collect 시작 t0에서 deadline은 기존 코드 그대로 `min(E, t0 + 180)`이다. 시작이 늦을수록 남은 시간은 짧아진다. 사전 입력·세션 확인이 오래 걸려 E에 도달해도 연장하지 않는다. HTTP 구간은 벽시계와 단조시계 잔여 시간 중 작은 값으로 제한한다. 종료 파일의 동기 디스크 기록까지 OS가 정확히 180초에 강제 중단한다는 보장은 아니며, 만료 후 추가 HTTP를 허용하는 뜻도 아니다.

| Q | 고정 요청 | 예약 상한 |
| --- | --- | ---: |
| Q1 | GET /auth/v1/user | 1 |
| Q2 | POST /rest/v1/rpc/get_sync_handshake | 1 |
| Q3 | GET /rest/v1/projects | 1 |
| Q4 | GET /rest/v1/project_sync_settings | 1 |
| Q5 | GET /rest/v1/documents | 1 |
| Q6 | GET /rest/v1/folders | 1 |
| Q7 | GET /rest/v1/tree_orders | 1 |

Q2 body는 `p_project_id`와 `p_contract_sha256`만 사용하며 계약 SHA는 `416c1b99edb9bda694731dee4b25688d9d82d1f32610aa23ddfda571ec3c7670`이다. Q3~Q7은 `project_id=eq.<위 project_id>`, `select=*`, `limit=10000`, `Prefer: count=exact`다. Q1/Q2는 HTTP 200만, Q3~Q7은 200/206과 정확한 count 조건을 함께 요구한다.

빈 배열 count는 `*/0` 또는 `0-0/0`, 비어 있지 않은 길이 n은 정확히 `0-(n-1)/n`이어야 한다. count 누락·잘림·배열 10000행 초과는 추가 페이지 없이 중단한다. Content-Range 형식 기록만으로 count 검증 성공이라 하지 않는다.

요청별 timeout은 남은 시간과 15초 중 작은 값이며 HTTPX 연결/읽기 등 단계 기준이다. 요청 전체가 무조건 15초 이하라는 뜻은 아니다. 예약은 전송 전 collector journal에 flush/fsync하고 실패·취소·전송 경계 거부에도 환급하지 않는다. 총 7회가 목표 소진량은 아니며 첫 실패 뒤 남은 요청은 하지 않는다. Q2 POST도 HTTP 1회이고 문서/구조 쓰기 0회다. 로그인 발급·갱신, 추가 인증 점검 HTTP, retry, redirect, 추가 페이지, 자동 주기, receipt 조회는 모두 0회다.

**5. 기존 세션 사용 조건**

소스에서 확인한 기본 credential service는 `Antigravity_WebNovelApp`, access token 항목명은 `SupabaseAccessToken`이다. runtime_profile의 빈 profile을 사용하는 기존 통합 경로를 기준으로 제안한다. 다른 ANTIGRAVITY_PROFILE이나 service를 자동 탐색하지 않는다. 실제 해당 항목의 존재·계정·만료는 이번에 읽지 않았으므로 모두 미확인이다.

후속 호출부는 지정된 Windows 사용자와 빈 profile을 고정한 뒤 access token만 메모리로 읽는다. refresh token 읽기·사용·저장·삭제, 로그인 복원, 로그아웃은 구현 범위에서 제외한다. 토큰을 CLI 인수·환경 변수·콘솔·로그·ZIP·scope에 넣지 않는다. 사용자가 채팅에 토큰을 붙여 넣도록 요청하지 않는다.

로컬 JWT payload의 sub/exp 등은 형식·명백한 대상/만료 불일치를 사전에 거르는 데만 사용한다. 서명 검증이나 현재 서버 세션 유효성의 증거로 취급하지 않는다. 파싱 불가, sub 불일치, exp 부재/만료, 실행 창을 감당하지 못하는 만료는 Q1 전에 중단한다. 기본 보수 조건은 `exp >= E + 30초`로 제안한다. 30초는 시계 오차 여유이며 실행 시간을 늘리는 값이 아니다. 실제 계정 확인은 허용된 Q1 하나가 담당한다. Q1 실패 시 refresh나 별도 get_user 재호출을 하지 않는다.

같은 계정/profile의 기존 credential lease 이름 계산은 runtime_profile에 있다. 호출부는 앱과 동일한 lease를 즉시 획득해 종료까지 유지하는 방식을 제안한다. 획득 실패·이름 계산 실패 때 대기 루프, 강제 종료, 잠금 제거, 다른 이름으로 우회하지 않고 Q1 전에 중단한다. 이를 위해 sync_manager 전체를 import하는 대신 최소 독립 lease 연결이 필요하며 오프라인 검사 범위에 포함한다. 이 일시적 동시 실행 잠금은 기존 앱의 관문/hold 파일을 바꾸는 작업이 아니다.

일부 경로가 lease를 따르지 않을 가능성까지 잠금 하나로 해결됐다고 보지 않는다. 실제 요청마다 저장된 access token이 최초 읽은 값과 같은지 메모리에서 비교하고 변경되면 중단하도록 전송 경계에 넣는다. 새 토큰으로 자동 교체하지 않는다. 최초 읽기 후 모든 외부 계정 변경을 원자적으로 막는 보장은 없으며 서버의 거부도 정지 결과로 남긴다.

Staging publishable key는 별도로 지정한 **기존 설정 파일**에서 읽는다. cloud_config의 순수 파일 읽기/검증 경로는 사용할 수 있지만 SDK client는 만들지 않는다. 설정 URL은 위 endpoint와 정확히 같아야 하고 기존 검증기가 허용하는 publishable key여야 한다. prod, service-role/secret key, 대체 계정은 거부한다.

**설정 파일의 정확한 절대 경로와 해시는 아직 확정하지 못했다.** 기존 런타임은 설치의 `sys._MEIPASS` 아래 release_cloud_config.json을 읽도록 되어 있다. 이번 제한된 파일명 확인에서는 경로를 확인하지 못했으며 파일 부재로 단정하지 않는다. 프로젝트 루트의 같은 이름 파일을 대신 사용한다고 가정하지 않는다. 후속 오프라인 준비에서 기존 Staging 설정의 위치·비밀정보 없는 URL/상태와 원본 결합을 확정하고, 불명확하면 실행 차단으로 보고한다. 설정 생성·복사 설치·URL 변경은 하지 않는다.

기존 관문·hold·오프라인 제한의 적용 범위도 실행 명세에 기록해야 한다. 독립 수집기라는 이유로 현재 실제 읽기까지 금지하는 제한을 무시하지 않는다. 문서/쓰기 전용 hold와 모든 통신 차단을 구별하되 해석이 불명확하거나 실제 읽기 금지라면 중단한다. 이번 문서는 해제·삭제·권한 우회 승인이 아니다.

**6. 중단 기준과 보고 방식**

| 시점 | 중단 조건 | 보존·보고 |
| --- | --- | --- |
| collect 이전 | 승인 범위 없음/변경/시간창 밖, 경로·run 중복, 입력 hash 불일치, 설정 미확정/다른 endpoint, lease 거부, token 없음/만료/불일치, 적용되는 관문 차단 | 신규 점유가 있었다면 그 실패 기록 유지. HTTP 0. 기존 run은 건드리지 않음 |
| 요청 전 | 요청 순서/URL/body/헤더 범위 일탈, 세션·명세 변경, 만료/시계 역행 | 전송 거부. collector가 이미 예약했으면 사용량 유지 |
| 응답 처리 | 401/403/429/5xx/redirect 등 허용 밖 상태, 연결/TLS/timeout, 응답 크기 초과, JSON/중복 키/형식 오류, 자격정보가 body에 나타남, 기록 실패 | 해당 지점에서 정지. 저장할 수 있었던 원본과 journal만 보존 |
| 내용 대조 | account/owner/project/name/contract/mode/epoch/capability 불일치, count 불완전, 후보 충돌, 기존 reference 차이, 경로·그래프·정렬 모순 | 첫 차이와 도달 단계 보고. 뒤 테이블의 미검증을 같음/0행으로 채우지 않음 |
| 취소·비정상 종료 | 사용자 중단, 프로세스 종료, 부분 journal/최종 이벤트 누락 | 부분 상태 그대로 유지. 자동 수리·환급·재개하지 않음 |

실패한 응답은 body 보존 전에 문제가 날 수 있으므로 항상 Qn.body가 남는다고 약속하지 않는다. observation 저장 후 terminal 기록이 실패할 수도 있다. 최종 이벤트 없는 observation은 최종 결합 미확인으로 보고한다. 전송 전 실패까지 모두 기존 collector stop_reason으로 세분화돼 있다는 가정도 하지 않는다. 호출부가 구별한 로컬 중단 사실은 별도 launcher 기록으로 보고한다.

정상 종료 신호는 `관찰 완료 · HTTP 예약 n/7 · 적용 없음 · 결과 폴더` 정도로 짧게 한다. 중단 시에만 `중단 Qn/전송 전 · 정지 코드 · 예약 n/7 · 미확인 범위 · 다음에 필요한 조치`를 구체적으로 보고한다. 본문·토큰·원문 예외 메시지를 채팅에 출력하지 않는다.

observed도 execution_allowed/complete/baseline_ready/atomic_snapshot=false다. 원격 기준본 적용·후보 생성·iPad 초기 수신은 하지 않는다. RLS 가시 범위의 순차 관찰일 뿐 전역 부재·동일 시점 snapshot·생성 안전성을 증명하지 않는다. raw 없는 항목의 독립 내용 검증도 완료로 표시하지 않는다.

**7. 후속 구현·검사는 한 묶음으로 제한**

다음 구현은 호출부, 제한 transport, access-token 전용 읽기, 입력 원본 보존, 실행 1회 점유, 짧은 결과 보고를 함께 완성하는 한 묶음으로 제안한다. 기존 collector·편집기 변경이 필요해지면 이유와 영향부터 보고하고 조용히 범위를 넓히지 않는다. 후보 profile의 기계적 결합, iPad reader/adapter, 합성 예제 재생성, 플랫폼 체인 인코딩 구현은 포함하지 않는다.

완성 뒤 검사는 가짜 자격증명·임시 폴더·MockTransport를 사용하는 영향 검사 한 묶음으로 한다. 핵심 조건은 다음과 같다.

- 기본/import 경로의 네트워크·자격증명 비접근, token 누락·만료·변경·lease 거부 시 전송 차단, refresh/keyring 쓰기 없음.
- 명세/참조/설정 변경 및 다른 endpoint/요청을 차단하고 추가 HTTP·redirect·retry가 없을 것.
- 같은 run의 동시/중복 실행, collect 이전 실패, 중간 취소/부분 파일 뒤 재진입을 차단하고 기존 결과를 보존할 것.
- 신규 원본 reference의 정확한 바이트 보존, 7회·deadline 경계와 예약 사용량 유지, raw 비노출, 정상/중단 반환 경계를 확인할 것.

기존 53건·수집기 21건·iPad 완료 검사를 반복하지 않는다. 신규 경계 검사에서 실제 소켓·Windows credential store·기존 AppData/DB를 사용하지 않는다. 이번에는 위 검사도 실행하지 않았다. 실제 세션 확인은 나중에 승인된 실행 범위의 전송 전 단계에서 1회 읽고, 무효하면 그 자리에서 중단한다.

**8. 실제 조회 승인 전에 완성할 검토 자료**

호출부 오프라인 구현·검사 후에만 아래를 한 번에 제시한다.

1. 호출부와 의존 코드의 확정 경로·해시, 기존 Python 실행 경로와 의존성 사용 가능 여부, 오프라인 영향 검사 결과. 기존 문서에 기록된 Python 후보는 `C:\Users\xiix1\AppData\Local\Programs\Python\Python311\python.exe`이며 이번 실행 확인은 하지 않았다.
2. Staging 설정 원본의 정확한 경로·결합, 고정 credential service/profile, 결과 root, 보존할 참조 원본과 후보 수동 profile.
3. 새 run_id, scope와 호출 명세의 정확한 바이트·해시, S/E의 KST·UTC·epoch, 최대 7회·180초, 허용된 한 번의 로컬 token 읽기와 요청 전 동일성 확인 범위, 추가 쓰기 없는 결과 저장 범위.
4. 사용자가 검토할 실제 명령과 성공/실패 시 종료 행동. 설정/관문 문제가 있으면 해결됐다고 가정하지 않고 차단 사유로 제시.

그때 사용자가 실제 읽기 1회 범위를 승인하면 동일 범위의 Q1~Q7마다 다시 묻지 않는다. 승인되기 전에는 키체인 읽기나 첫 요청을 시작하지 않는다. 승인된 실행 중 새 설치·로그인 갱신·관문 변경이 필요해지면 중단하고 그 이유와 필요한 별도 범위를 설명한다. 자동으로 실행 범위를 바꾸지 않는다.

**사용자가 지금 할 일과 다음 지시**

현재는 Windows에서 아래 지시를 내리면 된다. 이 준비안을 iPad에 다시 보내 명세 승인을 반복할 필요는 없다.

> Windows 독립 조회 호출부를 이 준비안 범위에서 오프라인 구현해 주세요. 호출부·전송 제한·access token 전용 공급 경계·원본 참조 보존·1회 실행 차단을 한 묶음으로 구현하고 신규 영향 검사를 한 묶음으로 진행해 주세요. 실제 자격증명 접근·서버 조회·로그인 갱신·설치·관문/hold 변경·기존 완료 검사 재실행은 하지 마세요. 완료 후 실제 조회 1회 승인에 필요한 구체적 명령과 범위를 정리해 주세요.

iPad는 그동안 대기한다. Windows 호출부가 준비되면 Windows에서 실제 조회 범위의 승인 여부를 결정하고, 조회 결과를 검토한 뒤 필요한 전달 자료만 iPad에 보낸다. 지금 앱을 켜거나 로그인/수신 버튼을 누를 필요는 없다. 이 문서를 작성했다는 사실만으로 다음 구현이나 실제 실행을 시작하지 않는다.
