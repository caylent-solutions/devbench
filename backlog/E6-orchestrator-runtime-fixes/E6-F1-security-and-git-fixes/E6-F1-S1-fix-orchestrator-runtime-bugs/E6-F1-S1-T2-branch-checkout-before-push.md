# E6-F1-S1-T2: Create local branch before commit in commit_and_push()

## Status: in-queue

## Spec Reference

| Section | Lines | Description |
|---------|-------|-------------|
| — | orchestrator.py:192 | Branch name generated from BRANCH_NAME_TEMPLATE |
| — | git_ops.py:56 | commit_and_push() does git add, commit, push — no checkout |
| — | constants.py:74 | BRANCH_NAME_TEMPLATE = "backlog/{unit_id}" |

## Description

`commit_and_push()` in `git_ops.py` runs `git add -A`, `git commit`, then `git push origin <branch>` — but never creates or checks out a local branch first. `git push origin branch_name` resolves `branch_name` as a local ref; if no local branch by that name exists, git errors out.

The fix is to run `git checkout -B <branch>` as the first command inside `commit_and_push()`. The `-B` flag creates the branch if it does not exist, or resets it to the current HEAD if it does (making retries safe without additional logic).

## Target Repository

- **Repo:** `caylent-solutions/devbench`
- **Local path:** `{JUDGE_WORKSPACE_ROOT}/devbench/`
- **Branch:** `feature/e6-f1-s1-t2-branch-checkout-before-push`

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| — | None — independent fix | — |

## Blocked By

No blockers.

## Definition of Ready

- [❌] All dependencies listed above are `done` (verified in BACKLOG.md)
- [❌] All spec sections in the Spec Reference table have been read by the agent
- [❌] Agent has read CLAUDE.md in the repository root
- [❌] Agent has read backlog/AGENT-INSTRUCTIONS.md
- [❌] Agent has completed the pre-flight checklist
- [❌] No other agent has this work unit `in-progress`

## Definition of Done

- [❌] All acceptance criteria met (every AC item below shows a green checkmark)
- [❌] All tests pass — unit tests AND functional tests
- [❌] `make validate` passes in the target repo with zero errors
- [❌] TDD Cycle Log shows red-green-refactor cycle for each test written
- [❌] Documentation created or updated per acceptance criteria
- [❌] All code compliant with CLAUDE.md standards
- [❌] Changes manifest verified — only the files listed below were modified
- [❌] Judge agent system has been notified and approval is pending

## Acceptance Criteria

- [❌] AC-1: `commit_and_push()` runs `git checkout -B <branch>` as its first git command (after repo validation)
- [❌] AC-2: The git command sequence is: `checkout -B`, `add -A`, `commit`, `push origin <branch>`
- [❌] AC-3: `TestCommitAndPush.test_calls_git_commands` asserts 4 git calls in the correct order
- [❌] AC-4: On retry (branch already exists locally), `git checkout -B` resets to current HEAD without error — no additional error-handling logic required

## Changes Manifest

| Action | File Path |
|--------|-----------|
| modify | `src/devbench/github/git_ops.py` |
| modify | `tests/test_github/test_git_ops.py` |

## Code Standards and Requirements

### Tier 1: Universal Rules

1. Follow SOLID principles — single responsibility, open/closed, Liskov substitution, interface segregation, dependency inversion
2. DRY — do not duplicate logic; extract shared code into helpers
3. Fail-Fast — validate inputs at the earliest possible point; raise immediately on bad state
4. 12-Factor App — configuration via environment variables, no hardcoded values
5. Security — never log secrets; never commit credentials; validate all external inputs
6. No time-based waits — do not use `sleep()` or `time.sleep()` to wait for external state
7. No bypass annotations — do not use `# noqa`, `# type: ignore`, or `# nosec` without a documented reason
8. No `--no-verify` — never skip git hooks
9. No hardcoded config — all configuration comes from environment variables or explicit parameters
10. Explicit over implicit — prefer explicit parameter passing over globals and implicit state
11. Single source of truth — each piece of configuration lives in exactly one place
12. No silent failures — every error must be surfaced; never swallow exceptions without logging
13. Immutable data — prefer immutable structures (`frozenset`, `tuple`, `NamedTuple`) for configuration
14. Type annotations — all public functions and methods must have complete type annotations
15. Docstrings — all public modules, classes, and functions must have docstrings
16. Test coverage — every new function must have at least one unit test
17. Small functions — functions should do one thing; aim for under 30 lines
18. No global mutable state — module-level variables must be constants (immutable)
19. Dependency injection — pass dependencies explicitly rather than importing them inside functions
20. Error messages must be actionable — tell the user what to do, not just what went wrong
21. Log at appropriate levels — DEBUG for tracing, INFO for milestones, WARNING for recoverable issues, ERROR for failures
22. No print statements in library code — use the logging module
23. Backwards compatibility — do not remove or rename public interfaces without a deprecation path

### Tier 2: Contextual Rules — Python

- Use `self._git(["checkout", "-B", branch], repo_path)` — the same internal helper used for all git commands
- `-B` is preferred over `-b` because it is idempotent: safe on retries and does not require a try/except
- Do not move branch creation to the orchestrator; it belongs inside `commit_and_push()` as part of its contract
- The test must update `call_count` assertion from 3 to 4 and shift all existing index assertions by +1

## Test Plan (Spec-Driven TDD)

### Contract Definition

After this task, `commit_and_push()` in `git_ops.py` reads:
```python
def commit_and_push(self, repo: str, repo_path: Path, branch: str, message: str) -> None:
    validate_repo(repo)

    self._git(["checkout", "-B", branch], repo_path)
    self._git(["add", "-A"], repo_path)
    self._git(["commit", "-m", message], repo_path)
    self._git(["push", "origin", branch], repo_path)
    self.logger.info("Committed and pushed to %s on %s", branch, repo)
```

### Acceptance Tests (BDD-style)

# AC-2 / AC-3: Correct 4-command sequence
Given a GitOpsJudge with _git mocked
When commit_and_push("caylent-solutions/git-repo", tmp_path, "feature/x", "msg") is called
Then mock_git.call_count == 4
And calls[0] == ["checkout", "-B", "feature/x"]
And calls[1] == ["add", "-A"]
And calls[2] == ["commit", "-m", "msg"]
And calls[3] == ["push", "origin", "feature/x"]

# AC-4: Idempotent on retry (no additional logic needed)
Given -B flag is used for checkout
When the branch already exists locally
Then git resets the branch pointer to HEAD without error — no exception path required

### Unit Tests

| Test Name | Spec Ref | Status |
|-----------|----------|--------|
| test_calls_git_commands (updated) | git_ops.py:56 | ❌ |

### TDD Cycle Log

<!-- TDD cycle log will be filled in by agent -->

## Rollback Instructions

1. `git checkout main -- src/devbench/github/git_ops.py`
2. `git checkout main -- tests/test_github/test_git_ops.py`
3. Verify `make validate` passes

## Output Location

| Artifact | Path |
|----------|------|
| Git ops | `{JUDGE_WORKSPACE_ROOT}/devbench/src/devbench/github/git_ops.py` |
| Git ops tests | `{JUDGE_WORKSPACE_ROOT}/devbench/tests/test_github/test_git_ops.py` |

## Comments / Agent Log

<!-- Agent log will be filled in during execution -->
