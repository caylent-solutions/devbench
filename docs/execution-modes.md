# Execution Modes

DevBench supports two execution modes. Both follow the same lifecycle and the same ownership rules -- the difference is whether the orchestrate skill runs interactively (human in the loop) or non-interactively (background/unattended).

For the wider context (component architecture, judge tier, multi-PR vs single-PR mode), see the [architecture overview](architecture.md). This doc focuses on the per-step lifecycle and ownership rules.

## Table of contents

- [Modes at a Glance](#modes-at-a-glance)
- [Lifecycle: Step-by-Step](#lifecycle-step-by-step)
- [Ownership rules](#ownership-rules)
- [Mode-Specific Details](#mode-specific-details)
- [Status Source of Truth](#status-source-of-truth)
- [Retry Behaviour](#retry-behaviour)
- [Stop Hook and Circuit Breaker](#stop-hook-and-circuit-breaker)

---

## Modes at a Glance

| Aspect | Automated (`make start`) | Interactive (`make start-interactive`) |
| --- | --- | --- |
| Orchestrator | `uv run devbench start` → Agent SDK `query()` runs orchestrate SKILL.md non-interactively | Claude Code session with orchestrate SKILL.md active |
| Executor | `devbench:executor` agent (invoked by orchestrate skill) | Same -- orchestrate skill invokes `devbench:executor` agent |
| Human control | Background -- monitor via log file | Foreground -- pause with Escape, give instructions, resume |
| Git operations | `devbench:executor` agent via `devbench git-ops` CLI command | Same |
| Best for | Unattended runs, CI-like pipelines | Active oversight, course correction, debugging |

---

## Lifecycle: Step-by-Step

Both modes execute the same logical steps in the same order.

```text
1. Pre-flight validation
   └── Abort if BACKLOG.md or work-unit files are structurally invalid

2. Parse BACKLOG.md
   └── BacklogParser.parse_index() reads each work-unit file
       → WorkUnit.branch resolved here (spec Branch: field, or backlog/<unit-id-lower>)
       → WorkUnit.status comes from the work-unit FILE (BACKLOG.md row is cross-checked; mismatch = WARNING)

3. Find next actionable work unit
   └── IN_PROGRESS tasks first (resume interrupted work), then IN_QUEUE
   └── Task-level dependencies must all be DONE

4. Implement the work unit  [devbench:executor AGENT RESPONSIBILITY]
   ├── Read work-unit file, CLAUDE.md, AGENT-INSTRUCTIONS.md
   ├── Check dependencies
   ├── TDD cycle: RED → GREEN → REFACTOR
   ├── Implement all acceptance criteria
   ├── Update documentation in the same change as code
   ├── Run full test suite, confirm passing
   ├── Stage changed files (git add -- NO commit)
   └── Update work-unit status to in-review

5. Judge review  [devbench:review-supervisor AGENT RESPONSIBILITY]
   ├── code-reviewer    -- SOLID, DRY, fail-fast, 12-factor
   ├── test-reviewer    -- TDD discipline, test quality, coverage
   ├── doc-reviewer     -- accuracy, completeness, sync with code
   └── changes-manifest -- actual changes vs. declared manifest
   (review-supervisor invokes all four judge agents in parallel)

6. If any judge FAILs → inject feedback, return to step 4 (max max_executor_retries)

7. Security review (after all 4 judges pass)  [devbench:security-reviewer AGENT RESPONSIBILITY]
   └── If FAIL → write SECURITY_FAIL + REVIEW_REJECTED, return to step 4

8a. Git operations -- STANDARD MODE (default; per-task branch + per-task PR)
    [devbench:executor AGENT RESPONSIBILITY]
    ├── Create/checkout branch in the target repo
    ├── Stage files from the Changes Manifest (selective -- never git add -A)
    ├── Commit: "<unit-id>: <title>"
    ├── Push branch to origin
    ├── Create PR (--base from devbench.yaml repos.<org/repo>.default_branch)
    ├── Wait for CI checks to pass
    ├── Squash-merge PR, delete branch
    └── Update parent repo's submodule reference (only when git_ops.update_submodule: true)

8b. Git operations -- SINGLE-BRANCH MODE (git_ops.single_branch + git_ops.defer_pr: true)
    [devbench:executor AGENT RESPONSIBILITY for per-task; orchestrate finalizes]
    ├── ensure-branch creates/checks out the shared branch (same for every task)
    ├── Stage files
    ├── Commit locally: "<unit-id>: <title>" (no push, no PR, no merge)
    └── After ALL tasks are done: `devbench git-ops-finalize <repo>` pushes the
        accumulated commits and creates one PR for the batch

    Note: In this mode `devbench get-diff` returns the current task's
    commit-local scope only (staged + unstaged + untracked, or `git show
    HEAD` post-commit). The branch-vs-default hunk is deliberately omitted
    because it would include every prior completed task's commits on the
    shared branch. See ADR-12.

9. Mark Done (done-gate: verifies all 4 judges passed in most recent round)

10. Repeat from step 2
```

---

## Ownership Rules

These rules apply in **both modes** without exception.

### Executor owns: implement only

The `devbench:executor` agent is responsible for:

- Reading the work-unit spec and all referenced standards
- Writing code, tests, and documentation
- Running the test suite
- Staging files (`git add`) so judge evidence is complete
- Updating work-unit status to `in-review`

The executor **must not**:

- Create branches
- Commit
- Push
- Create or merge PRs
- Modify `BACKLOG.md` or any file under `backlog/`

### Orchestrator owns: review and git lifecycle

The orchestrate skill is responsible for:

- Invoking `devbench:review-supervisor` (which runs all four judge agents in parallel)
- Injecting review feedback into retry attempts
- Invoking `devbench:security-reviewer` after the four judges pass
- Delegating git operations to the executor agent (`devbench git-ops`)
- Marking work units Done (via the done-gate)
- Marking work units Blocked (after max retries)

> A `devbench:blocker-resolver` agent file exists in the plugin but the orchestrate skill does not currently invoke it. Blocked work units stay blocked until human intervention. See [Current gaps](architecture.md#10-current-gaps-known-limitations).

### Branch name resolution

Branch name is resolved once at parse time. See the canonical rules in the [backlog contract](backlog-contract.md#branch-name-resolution). The resolved name is stored on `WorkUnit.branch` and used by the executor's git operations; neither the executor nor the orchestrate skill re-derives it at runtime.

---

## Mode-Specific Details

### Automated mode (`make start` / `make run-backlog`)

```text
uv run devbench start
    │
    └── Agent SDK query() runs orchestrate SKILL.md non-interactively
            │
            ├── invokes devbench:executor agent to implement each work unit
            │
            ├── invokes devbench:review-supervisor agent to run judge review
            │       └── review-supervisor runs all 4 judge agents in parallel
            │
            ├── invokes devbench:security-reviewer agent
            │
            ├── invokes devbench:executor agent to run git-ops
            │
            └── (devbench:blocker-resolver agent file exists but is NOT currently invoked)
```

The Agent SDK session runs the orchestrate skill with `--dangerously-skip-permissions` so it can invoke CLI tools and agents without interactive approval prompts.

### Interactive mode (`make start-interactive`)

```text
Claude Code session (with devbench plugin active)
    │
    ├── orchestrate SKILL.md active from session start
    │
    ├── Claude invokes devbench:executor agent for implementation
    │
    ├── Claude invokes devbench:review-supervisor for judge review
    │       └── review-supervisor runs all 4 judge agents in parallel
    │
    ├── Claude invokes devbench:security-reviewer for security gate
    │
    └── Claude invokes devbench:executor for git-ops (commit, push, PR, merge)
```

The human can pause at any time (Escape), give instructions, and resume. The same ownership rules apply -- the executor does not commit until all judges pass.

---

## Status Source of Truth

`WorkUnit.status` always comes from the **work-unit file** (`## Status:` line), not from the BACKLOG.md index table. When `parse_index` reads the index, it:

1. Uses the file path from each index row to locate the work-unit file.
2. Calls `parse_work_unit_file` to construct the `WorkUnit` from the file.
3. Logs a `WARNING` if the index row's status column disagrees with the file.

The BACKLOG.md index is an at-a-glance summary; the work-unit file is authoritative.

---

## Retry Behaviour

| Event | Behaviour |
| --- | --- |
| Judge FAIL | Feedback injected into the next executor invocation; executor reads feedback, fixes code, re-runs review. |
| BLOCKED (executor reported) | Work unit marked `blocked`. Stays blocked until human intervention (the `blocker-resolver` agent is not currently invoked -- see architecture doc gaps). |
| Max executor retries exhausted (`max_executor_retries` / `JUDGE_MAX_RETRIES`, default 10) | `devbench set-status <id> blocked` -- unit marked BLOCKED in BACKLOG.md and orchestrator moves on. |
| Security FAIL | `SECURITY_FAIL` + `REVIEW_REJECTED` written to work unit; done-gate window reset; review tier re-runs after fix. Security tier is **not** retried. |

---

## Stop Hook and Circuit Breaker

The orchestrator registers a Claude Code **Stop hook** (`continue-orchestration.sh`) that fires whenever Claude attempts to stop responding. When an orchestration loop is active (any task is in-progress in `BACKLOG.md`), the hook blocks the stop and injects context so the agent can resume.

### What the hook injects

- **Task ID and file path** extracted from the `BACKLOG.md` in-progress row
- **Last action** parsed from the most recent `[judge/...]` or `[agent/...]` comment in the work-unit file
- **Specific next step** based on the last action (e.g. "run review-supervisor" after executor, "run git-ops" after security pass)

### Circuit breaker

If the agent enters a tight stop-block loop (stops, gets blocked, does nothing useful, stops again), the circuit breaker prevents infinite cycling:

- After `max_blocks` blocks within `window_seconds`, the hook allows the stop
- The counter resets when the time window expires
- When the circuit breaker trips, a `[CIRCUIT_BREAKER]` comment is logged to the work unit for audit

### Blocked transitional state

If the work-unit file says `blocked` but `BACKLOG.md` still shows `in-progress` (timing mismatch), the hook detects this and instructs the agent to run `devbench next` to find the next actionable task.

### Stale task detection

If a task has been in-progress longer than `stale_task_minutes`, the hook warns that the task may be stale from a crashed session and recommends running `devbench status` to assess.

### Configuration

All values are configured in `backlog/config/devbench.yaml` under `stop_hook:`, with environment variable overrides:

| YAML key | Env var | Default | Description |
| --- | --- | --- | --- |
| `max_blocks` | `JUDGE_STOP_MAX_BLOCKS` | 5 | Circuit breaker trips after this many blocks |
| `window_seconds` | `JUDGE_STOP_WINDOW_SECONDS` | 180 | Counter resets after this period |
| `stale_task_minutes` | `JUDGE_STOP_STALE_MINUTES` | 120 | Warn about stale tasks older than this |
