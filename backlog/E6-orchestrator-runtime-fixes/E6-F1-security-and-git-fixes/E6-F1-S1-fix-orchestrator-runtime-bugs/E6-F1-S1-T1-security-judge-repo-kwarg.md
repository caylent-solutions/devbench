# E6-F1-S1-T1: Pass repo= kwarg to SecurityReviewJudge.evaluate()

## Status: in-queue

## Spec Reference

| Section | Lines | Description |
|---------|-------|-------------|
| — | orchestrator.py:171 | SecurityReviewJudge.evaluate() called without required repo= kwarg |
| — | security_review.py:29 | evaluate() raises ValueError immediately if repo kwarg is absent/empty |

## Description

`process_work_unit()` in `orchestrator.py` calls `security_judge.evaluate(work_unit_path=..., repo_path=...)` but omits the required `repo=` keyword argument. `SecurityReviewJudge.evaluate()` checks for this kwarg and raises `ValueError("SecurityReviewJudge requires 'repo' keyword argument ...")` immediately if it is missing. This means every work unit fails at the security gate before any security analysis is attempted.

The fix is a one-line addition: pass `repo=work_unit.repo` to the evaluate call. `work_unit.repo` holds the GitHub repository in `owner/name` format, which is exactly what the judge expects.

## Target Repository

- **Repo:** `caylent-solutions/devbench`
- **Local path:** `{JUDGE_WORKSPACE_ROOT}/devbench/`
- **Branch:** `feature/e6-f1-s1-t1-security-judge-repo-kwarg`

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

- [❌] AC-1: `security_judge.evaluate()` in `process_work_unit()` receives `repo=work_unit.repo` as a keyword argument
- [❌] AC-2: No `ValueError` is raised when `process_work_unit()` reaches the security gate for a valid work unit
- [❌] AC-3: The orchestrator test that exercises the security judge path asserts that `evaluate()` was called with `repo=work_unit.repo`

## Changes Manifest

| Action | File Path |
|--------|-----------|
| modify | `src/devbench/execution/orchestrator.py` |
| modify | `tests/test_execution/test_orchestrator.py` |

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

- The `repo` value to pass is `work_unit.repo` (already available in scope at the call site)
- Do not change the signature of `SecurityReviewJudge.evaluate()` — the caller must supply the kwarg
- The orchestrator test update must use `assert_called_once_with` or `assert_called_with` to verify the kwarg

## Test Plan (Spec-Driven TDD)

### Contract Definition

After this task, the call in `orchestrator.py` reads:
```python
security_result = security_judge.evaluate(
    work_unit_path=work_unit.file_path,
    repo_path=repo_path,
    repo=work_unit.repo,
)
```

### Acceptance Tests (BDD-style)

# AC-2: No ValueError at security gate
Given a valid WorkUnit with repo="caylent-solutions/devbench"
When process_work_unit() is called and execution reaches the security check
Then security_judge.evaluate() is called without raising ValueError

# AC-3: repo kwarg forwarded correctly
Given a mock SecurityReviewJudge
When process_work_unit() calls security_judge.evaluate()
Then the mock receives repo="caylent-solutions/devbench" as a keyword argument

### Unit Tests

| Test Name | Spec Ref | Status |
|-----------|----------|--------|
| test_security_judge_called_with_repo_kwarg | orchestrator.py:171 | ❌ |

### TDD Cycle Log

<!-- TDD cycle log will be filled in by agent -->

## Rollback Instructions

1. `git checkout main -- src/devbench/execution/orchestrator.py`
2. `git checkout main -- tests/test_execution/test_orchestrator.py`
3. Verify `make validate` passes

## Output Location

| Artifact | Path |
|----------|------|
| Orchestrator | `{JUDGE_WORKSPACE_ROOT}/devbench/src/devbench/execution/orchestrator.py` |
| Orchestrator tests | `{JUDGE_WORKSPACE_ROOT}/devbench/tests/test_execution/test_orchestrator.py` |

## Comments / Agent Log

<!-- Agent log will be filled in during execution -->
