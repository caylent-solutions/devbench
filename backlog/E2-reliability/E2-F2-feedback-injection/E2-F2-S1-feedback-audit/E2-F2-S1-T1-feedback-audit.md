# E2-F2-S1-T1: Audit execution/orchestrator.py, fix any missing feedback pass-through, add regression test

## Status: done

## Spec Reference

| Section | Lines | Description |
|---------|-------|-------------|
| Phase 4 §4.4 | — | Audit every retry branch in `execution/orchestrator.py`; confirm `previous_feedback` is populated and passed to `claude_executor.execute()` on every retry; add a test asserting feedback from a failed judge is present in subsequent execution call |

## Description

This task performs a targeted audit of `src/devbench/execution/orchestrator.py`, locating every code path that calls `execute()` during a retry and checking whether `previous_feedback` contains the prior failed judge's output. Any path found to be missing feedback is corrected. After all fixes, a regression test is added to `tests/test_execution/test_orchestrator.py` that mocks `execute()`, triggers a judge failure, and asserts that the next `execute()` call receives the feedback text from the failed judge.

## Target Repository

- **Repo:** `caylent-solutions/devbench`
- **Local path:** `{JUDGE_WORKSPACE_ROOT}/devbench/`
- **Branch:** `feature/feedback-injection-audit`

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| E2-F1-S2-T1 | Add devbench validate-backlog command + wire as pre-flight in orchestrator.py | in-queue |

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

- [❌] AC-1: Audit report written as code comments in `orchestrator.py` identifying each retry path examined and whether feedback was found to be correctly wired
- [❌] AC-2: Every `execute()` call in the retry loop (attempt ≥ 2) passes a non-empty `previous_feedback` string
- [❌] AC-3: Feedback string is the concatenation of all prior failed judge output; test verifies specific content
- [❌] AC-4: Regression test `test_feedback_injected_on_retry` is added to `tests/test_execution/test_orchestrator.py`
- [❌] AC-5: The regression test uses `unittest.mock.patch` to mock `execute()` and captures `call_args`
- [❌] AC-DOC-1: The retry section of `orchestrator.py` has a comment block documenting how `previous_feedback` is built and passed

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

- Build `previous_feedback` by joining all failed judge `.feedback` strings with `"\n\n---\n\n"` delimiter
- Store accumulated feedback in a local variable `accumulated_feedback: str` that is reassigned on each retry
- Test should assert `"specific failure text" in mock_execute.call_args.kwargs["previous_feedback"]`
- Use `pytest.mark.parametrize` if testing multiple retry depths

## Test Plan (Spec-Driven TDD)

### Contract Definition

After this task, the orchestrator's retry loop has the form:
```python
accumulated_feedback: str = ""
for attempt in range(max_retries):
    result = execute(work_unit, previous_feedback=accumulated_feedback or None)
    judge_results = run_all_judges(result)
    if all_pass(judge_results):
        break
    # Collect feedback from all failing judges for next attempt
    accumulated_feedback = "\n\n---\n\n".join(
        r.feedback for r in judge_results if r.verdict != Verdict.PASS
    )
```

### Acceptance Tests (BDD-style)

# AC-2: retry 2 has non-empty feedback
Given attempt 1 produces a failing judge with feedback text "Missing tests for error path"
When attempt 2 calls `execute()`
Then `previous_feedback` contains "Missing tests for error path"

# AC-4 & AC-5: regression test structure
Given `execute` is patched with a mock that records call args
When orchestrator processes a work unit through 2 attempts where attempt 1 fails
Then `mock_execute.call_args_list[1].kwargs["previous_feedback"]` is non-empty

# AC-3: feedback accumulates across multiple retries
Given attempts 1 and 2 both fail with distinct feedback strings
When attempt 3 calls `execute()`
Then `previous_feedback` contains both feedback strings

### Unit Tests

| Test Name | Spec Ref | Status |
|-----------|----------|--------|
| test_feedback_injected_on_retry | Phase 4 §4.4 | ❌ |
| test_feedback_not_none_on_second_retry | Phase 4 §4.4 | ❌ |
| test_feedback_accumulates_across_retries | Phase 4 §4.4 | ❌ |
| test_first_attempt_feedback_is_empty_or_none | Phase 4 §4.4 | ❌ |
| test_feedback_contains_judge_output_text | Phase 4 §4.4 | ❌ |

### TDD Cycle Log

<!-- TDD cycle log will be filled in by agent -->

## Rollback Instructions

1. `git checkout main -- src/devbench/execution/orchestrator.py tests/test_execution/test_orchestrator.py`
2. Verify `make validate` passes

## Output Location

| Artifact | Path |
|----------|------|
| Orchestrator | `{JUDGE_WORKSPACE_ROOT}/devbench/src/devbench/execution/orchestrator.py` |
| Regression test | `{JUDGE_WORKSPACE_ROOT}/devbench/tests/test_execution/test_orchestrator.py` |

## Comments / Agent Log

<!-- Agent log will be filled in during execution -->
