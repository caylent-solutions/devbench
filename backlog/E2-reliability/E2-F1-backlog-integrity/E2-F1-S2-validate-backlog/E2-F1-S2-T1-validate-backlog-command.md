# E2-F1-S2-T1: Add devbench validate-backlog command + wire as pre-flight in orchestrator.py

## Status: done

## Spec Reference

| Section | Lines | Description |
|---------|-------|-------------|
| Phase 4 §4.3 | — | Add `devbench validate-backlog` CLI command; checks include missing files, status mismatches, orphaned files, and invalid dependency IDs; wire as pre-flight check in `orchestrator.py` |

## Description

This task implements `validate_backlog(backlog_root: Path) -> list[str]` in the backlog module and exposes it as the `devbench validate-backlog` subcommand in `cli.py`. It also modifies `orchestrator.py` to call this function before starting its main work-unit loop, aborting if any errors are returned. The four checks mirror the schema validation rules: file existence, status consistency, orphan detection, and dependency ID validity.

## Target Repository

- **Repo:** `caylent-solutions/devbench`
- **Local path:** `{JUDGE_WORKSPACE_ROOT}/devbench/`
- **Branch:** `feature/validate-backlog`

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| E2-F1-S1-T2 | Consolidate all status writes through single set_status() in backlog/manager.py | in-queue |

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

- [❌] AC-1: `validate_backlog(backlog_root)` returns an empty list for a fully consistent backlog
- [❌] AC-2: Returns error strings for each inconsistency found; each error names the work unit ID and describes the problem
- [❌] AC-3: `devbench validate-backlog` is a registered CLI subcommand reachable via `devbench validate-backlog`
- [❌] AC-4: The CLI command prints each error line to stdout and exits 1 if errors exist, exits 0 otherwise
- [❌] AC-5: `orchestrator.py` calls `validate_backlog()` before the work-unit loop and exits with `sys.exit(1)` if errors are returned
- [❌] AC-6: Orchestrator logs each validation error at `ERROR` level before exiting
- [❌] AC-DOC-1: `devbench validate-backlog --help` output lists all four check categories with descriptions

## Changes Manifest

| Action | File Path |
|--------|-----------|
| modify | `src/devbench/cli.py` |
| modify | `src/devbench/execution/orchestrator.py` |

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

- The core logic `validate_backlog()` belongs in `src/devbench/backlog/manager.py` (or a new `src/devbench/backlog/validator.py`) — CLI is a thin wrapper
- Error messages follow the pattern: `"{work_unit_id}: {description of inconsistency}"`
- Use `argparse` subcommand pattern consistent with existing CLI structure
- Return type annotation: `list[str]` — never raise for a validation failure, always collect and return

## Test Plan (Spec-Driven TDD)

### Contract Definition

```python
def validate_backlog(backlog_root: Path) -> list[str]:
    """Validate backlog consistency. Returns list of error strings; empty = valid."""

# CLI entry point:
# devbench validate-backlog [--backlog-root PATH]
# Defaults backlog_root to Path("backlog") relative to CWD
```

### Acceptance Tests (BDD-style)

# AC-1: clean backlog returns empty list
Given a test backlog directory where all statuses match and all files exist
When `validate_backlog(test_root)` is called
Then the returned list is empty

# AC-2: missing file detected
Given a BACKLOG.md row referencing `backlog/E0-fix-critical-issues/E0.md` but the file does not exist
When `validate_backlog(test_root)` is called
Then the returned list contains a string mentioning "E0" and "not found" or "missing"

# AC-5: orchestrator aborts on errors
Given `validate_backlog()` returns `["E0: status mismatch"]`
When `orchestrator.run()` is called
Then it logs the error and exits before processing any work unit

### Unit Tests

| Test Name | Spec Ref | Status |
|-----------|----------|--------|
| test_validate_backlog_empty_on_clean | Phase 4 §4.3 | ❌ |
| test_validate_backlog_missing_file_error | Phase 4 §4.3 | ❌ |
| test_validate_backlog_status_mismatch_error | Phase 4 §4.3 | ❌ |
| test_validate_backlog_orphaned_file_error | Phase 4 §4.3 | ❌ |
| test_validate_backlog_invalid_dependency_error | Phase 4 §4.3 | ❌ |
| test_orchestrator_aborts_if_validate_fails | Phase 4 §4.3 | ❌ |
| test_cli_validate_backlog_exits_0 | Phase 4 §4.3 | ❌ |

### TDD Cycle Log

<!-- TDD cycle log will be filled in by agent -->

## Rollback Instructions

1. `git checkout main -- src/devbench/cli.py src/devbench/execution/orchestrator.py`
2. Remove `validate_backlog` from `src/devbench/backlog/manager.py` if added there
3. Verify `make validate` passes
4. Note: E2-F2-S1-T1 depends on this task

## Output Location

| Artifact | Path |
|----------|------|
| CLI module | `{JUDGE_WORKSPACE_ROOT}/devbench/src/devbench/cli.py` |
| Orchestrator | `{JUDGE_WORKSPACE_ROOT}/devbench/src/devbench/execution/orchestrator.py` |

## Comments / Agent Log

<!-- Agent log will be filled in during execution -->
