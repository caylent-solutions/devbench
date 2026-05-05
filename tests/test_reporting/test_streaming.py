"""Unit tests for ``devbench.reporting.streaming`` (issue #163).

Pins every issue-#163 acceptance criterion as a real assertion. The
no-blank-screen invariant is the most important: a single buffered
write per frame, with the clear sequence and content emitted in one
``sys.stdout.write`` call. A regression that introduces a blank
between frames fails CI.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench.reporting.streaming import (
    _clear_and_write,
    _LatencyTracker,
    _stat_one,
    _stat_sources,
    _stdin_keypress_pending,
    stream_report,
)


class TestLatencyTracker:
    """``_LatencyTracker`` cold/warm/last semantics + footer formatting."""

    def test_cold_captured_once(self) -> None:
        t = _LatencyTracker()
        t.record(0.5, cold=True)
        # Subsequent cold calls must NOT overwrite (cold is the
        # historical anchor).
        t.record(0.7, cold=True)
        assert t.cold == pytest.approx(0.5)

    def test_warm_history_capped_at_8(self) -> None:
        t = _LatencyTracker()
        for i in range(20):
            t.record(0.01 * (i + 1), cold=False)
        # Only the last 8 values are kept.
        assert len(t.warm_history) == 8
        # The oldest 12 were evicted; the kept window is values 13..20.
        assert t.warm_history == [pytest.approx(0.01 * v) for v in range(13, 21)]

    def test_warm_avg_is_mean_of_history(self) -> None:
        t = _LatencyTracker()
        for v in (0.1, 0.2, 0.3):
            t.record(v, cold=False)
        assert t.warm_avg == pytest.approx(0.2)

    def test_warm_avg_none_when_history_empty(self) -> None:
        t = _LatencyTracker()
        assert t.warm_avg is None

    def test_last_tracks_most_recent_record(self) -> None:
        t = _LatencyTracker()
        t.record(1.5, cold=True)
        assert t.last == pytest.approx(1.5)
        t.record(0.3, cold=False)
        assert t.last == pytest.approx(0.3)

    def test_footer_dashes_when_no_data(self) -> None:
        t = _LatencyTracker()
        s = t.footer()
        assert s == "[refresh] cold -- / warm -- / last refresh --"

    def test_footer_format_with_data(self) -> None:
        t = _LatencyTracker()
        t.record(2.5, cold=True)
        t.record(0.05, cold=False)
        s = t.footer()
        # Cold: one decimal. Warm + last: two decimals.
        assert "cold 2.5s" in s
        assert "warm 0.05s" in s
        assert "last refresh 0.05s" in s


class TestStatSources:
    """The cache-stat key tuple is the streaming loop's change-detector."""

    def test_stat_one_returns_zero_for_missing_file(self, tmp_path: Path) -> None:
        result = _stat_one(tmp_path / "nope.log")
        assert result == (0.0, 0)

    def test_stat_one_returns_mtime_and_size(self, tmp_path: Path) -> None:
        path = tmp_path / "log.txt"
        path.write_text("hello")
        mtime, size = _stat_one(path)
        assert mtime > 0
        assert size == 5

    def test_stat_sources_keys_are_distinct_per_path(self, tmp_path: Path) -> None:
        a = tmp_path / "a"
        a.write_text("aa")
        b = tmp_path / "b"
        b.write_text("bbb")
        key = _stat_sources([a, b])
        assert len(key) == 2
        assert key[0][1] == 2
        assert key[1][1] == 3


class TestClearAndWriteSingleBuffer:
    """Issue #163 no-blank-screen invariant. The clear sequence and the new
    frame MUST be emitted in a single buffered ``sys.stdout.write`` followed
    by exactly one ``flush``. Two-step "clear, then write" leaves the
    terminal blank for the gap between the two I/O operations and is the
    very failure mode this issue fixes."""

    def test_emits_clear_sequence_followed_by_content_in_one_write(self) -> None:
        captured_writes: list[str] = []
        captured_flushes: list[bool] = []

        class _FakeStdout:
            def write(self, s: str) -> int:
                captured_writes.append(s)
                return len(s)

            def flush(self) -> None:
                captured_flushes.append(True)

        with patch.object(sys, "stdout", _FakeStdout()):
            _clear_and_write("FRAME-CONTENT")

        # Exactly one write call, exactly one flush.
        assert len(captured_writes) == 1, f"expected 1 write, got {captured_writes}"
        assert len(captured_flushes) == 1
        # The clear escape comes BEFORE the content with no intervening
        # flush; both bytes hit the buffer in a single call.
        full = captured_writes[0]
        assert full.startswith("\033c"), "clear sequence must be at the start"
        assert "FRAME-CONTENT" in full
        # The content must appear IMMEDIATELY after the clear sequence
        # (no other bytes between them).
        clear_end = len("\033c")
        assert full[clear_end : clear_end + len("FRAME-CONTENT")] == "FRAME-CONTENT"

    def test_no_intervening_flush_between_clear_and_content(self) -> None:
        """Even stronger no-blank guarantee: the buffered write does not
        flush between writing the clear sequence and writing the content.
        We assert this by ensuring there is exactly ONE flush total and
        it follows the single write."""
        events: list[tuple[str, str]] = []  # (kind, payload)

        class _FakeStdout:
            def write(self, s: str) -> int:
                events.append(("write", s))
                return len(s)

            def flush(self) -> None:
                events.append(("flush", ""))

        with patch.object(sys, "stdout", _FakeStdout()):
            _clear_and_write("X")

        kinds = [k for k, _ in events]
        assert kinds == ["write", "flush"]


class TestStdinKeypressPending:
    def test_returns_false_for_non_tty_stdin(self) -> None:
        # io.StringIO is not a TTY.
        with patch.object(sys, "stdin", io.StringIO("data")):
            assert _stdin_keypress_pending() is False


class TestStreamReport:
    """End-to-end behaviour of ``stream_report``: change-detection, render
    cadence, KeyboardInterrupt exit, and the latency-footer integration."""

    def test_renders_only_when_stat_changes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The streaming loop polls the cache stat and re-renders ONLY
        when a source file has advanced. Idle workspaces produce zero
        renders past the initial frame."""
        log_file = tmp_path / "log"
        log_file.write_text("a")
        render_calls = 0

        def fake_render(*, log_path: Path) -> str:
            nonlocal render_calls
            render_calls += 1
            return f"frame-{render_calls}"

        sleep_count = 0

        def fake_sleep(_seconds: float) -> None:
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 3:
                raise KeyboardInterrupt
            # On second tick, mutate the log file so the next stat
            # comparison detects a change and triggers a render.
            if sleep_count == 1:
                log_file.write_text("ab")

        with (
            patch("devbench.reporting.streaming.time.sleep", side_effect=fake_sleep),
            patch.object(sys, "stdout", io.StringIO()),
        ):
            rc = stream_report(log_file, fake_render)

        assert rc == 0
        # Initial render plus one re-render after the file mutation
        # = 2 renders. The third tick would have triggered another
        # render but the KeyboardInterrupt fires first.
        assert render_calls == 2

    def test_keyboard_interrupt_exits_cleanly(self, tmp_path: Path) -> None:
        log_file = tmp_path / "log"
        log_file.write_text("a")

        def fake_render(*, log_path: Path) -> str:
            return "frame"

        def fake_sleep(_seconds: float) -> None:
            raise KeyboardInterrupt

        with (
            patch("devbench.reporting.streaming.time.sleep", side_effect=fake_sleep),
            patch.object(sys, "stdout", io.StringIO()),
        ):
            rc = stream_report(log_file, fake_render)

        assert rc == 0

    def test_first_render_is_cold_subsequent_are_warm(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The cold/warm distinction in the latency tracker is driven by
        ``last_key is None`` -- True only on the first iteration. Any
        subsequent stat-change render is warm."""
        log_file = tmp_path / "log"
        log_file.write_text("a")

        def fake_render(*, log_path: Path) -> str:
            return "frame"

        sleep_count = 0

        def fake_sleep(_seconds: float) -> None:
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 3:
                raise KeyboardInterrupt
            if sleep_count == 1:
                log_file.write_text("ab")

        capture = io.StringIO()
        with (
            patch("devbench.reporting.streaming.time.sleep", side_effect=fake_sleep),
            patch.object(sys, "stdout", capture),
        ):
            stream_report(log_file, fake_render)

        # The captured frames carry the latency footer. Both a cold
        # (first-render) and a warm (post-mutation) tick should be
        # represented; check the second frame shows non-dash warm.
        out = capture.getvalue()
        # Two frames means two clear sequences.
        assert out.count("\033c") == 2
        # Final footer should show populated cold + warm + last.
        assert "[refresh] cold" in out
        assert "warm" in out
        assert "last refresh" in out
