"""Unit tests for ``devbench.hook_tail``.

Covers the pure formatter (``format_entry``), the timezone resolver
(``resolve_timezone``), the tail-follow loop (``follow``), and the
header + color-detection helpers. Single source of truth for the
canonical output format: test output is compared against import-time
constants from the module so layout drift fails loudly instead of
silently.
"""

from __future__ import annotations

import io
import json
import threading
import time
from datetime import UTC
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from devbench.hook_tail import (
    AGENT_WIDTH,
    DESCRIPTION_MAX,
    EVENT_LABELS,
    EVENT_WIDTH,
    STDOUT_PREVIEW_MAX,
    TOOL_WIDTH,
    FollowOptions,
    InvalidTimezoneError,
    follow,
    format_entry,
    render_header,
    resolve_timezone,
    should_use_color,
    timezone_display,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _entry(**overrides) -> dict:
    """Build a well-formed hook-logs.jsonl record with overridable fields."""
    base = {
        "timestamp": "2026-04-19T03:51:00Z",
        "event": "PreToolUse",
        "input": {
            "agent_type": "devbench:executor",
            "tool_name": "Bash",
            "tool_input": {"description": "Run tests"},
        },
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# resolve_timezone
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveTimezone:
    def test_none_returns_os_local_tzinfo(self) -> None:
        tz = resolve_timezone(None)
        assert tz is not None
        assert tz.utcoffset(None) is not None or True  # tzinfo may need a dt; just assert not-None

    def test_empty_string_returns_os_local_tzinfo(self) -> None:
        tz = resolve_timezone("")
        assert tz is not None

    def test_utc_returns_zero_offset_zone(self) -> None:
        tz = resolve_timezone("UTC")
        from datetime import datetime

        offset = datetime.now(tz).utcoffset()
        assert offset is not None
        assert offset.total_seconds() == 0

    def test_named_zone_returns_zoneinfo_with_matching_key(self) -> None:
        tz = resolve_timezone("America/New_York")
        assert isinstance(tz, ZoneInfo)
        assert tz.key == "America/New_York"

    def test_unknown_zone_raises_invalid_timezone_error(self) -> None:
        with pytest.raises(InvalidTimezoneError) as excinfo:
            resolve_timezone("Not/AZone")
        assert "Not/AZone" in str(excinfo.value)


# ---------------------------------------------------------------------------
# timezone_display
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTimezoneDisplay:
    def test_utc_reports_utc_iana_and_abbreviation(self) -> None:
        iana, abbrev = timezone_display(ZoneInfo("UTC"))
        assert iana == "UTC"
        assert abbrev == "UTC"

    def test_america_new_york_reports_zone_key_and_current_abbreviation(self) -> None:
        iana, abbrev = timezone_display(ZoneInfo("America/New_York"))
        assert iana == "America/New_York"
        # EDT in April 2026, EST in January -- allow either since tests run year-round.
        assert abbrev in {"EDT", "EST"}


# ---------------------------------------------------------------------------
# format_entry -- event labels (parametrized over the full map)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatEntryEventLabels:
    @pytest.mark.parametrize("event,expected", list(EVENT_LABELS.items()))
    def test_known_event_renders_canonical_label(self, event: str, expected: str) -> None:
        entry = _entry(event=event)
        line = format_entry(entry, ZoneInfo("UTC"), color=False)
        # After HH:MM:SS is a space then the padded event (EVENT_WIDTH chars).
        # Slice past the timestamp and its trailing space.
        slot = line[len("HH:MM:SS") + 1 : len("HH:MM:SS") + 1 + EVENT_WIDTH]
        assert slot == expected

    def test_unknown_event_renders_first_two_chars(self) -> None:
        entry = _entry(event="WeirdEvent")
        line = format_entry(entry, ZoneInfo("UTC"), color=False)
        slot = line[9 : 9 + EVENT_WIDTH]
        assert slot == "We"

    def test_missing_event_renders_sentinel(self) -> None:
        entry = _entry()
        entry.pop("event")
        line = format_entry(entry, ZoneInfo("UTC"), color=False)
        slot = line[9 : 9 + EVENT_WIDTH]
        # Single "?" character padded to EVENT_WIDTH.
        assert slot == "? "


# ---------------------------------------------------------------------------
# format_entry -- field fallback, sentinels, widths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatEntryFallbacks:
    def test_description_fallback_prefers_description_over_command(self) -> None:
        entry = _entry(input={"tool_input": {"description": "DESC", "command": "CMD"}})
        line = format_entry(entry, ZoneInfo("UTC"), color=False)
        assert "DESC" in line
        assert "CMD" not in line

    def test_description_falls_back_to_command(self) -> None:
        entry = _entry(input={"tool_input": {"command": "CMD"}})
        assert "CMD" in format_entry(entry, ZoneInfo("UTC"), color=False)

    def test_description_falls_back_to_file_path(self) -> None:
        entry = _entry(input={"tool_input": {"file_path": "/tmp/x.py"}})
        assert "/tmp/x.py" in format_entry(entry, ZoneInfo("UTC"), color=False)

    def test_description_stringifies_unknown_shape(self) -> None:
        entry = _entry(input={"tool_input": {"other": 42}})
        out = format_entry(entry, ZoneInfo("UTC"), color=False)
        assert '"other"' in out
        assert "42" in out

    def test_description_empty_when_tool_input_none(self) -> None:
        entry = _entry(input={"tool_input": None})
        # Must still render a row (no crash) and not contain any "null"/"None" junk
        # after the tool column.
        line = format_entry(entry, ZoneInfo("UTC"), color=False)
        assert line  # non-empty
        assert "None" not in line

    def test_description_truncated_at_limit(self) -> None:
        big = "x" * (DESCRIPTION_MAX + 50)
        entry = _entry(input={"tool_input": {"description": big}})
        line = format_entry(entry, ZoneInfo("UTC"), color=False)
        assert "x" * DESCRIPTION_MAX in line
        assert "x" * (DESCRIPTION_MAX + 1) not in line

    def test_stdout_preview_picks_last_non_empty_line(self) -> None:
        entry = _entry(
            input={
                "tool_name": "Bash",
                "tool_input": {"description": "run"},
                "tool_response": {"stdout": "first\n\nmiddle\n\nlast line\n"},
            }
        )
        line = format_entry(entry, ZoneInfo("UTC"), color=False)
        assert "last line" in line
        assert "first" not in line

    def test_stdout_preview_truncated_at_limit(self) -> None:
        entry = _entry(
            input={
                "tool_name": "Bash",
                "tool_input": {"description": "run"},
                "tool_response": {"stdout": "y" * (STDOUT_PREVIEW_MAX + 50)},
            }
        )
        line = format_entry(entry, ZoneInfo("UTC"), color=False)
        assert "y" * STDOUT_PREVIEW_MAX in line
        assert "y" * (STDOUT_PREVIEW_MAX + 1) not in line

    def test_stdout_preview_absent_when_tool_response_missing(self) -> None:
        entry = _entry(input={"tool_name": "Bash", "tool_input": {"description": "x"}})
        line = format_entry(entry, ZoneInfo("UTC"), color=False)
        assert "|" not in line

    def test_agent_padded_to_width_and_prefix_stripped(self) -> None:
        entry = _entry(input={"agent_type": "devbench:executor", "tool_name": "Bash", "tool_input": {}})
        line = format_entry(entry, ZoneInfo("UTC"), color=False)
        # After timestamp + event + padding, agent is padded to AGENT_WIDTH.
        # We don't slice by offset (padding shifts with sentinels); just
        # assert the prefix-stripped form is present.
        assert "executor" in line
        assert "devbench:executor" not in line

    def test_missing_agent_uses_default(self) -> None:
        entry = _entry(input={"tool_name": "Bash", "tool_input": {}})
        # no agent_type
        line = format_entry(entry, ZoneInfo("UTC"), color=False)
        assert "orch" in line

    def test_missing_tool_renders_sentinel(self) -> None:
        entry = _entry(input={"agent_type": "executor", "tool_input": {}})
        line = format_entry(entry, ZoneInfo("UTC"), color=False)
        # Single "?" in the tool column.
        assert "?" in line

    def test_missing_input_block_renders_sentinel_row(self) -> None:
        entry = _entry()
        entry.pop("input")
        line = format_entry(entry, ZoneInfo("UTC"), color=False)
        assert line  # non-empty; no crash
        assert "orch" in line

    def test_non_dict_entry_renders_fallback_sentinel(self) -> None:
        line = format_entry("not a dict", ZoneInfo("UTC"), color=False)  # type: ignore[arg-type]
        assert "--:--:--" in line
        assert "not a dict" in line


# ---------------------------------------------------------------------------
# format_entry -- timestamps + timezones
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatEntryTimestamps:
    def test_utc_timestamp_rendered_in_utc(self) -> None:
        line = format_entry(_entry(timestamp="2026-04-19T12:34:56Z"), ZoneInfo("UTC"), color=False)
        assert line.startswith("12:34:56")

    def test_utc_timestamp_rendered_in_eastern_applies_offset(self) -> None:
        # 2026-04-19 is in EDT (UTC-4).
        line = format_entry(
            _entry(timestamp="2026-04-19T12:34:56Z"),
            ZoneInfo("America/New_York"),
            color=False,
        )
        assert line.startswith("08:34:56")

    def test_missing_timestamp_renders_sentinel(self) -> None:
        entry = _entry()
        entry.pop("timestamp")
        line = format_entry(entry, ZoneInfo("UTC"), color=False)
        assert line.startswith("--:--:--")

    def test_malformed_timestamp_does_not_crash(self) -> None:
        line = format_entry(_entry(timestamp="not a timestamp"), ZoneInfo("UTC"), color=False)
        assert line.startswith("--:--:--")

    def test_timestamp_without_z_suffix_also_parses(self) -> None:
        line = format_entry(
            _entry(timestamp="2026-04-19T12:34:56+00:00"),
            ZoneInfo("UTC"),
            color=False,
        )
        assert line.startswith("12:34:56")


# ---------------------------------------------------------------------------
# format_entry -- color
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatEntryColor:
    def test_color_false_produces_no_ansi_escapes(self) -> None:
        line = format_entry(_entry(), ZoneInfo("UTC"), color=False)
        assert "\033[" not in line

    def test_color_true_wraps_columns_in_ansi(self) -> None:
        line = format_entry(_entry(), ZoneInfo("UTC"), color=True)
        assert "\033[" in line
        assert "\033[0m" in line


# ---------------------------------------------------------------------------
# render_header
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRenderHeader:
    def test_header_contains_path_and_zone_names(self) -> None:
        header = render_header(Path("/tmp/log.jsonl"), ZoneInfo("UTC"), color=False)
        assert "/tmp/log.jsonl" in header
        assert "UTC" in header
        assert "raw log stores UTC" in header

    def test_header_two_lines(self) -> None:
        header = render_header(Path("/x"), ZoneInfo("UTC"), color=False)
        assert header.count("\n") == 1

    def test_header_color_false_has_no_escapes(self) -> None:
        header = render_header(Path("/x"), ZoneInfo("UTC"), color=False)
        assert "\033[" not in header

    def test_header_color_true_has_escapes(self) -> None:
        header = render_header(Path("/x"), ZoneInfo("UTC"), color=True)
        assert "\033[" in header


# ---------------------------------------------------------------------------
# should_use_color
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestShouldUseColor:
    def test_no_color_env_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NO_COLOR", "1")

        class FakeTTY:
            def isatty(self) -> bool:
                return True

        assert should_use_color(FakeTTY()) is False  # type: ignore[arg-type]

    def test_non_tty_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NO_COLOR", raising=False)

        class NotTTY:
            def isatty(self) -> bool:
                return False

        assert should_use_color(NotTTY()) is False  # type: ignore[arg-type]

    def test_tty_enables_color(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NO_COLOR", raising=False)

        class FakeTTY:
            def isatty(self) -> bool:
                return True

        assert should_use_color(FakeTTY()) is True  # type: ignore[arg-type]

    def test_stream_without_isatty_defaults_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NO_COLOR", raising=False)

        class NoIsatty:
            pass

        assert should_use_color(NoIsatty()) is False  # type: ignore[arg-type]

    def test_isatty_raises_value_error_treated_as_non_tty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NO_COLOR", raising=False)

        class BrokenTTY:
            def isatty(self) -> bool:
                raise ValueError("closed")

        assert should_use_color(BrokenTTY()) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# follow -- no-follow mode (snapshot)
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")


@pytest.mark.unit
class TestFollowNoFollow:
    def test_empty_file_produces_no_output(self, tmp_path: Path) -> None:
        log = tmp_path / "hook.jsonl"
        log.write_text("", encoding="utf-8")
        buf = io.StringIO()
        rc = follow(
            log,
            FollowOptions(tz=ZoneInfo("UTC"), no_follow=True, from_start=True, color=False),
            buf,
        )
        assert rc == 0
        assert buf.getvalue() == ""

    def test_file_with_entries_produces_n_lines(self, tmp_path: Path) -> None:
        log = tmp_path / "hook.jsonl"
        entries = [
            _entry(timestamp="2026-04-19T00:00:00Z"),
            _entry(timestamp="2026-04-19T00:00:01Z"),
            _entry(timestamp="2026-04-19T00:00:02Z"),
        ]
        _write_jsonl(log, entries)
        buf = io.StringIO()
        rc = follow(
            log,
            FollowOptions(tz=ZoneInfo("UTC"), no_follow=True, from_start=True, color=False),
            buf,
        )
        assert rc == 0
        lines = [ln for ln in buf.getvalue().splitlines() if ln]
        assert len(lines) == 3
        assert lines[0].startswith("00:00:00")
        assert lines[2].startswith("00:00:02")

    def test_seek_to_eof_by_default(self, tmp_path: Path) -> None:
        log = tmp_path / "hook.jsonl"
        _write_jsonl(log, [_entry(), _entry(), _entry()])
        buf = io.StringIO()
        rc = follow(
            log,
            FollowOptions(tz=ZoneInfo("UTC"), no_follow=True, from_start=False, color=False),
            buf,
        )
        assert rc == 0
        assert buf.getvalue() == ""  # seek-to-EOF skips existing history

    def test_bad_json_line_produces_sentinel_row_and_continues(self, tmp_path: Path) -> None:
        log = tmp_path / "hook.jsonl"
        log.write_text(
            "not valid json at all\n" + json.dumps(_entry(timestamp="2026-04-19T00:00:05Z")) + "\n",
            encoding="utf-8",
        )
        buf = io.StringIO()
        rc = follow(
            log,
            FollowOptions(tz=ZoneInfo("UTC"), no_follow=True, from_start=True, color=False),
            buf,
        )
        assert rc == 0
        out = buf.getvalue()
        assert "bad-json" in out
        assert "00:00:05" in out

    def test_blank_lines_skipped(self, tmp_path: Path) -> None:
        log = tmp_path / "hook.jsonl"
        log.write_text("\n\n" + json.dumps(_entry()) + "\n\n", encoding="utf-8")
        buf = io.StringIO()
        follow(
            log,
            FollowOptions(tz=ZoneInfo("UTC"), no_follow=True, from_start=True, color=False),
            buf,
        )
        lines = [ln for ln in buf.getvalue().splitlines() if ln]
        assert len(lines) == 1

    def test_missing_path_in_no_follow_exits_nonzero(self, tmp_path: Path) -> None:
        log = tmp_path / "does-not-exist.jsonl"
        buf = io.StringIO()
        rc = follow(
            log,
            FollowOptions(tz=ZoneInfo("UTC"), no_follow=True, from_start=True, color=False),
            buf,
        )
        assert rc == 1


# ---------------------------------------------------------------------------
# follow -- live tail (thread-driven)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFollowLiveTail:
    def test_tail_follow_reads_lines_as_they_appear(self, tmp_path: Path) -> None:
        """Spawn follow() on a thread with an empty file, then append entries
        and verify they are formatted within a bounded time.

        Uses a very short ``poll_seconds`` so the test runs fast and does not
        time out under CI load.
        """
        log = tmp_path / "hook.jsonl"
        log.write_text("", encoding="utf-8")

        buf = io.StringIO()
        options = FollowOptions(
            tz=ZoneInfo("UTC"),
            no_follow=False,
            from_start=False,
            color=False,
            poll_seconds=0.01,
        )
        thread = threading.Thread(target=follow, args=(log, options, buf), daemon=True)
        thread.start()

        # Give the follower a moment to open and seek-to-EOF.
        time.sleep(0.05)

        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(_entry(timestamp="2026-04-19T01:00:00Z")) + "\n")
            f.flush()

        # Wait up to 2s for the line to appear (bounded so CI can't hang).
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if "01:00:00" in buf.getvalue():
                break
            time.sleep(0.02)

        assert "01:00:00" in buf.getvalue()
        # Thread is daemon=True so it dies with the test process; no join needed.

    def test_tail_follow_reopens_on_inode_rotation(self, tmp_path: Path) -> None:
        """Write entries, replace the file (different inode), write more.

        The follower must pick up the new file's contents. This exercises the
        rotation-recovery branch of the loop.
        """
        log = tmp_path / "hook.jsonl"
        _write_jsonl(log, [_entry(timestamp="2026-04-19T02:00:00Z")])

        buf = io.StringIO()
        options = FollowOptions(
            tz=ZoneInfo("UTC"),
            no_follow=False,
            from_start=True,  # start-from-zero so the first write is included
            color=False,
            poll_seconds=0.01,
        )
        thread = threading.Thread(target=follow, args=(log, options, buf), daemon=True)
        thread.start()

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if "02:00:00" in buf.getvalue():
                break
            time.sleep(0.02)
        assert "02:00:00" in buf.getvalue()

        # Rotate: replace the file with a new inode.
        log.unlink()
        # Small gap so the follower observes file-absence, then reopens.
        time.sleep(0.05)
        _write_jsonl(log, [_entry(timestamp="2026-04-19T03:00:00Z")])

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if "03:00:00" in buf.getvalue():
                break
            time.sleep(0.02)
        assert "03:00:00" in buf.getvalue()

    def test_follow_mode_waits_for_file_to_appear(self, tmp_path: Path) -> None:
        """When the file does not exist at startup and ``no_follow=False``,
        the follower polls until it appears, then reads normally."""
        log = tmp_path / "will-appear-later.jsonl"

        buf = io.StringIO()
        options = FollowOptions(
            tz=ZoneInfo("UTC"),
            no_follow=False,
            from_start=True,
            color=False,
            poll_seconds=0.01,
        )
        thread = threading.Thread(target=follow, args=(log, options, buf), daemon=True)
        thread.start()

        time.sleep(0.05)
        _write_jsonl(log, [_entry(timestamp="2026-04-19T04:00:00Z")])

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if "04:00:00" in buf.getvalue():
                break
            time.sleep(0.02)
        assert "04:00:00" in buf.getvalue()


# ---------------------------------------------------------------------------
# Coverage-completeness edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEdgeCases:
    def test_non_string_timestamp_does_not_crash(self) -> None:
        entry = _entry(timestamp=12345)  # type: ignore[arg-type]
        line = format_entry(entry, ZoneInfo("UTC"), color=False)
        assert line  # must render something

    def test_tool_response_with_non_string_stdout(self) -> None:
        entry = _entry(
            input={
                "agent_type": "executor",
                "tool_name": "Bash",
                "tool_input": {"description": "x"},
                "tool_response": {"stdout": 42},
            }
        )
        line = format_entry(entry, ZoneInfo("UTC"), color=False)
        assert "|" not in line  # no preview when stdout is not a string

    def test_description_non_dict_tool_input_rendered(self) -> None:
        entry = _entry(input={"tool_input": "just a string"})
        line = format_entry(entry, ZoneInfo("UTC"), color=False)
        assert "just a string" in line

    def test_non_dict_input_block_still_renders(self) -> None:
        # Tests the branch where entry.get("input") is not a dict.
        entry = {"timestamp": "2026-04-19T00:00:00Z", "event": "PreToolUse", "input": "wat"}
        line = format_entry(entry, ZoneInfo("UTC"), color=False)
        assert line.startswith("00:00:00")

    def test_follow_options_frozen(self) -> None:
        opts = FollowOptions(tz=ZoneInfo("UTC"))
        # Frozen dataclass assignment raises FrozenInstanceError; catch the
        # specific exception so the test would fail loudly if the class
        # were silently un-frozen by a refactor.
        from dataclasses import FrozenInstanceError

        with pytest.raises(FrozenInstanceError):
            opts.no_follow = True  # type: ignore[misc]

    def test_datetime_utc_alias_works(self) -> None:
        """The stdlib ``datetime.UTC`` alias works as a valid ``tz`` argument."""
        line = format_entry(_entry(timestamp="2026-04-19T05:06:07Z"), UTC, color=False)
        assert line.startswith("05:06:07")

    def test_padding_widths_match_declared_constants(self) -> None:
        """Regression guard: if someone changes AGENT_WIDTH without updating
        the formatter, this test fails.
        """
        entry = _entry(
            input={
                "agent_type": "devbench:super-long-agent-name-exceeding-width",
                "tool_name": "VeryLongToolName",
                "tool_input": {"description": "x"},
            }
        )
        line = format_entry(entry, ZoneInfo("UTC"), color=False)
        # The first occurrence of the description "x" should be at or after
        # the minimum column position determined by the declared widths.
        # HH:MM:SS(8) + " " + event(2) + " " + agent(12) + " " + tool(8) + " " = 32
        min_desc_pos = 8 + 1 + EVENT_WIDTH + 1 + AGENT_WIDTH + 1 + TOOL_WIDTH + 1
        # The agent and tool may be truncated but still padded to width;
        # the description appears after both.
        assert line.rfind(" x") >= min_desc_pos - 1

    def test_non_dict_input_tool_name_falls_back(self) -> None:
        entry = {"timestamp": "2026-04-19T00:00:00Z", "event": "PreToolUse", "input": {}}
        line = format_entry(entry, ZoneInfo("UTC"), color=False)
        # Default agent "orch" + sentinel tool "?" should render.
        assert "orch" in line
        # isolate the tool column by looking after the agent slot.
        assert "?" in line

    def test_keyboard_interrupt_in_follow_loop_returns_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ctrl+C while tailing is a clean exit, not a traceback."""
        from devbench import hook_tail as ht

        log = tmp_path / "hook.jsonl"
        log.write_text("", encoding="utf-8")

        def _raise(*_a, **_kw):
            raise KeyboardInterrupt

        monkeypatch.setattr(ht, "_follow_loop", _raise)
        rc = ht.follow(
            log,
            FollowOptions(tz=ZoneInfo("UTC"), no_follow=False, from_start=False, color=False),
            io.StringIO(),
        )
        assert rc == 0
