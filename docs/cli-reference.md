# DevBench CLI Reference

Complete reference for every `devbench` subcommand. Commands are grouped by purpose; within each group they are ordered by the sequence an operator or agent typically hits them.

Every command runs from the parent workspace root (the directory containing the `devbench` checkout):

```bash
uv run devbench <command> [args]
# or: python3 -m devbench <command> [args]
```

Two environment variables MUST be set before any command runs; commands that depend on them exit non-zero with a clear message when unset:

- `DEVBENCH_WORKSPACE_ROOT` -- absolute path to the backlog workspace (contains `BACKLOG.md`, `backlog/`, `.devbench/`).
- `DEVBENCH_CLAUDE_MODEL` -- SDK caller's model id (example: `us.anthropic.claude-opus-4-7-v1`). Governs the orchestrate skill's coordination calls only. Per-agent work models live in the `agents:` block of `devbench.yaml` (see [ADR-25](adr/25-per-agent-model-overrides.md)).

Optional: `--config <path>` (or `DEVBENCH_CONFIG_PATH` env var) overrides the default `backlog/config/devbench.yaml` lookup.

## Exit codes (all commands)

- **0** -- success.
- **1** -- application-level error (invalid state, refused guard, missing work unit, bad args after parse).
- **2** -- argument-parsing error (unknown flag, missing required positional).

Commands that run a blocking external process (git, tests, judges) propagate the process exit code through, subject to the 0/1/2 contract above.

## Contents

- [Backlog read](#backlog-read)
- [Gates](#gates)
- [Backlog write](#backlog-write)
- [Drain (graceful orchestrator stop)](#drain-graceful-orchestrator-stop)
- [Named sessions](#named-sessions)
- [Instances (per-host discovery)](#instances-per-host-discovery)
- [Scope selectors (printer-pages syntax)](#scope-selectors-printer-pages-syntax)
- [Orchestration and reporting](#orchestration-and-reporting)
- [Orchestrator helpers (invoked by agents)](#orchestrator-helpers-invoked-by-agents)
- [Git operations](#git-operations)
- [Amendment workflow](#amendment-workflow)
- [Proposal workflow (task factory)](#proposal-workflow-task-factory)
- [Environment migration](#environment-migration)

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

When neither flag is supplied, `devbench status` consults the active `<workspace>/.devbench/scope.json` (if present) and applies its filter automatically. The file is a JSON object with `include`, `exclude`, `expanded_ids`, `started_at` and `started_by`. A legacy list-shaped payload (issue #270) is migrated in place to this canonical object form -- see [Legacy list-shape migration (issue #270)](#legacy-list-shape-migration-issue-270) below -- while every OTHER non-object shape still raises with the pre-existing message text naming the file path. A session that runs unscoped writes no `scope.json` at all, since absent is how every reader expresses "no scope". When a scope is active -- whether from flags or from `scope.json` -- a `SCOPE:` banner is printed above the Status Summary:

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

**Drain banner (issue #188, db-306):** `devbench status` prepends one `DRAIN REQUESTED` line above the Status Summary for EVERY pending drain signal found under `<workspace>/.devbench/` -- the workspace-root `drain.signal` (if present) AND every per-session `<workspace>/.devbench/sessions/<name>/drain.signal` (if present). The scan is read-only and unconditional, independent of the caller's `DEVBENCH_SESSION_NAME` environment, so a drain requested against a named session is visible even from a shell that never exported that variable. The workspace-root signal (when present) renders first, followed by per-session signals sorted by session directory name:

```
DRAIN REQUESTED: at 2026-05-14T13:55:01+00:00 by matt (reason: nightly cutover)
DRAIN REQUESTED [session=alpha]: at 2026-05-14T13:56:12+00:00 by matt (reason: pausing alpha only)
```

The workspace-root line omits the session qualifier; each per-session line inserts `[session=<name>]` immediately after `DRAIN REQUESTED` so an operator scanning the output can tell at a glance which signal(s) are pending and where each one came from. Every line names the requester, the UTC timestamp, and the reason (or `(none)` when no reason was supplied). When no drain signal is present anywhere, the banner is suppressed entirely. See [`### drain`](#drain-graceful-orchestrator-stop) for the full drain subcommand reference, including the identical listing produced by `devbench drain --status`.

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

**Drain banner (issue #188, db-306):** like `devbench status`, `devbench report` prepends one `DRAIN REQUESTED` line above the report body for every pending drain signal -- the workspace-root `drain.signal` (if present) AND every per-session `<workspace>/.devbench/sessions/<name>/drain.signal` (if present), regardless of `DEVBENCH_SESSION_NAME`. The line format, ordering, and session-qualifier rules (`[session=<name>]` on per-session lines, omitted on the workspace-root line) are identical to the `status` banner -- see [`status`](#status) for the exact wording and worked format. The banner is rendered LIVE, immediately before the report body, on all three of `report`'s emit paths: the cached-snapshot fast-path (a `read_snapshot` hit), the one-shot live path (`generate_report` invoked directly), and every frame of the streaming loop. In every case the banner text is produced by a fresh scan of the drain signals and is never baked into the cached snapshot string or memoised inside a streamed frame's report body, so a drain requested after a snapshot was written -- or between two streaming redraws -- is never hidden behind stale output.

**Transport restarts row (issue #331):** rendered immediately below the banner line, via `report.transport_restarts_line()`. Streams the orchestrator log and counts every genuine `[ORCHESTRATOR_TRANSPORT_RESTART]` audit line that `start`'s transport-error recovery path (see [`start`](#start) above and [ADR-34](adr/34-orchestrator-transport-restart.md)) has logged, **counted separately per window and labelled with the window it measures**, rendering:

```
Transport restarts        2134 all-time / 3 session / 0 this run
```

The windows are the SAME boundaries the table below uses -- All-time, the current orchestrator session, and (watch mode only) the report run -- resolved once and shared, so the row and the table can never disagree. `session` is omitted when no session boundary is known and `this run` is omitted outside watch mode.

Each count is labelled because the bare number was actively misleading: the orchestrator log is append-only and never rotated, so the row reported a lifetime total while sitting directly above columns headed All-time / Session / This run. A restart storm from days earlier therefore rendered as a four-figure number beside a perfectly healthy current run, which reads as an in-progress failure. The all-time count alone decides whether the row appears at all, so a workspace that has never had a transport restart still renders nothing.

Rendered only when at least one transport restart has been logged (`n > 0`); omitted entirely on a clean run, so a run with no transport restarts stays byte-identical to the pre-#331 layout.

**Review rejections row (issue #122):** rendered immediately below the transport-restarts row, via `report.review_rejections_line()`. For every non-terminal task (`in-progress` or `blocked`) carrying at least one `[REVIEW_FAIL]`, it lists the rejection rounds each canonical reviewer has spent against that judge's budget:

```
Review rejections        E2-F5-S1-T2 changes_manifest 1/10, doc_review 2/10
```

This row exists because a review-rejection loop is otherwise indistinguishable from steady progress: the process stays alive, the log keeps advancing, and no error is ever logged, while a single task can consume hours and a large token budget being rejected and reworked. A task showing `3/10` is not making progress in the way the rest of the report implies.

The denominator is resolved by the same `backlog.manager.resolve_judge_retry_budget` that [`log-verdict`](#log-verdict) enforces, so the number displayed can never disagree with the budget actually applied. Audit-only workflow agents are excluded -- they own no review gate. Rendered only when some non-terminal task has a rejection; omitted entirely otherwise, so a clean run's report stays byte-identical.

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

**ETA formula (issue #157):** the `Est. time to complete remaining` cell now multiplies the recent-pace minutes by `tasks_active + tasks_blocked_recovery + tasks_blocked_auto`. Both blocked buckets resolve on devbench's own (proposal cascade or auto-recovery loop), so excluding them produced an unrealistically optimistic ETA. The `needs operator attention` bucket stays excluded -- those are genuine halts with unbounded ETA. The cell carries a comment-suffix naming the bucket counts and pace, e.g. `~5.4 h (active 4 + blocked-recovery 60 + blocked-auto 27 at 5.6 min/task)`. The cost projection uses the same denominator. ETA falls back to `n/a` when fewer than the required pace samples have completed in the recent window (the metric is fragile and a single completion would project meaningless numbers). `Recent pace (last N tasks)` and `Average time per task` are medians of same-session execution-time samples (issue #326): a completion counts only when its `in-progress` claim and its `done` transition fall in the same orchestrator session, so a stale claim from an earlier session (for example an operator `set-status ... done`) no longer skews the pace or the ETA it drives. Completions dropped for having no execution window are named rather than silently excluded: both cells, and the trailing summary line when it drives the sentence, append `(<k> excluded: no execution window)` when any were dropped. Issue #329 hardens the same anchor on two further fronts. First, the `in-progress` claim consumed above must be a genuine transition: the query binds `logger = 'devbench.backlog_manager'`, the sole in-tree emitter of the quoted `Set <id> to '<status>'` record, so a `devbench.cli` line that merely echoes an earlier transition -- for example an SDK `ToolResultBlock` payload that reproduces a prior audit comment -- can never be counted as a transition even though the phrase appears in the line; echoed log lines are not transitions. Second, the anchor is the EARLIEST same-session `in-progress` transition rather than the latest: a task claimed twice in the same session (claimed, bounced by review, re-claimed) is measured from its first claim onward, because review-bounce time inside a session is real elapsed time, not idle time. Candidate `in-progress` rows rejected for failing the logger check are surfaced rather than silently dropped: both cells append a second suffix, `(<k> non-transition rows rejected)`, composed AFTER the `#326` exclusion suffix above, e.g. `32.1 min (44 non-transition rows rejected)`; the two suffixes are orthogonal and either, both, or neither may appear on a given render. On the log that originally surfaced the defect the two flaws combined understated the median execution window by roughly 10.6x (32.1 min true vs 3.0 min as sampled).

**In-progress duration (issue #158):** the `In-progress tasks:` panel suffixes every row with a humanized attempt duration (`23m`, `1h 47m`, `2d 3h`). Multiple in-progress transitions for the same task (blocked-then-resumed) resolve to the most recent one. When neither the structured log nor the work-unit's audit comments yield a parseable timestamp the row renders `(in-progress, timer unavailable)` -- never silently omitted. The same suffix appears on `devbench status` and `devbench status --detail` Active rows.

The duration is anchored to the transition record written by `devbench.backlog_manager`, matched on the full record shape rather than on the phrase alone (issue #293). The orchestrator logs whole SDK messages, so a tool result that read a work unit's `[WU_CLAIMED]` audit comment reproduces the text `Set <id> to 'in-progress'` inside a line stamped with the time of the *dump*. Matching the phrase anywhere in a line made those echoes win, under-reporting a unit's age by the gap between the claim and the echo, and the error grew with every further echo.

**Actionability line (issues #251, #309):** both `devbench status` and `devbench report` end with the same one-line answer to "can the run proceed?", produced by a single shared helper so the two commands cannot disagree. Exactly one of five statements prints, in priority order:

- `Next actionable: <id> -- <title>` -- at least one unit is claimable.
- `All work units are DONE.` -- nothing remains.
- `<id> active; nothing else can start yet. <tail>` -- exactly one unit is already running (`in-progress` / `in-review`) and nothing else is claimable.
- `<N> units active; nothing else can start yet. <tail>` -- two or more units are already running and nothing else is claimable.
- `No actionable units. <tail>` -- work remains, nothing is running, and none of it can start.

`<tail>` is `<B> blocked` when no unit is on hold, or `<B> blocked, <H> on hold` when `H` (units with status `HOLD`) is greater than zero.

Issue #309: a serially-ordered backlog's steady state is exactly one unit `in-progress` and everything else `blocked` on it. `get_parallel_candidates` deliberately includes `in-progress` units (issue #185, resume support), so once the active ids are subtracted from the candidate list the result was always empty in that steady state, and the old stuck-state line (`No actionable units. <N> blocked.`) printed while work was actively executing -- camouflaging the genuine deadlock case that same line is meant to flag. The dedicated active-unit outcomes above name the running unit(s) instead, and the tail now counts `HOLD` units alongside `BLOCKED` ones so they no longer vanish from the total.

The per-status counts do not answer this on their own: a backlog can hold many `in-queue` units and still have nothing actionable, because only leaf Tasks execute and every one of them may be waiting on a dependency. `devbench next` deliberately keeps its machine tokens (`ALL_DONE` / `NO_ACTIONABLE` / `NO_ACTIONABLE_IN_SCOPE`) instead; those are a contract consumed by the orchestrate skill's loop-continuation check.

**Orchestrator-alive banner (issues #161, #250):** the very first line of `devbench report` is a one-line liveness banner. The process table decides whether an orchestrator is running; log recency only describes what it has been doing. Five states:

- `[ORCHESTRATOR ALIVE]` (green) -- the PID file names a running process. Suffix names the pid and the elapsed-since duration (`pid 1669778; last activity 12s ago`).
- `[ORCHESTRATOR ALIVE] ... idle` (yellow) -- running, but quiet for longer than `stop_hook.window_seconds`. A live orchestrator is never reported STOPPED merely for being quiet.
- `[ORCHESTRATOR STOPPED]` (red) -- the PID file names a process that is not running. Authoritative: log recency is used only for the last-seen timestamp, not for the verdict.
- `[ORCHESTRATOR STARTING]` (yellow) -- PID file present but not yet parseable, i.e. the daemon is mid-write.
- `[ORCHESTRATOR NOT RUNNING]` (red) / `[ORCHESTRATOR UNKNOWN]` (yellow) -- no PID file, with and without log activity respectively.

Deriving ALIVE from recency alone reported a healthy orchestrator when none was running: a recent log line proves only that *something* wrote to the log, not that the writer still exists. A crashed or killed daemon read as ALIVE for the whole quiet window, and any other process writing to the same log kept it ALIVE indefinitely.

Every banner ends with the active session id when `DEVBENCH_ORCHESTRATOR_SESSION_ID` is set (`-- session backlog-a-orchestrator`); the suffix is suppressed when the env var is unset so multi-session operators never see a `-- session None` artefact.

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

## Gates

Read-only introspection and structured-waiver tooling for the eight integration-reality gates (spec `integration-reality-gates-hardening.md` section 4.1; D-2, D-15, D-17). This section is the home for every gate-related verb; today it documents `gates`, `log-waiver`, `log-newly-reachable` and the `git-ops-finalize --provenance` flag -- the per-gate check commands (`check-reachability`, `check-shared-file-impact`, `check-fixture-consistency`, and the ones later units add) continue to live under [Orchestrator helpers](#orchestrator-helpers-invoked-by-agents) until a follow-up unit relocates them here.

### `gates`

```
uv run devbench gates
```

Show every gate's tier, status and repo overrides. Renders one row per declared gate (`reachability`, `ancestry`, `shared_file_impact`, `fixture_consistency`, `write_path_audit`, `newly_reachable_paths`, `composition_root`, `layout_geometry`, in that order), each resolved exclusively through `config_loader.resolve_gate_config` -- the single read path for the four-layer precedence model (built-in -> project -> per-repo -> env; see [devbench-yaml-reference.md](devbench-yaml-reference.md#gates----integration-reality-gates-spec-41)). This command never reads `gates:` config directly.

Columns:

- **gate** -- the gate's declared name.
- **tier** -- `machine-blocking` or `judge-evidence`, the gate's declared enforcement tier (`constants.GATE_TIERS`, spec section 4.2, D-6). `reachability`, `ancestry`, `shared_file_impact`, and `fixture_consistency` are `machine-blocking`; the other four gates are `judge-evidence`. No gate carries the weaker `advisory` tier today. A `machine-blocking` gate that is `enabled` for a unit's repo is wired into `mark-done` (see below); a `judge-evidence` gate never blocks `mark-done` on its own.
- **status** -- `enabled` or `disabled`, the resolved value of that gate's `enabled` field.
- **repos** -- the `org/repo` name(s) carrying an explicit override for that gate (comma-separated when more than one), or `-` when none override it.
- **provenance** -- which layer set the resolved `status`: `builtin`, `project`, `repo`, or `env`.

Read-only and total: on a fresh workspace with no `gates:` key at all, every row renders `disabled` / `-` / `builtin` (D-17: every gate disabled by default). Column widths are computed from the row data on every run, not hard-coded.

Exits 1 with the loader's own fail-fast message on stderr (and no table on stdout) when `backlog/config/devbench.yaml` is missing or fails YAML/schema validation.

Example, with a per-repo override enabling `shared_file_impact` for `caylent-solutions/devbench`:

```
$ uv run devbench gates
gate                   tier              status    repos                       provenance
reachability           machine-blocking  disabled  -                           builtin
ancestry               machine-blocking  disabled  -                           builtin
shared_file_impact     machine-blocking  enabled   caylent-solutions/devbench  repo
fixture_consistency    machine-blocking  disabled  -                           builtin
write_path_audit       judge-evidence    disabled  -                           builtin
newly_reachable_paths  judge-evidence    disabled  -                           builtin
composition_root       judge-evidence    disabled  -                           builtin
layout_geometry        judge-evidence    disabled  -                           builtin
```

### `log-waiver`

```
uv run devbench log-waiver <judge> <id> --gate <g> --target <t> --reason <r> [--operator]
```

Record a structured gate waiver: log-waiver <judge> <id> --gate <g> --target <t> --reason <r> [--operator]

Writes a `[GATE_WAIVER <gate>] <iso-utc> <target> <operator|executor> <reason>` marker (spec section 5.3 field order) into the unit's `## TDD Cycle Log` section -- the audit surface that survives every review judge's `read-unit --strip-comments` Evidence fetch (the PM-6 evidence-horizon rule, E2-F3-S1-T2). `## Comments` itself is stripped by that fetch, so a marker appended there would be invisible to the very judge spec section 3.6 requires to weigh it; `log-waiver` never writes to `## Comments`.

Arguments:

- `<judge>` -- one of the five canonical review judges (`code_review`, `test_review`, `doc_review`, `changes_manifest`, `security_review` -- `constants.ALL_REQUIRED_JUDGE_NAMES`, the same vocabulary `log-verdict` validates against) whose Evidence block should treat this waiver as evidence.
- `<id>` -- the work unit ID.
- `--gate <g>` -- one of the eight declared gate names (`constants.GATE_NAMES`).
- `--target <t>` -- the specific file, path, or artifact the waiver covers. A single token with no whitespace.
- `--reason <r>` -- REQUIRED, non-empty rationale. Validated by the same em-dash / control-character / bracketed-TDD-phase-tag boundary check `log-comment` / `log-tdd` / `log-verdict` use (`_validate_agent_free_text`).
- `--operator` -- marks the waiver as operator-attributed. REQUIRED when `--gate` names a `machine-blocking` gate (spec Section 3.6: the operator is the only waiver authority for a machine-blocking gate); omit for an executor-attributed waiver, valid only for a `judge-evidence` gate.

Exit codes:

- `0` -- the marker was written; stdout carries a JSON summary (`unit_id`, `judge`, `gate`, `target`, `attribution`).
- `1` -- the work unit does not exist, or `--reason` fails the free-text validation (em-dash, control character, or bracketed phase tag).
- `2` -- a usage error naming the offending argument: unknown `<judge>`, unknown `--gate`, an empty/missing `--gate` / `--target` / `--reason`, or a `machine-blocking` gate waived without `--operator`.

`validate-backlog` rejects a malformed `[GATE_WAIVER]` marker (grammar drift from spec 5.3) naming the unit and the offending line; `report` counts outstanding waivers split by `operator` / `executor` attribution (spec PM-5).

Example (spec G7):

```
$ uv run devbench log-waiver code_review E9-F1-S1-T1 \
    --gate reachability --target src/ui/LegacyPanel.tsx \
    --reason "mounted via route-split registry resolved at runtime" --operator
{"unit_id": "E9-F1-S1-T1", "judge": "code_review", "gate": "reachability", "target": "src/ui/LegacyPanel.tsx", "attribution": "operator"}
```

### `log-newly-reachable`

```
uv run devbench log-newly-reachable <id> --path <p> --method <m> --result <r>
```

Record a newly-reachable-path verification: log-newly-reachable <id> --path <p> --method <m> --result <r>

Writes a `[NEWLY_REACHABLE] <path> <method> <result>` marker (spec section 5.3 field order) into the unit's `## TDD Cycle Log` section -- the audit surface that survives every review judge's `read-unit --strip-comments` Evidence fetch (the PM-6 evidence-horizon rule, E2-F3-S1-T2). `## Comments` itself is stripped by that fetch, so a marker appended there would be invisible to the judges that must weigh it; `log-newly-reachable` never writes to `## Comments`. It replaces the free-text `[NEWLY_REACHABLE]` prose convention written via `log-comment` into `## Comments` (spec 4.9(a); AC-21: the structured marker survives the Evidence fetch the prose convention did not).

Arguments:

- `<id>` -- the work unit ID.
- `--path <p>` -- REQUIRED, non-empty. The specific code path (file, route, component) newly reachable because of this fix. A single token with no whitespace.
- `--method <m>` -- REQUIRED. How the path was verified: `manual`, `unit_test`, `integration_test`, or `functional_test` (`cli.NEWLY_REACHABLE_METHODS`; PR #320's proposed schema).
- `--result <r>` -- REQUIRED. The verification outcome: `verified` (the path behaves correctly) or `broken` (verification surfaced a new, independent defect) (`cli.NEWLY_REACHABLE_RESULTS`).

Exit codes:

- `0` -- the marker was written; stdout carries a JSON summary (`unit_id`, `path`, `method`, `result`).
- `1` -- the work unit does not exist.
- `2` -- a usage error naming the offending argument: an empty/missing `--path`, an unknown `--method`, or an unknown `--result` (listing the accepted values for the enumerated fields).

Example (spec 4.9(a)):

```
$ uv run devbench log-newly-reachable E9-F1-S1-T1 \
    --path src/ui/LegacyPanel.tsx --method manual --result verified
{"unit_id": "E9-F1-S1-T1", "path": "src/ui/LegacyPanel.tsx", "method": "manual", "result": "verified"}
```

### `git-ops-finalize --provenance`

```
uv run devbench git-ops-finalize <repo> [--provenance <path>]
```

Spec 4.13 / D-17 (issue #334): `git-ops-finalize`'s PR body is composed by `GitOpsService.compose_finalize_pr_body`, which reads a JSON provenance map and renders the PR title, one per-epic summary section, then a closing-keyword block with one `Fixes ...` line per mapped issue -- so the combined PR auto-closes every issue it fixes on merge, whether or not GitHub's auto-close already fired via a per-commit reference. Full command semantics live under [`git-ops-finalize`](#git-ops-finalize) in [Git operations](#git-operations); this entry documents the flag and the map it reads.

Precedence: `--provenance <path>` beats `git_ops.provenance_path` in `devbench.yaml` for this single invocation; the config key alone is what lets an unattended `auto_finalize` run pick up the feature with no operator step. With neither set, the composed body is byte-identical to the plain body `git-ops-finalize` has always produced -- this is a pure additive opt-in (spec Section 6). There is no `DEVBENCH_*` environment override for `git_ops.provenance_path` (YAML-only, like its sibling `single_branch` and `branch_prefix` settings).

Path resolution: a relative value (from either `git_ops.provenance_path` or `--provenance`) resolves against the TARGET REPO working tree -- the `repos.<org/repo>` checkout `<repo>` names -- never against the workspace root and never against the devbench process's current working directory. An absolute path is used as-is. Because `git_ops` is a single GLOBAL config block while `git-ops-finalize <repo>` runs per repo, one relative `git_ops.provenance_path` value resolves to a DIFFERENT file inside each repo's checkout in a multi-repo workspace; see [devbench-yaml-reference.md](devbench-yaml-reference.md#git_ops----git-workflow-settings) for the full field reference including that consequence.

Provenance map shape (JSON; required fields marked; see [devbench-yaml-reference.md](devbench-yaml-reference.md#git_ops----git-workflow-settings) for the full `git_ops.provenance_path` field reference):

```json
{
  "epics": [
    {
      "name": "E1: Cherry-pick integration",
      "summary": "One-line summary of what this epic delivered.",
      "issues": [
        {"repo": "org/other-repo", "number": 10},
        {"number": 335}
      ]
    }
  ]
}
```

Top-level `epics` is required and must be a list. Each epic requires a non-empty string `name` and a non-empty string `summary`; an epic's `issues` is optional but, when present, must be a list. Each `issues` entry needs an integer `number`; an omitted `repo` (or a `repo` equal to the target repo) renders `Fixes #<n>` (same-repo); any other `repo` (a string matching `owner/name`) renders `Fixes <repo>#<n>` (cross-repo) -- both forms rendered by the same code path.

Exit codes:

- `0` -- the body composes successfully, the PR step completes (a new PR is created with the composed body passed to `gh pr create --body`, or, per issue #129, an already-open PR on the branch is reused as-is with the freshly-composed body computed but never posted), AND the post-PR CI watcher reports `CIResult.GREEN`. The PR stays open for human merge (or `auto_merge`, when enabled).
- `1` -- a usage or provenance-resolution failure before any push or PR creation. Causes include: the pre-existing `single_branch` / `defer_pr` prerequisite checks; no `<repo>` positional; an unexpected extra positional argument (`ERROR: unexpected argument <arg>`); `--provenance` passed with no value; no local path configured for the resolved repo (`ERROR: No local path configured for repo '<repo>'`); and the resolved provenance path (from `--provenance` or the config key) failing to resolve to a usable map -- not just missing, unreadable, not valid JSON, or resolving to zero mapped issues, but also any structurally malformed map (a payload that does not decode to a JSON object, a missing or non-list top-level `epics`, a non-object epic, an epic missing a non-empty `name` or `summary`, an epic whose `issues` is present but not a list, a non-object issue entry, an issue missing an integer `number`, or an issue `repo` that is not an `owner/name` string). The command never silently falls back to the plain body on any of these.
- `2` -- the PR was created (or reused), but the post-PR CI watcher did not report GREEN: CI failed and was attributed to a known task (`CIResult.FAILED_KNOWN_TASK` -- a recovery proposal is written and that task is blocked, or the attempt is logged as cascade-capped), CI failed with unknown attribution (`CIResult.FAILED_UNKNOWN` -- the most-recent active/done task is blocked), or the CI watch timed out (`CIResult.TIMEOUT` -- no task status changes). See `_handle_finalize_ci_result` / `_handle_finalize_known_task_failure` for the full four-branch dispatch.

Example, with a provenance map at `docs/release-notes/provenance-map.json`:

```
$ uv run devbench git-ops-finalize caylent-solutions/devbench --provenance docs/release-notes/provenance-map.json
```

---

## Backlog write

Mutating commands on the backlog itself. All writes go through the workflow gates; operators use these commands directly only for recovery or lifecycle transitions that the orchestrator does not drive.

### `claim`

```
uv run devbench claim <id>
```

Set the work unit's status to `in-progress`. Fails if the unit is already in a terminal state. Invoked by the orchestrate SKILL at the start of each loop iteration.

`claim` refuses, with a non-zero exit and no status write, when the unit's Changes Manifest still carries a placeholder row. Replace it with real file entries, or let the manifest-amendment workflow fill it in.

**Active-work-unit marker (issue #336).** On every successful claim, under the same `BACKLOG.lock` as the status write, `claim` records the absolute path of the claimed unit's `.md` file in `<workspace>/.devbench/active-work-unit` (or `active-work-unit-<session>` when `DEVBENCH_SESSION_NAME` is set). The `guard-git-stage.sh` PreToolUse hook resolves the active work unit from this marker to enforce Changes-Manifest scope on `git add` -- hook processes inherit the long-lived orchestrator environment, so a per-work-unit environment variable can never reach them. The marker is never cleared: the hook checks that the referenced unit still declares `## Status: in-progress` before enforcing, so a stale marker after a terminal transition is a designed skip.

**Checkout quarantine.** Before claiming, `claim` clears any uncommitted change in the unit's target checkout that falls outside that unit's Changes Manifest.

This exists because the single-branch modes (`git_ops.single_branch` with `defer_pr`) run every work unit in one shared checkout. A unit that blocks, or a run that is interrupted, leaves its work in the tree, and the next unit to claim inherits it: its commit absorbs the sibling's files under the wrong unit's message, and the review judges reject it over code it does not own and cannot fix.

devbench runs unattended, so the residue is moved rather than reported. Each foreign path is stashed under the ID of the unit whose Changes Manifest declares it, and the claim proceeds against a checkout holding only the claiming unit's scope. Stopping to ask an operator would turn one blocked unit into a stopped run.

The scan covers staged, unstaged, and untracked-but-not-gitignored paths, so residue left by a unit that blocked before staging is caught too. The unit's own manifest files are never quarantined, so re-claiming an `in-progress` unit after an interrupted run keeps its work in place.

Quarantine is non-destructive. Each entry is a normal git stash with a discoverable message:

```
$ git stash list
stash@{0}: On <branch>: devbench-quarantine:<owner-id>: displaced by claim of <claiming-id>
$ git stash apply stash@{0}     # recover it whenever you want
```

One entry is created per owning unit, so each unit's work stays recoverable as a unit. Paths that no work unit declares are quarantined under the `unattributed` key: they are still outside the claiming unit's scope and would corrupt its commit just the same. The owning unit also receives a `[WORK_QUARANTINED]` audit comment naming the stash.

Nothing is restored automatically. A blocked unit re-executes from its Changes Manifest when it unblocks, and silently re-injecting a superseded attempt into a later run's tree would recreate the contamination the quarantine removed.

`claim` fails, and does not claim, only when the quarantine itself fails or leaves residue behind. Both mean the checkout was not actually cleared, and proceeding would hand the unit exactly the contaminated tree the quarantine was meant to remove. When the unit's repo has no configured local checkout there is no shared tree to guard; the step is logged as skipped and `git-ops` still fails fast on the same missing configuration at commit time.

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

Mark the unit as `done`. Enforces the done-gate: all four review judges (`code_review`, `test_review`, `doc_review`, `changes_manifest`) must have logged `[REVIEW_PASS]` in the most recent round (after any intervening `[REVIEW_REJECTED]`). Security review must also have passed. Exits 1 with a clear error naming the missing judge(s) when the gate fails.

**Task-type completion invariant (FR-4.5/FR-4.6, E4-F4-S1-T2).** Before either the review-judge check above or the status write, `cmd_mark_done` delegates to `BacklogManager.mark_done`, which calls `_check_task_type_done_invariant` and refuses (rc=1, `RuntimeError` message naming all three FR-4.5 remedies) unless the task's own declared `## Task Type:` invariant is machine-provably satisfied:

- **Gated types** (`behavior-fix`, `feature`, and the default when `## Task Type:` is omitted): requires a machine-observed `RED_OBSERVED` entry in the TDD Cycle Log, written only by `uv run devbench tdd-gate <id>`.
- **`refactor`**: requires a machine-observed `GREEN_GREEN_OBSERVED` entry in the TDD Cycle Log, written only by `uv run devbench green-green-check <id> <test_node_id> [...]`.

This invariant check is deliberately implemented once in `BacklogManager.mark_done` -- not in a CLI-layer wrapper -- so every caller inherits it identically; see `check-merge` below.

**Machine-blocking gate-record invariant (spec `integration-reality-gates-hardening.md` section 4.2, G4; E2-F2-S1-T2).** `BacklogManager.mark_done` also calls `_check_gate_pass_done_invariant`: for every gate in `constants.GATE_TIERS` whose tier is `machine-blocking` (`reachability`, `ancestry`, `shared_file_impact`, `fixture_consistency`) that resolves `enabled` for the unit's repo (`config_loader.resolve_gate_config`, the single read path), the unit must carry either a fresh `[GATE_PASS <gate>]` record or an operator-attributed `[GATE_WAIVER <gate>]` marker (spec 5.3); an executor-attributed waiver never satisfies a machine-blocking gate (spec Section 3.6: executors do not self-certify gate outcomes). Absent both, `mark-done` exits 1, writes no status, and names the exact remediation command, matching the spec G4 worked example in shape:

```
$ uv run devbench mark-done E9-F1-S1-T1
ERROR: done-gate: gate 'reachability' is enabled for repo
'caylent-solutions/devbench' but has no [GATE_PASS reachability] record for
E9-F1-S1-T1. Run: uv run devbench check-reachability E9-F1-S1-T1
```

A `[GATE_PASS <gate>]` record's `scope_hash` is recomputed from the unit's current `## Changes Manifest` file list (SHA-256 over the sorted file list plus each file's live `git hash-object` blob hash, `gate_records.compute_scope_hash`); any edit to an in-scope file's content after the gate ran invalidates the record, refused with `ERROR: gate '<name>' record is stale (scope changed since it ran)`. A malformed `[GATE_WAIVER <gate>]` marker (missing target, missing or empty reason) is never silently treated as "no waiver": `mark-done` refuses with `ERROR: malformed [GATE_WAIVER <gate>] marker: <parse detail> (unit <unit-id>)`, naming both the offending marker line and the unit -- this is `mark-done`'s own wording (`BacklogManager._check_gate_pass_done_invariant`, which folds the unit id into `_latest_gate_waiver_attribution`'s `RuntimeError`); `check-reachability` reports the analogous failure as `ERROR: malformed [GATE_WAIVER <gate>] marker in <unit-id>: <detail>`. A disabled gate imposes nothing at all. Like the task-type invariant above, this check is implemented once in `BacklogManager.mark_done` and inherits into every caller, including `check-merge` below.

### `decline`

```
uv run devbench decline <id> --reason "<message>" [--citation <commit-hash-or-task-id>]
```

Mark a work unit `declined`: it will never be done. Used when the operator decides the unit's scope is being removed, the functionality is being deleted instead, or a different task delivered the same outcome. Declined children count as terminal-complete for parent rollup. See [ADR-05](adr/05-declined-status.md).

**`--citation` (FR-4.5, E4-F4-S1-T2).** When `--reason` contains the routing keyword `already-satisfied` (case-insensitive), an already-satisfied decline is an unfalsifiable claim without proof it was checked, so `--citation <value>` is REQUIRED too. `<value>` must be either a 7-40 character lowercase hex commit hash or a canonical work-unit id (validated by `BacklogManager.is_valid_citation`); an uncited already-satisfied decline is rejected (rc=1) with a message naming all three FR-4.5 remedies. On success the citation is folded into the persisted `[DECLINED]` comment as `"... (citing <value>)"`, which `validate-backlog` check 22 re-verifies independently on read (see [backlog-contract.md](backlog-contract.md)).

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

### `remove`

```
uv run devbench remove <id> --reason "<message>"
```

Remove a work unit through the managed path (db-303): deletes the work-unit `.md` file and its `BACKLOG.md` index row under a single `flock(BACKLOG.lock)`, re-rolls the `## Status Summary` table, and appends a `[WU_REMOVED] <id> -- <reason>` line to the workspace audit log (`BacklogManager.remove_unit`). Unlike `decline`, which keeps a unit visible with a terminal `declined` status, `remove` is the managed path to make a superseded unit disappear from the backlog entirely. `BACKLOG.md` is otherwise protected by `guard-work-unit-write.sh`: a raw `Write`/`Edit` to it is blocked by default (see [architecture.md](architecture.md#9-hooks-layer)) unless the operator sets `DEVBENCH_ALLOW_BACKLOG_EDIT=1`, so `remove` -- which writes through Python I/O, not the `Write`/`Edit` tools -- is the normal path to drop a unit.

The `--reason` is REQUIRED (rc=1 with no write when missing); em-dashes in the reason text are rejected at the input boundary, same as `hold`/`unhold`/`decline`. An unknown `<id>` fails fast with rc=1 before any file is touched -- the index row is only deleted after the id is confirmed to exist, and the row delete itself raises before the work-unit file delete runs, so there is no partial-removal state.

**Exit codes:**

| Scenario | rc |
|----------|-----|
| Unit removed successfully | 0 |
| `<id>` or `--reason` missing | 1 |
| `--reason` flag present with no value | 1 |
| Em-dash detected in reason text | 1 |
| `<id>` not found in `BACKLOG.md` | 1 |

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

### `reconcile-cascade`

```
uv run devbench reconcile-cascade
```

Reconcile every `blocked` Task against marker-target and regular-dependency state (issue #150), then repair any Story/Feature/Epic container stranded before the issue #332 FR-1 live-rollup fix landed. Two passes, always run together:

1. **Blocked-task pass.** For each `blocked` Task: evaluates every `[BLOCKED_PENDING_PROPOSAL]` marker target's status via the loaded backlog index, and evaluates the Task's regular `## Dependencies` via `BacklogParser._deps_satisfied`. Flips the Task to `in-queue` ONLY when every marker target is terminal (`done` or `declined`) AND every regular dep is satisfied, writing a `[CASCADE_RECONCILED]` audit comment naming the closed markers. A Task left `blocked` is reported with the specific reason (an open marker, an unknown marker target, or an unsatisfied dep) so the operator can decide what to do next.

2. **Container repair pass (issue #332 FR-2).** The live auto-rollup (see [Auto-rollup behavior](backlog-contract.md#auto-rollup-behavior)) only fires from a fresh terminal transition, so a Story/Feature/Epic whose children were already all terminal before that fix landed has no live event left to promote it. This pass walks every non-terminal container, re-evaluates `_all_children_done` fresh, and promotes qualifying containers -- cascading upward exactly as a live rollup would -- via `_repair_stranded_containers`. The whole pass runs under a single `flock_backlog` acquisition, and is idempotent: a second run against an already-repaired backlog reports zero rolled up.

Output is a JSON envelope of the form `{"flipped": [...], "skipped": [...], "rolled_up": [...]}`, where `flipped` and `skipped` cover pass 1 (each entry names the Task id, and `flipped` entries also list the closed marker ids) and `rolled_up` lists every container id promoted by pass 2 (including ones promoted purely as a cascade side-effect of promoting a descendant). Exits **0** always; the operator reads the JSON envelope or the summary log line to see what happened:

```
reconcile-cascade: <n> flipped, <m> skipped, <k> parent(s) rolled up
```

Useful for triage when a backlog has drifted out of sync (a promoted proposal's auto-requeue trigger never fired, a process crashed mid-write) or after upgrading past the #332 fix, when existing backlogs may already be stranded and need the repair pass once. `devbench next`'s "no actionable units" message names both `validate-backlog` and `reconcile-cascade` as the next diagnostic steps.

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

- **`scope show`** -- print the active scope state (include list, exclude list, expanded ID count, `started_at`, `started_by`) or `no scope pending` when no scope file exists. Exits 0 in both cases. A legacy list-shaped `scope.json` (issue #270) is migrated in place on this read path too, exactly as it is for `devbench status` -- see [Legacy list-shape migration (issue #270)](#legacy-list-shape-migration-issue-270); this makes `scope show` write to disk on that one legacy code path even though it is otherwise a pure display command.

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
uv run devbench start [--daemon] [--include "<tokens>"] [--exclude "<tokens>"] [--name <name>] [--allow-overlap]
```

Run the orchestrate SKILL non-interactively via the Agent SDK. Invoked by `make start` (the recommended way to run DevBench). Loads the plugin ad-hoc from the devbench checkout; no global `make plugin-install` required. When the workspace's `backlog/config/devbench.yaml` declares an `agents:` block (see [`docs/adr/25-per-agent-model-overrides.md`](adr/25-per-agent-model-overrides.md)), `start` materialises a workspace-local shadow plugin tree at `<workspace>/.devbench/plugin-shadow/devbench/` and passes that path to the SDK in place of the canonical plugin.

**Premature-turn-end recovery:** the orchestrate loop is designed to stop on exactly three conditions -- `ALL_DONE`, `NO_ACTIONABLE`, or an operator drain. A fourth path used to end it: the model ending its own turn while backlog work remained. The SDK generator reports `StopAsyncIteration`, and `start` used to treat that as a normal exit, so the fastest-firing failure mode was the only one with no recovery, while a model going *silent* for the inactivity window (a slower form of the same failure) already earned a bounded fresh-session restart. A genuine end-of-run never reaches this path: the loop returns the moment a terminal sentinel is observed. `start` now raises an internal `_OrchestratePrematureTurnEnd` carrying the model's last result text, logs it at ERROR with its restart ordinal and cap, and opens a brand-new SDK session on the remaining backlog. This restart is bounded by its own `DEVBENCH_MAX_PREMATURE_TURN_END_RESTARTS` cap (default 10), deliberately far below the shared 1000-resume ceiling: quota and inactivity faults each self-throttle, whereas a model that ends its turn immediately can do so again immediately, so this cap is a cost guard and its exhaustion is itself the signal that a human is needed. A transport fault does not self-throttle either, and carries its own cap and backoff for the same reason (see below). Once exhausted, `start` fails fast and the `orchestrator_stop` notification carries the premature-turn-end stop class.

**Transport-error recovery (issue #331):** `start`'s SDK message loop treats a transient Claude Agent SDK transport failure as a bounded-restart case, joining drain, quota resume ([`docs/quota-handling.md`](quota-handling.md)), and the inactivity timeout (`timeouts.orchestrator_inactivity` -- see [`docs/devbench-yaml-reference.md`](devbench-yaml-reference.md)) as the orchestrator's named recovery paths. Any exception raised by the SDK generator boundary other than `StopAsyncIteration` or `TimeoutError` -- an upstream defect, a dropped connection, anything devbench does not already have a name for -- is classified structurally (by which call raised, never by the exception's message text) and re-raised as an internal `_OrchestrateTransportError`. `start` logs the verbatim upstream exception at ERROR with its restart ordinal and cap, then opens a brand-new SDK session on the remaining backlog (no conversation state is carried over, matching the quota-resume and inactivity-restart contract) rather than exiting. The restart is bounded by its own `DEVBENCH_MAX_TRANSPORT_RESTARTS` cap (default 14, `orchestrate.max_transport_restarts` in YAML), tracked with its own independent counter, so a transport restart never consumes quota-resume or inactivity-restart budget and vice versa.

**Each restart is preceded by exponential backoff**: `orchestrate.transport_restart_backoff_base_seconds * 2 ** restarts_already_done`, clamped to `orchestrate.transport_restart_backoff_max_seconds` (defaults 1.0s and 60.0s, so the waits run 1s, 2s, 4s, 8s, 16s, 32s, 60s...). The delay is recorded in the audit line as `backoff=<n>s`.

This cap and this pacing are deliberately NOT the shared 1000-resume ceiling they once were. That pairing was unsound: a quota window must elapse and an inactivity restart costs a full timeout window, so both self-throttle, but a transport fault imposes no delay of its own and recurs as fast as the SDK can reject a session. Retrying a 1000-restart budget with no delay spent it as fast as the transport could fail -- observed in the field as ~1000 restarts inside 39 minutes, after which the run ended and the daemon exited with no operator signal until someone read the log. A low bound plus spacing means a transient fault still recovers, while a persistent one fails fast and loudly, which is the intended signal.

Once the cap is exhausted, `start` re-raises the final exception verbatim and exits non-zero, and the `orchestrator_stop` notification carries the `transport-error-restart-cap-exhausted` stop class (see [`report`](#report) below for the matching `Transport restarts` row). Note the backoff ceiling also bounds how long an in-flight wait can delay a `devbench stop`. See [ADR-34](adr/34-orchestrator-transport-restart.md) for the full design record, including why classification must be structural rather than message-based.

**Daemon flag:**

- `--daemon, -d` -- detach the orchestrator into the background and return immediately (issue #209). `start` double-forks: the invoking shell prints `started devbench orchestrator in daemon mode (parent pid <pid>); follow logs with: devbench tail <instance_id> --follow` and exits at once. The intermediate first-fork child calls `setsid()` to become a session leader detached from the controlling terminal, then exits once the second fork completes; the resulting grandchild is deliberately not a session leader, which prevents it from reacquiring a controlling terminal. The grandchild redirects stdin from `/dev/null` and appends stdout/stderr to `<workspace>/logs/orchestrator.log`, then writes the same PID file a foreground run writes, at `<workspace>/.devbench/orchestrator.pid`, recording its PID, session name, mode (`"daemon"`), and start time; this is the file `devbench instances` walks to discover and list the running instance. `--daemon` requires POSIX (`fork()`); on a non-POSIX platform it fails fast with an actionable error before any daemonisation begins.

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

### `quota-watcher`

```
uv run devbench quota-watcher
```

Print the current quota-pause checkpoint, if any (spec FR-2.11, AC-28). No flags -- the plain invocation is the entire command surface. There is deliberately no `--daemon` background-monitor mode; that design was removed upstream in commit `9883d13`.

Reads `<workspace>/.devbench/quota_pause.json`, the checkpoint [`devbench start`](#start) writes when it enters a quota wait, and prints the pause details. The watcher is advisory: when a running orchestrator owns the session, its in-loop wait is authoritative -- this command only surfaces the same on-disk checkpoint for operator visibility (journey J-3: quota fires, operator runs `quota-watcher`, then `devbench status`).

**Exit codes:**

- **0** -- a checkpoint exists (the orchestrator is paused); details printed.
- **1** -- no checkpoint (not paused).

**Output when paused:**

```
[QUOTA_WAITING] reason=claude-code-cli reset_at=2026-05-23T16:10:00+00:00
```

`reset_at` prints `unknown` when the provider did not supply a reset time.

**Error handling:** a corrupt checkpoint (invalid JSON, a missing required field, or an unparseable timestamp) prints `load_checkpoint`'s `ValueError` message -- which names the checkpoint path -- to stderr and returns 1, never a Python traceback. An unreadable workspace path is checked before any read attempt and also exits 1 with the path named.

### `prepare-plugin-shadow`

```
uv run devbench prepare-plugin-shadow
```

Materialise the workspace-local shadow plugin (ADR-25) without launching anything and print its absolute path to stdout. Used by interactive launchers so the same per-agent model overrides apply when an operator drives the orchestrate skill manually:

```
claude --plugin-dir "$(uv run devbench prepare-plugin-shadow)"
```

When the workspace has no `agents:` overrides configured, prints the canonical plugin path; otherwise rewrites every overridden agent `.md` and symlinks the rest. Shares its implementation with `start`'s pre-flight so the two modes always produce identical plugin trees.

The YAML schema for the override block is shown below with each field set to the **current frontmatter default**. The defaults are tuned by the role each agent plays: `executor` (writes code under TDD) on `sonnet` for a fast happy path; the five judges (`code-reviewer`, `test-reviewer`, `doc-reviewer`, `changes-manifest`, `security-reviewer`) on `opus` because a bad verdict costs more than the inference savings; `blocker-resolver`, `manifest-amender`, `task-factory` on `opus` because they fire only on unhappy paths and a wrong call spins the recovery cascade; `review-supervisor` on `sonnet` because its post-flatten (ADR-33) role is read-only aggregation of already-persisted verdicts, not spawning -- a lighter model is sufficient for that task. `haiku` is rejected at config-load for all per-agent fields (caylent-solutions/devbench#198). Setting a field to its frontmatter default value is a no-op; flip individual fields when you need to retarget an agent (e.g., drop the judges to `sonnet` when opus quota is exhausted):

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

Every field defaults to `null` when absent (use the agent's `.md` frontmatter model). When `use_bedrock: true`, every value must be a Bedrock ARN (`us.anthropic.claude-<name>-<ver>-v<N>`); when `false`, values must be a short name (`opus`/`sonnet`) or an Anthropic API id (`claude-opus-4-7`). `haiku` is rejected at config-load time for all per-agent fields (caylent-solutions/devbench#198). `DEVBENCH_AGENT_MODEL_<NAME>` env vars (e.g. `DEVBENCH_AGENT_MODEL_EXECUTOR=opus`, `JUDGE_AGENT_MODEL_CODE_REVIEWER=opus`) override the YAML on a per-call basis (env > yaml > frontmatter).

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

- **`devbench drain --reason "<text>"`** -- same as the bare form, with a non-empty reason string embedded in the payload. The reason is stored verbatim and surfaced by `devbench status`, `devbench report`, and `devbench drain --status`.

- **`devbench drain --cancel`** -- withdraw the drain request. Deletes the drain signal from both the per-session path (when `DEVBENCH_SESSION_NAME` is set) and the workspace-root path so a writer at either location is cleared in one call (issue #212). Idempotent: exits 0 silently whether or not a signal file was present at either path. Cancelling while the orchestrator is mid-WU prevents the orchestrator from exiting at the next WU boundary -- it continues as if no drain was requested (AC-188-10). Filesystem failures propagate as unhandled exceptions.

- **`devbench drain --status`** -- print every pending drain signal and exit rc=0 in all cases (db-306):
  - No signal anywhere: prints `no drain pending`.
  - One or more signals present: prints one `DRAIN REQUESTED` line per pending signal -- the workspace-root signal first (if present), then per-session signals sorted by session directory name. This is the SAME resolver and line format rendered by the `status` and `report` banners: the workspace-root line reads `DRAIN REQUESTED: at <ISO-8601> by <user> (reason: <reason-or-none>)`, and each per-session line inserts the qualifier: `DRAIN REQUESTED [session=<name>]: at <ISO-8601> by <user> (reason: <reason-or-none>)`.

  The listing is unconditional and independent of `DEVBENCH_SESSION_NAME` -- it always reports every signal actually on disk (root plus every session directory), so an operator running `--status` from a shell with no session env var still sees a drain requested against a named session.

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

# Check current drain state (rc=0 either way; one DRAIN REQUESTED line per pending signal):
uv run devbench drain --status
# -> DRAIN REQUESTED: at 2026-05-14T13:55:01+00:00 by matt (reason: nightly cutover)
# -- or, with a named session also draining --
# -> DRAIN REQUESTED [session=alpha]: at 2026-05-14T13:56:12+00:00 by matt (reason: pausing alpha only)
# -- or, with nothing pending --
# -> no drain pending

# Withdraw the request before the orchestrator picks it up:
uv run devbench drain --cancel
# (no output; exits rc=0 whether or not a signal was present)

# Pre-arm: drop drain before start so orchestrator runs exactly one WU then exits:
uv run devbench drain
uv run devbench start --include "E1-F2-S3-T4"
# -> orchestrator claims E1-F2-S3-T4, completes it, detects drain, exits rc=0
```

**Status and report banners:** when one or more drain signals are pending, `devbench status` and `devbench report` each prepend one `DRAIN REQUESTED` line per pending signal (root plus every draining session) above their respective output, using the same format `devbench drain --status` prints above. See the [`status`](#status) and [`report`](#report) sections for the full banner format and per-command rendering details.

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
3. The SIGTERM handler in `cmd_start` intercepts the signal, writes an `[INTERRUPTED_ON_STOP] session=<name>` audit comment to the in-flight work unit, returns the work unit to `in-queue`, and exits with rc=0. The unit is released rather than blocked because a stop is not a dependency problem: the next run claims it again and the claim path restores whatever work was displaced, so an interrupted unit resumes instead of restarting.
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
# The in-flight WU now carries: [INTERRUPTED_ON_STOP] session=alpha

# Clean up the stale session entry:
uv run devbench sessions --cleanup
```

---

## Instances (per-host discovery)

`devbench instances` discovers every live devbench orchestrator process on the current host by walking a set of search roots for `.devbench/orchestrator.pid` files (issue #209). Discovery is scoped to the current host only (it never contacts other hosts); unlike [Named sessions](#named-sessions) (which enumerate sessions registered against ONE workspace), `instances` finds every orchestrator process reachable from the search roots on this host, regardless of which workspace started it.

### `instances`

```
uv run devbench instances [--json]
```

List every live devbench orchestrator instance on this host, one row per instance (`instance_id`, `pid`, `mode`, `session`, `workspace`, `started_at`). In table mode (the default), prints `no devbench orchestrator instances running` when the walk finds none; in `--json` mode an empty result prints `[]` instead of that message. Always exits 0 -- an empty result is not an error.

**Search roots (resolution order):** `DEVBENCH_INSTANCE_SEARCH_ROOTS` (colon-separated) when set; otherwise `$HOME` plus the current `DEVBENCH_WORKSPACE_ROOT`. When `DEVBENCH_WORKSPACE_ROOT` is already under `$HOME` it is not duplicated in the returned roots. This default keeps a workspace outside `$HOME` (for example, one checked out under `/workspaces`) discoverable with no configuration, while a workspace that already has `DEVBENCH_INSTANCE_SEARCH_ROOTS` configured sees identical behavior to before (obs-spec FR-D2 / OD-2).

**Flags:**

- `--json` -- print a JSON array of instance objects instead of the human-readable table. Each object carries `instance_id`, `pid`, `workspace`, `workspace_name`, `session`, `mode`, `started_at`, and `model` (the full workspace path is available only here; the table's WORKSPACE column shows `workspace_name`, the basename).

**Exit codes:**

| Scenario | rc |
|----------|----|
| Zero or more instances listed (table or `--json`). | 0 |
| Unknown flag supplied. | 2 |

**Worked example:**

```bash
# With DEVBENCH_INSTANCE_SEARCH_ROOTS unset, from a workspace outside $HOME:
DEVBENCH_WORKSPACE_ROOT=/workspaces/anywhere/workspace \
DEVBENCH_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
uv run devbench start --daemon

DEVBENCH_WORKSPACE_ROOT=/workspaces/anywhere/workspace \
DEVBENCH_CLAUDE_MODEL=us.anthropic.claude-opus-4-7-v1 \
uv run devbench instances
# INSTANCE_ID                     PID  MODE        SESSION         WORKSPACE                STARTED
# ----------------------------------------------------------------------------------------------
# workspace-3458                233458  daemon      default         workspace                 2026-07-28T18:40:38Z
# The WORKSPACE column shows the workspace basename (workspace_name); the full
# absolute path is available only in --json output.
```

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

#### Legacy list-shape migration (issue #270)

An older format wrote a bare JSON array of work-unit IDs directly to `scope.json` instead of the canonical object above. Rather than crashing on that shape forever, every scope.json read path migrates it in place to the canonical object form (empty `include`/`exclude`, the array as `expanded_ids`) with an atomic rewrite. The read paths that perform this migration are `ScopeFilter.from_file` (the scope-filtering path), the CLI scope-banner reader used by `devbench status`, `devbench report` and `devbench next`, and `devbench scope show`; all three delegate to the same shared migration routine, so no reader rejects the legacy list shape. A second read of the now-migrated file takes the ordinary object path -- the migration does not recur.

- **Operator-visible signal:** migration emits exactly one INFO line naming the migrated file. A write failure during the atomic rewrite is never swallowed: it propagates as an `OSError` on the `ScopeFilter.from_file` and scope-banner (`devbench status` / `devbench report` / `devbench next`) paths, while `devbench scope show` catches it and reports `ERROR: cannot read scope.json at <path>: <exc>` on stderr with exit code 1 instead of letting it propagate.
- **Provenance sourcing:** the migrated file's `started_at` and `started_by` are read from the sibling session-state files (the same `started_at` / `started_by` files a named session writes) when present. When either sibling file is absent, both fields are recorded as the explicit `"unknown"` sentinel. These values are never fabricated from the migration's own clock or the current OS user.
- **Empty-list reconciliation:** a migrated file whose array is empty (`[]`) is not a counter-example to the [multi-session-runs.md](multi-session-runs.md) invariant "An unscoped session writes no `scope.json`: absent is the unscoped signal every reader honours." Migration only repairs an already-present, corrupt legacy file -- it never creates a `scope.json` for an unscoped session. A migrated empty array leaves the file present on disk with an active (if empty) scope; that is a repaired-corruption case, not an unscoped-session write.
- Every other non-object top-level shape (string, number, null, bool) still raises the pre-existing `TypeError` naming the file path; the migration applies to the documented list shape only.

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

**Manifest-scoped (db-296).** Every git query below is restricted to an explicit `-- <manifest_paths>` pathspec, derived from the unit's real Changes Manifest rows (sentinels like `(none)` / `<verification-only>` filtered via `_is_real_manifest_path`). This is what keeps a sibling task's dirty residue in the shared checkout out of this unit's diff. Two fail-fast cases precede every query:

- A missing work-unit file or a malformed `## Changes Manifest` table exits 1 with `ERROR: Cannot scope diff for '<unit_id>': Changes Manifest is malformed: <exc>` (or, for a missing file, the equivalent "work-unit file not found" variant).
- An empty (verification-only) Manifest returns `(no changes)` immediately, before any git command runs -- never an unscoped whole-tree diff.

Mode-aware per ADR-12 (decision point 2 and its consequence superseded in place by db-247; see the ADR's "Correction" section):

- **Per-task-branch mode** (default, `git_ops.defer_pr: false`): emits staged + unstaged + `git diff origin/<default_branch>` + untracked hunks, all Manifest-scoped. Each work unit runs on its own branch, so the branch-vs-default diff IS the task's scope.
- **defer_pr mode** (`git_ops.single_branch: <branch>` + `git_ops.defer_pr: true`): emits staged + unstaged + untracked only, Manifest-scoped. When staged and unstaged are both empty the executor has just committed; instead of trusting HEAD (which may belong to a sibling task that committed later on the shared branch), this unit's OWN commit(s) are resolved via `git log --grep '^<unit_id>:' --format=%H` (matching every commit whose subject starts with the exact `<unit_id>: <title>` shape `git-ops` writes -- a task may carry more than one of its own commits, e.g. an initial commit plus a later `pr_review_resolution` fix commit) and emitted as `git show --format= <sha> -- <manifest_paths>` per commit. Zero matching commits fails fast (rc=1, no HEAD fallback):
  ```
  ERROR: get-diff (defer_pr, post-commit): no commit found for work unit '<unit_id>' on branch '<branch>'.
  The working tree is clean but no commit subject matches '^<unit_id>:'. This task's changes were not
  committed under its own name (possibly bundled into a sibling's commit, or the commit is missing).
  Inspect with: git log --grep '^<unit_id>:' --format='%H %s' in <repo_path>.
  ```
  The branch-vs-default hunk is deliberately skipped because it would include every prior completed task's commits on the shared branch.

Exit 0 on success; exit 1 when the work unit is not found, no local path is configured for its repo, the Changes Manifest cannot be resolved, or (defer_pr, post-commit) no commit matches this unit's own name. Output is `(no changes)` when every hunk is empty.

### `check-manifest-scope`

```
uv run devbench check-manifest-scope <id>
```

Read-only, deterministic wrapper around `assert_staged_matches_manifest`'s check (spec 4.C, db-296 x db-327): prints the staged paths that are NOT in the unit's Changes Manifest and exits non-zero when that set is non-empty; exits zero when the staged set is within the Manifest. Same malformed-Manifest ERROR as `get-diff` on a missing/malformed Manifest.

Exists because `get-diff` is now Manifest-scoped (above): a staged-but-unmanifested file no longer appears in the diff a judge reads, so the `changes-manifest` judge runs this verb to get a deterministic staged-vs-Manifest signal it cannot drift from. A non-empty result is an unconditional automatic REVIEW_FAIL for that judge (FR-11-A2), never a judged PASS.

### `check-reachability`

```
uv run devbench check-reachability <id>
```

Reachability gate (spec `integration-reality-gates-hardening.md` section 4.4; machine-blocking per `constants.GATE_TIERS`). Heuristic, language-agnostic evidence for `code-reviewer`'s `UNREACHABLE_ARTIFACT` check (`caylent-solutions/devbench-internal-backlog#10`). For every file in the unit's own Changes Manifest -- resolved through the shared `devbench.work_unit_scope.resolve_changed_files` (spec 4.3, AC-9), never a raw diff scan -- that is classified as source (extension in `devbench.source_classification.SOURCE_EXTENSIONS`, not a test/spec/story/fixture path, not an entry-point stem), derives candidate exported-symbol names (basename plus regex-extracted `export`/`def`/`class`/`func` names) and searches the rest of the target repo -- tracked and untracked -- for a word-boundary reference (`git grep --word-regexp --fixed-strings`, restricted to source-classified pathspecs). A symbol named `Card` is therefore never satisfied by `Cardinal` or `discardCards`, and a mention in `CHANGELOG.md` or a design doc can never clear an orphan (the search never looks at non-source extensions at all). A candidate with zero referrers at all prints `[POTENTIALLY UNREACHABLE]`. A candidate with at least one non-test referrer clears as `[OK]` (with the importer list, first 10 named then a remainder count) only when at least one of those referrers is itself transitively reachable from the configured `gates.reachability.entry_points` set (issue #10 AC2, spec 4.4 bullet 2) -- a list of repo-relative paths seeding the walk, each validated repo-relative at config load and required to exist in the checkout. Absent or empty `entry_points` falls back to the built-in `devbench.source_classification` entry-point-stem default (`main`, `app`, `index`, `__init__`, `setup`, `conftest`, `wsgi`, `asgi`), matched case-insensitively against each referrer's own basename stem. When every referrer is itself unreachable from the entry-point set, the candidate prints `[POTENTIALLY UNREACHABLE via orphan-chain]` instead of `[OK]`, naming the dead referrer(s), even though a non-test reference exists. A file outside the unit's own Changes Manifest is never itself a *candidate* (candidates come solely from `work_unit_scope.resolve_changed_files`), but such a file IS named inside a finding whenever it acts as a referrer: the `[OK]` and `[POTENTIALLY UNREACHABLE via orphan-chain]` importer lists above name every referrer regardless of Manifest membership, and (see the `[LOAD_ERROR]` paragraph below) a referrer met during the entry-point walk that cannot itself be read is also named, even though it is never a candidate.

This is a candidate-surfacing tool, not a final verdict: a grep miss can be a false positive (dynamic `import()`, a barrel re-export the regex missed, a lazy route split). `code-reviewer` makes the final judgment call from this evidence. There is no source-comment escape hatch: an operator records a legitimate deferral with `uv run devbench log-waiver <judge> <unit-id> --gate reachability --target <t> --reason <r> --operator` (spec 4.9, PM-5), which always leaves an audited `[GATE_WAIVER reachability]` record -- no path clears an artifact silently.

`git grep` rc semantics (spec 4.4, Section 7): rc=1 (no match) is data, never an error; rc>=2 is a loud plumbing failure that prints `ERROR: git grep failed: <stderr>` on stderr and exits 1 (never swallowed by a `continue`). A classified candidate that cannot be read -- a permission failure or a non-UTF-8 decode failure alike -- prints `[LOAD_ERROR]` naming the CANDIDATE and the error, and is counted in the status line's `findings` total; there is no silent skip. The same fail-loud contract also covers a referrer met while walking the entry-point graph for a candidate that WAS read successfully: if that referrer (or a referrer of a referrer, and so on) cannot itself be read, `_is_reachable_from_entry_points` raises rather than guessing a `True`/`False` reachability verdict, and `[LOAD_ERROR]` is printed naming that REFERRER, not the candidate under examination -- the candidate yields no `[OK]`, `[POTENTIALLY UNREACHABLE]` or orphan-chain block at all in that run, since its own verdict could not be computed, and the run still exits 1 with the referrer's `[LOAD_ERROR]` counted in `findings`. A Manifest path with no on-disk file (e.g. one a prior stage of the same unit already deleted, per the complete-replacement standard) is never a candidate in the first place and is never reported at all -- a deleted artifact cannot be an orphan.

Prints the spec 5.2 gate status line as the FIRST stdout line. When the gate is disabled (or unconfigured) for the unit's repo: `{"gate": "reachability", "status": "disabled"}`, exit 0 (spec 4.1 final bullet, AC-4). When enabled: `{"gate": "reachability", "tier": "machine-blocking", "status": "pass"|"fail", "findings": <int>, "scope_hash": "<sha256>"}`, followed by the human-readable findings.

| Exit code | Meaning |
|---|---|
| 0 | Gate disabled for the unit's repo, or an enabled run found zero findings. |
| 1 | Work unit not found, no local path configured for its repo, the config file failed to load (including a `gates.reachability.entry_points` element that is absolute or contains a `..` segment), a configured `entry_points` path that does not exist in the repo checkout (`ERROR: gates.reachability.entry_points names a path that is not present in the repo: <path>`, checked before any candidate is examined), a malformed `[GATE_WAIVER reachability]` marker on the unit (`ERROR: malformed [GATE_WAIVER reachability] marker in <unit-id>: <detail>`, checked before scope is resolved, no status line printed), `work_unit_scope.resolve_changed_files` raised (no status line printed), `git grep` exited rc>=2 (no status line printed), the work-unit file could not be read to check for waivers or could not be written to persist a passing record (no status line printed), or an enabled run has at least one `[POTENTIALLY UNREACHABLE]` / `[POTENTIALLY UNREACHABLE via orphan-chain]` / `[LOAD_ERROR]` finding. |

**Persisted machine record (spec 4.2, 4.4 final bullet).** A clean enabled run (`findings: 0`, at least one file in the unit's Changes Manifest) appends exactly one `[GATE_PASS reachability] <iso-utc> <scope-hash>` line to the unit's audit section -- the `<scope-hash>` is identical to the status line's `scope_hash`, computed by `devbench.gate_records.compute_scope_hash` over the sorted Changes Manifest file list plus each file's current git blob hash, so any later edit to an in-scope file invalidates the record. `devbench.gate_records.compose_gate_pass_record` is the sole authorized BUILDER of that marker text -- `check-reachability` never hand-formats it, and no other command composes one. A failing run, or a disabled gate, writes no record.

**`mark-done` requirement.** When `gates.reachability.enabled` is `true` for the unit's repo, `mark-done` refuses (exit 1, writes no status) unless the unit carries a fresh `[GATE_PASS reachability]` record or an operator-attributed `[GATE_WAIVER reachability]` marker:

```
$ uv run devbench mark-done E9-F1-S1-T1
ERROR: done-gate: gate 'reachability' is enabled for repo 'caylent-solutions/devbench' but has no
[GATE_PASS reachability] record for E9-F1-S1-T1. Run: uv run devbench check-reachability E9-F1-S1-T1
```

Editing any Manifest file after the record was written re-derives a different scope hash, so the stale record no longer satisfies the gate:

```
ERROR: gate 'reachability' record is stale (scope changed since it ran). Run: uv run devbench check-reachability E9-F1-S1-T1 to produce a fresh record.
```

An operator-attributed `[GATE_WAIVER reachability]` marker (see [`log-waiver`](#log-waiver)) satisfies the requirement in place of a record, and does so even when an existing record has gone stale (spec Section 3.6: the operator is the only waiver authority for a machine-blocking gate); an executor-attributed waiver alone is never sufficient.

**Waiver adoption (spec 4.9, Section 2 G7).** Before scanning, `check-reachability` reads every `[GATE_WAIVER reachability]` marker on the unit via `devbench.gate_records.gate_waiver_targets`, the module's per-target reader built on `gate_records.gate_waiver_records` -- the sole scan-and-parse loop for the `[GATE_WAIVER <gate>]` marker family (also consumed by `mark-done`'s generic gate-record invariant for its own whole-gate bypass). Because reachability is machine-blocking (spec Section 3.6/D-6), only an OPERATOR-attributed record clears a candidate: it is rendered `[WAIVED] <target> -- <reason>` instead of `[OK]` / `[POTENTIALLY UNREACHABLE]` / `[LOAD_ERROR]`, is excluded from the blocking `findings` count, and the run exits 0 when every finding is waived this way. A target with only an executor-attributed `[GATE_WAIVER reachability]` marker on file is scanned normally, exactly as if no waiver existed -- an executor cannot self-certify a waiver for a machine-blocking gate. Clear a candidate this way with:

```
$ uv run devbench log-waiver code_review E9-F1-S1-T1 \
    --gate reachability --target src/ui/LegacyPanel.tsx \
    --reason "mounted via route-split registry resolved at runtime" --operator
```

A malformed `[GATE_WAIVER reachability]` marker (missing target, missing/empty reason) is never silently treated as "no waiver": the run fails loud with `ERROR: malformed [GATE_WAIVER reachability] marker in <unit-id>: <detail>` naming the offending line, and prints no status line.

### `run-tests`

```
uv run devbench run-tests <id>
```

Run the test suite in the work unit's target repo. Uses the repo's `make test` target when present; falls back to bare `pytest`. Used by `test_review`. Returns the test runner's exit code.

### `check-fixture-consistency`

```
uv run devbench check-fixture-consistency <id>
```

Cross-reference the work unit's target repo's mock/fixture files against a workspace-designated canonical fixture/dataset (caylent-solutions/devbench-internal-backlog#17, fixture-catalog cross-reference lint). Catches the pattern where a feature's data-fetch logic is correct but reads from a mock lookup table whose keys were fabricated, keyed in the wrong namespace, or left incomplete relative to the project's canonical shared fixture data -- functionally dead or crash-on-save for real records even though the underlying logic is sound.

**Opt-in and project-specific.** devbench cannot infer a target repo's fixture-file layout, so this is a deliberate no-op (prints a skip note, exits 0) unless the workspace configures `gates.fixture_consistency.canonical_sources` in `backlog/config/devbench.yaml`:

```yaml
gates:
  fixture_consistency:
    canonical_sources:
      - path: tests/fixtures/catalog.json   # repo-relative path to the canonical dataset
        identifier_field: sku               # key whose values other fixtures must reference
        expected_count: 24                  # optional: assert full backfill coverage
    scan:
      - path: tests/fixtures/mock_catalog_lookup.json
        identifier_field: sku
        # canonical_source: tests/fixtures/catalog.json  # required when >1 canonical_sources entry
        allow_missing:                      # opt-out for intentional edge-case fixtures
          - SKU-DOES-NOT-EXIST
```

Exit 0 when every `scan` target's identifier values (other than those listed in `allow_missing`) are present in their `canonical_source`, and every canonical source's distinct-identifier count matches its `expected_count` (when set). Exit 1 with one `[missing_key|coverage_shortfall|load_error]` finding line per problem otherwise. Used as review evidence by `test_review` (rejection-feedback code `FIXTURE_CATALOG_MISMATCH`; see `docs/review-feedback-vocabulary.md`).

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

`<judge>` must be one of the names in the allowlist defined by `devbench.constants.KNOWN_JUDGE_NAMES`. The allowlist is split into two tiers:

- **Canonical reviewers (5)** -- `code_review`, `test_review`, `doc_review`, `changes_manifest`, `security_review`. Only these names satisfy the done-gate's `BacklogManager._last_round_all_passed` check. Each of the four review-team judges self-logs its own verdict (`code_review`/`test_review`/`doc_review`/`changes_manifest`), and `security-reviewer` writes `security_review`; `review-supervisor` does not write any of the five, it only reads them back to aggregate.
- **Audit-only workflow agents (4)** -- `executor`, `blocker_resolver`, `manifest_amender`, `task_factory`. Their verdicts land in the work-unit Comments section as audit metadata but do NOT count toward the done-gate. Workflow agents use these to record progress (for example, the executor logging `executor` verdicts during AC enforcement, or task-factory recording `task_factory` after a successful materialise).

Two enforcement layers prevent malformed audit rows:

1. **CLI layer** (`cmd_log_verdict`): refuses any `<judge>` outside `KNOWN_JUDGE_NAMES` with a clear error naming the valid choices. Catches typos like `judge` (literal) or hyphenated forms like `code-reviewer`.
2. **Hook layer** (`guard-verdict-format.sh`, PreToolUse): mirrors the same allowlist, plus an additional **executor scope** rule -- when the calling agent's `agent_type == "devbench:executor"` AND the judge is one of the canonical 5 reviewers, the hook blocks. The executor is an authoring agent, not a reviewer; the audit-only `executor` judge name remains allowed (records progress without satisfying the gate). The four review-team judges and security-reviewer can still write their own canonical reviewer verdicts.

Override env var: none -- this is a security/correctness gate, not a tunable. If a legitimate use case needs to write a verdict outside the allowlist, extend `KNOWN_JUDGE_NAMES` in `src/devbench/constants.py` AND update `KNOWN_JUDGES` in `plugin/devbench-orchestrate/scripts/guard-verdict-format.sh` (the two lists must stay in sync).

#### Retry-budget enforcement (issue #122)

A `fail` verdict from one of the **canonical reviewers** also enforces that judge's executor retry budget, so a review-rejection loop is bounded instead of able to repeat indefinitely.

- The count is the number of `[judge/<name>] [REVIEW_FAIL]` rows already in the work-unit file, so the audit trail is the counter -- there is no separate state to drift.
- The budget is that judge's entry in [`max_executor_retries_per_judge`](devbench-yaml-reference.md) when listed, otherwise the global `max_executor_retries` (env override `DEVBENCH_MAX_RETRIES`).
- On exhaustion the command appends a `[BLOCKED] [RETRY_BUDGET_EXHAUSTED]` audit row, forces the unit to `blocked`, and sends the operator-action-required notification. The tag is what makes [`block-types.md`](block-types.md) classify the unit `OPERATOR_ACTION_REQUIRED` rather than `AWAITING_AMENDMENT_RECOVERY`.
- The emitted JSON carries `retry_budget_exhausted`. When `true` the unit is already blocked: do not re-invoke the executor for it and do not write a second `[RETRY_BUDGET_EXHAUSTED]` row.
- **Audit-only workflow agents never charge a budget** -- they do not own a review gate, so their verdicts cannot block a unit.

Below budget, behaviour is unchanged from a plain verdict write.

### `config-resolve`

```
uv run devbench config-resolve <field> [<field>...]
```

Print fully-resolved runtime-config values (env > YAML > built-in default) as one-line JSON, so an agent can read a setting without re-deriving the precedence chain or assuming a default.

`<field>` names are `RuntimeConfig` attributes, for example `max_executor_retries`, `max_executor_retries_per_judge`, `manifest_amendment`. Nested sections are returned as JSON objects.

```
$ uv run devbench config-resolve max_executor_retries max_executor_retries_per_judge
{"max_executor_retries": 10, "max_executor_retries_per_judge": {}}
```

An unknown field name exits non-zero and lists the valid choices; it never returns a silent `null`, which would read as "configured empty" and hide a typo. Calling with no field name is likewise an error.

### `log-comment`

```
uv run devbench log-comment <agent> <id> <message>
```

Append a non-verdict agent comment to the work-unit file's `## Comments` section with a timestamp and agent name prefix. Blocked by the `guard-comment-format.sh` hook when the message contains control-language imperatives (for example `halt orchestration`, `operator action required`); see [docs/faq.md](faq.md) for the rule and rationale.

### `log-tdd`

```
uv run devbench log-tdd <id> <RED|GREEN|REFACTOR> <message>
```

Append a TDD phase entry to the work-unit's `## TDD Cycle Log` section. `devbench.constants.VALID_TDD_PHASES` names five phases -- `RED`, `GREEN`, `REFACTOR`, `RED_OBSERVED`, `GREEN_GREEN_OBSERVED` -- but this agent-facing verb accepts only the agent-writable subset (`devbench.constants.AGENT_WRITABLE_TDD_PHASES`): `RED`, `GREEN`, `REFACTOR` (case-insensitive). The other two are `devbench.constants.ORCHESTRATOR_ONLY_TDD_PHASES`.

`RED_OBSERVED` is orchestrator-only. An agent invocation naming `RED_OBSERVED` -- for example `uv run devbench log-tdd <id> RED_OBSERVED <message>` -- is always rejected: `cmd_log_tdd` exits 1 and writes nothing to the `## TDD Cycle Log` section, printing:

```
ERROR: TDD phase 'RED_OBSERVED' is orchestrator-only and cannot be written via log-tdd; agent-writable phases are: GREEN, RED, REFACTOR.
```

The `RED_OBSERVED` entry itself is written exclusively by the orchestrator's internal `write_red_observed_entry` function after it independently runs the test suite and observes a nonzero exit code; there is no `log-tdd-red-observed` CLI subcommand an agent could invoke. The record is a fixed three-field message, not free text -- every field in `devbench.constants.RED_OBSERVED_RECORD_FIELDS` is required: `exit_code` (the test-runner's observed exit code), `test_node_id` (the failing pytest node ID), and `failure_digest` (a hash-shaped digest of the failure output). A record missing any field is rejected before it is written, naming the missing field:

```
RED_OBSERVED record is missing required field '<field>'.
```

`GREEN_GREEN_OBSERVED` (FR-4.6, E4-F4-S1-T2) is the second orchestrator-only phase and is subject to the identical rejection path when an agent names it via `log-tdd`:

```
ERROR: TDD phase 'GREEN_GREEN_OBSERVED' is orchestrator-only and cannot be written via log-tdd; agent-writable phases are: GREEN, RED, REFACTOR.
```

The `GREEN_GREEN_OBSERVED` entry is written exclusively by `uv run devbench green-green-check` (see below) after it independently reconstructs the pre-change ("before") state and confirms the named tests pass on both sides; there is no `log-tdd-green-green-observed` CLI subcommand an agent could invoke.

### `green-green-check`

```
uv run devbench green-green-check <id> <test_node_id> [<test_node_id> ...]
```

Each `<test_node_id>` must be a fully-qualified pytest node id -- there is no single fixed shape, since the shape depends on how the test is defined. Accepted forms:

- `<path>.py::<test_name>` (a module-level test function, e.g. `tests/test_foo.py::test_bar`).
- `<path>.py::<Class>::<test_name>` (a test method nested in a class -- the common case in this repo, since most files under `tests/` define their tests inside a `class Test*`, e.g. `tests/test_foo.py::TestFoo::test_bar`).
- `<path>.py::<Class>::<test_name>[<param>]` (a parametrized test method, with its `pytest.mark.parametrize` id suffix, e.g. `tests/test_foo.py::TestFoo::test_bar[case-1]`).

A bare test *file* path is not accepted, and neither is a two-segment id for a class-nested test (it is missing the class segment): `devbench.tdd_gate.default_pytest_runner` scopes the run to the file but then matches the exact node id against the `-rA` outcome line via `_parse_node_outcome`, so anything short of the exact node id pytest itself would print on that `PASSED` line yields `node_outcome=None` and the check fails closed with "could not collect test". The authoritative source for a given test's node id is what pytest reports for it -- run `pytest <path>.py --collect-only -q` (or `-rA`) and copy the emitted id verbatim rather than hand-deriving it from the file's source.

Orchestrator-only helper that machine-observes the FR-4.6 done-gate precondition for `refactor`-type work units: the named test(s) must PASS in the current ("after") working tree state AND PASS again in a reconstructed "before" state (the production-source `## Changes Manifest` rows stashed out, path-scoped, so only the refactor's own production edits are reverted -- test files and unrelated files are left as-is). This proves the refactor changed implementation without changing observable behavior for the tests that pin it.

On success, appends an orchestrator-only `GREEN_GREEN_OBSERVED` entry to the work unit's `## TDD Cycle Log` section and exits 0. `BacklogManager.mark_done`'s `_check_task_type_done_invariant` requires this record before a `refactor` task can reach `done` (see `mark-done` above).

Exits 1 (writing nothing) when: the tree is dirty outside the declared Changes Manifest; the Manifest contains no production-source rows to reconstruct a "before" state from; the stash push finds no uncommitted production-source change to reconstruct a "before" state from (a refactor's change must still be uncommitted in the working tree when this check runs); the stash operation fails; or any named test fails or fails to collect on either the "after" or the reconstructed "before" side (the check fails closed on collection failure rather than treating a missing test as vacuously passing). The stash is always restored, success or failure.

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
- **Manifest-scope:** staged files must exactly match the work unit's Changes Manifest (AC-FINAL-015). `git-ops` stages only the paths the work unit's own Changes Manifest declares -- it no longer runs a whole-tree `git add -A`. A caller that cannot resolve a Manifest for the unit, or whose Manifest holds only execution-time sentinels, is refused (exit 1) rather than silently given a whole-tree commit; previously this case warned and committed anyway. `git-ops-finalize`, which batches many units and legitimately has no single Manifest, opts into whole-tree staging explicitly via its `stage_all` behaviour -- this is the one intentional exception to Manifest-scoped staging.
- **Branch-anchor:** HEAD must be on the expected branch (prevents orphan-branch commits).
- **Orphan-pattern:** no staged or already-tracked path may match a build/state ignore pattern (terraform state and plan output, terragrunt cache, Python pycache / `.venv` / `*.egg-info`, coverage artefacts, ansible `*.retry`, helm `charts/*.tgz`, `node_modules`, `.DS_Store`). The default behaviour (Phase 1 of the orphan-cascade fix) is **inline cleanup**: git-ops runs `cleanup_tracked_orphans` programmatically, commits the result as a devbench-authored chore commit (canonical message `chore(cleanup): untrack devbench-managed orphan paths and update .gitignore`), then continues with the original task's commit on the same invocation. Two commits land on the task's branch; the executor's staging is preserved (filtered to exclude orphan paths). When the operator sets `DEVBENCH_DISABLE_INLINE_ORPHAN_CLEANUP=1`, git-ops falls back to the legacy proposal flow (cleanup-as-task) with cross-task de-duplication so two parents detecting the same orphan set wire to the SAME cleanup task. Override the active pattern list per backlog via `git_ops.orphan_patterns` in `devbench.yaml` (a YAML list) or `DEVBENCH_ORPHAN_IGNORE_PATTERNS` (comma-separated fnmatch globs); the env var wins, and either REPLACES the built-in list wholesale rather than extending it, so a workspace that declares one owns the complete set. Dependency LOCK files (`uv.lock`, `package-lock.json`, `poetry.lock`, `Cargo.lock`, `go.sum`, `.terraform.lock.hcl`, `Chart.lock`) are deliberately NOT in the built-in list -- they pin resolved versions and belong in version control, so untracking one is a reproducibility regression rather than cleanup.

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

#### Head-SHA-pinned check quorum (db-328)

Once `gh pr checks --watch` returns `rc == 0` (including after the workflow-registration retry above resolves), `wait_for_checks` does NOT declare the PR green on that return code alone: on a many-job repo the watch call can return success after an early subset of check-runs completes while later jobs are still queued. Instead, `GitOpsService._confirm_check_quorum` gates the verdict on a head-SHA-pinned, stability-confirmed quorum:

1. Resolve the PR's current head commit SHA via `gh pr view <n> --json headRefOid` (`GitOpsService._resolve_pr_head_sha`). A head SHA that cannot be resolved raises `RuntimeError` rather than falling back to an assumed-green verdict.
2. Poll `gh api repos/<repo>/commits/<sha>/check-runs --paginate` until: at least one check-run is present, every check-run's `status` is `completed`, every `conclusion` is in `{success, neutral, skipped}`, and the set of check-run ids is unchanged across `DEVBENCH_CHECK_QUORUM_STABLE_POLLS` consecutive polls (a check-run that first appears mid-poll resets the stability counter).
3. Any completed check-run with a conclusion outside the good set (`failure`, `cancelled`, `timed_out`, `action_required`, or any other value) fails the quorum immediately.
4. If the check-run set never stabilizes within the timeout budget, the merge is refused (no warn-and-pass fallback), and the log names the PR number, the pinned head SHA, and the `gh api` command an operator can re-run to inspect the stuck runs.

Local `.github/workflows/*.y[a]ml` file counts are never used as the quorum source: `on:`/path filters skip files, and a single workflow file can fan out to a variable number of check-runs.

| env var | purpose | scoping | default source |
|---------|---------|---------|-----------------|
| `DEVBENCH_CHECK_QUORUM_STABLE_POLLS` | number of consecutive polls the check-run id set must stay unchanged, with every run completed and every conclusion good, before the quorum is declared stable | env-only (no `devbench.yaml` field; no `DEFAULT_*` entry in `constants.py`) | module-private constant `_DEFAULT_CHECK_QUORUM_STABLE_POLLS = 3` in `src/devbench/config.py` |
| `DEVBENCH_CHECK_QUORUM_POLL_INTERVAL_SECONDS` | seconds slept between successive `gh api .../check-runs` polls | env-only (no `devbench.yaml` field; no `DEFAULT_*` entry in `constants.py`) | module-private constant `_DEFAULT_CHECK_QUORUM_POLL_INTERVAL_SECONDS = 5` in `src/devbench/config.py` |

Both knobs resolve with the standard **env > default** precedence (`devbench.config._resolve_int`); since neither has a YAML field, the middle tier of the usual env > YAML > default chain is always skipped for these two.

### `git-ops-finalize`

```
uv run devbench git-ops-finalize <repo> [--provenance <path>]
```

Single-branch mode only: push the shared branch and create one PR for every accumulated commit. Use once, after every work unit targeting this repo is done. See [architecture.md §6](architecture.md#6-multi-pr-vs-single-pr-mode) for the full single-branch mode reference. See [`git-ops-finalize --provenance`](#git-ops-finalize---provenance) in [Gates](#gates) for the optional flag, the config-key equivalent, and the provenance-map format it reads.

Not applicable under `git_ops.local_only: true` -- the target repo has no remote to push to. The local single branch is the deliverable; running `git-ops-finalize` against a local-only workspace is an error.

**Slack notifications** (issue #219): when the operator has the corresponding `notifications.events.*` toggle enabled, `git-ops-finalize` fires `pr_opened` immediately after `gh pr create` succeeds, then fires `ci_failure` (FAILED_KNOWN_TASK or FAILED_UNKNOWN) or `ci_pass` (GREEN) when the CI watch resolves. `pr_merged` is NOT fired from this path because `auto_merge: false` leaves the squashed PR open for manual merge. The new `ci_pass` toggle defaults to `false` on upgrade.

### `check-merge`

```
uv run devbench check-merge <id>
```

Issue #101 reconciliation step for `pause_before_merge: true` workspaces. Queries `gh pr list --head <branch> --json number,state,merged,url` for the PR associated with the work unit's branch and dispatches:

- **PR merged externally**: promote the work unit to `done` via the existing done-gate (every required judge must have passed in the most recent round, AND the task's own `## Task Type:` completion invariant is satisfied). Logs `[PR_MERGED]` audit comment.
- **PR closed without merge**: transition the work unit to `blocked` with a `[BLOCKED]` audit comment naming the PR.
- **PR still open**: no-op; the orchestrator's loop picks the work unit up again on the next iteration.
- **No PR found for branch**: prints `{"pr_state": "no-pr-found"}` and returns 0; the orchestrator treats it the same as "still open".

Returns rc=0 in every normal case; rc=1 only on hard failure (gh API failure, malformed JSON, done-gate refusal). Output is a single JSON line so the orchestrator skill's step 1b reconciliation can parse it.

**Same task-type invariant as `mark-done` (FR-4.5/FR-4.6, E4-F4-S1-T2).** The merged-PR path promotes through the identical `BacklogManager.mark_done` call `cmd_mark_done` uses -- not a separate, possibly-divergent status write -- so a gated task (`behavior-fix` / `feature`) merged externally with no `RED_OBSERVED` record, or a `refactor` task merged with no `GREEN_GREEN_OBSERVED` record, is refused (rc=1) exactly as `mark-done` would refuse it. See `mark-done` above for the full invariant.

### `check-ancestry`

```
uv run devbench check-ancestry <id> <dependency-ref> [<target-ref>]
```

Ancestry gate (spec `integration-reality-gates-hardening.md` section 4.5; machine-blocking per `constants.GATE_TIERS`). **Canonical dependency-deliverability check** for "is a declared prerequisite deliverable" across the pipeline: any tooling that needs the same answer should shell out to this command (see [`cross-backlog-dependencies.md`](cross-backlog-dependencies.md)) rather than inventing a weaker proxy (e.g. checking for a local snapshot/report file).

Gate enablement is read exclusively through `resolve_gate_config("ancestry", repo)` (spec 4.1's single read path). When the gate is disabled (or unconfigured) for the unit's repo, stdout is exactly `{"gate": "ancestry", "status": "disabled"}` and rc is 0, before any git call. On an enabled run that reaches a terminal probe decision (strict pass, squash-PR pass, or BLOCKED), the spec 5.2 status line is the FIRST stdout line: `{"gate": "ancestry", "tier": "machine-blocking", "status": "pass"|"fail", "findings": <int>, "mode": "strict"|"squash-pr"|"none", "dependency_ref": "<ref>", "target_ref": "<ref>"}`, followed by both probes' human-readable outcomes. An enabled run that hits a hard failure before a terminal probe decision (fetch failure, `git rev-parse`/`gh` failure, unparseable probe JSON, or a merge-base rc>=2 evaluation error) prints only an `ERROR:` line on stderr and no status line.

**Two-probe contract (317-D02).** A strict `git merge-base --is-ancestor <dependency-ref> <target-ref>` probe runs first in the work unit's target repo.

- rc=0 (dependency IS an ancestor): the gate passes with `mode: "strict"`.
- rc=1 ("not an ancestor" -- the strict, commit-graph-only answer a squash-merged, rebased, or fix-pack-landed dependency can never satisfy): a second probe searches for the dependency's merged PR via `gh pr list --search "<sha>" --state merged --base <default-branch> --json number,mergedAt,title`, where `<sha>` is `dependency-ref` resolved through `git rev-parse`. Finding a merged PR passes the gate with `mode: "squash-pr"`. Finding none blocks the gate (`status: "fail"`, `mode: "none"`) with a `BLOCKED` message on stderr.
- rc>=2 (or the `run_command` sentinel 127 for a missing/timed-out git): git itself could not answer the question -- an evaluation failure, never reported as "not merged". No status line is printed and the squash-PR probe never runs.

Both probes' outcomes are always printed together on every terminal decision reached through a genuine probe result (spec 3.5 fallback ban -- never a silent hand-off from one probe to the other, and never a single probe's result standing in for the whole decision).

**Prerequisite:** the squash-PR probe requires an authenticated `gh` CLI in the execution environment. Its absence, or an unauthenticated session, is a hard `gh pr list` failure -- `ERROR: squash-merge probe failed for '<dependency-ref>': <stderr>` on stderr, exit 1 -- not a silent skip of the second probe.

`dependency-ref` should be a fully qualified, fetchable ref (e.g. `<remote>/<dependency-branch>` or a commit SHA); this command does not invent a remote-tracking prefix for a bare branch name. `target-ref` defaults to `<remote>/<default-branch>` when omitted, where `<remote>` is resolved from the repo's own git configuration (`git config --get branch.<default-branch>.remote`) rather than assumed to be the literal `origin` -- a repo whose tracking remote uses a different name is never silently checked against a ref that does not exist. `git fetch <remote>` runs before either probe so a stale local view cannot produce a false answer; a fetch failure is FATAL (spec 3.5): `ERROR: git fetch '<remote>' failed: <stderr>` on stderr, exit 1, and neither probe runs against stale refs.

| Exit code | Meaning |
|---|---|
| 0 | Gate disabled for the unit's repo, or an enabled run passed (either probe). |
| 1 | Work unit not found, no local path configured for its repo, the config file failed to load, the default branch or its configured tracking remote could not be resolved, `git fetch` failed, the squash-PR probe itself could not run (`git rev-parse` or `gh pr list` failed, or its output could not be parsed as the expected JSON array), `git merge-base --is-ancestor` returned rc>=2 (an evaluation failure, never "not merged"), or a BLOCKED result (neither probe confirmed the dependency merged). |
| 2 | Usage error: an empty/whitespace `dependency-ref`. |

This command is not invoked automatically by the orchestrator skill's main loop. The "runs on every `in-review` work unit at the top of each loop iteration when `git_ops.pause_before_merge: true`" reconciliation described here previously belongs to [`check-merge`](#check-merge) (issue #101), not `check-ancestry` -- `plugin/devbench-orchestrate/skills/orchestrate/SKILL.md` step 1b calls `uv run devbench check-merge <id>` for that reconciliation. `check-ancestry` is instead embedded in a generated ancestry-gate task's `### Approach` (see [`cross-backlog-dependencies.md`](cross-backlog-dependencies.md)) and re-evaluated each time that task is (re-)attempted, like any other Task's Approach.

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
  "files_to_add": [{"path": "src/path/to/file.py", "change": "minimum fix for AC-TEST-001"}],
  "files_to_remove": ["tests/test_stale.py"],
  "justification": "...",
  "linked_acs": ["AC-TEST-001"]
}
EOF
```

Register an amendment request at `<workspace>/.devbench/amendments/<id>.json`. Payload is JSON on stdin. `reason`, `justification`, `files_to_add` and `linked_acs` are required (`files_to_add` entries are `{path, change}` objects, not bare strings); `files_to_remove` is optional. At least one of `files_to_add` / `files_to_remove` must be non-empty -- a request that changes nothing is rejected as a no-op.

Invoked by the executor during TDD GREEN when a production fix is needed but out of manifest scope, and by the review-fix path when a judge requires a Manifest correction.

**Every Layer 1 pre-filter check runs before the request is written**, so a request that cannot be approved never reaches disk and never occupies the single pending-request slot. The checks are deterministic: the workflow must be enabled, the `reason` must be in this backlog's configured [`manifest_amendment.allowed_reasons`](devbench-yaml-reference.md), the rate limit must allow it, the task must be `in-progress`, `linked_acs` must exist, added files must not already be declared and must be present in the staged diff, and removals must satisfy the rule below.

**`files_to_remove` and `AC-FINAL-015`.** [`AC-FINAL-015`](acceptance-criteria-canonical.md) requires the Changes Manifest to match the files git changed *exactly* -- no extra, no missing -- so a declared row whose file ends up with a zero-line diff (its work having landed under a sibling unit, for instance) is a real violation that `changes_manifest` fails the unit for. `files_to_remove` is how a unit complies.

A row may only be dropped when its file has **no staged, unstaged, or untracked changes**. That is the safety property: the Manifest row is the only thing authorising a file to appear in the unit's commit, so permitting removal for a file with real changes would let work leave the unit's reviewed scope. A dirty path is refused with an error naming it. Removals are also recorded in the `[AMENDMENT_APPLIED]` audit row, so a dropped row is never invisible to a reviewer.

### `apply-amendment`

```
uv run devbench apply-amendment <id>
```

Atomically update the Changes Manifest after the `manifest-amender` judge approves. Runs a deterministic Layer 3 post-check (em-dash scan plus `validate-backlog`) and rolls back on any failure, so a failed post-check cannot leave the backlog half-updated.

Removals and additions are applied inside the **same** atomic write and rollback envelope, so a post-check failure restores the Manifest whole rather than leaving it half-amended. The `reason` is re-checked here against the same configured `allowed_reasons` the request passed, so hand-editing a pending request on disk between `request-amendment` and this command cannot smuggle in a reason the backlog disallows.

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

See [task-factory.md](task-factory.md) and ADRs [03](adr/03-task-factory.md), [06](adr/06-validation-gate-bug-escalation.md), [07](adr/07-auto-requeue-on-proposal-completion.md), [08](adr/08-proposal-lifecycle-observability.md), [09](adr/09-idempotent-materialise-proposal.md) for the full design. This workflow is opt-in: enable with `task_factory.enabled: true` in `backlog/config/devbench.yaml`.

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

**Idempotent (ADR-09).** Every task classifies through `classify_proposed_task` first; the call skips anything in state `PROPOSED`, `PROMOTED`, `DONE`, `DECLINED`, or `REJECTED`. Only `UNMATERIALISED` tasks are created. Safe to re-run after a partial materialisation or after rejecting a draft from the same JSON. Output JSON includes a `skipped` map so the operator sees why a no-op call was a no-op.

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

Refuses an edge that would close a dependency cycle, naming the offending chain (`A -> B -> A`), rather than writing it and letting `validate-backlog` report it on a later sweep with nothing in either work-unit file naming the edge responsible. The check runs the same detector `devbench next` uses, against the graph the edge would produce. Wire the edge in the other direction, or break the existing path first.

Wire a canonical `## Dependencies` row -- the form `validate-backlog`'s Manifest Conflict Rule reads -- alongside the existing `[BLOCKED_PENDING_PROPOSAL] <blocker-task-id>` audit marker on `<blocked-task-id>`'s work-unit file (#330 FR-1). The row's Title and Status cells carry `<blocker-task-id>`'s real, current values as of this call, not a placeholder. The ADR-07 auto-requeue cascade still only auto-unblocks `<blocked-task-id>` when `<blocker-task-id>` reaches `done` / `declined` AND `<blocked-task-id>`'s own status is `blocked`; the `## Dependencies` row has no such restriction, so it is what satisfies the validator now, independent of `<blocked-task-id>`'s current status. See [ADR-10: Multi-target proposal wiring](adr/10-multi-target-proposal-wiring.md).

Use this when the `promote-proposal` flow does not cover your case:

- You realise AFTER a promote that a peer task should have been in `affected_task_ids` and want to wire it retroactively.
- You hand-authored a work unit (not via task-factory) that unblocks another task.
- You are correcting a proposal authored without `affected_task_ids`.

Fail-fast (#330 FR-1 error handling): every path below exits non-zero, prints a message naming the file (when one is implicated) and the reason, and leaves no partial write behind.

- Both IDs must match the `E<N>-F<N>-S<N>-T<N>` task-ID format.
- `<blocked-task-id>` must exist in the backlog index.
- `<blocker-task-id>` must exist in the backlog index.
- `<blocker-task-id>` must NOT be in a terminal state (`done` / `declined`); wiring a dep on terminal work is a no-op and almost always a mistake.
- `<blocked-task-id>` and `<blocker-task-id>` cannot be the same.
- `<blocked-task-id>`'s work-unit file must be readable (valid UTF-8) and contain a `## Dependencies` section.

A request that cannot produce a validator-visible edge reports `wired: false` and exits non-zero with a `reason`, leaving no partial write.

Warns (does not refuse) when `<blocked-task-id>` is not currently in `blocked` status: the ADR-07 cascade will not fire until the task is blocked -- but the `## Dependencies` row this call writes to `<blocked-task-id>`'s work-unit file satisfies the Manifest Conflict Rule now, independent of that status.

Idempotent: calling `add-dep` twice for the same pair leaves exactly one Dependencies row and one marker. `wired: true` in the output JSON means `<blocked-task-id>`'s `## Dependencies` table carries a validator-visible row for `<blocker-task-id>` as of THIS call -- true whether the row was newly written or already present. `wired: false` means no such row could be produced; the exit code is non-zero in that case and `reason` explains why.

Output JSON:

```json
{
  "blocked": "E1-F1-S16-T1",
  "blocker": "E1-F1-S16-T2",
  "wired": true,
  "reason": "post-promote correction for shared 14-test blocker"
}
```

**Exit codes (#330 FR-2):**

| Outcome | rc | `wired` |
|---------|----|---------|
| A `## Dependencies` row for `<blocker-task-id>` is validator-visible on `<blocked-task-id>`'s file as of this call (newly written or already present on a repeat call). | 0 | `true` |
| Any Fail-fast precondition above is not met (bad ID format, blocked/blocker not found, blocker terminal, self-wire, unreadable file, missing `## Dependencies` section) -- no row could be written. | 1 | `false` |

The exit code is what a script should branch on, not the printed `WARNING:` status line: the warning fires only on the soft, non-fatal "not currently blocked" case above and never changes the exit code, while every `wired: false` outcome exits non-zero with `reason` populated on the same JSON payload (same keys as the success path).

### `reject-proposal`

```
uv run devbench reject-proposal <task-id> --reason "<message>"
uv run devbench reject-proposal --unmaterialised <source-task-id> --reason "<message>"
```

Two forms:

1. **Per-draft reject** (first form) -- archives the draft `.md` to `<workspace>/.devbench/rejected-proposals/<task-id>-<timestamp>.md`, removes the BACKLOG.md row, writes a `[PROPOSAL_REJECTED]` audit comment on the source, strips the `[BLOCKED_PENDING_PROPOSAL]` marker, and invokes the auto-requeue cascade. If the source's remaining markers are all terminal, the source auto-unblocks.
2. **Un-materialised reject** (`--unmaterialised <source-id>`) -- archives the whole proposal JSON to `<workspace>/.devbench/rejected-proposals/<source-id>-unmaterialised-<timestamp>.json` and writes a `[PROPOSAL_JSON_REJECTED]` audit comment. Refuses when any task in the JSON already has a materialised draft; use the per-draft form for those first.

Exactly one form must be supplied; missing or both-supplied raises an argument-parse error. `--reason` is required and non-empty.
