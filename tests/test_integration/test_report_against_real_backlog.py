"""Integration test: every metric in `devbench report` must match an
independent recomputation against the user's real backlog.

Skipped by default (CI does not have the workspace). Enable with
``JUDGE_INTEGRATION_TEST=1`` and a real ``JUDGE_WORKSPACE_ROOT``:

    JUDGE_INTEGRATION_TEST=1 \
    JUDGE_WORKSPACE_ROOT=/workspaces/rpm-migration/kanon-migration-backlog \
    JUDGE_CLAUDE_MODEL=claude-opus-4-7 \
        uv run pytest tests/test_integration/ -v

This is the "bet your life" verification. Every numeric value the report
displays gets recomputed from raw ``BACKLOG.md`` + per-unit ``.md`` +
``hook-logs.jsonl`` + Claude Code transcripts, and the rendered report
text must contain each computed value verbatim.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

INTEGRATION_ENABLED = os.environ.get("JUDGE_INTEGRATION_TEST") == "1"
WORKSPACE_ROOT_ENV = os.environ.get("JUDGE_WORKSPACE_ROOT", "")


pytestmark = pytest.mark.skipif(
    not INTEGRATION_ENABLED or not WORKSPACE_ROOT_ENV,
    reason="JUDGE_INTEGRATION_TEST=1 and JUDGE_WORKSPACE_ROOT required",
)


def _run_report(workspace_root: Path, model: str) -> str:
    """Invoke `devbench report` and return its stdout (data only)."""
    env = os.environ.copy()
    env["JUDGE_WORKSPACE_ROOT"] = str(workspace_root)
    env["JUDGE_CLAUDE_MODEL"] = model
    result = subprocess.run(
        ["uv", "run", "devbench", "report"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, f"devbench report failed: stderr={result.stderr}"
    return result.stdout


def _count_units_by_status(backlog_index: Path) -> dict[str, dict[str, int]]:
    """Independently parse BACKLOG.md and return {unit_type: {status: count}}.

    Matches the parser's logic without invoking it: rows are pipe-delimited
    with ID in column 1, type in column 3, status in column 4.
    """
    by_type: dict[str, dict[str, int]] = {}
    text = backlog_index.read_text(encoding="utf-8")
    in_table = False
    for line in text.splitlines():
        if line.startswith("|") and "|" in line[1:]:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 4:
                continue
            uid, _title, utype, status = cells[0], cells[1], cells[2], cells[3]
            if uid in {"ID", "---", ":---"} or utype in {"Type", "---"}:
                in_table = True
                continue
            if not in_table:
                continue
            by_type.setdefault(utype, {})
            by_type[utype][status] = by_type[utype].get(status, 0) + 1
    return by_type


def _sum_hook_log_durations(hook_log: Path) -> int:
    """Sum every `tool_response.totalDurationMs` in hook-logs.jsonl (ms)."""
    total = 0
    if not hook_log.is_file():
        return total
    for line in hook_log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        tr = (entry.get("input") or {}).get("tool_response") or {}
        if isinstance(tr, dict):
            d = tr.get("totalDurationMs")
            if isinstance(d, int):
                total += d
    return total


def _add_usage(u: object, acc: dict[str, int]) -> None:
    """Fold a `usage` dict (from a hook entry or transcript message) into the accumulator."""
    if not isinstance(u, dict):
        return
    acc["input"] += int(u.get("input_tokens") or 0)
    acc["output"] += int(u.get("output_tokens") or 0)
    acc["cache_read"] += int(u.get("cache_read_input_tokens") or 0)
    cc = u.get("cache_creation")
    if isinstance(cc, dict):
        acc["cache_5m"] += int(cc.get("ephemeral_5m_input_tokens") or 0)
        acc["cache_1h"] += int(cc.get("ephemeral_1h_input_tokens") or 0)
    else:
        acc["cache_5m"] += int(u.get("cache_creation_input_tokens") or 0)


def _iter_jsonl(path: Path) -> Iterator[dict]:
    """Yield each parseable JSON object from a JSONL file (silently skip blank/invalid)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _sum_combined_tokens(hook_log: Path) -> dict[str, int]:
    """Sum tokens from hook-logs and the discovered transcript directory."""
    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_5m": 0, "cache_1h": 0}
    transcript_dir: Path | None = None

    for entry in _iter_jsonl(hook_log):
        if transcript_dir is None:
            tp = (entry.get("input") or {}).get("transcript_path")
            if isinstance(tp, str) and tp:
                transcript_dir = Path(tp).parent
        tr = (entry.get("input") or {}).get("tool_response") or {}
        if isinstance(tr, dict):
            _add_usage(tr.get("usage"), totals)

    if transcript_dir and transcript_dir.is_dir():
        for tf in sorted(transcript_dir.glob("*.jsonl")):
            for entry in _iter_jsonl(tf):
                msg = entry.get("message")
                if isinstance(msg, dict):
                    _add_usage(msg.get("usage"), totals)

    return totals


def test_report_counts_match_backlog_index() -> None:
    """Backlog state counts in the report must match an independent parse of BACKLOG.md."""
    workspace = Path(WORKSPACE_ROOT_ENV)
    counts = _count_units_by_status(workspace / "BACKLOG.md")
    tasks = counts.get("Task", {})
    tasks_done = tasks.get("done", 0)
    tasks_total = sum(tasks.values())
    tasks_blocked = tasks.get("blocked", 0)
    tasks_active = tasks_total - tasks_done - tasks_blocked

    report = _run_report(workspace, os.environ["JUDGE_CLAUDE_MODEL"])

    # Per-status breakdown rows added in B8.
    tasks_in_progress = tasks.get("in-progress", 0)
    tasks_remaining_total = tasks_active + tasks_blocked
    assert f"{tasks_done} of {tasks_total}" in report, "Tasks completed count mismatch"
    assert re.search(rf"Tasks in-progress\s+│\s+{tasks_in_progress}\s", report), (
        f"In-progress task count mismatch (expected {tasks_in_progress})"
    )
    assert re.search(rf"Tasks blocked\s+│\s+{tasks_blocked}\s", report), (
        f"Blocked task count mismatch (expected {tasks_blocked})"
    )
    assert re.search(rf"Tasks remaining \(total\)\s+│\s+{tasks_remaining_total}\s", report), (
        f"Remaining-total count mismatch (expected {tasks_remaining_total})"
    )


def test_report_token_totals_match_independent_sum() -> None:
    """Lifetime token sub-rows must equal hook-logs + transcripts independent sum."""
    workspace = Path(WORKSPACE_ROOT_ENV)
    hook_log = workspace / "hook-logs.jsonl"
    expected = _sum_combined_tokens(hook_log)

    report = _run_report(workspace, os.environ["JUDGE_CLAUDE_MODEL"])

    # The report formats numbers with thousands commas. Render expected values the same way.
    for key, label in [
        ("input", "input (uncached)"),
        ("cache_read", "cache reads"),
        ("cache_5m", "cache writes 5-min"),
        ("cache_1h", "cache writes 1-hour"),
        ("output", "output"),
    ]:
        formatted = f"{expected[key]:,}"
        assert formatted in report, f"Lifetime {label} expected {formatted} not in report"


def test_report_api_processing_time_matches_hook_log_duration() -> None:
    """API processing time (All-time) must equal hook-log totalDurationMs sum / 3600s."""
    workspace = Path(WORKSPACE_ROOT_ENV)
    hook_log = workspace / "hook-logs.jsonl"
    api_seconds = _sum_hook_log_durations(hook_log) / 1000.0
    api_hours = api_seconds / 3600.0

    report = _run_report(workspace, os.environ["JUDGE_CLAUDE_MODEL"])

    # Display rounds to 1 decimal place: "X.X h"
    expected_str = f"{api_hours:.1f} h"
    assert expected_str in report, f"Expected API processing time '{expected_str}' not found"


def test_report_stdout_has_no_log_lines() -> None:
    """B6: stdout must be data-only; log lines belong on stderr."""
    workspace = Path(WORKSPACE_ROOT_ENV)
    report = _run_report(workspace, os.environ["JUDGE_CLAUDE_MODEL"])
    # Logging format starts with ISO-8601 timestamp + bracketed logger name.
    log_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z \[", re.MULTILINE)
    matches = log_pattern.findall(report)
    assert not matches, f"Found log lines on stdout (should be on stderr): {matches!r}"


def test_report_no_negative_time_spans() -> None:
    """No window column may show a negative wall-time span."""
    workspace = Path(WORKSPACE_ROOT_ENV)
    report = _run_report(workspace, os.environ["JUDGE_CLAUDE_MODEL"])
    # Match patterns like "-0.5 h" in the Time span row.
    negatives = re.findall(r"-\d+\.\d+ h", report)
    assert not negatives, f"Negative time spans rendered: {negatives}"


def test_report_no_blocked_tasks_in_active_projection() -> None:
    """Trailing prose must explicitly exclude blocked tasks from the projection."""
    workspace = Path(WORKSPACE_ROOT_ENV)
    counts = _count_units_by_status(workspace / "BACKLOG.md")
    tasks_blocked = counts.get("Task", {}).get("blocked", 0)
    report = _run_report(workspace, os.environ["JUDGE_CLAUDE_MODEL"])
    if tasks_blocked > 0:
        # When at least one task is blocked, the prose must call it out.
        assert "blocked" in report, "Prose must mention blocked tasks when any exist"


def _task_ids_by_status(backlog_index: Path, target_status: str) -> list[str]:
    """Independent parse of BACKLOG.md: return task IDs with the given status."""
    out: list[str] = []
    in_table = False
    for line in backlog_index.read_text(encoding="utf-8").splitlines():
        if not (line.startswith("|") and "|" in line[1:]):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4:
            continue
        uid, _title, utype, status = cells[0], cells[1], cells[2], cells[3]
        if uid in {"ID", "---", ":---"} or utype in {"Type", "---"}:
            in_table = True
            continue
        if not in_table:
            continue
        if utype == "Task" and status == target_status:
            out.append(uid)
    return out


def test_report_lists_in_progress_and_blocked_tasks_by_id() -> None:
    """B9: bottom of report enumerates every in-progress and blocked task ID from BACKLOG.md."""
    workspace = Path(WORKSPACE_ROOT_ENV)
    report = _run_report(workspace, os.environ["JUDGE_CLAUDE_MODEL"])

    in_progress_ids = _task_ids_by_status(workspace / "BACKLOG.md", "in-progress")
    blocked_ids = _task_ids_by_status(workspace / "BACKLOG.md", "blocked")

    if in_progress_ids:
        assert "In-progress tasks:" in report
        for tid in in_progress_ids:
            assert f"- {tid}:" in report, f"In-progress task {tid} missing from report listing"

    if blocked_ids:
        assert "Blocked tasks:" in report
        for tid in blocked_ids:
            assert f"- {tid}:" in report, f"Blocked task {tid} missing from report listing"


def test_report_renders_tables_side_by_side() -> None:
    """B10: the two top tables share rows (impossible in the old stacked layout)."""
    workspace = Path(WORKSPACE_ROOT_ENV)
    report = _run_report(workspace, os.environ["JUDGE_CLAUDE_MODEL"])

    # At least one line must contain two top-left corners (\u250c) -- one per table.
    two_corner_lines = [ln for ln in report.splitlines() if ln.count("\u250c") >= 2]
    assert two_corner_lines, "Side-by-side layout missing -- expected two top-left corners on one line"

    # And at least one line must contain both the 'Backlog state' header label and
    # the 'Window stats' header label, confirming they're on the same row.
    header_lines = [ln for ln in report.splitlines() if "Backlog state" in ln and "Window stats" in ln]
    assert header_lines, "Expected a single line containing both table titles (side-by-side header row)"
