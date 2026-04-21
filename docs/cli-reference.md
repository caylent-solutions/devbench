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

Print a summary of the backlog grouped by status. Output includes counts per lifecycle value (in-queue, in-progress, in-review, done, blocked, proposed, declined) plus an always-rendered `Un-materialised` count of proposal JSONs pending materialisation. Also lists active and blocked work units by ID. No arguments.

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

### `watch`

```
uv run devbench watch [--watch N]
```

Read-only one-screen live dashboard of the currently-active orchestration: mode, active task, phase, latest agent thinking, recent tool calls, repo state, pending amendment. `--watch N` refreshes every N seconds. See [watch-activity.md](watch-activity.md) for panel details.

### `hook-tail`

```
uv run devbench hook-tail [<path>] [--tz <zone>] [--no-follow] [--from-start]
```

Read-only pretty-tail of the plugin hook event stream (`hook-logs.jsonl`). One-line colourised summary per PreToolUse / PostToolUse / SubagentStart / SubagentStop / Stop event. Complements `watch`: where `watch` shows current state, `hook-tail` shows events as they happen.

- `<path>` defaults to `$JUDGE_WORKSPACE_ROOT/hook-logs.jsonl`.
- `--tz <zone>` overrides the display timezone (any IANA zone, for example `America/Denver`). Internal storage stays in UTC.
- `--no-follow` exits after emitting existing events instead of tailing.
- `--from-start` emits every event from the beginning of the file before entering follow mode.

See [hook-activity.md](hook-activity.md) for the event glyphs and the full column legend.

### `list-proposals`

```
uv run devbench list-proposals
```

Print every pending task-factory proposal with a per-task `[state]` label: `[unmaterialised]`, `[proposed]`, `[promoted]`, `[done]`, `[declined]`, `[rejected]`. Used by the orchestrator and by operators triaging proposed drafts. See [task-factory.md](task-factory.md).

### `validate-backlog`

```
uv run devbench validate-backlog
```

Integrity check across the full backlog: file existence, status sync between BACKLOG.md and work-unit files, orphaned files, invalid dependency references, Status Summary table count accuracy, content-rule violations. Exits 1 and prints every finding when any violation is found.

Invoked automatically at orchestrator startup; operators should run it after hand-edits.

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

Force any status on a work unit. Skips the done-gate and other workflow checks. Used for recovery (unblock a stuck unit, resurrect a declined unit) and for orchestrator-internal lifecycle transitions. Accepted values: `in-queue`, `in-progress`, `in-review`, `done`, `blocked`, `proposed`, `declined`.

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

### `start`

```
uv run devbench start
```

Run the orchestrate SKILL non-interactively via the Agent SDK. Invoked by `make start-interactive` and `make start`; operators rarely call this directly. No arguments.

---

## Orchestration and reporting

See the [report](#report), [watch](#watch), and [hook-tail](#hook-tail) entries under Backlog read.

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

Append a free-form message to the orchestrator log (`src/devbench/logs/orchestrator.log`). Not audited to the work-unit file. Useful for emitting narrative breadcrumbs from agents.

### `log-verdict`

```
uv run devbench log-verdict <judge> <id> <pass|fail> [feedback]
```

Record a judge verdict as an audit comment on the work-unit file. Writes `[REVIEW_PASS]` or `[REVIEW_FAIL]` (or `[SECURITY_FAIL]` for `security_review`). `<judge>` must be one of the canonical underscored names: `code_review`, `test_review`, `doc_review`, `changes_manifest`, `security_review`. Feedback is mandatory when the verdict is `fail`; rejected by the `guard-verdict-format.sh` hook otherwise.

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

Enforces two deterministic safety rails:
- Staged files must exactly match the work unit's Changes Manifest (AC-FINAL-015).
- HEAD must be on the expected branch (prevents orphan-branch commits).

Both rails exit 1 with a clear diagnostic when violated.

### `git-ops-finalize`

```
uv run devbench git-ops-finalize <repo>
```

Single-branch mode only: push the shared branch and create one PR for every accumulated commit. Use once, after every work unit targeting this repo is done. See [README Single-branch mode](../README.md#single-branch-mode) and [architecture.md §6](architecture.md#6-multi-pr-vs-single-pr-mode).

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
