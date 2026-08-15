# Stage 9 cloud stabilization preflight — 2026-08-12

## Verdict

```yaml
status: PREFLIGHT_PASSED
branch: codex/windows-post-stage8-stabilization
base_head: 221db4fa69e06210c0afad16870ccb25ef015094
contract_version: 0.2.0
canonical_contract_sha256: 416c1b99edb9bda694731dee4b25688d9d82d1f32610aa23ddfda571ec3c7670
unit_tests: 444 passed, 1 skipped
consecutive_full_runs: 7
isolated_module_runs: 27 modules, 0 failures
packaged_build: produced, unsigned
packaged_sha256: 9cfa8f8bd70081c4c28e5ff57a93598bf57654e175c7be358e6bdbe1a61fbdc8
end_to_end_app_run: passed offscreen with an isolated profile
packaged_binary_run: passed, shutdown 0.08 s
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
| 3 | AI editors kept `typewriter_enabled = False` because the settings checkbox was `setChecked()` before its `toggled` connection | `8fb4920`, `4c4bef9` | `tests/test_typewriter_mode.py` (10) |
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

## Construction regression found and fixed during preflight

Stage 3 restored the saved typewriter setting by rewriting the document root
frame's bottom margin while the AI panels were still being constructed. The
paired AI editors share one `QTextDocument`, and mutating that layout before
either view exists kills Qt with no Python traceback — the app never finished
starting. The original code only did this from `resizeEvent`, so it was always
running against a realized view.

No test caught it: every typewriter test replaced the mode widgets with stubs,
so the real construction path had no coverage at all. `4c4bef9` defers the
layout work to `showEvent`/`resizeEvent`, keeps the state flag at construction
time, and adds `RealAssistantConstructionTestCase`, which builds the real
`AssistantModeWidget` instead of stubs.

This is also why the first packaged smoke run looked inconclusive: on an empty
profile the app stops at the first-run project dialog inside
`AssistantModeWidget.__init__`, and cancelling it calls `sys.exit(0)` before
the Qt event loop ever starts — so `aboutToQuit` correctly never ran.

## End-to-end application run

Real `MainWindow`, real assistant and writing modes, isolated profile
(`ANTIGRAVITY_ROOT_DIR`, `ANTIGRAVITY_APP_DATA_DIR`, `ANTIGRAVITY_AUTO_PROJECT`),
forced offline, offscreen platform. `config.json` requested the writing startup
screen with the AI draft and writing typewriter settings on.

```yaml
startup_ms: 422
startup_widget: WritingModeWidget   # finding 2
ai_draft_typewriter: [true, true]   # finding 3
writing_typewriter: [true, true]
shutdown_ms: 16                     # finding 4, cloud disabled
about_to_quit_ran: true
pid_file_removed: true
dialogs_shown: []
```

## Test execution

- `python -m unittest discover -s tests -t .` — 444 tests, 7 consecutive runs,
  all `OK (skipped=1)`.
- Every test module also runs standalone with no failures. This matters because
  `SyncManager` is a singleton; `reset_shutdown_state()` keeps a closed shutdown
  window from leaking into later tests.

## Packaged build

`python -m PyInstaller --noconfirm --clean Antigravity_AI_Writer.spec` succeeds.
The archive was inspected directly:

```yaml
size_bytes: 81238469
sha256: 9cfa8f8bd70081c4c28e5ff57a93598bf57654e175c7be358e6bdbe1a61fbdc8
archive_entries: 315
env_entries: []                     # no .env of any kind ships
release_config_fields: [supabase_publishable_key, supabase_url]
release_config_values: [empty, empty]
credential_field_leaks: []
jwt_in_release_config: false
```

### Packaged binary run

The frozen executable was launched in the same isolated profile and closed with
a real `WM_CLOSE`. PyInstaller onefile runs the UI in a child process, so the
close request has to go to the process that owns the window, not to the
launcher.

```yaml
startup_seconds: 2.7
onefile_child: true          # launcher pid != window owner pid
close_request_accepted: true
shutdown_seconds: 0.08       # release config ships cloud disabled
ui_process_exited: true
launcher_exited: true
pid_file_removed: true       # aboutToQuit ran to completion
leftover_processes: 0
```

## Known gaps

- The executable is unsigned, and no Windows staging E2E was run against a live
  Supabase project. Both remain outstanding before release.
- The packaged shutdown above was measured with cloud sync disabled, which is
  what the release config ships. The degraded-network worst case (`3.5 s`) comes
  from the component measurement, not from the frozen binary.
- `release_cloud_config.json` ships empty, so a release build starts with cloud
  sync disabled until the two public values are filled in.
- `mode_writing_old.py` is tracked, contains null bytes, is imported by nothing
  and fails `compileall`. Pre-existing and untouched by this branch.
