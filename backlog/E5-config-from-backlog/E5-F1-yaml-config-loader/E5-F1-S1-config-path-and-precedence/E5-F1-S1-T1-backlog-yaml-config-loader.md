# E5-F1-S1-T1: Load backlog YAML config with deterministic precedence and repo branch fallback

## Status: done

## Spec Reference

| Section | Lines | Description |
|---------|-------|-------------|
| Config precedence decision | 2026-03-10 | Implement YAML config loading under backlog path, apply precedence, and support optional per-repo default branch |

## Description

This task implements a backlog-native YAML configuration loader and merges it with the current environment configuration model. It introduces a default config file at `backlog/config/devbench.yaml`, supports path override through existing top-level `--config` flow, and applies value precedence as `env > yaml > code defaults`. Repository entries are defined in YAML with optional `default_branch`; when absent, branch selection falls back to `origin/HEAD`.

## Target Repository

- **Repo:** `caylent-solutions/devbench`
- **Local path:** `{JUDGE_WORKSPACE_ROOT}/devbench/`
- **Branch:** `feature/config-yaml`

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| E0 | Fix Critical Issues | in-queue |

## Blocked By

No blockers.

## Definition of Ready

- [ ] Config schema for `backlog/config/devbench.yaml` is finalized
- [ ] Existing config reads in `src/devbench/config.py` are inventoried
- [ ] Branch resolution consumers are identified (`judges/*`, `github/git_ops.py`)
- [ ] Top-level `--config` handoff contract is confirmed

## Definition of Done

- [ ] Loader reads YAML config from resolved path
- [ ] Path resolution precedence is enforced
- [ ] Value precedence is enforced
- [ ] Repo list and optional default-branch behavior are integrated
- [ ] Branch consumers use shared resolution logic
- [ ] No new CLI flags/options are introduced in `src/devbench/cli.py`
- [ ] Tests pass for loader, precedence, and branch fallback behavior

## Acceptance Criteria

- [ ] AC-1: Default config file path is `backlog/config/devbench.yaml`
- [ ] AC-2: Config path selection order is:
  - `--config` from top-level runner (materialized into runtime path input),
  - else `CONFIG_PATH` environment variable,
  - else default path
- [ ] AC-3: Value precedence is `environment variables > YAML values > existing code defaults`
- [ ] AC-4: YAML schema supports:
  - `env` mapping for current config keys
  - `repos` mapping where keys are full `org/repo`
  - optional `default_branch` per repo
- [ ] AC-5: Allowed repository set is sourced from YAML `repos` keys unless explicitly overridden by environment per precedence rules
- [ ] AC-6: Branch resolution for repo operations uses:
  - configured repo `default_branch` when present,
  - otherwise fallback to `origin/HEAD`
- [ ] AC-7: PR creation path uses resolved base branch explicitly (`--base <branch>`) so non-`main` defaults are deterministic
- [ ] AC-8: No additional CLI flags/options are added in `src/devbench/cli.py`; config-path input remains limited to existing top-level `--config`
- [ ] AC-DOC-1: Config schema and precedence are documented with examples
- [ ] AC-TEST-1: Tests cover path precedence, value precedence, repo parsing, branch fallback, and PR base branch usage

## Changes Manifest

| Action | File Path |
|--------|-----------|
| add | `backlog/config/devbench.yaml` |
| add | `src/devbench/config_loader.py` (or equivalent helper module) |
| modify | `src/devbench/config.py` |
| modify | `src/devbench/judges/base.py` |
| modify | `src/devbench/judges/code_review.py` |
| modify | `src/devbench/judges/doc_review.py` |
| modify | `src/devbench/judges/test_review.py` |
| modify | `src/devbench/judges/changes_manifest.py` |
| modify | `src/devbench/judges/security_review.py` |
| modify | `src/devbench/github/git_ops.py` |
| modify | `tests/` (config + branch resolution coverage) |

## Code Standards and Requirements

### Tier 2: Contextual Rules — Python

- Keep loader logic side-effect free and testable
- Fail fast on malformed YAML schema with actionable errors
- Log resolved config path once at startup; do not log secrets
- Keep compatibility with existing env-based configuration keys
- Avoid broad CLI refactors; only consume existing config-path input

## Test Plan (Spec-Driven TDD)

### Contract Definition

```python
def resolve_config_path(explicit_path: str | None, env: Mapping[str, str]) -> Path:
    """Return config file path using precedence: explicit > CONFIG_PATH > default."""

def load_runtime_config(path: Path, env: Mapping[str, str]) -> RuntimeConfig:
    """Load YAML, apply env overrides, validate, and return typed config."""

def resolve_repo_default_branch(repo: str, repo_path: Path, runtime_config: RuntimeConfig) -> str:
    """Return configured repo default branch, else derive from origin/HEAD."""
```

### Acceptance Tests (BDD-style)

# AC-2: config path precedence
Given an explicit config path from top-level `--config`
When runtime config path is resolved
Then that explicit path is used regardless of `CONFIG_PATH` or defaults

# AC-3: env overrides yaml
Given YAML sets `JUDGE_MAX_RETRIES=5` and environment sets `JUDGE_MAX_RETRIES=10`
When runtime config is loaded
Then effective `MAX_RETRY_ATTEMPTS` is 10

# AC-6: default branch fallback
Given repo `org/repo-a` has no `default_branch` in YAML
When repo default branch is resolved
Then `origin/HEAD` value is used

# AC-7: deterministic PR base
Given repo `org/repo-b` has `default_branch: main2`
When PR is created
Then `gh pr create` is invoked with `--base main2`

### Unit Tests

| Test Name | Spec Ref | Status |
|-----------|----------|--------|
| test_resolve_config_path_prefers_explicit | Config precedence decision | ❌ |
| test_resolve_config_path_uses_config_path_env | Config precedence decision | ❌ |
| test_resolve_config_path_uses_default_backlog_path | Config precedence decision | ❌ |
| test_load_runtime_config_env_overrides_yaml | Config precedence decision | ❌ |
| test_runtime_config_parses_repo_map | Config precedence decision | ❌ |
| test_repo_branch_uses_yaml_default_branch | Config precedence decision | ❌ |
| test_repo_branch_falls_back_to_origin_head | Config precedence decision | ❌ |
| test_create_pr_uses_resolved_base_branch | Config precedence decision | ❌ |
| test_cli_surface_unchanged_for_config_options | Config precedence decision | ❌ |

### TDD Cycle Log

**RED**: Wrote 19 tests in `tests/test_config_loader.py` covering `resolve_config_path` precedence (4), `load_runtime_config` validation (10), `get_configured_default_branch` (3), and dataclass defaults (2). Added test fixture YAML at `tests/fixtures/test_devbench.yaml`. All 19 tests failed (module didn't exist).

**GREEN**: Implemented `src/devbench/config_loader.py` with `RepoConfig`, `RuntimeConfig` dataclasses, `resolve_config_path`, `load_runtime_config`, `get_configured_default_branch`. Updated `config.py` to load YAML at import time via `RUNTIME_CONFIG`, deprecate `JUDGE_ALLOWED_REPOS`. Updated `conftest.py` to set `JUDGE_CONFIG_PATH` to test fixture. All 19 tests passed.

**REFACTOR**: Fixed 2 failing `test_config.py` tests that expected RuntimeError for missing `JUDGE_ALLOWED_REPOS` — replaced with tests verifying YAML-driven repo loading. Updated `judges/base.py` `_get_default_branch` to consult YAML config first. Threaded `repo` kwarg through all 5 judge `evaluate()` methods and their git-diff helpers. Updated `git_ops.py` `create_pr` to pass `--base <branch>`. Added `--config` pre-parse in `cli.py`. Added `test_create_pr_uses_resolved_base_branch`, `test_omits_base_branch_when_not_configured`, and `TestPreParseConfig` tests. Fixed ruff (UP035, ARG001, PLW2901, I001, E402) and mypy (added `types-PyYAML`). `make validate` passes: 374 tests, 1 skipped.

## Rollback Instructions

1. `git checkout main -- src/devbench/config.py src/devbench/github/git_ops.py src/devbench/judges`
2. Remove new loader module and YAML template file
3. Verify `make validate` passes

## Output Location

| Artifact | Path |
|----------|------|
| YAML config template | `{JUDGE_WORKSPACE_ROOT}/devbench/backlog/config/devbench.yaml` |
| Runtime config module | `{JUDGE_WORKSPACE_ROOT}/devbench/src/devbench/config.py` |
| Config loader helper | `{JUDGE_WORKSPACE_ROOT}/devbench/src/devbench/config_loader.py` |

## Comments

2026-03-11 — [REVIEW_PASS] code_review, test_review, doc_review, changes_manifest all passed after 3 review rounds. Remaining LOW/MEDIUM findings accepted as tech debt per 3-round limit. Merged to feature/config-yaml.
