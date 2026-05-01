# DevBench CLI Reference

Complete reference for every `devbench` subcommand. Commands are grouped by purpose; within each group they are ordered by the sequence an operator or agent typically hits them.

Every command runs from the parent workspace root (the directory containing the `devbench` checkout):

```bash
uv run devbench <command> [args]
# or: python3 -m devbench <command> [args]
```

Two environment variables MUST be set before any command runs; commands that depend on them exit non-zero with a clear message when unset:

- `JUDGE_WORKSPACE_ROOT` -- absolute path to the backlog workspace (contains `BACKLOG.md`, `backlog/`, `.devbench/`).
- `JUDGE_CLAUDE_MODEL` -- model identifier (example: `us.anthropic.claude-opus-4-7-v1`).

Optional: `--config <path>` (or `JUDGE_CONFIG_PATH` env var) overrides the default `backlog/config/devbench.yaml` lookup.

## Exit codes (all commands)

- **0** -- success.
- **1** -- application-level error (invalid state, refused guard, missing work unit, bad args after parse).
- **2** -- argument-parsing error (unknown flag, missing required positional).

Commands that run a blocking external process (git, tests, judges) propagate the process exit code through, subject to the 0/1/2 contract above.

## Contents

- [Backlog read](#backlog-read)
- [Backlog write](#backlog-write)
- [Orchestration and reporting](#orchestration-and-reporting)
- [Orchestrator helpers (invoked by agents)](#orchestrator-helpers-invoked-by-agents)
- [Git operations](#git-operations)
- [Amendment workflow](#amendment-workflow)
- [Proposal workflow (task factory)](#proposal-workflow-task-factory)

---

## Backlog read

Non-mutating commands for inspecting backlog state.

### `status`

```
uv run devbench status
```

Print a summary of the backlog grouped by status. Output includes counts per lifecycle value (in-queue, in-progress, in-review, done, blocked, proposed, declined, hold) plus an always-rendered `Un-materialised` count of proposal JSONs pending materialisation. Also lists active and blocked work units by ID.

Pass `--detail` (E220) to additionally render three panels at the bottom of the output:

- **In-queue tasks (with dep status):** every Task currently in `in-queue`, marked `[ready]` if every dependency is terminal or `[waiting]` with the offending blocker ID otherwise.
- **Blocked tasks (with markers / blockers):** every Task currently in `blocked`, with the first `[BLOCKED_PENDING_PROPOSAL] <id>` marker found in its Comments and the first non-terminal dep ID; either may be empty when the block is from a different cause (manual block, review fail).
- **Held tasks (with most recent [HOLD] reason):** every Task currently on `hold`, with the latest `[HOLD] <reason>` line from its Comments.

Without `--detail` the panels are omitted (default invocation matches the historical output shape).

The summary's `Blocked` row is split into three lines (Part-1, post-issue-#118):

- `Blocked (auto)` -- ADR-07 cascade-clearing: the task carries a `[BLOCKED_PENDING_PROPOSAL]` marker chain that will resolve when its target tasks reach terminal.
- `Blocked (recovery)` -- AWAITING_AUTO_RECOVERY: no marker yet, but devbench's recovery loop has an artefact on disk (a pending proposal JSON, a rejected-amendment archive, or a recent recovery-agent `[BLOCKED]` audit comment within `JUDGE_BLOCKED_RECOVERY_WINDOW_SECONDS` -- default 1800s / 30min). The orchestrator's next sweep cycle will advance the task into the auto-clearing bucket. Operator does nothing.
- `Blocked (attn)` -- the true halt list: manual gates (`DO NOT CLAIM`), unknown marker targets, cascade-stuck states. Operator must act.

### `next`

```
uv run devbench next
```

Print the next actionable work unit as JSON. Returns `ALL_DONE` when every unit is done and `NO_ACTIONABLE` when something is blocked or in-progress but nothing is ready to start. Used by the orchestrate SKILL to drive the main loop. No arguments.

### `report`

```
uv run devbench report [--watch N] [--since <ISO-8601>]
```

Print the progress report with velocity, token consumption, and estimated cost. Default layout renders two side-by-side tables: **All-time** (full log) and **Current run** (most recent contiguous block of orchestration events, boundary detected as a gap over 10 minutes between consecutive `Set X to ...` log lines).

- `--watch N` refreshes every N seconds (Ctrl+C to exit). Adds a **This run** column tracking activity since the watch loop started.
- `--since <ISO-8601>` renders a single custom-window table instead of the dual layout.

Cost is computed per call, per token type, from real `usage` data. See [model-pricing.md](model-pricing.md) for the cost formula, per-model rates, and cache-multiplier env vars.

**Log-file resolution (fail-fast, no fallbacks):** `devbench report` reads its log file in this order. The same chain is used by the orchestrator's `setup_logging` writer, so both reader and writer always resolve to the same path:

1. `JUDGE_LOG_FILE` environment variable -- explicit override; the caller takes responsibility. Wins over everything below; useful for ad-hoc redirects and tests.
2. `log_file:` in the workspace's `backlog/config/devbench.yaml` (top-level field) -- the **single source of truth** for ordinary launches. Resolved relative to `JUDGE_WORKSPACE_ROOT` when not absolute. Both the orchestrator (writer) and `devbench report` / `devbench hook-tail` (readers) consult this field, so coordinating shell envs across panes is no longer required.
3. `<JUDGE_WORKSPACE_ROOT>/logs/orchestrator.log` -- the canonical per-workspace default applied when neither (1) nor (2) is set.

When NONE of (1)/(2)/(3) yields a path -- i.e. `JUDGE_LOG_FILE` unset, `log_file:` absent from yaml, AND `JUDGE_WORKSPACE_ROOT` unset -- `devbench report` exits 1 with an actionable error naming all three sources. The previous implementation silently fell back to the devbench source-tree's log (`<devbench>/src/devbench/logs/orchestrator.log`), which let operators read a stale, unrelated log without noticing -- the BACKLOG.md done count and the log-derived throughput count then diverged silently.

**Divergence WARNING:** when `BACKLOG.md` reports a non-zero "Tasks completed" count but the All-time throughput window finds zero `Set <id> to 'done'` events, the report emits a one-line WARNING above the trailing summary. The two counts MUST agree on a healthy backlog (the throughput row narrates the events that produced the backlog state). A divergence almost always means `devbench report` is reading a different log than the orchestrator writes to. The warning names the log file path so the operator can immediately identify the mismatch and either set `JUDGE_LOG_FILE` correctly or invoke `devbench report` from the same env the orchestrator was launched with.

**Blocked-task classification (Part-1, post-issue-#118):** the report renders blocked tasks across three panels, ordered by what the operator should do:

- `Blocked tasks (auto-clearing via proposal)` -- the ADR-07 cascade will fire when every `[BLOCKED_PENDING_PROPOSAL]` marker target reaches terminal. Each row names the IDs the task is waiting on. Operator does nothing.
- `Blocked tasks (auto-recovery in flight)` -- no marker yet, but devbench's recovery loop has an artefact on disk: a pending proposal JSON, a rejected-amendment archive, or a recent recovery-agent `[BLOCKED]` audit comment within `JUDGE_BLOCKED_RECOVERY_WINDOW_SECONDS` (default 1800s). Each row carries a `[recovery: <signal-source>]` annotation so the operator can see which signal drove the classification. Operator does nothing for now -- the next sweep cycle will advance the task into the auto-clearing bucket.
- `Blocked tasks (needs operator attention)` -- the true halt list: manual gates (`DO NOT CLAIM`), unknown marker targets, cascade-stuck states. Each row carries just ID + title; the operator opens the work-unit file to read the blocker comment.

Empty panels are omitted entirely. The recency-window override (`JUDGE_BLOCKED_RECOVERY_WINDOW_SECONDS=<seconds>`) lets operators with slower iteration cadences extend the audit-comment window.

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

- `<path>` defaults to `$JUDGE_WORKSPACE_ROOT/hook-logs.jsonl`.
- `--tz <zone>` overrides the display timezone (any IANA zone, for example `America/Denver`). When `--tz` is absent, `hook-tail` falls back to the top-level `display_timezone:` yaml key (or `JUDGE_DISPLAY_TIMEZONE` env), then to OS local. Internal storage stays in UTC.
- `--no-follow` exits after emitting existing events instead of tailing.
- `--from-start` emits every event from the beginning of the file before entering follow mode.
- `--orchestrator-only` (Phase 11 / E230) filters the stream to events whose `orchestrator_session` field equals `$JUDGE_ORCHESTRATOR_SESSION_ID`. When the env var is unset the command exits 2 with an actionable error -- pass `--orchestrator-session <id>` instead to supply the value explicitly.
- `--orchestrator-session <id>` filters by an explicit session id (audit / replay use case). Pre-Phase-11 log entries that lack the field are passed through unfiltered so historical events stay visible.

The launch command in `caylent-telemetry-spec/devbench-launch-commands.txt` sets `JUDGE_ORCHESTRATOR_SESSION_ID` on both the orchestrator pane (so the plugin's `hook-logger.sh` stamps every event) and the hook-tail pane (so the filter has a value to match). Side-pane Claude sessions started ad-hoc inherit the workspace root but NOT the session id, so their tool calls land in the log with an empty `orchestrator_session` and are dropped by the filter -- a `tail -f hook-logs.jsonl` would still see them, but the pretty-printed orchestrator pane stays clean.

See [hook-activity.md](hook-activity.md) for the event glyphs and the full column legend.

### `watchdog`

```
uv run devbench watchdog [--idle-minutes N] [--flag-file PATH] [--log-file PATH] [--print-if-stuck]
```

Single-shot poll that detects a stuck `/devbench:orchestrate` loop and writes a marker file the operator can surface in their shell prompt. Exits 0 always -- it is a checker, not a daemon.

A run is considered stuck when **both** conditions hold:

1. `BACKLOG.md` contains at least one row with `Status: in-progress`.
2. The most recent dated line in the orchestrator log is older than `--idle-minutes` (default 5). Path resolution mirrors `devbench report` -- (1) `JUDGE_LOG_FILE`, (2) `log_file:` in `backlog/config/devbench.yaml`, (3) `<JUDGE_WORKSPACE_ROOT>/logs/orchestrator.log`.

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
- `--flag-file PATH` -- override the marker path (default `$JUDGE_WORKSPACE_ROOT/.devbench/needs-restart.flag`).
- `--log-file PATH` -- override the orchestrator log location. Default is the devbench repo's `src/devbench/logs/orchestrator.log` relative to the installed package; pass an explicit path (or set `JUDGE_LOG_FILE` and read `$JUDGE_LOG_FILE`) to point watchdog at the same workspace-local log the orchestrator wrote to. `cmd_watchdog` does NOT consult the `log_file:` yaml field today; pass `--log-file` (or wrap with the env) to keep the writer/reader in sync.
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

### `check`

```
uv run devbench check
```

Pre-flight verifier for orchestrator launch readiness. For every repo in `backlog/config/devbench.yaml`'s `repos:` map, confirms (1) symlink at `$JUDGE_WORKSPACE_ROOT/<checkout_directory>` exists, (2) the local clone has an `origin` remote, (3) the remote's `default_branch` matches `devbench.yaml` (when set), and (4) no open PR already targets `git_ops.single_branch` (when single-branch mode is on). Exits 0 when every repo passes; exits 1 with one actionable error per failure otherwise. The `gh api` / `gh pr list` calls use the timeout in `DEVBENCH_CHECK_GH_API_TIMEOUT` (seconds, default `30`).

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
```

Force any status on a work unit. Skips the done-gate and other workflow checks. Used for recovery (unblock a stuck unit, resurrect a declined unit) and for orchestrator-internal lifecycle transitions. Accepted values: `in-queue`, `in-progress`, `in-review`, `done`, `blocked`, `proposed`, `declined`, `hold`.

### `mark-done`

```
uv run devbench mark-done <id>
```

Mark the unit as `done`. Enforces the done-gate: all four review judges (`code_review`, `test_review`, `doc_review`, `changes_manifest`) must have logged `[REVIEW_PASS]` in the most recent round (after any intervening `[REVIEW_REJECTED]`). Security review must also have passed. Exits 1 with a clear error naming the missing judge(s) when the gate fails.

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

- Flips `in-queue` Tasks whose dependencies are NOT satisfied to `blocked` (with a `[BLOCKED] sync-blocked: dependency '<id>' not yet terminal` audit comment naming the first offending dep).
- Flips `blocked` Tasks whose dependencies are now satisfied (every dep -- including epic / feature / story-level deps that recurse into descendants -- is `done` or `declined`) back to `in-queue` (with a `[UNBLOCKED] sync-blocked: dependencies now terminal` audit comment).

Tasks carrying an open `[BLOCKED_PENDING_PROPOSAL] <id>` marker are left alone -- the ADR-07 cascade owns that path. Tasks whose status is anything other than `in-queue` or `blocked` (e.g. `in-progress`, `in-review`, `done`, `declined`, `hold`, `proposed`) are also untouched. Output is a JSON envelope of the form `{"flipped_to_blocked": [...], "flipped_to_in_queue": [...]}` for scripting.

Useful as a pre-flight sweep before `devbench next` (after manual edits to the backlog) and for triage when a backlog has drifted out of sync. Combine with `validate-backlog` for a complete consistency check.

### `start`

```
uv run devbench start
```

Run the orchestrate SKILL non-interactively via the Agent SDK. Invoked by `make start-interactive` and `make start`; operators rarely call this directly. No arguments.

---

## Orchestration and reporting

See the [report](#report), [watch](#watch), [hook-tail](#hook-tail), and [watchdog](#watchdog) entries under Backlog read.

The main entry point for running the orchestrator is `make start-interactive` (interactive Claude session) or `make start` (background). See [README Interactive Mode](../README.md#interactive-mode).

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
- **defer_pr mode** (`git_ops.single_branch: <branch>` + `git_ops.defer_pr: true`): emits staged + unstaged + untracked only. When staged and unstaged are both empty the executor has just committed, so `git show HEAD` is substituted. The branch-vs-default hunk is deliberately skipped because it would include every prior completed task's commits on the shared branch.

Exit 0 on success; exit 1 when the work unit is not found or no local path is configured for its repo. Output is `(no changes)` when every hunk is empty.

### `run-tests`

```
uv run devbench run-tests <id>
```

Run the test suite in the work unit's target repo. Uses the repo's `make test` target when present; falls back to bare `pytest`. Used by `test_review`. Returns the test runner's exit code.

### `log`

```
uv run devbench log <message>
```

Append a free-form message to the orchestrator log. The destination path is resolved via `setup_logging` (`JUDGE_LOG_FILE` > `log_file:` in `backlog/config/devbench.yaml` > `<JUDGE_WORKSPACE_ROOT>/logs/orchestrator.log` > source-tree default), so `devbench log` and `devbench report` always agree on the file. Not audited to the work-unit file. Useful for emitting narrative breadcrumbs from agents.

### `log-verdict`

```
uv run devbench log-verdict <judge> <id> <pass|fail> [feedback]
```

Record a judge verdict as an audit comment on the work-unit file. Writes `[REVIEW_PASS]` or `[REVIEW_FAIL]` (or `[SECURITY_FAIL]` for `security_review`). Feedback is mandatory when the verdict is `fail`; rejected by the `guard-verdict-format.sh` hook otherwise.

`<judge>` must be one of the names in the allowlist defined by `devbench.constants.KNOWN_JUDGE_NAMES`. The allowlist is split into two tiers:

- **Canonical reviewers (5)** -- `code_review`, `test_review`, `doc_review`, `changes_manifest`, `security_review`. Only these names satisfy the done-gate's `BacklogManager._last_round_all_passed` check. They are written by `review-supervisor` and `security-reviewer`.
- **Audit-only workflow agents (4)** -- `executor`, `blocker_resolver`, `manifest_amender`, `task_factory`. Their verdicts land in the work-unit Comments section as audit metadata but do NOT count toward the done-gate. Workflow agents use these to record progress (for example, the executor logging `executor` verdicts during AC enforcement, or task-factory recording `task_factory` after a successful materialise).

Two enforcement layers prevent malformed audit rows:

1. **CLI layer** (`cmd_log_verdict`): refuses any `<judge>` outside `KNOWN_JUDGE_NAMES` with a clear error naming the valid choices. Catches typos like `judge` (literal) or hyphenated forms like `code-reviewer`.
2. **Hook layer** (`guard-verdict-format.sh`, PreToolUse): mirrors the same allowlist, plus an additional **executor scope** rule -- when the calling agent's `agent_type == "devbench:executor"` AND the judge is one of the canonical 5 reviewers, the hook blocks. The executor is an authoring agent, not a reviewer; the audit-only `executor` judge name remains allowed (records progress without satisfying the gate). Other agents (review-supervisor, security-reviewer, main session) can still write canonical reviewer verdicts.

Override env var: none -- this is a security/correctness gate, not a tunable. If a legitimate use case needs to write a verdict outside the allowlist, extend `KNOWN_JUDGE_NAMES` in `src/devbench/constants.py` AND update `KNOWN_JUDGES` in `plugin/devbench/scripts/guard-verdict-format.sh` (the two lists must stay in sync).

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

---

## Git operations

### `ensure-branch`

```
uv run devbench ensure-branch <id>
```

Create or switch to the work-unit branch before the executor runs. Branch name resolves from the work-unit file's `- **Branch:** ...` field; defaults to `backlog/<id-lower>`. In single-branch mode, switches to the configured `single_branch` instead. Handles dirty trees via stash-pop.

### `git-ops`

```
uv run devbench git-ops <id>
```

Commit, push, create PR, wait for CI, merge. The full git-ops sequence runs after every review judge passes and before `mark-done`. In single-branch + `defer_pr: true` mode, commits locally only (no push, no PR); the shared branch is pushed by `git-ops-finalize` after every unit is done.

Enforces three deterministic safety rails:
- **Manifest-scope:** staged files must exactly match the work unit's Changes Manifest (AC-FINAL-015).
- **Branch-anchor:** HEAD must be on the expected branch (prevents orphan-branch commits).
- **Orphan-pattern:** no staged or already-tracked path may match a build/state ignore pattern (terraform state, terragrunt cache, Python pycache, coverage artefacts, `node_modules`, `.DS_Store`). The default behaviour (Phase 1 of the orphan-cascade fix) is **inline cleanup**: git-ops runs `cleanup_tracked_orphans` programmatically, commits the result as a devbench-authored chore commit (canonical message `chore(cleanup): untrack devbench-managed orphan paths and update .gitignore`), then continues with the original task's commit on the same invocation. Two commits land on the task's branch; the executor's staging is preserved (filtered to exclude orphan paths). When the operator sets `DEVBENCH_DISABLE_INLINE_ORPHAN_CLEANUP=1`, git-ops falls back to the legacy proposal flow (cleanup-as-task) with cross-task de-duplication so two parents detecting the same orphan set wire to the SAME cleanup task. Override the active pattern list per backlog via `DEVBENCH_ORPHAN_IGNORE_PATTERNS` (comma-separated fnmatch globs).

Each rail exits 1 with a clear diagnostic when violated.

#### Exit code contract

The orchestrator skill ([`plugin/devbench/skills/orchestrate/SKILL.md`](../plugin/devbench/skills/orchestrate/SKILL.md) step 8) handles each non-zero exit code distinctly:

| rc | Meaning |
|----|---------|
| 0 | PR merged (or commit landed locally in deferred mode). |
| 1 | Hard failure -- block the task with a `[BLOCKED]` audit comment. |
| 2 | CI failed; executor retry budget not exhausted. Audit comment `[CI_FAIL]` names the trimmed log under `.devbench/ci-failures/<id>-<n>.log`. Re-invoke the executor with `ci-fail` feedback, then re-run git-ops. (Issue #115; **default on**. Disable via `git_ops.ci_failure_retry: false` in `devbench.yaml` or env `JUDGE_CI_FAILURE_RETRY_ENABLED=0`.) |
| 3 | PR has unresolved review feedback; executor retry budget not exhausted. Audit comment `[PR_BOT_FAIL]` names the JSON feedback file under `.devbench/pr-bot-feedback/<id>-<n>.json`. Re-invoke the executor with `pr-bot` feedback, then re-run git-ops. (Issue #116; opt-in. Enable via `git_ops.pr_review_resolution.enabled: true` in `devbench.yaml` or env `JUDGE_PR_REVIEW_RESOLUTION_ENABLED=1`.) |

The retry budget for rc=2 / rc=3 is shared with the existing review-judge retry budget (`MAX_RETRY_ATTEMPTS`); when exhausted, git-ops returns rc=1 instead of 2/3 and writes a `[CI_FAIL_BLOCKED]` / `[PR_BOT_FAIL_BLOCKED]` marker so the operator sees the full failure surface.

Every toggle below resolves with **env > YAML > default** precedence. Boolean env values are case-insensitive: truthy = `1`/`true`/`yes`/`on`; falsy = `0`/`false`/`no`/`off`. Any other value fails fast at process start with a `ValueError`.

#### CI-failure retry (issue #115, default on)

Default-on as of v-next; opt out via `git_ops.ci_failure_retry: false` in `devbench.yaml` (or env `JUDGE_CI_FAILURE_RETRY_ENABLED=0`). When `wait_for_checks` reports CI failure:

1. `gh pr checks --json name,state,link` identifies the failing run.
2. `gh run view <run-id> --log-failed` fetches the log; the trailing `JUDGE_CI_FAILURE_LOG_BYTES` bytes (default 32 KiB) are saved to `.devbench/ci-failures/<task-id>-<attempt>.log`.
3. A `[CI_FAIL]` audit comment names the log path; rc=2 signals the orchestrator to re-invoke the executor.
4. After `MAX_RETRY_ATTEMPTS` retries the path transitions to `[CI_FAIL_BLOCKED]` + rc=1.

#### PR review-comment polling (issue #116, opt-in)

Configure via YAML `git_ops.pr_review_resolution:` block (every sub-field
overridable via the env vars below). Or stay env-only:

Set `JUDGE_PR_REVIEW_RESOLUTION_ENABLED=1` AND configure at least one signal (a non-empty `JUDGE_PR_REVIEW_AGENTS` allowlist or `JUDGE_PR_REVIEW_DECISION_BLOCKS=1`) to enable. After `wait_for_checks` returns True, git-ops polls `gh pr view --json reviewDecision,reviews` and `gh api repos/<repo>/pulls/<n>/comments` for up to `JUDGE_PR_REVIEW_SETTLE_SECONDS` seconds (default 60), polling every `JUDGE_PR_REVIEW_POLL_INTERVAL` seconds (default 5). The poll exits early on the first signal; otherwise the merge proceeds. Knobs:

| env var | default | purpose |
|---------|---------|---------|
| `JUDGE_PR_REVIEW_RESOLUTION_ENABLED` | unset (off) | top-level toggle |
| `JUDGE_PR_REVIEW_AGENTS` | empty | comma-separated bot login allowlist (e.g. `github-copilot[bot],amazon-q-developer[bot]`) whose unresolved comments block merge |
| `JUDGE_PR_REVIEW_DECISION_BLOCKS` | True | whether `reviewDecision == CHANGES_REQUESTED` blocks merge |
| `JUDGE_PR_REVIEW_SETTLE_SECONDS` | 60 | total poll budget |
| `JUDGE_PR_REVIEW_POLL_INTERVAL` | 5 | per-poll cadence |

#### Workflow-registration race defence (issue #114)

The `wait_for_checks` step that runs between `gh pr create` and `gh pr merge` no longer treats `gh pr checks --watch` returning `"no checks reported"` as an unconditional pass. The previous behaviour merged before GitHub Actions had a chance to enqueue the workflow when CI was actually configured. The new disambiguation:

- **Repo has no `.github/workflows/*.y[a]ml` files locally**: legitimate "no CI configured" -> pass immediately (legacy fast path).
- **Repo has at least one workflow file**: race condition. Retry `gh pr checks` up to `JUDGE_CHECK_REGISTRATION_RETRIES` times (default 12), sleeping `JUDGE_CHECK_REGISTRATION_DELAY_SECONDS` between attempts (default 5). 12 * 5 = 60s of default coverage for the GitHub Actions queue.
- **Retry exhausted**: refuse the merge with an actionable error naming the PR number, the elapsed wait, and the workflow files found. No warn-and-pass fallback.

Operators with unusual CI cadence override the knobs via the env vars above. Defaults live in `src/devbench/constants.py` (`DEFAULT_CHECK_REGISTRATION_RETRIES`, `DEFAULT_CHECK_REGISTRATION_DELAY_SECONDS`).

### `git-ops-finalize`

```
uv run devbench git-ops-finalize <repo>
```

Single-branch mode only: push the shared branch and create one PR for every accumulated commit. Use once, after every work unit targeting this repo is done. See [README Single-branch mode](../README.md#single-branch-mode) and [architecture.md §6](architecture.md#6-multi-pr-vs-single-pr-mode).

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

The orchestrator skill (`plugin/devbench/skills/orchestrate/SKILL.md`) calls this command on every `in-review` work unit at the top of each loop iteration when `git_ops.pause_before_merge: true` is set in the YAML. See [`docs/git-ops-modes.md`](git-ops-modes.md) for the full pause-before-merge mode reference and [ADR-13](adr/13-pause-before-merge.md) for the design rationale.

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

See [manifest-amendments.md](manifest-amendments.md) and [ADR-02](adr/02-manifest-amendment-workflow.md) for the full design. This workflow is opt-in: enable with `manifest_amendment.enabled: true` in `backlog/config/devbench.yaml`.

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

Reject the pending amendment, archive the request to `<workspace>/.devbench/rejected-requests/<id>-<timestamp>.json`, and write a `[AMENDMENT_REJECTED]` audit comment. The task is typically marked `blocked` and may trigger the task-factory flow (see below) if `blocker-resolver` emits a proposal.

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

**Auto-cascade when `auto_accept_proposals: true`:** with the flag set in `devbench.yaml`'s `task_factory:` section, `write-proposal` ALSO calls `materialise-proposal` and `promote-proposal` synchronously inside the same Python invocation. The cascade is therefore actionable the moment the JSON lands — the source task's `## Dependencies` table is wired with a `proposed`-status row immediately, the cascade-classifier moves it into the `auto-clearing via proposal` bucket, and the orchestrator's next iteration claims the materialised draft.

This closes a timing window in which a resolver-written proposal could sit orphaned for up to one full orchestrator iteration (between the resolver's `write-proposal` call and the next `sweep-proposals` cycle) — long enough for the source task to read as "needs operator attention" even though the auto-resolution path was already on disk.

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

### `reject-proposal`

```
uv run devbench reject-proposal <task-id> --reason "<message>"
uv run devbench reject-proposal --unmaterialised <source-task-id> --reason "<message>"
```

Two forms:

1. **Per-draft reject** (first form) -- archives the draft `.md` to `<workspace>/.devbench/rejected-proposals/<task-id>-<timestamp>.md`, removes the BACKLOG.md row, writes a `[PROPOSAL_REJECTED]` audit comment on the source, strips the `[BLOCKED_PENDING_PROPOSAL]` marker, and invokes the auto-requeue cascade. If the source's remaining markers are all terminal, the source auto-unblocks.
2. **Un-materialised reject** (`--unmaterialised <source-id>`) -- archives the whole proposal JSON to `<workspace>/.devbench/rejected-proposals/<source-id>-unmaterialised-<timestamp>.json` and writes a `[PROPOSAL_JSON_REJECTED]` audit comment. Refuses when any task in the JSON already has a materialised draft; use the per-draft form for those first.

Exactly one form must be supplied; missing or both-supplied raises an argument-parse error. `--reason` is required and non-empty.
