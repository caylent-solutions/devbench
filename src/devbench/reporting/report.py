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
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from devbench.actionability import check_actionability
from devbench.backlog.parser import BacklogParser
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType
from devbench.config import (
    BACKLOG_INDEX,
    BACKLOG_ROOT,
    DISPLAY_TIMEZONE,
    RECENT_PACE_TASKS,
    REPORT_CACHE_READ_MULTIPLIER,
    REPORT_CACHE_WRITE_1HR_MULTIPLIER,
    REPORT_CACHE_WRITE_5MIN_MULTIPLIER,
    REPORT_DATA_RESIDENCY_MULTIPLIER,
    REPORT_DEFAULT_MODEL_RATES,
    REPORT_DISPLAY_TIMEZONE,
    REPORT_FAST_MODE_MULTIPLIER,
    REPORT_MODEL_RATES,
    STOP_HOOK_WINDOW_SECONDS,
    WORKSPACE_ROOT,
)
from devbench.constants import (
    DEFAULT_LOG_FILENAME,
    DEFAULT_SESSION_GAP_MINUTES,
    LOG_NOISE_LOGGER_NAME,
    MIN_PACE_SAMPLES,
    MS_PER_SECOND,
    PERCENT_MULTIPLIER,
    SECONDS_PER_HOUR,
    SECONDS_PER_MINUTE,
    SESSION_DRAIN_SIGNAL_FILENAME,
    SIDE_BY_SIDE_GAP_CHARS,
    TOKENS_PER_MILLION,
)
from devbench.instances import is_pid_alive, pid_file_path, read_pid_file
from devbench.reporting.event_index import EventIndex
from devbench.scope import ScopeFilter
from devbench.session import Session, SessionRegistry

if TYPE_CHECKING:
    from devbench.backlog.proposal import BlockedTaskState

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
    # Issue #214: the renderer caps column widths and wraps long
    # breakdowns at " + " boundaries so this value never blows out the
    # table layout, even when all four buckets contribute.
    eta_active: int = 0
    eta_blocked_recovery: int = 0
    eta_blocked_auto: int = 0
    # Issue #183 follow-up: RUNTIME_DEGRADATION tasks auto-recover when
    # the orchestrator restarts (cmd_start exit-42 + Makefile while-loop).
    # They belong in the ETA denominator alongside the other auto-recover
    # buckets so the projection reflects work that WILL get done without
    # operator intervention.
    eta_blocked_runtime_degradation: int = 0

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

    Issue #162: an indexed equivalent
    (``_find_current_session_start_from_index``) is preferred by
    ``generate_report``; this string-input form is kept because the
    existing test suite calls it directly with crafted log payloads
    and because the parity tests use both forms to assert that the
    indexed path produces the same boundary as the parser path.
    """
    events: list[datetime] = []
    for m in _LOG_LINE_RE.finditer(log_text):
        logger_name = m.group(2)
        if logger_name == LOG_NOISE_LOGGER_NAME:
            continue
        events.append(_parse_ts(m.group(1)))

    return _walk_for_session_boundary(events, gap_minutes)


def _find_current_session_start_from_index(
    event_index: EventIndex,
    log_path: Path,
    gap_minutes: int = DEFAULT_SESSION_GAP_MINUTES,
    *,
    workspace_root: Path | None = None,
) -> datetime | None:
    """Indexed equivalent of ``_find_current_session_start`` (issue #162 Phase 1+4).

    Same gap-walking logic; the difference is that the timestamps come
    from indexed SQL queries instead of a regex full-scan of the whole
    log file. The boundary semantics are identical -- the parity test
    ``TestParityAgainstParserPath::test_session_boundary_matches_parser``
    pins this.

    Issue #168: when ``workspace_root`` is provided, the timestamps are
    pulled from the union of orch-log shards + live log so post-Phase-3-
    migration workspaces detect the session boundary across the merged
    history. When ``workspace_root`` is None (legacy callers / tests),
    the single-file query path runs unchanged.
    """
    if workspace_root is not None:
        events = event_index.non_noise_log_timestamps_for_workspace(workspace_root, log_path, LOG_NOISE_LOGGER_NAME)
    else:
        events = event_index.non_noise_log_timestamps(log_path, LOG_NOISE_LOGGER_NAME)
    return _walk_for_session_boundary(events, gap_minutes)


def _walk_for_session_boundary(events: list[datetime], gap_minutes: int) -> datetime | None:
    """Run the gap-walk used by both the parser and indexed session detectors.

    Centralised so the two callers cannot drift; the gap rule and the
    "no events -> None" semantic live in exactly one place.
    """
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


def _resolve_transcript_dir(event_index: EventIndex, hook_log_path: Path) -> Path | None:
    """Issue #162 Phase 1+4: resolve transcript dir via the cache, falling back to file scan.

    The hook-log cache stores ``transcript_path`` for every entry that
    carries one, so the resolver becomes a single SELECT instead of a
    re-read of the hook log every invocation. The fallback path
    triggers only when the cache is empty (first run on this workspace);
    after that one-time scan the index serves the answer.
    """
    cached = event_index.first_hook_transcript_path(hook_log_path)
    if cached:
        return Path(cached).parent
    return _discover_transcript_dir(hook_log_path)


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


def _accumulate_transcript_message(
    message: object, totals_acc: dict[str, int], seen_ids: set[str] | None = None
) -> None:
    """Fold one transcript message's usage into the totals accumulator.

    When ``seen_ids`` is supplied, messages whose ``id`` has already been
    accumulated are skipped. This is the dedup gate for issue #169: Claude
    Code copies prior assistant messages (with their ``usage`` blocks) into
    resumed/forked session transcripts, so summing every ``*.jsonl`` in a
    transcript directory double-counts every message that crossed a resume.
    Messages without a stable ``id`` still accumulate -- the dedup is opt-in
    on the presence of the id rather than enforced absence.
    """
    if not isinstance(message, dict):
        return
    if seen_ids is not None:
        msg_id = message.get("id")
        if isinstance(msg_id, str) and msg_id:
            if msg_id in seen_ids:
                return
            seen_ids.add(msg_id)
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

    Issue #169: dedups by ``message.id`` across all files in the directory.
    Resumed Claude Code sessions copy prior assistant messages forward, so
    the same logical message appears in N files; without dedup that's N-fold
    over-count of token usage and cost.
    """
    totals_acc: dict[str, int] = _empty_totals_acc()
    if transcript_dir is None or not transcript_dir.is_dir():
        return HookLogTotals(**totals_acc)

    seen_ids: set[str] = set()
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
            _accumulate_transcript_message(entry.get("message"), totals_acc, seen_ids)

    return HookLogTotals(**totals_acc)


_ROLE_ORCHESTRATOR = "orchestrator"


def _role_for_entry(entry: dict) -> str:
    """Return the per-role bucket for one transcript entry (issue #123).

    Each Claude Code transcript message carries an ``attributionAgent`` field
    naming the active agent (e.g. ``"devbench-orchestrate:executor"``,
    ``"devbench-orchestrate:code-reviewer"``). Messages emitted by the outer orchestrator
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

    Issue #169: dedups by ``message.id`` across all files in the directory.
    The dedup set is shared across roles so the per-role buckets sum to the
    same deduped aggregate that ``_parse_transcript_metrics`` returns.
    """
    if transcript_dir is None or not transcript_dir.is_dir():
        return {}

    seen_ids: set[str] = set()
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
            _accumulate_transcript_message(entry.get("message"), acc, seen_ids)
    return {role: HookLogTotals(**acc) for role, acc in by_role.items()}


def _combine_many(parts: Iterable[HookLogTotals]) -> HookLogTotals:
    """Sum any iterable of ``HookLogTotals`` into one (empty -> zero).

    Issue #223 helper: per-model aggregation produces N buckets that some
    downstream renderers still want as a single roll-up; reuse the
    pairwise ``_combine_totals`` here so the field-list stays in one place.
    """
    aggregate = HookLogTotals()
    for part in parts:
        aggregate = _combine_totals(aggregate, part)
    return aggregate


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


def _resolve_rates_for_model(model_id: str) -> tuple[float, float, float, float, float, float]:
    """Return ``(input_rate, output_rate, cache_read_mult, cache_5m_mult, cache_1h_mult, correction)``.

    Issue #223 lookup: per-model rates from ``REPORT_MODEL_RATES`` win for
    the four scalar pricing fields; per-model cache multiplier overrides
    (when set) win, otherwise the top-level ``REPORT_CACHE_*_MULTIPLIER``
    defaults apply.  An unknown ``model_id`` falls back to
    ``REPORT_DEFAULT_MODEL_RATES`` -- the canonical pricing for the
    ``"<unknown>"`` aggregation bucket.
    """
    rates = REPORT_MODEL_RATES.get(model_id, REPORT_DEFAULT_MODEL_RATES)
    cache_read = (
        rates.cache_read_multiplier if rates.cache_read_multiplier is not None else REPORT_CACHE_READ_MULTIPLIER
    )
    cache_5m = (
        rates.cache_write_5min_multiplier
        if rates.cache_write_5min_multiplier is not None
        else REPORT_CACHE_WRITE_5MIN_MULTIPLIER
    )
    cache_1h = (
        rates.cache_write_1hr_multiplier
        if rates.cache_write_1hr_multiplier is not None
        else REPORT_CACHE_WRITE_1HR_MULTIPLIER
    )
    return (rates.input, rates.output, cache_read, cache_5m, cache_1h, rates.correction_factor)


def _compute_cost_by_model(
    totals_by_model: dict[str, HookLogTotals],
    *,
    data_residency_multiplier: float = 1.0,
    fast_mode_multiplier: float = 1.0,
) -> CostBreakdown:
    """Sum per-model cost across every model id observed in the window (issue #223).

    For each model id, look up its rates via ``_resolve_rates_for_model``
    and price that bucket via ``_compute_cost``.  Each model's contribution
    is multiplied by its own ``correction_factor`` BEFORE being added to
    the aggregate so per-model contract corrections compose cleanly.

    Returns a single aggregate ``CostBreakdown`` whose per-bucket totals
    sum to ``total_cost`` (the existing invariant ``_compute_cost``'s
    callers rely on).  The ``"<unknown>"`` bucket -- transcript messages
    with no ``model`` field, plus model ids not present in
    ``REPORT_MODEL_RATES`` -- is priced against
    ``REPORT_DEFAULT_MODEL_RATES``.

    The two side-channel multipliers (data-residency and fast-mode) apply
    inside each per-model call, so a residency premium on a Sonnet bucket
    multiplies the Sonnet input rate, not the Opus input rate.
    """
    aggregate = CostBreakdown(
        input_cost=0.0,
        output_cost=0.0,
        cache_read_cost=0.0,
        cache_write_5m_cost=0.0,
        cache_write_1h_cost=0.0,
        total_cost=0.0,
    )
    for model_id, totals in totals_by_model.items():
        input_rate, output_rate, cache_read, cache_5m, cache_1h, correction = _resolve_rates_for_model(model_id)
        bucket = _compute_cost(
            totals,
            input_rate,
            output_rate,
            cache_read,
            cache_5m,
            cache_1h,
            data_residency_multiplier=data_residency_multiplier,
            fast_mode_multiplier=fast_mode_multiplier,
        )
        if correction != 1.0:
            bucket = CostBreakdown(
                input_cost=bucket.input_cost * correction,
                output_cost=bucket.output_cost * correction,
                cache_read_cost=bucket.cache_read_cost * correction,
                cache_write_5m_cost=bucket.cache_write_5m_cost * correction,
                cache_write_1h_cost=bucket.cache_write_1h_cost * correction,
                total_cost=bucket.total_cost * correction,
            )
        aggregate = CostBreakdown(
            input_cost=aggregate.input_cost + bucket.input_cost,
            output_cost=aggregate.output_cost + bucket.output_cost,
            cache_read_cost=aggregate.cache_read_cost + bucket.cache_read_cost,
            cache_write_5m_cost=aggregate.cache_write_5m_cost + bucket.cache_write_5m_cost,
            cache_write_1h_cost=aggregate.cache_write_1h_cost + bucket.cache_write_1h_cost,
            total_cost=aggregate.total_cost + bucket.total_cost,
        )
    return aggregate


def _recent_per_task_cost(
    log_path: Path,
    done_times: dict[str, datetime],
    progress_times: dict[str, datetime],
    n: int,
    *,
    event_index: EventIndex | None = None,
) -> float | None:
    """Cost per task averaged over the most-recent ``n`` task completions, log-wide.

    Issue #164: the legacy cost-projection denominator was the per-window
    completion count, which produced different "Estimated total cost at
    completion" numbers per column for the same physical workspace. The
    correct denominator is a global rate -- the same approach the existing
    ``_recent_pace_minutes`` already uses for the time projection.

    Implementation: take the most-recent ``n`` task completions log-wide
    (where each must have both a ``progress`` and a ``done`` timestamp),
    determine the umbrella interval ``[earliest_progress, now]``, sum the
    hook + transcript token costs across that interval, and divide by
    ``n``. The umbrella interval is intentionally open-ended on the upper
    side because the underlying ``aggregate_*_window`` helpers only take
    a ``window_start`` parameter; the slight overcount of any cost that
    falls AFTER the latest done timestamp is tolerable because (a) the
    orchestrator is between tasks at that moment and (b) the cost still
    belongs to the orchestrate session under measurement.

    Returns None when fewer than ``n`` task completions have valid
    progress + done pairs (callers fall back to the per-window average,
    matching the existing ``recent_pace_minutes`` fallback contract).
    """
    task_done = [(tid, ts) for tid, ts in done_times.items() if "-T" in tid and tid in progress_times]
    if len(task_done) < n:
        return None
    task_done.sort(key=lambda kv: kv[1], reverse=True)
    recent_n = task_done[:n]
    earliest_progress = min(progress_times[tid] for tid, _ in recent_n)

    hook_log_path = _hook_log_path(log_path)
    if event_index is not None:
        transcript_dir_indexed = _resolve_transcript_dir(event_index, hook_log_path)
        totals_hook_by_model = _per_model_totals_from_aggregator(
            event_index.aggregate_hook_window_by_model, hook_log_path, earliest_progress
        )
        totals_transcript_by_model = _per_model_totals_from_aggregator(
            event_index.aggregate_transcript_window_by_model, transcript_dir_indexed, earliest_progress
        )
    else:
        # Non-indexed fallback: parser path doesn't yet bucket by model, so
        # every entry collapses to the ``"<unknown>"`` bucket priced against
        # ``REPORT_DEFAULT_MODEL_RATES``.  The indexed path (the normal
        # case for any real workspace) preserves per-model attribution.
        totals_hook = _parse_hook_log_metrics(log_path, earliest_progress)
        transcript_dir = _discover_transcript_dir(hook_log_path)
        totals_transcript = _parse_transcript_metrics(transcript_dir, earliest_progress)
        totals_hook_by_model = {"<unknown>": totals_hook}
        totals_transcript_by_model = {"<unknown>": totals_transcript}
    totals_by_model = _merge_totals_by_model(totals_hook_by_model, totals_transcript_by_model)
    cost = _compute_cost_by_model(
        totals_by_model,
        data_residency_multiplier=REPORT_DATA_RESIDENCY_MULTIPLIER,
        fast_mode_multiplier=REPORT_FAST_MODE_MULTIPLIER,
    )
    return cost.total_cost / n


def _per_model_totals_from_aggregator(
    aggregator: Callable[..., dict[str, dict[str, int]]],
    source: object,
    window_start: datetime,
) -> dict[str, HookLogTotals]:
    """Wrap an event-index per-model aggregator result into ``HookLogTotals``.

    Issue #223.  The aggregator returns ``{model_id -> totals_dict}``;
    this helper materialises each ``totals_dict`` as a frozen
    ``HookLogTotals``.  Empty source -> empty dict (callers iterate so
    no cost is contributed, which matches the pre-#223 single-bucket
    "no rows" semantic).
    """
    raw = aggregator(source, window_start)
    return {model_id: HookLogTotals(**totals_dict) for model_id, totals_dict in raw.items()}


def _merge_totals_by_model(
    *per_model_buckets: dict[str, HookLogTotals],
) -> dict[str, HookLogTotals]:
    """Sum per-model totals across one or more source buckets.

    Used to combine ``hook_entries`` and ``transcript_entries`` per-model
    aggregates into a single ``{model_id -> HookLogTotals}`` view that
    feeds ``_compute_cost_by_model``.  Each model id contributes its
    summed ``HookLogTotals`` across every source bucket it appears in.
    """
    merged: dict[str, HookLogTotals] = {}
    for bucket in per_model_buckets:
        for model_id, totals in bucket.items():
            if model_id in merged:
                merged[model_id] = _combine_totals(merged[model_id], totals)
            else:
                merged[model_id] = totals
    return merged


def _compute_window_stats(
    log_path: Path,
    window_start: datetime,
    window_end: datetime,
    done_times: dict[str, datetime],
    progress_times: dict[str, datetime],
    tasks_active: int,
    tasks_blocked_recovery: int = 0,
    tasks_blocked_auto: int = 0,
    tasks_blocked_runtime_degradation: int = 0,
    *,
    event_index: EventIndex | None = None,
    recent_per_task_cost: float | None = None,
    lifetime_total_cost: float | None = None,
) -> WindowStats:
    """Compute all time-windowed statistics for a single window.

    Issue #157 + issue #183 follow-up: the ETA denominator includes
    blocked tasks that devbench will recover on its own --
    ``tasks_blocked_recovery`` (AWAITING_AMENDMENT_RECOVERY +
    AWAITING_DEPENDENCY), ``tasks_blocked_auto`` (AUTO_CLEARING_VIA_PROPOSAL),
    and ``tasks_blocked_runtime_degradation`` (RUNTIME_DEGRADATION --
    `make start`'s auto-restart loop clears these without operator
    intervention) -- in addition to ``tasks_active``. The
    operator-attention bucket stays excluded since those represent
    genuine halts with unbounded ETA. When the recent-pace window has
    fewer than ``MIN_PACE_SAMPLES`` completed tasks the pace fallback
    path is taken; ``est_hours`` reads zero (renderer shows "n/a").

    Issue #162 Phase 1+4: when ``event_index`` is supplied, hook-log
    and transcript aggregations are served by indexed SQL range scans
    instead of full-file re-parses. When ``event_index`` is ``None``
    the legacy parser path runs unchanged so direct callers (the
    existing test suite, ad-hoc invocations) keep their previous
    behaviour. Both paths produce identical output for the same input
    -- the parity is asserted by the regression tests in
    ``test_event_index.py::TestParityAgainstParserPath``.
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
    eta_task_count = tasks_active + tasks_blocked_recovery + tasks_blocked_auto + tasks_blocked_runtime_degradation
    est_hours = (eta_task_count * pace_for_projection) / SECONDS_PER_MINUTE if pace_for_projection else 0.0

    # Combine usage from two sources, both filtered by window_start:
    #   1. hook-logs.jsonl: subagent (Agent tool) invocations -- captures executor / judge / etc costs
    #   2. Claude Code transcripts: per-turn outer-session reasoning -- captures what the orchestrate
    #      skill itself spends between Agent calls. Without these, cost can be off by 10-20x.
    # Issue #162: when ``event_index`` is supplied the totals come from
    # indexed SQL range-scans of the persistent cache (~ms cost). When
    # ``event_index`` is None the legacy full-file parsers run.
    hook_log_path = _hook_log_path(log_path)
    if event_index is not None:
        transcript_dir_indexed = _resolve_transcript_dir(event_index, hook_log_path)
        totals_hook_by_model = _per_model_totals_from_aggregator(
            event_index.aggregate_hook_window_by_model, hook_log_path, window_start
        )
        totals_transcript_by_model = _per_model_totals_from_aggregator(
            event_index.aggregate_transcript_window_by_model, transcript_dir_indexed, window_start
        )
    else:
        # Non-indexed fallback: see ``_recent_per_task_cost`` for the
        # equivalent ``"<unknown>"`` collapse rationale.
        totals_hook = _parse_hook_log_metrics(log_path, window_start)
        transcript_dir = _discover_transcript_dir(hook_log_path)
        totals_transcript = _parse_transcript_metrics(transcript_dir, window_start)
        totals_hook_by_model = {"<unknown>": totals_hook}
        totals_transcript_by_model = {"<unknown>": totals_transcript}
    totals_by_model = _merge_totals_by_model(totals_hook_by_model, totals_transcript_by_model)
    # Aggregate totals across every model id for downstream renderers that
    # still need a single ``HookLogTotals``-shaped view of the window.
    totals = _combine_many(totals_by_model.values())
    # Per-model cost: each model id is priced against its own rates; the
    # per-model contract correction factor (issue #223) composes inside the
    # helper.  The premium multipliers (residency / fast-mode) apply
    # inside each per-model call so they multiply the correct base rate.
    cost = _compute_cost_by_model(
        totals_by_model,
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
    # Issue #164: cost projection now uses a GLOBAL recent-pace per-task
    # rate (computed once log-wide by ``_recent_per_task_cost`` and passed
    # in by the caller) instead of the window-specific ``tasks_in_window``
    # denominator. The window-specific denominator was producing wildly
    # different "Estimated total cost at completion" numbers per column
    # (e.g. $13k all-time vs $42k session vs $8 this-run) for the same
    # physical workspace; completion is one global event, not three.
    # Fallback chain: recent-pace global rate (most accurate) ->
    # window's own per-task average (legacy behaviour, matches the
    # per-pace-minutes fallback path) -> zero (no data).
    #
    # Spanning-row follow-up: the multiplier above is global, but the
    # ADDITIVE base ``cost.total_cost`` is window-scoped, so per-column
    # est_total_cost values still diverged by exactly the cost-so-far
    # delta between windows (All-time spend > Session spend > This-run
    # spend). The render-side ``_merge_spanning_values`` collapse only
    # fires when every column produces an identical string, so the row
    # never collapsed in practice. Threading ``lifetime_total_cost``
    # (the All-time cost.total_cost, computed once by ``generate_report``
    # before any narrower window) gives every column the same additive
    # base; the projection becomes a single global number, the spanning
    # collapse fires, and the report expresses the underlying truth:
    # one global completion, one cost. ``lifetime_total_cost=None``
    # preserves the legacy formula for direct test callers that exercise
    # ``_compute_window_stats`` in isolation.
    if recent_per_task_cost is not None:
        per_task_cost = recent_per_task_cost
    elif tasks_in_window:
        per_task_cost = cost.total_cost / tasks_in_window
    else:
        per_task_cost = 0.0
    additive_base = lifetime_total_cost if lifetime_total_cost is not None else cost.total_cost
    est_total_cost = additive_base + per_task_cost * eta_task_count

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
        eta_blocked_runtime_degradation=tasks_blocked_runtime_degradation,
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
_COLOR_YELLOW = "\033[33m"
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


def _format_duration(seconds: float) -> str:
    """Render a wall-clock duration in human-friendly form (issue #158).

    Output forms: ``42s``, ``23m``, ``1h 47m``, ``2d 3h``. Negative
    inputs collapse to ``0s`` so a clock-skew artefact never raises.
    """
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    minutes = s // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    rem_min = minutes % 60
    if hours < 24:
        return f"{hours}h {rem_min}m"
    days = hours // 24
    rem_hours = hours % 24
    return f"{days}d {rem_hours}h"


_LIVENESS_TAIL_BYTES = 4096


def _read_last_log_timestamp(log_path: Path) -> datetime | None:
    """Tail-read the last parseable log timestamp without loading the whole file.

    Reads at most the trailing ``_LIVENESS_TAIL_BYTES`` of ``log_path`` and
    returns the parsed timestamp of the last log line in that window.
    Returns ``None`` when the file is missing, empty, or contains no
    parseable log line in the tail window.
    """
    if not log_path.is_file():
        return None
    try:
        size = log_path.stat().st_size
    except OSError:
        return None
    if size == 0:
        return None
    try:
        with log_path.open("rb") as f:
            if size > _LIVENESS_TAIL_BYTES:
                f.seek(-_LIVENESS_TAIL_BYTES, os.SEEK_END)
            tail = f.read()
    except OSError:
        return None
    text = tail.decode("utf-8", errors="replace")
    matches = list(_LOG_LINE_RE.finditer(text))
    if not matches:
        return None
    return _parse_ts(matches[-1].group(1))


def _orchestrator_liveness_banner(
    log_path: Path,
    session_id: str | None,
    threshold_seconds: int,
    *,
    pid_file: Path | None = None,
    display_tz: tzinfo | None = None,
    now: datetime | None = None,
) -> str:
    """Render a one-line orchestrator-alive status banner (issue #161, #250).

    Three states derived from live-PID presence (issue #250):
      * **ALIVE** (green) -- a live PID exists AND the log has a parseable
        timestamp.
      * **STOPPED** (red) -- no live PID, regardless of log recency.  Banner
        includes the elapsed-since duration and last-seen timestamp when a
        parseable log line exists.
      * **STARTING** (yellow) -- a live PID exists but no parseable log line
        has been written yet (log missing, empty, or tail is an untimestamped
        traceback).

    An untimestamped traceback tail (non-empty file, ``_read_last_log_timestamp``
    returns ``None``) is never treated as ALIVE -- it maps to STARTING when a
    live PID exists, or STOPPED when none does.

    ANSI colour is emitted only when stdout is a TTY and ``NO_COLOR`` is unset
    (mirrors ``_should_use_color``); pipes and CI redirects receive plain text.

    The ``threshold_seconds`` parameter is retained for the STOPPED-state
    elapsed-since display but no longer governs the ALIVE/STOPPED decision.

    Args:
        log_path: Path to the structured orchestrator log.
        session_id: Optional ``DEVBENCH_ORCHESTRATOR_SESSION_ID`` value.  When
            empty/None the banner suppresses the trailing ``-- session ...``
            suffix.
        threshold_seconds: Used only for display of the elapsed-since duration
            in the STOPPED state.  Sourced from ``stop_hook.window_seconds``.
        pid_file: Path to the orchestrator PID file.  When ``None`` the banner
            falls back to STOPPED (safe default -- no evidence of a live
            process).
        display_tz: Display-timezone for the STOPPED-state last-seen timestamp.
            ``None`` falls back to system local.
        now: Override for the current wall-clock (test injection point).
    """
    current = now if now is not None else datetime.now(UTC)
    suffix = f" -- session {session_id}" if session_id else ""

    # Determine whether a live orchestrator process exists.
    live_pid = False
    if pid_file is not None:
        inst = read_pid_file(pid_file)
        if inst is not None:
            live_pid = is_pid_alive(inst.pid)

    last_ts = _read_last_log_timestamp(log_path)

    if not live_pid:
        # No live PID -- always STOPPED, regardless of log recency.
        if last_ts is not None:
            delta = max(0.0, (current - last_ts).total_seconds())
            seen = _format_local_timestamp(last_ts, display_tz)
            body = f"[ORCHESTRATOR STOPPED] no activity for {_format_duration(delta)} (last seen {seen})"
        else:
            # No parseable log line; use the configured quiet window as a
            # lower-bound estimate of how long the orchestrator has been idle.
            min_quiet = _format_duration(threshold_seconds)
            body = f"[ORCHESTRATOR STOPPED] no activity recorded; quiet for at least {min_quiet}"
        color = _COLOR_RED_LIGHT
    elif last_ts is None:
        # Live PID but no parseable log line yet (empty file, missing file,
        # or untimestamped traceback tail) -- STARTING.
        body = "[ORCHESTRATOR STARTING] log file empty; no activity recorded yet"
        color = _COLOR_YELLOW
    else:
        # Live PID and parseable log line -- ALIVE.
        delta = max(0.0, (current - last_ts).total_seconds())
        body = f"[ORCHESTRATOR ALIVE] last activity {_format_duration(delta)} ago"
        color = _COLOR_GREEN

    line = body + suffix
    if not _should_use_color():
        return line
    return f"{color}{line}{_COLOR_RESET}"


def _session_drain_pending(session: Session) -> bool:
    """Return ``True`` when *session* has a pending drain signal.

    Reads the per-session drain signal file directly from the session's
    ``state_dir`` (``<state_dir>/drain.signal``) -- the SAME file
    ``devbench sessions`` consults via ``_session_drain_state_str`` and the
    same per-session path :func:`devbench.drain.resolve_drain_signal_path`
    writes for a named session.  The check is independent of
    ``DEVBENCH_SESSION_NAME`` so it is accurate regardless of the reporting
    process's environment (issue: report banner not session-aware).

    Args:
        session: The :class:`~devbench.session.Session` to inspect.

    Returns:
        ``True`` when a drain signal file exists in the session's state dir;
        ``False`` otherwise.
    """
    return (session.state_dir / SESSION_DRAIN_SIGNAL_FILENAME).exists()


def _session_log_path(session: Session) -> Path:
    """Return the per-session orchestrator log path for *session*.

    Mirrors ``cmd_report --session`` log resolution
    (``<state_dir>/orchestrator.log``) so the banner's last-activity recency
    is computed against the SAME per-session log the orchestrator writes,
    never the shared aggregate log.

    Args:
        session: The :class:`~devbench.session.Session` to inspect.

    Returns:
        Absolute :class:`~pathlib.Path` of the session's orchestrator log.
    """
    return session.state_dir / DEFAULT_LOG_FILENAME


def _render_one_session_banner(
    session: Session,
    *,
    live_pid: bool,
    drain_pending: bool,
    threshold_seconds: int,
    display_tz: tzinfo | None,
    now: datetime,
) -> str:
    """Render ONE ``[SESSION <name> STATE]`` banner line for a single session.

    The state machine mirrors :func:`_orchestrator_liveness_banner` but is
    driven by THIS session's own PID liveness (already resolved by the caller
    via :meth:`~devbench.session.SessionRegistry.is_alive`) and THIS session's
    own per-session log recency, with an extra DRAINING state:

      * **ALIVE** -- live PID + parseable log line, no drain pending.
      * **DRAINING** -- live PID + drain signal present.  Surfaces the same
        ``DRAIN=pending`` state ``devbench sessions`` shows.  The line carries
        an explicit ``drain=pending`` marker.
      * **STARTING** -- live PID but no parseable log line yet.
      * **STOPPED** -- no live PID, regardless of log recency.

    Args:
        session: The session whose line to render.
        live_pid: Whether this session's PID is alive (caller-resolved).
        drain_pending: Whether this session has a pending drain signal.
        threshold_seconds: Display-only lower bound for the STOPPED elapsed.
        display_tz: Display timezone for the STOPPED last-seen timestamp.
        now: Current wall-clock for elapsed-since math.

    Returns:
        A single banner line (ANSI-coloured only when colour is enabled).
    """
    last_ts = _read_last_log_timestamp(_session_log_path(session))
    name = session.name

    if not live_pid:
        if last_ts is not None:
            delta = max(0.0, (now - last_ts).total_seconds())
            seen = _format_local_timestamp(last_ts, display_tz)
            body = f"[SESSION {name} STOPPED] no activity for {_format_duration(delta)} (last seen {seen})"
        else:
            min_quiet = _format_duration(threshold_seconds)
            body = f"[SESSION {name} STOPPED] no activity recorded; quiet for at least {min_quiet}"
        color = _COLOR_RED_LIGHT
    elif last_ts is None:
        # Live PID but no parseable log line yet -- STARTING.  A pending drain
        # is still surfaced so the operator sees it even before first activity.
        body = f"[SESSION {name} STARTING] log file empty; no activity recorded yet"
        if drain_pending:
            body += " -- drain=pending"
        color = _COLOR_YELLOW
    else:
        delta = max(0.0, (now - last_ts).total_seconds())
        if drain_pending:
            # Live + draining: DRAINING state with an explicit marker so the
            # banner agrees with ``devbench sessions`` (DRAIN=pending).
            body = f"[SESSION {name} DRAINING] last activity {_format_duration(delta)} ago -- drain=pending"
            color = _COLOR_YELLOW
        else:
            body = f"[SESSION {name} ALIVE] last activity {_format_duration(delta)} ago"
            color = _COLOR_GREEN

    if not _should_use_color():
        return body
    return f"{color}{body}{_COLOR_RESET}"


def _session_banner_lines(
    workspace_root: Path,
    threshold_seconds: int,
    *,
    display_tz: tzinfo | None = None,
    now: datetime | None = None,
) -> list[str] | None:
    """Render one banner line per registered session, or ``None`` when none.

    Reads the session registry that ``devbench sessions`` reads
    (:class:`~devbench.session.SessionRegistry`, backed by
    ``.devbench/sessions/registry.json``) and, for each registered session,
    emits one ``[SESSION <name> STATE]`` line reflecting THAT session's own
    PID liveness (:meth:`~devbench.session.SessionRegistry.is_alive`), its own
    per-session log recency, and its own drain state.  This is the
    session-aware replacement for the single global
    :func:`_orchestrator_liveness_banner` line in multi-session runs: no
    single aggregate ``[ORCHESTRATOR STOPPED]`` line appears while any session
    daemon is alive, because each session is evaluated independently.

    Liveness, registry, and drain reads all delegate to the SAME helpers the
    ``devbench sessions`` command uses, so the banner cannot drift from
    ``devbench sessions`` output.

    Args:
        workspace_root: Devbench workspace root (the registry lives under
            ``<workspace_root>/.devbench/sessions/``).
        threshold_seconds: Display-only lower bound for the STOPPED elapsed.
        display_tz: Display timezone for STOPPED last-seen timestamps.
        now: Override for the current wall-clock (test injection point).

    Returns:
        A list of per-session banner lines (one per registered session,
        ordered as the registry stores them), or ``None`` when the registry
        is absent or empty -- the signal for the caller to fall back to the
        single-line :func:`_orchestrator_liveness_banner`.
    """
    registry = SessionRegistry(workspace_root)
    sessions = registry.load()
    if not sessions:
        return None

    current = now if now is not None else datetime.now(UTC)
    lines: list[str] = []
    for session in sessions:
        lines.append(
            _render_one_session_banner(
                session,
                live_pid=registry.is_alive(session.pid),
                drain_pending=_session_drain_pending(session),
                threshold_seconds=threshold_seconds,
                display_tz=display_tz,
                now=current,
            )
        )
    return lines


def _resolve_banner_lines(log_path: Path, display_tz: tzinfo | None) -> list[str]:
    """Resolve the liveness banner line(s) prepended to ``devbench report``.

    Session-aware: when the session registry holds >=1 session, returns one
    ``[SESSION <name> ...]`` line per session (each reflecting that session's
    own PID liveness, log recency, and drain state) so a single global
    ``[ORCHESTRATOR STOPPED]`` line never appears while another session daemon
    is alive.  When no registry exists, returns a one-element list with the
    classic single-line ``[ORCHESTRATOR ...]`` banner (back-compat for
    single-session / no-registry runs).

    Extracted from :func:`generate_report` so the report body's branch count
    stays under the lint ceiling and the banner-resolution policy lives in one
    place (SRP).

    Args:
        log_path: Path to the orchestrator log used by the single-line
            fallback's last-activity recency.
        display_tz: Display timezone for STOPPED last-seen timestamps.

    Returns:
        A non-empty list of banner lines ready to prepend to the report.
    """
    session_lines = _session_banner_lines(
        WORKSPACE_ROOT,
        STOP_HOOK_WINDOW_SECONDS,
        display_tz=display_tz,
    )
    if session_lines is not None:
        return session_lines

    session_id = os.environ.get("DEVBENCH_ORCHESTRATOR_SESSION_ID", "").strip() or None
    return [
        _orchestrator_liveness_banner(
            log_path=log_path,
            session_id=session_id,
            threshold_seconds=STOP_HOOK_WINDOW_SECONDS,
            pid_file=pid_file_path(WORKSPACE_ROOT),
            display_tz=display_tz,
        )
    ]


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


#: Issue #214: cap any value column at this width.  Cells longer than the
#: cap wrap onto multiple physical lines (see :func:`_wrap_cell_value`).
#: 50 chars accommodates the longest natural ETA breakdown segment
#: (``blocked-runtime-degradation NN``) with room for the prefix / suffix
#: without exceeding a comfortable terminal column width.
MAX_VALUE_COL_WIDTH: int = 50


def _longest_word_len(text: str) -> int:
    """Length of the longest whitespace-delimited token in ``text``.

    Used as a per-column floor so :func:`_wrap_cell_value` never has to
    break a word mid-character (which would mangle identifiers like
    ``blocked-runtime-degradation``).
    """
    return max((len(w) for w in text.split()), default=0)


def _word_wrap(text: str, max_width: int) -> list[str]:
    """Greedy word-wrap: lines of at most ``max_width`` chars; never breaks
    a word.  When a single word exceeds ``max_width`` it occupies its own
    line at its natural length (the renderer's column-width floor ensures
    the column is wide enough to hold it without truncation).
    """
    words = text.split()
    if not words:
        return [text]
    lines: list[str] = []
    current = words[0]
    for w in words[1:]:
        candidate = f"{current} {w}"
        if len(candidate) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = w
    lines.append(current)
    return lines


def _wrap_cell_value(text: str, max_width: int) -> list[str]:
    """Wrap ``text`` onto multiple lines, each at most ``max_width`` chars.

    Strategy (#214):

    1. Split on `` + `` boundaries -- the natural ETA-breakdown separator.
       Continuation segments are prefixed with ``+ `` so the line break is
       visually unambiguous.
    2. Greedy: build each line by accumulating segments until the next
       would push the line past ``max_width``.
    3. If any built line is still too wide, word-wrap that line internally
       on whitespace.
    4. Never break a word: a single token longer than ``max_width`` occupies
       its own line at its natural length.  Callers must size the column
       to :func:`_longest_word_len` so it still fits the table border.
    """
    if max_width <= 0 or len(text) <= max_width:
        return [text]
    plus_segments = text.split(" + ")
    if len(plus_segments) > 1:
        prefixed = [plus_segments[0]] + [f"+ {p}" for p in plus_segments[1:]]
        lines: list[str] = []
        current = ""
        for seg in prefixed:
            if not current:
                current = seg
                continue
            candidate = f"{current} {seg}"
            if len(candidate) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = seg
        if current:
            lines.append(current)
    else:
        lines = [text]
    refined: list[str] = []
    for ln in lines:
        if len(ln) <= max_width:
            refined.append(ln)
        else:
            refined.extend(_word_wrap(ln, max_width))
    return refined


def _compute_value_widths(
    column_labels: list[str],
    rows_iter: list[tuple[str, list[str] | str]],
    default_min: int,
    max_width: int = MAX_VALUE_COL_WIDTH,
) -> list[int]:
    """Compute per-column value widths so a wide cell in one column does NOT
    inflate the other columns (#214).

    Each column's natural width is the max of ``default_min``, that column's
    label length, and the longest cell observed in that column across
    non-spanning rows.  The natural width is then capped at ``max_width`` --
    but never below the longest single (unbreakable) word found in the column,
    so :func:`_wrap_cell_value` never has to break a word mid-character.

    Spanning rows (value is a single ``str``) are handled separately by
    :func:`_widen_for_spanning`.
    """
    n_cols = len(column_labels)
    natural: list[int] = [max(default_min, len(label)) for label in column_labels]
    word_floor: list[int] = [max(default_min, len(label)) for label in column_labels]
    for _, vals in rows_iter:
        if isinstance(vals, list):
            for i in range(min(n_cols, len(vals))):
                natural[i] = max(natural[i], len(vals[i]))
                word_floor[i] = max(word_floor[i], _longest_word_len(vals[i]))
    return [max(word_floor[i], min(natural[i], max_width)) for i in range(n_cols)]


def _widen_for_spanning(value_widths: list[int], max_spanning: int) -> list[int]:
    """Widen every value column uniformly so the joined span fits a
    spanning value of length ``max_spanning`` (#214).

    Joined span width formula (mirrors ``spanning_w`` in the renderers):
    ``spanning_w = sum(w + 2 for w in value_widths) + (n_cols - 1) - 2``.
    When ``spanning_w < max_spanning``, distribute the deficit by adding
    ``deficit // n_cols`` to every column and ``+1`` to the first
    ``deficit % n_cols`` columns -- preserving the relative ordering
    established by :func:`_compute_value_widths`.
    """
    if max_spanning <= 0 or not value_widths:
        return value_widths
    n_cols = len(value_widths)
    current_span = sum(w + 2 for w in value_widths) + (n_cols - 1) - 2
    deficit = max_spanning - current_span
    if deficit <= 0:
        return value_widths
    bump = deficit // n_cols
    remainder = deficit - bump * n_cols
    new_widths = [w + bump for w in value_widths]
    for i in range(remainder):
        new_widths[i] += 1
    return new_widths


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

    # Issue #214: per-column widths so one wide cell does NOT inflate every
    # column.  Each column's natural width is max(default, label, own cells).
    # Spanning rows are handled afterwards by widening columns uniformly.
    value_widths = _compute_value_widths(column_labels, rows, default_min=value_w)
    max_spanning = max(
        (len(vals) for _, vals in rows if isinstance(vals, str)),
        default=0,
    )
    value_widths = _widen_for_spanning(value_widths, max_spanning)

    # Width a spanning cell occupies (covers all n_cols value columns plus the
    # n_cols-1 internal "│" separators that would otherwise split them).
    spanning_w = sum(w + 2 for w in value_widths) + (n_cols - 1) - 2

    def hborder(left: str, junction_metric: str, junction_inner: str, right: str) -> str:
        inner_runs = [("\u2500" * (w + 2)) for w in value_widths]
        joined = junction_inner.join(inner_runs)
        return left + "\u2500" * (metric_w + 2) + junction_metric + joined + right

    border_top = hborder("\u250c", "\u252c", "\u252c", "\u2510")
    border_mid = hborder("\u251c", "\u253c", "\u253c", "\u2524")
    border_bot = hborder("\u2514", "\u2534", "\u2534", "\u2518")

    header_cells = [f" {title:<{metric_w}} "] + [
        f" {label:>{value_widths[i]}} " for i, label in enumerate(column_labels)
    ]
    header_line = "\u2502" + "\u2502".join(header_cells) + "\u2502"

    lines: list[str] = [border_top, header_line, border_mid]
    for i, (metric, values) in enumerate(rows):
        if i > 0:
            lines.append(border_mid)
        if isinstance(values, str):
            # Spanning row: wrap to spanning_w; metric shows on line 0 only.
            wrapped = _wrap_cell_value(values, spanning_w)
            for k, wl in enumerate(wrapped):
                metric_text = metric if k == 0 else ""
                row_line = f"\u2502 {metric_text:<{metric_w}} \u2502 {wl:>{spanning_w}} \u2502"
                lines.append(_colorize_row(row_line, metric))
        else:
            # Per-column wrap; row height = max wrapped lines across columns.
            wrapped_cells = [_wrap_cell_value(v, value_widths[j]) for j, v in enumerate(values)]
            height = max((len(c) for c in wrapped_cells), default=1)
            for c in wrapped_cells:
                while len(c) < height:
                    c.append("")
            for k in range(height):
                metric_text = metric if k == 0 else ""
                cells = [f" {metric_text:<{metric_w}} "] + [
                    f" {wrapped_cells[j][k]:>{value_widths[j]}} " for j in range(n_cols)
                ]
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

    # Issue #214: per-column widths so one wide cell does NOT inflate every
    # column.  Spanning rows are handled afterwards by widening uniformly.
    value_widths = _compute_value_widths(column_labels, all_rows, default_min=value_w)
    max_spanning = max(
        (len(vals) for _, section_rows in sections for _, vals in section_rows if isinstance(vals, str)),
        default=0,
    )
    value_widths = _widen_for_spanning(value_widths, max_spanning)

    # Width a spanning cell occupies across all n_cols value columns (plus
    # the n_cols-1 internal separators). Used for both the section-header row
    # and any individual metric whose value was merged into a str.
    spanning_w = sum(w + 2 for w in value_widths) + (n_cols - 1) - 2
    # Width of the ENTIRE merged cell for a section-header row: metric column
    # + all value columns + every separator between them - leading/trailing
    # padding (2 spaces).
    section_w = metric_w + 2 + 1 + (sum(w + 2 for w in value_widths) + (n_cols - 1)) - 2

    def hborder(left: str, junction_metric: str, junction_inner: str, right: str) -> str:
        inner_runs = [("\u2500" * (w + 2)) for w in value_widths]
        joined = junction_inner.join(inner_runs)
        return left + "\u2500" * (metric_w + 2) + junction_metric + joined + right

    border_top = hborder("\u250c", "\u252c", "\u252c", "\u2510")
    border_mid = hborder("\u251c", "\u253c", "\u253c", "\u2524")
    border_bot = hborder("\u2514", "\u2534", "\u2534", "\u2518")

    header_cells = [f" {title:<{metric_w}} "] + [
        f" {label:>{value_widths[i]}} " for i, label in enumerate(column_labels)
    ]
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
                wrapped = _wrap_cell_value(values, spanning_w)
                for k, wl in enumerate(wrapped):
                    metric_text = metric if k == 0 else ""
                    row_line = f"\u2502 {metric_text:<{metric_w}} \u2502 {wl:>{spanning_w}} \u2502"
                    lines.append(_colorize_row(row_line, metric))
            else:
                wrapped_cells = [_wrap_cell_value(v, value_widths[j]) for j, v in enumerate(values)]
                height = max((len(c) for c in wrapped_cells), default=1)
                for c in wrapped_cells:
                    while len(c) < height:
                        c.append("")
                for k in range(height):
                    metric_text = metric if k == 0 else ""
                    cells = [f" {metric_text:<{metric_w}} "] + [
                        f" {wrapped_cells[j][k]:>{value_widths[j]}} " for j in range(n_cols)
                    ]
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
        # Only surface non-zero blocked-bucket terms so a typical report --
        # where every blocked counter sits at zero -- keeps the breakdown
        # short. The "active" term always shows.  Issue #214 caps the
        # rendered cell width by wrapping long breakdowns at " + "
        # boundaries inside the table renderer; this builder keeps the
        # full classification names so the table reads naturally on a
        # wide terminal.
        parts: list[str] = [f"active {stats.eta_active}"]
        if stats.eta_blocked_recovery:
            parts.append(f"blocked-recovery {stats.eta_blocked_recovery}")
        if stats.eta_blocked_auto:
            parts.append(f"blocked-auto {stats.eta_blocked_auto}")
        if stats.eta_blocked_runtime_degradation:
            parts.append(f"blocked-runtime-degradation {stats.eta_blocked_runtime_degradation}")
        breakdown = " + ".join(parts)
        return f"~{stats.est_hours:.1f} h ({breakdown} at {stats.recent_pace_minutes:.1f} min/task)"
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
        # Issue #164: total-cost-at-completion is a global measure (one
        # finishing point for the backlog). The narrower-window numbers
        # were rendered as different per-column projections that couldn't
        # all be right at once. Spanning the row makes the report express
        # the underlying truth: one global completion, one cost projection.
        "Estimated total cost at completion",
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
    eta_total = (
        tasks_active + stats.eta_blocked_recovery + stats.eta_blocked_auto + stats.eta_blocked_runtime_degradation
    )
    attn_blocked = max(
        0,
        tasks_blocked - stats.eta_blocked_recovery - stats.eta_blocked_auto - stats.eta_blocked_runtime_degradation,
    )
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
    tasks_blocked: int  # non-Done tasks with status == BLOCKED or HOLD
    tasks_active: int  # tasks_remaining - tasks_blocked (in-queue / in-progress / in-review)
    tasks_in_progress: int  # non-Done tasks with status == IN_PROGRESS (subset of tasks_active)
    tasks_in_queue: int  # non-Done tasks with status == IN_QUEUE (subset of tasks_active)
    tasks_in_review: int  # non-Done tasks with status == IN_REVIEW (subset of tasks_active)
    tasks_proposed: int  # task-factory-generated drafts awaiting human review
    tasks_declined: int  # explicitly declined work (won't ever be done)
    tasks_draft: int = 0  # pre-queue gate; not yet promoted to in-queue
    # E2-F2-S1: per-state blocked counts (one per BlockedTaskState).
    tasks_blocked_auto_clearing: int = 0  # AUTO_CLEARING_VIA_PROPOSAL
    tasks_blocked_amendment_recovery: int = 0  # AWAITING_AMENDMENT_RECOVERY
    tasks_blocked_dependency: int = 0  # AWAITING_DEPENDENCY
    tasks_blocked_held: int = 0  # HELD (task's own status is hold)
    tasks_blocked_on_held: int = 0  # BLOCKED_ON_HELD
    tasks_blocked_runtime_degradation: int = 0  # RUNTIME_DEGRADATION (auto-recovers on orchestrator restart)
    tasks_blocked_interrupted_on_stop: int = 0  # INTERRUPTED_ON_STOP (auto-requeued on next sweep, TDI-002)
    tasks_blocked_operator: int = 0  # OPERATOR_ACTION_REQUIRED

    @property
    def tasks_blocked_recovery(self) -> int:
        """Combined recovery bucket: AWAITING_AMENDMENT_RECOVERY + AWAITING_DEPENDENCY.

        Used by _compute_window_stats for the ETA projection denominator.
        """
        return self.tasks_blocked_amendment_recovery + self.tasks_blocked_dependency

    @property
    def tasks_blocked_auto(self) -> int:
        """Auto-clearing bucket: AUTO_CLEARING_VIA_PROPOSAL.

        Used by _compute_window_stats for the ETA projection denominator.
        """
        return self.tasks_blocked_auto_clearing


def _count_blocked_states(tasks_blocked_and_hold: list) -> dict[BlockedTaskState, int]:
    """Count each blocked/hold task into its ``BlockedTaskState`` bucket.

    Returns a dict keyed by every ``BlockedTaskState`` member (zero-filled).
    HOLD-status tasks short-circuit to ``HELD`` without a filesystem read.
    Fail-loud: a classifier result that is not an enum member raises
    ``RuntimeError`` (CLAUDE.md forbids silent fallback) so a new state added
    without updating the renderer is caught rather than silently dropped.
    """
    from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

    counts: dict[BlockedTaskState, int] = dict.fromkeys(BlockedTaskState, 0)
    for u in tasks_blocked_and_hold:
        if u.status is WorkUnitStatus.HOLD:
            counts[BlockedTaskState.HELD] += 1
            continue
        try:
            state = classify_blocked_task(BACKLOG_ROOT, BACKLOG_INDEX, u.id, workspace_root=WORKSPACE_ROOT)
        except (FileNotFoundError, ValueError, OSError):
            state = BlockedTaskState.OPERATOR_ACTION_REQUIRED
        if state not in counts:
            raise RuntimeError(
                f"Unhandled BlockedTaskState {state!r} in report counter path; "
                "update _BacklogTotals + the consumers of _count_blocked_states."
            )
        counts[state] += 1
    return counts


def _backlog_totals_from_units(units: list) -> _BacklogTotals:
    tasks = [u for u in units if u.unit_type == WorkUnitType.TASK]
    stories = [u for u in units if u.unit_type == WorkUnitType.STORY]
    features = [u for u in units if u.unit_type == WorkUnitType.FEATURE]
    epics = [u for u in units if u.unit_type == WorkUnitType.EPIC]
    tasks_done = [t for t in tasks if t.status == WorkUnitStatus.DONE]
    # Both BLOCKED and HOLD tasks are classified by the 6-state classifier
    # and counted in tasks_blocked so the six per-state fields sum to that total.
    tasks_blocked_status = [t for t in tasks if t.status == WorkUnitStatus.BLOCKED]
    tasks_hold_status = [t for t in tasks if t.status == WorkUnitStatus.HOLD]
    tasks_blocked_and_hold = tasks_blocked_status + tasks_hold_status
    tasks_in_progress = [t for t in tasks if t.status == WorkUnitStatus.IN_PROGRESS]
    tasks_in_queue = [t for t in tasks if t.status == WorkUnitStatus.IN_QUEUE]
    tasks_in_review = [t for t in tasks if t.status == WorkUnitStatus.IN_REVIEW]
    tasks_proposed = [t for t in tasks if t.status == WorkUnitStatus.PROPOSED]
    tasks_declined = [t for t in tasks if t.status == WorkUnitStatus.DECLINED]
    tasks_draft = [t for t in tasks if t.status == WorkUnitStatus.DRAFT]
    tasks_remaining = len(tasks) - len(tasks_done) - len(tasks_proposed) - len(tasks_declined) - len(tasks_draft)
    n_blocked_total = len(tasks_blocked_and_hold)

    # E2-F2-S1: classify each blocked/hold task into one of the per-state
    # BlockedTaskState buckets so the ETA projection and the panel report can
    # operate from pre-computed per-state counts.
    from devbench.backlog.proposal import BlockedTaskState

    counts = _count_blocked_states(tasks_blocked_and_hold)

    return _BacklogTotals(
        tasks_total=len(tasks),
        tasks_done=len(tasks_done),
        units_total=len(units),
        units_done=len([u for u in units if u.status == WorkUnitStatus.DONE]),
        stories_done=len([s for s in stories if s.status == WorkUnitStatus.DONE]),
        features_done=len([f for f in features if f.status == WorkUnitStatus.DONE]),
        epics_done=len([e for e in epics if e.status == WorkUnitStatus.DONE]),
        tasks_remaining=tasks_remaining,
        tasks_blocked=n_blocked_total,
        tasks_active=tasks_remaining - n_blocked_total,
        tasks_in_progress=len(tasks_in_progress),
        tasks_in_queue=len(tasks_in_queue),
        tasks_in_review=len(tasks_in_review),
        tasks_proposed=len(tasks_proposed),
        tasks_declined=len(tasks_declined),
        tasks_draft=len(tasks_draft),
        tasks_blocked_auto_clearing=counts[BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL],
        tasks_blocked_amendment_recovery=counts[BlockedTaskState.AWAITING_AMENDMENT_RECOVERY],
        tasks_blocked_dependency=counts[BlockedTaskState.AWAITING_DEPENDENCY],
        tasks_blocked_held=counts[BlockedTaskState.HELD],
        tasks_blocked_on_held=counts[BlockedTaskState.BLOCKED_ON_HELD],
        tasks_blocked_runtime_degradation=counts[BlockedTaskState.RUNTIME_DEGRADATION],
        tasks_blocked_interrupted_on_stop=counts[BlockedTaskState.INTERRUPTED_ON_STOP],
        tasks_blocked_operator=counts[BlockedTaskState.OPERATOR_ACTION_REQUIRED],
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
        ("Tasks draft", str(b.tasks_draft)),
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


def _classify_blocked_unit_into_buckets(
    u,
    auto_rows: list,
    amendment_recovery_rows: list,
    dependency_rows: list,
    held_rows: list,
    on_held_rows: list,
    runtime_degradation_rows: list,
    interrupted_rows: list,
    operator_rows: list,
) -> None:
    """Route one blocked/hold task unit into the appropriate display bucket.

    Separated from ``_blocked_listing`` to keep the outer function's branch
    count within the PLR0912 threshold.  HOLD-status units short-circuit to
    the held bucket without a filesystem read.

    Every ``BlockedTaskState`` enum member is handled explicitly. Adding a
    new member to the enum without extending this routing function raises
    ``RuntimeError`` -- CLAUDE.md forbids silent fallbacks for unhandled
    cases. Tests in ``tests/test_reporting/test_report.py`` parametrise
    every enum member against this function.
    """
    from devbench.backlog.proposal import (
        BlockedTaskState,
        classify_blocked_task,
        classify_blocked_task_excluding_degradation,
        recovery_signal_for_task,
    )

    if u.status is WorkUnitStatus.HOLD:
        held_rows.append(u)
        return
    state = classify_blocked_task(
        BACKLOG_ROOT,
        BACKLOG_INDEX,
        u.id,
        workspace_root=WORKSPACE_ROOT,
    )
    if state is BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL:
        from devbench.backlog.manager import BacklogManager

        waiting_on = sorted(BacklogManager()._extract_pending_proposal_markers(u.file_path))
        auto_rows.append((u, waiting_on))
    elif state is BlockedTaskState.AWAITING_AMENDMENT_RECOVERY:
        signal = recovery_signal_for_task(WORKSPACE_ROOT, u.id)
        amendment_recovery_rows.append((u, signal))
    elif state is BlockedTaskState.AWAITING_DEPENDENCY:
        dependency_rows.append(u)
    elif state is BlockedTaskState.HELD:
        held_rows.append(u)
    elif state is BlockedTaskState.BLOCKED_ON_HELD:
        on_held_rows.append(u)
    elif state is BlockedTaskState.RUNTIME_DEGRADATION:
        # Issue #248a: check for a co-existing structural blocker so the
        # operator sees that a restart alone will not clear the task.
        structural_state = classify_blocked_task_excluding_degradation(
            BACKLOG_ROOT,
            BACKLOG_INDEX,
            u.id,
            workspace_root=WORKSPACE_ROOT,
        )
        runtime_degradation_rows.append((u, structural_state))
    elif state is BlockedTaskState.INTERRUPTED_ON_STOP:
        interrupted_rows.append(u)
    elif state is BlockedTaskState.OPERATOR_ACTION_REQUIRED:
        operator_rows.append(u)
    else:
        raise RuntimeError(
            f"Unhandled BlockedTaskState {state!r} in report per-row routing; "
            "update _classify_blocked_unit_into_buckets + _render_blocked_panels."
        )


def _render_simple_panel(rows: list, title: str, hint: str, row_suffix: str) -> list[str]:
    """Render one blocked-task panel with a fixed per-row suffix."""
    if not rows:
        return []
    out = ["", f"Blocked tasks ({title}) ({len(rows)}):", hint]
    for u in rows:
        out.append(f"  - {u.id}: {u.title}    {row_suffix}")
    return out


def _render_runtime_degradation_panel(rows: list) -> list[str]:
    """Render the RUNTIME_DEGRADATION panel; composite rows carry the structural-blocker suffix.

    Issue #248a: each row in ``rows`` is a ``(unit, structural_state)`` tuple where
    ``structural_state`` is the result of ``classify_blocked_task_excluding_degradation``.
    When ``structural_state`` is not ``OPERATOR_ACTION_REQUIRED``, the row carries the
    verbatim composite line:

        RUNTIME_DEGRADATION + structural blocker (<bucket>): a restart alone will not
        clear the structural blocker <id>

    A pure-degradation task (``structural_state`` is ``OPERATOR_ACTION_REQUIRED``) renders
    the standard ``[runtime-degradation -- retries on next orchestrator restart]`` suffix.
    """
    if not rows:
        return []
    from devbench.backlog.proposal import BlockedTaskState

    out = [
        "",
        f"Blocked tasks (runtime-degradation) ({len(rows)}):",
        (
            "SDK lost Agent-tool access mid-session; task remains blocked until the orchestrator restarts "
            "(auto on NO_ACTIONABLE exit; otherwise manual `make start`)."
        ),
    ]
    for u, structural_state in rows:
        if structural_state is not BlockedTaskState.OPERATOR_ACTION_REQUIRED:
            suffix = (
                f"RUNTIME_DEGRADATION + structural blocker ({structural_state.value}): "
                f"a restart alone will not clear the structural blocker {u.id}"
            )
        else:
            suffix = "[runtime-degradation -- retries on next orchestrator restart]"
        out.append(f"  - {u.id}: {u.title}    {suffix}")
    return out


def _render_auto_clearing_panel(auto_rows: list) -> list[str]:
    """Render the AUTO_CLEARING_VIA_PROPOSAL panel; per-row suffix names marker targets."""
    if not auto_rows:
        return []
    out = [
        "",
        f"Blocked tasks (auto-clearing via proposal) ({len(auto_rows)}):",
        "Resolves when marker targets reach terminal; no action.",
    ]
    for u, waiting_on in auto_rows:
        suffix = f"    [waiting on {', '.join(waiting_on)}]" if waiting_on else ""
        out.append(f"  - {u.id}: {u.title}{suffix}")
    return out


def _render_amendment_recovery_panel(rows: list) -> list[str]:
    """Render the AWAITING_AMENDMENT_RECOVERY panel; per-row suffix names recovery signal."""
    if not rows:
        return []
    out = [
        "",
        f"Blocked tasks (awaiting amendment recovery) ({len(rows)}):",
        "Recovery agent in flight; orchestrator's next sweep advances these.",
    ]
    for u, signal in rows:
        suffix = f"    [recovery: {signal}]" if signal else ""
        out.append(f"  - {u.id}: {u.title}{suffix}")
    return out


def _render_blocked_panels(
    auto_rows: list,
    amendment_recovery_rows: list,
    dependency_rows: list,
    held_rows: list,
    on_held_rows: list,
    runtime_degradation_rows: list,
    interrupted_rows: list,
    operator_rows: list,
) -> list[str]:
    """Render every per-state blocked panel into display lines.

    Each non-empty panel produces a header, a canonical resolution hint,
    and one row per task unit.  Empty panels are omitted.  Kept separate
    from ``_blocked_listing`` so the branch count of the outer function
    stays within the PLR0912 threshold.

    Display order matches the priority ordering of ``BlockedTaskState``
    in ``devbench.backlog.proposal``: auto-clearing first (purely
    cascade-driven), then amendment-recovery (orchestrator-driven), then
    dependency, then held / blocked-on-held (operator-driven HOLD state),
    then runtime-degradation (SDK auto-restart), and finally
    operator-action-required (the residual "needs human").
    """
    lines: list[str] = []
    lines.extend(_render_auto_clearing_panel(auto_rows))
    lines.extend(_render_amendment_recovery_panel(amendment_recovery_rows))
    lines.extend(
        _render_simple_panel(
            dependency_rows,
            "awaiting dependency",
            "Resolves when the dependency completes; no action.",
            "[dependency not yet terminal]",
        )
    )
    lines.extend(
        _render_simple_panel(
            held_rows,
            "held",
            "On hold by operator; unhold to release.",
            "[HOLD]",
        )
    )
    lines.extend(
        _render_simple_panel(
            on_held_rows,
            "blocked-on-held",
            "Waiting on a held unit; unhold the target or redirect this task.",
            "[blocked-on-held]",
        )
    )
    lines.extend(_render_runtime_degradation_panel(runtime_degradation_rows))
    lines.extend(
        _render_simple_panel(
            interrupted_rows,
            "interrupted on stop",
            "Force-blocked by the orchestrator shutdown safeguard; auto-requeued on the next sweep.",
            "[interrupted-on-stop -- auto-requeues on next sweep]",
        )
    )
    lines.extend(
        _render_simple_panel(
            operator_rows,
            "operator action required",
            "No automation path; operator must inspect and resolve manually.",
            "[operator action required]",
        )
    )
    return lines


def _blocked_listing(units: list) -> list[str]:
    """Render blocked tasks as one panel per BlockedTaskState.

    Each panel header reads ``Blocked tasks (<panel-name>) (<count>):``
    and is immediately followed by the canonical resolution hint for that
    state.  Empty panels are omitted so the operator's eye lands on the
    panels that have content.

    The panels in canonical display order:

    1. ``auto-clearing via proposal`` -- ADR-07 cascade resolves once every
       ``[BLOCKED_PENDING_PROPOSAL]`` marker target reaches terminal.
    2. ``awaiting amendment recovery`` -- a recovery agent has an artefact on
       disk; the orchestrator's next sweep will advance these automatically.
    3. ``awaiting dependency`` -- a regular dependency row is not yet
       terminal; the orchestrator unblocks these automatically.
    4. ``held`` -- the unit's own status is ``hold``; operator must resume.
    5. ``blocked-on-held`` -- a marker target is held; operator must unhold
       or redirect.
    6. ``runtime-degradation`` -- the SDK lost Agent-tool access mid-session
       (issue #183); the orchestrate skill exits NO_ACTIONABLE and ``cmd_start``
       returns the auto-restart exit code so ``make start``'s wrapping loop
       respawns the orchestrator with a fresh SDK subprocess. Operator does
       nothing.
    7. ``operator action required`` -- no automation path; operator must act.
    """
    # Admit BOTH BLOCKED and HOLD task units.
    eligible = [
        u
        for u in units
        if u.unit_type == WorkUnitType.TASK and u.status in (WorkUnitStatus.BLOCKED, WorkUnitStatus.HOLD)
    ]
    if not eligible:
        return []

    # Per-state row buckets in canonical display order.
    auto_rows: list[tuple] = []  # (unit, list[str] of marker targets)
    amendment_recovery_rows: list[tuple] = []  # (unit, signal-source string)
    dependency_rows: list = []
    held_rows: list = []
    on_held_rows: list = []
    runtime_degradation_rows: list[tuple] = []  # (unit, structural_state: BlockedTaskState)
    interrupted_rows: list = []
    operator_rows: list = []

    for u in eligible:
        _classify_blocked_unit_into_buckets(
            u,
            auto_rows,
            amendment_recovery_rows,
            dependency_rows,
            held_rows,
            on_held_rows,
            runtime_degradation_rows,
            interrupted_rows,
            operator_rows,
        )

    return _render_blocked_panels(
        auto_rows,
        amendment_recovery_rows,
        dependency_rows,
        held_rows,
        on_held_rows,
        runtime_degradation_rows,
        interrupted_rows,
        operator_rows,
    )


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


_WU_COMMENTS_SECTION_HEADER = "## Comments"
_WU_CLAIMED_MARKER = "[WU_CLAIMED]"
_WU_SESSION_MARKER = "session="


def _extract_session_from_wu(wu: WorkUnit) -> str | None:
    """Return the session name from the most recent ``[WU_CLAIMED]`` audit in a WU file.

    Reads the ``## Comments`` section of *wu*'s backing Markdown file and
    searches for lines containing ``[WU_CLAIMED]`` with a ``session=<name>``
    token.  Returns the name from the last such line (most recent claim wins).
    Returns ``None`` when the file does not exist, has no Comments section,
    has no ``[WU_CLAIMED]`` line, or the line carries no ``session=`` token
    (legacy single-session behaviour).

    Args:
        wu: The :class:`WorkUnit` whose backing file to inspect.

    Returns:
        The session name string, or ``None`` when absent.

    Raises:
        OSError: If the file exists but cannot be read (permissions, I/O error).
    """
    if not wu.file_path.exists():
        return None
    content = wu.file_path.read_text(encoding="utf-8")
    comments_start = content.find(_WU_COMMENTS_SECTION_HEADER)
    if comments_start == -1:
        return None
    comments_body = content[comments_start + len(_WU_COMMENTS_SECTION_HEADER) :]
    session_name: str | None = None
    for line in comments_body.splitlines():
        if _WU_CLAIMED_MARKER not in line:
            continue
        idx = line.find(_WU_SESSION_MARKER)
        if idx == -1:
            continue
        value_start = idx + len(_WU_SESSION_MARKER)
        value_end = line.find(" ", value_start)
        session_name = line[value_start:].strip() if value_end == -1 else line[value_start:value_end].strip()
    return session_name if session_name else None


def _filter_units_by_session(units: list[WorkUnit], session_name: str | None) -> list[WorkUnit]:
    """Return ``units`` filtered to those claimed by ``session_name``.

    When ``session_name`` is ``None``, the original list is returned unchanged
    (aggregate view across all sessions, AC-192-13).

    When ``session_name`` is provided, only work units whose most recent
    ``[WU_CLAIMED]`` audit names that session are included (AC-192-12).

    Args:
        units: Full list of parsed :class:`~devbench.backlog.work_unit.WorkUnit`
            objects from ``BacklogParser.parse_index``.
        session_name: Named-session filter.  Pass ``None`` to skip filtering.

    Returns:
        A (possibly shorter) list preserving original order.

    Raises:
        OSError: If any WU file exists but cannot be read (permissions, I/O error).
    """
    if session_name is None:
        return units
    return [u for u in units if _extract_session_from_wu(u) == session_name]


def _filter_units_by_scope(units: list, scope_filter: ScopeFilter | None) -> list:
    """Return ``units`` filtered to those allowed by ``scope_filter``.

    When ``scope_filter`` is ``None``, the original list is returned unchanged.

    A :class:`~devbench.scope.ScopeFilter` whose ``expanded_ids`` set is empty
    is re-expanded from its ``include`` / ``exclude`` token lists against the
    full list of unit IDs before filtering.  This handles the per-command
    ``--include`` / ``--exclude`` path (spec section 4.2.2, AC-190-11) where
    ``cmd_report`` builds the filter from raw tokens without yet knowing the
    full backlog ID set.

    Args:
        units: Full list of parsed :class:`~devbench.backlog.work_unit.WorkUnit`
            objects from ``BacklogParser.parse_index``.
        scope_filter: Optional :class:`~devbench.scope.ScopeFilter` instance.
            Pass ``None`` to skip filtering and return all units.

    Returns:
        A (possibly shorter) list containing only the units whose IDs satisfy
        the scope filter, preserving original order.

    Raises:
        InvalidScopeError: If token lists contain structurally malformed tokens
            (reversed ranges, empty segments).  Only raised when
            re-expansion is triggered (empty ``expanded_ids`` + non-empty tokens).
    """
    if scope_filter is None:
        return units
    active_filter = scope_filter
    if not active_filter.expanded_ids and (active_filter.include or active_filter.exclude):
        all_ids = [u.id for u in units]
        active_filter = ScopeFilter.parse(
            ", ".join(active_filter.include),
            ", ".join(active_filter.exclude),
            all_ids,
        )
    return [u for u in units if active_filter.allows(u.id)]


def _render_by_role_panel(log_path: Path, window_start: datetime) -> list[str]:
    """Render the per-role token + cost breakdown (issue #206).

    Uses ``_parse_transcript_metrics_by_role`` (the existing per-role
    bucket helper from #123) to bucket tokens by ``attributionAgent``,
    then prices each role's tokens at the rate of whichever model that
    role actually ran on.  Roles that span multiple models get a
    correct per-model-blended cost via ``_compute_cost_by_model``'s
    fallback to ``REPORT_DEFAULT_MODEL_RATES`` for the role-only
    aggregate (the model attribution lives on the SQL path, not the
    role aggregator).

    Returns the rendered lines as a list (caller appends to the report
    body).  An empty list when ``_parse_transcript_metrics_by_role``
    returns no buckets, so the panel is silently omitted on workspaces
    with no transcript activity.
    """
    hook_log_path = _hook_log_path(log_path)
    transcript_dir = _discover_transcript_dir(hook_log_path)
    by_role = _parse_transcript_metrics_by_role(transcript_dir, window_start)
    if not by_role:
        return []

    rendered: list[str] = ["", "Per-role cost breakdown (current run):"]
    rendered.append("role                  input_tokens  output_tokens  cache_read  cache_write  msgs   est_cost")
    total_in = 0
    total_out = 0
    total_cr = 0
    total_cw = 0
    total_msgs = 0
    total_cost = 0.0
    rows: list[tuple[str, int, int, int, int, int, float]] = []
    for role, totals in sorted(by_role.items()):
        # Per-role buckets do not carry per-call model attribution
        # individually -- the role aggregator collapses across models.
        # Pricing against the "<unknown>" bucket (-> REPORT_DEFAULT_MODEL_RATES)
        # produces the same total an operator would compute by hand from the
        # canonical Opus 4.7 list rates.  Issue #223's per-model panel
        # remains the more accurate axis for cost; #206 is per-role view.
        cost = _compute_cost_by_model({"<unknown>": totals})
        cache_write = totals.cache_write_5m_tokens + totals.cache_write_1h_tokens
        rows.append(
            (
                role,
                totals.input_tokens,
                totals.output_tokens,
                totals.cache_read_tokens,
                cache_write,
                totals.entries_with_usage,
                cost.total_cost,
            )
        )
        total_in += totals.input_tokens
        total_out += totals.output_tokens
        total_cr += totals.cache_read_tokens
        total_cw += cache_write
        total_msgs += totals.entries_with_usage
        total_cost += cost.total_cost
    # Sort by est_cost descending so the most expensive role surfaces
    # first; operators triaging cost want to see the biggest contributor
    # at the top without scrolling.
    rows.sort(key=lambda r: r[6], reverse=True)
    for role, in_t, out_t, cr, cw, msgs, est in rows:
        rendered.append(f"{role:<20}  {in_t:>12,}  {out_t:>13,}  {cr:>10,}  {cw:>11,}  {msgs:>4}   ${est:>7,.4f}")
    rendered.append(
        f"{'TOTAL':<20}  {total_in:>12,}  {total_out:>13,}  {total_cr:>10,}  "
        f"{total_cw:>11,}  {total_msgs:>4}   ${total_cost:>7,.4f}"
    )
    return rendered


def _no_actionable_line(parser: BacklogParser, units: list[WorkUnit]) -> str | None:
    """Return the no-actionable line when nothing is actionable and not all done.

    Returns the verbatim ``'No actionable units. <N> blocked.'`` string when
    the backlog has no actionable candidates and is not fully done; returns
    ``None`` otherwise.  Extracted from :func:`generate_report` so the branch
    count of that function stays within the linter threshold.
    """
    actionable, all_done_flag, blocked_count = check_actionability(parser, units)
    if not actionable and not all_done_flag:
        return f"\nNo actionable units. {blocked_count} blocked."
    return None


def generate_report(
    log_path: Path,
    since: datetime | None = None,
    report_started_at: datetime | None = None,
    scope_filter: ScopeFilter | None = None,
    session_name: str | None = None,
    *,
    by_role: bool = False,
) -> str:
    """Generate a formatted progress report.

    Args:
        log_path: Path to the orchestrator log file.  When ``session_name`` is
            provided, the caller is expected to pass the per-session log path
            (``<workspace>/.devbench/sessions/<name>/orchestrator.log``); the
            event-index queries then operate on that file's events only.
        since: If provided, render a single window starting at this timestamp.
            If omitted, render All-time + Current session, plus This run when
            ``report_started_at`` is also provided (watch mode).
        report_started_at: When set, adds a "This run" column tracking activity
            since this timestamp. Used by ``cmd_report`` in watch mode to show
            what's happened since the watch loop began.
        scope_filter: Optional :class:`~devbench.scope.ScopeFilter` instance
            (spec section 4.2.2, AC-190-10, AC-190-11).  When provided, only
            work units whose IDs are in the filter's scope are counted and
            listed in the report.  A ``ScopeFilter`` with empty
            ``expanded_ids`` is re-expanded from its ``include`` / ``exclude``
            token lists against the parsed backlog before filtering.  Pass
            ``None`` (default) to include all work units.
        session_name: Optional named-session filter (spec section 4.4.6,
            AC-192-12, AC-192-13).  When provided, only work units whose most
            recent ``[WU_CLAIMED]`` audit names this session are counted and
            listed in the report.  Pass ``None`` (default) to aggregate across
            all sessions.  Composes with ``scope_filter``: both filters are
            applied in sequence (session filter first, then scope filter).

    Returns:
        Formatted report string ready for terminal output.

    Raises:
        SystemExit(1): If the backlog cannot be read or parsed.
        InvalidScopeError: If ``scope_filter`` token lists contain
            structurally invalid tokens (reversed ranges, etc.).
        OSError: If a WU backing file cannot be read during session filtering.
    """
    # Operator-alive banner (issue #161). Prepended to every render so
    # ``devbench report --watch N`` shows liveness state on every tick.
    # Threshold reuses ``stop_hook.window_seconds`` -- the same quiet
    # window the circuit-breaker tolerates -- so the banner stays aligned
    # with the operator's already-tuned cadence.
    #
    # The banner is computed BEFORE the snapshot short-circuit (issue
    # #162 Phase 6) because the banner uses ``datetime.now()`` to
    # express "last activity Ns ago" -- if we cached it inside the
    # snapshot the elapsed-since string would freeze and a stalled
    # orchestrator would still appear ALIVE on every watch tick.
    banner_display_tz = _resolve_display_timezone(REPORT_DISPLAY_TIMEZONE or DISPLAY_TIMEZONE)
    banner_lines = _resolve_banner_lines(log_path, banner_display_tz)

    # Issue #162 Phase 6 (rendered-body snapshot) is deferred.
    # A snapshot keyed on log mtime + size is fast but unsafe:
    # cost-rate config (``REPORT_MODEL_RATES``, the cache + residency +
    # fast-mode multipliers, the display timezone, ``RECENT_PACE_TASKS``)
    # can change between invocations without the log advancing, and a
    # snapshot keyed only on the log would silently return numbers
    # computed against the old config. Per CLAUDE.md fail-fast: better
    # to recompute and stay correct than cache and risk wrong cost
    # output. Phases 1+4 alone already serve the report from indexed
    # range scans, which is the dominant cost on the live workspace
    # (196 MB hook log + 200+ MB transcripts) -- the marginal saving
    # from Phase 6 over Phases 1+4 is small. The schema row
    # ``report_snapshot`` is left in place for a future invocation-
    # safe snapshot design (one that hashes the full config + BACKLOG
    # mtime tree into the cache key).
    event_index = EventIndex.open(WORKSPACE_ROOT)

    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    try:
        units = parser.parse_index()
    except FileNotFoundError as exc:
        # FileNotFoundError can name either BACKLOG.md itself or a WU md
        # referenced by it. Surface the actual path so the diagnostic
        # stops blaming the index when the real culprit is a transient
        # writer-window race on a single WU md (SDK-driven Write/Edit
        # tools outside BacklogManager can leave a WU md momentarily
        # unreadable; the parser already does one retry, this prefix
        # tells the operator what to re-run if even the retry lost).
        missing = getattr(exc, "filename", None) or str(exc)
        sys.stderr.write(
            f"devbench report: cannot read '{missing}' "
            f"(referenced by '{BACKLOG_INDEX}'): {exc}\n"
            "  If the missing path is a work-unit md and your orchestrator is\n"
            "  active, this may be a transient writer-window race; re-run.\n"
            "  Otherwise run `devbench validate-backlog` for a full index audit.\n"
        )
        sys.exit(1)
    except ValueError as exc:
        # Issue #174: a malformed or non-canonical BACKLOG.md surfaces here
        # as a parser-level exception. Fail fast with an actionable
        # diagnostic naming the file + the parse failure so the operator
        # can fix the index directly instead of seeing a raw stack trace.
        sys.stderr.write(
            f"devbench report: cannot parse '{BACKLOG_INDEX}': {exc}\n"
            "  Run `devbench validate-backlog` for a full list of issues with the index.\n"
        )
        sys.exit(1)

    # AC-192-12 / AC-192-13: apply session filter first when provided, then scope.
    # Session filter restricts to WUs claimed by the named session; without it,
    # all sessions are aggregated (AC-192-13).
    units = _filter_units_by_session(units, session_name)

    # Issue #190 (AC-190-10, AC-190-11): apply scope filter when provided.
    units = _filter_units_by_scope(units, scope_filter)

    backlog = _backlog_totals_from_units(units)

    # Issue #162 Phase 1+4 cache: refresh the persistent SQLite index
    # against the orchestrator log + hook log + transcripts (each call
    # is a mtime+size check + delta-only re-parse on append, full
    # re-parse on rotation/truncation), then read aggregated views via
    # indexed queries instead of full-file regex scans. The legacy
    # text-parser path below is preserved as a fallback for callers
    # that pass ``event_index=None`` to ``_compute_window_stats``
    # directly (mostly the existing test suite, which tests the parser
    # building blocks individually).
    # Issue #168: route the orch-log refresh + queries through the
    # workspace-aware variants so events from sharded shards (post
    # Phase-3 migration) merge with the live flat log. When the
    # workspace has no sharded layout, the workspace-aware path
    # behaves identically to the legacy single-file path.
    event_index.refresh_orch_log_sources(WORKSPACE_ROOT, log_path)
    hook_log_path = _hook_log_path(log_path)
    event_index.refresh_hook_log(hook_log_path)
    transcript_dir = _resolve_transcript_dir(event_index, hook_log_path)
    event_index.refresh_transcripts(transcript_dir)

    done_times: dict[str, datetime] = event_index.task_transition_times_for_workspace(WORKSPACE_ROOT, log_path, "done")
    progress_times: dict[str, datetime] = event_index.task_transition_times_for_workspace(
        WORKSPACE_ROOT, log_path, "in-progress"
    )
    all_timestamps: list[datetime] = event_index.all_log_timestamps_for_workspace(WORKSPACE_ROOT, log_path)
    log_start_for_window, window_end, log_started = _resolve_window_endpoints(all_timestamps)

    # Precedence: report-specific (env DEVBENCH_REPORT_TIMEZONE > yaml
    # report.display_timezone) > top-level (env DEVBENCH_DISPLAY_TIMEZONE >
    # yaml display_timezone) > OS local. REPORT_DISPLAY_TIMEZONE already
    # encodes the first pair; DISPLAY_TIMEZONE the second.
    display_tz = _resolve_display_timezone(REPORT_DISPLAY_TIMEZONE or DISPLAY_TIMEZONE)

    # Issue #164: compute the global recent-pace per-task cost ONCE (it's
    # log-wide, not window-specific) and reuse across every window's
    # ``_compute_window_stats`` call. This is what makes the
    # "Estimated total cost at completion" number consistent across the
    # All-time / Session / This-run columns -- one global rate produces
    # one global completion cost regardless of window.
    recent_per_task_cost = _recent_per_task_cost(
        log_path,
        done_times,
        progress_times,
        RECENT_PACE_TASKS,
        event_index=event_index,
    )

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
            backlog.tasks_blocked_runtime_degradation,
            event_index=event_index,
            recent_per_task_cost=recent_per_task_cost,
        )
        if log_started is not None
        else None
    )

    lines: list[str] = [*banner_lines, ""]

    # Spanning-row follow-up: thread the All-time cost (already paid in
    # lifetime_stats above) as the additive base for every narrower
    # window's est_total_cost. When lifetime_stats is None (no log
    # started yet), fall through with None and the legacy per-window
    # additive base applies -- there is nothing to collapse against.
    lifetime_total_cost = lifetime_stats.cost.total_cost if lifetime_stats is not None else None

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
            backlog.tasks_blocked_runtime_degradation,
            event_index=event_index,
            recent_per_task_cost=recent_per_task_cost,
            lifetime_total_cost=lifetime_total_cost,
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
        detected_session = _find_current_session_start_from_index(event_index, log_path, workspace_root=WORKSPACE_ROOT)
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
                        event_index=event_index,
                        recent_per_task_cost=recent_per_task_cost,
                        lifetime_total_cost=lifetime_total_cost,
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
        # ``DEVBENCH_LOG_FILE`` was unset in the shell that ran
        # ``devbench report`` and the default fell back to the devbench
        # source-tree log. Surface the discrepancy as a one-line
        # warning so the user does not silently misread the table.
        all_time_stats = all_window_stats[0]
        if backlog.tasks_done > 0 and all_time_stats.tasks_in_window == 0:
            lines.append("")
            lines.append(
                f"WARNING: BACKLOG.md shows {backlog.tasks_done} done but log {log_path} shows 0 "
                "-- check DEVBENCH_LOG_FILE points at the orchestrator's log."
            )
        # Use the All-time stats for the trailing prose projection -- they're the
        # most stable sample. Narrower windows can have zero completed tasks
        # (e.g. just after a restart) which would project meaningless numbers.
        summary_stats = all_window_stats[0]

    if by_role:
        # Issue #206: opt-in per-role token/cost breakdown.  The data path
        # was landed in PR #202 (issue #123) via
        # ``_parse_transcript_metrics_by_role``; this section wires it
        # into the rendered output.  Pricing reuses the per-model
        # dispatcher (issue #223) -- per-role tokens are priced against
        # whatever model that role actually ran on, so a role that
        # spans multiple models (executor on opus + sonnet) gets a
        # correct blended cost.
        lines.extend(
            _render_by_role_panel(
                log_path=log_path,
                window_start=summary_stats.window_start,
            )
        )

    lines.append("")
    lines.append(_summary_line(summary_stats, backlog.tasks_active, backlog.tasks_blocked))

    # AC-251-1: emit the no-actionable line when candidates are empty and not
    # all done. This mirrors the verbatim format from cmd_status so both
    # surfaces are always consistent. The line is appended in both the default
    # and --once (since=<ts>) render paths.
    _no_act = _no_actionable_line(parser, units)
    lines.extend([_no_act] if _no_act is not None else [])

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
