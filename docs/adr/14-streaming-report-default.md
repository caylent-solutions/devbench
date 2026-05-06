# ADR-14: Always-on Streaming `devbench report` Default

**Status:** Accepted
**Date:** 2026-05-04

---

## Context

Issue #163. Before this ADR, `devbench report` was a one-shot snapshot;
operators who wanted live progress passed `--watch N` and chose an
interval (30s / 60s / 120s) with two failure modes either side:

- Too short (e.g., 5s) wasted work re-rendering against a log that
  hadn't advanced. The render-cost-itself became visible -- the
  screen sat blank for the duration of each render then flashed the
  new frame.
- Too long (e.g., 300s) and the operator squinted at stale numbers,
  unsure whether the orchestrator had stalled or whether the next
  refresh was just imminent.

After issue #162 (Phase 1 + Phase 4 cache) landed, the underlying
refresh cost dropped to single-digit milliseconds on warm calls. The
fixed-interval design itself became the bottleneck.

## Decision

Make `devbench report` an always-on streaming view by default on a
TTY. Operators stop choosing an interval; the report adapts its
cadence to the workspace.

User-visible behaviour:

- `devbench report` (no flags, on a TTY) opens a live, self-refreshing
  view. The screen is populated immediately and stays current. Ctrl+C
  exits cleanly.
- `devbench report --once` (or `--no-stream`) renders one snapshot and
  exits. Same contract as today's one-shot mode for scripts and CI
  consumers that pipe the output. Auto-engaged when stdout is not a
  TTY (pipe / file redirect / CI).
- `devbench report --since <ISO-8601>` stays one-shot (a frozen-window
  snapshot doesn't make sense to stream).
- `devbench report --watch N` keeps working but emits a one-line
  deprecation notice and falls through to the streaming loop. The
  integer interval is ignored; cadence is data-driven.

Under-the-hood model:

- A single long-lived process polls (mtime, size) tuples for the
  orchestrator log + hook log + transcript directory at ~100 ms
  cadence using stdlib `select` / `Path.stat`.
- Re-render fires ONLY when any stat tuple changes. Idle workspaces
  produce no work; active workspaces get sub-frame refreshes.
- Render is the same `generate_report` code path as one-shot mode.
- The render-latency footer at the bottom (`[refresh] cold X.Xs /
  warm Y.YYs / last refresh Z.ZZs`) gives the operator real-time
  feedback on the loop's pace without instrumenting `generate_report`
  itself.

### No-blank-screen invariant (the headline guarantee)

Two implementation rules pinned by tests:

1. `render_fn` runs to completion BEFORE any clear escape sequence
   reaches the terminal. The new frame is captured to an in-memory
   string first; the terminal is not touched until the new frame is
   fully built.
2. `_clear_and_write` issues the clear sequence and the new content
   in a single buffered `sys.stdout.write` followed by exactly one
   `flush`. Two-step "clear, then write" patterns are forbidden
   because they leave the terminal blank for the gap between the
   two I/O operations.

Together these guarantee the terminal flips OLD frame -> NEW frame in
one redraw cycle. Tests in `tests/test_reporting/test_streaming.py`
pin both invariants; a regression that introduces a blank between
frames fails CI.

## Alternatives considered

- **Keep `--watch N` as the only live mode.** Operators continued to
  guess at the right cadence; the screen-blank problem remained.
- **Multi-pane terminal UI.** Out of scope per the issue's "stays
  one-screen-at-a-time" non-goal; complexity disproportionate to the
  benefit.
- **Sub-second polling.** Bounded at ~100 ms for this design; faster
  polling burns CPU on workspace stat churn without being humanly
  perceptible.
- **Replace `devbench watch`.** Different tool, different audience.
  `devbench report` is the rolled-up dashboard; `devbench watch` is
  the per-event hook stream.

## Consequences

- The streaming default is the new everyday experience.
- New module `devbench.reporting.streaming` holding
  `_LatencyTracker`, `_stat_sources`, `_stdin_keypress_pending`,
  `_clear_and_write`, `stream_report`. Stdlib only -- no new
  mandatory dependencies.
- New CLI flags `--once` / `--no-stream`; legacy `--watch N` deprecated
  (still works, emits warning, falls through to streaming).
- 16 new unit tests pin the behaviour: cold/warm/last latency
  semantics, capped warm history, footer formatting, single-buffered
  write, change-detection, `KeyboardInterrupt` clean exit.
- One existing legacy test (`test_cmd_report_watch_invokes_clear_command_each_tick`)
  was replaced because it asserted the OLD subprocess-clear pattern
  the streaming wrapper no longer uses.

## References

- Issue #163 (parent)
- `src/devbench/reporting/streaming.py`
- `src/devbench/cli.py::cmd_report`
- `tests/test_reporting/test_streaming.py`
- ADR-16 (incremental cache), ADR-19 (SQLite indexed event store) -- the
  foundation that makes streaming affordable.
