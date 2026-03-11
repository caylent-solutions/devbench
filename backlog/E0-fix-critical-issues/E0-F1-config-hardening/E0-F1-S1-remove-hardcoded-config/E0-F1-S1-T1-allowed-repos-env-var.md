# E0-F1-S1-T1: Move ALLOWED_REPOS to JUDGE_ALLOWED_REPOS env var

## Status: done

## Spec Reference

| Section | Lines | Description |
|---------|-------|-------------|
| Phase 3 §3.1 | — | Move `ALLOWED_REPOS` to `JUDGE_ALLOWED_REPOS` env var; parse at import time; raise `RuntimeError` if unset or empty |

## Description

This task removes the hardcoded `frozenset` of Caylent-specific repository names from `config.py` and replaces it with a dynamic parse of the `JUDGE_ALLOWED_REPOS` environment variable. The variable accepts a comma-separated list of `org/repo` strings. If the variable is absent or resolves to an empty set after parsing, a `RuntimeError` is raised immediately at import time with a message that tells the user exactly what to set and why.

## Target Repository

- **Repo:** `caylent-solutions/devbench`
- **Local path:** `{JUDGE_WORKSPACE_ROOT}/devbench/`
- **Branch:** `feature/remove-hardcoded-config`

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| — | None — first task in story | — |

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

- [❌] AC-1: Hardcoded `frozenset` of repo names is removed from `config.py`
- [❌] AC-2: `JUDGE_ALLOWED_REPOS` absent or empty raises `RuntimeError` with message containing `"JUDGE_ALLOWED_REPOS"`
- [❌] AC-3: `JUDGE_ALLOWED_REPOS="org/repo1, org/repo2"` produces `ALLOWED_REPOS == frozenset({"org/repo1", "org/repo2"})` (whitespace stripped)
- [❌] AC-4: `ALLOWED_REPOS` is still exported as a `frozenset[str]` — callers require no changes
- [❌] AC-DOC-1: Inline comment in `config.py` documents `JUDGE_ALLOWED_REPOS` format and the fail-fast behavior

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

- Use `os.environ.get("JUDGE_ALLOWED_REPOS", "")` then check if the result is falsy
- Strip whitespace from each repo entry after splitting on commas
- Filter out empty strings before building the `frozenset`
- `RuntimeError` message must tell the user the variable name, expected format, and an example value

## Test Plan (Spec-Driven TDD)

### Contract Definition

After this task, `config.py` contains:
```python
_allowed_repos_raw = os.environ.get("JUDGE_ALLOWED_REPOS", "")
if not _allowed_repos_raw:
    raise RuntimeError(
        "JUDGE_ALLOWED_REPOS environment variable is not set. "
        "Provide a comma-separated list of allowed repositories (e.g. org/repo1,org/repo2)."
    )
ALLOWED_REPOS: frozenset[str] = frozenset(r.strip() for r in _allowed_repos_raw.split(",") if r.strip())
```

### Acceptance Tests (BDD-style)

# AC-2: JUDGE_ALLOWED_REPOS absent
Given `JUDGE_ALLOWED_REPOS` is not in `os.environ`
When the `config` module is imported (env patched via `importlib.reload`)
Then `RuntimeError` is raised and its message contains "JUDGE_ALLOWED_REPOS"

# AC-3: JUDGE_ALLOWED_REPOS with whitespace padding
Given `JUDGE_ALLOWED_REPOS=" org/repo1 , org/repo2 "`
When the `config` module is imported
Then `ALLOWED_REPOS == frozenset({"org/repo1", "org/repo2"})`

# AC-4: ALLOWED_REPOS type preserved
Given `JUDGE_ALLOWED_REPOS="org/repo"`
When the `config` module is imported
Then `isinstance(ALLOWED_REPOS, frozenset)` is `True`

### Unit Tests

| Test Name | Spec Ref | Status |
|-----------|----------|--------|
| test_allowed_repos_raises_when_env_var_not_set | Phase 3 §3.1 | ❌ |
| test_allowed_repos_raises_when_env_var_empty_string | Phase 3 §3.1 | ❌ |
| test_allowed_repos_parses_single_repo | Phase 3 §3.1 | ❌ |
| test_allowed_repos_parses_multiple_repos | Phase 3 §3.1 | ❌ |
| test_allowed_repos_strips_whitespace | Phase 3 §3.1 | ❌ |
| test_allowed_repos_is_frozenset | Phase 3 §3.1 | ❌ |

### TDD Cycle Log

<!-- TDD cycle log will be filled in by agent -->

## Rollback Instructions

1. `git checkout main -- src/devbench/config.py`
2. Verify `make validate` passes
3. Note: T2 (workspace root) and T3 (merge strategy) in this story depend on this task completing first

## Output Location

| Artifact | Path |
|----------|------|
| Config module | `{JUDGE_WORKSPACE_ROOT}/devbench/src/devbench/config.py` |

## Comments / Agent Log

<!-- Agent log will be filled in during execution -->
