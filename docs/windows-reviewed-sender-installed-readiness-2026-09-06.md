# Windows 회신 — 검토 송신 구현 설치 및 닫힌 관문 준비 점검

2026-09-06 KST. iPad `ipad-windows-reviewed-sender-review-20260906.zip`을 대조하고 사용자의 앱 종료·새 설치본 실행 확인에 이어 설치 및 읽기 전용 준비 점검을 진행했다. 첨부 문서를 실제 송신 승인으로 취급하지 않았다.

**검토된 소스를 빌드·설치했고 정상 실행 후 schema 8010, 준비 원본 1개, 실행 기록 0개를 확인했다. 서버 구조와 로컬 기준도 일치한다. 사용자는 현재 앱이 준비 당시와 같은 계정임을 확인했다. 설치·읽기 전용 준비 점검 회신을 확정하며, 실제 관문 개방·계약 전송·재전송은 아직 승인되거나 수행되지 않았다.**

| 설치 식별 | 값 |
|---|---|
| base HEAD | `e5ff6e0b868deca909b9f6d0817bf328d5bf77ed` |
| 전체 패치 SHA256 | `1893a43bd2ba42e287fcf23789a96ed9a43bc97e4bde646ca5e64e95d99e0053` |
| 새 빌드·설치 EXE SHA256 | `9a7940bdcf1905ccf44b38812803a158c05760be5bab5ccb46ff200a3f20a44b` |
| EXE 크기 | 79,271,032 bytes |
| 설치 시각 | 2026-09-06 21:10:53 KST |
| 설치 위치 | `D:/안티그래비티/scratch/집필프로그램/작가님 힘내세요.exe` |
| 이전 설치 SHA256 | `8d16bb3946a517eea702b8d14e1ea5366e16e4d40c892b2b4373e3e0b8038edb` |
| contract client_build_id | `writerpad-windows-stage8-contract-0.2.0` — 원본과 동일 |

수신 iPad manifest 3개와 iPad가 검토한 Windows ZIP의 41개 항목을 재해시했고, 현재 구현·시험 소스 8개가 검토본과 일치함을 확인했다. 소스 변경이나 이전 288개 검사의 재실행은 하지 않았다. 새 빌드에 필요한 PyInstaller 빌드, 포함 모듈 검사, 빌드/설치 각각 Qt smoke만 수행했다. 두 smoke 종료 코드는 0이며 프로젝트·인증 초기화 이전의 Qt 로딩 검사다.

PYZ에서 `reviewed_contract_sender`, `contract_preparation`, `handshake_lifecycle`, `sync_manager`, `sync_v2_store`, `settings_panel`, `contract_transport`, `sync_contract`를 확인했다. 패키지 내부 고정 batch/request hash, schema 8010 및 수동 송신 버튼 상수도 일치한다. 실제 실행 중 프로세스의 경로·시작 시각과 설치 파일 SHA256을 별도로 기록했다. 프로세스 메모리에서 모듈이나 인증 정보를 추출하지 않았다.

기존 EXE는 `_evidence/windows-reviewed-sender-install-20260906/previous-installed.exe`로 보관했다. 사용자 종료 후 파일을 교체했고, 사용자가 21:12:36 KST 이후 새 앱을 열었다고 확인했다. 설치 자체는 DB를 변경하지 않아 설치 직후 schema 8009였으며, 정상 앱 실행 후 schema 8010으로 이동했다. 시점별 증거를 구분해 보존했다.

**원본 및 운영 상태 확인**

- 고정 batch `6fd8c11b-5b74-4219-aa0b-a5d408ca8505`, request SHA256 `bbc9571cb222b35b268c7eaf5a823cc54f7d9e5f9199736e511562a7fe458045` 유지.
- export 파일 SHA256 `83e58abdd93e23064bd2ca90fab18c0b19a09ff4f49a71cc22c20f79c8947bbc`, canonical envelope SHA256 `3de0b65559b658e2647191962e72e23bca03f2016486cc3cc2c39c47725223ae`가 DB 준비 원본과 일치.
- 설치 전·설치 직후·정상 실행 후 로컬 기준 SHA256 재계산 결과 `a26eed18ecb701308b900d26e66d12d3715fbe550f637bd9ea3d3cdc0271e98d` 유지.
- schema 8010, 준비 원본 1개, `sync_reviewed_executions` 존재/기록 0개, 대상 계약 송신 배치 0개, 활성 작업 0개. 전체 15개 작품의 열린 관문 0개.
- 기존 폴더 12개·문서 메타데이터 26개·원고 파일 25개와 identity·저장 순서를 보존. 빈 2권은 유지되며 실제 3권은 디스크·identity·폴더 테이블에 없음. 원고 본문은 읽지 않았다.

운영 DB 조회는 WAL 부재를 확인한 immutable read-only 방식이며 DB 크기·수정 시각이 해당 조회 전후 같음을 확인했다. 제품 생성자나 sender/claim을 호출해 검사하지 않았고, DB 수동 마이그레이션·원본 재발급·삭제·수동 3권 생성도 하지 않았다.

**서버 읽기 전용 대조**

Supabase 프로젝트는 WriterPad Staging / `mhpnszcorfzrvhyondxr` / ACTIVE_HEALTHY다. 21:14:40 KST SELECT에서 최종검증03 `1bd47431-0773-482c-8eb5-ac9e2952b6f4`는 active, LEGACY/0이며 contract 0.2.0·protocol 3의 기존 allowlist는 enabled, revoked_at 없음으로 확인했다. 서버 설정이나 allowlist를 변경하지 않았다.

원본 준비 계정 marker `bbca16c03e5b9711`이 현재 서버 owner와 일치하고 해당 membership도 owner다. 이는 관리자 SELECT로 확인한 저장 권한이다. 현재 실행 프로세스의 사용자 식별자를 직접 읽거나 별도 클라이언트로 저장 토큰을 갱신하지 않았다. **사용자가 “같은계정”이라고 답해 현재 앱이 준비 당시와 동일 계정임을 확인했다.** 사용자 확인은 `user-account-confirmation.json`에 별도로 기록했으며, 프로세스 인증 정보의 직접 검증이나 실제 전송 승인으로 확대하지 않는다.

새 앱 시작 뒤 `contract_validated_at=2026-09-06T12:12:42.569427+00:00`, protocol 3·계약 digest·capabilities 및 LEGACY/0가 기록됐다. 검토 소스에서 이 기록은 호환 handshake 수락 경로에서 저장된다. 21:12:43 KST 일반 수신 성공/saved와 대기 0건의 진단 기록도 함께 보존했다. 이는 정상 실행 후 저장된 관찰 근거이며, 현재 프로세스 메모리의 handshake freshness·계정·구조 authority를 직접 조회한 결과는 아니다.

서버 폴더 12개·문서 메타데이터 26개·tree_order 1개를 실제 sender가 비교하는 열로 로컬과 대조해 모두 일치했다. 새 3권 UUID `77627d68-8388-4d6c-9363-ba6537aac1f0`와 원고 아래 3권 이름은 없다. 기존 order `68c1a7b5-0dda-49ba-bc9a-42e92ce2b758`는 revision 1, children `[1권, 2권]`이다.

검토된 `server-receipt-readonly.sql`도 21:14:05 KST 실제 SELECT로 실행해 성공했다. 고정 batch·operations·attempts·results·new_folders는 모두 빈 배열이고, 기존 order만 revision 1로 조회됐다. 미전송 상태와 부합하며, 빈 결과를 재전송 허가로 해석하지 않는다. 실제 성공 revision 1/2나 서버 영수증을 받은 것은 아니다.

DB·프로세스·서버 SELECT는 순차 관찰이며 하나의 원자적 snapshot이 아니다. 관리자 SELECT는 사용자 인증에 의한 쓰기 성공 검증이 아니고, 이 사전 자료가 향후 실제 sender의 두 경계 검사를 대신하지 않는다. 송신 버튼은 사전 점검 실패만으로도 영속 실행 기록을 남기므로 이번 점검에 사용하지 않았다.

**다음 행동**

사용자는 이 회신과 근거 ZIP을 iPad에 전달해 설치 식별·원본 보존·실행 0개·읽기 전용 결과와 동일 계정 확인을 대조하도록 한다. iPad 회신을 이 Windows 작업으로 가져오면 별도 사용자 승인을 받을 구체적 1회 실행 범위를 제시한다. 지금은 추가 앱 조작·편집·관문 조작·송신·재전송 없이 대기한다. 계정 확인 뒤 기존 시험이나 서버 조회를 반복하지 않았으며 위 조회 시각의 근거를 유지했다.

근거 위치: `_evidence/windows-reviewed-sender-install-20260906/`. 전달 ZIP에는 설치/빌드 식별, 모듈 포함, 시점별 원본 및 메타데이터 비교, 조회 SQL/결과, 검증 스크립트와 manifest를 포함하고 EXE·전체 운영 DB·원고 본문·토큰은 제외한다.
