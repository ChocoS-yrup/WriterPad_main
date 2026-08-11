# Stage 8 Windows UI structure integration

## Pin and branch strategy

- Repository: `ChocoS-yrup/WriterPad_main`
- Contract stacked base: `codex/stage-8-windows-contract-implementation`
- Exact base commit: `5b8f0b2b2867fe5340e98d0d4c5b460e1c442219`
- Integration branch: `codex/stage-8-windows-ui-structure-integration`
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
- SQLite additive schema version `8003`, including local tree-order projection.

Overlapping `sync_manager.py`, `sync_v2_store.py`, `tests/test_sync_state.py`
and `tests/test_sync_v2.py` changes were reconstructed at function/hunk level
over the exact PR #1 head. No local commit was cherry-picked, grafted, merged or
rebased.

## Provenance and exclusions

- Source manifest: `docs/stage-8-source-reconstruction-manifest.json`
- Source entries: `35`
- Source manifest SHA-256: `171a534c6b81de600a39ecaaccea85037d46141a533b9062a0e7309233647657`
- All included files passed UTF-8 text, forbidden-path, binary, 1 MiB and secret-rule gates.
- Excluded: local 50-commit history, `.env`, historic secret material,
  `saves/project_data.json`, real work content, DB/WAL/SHM, EXE/DLL,
  build/dist/cache, unsafe provenance paths and server migration reference SQL.

The original dirty worktree remained unchanged:

- HEAD: `77c57b8d39ce726f6aad0a565a0397b7a5a98fc3`
- Status SHA-256: `3813c67544e1ca7cc955e7025c89284ba34193020d90b76653d8c68f4fd381dd`
- NUL-delimited status SHA-256: `129c1dcfadc4c37dc57f1c857bb29527e6fbfef1c536f2487af626bd51a74ae8`
- Status records: `280`

## Validation

- Baseline at PR #1 exact head: `126 passed, 9 skipped`.
- Final Windows Python regression: `365 passed, 1 skipped`.
- One long-run backup-coalescing test failed once during the first full run,
  passed alone immediately, and the complete suite then passed on rerun.
- Contract verifier: 7 schemas, contract `0.2.0`, 12 transition vectors,
  15 Unicode 15.0 storage-name vectors, 4 atomic wire cases and 7 document wire cases passed.
- Canonical bytes: `23256`.
- PyInstaller `6.21.0` Windows build: passed.
- Local EXE SHA-256: `f9ecf761277598219d557c96985fad7cce94054622e8cae504e796b6a8732b89`.
- Local EXE size: `78,580,326` bytes; `build/` and `dist/` remained ignored and are not PR content.
- `git diff --check`: passed.
- Staging/production access or writes: `none`.
- Contract allowlist changes: `none`.
- Existing project promotion: `none`.

## Remaining gates

- The stacked pull request must remain Draft.
- PR #1 must not be rebased, updated, readied or merged by this work.
- Staging client connection and allowlist activation require separate approval.
- Stage 9 must not begin until Stage 8 review, merge and approved staging validation complete.
