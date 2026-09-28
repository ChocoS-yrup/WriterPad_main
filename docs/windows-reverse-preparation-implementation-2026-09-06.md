# Windows 역방향 검토 요청 준비·보존·내보내기 구현

2026-09-06 KST. **요청한 검토 전용 기능과 자동 송신 제외 처리를 소스에 구현했고, 관련 회귀 232개 및 최종 신규 기능 검사 13개가 모두 통과했다. 실제 관문 개방·전송·재전송은 0회다.**

현재 설치된 EXE는 기존 버전 그대로다. 새 실행 파일 빌드·설치 및 실제 작품의 준비 요청 발급은 이번 단계에서 하지 않았다. 동봉된 요청 예제는 격리 시험의 합성 요청이며 실제 전송용이 아니다.

## 구현한 동작

설정의 클라우드 영역에 **‘역방향 계약 검토 · 최종검증03’** 카드를 추가했다.

- **3권 검토 요청 준비:** 최종검증03의 연결·계정 식별·기존 핸드셰이크·구조 수신 기준·닫힌 관문·미완료 큐 없음·로컬 구조를 확인한다. 원고 아래 빈 3권 create(base 0)와 기존 원고 tree_order reorder(base 1) 두 intent만 만든다. 기존 순서 UUID를 보존하고 children은 `[1권,2권,새 3권]`이다.
- **저장된 요청 내보내기:** 저장된 원본 envelope를 JSON 파일로 원자적으로 내보낸다. 내보내기 취소·실패로 요청이 재발급되지 않는다. 작품 폴더 내부로의 내보내기는 거절한다.
- 실제 앱에 연결된 `writer_device_id`와 실행 프로세스가 읽은 `CLIENT_BUILD_ID`를 builder에 전달한다. 외부 검증 스크립트에서 소스 기본값을 실제 기기 값으로 추정해 넣지 않는다.
- 새 3권/batch/operation ID는 최초 준비에서 한 번 발급한다. 같은 기준의 재준비·재열기·재내보내기에서 같은 요청·해시를 유지한다. 기준이나 계정/기기/build가 달라지면 기존 요청을 보존하고 새 발급을 거절한다.
- 이전 요청 내보내기는 현재 구조가 달라졌어도 가능하다. 그 파일은 과거에 보존한 검토 원본이며, 현재 서버 상태나 송신 승인을 뜻하지 않는다. 다른 로그인 계정에서는 내보내지 않는다.

대상은 `1bd47431-0773-482c-8eb5-ac9e2952b6f4`의 원고 `14df4a55-b7cd-4790-bc53-f84148418c0f`, 기존 1권 `c0b43fc4-89a0-4a5a-b140-768a62cfde5a`, 기존 2권 `4711b2ce-5de9-44ba-80fe-570515839549`, 기존 순서 `68c1a7b5-0dda-49ba-bc9a-42e92ce2b758`로 제한했다. 일반 폴더 생성이나 25화 자동 생성 명령을 호출하지 않는다.

## 보존과 자동 송신 제외

`sync_contract_preparations`를 기존 송신 테이블과 별도로 추가했다. schema user_version은 8008→8009다. local_key/purpose별 준비 1개와 envelope/hash를 저장하며 UPDATE/DELETE를 거절한다. 기존의 명시적 작품 purge 절차에 한해서만 연쇄 삭제를 허용한다.

준비 과정은 `sync_contract_batches`, `sync_structure_operations`, 일반 작업·사건, 폴더·문서·identity·설정을 수정하지 않는다. 일반 dispatcher의 조회 대상에도 preparation 테이블을 추가하지 않았다. 준비 ID를 송신 batch ID로 삽입하려는 경로는 DB 트리거로 거절한다.

합성 DB의 관문을 나중에 열어 둔 상황에서도 일반 자동 재시도 및 일반 수동 재시도가 준비 요청을 선택하지 않았다. 이 시험은 임시 DB에서 수행했고 실제 관문은 열지 않았다.

**수동 송신/승격 API나 버튼은 구현하지 않았다.** 이번 승인 범위인 준비·보존·내보내기와 자동 송신 제외까지만 구현했다. 이후 같은 불변 요청의 실제 송신이 필요하면 별도의 명시적 경로와 C9 재검사 연결을 검토해야 한다. 기존 C4/C5/C9 구현은 재작성하지 않았다.

## 검증 결과와 범위

| 검증 | 결과 |
|---|---|
| 관련 회귀 | 232 실행 / 232 통과 / 실패·건너뜀 0 |
| 최종 신규 기능 검사 | 13 실행 / 13 통과 / 실패·건너뜀 0 |
| 준비 전후 기존 DB 테이블·작품 파일 | 합성 fixture에서 동일 |
| 재준비·저장소 재열기·JSON 재내보내기 | 동일 요청·ID·해시 유지 |
| 8008 형태에서 schema 확장 | 기존 테이블 행 보존 |
| immutable UPDATE/DELETE·송신 큐 삽입 | 거절 |
| 원고 순서 변경·새 로컬 3권·2권 내용 발생·큐 활동 후 해소 | 재준비 거절, 원본 보존 |
| 핸드셰이크 부재·계정/작품 변경·읽기 중 연결 세대 변경 | 준비 거절 |
| 자동/일반 수동 dispatcher | 준비 요청 송신 호출 0 |
| 설정 카드 처리·내보내기 취소/실패 | 제품 메서드 연결 및 원본 보존 |
| 합성 내보내기 예제 | 제품 준비/내보내기 메서드 사용, RPC 0회, 대기 작업 0, 관문 닫힘 |

13개는 232개에 포함되며 합산해 245개의 서로 다른 검사로 보고하지 않는다. 232개 회귀 후 내보내기 중 작품 경로를 잠금 안에서 확보하도록 조정하고 import 위치를 정리했으며, 최종 코드에서 13개를 재검사했다.

실행 명령:

```text
python -m unittest tests.test_contract_preparation tests.test_contract_send_policy tests.test_contract_followup tests.test_handshake_stability tests.test_sync_contract_stage8 tests.test_sync_diagnostics -v
python -m unittest tests.test_contract_preparation -v
```

검사는 Qt offscreen과 임시 SQLite/디스크·가짜 계정/응답을 사용한다. 화면 캡처·실기기 전송·실제 원고 본문 읽기는 하지 않았다. 새 테스트는 제품 경계와 기존 회귀에 필요한 범위로 한정했으며 첫 2권 모의 수신/실제 송신을 반복하지 않았다.

준비 기준 지문은 현재 앱이 받은 로컬 메타데이터·identity·설정 순서·원고 파일 이름/크기와 영속 큐 이력이다. 서버의 신규 SELECT나 새 네트워크 handshake를 이 준비 메서드가 호출하는 것은 아니다. envelope에 이 범위를 명시했다. 실행 직전의 최신 서버 revision·이름·권한·동시 편집 검증을 대신하지 않으며, 원고 본문 바이트 동일성도 주장하지 않는다.

## 실제 Windows 보존 확인 — 19:25:37

읽기 전용 재조회 결과, 작업 전과 폴더·문서·순서·큐·설치 해시가 같았다. 폴더 12개, 문서 메타데이터 26개, 원고 파일 25개, 원고 순서 revision 1 `[1권,2권]`, 빈 2권, 열린 관문 0/15, 미완료 작업 0, 송신 계약 배치 0이다. 3권은 DB·identity·디스크에 없다.

실제 DB에는 새 preparations 테이블이 아직 없으며 마이그레이션하지 않았다. 설치 SHA256은 기존 `202a7487e85aa399dcedab7897f2621d63c4a765ead8824cef5f7f18b495d72c` 그대로다. 실제 새 writer_device_id/client_build_id와 실제 준비 요청은 아직 내보내지 않았다.

## 소스 식별과 전달 근거

base HEAD: `e5ff6e0b868deca909b9f6d0817bf328d5bf77ed`. 커밋·푸시하지 않은 작업 변경이다.

- 신규: `contract_preparation.py`, `tests/test_contract_preparation.py`.
- 수정: `sync_v2_store.py`, `sync_manager.py`, `settings_panel.py`.
- `implementation.patch` SHA256: `f36e0be6da863fa0c521608b15d31dd6d393e59b085354ed21ba636c121e8c64`.
- 근거: `_evidence/windows-reverse-preparation-implementation-20260906/`의 전체 변경 파일, 패치, 파일별 해시, tests.log, targeted-final.log, implementation-verification.json, 실제 읽기 전용 상태, 합성 예제와 검증 JSON.

`synthetic-prepared-request.json`은 시험용 UUID/계정으로 만든 예제다. 이 파일의 ID·해시를 실제 역방향 요청에 사용하지 않는다.

## 사용자가 지금 할 행동

1. **이 구현 결과와 `windows-reverse-preparation-implementation-20260906.zip`을 iPad 측으로 보내세요.** “검토 전용 준비·내보내기 및 자동 송신 제외 구현/격리 검증 완료, 실제 준비 요청은 아직 없음”이라고 전달하세요.
2. **양쪽 앱의 추가 조작은 없습니다. 관문은 계속 닫아 두세요.** 현재 설치본에는 새 버튼이 아직 없습니다.

다음 단계는 이 결과의 대조 회신 후 새 실행 파일의 빌드·설치와 실제 앱에서 닫힌 관문으로 고정 요청을 준비·내보내는 단계다. 실제 전송·재전송은 이번 구현 완료로 승인되지 않는다.
