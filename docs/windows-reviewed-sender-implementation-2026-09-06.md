# Windows 회신 — 고정 검토 배치의 명시적 1회 송신 구현

2026-09-06 KST. iPad의 `ipad-windows-reverse-prepared-request-review-20260906.zip` 회신을 대조하고, 사용자의 “진행”에 따라 수동 송신 경로 구현과 격리 검증을 수행했다. 첨부 문서 자체를 실제 실행 승인으로 해석하지 않았다.

**빈 3권 생성 + 기존 원고 순서 갱신의 고정 요청을 처리하는 소스 구현을 완료했다. 실제 관문 개방·전송·재전송·서버 조회·빌드·설치는 수행하지 않았다. 현재 설치 제품은 기존 준비·내보내기 전용이다. 다음 단계는 iPad의 구현 검토 회신이다.**

| 원본 식별 | 값 |
|---|---|
| 작품 | 최종검증03 / `1bd47431-0773-482c-8eb5-ac9e2952b6f4` |
| 환경/계약 | Staging `mhpnszcorfzrvhyondxr`, LEGACY/0, protocol 3, contract 0.2.0 |
| 고정 batch | `6fd8c11b-5b74-4219-aa0b-a5d408ca8505` |
| request SHA256 | `bbc9571cb222b35b268c7eaf5a823cc54f7d9e5f9199736e511562a7fe458045` |
| batch payload SHA256 | `312633da1c5ee10c39be9c24e1a61cc71a2787ee9a3e9e81fa19235788107f14` |
| export 파일 SHA256 | `83e58abdd93e23064bd2ca90fab18c0b19a09ff4f49a71cc22c20f79c8947bbc` |
| canonical envelope SHA256 | `3de0b65559b658e2647191962e72e23bca03f2016486cc3cc2c39c47725223ae` |
| 로컬 기준 SHA256 | `a26eed18ecb701308b900d26e66d12d3715fbe550f637bd9ea3d3cdc0271e98d` |
| device / client build | `1d6bf0ad-86ff-43ce-a84a-d220134312e8` / `writerpad-windows-stage8-contract-0.2.0` |

sequence 1은 operation `7998be14-c48d-49e0-84b0-08030070d312`, 새 folder `77627d68-8388-4d6c-9363-ba6537aac1f0`, create/base 0이다. sequence 2는 operation `f55e8fab-ea38-468e-a4fe-fbe2cf9ad127`, 기존 tree_order `68c1a7b5-0dda-49ba-bc9a-42e92ce2b758`, reorder/base 1이다. 부모 원고와 children `[1권, 2권, 새 3권]`은 원본 그대로다. 예상 revision 1/2는 실제 서버 적용 결과가 아니다.

**구현한 동작**

1. 새 `reviewed_contract_sender.py`는 위 batch와 request SHA256이 일치하는 저장 원본만 받는다. 별도 명시적 승인 인자가 필요하며 원본 envelope의 review-only 정책을 수정하지 않는다. UI에는 기본 응답이 ‘아니요’인 별도 확인과 `검토 배치 1회 송신` 경로를 추가했다. 이 버튼은 현재 설치본에는 없다.
2. 원본 준비 테이블과 별도로 `sync_reviewed_executions`에 승인·요청 해시·시각·프로세스 식별·상태·HTTP 시도 여부·검증 영수증을 기록한다. 준비 원본의 update/delete 방지와 같은 ID의 일반 계약 배치 삽입 차단을 유지한다. 실행 원장을 pending 큐로 전환하지 않으며 자동 dispatcher와 일반 수동 재시도는 이 요청을 선택하지 않는다.
3. 실행 시작과 쓰기 HTTP 직전 구간에서 새 인증 handshake, active 상태, 인증 서버의 사용자 식별, 소유자 또는 owner/editor 권한, 기기/build, 로컬 기준, 폴더·문서 메타데이터·순서, 영속 큐를 확인한다. 메타데이터 조회는 작품으로 제한하고 exact count와 범위를 함께 검사해 누락·추가·잘린 응답을 거절한다. 이름과 UUID 부재도 원본의 전체 기준과 일치해야 한다. 본문을 조회하지 않는다.
4. `handshake_lifecycle.py`의 기존 C9, 쓰기 세대, 큐 이력 stamp, single-flight와 재시도 없는 계약 전송 함수를 재사용한다. 두 번째 서버 검사 뒤 RPC를 구성하고, 마지막 잠금 안에서 C9와 로컬 기준을 다시 검사한 다음 HTTP 시도를 먼저 영속 기록한다. 네트워크 실행을 기록 없이 시작하지 않는다.
5. 중복 클릭과 같은 요청의 두 번째 실행을 영속 PK로 차단한다. HTTP 전 중단은 `stopped`, HTTP 시도 뒤 결과 불명은 `uncertain`으로 남으며 둘 다 자동/수동 재시작 대상이 아니다. 실제 쓰기 직전 크래시도 보수적으로 시도한 것으로 취급한다. 새 프로세스는 이전 실행의 관문을 닫고 진행 중 상태를 중단/불명으로 복구하며 송신하지 않는다.
6. 응답은 원본 요청의 ID·hash·구조 영수증으로 검증하며 성공 revision도 1/2여야 한다. 성공은 `committed`, 거절은 `rejected`로 원래 저장소에 별도 보존한다. 늦은 응답은 화면이 다른 작품으로 이동해도 원래 저장소에 기록한다. 종료 시 캡처한 작품의 관문을 닫고 닫힘을 재조회한다. 로컬 3권이나 순서 projection은 생성하지 않으며 일반 수신에 맡긴다.

**검증 근거와 범위**

최종 소스의 영향 범위 회귀 **288개 통과 / 실패 0 / 건너뜀 0**. 이 중 새 송신 전용 검사는 **27개**이며 별도 실행도 통과했다. 두 수를 합산하지 않는다. 범위는 준비/보존, 송신 정책, handshake/C9, Stage8 저장소·전송, 진단, 종료 예산이다. 완료한 iPad 첫 계약이나 실기기 일반 수신 시험은 반복하지 않았다.

전용 검사는 성공·거절·응답 유실·잘못된 영수증, 중복 클릭, 재열기, HTTP 전후 크래시, 계정/관문/로컬 기준 변경, blocked→cancelled 이력, 서버 기준 변경·추가 행·잘린 응답·count 부재·조회 실패, viewer 거절 및 editor 권한의 두 경계 확인, 원장 재설정 차단, 자동/일반 수동 재시도 제외, 늦은 응답의 원래 저장소 보존과 UI ‘아니요’를 포함한다. 실제 PostgREST 클라이언트의 select/count/range는 `httpx.MockTransport`로 검사했다. 임시 SQLite·디렉터리·가짜 계정·합성 요청 ID와 가짜 RPC를 사용했으며 실제 계정 저장은 mock으로 차단했다.

8009→8010 마이그레이션은 임시 DB에서 원본 준비 행 전체와 envelope, 기존 테이블 내용 및 파일을 비교했다. 새 실행 테이블만 추가되고 원본은 그대로 보존됨을 확인했다. 실제 운영 DB를 새 `SyncV2Store`로 열거나 마이그레이션하지 않았다.

20:18 KST 읽기 전용 재확인에서 실제 DB는 schema 8009, 준비 1개, 실행 테이블 없음, 최종검증03 계약 배치 0개·활성 작업 0개였다. 전체 15개 작품의 열린 관문은 0개다. 원본 JSON과 DB 저장 원본이 일치하며 로컬 기준 해시도 재계산 결과 일치했다. 폴더 12개·문서 메타데이터 26개·원고 파일 25개, 빈 2권 및 기존 순서 revision 1을 유지하고 실제 3권은 디스크/identity/폴더 테이블에 없다. WAL 부재를 확인한 immutable read-only 접근이며 DB 크기·수정 시각도 읽기 전후 같았다. 파일·identity·DB는 순차 조회했으며 원고 본문 바이트 일치 검사가 아니다.

현재 설치 EXE SHA256은 `8d16bb3946a517eea702b8d14e1ea5366e16e4d40c892b2b4373e3e0b8038edb`로 기존 설치 기록과 같다. 이번 소스는 빌드/설치되지 않았다. 계약 client_build_id는 변경하지 않았고, 소스 및 향후 바이너리 식별은 별도 SHA256으로 연결해야 한다. 실계정 권한, 최신 서버 revision, 실기기 실행 성공을 이번 모의 검증으로 확정하지 않는다.

**읽기 전용 결과 확인 절차 — 이번에는 실행하지 않음**

동봉 `server-receipt-readonly.sql`은 위 고정 작품·batch·operations·새 folder·기존 order만 조회하는 SELECT다. 향후 결과 확인이 필요한 단계에서 Staging 대상을 확인한 뒤 사용하고 조회 시각과 결과를 보존한다. batch/request hash, operation 두 개와 payload hash, 해당 결과의 applied/response hash, 시도 이력, folder revision 1과 기존 order revision 2 및 children을 함께 대조한다. 원본 JSON이 비교 기준이다.

기존 order는 전송 전에도 revision 1로 조회된다. order 행이 있다는 이유만으로 성공으로 판단하지 않는다. 거절은 영구 거절 영수증으로 구분하며, 빈 결과·결과 불명·조회 실패는 재전송 허가가 아니다. 관리자 SQL 결과를 로컬 영수증으로 수입하거나 DB를 수동 완료 처리하지 않는다. 최신 메타데이터 SELECT들은 하나의 서버 트랜잭션 snapshot이 아니므로 검사 후 경쟁 변경의 최종 판정은 서버 계약 RPC의 권한·잠금·revision 검사에 맡긴다.

**전달 자료**

`windows-reviewed-sender-implementation-20260906.zip`에는 이 회신, 구현·시험 소스 8개, 변경 전 소스, 증분/전체 패치, 원본 요청, 시험 로그, 원본 보존 및 운영 읽기 전용 확인, 조회 SQL, 소스/파일 SHA256 manifest를 포함했다. 전체 실제 DB·원고 본문·토큰·EXE는 포함하지 않았다. 근거 스크립트는 저장소 내 경로를 전제로 하며 실제 DB 검사 스크립트는 Windows 호스트 전용이다.

base HEAD는 `e5ff6e0b868deca909b9f6d0817bf328d5bf77ed`, 브랜치는 `feat/contract-handshake-closed-gate`다. `sender-implementation.patch`는 기존 준비 구현 위의 이번 변경만 담으며 SHA256은 `b394a978efaf2b304d9c90dad2dc6a778d97e091ab14d80360e4bbe827577b48`이다. `full-implementation.patch`는 base HEAD 대비 준비 구현까지 포함한 대안이며 SHA256은 `1893a43bd2ba42e287fcf23789a96ed9a43bc97e4bde646ca5e64e95d99e0053`이다. 두 패치를 연속 적용하는 절차가 아니다. 두 패치 모두 현재 소스에 대한 `git apply --check --reverse`와 `git diff --check`를 통과했다.

**사용자가 지금 할 일:** 이 회신 Markdown과 근거 ZIP을 iPad 측에 보내 구현 검토 회신을 요청한다. 추가 앱 조작 없이 양쪽 관문을 닫아 둔다. 준비 요청을 다시 만들거나 송신/재전송을 누르지 않는다. iPad 회신을 이 Windows 작업으로 가져오면 설치·실행 준비에 남은 조건을 대조한다. 실제 실행은 별도 승인 단계다.
