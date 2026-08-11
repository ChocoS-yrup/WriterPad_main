# stage-8-windows-contract-implementation

## Status

- Platform: Windows 10, Python 3.11.9, PyInstaller 6.21.0
- Windows source baseline: `origin/main` at `539cbd39074475b59cbd729923fbc2bc5ee5a7f9`
- Baseline freshness: confirmed by `git fetch origin main` on 2026-08-11
- Branch: `codex/stage-8-windows-contract-implementation`
- Contract 0.2 initial implementation commit: `f0de952d180babf56798ce27e00fa2e455cbbd15`
- Mode/epoch corrective commit: `b81051c85d8bf736a1830c1c320f05847ec20376`
- Combined UI/structure merge head tested in staging: `7b68edceefdc60df244dadce0760c374cfc8ef01`
- Review status: Draft PR #1, `OPEN/MERGEABLE`; combined Windows CI and scoped staging E2E passed
- Production Supabase writes: none
- Staging Supabase writes during Stage 8: synthetic E2E fixtures only
- Preserved user database writes: none

## Contract and Stage 7 server pin

```yaml
stage_7_status: COMPLETE
server_repository: https://github.com/ChocoS-yrup/Writerpad
server_merge_sha: 20d60ea94da4cd2543db489ea240efa5db2f4091
contract_version: 0.2.0
contract_git_commit: fcd99b7098b9a04bd93c585d89b16588aa482530
contract_content_commit: 7bcb5d25c5376b02469666df7318b90b456ffee6
canonical_contract_bytes: 23256
canonical_contract_sha256: 416c1b99edb9bda694731dee4b25688d9d82d1f32610aa23ddfda571ec3c7670
staging_project_id: mhpnszcorfzrvhyondxr
staging_endpoint: https://mhpnszcorfzrvhyondxr.supabase.co
staging_migration_ids:
  - 20260811000000
  - 20260811010000
  - 20260811020000
staging_functional_validation: passed_with_retest
staging_fixture_project_id: 8aa1157b-811d-4909-825d-5bda0f0d8891
staging_allowlist_enabled: false
production_changes: none
```

Every protocol-3 batch pins the released version and canonical digest. Contract activation requires an explicit, server-proven project mode, epoch, protocol, digest, and capability set. Opening or migrating SQLite never promotes a project automatically.

## Implemented Windows client behavior

- Additive SQLite migration to `PRAGMA user_version = 8004`.
- Existing projects and operations import as `LEGACY`, epoch `0`, protocol `2`, and `LEGACY_EPOCH_0` without invented contract digest, batch ID, attempt, or folder identity.
- Operation intent fields are immutable after creation. SQLite triggers reject operation deletion and batch/event/attempt/result mutation.
- State, cancellation, dispatch, retry, commit, replay, conflict, and supersession are derived from append-only events and attempts.
- Interrupted dispatch recovers as `transport_unknown` plus `retry_scheduled` while preserving the operation and batch IDs.
- Rebase and dependent revision promotion create a new intent with `supersedes_operation_id`; the original intent is never overwritten.
- Protocol-3 document create/update/delete/restore and intentional empty content use the exact `document_commit(p_request)` wire format.
- A document response is accepted only when batch digest, sequence, operation ID, document ID, revision, structure revision, parent ID, name, content digest, byte count, and deletion state all match the immutable request.
- A partial or mismatched document response is rejected before local state changes. A response recorded before client termination is reused after restart without a second document apply.
- Atomic ordered structure requests use `atomic_structure_commit(p_request)`. Partial structure results do not change any local operation state.
- Identical committed/replayed responses are deterministic; changed payloads or incompatible results are rejected.
- Server-proven folder snapshots preserve stable IDs. A nested document without a known folder ID or structure revision fails closed instead of falling back to a path-only protocol-3 write.
- Unicode 15.0.0 `NFKC -> default casefold -> NFKC` normalization is pinned through `unicodedata2==15.0.0`. Invalid, reserved, and normalized sibling collisions are rejected before queueing.
- Diagnostics retain only allowlisted IDs, digests, states, protocol metadata, and error codes. Document bodies, tokens, passwords, endpoints, and arbitrary URLs are discarded.
- SQLite pair and `OLD -> NEW` transition triggers enforce only same-state
  updates, `LEGACY/0 -> MIGRATING/1`, and `MIGRATING/n -> ID_BASED/n`.
  Direct SQL epoch jumps, reverse transitions and demotions fail closed.
- A server-verified Contract `0.2.0` project may use protocol 3 while remaining
  `LEGACY/0`; protocol use never promotes mode or epoch. Unverified compatibility
  retains the protocol-2 adapter or fails closed.

## Preserved database: directly observed facts

The original was identified and hashed before any copy was opened. Migration was run only against a disposable copy.

```yaml
original_path: D:\안티그래비티\scratch\작가님 힘내세요\evidence\windows-99516096-20260810-055633\sync_v2.sqlite3
original_bytes: 9273344
original_sha256_before: 512e131e038f51dc3c4b0ae281cfef8b2ccd82a57ffa4f4bec2390bdbde5ba77
original_sha256_after: 512e131e038f51dc3c4b0ae281cfef8b2ccd82a57ffa4f4bec2390bdbde5ba77
original_user_version: 0
original_wal_present: false
original_shm_present: false
projects: 9
operations: 218
folder_snapshots: 40
```

Disposable-copy migration and second-run result:

```yaml
migrated_user_version: 8004
migrated_integrity_check: ok
legacy_epoch_zero_projects: 9
legacy_epoch_zero_operations: 218
contract_batch_count: 0
folder_snapshot_count: 40
foreign_key_failures: 0
second_migration_run: passed
final_copy_sha256: 917a5ec36b9871b6eb45b482a5c45137ad2147b6c8f673954fe61d84a2d80243
```

The final disposable-copy validation reached `8004`, passed integrity and
foreign-key checks, and produced no WAL or SHM residue. The original database
was not opened and its SHA-256 remained unchanged.

All nine projects remained `LEGACY/epoch 0`; all 218 historical operations became `LEGACY_EPOCH_0`. No contract batch or invented protocol-3 provenance was created. The preserved source file hash was unchanged after validation.

## Source-code inferences, separate from database facts

- The unsafe local 50-commit history and dirty workspace were not published.
  Reviewed text source was reconstructed as clean commits and merged through
  stacked PR #2 into this branch.
- The combined branch contains stable folder identity, root/nested lifecycle,
  document/folder moves, binder order, and pull/commit barrier integration.
- The real authenticated Windows source client completed the scoped staging E2E
  at exact head `7b68edce...`; evidence is in
  `docs/stage-8-windows-staging-e2e-2026-08-12.md`.

## Verification evidence

Official contract verifier, Python 3.12.13 / Unicode 15.0.0:

```text
Validated 7 JSON schemas.
Validated released protocol contract 0.2.0.
Validated 12 transition vectors with cross-file semantics.
Validated 15 storage-name conformance vectors (Unicode 15.0.0).
Validated 4 atomic wire conformance cases.
Validated 7 document wire conformance cases.
Canonical protocol bytes: 23256
Canonical SHA-256: 416c1b99edb9bda694731dee4b25688d9d82d1f32610aa23ddfda571ec3c7670
```

Combined Windows test suite:

```text
python -m unittest
Ran 380 tests
OK
```

The Stage 8 tests cover exact pins, all released vectors, protocol/capability/digest rejection, new and preserved DB migration, immutable intent, append-only recovery/cancellation, document create and intentional-empty commit, exact RPC payload, replay after response loss, partial-response refusal, normalized collision, server-proven folder identity, structure batch rollback, direct-SQL mode/epoch transition rejection and secret-free diagnostics.

Windows executable generated from the local implementation:

```yaml
source: combined exact-head Windows CI artifact
bytes: 55549410
sha256: a100118d0c94ec21ebea49dd1fdd1cae559f6dd1d5bdde01ef31518f4d5ec4aa
```

Windows CI evidence for combined head `7b68edceefdc60df244dadce0760c374cfc8ef01`:

```yaml
run: https://github.com/ChocoS-yrup/WriterPad_main/actions/runs/31528283650
result: success
tests: 380
ci_executable_bytes: 55549410
ci_executable_sha256: a100118d0c94ec21ebea49dd1fdd1cae559f6dd1d5bdde01ef31518f4d5ec4aa
```

The CI workflow checks out contract content commit `7bcb5d25...`, runs the official verifier on Python 3.12, runs the complete Windows test suite on Python 3.11.9, builds the distributable executable, and prints its digest. Local and CI executable digests differ because they are produced in different dependency environments; the pinned clean-runner CI digest is the release-candidate evidence.

## Remaining gates

1. Obtain final Stage 8 review before changing PR #1 from Draft or merging it to main.
2. After approved main merge, wait for exact post-merge Windows CI and record the
   release-candidate executable digest.
3. Treat official release packaging and publication as a separate approval.
4. Do not start Stage 9 until Stage 8 is declared complete and the merged Windows
   SHA, executable digest and this staging evidence are handed off.

No production project was accessed or changed. Only synthetic staging fixtures
were written; the successful fixture was explicitly promoted through the public
migration RPCs, while no existing project was promoted. The allowlist returned
to `enabled=false` with zero active rows. No secret or real manuscript body is
present in this handoff.
