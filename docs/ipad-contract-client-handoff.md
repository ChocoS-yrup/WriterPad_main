# iPad 계약 클라이언트 핸드오프 — 서버 워크플로 8단계

Windows 클라이언트가 먼저 구현한 계약 `0.2.0` 경로를 iPad가 그대로 따라올 수
있도록 정리한 문서다. Python 클래스 구조를 옮기지 말고, 아래의 **와이어 계약과
로컬 보존 규칙**만 Swift로 옮긴다.

## 계약 pin — 빌드에 그대로 넣는다

```yaml
contract_version:          "0.2.0"
contract_git_commit:       fcd99b7098b9a04bd93c585d89b16588aa482530
contract_content_commit:   7bcb5d25c5376b02469666df7318b90b456ffee6
canonical_contract_sha256: 416c1b99edb9bda694731dee4b25688d9d82d1f32610aa23ddfda571ec3c7670
canonical_contract_bytes:  23256
canonicalization:          RFC 8785
sync_protocol_version:     3
storage_name_algorithm:    storage-name-v1
storage_name_unicode:      15.0.0
```

세 값(`contract_version`, `contract_git_commit`, `canonical_contract_sha256`)은
`sync-contract/contract-lock.json`과 반드시 일치해야 한다. Windows 구현은
`sync_contract.py` 상단에 상수로 고정해두었다.

## 지금 벌어지고 있는 일

iPad는 아직 계약 이전 경로로 서버와 통신한다.

| | Windows (계약 구현 후) | iPad (현재) |
|---|---|---|
| 구조 변경 | `atomic_structure_commit` | `commit_folder` |
| 문서 저장 | `document_commit` (CONTRACT_BATCH) / `commit_document` (LEGACY) | `commit_document` |
| batch 메타데이터 | 8개 필드 전송 | 전송 없음 |
| 계약 다이제스트 | 전송·검증 | 없음 |

계약의 `minimum_write_protocol`이 `LEGACY`에 대해 `1`이므로 **지금은 iPad 쓰기가
거부되지 않는다.** 두 클라이언트가 같은 프로젝트를 서로 다른 규약으로 쓰고 있을
뿐이다. 프로젝트가 `MIGRATING` 또는 `ID_BASED`로 승격되는 순간 최소 protocol이
`3`이 되어 현재 iPad 빌드는 거부된다.

```
LEGACY    content 1 / structure 1     ← 구형 클라이언트 허용 (현재 상태)
MIGRATING content 3 / structure 3
ID_BASED  content 3 / structure 3
```

## 1. immutable batch 메타데이터

protocol 3 요청은 batch 하나에 아래 8개 필드를 싣고, 생성 후 변경하지 않는다.

```
batch_id                  uuid
writer_device_id          uuid
client_build_id           string
sync_protocol_version     3
contract_version          "0.2.0"
canonical_contract_sha256 소문자 64자 hex
client_capabilities       capability 집합
batch_payload_sha256      ordered_intents 의 canonical JSON SHA-256
```

`CONTRACT_BATCH` operation은 정확히 하나의 기존 batch를 참조해야 한다.

### client capabilities

```
folders_authoritative
tree_order_ids
tombstones
immutable_batch_contract_metadata
operation_attempt_history
operation_state_events
storage_name_v1
document_commit_v1
```

### 요구하는 server capabilities

서버 응답의 capability 집합이 아래를 **모두 포함하지 않으면** `CAPABILITY_MISMATCH`로
실패시킨다.

```
atomic_structure_commit
contract_allowlist_validation
project_mode_migration_lock
folder_tombstones
id_tree_validation
legacy_epoch_zero_adapter
storage_name_v1
document_commit_v1
```

## 2. 서버 호환성 사전 검사 — fail closed

쓰기 전에 반드시 통과시킨다. Windows는 `require_server_compatibility()`다.

```
project_sync_mode/migration_epoch 조합이 유효한가
  LEGACY 는 epoch == 0
  MIGRATING·ID_BASED 는 epoch >= 1
  아니면 STALE_MIGRATION_EPOCH

server_protocol_version >= 3            아니면 PROTOCOL_TOO_OLD
server_contract_sha256 == 위 pin        아니면 CONTRACT_DIGEST_MISMATCH
server_capabilities ⊇ 위 목록            아니면 CAPABILITY_MISMATCH
```

하나라도 어긋나면 **쓰지 않는다.** 추측해서 진행하지 않는다.

## 3. document_commit 요청

```
kind:              "document_commit_request"
project_id:        uuid
project_sync_mode: LEGACY | MIGRATING | ID_BASED
migration_epoch:   int
batch:             위 8개 필드
ordered_intents:   [intent] (문서 커밋은 1개)
```

intent:

```
sequence:        1
operation_id:    uuid
batch_id:        uuid
entity_kind:     "document"
document_id:     uuid
intent_kind:     create | update | delete | restore
base_revision:   int
payload_sha256:  payload 의 canonical JSON SHA-256
payload:
  parent_folder_id:    uuid | null
  name:                storage-name 정규화 통과값
  content:             string (UTF-8 10,485,760 바이트 이하)
  content_sha256:      본문 UTF-8 SHA-256
  content_byte_count:  int
  is_deleted:          bool
  structure_revision:  int >= 1
supersedes_operation_id: uuid (선택)
```

거부해야 하는 조합:

```
create 인데 base_revision != 0
create 가 아닌데 base_revision < 1
(intent_kind == "delete") != is_deleted
structure_revision < 1
name 이 storage-name 규칙 위반
```

## 4. atomic_structure_commit 요청

```
kind:              "atomic_structure_commit_request"
project_id:        uuid
project_sync_mode / migration_epoch
batch:             위 8개 필드
ordered_intents:   [intent, ...]  순서가 계약이다
```

intent 는 `sequence` 가 1부터 연속이고, `entity_kind` 는

```
project | folder | document | tree_order | trash_purge
```

`intent_kind` 는

```
ensure | create | update | rename | move | delete | restore | reorder | migrate
```

**빠른 연속 이름 변경은 여기로 간다.** 6개를 하나의 ordered batch로 원자 커밋하며,
부분 적용이 없다. 이것이 미해결 incident(마지막 3개가 대기에 남음)와 직접 관련된
경로다.

## 5. 응답 검증

성공 응답은 키 집합이 정확히 일치해야 한다.

```
kind, batch_id, batch_payload_sha256, status, applied, results
```

- `batch_id` 가 요청과 다르면 `INVALID_ATOMIC_RESPONSE`
- `batch_payload_sha256` 가 요청과 다르면 `INVALID_ATOMIC_RESPONSE`
- `status` 는 `committed` 또는 `replayed`
- `applied` 는 `true`
- revision 이 없으면 **성공으로 처리하지 않는다**

`replayed` 는 정상 성공으로 수렴시킨다. 같은 `operation_id` 재전송의 멱등 응답이다.

## 6. operation 상태는 이벤트에서 파생한다

상태 컬럼을 직접 쓰지 말고 append-only 이벤트에서 파생한다. Windows는 이 규칙을
어긴 곳에서 버그가 났다(완료된 작업의 `status` 가 `pending` 으로 남아 대기 건수가
틀리게 표시됨).

```
enqueued          -> pending
dispatch_started  -> inflight
retry_scheduled   -> retry_wait
blocked           -> blocked
conflict_detected -> conflict
committed         -> completed
replayed          -> completed
cancel_requested  -> cancelled
superseded        -> cancelled
```

terminal: `completed`, `cancelled`

### Windows가 실제로 밟은 지뢰

iPad 구현에서 같은 실수를 피하도록 남긴다.

1. **연쇄 편집의 고아화.** 앞선 operation을 기다리는 후속 작업을 "base_revision
   없음"으로 큐에 넣었는데, 선행 작업이 성공 경로를 타지 못하면(취소·superseded·
   강제 종료) 후속 작업이 영구히 발송 대상에서 빠졌다. 큐가 통째로 멈추는데
   화면에는 대기 건수만 보였다. → 선행 작업이 살아있지 않은 대기 작업은 문서의
   현재 revision으로 재발행하는 복구 경로가 필요하다.
2. **낡은 오류가 현재 상태로 표시됨.** 최신 오류를 찾을 때 이미 끝난 작업의 과거
   오류까지 훑으면, 성공 이벤트에는 오류 코드가 없으므로 옛 오류가 영원히 이긴다.
   → 활성 상태인 작업의 오류만 본다.
3. **건수 집계에 완료분 포함.** 대기 건수가 늘기만 하고 줄지 않는다.
   → 활성 상태 작업의 문서만 센다.

## 7. storage-name 정규화

- 알고리즘 `storage-name-v1`, **Unicode 15.0.0 고정**
- 런타임 Unicode 버전이 다르면 `UNICODE_VERSION_MISMATCH`로 실패
- `/`, `\`, 제어문자(≤31), DEL(127) 거부
- Windows 예약 이름(`con`, `prn`, `aux`, `nul`, `com1~9`, `lpt1~9`) 거부
- 충돌 키는 정규화 결과로 판정한다. iPad의 NFD 입력과 Windows의 NFC가 같은 키로
  수렴해야 한다. `sync-contract/storage-name-vectors.schema.json`과 벡터 15개로
  검증한다.

## 8. project_sync_mode 전이

```
LEGACY -> MIGRATING -> ID_BASED     단방향
자동 승격 없음, 자동 강등 없음
initial: LEGACY / epoch 0
MIGRATING 중에는 project_id 범위 서버 잠금
동시 writer는 MIGRATION_LOCKED 로 거부
```

**클라이언트가 임의로 승격시키지 않는다.** 현재 운영 프로젝트는 전부 `LEGACY/0`
이며 이것은 의도된 상태다. 승격은 명시적 작업이다.

## 9. 표준 오류 코드

```
AUTH_REQUIRED           FORBIDDEN              INVALID_ARGUMENT
DOCUMENT_NOT_FOUND      DOCUMENT_ALREADY_EXISTS
REVISION_CONFLICT       OPERATION_ID_REUSED
LEASE_REQUIRED          LEASE_CONFLICT         LEASE_EXPIRED
PATH_CONFLICT           FOLDER_NOT_FOUND
PROTOCOL_TOO_OLD        CONTRACT_DIGEST_MISMATCH
CAPABILITY_MISMATCH     STALE_MIGRATION_EPOCH
MIGRATION_LOCKED        OPERATION_TERMINAL
```

번역된 문구가 아니라 이 코드로 분기한다.

## 10. 상태 전이 테스트 벡터 12개

`sync-contract/test_vectors/` 에 있다. iPad와 Windows가 **같은 벡터로 같은 결과**를
내야 한다.

```
01-empty-folder-create
02-folder-with-document-create
03-legacy-first-connect
04-rapid-six-renames                 ← 미해결 incident 와 같은 시나리오
05-revision-conflict-rebase
06-response-loss-idempotent-retry
07-restart-queue-recovery
08-rename-delete-conflict
09-same-name-different-folder-ids
10-legacy-structure-write-to-id-based
11-cancellation-event-derivation
12-atomic-structure-rollback
```

## 11. Windows 참조 파일

계약 경로만 보면 된다. 나머지는 Windows UI 사정이다.

```
sync_contract.py     계약 상수, 요청 빌더, 응답 검증, storage-name  ← 핵심
sync_v2_store.py     operation 큐, 이벤트 파생 상태, 복구
sync_manager.py      dispatcher, 재시도, lease, pull
three_way_merge.py   3방향 병합 (11단계)
tests/test_sync_contract_stage8.py   계약 테스트 42건  ← 기대값 참고
```

## 12. 완료 판정

- 세 pin 값이 빌드에 포함되고 `contract-lock.json` 과 일치한다
- batch 메타데이터 8개 필드를 모든 protocol 3 요청에 싣는다
- 서버 호환성 검사가 fail-closed 로 동작한다
- `atomic_structure_commit` 으로 빠른 연속 구조 변경을 원자 커밋한다
- operation 상태를 append-only 이벤트에서 파생한다
- 응답 검증이 batch digest 와 키 집합을 확인한다
- storage-name 벡터 15개 통과
- 상태 전이 벡터 12개가 Windows 결과와 일치한다
