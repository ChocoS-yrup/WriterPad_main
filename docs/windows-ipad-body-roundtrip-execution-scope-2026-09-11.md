# Windows ↔ iPad 본문 1회 왕복 실행 범위 — 2026-09-11

**준비 완료·실제 실행 승인 대기.** 이번 사용자의 “다음 진행”에 따라 Windows 제품 연결, 새 격리 검사, 고정 후보 빌드까지 수행했다. 과거 인계 문서·iPad 첨부의 실행 제안과 승인 기록을 이번 설치·송신 승인으로 간주하지 않았다. 아래 전체 범위를 한 번 승인받으면 같은 범위의 설치/버튼마다 승인을 다시 요청하지 않는다.

실제 앱 설치·실제 DB 열기·실서버 Auth/조회/송신·관문/hold 변경·prod 전환은 이번 준비에서 수행하지 않았다. 외부 iPad 담당자에게 도구로 메시지를 보내지 않았다.

## 무엇을 검증하는가

| 구분 | 상태와 이번 취급 |
|---|---|
| `본문수신검증 20260911` | LEGACY / epoch 0. 지정 합성 본문 1개만 Windows → iPad → Windows로 왕복 |
| 일반 시험 작품 | ID_BASED / epoch 1. 기존 binding, 관문, durable hold, 큐를 보존하며 이번 송신에 포함하지 않음 |
| 기존 iPad 설치 후보 | 수신 전용. 전역 sendingAllowed를 풀어 사용하지 않음 |
| 이번 iPad 후보 | 전역 잠금을 유지하면서 지정 본문만 명시적으로 수신·송신하는 별도 흐름. 이전 보완 검토는 완료됐으므로 같은 소스 검토 회신을 추가로 요구하지 않음 |
| 이번 Windows 후보 | 일반 창/dispatcher 대신 본문 검증 전용 창으로 시작. 일반 자동 동기화 전체 성공을 입증하는 시험은 아님 |

대상 작품 UUID `9a78c51c-7de9-43a8-be54-d25a22d08a28`, 원고 UUID `502cdbe7-814c-42f8-8ed4-81c40cd94902`, 경로 `메인/원고/1권/1화.txt`. endpoint는 `https://mhpnszcorfzrvhyondxr.supabase.co`로 고정한다. 실제 실행 직전에도 서버 settings 행 부재, LEGACY/epoch 0, 본문·정렬 2문서, 기존 11폴더, 비어 있는 UUID tree_orders를 확인한다. 이 문서에 적힌 기준을 현재 서버 조회 결과라고 표현하지 않는다.

| 단계 | revision | UTF-8 bytes | SHA-256 |
|---|---:|---:|---|
| 현재 기준 3줄 | 1 | 93 | `cdfcc92e4b06906d9c54e11b4ad263cf913105fdff7669ac5b69b1f42bb7f96d` |
| Windows 문장 추가 | 2 | 127 | `4cf361c4a26d342dbe712fec07f5ff7ccd3610605b471a87f5abc5250e8ca15f` |
| iPad 문장 추가 | 3 | 158 | `eee3691fbe8e6805a9d74b56dfd1d5cb9df53ad0c01e6325069cb091e68ff161` |

최종 본문은 BOM 없이 UTF-8, LF, 마지막 LF를 사용한다.

```text
본문 수신 검증 20260911
이 문서는 동기화 시험용 합성 원고입니다.
끝.
Windows 양방향 검증 20260911
iPad 양방향 검증 20260911
```

정렬 문서는 UUID `ef6e1de1-a3d0-5959-96be-58f87a683cc0`, 경로 `__antigravity__/tree-order.json`, revision 1, 385 bytes, SHA `1cfe9438a3ff000c3af3b5593f973554b2966e9fd8c29e704e73143fda27b5be`를 보존한다. 문서의 서버 parent/name/structure_revision은 null 그대로다. 기존 잘못된 UUID marker, 일반 시험 작품, 최초 가져오기 작품은 변경하지 않는다.

## 설치할 후보

| 항목 | Windows | iPad |
|---|---|---|
| 후보 ID | `windows-staging-body-snapshot-scope-20260911` | `ipad-staging-body-snapshot-scope-20260911` |
| source digest | `413be8d39a770ebf43aeb6845d6831fae2ff36541f8da3e456972bf2db9c5331` | `49904443d9b393b5647a0f134be69116099f13b4c992f672fcaf6f918ec35898` |
| 설치 파일 SHA | EXE `9893f7c434bf9a4118c58a3ef015a6a5fa94bf285c75fad5caec566b34babd3f` | app ZIP `4272d1776a99857ff7c9ecaf8cf4847c5890e7b1f0741cd8ffb785cca4279d67` |
| 설치 파일 bytes | 79,406,832 | 28,939,288 |
| 설치 상태 | 미설치 | 미설치 보고 |
| 식별/서명 범위 | 외부 후보 ID와 정확한 EXE SHA. Authenticode 서명 없음 | `com.chocos.writerpad.debug`, Debug Staging arm64, 0.1.0(1); 서명/프로필 검증은 iPad 측 보고 |

Windows 최종 EXE 위치:

`D:\안티그래비티\scratch\작가님 힘내세요\_evidence\windows-body-validation-candidate-20260911\final\candidate\작가님 힘내세요.exe`

설치 대상은 기존 `D:\안티그래비티\scratch\집필프로그램\작가님 힘내세요.exe`다. 현재 프로젝트 작업 폴더와 설치 폴더를 혼동하지 않는다. 이전 설치 EXE의 보존 기준 SHA는 `76e695051d83977a15b66892557cba13dc4eb95a6124d0710d503337193fb04d`이며 실제 설치 직전에 다시 대조한다.

iPad app ZIP은 iPad 측 private에 보존되어 있고 Windows가 바이너리 자체를 받은 것은 아니다. 설치 시 현지 파일/후보 hash와 기존 프로필 유효성을 확인한다. 프로필 만료 보고는 **2026-09-12 18:19:25 KST**다. 프로필이 만료됐거나 후보가 다르면 설치를 멈춘다. 재서명·프로필 갱신·기기 신뢰 변경은 이 범위에 포함하지 않는다.

## 승인 후 실행 순서와 사용자 행동

1. **Windows 측 — 현재 상태 백업과 후보 설치.** 기존 앱/관련 작업이 종료돼 있는지 확인하고, 기존 EXE·작품 폴더·설정·identity·실제 DB/WAL/SHM·hold/marker를 그 시점과 일관된 새 백업 두 벌로 보존한다. DB 검사는 복사본에서만 한다. 기존 byte/행/큐·관문/hold 기준을 남기고 EXE만 교체한다. 전용 창을 열어 기본 잠금을 확인한다. **사용자는 Windows/iPad 앱을 임의로 열거나 다른 원고를 편집하지 않고, 필요한 종료 안내만 따른다.**

2. **iPad 측 — 현재 상태 백업과 후보 설치.** 사용자가 이 승인된 실행 범위를 iPad 담당 측에 전달한다. 지정 bundle을 종료하고 현재 컨테이너/DB/원고/metadata에 일관된 새 백업 두 벌을 확보한다. 예전 백업의 단순 복제는 새 현재 상태 백업으로 간주하지 않는다. 정확한 후보를 데이터 유지 방식으로 설치하고 전역 수신/송신 보호를 확인한다. 삭제 후 재설치·데이터 초기화는 하지 않는다. **사용자는 iPad 설치 연결과 잠금 해제 등 실제 기기 조작만 안내에 따라 진행한다.** 이 단계의 결과는 실행 증거이며 새 코드 검토 왕복이 아니다.

3. **Windows 측 — 지정 본문 1회 송신.** 양쪽 준비가 끝나면 Windows 전용 창을 전면에 두고 **‘1. Windows 검증 문장 추가·송신 1회’**를 누른다. 이 명시 동작은 고정 staging endpoint의 제한된 Auth 및 대상 GET을 수행한다. 기존 세션을 복원하며 필요하면 refresh 1회와 그 결과 토큰의 기존 안전한 보관을 허용한다. 검증된 서버 사용자와 대상 작품 소유자가 일치해야 한다. 새 큐 operation 1개를 만든 뒤 ensure_project 1회, acquire_edit_lease 요청 TTL 90초 1회, commit_document 1회, 정상 응답 후 release_edit_lease 1회를 허용한다. revision 2·127 bytes·SHA와 전체 고정 snapshot, release 성공을 확인한다. **사용자는 완료까지 창을 벗어나거나 최소화하지 않는다.** 일반 dispatcher, 폴더·정렬·삭제·이름변경 송신은 허용하지 않는다.

4. **iPad 측 — 수신 후 같은 화면에서 1회 송신.** Windows의 정상 release와 완료 기록을 확인한 뒤 전용 foreground 시험 화면에서 revision 2를 수신한다. 고정 snapshot과 저장 본문 일치 검사를 통과하면 **같은 화면에서** iPad 문장 추가·송신을 한 번 진행한다. acquire TTL 요청은 60초, commit 1회, 정상 성공 후 release 1회다. revision 3·158 bytes·SHA와 lease 종료를 확인한다. **사용자는 중간에 원고 화면으로 이동하거나 앱을 비활성화하지 않는다.** 이탈 시 권한/수신 상태가 무효화된다. 수신 성공 메시지가 본문이나 전체 해시를 직접 표시한다고 가정하지 않는다.

5. **Windows 측 — 최종 수신.** iPad의 정상 송신·release 기록을 받은 뒤 Windows 전용 창을 전면에 두고 **‘2. iPad 송신 완료 후 최종 본문 받기’**를 누른다. 새 foreground 권한으로 서버 전체 고정 snapshot을 검사하고, 받은 snapshot의 지정 revision 3 본문 그대로 기존 파일 저장·baseline 적용에 전달한다. 이 동작에는 데이터 쓰기 RPC가 없다. **사용자는 완료까지 창을 유지한다.** Windows를 재시작했더라도 첫 송신은 다시 누르지 않으며, 완료된 송신/lease 기록이 있어야 최종 수신이 가능하다.

6. **양쪽 — 새 5줄 관찰·감사·종료.** 양쪽 lease가 정상 종료됐음을 확인하고 네트워크를 끈 뒤 새 최종 5줄을 확인한다. Windows는 지정 TXT를 편집 없이 열어 확인하고 iPad는 최종 원고를 관찰한다. 새 operation/receipt/revision/hash와 대상 외 파일/행/기존 큐·관문/hold·marker 보존을 감사한다. 사용자 원고와 민감한 원시 증거는 외부 전달 묶음에 넣지 않는다. **사용자는 새 5줄 확인 후 양쪽 앱을 종료한다.** 기존 3줄 수신 관찰·UUID 교정·해시 보완은 반복하지 않는다.

이 승인은 위 두 후보의 설치와 위 문구의 **각 방향 commit 1회**까지다. 일반 시험 관문/hold 변경, 전역 sendingAllowed 해제, prod 전환, 일반 자동 동기화 시험으로 확대하지 않는다. Windows 전용 창은 일반 시작 경로를 실행하지 않으므로 전역 offline marker를 해제할 필요도 없다. 이 창에서 누른 제한된 동작만 서버에 접근한다. 실제 원고/DB 쓰기는 이 승인 후의 앱 동작에 한정하며 사전 진단을 이유로 실제 DB를 제자리에서 열지 않는다.

## 멈추는 경우

- 계정/기기/endpoint/작품 mode/epoch/binding, 본문·정렬·11폴더·tree_orders, 로컬 UUID/경로/본문/큐가 기준과 다르거나 대상에 기존 활성 작업이 있으면 중단한다. 추가·수정된 원고를 덮어쓰거나 구조를 보정하지 않는다.
- 5분 foreground 권한 만료, 창 이탈·최소화·중지·닫기는 이후 HTTP 및 로컬 적용을 차단한다. 이미 전송 경계를 지난 요청은 서버에 도착했을 수 있으므로 실패로 단정하지 않는다.
- commit/lease 응답 유실, 잘못된 응답, 충돌, 저장 이후 큐/metadata 실패는 journal과 현재 파일/DB/operation을 보존하고 중단한다. 자동 재시도·큐 회수·rebase·작업 삭제·백업 자동 복원·다음 방향 송신은 하지 않는다. 정상 성공 경로 이외에는 자동 lease 정리도 수행하지 않는다.
- Windows의 `body-validation-20260911/send.jsonl`과 `receive.jsonl`은 AppData의 영구 시도 기록이다. 새 실행 권한은 아니며, 기록 삭제로 다시 시도하지 않는다. acquire 응답을 잃었으면 토큰을 모르더라도 lease가 남았을 가능성을 기록한다. 필요하면 승인된 범위의 읽기 감사로 영수증/실제 lease 만료를 확인하고 상황을 보고한다. 미확정 상태에서 계속 송신하는 변경은 이번 범위가 아니다.
- 프로그램/DB와 파일 교체는 하나의 원자적 거래가 아니다. 파일만 저장되고 큐/metadata 적용이 끝나지 않은 상태도 보존 기록에 남긴다. 안전하게 멈추는 것을 전체 거래가 자동 롤백된 것으로 표현하지 않는다.

## 이번 준비의 구현·검증 근거

- 기존 미커밋 변경을 포함한 입력을 보존했다. 기존 파일 변경은 `main.py`, `sync_manager.py`, `sync_v2_store.py`, `project_manager_writing.py`와 이전 오프라인 기준 모듈의 최종 수신 방향 추가다. 전용 UI/service/최종 HTTP transport/build 선택 모듈 및 새 검사를 추가했다.
- 일반 소스의 `BODY_VALIDATION_ONLY=False`는 유지한다. **후보의 동결 소스만 True**로 고정했으며 EXE에 포함된 bytecode에서도 확인했다. runtime 환경변수로 일반 시작 경로를 다시 여는 전환은 제공하지 않는다.
- 기존 `WritingProjectManager`에 비교 저장을 추가하고, 기존 `SyncV2Store.enqueue/mark_attempt/mark_success/apply_remote_snapshot`과 Supabase client 생성 경로에 연결했다. 별도 과거 업로드 도우미나 일반 `_process_v2_operation`/자동 dispatcher의 성공을 대신 주장하지 않는다. `open_existing_current_schema`는 schema 8013에만 연결하고 마이그레이션·중단 큐 회수를 하지 않는다.
- 최종 HTTP transport는 실제 직전의 계정 bearer·대상/operation/로컬 바이트/큐/프로젝트 상태를 대조한다. 지정 Auth, 대상 5종 GET 및 정확한 4종 RPC만 허용한다. SDK 재호출도 같은 소모된 예산을 다시 통과해야 한다. redirect/transport retry/환경 proxy는 끈다. 네트워크 직전 요청 기록을 fsync한다.
- Windows 새 격리 검사 **27개 통과**. 실제 제품 SQLite/파일 저장/SDK를 사용하되 합성 DB/파일·fake HTTP로 실행하고 OS socket과 격리 밖 SQLite를 차단했다. 원고 왕복, 다른 ID_BASED 작품 큐/다른 테이블/파일 보존, snapshot/로컬 변동, 응답 유실·충돌·lease 실패, 중복 HTTP·재시작·취소·만료·부분 저장을 다룬다. 완료된 기존 15개/UUID/수신/해시/과거 전체 검사는 재실행하지 않았다.
- 최초 두 실패는 fixture의 sibling order 및 LEGACY→ID_BASED 직접 전이 오류였고 fixture를 제품 규칙에 맞췄다. 추가 최종 경계 검사에서 inflight attempt는 완료 횟수 대신 dispatch event로 판정해야 함을 확인해 수정했다. 실패 로그를 보존했으며 최종 합계에 더하지 않는다.
- 고정 입력 **97개**, 포함된 로컬 Python module과 main의 재귀 code object 대조, 고정 build flag/entry 분기, 데이터·Qt runtime DLL 검사를 통과했다. Qt 초기화와 전용 창의 두 시작 smoke는 격리 경로에서 실행해 프로젝트/DB를 생성하지 않았다. 화면 캡처를 사용하지 않았다.
- 첫 빌드는 lease 불명 기록 보완 전 초안이며 배포 대상이 아니다. `final/candidate`의 위 SHA를 가진 EXE만 승인 대상이다. 최종 27개 검사와 빌드 소스는 일치한다.
- iPad의 37개 통과와 후보/서명 정보는 제공 보고 및 완료된 소스 검토 근거다. Windows에서 Swift/Simulator/실기기/서명 검증을 재실행하지 않았다. 양쪽 합계를 합쳐 하나의 동일 환경 검사 수처럼 표현하지 않는다.

근거 디렉터리: `_evidence/windows-body-validation-candidate-20260911/`의 `tests-final.log`, `before-source/`, `final/source-manifest.json`, `final/build-verification.json`, `final/smoke-result.json` 및 `final/source/`. iPad 수용 근거는 `docs/ipad-bidirectional-snapshot-review-accepted-2026-09-11.md`와 해당 evidence다.

사용 SDK 인터페이스는 설치된 Supabase Python 2.31.0 소스와 [공식 Python 초기화 문서](https://supabase.com/docs/reference/python/initializing)를 대조했다. 서비스/서버 설정은 변경하지 않았다.
