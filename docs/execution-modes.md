# Execution Modes

DevBench supports two execution modes. Both follow the same lifecycle and the same ownership rules — the difference is whether the orchestrate skill runs interactively (human in the loop) or non-interactively (background/unattended).

---

## Modes at a Glance

| Aspect | Automated (`make start`) | Interactive (`make start-interactive`) |
| --- | --- | --- |
| Orchestrator | `uv run devbench start` → Agent SDK `query()` runs orchestrate SKILL.md non-interactively | Claude Code session with orchestrate SKILL.md active |
| Executor | `devbench:executor` agent (invoked by orchestrate skill) | Same — orchestrate skill invokes `devbench:executor` agent |
| Human control | Background — monitor via log file | Foreground — pause with Escape, give instructions, resume |
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
   ├── Stage changed files (git add — NO commit)
   └── Update work-unit status to in-review

5. Judge review  [devbench:review-supervisor AGENT RESPONSIBILITY]
   ├── code-reviewer    — SOLID, DRY, fail-fast, 12-factor
   ├── test-reviewer    — TDD discipline, test quality, coverage
   ├── doc-reviewer     — accuracy, completeness, sync with code
   └── changes-manifest — actual changes vs. declared manifest
   (review-supervisor invokes all four judge agents in parallel)

6. If any judge FAILs → inject feedback, return to step 4 (max JUDGE_MAX_RETRIES)

7. Security review (after all 4 judges pass)  [devbench:security-reviewer AGENT RESPONSIBILITY]
   └── If FAIL → write SECURITY_FAIL + REVIEW_REJECTED, return to step 4

8. Git operations  [devbench:executor AGENT RESPONSIBILITY — ALWAYS, BOTH MODES]
   ├── a. Create/checkout branch in the target submodule
   ├── b. Stage files from the Changes Manifest (selective — never git add -A)
   ├── c. Commit: "<unit-id>: <title>"
   ├── d. Push branch to origin
   ├── e. Create PR (--base from devbench.yaml repos.<org/repo>.default_branch)
   ├── f. Wait for CI checks to pass
   ├── g. Squash-merge PR, delete branch
   └── h. Update parent repo's submodule reference (only when git_ops.update_submodule: true)

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
- Optionally invoking `devbench:blocker-resolver` when a unit is stuck

### Branch name resolution

Branch name is resolved **once, at parse time**, in `BacklogParser.parse_work_unit_file`:

1. If the work-unit file's **Target Repository** section has a `Branch:` field, use it exactly.
2. Otherwise, derive it: `backlog/<unit-id-lowercase>` (e.g., `E0-F1-S1-T1` → `backlog/e0-f1-s1-t1`).

The resolved name is stored in `WorkUnit.branch` and used by the executor's git operations. Neither the executor nor the orchestrate skill invents a third naming scheme.

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
            └── invokes devbench:blocker-resolver when a unit is stuck (optional)
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

The human can pause at any time (Escape), give instructions, and resume. The same ownership rules apply — the executor does not commit until all judges pass.

---

## Status Source of Truth

`WorkUnit.status` always comes from the **work-unit file** (`## Status:` line), not from the BACKLOG.md index table. When `parse_index` reads the index, it:

1. Uses the file path from each index row to locate the work-unit file.
2. Calls `parse_work_unit_file` to construct the `WorkUnit` from the file.
3. Logs a `WARNING` if the index row's status column disagrees with the file.

The BACKLOG.md index is an at-a-glance summary; the work-unit file is authoritative.

---

## Retry Behaviour

| Event | Both modes |
| --- | --- |
| Judge FAIL | Feedback injected into next executor invocation; executor reads feedback, fixes code, re-runs review |
| BLOCKED (executor reported) | `devbench:blocker-resolver` evaluates; resolution or failure feedback fed to next attempt |
| Max retries exhausted (`JUDGE_MAX_RETRIES`, default 10) | `devbench set-status <id> blocked` — unit marked BLOCKED in BACKLOG.md |
| Security FAIL | `SECURITY_FAIL` + `REVIEW_REJECTED` written to work-unit; done-gate reset; retry |
