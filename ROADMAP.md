# DevBench Roadmap

Planned work items and technical debt. Items derived from the incomplete backlog and prior review findings.

## Table of contents

- [Dependency Order](#dependency-order)
- [Status legend](#status-legend)
- [In-queue / blocked work items](#in-queue--blocked-work-items)
- [Technical debt](#technical-debt)

## Status legend

- **in-queue** -- Ready to execute. No unmet dependencies.
- **blocked** -- Cannot start until a listed dependency completes.
- **done** -- Completed (omitted from this list; see `BACKLOG.md` index for completed items).

---

## Dependency Order

```
E222 (hold status) ──► E215 (dep integrity)
                  └──► E220 (status --detail)
E210 (YAML config) ──► E208 (deprecation removal)
               └──► E213 (RepoConfig runtime object)
```

Ready to execute with no blockers: **E209, E210, E214, E219, E222, E223**

---

### E208: Deprecation Removal

Removes deprecated environment variable compatibility shims that were soft-deprecated during the YAML config migration (E206). After E210 completes the full YAML migration, residual compat shims in `config.py` and `config_loader.py` can be deleted along with any tests that exercised the deprecated code paths.

**Depends on**: E210

#### E208-F1: Remove deprecated env var shims

**E208-F1-S1: Strip deprecated compat code**

| ID | Title | Status |
|----|-------|--------|
| E208-F1-S1-T1 | Remove deprecated env var shims from config.py/config_loader.py; delete deprecated-path tests | in-queue |

---

### E209: Backlog Contract Alignment

Adds a `docs/backlog-contract.md` schema reference that defines the required sections, field names, and status enum values for all work unit types. Hardens `validate-backlog` to enforce field presence, status enum correctness, and dependency ID format automatically on every run.

**Files**: `src/devbench/backlog/manager.py`, `src/devbench/cli.py`, `docs/backlog-contract.md`

#### E209-F1: Backlog contract document and validate-backlog hardening

**E209-F1-S1: Add contract doc and enforcement checks**

| ID | Title | Status |
|----|-------|--------|
| E209-F1-S1-T1 | Add docs/backlog-contract.md; add field-presence, enum, and dep-format checks to validate-backlog | in-queue |

---

### E210: YAML Schema and Full Config Migration

Extends `devbench.yaml` to cover all non-secret configuration: model selection (`judge_model`, `executor_model`), merge strategy, max retries, and timeout values. After this epic, only `JUDGE_WORKSPACE_ROOT` and credentials need to be environment variables -- everything else lives in the YAML config file with env vars as optional silent overrides.

**Files**: `src/devbench/config_loader.py`, `src/devbench/config.py`, `backlog/config/devbench.yaml` schema

**Blocks**: E208, E213

#### E210-F1: Extend YAML schema to all operational config

**E210-F1-S1: Add model, retry, and timeout fields to YAML schema**

| ID | Title | Status |
|----|-------|--------|
| E210-F1-S1-T1 | Add judge_model, executor_model, merge_strategy, max_executor_retries, all timeout values to YAML schema and config_loader.py | in-queue |

---

### E213: RepoConfig Runtime Object

Enriches `RepoConfig` in `config_loader.py` with runtime-resolved fields (resolved checkout path, validated repo string) so consumers receive a fully-populated object and inline resolution logic is removed from call sites.

**Files**: `src/devbench/config_loader.py`, consumers in `src/devbench/cli.py`, `src/devbench/github/git_ops.py`

**Depends on**: E210

#### E213-F1: Add runtime fields to RepoConfig

**E213-F1-S1: Enrich RepoConfig with resolved runtime properties**

| ID | Title | Status |
|----|-------|--------|
| E213-F1-S1-T1 | Add resolved_checkout_path and validated_repo to RepoConfig; remove inline resolution from call sites | in-queue |

---

### E214: Git Ops Service Cleanup

`GitOpsJudge` in `src/devbench/github/git_ops.py` retains a misleading class name from the old judge architecture. Renames it to `GitOpsService` (or `GitOps`) throughout the codebase and verifies no judge-pattern dependencies remain.

**Files**: `src/devbench/github/git_ops.py`, `src/devbench/cli.py`, all callers

#### E214-F1: Rename GitOpsJudge to GitOpsService

**E214-F1-S1: Rename class and update all call sites**

| ID | Title | Status |
|----|-------|--------|
| E214-F1-S1-T1 | Rename GitOpsJudge → GitOpsService in git_ops.py; update all imports and references | in-queue |

---

### E215: Dependency Integrity Enforcement

The backlog parser's `_deps_satisfied` check only evaluates task-to-task dependencies -- dependencies on epics, features, and stories are silently treated as satisfied. Fixes this to check all dependency types and adds a `sync-blocked` CLI command to bulk-update blocked status.

**Files**: `src/devbench/backlog/parser.py`, `src/devbench/cli.py`

**Depends on**: E222

#### E215-F1: Fix dependency integrity enforcement

**E215-F1-S1: Fix _deps_satisfied and add sync-blocked command**

| ID | Title | Status |
|----|-------|--------|
| E215-F1-S1-T1 | Fix _deps_satisfied to check all dep types; add devbench sync-blocked command; harden validate-backlog dep warnings | blocked (depends on E222-F1-S1-T1) |

---

### E219: Unique Branch Enforcement

Each task work unit derives its branch name from its ID (`backlog/<unit-id-lower>`). A future naming change or manual edit could silently introduce a branch collision causing false review failures. Adds a `validate-backlog` check that fails if two task units derive the same branch name.

**Files**: `src/devbench/backlog/manager.py`

#### E219-F1: Enforce unique branch names in validate-backlog

**E219-F1-S1: Add branch uniqueness check**

| ID | Title | Status |
|----|-------|--------|
| E219-F1-S1-T1 | Add _check_branch_uniqueness to BacklogManager; call from validate() | blocked |

---

### E220: Backlog Status Detail

`devbench status` shows count summaries only. Adds a `--detail` flag that lists all in-queue tasks in priority order with dependency status, all blocked tasks with blocking reasons, and (after E222) all held tasks.

**Files**: `src/devbench/cli.py`

**Depends on**: E222

#### E220-F1: Add --detail flag to devbench status

**E220-F1-S1: Implement status --detail output**

| ID | Title | Status |
|----|-------|--------|
| E220-F1-S1-T1 | Add --detail flag to cmd_status showing in-queue, blocked, and held tasks with dep status | blocked (on E222-F1-S1-T1) |

---

### E222: Work Unit Hold Status

Introduces a `hold` status for work units that are under debate or intentionally deferred. The orchestrator silently skips held units. Adds `devbench hold <id>` and `devbench unhold <id>` CLI commands.

**Files**: `src/devbench/constants.py`, `src/devbench/backlog/work_unit.py`, `src/devbench/cli.py`

**Blocks**: E215, E220

#### E222-F1: Add hold status and hold/unhold commands

**E222-F1-S1: Implement hold status constant, enum value, and CLI commands**

| ID | Title | Status |
|----|-------|--------|
| E222-F1-S1-T1 | Add STATUS_HOLD to constants.py, WorkUnitStatus.HOLD to work_unit.py, cmd_hold/cmd_unhold to cli.py | blocked |

---

### E223: Work Unit Templates and Realignment

Provides canonical template files for epic, feature, story, and task `.md` files so new backlog entries are created consistently with all required sections. Optionally adds a `devbench new-task` CLI command to scaffold task specs from the template.

**Files**: `backlog/templates/` (new), optionally `src/devbench/cli.py`

#### E223-F1: Add work unit templates

**E223-F1-S1: Create templates and scaffold command**

| ID | Title | Status |
|----|-------|--------|
| E223-F1-S1-T1 | Add epic/feature/story/task template .md files under backlog/templates/; add devbench new-task scaffold command | in-queue |

---

## Technical debt

Issues carried over from prior review, mapped to current file paths. Listed by severity within each group; each entry uses a definition-list format to keep file paths readable without column overflow.

### Code quality

**HIGH -- Dynamic attribute setting on dataclass loses type safety**
File: `src/devbench/github/security.py:136`
`setattr()` on a dataclass instance bypasses the type checker; refactor to direct field assignment.

**MEDIUM -- Raw API exception messages may leak details into judge feedback**
File: `src/devbench/github/security.py` (security fetch error handling)
Wrap exception messages before they reach judge feedback to avoid disclosing internal API details.

**MEDIUM -- CLI args not validated at system boundary**
File: `src/devbench/cli.py`
Add type / format / range validation at the CLI entry point rather than relying on downstream code to fail fast.

**MEDIUM -- kwargs concatenated into `gh` CLI args without sanitization**
File: `src/devbench/github/security.py:62-64`
Validate or shell-escape kwargs values before they become subprocess arguments.

**LOW -- `SecurityReview` mixes API fetching, parsing, and summarization (SRP violation)**
File: `src/devbench/github/security.py`
Split into a fetch service, a parser, and a summarizer for testability.

### Test quality

**CRITICAL -- Hardcoded path in test**
File: `tests/test_backlog/test_parser.py:18-20`
Replace with the pytest `tmp_path` fixture.

**CRITICAL -- Hardcoded `/tmp` log path**
File: `tests/conftest.py:10`
Use the pytest `tmp_path` fixture instead of writing to a fixed `/tmp` path.

**HIGH -- No `@pytest.mark.unit` / `@pytest.mark.functional` decorators on any test**
File: all test files
Categorize tests so `make test-unit` and `make test-functional` can target subsets.

**HIGH -- Fixtures defined in `conftest.py` instead of `tests/fixtures/`**
File: `tests/conftest.py`
Move data fixtures out of `conftest.py` into a dedicated fixtures directory.

**MEDIUM -- `importlib.reload(config)` causes test state pollution**
File: `tests/test_config.py:185-210`
Refactor to use a fixture-scoped fresh config rather than mutating module state.

**MEDIUM -- Duplicate predicate tests not parameterized**
File: `tests/test_backlog/test_work_unit.py:203-251`
Collapse with `@pytest.mark.parametrize`.

**MEDIUM -- `cmd_next` assertions only check one field of JSON output**
File: `tests/test_cli.py:132`
Assert on the full envelope shape, not just one field.
