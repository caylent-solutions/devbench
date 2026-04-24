"""Watchdog for detecting stuck `/devbench:orchestrate` loops.

Polls two on-disk signals:

1. ``BACKLOG.md`` for any work unit whose Status column is ``in-progress``.
2. ``orchestrator.log`` mtime / most recent timestamped line.

If there is an in-progress task AND the orchestrator log has been silent
longer than ``idle_threshold_seconds`` (default 5 minutes), writes a
``needs-restart.flag`` marker under ``<workspace>/.devbench/`` so the
operator's shell prompt or a simple ``watch`` loop can surface the hang.

This module never tries to restart orchestration; writing the flag is its
entire output contract. Rationale: restarts must remain under operator
control (they affect billing and may overlap with manual edits).
"""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import UTC, datetime
from pathlib import Path

_IN_PROGRESS_ROW_RE = re.compile(
    r"^\|\s*(?P<id>[^|\s][^|]*?)\s*\|"
    r"[^|]*\|"
    r"[^|]*\|"
    r"\s*in-progress\s*\|"
    r".*?`(?P<path>[^`]+)`\s*\|\s*$",
    re.MULTILINE,
)

_LOG_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z")


@dataclasses.dataclass(frozen=True)
class StuckDetection:
    """Stuck-loop evidence suitable for writing to the flag file."""

    task_id: str
    task_file_path: str
    orchestrator_log_last_ts: datetime | None
    idle_seconds: int
    stale_minutes_threshold: int
    idle_threshold_seconds: int


@dataclasses.dataclass(frozen=True)
class WatchdogResult:
    """Outcome of a single watchdog poll. ``stuck`` is None when healthy."""

    stuck: StuckDetection | None
    reason: str


def find_in_progress_task(backlog_index: Path) -> tuple[str, str] | None:
    """Return ``(task_id, file_path)`` for the first in-progress row, or None."""
    if not backlog_index.is_file():
        return None
    text = backlog_index.read_text(encoding="utf-8")
    m = _IN_PROGRESS_ROW_RE.search(text)
    if not m:
        return None
    return m.group("id"), m.group("path")


def last_orchestrator_log_ts(log_file: Path, *, tail_bytes: int = 16384) -> datetime | None:
    """Return the UTC timestamp of the most recent dated log line, or None."""
    if not log_file.is_file():
        return None
    size = log_file.stat().st_size
    offset = max(0, size - tail_bytes)
    with log_file.open("rb") as f:
        f.seek(offset)
        tail = f.read().decode("utf-8", errors="replace")
    last_ts: datetime | None = None
    for line in tail.splitlines():
        m = _LOG_TS_RE.match(line)
        if m:
            last_ts = datetime.fromisoformat(m.group(1)).replace(tzinfo=UTC)
    return last_ts


def detect_stuck(
    *,
    backlog_index: Path,
    log_file: Path,
    now: datetime,
    idle_threshold_seconds: int,
    stale_task_minutes: int,
) -> WatchdogResult:
    """Decide whether the orchestrator appears hung.

    Returns a ``WatchdogResult`` with ``stuck=StuckDetection(...)`` iff
    BACKLOG.md has an in-progress task AND the orchestrator.log's most
    recent timestamp is older than ``idle_threshold_seconds``. When the
    log file is absent or contains no parsable timestamps, idle_seconds
    is reported as ``idle_threshold_seconds`` (the minimum that triggers).
    """
    task = find_in_progress_task(backlog_index)
    if task is None:
        return WatchdogResult(None, "no in-progress task")
    task_id, file_path = task
    last_ts = last_orchestrator_log_ts(log_file)
    idle_seconds = idle_threshold_seconds if last_ts is None else int((now - last_ts).total_seconds())
    if idle_seconds < idle_threshold_seconds:
        return WatchdogResult(
            None,
            f"orchestrator active ({idle_seconds}s since last log line)",
        )
    return WatchdogResult(
        StuckDetection(
            task_id=task_id,
            task_file_path=file_path,
            orchestrator_log_last_ts=last_ts,
            idle_seconds=idle_seconds,
            stale_minutes_threshold=stale_task_minutes,
            idle_threshold_seconds=idle_threshold_seconds,
        ),
        f"stuck: {task_id} in-progress, orchestrator idle {idle_seconds}s",
    )


def write_flag_file(flag_path: Path, stuck: StuckDetection, now: datetime) -> None:
    """Atomically write the needs-restart marker file."""
    payload = {
        "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "task_id": stuck.task_id,
        "task_file_path": stuck.task_file_path,
        "orchestrator_idle_seconds": stuck.idle_seconds,
        "last_orchestrator_log_ts": (
            stuck.orchestrator_log_last_ts.strftime("%Y-%m-%dT%H:%M:%SZ")
            if stuck.orchestrator_log_last_ts is not None
            else None
        ),
        "idle_threshold_seconds": stuck.idle_threshold_seconds,
        "stale_task_minutes_threshold": stuck.stale_minutes_threshold,
    }
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = flag_path.with_suffix(flag_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(flag_path)
