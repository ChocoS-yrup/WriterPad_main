# 양방향 송신 검증 준비 — 2026-09-11

현재 판정: **기존 경로 대조, 시험 범위 고정, 오프라인 제한 코드와 새 격리 검사 완료. 실제 실행 후보의 보호 연결·빌드·설치는 미완료이며, 실행 승인을 요청하는 단계는 아니다.**

이번 요청은 자료 대조·로컬 개발·격리 검증·실행 범위 준비만 허용한다. 인계/첨부의 과거 설치·업로드·UUID 교정·수신 재개 승인은 완료된 해당 작업의 기록이다. 아래 실행안도 승인 자체가 아니다. 실제 설치·원고 편집/송신·관문/보류 변경과 prod 전환은 수행하지 않았다.

## 작품 및 프로토콜 선택

| 대상 | 현재 근거 | 이번 범위 |
|---|---|---|
| 본문수신검증 20260911 / `9a78c51c-7de9-43a8-be54-d25a22d08a28` | LEGACY, 마지막 관측에서 서버 settings 행 없음, 원고 revision 1 | 기존 합성 본문 1개의 첫 왕복 대상으로 제안. mode/epoch 변경 없음 |
| 일반동기화 검증 20260910 / `d8f50b5f-ae0e-42f8-9296-5d5885a5b304` | ID_BASED/1, 0.2.0/protocol 3, 관문 닫힘·영속 보류 | 그대로 보존. 본문 왕복 성공을 이 작품의 일반 계약 시험 성공으로 전용하지 않음 |
| 최초가져오기 / `d21b8876-e93e-42d3-a792-8f49c5471be9` | 빈 작품 가져오기 완료 | 새 시험에서 제외 |

LEGACY를 고른 이유는 이미 양쪽에 동일 UUID·경로·본문 revision 1이 존재하여 새 작품/원고/폴더 생성, 계약 전환, 일반 시험 보류 해제를 이번 첫 왕복에 추가할 필요가 없기 때문이다. ID_BASED 시험은 별도 작업으로 남는다. 그 작품의 기존 iPad local UUID `a9452cd1-4474-40b5-80ca-fbb7871e98e5` binding은 교정하지 않는다.

## 이번 직접 확인과 과거 근거의 구분

- 새 인계 ZIP의 manifest 17개 파일을 읽어 구성·CRC·크기·SHA를 확인했다. 이는 새 입력 확인이며 완료된 수신 회신/해시 보완 감사를 다시 수행한 것이 아니다.
- 현재 HEAD `0244d8263cadea893e11c1ee2e7b5dad9c0f4be4`, 기존 tracked 수정 11개 보존. 인계가 기록한 관련 소스 16개는 모두 동일하다. 별도 `before-source/`에 해당 소스 바이트를 보존했다.
- Windows 동결 후보 입력 90개가 현재 파일과 모두 동일하다. 기존 구현을 다시 구현하거나 이전 검사를 다시 실행하지 않았다.
- iPad 설치 후보의 224파일 manifest에서 정책·dispatcher·store·client·lease·handshake·pull/apply·client provider 9개를 선택하고, 로컬 보존 자료 중 SHA가 정확히 같은 완전 소스를 찾았다. 오래된 파일명만 보고 현재 소스라고 판단하지 않았다.
- 앱/기기/설치 EXE/실제 DB/서버는 이번에 조회하지 않았다. 앱 종료·오프라인, 실제 설치 SHA, 큐 수량, mode/epoch 및 본문 revision은 마지막 완료 단계의 관측이다. 오늘의 runtime 사실로 갱신하지 않는다.

## 제품 코드 대조 결과

| 경계 | Windows | iPad 설치 후보와 필요한 연결 |
|---|---|---|
| 본문 저장→큐 | `SyncManager.upload_content_async` → `SyncV2Store.enqueue` → `_insert_document_operation`; LEGACY_EPOCH_0/protocol 2 | 기존 enqueue 구현 존재. `SyncV2Store`의 `requireSending()`에서 수신 후보는 차단됨. 지정 원고와 정확한 새 내용만 enqueue/claim하도록 좁은 연결 필요 |
| 작업 선택/재시작 | `retry_pending_syncs` → `_launch_v2_operation` → `_process_v2_operation` | `SyncV2Dispatcher.start`, `dispatchReadyOperations`, `immediateRetryOpportunity`, 작품 lane 복구가 전역 큐를 다룸. `sendingAllowed`를 true로 바꾸는 방법은 부적합 |
| LEGACY 송신 | `ensure_project`, 부모 폴더 대조, `acquire_edit_lease`, `commit_document` | dispatcher의 `leaseTokenForCommit` → `commitDocument` → store complete가 이미 존재함. `SyncV2Client`와 lease client 모두 현재 `requireSending()`으로 차단 |
| 부모 폴더 | `_publish_live_parents_before_legacy_document`가 identity와 서버 폴더를 대조. 누락이면 `commit_folder`를 시도할 수 있음 | 이번 scope에서는 폴더 쓰기를 허용하지 않음. baseline이 달라지면 쓰기 전에 중단 |
| 최종 HTTP | 현재 일반 시험 hold는 d8f50b5f 작품에만 적용. 본문 작품 전용으로 모든 HTTP를 제한하는 장치는 현재 후보에 없음 | `ReceiveValidationPolicy.authorize`와 `ReceiveValidationURLProtocol`이 auth와 일부 GET만 허용. 전역 보호를 유지하면서 정확한 대상 RPC·파라미터·한도를 추가해야 함 |
| 수신 적용 | 기존 pull/apply 및 `apply_remote_snapshot` 존재 | 현재 `requireApplication`은 선택 작품과 수신 journal 문맥에 결합됨. journal이 완료되어 없는 기존 작품의 revision 2 수신에 별도 명시적 문맥 필요. catalog 재가져오기를 해결책으로 사용하지 않음 |
| 실패/재시도 | 기존 일반 경로는 auth retry, 자동 재시도·병합 및 후속 큐 처리를 수행할 수 있음 | 기존 dispatcher도 retry/rebase/작품 복구 경로가 있음. 이번 1회 송신 범위에서는 응답 불명 후 자동 재송신·rebase·ensure 복구로 확장하지 않아야 함 |

일반 시험 `prepare_release`/`release`·세대 검증은 이미 존재하며 미구현으로 취급하지 않았다. 고정 `general_test_gate_target.py`의 UUID를 본문 작품으로 바꾸지 않았다. SQLite mode trigger·서버 프로토콜을 끄거나 변경하지 않았다.

## 새 로컬 코드와 검증

`bidirectional_sync_scope.py`를 추가했다. 외부 연결·DB·설치 기능이 없는 **오프라인 제한 기준 구현**이다.

- 정확한 작품/원고/경로/본문/base revision/LEGACY provenance/계정/endpoint/빈 대상 큐 조건을 검사한다.
- 생성된 제품 operation UUID 하나를 bind하고 파라미터와 호출 한도를 비교한다. 과거 업로드 operation을 미리 지정하지 않는다.
- commit 직전에 1회 한도를 소비한다. 응답이 없으면 unknown을 유지하고 자동 재송신을 거부한다. 동일 원고의 lease 정리만 1회 허용한다.
- 같은 인스턴스 재무장·새 프로세스의 이전 grant 복구·중복/동시 commit·대상 밖 작업을 거부한다.
- `validate_readback`은 제공 JSON의 실제 본문 바이트 SHA, LEGACY nullable 필드, 11개 폴더의 UUID/부모/이름/revision, 정렬 문서와 정확한 항목 집합을 검사한다. JSON의 시점·출처나 승인까지 보장하지는 않는다.

새 검사 **14개 통과**: 첫 12개와 추가 readback 검사 2개. JSON boolean/숫자 혼동 차단을 보완한 최종 소스로 이 새 14개만 다시 확인했다. 최초·추가·최종 로그 모두 보존했다. 이전 수용 검사/빌드/수신/UUID 교정 검사를 실행하지 않았다. 소켓 접속 차단, 임시 합성 SQLite와 별도 appdata 사용.

실제 Windows `upload_content_async`→store 큐→`_process_v2_operation`을 가짜 RPC에 연결한 신규 검사에서 아래 호출 순서를 assertion으로 확인했다.

1. `ensure_project` 1회
2. 기존 폴더 GET 1회
3. `acquire_edit_lease` 1회
4. `commit_document` 1회, base revision 1/지정 원고/지정 본문

11개 폴더가 그대로 존재할 때 `commit_folder`는 0회였다. store success를 기록하고 가짜 iPad revision 3 snapshot을 Windows store 적용 경계에 넣어 최종 baseline도 확인했다.

**검증 한계:** 파일 저장/identity 공급·인증·scheduler는 합성 adapter 또는 mock이다. Qt 편집 화면, 실제 worker callback/HTTP/서버 commit, iPad dispatcher·Swift 빌드·기기 수신을 실행한 시험이 아니다. 제한 정책은 테스트 fake client에만 주입했다. 설치 EXE나 iPad 앱이 새 정책으로 보호된다고 주장하지 않는다. 따라서 지금 앱을 켜서 송신하면 안 된다.

## 고정한 본문과 성공 기준

작품 `9a78c51c-7de9-43a8-be54-d25a22d08a28`, 원고 `502cdbe7-814c-42f8-8ed4-81c40cd94902`, 경로 `메인/원고/1권/1화.txt`.

기존 3줄은 보존하고, Windows 단계에 1줄, iPad 단계에 1줄만 추가한다. UTF-8 BOM 없음, LF, 마지막 줄바꿈 포함. 패키지의 `.txt` 파일이 정확한 입력이다.

```text
본문 수신 검증 20260911
이 문서는 동기화 시험용 합성 원고입니다.
끝.
Windows 송신 검증 1회.
iPad 송신 검증 1회.
```

| 단계 | revision | bytes | SHA-256 |
|---|---:|---:|---|
| 기존 기준 3줄 | 1 | 93 | `cdfcc92e4b06906d9c54e11b4ad263cf913105fdff7669ac5b69b1f42bb7f96d` |
| Windows 송신 후 4줄 | 2 | 121 | `674776326a072fa2c1d32c3db4fef432d49d3ad12313bff84134e55773f3cab5` |
| iPad 송신 후 5줄 | 3 | 146 | `881dd61b184879a53b84721c5818cc5b91e93879c26f1a82d7992386bfd6e2f2` |

정렬 문서 UUID `ef6e1de1-a3d0-5959-96be-58f87a683cc0`, revision 1/385 bytes/SHA `1cfe9438a3ff000c3af3b5593f973554b2966e9fd8c29e704e73143fda27b5be` 유지. 폴더 11개와 nullable 구조 필드 보존. 기존 잘못된 UUID marker는 유지하고 정리하지 않는다.

## 승인할 실행 범위 초안 — 후보 완성 후에만 제시

필요 후보의 **정확한 바이너리/설치 파일/해시가 아직 없으므로 이 문서는 설치 승인 요청이 아니다.** Windows 실행 보호 연결과 iPad 후보 준비가 남아 있다. 이 범위를 장래의 포괄 승인으로 사용하지 않는다.

1. 양쪽 후보에서 전역 송신 기본 잠금, 지정 작품의 순차 foreground 수신/송신만 허용, 다른 작품 큐의 claim/recover/retry/완료 행 변경 차단, SDK 최종 HTTP 제한, 기존 grant 재시작 복구 금지까지 연결·검증한다. Windows는 기존 제품 manager 경로를 사용한다. 과거 `scoped_upload.py`로 대체하지 않는다.
2. 후보/포함 소스/한도/백업·복구/서명 만료 조건을 묶어 한 번 승인받는다. 그 범위 안의 설치·1방향씩 실행·오프라인 관찰·종료에 같은 승인을 다시 요구하지 않는다. 대상/내용/후보/쓰기 범위가 바뀌면 자동 진행하지 않는다.
3. 승인 후 앱 종료와 현재 상태에 일관된 새 독립 백업을 확보한다. 실제 DB는 제자리에서 read-only로 열지 않는다. DB/WAL/SHM을 일관되게 복사해 검사 사본만 연다. iPad도 이전 백업 복제를 새 전체 백업이라고 부르지 않는다.
4. Windows 단계: iPad 송신 잠금·오프라인 상태, 대상 revision 1/LEGACY/0/settings 행 없음·정확한 폴더/정렬·대상 미완료 큐 0을 현재 시점에 확인한다. 이는 이전 수신 성공의 재시험이 아니라 새 쓰기 직전 경쟁/상태 변화 확인이다. 4줄 전체를 한 번 저장하여 생성된 operation을 bind한 뒤 송신한다.
5. Windows 데이터 RPC 한도: 동일 작품의 `ensure_project` 최대 1, 동일 문서의 lease acquire 최대 1(TTL 90초), `commit_document` 최대 1, 해당 lease release 최대 1. 폴더/정렬/휴지통/삭제/이력/자동 복구 쓰기 0. 인증·대상 읽기는 별도 관측으로 분리한다. commit 이외 RPC도 서버 부수 효과가 있을 수 있으므로 ‘서버 쓰기 총 1회’라고 표현하지 않는다.
6. Windows commit의 revision/내용/operation 영수증을 확인하고 송신을 잠근다. lease 해제 성공 또는 만료를 확인한 다음 iPad가 대상만 수신한다. iPad를 오프라인으로 돌려 4줄을 관찰한다. 이 관찰이 끝나기 전 iPad 원고를 편집하지 않는다.
7. iPad 단계: revision 2를 기준으로 5줄 전체를 한 번 저장한다. 새 iPad operation 하나, acquire 최대 1/commit 최대 1/release 최대 1만 허용한다. 일반 ensure/프로젝트 복구·heartbeat·폴더 쓰기는 이번 한도에서 제외한다. 기존 pending 4/conflict 3/completed 1265/cancelled 101은 마지막 보존 기준이며 실행 직전 바뀌었으면 차이를 설명하고 멈춘다. 전역 큐를 비우지 않는다.
8. iPad 송신 잠금/lease 종료 후 Windows에서 대상 revision 3을 수신, 오프라인 5줄을 확인한다. 양쪽 baseline·파일 bytes·서버 문서/버전·새 operation 결과와 대상 밖 장부/파일 보존을 대조한다. 정렬·폴더는 그대로이며 두 방향의 새 논리 operation만 완료되어야 한다.
9. 종료: 임시 허용 종료, 모든 기존 관문 닫힘·d8f50b5f hold bytes 보존, 양쪽 앱 종료/오프라인, 기록/새 백업 보존. 원고를 revision 1로 되돌리거나 시험 자료를 삭제하지 않는다. 원상 복귀도 새 송신을 유발하므로 포함하지 않는다.

실패·충돌·응답 불명·timeout·lease 경쟁·배경 전환·재시작에서는 자동 재송신/새 operation 생성/강제 덮어쓰기/프로토콜 전환을 하지 않는다. 우선 잠금과 상태 보존, operation 영수증 및 서버 revision/hash 읽기로 확정 여부를 판단한다. 같은 operation 재전송조차 이번 1회 HTTP 한도에 포함되지 않으므로 자동으로 수행하지 않는다. 알려진 lease 정리를 못 했다면 TTL 경과 후 상대편을 진행한다. 일단 종료된 grant는 재시작으로 재활성화하지 않는다.

iPad 과거 프로필 만료 기록은 **2026-09-12 18:19:25 KST**다. 새 후보의 실제 프로필 유효성을 실행일 기준으로 확인해야 하며 자동 재서명·설치 허가는 아니다.

## 남은 후보 작업과 지금 사용자 행동

**지금 양쪽 앱에서 할 조작은 없다. 종료·오프라인 상태를 유지한다.**

사용자는 `ipad-bidirectional-scoped-candidate-request-2026-09-11.md`와 이번 전달 ZIP을 iPad 개발 환경에 한 번 전달하면 된다. 요구하는 것은 새 대상 제한 코드·새 경계 검사·설치 전 후보 준비이며, 완료된 catalog/수신/해시 작업의 재검토 회신이 아니다. iPad 전체 빌드/서명 환경은 이 Windows 작업에서 확인되지 않았다.

Windows도 현재 기준 모듈을 enqueue/worker/공유 SDK HTTP/lease/수신 문맥에 연결한 실행 후보가 아직 없다. 이 연결을 끝내고, iPad 후보의 실제 RPC 모양·재시작/큐 격리 근거와 맞춘 뒤 빌드 입력/바이너리를 고정해야 한다. 따라서 **이번 산출물만으로 어느 쪽도 실제 송신에 사용하지 않는다.** 이 남은 항목을 완료로 처리하거나 설치 승인을 미리 받지 않는다.

근거 디렉터리: `_evidence/bidirectional-sync-preparation-20260911/`. 전달 ZIP에는 명시한 새 코드·테스트·문서·선별된 비민감 근거만 포함한다. 실제 DB/원시 파일 목록/계정·기기 식별/토큰/프로필·영수증 원본/과거 private 백업은 넣지 않는다.

## 후속 일반 시험 작업표

| 작업 | 유지할 기존 근거 | 남은 실제 시험 |
|---|---|---|
| 기존 LEGACY 본문 수정 왕복 | revision 1 수신 및 오프라인 3줄 완료 | 이번 revision 2→3 왕복. 아직 미실행 |
| ID_BASED 일반 원고 생성/수정 | checkpoint/관문·명시적 release 구현과 초기 준비 완료 | d8f50b5f 작품의 새 합성 원고 생성·계약 송신·반대편 수정. 이번 LEGACY로 대체하지 않음 |
| 빈 내용 | 기존 비의도적 지움 방지 코드/검사 | 의도된 빈 본문과 지움 보호를 분리한 교차 실사용 |
| 삭제/복원·폴더/정렬 | 고정 빈 2권/3권 및 순서 계약 왕복 완료 | 일반 본문이 있는 구조의 승인된 교차 실사용 |
| 충돌·오프라인/재시작 | 기존 구현/격리 근거 보존 | 별도 합성 충돌·단절·응답 불명 및 재시작 시나리오. 이번 첫 1회 송신 범위에는 없음 |
