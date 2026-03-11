# E7-F1-S1-T1: Remove JUDGE_ALLOWED_REPOS, JUDGE_BACKLOG_ROOT, JUDGE_BACKLOG_INDEX compat shims

## Status: in-queue

## Spec Reference

| Section | Lines | Description |
|---------|-------|-------------|
| E5 deprecation | 2026-03-11 | Vars deprecated with warning in E5; E7 completes the removal |

## Description

Remove the backward-compatibility shims in `config.py` that honor the three deprecated env vars
(`JUDGE_ALLOWED_REPOS`, `JUDGE_BACKLOG_ROOT`, `JUDGE_BACKLOG_INDEX`) and emit deprecation warnings.
After this task the vars have no effect on runtime behavior. Remove the tests that exercised the
deprecated paths and scrub all documentation references.

## Target Repository

- **Repo:** `caylent-solutions/devbench`
- **Local path:** `{JUDGE_WORKSPACE_ROOT}/devbench/`
- **Branch:** `feature/remove-deprecated-env-vars`

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| E5 | Backlog-Native Configuration | in-progress |

## Blocked By

No blockers. E5 must be merged before this task starts.

## Definition of Ready

- [ ] E5 PR is merged to main2
- [ ] All operators using deprecated vars have been notified (or a migration window has elapsed)

## Definition of Done

- [ ] `JUDGE_ALLOWED_REPOS` compat block removed from `config.py`
- [ ] `JUDGE_BACKLOG_ROOT` compat block removed from `config.py`
- [ ] `JUDGE_BACKLOG_INDEX` compat block removed from `config.py`
- [ ] Corresponding `_log.warning(...)` calls removed
- [ ] `TestAllowedRepos` tests for env var override behavior removed or updated to reflect YAML-only
- [ ] `TestDeprecatedPathEnvVars` class removed from `test_config.py`
- [ ] `README.md` deprecation note removed; config section updated to YAML-only
- [ ] `SYSTEM-OVERVIEW.md` deprecated vars table rows removed
- [ ] `make validate` passes

## Acceptance Criteria

- [ ] AC-1: `config.py` does not read `JUDGE_ALLOWED_REPOS` at import time
- [ ] AC-2: `config.py` does not read `JUDGE_BACKLOG_ROOT` at import time
- [ ] AC-3: `config.py` does not read `JUDGE_BACKLOG_INDEX` at import time
- [ ] AC-4: No `_log.warning` calls reference the three deprecated vars
- [ ] AC-5: `test_judge_allowed_repos_env_var_overrides_yaml` and related env-var override tests are removed
- [ ] AC-6: `TestDeprecatedPathEnvVars` class is removed from `test_config.py`
- [ ] AC-7: README and SYSTEM-OVERVIEW no longer list the three vars as deprecated — they are absent
- [ ] AC-8: `make validate` passes with no regressions

## Changes Manifest

| Action | File Path |
|--------|-----------|
| modify | `src/devbench/config.py` |
| modify | `tests/test_config.py` |
| modify | `README.md` |
| modify | `SYSTEM-OVERVIEW.md` |

## Test Plan

### Unit Tests

| Test Name | Spec Ref | Status |
|-----------|----------|--------|
| test_allowed_repos_from_yaml_when_env_var_absent | AC-1 (retain, rename) | ❌ |
| test_backlog_root_derived_from_workspace_root | AC-2 | ❌ |
| test_backlog_index_derived_from_workspace_root | AC-3 | ❌ |

### Removed Tests

| Test Name | Reason |
|-----------|--------|
| `test_judge_allowed_repos_env_var_overrides_yaml` | compat shim removed |
| `test_judge_allowed_repos_env_var_strips_whitespace` | compat shim removed |
| `test_allowed_repos_from_yaml_when_env_var_empty` | compat shim removed |
| `TestDeprecatedPathEnvVars.test_backlog_root_env_override_warns_deprecated` | compat shim removed |
| `TestDeprecatedPathEnvVars.test_backlog_index_env_override_warns_deprecated` | compat shim removed |

## Rollback Instructions

1. `git checkout main2 -- src/devbench/config.py tests/test_config.py README.md SYSTEM-OVERVIEW.md`

## Output Location

| Artifact | Path |
|----------|------|
| Task spec | `{JUDGE_WORKSPACE_ROOT}/devbench/backlog/E7-deprecation-removal/E7-F1-remove-deprecated-env-vars/E7-F1-S1-remove-compat-shims/E7-F1-S1-T1-remove-deprecated-env-vars.md` |

## TDD Cycle Log

<!-- RED / GREEN / REFACTOR entries added during execution -->

## Comments

<!-- Agent log will be filled in during execution -->
