# Windows compatibility with Writerpad PR #50

> Historical report for the main-based PR #10 only. The Python 3.14 integration
> supersedes its runtime/base assumptions; see
> [Python 3.14 integration](python314-pr50-integration-2026-09-28.md).
> The 3.11 results below are not evidence for the integrated 0.3 client.

## Reviewed baselines

- Windows main: `8ac1685359d03be245a44b0a808a486c877cea16`.
- Independent branch: `codex/windows-pr50-compat-fixes`.
- Windows PR #9: `0244d8263cadea893e11c1ee2e7b5dad9c0f4be4` (open draft).
- Windows PR #8: `2ecb21b18601e67db7e2e78733e1e9291ddae2f1` (open draft).
- Server/iPad PR #50 base: `49585a509982b7564ace3a6a313bbea46f1f37ee`.
- Server/iPad PR #50 head: `3c2c119703e4d711e821d01c6d4a12181804474f`.

The heads were queried from GitHub before implementation. The PR and final
handoff record the published Windows head and its exact-head CI evidence.
No existing PR branch is used as the implementation base or modified.

## Equal-body-revision initialization

`SyncManager` fetches project ID and the server bytea storage key with document
snapshots. Before either equal-revision return, it calls the separate
`SyncV2Store.apply_equal_revision_structure` boundary. This does not relax
`apply_remote_snapshot` or the outbound missing-structure-revision guard.

Inside one SQLite transaction, the new boundary requires the same project and
document identity, positive equal body revision, identical baseline content,
unchanged deletion state and server path, complete structure proof, matching
parent identity/path and canonical name/storage key. A previously initialized
structure must keep its parent/name/key and may only advance its revision.
Equal structure revisions are no-ops; lower revisions and partial/contradictory
proofs are rejected. Parent IDs come from the server-revisioned folder projection.

Only `parent_folder_id`, `name`, `storage_name_key`, and `structure_revision` are
updated. Document/project IDs, paths, content/hash, body revision, deletion state,
timestamps, conflict data, operation/version/tombstone history are not written
by this boundary. No server write or artificial body revision is involved.

Document queue states, active structure operations and pending ancestor rename
intents defer the update. Folder structure operations are conservatively deferred
for the project. The manager additionally respects protected editor paths,
unavailable protection information, reparse paths, and differing local file
content. A rejected proof blocks completion of the pull.

## Binder root ordering

The canonical mapping is the unique live, server-revisioned, top-level folder
whose name and physical path are both `메인`. It is resolved within the current
project. Missing, ambiguous, nested, unproven or contradictory candidates cannot
be used as the binder root.

- `parent_folder_id = MAIN_FOLDER_ID` maps to durable/UI `<root>`.
- Actual null-parent order is stored as `__server_root__` and omitted from the
  binder UI projection. It is never merged with binder `<root>`.
- Snapshot validation and successful reorder receipt recording share the parent
  mapping. Parent/child IDs must belong to the project; inbound children must be
  live and match both the parent ID and physical parent path.
- The manager validates order after receiving document metadata, so a transition
  document and its order can arrive in the same pull.
- Outbound `<root>` resolves the real main ID. Existing order ID/revision lookup
  uses parent identity, including old durable `메인` path aliases. It does not
  create an extra null-parent order.
- Duplicate/missing/deleted/foreign/cross-parent children, contradictory identity,
  and stale or contradictory equal-revision order snapshots fail closed before
  replacing durable/UI order. Replay preserves the existing durable timestamps.

`writing_tree.py` already reads and writes `<root>` and needs no product change.

## Local verification

Windows 10; disposable SQLite/project fixtures; Qt offscreen; Python 3.11.9 with
the unmodified `requirements-stage8.txt`. No installed app is launched.
The interpreter is in task-specific `scratch/pr50-test-env311`; canonical
contract files, pins and dependency declarations are unchanged.

Commands below use that interpreter with `PYTHONIOENCODING=utf-8`,
`PYTHONUTF8=1` and `QT_QPA_PLATFORM=offscreen`:

```text
python -X faulthandler -B -m unittest tests.test_sync_pr50_compat tests.test_sync_contract_stage8 -q
python -X faulthandler -B -m unittest tests.test_sync_v2 tests.test_sync_state tests.test_sync_resilience tests.test_sync_diagnostics tests.test_project_identity_v1 tests.test_folder_identity_migration -q
git diff --check
```

- Targeted/contract: 79 tests passed (32 new transition regressions, 47 existing).
- Related regression: 267 run, 266 passed, 1 skipped because the host cannot
  create the symbolic-link fixture. No failing test is counted as a pass.
- Final combined execution of these eight modules: 346 run, 345 passed,
  1 symbolic-link fixture skipped; exit code 0 (36.119 seconds).
- `git diff --check` passed.

Initial environment attempts are not successful verification: the pre-existing
Python 3.14 environment rejected Unicode 16 versus required Unicode 15; an
isolated Python 3.12 environment hit native stack overflow during Qt object
initialization. Python 3.11.9 matches this base's CI runtime and passed.

Three existing project-isolation tests initially failed on the unmodified base
as well: their class-level `supabase` mock was shadowed by the singleton's instance
attribute. The one-line fixture change patches the instance, allowing the mocked
pull worker to be constructed. It changes no product behavior or network access.
An initial command also named a test module absent from main; it was corrected
to the existing identity regression modules above.

## Existing PR overlap and boundaries

PR #9 still contains both original defects. Its inbound tree-order application
also adds materialization/identity rollback and a structure lock. When rebasing
#9, preserve those additions while integrating the metadata boundary, the delayed
validated order replacement, and canonical parent mapping/receipt handling.
The shared `sync_manager.py` and `sync_v2_store.py` regions and the expanded
`tests/test_sync_v2.py` may require conflict resolution. This PR does not depend
on #9. Its dedicated transition test module reduces test-file overlap.

PR #8 changes CI diagnostics, not these product paths; this PR leaves its workflow
and branch unchanged.

No preserved SQLite, live server/database, installed Windows app, iPad device,
cross-device E2E, migration execution, real-project transition or other repository
was modified or exercised. EP and other chats/agents were not used. Windows PR
merge, subsequent PR #50 final compatibility review, PR #50 merge, and database
migration remain separate approval gates. CI success is not a merge or complete
Windows compatibility approval.
