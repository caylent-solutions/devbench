# ADR-17: Pre-Aggregated Per-Task Window Indices

**Status:** Accepted (with v1.0 cleanup note below)
**Date:** 2026-05-04

> **Note (2026-05-13, issue #167).** This ADR references the unified
> `devbench upgrade` command (ADR-22) as the Phase 2 invocation surface.
> That command was removed in the v1.0 cleanup. The window-stats architecture
> described here is unchanged; operators rebuild aggregates manually via
> `devbench rebuild-window-stats` when needed. See ADR-22 for the cleanup
> rationale.

---

## Context

Issue #162 Phase 2. The Phase 1+4 cache (ADR-16, ADR-19) makes the
report fast on warm calls -- the SQLite indexed event store serves
windowed queries with low latency, and the parser only reads appended
log bytes. The remaining cost is the per-window aggregation step that
walks every task's transition pair to compute durations / token
totals / cost increments.

This ADR shifts that cost to write time. Every task-state transition
appends a structural entry to a per-task aggregate JSON at
`<workspace>/.devbench/window-stats/<task-id>.json`. The reporter
reads aggregates directly -- O(task_count) -- instead of re-iterating
event pairs from the indexed event store.

## Decision

Single hook point at `BacklogManager._set_status`
(`src/devbench/backlog/manager.py:623`). Every public transition
method in the manager (`mark_done`, `mark_blocked`, `force_status`,
auto-rollup paths) routes through `_set_status`, so one hook covers
all entry points. After the existing transition log line, the manager
calls `update_aggregate(workspace_root, task_id, canonical_status,
datetime.now(UTC))`.

Tasks only. Stories / Features / Epics share the `E<...>` ID prefix
but their state is auto-rolled from children; window-stats tracks
only Task-level units (IDs containing `-T`).

Atomic write. `update_aggregate` reads the current aggregate (if
any), appends the new transition, writes to `<task-id>.json.tmp` in
the same directory, then `os.replace`s. POSIX same-filesystem rename
is atomic; a crash mid-write leaves either the prior aggregate intact
or the new aggregate fully present -- never a torn JSON document.

Schema versioning. The top-level `schema_version` field lets future
devbench releases evolve the aggregate format without manual cleanup.
A version mismatch on read returns `None`, and the next transition
write overwrites the file with the current schema.

Self-healing rebuild. `rebuild_from_log(workspace_root, log_path)`
walks the orchestrator log and reconstructs every per-task aggregate
from scratch. Used by:

- `devbench rebuild-window-stats` (operator command after upgrade or
  after manually deleting `.devbench/window-stats/`).
- `devbench upgrade` (Phase 2 step in the unified migration).

The aggregate is a derived view; the JSONL log is authoritative.
Aggregate deletion is always safe.

## Alternatives considered

- **Hook every CLI subcommand individually** (`cmd_claim`, `cmd_set_status`,
  `cmd_mark_done`, etc.). N hook points means N opportunities to
  forget one. The single `_set_status` chokepoint guarantees coverage.
- **Compute aggregates on read** (no per-write hook). Same problem
  the layer is solving -- O(log_size) per report invocation.
- **Embed in the SQLite event index.** Conflates two concerns: the
  event index is event-tier; aggregates are task-tier rollups. Keep
  them in separate stores so rebuild semantics stay clean (drop one,
  rebuild from the other).

## Consequences

- Reporter aggregate cost drops from O(log_size) to O(task_count) per
  invocation.
- New disk artefact `<workspace>/.devbench/window-stats/<task-id>.json`
  per task; documented in upgrade-guide.md as a self-healing derived
  view.
- New CLI subcommand `devbench rebuild-window-stats` for operator-
  initiated rebuild. Idempotent.
- New module `devbench.reporting.window_stats` joins the coverage
  gate at 100% line + branch.
- `BacklogManager._set_status` gains one extra call per transition
  (a JSON read + JSON write to a tiny per-task file). Cost is
  negligible vs the existing log write + backlog index update.

## References

- Issue #162 (parent roadmap)
- ADR-16 (incremental cache), ADR-19 (SQLite indexed event store)
- ADR-22 (unified `devbench upgrade` command -- Phase 2 step)
- `src/devbench/reporting/window_stats.py`
- `src/devbench/backlog/manager.py::_set_status`
- `src/devbench/cli.py::cmd_rebuild_window_stats`
- `tests/test_reporting/test_window_stats.py`
