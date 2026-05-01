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

### Keeping the backlog in a separate git repo

The backlog should live in its own local git repo, separate from the target repositories DevBench
modifies. This lets you commit backlog progress (status changes, TDD logs, judge comments) without
mixing them into target repo history.

Set `JUDGE_WORKSPACE_ROOT` to the backlog repo. Create symlinks inside it pointing to target
repos, and reference them as relative paths in `devbench.yaml`:

```bash
ln -s /real/path/to/my-repo /path/to/my-backlog/my-repo
```

```yaml
repos:
  org/my-repo:
    default_branch: main
    checkout_directory: my-repo    # relative -- resolves via symlink
```

Symlinks bridge the gap between the backlog repo and target repos outside it.

---

## Config Validation

Validation of `backlog/config/devbench.yaml` happens at config load time (before the orchestrator starts), separate from work-unit validation. Notable rules:

- `checkout_directory` must be **relative** to `JUDGE_WORKSPACE_ROOT`. Absolute paths and `..` traversal are rejected -- the loader raises `ValueError` immediately.
- `git_ops.defer_pr: true` requires `git_ops.single_branch` to be set. Misconfigured combinations raise `ValueError`.
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

| Status | Meaning | Written as |
|--------|---------|-----------|
| In Queue | Ready to be picked up | `in-queue` |
| In Progress | Agent is implementing | `in-progress` |
| In Review | Staged, awaiting judge review | `in-review` |
| Done | Merged and closed | `done` |
| Blocked | Max retries exhausted or dependency blocked | `blocked` |

Status is stored in the **work unit file** (the `## Status:` line). `BACKLOG.md` is a derived index that mirrors the work-unit files; the work-unit file is the source of truth. `validate-backlog` reports mirror drift between the two as an error so it can be reconciled -- it does not auto-correct.

---

## BACKLOG.md Index

`BACKLOG.md` is the master index. It must contain a Status Summary table and one row per work unit.

### Canonical Status Summary format (per-epic)

The canonical Status Summary format is **per-epic** -- one row per top-level epic, with columns for each status. This is the format `BacklogManager._update_status_summary()` writes:

```markdown
## Status Summary

| Epic | Title | Done | In Progress | In Queue | Blocked |
|------|-------|------|-------------|----------|---------|
| E1   | Backlog tooling | 8 | 1 | 2 | 0 |
| E2   | Migration scripts | 4 | 0 | 5 | 1 |
```

`in-review` is a transient state during the review tier and is not surfaced in the summary; units in review are still counted under `In Progress` for summary purposes.

### Index rows

Below the Status Summary, one row per work unit:

```markdown
## Work Units

| ID | Title | Status | File |
|----|-------|--------|------|
| E1-F1-S1-T1 | Add greeting utility | done | backlog/E1-name/E1-F1-name/E1-F1-S1-name/E1-F1-S1-T1-name.md |
```

The `File` column must be a path relative to `JUDGE_WORKSPACE_ROOT`. `validate-backlog` verifies each file exists at that path.

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

The companion rule for cross-cutting infrastructure (e.g., `pyproject.toml` is owned by one Task that authors all build/lint/test config edits in one coordinated commit) is documented in [`source-test-atomicity.md`](source-test-atomicity.md).

---

## Orphan-Pattern Rule (git-ops self-defense)

`git-ops` refuses any commit whose staged or already-tracked paths match a build/state ignore pattern (terraform state, `.terragrunt-cache/`, terraform provider binaries, Python `__pycache__/` and `*.pyc`, `.coverage*`, `node_modules/`, `.DS_Store`). The active pattern list is the union of [`git_orphans._DEFAULT_ORPHAN_PATTERNS`](../src/devbench/git_orphans.py) and any `DEVBENCH_ORPHAN_IGNORE_PATTERNS` env-var override (comma-separated fnmatch globs replacing the default).

### Why

Build / state artefacts have no place in version control. Terraform state files in particular contain real AWS account IDs, role ARNs, and resource attributes that trip security review on every subsequent diff. Provider binaries (~600 MB each) bloat the repo and slow every clone. Python pycache and coverage data leak host-specific paths and Python-version-specific bytecode, breaking dev/prod parity.

In production at `caylent-telemetry/`, two work-unit commits (`E1-F1-S1-T5`, `E1-F1-S1-T6`) accidentally staged 13 such files (totalling ~656 MB after the `terraform-provider-aws` binary). Every later Task's security-review then failed against HEAD, forming a cascade that could not self-resolve.

### Behaviour

When the rule fires, `git-ops` does NOT silently skip the commit -- it:

1. Refuses with a non-zero exit and a stderr message naming a sample of the offending paths.
2. Auto-emits a follow-up cleanup task (a Proposal whose executor runs `devbench cleanup-tracked-orphans <repo>` to untrack each match and append the canonical block to the repo's root `.gitignore`).
3. Wires the source task as a `BLOCKED_PENDING_PROPOSAL` dependent of the cleanup task so the orchestrator's existing cascade picks the cleanup up next iteration. The source task auto-clears once the cleanup commits.

The cleanup is forward-only (`git rm --cached` + `.gitignore`), preserving every file on disk and NOT rewriting git history. Operators may also run `cleanup-tracked-orphans` directly to drain a polluted state in one shot before launching the orchestrator. See [cli-reference.md](cli-reference.md#cleanup-tracked-orphans) for the operator-facing surface.

### Override

For backlogs that legitimately need to track one of the default-blocked shapes (e.g., a fixture repo that ships a sample `.tfstate`), set `DEVBENCH_ORPHAN_IGNORE_PATTERNS` to the narrowed list before invoking the orchestrator. The override REPLACES the default list entirely; include every pattern you still want to enforce.
