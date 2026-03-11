# E0-F1-S1-T3: Add JUDGE_MERGE_STRATEGY configurable env var

## Status: done

## Spec Reference

| Section | Lines | Description |
|---------|-------|-------------|
| Phase 3 §3.2 | — | Add `JUDGE_MERGE_STRATEGY` env var; thread through to `git_ops.py` `merge_pr()`; validate against `merge`, `squash`, `rebase` |

## Description

This task adds `JUDGE_MERGE_STRATEGY` to `config.py` and wires it into `git_ops.py` `merge_pr()`. The current code hardcodes `--merge` in every PR merge call, which causes infinite failure loops on repositories that enforce squash-only branch protection. After this task, the merge flag is selected dynamically from the config constant, defaulting to `squash` and rejecting any value outside the three supported strategies.

## Target Repository

- **Repo:** `caylent-solutions/devbench`
- **Local path:** `{JUDGE_WORKSPACE_ROOT}/devbench/`
- **Branch:** `feature/remove-hardcoded-config`

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| E0-F1-S1-T2 | Remove hardcoded WORKSPACE_ROOT default (fail-fast) | in-queue |

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

- [❌] AC-1: `MERGE_STRATEGY` in `config.py` defaults to `"squash"` when `JUDGE_MERGE_STRATEGY` is not set
- [❌] AC-2: `JUDGE_MERGE_STRATEGY` set to any value outside `{"merge", "squash", "rebase"}` raises `RuntimeError` at import time listing valid options
- [❌] AC-3: `git_ops.py` `merge_pr()` uses a lookup dict to map `MERGE_STRATEGY` to `--merge`, `--squash`, or `--rebase` flag
- [❌] AC-4: The hardcoded `--merge` string is removed from `git_ops.py`
- [❌] AC-DOC-1: Inline comment in `config.py` documents `JUDGE_MERGE_STRATEGY`, the three valid values, and why `squash` is the default

## Changes Manifest

| Action | File Path |
|--------|-----------|
| modify | `src/devbench/config.py` |
| modify | `src/devbench/github/git_ops.py` |

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

- Use a constant `_VALID_MERGE_STRATEGIES = frozenset({"merge", "squash", "rebase"})` to check against
- Use a dict literal to map strategy string to CLI flag; avoid if/elif chains
- Import `MERGE_STRATEGY` from `config` in `git_ops.py`; do not duplicate the validation logic

## Test Plan (Spec-Driven TDD)

### Contract Definition

`config.py` adds:
```python
MERGE_STRATEGY: str = os.environ.get("JUDGE_MERGE_STRATEGY", "squash")
if MERGE_STRATEGY not in {"merge", "squash", "rebase"}:
    raise RuntimeError(f"JUDGE_MERGE_STRATEGY must be one of: merge, squash, rebase. Got: {MERGE_STRATEGY!r}")
```

`git_ops.py` `merge_pr()` uses:
```python
flag = {"merge": "--merge", "squash": "--squash", "rebase": "--rebase"}[MERGE_STRATEGY]
rc, _, stderr = self._gh(["pr", "merge", str(pr_number), flag], ...)
```

### Acceptance Tests (BDD-style)

# AC-1: MERGE_STRATEGY defaults to squash
Given `JUDGE_MERGE_STRATEGY` is not set
When `config` is imported
Then `MERGE_STRATEGY == "squash"`

# AC-2: Invalid strategy raises
Given `JUDGE_MERGE_STRATEGY="fast-forward"`
When `config` is imported
Then `RuntimeError` is raised containing "JUDGE_MERGE_STRATEGY" and listing valid options

# AC-3: git_ops uses config strategy
Given `MERGE_STRATEGY = "rebase"` in config
When `merge_pr(99)` is called on a `GitOps` instance
Then the subprocess receives `["pr", "merge", "99", "--rebase"]`

### Unit Tests

| Test Name | Spec Ref | Status |
|-----------|----------|--------|
| test_merge_strategy_defaults_to_squash | Phase 3 §3.2 | ❌ |
| test_merge_strategy_accepts_merge | Phase 3 §3.2 | ❌ |
| test_merge_strategy_accepts_rebase | Phase 3 §3.2 | ❌ |
| test_merge_strategy_raises_on_invalid | Phase 3 §3.2 | ❌ |
| test_git_ops_merge_pr_passes_squash_flag | Phase 3 §3.2 | ❌ |
| test_git_ops_merge_pr_passes_merge_flag | Phase 3 §3.2 | ❌ |
| test_git_ops_merge_pr_passes_rebase_flag | Phase 3 §3.2 | ❌ |

### TDD Cycle Log

<!-- TDD cycle log will be filled in by agent -->

## Rollback Instructions

1. `git checkout main -- src/devbench/config.py src/devbench/github/git_ops.py`
2. Verify `make validate` passes
3. Note: E0-F1-S2-T1 (script hardening) depends on this task completing; rolling back will block it

## Output Location

| Artifact | Path |
|----------|------|
| Config module | `{JUDGE_WORKSPACE_ROOT}/devbench/src/devbench/config.py` |
| Git ops module | `{JUDGE_WORKSPACE_ROOT}/devbench/src/devbench/github/git_ops.py` |

## Comments / Agent Log

<!-- Agent log will be filled in during execution -->
