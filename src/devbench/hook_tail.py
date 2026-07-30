"""Live pretty-tail of devbench hook-logs.jsonl.

Renders each JSONL record written by the plugin's hook-logger into a colorized
single-line summary so operators can follow an orchestration run in real time.

The raw log always stores UTC; this module converts to a display timezone at
render time. The OS local timezone is the default (``datetime.now().astimezone()``);
operators override via the ``--tz <zoneinfo-name>`` flag on ``devbench hook-tail``.

Public API (all consumed by ``devbench.cli.cmd_hook_tail``):

- ``resolve_timezone(name)`` -- parse a zoneinfo name or fall back to OS local.
- ``format_entry(entry, tz, *, color)`` -- pure formatter for a single record.
- ``follow(path, ...)`` -- tail-follow loop; writes formatted lines to ``output``.

``format_entry`` is pure and total -- given any dict (even a malformed one) it
returns a renderable row, using sentinel values for missing fields. A single
bad line in the log can never kill the tail.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TextIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from devbench.config import (
    HOOK_TAIL_AGENT_WIDTH,
    HOOK_TAIL_DESCRIPTION_MAX,
    HOOK_TAIL_STDOUT_PREVIEW_MAX,
    HOOK_TAIL_TOOL_WIDTH,
)

# ---------------------------------------------------------------------------
# Formatting constants. Single source of truth; tests import from here so
# layout drift breaks loudly.
# ---------------------------------------------------------------------------

# Event -> two-char glyph. Identical to the bash hooktail script's mapping.
EVENT_LABELS: dict[str, str] = {
    "PreToolUse": "->",
    "PostToolUse": "<-",
    "PostToolUseFailure": "!!",
    "UserPromptSubmit": "U>",
    "Stop": "||",
    "SubagentStart": "+s",
    "SubagentStop": "-s",
    "PreCompact": "Cp",
    "PermissionRequest": "P?",
    "Notification": "No",
}

# Column-width / truncation caps. Resolved env > YAML > default at module
# import time via devbench.config (issue #134); operators tune these in
# `backlog/config/devbench.yaml` under `hook_tail:`. Defaults: 12 / 8 / 120 /
# 80. EVENT_WIDTH stays a plain constant -- the arrow column is intrinsic to
# the format (`->`, `<-`, `+s`, `-s`, `!!`, `U>`, `Cp`, `P?`, `No`) and 2
# chars is the only sensible width.
AGENT_WIDTH = HOOK_TAIL_AGENT_WIDTH
TOOL_WIDTH = HOOK_TAIL_TOOL_WIDTH
DESCRIPTION_MAX = HOOK_TAIL_DESCRIPTION_MAX
STDOUT_PREVIEW_MAX = HOOK_TAIL_STDOUT_PREVIEW_MAX
EVENT_WIDTH = 2
POLL_SECONDS_DEFAULT = 0.25
_WHITESPACE_RUN_RE = re.compile(r"\s+")

# ANSI SGR color codes. Named so call sites stay readable.
_ANSI_RESET = "\033[0m"
_ANSI_GRAY = "\033[90m"
_ANSI_CYAN = "\033[36m"
_ANSI_YELLOW = "\033[33m"
_ANSI_GREEN = "\033[32m"

# Sentinels surfaced when the corresponding field is missing or malformed.
# Explicit constants so operators can grep for them across a long run.
_MISSING_TIMESTAMP = "--:--:--"
_MISSING_EVENT = "?"
_DEFAULT_AGENT = "orch"
_MISSING_TOOL = "?"


class InvalidTimezoneError(ValueError):
    """Raised by ``resolve_timezone`` when the name is not a known zoneinfo.

    ``cmd_hook_tail`` catches this and maps to a stderr message + exit 2.
    Separate from ``ZoneInfoNotFoundError`` so callers don't have to import
    the zoneinfo module to handle the failure.
    """


# ---------------------------------------------------------------------------
# Timezone resolution
# ---------------------------------------------------------------------------


def resolve_timezone(name: str | None):
    """Return a ``tzinfo`` for ``name``, or the OS local zone when ``name`` is falsy.

    Parameters
    ----------
    name:
        IANA zoneinfo name (``America/New_York``, ``UTC``, etc.) or ``None``.

    Returns
    -------
    tzinfo
        ``ZoneInfo(name)`` when ``name`` is provided; otherwise the tzinfo
        attached to ``datetime.now().astimezone()`` (the OS-resolved local zone).

    Raises
    ------
    InvalidTimezoneError
        When ``name`` is a non-empty string that does not resolve to a
        known zoneinfo entry (message includes the offending name), or when
        ``name`` is empty and the OS local zone cannot be resolved.
    """
    if not name:
        local = datetime.now().astimezone().tzinfo
        if local is None:
            # Defensive, and deliberately not a silent substitution. Quietly
            # rendering timestamps in UTC when the OS zone could not be
            # resolved would present wrong local times as if they were right.
            raise InvalidTimezoneError(
                "could not resolve the local timezone: datetime.astimezone() returned no tzinfo. "
                "Set an explicit IANA name via the display-timezone config or DEVBENCH_DISPLAY_TIMEZONE."
            )
        return local
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise InvalidTimezoneError(f"unknown timezone: {name!r}") from exc


def timezone_display(tz) -> tuple[str, str]:
    """Return ``(iana_name, current_abbrev)`` for the header line.

    IANA name comes from ``tz.key`` when present (real ``ZoneInfo`` objects
    always have it). For OS-resolved local zones that don't expose a key
    attribute, falls back to ``str(tz)``. The abbreviation is pulled from
    the current moment so DST boundaries render correctly (EDT in April,
    EST in January, etc.).
    """
    iana = getattr(tz, "key", None) or str(tz)
    abbrev = datetime.now(tz).strftime("%Z") or iana
    return iana, abbrev


# ---------------------------------------------------------------------------
# Row formatter
# ---------------------------------------------------------------------------


def _pad(width: int, value: str) -> str:
    """Right-pad or truncate ``value`` to exactly ``width`` characters."""
    padded = value + " " * width
    return padded[:width]


def _color(body: str, code: str, *, enabled: bool) -> str:
    return f"{code}{body}{_ANSI_RESET}" if enabled else body


def _event_label(event: str) -> str:
    """Return the two-char glyph for ``event``, or its first two chars if unknown."""
    if event in EVENT_LABELS:
        return EVENT_LABELS[event]
    return (event or _MISSING_EVENT)[:EVENT_WIDTH]


def _format_timestamp(raw: str, tz) -> str:
    """Render an ISO-8601-Z string as ``HH:MM:SS`` in ``tz``.

    Returns the sentinel when parsing fails -- a malformed timestamp never
    kills the tail. Trailing ``Z`` is normalised to ``+00:00`` before
    ``datetime.fromisoformat`` because 3.10 rejects the ``Z`` suffix.
    """
    if not raw:
        return _MISSING_TIMESTAMP
    iso = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return _MISSING_TIMESTAMP
    return dt.astimezone(tz).strftime("%H:%M:%S")


def _description(tool_input) -> str:
    """Fallback order: description > command > file_path > stringified input.

    Matches the bash script's jq expression exactly. Returns empty string
    when no usable field is present so the row still renders without a
    dangling description column.

    Issue #133: every run of whitespace in the resolved string -- including
    embedded ``\\n`` / ``\\r\\n`` / ``\\t`` and runs of spaces -- is
    collapsed to a single space then stripped. The previous implementation
    passed the raw string through verbatim, which caused per-event lines
    to break onto a continuation line with no timestamp prefix when an
    agent supplied a multi-line description (e.g.
    ``"# Check ...\\n# The actual command..."``).
    """
    if isinstance(tool_input, dict):
        for key in ("description", "command", "file_path"):
            value = tool_input.get(key)
            if value:
                return _WHITESPACE_RUN_RE.sub(" ", str(value)).strip()
        return _WHITESPACE_RUN_RE.sub(" ", json.dumps(tool_input, sort_keys=True)).strip()
    if tool_input is None:
        return ""
    return _WHITESPACE_RUN_RE.sub(" ", str(tool_input)).strip()


def _stdout_preview(tool_response) -> str:
    """Return the last non-empty line of ``tool_response.stdout``, or ``""``.

    Truncation at ``STDOUT_PREVIEW_MAX`` happens at the call site so tests
    can assert on the full extraction logic separately from width limits.
    """
    if not isinstance(tool_response, dict):
        return ""
    stdout = tool_response.get("stdout", "")
    if not isinstance(stdout, str) or not stdout:
        return ""
    lines = [ln for ln in stdout.splitlines() if ln]
    return lines[-1] if lines else ""


def format_entry(entry: dict, tz, *, color: bool = True) -> str:
    """Format one hook-logs.jsonl record as a single colorized line.

    Parameters
    ----------
    entry:
        The parsed JSON object. Any shape; missing fields produce sentinels
        rather than raising.
    tz:
        ``tzinfo`` for rendering the timestamp. Call ``resolve_timezone``.
    color:
        When ``False`` no ANSI codes are emitted. ``cmd_hook_tail`` sets
        this from the ``NO_COLOR`` env var and the stdout-isatty check.

    Returns
    -------
    str
        A single line (no trailing newline) suitable for ``print``.
    """
    if not isinstance(entry, dict):
        raw = str(entry)[:DESCRIPTION_MAX]
        return _color(f"{_MISSING_TIMESTAMP} ?? {raw}", _ANSI_GRAY, enabled=color)

    ts = _format_timestamp(str(entry.get("timestamp") or ""), tz)
    event = _event_label(str(entry.get("event") or _MISSING_EVENT))

    input_block = entry.get("input")
    if not isinstance(input_block, dict):
        input_block = {}

    agent_raw = input_block.get("agent_type") or _DEFAULT_AGENT
    agent = str(agent_raw).removeprefix("devbench:")
    tool = str(input_block.get("tool_name") or _MISSING_TOOL)

    desc = _description(input_block.get("tool_input"))[:DESCRIPTION_MAX]
    preview = _stdout_preview(input_block.get("tool_response"))[:STDOUT_PREVIEW_MAX]

    parts = [
        _color(ts, _ANSI_GRAY, enabled=color),
        _color(_pad(EVENT_WIDTH, event), _ANSI_CYAN, enabled=color),
        _color(_pad(AGENT_WIDTH, agent), _ANSI_YELLOW, enabled=color),
        _color(_pad(TOOL_WIDTH, tool), _ANSI_GREEN, enabled=color),
        desc,
    ]
    line = " ".join(parts)
    if preview:
        line += _color(f"  |  {preview}", _ANSI_GRAY, enabled=color)
    return line


# ---------------------------------------------------------------------------
# Tail-follow loop
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FollowOptions:
    """Config bundle for ``follow``. Frozen so tests can't accidentally mutate."""

    tz: object  # tzinfo
    from_start: bool = False
    no_follow: bool = False
    color: bool = True
    poll_seconds: float = POLL_SECONDS_DEFAULT
    # Phase 11 (E230): when set, hook-tail emits ONLY events whose
    # ``orchestrator_session`` field equals this value. Enables the
    # orchestrator pane to filter out tool calls fired by side-pane
    # Claude sessions sharing the same DEVBENCH_WORKSPACE_ROOT.
    orchestrator_session_id: str | None = None


def _format_line(raw_line: str, tz, *, color: bool, orchestrator_session_id: str | None = None) -> str:
    """Parse ``raw_line`` as JSON and format it.

    A line that is not valid JSON becomes a sentinel row showing its first
    ``DESCRIPTION_MAX`` characters so the operator can see the bad input
    without having to grep the raw log.

    When *orchestrator_session_id* is provided, lines whose
    ``orchestrator_session`` field does not match are silently
    suppressed (returns ``""``). Lines from older log formats lack
    the field; they are passed through unchanged so historical events
    remain visible.
    """
    stripped = raw_line.rstrip("\n")
    if not stripped.strip():
        return ""
    try:
        entry = json.loads(stripped)
    except json.JSONDecodeError:
        prefix = f"{_MISSING_TIMESTAMP} !? bad-json  "
        body = stripped[:DESCRIPTION_MAX]
        return _color(f"{prefix}{body}", _ANSI_GRAY, enabled=color)
    if orchestrator_session_id is not None:
        recorded = entry.get("orchestrator_session")
        if isinstance(recorded, str) and recorded and recorded != orchestrator_session_id:
            return ""
    return format_entry(entry, tz, color=color)


def follow(path: Path, options: FollowOptions, output: TextIO) -> int:
    """Tail-follow ``path``, writing one formatted line per JSONL record to ``output``.

    Seeks to end-of-file before the first read (``tail -f`` default) unless
    ``from_start=True``. Polls for new content every ``poll_seconds`` when
    the file has no new data. Re-opens when the file's inode changes to
    survive log rotation.

    Exit code semantics:
    - ``0`` for a clean exit (EOF under ``no_follow``, or ``KeyboardInterrupt``).
    - ``1`` for a missing path under ``no_follow`` (no fallback polling).
    """
    if not path.exists() and options.no_follow:
        print(f"hook-tail: file not found: {path}", file=sys.stderr)
        return 1

    try:
        return _follow_loop(path, options, output)
    except KeyboardInterrupt:
        return 0


def _open_at_start_position(path: Path, *, from_start: bool):
    """Open ``path`` for reading and seek per ``from_start`` semantics."""
    handle = path.open("r", encoding="utf-8")
    if not from_start:
        handle.seek(0, os.SEEK_END)
    return handle


def _current_inode(path: Path) -> int | None:
    try:
        return path.stat().st_ino
    except FileNotFoundError:
        return None


def _wait_for_file(path: Path, poll_seconds: float) -> None:
    """Block until ``path`` exists. Used only in follow mode (``no_follow=False``)."""
    while not path.exists():
        time.sleep(poll_seconds)


def _follow_loop(path: Path, options: FollowOptions, output: TextIO) -> int:
    """Main readline / poll / reopen loop. Extracted for clarity."""
    if not path.exists():
        _wait_for_file(path, options.poll_seconds)

    handle = _open_at_start_position(path, from_start=options.from_start)
    inode = _current_inode(path)

    try:
        while True:
            line = handle.readline()
            if line:
                formatted = _format_line(
                    line,
                    options.tz,
                    color=options.color,
                    orchestrator_session_id=options.orchestrator_session_id,
                )
                if formatted:
                    output.write(formatted + "\n")
                    output.flush()
                continue

            # Empty readline -> EOF as of this moment.
            if options.no_follow:
                return 0

            # Check for rotation: a new file at the same path means reopen.
            new_inode = _current_inode(path)
            if new_inode is not None and new_inode != inode:
                handle.close()
                handle = _open_at_start_position(path, from_start=True)
                inode = new_inode
                continue

            time.sleep(options.poll_seconds)
    finally:
        handle.close()


# ---------------------------------------------------------------------------
# Header helpers (used by cli.cmd_hook_tail)
# ---------------------------------------------------------------------------


def render_header(path: Path, tz, *, color: bool = True) -> str:
    """Two-line header describing the source path and the display timezone."""
    iana, abbrev = timezone_display(tz)
    line1 = f"# devbench hook-tail: {path}"
    line2 = f"# timestamps rendered in {iana} ({abbrev}); raw log stores UTC"
    return _color(line1, _ANSI_GRAY, enabled=color) + "\n" + _color(line2, _ANSI_GRAY, enabled=color)


def should_use_color(output: TextIO) -> bool:
    """Return ``True`` when color should be emitted to ``output``.

    Discipline: respect ``NO_COLOR`` per https://no-color.org/, and auto-
    disable color when the output is not a TTY (standard piping behaviour).
    """
    if os.environ.get("NO_COLOR"):
        return False
    isatty = getattr(output, "isatty", None)
    if callable(isatty):
        try:
            return bool(isatty())
        except ValueError:
            return False
    return False
