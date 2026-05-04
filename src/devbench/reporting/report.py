"""Backlog progress report generator.

Parses BACKLOG.md and the orchestrator log to produce a formatted progress
report showing velocity, completion stats, time estimates, and token cost.

Output structure:

The default (no ``since``) output is ONE grouped table with a single
"Metric" column followed by one column per window. Rows are grouped into
five sections:

- **BACKLOG STATE** -- instantaneous snapshots (task counts, rollups).
  Only the All-time column is populated; Session and This run are blank.
- **THROUGHPUT** -- per-window task completion and pace metrics.
- **API USAGE** -- API processing time and utilization.
- **TOKENS** -- per-window token breakdown, input/output share,
  cache hit rate, and average tokens per task.
- **COST** -- estimated cost so far and projected total at completion.

Default windows for the non-``since`` path:

- **All-time** -- cumulative across the entire orchestrator log history.
- **Current session** -- since the most recent gap of more than
  ``DEFAULT_SESSION_GAP_MINUTES`` between consecutive log entries (a
  proxy for "the orchestrator was restarted here"). Filters out noise
  from the ``judges.log_setup`` logger that fires on every CLI tick.
- **This run** (watch mode only) -- since the report watch loop began.

When ``generate_report`` is called with an explicit ``since`` timestamp,
the legacy two-box layout is used (Backlog state on top, a single-window
stats box beneath) for backward compatibility with callers that grep the
output in a known shape.

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
import os
import re
import sys
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
    DISPLAY_TIMEZONE,
    RECENT_PACE_TASKS,
    REPORT_CACHE_READ_MULTIPLIER,
    REPORT_CACHE_WRITE_1HR_MULTIPLIER,
    REPORT_CACHE_WRITE_5MIN_MULTIPLIER,
    REPORT_DATA_RESIDENCY_MULTIPLIER,
    REPORT_DISPLAY_TIMEZONE,
    REPORT_FAST_MODE_MULTIPLIER,
    TOKEN_COST_DISCOUNT,
    TOKEN_COST_PER_M_INPUT,
    TOKEN_COST_PER_M_OUTPUT,
    WORKSPACE_ROOT,
)
from devbench.constants import (
    DEFAULT_SESSION_GAP_MINUTES,
    LOG_NOISE_LOGGER_NAME,
    MIN_PACE_SAMPLES,
    MS_PER_SECOND,
    PERCENT_MULTIPLIER,
    SECONDS_PER_HOUR,
    SECONDS_PER_MINUTE,
    SIDE_BY_SIDE_GAP_CHARS,
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
    metrics). This is the fail-fast posture -- missing cost data surfaces as
    visibly-low cost rather than silently-masked blended estimates.
    """

    total_duration_ms: int = 0
    input_tokens: int = 0  # uncached input
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    entries_with_usage: int = 0
    entries_us_geo: int = 0
    entries_fast_mode: int = 0
    # Token volumes from entries flagged with ``inference_geo`` (data-residency
    # premium, issue #124). Tracked separately so ``_compute_cost`` can apply
    # ``report.data_residency_multiplier`` (default 1.10 from
    # DEFAULT_DATA_RESIDENCY_MULTIPLIER) only to the residency-restricted
    # subset of the token volume, not the full aggregate.
    us_only_input_tokens: int = 0
    us_only_output_tokens: int = 0
    us_only_cache_read_tokens: int = 0
    us_only_cache_write_5m_tokens: int = 0
    us_only_cache_write_1h_tokens: int = 0
    # Token volumes from entries with ``usage.speed == 'fast'`` (fast-mode
    # premium, issue #124). Same per-subset accounting; multiplied by
    # ``report.fast_mode_multiplier`` (default 6.0).
    fast_input_tokens: int = 0
    fast_output_tokens: int = 0
    fast_cache_read_tokens: int = 0
    fast_cache_write_5m_tokens: int = 0
    fast_cache_write_1h_tokens: int = 0


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
    # Number of completed-task duration samples that produced ``avg_minutes``.
    # When < MIN_PACE_SAMPLES the display marks the pace as fragile so a single
    # sample (e.g. a freshly-restarted Session window with one completion)
    # cannot drive a multi-task projection.
    pace_sample_count: int = 0
    # Average minutes per task across the most-recently-completed N tasks
    # (N = RECENT_PACE_TASKS), regardless of window. None when fewer than N
    # task completions exist log-wide. Used in preference to ``avg_minutes``
    # for projections so the rate metric reflects current orchestrator pace
    # rather than being anchored by historical completions.
    recent_pace_minutes: float | None = None
    # Wall-clock completion moment = now() + est_hours. None when est_hours is
    # zero / unknown (no pace data yet). Stored in UTC; the renderer converts
    # to the resolved display timezone.
    est_completion_at: datetime | None = None
    # Issue #157: ETA breakdown so the renderer can print
    # ``~5.4 h (active 4 + blocked-recovery 60 + blocked-auto 27 at 5.6 min/task)``.
    eta_active: int = 0
    eta_blocked_recovery: int = 0
    eta_blocked_auto: int = 0

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
        "us_only_input_tokens": 0,
        "us_only_output_tokens": 0,
        "us_only_cache_read_tokens": 0,
        "us_only_cache_write_5m_tokens": 0,
        "us_only_cache_write_1h_tokens": 0,
        "fast_input_tokens": 0,
        "fast_output_tokens": 0,
        "fast_cache_read_tokens": 0,
        "fast_cache_write_5m_tokens": 0,
        "fast_cache_write_1h_tokens": 0,
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

    in_t = int(usage.get("input_tokens") or 0)
    out_t = int(usage.get("output_tokens") or 0)
    read_t = int(usage.get("cache_read_input_tokens") or 0)
    cc = usage.get("cache_creation")
    if isinstance(cc, dict):
        write_5m_t = int(cc.get("ephemeral_5m_input_tokens") or 0)
        write_1h_t = int(cc.get("ephemeral_1h_input_tokens") or 0)
    else:
        # Older format / fallback: all cache writes counted as 5-min.
        write_5m_t = int(usage.get("cache_creation_input_tokens") or 0)
        write_1h_t = 0

    totals_acc["input_tokens"] += in_t
    totals_acc["output_tokens"] += out_t
    totals_acc["cache_read_tokens"] += read_t
    totals_acc["cache_write_5m_tokens"] += write_5m_t
    totals_acc["cache_write_1h_tokens"] += write_1h_t

    # Per-subset token tallies for the data-residency and fast-mode premium
    # multipliers (issue #124). Tracked per-call so the multipliers apply
    # only to the affected token volume, not the full aggregate.
    is_us_only = bool(usage.get("inference_geo"))
    is_fast = usage.get("speed") == "fast"
    if is_us_only:
        totals_acc["entries_us_geo"] += 1
        totals_acc["us_only_input_tokens"] += in_t
        totals_acc["us_only_output_tokens"] += out_t
        totals_acc["us_only_cache_read_tokens"] += read_t
        totals_acc["us_only_cache_write_5m_tokens"] += write_5m_t
        totals_acc["us_only_cache_write_1h_tokens"] += write_1h_t
    if is_fast:
        totals_acc["entries_fast_mode"] += 1
        totals_acc["fast_input_tokens"] += in_t
        totals_acc["fast_output_tokens"] += out_t
        totals_acc["fast_cache_read_tokens"] += read_t
        totals_acc["fast_cache_write_5m_tokens"] += write_5m_t
        totals_acc["fast_cache_write_1h_tokens"] += write_1h_t

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


_ROLE_ORCHESTRATOR = "orchestrator"


def _role_for_entry(entry: dict) -> str:
    """Return the per-role bucket for one transcript entry (issue #123).

    Each Claude Code transcript message carries an ``attributionAgent`` field
    naming the active agent (e.g. ``"devbench:executor"``,
    ``"devbench:code-reviewer"``). Messages emitted by the outer orchestrator
    loop have no attributionAgent and are bucketed as ``orchestrator``.
    Subagent attributions are stripped of the ``devbench:`` prefix and
    normalised to underscores so the buckets match the canonical role names
    used elsewhere (e.g. ``executor``, ``code_review``).
    """
    raw = entry.get("attributionAgent")
    if not isinstance(raw, str) or not raw:
        return _ROLE_ORCHESTRATOR
    # Strip plugin prefix (``devbench:``) and normalise dashes to underscores.
    # ``code-reviewer`` -> ``code_review`` (matches REVIEW_JUDGE_NAMES).
    base = raw.split(":", 1)[1] if ":" in raw else raw
    return base.replace("-reviewer", "_review").replace("-", "_")


def _parse_transcript_metrics_by_role(transcript_dir: Path | None, window_start: datetime) -> dict[str, HookLogTotals]:
    """Like ``_parse_transcript_metrics`` but bucketed per agent role (issue #123).

    Returns a dict mapping role name -> HookLogTotals. The summed totals
    across all roles equal what ``_parse_transcript_metrics`` returns; the
    aggregate-row contract is asserted in the regression test.
    """
    if transcript_dir is None or not transcript_dir.is_dir():
        return {}

    by_role: dict[str, dict[str, int]] = {}
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
            role = _role_for_entry(entry)
            acc = by_role.setdefault(role, _empty_totals_acc())
            _accumulate_transcript_message(entry.get("message"), acc)
    return {role: HookLogTotals(**acc) for role, acc in by_role.items()}


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
        us_only_input_tokens=a.us_only_input_tokens + b.us_only_input_tokens,
        us_only_output_tokens=a.us_only_output_tokens + b.us_only_output_tokens,
        us_only_cache_read_tokens=a.us_only_cache_read_tokens + b.us_only_cache_read_tokens,
        us_only_cache_write_5m_tokens=a.us_only_cache_write_5m_tokens + b.us_only_cache_write_5m_tokens,
        us_only_cache_write_1h_tokens=a.us_only_cache_write_1h_tokens + b.us_only_cache_write_1h_tokens,
        fast_input_tokens=a.fast_input_tokens + b.fast_input_tokens,
        fast_output_tokens=a.fast_output_tokens + b.fast_output_tokens,
        fast_cache_read_tokens=a.fast_cache_read_tokens + b.fast_cache_read_tokens,
        fast_cache_write_5m_tokens=a.fast_cache_write_5m_tokens + b.fast_cache_write_5m_tokens,
        fast_cache_write_1h_tokens=a.fast_cache_write_1h_tokens + b.fast_cache_write_1h_tokens,
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
    API-utilization accounting). Their token counts are skipped -- there is
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
    *,
    data_residency_multiplier: float = 1.0,
    fast_mode_multiplier: float = 1.0,
) -> CostBreakdown:
    """Compute per-token-type cost subtotals and the rolled-up total. Pure function.

    Cost is always computed from real per-token-type counts. No blended rate,
    no estimated input/output ratio -- if an LLM call didn't record ``usage``,
    it contributes zero cost (and an audit-visible gap in ``entries_with_usage``).

    Issue #124: ``data_residency_multiplier`` (default 1.0 = no boost) is
    applied to the residency-flagged subset (``us_only_*_tokens``) AFTER the
    cache scaling and BEFORE the discount. ``fast_mode_multiplier`` (default
    1.0 = no boost) is applied identically to the fast-mode subset
    (``fast_*_tokens``). Each multiplier composes with the cache + base-rate
    multipliers per AC-FUNC-003. Discount composition is handled by the
    caller (``apply_discount`` runs on the final total).

    The premium cost contributions are added to the per-token-type buckets
    (``input_cost`` etc.) so the breakdown row totals still sum to
    ``total_cost`` for the existing aggregate-row contract.
    """

    def _bucket_cost(
        in_t: int, out_t: int, read_t: int, w5_t: int, w1_t: int
    ) -> tuple[float, float, float, float, float]:
        return (
            in_t * input_rate / TOKENS_PER_MILLION,
            out_t * output_rate / TOKENS_PER_MILLION,
            read_t * input_rate * cache_read_mult / TOKENS_PER_MILLION,
            w5_t * input_rate * cache_5m_mult / TOKENS_PER_MILLION,
            w1_t * input_rate * cache_1h_mult / TOKENS_PER_MILLION,
        )

    in_c, out_c, read_c, w5_c, w1_c = _bucket_cost(
        totals.input_tokens,
        totals.output_tokens,
        totals.cache_read_tokens,
        totals.cache_write_5m_tokens,
        totals.cache_write_1h_tokens,
    )

    # Residency premium: residency-flagged tokens cost (multiplier - 1) MORE
    # than the base. Adding to the per-bucket cost preserves the bucket-sum
    # invariant.
    if data_residency_multiplier != 1.0:
        boost = data_residency_multiplier - 1.0
        ri_c, ro_c, rr_c, rw5_c, rw1_c = _bucket_cost(
            totals.us_only_input_tokens,
            totals.us_only_output_tokens,
            totals.us_only_cache_read_tokens,
            totals.us_only_cache_write_5m_tokens,
            totals.us_only_cache_write_1h_tokens,
        )
        in_c += ri_c * boost
        out_c += ro_c * boost
        read_c += rr_c * boost
        w5_c += rw5_c * boost
        w1_c += rw1_c * boost

    if fast_mode_multiplier != 1.0:
        boost = fast_mode_multiplier - 1.0
        fi_c, fo_c, fr_c, fw5_c, fw1_c = _bucket_cost(
            totals.fast_input_tokens,
            totals.fast_output_tokens,
            totals.fast_cache_read_tokens,
            totals.fast_cache_write_5m_tokens,
            totals.fast_cache_write_1h_tokens,
        )
        in_c += fi_c * boost
        out_c += fo_c * boost
        read_c += fr_c * boost
        w5_c += fw5_c * boost
        w1_c += fw1_c * boost

    total = in_c + out_c + read_c + w5_c + w1_c
    return CostBreakdown(
        input_cost=in_c,
        output_cost=out_c,
        cache_read_cost=read_c,
        cache_write_5m_cost=w5_c,
        cache_write_1h_cost=w1_c,
        total_cost=total,
    )


def _recent_pace_minutes(
    done_times: dict[str, datetime],
    progress_times: dict[str, datetime],
    n: int,
) -> float | None:
    """Average minutes per task across the most-recent ``n`` task completions.

    Looks log-wide (not window-bounded) so the metric reflects current
    orchestrator pace rather than being anchored by historical completions.
    Returns None when fewer than ``n`` task completions have valid durations.
    """
    task_done = [(tid, ts) for tid, ts in done_times.items() if "-T" in tid]
    task_done.sort(key=lambda kv: kv[1], reverse=True)
    durations: list[float] = []
    for tid, dt in task_done:
        if tid not in progress_times:
            continue
        dur = (dt - progress_times[tid]).total_seconds() / SECONDS_PER_MINUTE
        if dur > 0:
            durations.append(dur)
        if len(durations) >= n:
            break
    if len(durations) < n:
        return None
    return sum(durations) / len(durations)


def _compute_window_stats(
    log_path: Path,
    window_start: datetime,
    window_end: datetime,
    done_times: dict[str, datetime],
    progress_times: dict[str, datetime],
    tasks_active: int,
    tasks_blocked_recovery: int = 0,
    tasks_blocked_auto: int = 0,
) -> WindowStats:
    """Compute all time-windowed statistics for a single window.

    Issue #157: the ETA denominator now includes blocked tasks that
    devbench will recover on its own -- ``tasks_blocked_recovery``
    (AWAITING_AUTO_RECOVERY) and ``tasks_blocked_auto``
    (AUTO_CLEARING_VIA_PROPOSAL) -- in addition to ``tasks_active``.
    The operator-attention bucket stays excluded since those represent
    genuine halts with unbounded ETA. When the recent-pace window has
    fewer than ``MIN_PACE_SAMPLES`` completed tasks the pace fallback
    path is taken; ``est_hours`` reads zero (renderer shows "n/a").
    """
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
    pace_sample_count = len(task_durations)
    avg_minutes = sum(task_durations) / pace_sample_count if pace_sample_count >= MIN_PACE_SAMPLES else 0.0

    recent_pace_minutes: float | None = _recent_pace_minutes(done_times, progress_times, RECENT_PACE_TASKS)

    pace_for_projection = recent_pace_minutes if recent_pace_minutes is not None else avg_minutes
    eta_task_count = tasks_active + tasks_blocked_recovery + tasks_blocked_auto
    est_hours = (eta_task_count * pace_for_projection) / SECONDS_PER_MINUTE if pace_for_projection else 0.0

    # Combine usage from two sources, both filtered by window_start:
    #   1. hook-logs.jsonl: subagent (Agent tool) invocations -- captures executor / judge / etc costs
    #   2. Claude Code transcripts: per-turn outer-session reasoning -- captures what the orchestrate
    #      skill itself spends between Agent calls. Without these, cost can be off by 10-20x.
    hook_log_path = _hook_log_path(log_path)
    totals_hook = _parse_hook_log_metrics(log_path, window_start)
    transcript_dir = _discover_transcript_dir(hook_log_path)
    totals_transcript = _parse_transcript_metrics(transcript_dir, window_start)
    totals = _combine_totals(totals_hook, totals_transcript)
    # Apply the configured token-cost discount (contract rate / correction
    # factor off list) to the base input/output rates. final = list * (1 - d).
    # Cache multipliers stay as pure ratios; the discounted base propagates
    # into every component cost AND the ETA projection (est_total_cost is
    # derived from cost.total_cost), so one multiplication covers both.
    rate_factor = 1.0 - TOKEN_COST_DISCOUNT
    cost = _compute_cost(
        totals,
        TOKEN_COST_PER_M_INPUT * rate_factor,
        TOKEN_COST_PER_M_OUTPUT * rate_factor,
        REPORT_CACHE_READ_MULTIPLIER,
        REPORT_CACHE_WRITE_5MIN_MULTIPLIER,
        REPORT_CACHE_WRITE_1HR_MULTIPLIER,
        data_residency_multiplier=REPORT_DATA_RESIDENCY_MULTIPLIER,
        fast_mode_multiplier=REPORT_FAST_MODE_MULTIPLIER,
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
    # Issue #157: cost projection uses the same denominator as ETA (active +
    # auto-recovery buckets) so the cost forecast scales with the same task
    # set the ETA forecast does.
    est_total_cost = cost.total_cost + (cost.total_cost / tasks_in_window * eta_task_count if tasks_in_window else 0.0)

    api_hours = totals.total_duration_ms / MS_PER_SECOND / SECONDS_PER_HOUR
    api_efficiency = (api_hours / window_hours * PERCENT_MULTIPLIER) if window_hours > 0 else None

    # Wall-clock completion moment. None when est_hours is zero/unknown
    # (no pace data yet, or no remaining active tasks). Stored in UTC;
    # the renderer converts to the resolved display timezone.
    est_completion_at = datetime.now(UTC) + timedelta(hours=est_hours) if est_hours > 0 else None

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
        pace_sample_count=pace_sample_count,
        recent_pace_minutes=recent_pace_minutes,
        est_completion_at=est_completion_at,
        eta_active=tasks_active,
        eta_blocked_recovery=tasks_blocked_recovery,
        eta_blocked_auto=tasks_blocked_auto,
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


# ANSI color codes. Applied AFTER alignment so escape bytes never count
# toward the visible-width math the table renderers do via len().
_COLOR_GREEN = "\033[32m"
_COLOR_RED_LIGHT = "\033[91m"
_COLOR_MAGENTA = "\033[35m"
_COLOR_RESET = "\033[0m"

# Map metric labels to ANSI color codes. The renderers wrap the entire
# row line (borders included) so the colour visually pops while the
# alignment stays exact.
_ROW_COLORS: dict[str, str] = {
    "Tasks completed": _COLOR_GREEN,
    "Tasks completed in window": _COLOR_GREEN,
    "Work units done (tasks + auto-rolled stories/features/epics)": _COLOR_GREEN,
    "Stories / Features / Epics auto-rolled to done": _COLOR_GREEN,
    "Tasks blocked": _COLOR_RED_LIGHT,
    "Estimated cost so far": _COLOR_MAGENTA,
}


def _should_use_color() -> bool:
    """Return True when ANSI colour should be emitted.

    Honours the de-facto NO_COLOR convention (https://no-color.org/) and
    only emits colour when stdout is a TTY -- pipes / log files stay
    plain text.
    """
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _colorize_row(line: str, metric: str) -> str:
    """Wrap ``line`` with the ANSI colour for ``metric`` when colour is on.

    Returns the line unchanged when the metric has no colour mapping or
    the runtime environment is not TTY-friendly.
    """
    color = _ROW_COLORS.get(metric)
    if color is None or not _should_use_color():
        return line
    return f"{color}{line}{_COLOR_RESET}"


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
        row_line = f"\u2502 {metric:<{metric_w}} \u2502 {value:>{value_w}} \u2502"
        lines.append(_colorize_row(row_line, metric))
    lines.append(border_bot)
    return lines


def _render_multi_column_table(
    title: str,
    column_labels: list[str],
    rows: list[tuple[str, list[str] | str]],
    value_w: int = 16,
) -> list[str]:
    """Render a bordered table with one metric column and N value columns.

    ``column_labels`` is the header row for the value columns.
    ``rows`` is a list of (metric, value). ``value`` is either a list with one
    entry per column (normal row) OR a single string (spanning row -- used when
    a metric is window-agnostic, e.g. log-wide Recent pace / projected ETA; the
    same number repeating in every column is just noise).

    The metric column auto-sizes to the longest label (or the title), and each
    value column widens to the longest value (or its label) so future label
    additions never overflow and break alignment.
    """
    n_cols = len(column_labels)
    metric_w = max((len(metric) for metric, _ in rows), default=0)
    metric_w = max(metric_w, len(title))

    # Value column width = max of: default minimum, label width, max cell width.
    # Spanning rows (value is str) span all n_cols value columns, so they
    # contribute a derived minimum value_w too -- otherwise a wider-than-default
    # spanning value (e.g. an ETA breakdown like "~41.9 h (active 4 + blocked-
    # recovery 60 + blocked-auto 27 at 27.6 min/task)") busts the table layout.
    def _cells_of(v: list[str] | str) -> list[str]:
        return v if isinstance(v, list) else []

    max_cell = max((max((len(v) for v in _cells_of(vals)), default=0) for _, vals in rows), default=0)
    max_label = max((len(label) for label in column_labels), default=0)
    # Reverse-derive the minimum value_w from the spanning-cell width formula
    # (spanning_w computed below) so a spanning value never exceeds the joined
    # span. Ceiling division of (max_spanning + 3 - 3 * n_cols) by n_cols.
    max_spanning = max(
        (len(vals) for _, vals in rows if isinstance(vals, str)),
        default=0,
    )
    spanning_min_value_w = (
        (max_spanning + 3 - 3 * n_cols + n_cols - 1) // n_cols if max_spanning > 0 and n_cols > 0 else 0
    )
    value_w = max(value_w, max_cell, max_label, spanning_min_value_w)

    # Width a spanning cell occupies (covers all n_cols value columns plus the
    # n_cols-1 internal "│" separators that would otherwise split them).
    spanning_w = n_cols * (value_w + 2) + (n_cols - 1) - 2  # -2 for leading/trailing space padding

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
        if isinstance(values, str):
            # Spanning row: a single right-aligned value occupies the full width
            # of all value columns (including the column separators). The top /
            # bottom borders of this row still show the regular ┬/┴ junctions
            # so the column boundaries stay visually consistent throughout the
            # table; only the content row's internal │ separators are merged.
            row_line = f"\u2502 {metric:<{metric_w}} \u2502 {values:>{spanning_w}} \u2502"
        else:
            cells = [f" {metric:<{metric_w}} "] + [f" {v:>{value_w}} " for v in values]
            row_line = "\u2502" + "\u2502".join(cells) + "\u2502"
        lines.append(_colorize_row(row_line, metric))
    lines.append(border_bot)
    return lines


def _render_grouped_progress_table(
    title: str,
    column_labels: list[str],
    sections: list[tuple[str, list[tuple[str, list[str] | str]]]],
    value_w: int = 16,
) -> list[str]:
    """Render a single unified progress table with section headers.

    ``sections`` is a list of ``(section_label, rows)``. Each ``rows`` entry
    is ``(metric_label, values)`` with the same semantics as
    :func:`_render_multi_column_table`: ``list[str]`` for per-column values
    (empty strings render as blank cells) and ``str`` for spanning values
    that collapse across every value column.

    Section-label rows are emitted as full-width merged cells (spanning the
    metric column + every value column + their internal separators), with
    mid-borders above and below, so the reader scans:
    header row -> BACKLOG STATE rows -> THROUGHPUT rows -> ... -> bottom border.
    """
    n_cols = len(column_labels)
    all_rows = [row for _, section_rows in sections for row in section_rows]
    metric_w = max((len(metric) for metric, _ in all_rows), default=0)
    metric_w = max(metric_w, len(title), max((len(name) for name, _ in sections), default=0))

    def _cells_of(v: list[str] | str) -> list[str]:
        return v if isinstance(v, list) else []

    max_cell = max((max((len(v) for v in _cells_of(vals)), default=0) for _, vals in all_rows), default=0)
    max_label = max((len(label) for label in column_labels), default=0)
    # Spanning rows (value is str) cover all n_cols value columns plus their
    # internal separators. Without measuring them, a wide spanning value (e.g.
    # the ETA breakdown "~41.9 h (active 4 + blocked-recovery 60 + ...)" busts
    # the table layout. Reverse-derive the minimum value_w from the spanning
    # formula below so the joined span fits the longest observed string.
    max_spanning = max(
        (len(vals) for _, section_rows in sections for _, vals in section_rows if isinstance(vals, str)),
        default=0,
    )
    spanning_min_value_w = (
        (max_spanning + 3 - 3 * n_cols + n_cols - 1) // n_cols if max_spanning > 0 and n_cols > 0 else 0
    )
    value_w = max(value_w, max_cell, max_label, spanning_min_value_w)

    # Width a spanning cell occupies across all n_cols value columns (plus
    # the n_cols-1 internal separators). Used for both the section-header row
    # and any individual metric whose value was merged into a str.
    spanning_w = n_cols * (value_w + 2) + (n_cols - 1) - 2
    # Width of the ENTIRE merged cell for a section-header row: metric column
    # + all value columns + every separator between them - leading/trailing
    # padding (2 spaces).
    section_w = metric_w + 2 + 1 + (n_cols * (value_w + 2) + (n_cols - 1)) - 2

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

    lines: list[str] = [border_top, header_line]

    for section_label, rows in sections:
        if not rows:
            continue
        # One mid-border divides the previous section (or the column-header
        # row) from this section. The section-label row flows directly into
        # the first metric row with no additional border between them, so
        # the sections read as compact visual blocks like the plan's mockup.
        lines.append(border_mid)
        lines.append(f"\u2502 {section_label.upper():<{section_w}} \u2502")
        for metric, values in rows:
            if isinstance(values, str):
                row_line = f"\u2502 {metric:<{metric_w}} \u2502 {values:>{spanning_w}} \u2502"
            else:
                cells = [f" {metric:<{metric_w}} "] + [f" {v:>{value_w}} " for v in values]
                row_line = "\u2502" + "\u2502".join(cells) + "\u2502"
            lines.append(_colorize_row(row_line, metric))
    lines.append(border_bot)
    return lines


def _render_side_by_side(left: list[str], right: list[str], gap: int = SIDE_BY_SIDE_GAP_CHARS) -> list[str]:
    """Merge two pre-rendered table block lists onto the same rows, left-right.

    The shorter list is padded with whitespace matching its own rendered
    width so the resulting output stays rectangular -- i.e. the right block
    does not shift left on rows where the left block has ended. An empty
    input on either side returns the other unchanged.
    """
    if not left:
        return list(right)
    if not right:
        return list(left)
    left_width = max(len(line) for line in left)
    right_width = max(len(line) for line in right)
    row_count = max(len(left), len(right))
    out: list[str] = []
    blank_left = " " * left_width
    blank_right = " " * right_width
    for i in range(row_count):
        lft = left[i] if i < len(left) else blank_left
        rgt = right[i] if i < len(right) else blank_right
        out.append(lft.ljust(left_width) + " " * gap + rgt)
    return out


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


def _format_est_hours_display(stats: WindowStats) -> str:
    """Render the ``Est. time to complete remaining`` cell (#157).

    When recent-pace data is available, attaches the ETA breakdown
    suffix so the operator can verify which task buckets contributed
    to the multiplier and at what pace. Falls back to a bare hours
    figure when recent pace is unknown but est_hours was computable
    from the window's avg_minutes; falls back to ``n/a`` otherwise.
    """
    if stats.est_hours and stats.recent_pace_minutes is not None:
        return (
            f"~{stats.est_hours:.1f} h (active {stats.eta_active}"
            f" + blocked-recovery {stats.eta_blocked_recovery}"
            f" + blocked-auto {stats.eta_blocked_auto}"
            f" at {stats.recent_pace_minutes:.1f} min/task)"
        )
    if stats.est_hours:
        return f"~{stats.est_hours:.1f} h"
    return "n/a"


def _stats_to_value_list(stats: WindowStats, display_tz: tzinfo | None = None) -> list[str]:
    """Return the per-row value list for a single window, in display order matching METRIC_LABELS.

    ``display_tz`` is used to render the estimated-completion datetime in the
    operator's configured timezone. When ``None``, the OS local zone applies.
    """
    # API utilization > 100% means API time exceeds wall time -- concurrent
    # subagent calls (legitimate parallelism) or two orchestrators writing to
    # the same hook log. The raw percentage reads as broken; surface the
    # condition explicitly. The underlying WindowStats.api_efficiency stays
    # untouched for programmatic callers.
    if stats.api_efficiency is None:
        api_eff = "n/a"
    elif stats.api_efficiency > PERCENT_MULTIPLIER:
        api_eff = ">100% (parallel)"
    else:
        api_eff = f"{stats.api_efficiency:.1f}%"
    cache_hit = f"{stats.cache_hit_rate:.2f}%" if stats.cache_hit_rate is not None else "n/a"
    tokens_per_task = f"{stats.tokens_per_task:,.0f}" if stats.tokens_per_task else "n/a"
    est_total = f"~${stats.est_total_cost:.2f}" if stats.est_total_cost else "n/a"
    # Per-task averages are meaningful only when ≥ MIN_PACE_SAMPLES tasks
    # completed in the window. Below that, _compute_window_stats sets avg=0
    # and we display "n/a" with the actual sample count so the reader knows
    # whether the metric is genuinely empty (N=0) or fragile (1 ≤ N < min).
    if stats.avg_minutes:
        avg_min_display = f"{stats.avg_minutes:.1f} min"
    elif stats.pace_sample_count > 0:
        avg_min_display = f"n/a (N={stats.pace_sample_count} sample{'s' if stats.pace_sample_count != 1 else ''})"
    else:
        avg_min_display = "n/a"
    # Recent pace is log-wide; same value across all window columns. None
    # when fewer than RECENT_PACE_TASKS completions exist.
    recent_pace_display = f"{stats.recent_pace_minutes:.1f} min" if stats.recent_pace_minutes is not None else "n/a"
    est_hours_display = _format_est_hours_display(stats)
    # Wall-clock completion datetime rendered in the resolved display TZ.
    # Format: "Thu Apr 24 2026 14:23 EDT". "n/a" when est_hours is zero.
    if stats.est_completion_at is None:
        est_completion_display = "n/a"
    else:
        local_completion = (
            stats.est_completion_at.astimezone(display_tz) if display_tz else stats.est_completion_at.astimezone()
        )
        est_completion_display = local_completion.strftime("%a %b %d %Y %H:%M %Z")
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
        recent_pace_display,
        est_hours_display,
        est_completion_display,
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
    f"Recent pace (last {RECENT_PACE_TASKS} tasks)",
    "Est. time to complete remaining",
    "Est. completion date/time",
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

# Rows whose value is window-agnostic (identical across every window column).
# These render as a single cell spanning all value columns instead of repeating
# the same number per column. Recent pace is log-wide by construction; Est. time
# to complete remaining = tasks_active * log-wide pace / 60 when recent pace is
# available (the usual case after RECENT_PACE_TASKS completions).
_SPANNING_METRIC_LABELS: frozenset[str] = frozenset(
    {
        f"Recent pace (last {RECENT_PACE_TASKS} tasks)",
        "Est. time to complete remaining",
        "Est. completion date/time",
    }
)


def _merge_spanning_values(metric: str, values: list[str]) -> list[str] | str:
    """Collapse a per-column value list into a single spanning str when applicable.

    Spanning only triggers when the metric is in ``_SPANNING_METRIC_LABELS`` AND
    every value is identical -- if values differ (e.g. recent pace falls back to
    per-window avg_minutes when log-wide samples are insufficient), keep the
    per-column layout so any divergence stays visible.
    """
    if metric not in _SPANNING_METRIC_LABELS:
        return values
    if len(set(values)) == 1:
        return values[0]
    return values


def _stats_to_rows_single(stats: WindowStats, display_tz: tzinfo | None = None) -> list[tuple[str, str]]:
    """Convert one WindowStats into (metric, value) rows for the single-column table."""
    values = _stats_to_value_list(stats, display_tz)
    return list(zip(_METRIC_LABELS, values, strict=True))


def _resolve_window_endpoints(log_timestamps: list[datetime]) -> tuple[datetime, datetime, datetime | None]:
    """Return (log_start_for_window, window_end, log_started) for an empty or non-empty log.

    ``window_end`` is the report-generation moment, bounded below by
    ``datetime.now(UTC)``. Without that bound, a "This run" window whose
    ``start`` post-dates every log entry (common in watch mode when no new
    log lines have arrived yet) yields a negative span and an n/a cascade
    across every derived metric.

    For an empty log, both window endpoints fall back to ``datetime.now(UTC)``
    and ``log_started`` is None.
    """
    now = datetime.now(UTC)
    if log_timestamps:
        log_started = min(log_timestamps)
        return log_started, max(*log_timestamps, now), log_started
    return now, now, None


def _summary_line(stats: WindowStats, tasks_active: int, tasks_blocked: int) -> str:
    """Trailing one-line completion projection.

    Issue #157: ETA denominator now also includes blocked tasks devbench
    will recover on its own (``stats.eta_blocked_recovery`` +
    ``stats.eta_blocked_auto``). Only the operator-attention bucket is
    excluded (genuine halt -> unbounded ETA). The pace prefers
    ``recent_pace_minutes`` when available; falls back to
    ``avg_minutes`` otherwise.
    """
    eta_total = tasks_active + stats.eta_blocked_recovery + stats.eta_blocked_auto
    attn_blocked = max(0, tasks_blocked - stats.eta_blocked_recovery - stats.eta_blocked_auto)
    blocked_note = f" -- {attn_blocked} blocked excluded" if attn_blocked else ""
    if eta_total == 0:
        if tasks_blocked:
            return (
                f"0 active tasks. {tasks_blocked} blocked task(s) remaining -- "
                "need external action before the orchestrator can proceed."
            )
        return "All tasks complete."
    pace_label, pace_minutes = (
        ("Recent", stats.recent_pace_minutes)
        if stats.recent_pace_minutes is not None
        else ("All-time", stats.avg_minutes)
    )
    if not pace_minutes:
        return f"{eta_total} active task(s) remaining{blocked_note}. (Not enough completed tasks for a pace estimate.)"
    return (
        f"At the {pace_label} pace of ~{pace_minutes:.1f} minutes per task, "
        f"the remaining {eta_total} active task(s){blocked_note} should take roughly "
        f"{stats.est_hours:.1f} more hours of continuous execution."
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
    tasks_remaining: int  # all non-Done tasks (active + blocked); kept for backward-compat
    tasks_blocked: int  # non-Done tasks with status == BLOCKED
    tasks_active: int  # tasks_remaining - tasks_blocked (in-queue / in-progress / in-review)
    tasks_in_progress: int  # non-Done tasks with status == IN_PROGRESS (subset of tasks_active)
    tasks_in_queue: int  # non-Done tasks with status == IN_QUEUE (subset of tasks_active)
    tasks_in_review: int  # non-Done tasks with status == IN_REVIEW (subset of tasks_active)
    tasks_proposed: int  # task-factory-generated drafts awaiting human review
    tasks_declined: int  # explicitly declined work (won't ever be done)
    # Issue #157: blocked tasks split by their recovery classifier so the
    # ETA projection can include recovery+auto buckets while excluding the
    # genuine-halt operator-attention bucket.
    tasks_blocked_recovery: int = 0  # AWAITING_AUTO_RECOVERY
    tasks_blocked_auto: int = 0  # AUTO_CLEARING_VIA_PROPOSAL
    tasks_blocked_attn: int = 0  # NEEDS_OPERATOR_ATTENTION


def _backlog_totals_from_units(units: list) -> _BacklogTotals:
    tasks = [u for u in units if u.unit_type == WorkUnitType.TASK]
    stories = [u for u in units if u.unit_type == WorkUnitType.STORY]
    features = [u for u in units if u.unit_type == WorkUnitType.FEATURE]
    epics = [u for u in units if u.unit_type == WorkUnitType.EPIC]
    tasks_done = [t for t in tasks if t.status == WorkUnitStatus.DONE]
    tasks_blocked = [t for t in tasks if t.status == WorkUnitStatus.BLOCKED]
    tasks_in_progress = [t for t in tasks if t.status == WorkUnitStatus.IN_PROGRESS]
    tasks_in_queue = [t for t in tasks if t.status == WorkUnitStatus.IN_QUEUE]
    tasks_in_review = [t for t in tasks if t.status == WorkUnitStatus.IN_REVIEW]
    tasks_proposed = [t for t in tasks if t.status == WorkUnitStatus.PROPOSED]
    tasks_declined = [t for t in tasks if t.status == WorkUnitStatus.DECLINED]
    tasks_remaining = len(tasks) - len(tasks_done) - len(tasks_proposed) - len(tasks_declined)

    # Issue #157: classify blocked tasks so the ETA projection can include
    # the auto-clearing + awaiting-recovery buckets (devbench will resolve
    # them on its own) while excluding the operator-attention bucket
    # (genuine halt -> unbounded ETA).
    blocked_recovery = 0
    blocked_auto = 0
    blocked_attn = 0
    if tasks_blocked:
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        for u in tasks_blocked:
            try:
                state = classify_blocked_task(
                    BACKLOG_ROOT,
                    BACKLOG_INDEX,
                    u.id,
                    workspace_root=WORKSPACE_ROOT,
                )
            except (FileNotFoundError, ValueError, OSError):
                state = BlockedTaskState.NEEDS_OPERATOR_ATTENTION
            if state is BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL:
                blocked_auto += 1
            elif state is BlockedTaskState.AWAITING_AUTO_RECOVERY:
                blocked_recovery += 1
            else:
                blocked_attn += 1

    return _BacklogTotals(
        tasks_total=len(tasks),
        tasks_done=len(tasks_done),
        units_total=len(units),
        units_done=len([u for u in units if u.status == WorkUnitStatus.DONE]),
        stories_done=len([s for s in stories if s.status == WorkUnitStatus.DONE]),
        features_done=len([f for f in features if f.status == WorkUnitStatus.DONE]),
        epics_done=len([e for e in epics if e.status == WorkUnitStatus.DONE]),
        tasks_remaining=tasks_remaining,
        tasks_blocked=len(tasks_blocked),
        tasks_active=tasks_remaining - len(tasks_blocked),
        tasks_in_progress=len(tasks_in_progress),
        tasks_in_queue=len(tasks_in_queue),
        tasks_in_review=len(tasks_in_review),
        tasks_proposed=len(tasks_proposed),
        tasks_declined=len(tasks_declined),
        tasks_blocked_recovery=blocked_recovery,
        tasks_blocked_auto=blocked_auto,
        tasks_blocked_attn=blocked_attn,
    )


def _backlog_state_rows(b: _BacklogTotals, lifetime: WindowStats | None = None) -> list[tuple[str, str]]:
    """Instantaneous backlog-state rows (task counts, percentages, rollups).

    ``lifetime`` is accepted for backward compatibility with callers that
    pass it, but the Lifetime cost / token / cache-hit rows it used to
    inject were duplicates of the All-time column in the consolidated table
    and have been removed. The argument is ignored; the rows returned here
    carry only instantaneous state that does not belong in a windowed view.
    """
    _ = lifetime  # retained for API compatibility; no longer emits Lifetime rows.
    task_pct = round(PERCENT_MULTIPLIER * b.tasks_done / b.tasks_total) if b.tasks_total else 0
    total_pct = round(PERCENT_MULTIPLIER * b.units_done / b.units_total) if b.units_total else 0
    return [
        ("Tasks completed", f"{b.tasks_done} of {b.tasks_total} ({task_pct}%)"),
        (
            "Work units done (tasks + auto-rolled stories/features/epics)",
            f"{b.units_done} of {b.units_total} ({total_pct}%)",
        ),
        ("Tasks in-progress", str(b.tasks_in_progress)),
        ("Tasks proposed", str(b.tasks_proposed)),
        ("Tasks declined", str(b.tasks_declined)),
        ("Tasks blocked", str(b.tasks_blocked)),
        ("Tasks remaining (total)", str(b.tasks_active + b.tasks_blocked)),
        (
            "Stories / Features / Epics auto-rolled to done",
            f"{b.stories_done} / {b.features_done} / {b.epics_done}",
        ),
    ]


# ---------------------------------------------------------------------------
# Per-section metric grouping for the consolidated progress table
# ---------------------------------------------------------------------------
# The Window stats rows are split into four logical sections: THROUGHPUT,
# API USAGE, TOKENS, COST. Order within each section is preserved from
# ``_METRIC_LABELS``; the ordering in ``_METRIC_LABELS`` is therefore the
# single source of truth for how rows appear in the output.

_SECTION_THROUGHPUT: frozenset[str] = frozenset(
    {
        "Time span",
        "Tasks completed in window",
        "Average time per task",
        f"Recent pace (last {RECENT_PACE_TASKS} tasks)",
        "Est. time to complete remaining",
        "Est. completion date/time",
    }
)

_SECTION_API_USAGE: frozenset[str] = frozenset(
    {
        "API processing time",
        "API utilization",
    }
)

_SECTION_COST: frozenset[str] = frozenset(
    {
        "Estimated cost so far",
        "Estimated total cost at completion",
    }
)

# Every row in ``_METRIC_LABELS`` that is not in THROUGHPUT, API USAGE, or
# COST falls into TOKENS by elimination. That includes the token-breakdown
# rows, Input/output share, Cache hit rate, and Avg tokens per task -- the
# plan's TOKENS section ends with "Avg tokens per task" immediately before
# the COST section begins.


def _section_for_metric(metric: str) -> str:
    """Return the section label ("Throughput"/"API usage"/"Tokens"/"Cost") for a metric row."""
    if metric in _SECTION_THROUGHPUT:
        return "Throughput"
    if metric in _SECTION_API_USAGE:
        return "API usage"
    if metric in _SECTION_COST:
        return "Cost"
    return "Tokens"


def _unit_status_listing(units: list, status: WorkUnitStatus, header: str) -> list[str]:
    """Return `[<blank>, "<header>:", "  - <id>: <title>", ...]` for task units in the given status.

    Returns an empty list when no task matches -- the caller then omits the
    whole section (no empty `In-progress tasks:` header in reports where
    everything is done or everything is blocked, etc.). Listings are task-only
    (unit_type == TASK); parent Story/Feature/Epic units are excluded since
    their status is derived from their children via auto-rollup.
    """
    matches = [u for u in units if u.unit_type == WorkUnitType.TASK and u.status == status]
    if not matches:
        return []
    lines = ["", f"{header}:"]
    lines.extend(f"  - {u.id}: {u.title}" for u in matches)
    return lines


def _in_progress_listing(units: list, log_path: Path | None = None) -> list[str]:
    """B9: list every in-progress task; suffix each row with attempt duration (#158)."""
    matches = [u for u in units if u.unit_type == WorkUnitType.TASK and u.status == WorkUnitStatus.IN_PROGRESS]
    if not matches:
        return []
    from devbench.cli import _in_progress_attempt_duration

    lines = ["", "In-progress tasks:"]
    for u in matches:
        duration = _in_progress_attempt_duration(u.id, log_path)
        suffix = f" (in-progress for {duration})" if duration is not None else " (in-progress, timer unavailable)"
        lines.append(f"  - {u.id}: {u.title}{suffix}")
    return lines


def _blocked_listing(units: list) -> list[str]:
    """Part-1: render blocked tasks in three panels.

    The 3-state classifier sorts each blocked task into one of:

    1. ``Blocked tasks (auto-clearing via proposal)`` -- ADR-07 cascade
       will resolve when every ``[BLOCKED_PENDING_PROPOSAL]`` marker
       target reaches terminal. Operator does nothing.
    2. ``Blocked tasks (auto-recovery in flight)`` -- no marker yet, but
       devbench's recovery loop has an artefact on disk (pending
       proposal JSON, rejected-amendment archive, or recent
       recovery-agent ``[BLOCKED]`` audit comment). The next sweep
       cycle will advance these into panel 1. Operator does nothing
       for now.
    3. ``Blocked tasks (needs operator attention)`` -- the true halt
       list: manual gates (``DO NOT CLAIM``), unknown marker targets,
       cascade-stuck states. Operator must act.

    Each row carries a per-state annotation: panel 1 names the task IDs
    it is waiting on; panel 2 names which recovery signal devbench
    found on disk; panel 3 carries just ID + title. Empty panels are
    omitted entirely so the operator's eye lands on the panels that
    have content.
    """
    from devbench.backlog.proposal import (
        BlockedTaskState,
        classify_blocked_task,
        recovery_signal_for_task,
    )

    blocked_tasks = [u for u in units if u.unit_type == WorkUnitType.TASK and u.status == WorkUnitStatus.BLOCKED]
    if not blocked_tasks:
        return []

    auto_rows: list[tuple] = []  # (unit, list[str] of marker targets)
    recovery_rows: list[tuple] = []  # (unit, signal-source string)
    attn_rows: list = []
    for u in blocked_tasks:
        state = classify_blocked_task(
            BACKLOG_ROOT,
            BACKLOG_INDEX,
            u.id,
            workspace_root=WORKSPACE_ROOT,
        )
        if state is BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL:
            # Surface which task(s) this one is waiting on. Reuse the same
            # marker-extract helper the cascade uses so the string is always
            # accurate.
            from devbench.backlog.manager import BacklogManager

            waiting_on = sorted(BacklogManager()._extract_pending_proposal_markers(u.file_path))
            auto_rows.append((u, waiting_on))
        elif state is BlockedTaskState.AWAITING_AUTO_RECOVERY:
            signal = recovery_signal_for_task(WORKSPACE_ROOT, u.id)
            recovery_rows.append((u, signal))
        else:
            attn_rows.append(u)

    lines: list[str] = []
    if auto_rows:
        lines.append("")
        lines.append(f"Blocked tasks (auto-clearing via proposal) ({len(auto_rows)}):")
        for u, waiting_on in auto_rows:
            suffix = f"    [waiting on {', '.join(waiting_on)}]" if waiting_on else ""
            lines.append(f"  - {u.id}: {u.title}{suffix}")
    if recovery_rows:
        lines.append("")
        lines.append(f"Blocked tasks (auto-recovery in flight) ({len(recovery_rows)}):")
        for u, signal in recovery_rows:
            suffix = f"    [recovery: {signal}]" if signal else ""
            lines.append(f"  - {u.id}: {u.title}{suffix}")
    if attn_rows:
        lines.append("")
        lines.append(f"Blocked tasks (needs operator attention) ({len(attn_rows)}):")
        for u in attn_rows:
            lines.append(f"  - {u.id}: {u.title}")
    return lines


def _proposed_listing(units: list) -> list[str]:
    """List every proposed task-factory draft so the human knows which drafts await review.

    Rendered before the In-progress / Blocked panels so the "waiting on human
    decision" set is front-and-center (task-factory proposals are inert until
    promoted). Omitted entirely when no proposed tasks exist -- the plan
    requires the panel to disappear rather than print an empty "Proposed (0):".
    """
    return _listing_by_status(units, WorkUnitStatus.PROPOSED, "Proposed")


def _declined_listing(units: list) -> list[str]:
    """List every Declined task so the human can audit the decisions.

    Same shape as the Proposed panel -- ``Declined (N):`` followed by one
    ``  <title>    <path>`` line per task. Omitted when no declined tasks.
    """
    return _listing_by_status(units, WorkUnitStatus.DECLINED, "Declined")


def _unmaterialised_proposals_listing() -> list[str]:
    """List every proposal-JSON entry whose draft .md has not yet been created.

    Reads ``<workspace>/.devbench/proposals/*.json`` via ``list_proposals`` and
    filters each ``proposed_tasks[].suggested_id`` through
    ``classify_proposed_task``. Entries in ``UNMATERIALISED`` state get one row
    per task in a dedicated panel so the operator can see at a glance which
    proposal JSONs are waiting for ``devbench sweep-proposals`` /
    ``materialise-proposal`` to produce drafts.

    Omitted entirely when no un-materialised entries exist, mirroring the
    Proposed / Declined panels' empty-state discipline.
    """
    from devbench.backlog.proposal import (
        ProposalTaskState,
        classify_proposed_task,
        list_proposals,
    )

    workspace_root = BACKLOG_ROOT.parent
    entries: list[tuple[str, str, str, str]] = []
    for proposal in list_proposals(workspace_root):
        for task in proposal.proposed_tasks:
            state = classify_proposed_task(BACKLOG_ROOT, workspace_root, task.suggested_id)
            if state is ProposalTaskState.UNMATERIALISED:
                entries.append((task.suggested_id, task.title, proposal.source_task_id, proposal.generated_at))

    if not entries:
        return []

    lines = ["", f"Proposal JSONs pending materialisation ({len(entries)}):"]
    id_col = max(len(sid) for sid, _, _, _ in entries)
    title_col = max(len(title) for _, title, _, _ in entries)
    for sid, title, source, generated in entries:
        lines.append(f"  {sid:<{id_col}}  {title:<{title_col}}  (from {source}, generated {generated})")
    return lines


def _listing_by_status(units: list, status: WorkUnitStatus, label: str) -> list[str]:
    """Shared renderer for the Proposed + Declined title/path panels."""
    matches = [u for u in units if u.unit_type == WorkUnitType.TASK and u.status == status]
    if not matches:
        return []
    lines = ["", f"{label} ({len(matches)}):"]
    title_col = max((len(u.title) for u in matches), default=0)
    for u in matches:
        try:
            rel = u.file_path.relative_to(BACKLOG_ROOT.parent)
        except ValueError:
            rel = u.file_path
        lines.append(f"  {u.title:<{title_col}}    {rel}")
    return lines


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

    # Precedence: report-specific (env JUDGE_REPORT_TIMEZONE > yaml
    # report.display_timezone) > top-level (env JUDGE_DISPLAY_TIMEZONE >
    # yaml display_timezone) > OS local. REPORT_DISPLAY_TIMEZONE already
    # encodes the first pair; DISPLAY_TIMEZONE the second.
    display_tz = _resolve_display_timezone(REPORT_DISPLAY_TIMEZONE or DISPLAY_TIMEZONE)

    # Compute lifetime (since-log-started) stats once. Used to enrich the top
    # box with cost / token / cache-hit summary so the most relevant numbers
    # are visible at a glance without scanning across the wider window-stats table.
    lifetime_stats: WindowStats | None = (
        _compute_window_stats(
            log_path,
            log_start_for_window,
            window_end,
            done_times,
            progress_times,
            backlog.tasks_active,
            backlog.tasks_blocked_recovery,
            backlog.tasks_blocked_auto,
        )
        if log_started is not None
        else None
    )

    lines: list[str] = []

    if since is not None:
        # Single-window mode (legacy API for callers passing --since explicitly).
        # Kept stacked -- the single window block stays beneath the Backlog state
        # box the way older callers / tests expect it.
        backlog_state_block = _render_table("Backlog state", _backlog_state_rows(backlog))
        single_stats = _compute_window_stats(
            log_path,
            since,
            window_end,
            done_times,
            progress_times,
            backlog.tasks_active,
            backlog.tasks_blocked_recovery,
            backlog.tasks_blocked_auto,
        )
        lines.extend(backlog_state_block)
        lines.append("")
        lines.extend(
            _render_table(
                _format_window_title("Window", since, log_started, display_tz),
                _stats_to_rows_single(single_stats, display_tz),
            )
        )
        # Backward-compat label so callers grepping for "Tasks in this session" still find it.
        lines.append(f"\nTasks in this session: {single_stats.tasks_in_window}")
        summary_stats = single_stats
    else:
        # Default: ONE unified grouped table with sections (BACKLOG STATE,
        # THROUGHPUT, API USAGE, TOKENS, COST). The reader scans top-down;
        # the Backlog state rows have only the All-time column populated
        # (Session and This run cells render as blank); the windowed rows
        # populate every column.
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
                        log_path,
                        w.start,
                        window_end,
                        done_times,
                        progress_times,
                        backlog.tasks_active,
                        backlog.tasks_blocked_recovery,
                        backlog.tasks_blocked_auto,
                    )
                )

        column_labels = [_short_window_label(w.label, w.start, display_tz) for w in windows]
        n_cols = len(column_labels)
        value_columns = [_stats_to_value_list(s, display_tz) for s in all_window_stats]
        # Transpose per-window stats into (metric, values) rows and group by
        # section label. Spanning metrics (Recent pace / Est. time) still
        # collapse into a single cell when every column agrees.
        windowed_rows: list[tuple[str, list[str] | str]] = [
            (metric, _merge_spanning_values(metric, [col[i] for col in value_columns]))
            for i, metric in enumerate(_METRIC_LABELS)
        ]
        sections_by_label: dict[str, list[tuple[str, list[str] | str]]] = {
            "Backlog state": [(m, [v, *[""] * (n_cols - 1)]) for m, v in _backlog_state_rows(backlog)],
            "Throughput": [],
            "API usage": [],
            "Tokens": [],
            "Cost": [],
        }
        for metric, values in windowed_rows:
            sections_by_label[_section_for_metric(metric)].append((metric, values))

        sections: list[tuple[str, list[tuple[str, list[str] | str]]]] = [
            ("Backlog state", sections_by_label["Backlog state"]),
            ("Throughput", sections_by_label["Throughput"]),
            ("API usage", sections_by_label["API usage"]),
            ("Tokens", sections_by_label["Tokens"]),
            ("Cost", sections_by_label["Cost"]),
        ]
        lines.extend(_render_grouped_progress_table("Metric", column_labels, sections))
        # Divergence warning: the BACKLOG STATE row "Tasks completed"
        # counts ``Status: done`` rows in BACKLOG.md while the THROUGHPUT
        # row "Tasks completed in window" counts ``Set <id> to 'done'``
        # log lines parsed from ``log_path``. The two MUST agree for a
        # healthy backlog. When backlog state shows completions but the
        # All-time throughput window (which spans the entire log) shows
        # zero, the operator is reading a different log than the one
        # the orchestrator writes to -- typically because
        # ``JUDGE_LOG_FILE`` was unset in the shell that ran
        # ``devbench report`` and the default fell back to the devbench
        # source-tree log. Surface the discrepancy as a one-line
        # warning so the user does not silently misread the table.
        all_time_stats = all_window_stats[0]
        if backlog.tasks_done > 0 and all_time_stats.tasks_in_window == 0:
            lines.append("")
            lines.append(
                f"WARNING: BACKLOG.md shows {backlog.tasks_done} done but log {log_path} shows 0 "
                "-- check JUDGE_LOG_FILE points at the orchestrator's log."
            )
        # Use the All-time stats for the trailing prose projection -- they're the
        # most stable sample. Narrower windows can have zero completed tasks
        # (e.g. just after a restart) which would project meaningless numbers.
        summary_stats = all_window_stats[0]

    lines.append("")
    lines.append(_summary_line(summary_stats, backlog.tasks_active, backlog.tasks_blocked))
    if since is None:
        # B9: per-unit listings at the very end so the user can act on each.
        # Order surfaces the most operationally-actionable panels first
        # (In Progress, then Blocked) and pushes long-tail / decision-only
        # state (Proposed, Unmaterialised Proposals, Declined) to the edges.
        # Declined renders LAST since it represents tasks already taken off
        # the table -- useful as historical reference but not actionable.
        # Each panel is omitted when its respective status has zero tasks.
        lines.extend(_proposed_listing(units))
        lines.extend(_unmaterialised_proposals_listing())
        lines.extend(_in_progress_listing(units, log_path))
        lines.extend(_blocked_listing(units))
        lines.extend(_declined_listing(units))

    return "\n".join(lines)
