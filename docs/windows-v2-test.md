# Windows Supabase v2 테스트

## 1. Supabase 준비

Supabase SQL Editor에서 순서대로 실행한다.

1. `supabase/migrations/20260714000000_supabase_v2_protocol.sql`
2. `supabase/migrations/20260714010000_windows_v2_client_support.sql`

이미 첫 번째 SQL을 적용했다면 두 번째 SQL만 추가 실행해도 된다.

Supabase Authentication의 Users 화면에서 테스트할 이메일 사용자를 만든다. 앱의 `.env`에는 `SUPABASE_URL`과 publishable `SUPABASE_KEY`만 둔다. service role key는 사용하지 않는다.

## 2. Windows 앱 실행

```powershell
python main.py
```

1. 설정 → 프로그램 설정 → **Supabase v2 동기화 계정**에서 로그인한다.
2. 집필 모드에서 문서를 열고 수정한다.
3. `Ctrl+S`를 누른다.
4. 상단 상태가 `서버 동기화 중`에서 `저장됨`으로 바뀌는지 확인한다.
5. Supabase의 `documents`와 `document_versions`에서 동일 `document_id`, 증가한 `revision`을 확인한다.

## 3. 오프라인 큐 확인

네트워크를 끊은 상태에서 수정 후 `Ctrl+S`를 누르면 상단에 `오프라인 임시 저장됨 · N건 대기`가 표시되어야 한다. 앱을 완전히 종료했다 다시 실행해도 대기 건수가 유지되어야 한다.

큐 DB 위치:

```text
%LOCALAPPDATA%\AntigravityWriter\sync_v2.sqlite3
```

네트워크 복구 후 상태 버튼을 누르거나 Supabase 로그인에 성공하면 같은 `operation_id`로 재시도한다.

## 4. 이름 변경·이동 확인

서버 저장을 한 번 완료한 문서의 이름을 바꾸거나 다른 폴더로 이동한다. 서버에서 `document_id`는 그대로이고 `relative_path`와 `revision`만 변경되어야 한다.

## 5. 충돌 확인

같은 문서를 다른 기기에서 먼저 저장한 뒤 Windows에서 이전 revision 기준 편집본을 저장한다.

- 서로 다른 줄을 수정했으면 3방향 자동 병합 후 재전송된다.
- 같은 줄을 수정했으면 상단에 `충돌 해결 필요`가 표시된다.
- 원본 로컬 원고는 덮어쓰지 않는다.
- `집필모드/백업/충돌`에 마지막 공통본 표시가 포함된 병합본, 로컬본, 서버본이 생성된다.
- 충돌 상태 버튼을 누르면 충돌 폴더가 열린다. 원고를 직접 정리하고 다시 `Ctrl+S`하면 서버 최신 revision을 기준으로 새 커밋을 시도한다.

## 6. 자동 테스트

```powershell
python -m unittest discover -s tests -v
```

`test_sync_v2.py`가 SQLite 재시작 복구, operation 순서, revision 승격, UUID 이동 보존, RPC 요청, 3방향 병합을 검증한다.

## 7. Windows 두 인스턴스 충돌 테스트

일반 `python main.py`는 단일 실행만 허용하므로 두 기기 시험에는 전용 실행기를 사용한다.

```powershell
python windows_v2_dual_test.py new
```

이 명령은 원본 작품을 건드리지 않는 A/B 테스트 작품을 만들고 창 두 개를 연다. 창 제목에는 각각 `V2-A`, `V2-B`가 표시된다. 두 창은 다음 항목만 공유한다.

- 같은 Supabase `project_id`
- 처음 생성된 `충돌테스트.txt`의 같은 `document_id`

원고 폴더, device UUID, SQLite 큐, 로그인 세션, 단일 실행 키는 서로 분리된다. `.env`에 테스트 계정이 없으면 두 창에서 각각 설정 → 프로그램 설정 → Supabase v2 계정 로그인을 한다.

### 최초 기준본

각 새 테스트 세트에서 먼저 A의 `충돌테스트.txt`를 열고 내용은 바꾸지 않은 채 `Ctrl+S`를 한 번 눌러 revision 1 기준본을 만든다.

### 동시에 저장

1. A와 B에서 같은 문서를 연다.
2. 같은 `양쪽에서 겹쳐 바꿀 줄`을 서로 다르게 수정한다.
3. 두 창에서 거의 동시에 `Ctrl+S`를 누른다.
4. 먼저 lease를 얻은 쪽만 저장되고, 다른 쪽은 lease 대기 또는 revision 충돌 상태가 되어야 한다.
5. lease 대기라면 먼저 저장된 창에서 다른 문서를 열어 lease를 놓은 뒤, 대기 중인 창의 저장 상태 버튼을 누른다.
6. revision 충돌까지 진행된 쪽은 원고가 덮어써지지 않고 빨간 `충돌 해결 필요`와 `백업/충돌`의 3개 파일이 남아야 한다.

### 양쪽 오프라인

```powershell
python windows_v2_dual_test.py offline both
```

1. A에서는 `A에서 바꿀 줄`, B에서는 `B에서 바꿀 줄`을 수정하고 각각 `Ctrl+S`한다.
2. 두 창 모두 `오프라인 임시 저장됨 · 1건 대기`인지 확인한다.
3. 큐를 확인한다.

```powershell
python windows_v2_dual_test.py status
```

4. 온라인으로 돌린 뒤 각 창의 저장 상태 버튼을 눌러 재시도한다.

```powershell
python windows_v2_dual_test.py online both
```

A를 먼저 재시도한 다음 다른 문서를 열어 lease를 놓고 B를 재시도한다. 서로 다른 줄의 변경은 자동 병합되어 최종 서버 문서에 둘 다 남아야 한다.

### 이름 변경과 수정

새 테스트 세트에서 기준본을 만든 뒤 양쪽을 오프라인으로 전환한다.

1. A에서 `충돌테스트.txt`를 `이름변경됨.txt`로 바꾼다.
2. B에서는 옛 이름의 문서 본문만 수정하고 `Ctrl+S`한다.
3. A만 온라인으로 복구해 이름 변경을 먼저 전송한다.
4. A가 저장된 뒤 다른 문서를 열어 lease를 놓는다.
5. B를 온라인으로 복구해 재시도한다.
6. B도 `이름변경됨.txt`를 사용하고, A/B/서버의 `document_id`는 같으며 내용 수정도 남아야 한다.

### 강제 종료와 재실행

오프라인 상태에서 A를 수정·저장해 pending 작업을 만든다.

```powershell
python windows_v2_dual_test.py kill A
python windows_v2_dual_test.py status
python windows_v2_dual_test.py launch A
python windows_v2_dual_test.py online A
```

재실행 뒤 대기 건수가 그대로이고, 재시도할 때 새 operation을 만들지 않고 종료 전 `operation_id`를 그대로 사용해야 한다. 실행 중인 테스트 폴더는 다음 명령으로 확인한다.

```powershell
python windows_v2_dual_test.py paths
```
