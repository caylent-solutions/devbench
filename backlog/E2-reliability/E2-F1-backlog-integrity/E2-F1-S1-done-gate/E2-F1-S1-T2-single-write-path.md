# E2-F1-S1-T2: Consolidate all status writes through single set_status() in backlog/manager.py

## Status: done

## Spec Reference

| Section | Lines | Description |
|---------|-------|-------------|
| Phase 4 §4.2 | — | Consolidate all status updates into a single `set_status()` method; remove any direct file writes outside this method; rollup cascade must use same path |

## Description

This task refactors `src/devbench/backlog/manager.py` to ensure every status write — whether to the work unit file or to BACKLOG.md — goes through a single `set_status()` method. Currently, work unit completion involves separate calls that can diverge if the process is interrupted between them. After this task, `set_status()` writes both files in one function call, making divergence structurally impossible within a single Python process. The `_rollup_parent_status` cascade is also updated to call `set_status()` for each rollup rather than writing directly.

## Target Repository

- **Repo:** `caylent-solutions/devbench`
- **Local path:** `{JUDGE_WORKSPACE_ROOT}/devbench/`
- **Branch:** `feature/done-gate`

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| E2-F1-S1-T1 | Add require_judge_approval guard in backlog/manager.py | in-queue |

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

- [❌] AC-1: `set_status(work_unit_id: str, new_status: str) -> None` is defined in `manager.py`
- [❌] AC-2: `set_status` updates the `## Status:` line in the work unit's `.md` file
- [❌] AC-3: `set_status` updates the `Status` column in the corresponding BACKLOG.md row in the same function call
- [❌] AC-4: No other method in `manager.py` writes to either file's status directly — all call `set_status()`
- [❌] AC-5: `_rollup_parent_status` (or equivalent rollup logic) calls `set_status()` for each parent it updates
- [❌] AC-TEST-1: A test verifies that after a single `set_status()` call, both the work unit file and BACKLOG.md reflect the new status
- [❌] AC-DOC-1: `set_status` docstring documents the write order, atomicity guarantee, and parameters

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

- Write order: work unit `.md` file first, BACKLOG.md second — if the process dies after the first write, the next `validate-backlog` run will detect the mismatch
- Log at DEBUG level each write with `work_unit_id`, `new_status`, and file path
- Validate `new_status` is one of `{"in-queue", "in-progress", "in-review", "done"}` before writing

## Test Plan (Spec-Driven TDD)

### Contract Definition

```python
def set_status(self, work_unit_id: str, new_status: str) -> None:
    """
    Update status for a work unit in both the work unit file and BACKLOG.md.

    Args:
        work_unit_id: The work unit ID (e.g. "E0-F1-S1-T1")
        new_status: One of: in-queue, in-progress, in-review, done

    Raises:
        ValueError: If new_status is not a valid status value
        FileNotFoundError: If the work unit file cannot be found
    """
```

### Acceptance Tests (BDD-style)

# AC-2 & AC-3: set_status updates both files
Given a work unit file with `## Status: in-progress` and matching BACKLOG.md row
When `set_status("E2-F1-S1-T1", "in-review")` is called
Then the work unit file contains `## Status: in-review` AND BACKLOG.md row shows `in-review`

# AC-4: no direct writes outside set_status
Given the source code of `manager.py`
When searched for direct file writes to status fields (excluding `set_status` itself)
Then no matches are found

# AC-5: rollup calls set_status
Given a work unit that triggers a parent rollup when all siblings are done
When the rollup is triggered
Then `set_status` is called for the parent work unit

### Unit Tests

| Test Name | Spec Ref | Status |
|-----------|----------|--------|
| test_set_status_updates_work_unit_file | Phase 4 §4.2 | ❌ |
| test_set_status_updates_backlog_md | Phase 4 §4.2 | ❌ |
| test_set_status_raises_on_invalid_status | Phase 4 §4.2 | ❌ |
| test_set_status_validates_status_values | Phase 4 §4.2 | ❌ |
| test_rollup_uses_set_status | Phase 4 §4.2 | ❌ |
| test_no_direct_writes_outside_set_status | Phase 4 §4.2 | ❌ |

### TDD Cycle Log

<!-- TDD cycle log will be filled in by agent -->

## Rollback Instructions

1. `git checkout main -- src/devbench/backlog/manager.py`
2. Verify `make validate` passes
3. Note: E2-F1-S2-T1 (validate-backlog) depends on this task completing first

## Output Location

| Artifact | Path |
|----------|------|
| Backlog manager | `{JUDGE_WORKSPACE_ROOT}/devbench/src/devbench/backlog/manager.py` |

## Comments / Agent Log

<!-- Agent log will be filled in during execution -->
