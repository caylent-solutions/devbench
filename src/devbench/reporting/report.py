"""Backlog progress report generator.

Parses BACKLOG.md and the orchestrator log to produce a formatted progress
report showing velocity, completion stats, time estimates, and token cost.

Output structure:

- **Backlog state** — single small table with time-independent counts
  (tasks completed, total work units done, tasks remaining, rollups).
- **Window stats** — single multi-column table with one metric column and
  one column per window. Default windows:

  - **All-time** — cumulative across the entire orchestrator log history.
  - **Current session** — since the most recent gap of more than
    ``DEFAULT_SESSION_GAP_MINUTES`` between consecutive log entries (a
    proxy for "the orchestrator was restarted here"). Filters out noise
    from the ``judges.log_setup`` logger that fires on every CLI tick.
  - **This run** (watch mode only) — since the report watch loop began.

When ``generate_report`` is called with an explicit ``since`` timestamp,
only one window is reported, labeled with that timestamp.

Cost calculation uses the per-token-type breakdown from
``hook-logs.jsonl`` (``usage.input_tokens``, ``output_tokens``,
``cache_read_input_tokens``, ``cache_creation``) and applies Anthropic's
published multipliers for cache reads and writes. Multipliers are
overridable via ``report.cache_*_multiplier`` in ``devbench.yaml`` for
users on Bedrock or other platforms with different pricing.
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
    REPORT_CACHE_READ_MULTIPLIER,
    REPORT_CACHE_WRITE_1HR_MULTIPLIER,
    REPORT_CACHE_WRITE_5MIN_MULTIPLIER,
    REPORT_DISPLAY_TIMEZONE,
    TOKEN_COST_PER_M_INPUT,
    TOKEN_COST_PER_M_OUTPUT,
)
from devbench.constants import (
    DEFAULT_SESSION_GAP_MINUTES,
    LOG_NOISE_LOGGER_NAME,
    MS_PER_SECOND,
    PERCENT_MULTIPLIER,
    SECONDS_PER_HOUR,
    SECONDS_PER_MINUTE,
    TOKENS_PER_MILLION,
)

_log = logging.getLogger("devbench.reporting.report")

# Match a log line of the form "YYYY-MM-DDTHH:MM:SSZ [logger.name] LEVEL ...",
# capturing the ISO-8601 timestamp (group 1) and the logger name (group 2).
_LOG_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z \[([^\]]+)\]", re.MULTILINE)
_DONE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z .* Set (E\S+) to 'done'", re.MULTILINE)
_PROGRESS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z .* Set (E\S+) to 'in-progress'", re.MULTILINE)


@dataclass(frozen=True)
class HookLogTotals:
    """Aggregated per-token-type totals from one or more hook-log entries.

    Cost is computed exclusively from these per-token-type counters via
    ``_compute_cost``. There is no blended-rate fallback: an LLM call that
    lacks a usage breakdown in the source log is excluded from cost (its
    duration is still counted in ``total_duration_ms`` for API-utilization
    metrics). This is the fail-fast posture — missing cost data surfaces as
    visibly-low cost rather than silently-masked blended estimates.
    """

    total_duration_ms: int = 0
    input_tokens: int = 0  # uncached input
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    entries_with_usage: int = 0
    entries_us_geo: int = 0  # counted for display; per-call multipliers deferred
    entries_fast_mode: int = 0


@dataclass(frozen=True)
class CostBreakdown:
    """Cost subtotals by token type and the rolled-up total."""

    input_cost: float
    output_cost: float
    cache_read_cost: float
    cache_write_5m_cost: float
    cache_write_1h_cost: float
    total_cost: float


@dataclass(frozen=True)
class WindowStats:
    """All time-windowed statistics for one reporting window."""

    window_start: datetime
    window_hours: float
    tasks_in_window: int
    avg_minutes: float
    est_hours: float
    totals: HookLogTotals
    cost: CostBreakdown
    cache_hit_rate: float | None  # None when no input/cache_read tokens
    tokens_per_task: float
    est_total_cost: float
    api_hours: float
    api_efficiency: float | None  # None when window_hours == 0

    @property
    def total_tokens(self) -> int:
        """Sum of every token type seen in this window (matches Claude Code's totalTokens)."""
        t = self.totals
        return (
            t.input_tokens + t.output_tokens + t.cache_read_tokens + t.cache_write_5m_tokens + t.cache_write_1h_tokens
        )

    @property
    def input_share(self) -> float | None:
        """Measured input/output ratio: input-side tokens / total tokens.

        "Input-side" includes uncached input plus cache reads plus cache writes
        (all charged at variants of the input rate). Purely descriptive:
        displayed in the report to show what the actual workload ratio is.
        Cost is computed per-call per-token-type from real ``usage`` data;
        this ratio is never used as a cost input. Returns ``None`` when the
        window has no tokens.
        """
        total = self.total_tokens
        if total == 0:
            return None
        t = self.totals
        input_side = t.input_tokens + t.cache_read_tokens + t.cache_write_5m_tokens + t.cache_write_1h_tokens
        return input_side / total


@dataclass(frozen=True)
class WindowSpec:
    """One window to render in the multi-column window-stats table."""

    label: str  # short column header, e.g. "All-time"
    start: datetime
    is_log_started: bool  # True if this window's start equals log_started (controls title)


def _parse_ts(raw: str) -> datetime:
    return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)


def _find_current_session_start(
    log_text: str,
    gap_minutes: int = DEFAULT_SESSION_GAP_MINUTES,
) -> datetime | None:
    """Return the start of the current orchestrator session, or None if log is empty.

    Walks all log entries except those from the noise logger
    (``LOG_NOISE_LOGGER_NAME``, which fires on every CLI invocation including
    every ``devbench report --watch`` tick). The most recent gap of more than
    ``gap_minutes`` between consecutive non-noise entries marks the start of
    the current session. If no gap exceeds the threshold, the session start
    is the earliest non-noise entry.
    """
    events: list[datetime] = []
    for m in _LOG_LINE_RE.finditer(log_text):
        logger_name = m.group(2)
        if logger_name == LOG_NOISE_LOGGER_NAME:
            continue
        events.append(_parse_ts(m.group(1)))

    if not events:
        return None

    threshold = timedelta(minutes=gap_minutes)
    session_start = events[0]
    for prev, curr in pairwise(events):
        if curr - prev > threshold:
            session_start = curr
    return session_start


def _empty_totals_acc() -> dict[str, int]:
    """Return a zero-initialized accumulator matching ``HookLogTotals`` fields.

    Centralized so hook-log and transcript parsers share the same shape and
    a new field only needs adding in one place.
    """
    return {
        "total_duration_ms": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_5m_tokens": 0,
        "cache_write_1h_tokens": 0,
        "entries_with_usage": 0,
        "entries_us_geo": 0,
        "entries_fast_mode": 0,
    }


def _extract_usage_totals(usage: object, totals_acc: dict[str, int]) -> bool:
    """Read per-token-type counts from a usage dict into the totals accumulator.

    Returns True if usage contained recognizable counts (non-empty entry).
    The accumulator is mutated in place; caller wraps it into ``HookLogTotals``.
    Accepts ``object`` so callers can pass possibly-None values from JSON parsing
    without an extra isinstance check at every call site.
    """
    if not isinstance(usage, dict):
        return False

    totals_acc["input_tokens"] += int(usage.get("input_tokens") or 0)
    totals_acc["output_tokens"] += int(usage.get("output_tokens") or 0)
    totals_acc["cache_read_tokens"] += int(usage.get("cache_read_input_tokens") or 0)

    cc = usage.get("cache_creation")
    if isinstance(cc, dict):
        totals_acc["cache_write_5m_tokens"] += int(cc.get("ephemeral_5m_input_tokens") or 0)
        totals_acc["cache_write_1h_tokens"] += int(cc.get("ephemeral_1h_input_tokens") or 0)
    else:
        # Older format / fallback: all cache writes counted as 5-min.
        totals_acc["cache_write_5m_tokens"] += int(usage.get("cache_creation_input_tokens") or 0)

    if usage.get("inference_geo"):
        totals_acc["entries_us_geo"] += 1
    if usage.get("speed") == "fast":
        totals_acc["entries_fast_mode"] += 1

    return True


def _hook_log_path(log_path: Path) -> Path:
    """Locate hook-logs.jsonl relative to the orchestrator log path or backlog index."""
    candidate = log_path.parent.parent / "hook-logs.jsonl"
    if candidate.is_file():
        return candidate
    return BACKLOG_INDEX.parent / "hook-logs.jsonl"


def _discover_transcript_dir(hook_log_path: Path) -> Path | None:
    """Return the Claude Code transcript directory by reading the first usable hook entry.

    Each PostToolUse hook entry carries ``input.transcript_path`` pointing to
    the current session's ``~/.claude/projects/<slug>/<session-id>.jsonl`` file.
    The parent directory holds all session transcripts for the same workspace.
    """
    if not hook_log_path.is_file():
        return None
    for line in hook_log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        path_str = (entry.get("input") or {}).get("transcript_path")
        if isinstance(path_str, str) and path_str:
            return Path(path_str).parent
    return None


def _accumulate_transcript_message(message: object, totals_acc: dict[str, int]) -> None:
    """Fold one transcript message's usage into the totals accumulator."""
    if not isinstance(message, dict):
        return
    usage = message.get("usage")
    if _extract_usage_totals(usage, totals_acc):
        totals_acc["entries_with_usage"] += 1


def _parse_transcript_metrics(transcript_dir: Path | None, window_start: datetime) -> HookLogTotals:
    """Aggregate per-token-type usage from Claude Code transcript files.

    Each transcript line is a JSON message. Lines with role=assistant carry a
    ``message.usage`` dict whose shape matches ``hook-logs.jsonl`` usage. We
    walk every transcript file in ``transcript_dir`` and sum filtered by
    ``window_start``. This captures the OUTER orchestrator session's per-turn
    LLM cost, which hook-logs.jsonl misses (hook-logs only captures Agent
    subagent invocations).
    """
    totals_acc: dict[str, int] = _empty_totals_acc()
    if transcript_dir is None or not transcript_dir.is_dir():
        return HookLogTotals(**totals_acc)

    for transcript_file in sorted(transcript_dir.glob("*.jsonl")):
        for line in transcript_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = _json.loads(line)
            except _json.JSONDecodeError:
                continue
            if not _entry_in_window(entry, window_start):
                continue
            _accumulate_transcript_message(entry.get("message"), totals_acc)

    return HookLogTotals(**totals_acc)


def _combine_totals(a: HookLogTotals, b: HookLogTotals) -> HookLogTotals:
    """Sum two HookLogTotals field-by-field. Used to merge hook-log + transcript usage."""
    return HookLogTotals(
        total_duration_ms=a.total_duration_ms + b.total_duration_ms,
        input_tokens=a.input_tokens + b.input_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
        cache_read_tokens=a.cache_read_tokens + b.cache_read_tokens,
        cache_write_5m_tokens=a.cache_write_5m_tokens + b.cache_write_5m_tokens,
        cache_write_1h_tokens=a.cache_write_1h_tokens + b.cache_write_1h_tokens,
        entries_with_usage=a.entries_with_usage + b.entries_with_usage,
        entries_us_geo=a.entries_us_geo + b.entries_us_geo,
        entries_fast_mode=a.entries_fast_mode + b.entries_fast_mode,
    )


def _entry_in_window(entry: dict, window_start: datetime) -> bool:
    """Return True if the hook-log entry's timestamp is within the window (or unparseable)."""
    ts_str = entry.get("timestamp", "")
    if not ts_str:
        return True
    try:
        entry_ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except ValueError:
        return True
    return entry_ts >= window_start


def _accumulate_entry(entry: dict, totals_acc: dict[str, int]) -> None:
    """Fold one hook-log entry's metrics into the running totals accumulator.

    Entries without a ``usage`` block contribute duration only (for
    API-utilization accounting). Their token counts are skipped — there is
    no blended-rate fallback.
    """
    tool_resp = (entry.get("input") or {}).get("tool_response") or {}
    if not isinstance(tool_resp, dict):
        return
    dur = tool_resp.get("totalDurationMs")
    if isinstance(dur, int):
        totals_acc["total_duration_ms"] += dur

    if _extract_usage_totals(tool_resp.get("usage"), totals_acc):
        totals_acc["entries_with_usage"] += 1


def _parse_hook_log_metrics(log_path: Path, window_start: datetime) -> HookLogTotals:
    """Aggregate per-token-type usage from hook-logs.jsonl, filtered by window start."""
    totals_acc: dict[str, int] = _empty_totals_acc()

    hook_log_path = _hook_log_path(log_path)
    if not hook_log_path.is_file():
        return HookLogTotals(**totals_acc)

    for line in hook_log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        if not _entry_in_window(entry, window_start):
            continue
        _accumulate_entry(entry, totals_acc)

    return HookLogTotals(**totals_acc)


def _compute_cost(
    totals: HookLogTotals,
    input_rate: float,
    output_rate: float,
    cache_read_mult: float,
    cache_5m_mult: float,
    cache_1h_mult: float,
) -> CostBreakdown:
    """Compute per-token-type cost subtotals and the rolled-up total. Pure function.

    Cost is always computed from real per-token-type counts. No blended rate,
    no estimated input/output ratio — if an LLM call didn't record ``usage``,
    it contributes zero cost (and an audit-visible gap in ``entries_with_usage``).
    """
    input_cost = totals.input_tokens * input_rate / TOKENS_PER_MILLION
    output_cost = totals.output_tokens * output_rate / TOKENS_PER_MILLION
    cache_read_cost = totals.cache_read_tokens * input_rate * cache_read_mult / TOKENS_PER_MILLION
    cache_write_5m_cost = totals.cache_write_5m_tokens * input_rate * cache_5m_mult / TOKENS_PER_MILLION
    cache_write_1h_cost = totals.cache_write_1h_tokens * input_rate * cache_1h_mult / TOKENS_PER_MILLION

    total = input_cost + output_cost + cache_read_cost + cache_write_5m_cost + cache_write_1h_cost
    return CostBreakdown(
        input_cost=input_cost,
        output_cost=output_cost,
        cache_read_cost=cache_read_cost,
        cache_write_5m_cost=cache_write_5m_cost,
        cache_write_1h_cost=cache_write_1h_cost,
        total_cost=total,
    )


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

    # Combine usage from two sources, both filtered by window_start:
    #   1. hook-logs.jsonl: subagent (Agent tool) invocations — captures executor / judge / etc costs
    #   2. Claude Code transcripts: per-turn outer-session reasoning — captures what the orchestrate
    #      skill itself spends between Agent calls. Without these, cost can be off by 10-20x.
    hook_log_path = _hook_log_path(log_path)
    totals_hook = _parse_hook_log_metrics(log_path, window_start)
    transcript_dir = _discover_transcript_dir(hook_log_path)
    totals_transcript = _parse_transcript_metrics(transcript_dir, window_start)
    totals = _combine_totals(totals_hook, totals_transcript)
    cost = _compute_cost(
        totals,
        TOKEN_COST_PER_M_INPUT,
        TOKEN_COST_PER_M_OUTPUT,
        REPORT_CACHE_READ_MULTIPLIER,
        REPORT_CACHE_WRITE_5MIN_MULTIPLIER,
        REPORT_CACHE_WRITE_1HR_MULTIPLIER,
    )

    # Cache hit rate: cache reads / (cache reads + uncached input). Output and
    # cache writes are not "input" in the hit-rate sense.
    input_total = totals.cache_read_tokens + totals.input_tokens
    cache_hit_rate = (totals.cache_read_tokens / input_total * PERCENT_MULTIPLIER) if input_total > 0 else None

    total_tokens_window = (
        totals.input_tokens
        + totals.output_tokens
        + totals.cache_read_tokens
        + totals.cache_write_5m_tokens
        + totals.cache_write_1h_tokens
    )
    tokens_per_task = total_tokens_window / tasks_in_window if tasks_in_window else 0.0
    est_total_cost = cost.total_cost + (cost.total_cost / tasks_in_window * tasks_remaining if tasks_in_window else 0.0)

    api_hours = totals.total_duration_ms / MS_PER_SECOND / SECONDS_PER_HOUR
    api_efficiency = (api_hours / window_hours * PERCENT_MULTIPLIER) if window_hours > 0 else None

    return WindowStats(
        window_start=window_start,
        window_hours=window_hours,
        tasks_in_window=tasks_in_window,
        avg_minutes=avg_minutes,
        est_hours=est_hours,
        totals=totals,
        cost=cost,
        cache_hit_rate=cache_hit_rate,
        tokens_per_task=tokens_per_task,
        est_total_cost=est_total_cost,
        api_hours=api_hours,
        api_efficiency=api_efficiency,
    )


# --------------------------------------------------------------------------
# Display helpers
# --------------------------------------------------------------------------


def _resolve_display_timezone(tz_name: str | None) -> tzinfo | None:
    """Return a tzinfo for the configured display timezone, or None for system local."""
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
    """Format a tz-aware datetime for display, with TZ abbreviation."""
    converted = dt.astimezone(display_tz) if display_tz is not None else dt.astimezone()
    return converted.strftime("%Y-%m-%d %H:%M %Z")


def _render_table(title: str, rows: list[tuple[str, str]], value_w: int = 18) -> list[str]:
    """Render a single bordered two-column table (metric | value).

    The metric column auto-sizes to the longest label (or the title), so future
    label additions never overflow and break alignment.
    """
    metric_w = max((len(metric) for metric, _ in rows), default=0)
    metric_w = max(metric_w, len(title))
    value_w = max(value_w, max((len(v) for _, v in rows), default=0), len("Value"))

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


def _render_multi_column_table(
    title: str,
    column_labels: list[str],
    rows: list[tuple[str, list[str]]],
    value_w: int = 16,
) -> list[str]:
    """Render a bordered table with one metric column and N value columns.

    ``column_labels`` is the header row for the value columns.
    ``rows`` is a list of (metric, [value_per_column]). Each row's value list
    must match ``column_labels`` in length.

    The metric column auto-sizes to the longest label (or the title), and each
    value column widens to the longest value (or its label) so future label
    additions never overflow and break alignment.
    """
    n_cols = len(column_labels)
    metric_w = max((len(metric) for metric, _ in rows), default=0)
    metric_w = max(metric_w, len(title))
    # Value column width = max of: default minimum, label width, max cell width across all rows.
    max_cell = max((max((len(v) for v in vals), default=0) for _, vals in rows), default=0)
    max_label = max((len(label) for label in column_labels), default=0)
    value_w = max(value_w, max_cell, max_label)

    def hborder(left: str, junction_metric: str, junction_inner: str, right: str) -> str:
        return (
            left
            + "\u2500" * (metric_w + 2)
            + junction_metric
            + (("\u2500" * (value_w + 2) + junction_inner) * (n_cols - 1))
            + "\u2500" * (value_w + 2)
            + right
        )

    border_top = hborder("\u250c", "\u252c", "\u252c", "\u2510")
    border_mid = hborder("\u251c", "\u253c", "\u253c", "\u2524")
    border_bot = hborder("\u2514", "\u2534", "\u2534", "\u2518")

    header_cells = [f" {title:<{metric_w}} "] + [f" {label:>{value_w}} " for label in column_labels]
    header_line = "\u2502" + "\u2502".join(header_cells) + "\u2502"

    lines: list[str] = [border_top, header_line, border_mid]
    for i, (metric, values) in enumerate(rows):
        if i > 0:
            lines.append(border_mid)
        cells = [f" {metric:<{metric_w}} "] + [f" {v:>{value_w}} " for v in values]
        lines.append("\u2502" + "\u2502".join(cells) + "\u2502")
    lines.append(border_bot)
    return lines


def _format_int(n: int) -> str:
    return f"{n:,}"


def _format_window_title(label: str, start: datetime, log_started: datetime | None, display_tz: tzinfo | None) -> str:
    """Format the title shown above a window-stats column for `--since` mode."""
    formatted = _format_local_timestamp(start, display_tz)
    if log_started is not None and start == log_started:
        return f"{label} (since log started: {formatted})"
    return f"{label} (since {formatted})"


def _short_window_label(label: str, start: datetime, display_tz: tzinfo | None) -> str:
    """Compact column header for the multi-column layout (no 'since' prefix)."""
    converted = start.astimezone(display_tz) if display_tz is not None else start.astimezone()
    return f"{label} {converted.strftime('%m-%d %H:%M')}"


def _stats_to_value_list(stats: WindowStats) -> list[str]:
    """Return the per-row value list for a single window, in display order matching METRIC_LABELS."""
    api_eff = f"{stats.api_efficiency:.1f}%" if stats.api_efficiency is not None else "n/a"
    cache_hit = f"{stats.cache_hit_rate:.2f}%" if stats.cache_hit_rate is not None else "n/a"
    tokens_per_task = f"{stats.tokens_per_task:,.0f}" if stats.tokens_per_task else "n/a"
    est_total = f"~${stats.est_total_cost:.2f}" if stats.est_total_cost else "n/a"
    # Per-task averages are meaningful only when at least one task completed in the window;
    # otherwise the rate is undefined and the projection is meaningless. Show "n/a" rather
    # than misleading "0.0 min" / "~0.0 h" rows.
    avg_min_display = f"{stats.avg_minutes:.1f} min" if stats.avg_minutes else "n/a"
    est_hours_display = f"~{stats.est_hours:.1f} h" if stats.avg_minutes else "n/a"
    if stats.input_share is None:
        input_share = "n/a"
    else:
        in_pct = stats.input_share * PERCENT_MULTIPLIER
        out_pct = (1 - stats.input_share) * PERCENT_MULTIPLIER
        input_share = f"{in_pct:.1f}% / {out_pct:.1f}%"
    return [
        f"{stats.window_hours:.1f} h",
        str(stats.tasks_in_window),
        avg_min_display,
        est_hours_display,
        f"{stats.api_hours:.1f} h",
        api_eff,
        _format_int(stats.total_tokens),
        _format_int(stats.totals.input_tokens),
        _format_int(stats.totals.cache_read_tokens),
        _format_int(stats.totals.cache_write_5m_tokens),
        _format_int(stats.totals.cache_write_1h_tokens),
        _format_int(stats.totals.output_tokens),
        input_share,
        cache_hit,
        f"~${stats.cost.total_cost:.2f}",
        tokens_per_task,
        est_total,
    ]


# Order MUST match _stats_to_value_list above.
_METRIC_LABELS: list[str] = [
    "Time span",
    "Tasks completed in window",
    "Average time per task",
    "Est. time to complete remaining",
    "API processing time",
    "API utilization",
    "Tokens consumed",
    "  ├─ input (uncached)",
    "  ├─ cache reads",
    "  ├─ cache writes 5-min",
    "  ├─ cache writes 1-hour",
    "  └─ output",
    "Input / output share (measured)",
    "Cache hit rate",
    "Estimated cost so far",
    "Avg tokens per task",
    "Estimated total cost at completion",
]


def _stats_to_rows_single(stats: WindowStats) -> list[tuple[str, str]]:
    """Convert one WindowStats into (metric, value) rows for the single-column table."""
    values = _stats_to_value_list(stats)
    return list(zip(_METRIC_LABELS, values, strict=True))


def _resolve_window_endpoints(log_timestamps: list[datetime]) -> tuple[datetime, datetime, datetime | None]:
    """Return (log_start_for_window, window_end, log_started) for an empty or non-empty log.

    For an empty log, both window endpoints fall back to ``datetime.now(UTC)`` and
    ``log_started`` is None.
    """
    if log_timestamps:
        log_started = min(log_timestamps)
        return log_started, max(log_timestamps), log_started
    now = datetime.now(UTC)
    return now, now, None


def _summary_line(stats: WindowStats, tasks_remaining: int) -> str:
    """Trailing one-line completion projection.

    The estimate uses the All-time average (most stable sample); narrower windows
    can have zero completed tasks (e.g. just after a restart), which would give a
    meaningless projection.
    """
    if not stats.avg_minutes:
        return (
            f"{tasks_remaining} tasks remaining. "
            "(Not enough completed tasks in the All-time window for a pace estimate.)"
        )
    return (
        f"At the All-time pace of ~{stats.avg_minutes:.1f} minutes per task, "
        f"the remaining {tasks_remaining} tasks should take roughly "
        f"{stats.est_hours:.1f} more hours of continuous execution."
    )


def _cost_basis_line() -> str:
    return (
        f"Token cost uses Anthropic's per-token-type pricing: input @ ${TOKEN_COST_PER_M_INPUT:.0f}/M, "
        f"output @ ${TOKEN_COST_PER_M_OUTPUT:.0f}/M, cache reads @ {REPORT_CACHE_READ_MULTIPLIER:.0%} of input, "
        f"5-min cache writes @ {REPORT_CACHE_WRITE_5MIN_MULTIPLIER:.0%}, "
        f"1-hr cache writes @ {REPORT_CACHE_WRITE_1HR_MULTIPLIER:.0%}. "
        f"Override per-rate in devbench.yaml under report:."
    )


def _windows_explanation() -> str:
    return (
        f"Windows: 'All-time' covers the full orchestrator log; 'Session' is the most recent "
        f"contiguous block of orchestration log entries (boundary = a gap of more than "
        f"{DEFAULT_SESSION_GAP_MINUTES} minutes). 'This run' (watch mode only) is since "
        f"the watch loop started. Pass --since <ISO-8601> for a custom window."
    )


@dataclass(frozen=True)
class _BacklogTotals:
    tasks_total: int
    tasks_done: int
    units_total: int
    units_done: int
    stories_done: int
    features_done: int
    epics_done: int
    tasks_remaining: int


def _backlog_totals_from_units(units: list) -> _BacklogTotals:
    tasks = [u for u in units if u.unit_type == WorkUnitType.TASK]
    stories = [u for u in units if u.unit_type == WorkUnitType.STORY]
    features = [u for u in units if u.unit_type == WorkUnitType.FEATURE]
    epics = [u for u in units if u.unit_type == WorkUnitType.EPIC]
    tasks_done = [t for t in tasks if t.status == WorkUnitStatus.DONE]
    return _BacklogTotals(
        tasks_total=len(tasks),
        tasks_done=len(tasks_done),
        units_total=len(units),
        units_done=len([u for u in units if u.status == WorkUnitStatus.DONE]),
        stories_done=len([s for s in stories if s.status == WorkUnitStatus.DONE]),
        features_done=len([f for f in features if f.status == WorkUnitStatus.DONE]),
        epics_done=len([e for e in epics if e.status == WorkUnitStatus.DONE]),
        tasks_remaining=len(tasks) - len(tasks_done),
    )


def _backlog_state_rows(b: _BacklogTotals, lifetime: WindowStats | None = None) -> list[tuple[str, str]]:
    """Rows for the top "Backlog state + lifetime totals" box.

    When ``lifetime`` is provided (i.e., we have orchestrator log data), the
    box also shows lifetime cost / token / cache-hit metrics so the all-time
    totals are visible at a glance without having to read across the wider
    multi-column window-stats table below.
    """
    task_pct = round(PERCENT_MULTIPLIER * b.tasks_done / b.tasks_total) if b.tasks_total else 0
    total_pct = round(PERCENT_MULTIPLIER * b.units_done / b.units_total) if b.units_total else 0
    rows: list[tuple[str, str]] = [
        ("Tasks completed", f"{b.tasks_done} of {b.tasks_total} ({task_pct}%)"),
        (
            "Work units done (tasks + auto-rolled stories/features/epics)",
            f"{b.units_done} of {b.units_total} ({total_pct}%)",
        ),
        ("Tasks remaining", str(b.tasks_remaining)),
        (
            "Stories / Features / Epics auto-rolled to done",
            f"{b.stories_done} / {b.features_done} / {b.epics_done}",
        ),
    ]
    if lifetime is not None:
        cache_hit = f"{lifetime.cache_hit_rate:.2f}%" if lifetime.cache_hit_rate is not None else "n/a"
        est_total = f"~${lifetime.est_total_cost:.2f}" if lifetime.est_total_cost else "n/a"
        if lifetime.input_share is None:
            input_share = "n/a"
        else:
            in_pct = lifetime.input_share * PERCENT_MULTIPLIER
            out_pct = (1 - lifetime.input_share) * PERCENT_MULTIPLIER
            input_share = f"{in_pct:.1f}% / {out_pct:.1f}%"
        rows.extend(
            [
                ("Lifetime tokens consumed", _format_int(lifetime.total_tokens)),
                ("  ├─ input (uncached)", _format_int(lifetime.totals.input_tokens)),
                ("  ├─ cache reads", _format_int(lifetime.totals.cache_read_tokens)),
                ("  ├─ cache writes 5-min", _format_int(lifetime.totals.cache_write_5m_tokens)),
                ("  ├─ cache writes 1-hour", _format_int(lifetime.totals.cache_write_1h_tokens)),
                ("  └─ output", _format_int(lifetime.totals.output_tokens)),
                ("Lifetime input / output share (measured)", input_share),
                ("Lifetime cache hit rate", cache_hit),
                ("Lifetime estimated cost so far", f"~${lifetime.cost.total_cost:.2f}"),
                ("Lifetime estimated total cost at completion", est_total),
            ]
        )
    return rows


def generate_report(
    log_path: Path,
    since: datetime | None = None,
    report_started_at: datetime | None = None,
) -> str:
    """Generate a formatted progress report.

    Args:
        log_path: Path to the orchestrator log file.
        since: If provided, render a single window starting at this timestamp.
            If omitted, render All-time + Current session, plus This run when
            ``report_started_at`` is also provided (watch mode).
        report_started_at: When set, adds a "This run" column tracking activity
            since this timestamp. Used by ``cmd_report`` in watch mode to show
            what's happened since the watch loop began.

    Returns:
        Formatted report string ready for terminal output.
    """
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()
    backlog = _backlog_totals_from_units(units)

    log_text = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""

    done_times: dict[str, datetime] = {}
    for m in _DONE_RE.finditer(log_text):
        done_times[m.group(2)] = _parse_ts(m.group(1))
    progress_times: dict[str, datetime] = {}
    for m in _PROGRESS_RE.finditer(log_text):
        progress_times[m.group(2)] = _parse_ts(m.group(1))

    all_timestamps: list[datetime] = [_parse_ts(m.group(1)) for m in _LOG_LINE_RE.finditer(log_text)]
    log_start_for_window, window_end, log_started = _resolve_window_endpoints(all_timestamps)

    display_tz = _resolve_display_timezone(REPORT_DISPLAY_TIMEZONE)

    # Compute lifetime (since-log-started) stats once. Used to enrich the top
    # box with cost / token / cache-hit summary so the most relevant numbers
    # are visible at a glance without scanning across the wider window-stats table.
    lifetime_stats: WindowStats | None = (
        _compute_window_stats(
            log_path, log_start_for_window, window_end, done_times, progress_times, backlog.tasks_remaining
        )
        if log_started is not None
        else None
    )

    lines: list[str] = []
    lines.extend(_render_table("Backlog state", _backlog_state_rows(backlog, lifetime_stats)))

    if since is not None:
        # Single-window mode (legacy API for callers passing --since explicitly).
        single_stats = _compute_window_stats(
            log_path, since, window_end, done_times, progress_times, backlog.tasks_remaining
        )
        lines.append("")
        lines.extend(
            _render_table(
                _format_window_title("Window", since, log_started, display_tz),
                _stats_to_rows_single(single_stats),
            )
        )
        # Backward-compat label so callers grepping for "Tasks in this session" still find it.
        lines.append(f"\nTasks in this session: {single_stats.tasks_in_window}")
        summary_stats = single_stats
    else:
        # Default: render Backlog state + multi-column window-stats table.
        # Lifetime stats (already computed above) double as the All-time column,
        # so we don't re-walk the hook log for the same window.
        windows: list[WindowSpec] = [
            WindowSpec(label="All-time", start=log_start_for_window, is_log_started=True),
        ]
        detected_session = _find_current_session_start(log_text)
        session_start = detected_session if detected_session is not None else log_start_for_window
        windows.append(WindowSpec(label="Session", start=session_start, is_log_started=False))
        if report_started_at is not None:
            windows.append(WindowSpec(label="This run", start=report_started_at, is_log_started=False))

        all_window_stats: list[WindowStats] = []
        for w in windows:
            if w.label == "All-time" and lifetime_stats is not None:
                all_window_stats.append(lifetime_stats)
            else:
                all_window_stats.append(
                    _compute_window_stats(
                        log_path, w.start, window_end, done_times, progress_times, backlog.tasks_remaining
                    )
                )

        column_labels = [_short_window_label(w.label, w.start, display_tz) for w in windows]
        value_columns = [_stats_to_value_list(s) for s in all_window_stats]
        # Transpose: rows = list of (metric, [value_for_each_window])
        multi_rows = [(metric, [col[i] for col in value_columns]) for i, metric in enumerate(_METRIC_LABELS)]

        lines.append("")
        lines.extend(_render_multi_column_table("Window stats", column_labels, multi_rows))
        # Use the All-time stats for the trailing prose projection — they're the
        # most stable sample. Narrower windows can have zero completed tasks
        # (e.g. just after a restart) which would project meaningless numbers.
        summary_stats = all_window_stats[0]

    lines.append("")
    lines.append(_summary_line(summary_stats, backlog.tasks_remaining))
    if summary_stats.cost.total_cost:
        lines.append(_cost_basis_line())
    if since is None:
        lines.append("\n" + _windows_explanation())

    return "\n".join(lines)
