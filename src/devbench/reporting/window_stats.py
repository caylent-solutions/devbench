"""Per-task window-stats aggregates (#162 Phase 2).

Records every task-state transition into a per-task aggregate JSON at
``<workspace>/.devbench/window-stats/<task-id>.json`` so the reporter
can read aggregates directly -- O(task_count) -- instead of re-scanning
the orchestrator log on every invocation.

The aggregate is the structural summary of one task's lifecycle:
the timestamps it entered each state, plus a list of every transition
in order. The reporter derives durations and per-state metrics from
those timestamps; nothing else flows through this layer.

Atomic write. ``update_aggregate`` reads the existing aggregate (if
any), merges the new transition, writes to ``<task-id>.json.tmp`` in
the same directory, then ``os.replace``s atomically (POSIX same-
filesystem rename). A crash mid-write either leaves the prior file
intact or makes the new file fully present; never a torn JSON
document.

Self-healing. The aggregate is a derived view of the JSONL log; the
log is authoritative. ``rebuild_from_log`` walks the orchestrator log
and rebuilds every aggregate from scratch (used by
``devbench rebuild-window-stats`` after an upgrade or after
``.devbench/window-stats/`` has been deleted). Aggregate deletion is
always safe.

Schema versioning. The top-level ``schema_version`` lets future
devbench releases evolve the aggregate format without manual cleanup.
A version mismatch on read returns an empty aggregate, which the
caller treats as "rebuild on next transition."
"""

from __future__ import annotations

import json as _json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

_log = logging.getLogger("devbench.reporting.window_stats")

WINDOW_STATS_DIR_NAME = ".devbench/window-stats"

# Bump whenever the on-disk aggregate schema changes incompatibly.
# A version mismatch on read yields an empty aggregate; the next
# update_aggregate call writes the new schema verbatim.
_SCHEMA_VERSION = 1

# Match the orchestrator's structured state-transition log line. The
# format is fixed by ``BacklogManager._set_status``:
#     YYYY-MM-DDTHH:MM:SSZ [...] Set <id> to '<status>' in both work-unit ...
_TRANSITION_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z .* Set (?P<task_id>E\S+) to '(?P<status>[^']+)'",
    re.MULTILINE,
)


@dataclass
class TransitionEvent:
    """One state-transition entry. Mutable to allow merge-in-place."""

    timestamp_iso: str
    new_status: str


@dataclass
class TaskAggregate:
    """Per-task lifecycle summary. Atomic-written to one JSON file."""

    task_id: str
    transitions: list[TransitionEvent] = field(default_factory=list)
    schema_version: int = _SCHEMA_VERSION


def aggregate_dir(workspace_root: Path) -> Path:
    """Return the canonical aggregates directory for a workspace."""
    return workspace_root / WINDOW_STATS_DIR_NAME


def aggregate_path(workspace_root: Path, task_id: str) -> Path:
    """Return the canonical aggregate file path for one task."""
    return aggregate_dir(workspace_root) / f"{task_id}.json"


def _decode_aggregate(raw: dict) -> TaskAggregate | None:
    """Return a ``TaskAggregate`` parsed from a payload dict, or None
    when the payload is corrupt / version-mismatched.

    Self-healing: callers treat None as "no prior aggregate" and start
    a fresh one. Subsequent writes overwrite the corrupt file.
    """
    if not isinstance(raw, dict):
        return None
    if raw.get("schema_version") != _SCHEMA_VERSION:
        return None
    task_id = raw.get("task_id")
    transitions_raw = raw.get("transitions")
    if not isinstance(task_id, str) or not isinstance(transitions_raw, list):
        return None
    transitions: list[TransitionEvent] = []
    for entry in transitions_raw:
        if not isinstance(entry, dict):
            return None
        ts = entry.get("timestamp_iso")
        status = entry.get("new_status")
        if not isinstance(ts, str) or not isinstance(status, str):
            return None
        transitions.append(TransitionEvent(timestamp_iso=ts, new_status=status))
    return TaskAggregate(task_id=task_id, transitions=transitions)


def read_aggregate(workspace_root: Path, task_id: str) -> TaskAggregate | None:
    """Return the persisted aggregate for ``task_id``, or None when
    none exists / is corrupt."""
    path = aggregate_path(workspace_root, task_id)
    if not path.is_file():
        return None
    try:
        raw = _json.loads(path.read_text(encoding="utf-8"))
    except (_json.JSONDecodeError, OSError):
        return None
    return _decode_aggregate(raw)


def _write_aggregate(workspace_root: Path, aggregate: TaskAggregate) -> Path:
    """Atomic write-temp-then-rename; returns the canonical path."""
    out_path = aggregate_path(workspace_root, aggregate.task_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "task_id": aggregate.task_id,
        "transitions": [asdict(t) for t in aggregate.transitions],
    }
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp_path.write_text(_json.dumps(payload), encoding="utf-8")
    tmp_path.replace(out_path)
    return out_path


def update_aggregate(
    workspace_root: Path,
    task_id: str,
    new_status: str,
    timestamp: datetime,
) -> Path:
    """Append one transition entry to ``task_id``'s aggregate file.

    Idempotent at the level of the appended transition: a duplicate
    transition (same timestamp + status) is recorded twice -- the
    aggregate is a fact log, not a state-set. Callers who need
    de-dup should run ``rebuild_from_log``.

    Atomic: writes to ``<task-id>.json.tmp`` then ``os.replace``s.
    """
    existing = read_aggregate(workspace_root, task_id)
    aggregate = TaskAggregate(task_id=task_id, transitions=[]) if existing is None else existing
    aggregate.transitions.append(
        TransitionEvent(
            timestamp_iso=timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            new_status=new_status,
        )
    )
    return _write_aggregate(workspace_root, aggregate)


def rebuild_from_log(workspace_root: Path, log_path: Path) -> int:
    """Walk the orchestrator log and rebuild every per-task aggregate.

    Idempotent and safe to invoke at any time. Used by
    ``devbench rebuild-window-stats`` (operator command after deleting
    ``.devbench/window-stats/`` or when aggregates drift out of sync).

    Returns the number of distinct tasks for which an aggregate was
    written.
    """
    if not log_path.is_file():
        return 0
    text = log_path.read_text(encoding="utf-8", errors="replace")
    aggregates: dict[str, TaskAggregate] = {}
    for match in _TRANSITION_RE.finditer(text):
        task_id = match.group("task_id")
        # Only persist task-level transitions. Story / Feature / Epic IDs
        # also trip the regex (they share the ``E<...>`` prefix) but their
        # rollup state is auto-derived; we don't track them in window-stats.
        if "-T" not in task_id:
            continue
        status = match.group("status")
        ts_iso = match.group("ts") + "Z"
        aggregate = aggregates.get(task_id)
        if aggregate is None:
            aggregate = TaskAggregate(task_id=task_id, transitions=[])
            aggregates[task_id] = aggregate
        aggregate.transitions.append(TransitionEvent(timestamp_iso=ts_iso, new_status=status))
    for aggregate in aggregates.values():
        _write_aggregate(workspace_root, aggregate)
    return len(aggregates)
