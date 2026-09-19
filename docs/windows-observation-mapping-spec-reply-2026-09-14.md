# Windows 관찰 출력 v1 — 기존 자료·명세 회신

2026-09-14. 수신 문서: `ipad-windows-observation-mapping-review-2026-09-14.md`.

이번 사용자 승인 범위는 **기존 자료·명세 회신 작성**이다. iPad 문서의 후속 제안이나 과거 문서의 실행 승인을 새 구현·실제 조회 승인으로 취급하지 않는다. 기존 소스·문서·저장된 예제를 정적으로 읽고 이 문서만 추가했다. collect/inspect·테스트·예제 생성·파일/체인 해시 재검증은 실행하지 않았다. 아래 해시는 기존 기록의 전재이며 이번 재검증 결과가 아니다.

회신 결론: Q1~Q7 요청·count 명세는 재요청할 필요가 없다. scope/journal 해석은 아래와 같이 설명할 수 있다. **합성 reference 원본은 보존되지 않아 제공할 수 없으며, 예제의 reference 대조와 플랫폼 간 체인 해시 호환은 미검증으로 유지한다.** iPad 관찰 reader/초기 수신 adapter 구현이나 baseline 적용 완료를 의미하지 않는다.

**1. 기존 파일 위치와 합성 reference 누락**

Windows 프로젝트 루트는 `D:\안티그래비티\scratch\작가님 힘내세요`다. 아래 경로는 해당 루트 기준이며, iPad에서는 이미 받은 ZIP 내부의 동일 파일을 가리킨다.

| 기존 자료 | 프로젝트 내 위치 |
| --- | --- |
| 관찰 출력 패키지 | `output/windows-independent-read-collector-20260914.zip` 및 같은 이름의 풀린 폴더 |
| 정상 예제 | `output/windows-independent-read-collector-20260914/synthetic-examples/cc5501fd-466e-4a61-b9ee-812f1a1a7192/` |
| 중단 예제 | `output/windows-independent-read-collector-20260914/synthetic-examples/68100a7a-cf6a-4e53-bbd9-0d9e33a7de26/` |
| 기존 17 node / 15 order 참조 파일 | `output/windows-ipad-isolated-target-reply-20260913/before-metadata.json`, `before-orders.json` |
| 원래 후보 제안 | 위 20260913 폴더의 `target-proposal.json`, `orders-proposal.json`, `candidate-metadata.json`, `SHA256SUMS.json` |
| 요청·count 준비안 | `docs/windows-minimal-read-preparation-2026-09-14.md` |
| 수집기·fixture 사본 | 20260914 패키지의 `sources/isolated_read_collector.py`, `sources/test_isolated_read_collector.py` |
| 당시 패키징 소스 | `_evidence/independent-read-collector-20260914/package_reply.py` |

패키징 소스의 `examples()`와 fixture의 `run_collector()`는 `encoded(self.metadata)` / `encoded(self.orders)`를 메모리 바이트로 만들어 collect에 직접 전달했다. collect는 두 입력의 해시를 저장하지만 입력 바이트 자체를 파일로 보존하지 않는다. 관련 output/evidence 파일 목록에서도 두 합성 reference 파일은 확인되지 않았다. 따라서 **제공 가능한 정확한 합성 reference 원본 파일 위치가 없다.** fixture 재작성이나 예제 재생성으로 대체하지 않았다.

두 예제의 scope에 기록된 합성 입력 해시:

```text
metadata 981fa782dc03256d5d50ed562dfbd16bc4c9b24f1082f9253e1ed520f0d3d800
orders   a136837a6d3a14942fa23f1bfb17b917b84f1c7613c0dbaaae6ee86b1d8ee882
```

별개의 과거 17/15 파일에 기록된 해시 (`reference-format-check.json` 및 과거 `SHA256SUMS.json`):

```text
metadata 079df5ba95deda09744f7632fe6f6ead2fbe7cbc2c994a0ce02d9ff2f5b132a9
orders   9a51f220ab631ef58ddf033b63dd8da89df38f48dffc095df87a712cb3e95d6a
```

과거 17/15 자료는 합성 예제에 연결하지 않는다. 예제의 이벤트·부분 출력 형식은 읽을 수 있지만, 누락된 reference까지 포함한 독립 재현·대조 통과로 보고할 수 없다. 새 원본 보존 방식이나 추가 벡터를 마련하는 작업은 이번 범위 밖이다.

**2. scope 필드와 파일 결합**

다음은 현재 collect가 받는 scope의 정확한 키 집합이다. 모두 필수이고 null은 허용하지 않는다. bool은 정수·숫자 한도값으로 허용하지 않는다.

| 필드 | 형식·제약 |
| --- | --- |
| format | 문자열 `windows-isolated-read-scope-v1` |
| run_id | 정규 소문자 하이픈 UUID 문자열. 기존 종료 run은 거부 |
| endpoint | 문자열 `https://mhpnszcorfzrvhyondxr.supabase.co` |
| account_id | 문자열 `e487c6ea-1c2b-4a90-821e-91e8547106de` |
| project_id | 문자열 `d8f50b5f-ae0e-42f8-9296-5d5885a5b304` |
| max_requests | 정수 7 |
| max_seconds | 유한 int/float, 0 초과 180 이하 |
| expires_at | 유한 int/float, Unix epoch 초, 호출 started보다 미래 |
| reference_sha256 | 정확히 metadata/orders 두 키를 가진 객체. 각 값은 입력 원래 전체 바이트 SHA-256 소문자 64자리 문자열과 일치 |

`scope.project_id`는 `observation.server_project_id`에 대응한다. iPad local project ID를 발급·대체하는 값이 아니다. deadline은 `min(expires_at, started + max_seconds)`다. scope.json은 호출 객체를 Windows 인코딩 규칙으로 저장한 사본이지 외부 서명·승인 파일이 아니다. 정상/중단 예제의 1000/1180/1200 시각은 합성값이다.

파일 결합은 다음 순서다. 이는 기존 기록의 관계 설명이며 이번에 검증기를 실행했다는 뜻이 아니다.

1. 결과 디렉터리 이름과 `scope.run_id`가 같다. 신규 수집은 이미 있는 디렉터리를 비어 있어도 거부한다.
2. journal 첫 opened의 `scope_sha256`은 저장된 scope.json 전체 바이트 해시에 연결된다.
3. scope와 observation의 `reference_sha256`은 동일한 입력 metadata/orders의 원래 바이트 해시다. 이번 합성 예제는 원본 부재로 이 연결의 입력 쪽을 확인할 수 없다.
4. 각 response의 file/sha256/bytes는 해당 Qn.body의 전체 응답 body 바이트에 연결된다. HTTP 프레이밍 원본이나 개별 문서 content 해시가 아니다.
5. 마지막 observed/stopped의 `observation_sha256`은 observation.json 전체 바이트에 연결된다. observation은 마지막 이벤트보다 먼저 파일로 작성된다.
6. 후보 profile은 현재 scope/observation에 기계적으로 결합돼 있지 않다. 아래 5번의 명세 참조는 이 누락을 자동으로 보완하거나 과거 journal을 변경하지 않는다.

이 관계와 해시 체인은 출처 진위·현재 서버 최신성·서명을 증명하지 않는다. 고정 결과 namespace 밖까지 검색하는 전역 run registry도 없다.

**3. journal 행·event 필드와 부분 종료**

행은 `sequence`, `previous`, `event`, `data`, `sha256` 다섯 키로 생성된다. sequence는 1부터 연속 증가하는 정수다. previous는 첫 행에서 빈 문자열, 이후 직전 행 sha256이다. sha256은 소문자 64자리 문자열이다. event는 opened/attempt/response/validated/observed/stopped 여섯 종류다. 아래 표에 적힌 data 키는 모두 생성 시 필수이며 nullable은 명시된 경우에만 해당한다.

| event | data 키·형식 |
| --- | --- |
| opened | scope_sha256: SHA 문자열; started, deadline: 숫자 epoch 초 |
| attempt | request: 정수 1~7; method: GET 또는 POST; path: 고정 경로 문자열; params: Q1/Q2는 null, Q3~Q7은 project_id/select/limit 문자열 객체; body_sha256: 요청 body SHA 문자열; at: 숫자 epoch 초 |
| response | request: 정수 1~7; file: 해당 `Qn.body`; sha256: body SHA 문자열; bytes: 0 이상 정수; status: HTTP 상태 정수; at: 숫자 epoch 초; content_range: 문자열 또는 null; content_range_state: value/missing/invalid |
| validated | request: 정수 1~7; count: Q1/Q2는 null, Q3~Q7은 대조를 통과한 raw 배열 길이 정수 0~10000; at: 숫자 epoch 초 |
| observed / stopped | reason: observed는 null, stopped는 정지 코드 문자열; http_used: 정수 0~7; at: 숫자 epoch 초; observation_sha256: 전체 observation 파일 SHA 문자열 |

content_range는 길이 64 이하이며 정규식 `(\d+-\d+|\*)/(\d+|\*)`에 맞을 때만 원래 문자열을 기록하고 state=value로 둔다. header 부재는 null/missing, 형식 불일치는 null/invalid다. **value는 정확한 count 대조 통과를 뜻하지 않는다.** 예를 들어 전체 수가 `*`여도 이 기록 단계의 문자 형식에는 맞지만 이후 count 검사는 통과하지 못한다.

Q1/Q2의 params=null은 의도된 값이다. Q2 요청 body는 p_project_id/p_contract_sha256 두 필드이며 본문 자체는 journal에 없다. body_sha256은 실제 요청 body 바이트 기준이고, GET의 빈 body 해시와 구별한다. 요청 header·토큰·키는 journal에 기록하지 않는다.

정상 생성 순서는 `opened → (attempt n → response n → validated n), n=1..7 → observed`다. 다음 요청은 직전 validated 이후에만 진행된다. 중단 결과는 이 순서의 접두 부분 뒤 stopped가 올 수 있으며, 열린 직후 요청 없이 stopped가 오는 경우도 가능하다. Q7 validated 이후 최종 시간 검사 등에서 stopped가 될 수도 있다. observed는 7회 검증 후 정상 종료라는 수집기 판정이며 원격 기준본 완성 판정이 아니다.

| 요청별 기록 상태 | 읽는 의미 |
| --- | --- |
| unattempted | 유효하게 연결된 기록에 해당 attempt가 없음. 부분/손상 기록 자체의 완전성을 보장하는 말은 아님 |
| attempted | attempt만 있음. 전송 전 예약했으며, 실제 발송·응답 성공은 알 수 없음 |
| response_received | 연결된 response가 있음. 저장 응답의 존재이며 HTTP 성공·JSON/count/내용 검증 성공은 별도 |
| validated | 해당 요청의 상태·파싱 및 해당 단계 검사 완료 기록이 있음. 뒤 단계 그래프/정렬·전체 성공까지 뜻하지 않음 |

중단 예제는 Q1~Q4 validated, Q5 response_received, Q6/Q7 unattempted다. Q5 Content-Range `0-0/1`과 `REFERENCE_DIFFERENCE`가 기록됐지만 Q5 validated는 없다. 소스 순서상 count 검사 뒤 내용 대조에서 중단할 수 있다. orders=[]는 서버에 정렬이 없다는 뜻이 아니다. Q5의 validated만 있더라도 Q6 경로 그래프 검사는 아직 끝나지 않았을 수 있다.

프로세스 강제 종료·디스크 실패 등에서는 마지막 이벤트가 없거나 파일이 부분적으로 남을 수 있다. scope만 있는 경우, body 저장 후 response 기록 실패, observation 저장 후 마지막 이벤트 실패도 구별해야 한다. 이런 파일을 임의로 연결 완료 처리하지 않는다. 마지막 이벤트 없는 observation은 최종 결합 미확인이다. 마지막 이벤트 없으면 종료 시각 대신 마지막으로 기록된 시각만 제시하며, opened만 있으면 started를 기록 시각으로 구분한다. LF가 없는 부분 journal은 보정·절단·재생성하지 않는다. 모순된 순서·누락은 검토 미완료로 남긴다. 예약 사용량을 환급하거나 실행을 재개하지 않는다.

현재 `inspect()`는 체인, scope 파일 해시와 디렉터리 run_id, 기록된 response 파일 해시, 최종 observation 해시, attempt 번호 연속성과 최대 7을 확인하는 보조 도구다. **위 모든 event 필드 타입·상태 전이·상호 일치를 포괄 검증하는 schema validator는 아니다.** 마지막 이벤트가 없으면 마지막 event 이름을 terminal로 반환할 수 있고, 이것을 observed/stopped로 바꾸면 안 된다. inspect를 통과했다는 사실만으로 모든 매핑 조건 통과를 주장하지 않는다. 이번에는 inspect도 실행하지 않았다.

**4. 원래 파일 해시와 행 체인 인코딩**

파일 해시는 읽은 원래 전체 바이트에 SHA-256을 적용한다. JSON을 재인코딩하거나 줄바꿈·공백을 보정하지 않는다. 행 체인은 sha256 키를 제외한 네 키 객체에 대해 아래 기존 Python 함수를 적용한 바이트의 SHA-256이다.

```python
(json.dumps(value, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
```

행을 파일에 쓸 때는 계산한 sha256 키를 포함해 같은 함수로 인코딩한다. previous는 앞 행 전체 바이트 해시가 아니라 앞 행에 기록된 sha256이다. sequence와 previous도 행 해시 계산에 포함된다. Unicode 정규화는 수행하지 않는다. JSON escaping, 키 순서, 정수/소수·지수 표기, 음수 0 등은 Python 직렬화 동작에 의존한다. Swift 기본 JSONEncoder나 임의 canonical JSON 규칙과 동치라고 가정하지 않는다.

기존 두 예제는 정수 시각만 포함한다. 소수·escaping 등을 포괄하는 교차 플랫폼 예제 벡터나 합의된 독립 인코딩 명세는 제공된 자료에 없다. 이번에는 벡터를 생성하지 않았다. 따라서 iPad는 원래 파일 보관·필드 읽기와 체인 재계산 호환 여부를 나눠 표시하고, 후자는 미검증으로 유지한다.

**5. 후보 명세 연결에 대한 Windows 제안**

현재 v1을 읽는 동안에는 **검토한 수집기 v1의 고정 후보 profile을 명시적으로 참조하는 방식**을 제안한다. 이는 이번 회신의 해석 제안이며 양쪽 reader 구현·자동 결합이 완료됐다는 뜻이 아니다. profile의 기준은 이미 받은 패키지의 `sources/isolated_read_collector.py`다. 기존 result-03.json의 해당 소스 해시는 다음과 같다.

```text
09969eab3a826fbacb0a029b13b4aa27a9066b96b0fa56dcc120cc08f812046f
```

| profile 항목 | 고정값 |
| --- | --- |
| endpoint/account/project | 위 scope의 고정값 |
| 기대 작품 이름 | 일반동기화 검증 20260910 |
| 공유 부모 | 95b8e4d0-1d8d-4af5-b121-0888d0157661 |
| 새 root | 58ed531f-56a3-4279-b0da-d5dbe5209839 |
| 문서 후보 | 949e9332-ee27-4100-b7f1-0421341d165d |
| 빈 문서 후보 | 0eb1aece-7212-4e8d-a5a1-fa0dbeaa89db |
| 새 정렬 후보 | e3c2f57d-e116-5c5d-8941-1bcf0dfce53c |
| root 이름 | 자동수신저장 격리검증 20260913 |

기존 후보 제안과의 문서상 연결은 20260913 SHA256SUMS.json의 두 파일을 함께 참조해야 한다. target-proposal만으로는 새 정렬 후보까지 다 설명되지 않는다.

```text
target-proposal.json 56dd66a7f82dde4005c0e1085dc8d575be023866ef71183e7ee911ea6a19eb9f
orders-proposal.json eb820a0e2f9bd318d090885e1894b04d834ef0018fcdf53a0a78ba3e415b96c4
```

현재 코드는 Q5/Q6의 ID와 Q7 정렬 ID를 후보 4개와 대조하고, Q7 parent가 새 root인지도 확인한다. 공유 부모 밑에서 root 이름을 storage-name-v1으로 비교할 때는 활성 행의 이름 충돌을 거부한다. Q3는 기대 작품 이름을 검사한다. 특수 metadata 행도 ID 충돌 검사에 포함하지만 일반 원고 node에서 제외한다. 중단 시 후보 검사가 전부 끝난 것으로 해석하지 않는다.

`candidate_check` 문자열만으로 이 profile과의 독립 결합이나 후보 전역 부재를 증명할 수 없다. 현재 예제와 명세 간 연결은 소스·기존 제안을 함께 읽는 수동 설명 수준이다. 향후 기계적으로 결합할 manifest/profile hash 필드는 아직 없다. 이 문서로 기존 scope·journal에 그런 필드가 생긴 것으로 취급하지 않는다.

과거 target-proposal의 policy_difference/blockers에는 J02 보완 전 서술이 남아 있다. 위 파일은 후보 식별의 역사적 근거이며 현재 초안 대기 정책이나 새 실행 권한의 근거가 아니다. J02 이후 기록과 J04/J05/J07의 플랫폼 차이는 유지한다.

**6. 계약 검증 근거와 비공개 raw / metadata 전달 구분**

observation의 endpoint/account/project/contract_version/protocol_version은 기대 상수다. Q1 이전 실패에서도 출력될 수 있다. 실제 수집 단계의 검증 완료 주장은 해당 validated 및 연결된 raw 근거와 구분해서 읽는다.

| 단계 | 근거와 현재 소스가 검사하는 내용 |
| --- | --- |
| Q1 | 연결된 Q1.body와 validated: 객체 id가 기대 account와 일치 |
| Q2 | 연결된 Q2.body와 validated: 객체 또는 길이 1 배열, 기대 project, handshake 필드 형태·내부 일관성, 계약 SHA·지원 프로토콜·capability, ID_BASED/epoch 1 |
| Q3 | 연결된 Q3.body와 validated: project/owner/name/삭제 상태, 정확히 1행 및 count |
| Q4 | 연결된 Q4.body와 validated: 기대 project, ID_BASED/정수 epoch 1, 정확히 1행 및 count |

Q2는 `sync_contract.py`의 `read_handshake_compatibility`, `_require_coherent_handshake`, `require_server_compatibility`를 사용한다. server_protocol_version은 3 이상이어야 하고 supported_protocol_versions에 3과 서버 선언 버전이 있어야 한다. server_contract_sha256은 아래 고정값과 같고 canonical_contract_sha256도 그 값과 같아야 한다. contract_version은 0.2.0이어야 한다. capability는 아래 필수 집합을 포함해야 하며 추가 항목 자체를 거부하는 규칙은 아니다.

```text
416c1b99edb9bda694731dee4b25688d9d82d1f32610aa23ddfda571ec3c7670

atomic_structure_commit
contract_allowlist_validation
project_mode_migration_lock
folder_tombstones
id_tree_validation
legacy_epoch_zero_adapter
storage_name_v1
document_commit_v1
```

Windows의 전달 해석 제안은 다음과 같다.

- 비공개 근거 묶음: 같은 run의 scope+원본 journal+observation과 주장에 필요한 Qn.body·정확한 reference 원본을 함께 관리한다. Q1~Q4 계약/계정 검토와 Q5~Q7 본문·구조 검토에 필요한 근거 범위를 구별한다. 전체 수집 내용 검토는 전체 응답과 reference가 필요하다. 누락 파일은 원문 미제공으로 명시한다.
- metadata 전달본: scope/journal/observation만 전달하면 journal에 검증 완료 기록이 있다는 사실까지 읽을 수 있다. 원문 내용을 독립 대조한 것으로 표시하지 않는다. 계약 SHA·mode/epoch/capability를 observation의 기대값으로 채워 넣지 않는다. 비공개 raw를 제공하지 않는 항목은 원문 독립 검증 미완료로 유지한다.
- 별도 파생 계약 보고서·manifest는 현재 없다. 이번에는 새로운 파생 출력 형식이나 raw 복사·비식별화본을 만들지 않았다. 원래 raw에서 필드를 지운 파일은 다른 바이트이므로 원본 response 해시와 같다고 표시할 수 없다.

실제 Q1.body는 계정 정보, Q5.body는 본문 등을 포함할 수 있다. 현재 전달된 Qn.body는 합성 예제이므로 실제 계약·유효 세션·서버 현재 상태의 증거가 아니다. 행/파일 해시 일치만으로 서버에서 왔다는 사실까지 증명하지 않는다.

**7. 나머지 매핑 차이와 현재 종료 상태**

계산 path와 observed_relative_path는 별도이며 누락을 빈 문자열이나 다른 경로로 채우지 않는다. nodes/orders는 중단 시 일부만 있을 수 있다. count 분모는 각 Q3~Q7 raw 배열이며 특수 metadata를 제외한 nodes 수와 다르다. 첫 differences 이후 중단하므로 전체 차이 목록·적용 명령이 아니다. 정렬 children 순서는 보존한다.

http_used는 전송 전 attempt 예약 수, auth_user_used는 Q1 예약 여부다. Q2 POST는 HTTP 예약 1이며 문서/구조 쓰기 사용량이 아니다. Windows 관찰 사용량을 iPad 사용량이나 기존 종료 run으로 이관하지 않는다. deadline은 finished_at이 아니다. 성공한 순차 7회도 RLS 밖 부재·원자적 snapshot·생성 후 baseline을 증명하지 않는다.

execution_allowed/complete/baseline_ready/atomic_snapshot=false를 유지한다. Windows 기존 53건 최종 결과와 독립 수집기 고유 21건 기록은 기존 결과로 보존했으며 재실행·합산하지 않았다. 기존 미커밋 소스·설치물·본문·iPad 충돌/미송신 18·19바이트 원본 2개·종료 실행의 사용량과 만료는 변경하지 않았다. 서버 요청·로그인 갱신·서명·설치·관문/hold 변경·prod 전환은 하지 않았다.

**사용자가 지금 할 일**

이 회신 문서 한 개를 iPad 측에 전달한다. 기존 ZIP을 다시 만들거나 합성 reference 대신 과거 17/15 파일을 추가할 필요는 없다. 아래 문구를 함께 전달하면 된다.

> Windows 기존 자료·명세 회신입니다. 합성 reference 원본은 미보존으로 제공 불가이며 관련 대조는 미검증으로 유지합니다. scope/journal·부분 종료 해석, 수집기 v1 고정 후보 profile의 문서상 참조, raw 없는 계약 근거의 미검증 표기를 정리했습니다. 먼저 문서만 읽고 수용 가능한 해석과 남은 차이를 회신해 주세요. reader/adapter 구현·예제 재생성·재검사·실제 조회·로그인·서명·설치·관문/hold 변경은 시작하지 않습니다.

iPad는 이 문서 대조 회신까지만 진행하고, Windows는 그 회신을 기다린다. 사용자가 Windows/iPad 앱을 켜거나 수신 버튼을 누를 단계는 아니다. iPad 회신을 받으면 Windows에 전달한다.
