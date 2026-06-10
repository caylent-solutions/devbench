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
- [Verification Contract](#verification-contract)
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
21. Verification command-path contract (TDI-001): a `## Verification` `type=command` path operand must not begin with the unit's `checkout_directory` name, must resolve against the present checkout, and must not feed a recursive `grep` from a `$(find ...)` substitution. WARNING by default, ERROR under `--strict`.
22. Command-vs-deferred classification (TDI-004): a `type=deferred` directive whose `reason` names a runnable project tool (with no live/operator-only signal) is flagged as a mis-classified runnable check. WARNING by default, ERROR under `--strict`.
23. AC referential integrity (TDI-005): a path an AC / `type=command` directive asserts must exist must either be present in the checkout, `add`ed by some task's Changes Manifest, or marked an external carve-out. WARNING by default, ERROR under `--strict`; runs only when the checkout is present.
24. No committable-file sentinels: a Changes Manifest sentinel that stands in for committable files (it is not a recognised no-op / undetermined family or `<family:detail>` variant, and it contains a path separator or a `files`/`template`/`example` keyword) cannot satisfy the git-ops integrity gate and is rejected. WARNING by default, ERROR under `--strict`. See "Committable-file sentinels (forbidden)" below.

Rules 15-17 were added by E209 to harden the contract; rule 18 was added by E219 to prevent silent branch collisions; rule 19 was added by issue #117 to stop the `changes_manifest` reviewer from passing work units whose authors never replaced the canonical placeholder row. Rule 20 was added after a teardown backlog burned an executor cycle on a spec where AC / DoD prose restated a path that disagreed with the Changes Manifest; it is on by default (set `validate.check_orphan_path_tokens: false` to opt a workspace out). Rules 21-23 were added to close the `## Verification` authoring gaps (see "Verification Contract" below): a command path written against the wrong working directory, a runnable check mis-marked `deferred`, and an AC asserting a path that nothing creates -- each previously surfaced only at execution time. Rule 24 was added after a unit (E9-F1-S1-T5) passed every judge and staged its files yet could never commit, because its Manifest used free-form sentinels naming files the git-ops integrity gate could not expand -- a terminal block a restart cannot clear. Together they catch hand-edited drift that the runtime parser would later silently survive.

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

### Dependency satisfaction (E215)

A Task's dependency is satisfied when the dep is in a terminal state (`done` or `declined`). Dependencies on non-task units (Epics, Features, Stories) are evaluated by walking every descendant TASK whose ID begins with `<dep_id>-`: every descendant must be terminal for the dep to count as satisfied. An Epic / Feature / Story with no Task descendants is vacuously satisfied. Unknown dep IDs (typos, drift) are also vacuously satisfied so the orchestrator's actionability scan does not deadlock; `validate-backlog` reports unknown deps as integrity errors so the typo cannot hide.

`devbench sync-blocked` is the operator-facing tool for reconciling status against this rule -- run it after manual edits or to triage a drifted backlog. The orchestrator's `next` query enforces the same rule automatically.

**Dependency-cycle detection (TDI-009).** `validate-backlog`, `next`, and `add-dep` detect cycles over one canonical dependency graph via a single shared routine (`devbench.backlog.dep_cycle.find_cycles`). The canonical graph unions the BACKLOG.md index dependency column, the work-unit `## Dependencies` tables (the source of truth an operator edits directly), and the `[BLOCKED_PENDING_PROPOSAL]` marker edges. This closes a prior gap where a cycle introduced by editing a `## Dependencies` table passed `validate-backlog` (which only walked the index column) while `next` later halted with `NO_ACTIONABLE -- cyclic`. The diagnostic now names the **actual** cycle members (e.g. `dependency cycle detected: T1 -> T2 -> T1`), not an arbitrary detection node; a cycle made purely of marker edges is reported by the dedicated marker-cycle check instead.

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

#### Committable-file sentinels (forbidden)

A sentinel documents a unit that produces **no** committable files (the
no-op families above) or whose file list is genuinely undetermined until
execution (``<source-drift-fix-targets-determined-at-execution>``, amended
at runtime via ``manifest_amendment``). A sentinel MUST NOT stand in for
committable files the author simply did not enumerate -- e.g.
``<providers/aws/primitives/waf-webacl/ example + aux template files,
determined at execution>``.

The reason is structural, not stylistic. The git-ops integrity gate
``assert_staged_matches_manifest`` (``src/devbench/backlog/manifest.py``)
verifies the staged file set against the Manifest by **exact path-set
membership** and never expands sentinels. A unit whose Manifest is a
free-form sentinel naming files it failed to list can therefore pass every
judge and ``verify-ac`` (exit 0), stage the correct files, yet **never
commit** -- the staged files are rejected as out-of-manifest. A restart
cannot clear this; only enumerating the paths can.

``validate-backlog`` enforces this: a sentinel that is neither a recognised
no-op family nor a recognised ``<family:detail>`` variant, and that stands
in for committable files (it contains a path separator, or a
``files``/``template``/``example`` keyword), is a **WARNING** by default and
an **ERROR** under ``--strict`` (which ``spec-to-backlog`` runs). To fix:
enumerate the concrete paths the unit creates/modifies (matching a done
sibling unit's Manifest is the fastest reference); only for a genuinely
unknowable list use ``<source-drift-fix-targets-determined-at-execution>``.

## Definition of Done

- [ ] All ACs checked
- [ ] AC-3 verified -- `make validate` passes in target repo
- [ ] Files staged with `git add`

## Verification

- VERIFY AC-3 | type=command | cmd=`make validate` | expect-exit=0

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
| `## Target Repository` | Yes | Author at creation |
| `## Description` | Yes | Author at creation |
| `## Dependencies` | Yes (empty table OK) | Author at creation |
| `## Acceptance Criteria` | Yes | Author at creation |
| `## Changes Manifest` | Yes | Author at creation |
| `## Definition of Done` | Yes | Author at creation |
| `## Verification` | Conditional -- required whenever an AC asserts a runnable/testable outcome (enforced as an ERROR under `validate-backlog --strict`; a warning otherwise). See [Verification Contract](#verification-contract). | Author at creation; evidence captured by `devbench verify-ac` |
| `## TDD Cycle Log` | Yes (may be empty) | `devbench log-tdd` during implementation |
| `## Comments` | Yes (may be empty) | `devbench log-verdict` / `devbench log-comment` |

---

## Verification Contract

DevBench anchors deterministic completion proof on **Acceptance Criteria**. The optional `## Verification` section maps each *executable* Acceptance Criterion to a command whose **real** exit code is captured by `devbench verify-ac` (never self-reported) and gated at `mark-done`: a work unit cannot be marked `done` until every executable AC has a tool-captured exit-0 evidence record for the current attempt. This is the deterministic mark-done evidence gate: `devbench verify-ac` runs each executable directive and records the captured exit code, and `devbench mark-done` is blocked in code until every executable AC has an exit-0 record (operator-only `deferred` ACs block by default unless `done_gate.allow_deferred_evidence` is enabled). See ADR-27 (the AC evidence gate) for the full design.

### Directive grammar

The `## Verification` section contains one `- VERIFY` directive per AC, in this exact grammar (one per line):

```
- VERIFY AC-N | type=<terratest|apply|plan|destroy|deploy|smoke|command> | tool=<optional> | cmd=`<command>` | expect-exit=0
- VERIFY AC-N | type=deferred | owner=operator | reason="<why a human must run this>"
- VERIFY AC-N | type=judge
```

| Field | Meaning |
|-------|---------|
| `AC-N` | The Acceptance Criterion this directive verifies (one or more `AC-N` ids before the first `\|`). Required. |
| `type` | One of `terratest`, `apply`, `plan`, `destroy`, `deploy`, `smoke`, `command` (executable -- must carry exit-code evidence), `deferred` (operator-only -- blocks `mark-done` by default), or `judge` (qualitative -- left to the core review judges, never gated). Required. |
| `tool` | Optional. Auto-detected from `cmd` when omitted (e.g. `terragrunt`, `cdk`, `aws-cli` via the IaC tool matrix in `src/devbench/verification.py`). |
| `cmd` | Backtick-wrapped command. A literal `\|` inside the command does not break field splitting because `cmd` is parsed first. Required for executable types. |
| `expect-exit` | The exit code that counts as success. Defaults to `0`. |
| `owner` / `reason` | Used by `type=deferred` to record who must run the step and why it cannot be executed in the run. |

A malformed directive (no `AC-N` id, or an unknown `type`) is **always an error** -- it would make the done-gate unparseable.

### The two contract rules

`validate-backlog` checks two findings, routed to **warnings by default** and **errors under `--strict`** (which `spec-to-backlog` runs at authoring time):

1. **Executable-AC coverage.** Any Acceptance Criterion whose text asserts a runnable/testable outcome -- it names a tool or verb from the IaC matrix (`terraform` / `terragrunt` / `tofu` / `terratest` / `tf-test` / `cdktf` / `cdk` / `cloudformation` / `sam`) or `pytest` / `go test` / `make <target>` / `apply` / `deploy` / `provision` / `passes` / `succeeds` / `smoke` -- MUST have a matching `VERIFY AC-N` directive of an executable or `deferred` type. Without it the done-gate cannot require tool-captured proof. A `type=judge` directive does NOT cover an executable AC.
2. **DoD/AC agreement.** Any `## Definition of Done` item that asserts such a runnable outcome MUST reference an existing `AC-N`. No un-AC'd verifiable claim may hide in the DoD -- certainty is anchored on AC. The DoD remains a *process checklist*; move any verifiable claim into an AC (with its `VERIFY` directive) or cite the `AC-N` it satisfies.

Back-compat: because the two findings default to warnings, pre-existing backlogs are not retroactively broken until they are re-validated with `--strict`.

### Worked example

```markdown
## Acceptance Criteria

- [ ] AC-3: a real `terragrunt apply` of the data-lake unit succeeds
- [ ] AC-7: the collector smoke check returns HTTP 200
- [ ] AC-9: the production apply completes
- [ ] AC-11: the module follows SOLID and DRY

## Definition of Done

- [ ] AC-3 verified -- `make tf-test` passes in the target repo
- [ ] Only files in Changes Manifest are staged with `git add`

## Verification

- VERIFY AC-3 | type=terratest | tool=terragrunt | cmd=`make tf-test UNIT=sandbox/000/data-lake/000` | expect-exit=0
- VERIFY AC-7 | type=smoke     |                  | cmd=`make smoke URL=$COLLECTOR_URL`            | expect-exit=0
- VERIFY AC-9 | type=deferred  | owner=operator   | reason="prod apply is operator-only (D30)"
- VERIFY AC-11 | type=judge
```

Here AC-3 and AC-7 are executable (exit-0 proof required), AC-9 is operator-only (blocks `mark-done` unless `done_gate.allow_deferred_evidence` is enabled), and AC-11 is qualitative (the core judges assess it). The DoD item references AC-3 rather than asserting an un-AC'd outcome.

### `verify-ac` working directory and path resolution (TDI-001)

`devbench verify-ac` runs **every `type=command` directive with the target-repo checkout root as the working directory** (it resolves the repo from the configured `repos:` paths and runs each `cmd` there). Therefore every path operand in a `cmd` MUST be **relative to the target-repo checkout root** -- never relative to the workspace root, and never prefixed with the repo's own `checkout_directory` name. A path written against the wrong base cannot resolve, and the AC-evidence gate becomes unsatisfiable regardless of the implementation (`test -f` exits non-zero; a negated `! grep ... $(find <bad-path> ...)` worse still -- `find` yields zero operands, the recursive `grep` scans the whole tree, and `!` inverts an unrelated match into a false failure).

Correct (repo-root-relative):

```
- VERIFY AC-3 | type=command | cmd=`test -d providers/aws/references/data-lake` | expect-exit=0
```

Incorrect (prefixed with the repo's `checkout_directory` name `tools-telemetry`):

```
- VERIFY AC-3 | type=command | cmd=`test -d tools-telemetry/providers/aws/references/data-lake` | expect-exit=0
```

`validate-backlog` flags a `type=command` path operand that begins with the unit's checkout-directory name (WARNING by default, ERROR under `--strict`), and -- when the checkout is present on disk -- a literal operand that does not resolve. Also avoid a `cmd` whose `grep` takes its file operands from a `$(find ...)` substitution: prefer an explicit file list or `grep -r <dir>` so a zero-operand expansion cannot trigger a tree-wide scan.

### Command vs deferred (TDI-004)

An acceptance criterion whose check runs with the **project's standard toolchain** -- the same environment `verify-ac` and the review judges run in -- MUST be `type=command` (or `terratest`/`plan`/etc.), NOT `type=deferred`. `type=deferred` is reserved strictly for checks that genuinely cannot run in the orchestrator environment: live-production mutations, credentials the orchestrator must not hold, or manual human sign-off.

`done_gate.allow_deferred_evidence: false` is the **secure default**: a unit carrying a `type=deferred` executable AC is held pending an operator policy decision. When a unit holds on deferred evidence, the remedy is almost always to **reclassify a mis-labelled `deferred` check as `command`** -- never to relax the policy toggle. `validate-backlog` flags a `type=deferred` directive whose `reason` names a runnable project tool (terraform / terragrunt / tofu / terratest / pytest / make / cdk / sam / ... ) or "at execution time" and carries no live/production/operator-only signal (WARNING by default, ERROR under `--strict`).

```
# Mis-classified -- a check the orchestrator CAN run; must be type=command:
- VERIFY AC-1 | type=deferred | owner=operator | reason="terraform validate requires the toolchain at execution time"

# Legitimately deferred -- cannot run in the orchestrator:
- VERIFY AC-9 | type=deferred | owner=operator | reason="real production terragrunt apply against a live account"
```

### AC Referential Integrity (TDI-005)

When an Acceptance Criterion (or a `type=command` path operand) asserts that a concrete path must **exist or resolve** (a module directory, a file, a referenced primitive), that path must satisfy one of three resolutions:

1. it **exists** in the target-repo checkout, or
2. it is **created by a task** -- it appears as an `add` row in some backlog task's `## Changes Manifest`, or
3. it is an explicit **external carve-out** (a pinned third-party / out-of-repo source; declare it inline with a trailing ` (ref)` or external wording).

A required path that satisfies none of these is unsatisfiable: the unit cannot reach `done` and the executor can only escalate. `validate-backlog` flags such a path (WARNING by default, ERROR under `--strict`) naming the AC, the path, and the three resolutions. The check runs only when the target-repo checkout is present, so absence is asserted with certainty.

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

The done-gate (`devbench mark-done`) requires that the five always-on core judges (`code_review`, `test_review`, `doc_review`, `changes_manifest`, `security_review`) each have a `[REVIEW_PASS]` entry after the most recent `[REVIEW_REJECTED]` line (or after the start of the Comments section if no rejection exists). The core five are mandatory and non-disableable.

In addition, an **optional specialty judge** is added to the required set for a unit when (and only when) the operator has enabled it AND it is applicable to that unit. The optional `iac_review` judge (enabled via `optional_judges.iac_review: true`, default off) is auto-required for any unit whose `## Verification` contract contains an infrastructure item (`unit_requires_iac_judge`) -- deterministically, never authored by hand and never self-judged. When no optional judge is enabled+applicable the required set is exactly the core five and behaviour is unchanged. A `type=deferred` (operator-only) Verification item blocks `mark-done` by default; set `done_gate.allow_deferred_evidence: true` to let deferred ACs pass.

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

### Status scoping

The conflict check is scoped to specific status sets. Not every status triggers the same severity:

**Default in-flight ERROR set** (`in-queue` / `proposed` / `blocked`): Two Tasks in any of these statuses that share the same `(repo, path)` pair with no serial dependency between them are a hard ERROR. `validate-backlog` exits non-zero and names both Task IDs. These statuses represent work that the orchestrator may pick up at any time, so an unresolved overlap is a guaranteed git-ops collision.

**Authoring-time WARNING set** (`draft` / `hold`): Two Tasks in `draft` or `hold` that share the same `(repo, path)` pair with no serial dependency are reported as a WARNING by default. `validate-backlog` prints the warning but still exits 0. These statuses represent work that is not yet actionable, so the conflict is a planning concern rather than an immediate runtime hazard.

**Out-of-scope statuses** (`done` / `declined` / `in-progress`): Tasks in terminal states (`done`, `declined`) have already landed or been cancelled; their Manifest entries no longer compete. Tasks `in-progress` are actively being executed by an agent; the conflict rule does not retroactively fire on them.

### Authoring-time strict check

To promote the `draft`/`hold` WARNING to a non-zero ERROR -- useful in CI or authoring gates where you want to catch planning conflicts before they become runtime conflicts -- run:

```bash
devbench validate-backlog --strict
# or equivalently:
devbench validate-backlog --include-draft
```

Both `--strict` and `--include-draft` are accepted as aliases. In strict mode `validate-backlog` exits 1 when any `draft`/`hold` conflict is found, exactly as it does for the in-flight ERROR set. The in-flight ERROR set is unaffected -- it always exits 1 regardless of the strict flag.

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
