# iPad snapshot 보완 검토 완료 — 2026-09-11

**이전 검토의 남은 두 항목을 종결한다.** 적용 전 전체 snapshot 보존 검사와 `LocalDocumentStore.swift` 완전 소스가 보완됐다. 이번 범위의 추가 iPad 코드 수정이나 같은 내용을 확인하는 회신을 요청하지 않는다.

이는 소스·제공 검사 근거에 대한 수용이다. 실제 설치, 실서버 양방향 성공, 앱 전체 완료 또는 실행 승인을 뜻하지 않는다. 첨부 README의 설치/송신 절차는 제안이며 현재 요청과 구분했다. 이번에는 로컬 첨부 검토와 증거 기록만 수행했다.

## 입력과 독립 확인

입력 `ipad-bidirectional-snapshot-review-response-20260911.zip`: **90,026 bytes**, SHA-256 `9255f687ea950896e5a7613a26898d010e485baf43c5216cc6e9ce97b1461257`.

- manifest 22파일/ZIP 23멤버의 정확한 구성·중복/경로/링크 부재·CRC·크기·SHA를 확인하고 원본 사본/추출 자료를 새 디렉터리에 보존했다.
- 228파일 source manifest digest `49904443d9b393b5647a0f134be69116099f13b4c992f672fcaf6f918ec35898`를 독립 재계산했다.
- 이전 후보와 달라진 파일은 `BodyValidationService.swift`, `SyncV2SnapshotPullService.swift`, `BodyValidationServiceTests.swift` 3개뿐이다. 각 before hash를 이전 manifest와 대조하고 패치를 사본에 재생해 제공 after 완전 소스와 바이트 단위로 일치함을 확인했다.
- `LocalDocumentStore.swift` 완전 소스 SHA는 요청한 `2985c12d3108cf5947624fbbdc45b584aeeabb494d7efe6761f1e675b39928ee`와 일치한다. 이전 단계에서 확보하지 못했던 이 파일만 역방향 패치 재생하여 before SHA `5e58f347d71adec4f53da68667431dbf4bbe0b5a63e8a3ab622d36ab7672c08d`도 확인했다. 이미 완료한 다른 14파일의 검사는 반복하지 않았다.
- 제공한 고정 2문서/11폴더 baseline을 Windows의 보존된 `target-baseline.json`과 항목/필드별로 대조했다. 실제 서버를 새로 읽은 결과가 아니다.

## 수신 보존 검사 수용 근거

`SyncV2SnapshotPullService.performPull`은 활성 본문 검증 run에서 먼저 `BodyValidationSnapshot.capture`를 호출한다. 이 호출은 manifest metadata 채택, `preparePull`, 폴더 및 원고 적용, baseline 저장보다 앞에 있다.

capture에서 확인하는 내용:

- 문서는 정확히 원고/정렬 2개이며 누락·추가·중복 UUID가 없어야 한다.
- 지정 원고는 revision 2·127 bytes에 해당하는 정확한 UTF-8 본문과 경로여야 한다.
- 정렬 문서는 UUID `ef6e1de1-a3d0-5959-96be-58f87a683cc0`, 고정 내부 경로, revision 1, 385 bytes 및 기존 SHA여야 한다.
- 두 문서의 삭제 상태와 nullable 구조 필드를 보존한다.
- 폴더는 고정 11 UUID 집합, 부모/이름/revision 1/비삭제 상태와 일치해야 한다. UUID tree_orders는 비어 있어야 한다.

검사에 통과한 문서/폴더 배열은 `private let`에 저장된다. 이 snapshot에는 live client를 저장하지 않는다. 이후 기존 pull의 manifest·필요 본문·폴더·정렬 조회는 지역 변수 `client`로 고정된 snapshot을 사용한다. 프로토콜의 기본 manifest/hydration 구현도 snapshot 자신의 `fetchDocuments`로 돌아오므로 실제 서버를 다시 읽지 않는다. 일반 수신은 원래 client를 그대로 사용한다.

따라서 지난 검토에서 지적한 **별도 사전 조회와 실제 적용 입력 사이의 틈**을 메웠다. 서버의 3종 GET이 한 transaction이라는 보장은 주장하지 않는다. 받아들일 수 있는 내용 자체를 고정하고 실제 적용 입력을 고정하는 방식이다.

시작 진단 로그와 메모리 실행/coordination 상태는 검사 전에 생길 수 있다. ‘원고/metadata/baseline 적용 전에 거부’를 ‘어떤 로컬 파일에도 쓰기가 전혀 없음’으로 확대하지 않는다. 폴더 갱신 시각은 고정 의미 필드에 포함하지 않는다.

## 완전 소스 검토

누락됐던 `LocalDocumentStore`의 비교 저장·취소 상태 공유·문서별 저장 순서·최종 TXT 교체·복구 marker·durable handoff 흐름을 읽었다. 이전 패치의 mutation 보호가 최종 교체와 기록 발행에 연결되어 있으며, 비교 저장 실패/후속 metadata 실패를 미저장으로 오인하지 않고 남은 자료를 보존하는 경로를 확인했다.

이번 delta는 해당 파일을 수정하지 않는다. 파일/근거 확보가 끝났으므로 추가 소스 회신을 요청하지 않는다.

## 검사 보고 및 재현 범위

iPad는 최종 고정 소스로 **36개 + 보호 플래그 선택 1개, 총 37개 통과·실패 0**을 보고했다. 이름 37개의 수량·중복 부재·각 run/source digest·후보 식별의 일관성을 확인했다. 이전 151개 통과를 더해 이번 후보의 188개 실행이라고 보고하지 않는다.

새 서비스 검사 6개는 고정 fixture의 2문서/11폴더, 구조/본문 변동과 조회 실패 등 26개 거부 입력, 사전 원고 조회 이후 변경, 검사한 내용 재사용을 다룬다. 코드상 실패 시 모든 sync 테이블 행·대상/다른 작품 metadata·workspace 파일 bytes 보존과 fake 데이터 쓰기 RPC 0을 assertion으로 확인한다. 정상 수신에서 live 전체 문서 fetch 1회/hydration 0회와 저장 본문을 확인하는 assertion도 읽었다.

Windows에서 Swift/Simulator/실기기/빌드를 다시 실행하지 않았다. 원시 xcresult/빌드 로그/설치용 앱 ZIP은 공유 묶음에 없으므로 검사·바이너리·서명 검증은 iPad 보고와 소스/manifest 대조까지다. 기존 수신·UUID 교정·해시 보완·Windows 기존 검사도 반복하지 않았다.

## 최신 iPad 후보 식별

| 항목 | 최신 보고 |
|---|---|
| 후보 | `ipad-staging-body-snapshot-scope-20260911` |
| bundle / 환경 | `com.chocos.writerpad.debug` / Debug Staging / arm64 |
| 버전 | 0.1.0(1) |
| source digest | `49904443d9b393b5647a0f134be69116099f13b4c992f672fcaf6f918ec35898` |
| app ZIP SHA | `4272d1776a99857ff7c9ecaf8cf4847c5890e7b1f0741cd8ffb785cca4279d67` |
| app ZIP bytes | 28,939,288 |
| app tree digest | `dfe3c86e1a770c4f266d8644fc8e62e68dc05d3d842c89642d89b905b9d6c69a` |
| 설치 상태 | 미설치 보고 |
| 프로필 만료 보고 | 2026-09-12 18:19:25 KST |

기존 프로필의 서명·CMS 내용 검증은 보고됐으며 인증서 체인/온라인 폐기 확인은 수행하지 않았다는 한계를 유지한다. 실제 설치일에 후보·프로필 유효성을 확인해야 한다. 이 기록은 재서명/갱신/신뢰 변경 승인이 아니다.

## 유지할 실행안과 다음 작업

- 대상은 LEGACY 본문 작품/원고 그대로다. revision 1→2→3, 93→127→158 bytes, 확정 제안 문구와 SHA를 유지한다. ID_BASED 일반 시험 작품/기존 binding/hold 및 최초가져오기 작품은 변경하지 않는다.
- Windows acquire 요청 90초, iPad acquire 요청 60초를 구분한다. 보존된 commit 정의의 90초 연장을 현재 서버 확인으로 취급하지 않는다. 실제 release 성공 또는 확인한 만료 뒤 다음 방향을 진행한다.
- iPad는 같은 foreground 시험 화면에서 revision 2 수신 완료를 확인하고 승인 범위의 송신 버튼을 사용한다. 화면 이탈/비활성화는 권한을 무효화하므로 중간에 원고 화면으로 나갔다가 바로 송신하는 절차는 넣지 않는다. 최종 5줄 오프라인 관찰은 송신/lease 정리 뒤에 한다. 수신 성공 메시지가 본문/전체 해시를 직접 표시한다고 표현하지 않는다.

**다음은 Windows 제품 연결·격리 검증·후보 빌드다.** 현재 Windows의 `bidirectional_sync_scope.py`와 15개 격리 검사는 오프라인 기준 구현이며 실제 앱의 최종 HTTP/큐/lease/수신 보호 연결을 완료한 것이 아니다. 전역 기본 잠금, 지정 합성 본문의 명시적 전용 동작, 기존 제품 경로 재사용, 재시작/응답 불명 보호와 검사한 snapshot의 그대로 적용을 연결한 뒤 후보를 만들어야 한다.

Windows 후보가 준비되면 양쪽 정확한 후보·새 일관된 백업·설치·각 방향 송신 1회·lease 종료·수신/관찰·사후 감사/잠금/종료를 하나의 구체 실행 범위로 승인받는다. 아직 설치/송신 승인을 요청하지 않는다. 승인된 같은 범위의 세부 버튼마다 재승인을 추가하지 않는다.

**지금 사용자가 할 앱 조작이나 추가 iPad 전달/회신은 없다.** 종료·오프라인 상태를 유지한다. 확인 완료를 전달받기 위한 새 ZIP 왕복도 만들지 않는다. 기존 작업 트리 변경과 증거는 보존했고 실제 설치·송신·관문/hold 변경·prod 전환은 하지 않았다.

근거: `_evidence/ipad-bidirectional-snapshot-review-20260911/input-and-patch-verification.json`, `cross-verification.json`, `received/` 및 `verify_response.py`.
