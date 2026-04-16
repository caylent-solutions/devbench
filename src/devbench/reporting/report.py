"""Backlog progress report generator.

Parses BACKLOG.md and the orchestrator log to produce a formatted progress
report showing velocity, completion stats, time estimates, and token cost.

Two windows are reported by default:

- **All-time** — cumulative across the entire orchestrator log history.
- **Current run** — since the most recent gap of more than
  ``DEFAULT_CURRENT_RUN_GAP_MINUTES`` between consecutive orchestration events
  (``Set X to 'in-progress'`` / ``Set X to 'done'`` log lines), which serves
  as a proxy for "the orchestrator was restarted here." If no such gap
  exists, the current run equals the full log and both windows show the
  same numbers.

When ``generate_report`` is called with an explicit ``since`` timestamp,
only one window is reported, labeled with that timestamp.
"""

from __future__ import annotations

import json as _json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from devbench.backlog.parser import BacklogParser
from devbench.backlog.work_unit import WorkUnitStatus, WorkUnitType
from devbench.config import (
    BACKLOG_INDEX,
    BACKLOG_ROOT,
    REPORT_DISPLAY_TIMEZONE,
    TOKEN_COST_INPUT_RATIO,
    TOKEN_COST_PER_M_INPUT,
    TOKEN_COST_PER_M_OUTPUT,
)
from devbench.constants import (
    DEFAULT_CURRENT_RUN_GAP_MINUTES,
    MS_PER_SECOND,
    PERCENT_MULTIPLIER,
    REPORT_METRIC_COLUMN_WIDTH,
    REPORT_VALUE_COLUMN_WIDTH,
    SECONDS_PER_HOUR,
    SECONDS_PER_MINUTE,
    TOKENS_PER_MILLION,
)

_log = logging.getLogger("devbench.reporting.report")

_DONE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z .* Set (E\S+) to 'done'", re.MULTILINE)
_PROGRESS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z .* Set (E\S+) to 'in-progress'", re.MULTILINE)
_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z", re.MULTILINE)


@dataclass(frozen=True)
class WindowStats:
    """All time-windowed statistics for one reporting window."""

    window_start: datetime
    window_hours: float
    tasks_in_window: int
    avg_minutes: float
    est_hours: float
    total_tokens: int
    est_cost: float
    tokens_per_task: float
    est_total_cost: float
    api_hours: float
    api_efficiency: float | None  # None when window_hours == 0


def _parse_ts(raw: str) -> datetime:
    return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)


def _find_current_run_start(
    progress_times: dict[str, datetime],
    done_times: dict[str, datetime],
    gap_minutes: int = DEFAULT_CURRENT_RUN_GAP_MINUTES,
) -> datetime | None:
    """Return the start of the current orchestrator run, or None if no events exist.

    The current run is defined as the contiguous block of orchestration events at
    the end of the log. Boundary detection: walk events in chronological order; the
    most recent gap of more than ``gap_minutes`` between consecutive events marks
    the start of the current run. If no gap exceeds the threshold, the current run
    equals the full event history (start = earliest event).
    """
    events = sorted(set(progress_times.values()) | set(done_times.values()))
    if not events:
        return None

    threshold = timedelta(minutes=gap_minutes)
    run_start = events[0]
    for prev, curr in pairwise(events):
        if curr - prev > threshold:
            run_start = curr
    return run_start


def _parse_hook_log_metrics(log_path: Path, window_start: datetime) -> tuple[int, int]:
    """Parse token and duration totals from hook-logs.jsonl, filtered by window start.

    Returns:
        Tuple of (total_tokens, total_duration_ms).
    """
    hook_log_path = log_path.parent.parent / "hook-logs.jsonl"
    if not hook_log_path.is_file():
        hook_log_path = BACKLOG_INDEX.parent / "hook-logs.jsonl"

    total_tokens = 0
    total_duration_ms = 0
    if not hook_log_path.is_file():
        return total_tokens, total_duration_ms

    for line in hook_log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        ts_str = entry.get("timestamp", "")
        if ts_str:
            try:
                entry_ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if entry_ts < window_start:
                    continue
            except ValueError:
                pass
        tool_resp = (entry.get("input") or {}).get("tool_response") or {}
        if isinstance(tool_resp, dict):
            tok = tool_resp.get("totalTokens")
            if isinstance(tok, int):
                total_tokens += tok
            dur = tool_resp.get("totalDurationMs")
            if isinstance(dur, int):
                total_duration_ms += dur

    return total_tokens, total_duration_ms


def _compute_window_stats(
    log_path: Path,
    window_start: datetime,
    window_end: datetime,
    done_times: dict[str, datetime],
    progress_times: dict[str, datetime],
    tasks_remaining: int,
) -> WindowStats:
    """Compute all time-windowed statistics for a single window."""
    window_hours = (window_end - window_start).total_seconds() / SECONDS_PER_HOUR

    task_ids_done = {uid for uid, ts in done_times.items() if "-T" in uid and window_start <= ts <= window_end}
    tasks_in_window = len(task_ids_done)

    task_durations: list[float] = []
    for tid in task_ids_done:
        if tid in progress_times:
            effective_start = max(progress_times[tid], window_start)
            dur = (done_times[tid] - effective_start).total_seconds() / SECONDS_PER_MINUTE
            if dur > 0:
                task_durations.append(dur)
    avg_minutes = sum(task_durations) / len(task_durations) if task_durations else 0.0

    est_hours = (tasks_remaining * avg_minutes) / SECONDS_PER_MINUTE if avg_minutes else 0.0

    total_tokens, total_duration_ms = _parse_hook_log_metrics(log_path, window_start)

    blended_per_m = TOKEN_COST_PER_M_INPUT * TOKEN_COST_INPUT_RATIO + TOKEN_COST_PER_M_OUTPUT * (
        1.0 - TOKEN_COST_INPUT_RATIO
    )
    total_tokens_m = total_tokens / TOKENS_PER_MILLION
    est_cost = total_tokens_m * blended_per_m
    tokens_per_task = total_tokens / tasks_in_window if tasks_in_window else 0.0
    est_total_cost = (
        est_cost + (tokens_per_task * tasks_remaining / TOKENS_PER_MILLION * blended_per_m) if tokens_per_task else 0.0
    )
    api_hours = total_duration_ms / MS_PER_SECOND / SECONDS_PER_HOUR
    api_efficiency = (api_hours / window_hours * PERCENT_MULTIPLIER) if window_hours > 0 else None

    return WindowStats(
        window_start=window_start,
        window_hours=window_hours,
        tasks_in_window=tasks_in_window,
        avg_minutes=avg_minutes,
        est_hours=est_hours,
        total_tokens=total_tokens,
        est_cost=est_cost,
        tokens_per_task=tokens_per_task,
        est_total_cost=est_total_cost,
        api_hours=api_hours,
        api_efficiency=api_efficiency,
    )


def _render_table(title: str, rows: list[tuple[str, str]]) -> list[str]:
    """Render a single bordered table with a title row, metric column, and value column."""
    metric_w = REPORT_METRIC_COLUMN_WIDTH
    value_w = REPORT_VALUE_COLUMN_WIDTH
    border_top = "\u250c" + "\u2500" * (metric_w + 2) + "\u252c" + "\u2500" * (value_w + 2) + "\u2510"
    border_mid = "\u251c" + "\u2500" * (metric_w + 2) + "\u253c" + "\u2500" * (value_w + 2) + "\u2524"
    border_bot = "\u2514" + "\u2500" * (metric_w + 2) + "\u2534" + "\u2500" * (value_w + 2) + "\u2518"

    lines: list[str] = [border_top, f"\u2502 {title:<{metric_w}} \u2502 {'Value':>{value_w}} \u2502", border_mid]
    for i, (metric, value) in enumerate(rows):
        if i > 0:
            lines.append(border_mid)
        lines.append(f"\u2502 {metric:<{metric_w}} \u2502 {value:>{value_w}} \u2502")
    lines.append(border_bot)
    return lines


def _stats_to_rows(stats: WindowStats) -> list[tuple[str, str]]:
    """Convert a WindowStats into displayable (metric, value) rows."""
    api_eff_display = f"{stats.api_efficiency:.1f}%" if stats.api_efficiency is not None else "n/a"
    tokens_per_task_display = f"{stats.tokens_per_task:,.0f}" if stats.tokens_per_task else "n/a"
    est_total_cost_display = f"~${stats.est_total_cost:.2f}" if stats.est_total_cost else "n/a"
    return [
        ("Time span", f"{stats.window_hours:.1f} hours"),
        ("Tasks completed in window", str(stats.tasks_in_window)),
        ("Average time per task", f"{stats.avg_minutes:.1f} minutes"),
        ("Estimated time to complete remaining", f"~{stats.est_hours:.1f} hours"),
        ("API processing time", f"{stats.api_hours:.1f} hours"),
        ("API utilization (API time / wall time)", api_eff_display),
        ("Tokens consumed", f"{stats.total_tokens:,}"),
        ("Estimated cost so far", f"~${stats.est_cost:.2f}"),
        ("Avg tokens per task", tokens_per_task_display),
        ("Estimated total cost at completion", est_total_cost_display),
    ]


def _resolve_display_timezone(tz_name: str | None) -> tzinfo | None:
    """Return a tzinfo for the configured display timezone, or None for system local.

    Returns None when ``tz_name`` is None (caller should use ``astimezone()`` with
    no argument, which honors the host's system local timezone). Falls back to
    None and logs a warning if the supplied name is not a valid IANA zone, so a
    typo in config does not break the report.
    """
    if tz_name is None:
        return None
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        _log.warning(
            "report.display_timezone=%r is not a valid IANA timezone name; falling back to host system local timezone.",
            tz_name,
        )
        return None


def _format_local_timestamp(dt: datetime, display_tz: tzinfo | None = None) -> str:
    """Format a tz-aware datetime for display, with TZ abbreviation.

    Conversion target:

    - When ``display_tz`` is provided, convert to that explicit IANA timezone.
    - Otherwise, convert to the host's system local timezone.

    Internal computation in this module is always in UTC; only display strings
    are converted so the report shows times the user can read without doing
    UTC math.
    """
    converted = dt.astimezone(display_tz) if display_tz is not None else dt.astimezone()
    return converted.strftime("%Y-%m-%d %H:%M %Z")


def _format_window_title(
    window_label: str,
    window_start: datetime,
    log_started: datetime | None,
    display_tz: tzinfo | None = None,
) -> str:
    """Return a window-table title showing the window label and its start timestamp.

    Timestamps are rendered in ``display_tz`` (when provided) or the host's
    system local timezone (when ``display_tz`` is None).
    """
    formatted = _format_local_timestamp(window_start, display_tz)
    if log_started is not None and window_start == log_started:
        return f"{window_label} (since log started: {formatted})"
    return f"{window_label} (since {formatted})"


def _resolve_window_endpoints(
    log_timestamps: list[datetime],
) -> tuple[datetime, datetime]:
    """Return (log_start, window_end) for an empty or non-empty log.

    For an empty log, both fall back to ``datetime.now(UTC)`` so a sensible
    zero-valued report still renders. The caller treats them as window
    boundaries; downstream filtering yields zero entries either way.
    """
    if log_timestamps:
        return min(log_timestamps), max(log_timestamps)
    now = datetime.now(UTC)
    return now, now


def generate_report(log_path: Path, since: datetime | None = None) -> str:
    """Generate a formatted progress report.

    Args:
        log_path: Path to the orchestrator log file.
        since: If provided, render a single window starting at this timestamp.
            If omitted, render two windows: all-time (since the log started)
            and current run (since the most recent gap in orchestration events).

    Returns:
        Formatted report string ready for terminal output.
    """
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()

    tasks = [u for u in units if u.unit_type == WorkUnitType.TASK]
    stories = [u for u in units if u.unit_type == WorkUnitType.STORY]
    features = [u for u in units if u.unit_type == WorkUnitType.FEATURE]
    epics = [u for u in units if u.unit_type == WorkUnitType.EPIC]

    tasks_done = [t for t in tasks if t.status == WorkUnitStatus.DONE]
    stories_done = [s for s in stories if s.status == WorkUnitStatus.DONE]
    features_done = [f for f in features if f.status == WorkUnitStatus.DONE]
    epics_done = [e for e in epics if e.status == WorkUnitStatus.DONE]
    all_done = [u for u in units if u.status == WorkUnitStatus.DONE]
    tasks_remaining = len(tasks) - len(tasks_done)

    # Parse log for timing data
    log_text = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""

    done_times: dict[str, datetime] = {}
    for m in _DONE_RE.finditer(log_text):
        done_times[m.group(2)] = _parse_ts(m.group(1))

    progress_times: dict[str, datetime] = {}
    for m in _PROGRESS_RE.finditer(log_text):
        progress_times[m.group(2)] = _parse_ts(m.group(1))

    all_timestamps: list[datetime] = [_parse_ts(m.group(1)) for m in _TIMESTAMP_RE.finditer(log_text)]
    log_started = min(all_timestamps) if all_timestamps else None
    log_start_for_window, window_end = _resolve_window_endpoints(all_timestamps)

    # Backlog state block (single, time-independent)
    task_pct = round(PERCENT_MULTIPLIER * len(tasks_done) / len(tasks)) if tasks else 0
    total_pct = round(PERCENT_MULTIPLIER * len(all_done) / len(units)) if units else 0
    backlog_rows: list[tuple[str, str]] = [
        ("Tasks completed", f"{len(tasks_done)} of {len(tasks)} ({task_pct}%)"),
        (
            "Total work units done (tasks + auto-rolled stories/features)",
            f"{len(all_done)} of {len(units)} ({total_pct}%)",
        ),
        ("Tasks remaining", str(tasks_remaining)),
        ("Stories auto-rolled to done", str(len(stories_done))),
        ("Features auto-rolled to done", str(len(features_done))),
        ("Epics auto-rolled to done", str(len(epics_done))),
    ]

    lines: list[str] = []
    lines.extend(_render_table("Backlog state", backlog_rows))

    blended_per_m = TOKEN_COST_PER_M_INPUT * TOKEN_COST_INPUT_RATIO + TOKEN_COST_PER_M_OUTPUT * (
        1.0 - TOKEN_COST_INPUT_RATIO
    )

    display_tz = _resolve_display_timezone(REPORT_DISPLAY_TIMEZONE)

    if since is not None:
        # Single-window mode: caller asked for a specific time window.
        single_stats = _compute_window_stats(log_path, since, window_end, done_times, progress_times, tasks_remaining)
        lines.append("")
        lines.extend(
            _render_table(
                _format_window_title("Window", since, log_started, display_tz),
                _stats_to_rows(single_stats),
            )
        )
        # Backward-compat label so callers grepping for "Tasks in this session" still find it.
        lines.append(f"\nTasks in this session: {single_stats.tasks_in_window}")
        summary_stats = single_stats
    else:
        # Default: render both All-time and Current-run windows.
        all_time_stats = _compute_window_stats(
            log_path, log_start_for_window, window_end, done_times, progress_times, tasks_remaining
        )
        lines.append("")
        lines.extend(
            _render_table(
                _format_window_title("All-time", log_start_for_window, log_started, display_tz),
                _stats_to_rows(all_time_stats),
            )
        )

        detected_run_start = _find_current_run_start(progress_times, done_times)
        current_run_start = detected_run_start if detected_run_start is not None else log_start_for_window
        current_run_stats = _compute_window_stats(
            log_path, current_run_start, window_end, done_times, progress_times, tasks_remaining
        )
        lines.append("")
        lines.extend(
            _render_table(
                _format_window_title("Current run", current_run_start, log_started, display_tz),
                _stats_to_rows(current_run_stats),
            )
        )
        # Use current-run stats for the trailing prose summary (more relevant to "now").
        summary_stats = current_run_stats

    lines.append("")
    lines.append(
        f"At the current pace of ~{summary_stats.avg_minutes:.1f} minutes per task, "
        f"the remaining {tasks_remaining} tasks should take roughly "
        f"{summary_stats.est_hours:.1f} more hours of continuous execution."
    )
    if summary_stats.est_total_cost:
        lines.append(
            f"Token cost estimate uses blended rate "
            f"(${blended_per_m:.0f}/M tokens, "
            f"{TOKEN_COST_INPUT_RATIO:.0%} input @ ${TOKEN_COST_PER_M_INPUT:.0f}/M, "
            f"{1.0 - TOKEN_COST_INPUT_RATIO:.0%} output @ ${TOKEN_COST_PER_M_OUTPUT:.0f}/M). "
            f"Override in devbench.yaml under report:."
        )
    if since is None:
        lines.append(
            f"\nWindows: the upper table covers the full orchestrator log; the lower table "
            f"covers the most recent contiguous block of orchestration events (boundary = a gap "
            f"of more than {DEFAULT_CURRENT_RUN_GAP_MINUTES} minutes between consecutive "
            f"'Set X to ...' log lines). Pass --since <ISO-8601> to report a custom window."
        )

    return "\n".join(lines)
