# DevBench Roadmap

Planned work items and technical debt. Items derived from the incomplete backlog and prior review findings.

## Table of contents

- [Dependency Order](#dependency-order)
- [Status legend](#status-legend)
- [In-queue / blocked work items](#in-queue--blocked-work-items)
- [Technical debt](#technical-debt)

## Status legend

- **in-queue** — Ready to execute. No unmet dependencies.
- **blocked** — Cannot start until a listed dependency completes.
- **done** — Completed (omitted from this list; see `BACKLOG.md` index for completed items).

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

Extends `devbench.yaml` to cover all non-secret configuration: model selection (`judge_model`, `executor_model`), merge strategy, max retries, and timeout values. After this epic, only `JUDGE_WORKSPACE_ROOT` and credentials need to be environment variables — everything else lives in the YAML config file with env vars as optional silent overrides.

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

The backlog parser's `_deps_satisfied` check only evaluates task-to-task dependencies — dependencies on epics, features, and stories are silently treated as satisfied. Fixes this to check all dependency types and adds a `sync-blocked` CLI command to bulk-update blocked status.

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

### E224: Agentic backlog authoring guardrails (post-Backlog-A lessons)

Codifies the lessons learned from Backlog A's first orchestration run as documentation and validate-backlog rules so the next agentic backlog author hits these constraints at authoring time, not run time.

**Files**: `docs/acceptance-criteria-canonical.md`, `docs/manual-blockers.md`, `docs/backlog-author-discovery.md`, `docs/cross-backlog-dependencies.md`, `docs/source-test-atomicity.md` (Tier 1, landed); `docs/creating-specs-and-backlogs.md`, `docs/task-factory.md`, `docs/backlog-contract.md`, `plugin/devbench/agents/blocker-resolver.md`, `plugin/devbench/agents/task-factory.md`, `plugin/devbench/agents/executor.md` (Tier 2 updates, landed); `src/devbench/cli.py`, `src/devbench/backlog/manager.py`, `src/devbench/backlog/proposal.py` (Tier 3, landed)

#### E224-F1: Tier 3 validate-backlog rules and pre-flight check

**E224-F1-S1: Land tooling for the new rules**

| ID | Title | Status |
|----|-------|--------|
| E224-F1-S1-T1 | Add Manifest Conflict, Language-AC Alignment, and Source-Test Atomicity rules to validate-backlog; add devbench check pre-flight; add test-validates-source heuristic to promote-proposal; allow multi-token --reason on add-dep | done |

---

### E225: git-ops orphan-pattern self-defense

Adds a third safety rail to `cmd_git_ops` (alongside the existing manifest-scope and branch-anchor checks) that detects build/state artefacts in staged or tracked paths and self-emits a cleanup proposal. Eliminates the cascade-deadlock pattern observed in production where polluted commits (a 626 MB AWS provider binary, terraform.tfstate, `__pycache__/`, `.coverage`) tripped security-review on every later task with no auto-resolution path. The cascade now self-heals without operator intervention; operators can also run the new `cleanup-tracked-orphans` command directly to clear an existing polluted state.

**Files**: `src/devbench/git_orphans.py` (new), `src/devbench/cli.py`, `tests/test_git_orphans.py` (new), `docs/cli-reference.md`, `docs/backlog-contract.md`

#### E225-F1: Land orphan-pattern guard and cleanup command

**E225-F1-S1: Implement detection, cleanup, and git-ops integration**

| ID | Title | Status |
|----|-------|--------|
| E225-F1-S1-T1 | Add `git_orphans` module with pattern matcher + tracked/staged detection + idempotent cleanup; register `cleanup-tracked-orphans` CLI command; gate `cmd_git_ops` and `_git_ops_deferred` on detection with auto-proposal emission; document override env var `DEVBENCH_ORPHAN_IGNORE_PATTERNS`; 43 unit tests covering globstar matcher, env-var override, polluted-repo detection, dry-run, idempotency, and gitignore-extension semantics | done |

---

### E226: write-proposal auto-cascade (close the resolver-write timing gap)

When `task_factory.auto_accept_proposals: true`, `cmd_write_proposal` now calls `materialise-proposal` + `promote-proposal` synchronously inside the same Python invocation. This closes a timing window in which a resolver-written proposal could sit orphaned for up to one full orchestrator iteration (between `write-proposal` and the next `sweep-proposals` cycle) -- long enough for the source task to bucket as "needs operator attention" before the auto-clearing dep row landed. Soft-failure semantics: cascade errors are logged and reported in the output JSON but never propagate as non-zero so the JSON-on-disk + next-sweep retry path remains intact. Behaviour unchanged when the flag is `false`.

**Files**: `src/devbench/cli.py`, `tests/test_cli.py`, `docs/cli-reference.md`

#### E226-F1: Land write-proposal auto-cascade

**E226-F1-S1: Implement and test the cascade**

| ID | Title | Status |
|----|-------|--------|
| E226-F1-S1-T1 | Extract `_maybe_auto_cascade_proposal` helper from `cmd_write_proposal`; wire it after `write_proposal` returns; add 3 tests (disabled-when-flag-off, failed-when-source-missing-from-index, applied-end-to-end-materialise+promote); document the new `auto_cascade` JSON output field in cli-reference.md | done |

---

### E227: log-verdict allowlist + executor-scope hook + .gitignore .coverage* fix

Three correctness fixes prompted by an empirical `log-verdict judge <id> pass` line that landed in production audit history:

- **Bug 1 (CLI allowlist):** `cmd_log_verdict` validates `<judge>` against `devbench.constants.KNOWN_JUDGE_NAMES` (single source of truth). Refuses any name outside the canonical-5 reviewers union the 4 workflow agents that legitimately write audit-only verdicts. Catches typos (`judge`, hyphenated forms, casing) at CLI entry rather than after the malformed row lands in the audit trail.
- **Bug 2 (hook scope):** `guard-verdict-format.sh` extracts `agent_type` and refuses canonical reviewer verdicts when the caller is `devbench:executor`. Audit-only `executor` judge name remains allowed (records progress without counting toward the done-gate). Same hook now uses jq + sed fallback for JSON parsing instead of `python3 -c shlex` so it works under asdf-shim PATHs that previously caused the guard to silently bow out. Pure-bash arg-splitting replaces the Python shlex parser for the same reason.
- **Bug 3 (.gitignore globber):** `git_orphans._DEFAULT_GITIGNORE_ENTRIES` writes `.coverage*` (no separator) instead of `.coverage` + `.coverage.*` so pytest-cov's stray `.coverage (1)` filename (allocated when the canonical file is locked) is matched by git's gitignore globber.

**Files**: `src/devbench/cli.py`, `src/devbench/constants.py`, `src/devbench/git_orphans.py`, `plugin/devbench/scripts/guard-verdict-format.sh`, `tests/test_cli.py`, `docs/cli-reference.md`

#### E227-F1: Land the three fixes

**E227-F1-S1: Implement and test**

| ID | Title | Status |
|----|-------|--------|
| E227-F1-S1-T1 | Add `KNOWN_JUDGE_NAMES` + `WORKFLOW_AGENT_JUDGE_NAMES` to constants; gate `cmd_log_verdict`; mirror the allowlist + add `agent_type==devbench:executor` scope check to `guard-verdict-format.sh`; replace python3-shlex parser with bash-native arg splitter; switch JSON field extraction to jq + sed fallback; collapse `.gitignore` coverage entries to `.coverage*`; 14 unit tests (5 reject + 9 accept) covering every name in the allowlist; 12 hook smoke-test scenarios | done |

---

### E228: Report log-path resolver + BACKLOG-vs-throughput divergence warning + ruff sweep

Three fixes addressing operator-observed report behaviour:

- **Log-path resolver:** `cmd_report` no longer falls back to the devbench source-tree's log when `JUDGE_LOG_FILE` is unset. New helper `_resolve_log_file_path` derives `<JUDGE_WORKSPACE_ROOT>/logs/orchestrator.log` as the canonical workspace-local path or fail-fasts with an actionable error when neither env var is set. Eliminates the silent "different log read" failure mode that produced the operator-observed `Tasks completed in window: 0` while `BACKLOG.md` showed a non-zero done count.
- **Divergence warning:** when `BACKLOG.md` reports completed tasks but the All-time throughput window finds zero `Set <id> to 'done'` events, the report now emits a one-line WARNING above the trailing summary naming the log file path. Surfaces the data-source mismatch deterministically rather than letting two materially different counts sit silently in the same table.
- **Ruff sweep:** fixed all 5 outstanding ruff errors across `src/` + `tests/` (RUF012 ClassVar annotations on `BacklogManager`'s class-level dict / tuple / frozenset; SIM103 condition-return collapses; PLR0912 `cmd_check` decomposition into 4 single-rail `_check_repo_*` helpers; E501 multi-line string for the registered command description). `ruff check src/ tests/` now reports `All checks passed!`. Also removed the now-unused `# type: ignore[name-defined]  # noqa: F821` annotation on `_read_proposal_from_stdin` (the import that justified it landed at the top of `cli.py` in an earlier turn).

**Files**: `src/devbench/cli.py`, `src/devbench/reporting/report.py`, `src/devbench/backlog/manager.py`, `tests/test_cli.py`, `tests/test_reporting/test_report.py`, `docs/cli-reference.md`

#### E228-F1: Land the resolver + warning + ruff sweep

**E228-F1-S1: Implement and test**

| ID | Title | Status |
|----|-------|--------|
| E228-F1-S1-T1 | Replace `cmd_report` log-path default with fail-fast resolver helper; add divergence WARNING in `generate_report` when backlog done count > 0 and All-time throughput == 0; refactor `cmd_check` into 4 single-rail helpers to satisfy PLR0912; add `ClassVar` annotations to `BacklogManager` class-level pattern constants; collapse 2 SIM103 if/else returns; remove dead `# type: ignore[name-defined]` annotation from `_read_proposal_from_stdin`; 4 resolver-precedence tests + 4 divergence-warning tests; full `ruff check src/ tests/` clean | done |

---

### E229: `log_file:` YAML field as single source of truth (writer + reader)

E228 fixed `cmd_report`'s reader-side resolver but left a coordination burden on operators: the orchestrator's `setup_logging` writer used a separate path (`<devbench>/src/devbench/logs/orchestrator.log` source-tree default), so per-pane `JUDGE_LOG_FILE` env vars were the only way to keep reader and writer in sync. The launch-commands file shipped with three different `JUDGE_LOG_FILE` values across the three panes (orchestrator.log / report.log / hook-tail.log) under the false assumption that "log isolation" was needed; multiple processes appending to the same log are POSIX-safe (line writes <4KB are atomic) and the report MUST read the same log the orchestrator writes for its throughput count to match `BACKLOG.md`'s done count.

This epic promotes the log-file path from per-process env-var coordination to a single per-workspace YAML field:

- **`log_file:` top-level YAML field:** added to `RuntimeConfig` (`config_loader.py`), `config-schema.json` (with description), and to all three live backlog YAMLs (`caylent-telemetry-spec/backlog/config/devbench.yaml`, `tf-modules-backlog/...`, `phase2-backlog/...`). Authors declare the path once; every devbench invocation against that workspace resolves to the same file.
- **Mirrored resolver in `setup_logging`:** new `_resolve_log_file()` in `log_setup.py` consults the same chain as `cli._resolve_log_file_path` (`JUDGE_LOG_FILE` env > `RUNTIME_CONFIG.log_file` > `<JUDGE_WORKSPACE_ROOT>/logs/orchestrator.log` > source-tree default). The yaml field is workspace-relative when not absolute. Lazy-imports `RUNTIME_CONFIG` to avoid a circular import.
- **Launch-commands cleanup:** `caylent-telemetry-spec/devbench-launch-commands.txt` no longer sets `JUDGE_LOG_FILE` on any of the three panes; the comment block now documents the YAML-as-single-source-of-truth rule and the prior misconception about needing per-process log isolation.
- **Test coverage:** 5 new `TestResolveLogFileYamlConfig` cases in `tests/test_cli.py` cover env-var-wins, yaml-relative-resolves-against-workspace, yaml-absolute, workspace-default-when-yaml-absent, and fail-fast-when-no-source. `tests/test_log_setup.py::test_uses_default_path_when_env_not_set` updated to also unset `JUDGE_WORKSPACE_ROOT` (since the new resolver consumes it before the source-tree fallback).
- **Doc updates:** `docs/cli-reference.md` (3-step resolution chain, `cmd_log` destination, watchdog caveat), `docs/architecture.md` (`Reporting & observability` updated, sample yaml gains `log_file:` field).

**Files**: `src/devbench/config_loader.py`, `src/devbench/config-schema.json`, `src/devbench/log_setup.py`, `src/devbench/cli.py`, `tests/test_cli.py`, `tests/test_log_setup.py`, `caylent-telemetry-spec/backlog/config/devbench.yaml`, `caylent-telemetry-spec/tf-modules-backlog/backlog/config/devbench.yaml`, `caylent-telemetry-spec/phase2-backlog/backlog/config/devbench.yaml`, `caylent-telemetry-spec/devbench-launch-commands.txt`, `docs/cli-reference.md`, `docs/architecture.md`

#### E229-F1: Promote `log_file` to a yaml field

**E229-F1-S1: Implement and test**

| ID | Title | Status |
|----|-------|--------|
| E229-F1-S1-T1 | Add `log_file: str | None = None` to `RuntimeConfig` and the JSON schema; introduce mirrored `_resolve_log_file()` in `log_setup.py` (lazy-imports `RUNTIME_CONFIG` to avoid the circular import) so writer + reader share one chain; add `log_file:` to all three live backlog yamls; strip every `JUDGE_LOG_FILE` from `devbench-launch-commands.txt` and rewrite the comment block to document the YAML-as-single-source-of-truth rule; 5 new resolver-precedence tests; update `test_uses_default_path_when_env_not_set` to also pop `JUDGE_WORKSPACE_ROOT`; full sweep `ruff check` + `ruff format --check` + `mypy` + `pytest` clean | done |

---

## Technical debt

Issues carried over from prior review, mapped to current file paths. Listed by severity within each group; each entry uses a definition-list format to keep file paths readable without column overflow.

### Code quality

**HIGH — Dynamic attribute setting on dataclass loses type safety**
File: `src/devbench/github/security.py:136`
`setattr()` on a dataclass instance bypasses the type checker; refactor to direct field assignment.

**MEDIUM — Raw API exception messages may leak details into judge feedback**
File: `src/devbench/github/security.py` (security fetch error handling)
Wrap exception messages before they reach judge feedback to avoid disclosing internal API details.

**MEDIUM — CLI args not validated at system boundary**
File: `src/devbench/cli.py`
Add type / format / range validation at the CLI entry point rather than relying on downstream code to fail fast.

**MEDIUM — kwargs concatenated into `gh` CLI args without sanitization**
File: `src/devbench/github/security.py:62-64`
Validate or shell-escape kwargs values before they become subprocess arguments.

**LOW — `SecurityReview` mixes API fetching, parsing, and summarization (SRP violation)**
File: `src/devbench/github/security.py`
Split into a fetch service, a parser, and a summarizer for testability.

### Test quality

**CRITICAL — Hardcoded path in test**
File: `tests/test_backlog/test_parser.py:18-20`
Replace with the pytest `tmp_path` fixture.

**CRITICAL — Hardcoded `/tmp` log path**
File: `tests/conftest.py:10`
Use the pytest `tmp_path` fixture instead of writing to a fixed `/tmp` path.

**HIGH — No `@pytest.mark.unit` / `@pytest.mark.functional` decorators on any test**
File: all test files
Categorize tests so `make test-unit` and `make test-functional` can target subsets.

**HIGH — Fixtures defined in `conftest.py` instead of `tests/fixtures/`**
File: `tests/conftest.py`
Move data fixtures out of `conftest.py` into a dedicated fixtures directory.

**MEDIUM — `importlib.reload(config)` causes test state pollution**
File: `tests/test_config.py:185-210`
Refactor to use a fixture-scoped fresh config rather than mutating module state.

**MEDIUM — Duplicate predicate tests not parameterized**
File: `tests/test_backlog/test_work_unit.py:203-251`
Collapse with `@pytest.mark.parametrize`.

**MEDIUM — `cmd_next` assertions only check one field of JSON output**
File: `tests/test_cli.py:132`
Assert on the full envelope shape, not just one field.
