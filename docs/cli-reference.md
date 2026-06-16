# DevBench CLI Reference

Complete reference for every `devbench` subcommand. Commands are grouped by purpose; within each group they are ordered by the sequence an operator or agent typically hits them.

Every command runs from the parent workspace root (the directory containing the `devbench` checkout):

```bash
uv run devbench <command> [args]
# or: python3 -m devbench <command> [args]
```

Two environment variables MUST be set before any command runs; commands that depend on them exit non-zero with a clear message when unset:

- `DEVBENCH_WORKSPACE_ROOT` -- absolute path to the backlog workspace (contains `BACKLOG.md`, `backlog/`, `.devbench/`).
- `DEVBENCH_CLAUDE_MODEL` -- SDK caller's model id (example: `us.anthropic.claude-opus-4-8-v1`). Governs the orchestrate skill's coordination calls only. Per-agent work models live in the `agents:` block of `devbench.yaml` (see [ADR-25](adr/25-per-agent-model-overrides.md)).

Optional: `--config <path>` (or `DEVBENCH_CONFIG_PATH` env var) overrides the default `backlog/config/devbench.yaml` lookup.

## Exit codes (all commands)

- **0** -- success.
- **1** -- application-level error (invalid state, refused guard, missing work unit, bad args after parse).
- **2** -- argument-parsing error (unknown flag, missing required positional).

Commands that run a blocking external process (git, tests, judges) propagate the process exit code through, subject to the 0/1/2 contract above.

### Devbench-specific exit codes

The following non-zero codes are reserved for specific orchestrator states and MUST NOT be confused with each other by the wrapping `make start` loop:

| rc | Constant | Command | Meaning |
|----|----------|---------|---------|
| 42 | `ORCHESTRATOR_RESTART_EXIT_CODE` | `start` | Auto-restart signal: the SDK exited via `NO_ACTIONABLE` with only `RUNTIME_DEGRADATION` blockers. The wrapping loop restarts up to `DEVBENCH_MAX_AUTO_RESTARTS` times. |
| 43 | `ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE` | `start` | Continuation budget exhausted: the in-session resume loop issued `DEVBENCH_ORCHESTRATOR_MAX_TURN_END_CONTINUATIONS` consecutive non-terminal continuations without progress. Logged as `[ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED]`. Distinct from rc=42 so the wrapping loop never misclassifies fail-fast as auto-restart. |
| 44 | `CLAIM_BLOCKED_PRECLAIM` | `claim` | Target repo unresolvable: the repo declared in the work-unit file is unknown. Work unit is set to `blocked` with a `[BLOCKED_TARGET_REPO_UNRESOLVED]` marker. |
| 45 | `GET_DIFF_NO_ATTRIBUTABLE` | `get-diff` | No task-attributed commit found in defer-PR mode: staged and unstaged changes are both empty and no commit matches the work-unit ID. |
| 127 | `SUBPROCESS_ERROR_EXIT_CODE` | various | Subprocess command not found or timed out (Unix convention). |

## Contents

- [Backlog read](#backlog-read)
- [Backlog write](#backlog-write)
- [Drain (graceful orchestrator stop)](#drain-graceful-orchestrator-stop)
- [Named sessions](#named-sessions)
- [Scope selectors (printer-pages syntax)](#scope-selectors-printer-pages-syntax)
- [Orchestration and reporting](#orchestration-and-reporting)
- [Orchestrator helpers (invoked by agents)](#orchestrator-helpers-invoked-by-agents)
- [Git operations](#git-operations)
- [Amendment workflow](#amendment-workflow)
- [Proposal workflow (task factory)](#proposal-workflow-task-factory)
- [Environment migration](#environment-migration)
- [configure-devbench skill](#configure-devbench-skill)

---

## Backlog read

Non-mutating commands for inspecting backlog state.

### `status`

```
uv run devbench status [--detail] [--include "<tokens>"] [--exclude "<tokens>"] [--session <name>]
```

Print a summary of the backlog grouped by status. Output includes counts per lifecycle value (draft, in-queue, in-progress, in-review, done, blocked, proposed, declined, hold) plus an always-rendered `Un-materialised` count of proposal JSONs pending materialisation. Also lists active and blocked work units by ID.

The summary includes a `Draft N` row rendered between the `TOTAL` line and the `In Queue` line when any work units have `draft` status. Draft work units are not eligible for autonomous claim until promoted to `in-queue` via `devbench promote`.

**Scope filter flags:**

- `--include "<tokens>"` -- one-off include selector using printer-pages syntax. Overrides any active `scope.json` when supplied. Accepts comma-separated tokens (single IDs or last-segment ranges). See [Scope selectors](#scope-selectors-printer-pages-syntax) for the full syntax reference.
- `--exclude "<tokens>"` -- one-off exclude selector. Subtracts the matched IDs from the include set. Applied after include expansion.

**Named-session filter flag:**

- `--session <name>` -- filter the output to the work units claimed by the named session. Only events emitted under `session=<name>` are counted; the status counts and active-task list reflect that session's view only. Without `--session`, the command aggregates across all active sessions and renders the unified backlog state. See [Named sessions](#named-sessions) for the full session reference.

When neither flag is supplied, `devbench status` consults the active `<workspace>/.devbench/scope.json` (if present) and applies its filter automatically. When a scope is active -- whether from flags or from `scope.json` -- a `SCOPE:` banner is printed above the Status Summary:

```
SCOPE: include=[E1-E3, E5] exclude=[] (started 2026-05-14T13:42Z)
```

The banner names the raw include / exclude token lists and the timestamp from `scope.json` (or omits the timestamp for one-off `--include` invocations). When no scope is active the banner is suppressed entirely.

Pass `--detail` (E220) to additionally render three panels at the bottom of the output:

- **In-queue tasks (with dep status):** every Task currently in `in-queue`, marked `[ready]` if every dependency is terminal or `[waiting]` with the offending blocker ID otherwise.
- **Blocked tasks (with markers / blockers):** every Task currently in `blocked`, with the first `[BLOCKED_PENDING_PROPOSAL] <id>` marker found in its Comments and the first non-terminal dep ID; either may be empty when the block is from a different cause (manual block, review fail).
- **Held tasks (with most recent [HOLD] reason):** every Task currently on `hold`, with the latest `[HOLD] <reason>` line from its Comments.

Without `--detail` the panels are omitted (default invocation matches the historical output shape).

The summary's `Blocked` row is split into three lines (Part-1, post-issue-#118):

- `Blocked (auto)` -- ADR-07 cascade-clearing: the task carries a `[BLOCKED_PENDING_PROPOSAL]` marker chain that will resolve when its target tasks reach terminal.
- `Blocked (recovery)` -- AWAITING_AUTO_RECOVERY: no marker yet, but devbench's recovery loop has an artefact on disk (a pending proposal JSON, a rejected-amendment archive, or a recent recovery-agent `[BLOCKED]` audit comment within `DEVBENCH_BLOCKED_RECOVERY_WINDOW_SECONDS` -- default 1800s / 30min). The orchestrator's next sweep cycle will advance the task into the auto-clearing bucket. Operator does nothing.
- `Blocked (attn)` -- the true halt list: manual gates (`DO NOT CLAIM`), unknown marker targets, cascade-stuck states. Operator must act.

**Drain banner (issue #188):** when a `drain.signal` file is present in `<workspace>/.devbench/`, `devbench status` prepends a one-line banner above the Status Summary:

```
DRAIN REQUESTED: at 2026-05-14T13:55:01Z by matt (reason: nightly cutover)
```

The banner names the requester, the UTC timestamp, and the reason (or `(none)` when no reason was supplied). When no drain marker is present the banner is suppressed. See [`### drain`](#drain-graceful-orchestrator-stop) for the full drain subcommand reference.

### `next`

```
uv run devbench next [--include "<tokens>"] [--exclude "<tokens>"]
```

Print the next actionable work unit as JSON. Returns `ALL_DONE` when every unit is done and `NO_ACTIONABLE` when something is blocked or in-progress but nothing is ready to start. Used by the orchestrate SKILL to drive the main loop.

**Scope filter flags:** `--include` and `--exclude` accept the same printer-pages-style tokens as `status` and `start`. One-off flags override any active `scope.json`; when neither flag is supplied, the active `scope.json` (if present) is consulted automatically. When a scope is active, only work units within the scope's `expanded_ids` set are eligible candidates. See [Scope selectors](#scope-selectors-printer-pages-syntax) for the token syntax.

### `report`

```
uv run devbench report [--once|--no-stream] [--since <ISO-8601>] [--watch N] [--include "<tokens>"] [--exclude "<tokens>"] [--session <name>] [--by-role]
```

**Scope filter flags:** `--include` and `--exclude` accept the same printer-pages-style tokens as `status`, `next`, and `start`. One-off flags override any active `scope.json`; when neither flag is supplied, the active `scope.json` (if present) is consulted automatically. When a scope is active, only work units within the scope's `expanded_ids` set are counted in the per-epic Status Summary table. See [Scope selectors](#scope-selectors-printer-pages-syntax) for the token syntax.

**Named-session filter flag:** `--session <name>` restricts the report to the work units and log events associated with the named session. The per-epic Status Summary, velocity, and cost panels reflect that session's activity only. Without `--session`, the command aggregates across all active sessions -- equivalent to the pre-session single-session behaviour and the default for new workspaces. See [Named sessions](#named-sessions) for the full session reference.

Print the progress report with velocity, token consumption, and estimated cost. Default layout renders two side-by-side tables: **All-time** (full log) and **Current run** (most recent contiguous block of orchestration events, boundary detected as a gap over 10 minutes between consecutive `Set X to ...` log lines).

The Status Summary per-epic table (also written to `BACKLOG.md` by `validate-backlog`) includes a `Draft` column alongside the existing status columns. The column count reflects the number of draft-status work units under each epic. Epics with no draft work units show `0` in the `Draft` column.

**Issue #163: streaming default on TTY.** `devbench report` (no flags) opens an always-on streaming view that polls cache stats every ~100ms and re-renders the report whenever any source file advances. The screen never goes blank between refreshes -- the new frame is rendered to memory first, then emitted with the clear sequence in a single buffered write so the terminal flips OLD frame -> NEW frame in one redraw cycle. Ctrl+C exits cleanly. A `[refresh] cold X.Xs / warm Y.YYs / last refresh Z.ZZs` footer at the bottom of every frame exposes the loop's pace.

**Required environment variables (issue #221 B7):** every `devbench` subcommand -- including `report` -- requires both `DEVBENCH_WORKSPACE_ROOT` and `DEVBENCH_CLAUDE_MODEL` to be set before invocation. The check fires at module-import time (`src/devbench/config.py::_require_env`); when either variable is missing devbench prints a single actionable line to stderr (`devbench: DEVBENCH_WORKSPACE_ROOT environment variable is not set. Set it to the absolute path of your workspace root.`) and exits with code 2. Before the issue #221 B7 fix this path raised a Python traceback to stderr instead, which stdout-only consumers (`devbench report > out.txt`) saw as "rc=0, empty output" -- the symptom that the issue is filed against. The current behaviour is fail-fast (CLAUDE.md): non-zero exit, no traceback, no silent fallback.

- `--once` (alias `--no-stream`) -- forces the legacy one-shot snapshot, suitable for scripts and CI consumers that pipe the output. Auto-engaged when stdout is not a TTY (pipe / file redirect / CI).
- `--since <ISO-8601>` -- renders a single custom-window table and exits one-shot. A frozen-window snapshot doesn't benefit from streaming.
- `--watch N` -- *deprecated.* Kept for backward compatibility; emits a one-line deprecation notice and falls through to the streaming loop. The integer interval is ignored (cadence is data-driven).
- `--by-role` (issue #206) -- opt-in per-role token/cost breakdown panel rendered beneath the aggregate Cost section. Default OFF; without the flag the output is unchanged from the pre-#206 layout. The panel groups every transcript message by `attributionAgent` (executor, code_review, test_review, doc_review, changes_manifest, security_review, blocker_resolver, manifest_amender, task_factory, orchestrator) and prints input/output/cache-read/cache-write tokens, message count, and est_cost per role. The TOTAL row is asserted equal to the sum of the per-role rows at render time. Example output:

  ```
  Per-role cost breakdown (current run):
  role                  input_tokens  output_tokens  cache_read  cache_write  msgs   est_cost
  executor                   500,000        100,000           0            0    47    $5.0000
  code_review                200,000         40,000           0            0    18    $2.0000
  TOTAL                      700,000        140,000           0            0    65    $7.0000
  ```

  Per-role and per-model (issue #223) are orthogonal axes; the per-model rate table in `report.models` prices each row's tokens at the model that actually produced them.

Cost is computed per call, per token type, from real `usage` data. See [model-pricing.md](model-pricing.md) for the cost formula, per-model rates, and cache-multiplier env vars.

**Log-file resolution (fail-fast, no fallbacks):** `devbench report` reads its log file in this order. The same chain is used by the orchestrator's `setup_logging` writer, so both reader and writer always resolve to the same path:

1. `DEVBENCH_LOG_FILE` environment variable -- explicit override; the caller takes responsibility. Wins over everything below; useful for ad-hoc redirects and tests.
2. `log_file:` in the workspace's `backlog/config/devbench.yaml` (top-level field) -- the **single source of truth** for ordinary launches. Resolved relative to `DEVBENCH_WORKSPACE_ROOT` when not absolute. Both the orchestrator (writer) and `devbench report` / `devbench hook-tail` (readers) consult this field, so coordinating shell envs across panes is no longer required.
3. `<DEVBENCH_WORKSPACE_ROOT>/logs/orchestrator.log` -- the canonical per-workspace default applied when neither (1) nor (2) is set.

When NONE of (1)/(2)/(3) yields a path -- i.e. `DEVBENCH_LOG_FILE` unset, `log_file:` absent from yaml, AND `DEVBENCH_WORKSPACE_ROOT` unset -- `devbench report` exits 1 with an actionable error naming all three sources. The previous implementation silently fell back to the devbench source-tree's log (`<devbench>/src/devbench/logs/orchestrator.log`), which let operators read a stale, unrelated log without noticing -- the BACKLOG.md done count and the log-derived throughput count then diverged silently.

**Divergence WARNING:** when `BACKLOG.md` reports a non-zero "Tasks completed" count but the All-time throughput window finds zero `Set <id> to 'done'` events, the report emits a one-line WARNING above the trailing summary. The two counts MUST agree on a healthy backlog (the throughput row narrates the events that produced the backlog state). A divergence almost always means `devbench report` is reading a different log than the orchestrator writes to. The warning names the log file path so the operator can immediately identify the mismatch and either set `DEVBENCH_LOG_FILE` correctly or invoke `devbench report` from the same env the orchestrator was launched with.

**Blocked-task classification (Part-1, post-issue-#118):** the report renders blocked tasks across three panels, ordered by what the operator should do:

- `Blocked tasks (auto-clearing via proposal)` -- the ADR-07 cascade will fire when every `[BLOCKED_PENDING_PROPOSAL]` marker target reaches terminal. Each row names the IDs the task is waiting on. Operator does nothing.
- `Blocked tasks (auto-recovery in flight)` -- no marker yet, but devbench's recovery loop has an artefact on disk: a pending proposal JSON, a rejected-amendment archive, or a recent recovery-agent `[BLOCKED]` audit comment within `DEVBENCH_BLOCKED_RECOVERY_WINDOW_SECONDS` (default 1800s). Each row carries a `[recovery: <signal-source>]` annotation so the operator can see which signal drove the classification. Operator does nothing for now -- the next sweep cycle will advance the task into the auto-clearing bucket.
- `Blocked tasks (needs operator attention)` -- the true halt list: manual gates (`DO NOT CLAIM`), unknown marker targets, cascade-stuck states. Each row carries just ID + title; the operator opens the work-unit file to read the blocker comment.

Empty panels are omitted entirely. The recency-window override (`DEVBENCH_BLOCKED_RECOVERY_WINDOW_SECONDS=<seconds>`) lets operators with slower iteration cadences extend the audit-comment window.

**ETA formula (issue #157):** the `Est. time to complete remaining` cell now multiplies the recent-pace minutes by `tasks_active + tasks_blocked_recovery + tasks_blocked_auto`. Both blocked buckets resolve on devbench's own (proposal cascade or auto-recovery loop), so excluding them produced an unrealistically optimistic ETA. The `needs operator attention` bucket stays excluded -- those are genuine halts with unbounded ETA. The cell carries a comment-suffix naming the bucket counts and pace, e.g. `~5.4 h (active 4 + blocked-recovery 60 + blocked-auto 27 at 5.6 min/task)`. The cost projection uses the same denominator. ETA falls back to `n/a` when fewer than the required pace samples have completed in the recent window (the metric is fragile and a single completion would project meaningless numbers).

**In-progress duration (issue #158):** the `In-progress tasks:` panel suffixes every row with a humanized attempt duration (`23m`, `1h 47m`, `2d 3h`). Multiple in-progress transitions for the same task (blocked-then-resumed) resolve to the most recent one. When neither the structured log nor the work-unit's audit comments yield a parseable timestamp the row renders `(in-progress, timer unavailable)` -- never silently omitted. The same suffix appears on `devbench status` and `devbench status --detail` Active rows.

**Orchestrator-alive banner (issue #161):** the very first line of `devbench report` is a one-line liveness banner that is PID-authoritative. Three states:

- `[ORCHESTRATOR ALIVE]` (green) -- a live PID exists AND the log contains a parseable timestamp. Suffix names the elapsed-since duration (`last activity 12s ago`).
- `[ORCHESTRATOR STOPPED]` (red) -- no live PID, regardless of log recency. Suffix names elapsed-since plus the last-seen UTC timestamp when a log line is available (`no activity for 14m (last seen 2026-05-04 13:21 UTC)`), or a "no activity recorded" message when the log has no parseable timestamp.
- `[ORCHESTRATOR STARTING]` (yellow) -- a live PID exists but the log file is missing, empty, or has no parseable log line yet (including an untimestamped traceback tail). The orchestrator process is running but has not written a timestamped event.

Every banner ends with the active session id when `DEVBENCH_ORCHESTRATOR_SESSION_ID` is set (`-- session backlog-a-orchestrator`); the suffix is suppressed when the env var is unset so multi-session operators never see a `-- session None` artefact.

**Session-aware banner (multi-session runs):** when the session registry (`.devbench/sessions/registry.json` -- the same source `devbench sessions` reads) holds one or more registered sessions, the single global `[ORCHESTRATOR ...]` line is replaced by **one `[SESSION <name> ...]` line per registered session**. Each line is evaluated independently against THAT session's own per-session PID liveness (`SessionRegistry.is_alive`), its own per-session log (`.devbench/sessions/<name>/orchestrator.log`) recency, and its own drain signal (`.devbench/sessions/<name>/drain.signal`). The classic single global line therefore never reports `[ORCHESTRATOR STOPPED]` while another session daemon is alive. Per-session states:

- `[SESSION <name> ALIVE]` (green) -- this session's PID is alive and its per-session log has a parseable timestamp (`last activity 4s ago`).
- `[SESSION <name> DRAINING]` (yellow) -- this session's PID is alive AND a drain signal is pending for it; the line carries an explicit `-- drain=pending` marker. This surfaces the same `DRAIN=pending` state shown by `devbench sessions`.
- `[SESSION <name> STARTING]` (yellow) -- this session's PID is alive but its per-session log has no parseable timestamp yet (a pending drain still appends `-- drain=pending`).
- `[SESSION <name> STOPPED]` (red) -- this session's PID is dead, regardless of log recency (`no activity for 6m (last seen ...)`).

When the registry is absent or empty (single classic session / no `--name` run), the banner falls back to the single-line `[ORCHESTRATOR ...]` behaviour above (back-compat). Both the daemon and foreground run modes use the same per-session rendering, since both register in the same registry.

ANSI colour is emitted only when stdout is a TTY and `NO_COLOR` is unset (mirrors the existing colour rules elsewhere in the report). When piped to `cat`, redirected to a file, or running in CI, the banner renders as plain text.

Refreshes on every `--watch N` tick alongside the rest of the table -- no separate clear sequence, so no flicker.

The threshold reuses the existing `stop_hook.window_seconds` knob (default 180s). This intentionally couples the banner's idea of "orchestrator quiet" with the circuit-breaker's, so an operator who tuned the stop-hook window to tolerate long terraform-apply / smoke-test stretches will not see the banner flash STOPPED during those quiet windows. There is no separate liveness-threshold env var or YAML field; if a future use case needs banner cadence to differ from circuit-breaker cadence, file an issue to decouple them.

### `watch`

```
uv run devbench watch [--watch N]
```

Read-only one-screen live dashboard of the currently-active orchestration: mode, active task, phase, latest agent thinking, recent tool calls, repo state, pending amendment. `--watch N` refreshes every N seconds. See [watch-activity.md](watch-activity.md) for panel details.

### `hook-tail`

```
uv run devbench hook-tail [<path>] [--tz <zone>] [--no-follow] [--from-start]
                          [--orchestrator-only | --orchestrator-session <id>]
```

Read-only pretty-tail of the plugin hook event stream (`hook-logs.jsonl`). One-line colourised summary per PreToolUse / PostToolUse / SubagentStart / SubagentStop / Stop event. Complements `watch`: where `watch` shows current state, `hook-tail` shows events as they happen.

- `<path>` defaults to `$DEVBENCH_WORKSPACE_ROOT/hook-logs.jsonl`.
- `--tz <zone>` overrides the display timezone (any IANA zone, for example `America/Denver`). When `--tz` is absent, `hook-tail` falls back to the top-level `display_timezone:` yaml key (or `DEVBENCH_DISPLAY_TIMEZONE` env), then to OS local. Internal storage stays in UTC.
- `--no-follow` exits after emitting existing events instead of tailing.
- `--from-start` emits every event from the beginning of the file before entering follow mode.
- `--orchestrator-only` (Phase 11 / E230) filters the stream to events whose `orchestrator_session` field equals `$DEVBENCH_ORCHESTRATOR_SESSION_ID`. When the env var is unset the command exits 2 with an actionable error -- pass `--orchestrator-session <id>` instead to supply the value explicitly.
- `--orchestrator-session <id>` filters by an explicit session id (audit / replay use case). Pre-Phase-11 log entries that lack the field are passed through unfiltered so historical events stay visible.

The launch command in `caylent-telemetry-spec/devbench-launch-commands.txt` sets `DEVBENCH_ORCHESTRATOR_SESSION_ID` on both the orchestrator pane (so the plugin's `hook-logger.sh` stamps every event) and the hook-tail pane (so the filter has a value to match). Side-pane Claude sessions started ad-hoc inherit the workspace root but NOT the session id, so their tool calls land in the log with an empty `orchestrator_session` and are dropped by the filter -- a `tail -f hook-logs.jsonl` would still see them, but the pretty-printed orchestrator pane stays clean.

See [hook-activity.md](hook-activity.md) for the event glyphs and the full column legend.

### `watchdog`

```
uv run devbench watchdog [--idle-minutes N] [--flag-file PATH] [--log-file PATH] [--print-if-stuck]
```

Single-shot poll that detects a stuck `/devbench:orchestrate` loop and writes a marker file the operator can surface in their shell prompt. Exits 0 always -- it is a checker, not a daemon.

A run is considered stuck when **both** conditions hold:

1. `BACKLOG.md` contains at least one row with `Status: in-progress`.
2. The most recent dated line in the orchestrator log is older than `--idle-minutes` (default 5). Path resolution mirrors `devbench report` -- (1) `DEVBENCH_LOG_FILE`, (2) `log_file:` in `backlog/config/devbench.yaml`, (3) `<DEVBENCH_WORKSPACE_ROOT>/logs/orchestrator.log`.

On stuck detection the marker file is written with:

```json
{
  "ts": "2026-04-22T21:00:00Z",
  "task_id": "E1-F2-S26-T3",
  "task_file_path": "backlog/E1/E1-F2/E1-F2-S26/E1-F2-S26-T3.md",
  "orchestrator_idle_seconds": 600,
  "last_orchestrator_log_ts": "2026-04-22T20:08:01Z",
  "idle_threshold_seconds": 300,
  "stale_task_minutes_threshold": 120
}
```

Flags:

- `--idle-minutes N` -- override the idle threshold (default 5; minimum 1).
- `--flag-file PATH` -- override the marker path (default `$DEVBENCH_WORKSPACE_ROOT/.devbench/needs-restart.flag`).
- `--log-file PATH` -- override the orchestrator log location. Default is the devbench repo's `src/devbench/logs/orchestrator.log` relative to the installed package; pass an explicit path (or set `DEVBENCH_LOG_FILE` and read `$DEVBENCH_LOG_FILE`) to point watchdog at the same workspace-local log the orchestrator wrote to. `cmd_watchdog` does NOT consult the `log_file:` yaml field today; pass `--log-file` (or wrap with the env) to keep the writer/reader in sync.
- `--print-if-stuck` -- print a one-line `[devbench watchdog] STUCK: <id> (idle Ns, threshold Mm)` status to stdout on detection. Silent when healthy so it pipes cleanly in `PROMPT_COMMAND`.

Typical operator integrations:

```bash
# Shell prompt nag (add to ~/.bashrc / ~/.zshrc):
PROMPT_COMMAND="uv run devbench watchdog --print-if-stuck; $PROMPT_COMMAND"

# Terminal watcher in a second pane:
watch -n 60 'uv run devbench watchdog --print-if-stuck'

# Cron-style polling (no follow loop -- one-shot):
*/5 * * * * cd /path/to/workspace && uv run devbench watchdog
```

The watchdog never attempts to restart orchestration itself. Restarts remain under operator control because they may overlap with manual edits and affect billing.

#### Liveness / turn-end recovery -- two-layer model

Devbench uses two independent, non-overlapping layers to detect and respond to orchestrator stalls. The layers share no code and use separate, independently configurable thresholds.

**Layer 1 -- In-process auto-recovery net (inactivity timeout + bounded in-session continuation)**

This layer runs inside the `devbench start` process. It monitors the time elapsed since the last meaningful orchestrator message within a single SDK session. When the elapsed time exceeds `DEVBENCH_ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS`, the orchestrator issues an in-session continuation prompt to nudge the model back into action. These continuations are bounded: once the in-session resume loop has issued `DEVBENCH_ORCHESTRATOR_MAX_TURN_END_CONTINUATIONS` consecutive non-terminal continuations without progress, the process exits with rc=43 (`ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE`) and logs `[ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED]`. The wrapping `make start` loop detects rc=43 (distinct from rc=42) and does NOT apply the auto-restart path -- fail-fast is intentional once the budget is exhausted.

Config keys for Layer 1:

| Environment variable | Type | Default | Notes |
|----------------------|------|---------|-------|
| `DEVBENCH_ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS` | float | `300.0` | Seconds of silence before an in-session continuation is issued. A value `<= 0` disables the inactivity check entirely (the continuation budget is never consumed). |
| `DEVBENCH_ORCHESTRATOR_MAX_TURN_END_CONTINUATIONS` | int | `5` | Maximum number of consecutive non-terminal continuations allowed before the process exits rc=43. Must be a positive integer when the inactivity check is active. |

**Layer 2 -- External detect-only watchdog (`devbench watchdog`)**

This layer runs outside the `devbench start` process -- as a cron job, a shell-prompt hook, or a terminal watcher (see typical integrations above). It polls the orchestrator log and the BACKLOG state, writes a `needs-restart.flag` file when a stall is detected, and then exits. It does NOT attempt to restart orchestration, issue continuations, or modify any in-process state. Its idle threshold (`--idle-minutes`, default 5 minutes) is a wall-clock check on log quiescence -- completely independent of Layer 1's per-message inactivity timer.

**When each layer fires:**

- Layer 1 fires entirely within the running session when the SDK model goes quiet for `DEVBENCH_ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS` seconds. If the model recovers (emits a tool call) before the budget is exhausted, no external action is needed and the session continues normally.
- Layer 2 fires after the external poll interval (typically 5 minutes of log quiescence) regardless of whether Layer 1 already recovered. If Layer 1 exited rc=43 and the wrapping loop did not restart, Layer 2 will eventually detect the stale `in-progress` marker and write the flag file.

**The two layers are complementary, not redundant.** Layer 1 auto-recovers transient model stalls in-session without operator involvement. Layer 2 provides an operator-visible signal for cases where the entire process has exited or the wrapping loop itself is absent. Neither layer replaces the other.

### `cost-calibrate`

```
uv run devbench cost-calibrate <actual-usd> [--window <ISO-8601>]
```

Issue #223: calibrate per-model correction factors against an actual Anthropic invoice. Sums devbench's reported cost across every model observed in the window, derives `correction_factor = actual_usd / reported_total`, and writes the factor back to `report.models.<id>.correction_factor` in `backlog/config/devbench.yaml` for every model that contributed. The next `devbench report` reflects the corrected total without further operator action.

- `<actual-usd>` -- required; the operator's actual spend in USD for the window (typically taken from an Anthropic invoice line). Must be > 0.
- `--window <ISO-8601>` -- optional; restricts the calibration window to events at or after the given timestamp. Defaults to `1970-01-01T00:00:00Z` (every event in the cache).

Exit codes:

- `0` on success (file written, summary printed).
- `1` when the selected window's reported cost is `$0.00` (no billable activity yet -- widen the window or run after a real session).
- `2` on argument validation errors or missing `backlog/config/devbench.yaml`.

Successive calibrations replace (not multiply) the prior `correction_factor` so re-running with the same actual-spend figure is idempotent. See [model-pricing.md](model-pricing.md) for the full calibration workflow.

### `list-proposals`

```
uv run devbench list-proposals
```

Print every pending task-factory proposal with a per-task `[state]` label: `[unmaterialised]`, `[proposed]`, `[promoted]`, `[done]`, `[declined]`, `[rejected]`. Used by the orchestrator and by operators triaging proposed drafts. See [task-factory.md](task-factory.md).

### `validate-backlog`

```
uv run devbench validate-backlog
```

Integrity check across the full backlog: file existence, status sync between BACKLOG.md and work-unit files, orphaned files, invalid dependency references, Status Summary table count accuracy, content-rule violations, and Changes Manifest path-prefix violations (reject any manifest entry that begins with a `<checkout_directory>/` prefix; see [backlog-contract.md](backlog-contract.md) for the path-prefix rule). Exits 1 and prints every finding when any violation is found.

Additional rules enforced as part of the Backlog A lessons-learned tooling (see [backlog-author-discovery.md](backlog-author-discovery.md) and [source-test-atomicity.md](source-test-atomicity.md)):

- **Manifest Conflict Rule**: two in-queue tasks targeting the same `(repo, file)` ownership without an explicit dependency ordering between them are rejected. Wire a dependency via `devbench add-dep <later> <earlier>` or reassign ownership.
- **Language-AC Alignment Rule**: AC-FINAL Python-tooling lines (002-005, 008, 014) on non-Python tasks (HCL/YAML/JSON-only Manifests) must end with the `-- N/A for <tier> Tasks (no Python source authored)` suffix.
- **Source-Test Atomicity Rule**: every production Python source file in a Manifest (under `src/`, `infra/scripts/`, or `services/<name>/src/`) must have a matching `test_<basename>.py` entry in the SAME Manifest. Splitting source/test across sibling tasks blocks AC-FINAL-014 coverage at task close.

Invoked automatically at orchestrator startup; operators should run it after hand-edits.

### `write-snapshot`

```
uv run devbench write-snapshot
```

Render the report once and persist it to `<workspace>/.devbench/report-snapshot.json`. Used by the orchestrate skill at the end of every loop iteration so subsequent `devbench report --once` calls serve from the snapshot quickly (file read + print only, skipping log parse and aggregation). Idempotent. ADR-20.

### `rebuild-window-stats`

```
uv run devbench rebuild-window-stats
```

Walks the orchestrator log and rebuilds every per-task aggregate JSON under `<workspace>/.devbench/window-stats/`. Idempotent; safe to run at any cadence. Use after manually deleting `.devbench/window-stats/` or when window-stats files appear out of sync with the log. ADR-17.

### `archive-session`

```
uv run devbench archive-session <session-id> [--log-path <path>]
```

Convert an ended session's JSONL log to a Parquet cold archive at `<workspace>/logs/legacy/<session-id>.parquet`. **Opt-in** via `pip install devbench[archive]`; raises `ArchiveDependencyMissing` with the install command when `pyarrow` isn't installed. Per-session; operator-driven. ADR-21.

### `check`

```
uv run devbench check
```

Pre-flight verifier for orchestrator launch readiness. For every repo in `backlog/config/devbench.yaml`'s `repos:` map, confirms (1) symlink at `$DEVBENCH_WORKSPACE_ROOT/<checkout_directory>` exists, (2) the local clone has an `origin` remote, (3) the remote's `default_branch` matches `devbench.yaml` (when set), and (4) no open PR already targets `git_ops.single_branch` (when single-branch mode is on). Exits 0 when every repo passes; exits 1 with one actionable error per failure otherwise. The `gh api` / `gh pr list` calls use the timeout in `DEVBENCH_CHECK_GH_API_TIMEOUT` (seconds, default `30`).

Under `git_ops.local_only: true`, the origin check inverts: the local clone MUST NOT have an `origin` remote (presence is a misconfiguration). Checks (3) and (4) are skipped because there is no remote to query through `gh`.

### `read-unit`

```
uv run devbench read-unit [--strip-comments] <id>
```

Print the work-unit content (the `.md` file body) plus the resolved repo path as JSON. Used by agents to fetch work-unit context for their prompts. `--strip-comments` omits the `## Comments` audit history, useful for first-round review agents that do not need prior feedback context.

---

## Backlog write

Mutating commands on the backlog itself. All writes go through the workflow gates; operators use these commands directly only for recovery or lifecycle transitions that the orchestrator does not drive.

### `claim`

```
uv run devbench claim <id>
```

Set the work unit's status to `in-progress`. Fails if the unit is already in a terminal state. Invoked by the orchestrate SKILL at the start of each loop iteration.

### `set-status`

```
uv run devbench set-status <id> <status>
uv run devbench set-status --include "<tokens>" [--exclude "<tokens>"] [--dry-run] [--yes] <new_status>
```

Force any status on a work unit. Skips the done-gate and other workflow checks. Used for recovery (unblock a stuck unit, resurrect a declined unit) and for orchestrator-internal lifecycle transitions. Accepted values: `draft`, `in-queue`, `in-progress`, `in-review`, `done`, `blocked`, `proposed`, `declined`, `hold`.

Note: to transition `draft -> in-queue` on one or more units, prefer `devbench promote` (which validates the source status and writes the `[PROMOTED] draft -> in-queue` audit comment). Use `set-status draft` only for ad-hoc recovery or for setting a new work unit's initial state when the default-status config is not sufficient.

**Range variant (bulk updates):**

The range variant accepts printer-pages-style selector tokens (the same syntax used by `devbench start --include` and `devbench scope set`) and applies the requested status to every matched work unit in a single atomic transaction:

- `--include "<tokens>"` -- printer-pages selector for the target work units. Accepts comma-separated tokens: single IDs (`E1-F2-S3-T4`), last-segment ranges (`E2-F1-S1-T3-T7`), epic/feature/story shorthands (`E1`, `E2-F1`, `E2-F1-S1`). See [Scope selectors](#scope-selectors-printer-pages-syntax) for the full token syntax.
- `--exclude "<tokens>"` -- subtract the matched IDs from the include set. Applied after include expansion.
- `--dry-run` -- enumerate the matched work units and the target status without writing any changes. Prints one line per matched unit and a summary count. Exits rc=0.
- `--yes` -- skip the interactive confirmation prompt. By default, `set-status` prompts for confirmation when the expanded set exceeds `bulk_update_confirm_threshold` (default: 10, configurable in `backlog/config/devbench.yaml`). Pass `--yes` to bypass the prompt in automation / CI.

All writes acquire the `BACKLOG.lock` flock once before iterating matched units, so the operation is atomic -- concurrent orchestrators see either all updates or none. Each per-unit write goes through `BacklogManager._set_status` so audit logic and rollup logic continue to fire. A workspace-level `[BULK_STATUS_UPDATE] <count> WUs set to '<status>' by --include="..." --exclude="..."` audit row is appended to the path declared in `bulk_update_audit_path` (default: `logs/bulk-updates.log`) after every successful bulk invocation.

**BacklogConfig keys consumed by the range variant:**

```yaml
backlog:
  bulk_update_confirm_threshold: 10           # prompt when expansion > N (default 10)
  bulk_update_audit_path: logs/bulk-updates.log   # workspace-relative audit log path
```

**Worked examples:**

```bash
# Promote all units in epic E1 to in-queue (release for autonomous work):
uv run devbench set-status --include "E1" in-queue
# -> prompts for confirmation if expansion > bulk_update_confirm_threshold
# -> [BULK_STATUS_UPDATE] 42 WUs set to 'in-queue' by --include="E1" --exclude=""

# Hold all units in epic E5 (pause while scope is reconsidered):
uv run devbench set-status --include "E5" hold
# -> [BULK_STATUS_UPDATE] 37 WUs set to 'hold' by --include="E5" --exclude=""

# Decline a range of tasks E2-F1-S1 T3 through T7:
uv run devbench set-status --include "E2-F1-S1-T3-T7" declined
# -> [BULK_STATUS_UPDATE] 5 WUs set to 'declined' by --include="E2-F1-S1-T3-T7" --exclude=""

# Preview which units would be affected without writing (--dry-run):
uv run devbench set-status --include "E3" --dry-run in-queue
# -> DRY RUN: 18 WUs would be set to 'in-queue':
#      E3-F1-S1-T1  (draft)
#      E3-F1-S1-T2  (draft)
#      ...
#    No changes written.

# Skip the confirmation prompt for large expansions (CI / automation):
uv run devbench set-status --include "E1-E4" --yes in-queue
# -> [BULK_STATUS_UPDATE] 163 WUs set to 'in-queue' by --include="E1-E4" --exclude="" (no prompt)

# Promote epic E2 but exclude a sub-tree already done:
uv run devbench set-status --include "E2" --exclude "E2-F3" in-queue
# -> [BULK_STATUS_UPDATE] 29 WUs set to 'in-queue' by --include="E2" --exclude="E2-F3"
```

**Exit codes:**

| Command | rc=0 | rc!=0 |
|---------|------|-------|
| `set-status <id> <status>` | Status written. | rc=1 when ID not found or status value unrecognised. |
| `set-status --include ... <status>` | All matched units written. | rc=1 when no units match, status value unrecognised, or reversed range token detected. |
| `set-status --include ... --dry-run <status>` | Preview printed; no write. | rc=1 on invalid selector syntax. |
| `set-status --include ... --yes <status>` | All matched units written (no prompt). | rc=1 on selector / status errors. |

### `mark-done`

```
uv run devbench mark-done <id>
```

Mark the unit as `done`. Enforces the done-gate: the five always-on core judges (`code_review`, `test_review`, `doc_review`, `changes_manifest`, `security_review`) must each have logged `[REVIEW_PASS]` in the most recent round (after any intervening `[REVIEW_REJECTED]`). When an optional specialty judge is enabled and applicable to the unit -- `iac_review` for a unit whose `## Verification` contract has an infrastructure item -- its `[REVIEW_PASS]` is also required. Exits 1 with a clear error naming the missing judge(s) when the gate fails.

### `decline`

```
uv run devbench decline <id> --reason "<message>"
```

Mark a work unit `declined`: it will never be done. Used when the operator decides the unit's scope is being removed, the functionality is being deleted instead, or a different task delivered the same outcome. Declined children count as terminal-complete for parent rollup. See [ADR-05](adr/05-declined-status.md).

### `hold`

```
uv run devbench hold <id> --reason "<message>"
```

Mark a work unit `hold`: it is intentionally deferred (under debate, awaiting external decision, scope being re-discussed). Held units are skipped by the orchestrator's `next`/parallel-candidate scan until an operator runs `unhold`. Unlike `declined`, `hold` is **not** terminal -- a held child keeps its parent open and uncounted toward auto-rollup. The `--reason` is REQUIRED so the deferral leaves an audit trail in the work-unit's Comments section (`[HOLD] <reason>`); em-dashes in the reason text are rejected at the input boundary. Multi-token reasons survive the dispatcher (the command is registered as variadic) so `--reason "needs product input on scope"` works without quoting tricks.

### `unhold`

```
uv run devbench unhold <id> --reason "<message>"
```

Return a held work unit to `in-queue`. Refuses any unit whose current status is anything other than `hold` (fail-fast keeps the lifecycle linear -- use `set-status` for ad-hoc transitions). The `--reason` is REQUIRED and captured as `[UNHOLD] <reason>` in the Comments section, so a hold-then-unhold round-trip is fully reconstructible from the audit trail.

### `promote`

```
uv run devbench promote <id>
uv run devbench promote --epic <id>
uv run devbench promote --feature <id>
uv run devbench promote --story <id>
uv run devbench promote --all [--yes]
```

Transition one or more work units from `draft -> in-queue`, making them eligible for autonomous claim. Each promoted work unit receives a `[PROMOTED] draft -> in-queue` audit comment in its `## Comments` section.

Refuses (rc=1) any work unit that is not currently in `draft` status -- use `set-status` for ad-hoc transitions between other statuses.

**Selector variants:**

- `devbench promote <id>` -- promote a single work unit by its ID (e.g. `E1-F2-S3-T4`). Exits 1 with an actionable error if the unit is not currently in `draft`.
- `devbench promote --epic <id>` -- promote every work unit under the named epic in one transaction (e.g. `devbench promote --epic E1`). All descendants must be in `draft` status; the entire transaction aborts with rc=1 if any descendant is not in `draft`.
- `devbench promote --feature <id>` -- promote every work unit under the named feature (e.g. `devbench promote --feature E1-F2`). All descendants must be in `draft` status; the entire transaction aborts with rc=1 if any descendant is not in `draft`.
- `devbench promote --story <id>` -- promote every work unit under the named story (e.g. `devbench promote --story E1-F2-S3`). All descendants must be in `draft` status; the entire transaction aborts with rc=1 if any descendant is not in `draft`.
- `devbench promote --all` -- promote every `draft`-status work unit in the entire backlog. Prompts for confirmation unless `--yes` is also passed.
- `devbench promote --all --yes` -- skip the confirmation prompt and promote all draft work units immediately. Safe for automation / CI.

**Example -- single unit:**

```bash
uv run devbench promote E1-F2-S3-T4
# -> [PROMOTED] draft -> in-queue appended to E1-F2-S3-T4.md
```

**Example -- bulk by epic:**

```bash
uv run devbench promote --epic E5
# -> all draft WUs under E5 promoted to in-queue in one pass
```

**Example -- all drafts with confirmation bypass:**

```bash
uv run devbench promote --all --yes
# -> every draft WU in the backlog promoted; no interactive prompt
```

Implementation detail: `promote` delegates to `BacklogManager.force_status` per unit and appends the audit comment via `BacklogManager._append_agent_comment`. No new status-transition logic is introduced; the command is a thin operator-facing wrapper that validates the pre-condition (`draft`) and iterates the selected scope.

### `new-task`

```
uv run devbench new-task --id <ID> --title "<TITLE>" --target <PATH>
                         [--repo <ORG/REPO>]
                         [--description <TEXT>]
                         [--source-file <PATH>]
                         [--test-file <PATH>]
                         [--ac-func <TEXT>]
```

Scaffold a new work-unit `.md` file from the canonical template. Required flags: `--id`, `--title`, `--target`. The template kind is derived from the ID's last segment: `T` -> task, `S` -> story, `F` -> feature, `E` -> epic; an unrecognised shape exits 1 with an actionable error. The command refuses to overwrite an existing `--target` and refuses a `--target` whose parent directory is missing -- create the directory first or pick a different path.

Optional flags fill `{{TOKEN}}` placeholders in the template. Tokens with no flag get a deterministic default (e.g. `--source-file` -> `src/<repo-name>/<id-lower>.py`, `--test-file` -> `tests/unit/test_<source-stem>.py`, `--ac-func` -> `"TBD: describe the functional outcome."`). The `--repo` flag also drives the `## Target Repository` section's `**Repo:**` line.

Templates ship under `backlog/templates/{epic,feature,story,task}.md` in the devbench repo and cross-link to `docs/acceptance-criteria-canonical.md` and `docs/source-test-atomicity.md` for authoring rules.

### `sync-blocked`

```
uv run devbench sync-blocked
```

Reconcile every task's status against current dependency satisfaction. Walks the parsed index and:

- **in-queue to blocked** (forward direction): flips `in-queue` Tasks whose dependencies are NOT satisfied to `blocked` (with a `[BLOCKED] sync-blocked: dependency '<id>' not yet terminal` audit comment naming the first offending dep).
- **blocked to in-queue** (reverse direction): flips `blocked` Tasks whose dependencies are now satisfied (every dep -- including epic / feature / story-level deps that recurse into descendants -- is `done` or `declined`) back to `in-queue` (with a `[UNBLOCKED] sync-blocked: dependencies now terminal` audit comment).

`sync-blocked` is **bidirectional**: it both blocks tasks whose deps are unmet and unblocks tasks whose deps are now satisfied. It operates on regular Dependencies-table rows only. The separate auto-unblock cascade for `[BLOCKED_PENDING_PROPOSAL]` markers fires automatically from `mark_done` via `_auto_requeue_marker_dependents` in `BacklogManager` -- that cascade triggers whenever the newly-done task is referenced either as a declared dependency OR as a `[BLOCKED_PENDING_PROPOSAL]` marker ID in the Comments section (issue #200 / AC-200-2).

Tasks whose status is anything other than `in-queue` or `blocked` (e.g. `in-progress`, `in-review`, `done`, `declined`, `hold`, `proposed`) are untouched by `sync-blocked`. Output is a JSON envelope of the form `{"flipped_to_blocked": [...], "flipped_to_in_queue": [...]}` for scripting.

Useful as a pre-flight sweep before `devbench next` (after manual edits to the backlog) and for triage when a backlog has drifted out of sync. Combine with `validate-backlog` for a complete consistency check.

### `scope`

```
uv run devbench scope set --include "<tokens>" [--exclude "<tokens>"]
uv run devbench scope clear
uv run devbench scope show
```

Persistent scope management without starting the orchestrator (spec section 4.2.6, issue #196). Writes, clears, or displays the active `<workspace>/.devbench/scope.json`. Useful for pre-arming a scope before launching interactive Claude Code so the orchestrate skill respects the filter without the operator having to launch and kill `devbench start` first.

**Subcommands:**

- **`scope set --include "<tokens>" [--exclude "<tokens>"]`** -- parse the printer-pages tokens, validate them against the current `BACKLOG.md`, and write `<workspace>/.devbench/scope.json` atomically (temp-then-rename). Out-of-range tokens emit a warning but do not fail (rc=0). The written `scope.json` is byte-identical to the one `devbench start --include "..."` would write -- subsequent `devbench start` / `devbench next` / `devbench status` / `devbench report` invocations honour it identically. Exits 0 on success; exits 1 with an actionable stderr message when a token is a reversed range or structurally malformed.

- **`scope clear`** -- delete `<workspace>/.devbench/scope.json`. Idempotent: exits 0 with the message `no scope pending` when no file is present.

- **`scope show`** -- print the active scope state (include list, exclude list, expanded ID count, `started_at`, `started_by`) or `no scope pending` when no scope file exists. Exits 0 in both cases.

**scope.json schema:**

```json
{
  "include": ["E1-E3", "E5"],
  "exclude": [],
  "expanded_ids": ["E1-F1-S1-T1", "..."],
  "started_at": "2026-05-14T13:42:11Z",
  "started_by": "matt"
}
```

The file lives at `<workspace>/.devbench/scope.json`. When `DEVBENCH_SESSION_NAME` is set (named sessions, spec section 4.4), the path is `<workspace>/.devbench/sessions/<name>/scope.json` instead.

**Interactive pre-arm workflow:**

```bash
# Pre-arm scope for epics E1 through E3 plus E5
DEVBENCH_WORKSPACE_ROOT=$PWD DEVBENCH_CLAUDE_MODEL=... \
  uv run devbench scope set --include "E1-E3, E5"

# Launch interactive Claude Code; the orchestrate skill respects the pre-armed scope.json
DEVBENCH_WORKSPACE_ROOT=$PWD DEVBENCH_CLAUDE_MODEL=... \
  claude --dangerously-skip-permissions --plugin-dir <devbench>/plugin/devbench

# Clear when done
uv run devbench scope clear
```

See [Scope selectors](#scope-selectors-printer-pages-syntax) for the full token syntax reference. For the step-by-step interactive pre-arm workflow, see [`docs/zero-to-ready.md` -- Scoping a run interactively](zero-to-ready.md#scoping-a-run-interactively).

### `start`

```
uv run devbench start [--include "<tokens>"] [--exclude "<tokens>"] [--name <name>] [--allow-overlap]
```

Run the orchestrate SKILL non-interactively via the Agent SDK. Invoked by `make start` (the recommended way to run DevBench). Loads the plugin ad-hoc from the devbench checkout; no global `make plugin-install` required. When the workspace's `backlog/config/devbench.yaml` declares an `agents:` block (see [`docs/adr/25-per-agent-model-overrides.md`](adr/25-per-agent-model-overrides.md)), `start` materialises a workspace-local shadow plugin tree at `<workspace>/.devbench/plugin-shadow/devbench/` and passes that path to the SDK in place of the canonical plugin.

**SDK teardown warning (issue #232):** The `sdk_teardown_filter` workaround that suppressed an asyncio teardown race in `claude-agent-sdk` is no longer present. The upstream fix landed in `claude-agent-sdk>=0.2.87` (issue #255); this project's SDK floor is now `>=0.2.87`, so the workaround is unnecessary.

**Scope filter flags:**

- `--include "<tokens>"` -- printer-pages-style include selector. Restricts the orchestrator to the named work units and their descendants. Accepts comma-separated tokens (single IDs or last-segment ranges). See [Scope selectors](#scope-selectors-printer-pages-syntax) for the full syntax.
- `--exclude "<tokens>"` -- subtract the matched IDs from the include set. Applied after include expansion.

When `--include` is supplied, the parsed scope is persisted to `<workspace>/.devbench/scope.json` atomically (temp-then-rename) before the orchestrate SKILL starts. The scope file is deleted on clean orchestrator exit; it survives orchestrator crashes and is visible to subsequent `devbench status` / `devbench report` invocations.

When `--include` is omitted (the default), all work units are eligible -- the existing behaviour is fully preserved.

**Named-session flags:**

- `--name <name>` -- assign the orchestrator session a unique name. The session creates a per-session state directory at `<workspace>/.devbench/sessions/<name>/` containing a `pid` file, `scope.json`, `drain.signal`, `orchestrator.log`, `report.json`, `started_at`, and `started_by` files. The session is registered in `<workspace>/.devbench/sessions/registry.json`. When `--name` is omitted, the session defaults to `default`, preserving the single-session behaviour exactly. The session name is exported as `DEVBENCH_SESSION_NAME` for all subprocesses, which drives per-session routing of logs, drain signals, and scope files. See [Named sessions](#named-sessions) for the full lifecycle reference.

- `--allow-overlap` -- by default, `devbench start` checks active sessions for scope overlap before registering the new session. If the new session's `expanded_ids` intersects any active session's scope, the command fails fast with a clear error naming the conflicting work unit IDs and the owning session(s). Pass `--allow-overlap` to skip this check and start the session anyway; a warning is printed listing the conflicting IDs. The atomic claim arbitration (`flock(BACKLOG.lock)`) resolves the race deterministically when two sessions attempt to claim the same work unit -- only one wins. Use `--allow-overlap` only when the operator has verified that the overlapping IDs are intended (for example, two read-only reporting sessions).

**Example -- scope to epics E1 through E3 plus E5:**

```bash
uv run devbench start --include "E1-E3, E5"
```

**Example -- scope to E1-E10, excluding E5 and everything under E7-F3:**

```bash
uv run devbench start --include "E1-E10" --exclude "E5, E7-F3"
```

**Example -- launch two named sessions with disjoint scopes:**

```bash
# Terminal 1: session "alpha" works on E1 through E3
uv run devbench start --name alpha --include "E1-E3"

# Terminal 2: session "beta" works on E4 through E6
uv run devbench start --name beta --include "E4-E6"
```

To pre-arm scope.json without immediately launching the orchestrator, use `devbench scope set` instead.

### `prepare-plugin-shadow`

```
uv run devbench prepare-plugin-shadow
```

Materialise the workspace-local shadow plugin (ADR-25) without launching anything and print its absolute path to stdout. Used by interactive launchers so the same per-agent model overrides apply when an operator drives the orchestrate skill manually:

```
claude --plugin-dir "$(uv run devbench prepare-plugin-shadow)"
```

When the workspace has no `agents:` overrides configured, prints the canonical plugin path; otherwise rewrites every overridden agent `.md` and symlinks the rest. Shares its implementation with `start`'s pre-flight so the two modes always produce identical plugin trees.

The YAML schema for the override block is shown below with each field set to the **current frontmatter default**. The defaults are tuned by the role each agent plays: `executor` (writes code under TDD) on `sonnet` for a fast happy path; the five judges (`code-reviewer`, `test-reviewer`, `doc-reviewer`, `changes-manifest`, `security-reviewer`) on `opus` because a bad verdict costs more than the inference savings; `blocker-resolver`, `manifest-amender`, `task-factory` on `opus` because they fire only on unhappy paths and a wrong call spins the recovery cascade; `review-supervisor` on `sonnet` (deprecated and inert after [ADR-28](adr/28-flatten-review-pipeline.md) -- the orchestrate skill now dispatches the four reviewers directly; the entry is retained only for config back-compat). `haiku` is rejected at config-load for all per-agent fields (caylent-solutions/devbench#198). Setting a field to its frontmatter default value is a no-op; flip individual fields when you need to retarget an agent (e.g., drop the judges to `sonnet` when opus quota is exhausted):

```yaml
agents:
  executor: sonnet
  blocker_resolver: opus
  manifest_amender: opus
  security_reviewer: opus
  task_factory: opus
  review_supervisor: sonnet
  review_team:
    code_reviewer: opus
    test_reviewer: opus
    doc_reviewer: opus
    changes_manifest: opus
```

Every field defaults to `null` when absent (use the agent's `.md` frontmatter model). When `use_bedrock: true`, every value must be a Bedrock ARN (`us.anthropic.claude-<name>-<ver>-v<N>`); when `false`, values must be a short name (`opus`/`sonnet`) or an Anthropic API id (`claude-opus-4-8`). `haiku` is rejected at config-load time for all per-agent fields (caylent-solutions/devbench#198). `DEVBENCH_AGENT_MODEL_<NAME>` env vars (e.g. `DEVBENCH_AGENT_MODEL_EXECUTOR=opus`, `JUDGE_AGENT_MODEL_CODE_REVIEWER=opus`) override the YAML on a per-call basis (env > yaml > frontmatter).

---

## Drain (graceful orchestrator stop)

Operator-initiated graceful stop of a running or about-to-start orchestrator. The drain protocol lets the orchestrator finish its current work unit, then exit cleanly rather than being killed mid-task. The mechanism is a JSON signal file at `<workspace>/.devbench/drain.signal` (or `<workspace>/.devbench/sessions/<name>/drain.signal` when a named session is active); the orchestrator polls the file between work units. The orchestrator's reader scans both candidate paths -- the per-session path first, then the workspace-root path as a fallback -- so a `devbench drain` issued from a shell with no `DEVBENCH_SESSION_NAME` env var is still observed by a session-scoped orchestrator (issue #212). The file is consumed (deleted) on orchestrator exit, and the `finally` clause additionally wipes any signal at either path on every exit (clean, drain-enforced, crash) so a subsequent `devbench start` never inherits a stale request. Spec source: `spec/devbench-self-improve.md` section 4.3. Issues #188 and #212.

### `drain`

```
uv run devbench drain [--reason "<text>"]
uv run devbench drain --cancel
uv run devbench drain --status
```

Request, withdraw, or inspect the drain signal. The bare form and `--reason` variant create the signal; `--cancel` removes it; `--status` is read-only and always exits rc=0.

**Variants:**

- **`devbench drain`** -- request a graceful stop with no reason. Writes `<workspace>/.devbench/drain.signal` with a JSON payload containing `requested_at` (UTC ISO 8601), `requested_by` (current `USER` / `USERNAME` env var, or `"unknown"`), and `reason` (empty string). The write is atomic (temp-then-rename) so readers never observe a partial file. Overwrites any existing signal. Exits 0 on success; filesystem failures propagate as unhandled exceptions (Python traceback to stderr).

- **`devbench drain --reason "<text>"`** -- same as the bare form, with a non-empty reason string embedded in the payload. The reason is stored verbatim and surfaced by `devbench status` and `devbench drain --status`.

- **`devbench drain --cancel`** -- withdraw the drain request. Deletes the drain signal from both the per-session path (when `DEVBENCH_SESSION_NAME` is set) and the workspace-root path so a writer at either location is cleared in one call (issue #212). Idempotent: exits 0 silently whether or not a signal file was present at either path. Cancelling while the orchestrator is mid-WU prevents the orchestrator from exiting at the next WU boundary -- it continues as if no drain was requested (AC-188-10). Filesystem failures propagate as unhandled exceptions.

- **`devbench drain --status`** -- print the current drain state and exit rc=0 in both cases:
  - Signal present: prints a one-line summary of the form `drain pending: requested_by=<user> at=<ISO-8601> reason=<reason-or-none>`.
  - No signal: prints `no drain pending`.

  The orchestrate skill calls `devbench drain --status` between Step 9 (mark-done) and Step 10 (loop back) so it can exit cleanly without a special sentinel file. rc=0 in both states lets the skill use the printed output as the discriminator rather than the exit code.

**How the orchestrator honours the drain signal:**

When the running orchestrator detects the drain signal between work units (either via the cooperative skill check or the SDK-wrapper poll in `cmd_start::_run`), it:

1. Finishes the current work unit (the WU in-progress reaches `done` or `blocked` first).
2. Consumes the signal (reads + deletes the marker file atomically from the orchestrator process's perspective).
3. Logs `[ORCHESTRATOR_DRAIN]` (cooperative skill path) or `[ORCHESTRATOR_DRAIN_ENFORCED]` (SDK-wrapper backstop) to the orchestrator log with the original reason.
4. Exits cleanly with rc=0.

The next `devbench start` runs without any drain restriction because the marker was consumed.

**Pre-arm pattern (AC-188-6):** dropping the marker BEFORE `devbench start` causes the orchestrator to claim and complete exactly one work unit, then exit. This is useful for a controlled single-step execution or for confirming a work unit runs cleanly before committing to a full unattended run.

**Exit codes:**

| Command | rc=0 | rc!=0 |
|---------|------|-------|
| `drain` | Signal written. | Filesystem failures propagate as unhandled OSError (Python traceback). |
| `drain --reason "<text>"` | Signal written. | Filesystem failures propagate as unhandled OSError (Python traceback). |
| `drain --cancel` | Signal removed or was already absent (silent, no output). | Filesystem failures propagate as unhandled OSError (Python traceback). |
| `drain --status` | Always (signal present or absent). | rc=2 on invalid argument combinations; startup errors (unset env vars) raise immediately. |

**Worked examples:**

```bash
# Request graceful stop with a reason:
uv run devbench drain --reason "nightly cutover"

# Check current drain state (rc=0 either way):
uv run devbench drain --status
# -> drain pending: requested_by=matt at=2026-05-14T13:55:01+00:00 reason=nightly cutover
# -- or --
# -> no drain pending

# Withdraw the request before the orchestrator picks it up:
uv run devbench drain --cancel
# (no output; exits rc=0 whether or not a signal was present)

# Pre-arm: drop drain before start so orchestrator runs exactly one WU then exits:
uv run devbench drain
uv run devbench start --include "E1-F2-S3-T4"
# -> orchestrator claims E1-F2-S3-T4, completes it, detects drain, exits rc=0
```

**Status banner:** when a drain signal is present, `devbench status` prepends a `DRAIN REQUESTED: at <ts> by <user> (reason: <text>)` banner above the Status Summary so the operator can see the pending drain at a glance. See the [`status`](#status) section for the full banner format. `devbench report` renders the same banner ahead of the report body, so the pending drain is visible from either surface.

---

## Named sessions

Named sessions let multiple independent orchestrator processes run concurrently against the same workspace without corrupting the shared backlog. Each session operates on a disjoint scope, writes to per-session log and drain files, and is registered in a shared `registry.json` so operators can inspect and manage running sessions. Spec source: `spec/devbench-self-improve.md` section 4.4. Issue #192. ADR-23.

Per-session state lives under `<workspace>/.devbench/sessions/<name>/`:

| File | Purpose |
|------|---------|
| `pid` | The orchestrator process's PID. Used for liveness checks and SIGTERM delivery. |
| `scope.json` | Session-scoped scope filter (overrides workspace-root `scope.json`). |
| `drain.signal` | Session-scoped drain marker. Takes priority over workspace-root `drain.signal` when both exist; if the session path is empty, the reader falls through to workspace-root so an operator's `devbench drain` (no session env var) is still observed (issue #212). |
| `orchestrator.log` | Per-session log, written in addition to the aggregate `<workspace>/logs/orchestrator.log`. |
| `report.json` | Session-scoped report cache. |
| `started_at` | ISO 8601 UTC timestamp of session start. |
| `started_by` | OS user that launched the session (`USER` / `USERNAME` env var, or `"unknown"`). |

The `default` session name is applied implicitly when `--name` is omitted from `devbench start`; single-session operators see no behaviour change.

**[WU_CLAIMED] audit format extension (spec 4.4.7):** when `DEVBENCH_SESSION_NAME` is set (an active named session), the work-unit audit comment written at claim time extends to:

```
[WU_CLAIMED] Set <id> to 'in-progress' session=<name>
```

When `DEVBENCH_SESSION_NAME` is unset (single-session legacy behaviour), the comment format is unchanged:

```
[WU_CLAIMED] Set <id> to 'in-progress'
```

### `sessions`

```
uv run devbench sessions [--cleanup]
```

List all registered orchestrator sessions and their liveness state. Each row in the output includes: session name, PID, scope (included ID count or the raw token list), `started_at` timestamp, drain state (`pending` or `none`), and a liveness indicator (`ACTIVE` when the process is running; `STALE` when the PID is no longer alive).

- **`devbench sessions`** (no flags) -- print a table of all sessions currently in `<workspace>/.devbench/sessions/registry.json`. Stale sessions are listed but not removed, so operators can review them before cleanup. Exits 0 in all cases, even when no sessions are registered (prints `no sessions registered`).

- **`devbench sessions --cleanup`** -- remove session directories whose `pid` file references a non-running process. Stale session entries are removed from `registry.json` and the corresponding `<workspace>/.devbench/sessions/<name>/` directory is deleted. Active sessions are left untouched. Prints one line per removed session (`[CLEANED] session <name> (pid <N>)`). Exits 0 on success.

  **Dead-session orphan recovery.** A session that dies without a clean SIGTERM stop (crash, OOM, host reboot, `kill -9`) leaves the unit it had set to `in-progress` (`[WU_CLAIMED] session=<name>`) stuck there forever, blocking dependents and tripping the Stop hook on every later session. After removing the stale entries, `--cleanup` re-queues every `in-progress` Task whose most recent `[WU_CLAIMED]` audit names a now-dead session, appending an explicit `[REQUEUED_AFTER_DEAD_SESSION] session=<name>` audit comment (no manual `set-status` needed). The recovery cross-checks pid liveness against the surviving registry, so a unit a **live** session holds in scope is never re-queued. Any staged WIP the dead session left in the target checkout's index is unstaged (edits stay in the working tree) so a later commit cannot sweep it in. Prints `Re-queued N orphaned in-progress unit(s) from dead session(s): <ids>` when any unit is recovered.

**Exit codes:**

| Command | rc=0 | rc!=0 |
|---------|------|-------|
| `sessions` | Success (zero or more sessions listed). | Startup errors (unset env vars) raise immediately. |
| `sessions --cleanup` | Success (zero or more stale sessions removed). | Filesystem errors propagate as unhandled OSError. |

**Worked examples:**

```bash
# List all registered sessions:
uv run devbench sessions
# Output (example):
#   NAME     PID    SCOPE        STARTED_AT              DRAIN    LIVENESS
#   alpha    12345  E1-E3 (42)   2026-05-14T13:42:00Z    none     ACTIVE
#   beta     99999  E4-E6 (37)   2026-05-14T13:50:00Z    none     STALE

# Remove stale sessions:
uv run devbench sessions --cleanup
# -> [CLEANED] session beta (pid 99999)
```

### `stop`

```
uv run devbench stop --session <name>
```

Send SIGTERM to a running session's orchestrator process, forcing it to exit after the in-flight work unit is marked `blocked`. The SIGTERM is delivered via the session's `pid` file located at `<workspace>/.devbench/sessions/<name>/pid`.

**What happens when stop runs:**

1. `devbench stop` reads the session's `pid` file.
2. Sends SIGTERM to the process.
3. The SIGTERM handler in `cmd_start` intercepts the signal, writes a `[FORCED_BLOCKED_ON_STOP] session=<name>` audit comment to the in-flight work unit, marks the work unit `blocked`, and exits with rc=0. If the unit was interrupted mid-git-ops (after `git add` but before the commit), any staged WIP left in the target checkout's index is unstaged (edits stay in the working tree, recoverable when the unit is re-claimed) so a subsequent commit in the same checkout cannot sweep those files in under the wrong unit/message.
4. The session directory is NOT cleaned up automatically -- run `devbench sessions --cleanup` afterward to remove the stale entry.

**Flags:**

- `--session <name>` -- REQUIRED. The name of the session to stop. Exits 1 with an actionable error when the session does not exist in the registry, the `pid` file is missing, or the process is not running.

**Exit codes:**

| Scenario | rc |
|----------|----|
| SIGTERM delivered successfully. | 0 |
| Session not found in registry. | 1 |
| PID file missing or unreadable. | 1 |
| Process already exited (stale session). | 1 (with actionable message; run `devbench sessions --cleanup`). |
| `--session` flag omitted. | 2 (argument-parse error). |

**Worked example:**

```bash
# Stop the session named "alpha" and block its in-flight work unit:
uv run devbench stop --session alpha
# The orchestrator for session "alpha" receives SIGTERM, blocks its WU, and exits.
# The in-flight WU now carries: [FORCED_BLOCKED_ON_STOP] session=alpha

# Clean up the stale session entry:
uv run devbench sessions --cleanup
```

---

## Supervise (interactive billing-mode orchestrator)

`devbench supervise` launches the orchestrator as an interactive `claude` CLI session under a detached `screen` daemon driven by a `pexpect` supervisor, so the run is unattended, survives terminal detach, and bills via the channel selected by `--billing-mode`: `subscription` (default; the Claude Code subscription's rolling 5-hour windows) or `bedrock` (AWS Bedrock, always-on, no 5-hour windows). AWS workload creds pass through in both modes. It is a NEW, purely additive verb group; the `devbench start` SDK path is untouched. Full operator guide: [supervise.md](supervise.md). Design rationale: ADR-31 ([adr/31-interactive-screen-supervisor.md](adr/31-interactive-screen-supervisor.md)).

The dispatcher is `devbench supervise <sub-verb>`; an unknown sub-verb exits 2 with the usage listing the six sub-verbs.

### `supervise start`

```
uv run devbench supervise start [--name N] [--include "<tokens>"] \
    [--exclude "<tokens>"] [--allow-overlap] [--model M] [--effort E] \
    [--billing-mode {subscription,bedrock}]
```

Runs the preflight, writes the per-session `scope.json`, creates the `screen` (`devbench-supervise-<name>`), launches `claude --model <m> --effort <e> --dangerously-skip-permissions --plugin-dir <resolved>`, waits for the ready prompt, injects `/devbench-orchestrate:orchestrate`, and transitions the session to `running`.

- `--name N` -- session name (default `default`); alphanumerics, `-`, `_` (rejects `..`).
- `--include "<tokens>"` / `--exclude "<tokens>"` -- scope tokens (printer-pages syntax); empty include = the entire backlog.
- `--allow-overlap` -- permit scope overlap with active sessions.
- `--model M` -- model (`opus|sonnet|claude-opus-4-8|...`); resolves `--model` > `supervise.model` > `orchestrate.model`, fail-fast if all unset; `haiku` rejected.
- `--effort E` -- `low|medium|high|xhigh|max` (default `supervise.effort`, `xhigh`).
- `--billing-mode {subscription,bedrock}` -- billing channel; resolves `--billing-mode` > `DEVBENCH_SUPERVISE_BILLING_MODE` env > `supervise.billing_mode` config > default `subscription`. `subscription` bills the Claude Code Max subscription (5-hour windows, quota wait engaged); `bedrock` bills via AWS Bedrock (no 5-hour windows, quota wait disabled). An invalid value fails fast.

**Preflight (fail-fast, exit 2):** `screen` present, non-root, model resolvable, plus mode-specific checks. In `subscription` mode: subscription auth present and NO Claude-to-API/Bedrock routing var (`ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`/...) in env. In `bedrock` mode: AWS Bedrock prerequisites present (an AWS credential among `AWS_ACCESS_KEY_ID`/`AWS_PROFILE`/`AWS_BEARER_TOKEN_BEDROCK`, plus `AWS_REGION`/`AWS_DEFAULT_REGION`). AWS workload creds pass through in both modes and are never a routing violation. Exit 0 only when the session reaches `state=running`.

### `supervise stop`

```
uv run devbench supervise stop [--name N] [--hard]
```

Graceful (default): writes the per-session `drain.signal`, lets the in-flight work unit finish, captures the `claude` session id, sends the drain command, and quits the screen. `--hard`: terminates `claude` + screen immediately. Exit 0 on stop; exit 2 if no such session.

### `supervise restart`

```
uv run devbench supervise restart [--name N]
```

Graceful `stop` then relaunch preserving context via `--continue` (or `--resume <id>` when a session id was captured). Bounded by `supervise.restart.max_attempts`. Exit 0 on relaunch; non-zero on relaunch failure.

### `supervise status`

```
uv run devbench supervise status [--name N]
```

With `--name`: one session; without: all supervise sessions. Columns: `name`, `state` (`starting|running|quota-waiting|draining|stopped|errored|restarting`), `in-progress`, `last-activity`, `screen`, `claude-session`, `billing-channel` (`subscription` or `bedrock`), `exit-reason`; `quota-waiting` also shows `expected-resume` and `resumes-used` (subscription mode only). Exit 0; exit 2 if `--name` unknown.

### `supervise info`

```
uv run devbench supervise info
```

Joins `screen -ls` with the registry and lists every supervise screen with SCREEN, NAME, STATE, PID, CLAUDE-SESSION, BILLING, and the exact `supervise attach --name N` command. Exit 0.

### `supervise attach`

```
uv run devbench supervise attach [--name N] [--screen]
```

Default: follow the redacted PTY transcript read-only (stdin is never wired to the child, so it cannot inject input or steal the PTY). Ctrl-C stops watching; the orchestration is untouched. `--screen` (input-capable `screen -x`) is gated off and fails fast (exit 2) until DI-4 verifies the write-removed ACL on the target `screen` build. Exit 0; exit 2 if no such session.

**Exit codes (all supervise verbs):**

| Scenario | rc |
|----------|----|
| `start` reaches `state=running`; `stop`/`restart`/`status`/`info`/`attach` succeed. | 0 |
| Preflight/argument failure, unknown sub-verb, unknown `--name`, scope overlap without `--allow-overlap`, `--screen` while gated. | 2 |
| Launch/runtime fault (crash, prompt-timeout, harness-self-edit block, restart/quota-resume cap exhausted). | non-zero classified code |

---

## Scope selectors (printer-pages syntax)

The `--include` and `--exclude` flags on `devbench start`, `devbench status`, `devbench report`, and `devbench next` all accept the same printer-pages-style token syntax described here. `devbench scope set` uses the same parser.

### Token types

Tokens are comma-separated strings. Whitespace around commas is ignored. Evaluation order: the include set is expanded first; then the exclude set is expanded and subtracted from it.

#### Single-ID token

A single-ID token matches the exact work-unit ID and every descendant. Descendants are IDs that start with `<token>-`.

Examples:

| Token | Matches |
|-------|---------|
| `E5` | `E5`, `E5-F1`, `E5-F1-S2`, `E5-F1-S2-T3`, ... (all WUs under epic E5) |
| `E5-F1` | `E5-F1`, `E5-F1-S1`, `E5-F1-S1-T1`, ... (all WUs under feature E5-F1) |
| `E5-F1-S2-T3` | `E5-F1-S2-T3` only (leaf task; no descendants) |

#### Range token

A range token consists of two adjacent same-type segments at the end of the token (both sharing the same letter prefix such as `E`, `F`, `S`, or `T`, but differing in the numeric suffix). The range expands inclusively on the final segment; earlier segments must match exactly.

Syntax: `<common-prefix><type><start>-<type><end>` where `start <= end`.

Examples:

| Token | Matches |
|-------|---------|
| `E1-E3` | all WUs under epics E1, E2, and E3 |
| `E5-F1-F3` | all WUs under features E5-F1, E5-F2, and E5-F3 |
| `E5-F1-S1-T2-T5` | tasks E5-F1-S1-T2, T3, T4, T5 and their descendants |

#### Mixed comma-separated list (union)

Multiple tokens are joined as a union. The result is the union of all matched IDs, then subtracted by the exclude set.

Example:

```bash
--include "E1-E3, E5"
# matches: all WUs under E1, E2, E3, and E5
```

### Exclude subtraction

`--exclude` tokens are expanded the same way as `--include` tokens. The expanded exclude set is then subtracted from the expanded include set. Evaluation order:

1. Expand all `--include` tokens (or use all backlog IDs when `--include` is empty).
2. Expand all `--exclude` tokens.
3. Subtract the exclude set from the include set.

Example:

```bash
--include "E1-E10" --exclude "E5, E7-F3"
# include set: all WUs under E1 through E10
# exclude set: all WUs under E5 + all WUs under E7-F3
# result: all WUs under E1-E4, E6, E7 (except E7-F3 descendants), E8-E10
```

### Edge cases

- **Reverse range** (`E3-E1`, `T5-T2`): rejected immediately with an actionable error message naming the token and the required ascending order. Exit code 1.
- **Out-of-range token** (no matching WU in the backlog): emits a `WARNING` log line naming the token but does not abort. The run continues with the matched IDs from other tokens.
- **Empty `--include`** (flag omitted): all backlog IDs are included before any exclusions. The existing behaviour is fully preserved (AC-190-9).
- **Malformed token** (leading/trailing/consecutive hyphens, e.g. `-E1`, `E1-`, `E1--E3`): rejected immediately with an actionable error message. Exit code 1.

### scope.json persistence

When `devbench start --include "..."` or `devbench scope set --include "..."` runs, the parsed scope is persisted to `<workspace>/.devbench/scope.json`:

```json
{
  "include": ["E1-E3", "E5"],
  "exclude": [],
  "expanded_ids": ["E1-F1-S1-T1", "..."],
  "started_at": "2026-05-14T13:42:11Z",
  "started_by": "matt"
}
```

- Written atomically (temp-then-rename) so concurrent readers never see a partial file.
- Consumed (deleted) on clean orchestrator exit (`devbench start` clean shutdown). Survives orchestrator crashes.
- `devbench status`, `devbench report`, and `devbench next` consult the file automatically when no per-command `--include`/`--exclude` flags are supplied. Per-command flags override the file for that invocation only.
- `devbench validate-backlog` ignores `scope.json` entirely -- it always validates the whole backlog regardless of active scope.

---

## Orchestration and reporting

See the [report](#report), [watch](#watch), [hook-tail](#hook-tail), and [watchdog](#watchdog) entries under Backlog read.

The main entry point for running the orchestrator is `make start` (non-interactive, recommended). Live observation while non-interactive runs is available via `devbench hook-tail` (every tool call streamed), `devbench report` (live progress dashboard), and `devbench status` -- so opening interactive mode just to see what's happening is unnecessary. `make start-interactive` exists for guided walk-throughs but should not be used to intervene mid-claim. Corrections happen between runs through two tools: the `devbench` CLI for state transitions / dep wiring (`set-status`, `decline`, `add-dep`, etc.), and Claude (separate session) for editing the work-unit `.md` content (Approach, Manifest, ACs, new work units). See [README Interactive Mode](../README.md#interactive-mode) and [`zero-to-ready.md`](zero-to-ready.md) Step 10 for the full split.

---

## Orchestrator helpers (invoked by agents)

These commands are the API surface the orchestrate SKILL and its subagents use. Operators rarely call them directly; they appear in hook-log traces and in audit comment lines. All respect the same env-var and config-path contract as operator commands.

### `get-diff`

```
uv run devbench get-diff <id>
```

Print the combined git diff for the work unit's target repo, scoped to *what this work unit changed*. Used by review agents (all five review judges plus `security-reviewer`) as the authoritative scope source; agents must not run raw `git diff origin/main` to compute scope (see ADR-12).

Mode-aware per ADR-12:

- **Per-task-branch mode** (default, `git_ops.defer_pr: false`): emits staged + unstaged + `git diff origin/<default_branch>` + untracked hunks. Each work unit runs on its own branch, so the branch-vs-default diff IS the task's scope.
- **defer_pr mode** (`git_ops.single_branch: <branch>` + `git_ops.defer_pr: true`): emits staged + unstaged + untracked only. When staged and unstaged are both empty the executor has just committed; the command performs a task-attributed commit lookup via `git log --grep "^<unit-id>:"` and emits those commit diffs. If no task-attributed commit is found, exits `GET_DIFF_NO_ATTRIBUTABLE` (45) with a verbatim diagnostic on stderr. The branch-vs-default hunk is deliberately skipped because it would include every prior completed task's commits on the shared branch.

Exit 0 on success; exit 1 when the work unit is not found or no local path is configured for its repo. Output is `(no changes)` when every hunk is empty.

### `run-tests`

```
uv run devbench run-tests <id>
```

Run the test suite in the work unit's target repo. Uses the repo's `make test` target when present; falls back to bare `pytest`. Used by `test_review`. Returns the test runner's exit code.

### `verify-ac`

```
uv run devbench verify-ac <id>
```

Execute the work unit's `## Verification` contract and record tool-captured evidence (ADR-27). Parses the `## Verification` section, then for every *executable* directive (skipping `type=judge` and `type=deferred`) runs the command in the unit's target repo via `bash -c`, capturing the command's REAL exit code -- never a self-reported one. Each result is trimmed to `limits.ci_failure_log_bytes` and written to a per-AC artifact `.devbench/evidence/<id>/<attempt>/<sanitized-ac>.log`; all results are aggregated into the ledger `.devbench/evidence/<id>/<attempt>/evidence.json` (a JSON list of evidence records), and the attempt is recorded in `.devbench/evidence/<id>/latest.json` so the done-gate loads the latest run. The runner also invokes the deterministic TDD genuine-RED gate, preferring the tool-captured RED exit code from the freshly written ledger over the executor's self-reported `log-tdd RED` value. A **verification-only** unit -- one whose `## Changes Manifest` is made up entirely of sentinel rows (e.g. `<verification-only>`) and therefore authors no source -- is structurally exempt from the genuine-RED gate: it only runs live verification against an already-green target, so a RED with `Exit: 0` is legitimate and both gate checks are waived. The waiver is granted ONLY by a sentinel-only Manifest; any real file path in the Manifest restores the full gate.

Run by the executor after implementation and before review (orchestrate step 4d). Exit `0` when every executable Acceptance Criterion met its `expect-exit` and the TDD gate passed; non-zero otherwise. A unit with no `## Verification` section produces an empty ledger and exits `0`. `mark-done` re-checks this ledger: a unit declaring a `## Verification` section cannot reach done until every executable AC has a tool-captured exit-0 record (deferred ACs block unless `done_gate.allow_deferred_evidence: true`).

**Deterministic gate ordering (`DEVBENCH_VERIFY_AC_PYTEST_SEED`).** Each executable directive runs with a pinned, reproducible pytest ordering seed overlaid on the inherited environment: `PYTHONHASHSEED` and `pytest-randomly`'s `--randomly-seed` (via `PYTEST_ADDOPTS`) are set to the configured seed. This makes a unit's per-unit pytest gate a deterministic function of the code under test -- the same input yields the same verdict on every run. Without it, a target repo using `pytest-randomly` (random order per run) can pass an order-dependent sibling test on one wall-clock seed and fail it on the next, non-deterministically blocking an otherwise-complete, unrelated unit. The inherited environment is preserved (PATH etc. still resolve the toolchain); only the two ordering knobs are overlaid. The seed is configurable via `DEVBENCH_VERIFY_AC_PYTEST_SEED` (a non-negative integer; fail-safe default in `constants.DEFAULT_VERIFY_AC_PYTEST_SEED`); rotate it for a one-off reproduction. The orthogonal randomized-order signal (catching cross-unit order-dependence) belongs to the epic-capstone / CI full-suite gate (`AC-FINAL-016`), never to a random per-unit block; see `docs/acceptance-criteria-canonical.md` "Per-unit gate vs. epic-capstone / CI gate".

### `log`

```
uv run devbench log <message>
```

Append a free-form message to the orchestrator log. The destination path is resolved via `setup_logging` (`DEVBENCH_LOG_FILE` > `log_file:` in `backlog/config/devbench.yaml` > `<DEVBENCH_WORKSPACE_ROOT>/logs/orchestrator.log` > source-tree default), so `devbench log` and `devbench report` always agree on the file. Not audited to the work-unit file. Useful for emitting narrative breadcrumbs from agents.

### `log-verdict`

```
uv run devbench log-verdict <judge> <id> <pass|fail> [feedback]
```

Record a judge verdict as an audit comment on the work-unit file. Writes `[REVIEW_PASS]` or `[REVIEW_FAIL]` (or `[SECURITY_FAIL]` for `security_review`). Feedback is mandatory when the verdict is `fail`; rejected by the `guard-verdict-format.sh` hook otherwise.

`<judge>` must be one of the names in the allowlist defined by `devbench.constants.KNOWN_JUDGE_NAMES`. The allowlist is split into three tiers:

- **Core canonical reviewers (5)** -- `code_review`, `test_review`, `doc_review`, `changes_manifest`, `security_review`. These five are always-on and non-disableable; they always satisfy (and are always required by) the done-gate's `BacklogManager._last_round_all_passed` check. The first four are written by the four `review_team` reviewers (`code-reviewer`, `test-reviewer`, `doc-reviewer`, `changes-manifest`), dispatched directly by the orchestrate skill after [ADR-28](adr/28-flatten-review-pipeline.md); `security_review` is written by `security-reviewer`.
- **Optional specialty reviewer (1)** -- `iac_review`. A canonical (done-gate-satisfying) reviewer verdict written by the `iac-deploy-reviewer` agent. It is required for a unit only when the operator enabled it (`optional_judges.iac_review: true`) AND the unit's `## Verification` contract makes it applicable (`unit_requires_iac_judge`); otherwise it is never dispatched or required.
- **Audit-only workflow agents (4)** -- `executor`, `blocker_resolver`, `manifest_amender`, `task_factory`. Their verdicts land in the work-unit Comments section as audit metadata but do NOT count toward the done-gate. Workflow agents use these to record progress (for example, the executor logging `executor` verdicts during AC enforcement, or task-factory recording `task_factory` after a successful materialise).

Two enforcement layers prevent malformed audit rows:

1. **CLI layer** (`cmd_log_verdict`): refuses any `<judge>` outside `KNOWN_JUDGE_NAMES` with a clear error naming the valid choices. Catches typos like `judge` (literal) or hyphenated forms like `code-reviewer`.
2. **Hook layer** (`guard-verdict-format.sh`, PreToolUse): mirrors the same allowlist, plus an **agent-type default-deny** rule (H3) -- a canonical reviewer verdict may only be written by an allowlisted reviewer `agent_type` AND only when the per-round token file is present and unit-scoped. The four `review_team` reviewers are registered by Claude Code under their subdirectory namespace, so the allowlisted agent types are the `review_team:`-infixed forms `devbench-orchestrate:review_team:code-reviewer`, `:review_team:test-reviewer`, `:review_team:doc-reviewer`, and `:review_team:changes-manifest` (the registered, load-bearing slugs per the ADR-28 registered-slug postmortem; the flat `devbench-orchestrate:code-reviewer` forms are retained as defensive cross-version coverage), plus `devbench-orchestrate:security-reviewer`, `devbench-orchestrate:iac-deploy-reviewer`, and the deprecated `devbench-orchestrate:review-supervisor`. The second factor is a per-round, unit-scoped token read from the file `<workspace>/.devbench/review-round-token` (written by `devbench review-token new <unit-id>`, removed by `devbench review-token clear`; [ADR-29](adr/29-file-based-review-token.md)) -- it must exist, be non-empty, and begin with `<unit-id>-` (ADR-28 round-awareness). This file replaces the former `DEVBENCH_REVIEW_ROUND_TOKEN` env var entirely (its `BASH_ENV` transport was never implemented in code and failed twice in production). The executor and every other agent type are blocked from canonical verdicts; the audit-only `executor` judge name remains allowed (records progress without satisfying the gate).

Override env var: none -- this is a security/correctness gate, not a tunable. If a legitimate use case needs to write a verdict outside the allowlist, extend `KNOWN_JUDGE_NAMES` in `src/devbench/constants.py` AND update `KNOWN_JUDGES` in `plugin/devbench-orchestrate/scripts/guard-verdict-format.sh` (the two lists must stay in sync).

### `log-comment`

```
uv run devbench log-comment <agent> <id> <message>
```

Append a non-verdict agent comment to the work-unit file's `## Comments` section with a timestamp and agent name prefix. Blocked by the `guard-comment-format.sh` hook when the message contains control-language imperatives (for example `halt orchestration`, `operator action required`); see [docs/faq.md](faq.md) for the rule and rationale.

### `log-tdd`

```
uv run devbench log-tdd <id> <RED|GREEN|REFACTOR> <message>
```

Append a TDD phase entry to the work-unit's `## TDD Cycle Log` section. Enforces the phase token (must be `RED`, `GREEN`, or `REFACTOR`, case-insensitive).

### `review-token`

```
uv run devbench review-token new <unit-id>
uv run devbench review-token clear
```

Manage the file-based per-round review token ([ADR-29](adr/29-file-based-review-token.md)) -- the H3 second factor that `guard-verdict-format.sh` requires before any canonical reviewer verdict.

- `review-token new <unit-id>` writes a fresh `<unit-id>-r<n>-<rand>` token to `<workspace>/.devbench/review-round-token` (mode `0600`) and prints it. `<n>` is the per-unit round number from a monotonic counter persisted in `<workspace>/.devbench/review-round-counters.json` (incremented on each `new` for that unit); `<rand>` is `secrets.token_hex(6)`. Fails fast (rc=1) on a missing or empty unit id.
- `review-token clear` removes the token file and reports whether one was present.

The orchestrate skill calls `review-token new <id>` at the start of each review round (step 5a) -- the token covers the four `review_team` reviewers plus the step-7 security reviewer and step-7b iac reviewer in the same round -- and `review-token clear` at the end of the round (step 5d). The guard reads `<workspace>/.devbench/review-round-token` directly (resolving the workspace from `DEVBENCH_WORKSPACE_ROOT`) and requires the token to exist, be non-empty, and begin with `<unit-id>-`. This file replaces the former `DEVBENCH_REVIEW_ROUND_TOKEN` env var entirely; there is no override env var (it is a security/correctness gate, not a tunable).

---

## Git operations

### `ensure-branch`

```
uv run devbench ensure-branch <id>
```

Create or switch to the work-unit branch before the executor runs. Branch name resolves from the work-unit file's `- **Branch:** ...` field; defaults to `backlog/<id-lower>`. In single-branch mode, switches to the configured `single_branch` instead. Handles dirty trees via stash-pop.

Under `git_ops.local_only: true`, branch creation skips `git fetch origin` entirely and creates the new branch off the **local** default ref (`refs/heads/<default_branch>`). The YAML-configured `default_branch` is mandatory in this mode; there is no `origin/HEAD` fallback.

### `git-ops`

```
uv run devbench git-ops <id>
```

Commit, push, create PR, wait for CI, merge. The full git-ops sequence runs after every review judge passes and before `mark-done`. In single-branch + `defer_pr: true` mode, commits locally only (no push, no PR); the shared branch is pushed by `git-ops-finalize` after every unit is done. Under `git_ops.local_only: true` (which requires `defer_pr: true`), git-ops also commits locally only -- and `git-ops-finalize` is **never** run, since there is no remote to push to.

Enforces three deterministic safety rails:
- **Manifest-scope:** staged files must exactly match the work unit's Changes Manifest (AC-FINAL-015).
- **Branch-anchor:** HEAD must be on the expected branch (prevents orphan-branch commits).
- **Orphan-pattern:** no staged or already-tracked path may match a build/state ignore pattern (terraform state, terragrunt cache, Python pycache, coverage artefacts, `node_modules`, `.DS_Store`). The default behaviour (Phase 1 of the orphan-cascade fix) is **inline cleanup**: git-ops runs `cleanup_tracked_orphans` programmatically, commits the result as a devbench-authored chore commit (canonical message `chore(cleanup): untrack devbench-managed orphan paths and update .gitignore`), then continues with the original task's commit on the same invocation. Two commits land on the task's branch; the executor's staging is preserved (filtered to exclude orphan paths). When the operator sets `DEVBENCH_DISABLE_INLINE_ORPHAN_CLEANUP=1`, git-ops falls back to the legacy proposal flow (cleanup-as-task) with cross-task de-duplication so two parents detecting the same orphan set wire to the SAME cleanup task. Override the active pattern list per backlog via `DEVBENCH_ORPHAN_IGNORE_PATTERNS` (comma-separated fnmatch globs).

Each rail exits 1 with a clear diagnostic when violated.

#### Exit code contract

The orchestrator skill ([`plugin/devbench-orchestrate/skills/orchestrate/SKILL.md`](../plugin/devbench-orchestrate/skills/orchestrate/SKILL.md) step 8) handles each non-zero exit code distinctly:

| rc | Meaning |
|----|---------|
| 0 | PR merged (or commit landed locally in deferred mode). |
| 1 | Hard failure -- block the task with a `[BLOCKED]` audit comment. |
| 2 | CI failed; executor retry budget not exhausted. Audit comment `[CI_FAIL]` names the trimmed log under `.devbench/ci-failures/<id>-<n>.log`. Re-invoke the executor with `ci-fail` feedback, then re-run git-ops. (Issue #115; **default on**. Disable via `git_ops.ci_failure_retry: false` in `devbench.yaml` or env `DEVBENCH_CI_FAILURE_RETRY_ENABLED=0`.) |
| 3 | PR has unresolved review feedback; executor retry budget not exhausted. Audit comment `[PR_BOT_FAIL]` names the JSON feedback file under `.devbench/pr-bot-feedback/<id>-<n>.json`. Re-invoke the executor with `pr-bot` feedback, then re-run git-ops. (Issue #116; opt-in. Enable via `git_ops.pr_review_resolution.enabled: true` in `devbench.yaml` or env `DEVBENCH_PR_REVIEW_RESOLUTION_ENABLED=1`.) |

The retry budget for rc=2 / rc=3 is shared with the existing review-judge retry budget (`MAX_RETRY_ATTEMPTS`); when exhausted, git-ops returns rc=1 instead of 2/3 and writes a `[CI_FAIL_BLOCKED]` / `[PR_BOT_FAIL_BLOCKED]` marker so the operator sees the full failure surface.

Every toggle below resolves with **env > YAML > default** precedence. Boolean env values are case-insensitive: truthy = `1`/`true`/`yes`/`on`; falsy = `0`/`false`/`no`/`off`. Any other value fails fast at process start with a `ValueError`.

#### CI-failure retry (issue #115, default on)

Default-on as of v-next; opt out via `git_ops.ci_failure_retry: false` in `devbench.yaml` (or env `DEVBENCH_CI_FAILURE_RETRY_ENABLED=0`). When `wait_for_checks_and_classify` returns a non-GREEN `CIResult` (i.e. `FAILED_KNOWN_TASK`, `FAILED_UNKNOWN`, or `TIMEOUT`):

1. `gh pr checks --json name,state,link` identifies the failing run.
2. `gh run view <run-id> --log-failed` fetches the log; the trailing `DEVBENCH_CI_FAILURE_LOG_BYTES` bytes (default 32 KiB) are saved to `.devbench/ci-failures/<task-id>-<attempt>.log`.
3. A `[CI_FAIL]` audit comment names the log path; rc=2 signals the orchestrator to re-invoke the executor.
4. After `MAX_RETRY_ATTEMPTS` retries the path transitions to `[CI_FAIL_BLOCKED]` + rc=1.

#### PR review-comment polling (issue #116, opt-in)

Configure via YAML `git_ops.pr_review_resolution:` block (every sub-field
overridable via the env vars below). Or stay env-only:

Set `DEVBENCH_PR_REVIEW_RESOLUTION_ENABLED=1` AND configure at least one signal (a non-empty `DEVBENCH_PR_REVIEW_AGENTS` allowlist or `DEVBENCH_PR_REVIEW_DECISION_BLOCKS=1`) to enable. After `wait_for_checks_and_classify` returns `CIResult.GREEN`, git-ops polls `gh pr view --json reviewDecision,reviews` and `gh api repos/<repo>/pulls/<n>/comments` for up to `DEVBENCH_PR_REVIEW_SETTLE_SECONDS` seconds (default 60), polling every `DEVBENCH_PR_REVIEW_POLL_INTERVAL` seconds (default 5). The poll exits early on the first signal; otherwise the merge proceeds. Knobs:

| env var | default | purpose |
|---------|---------|---------|
| `DEVBENCH_PR_REVIEW_RESOLUTION_ENABLED` | unset (off) | top-level toggle |
| `DEVBENCH_PR_REVIEW_AGENTS` | empty | comma-separated bot login allowlist (e.g. `github-copilot[bot],amazon-q-developer[bot]`) whose unresolved comments block merge |
| `DEVBENCH_PR_REVIEW_DECISION_BLOCKS` | True | whether `reviewDecision == CHANGES_REQUESTED` blocks merge |
| `DEVBENCH_PR_REVIEW_SETTLE_SECONDS` | 60 | total poll budget |
| `DEVBENCH_PR_REVIEW_POLL_INTERVAL` | 5 | per-poll cadence |

#### Workflow-registration race defence (issue #114)

The `wait_for_checks_and_classify` step that runs between `gh pr create` and `gh pr merge` no longer treats `gh pr checks --watch` returning `"no checks reported"` as an unconditional pass. The previous behaviour merged before GitHub Actions had a chance to enqueue the workflow when CI was actually configured. The new disambiguation:

- **Repo has no `.github/workflows/*.y[a]ml` files locally**: legitimate "no CI configured" -> pass immediately (legacy fast path).
- **Repo has at least one workflow file**: race condition. Retry `gh pr checks` up to `DEVBENCH_CHECK_REGISTRATION_RETRIES` times (default 12), sleeping `DEVBENCH_CHECK_REGISTRATION_DELAY_SECONDS` between attempts (default 5). 12 * 5 = 60s of default coverage for the GitHub Actions queue.
- **Retry exhausted**: refuse the merge with an actionable error naming the PR number, the elapsed wait, and the workflow files found. No warn-and-pass fallback.

Operators with unusual CI cadence override the knobs via the env vars above. Defaults live in `src/devbench/constants.py` (`DEFAULT_CHECK_REGISTRATION_RETRIES`, `DEFAULT_CHECK_REGISTRATION_DELAY_SECONDS`).

### `git-ops-finalize`

```
uv run devbench git-ops-finalize <repo>
```

Single-branch mode only: push the shared branch and create one PR for every accumulated commit. Use once, after every work unit targeting this repo is done. See [architecture.md §6](architecture.md#6-multi-pr-vs-single-pr-mode) for the full single-branch mode reference.

Not applicable under `git_ops.local_only: true` -- the target repo has no remote to push to. The local single branch is the deliverable; running `git-ops-finalize` against a local-only workspace is an error.

**Slack notifications** (issue #219): when the operator has the corresponding `notifications.events.*` toggle enabled, `git-ops-finalize` fires `pr_opened` immediately after `gh pr create` succeeds, then fires `ci_failure` (FAILED_KNOWN_TASK or FAILED_UNKNOWN) or `ci_pass` (GREEN) when the CI watch resolves. `pr_merged` is NOT fired from this path because `auto_merge: false` leaves the squashed PR open for manual merge. The new `ci_pass` toggle defaults to `false` on upgrade.

### `check-merge`

```
uv run devbench check-merge <id>
```

Issue #101 reconciliation step for `pause_before_merge: true` workspaces. Queries `gh pr list --head <branch> --json number,state,merged,url` for the PR associated with the work unit's branch and dispatches:

- **PR merged externally**: promote the work unit to `done` via the existing done-gate (every required judge must have passed in the most recent round). Logs `[PR_MERGED]` audit comment.
- **PR closed without merge**: transition the work unit to `blocked` with a `[BLOCKED]` audit comment naming the PR.
- **PR still open**: no-op; the orchestrator's loop picks the work unit up again on the next iteration.
- **No PR found for branch**: prints `{"pr_state": "no-pr-found"}` and returns 0; the orchestrator treats it the same as "still open".

Returns rc=0 in every normal case; rc=1 only on hard failure (gh API failure, malformed JSON, done-gate refusal). Output is a single JSON line so the orchestrator skill's step 1b reconciliation can parse it.

The orchestrator skill (`plugin/devbench-orchestrate/skills/orchestrate/SKILL.md`) calls this command on every `in-review` work unit at the top of each loop iteration when `git_ops.pause_before_merge: true` is set in the YAML. See [`docs/git-ops-modes.md`](git-ops-modes.md) for the full pause-before-merge mode reference and [ADR-13](adr/13-pause-before-merge.md) for the design rationale.

### `git-ops` orphan-pattern auto-emit (Phase 10 hardening)

When the `git-ops` command refuses a commit because of detected orphan paths, the auto-emitted cleanup proposal claims `.gitignore`. If a pre-existing in-queue / blocked / proposed peer task already lists `.gitignore` in its own `## Changes Manifest`, the auto-emitter now scans the live index and auto-wires `add-dep <peer> <new-cleanup>` for every conflicting peer in the SAME repo. The dep-chain is what the E224 Manifest-Conflict rule already accepts as a valid resolution -- so a backlog that previously halted with `Manifest conflict on '.gitignore' ... claimed by <peer>, <new-cleanup>` now auto-resolves and the orchestrator continues without operator intervention. Tasks already in `done` / `declined` are skipped (they are terminal). The auto-wired peer IDs appear in the stderr message that the auto-emit prints.

### `cleanup-tracked-orphans`

```
uv run devbench cleanup-tracked-orphans <org/repo|repo-path> [--dry-run]
```

Untrack build/state artefacts (terraform state, `.terragrunt-cache/`, terraform provider binaries, Python `__pycache__/` and `*.pyc`, `.coverage*`, `node_modules/`, `.DS_Store`) and write a devbench-managed block to the repo's root `.gitignore` so future commits cannot reintroduce the same shapes.

The argument accepts either an `org/repo` key from `devbench.yaml`'s `repos:` map (resolved via that repo's `checkout_directory`) or a direct filesystem path to a git repo.

Behaviour:

- Walks `git ls-files` and matches each tracked entry against the active orphan pattern list.
- Runs `git rm --cached --quiet` on every match (preserves the file on disk; subsequent commit removes the tracked entry going forward; existing history is NOT rewritten).
- Appends the canonical `.gitignore` block under the `# devbench-managed: tracked-orphan cleanup defaults` header. Idempotent: re-running on a repo that already contains the header is a no-op.
- Writes JSON to stdout summarising `detected_count`, `removed_count`, `gitignore_updated`, and the paths involved.

Override the default patterns per backlog via `DEVBENCH_ORPHAN_IGNORE_PATTERNS` (comma-separated fnmatch globs replacing the built-in list). Use `--dry-run` to preview without modifying state.

Self-resolving integration: `git-ops` invokes the same detection before every commit. When orphans are present, git-ops refuses the commit and auto-emits a `cleanup-tracked-orphans`-running follow-up task; the orchestrator's existing cascade picks it up the next iteration. Operators can also run this command directly to clear a polluted history-going-forward in one shot.

---

## Amendment workflow

See [manifest-amendments.md](manifest-amendments.md) and [ADR-02](adr/02-manifest-amendment-workflow.md) for the full design. This workflow is on by default; set `manifest_amendment.enabled: false` in `backlog/config/devbench.yaml` to opt out.

### `request-amendment`

```
cat <<'EOF' | uv run devbench request-amendment <id>
{
  "reason": "tdd_green_production_fix",
  "files_to_add": ["src/path/to/file.py"],
  "justification": "...",
  "linked_acs": ["AC-TEST-001"]
}
EOF
```

Register an amendment request at `<workspace>/.devbench/amendments/<id>.json`. Payload is JSON on stdin; the four fields above are required. Invoked by the executor during TDD GREEN when a production fix is needed but out of manifest scope.

### `apply-amendment`

```
uv run devbench apply-amendment <id>
```

Atomically update the Changes Manifest after the `manifest-amender` judge approves. Runs a deterministic Layer 3 post-check (em-dash scan plus `validate-backlog`) and rolls back on any failure, so a failed post-check cannot leave the backlog half-updated.

### `reject-amendment`

```
uv run devbench reject-amendment <id> <reason>
```

Reject the pending amendment, archive the request to `<workspace>/.devbench/rejected-requests/<id>-<timestamp>.json`, write a `[AMENDMENT_REJECTED]` audit comment, AND persist a structured rejection-feedback JSON to `<workspace>/.devbench/review-failures/<id>-manifest_amender-<n>.json` (issue #156, schema v1). The task is typically marked `blocked` and may trigger the task-factory flow (see below) if `blocker-resolver` emits a proposal. Legacy archives under `.devbench/amender-rejections/` are still readable for forward compatibility.

### `log-rejection-feedback`

```
uv run devbench log-rejection-feedback <judge> <task-id> --json '<payload>'
```

Persist a structured review-judge rejection JSON. `<judge>` is one of `code_review` / `test_review` / `doc_review` / `changes_manifest` / `security_review` / `manifest_amender`. `<payload>` must validate against `src/devbench/backlog/review-feedback-schema.json` -- in particular every `categories[*].code` must appear in the judge's controlled vocabulary (see `docs/review-feedback-vocabulary.md`). The JSON lands under `<workspace>/.devbench/review-failures/<task-id>-<judge>-<n>.json` and is consumed by the executor-feedback collector on retry and by the done-gate to refuse `mark-done` until each category is cleared via `[REJECTION_FEEDBACK_RESOLVED] <judge>:<code>` or escalated via `[NEEDS_DEP] <judge>:<code>`. Records over `MAX_RETRY_ATTEMPTS` are still written but stamped `capped: true`.

---

## Proposal workflow (task factory)

See [task-factory.md](task-factory.md) and ADRs [03](adr/03-task-factory.md), [06](adr/06-validation-gate-bug-escalation.md), [07](adr/07-auto-requeue-on-proposal-completion.md), [08](adr/08-proposal-lifecycle-observability.md), [09](adr/09-idempotent-materialise-proposal.md) for the full design. Task-factory is enabled by default (`task_factory.enabled: true` in `backlog/config/devbench.yaml`); set `task_factory.enabled: false` to disable.

### `write-proposal`

```
cat <<'EOF' | uv run devbench write-proposal <source-id>
{
  "source_task_id": "E0-F1-S1-T1",
  "generated_at": "2026-04-19T03:25:00Z",
  "rejection_reason": "...",
  "proposed_tasks": [
    { "suggested_id": "E0-F1-S1-T2", "title": "...", ... }
  ]
}
EOF
```

Persist a proposal JSON to `<workspace>/.devbench/proposals/<source-id>.json`. Payload is JSON on stdin. Written either by `blocker-resolver` (amendment-rejected path) or by the executor directly (validation-gate escalation path).

**Auto-cascade when `auto_accept_proposals: true`:** with the flag set in `devbench.yaml`'s `task_factory:` section, `write-proposal` ALSO calls `materialise-proposal` and `promote-proposal` synchronously inside the same Python invocation. The cascade is therefore actionable the moment the JSON lands -- the source task's `## Dependencies` table is wired with a `proposed`-status row immediately, the cascade-classifier moves it into the `auto-clearing via proposal` bucket, and the orchestrator's next iteration claims the materialised draft.

This closes a timing window in which a resolver-written proposal could sit orphaned for up to one full orchestrator iteration (between the resolver's `write-proposal` call and the next `sweep-proposals` cycle) -- long enough for the source task to read as "needs operator attention" even though the auto-resolution path was already on disk.

The auto-cascade is best-effort: any `ProposalError` during materialise or promote is logged and reported in the output JSON's `auto_cascade` / `error` fields but does NOT propagate as a non-zero exit. The JSON is already on disk; the next `sweep-proposals` cycle retries the cascade.

Output shape:

```json
{
  "source_task_id": "E0-F1-S1-T1",
  "proposal_path": "/.../E0-F1-S1-T1.json",
  "auto_cascade": "applied" | "disabled" | "failed",
  "materialised": ["/.../E0-F1-S1-T9.md"],   // when applied
  "promoted":     ["E0-F1-S1-T9"],            // when applied
  "error": "..."                              // when failed
}
```

When `auto_accept_proposals: false` the behaviour is unchanged from prior versions: the JSON is written and the operator promotes manually via `promote-proposal`.

### `materialise-proposal`

```
uv run devbench materialise-proposal <source-id>
```

Turn a proposal JSON into draft `.md` files with status `proposed` under the matching story directory. Each proposed task also gets a row appended to `BACKLOG.md`.

**Idempotent (ADR-09).** Every task classifies through `classify_proposed_task` first; the call skips anything in state `PROPOSED`, `PROMOTED`, `DONE`, `DECLINED`, or `REJECTED` **that THIS proposal authored** (provenance-checked). Only `UNMATERIALISED` tasks are created. Safe to re-run after a partial materialisation or after rejecting a draft from the same JSON. Output JSON includes a `skipped` map so the operator sees why a no-op call was a no-op.

**Collision re-home (ADR-32).** When a `suggested_id` collides with an UNRELATED pre-existing unit (not a draft this proposal authored), the proposed fix unit is NOT silently skipped: it is materialised under the next free id in the Story, the proposal is re-pointed so `promote-proposal` wiring targets the real fix unit, and the colliding unit is left untouched. The output JSON's `remapped` map associates each original `suggested_id` to the free id it was re-homed to.

### `sweep-proposals`

```
uv run devbench sweep-proposals
```

Best-effort materialise every pending proposal JSON in `<workspace>/.devbench/proposals/`. Runs `materialise-proposal` against each, tolerating per-proposal `ProposalError` (safety-guard refusals log and continue). Output lines:

- `sweep-proposals: materialised <source-id>: N new, M skipped`
- `sweep-proposals: skipped <source-id>: <reason>`
- `sweep-proposals: no-op <source-id>` (every task already resolved)
- `sweep-proposals: nothing to do (no proposal JSONs on disk)`

When `task_factory.auto_accept_proposals: true` is set in `backlog/config/devbench.yaml` (ADR-11), sweep also auto-promotes every `PROPOSED` draft in each proposal after the materialise pass. The output line gains a parenthetical count:

```
sweep-proposals: materialised <source-id>: N new, M skipped (auto-promoted: K)
```

Per-draft promote failures are logged to stderr and do not abort the sweep. When the flag is `false` (default), the output is byte-identical to the pre-ADR-11 format. See [ADR-11: Auto-accept proposals](adr/11-auto-accept-proposals.md) and the [task-factory doc](task-factory.md) for the full auto-accept contract.

Invoked by the orchestrate SKILL as step 0 on every loop iteration; safe to run manually at any time.

### `promote-proposal`

```
uv run devbench promote-proposal [--no-dep-on-source] <task-id>
uv run devbench promote-proposal --all-from <source-task-id>
```

Flip a proposed draft from `proposed` to `in-queue` and wire it as a dependency of the source task + every peer listed in the proposal's `affected_task_ids` field (ADR-10). Writes `[PROPOSAL_PROMOTED]` + a `[BLOCKED_PENDING_PROPOSAL] <task-id>` marker on every wired target so the ADR-07 auto-requeue cascade reaches each of them when this task completes.

- `--no-dep-on-source` skips the Dependencies-table row on the SOURCE task only; every entry in `affected_task_ids` still gets its marker + row. Use the flag when the promoted draft is independent of its source task, not to suppress peer-task wiring.
- `--all-from <source-task-id>` promotes every task in that source's proposal in one call.
- When the proposal JSON sets `source_dep_direction: "test_validates_source"`, the command auto-applies `--no-dep-on-source` and emits a `NOTE:` to stderr. When the heuristic (proposal's `title` starts with `Add tests/`, `Add unit tests`, `Add integration tests`, `Verify`, `Validate`, `Assert`; or every entry in `files_to_own` is under `tests/`) matches WITHOUT the explicit flag, the command emits a `WARNING:` and keeps the default direction; re-run with `--no-dep-on-source` if the warning's recommendation applies. See [task-factory.md](task-factory.md) "When to use --no-dep-on-source".

Fail-fast: if any target in `[source_task_id] + affected_task_ids` is missing from the backlog index, the call raises `ProposalError` BEFORE writing anything, so a missing peer never leaves the source half-wired.

Output JSON:

```json
{
  "task_id": "E1-F1-S16-T2",
  "status": "in-queue",
  "file_path": "/abs/path/E1-F1-S16-T2.md",
  "wired_targets": ["E1-F1-S16-T1", "E1-F1-S15-T1"]
}
```

`wired_targets` lists every task that received a marker on this call (source first, then affected in declared order).

### `add-dep`

```
uv run devbench add-dep <blocked-task-id> <blocker-task-id> [--reason "<audit message>"]
```

Wire a `[BLOCKED_PENDING_PROPOSAL] <blocker-task-id>` marker and a Dependencies-table row on `<blocked-task-id>`'s work-unit file. The ADR-07 cascade then auto-unblocks `<blocked-task-id>` when `<blocker-task-id>` reaches `done` or `declined`. See [ADR-10: Multi-target proposal wiring](adr/10-multi-target-proposal-wiring.md).

Use this when the `promote-proposal` flow does not cover your case:

- You realise AFTER a promote that a peer task should have been in `affected_task_ids` and want to wire it retroactively.
- You hand-authored a work unit (not via task-factory) that unblocks another task.
- You are correcting a proposal authored without `affected_task_ids`.

Fail-fast:

- Both IDs must match the `E<N>-F<N>-S<N>-T<N>` task-ID format.
- `<blocker-task-id>` must exist in the backlog index.
- `<blocker-task-id>` must NOT be in a terminal state (`done` / `declined`); wiring a dep on terminal work is a no-op and almost always a mistake.
- `<blocked-task-id>` must exist in the backlog index.
- `<blocked-task-id>` and `<blocker-task-id>` cannot be the same.
- `<blocker-task-id>` must not already depend on `<blocked-task-id>` (via a dep row or a `[BLOCKED_PENDING_PROPOSAL]` marker); wiring the reverse edge would create a direct cycle.

Warns (does not refuse) when `<blocked-task-id>` is not currently in `blocked` status -- the ADR-07 cascade only fires on blocked tasks, so wiring a marker on an in-queue task is harmless metadata; the operator almost certainly meant to flip to blocked first.

Idempotent: if either the Dependencies row or the marker is already present, the corresponding write is skipped. `wired: false` in the output JSON means the call was a complete no-op.

Output JSON:

```json
{
  "blocked": "E1-F1-S16-T1",
  "blocker": "E1-F1-S16-T2",
  "wired": true,
  "reason": "post-promote correction for shared 14-test blocker"
}
```

### `remove-dep`

```
uv run devbench remove-dep <blocked-task-id> <blocker-task-id> [--reason "<audit message>"]
```

Exact inverse of [`add-dep`](#add-dep). Removes the `## Dependencies`-table row for `<blocker-task-id>` from `<blocked-task-id>`'s work-unit file (collapsing the table to the canonical `| none | | |` row when it empties) AND strips the open `[BLOCKED_PENDING_PROPOSAL] <blocker-task-id>` marker so the ADR-07 cascade, the `add-dep` reverse-cycle guard, and every other marker reader stop treating the edge as live. A `[DEP_REMOVED]` audit comment recording the cut + operator reason is appended to the blocked task's append-only Comments history.

Use this to undo a dependency wired by `add-dep` (or by `promote-proposal`) when the edge is no longer correct -- for example, after re-scoping the work so the blocked task no longer needs to wait on the blocker.

Fail-fast:

- Both IDs must match the `E<N>-F<N>-S<N>-T<N>` task-ID format.
- `<blocked-task-id>` must exist in the backlog index.
- `<blocker-task-id>` must exist in the backlog index.
- `<blocked-task-id>` and `<blocker-task-id>` cannot be the same.

Idempotent: removing an edge that does not exist is a clean no-op (it prints a `no such dependency` info line to stderr and writes nothing). `removed: false` in the output JSON means there was no such dependency; `removed: true` means the dep row and/or the marker was actually removed on this call.

The marker is closed by physically stripping the `[BLOCKED_PENDING_PROPOSAL]` line (the same mechanism `reject-proposal` uses), not by the `[CASCADE_RESOLVED]` close the auto-requeue cascade writes -- that close only flips the blocker's *status* and leaves the marker text in place, so it would not clear the substring readers that `remove-dep` must satisfy.

Output JSON:

```json
{
  "blocked": "E1-F1-S16-T1",
  "blocker": "E1-F1-S16-T2",
  "removed": true,
  "reason": "re-scoped: T1 no longer depends on T2"
}
```

### `reject-proposal`

```
uv run devbench reject-proposal <task-id> --reason "<message>"
uv run devbench reject-proposal --unmaterialised <source-task-id> --reason "<message>"
```

Two forms:

1. **Per-draft reject** (first form) -- archives the draft `.md` to `<workspace>/.devbench/rejected-proposals/<task-id>-<timestamp>.md`, removes the BACKLOG.md row, writes a `[PROPOSAL_REJECTED]` audit comment on the source, strips the `[BLOCKED_PENDING_PROPOSAL]` marker, and invokes the auto-requeue cascade. If the source's remaining markers are all terminal, the source auto-unblocks.
2. **Un-materialised reject** (`--unmaterialised <source-id>`) -- archives the whole proposal JSON to `<workspace>/.devbench/rejected-proposals/<source-id>-unmaterialised-<timestamp>.json` and writes a `[PROPOSAL_JSON_REJECTED]` audit comment. Refuses when any task in the JSON already has a materialised draft; use the per-draft form for those first.

Exactly one form must be supplied; missing or both-supplied raises an argument-parse error. `--reason` is required and non-empty.

---

## Environment migration

For migrating from environment-variable-only configuration to a workspace config file, run the `configure-devbench` skill (see [configure-devbench skill](#configure-devbench-skill) below). The skill walks through every `RuntimeConfig` section -- including `log_file`, `report`, `orchestrate`, and `skills` -- and produces a validated `backlog/config/devbench.yaml` that consolidates all previously scattered env-var knobs into a single source of truth.

After writing the config file, every `devbench` subcommand reads it at startup via the `DEVBENCH_CONFIG_PATH` or default `backlog/config/devbench.yaml` lookup. Individual env-var overrides continue to take precedence (env > yaml > code default).

---

## configure-devbench skill

The `configure-devbench` authoring skill walks the operator through every `RuntimeConfig` section interactively and produces a complete, validated `backlog/config/devbench.yaml`. Each collected value is round-tripped through `RuntimeConfig` parsing immediately; invalid values are rejected with the parser's error message and the operator is re-prompted.

### Invocation

From any Claude Code session with the devbench-authoring plugin available:

```
claude run devbench-authoring:configure-devbench
```

If `backlog/config/devbench.yaml` already exists, the skill reads it and pre-populates defaults for every question. Enter a blank line to accept the shown default.

### Walk-through steps (issue #233)

The skill walks through 20 steps, validating each section before moving to the next:

1. **Read existing config** -- pre-populates defaults if `devbench.yaml` exists.
2. **repos** -- target repositories (`org/repo` key, `checkout_directory`, `default_branch`, per-repo `merge_strategy`).
3. **Top-level scalars** -- `merge_strategy`, `max_executor_retries`, `use_bedrock`, `bedrock_region`.
4. **timeouts** -- per-operation timeout values in seconds (`gh_api`, `test`, `security_fetch`, `llm`, `command`, `orchestrator_poll_interval`, `github_check`).
5. **limits** -- threshold and limit values (`alert_summary`, `output_truncation`, `llm_evidence_truncation`, `llm_file_context`, `llm_file_preview_chars`, `ci_failure_log_bytes`).
6. **agents** -- per-agent model overrides for executor, blocker_resolver, manifest_amender, security_reviewer, task_factory, review_supervisor, and per-judge review_team entries.
7. **git_ops** -- `single_branch`, `defer_pr`, `auto_finalize`, `auto_merge`, `pause_before_merge`, `update_submodule`, `inline_orphan_cleanup`, `ci_failure_retry`, `local_only`. Validates mutually exclusive combinations.
8. **task_factory** -- `enabled`, `auto_accept_proposals`.
9. **manifest_amendment** -- `enabled`, `allowed_reasons`, `max_requests_per_execution`.
10. **validate** -- `check_orphan_path_tokens` (rule 20 toggle).
11. **stop_hook** -- `max_blocks`, `window_seconds`, `stale_task_minutes`.
12. **hook_tail** -- column-cap settings: `agent_width`, `tool_width`, `description_max`, `stdout_preview_max`.
13. **debug** -- `check_registration_retries`, `check_registration_delay_seconds`, `blocked_recovery_window_seconds` (leave blank for production workspaces).
14. **backlog** -- `default_status_for_new_work_units` (`in-queue` or `draft`).
15. **notifications** -- master switch, per-event toggles, and Slack endpoint.
16. **log_file** -- workspace-relative path to the orchestrator's aggregate log file. Both `setup_logging` (writer) and `devbench report` / `devbench hook-tail` (readers) consult this single source of truth so they cannot diverge. [default: `logs/orchestrator.log`]
17. **report** -- per-model token pricing (`report.models` table), `default_model` rates, global cache multipliers (`cache_read_multiplier`, `cache_write_5min_multiplier`, `cache_write_1hr_multiplier`), `data_residency_multiplier`, `fast_mode_multiplier`, `recent_pace_tasks`, and `display_timezone`.
18. **orchestrate** -- `max_cascade_depth` cap on recovery-of-a-recovery cascade depth. [default: `2`; override via `DEVBENCH_ORCHESTRATE_MAX_CASCADE_DEPTH`]
19. **skills** -- authoring-skill knobs: `exemplar_backlog_path`, `exemplar_spec_path`, `fan_out_threshold` [default: 10], `max_iterations` [default: 5].
20. **Final validation and write** -- assembles the complete YAML (all sections present regardless of whether the operator changed their values from the defaults), runs the full `RuntimeConfig` round-trip, then writes `backlog/config/devbench.yaml`.

### Output

| Artefact | Location | Condition |
|----------|----------|-----------|
| Config file | `backlog/config/devbench.yaml` | Written after all 20 steps validate |
| `devbench-commands.txt` | `<workspace>/devbench-commands.txt` | Refreshed with foreground and daemon launch commands |
| Summary message | stdout | `[CONFIGURE_DEVBENCH_DONE]` block listing every configured section |

### Four-surface consistency

The four configuration surfaces that must agree for every section are:

1. **Schema** -- `src/devbench/config-schema.json` (structural shape)
2. **Loader defaults** -- field defaults in `src/devbench/config_loader.py`
3. **Sample config** -- `sample-config.yaml` (annotated reference)
4. **configure-devbench** -- step prompts and default labels in `plugin-authoring/devbench-authoring/skills/configure-devbench/SKILL.md`

Mismatches between these surfaces cause the operator to configure a value they believe is the default but which is actually different from what the loader applies. Verify all four surfaces agree before merging any change to one of them.

### Cross-references

- [`plugin-authoring/devbench-authoring/skills/configure-devbench/SKILL.md`](../plugin-authoring/devbench-authoring/skills/configure-devbench/SKILL.md) -- full skill prompt with every step
- [`docs/skills/configure-devbench.md`](skills/configure-devbench.md) -- operator quickstart
- [`sample-config.yaml`](../sample-config.yaml) -- annotated reference config
- [`docs/zero-to-ready.md`](zero-to-ready.md) -- Step 7 (manual alternative to running this skill)
