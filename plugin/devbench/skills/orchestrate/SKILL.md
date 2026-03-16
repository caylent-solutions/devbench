---
name: orchestrate
description: Run the devbench backlog execution loop until all work units are complete or blocked
---

Process the backlog using the steps below, repeating until all work units are done or no actionable units remain.

## Loop

1. `uv run devbench validate-backlog` — abort if the backlog has integrity errors.

2. `uv run devbench next` — get the next actionable unit ID.
   - If output is `ALL_DONE`: print a completion summary and exit.
   - If output is `NO_ACTIONABLE`: print a blocked summary and exit.
   - Otherwise: parse the JSON to get `id`, `title`, `repo`.

3. `uv run devbench ensure-branch <id>` — create or switch to the work unit's
   feature branch before the executor stages any files. Stashes and pops if the
   working tree is dirty.

4. Invoke `devbench:executor` with the unit ID.

5. Invoke each of the 4 review agents in sequence with the unit ID:
   - `devbench:code-reviewer`
   - `devbench:test-reviewer`
   - `devbench:doc-reviewer`
   - `devbench:changes-manifest`

6. Check verdicts. If **all 4 passed** in this round, proceed to step 7.
   If **any** of the 4 logged a `REVIEW_FAIL` in this round:
   - Collect all fail feedback from the work unit Comments.
   - Retry `devbench:executor` with the unit ID (the executor reads prior Comments for context).
   - Return to step 5 — re-run only the 4 review agents. Do NOT invoke security-reviewer here.
   - After `max_retries` consecutive failures, log a blocker comment and move to step 2 (skip this unit).

7. All 4 review agents passed. Invoke security — exactly once per work unit:
   - Invoke `devbench:security-reviewer` with the unit ID.
   - If security fails: log a blocker comment and move to step 2.
   - If security passes: proceed immediately to step 8. Do NOT re-run the 4 review agents.

8. `uv run devbench git-ops <id>` — commit, push, create PR, wait for CI, merge.

9. `uv run devbench mark-done <id>` — mark the unit done (enforces done-gate).

10. Return to step 1.

## Standards

- Never modify files under `backlog/` directly — use `uv run devbench log-verdict` and `mark-done`.
- Never bypass the done-gate — all 4 review judges must pass before git-ops.
- Security review runs after all 4 review judges pass in the same round — never before, never during the retry cycle.
- Security review runs exactly once per work unit — if it already passed, proceed to step 8 immediately.
- Log all significant actions and decisions to the work unit Comments via `log-verdict`.
