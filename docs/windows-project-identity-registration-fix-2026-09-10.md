# Windows 신규 작품 UUID 등록 수정 — 2026-09-10

## 결과

Windows에서 작품 생성 후 첫 동기화 등록을 하면 로컬 identity UUID와 다른 UUID가 발급되는 결함을 합성 임시 작품에서 재현하고 수정했다. 신규 등록은 검증된 `.writerpad/identity-v1.json`의 작품 UUID를 재사용한다. 실제 기존 시험 작품의 한 항목 교정·준비 확인 완료와는 별개의 재발 방지 소스 수정이다.

`project_creation_v1._start_project_transaction`은 생성 시 작품 UUID를 발급한다. 기존 `SyncV2Store.configure_project`는 새 DB 행을 만들 때 이 UUID를 읽지 않고 별도 발급했고, `_ensure_remote_project`는 이 DB UUID를 서버 등록 요청에 넣었다. 이번 합성 재현은 이 경로의 결함을 확인한 것이며, 과거 실제 시험 작품의 최초 호출 인자까지 복원한 결과는 아니다. iPad의 수동 연결이 Windows 파일 불일치의 직접 원인이었다고 단정하지 않는다.

## 변경 범위

- `sync_v2_store.py`: 새 연결에 identity가 있으면 검증 후 작품 UUID를 재사용한다. 다른 명시적 UUID는 등록 전에 거부한다. 이미 다른 로컬 위치에 연결된 UUID도 거부한다. 잘못되거나 읽을 수 없는 identity를 새 UUID 발급으로 우회하지 않는다.
- 기존 DB 연결은 유지하며 identity 또는 원장·대기열을 자동으로 재작성하지 않는다. identity가 없는 구형 경로는 종전 발급 방식을 유지한다.
- 기존 서버 작품 가져오기는 `begin_project_import`와 서버 identity 수용 경로를 그대로 사용한다. 가져온 작품의 루트 UUID도 서버 UUID와 같은지 기존 검사에 명시적으로 추가했다.
- 제품 변경은 이번 `configure_project` 경계에 한정된다. 같은 파일에 이미 있던 schema 8013·체크포인트·purge 수정은 이번 신규 작업으로 세지 않는다.

## 격리 검증

수정 전 새 검사 10개 중 7개 실패로 결함과 누락된 보호 경계를 확인했다. 수정 후 같은 10개가 통과했다. 서버 등록 요청의 UUID를 확인하는 모의 클라이언트 검사 1개도 통과했다. 생성·identity·문서/폴더 UUID 재사용·서버 작품 가져오기·목록 관련 기존 검사 및 추가 assertion 89개가 통과했다. 수정 후 합계 100개, 실패 0, 오류 0, 건너뜀 0이다.

모든 검사는 임시 합성 작품과 별도 DB·프로필·앱 데이터 경로에서 수행했다. 네트워크 연결을 차단한 runner에서 연결 시도 0건을 확인했다. 모의 서버 등록 요청은 실제 송신이 아니다. 설치된 앱의 수신·C9 성공 근거는 이전 완료 보고서이며, 새 소스의 실기기 설치 검증으로 바꾸어 해석하지 않는다.

근거: `_evidence/windows-project-identity-registration-20260910/`의 `before-tests.json`, `after-tests.json`, `regression-tests.json`, `payload-tests.json` 및 해당 로그.

## 사용자 다음 행동

1. Windows 앱은 종료 상태를 유지한다. 새 빌드·설치는 아직 하지 않았다.
2. 함께 제공한 검토 ZIP을 iPad 개발 측에 전달한다. 아래 iPad 검토 요청 문서에 따라 기존 구현 여부부터 확인하도록 요청한다.
3. iPad 측 회신이 오면 Windows 수정과 대조하고 다음 실기기 검증 범위를 정한다. 현재 관문 개방·송신 보류 해제·실제 송신·prod 전환 승인은 포함되지 않는다.

당분간 신규 작품은 iPad에서 한 번 생성하고 Windows에서는 기존 서버 작품을 가져온다. 양쪽에서 같은 이름으로 별도 생성한 뒤 UUID만 수동 연결하는 절차는 일반 사용 방식으로 삼지 않는다.
