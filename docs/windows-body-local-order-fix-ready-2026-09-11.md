# LOCAL_ORDER_CHANGED 수정 후보 준비 — 2026-09-11

**검사 구현의 오류였다. 실제 정렬이나 원고가 바뀐 상황이 아니다.** Windows 로컬 설정은 `메인/휴지통: []`를 보존하고, 기존 LEGACY 송신 정렬은 휴지통 내부 항목을 생략한다. 전용 검증의 직접 dictionary 비교가 이 정상적인 표현 차이를 변경으로 오인했다. 앞선 합성 fixture도 서버 정렬을 그대로 로컬 설정으로 사용해 차이를 놓쳤다.

현재 설치본과 승인된 실행 범위는 그대로 보존했다. 수정 후보는 빌드·검증만 했으며 **설치하거나 다시 송신하지 않았다.** iPad 설치 결과의 검토는 완료 상태이며 추가 iPad 코드 검토/설치가 필요하지 않다.

## 중단 직후 확인

- 사용자가 보고한 오류는 `LOCAL_ORDER_CHANGED`. Windows 앱 종료를 확인했다.
- 현재 설정 파일은 새 설치 전 백업과 바이트가 같다. 서버 고정 기준과의 유일한 차이는 로컬의 빈 `메인/휴지통` 항목이다. 공통 항목의 값·순서는 모두 동일하다.
- 실제 DB 파일을 쓰기/삭제 방지 상태로 복사한 **검사 사본**에서 30개 전체 테이블 행 digest가 송신 전 백업과 일치한다. 실제 DB를 SQLite 진단으로 열지 않았다.
- 원고는 revision 1·93 bytes, 활성 작업 0. 기존 원고·설정·identity·DB 등 파일 bytes/mtime은 그대로다. AppData에는 빈 `body-validation-20260911` 디렉터리만 추가됐다. `send.jsonl`/`receive.jsonl`은 없다.
- 이 오류와 journal 부재, 코드상의 실행 순서 및 파일/행 보존을 종합하면 최초 로컬 검사에서 멈춰 본문 저장·enqueue·ensure_project·lease·commit에 도달하지 않았다. 패킷 캡처나 실서버 사후 감사로 HTTP 전체 0회를 직접 측정한 결과는 아니다. 그 이전의 인증 및 대상 GET은 수행됐을 수 있으므로 ‘서버에 전혀 연결하지 않았다’고 표현하지 않는다.

## 수정 내용과 검증

`legacy_body_order_matches`가 복사본에서 **정확히 빈 휴지통 항목 하나만** 비교 대상에서 생략한다. 로컬 설정 파일이나 서버 정렬을 고치지 않는다. nonempty 휴지통, 추가 하위 경로, 알 수 없는 키, 잘못된 타입, 실제 자식 순서 변경이나 기존 키 누락은 계속 거부한다. 일반 송신 정규화 전체를 재사용하면 실제 변동까지 감출 수 있어 이 한 가지 표현 차이만 허용했다.

실제와 같은 로컬 fixture로 수정 전 오류를 먼저 재현했다. 수정 후 새 비교/거부 검사 2개와 영향받는 왕복·응답 유실·수신 보호·중복 요청 검사 4개, **총 6개**가 통과했다. 이전 27개 전체나 완료된 UUID/기존 수신 검사를 다시 실행하지 않았다. 또한 중단 후 DB 사본과 실제 현재 파일을 읽기만 하는 검사에서도 수정된 전체 로컬 검사를 통과했다. 이 확인의 서버 입력은 보존된 고정 snapshot과 합성 계정으로, 새 실서버 조회 결과가 아니다.

## 수정 후보

| 항목 | 값 |
|---|---|
| 후보 | `windows-staging-body-empty-trash-order-20260911` |
| EXE SHA-256 | `06306aeca8c30d5be236d421cf15b09a92f5ca653d013a79fbf9e6bdfc10fec2` |
| EXE bytes | 79,410,712 |
| 고정 source digest | `5a36a8f87814b454ad3d950012be3caeb7a2f077840118ae1125967d8b75f0df` |
| 고정 입력 | 97개 |
| 이전 후보 대비 변경 | `body_validation_service.py`, 후보 ID 모듈, 해당 fixture/검사 파일 |
| 빌드 검증 | 포함 코드·일반 시작 차단·기본 잠금·데이터/Qt DLL 대조, 격리 시작 검사 2개 통과 |
| 설치 상태 | 미설치 |

파일: `D:\안티그래비티\scratch\작가님 힘내세요\_evidence\windows-body-local-order-fix-20260911\candidate\작가님 힘내세요.exe`.

## 필요한 승인과 다음 행동

기존 승인은 SHA `9893f7c434bf9a4118c58a3ef015a6a5fa94bf285c75fad5caec566b34babd3f`의 Windows EXE를 구체적으로 지정했다. **새 SHA의 수정 후보로 같은 설치 경로의 EXE를 교체하는 변경만 승인 대상**이다. 기존 양방향 각 1회 시험 전체를 다시 승인받거나 iPad 준비 검토를 반복하지 않는다.

교체 승인 후 현재 상태와 두 백업의 보존 관계를 대조하고 현재 설치 EXE도 별도로 보존한 뒤 EXE만 바꾼다. 원고·설정·DB·관문/hold·journal은 변경하거나 삭제하지 않는다. 기본 잠금을 확인하면 사용자가 전용 창의 첫 송신 버튼을 한 번 누른다. 이번에는 commit 시도에 도달하지 않았으므로 기존에 승인된 1회 commit 범위를 늘리지 않는다. Windows 정상 완료/release 전까지 iPad는 종료 상태를 유지한다. 이후 본문 문구·UUID·127/158 bytes·SHA·lease TTL·수신/관찰 범위는 원래 승인 문서 그대로다.

증거: `_evidence/windows-body-local-order-fix-20260911/order-difference.json`, `reproduced-before-fix.log`, `isolated-fix-tests.log`, `stopped-audit.json`, `source-manifest.json`, `build-verification.json`, `smoke-result.json`. 기존 소스/바이너리/검사 실패 로그와 새 private 검사 사본은 보존했다.
