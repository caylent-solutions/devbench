# E2-F1-S1-T1: Add require_judge_approval guard in backlog/manager.py

## Status: done

## Spec Reference

| Section | Lines | Description |
|---------|-------|-------------|
| Phase 4 §4.1 | — | Add `require_judge_approval(verdicts: list[JudgeResult]) -> None`; raise `RuntimeError` if any verdict is not PASS; call in `mark_done()` |

## Description

This task adds the `require_judge_approval()` function to `src/devbench/backlog/manager.py` and wires it into `mark_done()`. The guard accepts the list of `JudgeResult` objects produced during a work unit's review phase and raises a `RuntimeError` if any verdict is not `PASS`, making it structurally impossible to transition a work unit to `done` without clean judge verdicts. This closes the gap where interactive mode agents could call `mark_done()` directly, bypassing the judge check in the retry loop.

## Target Repository

- **Repo:** `caylent-solutions/devbench`
- **Local path:** `{JUDGE_WORKSPACE_ROOT}/devbench/`
- **Branch:** `feature/done-gate`

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| E0 | Fix Critical Issues | in-queue |

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

- [❌] AC-1: `require_judge_approval(verdicts: list[JudgeResult]) -> None` is defined in `manager.py`
- [❌] AC-2: Called with an empty list, the function raises `RuntimeError` (no verdicts = not approved)
- [❌] AC-3: Called with a list where all verdicts are PASS, the function returns without raising
- [❌] AC-4: Called with a list containing at least one non-PASS verdict, the function raises `RuntimeError` naming the failing judges
- [❌] AC-5: `mark_done(work_unit_id, verdicts)` signature is updated to accept verdicts; it calls `require_judge_approval(verdicts)` as the first operation
- [❌] AC-DOC-1: `require_judge_approval` has a docstring stating its purpose, parameter types, and the exact condition that triggers the error

## Changes Manifest

| Action | File Path |
|--------|-----------|
| modify | `src/devbench/backlog/manager.py` |

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

- Use `RuntimeError` not `AssertionError`
- Error message must list the names of all failing judges, not just a count
- Use a list comprehension to collect failing judges before raising
- The function signature: `def require_judge_approval(verdicts: list[JudgeResult]) -> None:`

## Test Plan (Spec-Driven TDD)

### Contract Definition

```python
def require_judge_approval(verdicts: list[JudgeResult]) -> None:
    """
    Assert that all judge verdicts are PASS before marking a work unit done.

    Args:
        verdicts: Results from all judges that reviewed this work unit.

    Raises:
        RuntimeError: If verdicts is empty or any verdict is not PASS.
            Message includes the names/types of all failing judges.
    """
    failing = [v for v in verdicts if v.verdict != Verdict.PASS]
    if not verdicts or failing:
        raise RuntimeError(
            f"Cannot mark done: {len(failing) or 'no'} judge(s) did not pass: "
            f"{[type(v).__name__ for v in failing]}"
        )
```

### Acceptance Tests (BDD-style)

# AC-2: empty list raises
Given an empty list `[]`
When `require_judge_approval([])` is called
Then `RuntimeError` is raised

# AC-3: all PASS returns normally
Given `[JudgeResult(verdict=Verdict.PASS), JudgeResult(verdict=Verdict.PASS)]`
When `require_judge_approval(verdicts)` is called
Then no exception is raised and the function returns `None`

# AC-4: mixed verdicts raises and names failing judges
Given `[JudgeResult(verdict=Verdict.PASS), JudgeResult(verdict=Verdict.FAIL, judge="CodeReviewJudge")]`
When `require_judge_approval(verdicts)` is called
Then `RuntimeError` message contains "CodeReviewJudge"

### Unit Tests

| Test Name | Spec Ref | Status |
|-----------|----------|--------|
| test_require_judge_approval_raises_on_empty_list | Phase 4 §4.1 | ❌ |
| test_require_judge_approval_raises_on_fail_verdict | Phase 4 §4.1 | ❌ |
| test_require_judge_approval_raises_on_mixed_verdicts | Phase 4 §4.1 | ❌ |
| test_require_judge_approval_passes_on_all_pass | Phase 4 §4.1 | ❌ |
| test_mark_done_calls_require_judge_approval_first | Phase 4 §4.1 | ❌ |
| test_require_judge_approval_error_names_failing_judges | Phase 4 §4.1 | ❌ |

### TDD Cycle Log

<!-- TDD cycle log will be filled in by agent -->

## Rollback Instructions

1. `git checkout main -- src/devbench/backlog/manager.py`
2. Verify `make validate` passes
3. Note: T2 (single write path) depends on this task; rolling back will block T2

## Output Location

| Artifact | Path |
|----------|------|
| Backlog manager | `{JUDGE_WORKSPACE_ROOT}/devbench/src/devbench/backlog/manager.py` |

## Comments / Agent Log

<!-- Agent log will be filled in during execution -->
