# Backlog Validation Contract

This document describes the integrity checks performed by `BacklogManager.validate()` and the contract that every backlog file must satisfy.

## Runtime Status Vocabulary

The following five status values are valid in both the BACKLOG.md index and individual work-unit files:

| Status | Meaning |
|--------|---------|
| `in-queue` | Work unit is ready to be picked up |
| `in-progress` | An agent is actively working on this unit |
| `in-review` | Agent has completed work and requested human review |
| `done` | All judges passed, work is merged |
| `blocked` | Unit cannot proceed due to an unresolved blocker |

All five values are enforced by validation. Status mismatches between the index and the work-unit file are reported as errors.

## Validation Checks

`BacklogManager.validate(backlog_index, workspace_root)` runs six integrity checks in order and returns a list of error strings. An empty list means the backlog is valid.

### Check 1: File Existence

Every row in BACKLOG.md must have a corresponding work-unit `.md` file on disk.

**Error format:** `<ID>: work unit file missing — expected <file_path>`

### Check 2: Status Consistency

Every work-unit file's `## Status:` value must match the status column in the BACKLOG.md index row.

**Error format:** `<ID>: status mismatch — index has '<status>', file has '<status>'`

If a work-unit file is missing its `## Status:` line entirely:

**Error format:** `<ID>: work unit file missing '## Status:' line`

### Check 3: Orphan Detection

No `.md` files may exist in the `backlog/` subdirectory without a corresponding row in BACKLOG.md.

**Error format:** `<filename>: orphaned work unit file not in BACKLOG.md`

### Check 4: Dependency References

Every dependency ID listed in the BACKLOG.md index must reference a real work-unit ID present in the index.

**Error format:** `<ID>: dependency '<dep_id>' not found in backlog index`

### Check 5: Status Summary Count Accuracy

When a `## Status Summary` section is present in BACKLOG.md, the declared counts for each status column must match the actual counts in the Full Work Unit Index.

- Only status columns present in the Summary table header are checked.
- Columns not present in the Summary header are ignored.
- `blocked` units count toward the `Blocked` column if that column exists.
- Non-numeric cells (e.g. bold `**Total**` rows) are silently ignored.
- This check is silently skipped when no Status Summary section is found.

**Error format:** `Status Summary count mismatch for '<status>': summary declares <N> but index has <M>`

### Check 6: Required Section Headers

Every work-unit file must contain a `## Comments` section header. This section is required by the backlog contract so that agents have a designated location for log entries.

**Error format:** `<ID>: work unit file missing '## Comments' section header`

## Work-Unit File Contract

Every work-unit `.md` file must contain:

1. A top-level heading with the format `# <ID>: <Title>`
2. A `## Status: <status>` line using one of the five valid status values
3. A `## Comments` section for agent log entries

Example minimal compliant file:

```markdown
# E0-F1-S1-T1: Create Makefile

## Status: in-queue

## Comments
```

## BACKLOG.md Index Contract

Every row in the Full Work Unit Index table must have:

- A valid work-unit ID (e.g. `E0-F1-S1-T1`)
- A status value from the five valid statuses
- A relative file path of the form `` `backlog/<path>.md` ``
- Dependency IDs that reference real IDs in the same index (or `none`/`--`)

When a Status Summary section is present, its per-status counts must match the actual index counts.
