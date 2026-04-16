---
name: orchestrate
description: Run the devbench backlog execution loop until all work units are complete or blocked
---

Process the backlog using the steps below, repeating until all work units are done or no actionable units remain.

**CRITICAL: This is an autonomous loop. Never stop to ask the user if you should proceed. Never ask "Should I continue?" After completing a work unit, immediately return to step 1 and process the next one. The only valid exit conditions are ALL_DONE or NO_ACTIONABLE from step 2. If the context was compacted, re-read this instruction and continue the loop.**

## Loop

1. `uv run devbench validate-backlog` — abort if the backlog has integrity errors.

2. `uv run devbench next` — get the next actionable unit ID.
   - If output is `ALL_DONE`: print a completion summary and exit.
   - If output is `NO_ACTIONABLE`: print a blocked summary and exit.
   - Otherwise: parse the JSON to get `id`, `title`, `repo`.
   - Run `uv run devbench claim <id>` to mark the unit in-progress before proceeding.

3. `uv run devbench ensure-branch <id>` — create or switch to the work unit's
   feature branch before the executor stages any files. Stashes and pops if the
   working tree is dirty.

3b. Git state check — determine if implementation and review work are already complete:
    a. Run `uv run devbench read-unit <id>` to get `repo_path` and the work unit `content`.
    b. In `repo_path`, run `git diff --staged --name-only`.
    c. If staged files exist: proceed to step 4.
    d. If no staged files AND the work unit Comments contain REVIEW_PASS entries for
       `code_review`, `test_review`, `doc_review`, and `changes_manifest` in the most
       recent round: skip to step 8 (git-ops — implementation and reviews are done).
    e. If no staged files AND reviews are not all present: proceed to step 4 (executor
       did not stage its output — recovery run).

4. Invoke `devbench:executor` with the unit ID.

5. Invoke `review-supervisor` with the unit ID.
   - If result is REVIEW_FAIL: go to step 6.
   - If result is REVIEW_PASS: go to step 7.

6. On REVIEW_FAIL:
   - Retry `devbench:executor` with the unit ID (executor reads prior Comments for context).
   - Return to step 5 — invoke `review-supervisor` again. Do NOT invoke security-reviewer here.
   - After `max_executor_retries` consecutive failures, log a blocker comment and return to step 2.

7. On review team REVIEW_PASS:
   - Invoke `devbench:security-reviewer` with the unit ID.
   - If security PASS: proceed immediately to step 8. Do NOT re-run review-supervisor.
   - If security FAIL: log a blocker comment and return to step 2.

8. `uv run devbench git-ops <id>` — In standard mode: commit, push, create PR, wait for CI, merge. In single-branch mode (when `git_ops.defer_pr: true` in devbench.yaml): commit locally only (no push, no PR, no merge). The branch is shared across all work units.

9. `uv run devbench mark-done <id>` — mark the unit done (enforces done-gate).

10. Return to step 1.

11. When all work units are done and `defer_pr` mode is active, run `uv run devbench git-ops-finalize <repo>` to push the single branch and create the PR.

## Standards

- Never modify files under `backlog/` directly — use `uv run devbench log-verdict` and `mark-done`.
- Never bypass the done-gate — review-supervisor must pass before git-ops.
- Security review runs exactly once per work unit — after review-supervisor passes. If security passes, go directly to step 8.
- The retry loop (step 6) re-runs only review-supervisor, never security-reviewer.
- Log all significant actions and decisions to the work unit Comments via `log-verdict`.
