# iPad 요청: 기존 LEGACY 합성 원고 1개에 한정한 양방향 후보 준비

2026-09-11. 사용자가 이 파일을 전달하는 **개발·격리 검증·설치 전 후보 준비 요청**이다. 과거 설치·송신·수신 재개 승인의 재사용이 아니며 실제 설치/원고 편집/송신/관문·보류 변경/prod 전환은 지금 허용하지 않는다. 이 파일만 보고 현재 수신 후보의 전역 보호를 해제하지 않는다.

완료한 UUID 교정·같은 journal 재개·오프라인 3줄 관찰·해시 보완·catalog 가져오기·기존 수용 검사는 다시 하지 않는다. 이전 잘못된 UUID marker는 보존한다. 새 구현과 변경 영향에 해당하는 검사만 묶어서 진행하고, 후보/검사/설치 범위를 한 번 회신한다.

## 고정 대상과 순서

- 작품: `본문수신검증 20260911`, 서버/local UUID `9a78c51c-7de9-43a8-be54-d25a22d08a28`.
- LEGACY/epoch 0, 마지막 서버 settings 행 없음. 상태가 바뀌면 ID_BASED로 임의 전환하지 말고 중단한다.
- 원고: `502cdbe7-814c-42f8-8ed4-81c40cd94902`, `메인/원고/1권/1화.txt`.
- 기존 revision 1/93 bytes → Windows revision 2/121 bytes를 iPad 수신·오프라인 관찰 → iPad revision 3/146 bytes를 Windows 수신·오프라인 관찰.
- 정확한 본문과 SHA는 동봉 `proposed-plan.json`, `body-before.txt`, `body-after-windows.txt`, `body-after-ipad.txt`를 사용한다. BOM 없이 LF·끝 줄바꿈을 유지한다.
- 정렬 문서와 폴더 11개는 변경하지 않는다. 일반 ID_BASED 시험 작품 d8f50b5f와 기존 local binding a9452cd1, 최초가져오기 d21b8876은 대상이 아니다.

## 현재 소스 근거

설치 후보 `ipad-staging-receive-auth-loading-fix-20260911`, Staging/debug `com.chocos.writerpad.debug`, 0.1.0(1), `WRITERPAD_RECEIVE_VALIDATION`, 동결 224파일 digest `292241e2d46b7cd15fa2c0be16621fbca2f50ddbde1891a7935e725847760476`. 미커밋 변경 보존.

Windows는 설치 manifest와 정확히 일치하는 `ReceiveValidationPolicy`, `SyncV2Dispatcher`, `SyncV2Store`, `SyncV2Client`, `EditLeaseManager`, `SupabaseClientProvider`, `SyncV2Handshake`, `SyncV2SnapshotPullService`, `SyncV2LocalSnapshotApply`를 읽었다. 파일별 SHA와 위치는 동봉 `ipad-source-resolution.json`에 있다. 단순한 UUID 치환이나 sendingAllowed=true로 충분하지 않다는 점은 확인했다.

## 필요한 새 연결 — 기존 보호를 좁게 확장

1. 설치/재시작/백그라운드 복귀 기본값은 송신 잠금이다. defaults·실행 인자·전역 자동 동기화 true가 grant를 만들지 못해야 한다. 명시적 foreground action만 process/계정/endpoint/작품/문서/operation/기한/세대에 결합한 grant를 만든다. 기존 세션/예약/SDK refresh는 새 grant를 물려받지 못한다.
2. `sendingAllowed` 전역 플래그를 true로 돌리지 않는다. store enqueue/claim·dispatcher lane·retry/recoverInterruptedWork·lease manager·RPC client·최종 URLProtocol을 대상/단계별 정책으로 연결한다. 한 경계만 우회한 채 나머지를 꺼서 통과시키지 않는다.
3. 수신 단계는 이미 가져온 이 작품의 정상 snapshot/apply를 허용해야 한다. 완료 journal을 되살리거나 새 catalog import를 생성하지 않는다. 현 `requireApplication`의 journal/selectedProject 문맥과 독립적으로 검증된 기존 binding의 foreground 수신 문맥을 추가한다. 다른 작품 baseline/파일 쓰기는 허용하지 않는다.
4. iPad 송신 단계 enqueue는 revision 2/정확한 121 bytes baseline 위에서 지정 146 bytes 전체 저장 한 번만 허용한다. 제품에서 새로 생성된 operation UUID를 기록해 bind한다. 자동 병합·추가 입력·이력·폴더/정렬·휴지통·bulk enqueue로 범위를 넓히지 않는다.
5. 작업 선택 전에 대상 밖 큐를 제외한다. 이전 pending 4/conflict 3/completed 1265/cancelled 101은 마지막 기준이다. unrelated operation을 claim 후 거부하면 이미 상태가 바뀌므로 불충분하다. target 빈 큐와 기존 operation/batch/recovery/event 보존을 입증한다. target에 다른 미완료 작업이 있으면 중단한다.
6. lease acquire 최대 1, TTL 90, 정확한 target document/device; commit 최대 1, base revision 2, 정확한 operation/path/bytes; 같은 lease release 최대 1. heartbeat/일반 ensure_project 복구/contract batch/commit_folder는 허용하지 않는다. 상대편 lease 종료 후에만 다음 방향을 시작한다.
7. SDK 생성 request와 최종 전송 경계에서 HTTP method/path/endpoint/account/모든 RPC 파라미터·전체 payload를 확인한다. redirect·자동 auth retry 아래에서 commit을 재전송하지 못하게 한다. auth/read와 데이터 쓰기의 수량은 구분한다. body만 맞고 다른 operation/project/device/추가 파라미터인 요청은 거부한다.
8. 최초 전송 직전에 1회 한도를 소비한다. 예외/timeout/응답 불명은 unknown으로 남기고 자동 재시도하지 않는다. 서버 영수증의 operation/revision/hash를 먼저 조회·대조한다. 기기/앱 재시작 후 작업을 자동 claim·재전송하지 않는다. grant가 취소된 뒤 도착한 응답이 새 grant/다른 작품에 적용되지 않게 한다. 이미 도착한 성공의 기록 처리와 일반 재시도 개방을 구분한다.
9. Windows `bidirectional_sync_scope.py`는 위 범위의 오프라인 참조 구현이다. 설치 제품 보호가 아니며 Python 테스트 통과가 iPad XCTest를 대신하지 않는다. 구현상 필요한 정확한 RPC shape/정리 호출이 다르면 근거와 최소 수정안을 후보 회신에 함께 적는다. 실제 범위를 스스로 확대하지 않는다.

## 변경 영향에 필요한 검사

- 지정 원고 1회 enqueue/claim/lease/commit/complete, 새 revision 2 수신과 이후 revision 3 송신 성공의 fake transport 검사.
- 다른 작품/원고/정렬/폴더·오래된 pending/conflict/batch/recovery가 enqueue/claim/retry/HTTP 어느 경계에서도 바뀌지 않음.
- 빈 grant/잘못된 mode·epoch·binding·계정·endpoint·payload·operation·기한·세대와 취소/배경 전환/재시작 차단.
- 동시/중복 저장·late autosave·큐 변경·auth refresh·SDK 대기 요청·응답 불명·중복 commit·lease cleanup·취소 뒤 응답의 경계.
- 수신 전용 이전 후보와 일반 비시험 제품 설정이 의도 없이 약화되지 않음. 소켓 차단/가짜 transport 하에서 테스트 수행.

기존 전체 XCTest/기기 수신 시험을 일괄 재실행하지 말고 이번 변경에 필요한 경계만 실행한다. 제품의 실제 transport/store/dispatcher까지 연결한 시험인지, 순수 정책/가짜 인터페이스 시험인지를 결과에서 구분한다.

## 한 번에 회신할 결과

- 새 코드 diff와 정확한 변경/의존 소스, full source manifest와 기반 HEAD+보존 미커밋 입력, 새 검사 목록/최초 실패 포함 로그/최종 결과/한계.
- 새 Staging/debug 후보 app ZIP, bundle/version/빌드 flag/실제 candidate SHA·소스 digest·서명/프로필 만료. 가능하면 현재 수신 후보를 대체할 설치 대상은 하나로 유지한다.
- 설치 전 새 독립 백업 방법, 기존 settings/정책/큐/원고 보존, 기본 잠금으로 첫 실행하는 방법, 명시적 사용자 버튼 이름/동작, 실패/응답 불명/재시작 뒤 복구 범위.
- 아직 설치하거나 기기 원고를 편집하지 않는다. 옛 프로필 만료 기록 `2026-09-12 18:19:25 KST`를 새 후보의 유효성으로 복사하지 않는다.

현재 사용자가 누를 버튼은 없다. 위 준비를 끝낸 회신 하나를 Windows로 전달하면 양쪽 실제 후보/범위를 묶어 사용자에게 승인받는다. 완료된 과거 수신·해시 문제의 회신을 다시 요청하지 않는다.
