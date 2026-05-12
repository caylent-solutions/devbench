# Upgrade Guide

If you're pulling this update on a workspace that already has an
in-flight orchestrator log, follow this guide to migrate cleanly.
Operators starting on a fresh workspace can skip this document --
every new artefact self-builds on first read.

## TL;DR

```bash
git pull
make install
devbench upgrade
```

Run `devbench upgrade` from a shell whose `JUDGE_WORKSPACE_ROOT` and
`JUDGE_CLAUDE_MODEL` env vars point at the workspace you want to
upgrade. The command is **idempotent** -- safe to re-run at any cadence.
By default it runs the safe migrations and skips the one destructive
operation (the sharded-log migration). Pass `--migrate-log-shards` to
opt into the destructive step.

## What each phase migrates

| Phase | Storage location | Behaviour on first read | Operator action |
|---|---|---|---|
| **1+4 Cache + SQLite index** (ADR-16, ADR-19) | `<workspace>/.devbench/report-cache/events.sqlite` | Cache file absent on first read; rebuilt automatically from `logs/orchestrator.log` (one-time cold pass, then warm). | None. Self-healing. |
| **2 Window indices** (ADR-17) | `<workspace>/.devbench/window-stats/<task-id>.json` | Aggregates absent until next state transition; reporter falls back to live aggregation when missing. | None (auto on next transition); or `devbench upgrade` runs `rebuild-window-stats` once on accumulated history. |
| **3 Sharded event store** (ADR-18) | `<workspace>/logs/<YYYY-MM>/<task-id>.jsonl` and `<workspace>/logs/<YYYY-MM>/orchestrator-meta.jsonl` | Sharded tree absent; the orchestrator continues writing to flat `logs/orchestrator.log`. Readers merge sharded shards (when present) with the flat log. | **Optional + DESTRUCTIVE**. Run `devbench upgrade --migrate-log-shards` to partition accumulated history; original archived to `logs/legacy/orchestrator.log`. |
| **6 Materialised snapshots** (ADR-20) | `<workspace>/.devbench/report-snapshot.json` | Snapshot absent on first read; reporter falls back to live aggregation through the Phase 1+4 cache. | None. Auto-written by orchestrate skill at the end of every iteration after this update lands. |
| **7 Parquet cold archive (opt-in)** (ADR-21) | `<workspace>/logs/legacy/<session-id>.parquet` | No archives present; archive on demand via `devbench archive-session <session-id>`. | None unless you want long-term cold storage. Install the `archive` extra (`uv sync --extra archive`), then archive ended sessions per-session. |

## Phase 3 destructive migration walkthrough

Phase 3 is the only destructive operation. It is **opt-in**, gated
behind the `--migrate-log-shards` flag, **reversible**, and now safe
for everyone (issue [#168](https://github.com/caylent-solutions/devbench/issues/168)
closed the reader-path integration; live-verified parity on the
`caylent-telemetry-spec` workspace).

### When to run it

You probably don't need to. Phase 4 (SQLite indexed event store, ADR-19)
already serves windowed queries with low latency; Phase 3 is value-add
only when you have a multi-month log and want a date-partitioned
on-disk archive for grep / inspection / disk-usage tools.

If you decide to run it:

```bash
devbench upgrade --migrate-log-shards
```

### What it does

1. Walks `logs/orchestrator.log` line by line.
2. Routes each line into the right shard:
   - State-transition lines (`Set <id> to '<status>'`) where `<id>`
     contains `-T` go to `logs/<YYYY-MM>/<task-id>.jsonl`.
   - All other timestamped lines (sweep events, banners, hook
     activity) go to `logs/<YYYY-MM>/orchestrator-meta.jsonl`.
   - Continuation lines (multi-line records) attach to the last-
     opened shard so the byte sequence is preserved.
3. Atomically moves the source `logs/orchestrator.log` to
   `logs/legacy/orchestrator.log`.

### How to verify

```bash
ls logs/                     # expect a YYYY-MM/ directory + legacy/
ls logs/legacy/              # expect orchestrator.log
devbench validate-backlog    # expect exit 0
devbench report --once       # expect the same row counts as before
```

### How to roll back

The migration is reversible. To restore the pre-migration state:

```bash
mv logs/legacy/orchestrator.log logs/orchestrator.log
rm -rf logs/<YYYY-MM>/
```

The orchestrator's next iteration writes to the flat log unchanged.
The sharded-tree directories and the legacy archive are workspace-
local; nothing references them outside this workspace.

## Self-tests

`devbench upgrade` runs a parser-load self-test at the end. The
output looks like:

```
Self-tests:
  ✓ devbench validate-backlog (parser load) passes
```

If the self-test reports `! parser-load failed: ...`, the migrations
themselves succeeded but your `BACKLOG.md` has an issue independent
of the upgrade. Run `devbench validate-backlog` for the structured
report and fix the finding.

## Troubleshooting

**"Cache deletion is always safe."** If you delete
`<workspace>/.devbench/report-cache/events.sqlite` (e.g. to force
a clean rebuild), the next `devbench report` rebuilds it from the
JSONL log. No data loss.

**"Snapshot deletion is always safe."** If you delete
`<workspace>/.devbench/report-snapshot.json`, the next
`devbench report` falls back to live aggregation through the Phase
1+4 cache. The next orchestrate iteration writes a fresh snapshot.

**"Window-stats deletion is always safe."** If you delete
`<workspace>/.devbench/window-stats/`, run
`devbench rebuild-window-stats` to regenerate every aggregate from
the orchestrator log.

**"`pyarrow` not found"**: install the archive extra:
```bash
uv sync --extra archive
```
The mainline install does NOT carry pyarrow; only
operators who archive ended sessions install it.

**"`logs/legacy/orchestrator.log` is taking up disk"**: after one
release cycle, that archive is safe to delete (the sharded tree is
authoritative for migrated content). If you have any concern, wait
two release cycles.

## Per-phase commands (for fine-grained control)

`devbench upgrade` is the canonical entry point. The per-phase
commands let you run individual migrations without the surrounding
chain:

| Command | Phase | Notes |
|---|---|---|
| `devbench rebuild-window-stats` | 2 | Idempotent rebuild of every per-task aggregate |
| `devbench migrate-log-shards --migrate-log-shards` | 3 | Destructive; default-deny on the flag |
| `devbench write-snapshot` | 6 | Idempotent; orchestrate skill calls this automatically |
| `devbench archive-session <id>` | 7 | Per-session archive to Parquet; requires `[archive]` extra |

## References

- ADR-14 (streaming default)
- ADR-15 (orchestrator-tier hook bypass)
- ADR-16 (incremental cache)
- ADR-17 (window indices)
- ADR-18 (sharded log)
- ADR-19 (SQLite indexed event store)
- ADR-20 (materialised snapshots)
- ADR-21 (Parquet cold archive)
- ADR-22 (unified upgrade command)
