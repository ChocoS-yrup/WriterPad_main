# Windows 일반 시험 기준·요청 제한 준비 결과

사용자의 현재 “다음 진행”에 따라 Windows의 새 ID_BASED 시험 기준과 요청 제한·시도 기록 모듈을 구현하고 새 격리 검사 17개를 통과했다. 실제 일반 검증 UI/인증/dispatcher 구성에 연결된 설치 후보까지 완료된 것은 아니다. 현재 설치 EXE, 원고, 실제 DB, 관문, hold를 변경하지 않았다. 첨부의 과거 실행 지시는 이번 실행 승인으로 사용하지 않았다.

## 고정한 새 합성 본문 제안

작품은 `일반동기화 검증 20260910`, server UUID `d8f50b5f-ae0e-42f8-9296-5d5885a5b304`, ID_BASED / epoch 1이다. iPad local UUID는 `a9452cd1-4474-40b5-80ca-fbb7871e98e5`이며 server UUID와 같게 바꾸지 않는다.

- 계획 ID: `general-body-20260912-v1`.
- 제안 문서 UUID: `db8a3cc2-8b1a-5539-841c-042de34f5fd6`. 오프라인으로 정한 값이며 서버의 부재·충돌을 아직 조회하지 않았다.
- 부모: 기존 원고 폴더 `f4c92790-d675-4970-b1fc-b90f3a929ffb`.
- 이름/경로: `일반본문검증 20260912.txt` / `메인/원고/일반본문검증 20260912.txt`.
- structure_revision: 생성 후 1, 이후 본문 수정에서 보존.
- 부모 정렬표: `31eb06be-9cc9-55db-9a05-5882172474ce`, 기준 revision 1·children `[]` → revision 2·children `[새 문서 UUID]`. 서버/양쪽의 최신 기준이 다르면 덮어쓰지 않고 중단한다.

| 단계 | 본문 revision | UTF-8 bytes | SHA-256 |
|---|---:|---:|---|
| Windows 새 본문 생성 | 1 | 100 | `d2854a290b7bec5005c87aa9eaf80002b75f22f2f5cf77cfed7d853aea3ac20f` |
| iPad 문장 추가 | 2 | 128 | `e2e2247ad6b98784bd4fdeaf45401b5a7805f58659f98abcd508f489f252eb15` |
| Windows 문장 추가 | 3 | 159 | `c1b3b5460ab657ca64c52e8556c798561015214d56ae74656d476fc181784a22` |

최종 문장(UTF-8, LF, BOM 없음, 마지막 LF 있음):

```text
일반 본문 검증 20260912
이 문서는 일반 동기화 시험용 합성 원고입니다.
끝.
iPad 일반 검증 20260912
Windows 일반 검증 20260912
```

이 본문과 해시는 완료된 LEGACY 158바이트 본문과 다르다. 실행 순서는 제안이며 사용자에게 지금 버튼을 누르라는 지시가 아니다.

## 요청 순서의 중요한 구분

보존된 서버 정의 `private.apply_structure_intent`의 document 분기는 rename/move만 받는다. 따라서 문서 생성과 정렬 변경을 atomic_structure_commit 하나에 임의로 묶지 않는다. Windows 계약 생성기도 새 본문을 document_commit(create)로 만든다.

1. Windows: 새 본문 `document_commit(create)` 1회 → revision 1 확인.
2. Windows: 기존 부모 정렬표 `atomic_structure_commit(reorder)` 1회 → 정렬 revision 2 확인.
3. iPad: 본문 revision 1 및 정렬 revision 2 수신 → `document_commit(update)` 1회 → 본문 revision 2.
4. Windows: revision 2 수신 → `document_commit(update)` 1회 → 본문 revision 3.
5. iPad: 최종 revision 3 수신 → 양쪽 최종 관찰·보존 감사.

생성과 정렬은 **별도 커밋**이다. 1번 성공 후 2번 실패 시 생성된 문서와 기록을 남기고 중단한다. 자동 삭제/복원/정렬 재시도는 하지 않는다. 완료한 시험의 lease RPC를 복사하지 않는다. 현재 검토한 ID_BASED 계약의 이 범위에는 lease RPC가 없으며, 최종 후보에서 양쪽 실제 호출 경로도 이에 맞는지 확인해야 한다. 과거 서버 정의는 설계 근거이지 최신 서버를 이번에 재조회한 증거는 아니다.

## 이번 코딩과 검증 범위

- `general_validation_plan.py`: 대상·새 본문·단계별 내용/해시와 실제 제품 계약 builder를 사용하는 요청 생성. operation/batch ID 제안은 재사용 시도가 아니라 식별 기준이다. 실제 기존 store가 새 ID를 발급한 경우 그 실제 ID와 요청을 큐 생성 후 고정하며, 예시 ID로 DB를 고쳐 넣지 않는다.
- `general_validation_boundary.py`: 고정 요청 method/URL/body 바이트 및 순서 검사, 한 요청당 1회 예산, 계정/bearer/binding·전경/만료·로컬 상태 재검사 지점, 전송 전 영구 시도 기록, 늦은 응답 거부, 계약 응답과 정확한 결과 revision 검사, 중복/응답 유실 후 정지. 자동 network transport나 인증 권한을 만들지 않는다.
- `DurableExecutionJournal`: 고정 계획·단계 파일을 exclusive create하고 fsync한다. 기존 파일은 재사용·삭제하지 않으며 앱 재시작으로 같은 단계를 다시 실행하지 않는다. 사용자 데이터·토큰·임의 payload를 기록하지 않는다.
- `tests/test_general_validation_boundary.py`: 실제 Supabase SDK의 HTTP 요청과 실제 SyncV2Store의 CONTRACT_BATCH 생성 경로를 합성 SQLite·가짜 transport로 검사한다. 다른 작품 큐 보존, 잘못된 문서/부모/본문/모드, 범위 밖 URL·관계 조회·필터·Range·schema, 변경된 JSON 바이트, 중복 호출, 화면 권한 취소/만료, 인증 변경, 기록 실패, 응답 유실, 잘못된 revision/부분 응답, 생성 성공 후 정렬 실패, 재시작 시 중복 실행 차단을 포함한다.

새 검사 최종 17개 통과. OS socket 접속과 격리 밖 SQLite를 차단했다. 초기 새 검사 15개 통과 후 journal/URL 검사를 추가해 이 새 모듈 검사만 17개로 다시 실행했다. 이전 LEGACY 왕복/UUID/수신/해시/기존 전체 검사는 반복하지 않았다. 실제 DB·서버를 시험 대상으로 사용하지 않았다.

Supabase 초기화 API는 [공식 Python 문서](https://supabase.com/docs/reference/python/initializing)와 현재 설치된 SDK 사용 경로를 대조했다. changelog markdown은 웹 도구의 content-type 제한으로 읽히지 않았다. SDK 업그레이드나 새 서버 기능 도입은 하지 않았다.

## 남은 실제 실행 구성과 양쪽 작업

Windows: 전경 UI와 실제 인증 복원/계정 검증을 연결하고, `check_current`가 계정·binding·관문/hold·화면 수명에 대한 실측 상태를 검사하도록 구성해야 한다. `check_local`에는 전체 고정 snapshot과 해당 단계의 파일·큐 상태 검사를 연결한다. 명시적 선택 작품 실행이 기존 전체 dispatcher/자동 복구/재시도/백그라운드 수신을 시작하지 않는 최종 구성 검증도 필요하다. 현재 모듈이 callback을 제공한다는 이유로 이 작업이 완료됐다고 표현하지 않는다.

iPad: 첨부의 제한 모듈에 위 제안 기준을 연결한 전경 일반 검증 UI·인증 권한·단계별 시도 기록/횟수 예산을 준비한다. 문서 lease 목록은 임의로 늘리지 않는다. JSON dictionary 직렬화 순서가 Windows와 다를 수 있으므로 예시 RPC 바이트 해시를 그대로 허용 목록에 복사하지 말고, 동일한 의미를 검사한 뒤 해당 iPad 최종 전송 바이트를 고정한다. iPad 역시 실제 설치/송수신 없이 새 구성·새 관련 검사 결과와 최종 소스 식별을 회신하면 된다.

양쪽 최종 실행 후보에서 실제 계정·기기·operation/batch ID, 인증 및 읽기 요청 목록·횟수까지 확정하고, 최신 기준 읽기/백업/설치/필요한 관문·hold 변경/송수신/종료 상태를 한 범위로 제시한 뒤 승인받는다. 이 준비 자료는 그 실행 승인이 아니다. 불확실 응답 복구 조회, 자동 재시도, Realtime 및 prod 전환은 현재 제안에 포함하지 않는다.

현재 사용자가 앱에서 누를 추가 버튼은 없다. iPad에 전달할 자료는 `windows-general-validation-preparation-20260912.zip`이며, 합성 요청 예시에 든 기기 UUID는 실제 식별값이 아니다. 원본 DB·원고·계정·토큰은 넣지 않았다.
