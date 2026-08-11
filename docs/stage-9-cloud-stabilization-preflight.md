# Stage 9 cloud stabilization preflight — 2026-08-12

## Verdict

```yaml
status: PREFLIGHT_PASSED
branch: codex/windows-post-stage8-stabilization
base_head: 221db4fa69e06210c0afad16870ccb25ef015094
contract_version: 0.2.0
canonical_contract_sha256: 416c1b99edb9bda694731dee4b25688d9d82d1f32610aa23ddfda571ec3c7670
unit_tests: 442 passed, 1 skipped
consecutive_full_runs: 5
isolated_module_runs: 27 modules, 0 failures
packaged_build: not produced in this stage
production_changes: none
staging_changes: none
```

No user database, manuscript, staging or production endpoint was contacted.
Network paths were exercised through mocks and forced-offline mode only.

## Findings and their fixes

| # | Diagnosed root cause | Commit | Regression tests |
|---|---|---|---|
| 1 | Packaged cloud client config bundled `.env.example` placeholders, so login resolved `your-project.supabase.co` and surfaced a raw DNS errno | `958b9bc` | `tests/test_cloud_config.py` (14) |
| 2 | `MainWindow.init_ui()` never read `startup_mode`, so assistant always won | `c571c66` | `tests/test_startup_mode.py` (10) |
| 3 | AI editors kept `typewriter_enabled = False` because the settings checkbox was `setChecked()` before its `toggled` connection | `8fb4920` | `tests/test_typewriter_mode.py` (8) |
| 4 | Shutdown had no upper bound: a fixed 8 s remote flush ran while periodic timers could still start workers, then `worker.wait()` blocked without a timeout | `1c297b1` | `tests/test_shutdown_budget.py` (29) |

## Shutdown budget

One deadline is opened by `SyncManager.begin_shutdown()` and shared by every
later step. Registered stoppers halt the writing controller timers, the 5 s
remote pull timer and the v2 retry timer before the nested flush event loop
runs, so no new worker can start during the drain.

```yaml
SHUTDOWN_BUDGET_MS: 5000        # whole shutdown window
SHUTDOWN_FLUSH_BUDGET_MS: 3500  # remote flush share, leaves room for the rest
SHUTDOWN_GRACE_MS: 6000         # anti-crash wait only, no terminate()
```

`wait_all_workers()` deduplicates workers by object, waits only for the
remaining budget, and never calls `QThread.terminate()`. When the budget expires
with a thread still running it waits out one in-flight HTTP timeout rather than
destroying the thread, leaves the HTTP pool open, and defers the remaining
operations to the next run. Lease release is skipped when the cloud is
unconfigured or a DNS failure is already confirmed; the server-side TTL expires
those leases instead.

### Measured worst case

Diagnosis baseline on the same scenario: `7.995 s` for the remote flush alone,
followed by an unbounded worker wait.

```yaml
scenario: cloud appears configured, network dead, v2 queue never drains, 8 leases held
flush_completed: false
flush_elapsed_ms: 3500
total_shutdown_ms: 3500
workers_drained: true
periodic_timers_stopped: true
remote_pull_timer_stopped: true
lease_release_calls: 8
queue_still_pending: 3
```

The pending operations stay in the durable queue, so the next run resumes them.

## Test execution

- `python -m unittest discover -s tests -t .` — 442 tests, 5 consecutive runs,
  all `OK (skipped=1)`.
- Every test module also runs standalone with no failures. This matters because
  `SyncManager` is a singleton; `reset_shutdown_state()` keeps a closed shutdown
  window from leaking into later tests.

## Known gaps

- No packaged `dist/Antigravity_AI_Writer.exe` was produced or signed in this
  stage. Packaging and a Windows staging E2E remain outstanding before release.
- `release_cloud_config.json` ships empty, so a release build starts with cloud
  sync disabled until the two public values are filled in.
- `mode_writing_old.py` is tracked, contains null bytes, is imported by nothing
  and fails `compileall`. Pre-existing and untouched by this branch.
