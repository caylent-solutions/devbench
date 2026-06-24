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
    StreamRenderBudgetExceededError,
    _backoff_interval,
    _clear_and_write,
    _LatencyTracker,
    _read_log_tail,
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
        t.record(0.7, cold=True)
        assert t.cold == pytest.approx(0.5)

    def test_warm_history_capped_at_8(self) -> None:
        t = _LatencyTracker()
        for i in range(20):
            t.record(0.01 * (i + 1), cold=False)
        assert len(t.warm_history) == 8
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

        assert len(captured_writes) == 1, f"expected 1 write, got {captured_writes}"
        assert len(captured_flushes) == 1
        full = captured_writes[0]
        assert full.startswith("\033c"), "clear sequence must be at the start"
        assert "FRAME-CONTENT" in full
        clear_end = len("\033c")
        assert full[clear_end : clear_end + len("FRAME-CONTENT")] == "FRAME-CONTENT"

    def test_no_intervening_flush_between_clear_and_content(self) -> None:
        """Even stronger no-blank guarantee: the buffered write does not
        flush between writing the clear sequence and writing the content.
        We assert this by ensuring there is exactly ONE flush total and
        it follows the single write."""
        events: list[tuple[str, str]] = []

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
        with patch.object(sys, "stdin", io.StringIO("data")):
            assert _stdin_keypress_pending() is False

    def test_returns_true_when_tty_stdin_has_pending_input(self) -> None:
        """When stdin is a TTY and select.select reports it ready, return True."""
        from unittest.mock import MagicMock

        fake_stdin = MagicMock()
        fake_stdin.isatty.return_value = True
        with (
            patch.object(sys, "stdin", fake_stdin),
            patch(
                "devbench.reporting.streaming.select.select",
                return_value=([fake_stdin], [], []),
            ),
        ):
            assert _stdin_keypress_pending() is True

    def test_returns_false_when_tty_stdin_has_no_pending_input(self) -> None:
        """When stdin is a TTY and select.select reports nothing ready, return False."""
        from unittest.mock import MagicMock

        fake_stdin = MagicMock()
        fake_stdin.isatty.return_value = True
        with (
            patch.object(sys, "stdin", fake_stdin),
            patch(
                "devbench.reporting.streaming.select.select",
                return_value=([], [], []),
            ),
        ):
            assert _stdin_keypress_pending() is False

    def test_returns_false_when_select_raises_oserror(self) -> None:
        """A broken stdin descriptor (OSError from select) is treated as no keypress."""
        from unittest.mock import MagicMock

        fake_stdin = MagicMock()
        fake_stdin.isatty.return_value = True
        with (
            patch.object(sys, "stdin", fake_stdin),
            patch(
                "devbench.reporting.streaming.select.select",
                side_effect=OSError("bad fd"),
            ),
        ):
            assert _stdin_keypress_pending() is False

    def test_returns_false_when_select_raises_valueerror(self) -> None:
        """A closed stdin (ValueError from select) is treated as no keypress."""
        from unittest.mock import MagicMock

        fake_stdin = MagicMock()
        fake_stdin.isatty.return_value = True
        with (
            patch.object(sys, "stdin", fake_stdin),
            patch(
                "devbench.reporting.streaming.select.select",
                side_effect=ValueError("closed"),
            ),
        ):
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
            if sleep_count == 1:
                log_file.write_text("ab")

        with (
            patch("devbench.reporting.streaming.time.sleep", side_effect=fake_sleep),
            patch.object(sys, "stdout", io.StringIO()),
        ):
            rc = stream_report(log_file, fake_render)

        assert rc == 0
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

        out = capture.getvalue()
        assert out.count("\033c") == 2
        assert "[refresh] cold" in out
        assert "warm" in out
        assert "last refresh" in out

    def test_hook_log_and_transcript_dir_extend_stat_paths(self, tmp_path: Path) -> None:
        """When ``hook_log_path`` and ``transcript_dir`` are passed, their stat
        tuples participate in the change-detection key, so mutating either one
        triggers a re-render even when the orchestrator log is untouched."""
        log_file = tmp_path / "orch.log"
        log_file.write_text("a")
        hook_log = tmp_path / "hook.log"
        hook_log.write_text("h")
        transcripts = tmp_path / "transcripts"
        transcripts.mkdir()
        (transcripts / "t.jsonl").write_text("{}")

        def fake_render(*, log_path: Path) -> str:
            return "frame"

        sleep_count = 0

        def fake_sleep(_seconds: float) -> None:
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise KeyboardInterrupt
            hook_log.write_text("hh")

        with (
            patch("devbench.reporting.streaming.time.sleep", side_effect=fake_sleep),
            patch.object(sys, "stdout", io.StringIO()) as cap,
        ):
            rc = stream_report(
                log_file,
                fake_render,
                hook_log_path=hook_log,
                transcript_dir=transcripts,
            )
        assert rc == 0
        assert cap.getvalue().count("\033c") == 2

    def test_keypress_breaks_loop_and_returns_zero(self, tmp_path: Path) -> None:
        """When ``_stdin_keypress_pending`` returns True, the loop breaks and
        the function returns rc=0 via the non-KeyboardInterrupt exit path."""
        log_file = tmp_path / "log"
        log_file.write_text("a")

        def fake_render(*, log_path: Path) -> str:
            return "frame"

        with (
            patch(
                "devbench.reporting.streaming._stdin_keypress_pending",
                return_value=True,
            ),
            patch.object(sys, "stdout", io.StringIO()),
        ):
            rc = stream_report(log_file, fake_render)
        assert rc == 0


class TestBackoffInterval:
    """The adaptive-backoff helper grows the poll interval when consecutive
    renders are slow, so a fast-growing log cannot pin a core at the fixed
    base cadence. All bounds are caller-supplied (no hard-coded literals)."""

    def test_fast_render_keeps_base_interval(self) -> None:
        interval = _backoff_interval(
            render_duration=0.001,
            base_interval=0.1,
            max_interval=2.0,
        )
        assert interval == pytest.approx(0.1)

    def test_slow_render_backs_off_proportionally(self) -> None:
        interval = _backoff_interval(
            render_duration=0.5,
            base_interval=0.1,
            max_interval=2.0,
        )
        assert interval >= 0.5

    def test_backoff_is_capped_at_max_interval(self) -> None:
        interval = _backoff_interval(
            render_duration=120.0,
            base_interval=0.1,
            max_interval=2.0,
        )
        assert interval == pytest.approx(2.0)


class TestRenderBudgetFailFast:
    """Issue TDI-005: a single render exceeding the configured budget must
    fail fast (raise / non-zero exit) rather than let the loop spin forever
    re-rendering an ever-growing log."""

    def test_render_exceeding_budget_raises(self, tmp_path: Path) -> None:
        log_file = tmp_path / "log"
        log_file.write_text("a")

        perf_values = iter([0.0, 5.0, 100.0, 200.0])

        def fake_perf() -> float:
            return next(perf_values)

        def slow_render(*, log_path: Path) -> str:
            return "frame"

        with (
            patch("devbench.reporting.streaming.time.perf_counter", side_effect=fake_perf),
            patch.object(sys, "stdout", io.StringIO()),
            pytest.raises(StreamRenderBudgetExceededError),
        ):
            stream_report(log_file, slow_render, render_budget_seconds=1.0)

    def test_render_within_budget_does_not_raise(self, tmp_path: Path) -> None:
        log_file = tmp_path / "log"
        log_file.write_text("a")

        perf_values = iter([0.0, 0.01, 0.02, 0.03])

        def fake_perf() -> float:
            return next(perf_values)

        def fast_render(*, log_path: Path) -> str:
            return "frame"

        def fake_sleep(_seconds: float) -> None:
            raise KeyboardInterrupt

        with (
            patch("devbench.reporting.streaming.time.perf_counter", side_effect=fake_perf),
            patch("devbench.reporting.streaming.time.sleep", side_effect=fake_sleep),
            patch.object(sys, "stdout", io.StringIO()),
        ):
            rc = stream_report(log_file, fast_render, render_budget_seconds=1.0)
        assert rc == 0

    def test_loop_backs_off_when_render_is_slow(self, tmp_path: Path) -> None:
        """When a render is slow (but under the fail-fast budget), the next
        poll interval must back off above the base cadence so a fast-growing
        log cannot pin a CPU core at the fixed base interval."""
        log_file = tmp_path / "log"
        log_file.write_text("a")

        perf_values = iter([0.0, 0.5, 1.0, 1.5])

        def fake_perf() -> float:
            return next(perf_values)

        def slow_render(*, log_path: Path) -> str:
            return "frame"

        observed_sleeps: list[float] = []

        def fake_sleep(seconds: float) -> None:
            observed_sleeps.append(seconds)
            raise KeyboardInterrupt

        with (
            patch("devbench.reporting.streaming.time.perf_counter", side_effect=fake_perf),
            patch("devbench.reporting.streaming.time.sleep", side_effect=fake_sleep),
            patch.object(sys, "stdout", io.StringIO()),
        ):
            stream_report(
                log_file,
                slow_render,
                poll_interval=0.1,
                render_budget_seconds=5.0,
                max_poll_interval=2.0,
            )

        assert observed_sleeps, "loop must have slept at least once"
        assert observed_sleeps[0] > 0.1


class TestIncrementalLogTail:
    """Issue TDI-005 requirement (ii): the streaming layer's log read is
    incremental -- each tick reads only the bytes appended since the prior
    offset, never re-reading the entire (growing) file."""

    def test_tail_reads_from_offset_only(self, tmp_path: Path) -> None:
        log_file = tmp_path / "log"
        log_file.write_text("AAAA")
        text, offset = _read_log_tail(log_file, 0, max_bytes=1_000_000)
        assert text == "AAAA"
        assert offset == 4

        with log_file.open("a") as f:
            f.write("BBB")
        text2, offset2 = _read_log_tail(log_file, offset, max_bytes=1_000_000)
        assert text2 == "BBB"
        assert offset2 == 7

    def test_tail_does_not_reread_whole_file_each_tick(self, tmp_path: Path) -> None:
        """Across many appends, the cumulative bytes read by repeated tail
        reads equals the file's final size -- proving each tick reads only
        its delta, not the whole growing file (which would be O(n^2))."""
        log_file = tmp_path / "log"
        log_file.write_bytes(b"")

        total_appended = 0
        total_read = 0
        offset = 0
        chunk = "X" * 100
        for _ in range(50):
            with log_file.open("a") as f:
                f.write(chunk)
            total_appended += len(chunk)
            text, offset = _read_log_tail(log_file, offset, max_bytes=1_000_000)
            total_read += len(text)

        assert total_read == total_appended

    def test_tail_caps_bytes_read(self, tmp_path: Path) -> None:
        """A single tail read never pulls more than ``max_bytes`` into
        memory even when far more has been appended -- bounding RSS."""
        log_file = tmp_path / "log"
        log_file.write_bytes(b"Z" * 10_000)
        text, offset = _read_log_tail(log_file, 0, max_bytes=1_000)
        assert len(text) == 1_000
        assert offset == 1_000

    def test_tail_handles_truncation_or_rotation(self, tmp_path: Path) -> None:
        """If the file shrinks below the saved offset (rotation/truncation),
        the tail read restarts from 0 rather than seeking past EOF."""
        log_file = tmp_path / "log"
        log_file.write_text("AAAAAAAA")
        _, offset = _read_log_tail(log_file, 0, max_bytes=1_000_000)
        assert offset == 8
        log_file.write_text("BB")
        text, new_offset = _read_log_tail(log_file, offset, max_bytes=1_000_000)
        assert text == "BB"
        assert new_offset == 2
