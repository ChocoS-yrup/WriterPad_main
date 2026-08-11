# stage-8-windows-contract-implementation

## Status

- Platform: Windows 10, Python 3.11.9, PyInstaller 6.21.0
- Source baseline: `origin/main` at `539cbd39074475b59cbd729923fbc2bc5ee5a7f9`
- Branch: `codex/stage-8-windows-contract-implementation`
- Implementation commit: `0bcc3f1f55621b58519cbc8f2356178fdb5c1c3d`
- Review status: draft; staging evidence and the folder-identity integration gap below remain open
- Production Supabase writes: none
- Preserved user database writes: none

## Contract and server pin

```yaml
contract_version: 0.1.0
contract_git_commit: 45d18cff62cc48e29d0e6efcfc634fec96150198
contract_content_commit: 7f05f32dd385ce0e1922b88d688742fca2a503fa
canonical_contract_bytes: 19473
canonical_contract_sha256: fae86b4e6385ee37fbeb99f9256194ec319b64bfda92974ce90a3eb70d2e7a46
server_source_commit: 3111faa589a302404aa57ae88b9eee347a961dc8
server_migration_ids:
  - 20260811010000
  - 20260811020000
staging_project_id: UNVERIFIED_NOT_PROVIDED
staging_endpoint: UNVERIFIED_NOT_PROVIDED
```

The Windows client pins protocol 3 and the released digest for every contract batch. A contract-native write is rejected before dispatch when the protocol, digest, project mode, migration epoch, or required server capabilities do not match. An explicit server-proven activation call is required; opening or migrating SQLite never promotes a project automatically.

## Implemented Windows client behavior

- Additive SQLite migration to `PRAGMA user_version = 8001`.
- Existing projects and operations import as `LEGACY`, epoch `0`, protocol `2`, and `LEGACY_EPOCH_0` without invented contract digest, batch ID, or attempt rows.
- Operation intent fields are immutable after creation. SQLite triggers also reject operation deletion and update/delete of batches, events, attempts, and results.
- State and cancellation are derived from append-only events. Dispatch outcomes are append-only attempts. Interrupted dispatch recovers as `transport_unknown` plus `retry_scheduled` using the same operation ID.
- Rebase and dependent revision promotion create a new operation and preserve `supersedes_operation_id`; the original intent is never overwritten.
- Atomic ordered structure requests use the Stage 7 `atomic_structure_commit(p_request)` boundary. Batch attempt transitions and response recording are local transactions.
- Success is accepted only when every ordered result matches its sequence, operation ID, entity ID, and revision. Partial or mismatched responses roll back the local response transaction and leave all operations uncommitted.
- Identical batch replay returns the stored result. Reusing a batch ID with different request or response bytes is rejected.
- Unicode 15.0.0 `NFKC -> default casefold -> NFKC` storage-name validation is pinned through `unicodedata2==15.0.0`, including trailing ASCII space/dot removal and Windows reserved-name rejection.
- Diagnostics retain only allowlisted IDs, digests, states, protocol metadata, and error codes. Document bodies, tokens, passwords, endpoints, and arbitrary URLs are discarded.
- Legacy document sync remains on the protocol-2 `commit_document` adapter. Contract-mode document writes fail closed as `CONTRACT_DOCUMENT_RPC_UNAVAILABLE` because the Stage 7 server handoff does not provide a protocol-3 document commit RPC.
- Name-based hidden tree-order writes are allowed only for `LEGACY/epoch 0`. A migrated project must supply stable IDs through the atomic structure API; otherwise it fails closed as `CONTRACT_STRUCTURE_IDS_REQUIRED`.

## Preserved database: directly observed facts

The original was inspected using SQLite URI `mode=ro&immutable=1`. Migration was run only against a separate temporary copy.

```yaml
original_path: D:\안티그래비티\scratch\작가님 힘내세요\evidence\windows-99516096-20260810-055633\sync_v2.sqlite3
original_bytes: 9273344
original_sha256_before: 512e131e038f51dc3c4b0ae281cfef8b2ccd82a57ffa4f4bec2390bdbde5ba77
original_sha256_after: 512e131e038f51dc3c4b0ae281cfef8b2ccd82a57ffa4f4bec2390bdbde5ba77
original_user_version: 0
original_integrity_check: ok
projects: 9
documents: 574
operations: 218
operation_statuses:
  completed: 209
  pending: 9
```

The preserved database also contains `sync_folders`, `sync_tree_barriers`, `sync_folder_rename_intents`, and `sync_project_imports`. These tables are preserved by the additive migration.

On the disposable copy, the migration was run twice:

```yaml
migrated_user_version: 8001
migrated_integrity_check: ok
legacy_epoch_zero_projects: 9
legacy_epoch_zero_operations: 218
operation_events: 427
invented_attempt_rows: 0
```

The 427 imported events are the 218 enqueue snapshots plus 209 completion snapshots. Historical mutable `attempts` counts remain in `legacy_attempt_count`; they are not rewritten into fabricated append-only attempt records.

## Source-code inferences, kept separate from database facts

- The exact clean `origin/main` baseline does not contain the folder-identity and commit-barrier implementation represented by the extra preserved tables.
- The original dirty Windows workspace contains uncommitted folder identity, root-folder, order, empty-folder rename, pull-batch, and barrier work. Those user-owned edits were inspected read-only and were not copied, reset, cleaned, or committed into this branch.
- Consequently, this branch preserves legacy path/document behavior and provides the contract-native atomic structure boundary, but it does not yet translate the current UI's rename/move/order actions into stable-ID batches. Migrated projects fail closed instead of falling back to name-based writes.
- Folder identity, root folder, volume/manuscript order, empty folder, remote rename, pull, delete/restore, and trash behavior covered by the clean baseline tests still pass. This is not equivalent to a staging round trip of the uncommitted folder-ID implementation.

## Verification evidence

Official released-contract verifier, Python 3.12.13 / Unicode 15.0.0:

```text
Validated 6 JSON schemas.
Validated released protocol contract 0.1.0.
Validated 12 transition vectors with cross-file semantics.
Validated 15 storage-name conformance vectors (Unicode 15.0.0).
Validated 4 atomic wire conformance cases.
Canonical protocol bytes: 19473
Canonical SHA-256: fae86b4e6385ee37fbeb99f9256194ec319b64bfda92974ce90a3eb70d2e7a46
```

Windows test suite:

```text
python -m unittest discover -s tests -v
Ran 119 tests
OK
```

This includes 19 Stage 8 tests for exact pinning, all released vectors, fail-closed compatibility, legacy/new database migration, immutable intent, event-derived restart, idempotent cancellation, structure rebase, exact Supabase `p_request`, complete commit/replay, partial-response refusal, and secret-free diagnostics.

Windows executable:

```yaml
path: dist/Antigravity_AI_Writer.exe
bytes: 78393726
sha256: 52af8e2fa002b2b6d1acec6be36c22c7c14bcb0a62668f115c6f8f1f5717e454
smoke_test: process remained running for 5 seconds in a disposable directory, then was stopped
```

CI definition `.github/workflows/windows-contract.yml` verifies the exact PR head, checks out contract content commit `7f05f32d...`, runs the official verifier on Python 3.12.13, runs Windows tests on Python 3.11.9, builds the executable, and prints its digest.

## Not verified / required follow-up

1. Provide the Stage 7 staging endpoint and staging project ID, and confirm the two migration IDs are present in the staging migration ledger.
2. With explicit staging write authorization, exercise atomic commit, response-loss replay, rollback, concurrent rename/move/order, cancellation races, server restart, mixed legacy/new clients, and normalization collisions against the deployed RPC.
3. Reconcile or separately publish the user-owned folder-identity/barrier work, then wire Windows rename/move/order actions to `queue_atomic_structure_batch` with stable folder/document IDs.
4. Add or expose a Stage 7 protocol-3 document commit RPC before promoting projects that need content writes. Until then, those writes intentionally remain blocked.
5. Keep the PR in draft until the staging evidence and folder-ID integration are complete. Do not manually promote a legacy project merely because SQLite or server migrations exist.

No production project was promoted, no staging or production row was changed, and no secret value or manuscript body is present in this handoff.
