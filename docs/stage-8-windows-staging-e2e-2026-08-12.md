# Stage 8 Windows staging E2E — 2026-08-12

## Verdict

```yaml
status: PASSED_WITH_RETEST
source_head: 7b68edceefdc60df244dadce0760c374cfc8ef01
staging_project_ref: mhpnszcorfzrvhyondxr
contract_version: 0.2.0
canonical_contract_sha256: 416c1b99edb9bda694731dee4b25688d9d82d1f32610aa23ddfda571ec3c7670
successful_fixture_project_id: 8aa1157b-811d-4909-825d-5bda0f0d8891
failed_fixture_project_id: 51b11ed4-42bb-4944-a7bb-6ed574d7ac53
owner_test_user_id: 294c2661-6a96-4909-9505-c08dd9b21b12
unauthorized_test_user_id: 7a484fab-070a-4411-83b3-37169edbf504
writer_device_id: 2a55cf05-629f-4f78-907d-447eb915c84a
allowlist_enabled_after_test: false
enabled_allowlist_rows_after_test: 0
production_changes: none
```

The two synthetic users were created through the WriterPad Staging Supabase
Dashboard Auth administration boundary with auto-confirm. Passwords, access
tokens, refresh tokens, API keys and email addresses are not recorded here or
in Git. The Windows client used the staging publishable key and authenticated
email/password sessions; no service-role key was used by the E2E process.

## Preconditions and Data API boundary

- The exact allowlist row was disabled before the test and no allowlist row was
  enabled.
- PostgreSQL was `17.6` and the six public contract/migration RPCs granted
  `EXECUTE` to `authenticated`.
- The current Supabase Data API default cannot be inferred from object presence.
  Actual authenticated calls through the Windows `supabase-py 2.31.0` client
  reached `ensure_project`, `atomic_structure_commit`, `document_commit`, the
  migration RPCs and `cancel_sync_operation`.
- The Windows Credential Manager session was deliberately excluded from the
  isolated test process so only the synthetic staging credentials were used.
- Contract `0.2.0` and only digest
  `416c1b99edb9bda694731dee4b25688d9d82d1f32610aa23ddfda571ec3c7670`
  were enabled during the bounded write interval.

## Passed scenarios

- Exact authenticated protocol-3 RPC callability with the new publishable key.
- Wrong digest, missing capability and protocol 2 requests rejected as
  `CONTRACT_DIGEST_MISMATCH`, `CAPABILITY_MISMATCH` and `PROTOCOL_TOO_OLD`.
- A protocol-3 atomic folder/tree-order commit while the project remained the
  implicit `LEGACY/0`; no settings or migration row was created automatically.
- Explicit `LEGACY/0 -> MIGRATING/1 -> ID_BASED/1` through the public migration
  RPCs, with validation `valid=true` and the exact contract digest pinned.
- Root and nested folder creation, rename, move, order, delete and restore.
- Document create, update, intentional empty body, delete, restore and a later
  response-loss update. Revisions advanced deterministically from 1 through 6.
- Document rename, move and destination tree order committed as one ordered
  atomic batch. Final structure revision is 3.
- A valid first rename followed by a move to a missing folder returned
  `FOLDER_NOT_FOUND`, `applied=false`; the document remained `Renamed.md` under
  the original destination at structure revision 3.
- Identical structure replay returned `replayed` without another apply.
- A document response was deliberately ignored; a new authenticated client
  session resent the identical request and received `replayed`. Only one new
  document version was committed.
- Cancellation returned `cancelled`; the identical event replay returned
  `already_cancelled`; cancellation of a completed operation returned
  `OPERATION_TERMINAL`.
- Unicode full-width `Ｃａｓｅ` followed by sibling `case` produced
  `PATH_CONFLICT`, `applied=false`.
- The unauthorized second user read zero project rows and `ensure_project` was
  rejected as `FORBIDDEN`.

## Read-only final audit

The final SQL audit ran in `transaction_read_only=on` and rolled back.

```yaml
mode: ID_BASED
epoch: 1
migration_completed: true
migration_validation_valid: true
folders: 5
documents: 1
document_versions: 6
tree_orders: 2
sync_batches: 17
sync_batch_results: 17
sync_operations: 22
sync_operation_attempts: 22
sync_operation_events: 67
operation_states:
  completed: 19
  blocked: 2
  cancelled: 1
attempt_outcomes:
  committed: 19
  blocked: 3
event_types:
  enqueued: 22
  dispatch_started: 22
  committed: 19
  blocked: 3
  cancel_requested: 1
final_document_revision: 6
final_document_structure_revision: 3
final_document_content_bytes: 13
allowlist_enabled: false
enabled_allowlist_rows: 0
```

## Failure and retest history

The first full fixture reached `ID_BASED/1` and correctly rejected its restore
request as `CONTENT_DIGEST_MISMATCH`. The test had deleted an intentionally
empty document and then incorrectly attempted to restore a previous non-empty
body. Contract `0.2.0` requires delete/restore to preserve the current content
digest. No server, migration or Windows implementation was changed. The test
input was corrected to restore the current empty body, all IDs were regenerated,
and the complete matrix passed. The failed fixture and append-only evidence were
left intact; there was no manual row repair or deletion.

Two earlier launch attempts were stopped before the E2E program made an app
write: one rejected plaintext process arguments, and one found that a normal
pipe could not retain the hidden-input channel. The final run used an echo-free,
one-use terminal input; credentials were held only in process memory.

## Final safety state

- Exact allowlist row: `enabled=false`.
- Enabled allowlist row count: `0`.
- Synthetic fixtures and append-only ledgers were preserved.
- No production project, production allowlist or production data was accessed
  or changed.
- PR #1 remained Draft and was not merged.
- No official release package was published and Stage 9 was not started.
