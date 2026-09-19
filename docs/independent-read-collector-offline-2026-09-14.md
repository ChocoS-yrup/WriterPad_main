# Windows 독립 조회 수집기 — 오프라인 구현·출력 계약

2026-09-14. 독립 수집기 구현과 합성 변경·영향 검사를 완료했다. 신규 제품 파일은 `isolated_read_collector.py`, 신규 검사 파일은 `tests/test_isolated_read_collector.py`다. 기존 집필 UI/store/동기화·53개 최종 결과를 수정하거나 재검사하지 않았다. 실제 세션 읽기·HTTP·설치·관문/hold 변경은 없다.

## API와 실행 경계

`collect(results_root, scope, metadata_bytes, orders_bytes, *, transport, access_token, api_key, clock=...)`는 비동기 함수다. 결과 경로·새 범위·기존 reference의 원래 파일 바이트·transport·자격정보를 모두 호출자가 명시해야 한다. 기본 live transport, 현재 세션 로더, 앱/store 연결, CLI는 제공하지 않는다. 이번에는 임시 합성 scope와 MockTransport만 사용했다. 향후 실서버 실행에는 별도 검토된 호출부와 실제 실행 권한이 필요하며, 함수를 구현했다는 사실이 실행 승인인 것은 아니다.

허용 요청은 고정 account GET 1회, handshake POST 1회, 고정 작품의 projects/project_sync_settings/documents/folders/tree_orders GET 각 1회다. max_requests는 정확히 7, max_seconds는 0 초과 180 이하, 유효한 절대 expires_at을 요구한다. 자동/쓰기/refresh/receipt/페이지 추가/redirect/재시도 API는 없다. 기존 종료 run ID는 거부한다.

새 `results_root/<run_id>/`만 생성한다. 이미 존재하는 run 폴더는 비어 있거나 실패한 상태여도 거부하며 재개·초기화하지 않는다. 경로의 심볼릭 링크/Windows reparse point를 거부한다. 호출자가 고정한 결과 namespace를 사용하는 설계이며, 다른 namespace까지 검색하는 전역 run registry는 없다. 향후 호출부는 결과 root와 승인된 scope 바이트를 함께 고정해야 한다.

전송 전에 별도 journal의 attempt를 flush/fsync한다. 실패·취소·HTTP 전 중단 후 사용량을 돌려주지 않는다. 전체 비동기 구간에 asyncio deadline을 적용하고, 요청별 timeout은 남은 시간과 15초 중 작은 값이다. 응답 chunk·검증 전후에도 벽시계/단조시계 deadline과 시계 역행을 확인한다. 시험에서는 느린 async transport를 취소해 종료되는 것을 확인했다. 동기 파일시스템 호출 자체를 OS에서 강제 중단하는 기능은 없으며, 협조적 취소를 무시하는 임의 transport의 동작까지 보장하지 않는다.

`inspect(directory)`는 기존 수집 결과를 읽기만 한다. scope·journal 체인·기록된 raw 응답·완료 observation의 해시를 검사하고 예약 사용량을 반환한다. 부분 journal은 보정하지 않고 차단한다. inspect는 네트워크나 수집 재개를 하지 않는다.

## 검증과 정지

- account/작품/소유자/계약·mode/epoch, 응답 JSON 중복 키·비유한 수, 상태코드, 테이블 count의 시작 위치/범위/전체 길이를 확인한다. count 누락/잘림은 더 읽지 않고 중단한다.
- 모든 raw node/order ID에서 새 후보와의 충돌을 확인한다. 특수 `__antigravity__` metadata도 raw로 보존하고 충돌 검사에 포함하지만 원고 node로 변환하지 않는다.
- 기존 reference와 문서/폴더/정렬을 비교한다. UTF-8 바이트·끝LF·본문 SHA를 보정하지 않는다. 누락·값 차이·대조되지 않는 일반 행·경로 차이는 검토할 차이로 기록하고 중단한다. 대조되지 않는 행은 ‘과거 자료와 매칭되지 않음’이지 과거 이후 새로 생성됐다는 단정이 아니다.
- 부모 순환/누락, 삭제 조상의 활성 자식, 정렬 중복·누락·자식 집합 불일치 등을 차단한다. 차이 보고는 ID와 필드 이름을 사용하며 본문을 넣지 않는다.
- raw 응답은 정확한 JSON 응답 body 바이트를 새 파일에 보존한다. 이는 HTTP 프레이밍 원본이 아니라 httpx의 응답 body 바이트다. 요청 header/토큰/키는 로그에 넣지 않으며 제공한 자격정보 문자열이 응답 body에 그대로 나타나면 해당 body 저장을 거부한다. 임의 민감정보를 자동 분류·완전 비식별화하는 기능은 아니므로 raw 응답 전체는 비공개 자료로 취급한다.

## iPad에 전달하는 출력 계약 v1

결과는 **관찰 자료**다. 성공도 `status=observed`, `execution_allowed=false`, `complete=false`, `baseline_ready=false`, `atomic_snapshot=false`를 유지한다. iPad adapter가 실제 baseline으로 직접 적용하면 안 된다. 계정/RLS 가시 범위와 순차 조회 한계를 함께 읽어야 한다.

| 파일 | 의미 |
| --- | --- |
| scope.json | 새 호출에 제공된 범위의 사본. endpoint/account/project, run_id, 한도/만료, reference 원래 바이트 해시. 현재 실사용 범위는 발급하지 않음 |
| journal.jsonl | opened → attempt → response → validated → 마지막 observed/stopped. 중단 시 중간 단계만 남을 수 있음. attempt가 HTTP 차감 근거 |
| Q1.body~Q7.body | 받은 응답 body 원문. 실패 지점에 따라 일부만 존재. 인증 응답/기존 원고를 포함할 수 있어 실제 자료는 비공개 |
| observation.json | 판정·정지 이유·비밀정보 없는 metadata/order·차이·사용량·시간·가시 범위 |

`observation.json`의 정확한 필드와 nullable/부분 수집 조건은 전달 ZIP의 `observation.schema.json`에 정의한다. node의 name/parent/revision 등은 관찰값이고, path는 폴더 수집 후 parent/name으로 계산한다. 따라서 중단 결과의 node에는 path가 없을 수 있다. document의 observed_relative_path는 서버 응답 필드이며 계산 path와 일치 여부를 따로 검사한다. raw content는 observation에 넣지 않는다.

`reference_sha256.metadata`와 `.orders`는 각각 입력 before-metadata.json과 before-orders.json의 원래 전체 바이트 해시다. 과거 파일을 다시 인코딩해 해시를 바꾸지 않는다. journal의 response는 request 번호, 고정 파일명, body SHA/길이, HTTP status, 시각, 제한된 문자 형식의 Content-Range와 그 상태를 담는다. validated의 count는 정확한 길이 대조에 통과한 테이블 행 수다. 과거 오류코드/플랫폼 state 형식을 그대로 적용하지 않는다.

journal 각 행은 sequence/previous/event/data/sha256을 포함한다. sha256 계산은 sha256 필드를 뺀 JSON을 Python `ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False`로 인코딩하고 끝 LF를 붙인 UTF-8 바이트에 적용한다. 이 체인은 Windows 내부 감사 형식이다. iPad가 임의 JSONEncoder로 다시 인코딩해 같은 해시가 나온다고 가정하지 않는다. 전달 ZIP의 SHA256SUMS는 파일 원래 바이트 검토용이며 실제 수집 자료의 출처 진위/최신성 증명은 아니다.

독립 수집기에는 적용·자동 회복·전체 앱 상태를 이관하는 기능이 없다. 실제 adapter 결합 전에 iPad가 지원하는 입력의 누락·부분 결과·path·scope/time·row count 의미를 대조해야 한다. J04 복구 조건·J05 저장 단계·J07 UI/backend 차이는 이 출력 형식으로 변경하지 않는다.

## 오프라인 검사 결과

수집기 전용 기본 묶음 20개 통과 후, 출력 scope/observation 해시 결합과 Count-Range 진단을 보완해 영향받는 5개와 신규 1개를 다시 검사했고 모두 통과했다. **고유 21개 통과**이며 26개로 합산하지 않는다. 기존 Windows 53개나 iPad 49개에도 합산하지 않는다.

최초 시도는 테스트의 socket 차단이 Windows asyncio 내부 socketpair 생성까지 막아 20개 모두 수집기 검사에 진입하기 전에 실패했다. 해당 로그/result.json은 보존했다. 테스트용 이벤트 루프를 먼저 만들고 검사 구간의 socket/HTTPTransport 차단을 유지하도록 runner만 조정했다. 이후 result-02.json(20개), result-03.json(영향 6개)이 통과 결과다. 내부 이벤트 루프 통신은 외부 서비스 조회가 아니며 실제 서버 HTTP는 0이다.

증거 폴더는 `_evidence/independent-read-collector-20260914/`다. 패키지에 합성 출력 예제도 제공한다. 예제의 시간·run ID·본문·scope는 합성 fixture이며, 실제 실행 범위나 실제 원고·현재 서버 자료가 아니다. 예제 생성은 검사 수에 추가하지 않는다.

## 사용자가 지금 할 일

전달 ZIP을 iPad 측에 보내고 다음 문구를 전달한다.

> Windows 독립 조회 수집기와 출력 계약 v1입니다. observation schema와 합성 예제를 iPad 제안 reader/초기 수신 adapter에 오프라인 대조해 주세요. 이 자료는 baseline이 아닌 관찰 자료이며 complete=false를 유지합니다. 필요한 필드·부분 수집·정렬·원문 해시 매핑 차이만 알려 주세요. 실제 조회·로그인·서명·설치·관문/hold 변경은 하지 않습니다.

iPad 회신이 오면 Windows에 전달한다. 출력 계약의 차이를 먼저 해소한 뒤, 유효 세션과 결과 root를 고정하는 실제 호출부 및 조회 승인 범위를 구체화한다. 지금 사용자가 앱을 켜거나 로그인/수신 버튼을 누를 필요는 없다.
