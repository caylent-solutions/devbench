# E5-F1-S1-T2: Add checkout_directory mapping and simplify bootstrap path contract

## Status: done

## Spec Reference

| Section | Lines | Description |
|---------|-------|-------------|
| Config topology decision | 2026-03-11 | Add per-repo checkout directory mapping under workspace root and simplify bootstrap paths |

## Description

This task extends backlog YAML config so each repository can define an optional `checkout_directory`, resolved relative to `JUDGE_WORKSPACE_ROOT`. It also simplifies runtime bootstrap behavior by standardizing path derivation from `JUDGE_WORKSPACE_ROOT` and retaining `JUDGE_CONFIG_PATH` as the optional config-file override path.

## Target Repository

- **Repo:** `caylent-solutions/devbench`
- **Local path:** `{JUDGE_WORKSPACE_ROOT}/devbench/`
- **Branch:** `feature/config-yaml`

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| E5-F1-S1-T1 | Load backlog YAML config with deterministic precedence and repo branch fallback | in-review |

## Blocked By

No blockers.

## Definition of Ready

- [ ] T1 config-loader behavior has landed and is stable for extension
- [ ] Repo canonicalization contract is agreed (`work_unit.repo` input, canonical full repo internally)
- [ ] Field name is agreed (`checkout_directory`) and scoped to repo topology only

## Definition of Done

- [ ] YAML loader parses and validates `repos.<org/repo>.checkout_directory`
- [ ] Local checkout mapping resolves from `JUDGE_WORKSPACE_ROOT / checkout_directory`
- [ ] Fallback behavior remains `JUDGE_WORKSPACE_ROOT / <repo-short-name>` when `checkout_directory` is omitted
- [ ] Orchestrator resolves repo to canonical full name once and uses canonical repo for validate/path/downstream calls
- [ ] Deprecated env vars emit warnings with migration guidance
- [ ] Tests and docs updated for the new mapping and bootstrap contract

## Acceptance Criteria

- [ ] AC-1: YAML schema supports optional `repos.<org/repo>.checkout_directory`
- [ ] AC-2: `checkout_directory` is interpreted as a path relative to `JUDGE_WORKSPACE_ROOT`
- [ ] AC-3: Absolute `checkout_directory` values fail fast with actionable error messages
- [ ] AC-4: `checkout_directory` values containing parent traversal (`..`) fail fast with actionable error messages
- [ ] AC-5: If `checkout_directory` is omitted, local checkout defaults to `JUDGE_WORKSPACE_ROOT / <repo-short-name>`
- [ ] AC-6: `work_unit.repo` may remain short or full in backlog input, but runtime canonicalizes once and uses full `org/repo` internally
- [ ] AC-7: `JUDGE_BACKLOG_ROOT` and `JUDGE_BACKLOG_INDEX` are deprecated with warning-only compatibility behavior
- [ ] AC-8: `JUDGE_ALLOWED_REPOS` remains deprecated and is not reintroduced as a required startup input
- [ ] AC-9: Existing config-path selection contract remains unchanged (`--config` / `JUDGE_CONFIG_PATH` / default)

## Changes Manifest

| Action | File Path |
|--------|-----------|
| modify | `src/devbench/config_loader.py` |
| modify | `src/devbench/config.py` |
| modify | `src/devbench/execution/orchestrator.py` |
| modify | `backlog/config/devbench.yaml` |
| modify | `scripts/start.sh` |
| modify | `scripts/start-interactive.sh` |
| modify | `README.md` |
| modify | `SYSTEM-OVERVIEW.md` |
| modify | `tests/test_config_loader.py` |
| modify | `tests/test_config.py` |
| modify | `tests/test_execution/test_orchestrator.py` |

## Test Plan

### Unit Tests

| Test Name | Spec Ref | Status |
|-----------|----------|--------|
| test_loader_parses_checkout_directory | AC-1 | ❌ |
| test_checkout_directory_resolves_relative_to_workspace_root | AC-2 | ❌ |
| test_checkout_directory_rejects_absolute_path | AC-3 | ❌ |
| test_checkout_directory_rejects_parent_traversal | AC-4 | ❌ |
| test_repo_local_path_falls_back_to_repo_short_name | AC-5 | ❌ |
| test_orchestrator_canonicalizes_repo_once | AC-6 | ❌ |
| test_backlog_root_env_override_warns_deprecated | AC-7 | ❌ |
| test_backlog_index_env_override_warns_deprecated | AC-7 | ❌ |
| test_allowed_repos_env_still_warns_deprecated | AC-8 | ❌ |
| test_config_path_precedence_unchanged | AC-9 | ❌ |

## Rollback Instructions

1. `git checkout main -- src/devbench/config_loader.py src/devbench/config.py src/devbench/execution/orchestrator.py`
2. `git checkout main -- backlog/config/devbench.yaml scripts/start.sh scripts/start-interactive.sh`
3. `git checkout main -- README.md SYSTEM-OVERVIEW.md tests/test_config_loader.py tests/test_config.py tests/test_execution/test_orchestrator.py`

## Output Location

| Artifact | Path |
|----------|------|
| Task spec | `{JUDGE_WORKSPACE_ROOT}/devbench/backlog/E5-config-from-backlog/E5-F1-yaml-config-loader/E5-F1-S1-config-path-and-precedence/E5-F1-S1-T2-checkout-directory-and-bootstrap.md` |

## TDD Cycle Log

**RED**: Added 13 failing tests — `TestCheckoutDirectory` (5 tests: parse, omit, reject-absolute, reject-traversal, reject-non-string), `TestGetRepoLocalPath` (4 tests: uses checkout_directory, falls back to short name, missing repo, explicit None), `TestDeprecatedPathEnvVars` (2 tests: BACKLOG_ROOT/BACKLOG_INDEX warn deprecated), orchestrator `test_canonicalizes_short_repo_name` (1 test). All failed with ImportError or AttributeError.

**GREEN**: Added `checkout_directory` to `RepoConfig`; added validation in `load_runtime_config` (reject absolute, reject `..`); added `get_repo_local_path()` pure function; updated `config.py` `REPO_LOCAL_PATHS` to use `get_repo_local_path`, added deprecation warnings for `JUDGE_BACKLOG_ROOT`/`JUDGE_BACKLOG_INDEX`; updated `orchestrator.py` to canonicalize repo once via `resolve_repo` and use `canonical_repo` throughout; removed `JUDGE_ALLOWED_REPOS` from required_vars in scripts. All 13 tests passed.

**REFACTOR**: Fixed `PLR0912` (too many branches) with noqa in `load_runtime_config`. Updated `backlog/config/devbench.yaml`, `README.md`, `SYSTEM-OVERVIEW.md` with `checkout_directory` docs and deprecation notes. `make validate` passes: 406 tests, 1 skipped.

## Comments

2026-03-11 — [REVIEW_PASS] code_review, test_review, doc_review, changes_manifest all passed after 3 review rounds. Remaining LOW/MEDIUM findings accepted as tech debt per 3-round limit. Merged to feature/config-yaml.
