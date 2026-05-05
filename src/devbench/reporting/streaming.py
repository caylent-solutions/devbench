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

# Polling cadence between cache-stat checks (seconds). 100 ms gives
# human-perceptible immediate updates without burning CPU on workspace
# stat churn. Issue #163 explicitly bounds this at ~100 ms; do not
# tune lower without revisiting the CPU-utilisation budget.
_POLL_INTERVAL_SECONDS: float = 0.1

# Number of warm-tick durations the running-average tracks.
_WARM_HISTORY_SIZE: int = 8


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
            # Capture cold once. Refusing to overwrite means a long-
            # running session keeps the original cold value as a
            # historical anchor; subsequent cache rebuilds don't get
            # mistaken for cold ticks.
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
        # Closed stdin or otherwise-broken descriptor -- treat as
        # "no keypress pending" so the loop sleeps and retries.
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
    # VT100 full reset: clears the screen, scrollback (on most modern
    # terminals), cursor position, attributes. Selected over the
    # narrower ``\033[2J\033[H`` because it also wipes scrollback so
    # the operator's history doesn't accumulate stale frames.
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
) -> int:
    """Run the always-on streaming report loop.

    ``render_fn`` is the existing ``generate_report`` (or any
    pure-functional wrapper that returns the report text given a
    ``log_path`` keyword arg). The loop polls cache stats, calls
    ``render_fn`` only when something changes, captures the duration,
    appends the latency footer, and writes the result atomically.

    Exits cleanly (return code 0) on:
    - ``KeyboardInterrupt`` (Ctrl+C from the operator).
    - Any keypress on stdin (Ctrl+D / Enter / etc.).

    Per CLAUDE.md fail-fast: any other exception propagates -- the
    loop does NOT silently swallow render failures.
    """
    tracker = _LatencyTracker()
    paths: list[Path] = [log_path]
    if hook_log_path is not None:
        paths.append(hook_log_path)
    if transcript_dir is not None:
        # Stat the transcript directory itself; mtime advances when
        # any contained file changes, which is exactly the signal
        # the renderer cares about without having to enumerate
        # children every tick.
        paths.append(transcript_dir)

    last_key: tuple[tuple[float, int], ...] | None = None
    try:
        while True:
            current_key = _stat_sources(paths)
            if current_key != last_key:
                # Capture the new frame BEFORE clearing the screen
                # (no-blank-screen contract).
                start = time.perf_counter()
                output = render_fn(log_path=log_path)
                duration = time.perf_counter() - start
                tracker.record(duration, cold=last_key is None)
                # Clear + write in ONE buffered call.
                frame = output + "\n" + tracker.footer() + "\n"
                _clear_and_write(frame)
                last_key = current_key
            if _stdin_keypress_pending():
                break
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        # Newline so the next shell prompt doesn't land on the
        # latency-footer line.
        sys.stdout.write("\n")
        sys.stdout.flush()
        return 0
    return 0
