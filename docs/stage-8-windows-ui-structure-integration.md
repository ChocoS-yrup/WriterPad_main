# Stage 8 Windows UI structure integration

## Pin and branch strategy

- Repository: `ChocoS-yrup/WriterPad_main`
- Contract stacked base: `codex/stage-8-windows-contract-implementation`
- Original reconstruction base: `5b8f0b2b2867fe5340e98d0d4c5b460e1c442219`
- Published PR #1 mode/epoch correction: `b81051c85d8bf736a1830c1c320f05847ec20376`
- Local stacked-base merge commit: `d70aa3a054bb6a8edc451f37e748ecfe2904de9f`
- Exact source snapshot commit: `ec0d0181526f0aaede6c65459fc1cd61595ca8fd`
- Local corrective worktree branch: `codex/stage-8-windows-ui-structure-fixes`
- Published Draft PR branch: `codex/stage-8-windows-ui-structure-integration`
- Contract version: `0.2.0`
- Canonical contract SHA-256: `416c1b99edb9bda694731dee4b25688d9d82d1f32610aa23ddfda571ec3c7670`
- Local 50-commit history reused: `false`

The merge-candidate commit is the commit containing this handoff; resolve it
with `git rev-parse HEAD` after checkout. The pull request must remain Draft and
must target the Contract implementation branch above, not `main`.

## Reconstructed scope

- Stable folder identity, root/nested folders, rename, move and normalized-name handling.
- Project trash/purge and server-project import UI paths.
- Binder/manuscript ordering plus pull and commit barriers.
- Immutable document and structure intents with append-only attempts/events.
- Protocol-3 atomic folder/document rename/move and tree-order batches.
- Contract tree-order pull projection from UUIDs to Windows UI paths.
- Volume creation ordering: folder batch, 25 durable document commits, then order barrier.
- Diagnostics, reconnect/replay and long-run resource coverage.
- SQLite additive schema version `8004`, including mode/epoch constraints,
  local tree-order projection and append-only structure-recovery evidence.

Overlapping `sync_manager.py`, `sync_v2_store.py`, `tests/test_sync_state.py`
and `tests/test_sync_v2.py` changes were reconstructed at function/hunk level
over the exact PR #1 head. No local commit was cherry-picked, grafted, merged or
rebased.

## Provenance and exclusions

- Source manifest: `docs/stage-8-source-reconstruction-manifest.json`
- Source entries: `39`
- Source entry snapshot head: `ec0d0181526f0aaede6c65459fc1cd61595ca8fd`
- Source entries digest SHA-256: `64dec2ee55e2e3656e4ee66dfd8f8e86b002d7b66d3ddfffb12a366930d38573`
- Digest rule: sort entries by path, then SHA-256 the concatenated UTF-8
  records `<path>\\0<decimal byte_count>\\0<lowercase output_sha256>\\n`.
  `docs/*` and the manifest itself are excluded from that entry-set digest.
- Each entry's byte count and output hash use the committed Git blob returned
  by `git show <source_entry_snapshot_head>:<path>`. Checkout-specific CRLF
  conversion is never part of the hash, so Windows and macOS reproduce the
  same values.
- `test_flags.py` and `test_rename.py` are included as reviewed UTF-8 manual
  probes. Both are guarded by `__main__` and cannot mutate Qt state during
  unittest discovery.
- The manifest file's own SHA-256 is recorded below after the final manifest
  bytes are fixed; it is intentionally not self-embedded.
- Canonical LF/UTF-8 manifest file: `19,285` bytes, SHA-256
  `0b3b65de2df2314a8e53b74272c2eee4f87ac7a0be043ebf7273146cbe047d0e`.
- All included files passed UTF-8 text, forbidden-path, binary, 1 MiB and secret-rule gates.
- Excluded: local 50-commit history, `.env`, historic secret material,
  `saves/project_data.json`, real work content, DB/WAL/SHM, EXE/DLL,
  build/dist/cache, unsafe provenance paths and server migration reference SQL.

The original dirty worktree's source, tracked diff and pre-existing untracked
content were not modified. The authorized clean corrective worktree adds one
top-level status record while it remains mounted:

- HEAD: `77c57b8d39ce726f6aad0a565a0397b7a5a98fc3`
- Before status SHA-256: `3813c67544e1ca7cc955e7025c89284ba34193020d90b76653d8c68f4fd381dd`
- Before NUL-delimited status SHA-256: `129c1dcfadc4c37dc57f1c857bb29527e6fbfef1c536f2487af626bd51a74ae8`
- Before status records: `280`
- After status SHA-256: `0ce9f83748faddade7b4275714ab1026fa34242229e6483b051434474d2db5cd`
- After NUL-delimited status SHA-256: `c53b6426674b61ece7d21fbe1c2affb7cb5a32d3896fa2db468f8458cbec6ebf`
- After status records: `281`
- The sole added record is `_stage8_ui_fixes/`, the clean linked worktree used
  for this corrective branch. Its relocation was not forced after Windows
  reported the directory in use; no partial move or cleanup was attempted.
- Unstaged tracked binary-diff SHA-256 remained
  `4adeb539208a1d94edc01448321f9da9b098bfbffb9bc4ffcceac3384d73442a`;
  staged tracked diff remained empty (`e3b0c442...`).

## Corrective review findings

- Compatible Contract `0.2.0` projects may use protocol 3 while remaining
  exactly `LEGACY/0`; no automatic mode promotion occurs. Incompatible or
  unverified server metadata keeps the legacy adapter or fails closed.
- Drag/drop, rename, move, folder create/delete/restore and tree-order changes
  now produce one ordered atomic structure batch instead of discarding path
  intents or committing local SQLite projections first.
- Batch insertion and local folder/document/order projections share one SQLite
  transaction. Filesystem-first failures roll back filesystem and SQLite state;
  rollback failure creates append-only durable structure-recovery evidence.
- Folder delete/restore carries document path projections and immutable
  document operations as an uncommitted plan until the ordered lifecycle/order
  batch is created. Failure after the first document operation rolls back every
  document path, operation, structure batch and folder snapshot together.
- Delete, restore and their filesystem compensation paths are serialized by
  `local_structure_mutation()`; compensation failure appends durable recovery
  evidence instead of silently claiming success.
- A predecessor can be superseded only once within the combined operation set;
  duplicate predecessor relations are rejected before persistence.
- Rename-only, move-only, combined rename+move, lifecycle, validation failure,
  batch insertion failure, snapshot failure and restart recovery paths have
  explicit regression coverage.

## Validation

- Baseline at PR #1 exact head: `126 passed, 9 skipped`.
- PR #1 corrective head `2a560c7`: `136 passed`; Windows CI run
  `31516134432` passed contract vectors, client tests and executable build.
- Final Windows Python regression: `380 passed, 1 conditional symlink skip`,
  twice consecutively without retries at exact source snapshot `ec0d018`
  (`66.885s`, then `67.523s`).
- Backup coalescing: ManualWorker and real QThread coverage passed `30/30`
  repeated rounds. The QThread test uses signals/events and bounded waits, not
  fixed sleeps.
- Concurrent same-UUID import passed `30/30` repeated rounds. Its event wait
  and worker join are bounded, and release/join now run in `finally` even when
  the runner is unusually slow.
- Exact conditional skip:
  `RemoteTreeOrderMaterializationTestCase.test_remote_tree_order_never_follows_symbolic_link`.
  Windows returned `WinError 1314` because this process lacks symlink creation
  privilege. The adjacent reparse-point/path-escape rejection test always ran
  and passed.
- The first corrective full runs exposed queued QThread cleanup timing and two
  import-time manual Qt scripts that created and quit `QApplication` during
  unittest discovery. Cleanup now has an explicit thread boundary, and the
  manual probes run only under `__main__`; the final two full runs above are
  from the resulting unchanged code.
- Initial stacked CI run `31520691616` passed the Python 3.12 contract verifier
  but exposed the concurrent-import fixture's old 5-second pre-cleanup
  assertion on a slow hosted disk. One functional assertion and the temp-dir
  cleanup error had the same cause. The fixture now uses a 30-second bounded
  event timeout and unconditional release/join; production import code was not
  changed for this CI-only finding.
- Initial PR CI run `31487006626` exposed a Windows unittest fixture issue:
  a process-lifetime `QCoreApplication` could precede widget tests and cause a
  silent exit code 1. A module-held offscreen `QApplication` fixture now keeps
  the Qt application type and lifetime deterministic. The exact failing test
  order and the complete suite passed locally after the correction.
- Follow-up CI run `31488020552` confirmed two remaining headless-only test
  harness dependencies: a real `QTimer` in a queue-failure unit test and a
  focused `QLineEdit`/`QTest` interaction in a durable rename regression. They
  now test the same state transitions with a fake timer and direct durable
  handler. The autosave coalescing resource test also uses manual signals so
  worker cleanup and latest-follow-up semantics are deterministic.
- Contract verifier: PR #1 CI passed 7 schemas, contract `0.2.0`, 12 transition
  vectors, 15 Unicode 15.0 storage-name vectors, 4 atomic wire cases and 7
  document wire cases. The local Python 3.11 runtime correctly refused this
  verifier because it exposes Unicode 14.0; PR CI uses pinned Python 3.12.
- Canonical bytes: `23256`.
- PyInstaller `6.21.0` Windows build: passed.
- Exact-source local EXE SHA-256:
  `dca6130240809723624ff3ddba6c7bdcf390a796fb0502b546d3636990875b0c`.
- Exact-source local EXE size: `78,591,142` bytes; `build/` and `dist/` remained
  ignored and are not PR content.
- `git diff --check`: passed.
- PR #1 exact head `b81051c` passed workflow-dispatch CI run `31525762343`:
  129 tests, contract digest verification and executable packaging passed;
  CI EXE was `55,358,115` bytes with SHA-256
  `e9f9f0ab90fd69140915a0c4a4b58e6c7ca1d7506e3423af485bc8a5f3c2e4b4`.
- PR #2 evidence head `05e808a` passed pull-request CI run `31526070833`:
  380 tests, contract digest verification and executable packaging passed;
  CI EXE was `55,551,763` bytes with SHA-256
  `a7e0891bfbd864473ae1f2c422c1a2b2e507fcdc0b83a39036ab9fe8060fdb0c`.
- The final metadata-only handoff commit's exact-head CI URL is recorded in the
  Draft PR body after completion, avoiding a self-referential evidence commit.
- Staging/production access or writes: `none`.
- Contract allowlist changes: `none`.
- Existing project promotion: `none`.

## Remaining gates

- The stacked pull request must remain Draft.
- Both Draft PR branches are published and CI-validated; neither is authorized
  for Ready conversion or merge by this handoff update.
- PR #1 must not be rebased, updated, readied or merged by this work.
- Staging client connection and allowlist activation require separate approval.
- Stage 9 must not begin until Stage 8 review, merge and approved staging validation complete.
