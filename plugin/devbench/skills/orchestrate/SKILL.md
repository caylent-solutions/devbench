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

3. Invoke `devbench:executor` with the unit ID.

4. Invoke each review agent in sequence with the unit ID:
   - `devbench:code-reviewer`
   - `devbench:test-reviewer`
   - `devbench:doc-reviewer`
   - `devbench:changes-manifest`

5. If any review agent logs a `REVIEW_FAIL` verdict:
   - Collect all fail feedback from the work unit Comments.
   - Retry `devbench:executor` with the unit ID (the executor reads prior Comments for context).
   - Repeat review sequence.
   - After `max_retries` failures, log a blocker comment and move to step 2 (skip this unit).

6. Once all 4 review agents pass:
   - Invoke `devbench:security-reviewer` with the unit ID.
   - If security fails: log a blocker comment and move to step 2.

7. `uv run devbench git-ops <id>` — commit, push, create PR, wait for CI, merge.

8. `uv run devbench mark-done <id>` — mark the unit done (enforces done-gate).

9. Return to step 1.

## Standards

- Never modify files under `backlog/` directly — use `uv run devbench log-verdict` and `mark-done`.
- Never bypass the done-gate — all 4 review judges must pass before git-ops.
- Security review runs after all 4 review judges pass, not before.
- Log all significant actions and decisions to the work unit Comments via `log-verdict`.
