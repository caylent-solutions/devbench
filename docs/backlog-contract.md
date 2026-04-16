# Backlog Contract

This document defines the required format for all backlog files. `devbench validate-backlog`
enforces this contract at startup and aborts if any violation is found.

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
│           └── E1-F1-S1-T1-name.md  ← Task spec (leaf node — what agents implement)
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

The `checkout_directory` must be relative (absolute paths and ``..`` traversal are rejected).
Symlinks bridge the gap between the backlog repo and target repos outside it.

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

Status is stored in the **work unit file** (the `## Status:` line). `BACKLOG.md` is a derived index
— the file is authoritative. `validate-backlog` warns on mismatch between the two.

---

## BACKLOG.md Index

`BACKLOG.md` is the master index. It must contain a Status Summary table and one row per work unit:

```markdown
## Status Summary

| Status | Count |
|--------|-------|
| In Queue | 4 |
| In Progress | 1 |
| Done | 12 |
| Blocked | 0 |

## Work Units

| ID | Title | Status | File |
|----|-------|--------|------|
| E1-F1-S1-T1 | Add greeting utility | done | backlog/E1-name/E1-F1-name/E1-F1-S1-name/E1-F1-S1-T1-name.md |
```

The `File` column must be a path relative to `JUDGE_WORKSPACE_ROOT`. `validate-backlog` verifies
each file exists at that path.

---

## Work Unit File Structure

All sections below are required unless noted as optional.

### Task file (leaf — what agents implement)

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
| `src/foo/bar.py` | New — description |
| `tests/test_bar.py` | New — unit tests |
| `README.md` | Updated — architecture section |

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

## Required Sections — Task Files

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

The done-gate (`devbench mark-done`) requires that the four review judges (`code_review`,
`test_review`, `doc_review`, `changes_manifest`) each have a `[REVIEW_PASS]` entry after the most
recent `[REVIEW_REJECTED]` line (or after the start of the Comments section if no rejection exists).

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

The Status column in the dependency table is informational — the parser reads authoritative status
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

Non-task files (Epics, Features, Stories) are exempt from content quality checks.

Run before starting the orchestrator. The orchestrate skill runs it automatically at startup and
aborts if any error is found.
