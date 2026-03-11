# E0-F1-S1-T2: Remove hardcoded WORKSPACE_ROOT default (fail-fast)

## Status: done

## Spec Reference

| Section | Lines | Description |
|---------|-------|-------------|
| Phase 3 §3.3 | — | Remove the hardcoded `/workspaces/general-agent-env` default; raise `RuntimeError` if `JUDGE_WORKSPACE_ROOT` is not set |

## Description

This task strips the hardcoded `/workspaces/general-agent-env` fallback from `WORKSPACE_ROOT` in `config.py`. Instead of silently defaulting to a path that only exists in one specific container environment, the code will raise a `RuntimeError` at import time if `JUDGE_WORKSPACE_ROOT` is not set, with a message that explains what to set and why. This prevents silent misconfiguration where DevBench appears to run but targets the wrong workspace.

## Target Repository

- **Repo:** `caylent-solutions/devbench`
- **Local path:** `{JUDGE_WORKSPACE_ROOT}/devbench/`
- **Branch:** `feature/remove-hardcoded-config`

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| E0-F1-S1-T1 | Move ALLOWED_REPOS to JUDGE_ALLOWED_REPOS env var | in-queue |

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

- [❌] AC-1: The string `/workspaces/general-agent-env` does not appear anywhere in `config.py`
- [❌] AC-2: `JUDGE_WORKSPACE_ROOT` absent raises `RuntimeError` whose message contains "JUDGE_WORKSPACE_ROOT" and instructs the user to set it to an absolute path
- [❌] AC-3: `WORKSPACE_ROOT` is a `pathlib.Path` object when the env var is set
- [❌] AC-4: Existing callers of `WORKSPACE_ROOT` that use it as a `Path` require no changes
- [❌] AC-DOC-1: Inline comment in `config.py` documents `JUDGE_WORKSPACE_ROOT`, its expected format (absolute path), and provides an example

## Changes Manifest

| Action | File Path |
|--------|-----------|
| modify | `src/devbench/config.py` |

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

- Use `os.environ.get("JUDGE_WORKSPACE_ROOT", "")` and check for empty string
- Wrap the value in `pathlib.Path()` after the guard check
- The error message must be a full sentence: what the variable is, what it should contain, and an example

## Test Plan (Spec-Driven TDD)

### Contract Definition

After this task, `config.py` contains:
```python
_workspace_root = os.environ.get("JUDGE_WORKSPACE_ROOT", "")
if not _workspace_root:
    raise RuntimeError(
        "JUDGE_WORKSPACE_ROOT environment variable is not set. "
        "Set it to the absolute path of your workspace root (e.g. /workspaces/my-env)."
    )
WORKSPACE_ROOT: Path = Path(_workspace_root)
```

No occurrence of `/workspaces/general-agent-env` remains in the file.

### Acceptance Tests (BDD-style)

# AC-2: WORKSPACE_ROOT absent raises
Given `JUDGE_WORKSPACE_ROOT` is not in `os.environ`
When `config` is imported
Then `RuntimeError` is raised and message contains "JUDGE_WORKSPACE_ROOT"

# AC-3: WORKSPACE_ROOT is a Path
Given `JUDGE_WORKSPACE_ROOT="/workspaces/test"`
When `config` is imported
Then `WORKSPACE_ROOT == Path("/workspaces/test")` and `isinstance(WORKSPACE_ROOT, Path)` is `True`

# AC-1: Hardcoded path removed
Given the contents of `config.py`
When searched for the string `/workspaces/general-agent-env`
Then no match is found

### Unit Tests

| Test Name | Spec Ref | Status |
|-----------|----------|--------|
| test_workspace_root_raises_when_env_var_not_set | Phase 3 §3.3 | ❌ |
| test_workspace_root_raises_when_env_var_empty | Phase 3 §3.3 | ❌ |
| test_workspace_root_is_path_object | Phase 3 §3.3 | ❌ |
| test_workspace_root_error_message_is_actionable | Phase 3 §3.3 | ❌ |
| test_hardcoded_path_not_present_in_config | Phase 3 §3.3 | ❌ |

### TDD Cycle Log

<!-- TDD cycle log will be filled in by agent -->

## Rollback Instructions

1. `git checkout main -- src/devbench/config.py`
2. Verify `make validate` passes
3. Note: T3 (merge strategy) depends on this task; rolling back will block T3

## Output Location

| Artifact | Path |
|----------|------|
| Config module | `{JUDGE_WORKSPACE_ROOT}/devbench/src/devbench/config.py` |

## Comments / Agent Log

<!-- Agent log will be filled in during execution -->
