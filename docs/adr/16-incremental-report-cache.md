# ADR-16: Incremental Report Cache (mtime+offset+SQLite)

**Status:** Accepted
**Date:** 2026-05-04

---

## Context

`devbench report` historically re-parsed the entire `logs/orchestrator.log`,
walked every `backlog/**/*.md` file, and recomputed every metric on every
invocation. On a 50 MB log, mature workspaces saw tens of seconds per render.
Watch-mode multiplied that cost by every refresh tick.

The bottleneck was Python-bound parse + aggregation work, not disk I/O. The
log is append-only by orchestrator contract, so almost every byte we
re-parsed had been parsed already on the prior invocation -- pure waste.

## Decision

Persist parsed event state to a per-workspace SQLite database at
`<workspace>/.devbench/report-cache/events.sqlite`. The cache tracks every
source file (orchestrator log, hook log, every per-session transcript) by
mtime + size + the byte offset of the last parsed line, and serves report
queries from indexed columns instead of from a full re-scan of the JSONL/text
sources. Every `refresh_*` call validates each source against its row in
`source_files`:

- mtime + size unchanged -> cache is a perfect hit; no source-file IO.
- size grew (and mtime advanced, append-only contract) -> read only the bytes
  from `parsed_offset` to current size, parse the new lines, insert them, and
  update the row.
- size shrank or mtime regressed -> source was rotated / truncated /
  hand-edited; invalidate every event for that source and re-parse the whole
  file.

The SQLite database itself uses a `PRAGMA user_version` schema version so
incompatible schema changes force a rebuild on next open. SQLite is stdlib;
no new mandatory runtime dependency.

Every timestamp is stored as `ts_epoch_us INTEGER` (microseconds since the
Unix epoch, UTC). Storing a single normalised type avoids the pitfall of
comparing `YYYY-MM-DDTHH:MM:SSZ` against `YYYY-MM-DDTHH:MM:SS.ffffff+00:00`
lexicographically. Indexed range scans give correct windowed-query semantics.

## Alternatives considered

- **JSON file per source.** Simpler than SQLite but loses the indexed range
  scan that windowed queries need; would still O(events) on every report
  invocation.
- **In-memory daemon (RFC #165).** Faster warm path (~2-5 ms vs ~5-15 ms) but
  pays 500-1000 ms cold-spawn on first call after every reboot or idle-reap;
  net negative for one-shot invocations on a single-operator workload.
  Deferred as RFC.
- **Distributed sharded backend (RFC #166).** Multi-operator + persistent-
  beyond-workstation; not needed for the current operating model. Deferred
  as RFC.

## Consequences

- Warm `devbench report` runs in single-digit milliseconds against a 50 MB
  log workspace -- bounded by render code, not data fetch.
- Cache rebuilds itself from scratch when the JSONL log is the source of
  truth (cache deletion is harmless and self-healing).
- A new module (`devbench.reporting.event_index`) joins the coverage gate at
  100% line + branch.
- The reporter's `_compute_window_stats` and session-boundary detector each
  gain an indexed code path alongside the legacy parser path; parity tests
  pin the two paths byte-for-byte.

## References

- Issue #162 (parent roadmap)
- `src/devbench/reporting/event_index.py`
- `tests/test_reporting/test_event_index.py`
