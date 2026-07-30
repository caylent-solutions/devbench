# Backlog Contract

This document defines the required format for all backlog files. `devbench validate-backlog` enforces this contract at startup and aborts if any violation is found.

## Table of contents

- [File Hierarchy](#file-hierarchy)
- [Config Validation](#config-validation)
- [ID Format](#id-format)
- [Status Values](#status-values)
- [BACKLOG.md Index](#backlogmd-index)
- [Work Unit File Structure](#work-unit-file-structure)
- [Required Sections -- Task Files](#required-sections--task-files)
- [Comments Section Format](#comments-section-format)
- [Auto-rollup behavior](#auto-rollup-behavior)
- [Dependency Format](#dependency-format)
- [Branch Name Resolution](#branch-name-resolution)
- [Validation Checks](#validation-checks)

---

## File Hierarchy

Work units are organized as Epics > Features > Stories > Tasks. Each level is a separate `.md` file
in a directory named after its ID:

```text
backlog/
├── E1-name/
│   ├── E1.md                        ← Epic spec
│   └── E1-F1-name/
│       ├── E1-F1.md                 ← Feature spec
│       └── E1-F1-S1-name/
│           ├── E1-F1-S1.md          ← Story spec
│           └── E1-F1-S1-T1-name.md  ← Task spec (leaf node -- what agents implement)
└── config/
    └── devbench.yaml                ← workspace configuration (not a work unit)
```

Only task files (`*-T[n].md`) are implemented by agents. Epic, feature, and story files track
rollup status only.

### Workspace layout (what `DEVBENCH_WORKSPACE_ROOT` points at)

`DEVBENCH_WORKSPACE_ROOT` is the **parent directory** that contains `backlog/`, `BACKLOG.md`, and the target repos as siblings. The loader (`src/devbench/config.py`) resolves:

- `<DEVBENCH_WORKSPACE_ROOT>/BACKLOG.md` -- the master index (mandatory at this exact path).
- `<DEVBENCH_WORKSPACE_ROOT>/backlog/config/devbench.yaml` -- the per-workspace config (mandatory).
- `<DEVBENCH_WORKSPACE_ROOT>/backlog/<epic>/<feature>/<story>/*.md` -- work-unit specs.
- `<DEVBENCH_WORKSPACE_ROOT>/<repo-name>/` -- each target repo, as a sibling of `backlog/`.

So `DEVBENCH_WORKSPACE_ROOT` is **not** the backlog repo itself; it is the *parent* directory you place the backlog inside. Pointing it at the backlog repo (so `BACKLOG.md` ends up at `<backlog-repo>/BACKLOG.md` instead of `<workspace>/BACKLOG.md`) produces a chain of `FileNotFoundError` and orphan-detection failures that all trace back to this misalignment.

The recommended layout (used by every backlog in `caylent-telemetry-spec/`):

```
<DEVBENCH_WORKSPACE_ROOT>/
├── BACKLOG.md                       ← master index
├── backlog/
│   ├── config/devbench.yaml         ← per-workspace config
│   ├── E1/E1-F1/E1-F1-S1/E1-F1-S1-T1.md
│   └── ...
├── my-repo/                         ← target repo (sibling of backlog/)
└── another-repo/                    ← another target repo
```

`devbench.yaml` references each repo by its sibling directory name:

```yaml
repos:
  org/my-repo:
    default_branch: main
    checkout_directory: my-repo      # relative to DEVBENCH_WORKSPACE_ROOT
```

The loader populates `RepoConfig.resolved_checkout_path` (E213) at config-load time so every consumer reads `<workspace>/<checkout_directory>` from the dataclass field instead of re-resolving the path inline.

#### Keeping the backlog in its own git repo

The backlog directory (`backlog/` + `BACKLOG.md`) is typically committed to its own git repo so backlog progress (status changes, TDD logs, judge comments) lands separately from target-repo history. Init the backlog repo at `<DEVBENCH_WORKSPACE_ROOT>/.git` and add the target-repo sibling directories to `<DEVBENCH_WORKSPACE_ROOT>/.gitignore` so they don't pollute the backlog history.

#### Symlinks (optional, for repos outside the workspace)

The choice between a real directory and a symlink at `<workspace>/<repo-name>` is purely a filesystem operator decision -- there is no YAML field that toggles symlink-awareness. devbench opens whatever exists at the path `checkout_directory` names; the kernel resolves any symlinks transparently, and every devbench engine path treats the two layouts identically.

When a target repo cannot live as a workspace sibling (a shared workspace under `/workspaces/<workspace>/` with target repos cloned elsewhere on disk), symlink it into place:

```bash
ln -s /real/path/to/my-repo $DEVBENCH_WORKSPACE_ROOT/my-repo
```

The symlink goes at the sibling path (`<workspace>/my-repo`), NOT inside `backlog/` (`<workspace>/backlog/my-repo`). The loader walks the workspace from `<workspace>/<checkout_directory>`; a symlink at that path is transparent. Putting the symlink under `backlog/` makes `_check_orphans` flag it as an orphaned work-unit file.

Symlinked checkouts are first-class supported across every devbench engine path, including the inline orphan-cleanup chore commit, the manifest-scope assertion, the `cleanup-tracked-orphans` CLI, and `git-ops`'s commit / push / merge sequence. Each helper resolves the path symmetrically with its peers (every helper either passes paths through unmodified or canonicalises them via `Path.resolve()` -- never one of each), so a symlinked layout produces the same on-disk outcome as a non-symlinked layout. If you ever see a `ValueError` mentioning `relative_to` or a "path-mismatch" audit comment, that is a devbench bug: file an issue and include the audit-comment text + the symlink mapping.

---

## Config Validation

Validation of `backlog/config/devbench.yaml` happens at config load time (before the orchestrator starts), separate from work-unit validation. Notable rules:

- `checkout_directory` must be **relative** to `DEVBENCH_WORKSPACE_ROOT`. Absolute paths and `..` traversal are rejected -- the loader raises `ValueError` immediately.
- `git_ops.defer_pr: true` requires `git_ops.single_branch` to be set. Misconfigured combinations raise `ValueError`.
- `git_ops.local_only: true` requires `git_ops.defer_pr: true` (a local-only repo has no remote to push to, so PR creation is meaningless).
- `git_ops.local_only: true` is incompatible with `git_ops.pause_before_merge: true` (there is no PR to pause before merging).
- `git_ops.local_only: true` requires every entry in `repos:` to set an explicit `default_branch:` (no `origin/HEAD` fallback exists when the repo has no remote).
- The full YAML is JSON-Schema validated (`additionalProperties: false`), so typos in keys produce a clear schema error rather than being silently ignored.

For the full annotated YAML and value-resolution precedence (env var → YAML → constant default), see the [Configuration model](architecture.md#8-configuration-model) section of the architecture doc.

---

## ID Format

| Level | Pattern | Example |
|-------|---------|---------|
| Epic | `E{n}` | `E1`, `E42` |
| Feature | `E{n}-F{n}` | `E1-F1` |
| Story | `E{n}-F{n}-S{n}` | `E1-F1-S1` |
| Task | `E{n}-F{n}-S{n}-T{n}` | `E1-F1-S1-T1` |

IDs are case-insensitive in status matching but written in uppercase by convention.

---

## Status Values

| Status | Meaning | Written as | Terminal? |
|--------|---------|-----------|-----------|
| Draft | Pre-queue; not yet refined / approved for autonomous claim | `draft` | no |
| In Queue | Ready to be picked up | `in-queue` | no |
| In Progress | Agent is implementing | `in-progress` | no |
| In Review | Staged, awaiting judge review | `in-review` | no |
| Done | Merged and closed | `done` | yes |
| Blocked | Max retries exhausted or dependency blocked | `blocked` | no |
| Proposed | Auto-emitted draft awaiting human promote / reject | `proposed` | no |
| Declined | Will never be done; final operator decision | `declined` | yes |
| Hold | Deferred / under debate; orchestrator skips it until `unhold` | `hold` | no |

> **Note:** `draft` is the agile-standard term for items not yet refined / approved for autonomous claim. A work unit in `draft` is visible in the backlog but is never picked up by the orchestrator. Use `devbench promote <id>` to transition a `draft` unit to `in-queue` when it is ready for autonomous execution.

### Work unit lifecycle

The canonical happy-path lifecycle is:

```
draft -> in-queue -> in-progress -> in-review -> done
```

Side branches (all non-terminal unless noted):

- `in-queue` / `in-progress` / `in-review` --> `blocked` (max retries exhausted or dep blocked; non-terminal)
- `in-queue` / `in-progress` --> `hold` (operator-deferred; non-terminal)
- `in-queue` / `in-progress` / `blocked` --> `declined` (operator decision; terminal)

A status is *terminal* when a parent's auto-rollup treats it as complete. `done` and `declined` are terminal; `hold` is **not** -- a held child keeps its parent open. This guarantees that pausing a unit cannot accidentally close out its parent.

Every blocked work unit is routed into one of six classes by the classifier in `src/devbench/backlog/proposal.py`; see [block-types.md](block-types.md) for the operator-facing reference.

The orchestrator's `next` query and parallel-candidate scan filter to `in-queue` and `in-progress` only, so `hold`, `declined`, and `blocked` units are skipped automatically. Operators move units in and out of `hold` with `devbench hold <id> --reason <text>` and `devbench unhold <id> --reason <text>` (both reasons are required and captured in the work-unit's Comments audit trail).

### Validate-backlog rule list (E209)

Every backlog must pass `devbench validate-backlog`. The full rule set is enforced by `BacklogManager.validate()`:

1. Index row → file existence
2. Index status mirrors work-unit Status
3. No orphan work-unit files
4. Every dep ID is a known work-unit ID
5. Status Summary table counts match the index
6. Tasks have non-empty `## Description`
7. Tasks have at least one `AC-` row in `## Acceptance Criteria`
8. Tasks have at least one row in `## Changes Manifest`
9. Tasks have a `## Definition of Done` section
10. No em-dash characters (U+2014) anywhere in work-unit content
11. Manifest paths do not start with a `checkout_directory` prefix
12. Manifest path conflicts (no two in-queue Tasks claim the same file)
13. Language-AC alignment (non-Python tasks must mark Python ACs N/A)
14. Source-test atomicity (every prod source has a paired test in the same Manifest)
15. Required sections (`## Status:`, `## Dependencies`, `## Changes Manifest`) on every Task
16. Status enum (every parsed `## Status:` value is in `VALID_STATUSES`)
17. Dependency-ID format (every `## Dependencies` row's first cell matches `E[A-Z0-9]+(-F\d+)?(-S\d+)?(-T\d+)?`)
18. Branch uniqueness (no two Tasks derive the same branch name; skipped under single-PR mode)
19. No placeholder Manifest rows (no active Task -- `in-queue` / `in-progress` / `blocked` -- carries a `TBD` row in its Changes Manifest; terminal statuses are skipped)
20. No orphan path tokens in AC / DoD (gated by `validate.check_orphan_path_tokens` -- default on; set `false` to opt out per workspace)
21. Task-Type taxonomy (a Task that declares a `## Task Type:` value must use one of the six recognized types, and its Changes Manifest must satisfy that type's per-type invariant; a Task with no `## Task Type:` section defaults to `behavior-fix`, the strictest type, so omitting the section is never an escape hatch; terminal Tasks -- `done` / `declined` -- are skipped entirely; see "Task-Type Taxonomy Rule (FR-4.1)" below)

Rules 15-17 were added by E209 to harden the contract; rule 18 was added by E219 to prevent silent branch collisions; rule 19 was added by issue #117 to stop the `changes_manifest` reviewer from passing work units whose authors never replaced the canonical placeholder row. Rule 20 was added after a teardown backlog burned an executor cycle on a spec where AC / DoD prose restated a path that disagreed with the Changes Manifest; it is on by default (set `validate.check_orphan_path_tokens: false` to opt a workspace out). Rule 21 was added by FR-4.1 (E4-F2-S1-T1) to stop RED-gated Tasks from shipping a Manifest with zero production-source rows and to keep `test-only` / `docs` / `chore` Tasks honest about the files they claim to touch. Together they catch hand-edited drift that the runtime parser would later silently survive.

#### No Placeholder Rows Rule (issue #117)

The canonical Changes Manifest placeholder reads `TBD | Executor agent: replace this row with the actual files to be created or modified.`. Authors are expected to overwrite the row with one entry per file the Task will touch. The rule fires when an active Task (`in-queue` / `in-progress` / `blocked`) still carries a row whose first cell starts with `TBD` (case-insensitive). The error message names the Task ID and the offending cell text.

`devbench claim <id>` enforces the same rule at claim time as a fail-fast guard: if the Manifest still has a `TBD` row, the claim refuses and either the manifest-amender (when `manifest_amendment.enabled: true`) or the operator must replace the placeholder before the executor can run.

#### No Orphan Path Tokens Rule (rule 20, opt-in)

Acceptance Criteria and Definition of Done items describe **behaviour**, not artifacts. The Changes Manifest is devbench's single source of truth for the file set a Task produces; restating those paths in AC / DoD prose is duplication that drifts. When the prose disagrees with the Manifest, two reviewers can both honestly read the same diff and reach opposite verdicts.

To prevent this class of drift, AC and DoD lines should reference the Manifest symbolically rather than naming paths. Examples:

- "All entries in the Changes Manifest are created with the required content."
- "Manifest files committed on the work-unit branch."
- "Per-task evidence file from the Changes Manifest is created with task ID, timestamp, AWS response, and operator."

When a path **must** appear in AC or DoD prose -- typically because the Task reads an external configuration file or contract that is not part of the diff -- mark it as a read-only reference by suffixing the inline backtick token with `(ref)`:

```markdown
- [ ] AC-FUNC-001: behaviour matches the schema in `src/legacy/auth-contract.yaml` (ref).
```

The validator strips `(ref)`-marked tokens from the orphan-path scan. A token without `(ref)` that matches no Manifest entry (after path normalisation) is reported as an integrity error.

The rule is gated by `validate.check_orphan_path_tokens` in `backlog/config/devbench.yaml`. Set the toggle to `true` to opt in:

```yaml
validate:
  check_orphan_path_tokens: true
```

Path normalisation strips the configured `checkout_directory` prefix, leading `./`, and trailing `/` before comparing AC / DoD tokens to Manifest entries (the same shape rule 11 enforces on Manifest paths). Path-shape detection requires either a recognised file extension OR a directory prefix that is either built-in (`src/`, `tests/`, `infra/`, `docs/`, `backlog/`, `config/`) or observed in the same Task's Manifest. URLs (`http://...`, `s3://...`), shell flags (`--cov=src`), key=value forms, and glob patterns (`*.py`) are exempt by construction.

#### Branch Uniqueness Rule (E219)

Each Task pushes to a branch derived either from an explicit `- **Branch:** \`<name>\`` line in its work-unit file or from the canonical `backlog/<unit-id-lowercase>` template. Two Tasks resolving to the same branch would collide on push, breaking auto-merge and producing false review failures. `validate-backlog` reports the collision with both Task IDs so authors can rename one.

The rule is skipped entirely when `git_ops.single_branch` is set in `devbench.yaml` -- under single-PR mode every task legitimately shares the configured branch.

#### Task-Type Taxonomy Rule (FR-4.1, rule 21)

Every Task carries an optional `## Task Type:` section naming one of six
recognized types (canonical vocabulary lives in `src/devbench/constants.py`
as `VALID_TASK_TYPES`; no call site hard-codes the type strings):

| Type | RED-gated | Changes Manifest invariant |
|------|-----------|-----------------------------|
| `behavior-fix` | Yes | At least one production-source row. |
| `feature` | Yes | At least one production-source row. |
| `test-only` | No | Every row must be a test path. |
| `refactor` | No | No per-row invariant -- its requirement is green-green (tests pass before AND after the change), which is a TDD-cycle-log concern, not a static Manifest shape. |
| `docs` | No | Every row must be a documentation/markdown (`.md`) path. |
| `chore` | No | Every row must be a dependency/config/lockfile path. |

If the `## Task Type:` section is omitted entirely, the Task defaults
to `behavior-fix` -- the strictest type -- and the `behavior-fix`
invariant (at least one production-source Manifest row) is enforced
exactly as if the section had been declared explicitly. This fail-safe
default is deliberate: a task type is not an escape hatch, so a Task
cannot dodge the RED gate simply by omitting the section. The
scaffolding template (`backlog/templates/task.md`) writes the literal
value `behavior-fix` into every newly created Task, so the default
path and the explicit declaration converge on the same value for
freshly authored Tasks.

Terminal Tasks (`done` or `declined`) are skipped by rule 21
entirely, regardless of whether they declare a `## Task Type:`
section: a Task that has already reached a terminal status cannot be
retroactively rejected for a taxonomy violation, matching the
precedent set by rule 19's own terminal-status skip.

**Interaction with sentinel Manifests.** Rule 21 is NOT one of the
rules sentinel values are exempt from (see "Sentinel Manifest Values"
below). `_is_real_manifest_path` strips sentinel tokens before this
rule's classification runs, so a sentinel-only Manifest always
presents zero rows to the gated-type check. A sentinel-only Task
therefore MUST declare a non-gated `## Task Type:` (`test-only`,
`refactor`, `docs`, or `chore`); leaving the section absent, or
declaring `behavior-fix` / `feature`, fails the zero-production-source
invariant.

An unrecognized `## Task Type:` value fails validation naming the full
allowed set:

```
EX-F1-S1-T1: unrecognized '## Task Type:' value 'bugfix'. Allowed values:
behavior-fix, chore, docs, feature, refactor, test-only. See
docs/backlog-contract.md 'Task-Type Taxonomy'.
```

A per-row invariant violation names the offending row, the declared type,
and the violated invariant:

```
EX-F1-S1-T1: task type 'docs' allows only documentation/markdown rows in
the Changes Manifest, but 'src/devbench/cli.py' is not a
documentation/markdown path -- task-type invariant violated. See
docs/backlog-contract.md 'Task-Type Taxonomy'.
```

```
EX-F1-S1-T1: task type 'feature' requires at least one production-source
row in the Changes Manifest, but none was found -- task-type invariant
violated. See docs/backlog-contract.md 'Task-Type Taxonomy'.
```

**Classification reuse (AC-47).** "Is this path production Python
source" and "is this path a Python test" are decided in exactly one
place -- `BacklogManager._is_test_source_path` (shared by
`_is_production_source`, Rule 14's source-test atomicity check, and this
rule's `behavior-fix` / `feature` / `test-only` invariants). The `docs`
and `chore` invariants introduce two new, narrower extension-based
classifiers (`_is_documentation_path` for `.md`; `_is_chore_path` for
lockfile / config extensions) that do not overlap the production/test
boundary Rule 14 already owns, so the production/test classification
itself is never duplicated.

### Dependency satisfaction (E215)

A Task's dependency is satisfied when the dep is in a terminal state (`done` or `declined`). Dependencies on non-task units (Epics, Features, Stories) are evaluated by walking every descendant TASK whose ID begins with `<dep_id>-`: every descendant must be terminal for the dep to count as satisfied. An Epic / Feature / Story with no Task descendants is vacuously satisfied. Unknown dep IDs (typos, drift) are also vacuously satisfied so the orchestrator's actionability scan does not deadlock; `validate-backlog` reports unknown deps as integrity errors so the typo cannot hide.

`devbench sync-blocked` is the operator-facing tool for reconciling status against this rule -- run it after manual edits or to triage a drifted backlog. The orchestrator's `next` query enforces the same rule automatically.

Status is stored in the **work unit file** (the `## Status:` line). `BACKLOG.md` is a derived index that mirrors the work-unit files; the work-unit file is the source of truth. `validate-backlog` reports mirror drift between the two as an error so it can be reconciled -- it does not auto-correct.

---

## BACKLOG.md Index

`BACKLOG.md` is the master index. It must contain a Status Summary table and one row per work unit.

### Canonical Status Summary format (per-epic)

The canonical Status Summary format is **per-epic** -- one row per top-level epic, with columns for each status. This is the format `BacklogManager._update_status_summary()` writes:

```markdown
## Status Summary

| Epic | Title | Done | In Progress | In Queue | Blocked | Declined |
|------|-------|------|-------------|----------|---------|----------|
| E1   | Backlog tooling | 8 | 1 | 2 | 0 | 0 |
| E2   | Migration scripts | 4 | 0 | 5 | 1 | 0 |
```

`in-review` is a transient state during the review tier and is not surfaced in the summary; units in review are not counted in the summary table (`src/devbench/backlog/manager.py`, `_compute_epic_counts`).

### Index rows

Below the Status Summary, one row per work unit:

```markdown
## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
| E1-F1-S1-T1 | Add greeting utility | Task | done | None | org/my-repo | `backlog/E1-name/E1-F1-name/E1-F1-S1-name/E1-F1-S1-T1-name.md` |
```

> **Canonical 7-column format -- required exactly.** The header row MUST read
> `| ID | Title | Type | Status | Dependencies | Repo | File Path |`
> in that exact order and spelling. Any deviation (renamed columns, reordered
> columns, extra columns, missing columns, or a separator row with the wrong
> cell count) is rejected by `validate-backlog` as a Rule-0 error and causes
> `devbench report` to exit non-zero with a parse-error diagnostic.

The `File Path` column must be a path relative to `DEVBENCH_WORKSPACE_ROOT`. `validate-backlog` verifies each file exists at that path.

---

## Work Unit File Structure

All sections below are required unless noted as optional.

### Task file (leaf -- what agents implement)

```markdown
# {ID}: {Title}

## Status: {status}

## Task Type: {type}         ← optional; one of behavior-fix / feature / test-only / refactor / docs / chore; absent defaults to behavior-fix under rule 21 (the scaffolding template also writes behavior-fix by default)

## Target Repository

- **Repo:** `{org}/{repo-name}`
- **Branch:** `{branch-name}`             ← optional; derived as backlog/{id-lowercase} if absent

## Description

{Narrative description of what this task does and why.}

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| E1-F1-S1-T1 | Some prerequisite | done |

## Acceptance Criteria

- [ ] AC-1: {Specific, testable criterion}
- [ ] AC-2: {Specific, testable criterion}
- [ ] AC-DOC-1: {Documentation criterion}

## Changes Manifest

| File | Change |
|------|--------|
| `src/foo/bar.py` | New -- description |
| `tests/test_bar.py` | New -- unit tests |
| `README.md` | Updated -- architecture section |

#### Manifest Glob Rejection (issue #221 B4)

The validator rejects any Changes Manifest entry that contains ``*`` or
``**`` (glob patterns). Manifest paths must be concrete file paths so
that ``manifest_conflict`` detection, source-test atomicity, and the
``changes_manifest`` judge all operate on real, comparable values.

Tasks whose actual file list is determined at execution time (e.g.,
"fix every error-message drift between source and fixture") have two
alternatives:

1. **Use a sentinel value** (see "Sentinel Manifest Values" below).
   For example, ``<source-drift-fix-targets-determined-at-execution>``
   declares the Manifest intentionally non-concrete; the orchestrator's
   ``manifest_amendment`` workflow concretises it at runtime.
2. **List the canonical candidate files**. Enumerate the files most
   likely to need modification. Mark each ``Update (conditional on
   T1/T2 outcome)``. The executor stages only the files actually
   modified.

Glob rejection emits an error like:

```
EX-F1-S1-T1: Manifest entry 'src/**/*.py' contains a glob pattern.
Manifest paths must be concrete; for execution-determined file lists,
use a sentinel value (e.g.,
`<source-drift-fix-targets-determined-at-execution>`) and amend the
Manifest at runtime via `manifest_amendment`.
```

#### Status Summary count semantics (issue #221 B6 clarification)

The Status Summary table has one row per Epic and one column per status
value. Each cell counts the number of work units **of any type**
(Epic, Feature, Story, Task) in that Epic that currently hold that
status. The "In Queue" column is therefore not a leaf-task count -- it
is the count of all in-queue work units within the Epic's subtree
(including the Epic itself when its status is in-queue, plus every
in-queue Feature, Story, and Task).

Worked example: an Epic E1 with status in-queue containing 4 in-queue
Features, 4 in-queue Stories, and 11 in-queue Tasks contributes 20 to
the "In Queue" column (1 + 4 + 4 + 11). When the Epic transitions to
in-progress, that 20 decomposes: 1 to "In Progress", 19 to "In Queue".

Authors building BACKLOG.md by hand often miscount by listing only
leaf Tasks. The validator emits "Status Summary mismatch for E\<N\>:
expected in-queue=\<actual\>, got \<author-value\>" to flag the gap.

#### Sentinel Manifest Values (issue #221 B3)

For tasks that produce no concrete file changes -- verification gates,
decision-only tasks, audit-flip tasks -- the Changes Manifest may use
one of the accepted sentinel values in place of a real path. Sentinels
are exempt from the Manifest Conflict Rule, the source-test atomicity
rule, and the orphan-path-token rule.

**Sentinels are NOT exempt from the Task-Type Taxonomy Rule (rule
21).** `_is_real_manifest_path` strips every sentinel token before
rule 21's per-type invariant runs, so a sentinel-only Manifest always
resolves to zero real rows. A gated type (`behavior-fix`, the default
when `## Task Type:` is omitted, or `feature`) requires at least one
production-source row, and zero rows never satisfies that -- a
sentinel-only Task with no `## Task Type:` section, or an explicit
`behavior-fix` / `feature` declaration, is therefore rejected by rule
21 even though the Manifest Conflict Rule, source-test atomicity, and
the orphan-path-token rule all wave it through. A sentinel-only Task
MUST declare a non-gated `## Task Type:` value -- `test-only`,
`refactor`, `docs`, or `chore` -- so the aggregate production-source
check never fires. See "Task-Type Taxonomy Rule (FR-4.1, rule 21)"
above.

Accepted sentinel values (canonical list in
`src/devbench/backlog/sentinels.py`):

| Sentinel | Semantics |
|----------|-----------|
| `<verification-only>` | The task runs a verification step (test, lint, scan) and records evidence in `## Comments`. No source files are modified. |
| `<decision-only>` | The task makes a decision and records it in `## Comments`. No source files are modified. Typically paired with a follow-up task that executes the decision. |
| `<no changes>` | The task is a placeholder or audit-flip with no executor work. Rare. |
| `<no-op>` | The task collapses to a no-op based on prior-task outcomes. Conditional cleanup tasks use this. |
| `<source-drift-fix-targets-determined-at-execution>` | The task's concrete file list is enumerated at execution time via `manifest_amendment`. Acceptable when the surface depends on diagnostics that haven't run yet. |

Additionally, **any** token shaped as ``<name>`` (single ``<``,
no whitespace, single ``>``) is treated as a sentinel by the
``SENTINEL_PATTERN`` regex in ``sentinels.py``. This lets operators
introduce per-task variants like ``<verification-only:E15-F5-S1-T2>``
without round-tripping through the validator. Use the explicit
allowlist when possible; the pattern is a fallback for ad-hoc cases.

When a task uses a sentinel Manifest, the orchestrator's
``manifest_amendment`` workflow can replace it with concrete paths
mid-execution if real changes turn out to be required.

## Definition of Done

- [ ] All ACs checked
- [ ] `make validate` passes in target repo
- [ ] Files staged with `git add`

## TDD Cycle Log

<!-- Populated by devbench log-tdd during implementation -->

## Comments

<!-- Populated by devbench log-verdict and devbench log-comment during review -->
```

### Epic / Feature / Story files

Higher-level files require only:

```markdown
# {ID}: {Title}

## Status: {status}

## Description

{Summary of scope.}

## Dependencies

| ID | Title | Status |
|----|-------|--------|
```

Status rolls up automatically when all children reach `done`.

---

## Required Sections -- Task Files

| Section | Required | Populated by |
|---------|----------|-------------|
| `## Status:` | Yes | `devbench set-status` / `devbench mark-done` |
| `## Task Type:` | No (defaults to `behavior-fix` under rule 21 when absent; template also writes `behavior-fix`) | Author at creation |
| `## Target Repository` | Yes | Author at creation |
| `## Description` | Yes | Author at creation |
| `## Dependencies` | Yes (empty table OK) | Author at creation |
| `## Acceptance Criteria` | Yes | Author at creation |
| `## Changes Manifest` | Yes | Author at creation |
| `## Definition of Done` | Yes | Author at creation |
| `## TDD Cycle Log` | Yes (may be empty) | `devbench log-tdd` during implementation |
| `## Comments` | Yes (may be empty) | `devbench log-verdict` / `devbench log-comment` |

---

## Comments Section Format

The `## Comments` section is append-only. Each entry is one line:

```
[{ISO-8601 UTC timestamp}] [{agent}] [{event}] {message}
```

Events written by the CLI:

| Event | Written by | Meaning |
|-------|-----------|---------|
| `[REVIEW_PASS]` | `devbench log-verdict` | Judge passed this round |
| `[REVIEW_FAIL]` | `devbench log-verdict` | Judge failed; feedback follows |
| `[REVIEW_REJECTED]` | `devbench log-verdict` | Done-gate reset after security failure |
| `[SECURITY_FAIL]` | `devbench log-verdict` | Security review failed |
| `[comment]` | `devbench log-comment` | Free-form agent observation |

The done-gate (`devbench mark-done`) requires that the four review judges (`code_review`, `test_review`, `doc_review`, `changes_manifest`) each have a `[REVIEW_PASS]` entry after the most recent `[REVIEW_REJECTED]` line (or after the start of the Comments section if no rejection exists).

### Example entries

A real Comments section looks like this:

```
## Comments

[2026-04-15T14:23:11Z] [agent/executor] [comment] Implemented greeting utility per AC-1, AC-2.
[2026-04-15T14:25:33Z] [judge/code_review] [REVIEW_PASS] SOLID, DRY, fail-fast all satisfied.
[2026-04-15T14:25:42Z] [judge/test_review] [REVIEW_PASS] Real tests cover both branches.
[2026-04-15T14:25:48Z] [judge/doc_review] [REVIEW_PASS] README updated alongside code change.
[2026-04-15T14:26:01Z] [judge/changes_manifest] [REVIEW_PASS] Manifest matches staged files.
[2026-04-15T14:27:14Z] [judge/security_review] [REVIEW_PASS] No vulnerabilities found.
[2026-04-15T14:30:00Z] [agent/orchestrator] [comment] Auto-rolled to done -- all children completed.
```

---

## Auto-rollup behavior

When `devbench mark-done <task-id>` succeeds, `BacklogManager._rollup_parent_status()` walks up the parent chain:

1. If all sibling tasks of the parent story are now done, the parent story is marked done.
2. If marking that story done causes all sibling stories of the parent feature to be done, the parent feature is marked done.
3. Likewise for feature → epic.

Each auto-rollup writes an audit comment to the parent's Comments section so the trail is visible:

```
[2026-04-15T14:30:00Z] [agent/orchestrator] [comment] Auto-rolled to done -- all children completed.
```

Rollup happens synchronously inside `mark-done`. There is no background process and no race condition.

### Auto-tick of AC / DoD checkboxes on done

Whenever a work unit transitions to `done` (whether via `mark-done`, `force-status done`, or auto-rollup), `BacklogManager._tick_completion_checkboxes()` rewrites every checkbox line inside the `## Acceptance Criteria` and `## Definition of Done` sections of the work-unit file:

- `- [ ] <content>` becomes `- [x] <content> ✅`
- `- [x] <content>` (ticked but without the green-check emoji) becomes `- [x] <content> ✅`
- Lines already ending with `✅` are left unchanged (idempotent).

Lines outside the two target sections are never modified. The canonical N/A suffix (e.g. `-- N/A for Markdown Tasks (no Python source authored)`) is preserved verbatim; the green-check appends after the suffix. The file is written back only when at least one line changed, so a second call on an already-ticked file is a true no-op (mtime unchanged).

The green-check character is U+2705 (✅). U+2014 (em-dash) is never written; validate-backlog rule 10 rejects em-dashes in work-unit files and the helper is explicitly tested to produce zero em-dash bytes.

---

## Dependency Format

Dependencies reference other work unit IDs. IDs must match entries in `BACKLOG.md`. A task is
actionable only when all listed dependencies have status `done`.

```markdown
## Dependencies

| ID | Title | Status |
|----|-------|--------|
| E1-F1-S1-T1 | Some prerequisite | done |
| E2-F1-S1-T1 | Another prerequisite | done |
```

The Status column in the dependency table is informational -- the parser reads authoritative status
from each work unit file, not from this table.

---

## Branch Name Resolution

Branch name is resolved once, at parse time, from the work unit file:

1. If `## Target Repository` has a `Branch:` field, use it exactly.
2. Otherwise, derive it: `backlog/{id-lowercase}` (e.g., `E1-F1-S1-T1` → `backlog/e1-f1-s1-t1`).

The resolved name is stored in `WorkUnit.branch` and used for all git operations. It is not
re-derived at runtime.

---

## Validation Checks

`devbench validate-backlog` enforces:

**Structural integrity (all work unit types):**

1. Every file path in `BACKLOG.md` index exists on disk
2. Every work unit file's status matches the index (mismatch = error)
3. No orphaned work unit files in `backlog/` or subdirectories (recursive scan)
4. Every dependency ID in every work unit references a real ID in `BACKLOG.md`
5. Status Summary counts in `BACKLOG.md` match actual status distribution

**Content quality (task files only -- IDs containing `-T{n}`):**

6. `## Description` section exists and is non-empty
7. `## Acceptance Criteria` section exists with at least one `AC-` prefixed item
8. `## Changes Manifest` section exists with at least one entry
9. `## Definition of Done` section exists
10. No em-dash character (U+2014) anywhere in the work unit file
11. No Changes Manifest path begins with a `<checkout_directory>/` prefix (see below)

Non-task files (Epics, Features, Stories) are exempt from content quality checks.

**Changes Manifest path-prefix rule (check 11):** manifest paths must be
**repo-relative**. If `backlog/config/devbench.yaml` sets `checkout_directory: <dir>`
for a work unit's target repo, entries in that unit's `## Changes Manifest` MUST NOT
begin with `<dir>/`. The git-ops safety rail
(`src/devbench/backlog/manifest.py::assert_staged_matches_manifest`) compares manifest
entries against `git diff --name-only` output, which is always repo-relative; a
`checkout_directory` prefix on a manifest path produces a guaranteed miss at commit
time, blocking the commit after the executor has done the work. Example: for a repo
with `checkout_directory: example-repo`, the manifest row must read
```
| `README.md` | update |
```
not
```
| `example-repo/README.md` | update |
```
`validate-backlog` surfaces this at authoring / startup time so it never reaches git-ops.

**Issue #159 -- proposal-write enforcement.** Rule 11 has historically been enforced at the validator (this section) and at the `guard-work-unit-write.sh` PreToolUse hook. The third tier -- `cmd_write_proposal` -- now strips matching `<checkout_directory>/` prefixes from every `proposed_tasks[*].files_to_own` entry before persisting the JSON. The strip runs after the issue #146 backlog-repo filter so target-repo classification still fires on the prefixed form. Paths that match multiple configured `checkout_directories` are rejected with a structured error rather than silently picking one interpretation. blocker-resolver and any other agent that calls `devbench write-proposal` no longer needs to hand-strip prefixes; the strip is the safety net that makes rule 11 enforcement cover all three write tiers.

Run before starting the orchestrator. The orchestrate skill runs it automatically at startup and
aborts if any error is found.

---

## Manifest Conflict Rule (post-Backlog-A addendum)

No two in-queue Tasks MAY list the same file path in their `## Changes Manifest` tables. Each file path in the workspace MUST have a single owning Task. If two Tasks legitimately need to modify the same file at different points in time, express the order via `## Dependencies` so they execute sequentially against the same path; the LATER Task's Manifest declares the file even if the EARLIER Task created it (the later Task's edit IS the change git records when its branch is staged).

### Why

When two in-queue Tasks both claim the same file, the orchestrator's `next` command can claim them in either order. The first Task creates / modifies the file; the second Task tries to do the same and either (a) collides with the first Task's commit, or (b) writes a conflicting version that triggers a code-review failure. In production at `caylent-telemetry-spec/`, two file-ownership conflicts were observed:

- `.github/actions/monorepo-check/action.yaml` -- claimed by both `E0-F2-S1-T1` (skeleton) and `E5-F1-S1-T2` (full implementation).
- `.github/workflows/on-pr.yaml` -- claimed by both `E0-F2-S1-T2` (stub) and `E5-F2-S1-T1` (full).

The fix in both cases was to add a `## Dependencies` entry: the full-implementation Task waits on the skeleton Task. The full-implementation Task's Manifest still lists the file (because the full-impl IS its change to git), but the structural ordering prevents collision.

### Validation

`devbench validate-backlog` SHOULD reject any backlog state where two in-queue Tasks list the same file path with no explicit dependency between them. (This rule is part of the post-Backlog-A Tier 3 tooling proposal; until it lands, authors are responsible for self-checking via grep across `## Changes Manifest` blocks.)

For N claimants of the same path, the validator accepts **any DAG that totally orders the set via transitive reachability** (issue #145). A clean N-1 edge chain (`A <- B <- C <- D <- E`) is sufficient -- the validator no longer requires the full `N*(N-1)/2` direct pairwise edges. When the rule fires, the error message prints a suggested chain in lexical-sort order as an operator hint; operators may pick any other ordering that resolves their natural execution order.

The companion rule for cross-cutting infrastructure (e.g., `pyproject.toml` is owned by one Task that authors all build/lint/test config edits in one coordinated commit) is documented in [`source-test-atomicity.md`](source-test-atomicity.md).

---

## Orphan-Pattern Rule (git-ops self-defense)

`git-ops` refuses any commit whose staged or already-tracked paths match a build/state ignore pattern (terraform state, `.terragrunt-cache/`, terraform provider binaries, Python `__pycache__/` and `*.pyc`, `.coverage*`, `node_modules/`, `.DS_Store`). The active pattern list is the union of [`git_orphans._DEFAULT_ORPHAN_PATTERNS`](../src/devbench/git_orphans.py) and any `DEVBENCH_ORPHAN_IGNORE_PATTERNS` env-var override (comma-separated fnmatch globs replacing the default).

### Why

Build / state artefacts have no place in version control. Terraform state files in particular contain real AWS account IDs, role ARNs, and resource attributes that trip security review on every subsequent diff. Provider binaries (~600 MB each) bloat the repo and slow every clone. Python pycache and coverage data leak host-specific paths and Python-version-specific bytecode, breaking dev/prod parity.

In production at `caylent-telemetry/`, two work-unit commits (`E1-F1-S1-T5`, `E1-F1-S1-T6`) accidentally staged 13 such files (totalling ~656 MB after the `terraform-provider-aws` binary). Every later Task's security-review then failed against HEAD, forming a cascade that could not self-resolve.

### Behaviour (default: inline cleanup commit -- Phase 1 of the orphan-cascade fix)

When the rule fires, `git-ops` runs the cleanup **inline** as a devbench-authored chore commit:

1. Captures the executor's pre-cleanup staged paths via `git diff --cached --name-only`.
2. Resets the index to HEAD (`git reset HEAD --`) so the cleanup commit is purely cleanup-only.
3. Runs `cleanup_tracked_orphans(repo_path)` -- `git rm --cached` for each tracked orphan plus `.gitignore` extension under the canonical `# devbench-managed: tracked-orphan cleanup defaults` header.
4. Stages `.gitignore` and commits with the canonical message `chore(cleanup): untrack devbench-managed orphan paths and update .gitignore`.
5. Re-stages the executor's filtered paths (orphans excluded) so the downstream `assert_staged_matches_manifest` runs against the executor's intent without orphan pollution.
6. Continues with the original task's commit. Two commits land on the task's branch.

The cleanup is forward-only (`git rm --cached` + `.gitignore`), preserving every file on disk and NOT rewriting git history. Critically, the cleanup is **not a backlog work unit**: there is no executor invocation, no judge review, no manifest amendment, no proposal materialisation. This collapses the cascade pathology where multiple parents each emitted duplicate cleanup proposals and those proposals themselves got blocked by the manifest amender on predecessor staging.

### Legacy proposal mode (opt-out via `DEVBENCH_DISABLE_INLINE_ORPHAN_CLEANUP=1`)

For backlogs that require a backlog work unit per cleanup (audit / compliance reporting), operators set `DEVBENCH_DISABLE_INLINE_ORPHAN_CLEANUP=1`. In that mode, `git-ops` falls back to the legacy proposal flow:

1. Refuses the commit with a non-zero exit and a stderr message naming a sample of the offending paths.
2. Cross-task de-duplication: scans `<workspace>/.devbench/proposals/*.json` for any pending proposal whose `proposed_tasks[].files_to_own` contains `.gitignore`. If found, wires the current source task as a `BLOCKED_PENDING_PROPOSAL` dependent of the **existing** cleanup task instead of allocating a duplicate.
3. Otherwise, allocates a new cleanup-task ID, materialises the proposal, auto-wires the parent + any peer claimants of `.gitignore` via `add-dep`.

Operators may also run `cleanup-tracked-orphans` directly to drain a polluted state in one shot before launching the orchestrator. See [cli-reference.md](cli-reference.md#cleanup-tracked-orphans) for the operator-facing surface.

### Override

For backlogs that legitimately need to track one of the default-blocked shapes (e.g., a fixture repo that ships a sample `.tfstate`), set `DEVBENCH_ORPHAN_IGNORE_PATTERNS` to the narrowed list before invoking the orchestrator. The override REPLACES the default list entirely; include every pattern you still want to enforce.
