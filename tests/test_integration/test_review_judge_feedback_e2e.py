"""Issue #156: end-to-end review-judge rejection-feedback cycle.

Smoke test stitching together: cmd_log_rejection_feedback persists a
schema-v1 JSON, the executor-feedback collector orders it correctly,
and the done-gate refuses the transition until a
``[REJECTION_FEEDBACK_RESOLVED]`` audit clears the category.

Coverage of individual code paths lives in ``tests/test_cli.py``; this
test only verifies the contract between the three pieces.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from devbench import cli
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType


def _payload(code: str = "HARDCODED_URL") -> dict[str, object]:
    return {
        "categories": [
            {
                "code": code,
                "severity": "fail",
                "summary": "Hardcoded URL in src/devbench/cli.py:42",
                "remediation": "Read the value from JUDGE_FOO env var instead.",
                "files": ["src/devbench/cli.py"],
            }
        ],
        "raw_verdict_text": "Found hardcoded URL.",
    }


def test_review_judge_feedback_full_cycle(tmp_path: Path, backlog_dir: Path) -> None:
    """Persist -> collect -> done-gate refuse -> resolve -> done-gate accept."""
    task_id = "E0-F1-S1-T2"

    with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
        rc = cli.cmd_log_rejection_feedback("code_review", task_id, "--json", json.dumps(_payload()))
    assert rc == 0
    archive = tmp_path / ".devbench" / "review-failures"
    files = list(archive.glob(f"{task_id}-code_review-*.json"))
    assert len(files) == 1

    with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
        payloads = cli._collect_review_judge_feedback(task_id)
    assert len(payloads) == 1
    assert payloads[0]["judge"] == "code_review"
    assert payloads[0]["categories"][0]["code"] == "HARDCODED_URL"

    wu_file = backlog_dir / f"{task_id}.md"
    wu_file.write_text("# T\n## Status: in-review\n\n## Comments\n", encoding="utf-8")
    units = [
        WorkUnit(
            id=task_id,
            title="t",
            status=WorkUnitStatus.IN_REVIEW,
            unit_type=WorkUnitType.TASK,
            file_path=Path(f"backlog/{task_id}.md"),
            repo="org/repo",
            dependencies=[],
        )
    ]
    mock_parser = MagicMock()
    mock_parser.parse_index.return_value = units
    with (
        patch("devbench.cli.BacklogParser", return_value=mock_parser),
        patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
        patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
    ):
        rc = cli.cmd_mark_done(task_id)
    assert rc == 1

    wu_file.write_text(
        wu_file.read_text(encoding="utf-8")
        + "[2026-05-02 12:00 UTC] [agent/orchestrator] [REJECTION_FEEDBACK_RESOLVED] code_review:HARDCODED_URL\n",
        encoding="utf-8",
    )
    mock_mgr = MagicMock()
    with (
        patch("devbench.cli.BacklogParser", return_value=mock_parser),
        patch("devbench.cli.BACKLOG_ROOT", backlog_dir.parent),
        patch("devbench.cli.BacklogManager", return_value=mock_mgr),
        patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
    ):
        rc = cli.cmd_mark_done(task_id)
    assert rc == 0
    mock_mgr.mark_done.assert_called_once()
