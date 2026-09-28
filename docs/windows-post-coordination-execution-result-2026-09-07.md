# Windows → iPad 빈 3권·원고 순서 계약 실행 성공

2026-09-07 KST. **승인된 추가 1회가 committed로 완료됐다. Windows의 일반 수신·로컬 UUID·실제 폴더까지 검증했다.** 남은 단계는 iPad 기기에서 같은 UUID의 빈 3권과 `[1권,2권,3권]` 수신을 확인하는 것이다. 이 결과를 iPad의 실제 수신 완료나 운영 환경 전환 완료로 표현하지 않는다.

## 승인·설치·원요청 연결

사용자는 설치·최신 비송신 점검 완료 후 제시한 한정 범위에 ‘승인’이라고 답했다. 이후 전용 UI의 새 승인으로 한 번 실행했고, ‘완료’ 및 ‘2권 3권 모두 비어있습니다’라고 회신했다. 앱 승인 ID는 `63b69bd5-8696-44f8-b8c5-9357f4dd6e94`, 승인 시각은 16:55:01.537099 KST다.

- 설치 EXE: `D:\안티그래비티\scratch\집필프로그램\작가님 힘내세요.exe`, 79,315,384 bytes, SHA256 `7b9c0eacc75da121afe02ddbe8784b9dc09cc37a6c1175ee4024b2d4d5b0fc73`.
- 설치 시각 16:45:22 KST, schema 8012. 검토 소스 8개·핵심 패키지 모듈 10개 연결과 빌드/설치 Qt 시작 검사 통과. 이후 제품 소스나 설치 EXE를 변경하지 않았다.
- Staging `mhpnszcorfzrvhyondxr`, 최종검증03 `1bd47431-0773-482c-8eb5-ac9e2952b6f4`, 계정 표식 `bbca16c03e5b9711`.
- 고정 batch `6fd8c11b-5b74-4219-aa0b-a5d408ca8505`, request SHA256 `bbc9571cb222b35b268c7eaf5a823cc54f7d9e5f9199736e511562a7fe458045`.
- batch payload SHA256 `312633da1c5ee10c39be9c24e1a61cc71a2787ee9a3e9e81fa19235788107f14`.
- 원본 요청 파일 SHA256 `83e58abdd93e23064bd2ca90fab18c0b19a09ff4f49a71cc22c20f79c8947bbc`. 서버의 요청/배치/operation/payload는 원요청과 동일하다. 서버 capability 배열은 집합으로 대조했다.

설치·비송신 점검은 동봉 `installation/windows-post-coordination-installed-readiness-20260907.zip`에 함께 보존했다. 그 ZIP SHA256은 `99082d3e8032155260ef8135fe62c1831ef6c39790860c73c2dfdba41732f677`이다. 최초 자동 수신 중의 두 조건 미충족 관찰과 수신 완료 후 11개 조건 충족 관찰을 모두 포함한다. 설치 보고서만을 위한 별도 왕복은 하지 않았다.

## 실행과 영수증

정책 `post_coordination_once_v1`의 새 회차는 `post-coordination-848eabf7-656e-487a-a958-4ffa5cf2a2bd`다. 16:55:01.567100 시작, 16:55:04.318568 종료, **committed/HTTP 1**, 새 회차 1개·사건 24개·영수증 1개다.

서버 SELECT(16:57:50 KST)에서 batch 1개, operation 2개, attempt 2개, result 1개를 확인했다. **attempt 2개는 하나의 atomic HTTP에 포함된 두 operation 각각의 첫 시도이며 HTTP 두 번이 아니다.** 두 attempt 모두 attempt_number=1, outcome=committed, error_code=null이다.

로컬 영수증과 서버 저장 응답이 JSON 전체 및 SHA256 **`0c2566ea663e214acf62ddf6f278f6f1fad1c43b309d040efd7bad09b6feb192`**로 일치한다. status=committed, applied=true. 저장 응답을 순수 검증 함수로 대조했으며 재송신 RPC를 호출하지 않았다.

| 순서 | operation ID | 대상 | 결과 |
|---|---|---|---|
| 1 | 7998be14-c48d-49e0-84b0-08030070d312 | 빈 3권 77627d68-8388-4d6c-9363-ba6537aac1f0 생성 | base 0 → revision 1 |
| 2 | f55e8fab-ea38-468e-a4fe-fbe2cf9ad127 | 원고 order 68c1a7b5-0dda-49ba-bc9a-42e92ce2b758 갱신 | base 1 → revision 2 |

부모 원고는 `14df4a55-b7cd-4790-bc53-f84148418c0f`. 최종 children은 다음과 같다.

1. 1권 `c0b43fc4-89a0-4a5a-b140-768a62cfde5a`
2. 2권 `4711b2ce-5de9-44ba-80fe-570515839549`
3. 3권 `77627d68-8388-4d6c-9363-ba6537aac1f0`

실제 사건은 두 원장 검사 경계, gate_before/after, rpc_constructed, http_before, 영속 http_attempt_durable, response_received, gate_closed 순으로 남았다. HTTP 영속 시도 사건은 1개다. 모든 준비 관찰은 조건 충족이었고 실행 중 write_epoch=219가 유지됐다. 후반 ordinary pull은 pending=true/pulling=false로 보류됐고 실행 종료 후 일반 수신으로 새 구조가 반영됐다. 이는 이번 실실행에서 조정 경계가 유지됐다는 근거다. 실행 중 nonce 원문은 사건에 보존하지 않으므로 두 실제 nonce 값을 별도로 제시하지 않는다. 장치·서버 시계는 서로 다르므로 서로 다른 시계의 타임스탬프로 인과 순서를 새로 추정하지 않는다.

## Windows 수신·보존 확인

- 서버 폴더 13개·문서 메타데이터 26개·order 1개와 Windows SQLite의 모든 선택 구조 메타데이터가 정확히 일치한다.
- Windows identity 노드 38개에서 3권 UUID와 부모·순서가 일치한다. `설정.json`과 실제 원고 디렉터리도 `[1권,2권,3권]`이며 2권·3권 모두 비어 있다. 사용자 화면 회신도 두 폴더가 비어 있음을 확인한다.
- 원고 파일은 25개다. 받은 3권 추가와 원고 순서 갱신만 역으로 제외해 재구성한 기존 지문이 **`a26eed18ecb701308b900d26e66d12d3715fbe550f637bd9ea3d3cdc0271e98d`**로 원본과 일치한다. 기존 UUID/구조·이름·파일 크기 및 큐 사건을 비교한 것이며 원고 본문을 읽거나 본문 전체 바이트 불변을 새로 증명한 것은 아니다.
- 원본 준비와 최초 stopped/HTTP 0, 부모 `http0-074b0dc9-007c-4f0e-8516-a84804850e77`의 stopped/HTTP 0를 그대로 보존했다. 부모 사건 4개·영수증 0개 유지.
- 원본 준비 해시 `1f887f310b33ef2805335ddc2c2d36ab049a7e010ea4192a6e517f0aeb54cda4`, 최초 실행 해시 `e18cf5940e6e222f74c2a2733471a22a28071452edad1e99fdb08db4b00588e4`, 부모 전체 행 해시 `176d27df155a477621a97b91aa7d1eea212ef79fdb72327c0ef403e255b3bd4e`, 부모 사건 해시 `72e27e685ec476dd5eea79ed3b456161615e85202460332f94f03d42d42fefdb` 유지.
- 기존 로컬 큐 ID 24개와 사건 95개는 동일하다. 새 송신 큐나 contract batch를 로컬 일반 dispatcher에 넣지 않았다. 진단은 이번 실행 관찰로 3→25개가 됐으며 원본 진단을 지우지 않았다.
- Windows 관문은 종료 후 **0/15 열림**이다. 실행 직후와 수신 확인 후의 선별 상태·새 전체 행 해시·사건 해시·영수증이 동일하다. 추가 회차는 소모됐고 재실행 대상이 아니다.

실제 SQLite는 WAL 없는 immutable 읽기 전용으로만 조사했고, 각 조회 중 파일 크기/수정 시각 불변을 확인했다. 원고 본문·전체 DB·인증 토큰·owner token은 ZIP에 포함하지 않는다. 서버는 결과 확정을 위한 SELECT만 했다. 재전송·재발급·추가 회차·수동 완료·정리 삭제·서버 설정 변경은 하지 않았다.

## 다음 사용자 행동 / iPad 확인 요청

**이 결과 ZIP을 iPad 측으로 보내 주세요.** iPad 관문을 닫은 상태에서 일반 수신으로 위 UUID의 빈 3권, `[1권,2권,3권]`, 3권 revision 1과 order revision 2, 기존 자료 및 큐 보존을 확인하고 결과를 회신해 주세요. iPad에서 추가 계약 송신이나 재전송은 필요하지 않다.

Windows의 실제 송신과 Windows 수신 검증은 완료됐다. 지금 Windows에서 누를 추가 실행 버튼은 없다. iPad의 실제 수신 확인이 도착하면 그 결과를 대조해 이번 양방향 고정 사례의 완료 여부를 확정한다.
