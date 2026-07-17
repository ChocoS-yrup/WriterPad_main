# Supabase 동기화 v2 규약

- 상태: **확정(Baseline 2.0)**
- 확정일: 2026-07-14
- 적용 범위: 집필 문서의 생성, 본문 수정, 이름 변경, 이동, 삭제, 복원, 버전 조회, 편집권
- 기준 SQL: `supabase/migrations/20260714000000_supabase_v2_protocol.sql`
- Windows 보완 SQL: `supabase/migrations/20260714010000_windows_v2_client_support.sql`

이 문서에서 **MUST**, **MUST NOT**, **SHOULD**는 구현상의 강제 규칙이다. v2 클라이언트와 서버는 이 규칙을 임의로 완화하지 않는다.

## 1. 한 줄 원칙

문서의 정체성은 불변 UUID인 `document_id`, 순서는 서버가 증가시키는 `revision`, 재시도 동일성은 클라이언트가 생성하는 `operation_id`로 판단한다. 생성·수정·이동·삭제·복원은 모두 `commit_document` RPC 한 곳에서 원자적으로 처리한다.

## 2. 식별자와 시간

### `document_id`

- UUID v4이며 클라이언트가 문서 생성 전에 한 번 만든다.
- 생성 후 절대 바뀌지 않는다.
- 파일명, `relative_path`, 프로젝트명은 식별자가 아니다.
- 이름 변경과 폴더 이동은 같은 `document_id`의 새 revision이다.
- 로컬에는 경로와 별개인 영속 메타데이터로 저장해야 한다.

### `revision`

- 문서별 `bigint` 단조 증가 정수다.
- 첫 커밋은 `1`, 이후 승인된 커밋마다 정확히 `+1` 한다.
- 클라이언트는 값을 만들지 않고 `base_revision`으로 자신이 편집한 기준 revision만 보낸다.
- 서버의 현재 revision과 `base_revision`이 다르면 `REVISION_CONFLICT`이며 자동 덮어쓰지 않는다.
- 수정 시각은 충돌 판정에 사용하지 않는다. 모든 판정은 revision으로 한다.

### `operation_id`

- UUID v4이며 **사용자 동작 한 번당** 클라이언트가 한 번 만든다.
- 네트워크 타임아웃, 앱 재시작, 재전송에서도 같은 동작은 같은 ID를 재사용한다.
- 전역으로 유일하다.
- 같은 ID와 같은 요청을 다시 보내면 새 revision을 만들지 않고 최초 결과를 `replayed`로 반환한다.
- 같은 ID를 다른 문서나 다른 payload에 재사용하면 `OPERATION_ID_REUSED`다.

### 시간

- `created_at`, `updated_at`, `deleted_at`, lease 만료 판정은 모두 DB 서버 시각을 사용한다.
- 클라이언트 시계는 정렬·충돌·만료의 권위가 아니다.

## 3. 서버 데이터 모델

### `documents`: 현재 상태 projection

한 문서당 한 행만 존재한다.

| 필드 | 규약 |
|---|---|
| `document_id` | 불변 PK |
| `project_id` | 소속 프로젝트 |
| `relative_path` | 현재 표시 경로. 식별자 아님 |
| `content` | 현재 본문 전체 snapshot |
| `revision` | 현재 revision |
| `current_version_id` | 현재 상태를 만든 immutable 버전 |
| `is_deleted` | tombstone 여부 |
| `deleted_at` | 삭제 시 서버 시각, 활성 문서는 `null` |
| `created_by`, `updated_by` | Supabase Auth 사용자 ID |
| `created_at`, `updated_at` | 서버 시각 |

같은 프로젝트에서 삭제되지 않은 문서의 `relative_path`는 유일해야 한다. 경로 충돌 시 서버는 임의 이름을 만들지 않고 `PATH_CONFLICT`를 반환한다.

### `document_versions`: append-only 원장

승인된 커밋마다 정확히 한 행이 추가된다. update/delete를 허용하지 않는다.

각 행에는 `document_id`, `revision`, `base_revision`, `operation_id`, `device_id`, `operation_kind`, 당시의 `relative_path`, 전체 `content`, `content_hash`, `is_deleted`, 작성자와 서버 시각이 들어간다. tombstone도 복원 가능하도록 삭제 직전 본문 전체를 보존한다.

강제 유일성:

- `(document_id, revision)` unique
- `operation_id` unique
- `version_id` PK

`operation_kind`는 `create | update | move | delete | restore` 중 하나이며 서버가 이전 상태와 새 상태를 비교해 결정한다.

## 4. 유일한 쓰기 경로: `commit_document` RPC

클라이언트의 `documents`/`document_versions` 직접 INSERT, UPDATE, DELETE는 금지한다.

요청:

```text
commit_document(
  p_document_id uuid,
  p_project_id uuid,
  p_base_revision bigint,
  p_operation_id uuid,
  p_device_id uuid,
  p_relative_path text,
  p_content text,
  p_is_deleted boolean,
  p_lease_token uuid
)
```

생성은 `base_revision = 0`, `is_deleted = false`, `lease_token = null`로 요청한다. 기존 문서의 수정·이동·삭제·복원은 유효한 lease가 반드시 필요하다.

서버는 한 트랜잭션 안에서 다음 순서를 지킨다.

1. 로그인과 프로젝트 편집 권한 확인
2. 입력 크기와 상대 경로 검증
3. 프로젝트 단위 transaction lock 획득
4. 기존 `operation_id` 확인 및 동일 요청 replay
5. 기존 문서 행 `FOR UPDATE` 잠금
6. `base_revision` 일치 확인
7. 기존 문서는 lease의 사용자·기기·토큰·만료 확인
8. 활성 경로 중복 확인
9. `document_versions`에 immutable snapshot 추가
10. `documents` projection 생성 또는 갱신
11. `current_version_id` 연결 후 결과 반환

9~11 중 하나라도 실패하면 전부 rollback한다. 현재 문서만 바뀌거나 이력만 남는 부분 성공은 허용하지 않는다.

성공 응답은 JSON이며 최소 필드는 다음과 같다.

```json
{
  "status": "committed | replayed",
  "document_id": "uuid",
  "version_id": "uuid",
  "operation_id": "uuid",
  "operation_kind": "update",
  "revision": 12,
  "relative_path": "메인/원고/1권/012화.txt",
  "is_deleted": false,
  "content_hash": "sha256 hex",
  "committed_at": "server timestamp"
}
```

본문은 UTF-8 기준 최대 10 MiB, 경로는 최대 1024자다. 경로는 `/` 구분자를 사용하며 절대 경로, `\\`, 빈 segment, `.`/`..` segment, 앞뒤 공백을 금지한다.

## 5. Edit lease

lease는 충돌을 줄이는 편집권이고 revision 검사를 대체하지 않는다.

- 단위: 문서 1개
- holder: `auth.uid() + device_id`
- 기본 TTL: 90초
- 허용 TTL: 30~120초
- 권장 갱신: 30초마다, 그리고 저장 직전
- 판정 시각: DB 서버 시각
- 앱 설치마다 영속적인 `device_id` UUID를 하나 사용
- lease token은 비밀값으로 취급하며 다른 사용자에게 조회 노출하지 않음

RPC:

- `acquire_edit_lease(document_id, device_id, ttl_seconds)`
- `renew_edit_lease(document_id, device_id, lease_token, ttl_seconds)`
- `release_edit_lease(document_id, device_id, lease_token)`
- `get_edit_lease(document_id, device_id)` — token 없이 `available | held_by_me | held_by_other`만 반환

Windows 클라이언트는 로컬 프로젝트에 최초 UUID를 부여한 뒤 `ensure_project(project_id, name)`를 호출한다. 프로젝트가 없으면 현재 인증 사용자를 owner로 생성하고, 이미 있으면 editor 이상 권한만 이름을 동기화할 수 있다.

같은 사용자·같은 기기의 재획득은 기존 token을 유지하며 만료를 연장한다. 다른 holder의 유효 lease가 있으면 `LEASE_CONFLICT`다. 만료된 lease는 획득 과정에서 폐기할 수 있다.

기존 문서의 모든 커밋은 유효 lease를 요구한다. 트리에서 바로 삭제하더라도 먼저 lease를 획득해야 한다. tombstone 커밋 성공 시 lease는 즉시 해제한다.

오프라인에서는 로컬 저장과 작업 큐 적재만 허용한다. 서버 커밋 성공으로 표시해서는 안 된다. 재연결 후 lease를 새로 획득하고 원래 `base_revision`과 `operation_id`로 전송한다.

## 6. Tombstone 삭제와 복원

- 앱 클라이언트는 문서 행을 물리 DELETE하지 않는다.
- 삭제는 `p_is_deleted = true`인 `commit_document`다.
- 삭제도 revision과 version을 생성하며 다른 기기에 일반 변경처럼 전파된다.
- 기본 목록은 `is_deleted = false`, 휴지통은 `is_deleted = true`를 조회한다.
- 복원은 같은 `document_id`에 `p_is_deleted = false`를 커밋한다.
- 원래 경로가 이미 사용 중이면 `PATH_CONFLICT`다. 클라이언트가 사용자에게 새 경로를 받은 뒤 새 operation으로 재시도한다.
- tombstone의 물리 정리는 service role 전용 별도 작업이다. v2 기준 보존 기간은 최소 90일이며 앱 RPC/RLS로 실행할 수 없다.

## 7. RLS와 권한

역할은 프로젝트별 `owner | editor | viewer`다.

| 대상 | owner | editor | viewer | anon |
|---|---:|---:|---:|---:|
| documents 읽기 | O | O | O | X |
| versions 읽기 | O | O | O | X |
| lease 조회 RPC | O | O | O | X |
| lease 획득/갱신/해제 | O | O | X | X |
| commit_document | O | O | X | X |
| 테이블 직접 쓰기 | X | X | X | X |

모든 노출 테이블은 RLS를 enable/force한다. 정책은 `auth.uid()`와 프로젝트 멤버십으로 읽기 범위를 제한한다. 쓰기는 권한 검사와 불변식 보장을 포함한 `SECURITY DEFINER` RPC만 허용하고, 함수는 빈 `search_path`와 완전 수식 테이블명을 사용한다. `anon`에는 테이블·RPC 권한을 주지 않는다.

Service role key는 데스크톱/iPad 앱, 번들, 설정 파일에 절대 포함하지 않는다. 앱은 publishable/anon key와 사용자 JWT만 사용한다.

## 8. 동기화 순서

### Pull

1. 프로젝트의 `documents`를 `updated_at`만으로 증분 판정하지 말고, 각 `document_id`의 `revision`과 비교한다.
2. 서버 revision이 로컬보다 크면 projection을 적용한다.
3. `is_deleted = true`도 반드시 로컬 tombstone으로 반영한다.
4. 로컬 미전송 작업이 있는 문서에 더 높은 서버 revision이 도착하면 덮어쓰지 않고 충돌본으로 분리한다.

Realtime은 깨우기 신호일 뿐 전달 보장 원장이 아니다. reconnect, 앱 시작, 포그라운드 복귀 시 반드시 pull로 누락을 보정한다.

### Push

1. 로컬 파일 저장
2. 동일 문서의 대기 작업을 순서대로 처리
3. lease 획득/갱신
4. `base_revision`과 고정 `operation_id`로 `commit_document`
5. 성공/`replayed`이면 서버 revision·hash를 로컬 메타데이터에 반영하고 큐 제거
6. `REVISION_CONFLICT`이면 자동 force overwrite 금지, 서버본/로컬본을 모두 보존

같은 문서에는 동시에 RPC를 두 개 보내지 않는다. 서로 다른 문서는 병렬 전송할 수 있다.

## 9. 안정 오류 코드

클라이언트는 번역된 메시지가 아니라 다음 코드로 분기한다.

| 코드 | 의미 | 기본 처리 |
|---|---|---|
| `AUTH_REQUIRED` | 로그인/JWT 없음 | 로그인 요구 |
| `FORBIDDEN` | 프로젝트 권한 부족 | 쓰기 중단 |
| `INVALID_ARGUMENT` | UUID, 경로, 크기, 생성 조건 오류 | 요청 수정 |
| `DOCUMENT_NOT_FOUND` | 대상 문서 없음 | pull 후 재판단 |
| `DOCUMENT_ALREADY_EXISTS` | 생성 UUID 중복 | 새 문서 생성 중단 |
| `REVISION_CONFLICT` | base revision 불일치 | 양쪽 보존 후 사용자 해결 |
| `OPERATION_ID_REUSED` | 동일 ID의 payload 불일치 | 새 동작 ID 발급, 버그 기록 |
| `LEASE_REQUIRED` | token 없음 | lease 획득 |
| `LEASE_CONFLICT` | 다른 holder가 편집 중 | 읽기 전용/대기 |
| `LEASE_EXPIRED` | lease 만료 또는 token 불일치 | 재획득 후 revision 재확인 |
| `PATH_CONFLICT` | 활성 경로 중복 | 사용자에게 새 이름 요청 |

## 10. v1과의 경계

v1의 `writing_contents`, `writing_history`, `editor_locks` 직접 접근은 v2 쓰기 경로에서 사용하지 않는다. 마이그레이션 시 각 기존 파일에 `document_id`를 한 번 부여하고 현재 내용을 revision 1의 `create` 버전으로 적재한다. 전환 후에는 `project_name + relative_path`를 식별키나 충돌키로 다시 사용하지 않는다.

v2 클라이언트가 준비되기 전까지 v1과 v2의 동시 양방향 쓰기는 금지한다. 전환은 읽기 전용 점검 → 일회성 데이터 이관 → v2 클라이언트 배포 순서로 한다.

## 11. 출시 차단 조건

다음 계약 테스트를 통과하기 전에는 v2를 활성화하지 않는다.

1. 같은 operation 재전송이 revision을 두 번 올리지 않는다.
2. 같은 base revision의 두 커밋 중 하나만 성공한다.
3. version insert 또는 projection update 실패 시 둘 다 남지 않는다.
4. 만료·타 사용자·타 기기 lease로 커밋할 수 없다.
5. rename/move 후에도 document_id가 유지된다.
6. tombstone이 다른 기기에서 실제 파일 재생성을 막는다.
7. 복원 경로 충돌이 자동 덮어쓰기를 일으키지 않는다.
8. viewer와 비멤버가 RPC 쓰기 및 타 프로젝트 읽기를 할 수 없다.
9. 직접 테이블 쓰기가 owner에게도 거부된다.
10. 10 MiB 초과 본문과 비정규 경로가 거부된다.
