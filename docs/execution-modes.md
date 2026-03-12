# Execution Modes

DevBench supports two execution modes. Both follow the same lifecycle and the same ownership rules — the difference is where the orchestrator and executor run.

---

## Modes at a Glance

| Aspect | Automated (`make start`) | Interactive (`make start-interactive`) |
| --- | --- | --- |
| Orchestrator | `orchestrator.py` Python loop | Claude Code session with `orchestrator-prompt.md` injected |
| Executor | Subprocess: `uv run devbench execute` (spawns a Claude CLI agent) | Same Claude session (orchestrator IS the executor) |
| Human control | Background — monitor via log file | Foreground — pause with Escape, give instructions, resume |
| Git operations | `orchestrator.py` via `GitOpsJudge` | Claude session via Bash tool |
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

4. Implement the work unit  [EXECUTOR RESPONSIBILITY]
   ├── Read work-unit file, CLAUDE.md, AGENT-INSTRUCTIONS.md
   ├── Check dependencies
   ├── TDD cycle: RED → GREEN → REFACTOR
   ├── Implement all acceptance criteria
   ├── Update documentation in the same change as code
   ├── Run full test suite, confirm passing
   ├── Stage changed files (git add — NO commit)
   └── Update work-unit status to in-review

5. Judge review  [ORCHESTRATOR RESPONSIBILITY]
   ├── code_review    — SOLID, DRY, fail-fast, 12-factor
   ├── test_review    — TDD discipline, test quality, coverage
   ├── doc_review     — accuracy, completeness, sync with code
   └── changes_manifest — actual changes vs. declared manifest

6. If any judge FAILs → inject feedback, return to step 4 (max JUDGE_MAX_RETRIES)

7. Security review (after all 4 judges pass)  [ORCHESTRATOR RESPONSIBILITY]
   └── If FAIL → write SECURITY_FAIL + REVIEW_REJECTED, return to step 4

8. Git operations  [ORCHESTRATOR RESPONSIBILITY — ALWAYS, BOTH MODES]
   ├── a. Create/checkout branch in the target submodule
   ├── b. Stage files from the Changes Manifest (selective — never git add -A)
   ├── c. Commit: "<unit-id>: <title>"
   ├── d. Push branch to origin
   ├── e. Create PR (--base from devbench.yaml repos.<org/repo>.default_branch)
   ├── f. Wait for CI checks to pass
   ├── g. Squash-merge PR, delete branch
   └── h. Update parent repo's submodule reference

9. Mark Done (done-gate: verifies all 4 judges passed in most recent round)

10. Repeat from step 2
```

---

## Ownership Rules

These rules apply in **both modes** without exception.

### Executor owns: implement only

The executor (subprocess agent in automated mode; the Claude session in interactive mode) is responsible for:

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

The orchestrator is responsible for:

- Running judge reviews (all 5 judges)
- Injecting review feedback into retry attempts
- All git operations: branch, commit, push, PR, CI wait, merge, submodule update
- Marking work units Done (via the done-gate)
- Marking work units Blocked (after max retries)

### Branch name resolution

Branch name is resolved **once, at parse time**, in `BacklogParser.parse_work_unit_file`:

1. If the work-unit file's **Target Repository** section has a `Branch:` field, use it exactly.
2. Otherwise, derive it: `backlog/<unit-id-lowercase>` (e.g., `E0-F1-S1-T1` → `backlog/e0-f1-s1-t1`).

The resolved name is stored in `WorkUnit.branch` and used by the orchestrator's git operations. Neither the executor nor the orchestrator invents a third naming scheme.

---

## Mode-Specific Details

### Automated mode (`make start` / `make run-backlog`)

```text
orchestrator.py (Python loop)
    │
    ├── calls claude_executor.execute(work_unit_path, repo, feedback)
    │       └── spawns: uv run claude --print --dangerously-skip-permissions ...
    │               (executor.txt injected as system prompt)
    │
    ├── polls execution result (IN_REVIEW / FAILED / BLOCKED)
    │
    ├── runs judges (run_review_judges → CodeReviewJudge, TestReviewJudge, ...)
    │
    ├── runs security judge
    │
    └── runs GitOpsJudge: branch → commit → push → PR → CI wait → merge → submodule
```

The executor subprocess **cannot** be the orchestrator — it is a separate process with no access to the orchestrator's state.

### Interactive mode (`make start-interactive`)

```text
Claude Code session
    │
    ├── orchestrator-prompt.md injected at session start
    │
    ├── Claude IS the executor: uses Read/Write/Edit/Bash tools directly
    │       (does NOT call uv run devbench execute — that spawns a nested subprocess)
    │
    ├── judge review: uv run devbench review <unit-id>
    │       └── CLI delegates to the same judge classes as automated mode
    │
    ├── security review: uv run devbench security-review <unit-id>
    │
    └── git operations: Claude runs Bash commands directly
            (git checkout -b, git add, git commit, git push, gh pr create, gh pr merge)
```

The human can pause at any time (Escape), give instructions, and resume. The same ownership rules apply — Claude does not commit until all judges pass.

---

## Status Source of Truth

`WorkUnit.status` always comes from the **work-unit file** (`## Status:` line), not from the BACKLOG.md index table. When `parse_index` reads the index, it:

1. Uses the file path from each index row to locate the work-unit file.
2. Calls `parse_work_unit_file` to construct the `WorkUnit` from the file.
3. Logs a `WARNING` if the index row's status column disagrees with the file.

The BACKLOG.md index is an at-a-glance summary; the work-unit file is authoritative.

---

## Retry Behaviour

| Event | Automated mode | Interactive mode |
| --- | --- | --- |
| Judge FAIL | Feedback injected into next `execute()` call | Claude reads JSON output, fixes code, re-runs review |
| BLOCKED (executor reported) | `BlockerResolverJudge` evaluates; resolution or failure feedback fed to next attempt | Claude logs blocker, moves to next unit or seeks resolution |
| Max retries exhausted (`JUDGE_MAX_RETRIES`, default 10) | `mark_blocked()` — unit marked BLOCKED in BACKLOG.md | Claude logs and marks blocked via `uv run devbench log` |
| Security FAIL | `SECURITY_FAIL` + `REVIEW_REJECTED` written to work-unit; done-gate reset; retry | Same — Claude reads security-review JSON output, fixes, re-reviews |
