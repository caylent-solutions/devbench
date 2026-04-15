"""Backlog progress report generator.

Parses BACKLOG.md and the orchestrator log to produce a formatted progress
table showing velocity, completion stats, and time estimates.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from devbench.backlog.parser import BacklogParser
from devbench.backlog.work_unit import WorkUnitStatus, WorkUnitType
from devbench.config import (
    BACKLOG_INDEX,
    BACKLOG_ROOT,
    TOKEN_COST_INPUT_RATIO,
    TOKEN_COST_PER_M_INPUT,
    TOKEN_COST_PER_M_OUTPUT,
)

_DONE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z .* Set (E\S+) to 'done'", re.MULTILINE)
_PROGRESS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z .* Set (E\S+) to 'in-progress'", re.MULTILINE)
_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z", re.MULTILINE)
_TOTAL_TOKENS_RE = re.compile(r'"totalTokens":(\d+)')


def _parse_ts(raw: str) -> datetime:
    return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)


def generate_report(log_path: Path, since: datetime | None = None) -> str:
    """Generate a formatted progress report.

    Args:
        log_path: Path to the orchestrator log file.
        since: If provided, only count session events after this timestamp.

    Returns:
        Formatted report string ready for terminal output.
    """
    parser = BacklogParser(backlog_root=BACKLOG_ROOT, backlog_index=BACKLOG_INDEX)
    units = parser.parse_index()

    tasks = [u for u in units if u.unit_type == WorkUnitType.TASK]
    stories = [u for u in units if u.unit_type == WorkUnitType.STORY]
    features = [u for u in units if u.unit_type == WorkUnitType.FEATURE]

    tasks_done = [t for t in tasks if t.status == WorkUnitStatus.DONE]
    stories_done = [s for s in stories if s.status == WorkUnitStatus.DONE]
    features_done = [f for f in features if f.status == WorkUnitStatus.DONE]
    all_done = [u for u in units if u.status == WorkUnitStatus.DONE]

    # Parse log for timing data
    log_text = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""

    done_times: dict[str, datetime] = {}
    for m in _DONE_RE.finditer(log_text):
        ts = _parse_ts(m.group(1))
        done_times[m.group(2)] = ts

    progress_times: dict[str, datetime] = {}
    for m in _PROGRESS_RE.finditer(log_text):
        ts = _parse_ts(m.group(1))
        progress_times[m.group(2)] = ts

    all_timestamps: list[datetime] = []
    for m in _TIMESTAMP_RE.finditer(log_text):
        all_timestamps.append(_parse_ts(m.group(1)))

    # Apply --since filter
    effective_since = since
    if effective_since is None and all_timestamps:
        effective_since = min(all_timestamps)

    session_start = effective_since or datetime.now(UTC)
    session_end = max(all_timestamps) if all_timestamps else session_start
    session_hours = (session_end - session_start).total_seconds() / 3600

    # Tasks done in session (from log, filtered by since)
    task_ids_done_session = {uid for uid, ts in done_times.items() if "-T" in uid and ts >= session_start}
    tasks_in_session = len(task_ids_done_session)

    # Per-task durations (in-progress -> done), filtered by since.
    # If the in-progress event predates the session, use session_start as the
    # effective start so that "time spent in this session" is still captured.
    task_durations: list[float] = []
    for tid in task_ids_done_session:
        if tid in progress_times:
            effective_start = max(progress_times[tid], session_start)
            dur = (done_times[tid] - effective_start).total_seconds() / 60
            if dur > 0:
                task_durations.append(dur)

    avg_minutes = sum(task_durations) / len(task_durations) if task_durations else 0.0
    tasks_remaining = len(tasks) - len(tasks_done)
    est_hours = (tasks_remaining * avg_minutes) / 60 if avg_minutes else 0.0

    # Parse token usage from hook-logs.jsonl (sibling to BACKLOG.md).
    hook_log_path = log_path.parent.parent / "hook-logs.jsonl"
    if not hook_log_path.is_file():
        # Try workspace root (where BACKLOG.md lives).
        hook_log_path = BACKLOG_INDEX.parent / "hook-logs.jsonl"

    total_tokens = 0
    if hook_log_path.is_file():
        hook_text = hook_log_path.read_text(encoding="utf-8")
        for m in _TOTAL_TOKENS_RE.finditer(hook_text):
            total_tokens += int(m.group(1))

    blended_per_m = TOKEN_COST_PER_M_INPUT * TOKEN_COST_INPUT_RATIO + TOKEN_COST_PER_M_OUTPUT * (
        1.0 - TOKEN_COST_INPUT_RATIO
    )
    total_tokens_m = total_tokens / 1_000_000
    est_cost = total_tokens_m * blended_per_m
    tokens_per_task = total_tokens / tasks_in_session if tasks_in_session else 0.0
    est_total_cost = (
        est_cost + (tokens_per_task * tasks_remaining / 1_000_000 * blended_per_m) if tokens_per_task else 0.0
    )

    # Render table
    lines: list[str] = []

    def row(metric: str, value: str) -> None:
        lines.append(f"\u2502 {metric:<60} \u2502 {value:>16} \u2502")

    def sep() -> None:
        lines.append("\u251c" + "\u2500" * 62 + "\u253c" + "\u2500" * 18 + "\u2524")

    lines.append("\u250c" + "\u2500" * 62 + "\u252c" + "\u2500" * 18 + "\u2510")
    row("Metric", "Value")
    sep()

    task_pct = (100 * len(tasks_done) // len(tasks)) if tasks else 0
    row("Tasks completed", f"{len(tasks_done)} of {len(tasks)} ({task_pct}%)")
    sep()

    total_pct = (100 * len(all_done) // len(units)) if units else 0
    row(
        "Total work units done (tasks + auto-rolled stories/features)",
        f"{len(all_done)} of {len(units)} ({total_pct}%)",
    )
    sep()

    row("Time span (this session)", f"{session_hours:.1f} hours")
    sep()
    row("Tasks in this session", str(tasks_in_session))
    sep()
    row("Average time per task", f"{avg_minutes:.1f} minutes")
    sep()
    row("Stories auto-rolled to done", str(len(stories_done)))
    sep()
    row("Features auto-rolled to done", str(len(features_done)))
    sep()
    row("Tasks remaining", str(tasks_remaining))
    sep()
    row("Estimated time to complete", f"~{est_hours:.1f} hours")
    sep()
    row("Tokens consumed", f"{total_tokens:,}")
    sep()
    row("Estimated cost so far", f"~${est_cost:.2f}")
    sep()
    row("Avg tokens per task", f"{tokens_per_task:,.0f}" if tokens_per_task else "n/a")
    sep()
    row("Estimated total cost at completion", f"~${est_total_cost:.2f}" if est_total_cost else "n/a")

    lines.append("\u2514" + "\u2500" * 62 + "\u2534" + "\u2500" * 18 + "\u2518")
    lines.append("")
    lines.append(
        f"At the current pace of ~{avg_minutes:.1f} minutes per task, "
        f"the remaining {tasks_remaining} tasks should take roughly "
        f"{est_hours:.1f} more hours of continuous execution."
    )
    if est_total_cost:
        lines.append(
            f"Token cost estimate uses blended rate "
            f"(${blended_per_m:.0f}/M tokens, "
            f"{TOKEN_COST_INPUT_RATIO:.0%} input @ ${TOKEN_COST_PER_M_INPUT:.0f}/M, "
            f"{1.0 - TOKEN_COST_INPUT_RATIO:.0%} output @ ${TOKEN_COST_PER_M_OUTPUT:.0f}/M). "
            f"Override in devbench.yaml under git_ops."
        )

    return "\n".join(lines)
