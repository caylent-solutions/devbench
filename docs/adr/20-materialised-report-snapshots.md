# ADR-20: Materialised Report Snapshots

**Status:** Accepted
**Date:** 2026-05-04

---

## Context

Issue #162 Phase 6. After the Phase 1+4 cache (ADR-16 / ADR-19) lands,
`devbench report` already runs quickly on warm calls because the
indexed event store serves windowed queries efficiently and the log
parser only reads appended bytes. The remaining cost is the
per-window aggregation pass + render.

Phase 6 amortises that cost across every `devbench report` invocation
by writing a snapshot of the rendered report to disk after every
orchestrate-loop iteration. The orchestrator pays the render cost
once per iteration (work it's already doing for its own decisions);
external `devbench report` calls then pay only the file read.

## Decision

Cache the **rendered report text** at
`<workspace>/.devbench/report-snapshot.json` and the orchestrator log's
`(mtime_ns, size)` freshness key. `devbench report --once` (and the
non-streaming TTY-not paths) read the snapshot first; on a freshness
match the cached text is printed directly, skipping log parse, event
aggregation, and the render.

Mismatch = cache miss = fall back to live `generate_report` through the
Phase 1+4 cache. The snapshot is a derived view; the JSONL log is
authoritative.

Atomic write. `write_snapshot` writes to `report-snapshot.json.tmp`
in the same directory, then `os.replace`s atomically (POSIX same-
filesystem rename). A crash mid-write either leaves the prior
snapshot intact or makes the new snapshot fully present -- never a
torn JSON document the read path would mis-decode.

Schema versioning. The top-level `schema_version` field lets future
devbench releases evolve the snapshot format without manual cleanup.
Version mismatch on read returns `None` and the caller rebuilds via
the live path; the next iteration's `write_snapshot` overwrites the
stale file.

Streaming-mode interaction. The TTY streaming default (#163, ADR-14)
does NOT consume the snapshot: streaming polls source-file stats
every 100 ms (`src/devbench/reporting/streaming.py:45`) and triggers
its own render on change. Snapshot reads are exclusively for
`--once` / non-TTY callers (CI consumers, scripts, piped output).
Streaming callers benefit from Phase 1+4 cache only.

Why text-cache vs data-cache. Caching the rendered text is the
simplest correct solution and saves the entire aggregate + render
cost. The trade-off is that runtime-determined rendering (terminal
width, color via `NO_COLOR`, timezone via `JUDGE_DISPLAY_TIMEZONE`)
gets baked into the snapshot at write time. For the orchestrate-
skill use case this is acceptable -- the orchestrator runs in a
stable terminal env. A future ADR can promote the cache to a data-
layer snapshot if a runtime-rendering use case emerges.

## Alternatives considered

- **Cache WindowStats list (data-layer snapshot).** More flexible at
  read time; requires `to_dict` / `from_dict` on every nested
  dataclass + a re-render pass that handles renderer-runtime config.
  Substantial code surface; deferred until a real need surfaces.
- **Always recompute from cache.** What we had before this ADR. The
  Phase 1+4 cache makes recompute cheap but not free.
  Snapshot reads are cheaper still and remove the per-window
  aggregation cost entirely.
- **Keep the snapshot in memory only.** Loses the cross-invocation
  benefit -- every `devbench report` call from a fresh process
  pays the full recompute. Disk-persisted is the right shape.

## Consequences

- `devbench report --once` (non-TTY) warm path: file read + print,
  no log parse.
- `<workspace>/.devbench/report-snapshot.json` is a new disk artefact;
  appears in the upgrade-guide table; deletion is always safe.
- Self-healing: a missing / corrupt / stale-schema snapshot returns
  `None` from `read_snapshot` and the caller rebuilds via the live
  path through the Phase 1+4 cache.
- Per-orchestrate-iteration cost: one extra `devbench write-snapshot`
  invocation at the end of step 9. Same compute the orchestrator
  already does + a single JSON write.
- New module (`devbench.reporting.snapshot`) joins the coverage gate
  at 100% line (`make test-coverage-new`, Makefile:89).

## References

- Issue #162 (parent roadmap)
- ADR-16 (incremental cache), ADR-19 (SQLite indexed event store) --
  the layer this snapshot rides on top of
- ADR-14 (streaming default) -- explicit non-consumer of the snapshot
- `src/devbench/reporting/snapshot.py`
- `src/devbench/cli.py::cmd_write_snapshot`
- `src/devbench/cli.py::cmd_report` (snapshot-read integration)
- `tests/test_reporting/test_snapshot.py`
