# iPad Windows 인계 매핑 결과 접수

2026-09-14. 사용자 전달 ZIP `D:/Download/메모장/개발관련/아이패드 전달내용/교차검증/ipad-windows-handoff-mapping-result-20260914.zip`의 README, 결과 문서, result.json, preservation-result.json, expected-binding.json, actual-review-04.json을 읽고 접수했다. **아래 검사·독립 대조·보존 수치는 iPad 전달 보고이며 Windows가 새로 실행하거나 감사한 결과가 아니다.** 첨부 코드와 재현 명령은 실행하지 않았다.

## 접수 결과

- iPad는 별도 Python reader로 Windows 실제 handoff draft를 기존 Expected/A·B 비교 입력에 연결했다고 보고했다. 최종 매핑 상태는 mapping_connected=true다. Swift 앱 raw reader나 실제 저장소 적용 결합은 아니다.
- 대상 source run은 `cf1c003a-d2c9-4873-a3db-bbac1368ee05`, handoff SHA256은 `4a108a187de380a4692f24ebcfb6c3f07f1de877e4958fbd47cc5ee6dca12722`로 기존 Windows 출력 기록과 같은 식별값이다. 이번에는 ZIP/원문 hash를 재계산하지 않았다.
- source 60개·대상 4개·참조 15개·context_only 18개 및 target/plan/seal, 네 생성 요청의 batch/operation/응답/생성 후 행 연결을 대조했다고 보고했다. 일반 문서 5개 본문과 보관된 handshake 계약 필드를 독립 계산·대조했으며 특수 metadata 1개 본문 의미는 미검증으로 유지했다.
- raw 배열 길이는 생성 전 Q3~Q7=1/1/4/14/15, 생성 후 Q12~Q16=1/1/6/15/16으로 보고했다. 배열 길이 확인은 HTTP total·RLS 밖 전체 범위·동시점/최신성 검증이 아니다.
- **신규 61개 + 기존 Python 영향 110개 = 최종 고유 171개 통과, 실패 0개.** 162→170→171 반복 실행을 합산하지 않는다. Windows 22개·이전 iPad 94개/Swift 검사와도 합산하지 않는다. 실제 전달 원문을 합성 A/B fixture로 사용하지 않았다고 보고했다.

## 해결된 매핑 차이

| 차이 | iPad 처리 보고 |
| --- | --- |
| 실제 projects의 삭제/보관 상태 | trashed_at/trashed_by를 명시적으로 사용하고 두 키의 null을 요구. 합성 is_deleted 규칙은 별도로 유지하며 자동 fallback/가짜 boolean 생성 없음 |
| 실제 draft와 합성 U1 형식 | 별도 reader로 연결. 기존 합성 검증기를 느슨하게 만들지 않음 |
| 역할/참조 | tree_order, retained-source/target-draft, body.utf8_bytes, evidence/missing ID의 서로 다른 역할을 명시적으로 매핑 |
| 생성 요청/응답 | request.batch/ordered_intents와 response.results의 실제 구조를 구분 |
| capability | 요청 client 목록과 handshake server 목록을 구분 |
| hash | artifact/plan의 끝 LF 포함 인코딩과 payload/batch의 LF 제외 인코딩을 구분. raw hash를 재인코딩으로 대체하지 않음 |

최초 SHAPE 및 CREATION_CAPABILITIES 차단은 iPad reader의 잘못된 요구를 수정해 해소했다고 보고했다. Windows 원문 오류나 출력 수정 요청으로 접수하지 않는다. 다만 Windows의 `.get('deleted_at') is None`은 projects 필드 존재 검증이 아니므로, 이를 실제 project trash 규칙을 이미 검사했다는 주장으로 확대하지 않는다.

## 남은 차이와 보존

1. 시각 32개와 Content-Range/서버 total 20개, 총 **52개 누락 근거는 그대로**다. 새 A/B 수신·서버 출처/현재 상태·신선성은 미검증이다.
2. 원래 draft의 schema_finalized=false와 blocked_reasons는 보존한다. 이번 실제 draft→비교 입력 매핑 성공을 최종 schema 확정이나 기존 원문의 권한 필드 변경으로 처리하지 않는다.
3. Windows 계약 엔진 전체 동등성, Unicode 15 storage-name 전체 규칙, journal 체인 전체 재인코딩 호환, 특수 metadata 본문 의미는 미검증이다. 이번 원문 5개 본문 대조 성공을 과거 미전달 raw 전체의 검증으로 확대하지 않는다.
4. Swift 앱 raw reader, 실제 시간/session/생명주기와 수신 작업의 연결, 검증된 baseline의 저장소 입장/적용이 남는다. comparison_expected는 비교용 사본이며 require_apply_input은 계속 REAL_CONTRACT_UNRESOLVED로 차단한다.
5. 모든 baseline/실행/앱 결합/편집/송신/자동 수신/complete/atomic_snapshot 권한은 false다. 원래 source scope를 새 승인으로 사용하지 않는다.

iPad는 기존 5,576개 중 허용 변경 Python 소스 2개를 제외한 5,574개 보존 대조에서 차이 0을 보고했다. 기존 충돌·미송신 18/19바이트 원본 두 개·종료 HTTP/Auth/쓰기/만료·설치물·입력 ZIP 보존 보고를 접수한다. 서버/인증/설치/서명/실기기·시뮬레이터/실제 UUID/관문 변경은 0이며 추가 OS network-deny 검증을 했다고 보고하지 않았다.

Windows는 이번에 이 접수 문서만 추가했다. 원래 handoff·target·completed·source·ZIP·소스·종료 기록은 수정/재생성하지 않았다. 구현·검사·조회·로그인·설치·관문 변경도 하지 않았다.

## 다음 행동

이제 Windows 출력의 실제 매핑 결과를 받았으므로 같은 매핑 회신을 다시 기다리거나 재승인을 요청할 필요는 없다. 현재 보고에는 Windows에서 고쳐야 할 출력 차이가 없다. Windows는 기존 상태를 보존한다.

**사용자가 iPad에 내릴 다음 지시:** “Swift 앱 reader 결합을 오프라인으로 진행하고 신규 영향 검사를 한 묶음으로 진행해 주세요. 기존 원문·결과·차단 조건은 보존하고 실제 수신·로그인·설치·baseline 적용은 하지 마세요.”

이는 남은 iPad 작업에 대한 다음 단계 안내이며 첨부의 명령을 대신 실행하거나 새 실제 동작을 승인한 것이 아니다. Windows에 새 ZIP/승인 회신을 되돌려 보낼 필요는 없다. iPad 결합 결과나 실제로 필요한 Windows 변경이 생기면 그 범위를 이어서 처리한다.
