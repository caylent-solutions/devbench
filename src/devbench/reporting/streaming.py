"""Always-on streaming reporter (issue #163).

This module wraps ``generate_report`` in a self-refreshing loop so
``devbench report`` (no flags, on a TTY) never goes blank between
refreshes and renders the next frame as soon as any source file
advances. The loop polls cache-stat tuples (mtime + size of the
orchestrator log + hook log + transcripts directory's newest file) at
a fixed cadence; renders happen ONLY on stat-key change. Idle
workspaces produce no renders and burn no CPU; active workspaces get
sub-frame refreshes.

No-blank-screen guarantee (issue #163 acceptance criterion). The
two implementation rules:

1. ``render_fn`` runs to completion BEFORE any clear escape sequence
   reaches the terminal. The new frame is captured in memory first;
   the terminal is not touched until the frame is fully built.
2. ``_clear_and_write`` issues the clear sequence and the new content
   in a single buffered ``sys.stdout.write`` followed by exactly one
   ``flush``. Two-step "clear then write" patterns are forbidden
   because they leave the terminal blank for the gap between the two
   I/O operations.

Together these guarantee the terminal flips OLD frame -> NEW frame in
one redraw cycle. Tests in ``test_streaming.py`` pin both invariants
so a regression that introduces a blank fails CI.

The render-latency footer below the report exposes the loop's pace to
the operator without instrumenting ``generate_report`` itself.
"""

from __future__ import annotations

import select
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from devbench.config import (
    REPORT_STREAM_MAX_POLL_INTERVAL,
    REPORT_STREAM_RENDER_BUDGET_SECONDS,
    REPORT_STREAM_TAIL_BYTES,
)

_POLL_INTERVAL_SECONDS: float = 0.1

_WARM_HISTORY_SIZE: int = 8


class StreamRenderBudgetExceededError(RuntimeError):
    """A single streaming render exceeded its configured time budget.

    TDI-005: the always-on ``devbench report`` streaming loop re-renders
    on every source-file advance. Under an actively-writing orchestrator
    the source key changes on nearly every tick, so a render whose cost
    has grown past a sane ceiling would otherwise be re-run indefinitely,
    pinning a CPU core (observed: 5.5h R+ at multi-GB RSS). Per CLAUDE.md
    fail-fast, the loop raises this -- which propagates to a non-zero
    exit -- instead of spinning. The message names the budget so the
    operator can raise ``DEVBENCH_REPORT_STREAM_RENDER_BUDGET_SECONDS``
    or investigate the slow render.
    """


def _backoff_interval(*, render_duration: float, base_interval: float, max_interval: float) -> float:
    """Compute the next poll interval given the last render's duration.

    Adaptive backoff (TDI-005): when a render is cheap relative to the
    base cadence the loop polls at ``base_interval``; when a render is
    expensive the next interval grows to at least the render's own
    duration so the loop cannot re-render back-to-back and pin a core on
    a fast-growing log. The result is always clamped to
    ``[base_interval, max_interval]`` so a single pathological render can
    never push the cadence unboundedly and an idle/fast workspace keeps
    the responsive base cadence.

    All three bounds are caller-supplied (env/config-driven at the call
    site); this helper hard-codes no threshold.
    """
    candidate = max(base_interval, render_duration)
    return min(candidate, max_interval)


def _read_log_tail(path: Path, offset: int, *, max_bytes: int) -> tuple[str, int]:
    """Read at most ``max_bytes`` of ``path`` starting at byte ``offset``.

    Incremental, bounded read (TDI-005 requirement ii). Each call seeks
    to ``offset`` and returns only the bytes appended since -- never
    re-reading the whole (growing) file -- and never pulls more than
    ``max_bytes`` into memory in one call. The returned new offset is
    ``offset + len(bytes read)`` so the next tick resumes exactly where
    this one stopped (no gap, no overlap).

    Rotation/truncation safety: if the file is now shorter than
    ``offset`` (log rotated or truncated), the read restarts from 0 so
    the loop re-syncs instead of seeking past EOF and silently reading
    nothing.

    Returns ``("", offset)`` for a missing file so the streaming loop
    treats an absent log the same as an unchanged log.
    """
    try:
        size = path.stat().st_size
    except (FileNotFoundError, OSError):
        return ("", offset)
    start = offset if offset <= size else 0
    with path.open("rb") as handle:
        handle.seek(start)
        raw = handle.read(max_bytes)
    return (raw.decode("utf-8", errors="replace"), start + len(raw))


@dataclass
class _LatencyTracker:
    """Tracks cold / warm / last render durations for the latency footer.

    - ``cold`` is the duration of the first render of the streaming
      session (cache-cold). Captured once and held; subsequent ticks
      do not overwrite it because the value is "what the workspace
      would cost without the cache."
    - ``warm_history`` is a bounded list of the most-recent warm-cache
      render durations. Length is capped at :data:`_WARM_HISTORY_SIZE`.
    - ``last`` is the most-recent render duration (warm or cold);
      shown verbatim in the footer so the operator gets instant
      feedback on the latest tick's cost.
    """

    cold: float | None = None
    warm_history: list[float] = field(default_factory=list)
    last: float | None = None

    def record(self, duration: float, *, cold: bool) -> None:
        """Push a new render duration into the tracker."""
        if cold:
            if self.cold is None:
                self.cold = duration
        else:
            self.warm_history.append(duration)
            del self.warm_history[:-_WARM_HISTORY_SIZE]
        self.last = duration

    @property
    def warm_avg(self) -> float | None:
        """Mean of the bounded warm-history window, or None when empty."""
        if not self.warm_history:
            return None
        return sum(self.warm_history) / len(self.warm_history)

    def footer(self) -> str:
        """Format the single-line render-latency footer."""
        cold = f"{self.cold:.1f}s" if self.cold is not None else "--"
        warm = f"{self.warm_avg:.2f}s" if self.warm_avg is not None else "--"
        last = f"{self.last:.2f}s" if self.last is not None else "--"
        return f"[refresh] cold {cold} / warm {warm} / last refresh {last}"


def _stat_one(path: Path) -> tuple[float, int]:
    """Return ``(mtime, size)`` for ``path`` or ``(0.0, 0)`` if absent.

    Crash-safe by design: a missing file or stat error returns the
    zero tuple, so the streaming loop treats an absent log the same
    as an unchanged log. The next render is suppressed until the
    stat tuple actually transitions.
    """
    try:
        stat = path.stat()
    except (FileNotFoundError, OSError):
        return (0.0, 0)
    return (stat.st_mtime, stat.st_size)


def _stat_sources(paths: Iterable[Path]) -> tuple[tuple[float, int], ...]:
    """Stat every source path and return a deterministic key.

    The streaming loop compares this key tuple between ticks: a
    differing key means "something changed; re-render"; an unchanged
    key means "skip this tick."
    """
    return tuple(_stat_one(p) for p in paths)


def _stdin_keypress_pending() -> bool:
    """Return True iff stdin has bytes ready to read (non-blocking).

    Used to detect Ctrl+C / Ctrl+D / any keypress without blocking
    the streaming loop. Returns False when stdin is not a TTY
    (e.g. piped input) so the loop doesn't exit on non-interactive
    invocations -- the TTY guard in ``cmd_report`` prevents us
    reaching ``stream_report`` in those cases anyway, but defending
    here keeps the helper safe to import elsewhere.
    """
    if not sys.stdin.isatty():
        return False
    try:
        ready, _, _ = select.select([sys.stdin], [], [], 0)
    except (ValueError, OSError):
        return False
    return bool(ready)


def _clear_and_write(text: str) -> None:
    """Replace the terminal's contents with ``text`` in a SINGLE write.

    Issue #163 no-blank-screen contract:

    - Concatenates the clear escape sequence with ``text`` into one
      buffer.
    - Issues exactly one ``sys.stdout.write`` and one ``sys.stdout.flush``.

    The terminal goes from OLD frame to NEW frame in one redraw cycle
    -- never blank between the two. Two-step "clear, then write"
    patterns are forbidden because they create the very blank the
    streaming feature is fixing.

    Uses the VT100 ``\\033c`` full reset which works on every ANSI-
    compliant terminal and stays inside the same ``sys.stdout`` buffer
    (no subprocess fork between clear and content). The OS-binary
    clear path (``clear`` / ``cls``) is intentionally NOT used because
    forking a subprocess between clear and write reintroduces the
    blank-screen race the buffered-write contract is preventing.
    """
    clear_seq = "\033c"
    sys.stdout.write(clear_seq + text)
    sys.stdout.flush()


def stream_report(
    log_path: Path,
    render_fn: Callable[..., str],
    *,
    hook_log_path: Path | None = None,
    transcript_dir: Path | None = None,
    poll_interval: float = _POLL_INTERVAL_SECONDS,
    render_budget_seconds: float | None = None,
    max_poll_interval: float | None = None,
) -> int:
    """Run the always-on streaming report loop.

    ``render_fn`` is the existing ``generate_report`` (or any
    pure-functional wrapper that returns the report text given a
    ``log_path`` keyword arg). The loop polls cache stats, calls
    ``render_fn`` only when something changes, captures the duration,
    appends the latency footer, and writes the result atomically.

    Resource bounds (TDI-005). Two caller-tunable safety bounds prevent
    the loop from pinning a CPU core / growing RSS under an actively-
    writing orchestrator (where the source key changes on nearly every
    tick, so ``render_fn`` would otherwise run at the fixed base
    cadence):

    - ``render_budget_seconds``: if a single render exceeds this budget
      the loop raises :class:`StreamRenderBudgetExceededError` (fail-fast,
      non-zero exit) instead of re-running an ever-growing render
      forever. ``None`` resolves to
      :data:`devbench.config.REPORT_STREAM_RENDER_BUDGET_SECONDS`
      (env > default).
    - ``max_poll_interval``: the adaptive-backoff ceiling. After each
      render the next poll interval grows (via :func:`_backoff_interval`)
      to at least the last render's duration, clamped to this max, so a
      slow-but-under-budget render cannot pin a core at the base cadence.
      ``None`` resolves to
      :data:`devbench.config.REPORT_STREAM_MAX_POLL_INTERVAL`.

    Exits cleanly (return code 0) on:
    - ``KeyboardInterrupt`` (Ctrl+C from the operator).
    - Any keypress on stdin (Ctrl+D / Enter / etc.).

    Per CLAUDE.md fail-fast: any other exception propagates -- the
    loop does NOT silently swallow render failures.
    """
    budget = REPORT_STREAM_RENDER_BUDGET_SECONDS if render_budget_seconds is None else render_budget_seconds
    max_interval = REPORT_STREAM_MAX_POLL_INTERVAL if max_poll_interval is None else max_poll_interval

    tracker = _LatencyTracker()
    secondary_paths: list[Path] = []
    if hook_log_path is not None:
        secondary_paths.append(hook_log_path)
    if transcript_dir is not None:
        secondary_paths.append(transcript_dir)

    last_key: tuple[int, tuple[tuple[float, int], ...]] | None = None
    log_offset = 0
    next_interval = poll_interval
    try:
        while True:
            _, log_offset = _read_log_tail(log_path, log_offset, max_bytes=REPORT_STREAM_TAIL_BYTES)
            current_key = (log_offset, _stat_sources(secondary_paths))
            if current_key != last_key:
                start = time.perf_counter()
                output = render_fn(log_path=log_path)
                duration = time.perf_counter() - start
                if duration > budget:
                    raise StreamRenderBudgetExceededError(
                        f"streaming render took {duration:.1f}s, exceeding the "
                        f"{budget:.1f}s budget; raise "
                        f"DEVBENCH_REPORT_STREAM_RENDER_BUDGET_SECONDS or investigate the slow render"
                    )
                tracker.record(duration, cold=last_key is None)
                frame = output + "\n" + tracker.footer() + "\n"
                _clear_and_write(frame)
                last_key = current_key
                next_interval = _backoff_interval(
                    render_duration=duration,
                    base_interval=poll_interval,
                    max_interval=max_interval,
                )
            if _stdin_keypress_pending():
                break
            time.sleep(next_interval)
    except KeyboardInterrupt:
        sys.stdout.write("\n")
        sys.stdout.flush()
        return 0
    return 0
