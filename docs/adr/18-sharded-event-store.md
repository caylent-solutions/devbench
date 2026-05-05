# ADR-18: Sharded Event Store (Migration-Only, Live Writes Stay Flat)

**Status:** Accepted
**Date:** 2026-05-04

---

## Context

Issue #162 Phase 3. The original roadmap proposed replacing the flat
`logs/orchestrator.log` with a date + task partitioned tree, with both
the runtime writer and the reader routed through the new layout. Two
problems with the full proposal:

1. The runtime writer change is the highest-risk surgery in the
   rollup -- every test asserting log contents would need updates,
   and a bug at the live write path causes silent event loss.
2. After Phase 4 (SQLite indexed event store, ADR-19), windowed
   queries are already O(1) via index range scans. The all-time
   query benefit Phase 3 originally pitched is mostly delivered by
   Phase 4 already.

The pragmatic value Phase 3 still adds is **long-term archival
partitioning** -- operators with multi-month logs benefit from a
date-partitioned on-disk layout that keeps each shard small enough
for grep / sed / inspection without loading 50+ MB at a time.

## Decision

Ship the migration command + reader, **not** the runtime writer.
Operators run `devbench migrate-log-shards --migrate-log-shards` to
partition accumulated history into the sharded tree; the orchestrator
continues writing to a fresh flat `logs/orchestrator.log` after
migration. Readers merge sharded shards (historical) with the flat
log (recent).

Why this is enough. Phase 4 already serves windowed queries via
SQLite. Phase 6 (snapshots, ADR-20) eliminates per-window
aggregation cost. Phase 3 in this scoped form gives operators a
clean on-disk archive without taking the risk of swapping the
runtime FileHandler.

Re-running the migration is supported and idempotent: it appends
new accumulated content into existing shards and re-archives the
source. Operators can adopt a periodic-archive workflow if they
want.

## Migration semantics

Default-deny destructive flag. `cmd_migrate_log_shards` refuses to
run unless `--migrate-log-shards` is passed -- per CLAUDE.md
"execute with care" for any destructive operation. Without the flag
the command prints the warning and exits 0 (so a `devbench upgrade`
chain step can document it without breaking).

Transactional fail-safe. The migration:

1. Walks the source flat log into per-shard buffers in memory.
2. Writes each shard atomically (append to `<shard>.jsonl`).
3. Atomically moves the source log to `logs/legacy/orchestrator.log`.

A failure before step 3 leaves the source log intact; the partial
shard tree can be removed and the migration re-run. After step 3 the
source log is in the archive and is reversible by renaming back.

Reversibility. To roll back: `mv logs/legacy/orchestrator.log
logs/orchestrator.log && rm -rf logs/<YYYY-MM>/`. The legacy archive
is the source of truth for rollback; ADR-22 (`devbench upgrade`)
documents this in the operator workflow.

Continuation-line preservation. Multi-line log records (a timestamped
first line followed by indented continuation lines) attach to the
last-opened bucket so the byte sequence in the shards is byte-faithful
to the source. Logs starting mid-record (no leading timestamp) prepend
the leading lines to the first real bucket.

## Alternatives considered

- **Full Phase 3 (runtime writer + reader).** Higher risk; marginal
  benefit over Phase 4 + Phase 6 + this scoped Phase 3. Deferred.
- **Skip Phase 3 entirely.** Loses the on-disk archive benefit for
  operators with very long logs. The migration tool is small enough
  to ship.
- **Make migration automatic on `devbench upgrade`.** Default-deny
  destructive flag matches CLAUDE.md execute-with-care; auto-running
  destructive ops without confirmation is the wrong default.

## Consequences

- New CLI command `devbench migrate-log-shards --migrate-log-shards`
  partitions the historical log and archives the original.
- New module `devbench.reporting.sharded_log` joins the coverage gate
  at 100% line + branch.
- New disk artefacts: `logs/<YYYY-MM>/<task>.jsonl`,
  `logs/<YYYY-MM>/orchestrator-meta.jsonl`,
  `logs/legacy/orchestrator.log`.
- Operators who never run the migration are unaffected.
- The runtime `setup_logging` is **not** modified -- live writes go
  to the flat log unchanged.

## Post-shipping addendum (2026-05-04)

The initial ship state of this ADR was migration-only -- the reader
path was not extended to consume the sharded tree. Live verification
on `caylent-telemetry-spec` surfaced the gap: running
`devbench upgrade --migrate-log-shards` correctly archived the source
log and produced a byte-identical sharded tree, but `event_index`
only read from `logs/orchestrator.log` and found it absent post-
migration, so `devbench report` lost access to historical data
until rollback.

Issue [#168](https://github.com/caylent-solutions/devbench/issues/168)
closed the reader-path integration:

- `event_index.refresh_orch_log_sources(workspace_root, live_log_path)`
  refreshes the live flat log + every shard.
- New union-aware query helpers (`task_transition_times_for_workspace`,
  `all_log_timestamps_for_workspace`,
  `non_noise_log_timestamps_for_workspace`) fold transitions across
  the workspace's full orch-log set.
- Anti-double-count rule: a file_id is only included in the union
  when its underlying file currently exists on disk, so the pre-
  migration `logs/orchestrator.log` row in `source_files` (a stale
  artefact after migration archives it) doesn't double-count events
  that now also live in shards.
- Snapshot freshness key (ADR-20) bumped to schema v2; covers the
  live log AND the shard-mtime aggregate so any shard mutation
  invalidates the snapshot.
- `devbench upgrade` self-test extended to run an indexed refresh
  and assert non-zero events; would have caught the live regression
  immediately.

Live re-verification (2026-05-04 19:50 UTC): re-ran
`devbench upgrade --migrate-log-shards` on `caylent-telemetry-spec`
with the integration in place. Self-test reported 18,556 events
indexed (matching the original line count). Post-migration
`devbench report --once` rendered the same row counts as the pre-
migration golden (Tasks completed 40/150, recent pace 32.6 min,
identical session boundaries). Migration is safe for operators.

## References

- Issue #162 (parent roadmap)
- Issue #168 (reader-path integration; gates safe Phase 3 use)
- ADR-19 (SQLite indexed event store) -- the layer that makes
  windowed queries fast and reduces Phase 3's value to "archival
  partitioning"
- ADR-20 (materialised snapshots) -- the layer that makes
  per-invocation report cost milliseconds regardless of shard count
- ADR-22 (unified `devbench upgrade` command -- references the
  default-deny migration step)
- `src/devbench/reporting/sharded_log.py`
- `src/devbench/cli.py::cmd_migrate_log_shards`
- `tests/test_reporting/test_sharded_log.py`
