# ADR-19: SQLite Indexed Event Store

**Status:** Accepted
**Date:** 2026-05-04

---

## Context

Phase 1 of issue #162 (ADR-16) introduced an mtime+offset cache so the
reporter never re-parses the JSONL/text sources on every invocation. That
solves the "don't redo work you've already done" problem.

What it doesn't solve: windowed queries (`since=...`) still required scanning
every event to filter by timestamp. On a workspace with 100,000 hook entries,
filtering Python-side after a JSON load is itself slow. We need an indexed
range-scan over the event store.

## Decision

The same SQLite database introduced by ADR-16 carries indexed columns for
every dimension the reporter queries by:

- `(file_id, ts_epoch_us)` -- session-boundary detection per source file.
- `(task_id, kind, ts_epoch_us)` -- per-task event lookups.
- `(kind, ts_epoch_us)` -- cross-source windowed queries (cost-window,
  token-window, etc.).

Every JSONL append to the source file results in a corresponding SQLite
insert in the same `refresh_*` call. The `EventIndex.refresh_*` methods are
the only place that mutates the cache; the source files remain authoritative
and the SQLite is a derived view that can be rebuilt at any time.

`PRAGMA user_version` is the schema version. Schema changes that aren't
backwards-compatible bump the version; on open the cache compares the value
to `_SCHEMA_VERSION` and rebuilds from scratch if they differ.

## Alternatives considered

- **JSON-keyed dict in memory.** Same problem as a flat parse: O(N) filter
  on every invocation.
- **External SQL database (Postgres, etc.).** Adds a new runtime dependency;
  the user has explicitly stated single-machine, single-operator, no service
  scope.
- **Custom B-tree index.** Reinvents the wheel. SQLite gives us range-scan +
  index + transactions for free, all stdlib.

## Consequences

- Windowed queries become indexed range scans -- microseconds instead of
  Python full scans.
- After this layer lands the reporter is no longer bottlenecked on data
  fetch regardless of backlog size (per #162's "after Phase 4, reporting
  performance stops being a bottleneck" claim).
- The cache can be deleted at any time and rebuilds from the JSONL source
  on next access; SQLite WAL mode makes hard-kill survivable -- last-
  committed transaction stands; in-flight transactions roll back; reboot
  recovery is automatic.
- New module (`devbench.reporting.event_index`) at 100% line + branch
  coverage. Existing reporting tests retain full parity vs the legacy
  parser path.

## References

- ADR-16 (incremental cache, same module)
- Issue #162 (parent roadmap)
- `src/devbench/reporting/event_index.py`
- `tests/test_reporting/test_event_index.py`
