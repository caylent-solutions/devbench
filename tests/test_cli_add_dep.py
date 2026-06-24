"""CLI-layer tests for the add-dep cycle guard (issue #253b)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from devbench import cli

_BACKLOG_HEADER = (
    "# Backlog\n\n"
    "## Status Summary\n\n"
    "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
    "|------|-------|------|-------------|----------|---------|\n"
    "| E0 | Example Epic | 0 | 0 | 1 | 1 |\n\n"
    "## Full Work Unit Index\n\n"
    "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
    "|----|-------|------|--------|--------------|------|-----------|\n"
)

_TASK_TEMPLATE = """\
# {task_id}: Task

## Status: {status}

## Target Repository

- **Repo:** `caylent-solutions/example`

## Description

A placeholder task.

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| none | | |

## Acceptance Criteria

- [ ] AC-TEST-001 placeholder

## Changes Manifest

| File | Change |
|------|--------|
| `tests/test_example.py` | add |

## Definition of Done

- [ ] all AC complete

## Comments
"""


def _build_two_task_workspace(tmp_path: Path, t1_status: str = "blocked", t2_status: str = "in-queue") -> Path:
    """Build a minimal workspace with T1 and T2 tasks; no cross-deps initially."""
    index = tmp_path / "BACKLOG.md"
    index.write_text(
        _BACKLOG_HEADER + f"| E0-F1-S1-T1 | Task1 | Task | {t1_status} | None | caylent-solutions/example |"
        " `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
        f"| E0-F1-S1-T2 | Task2 | Task | {t2_status} | None | caylent-solutions/example |"
        " `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md` |\n"
    )
    story = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
    story.mkdir(parents=True)
    (story / "E0-F1-S1-T1.md").write_text(_TASK_TEMPLATE.format(task_id="E0-F1-S1-T1", status=t1_status))
    (story / "E0-F1-S1-T2.md").write_text(_TASK_TEMPLATE.format(task_id="E0-F1-S1-T2", status=t2_status))
    return tmp_path


class TestCmdAddDepCycleGuard:
    """cmd_add_dep must surface the cycle error with rc 1 and a clear message."""

    def test_cycle_via_dep_row_returns_rc1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-253-2: when the reverse edge exists as a dep row, rc is 1."""
        workspace = _build_two_task_workspace(tmp_path)

        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", workspace / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
        ):
            rc_first = cli.cmd_add_dep("E0-F1-S1-T1", "E0-F1-S1-T2")
            assert rc_first == 0

            rc = cli.cmd_add_dep("E0-F1-S1-T2", "E0-F1-S1-T1")

        assert rc == 1

    def test_cycle_via_dep_row_emits_error_message(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-253-2: the verbatim cycle error text is emitted to stderr."""
        workspace = _build_two_task_workspace(tmp_path)

        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", workspace / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
        ):
            cli.cmd_add_dep("E0-F1-S1-T1", "E0-F1-S1-T2")
            cli.cmd_add_dep("E0-F1-S1-T2", "E0-F1-S1-T1")

        err = capsys.readouterr().err
        assert "add-dep would create a cycle" in err

    def test_cycle_via_marker_returns_rc1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """AC-253-2: when the reverse edge exists as a marker, rc is 1."""
        workspace = _build_two_task_workspace(tmp_path)
        t1_path = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        t1_path.write_text(
            t1_path.read_text(encoding="utf-8") + "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n",
            encoding="utf-8",
        )

        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", workspace / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
        ):
            rc = cli.cmd_add_dep("E0-F1-S1-T2", "E0-F1-S1-T1")

        assert rc == 1

    def test_cycle_via_marker_emits_error_to_stderr(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """The marker-detected cycle error is emitted to stderr, not stdout."""
        workspace = _build_two_task_workspace(tmp_path)
        t1_path = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        t1_path.write_text(
            t1_path.read_text(encoding="utf-8") + "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n",
            encoding="utf-8",
        )

        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", workspace / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
        ):
            cli.cmd_add_dep("E0-F1-S1-T2", "E0-F1-S1-T1")

        captured = capsys.readouterr()
        assert "add-dep would create a cycle" in captured.err
        assert captured.out == ""

    def test_non_conflicting_add_dep_succeeds_at_cli(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Precision guard: a fresh dep that does not create a cycle must return rc 0."""
        workspace = _build_two_task_workspace(tmp_path)

        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", workspace / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
        ):
            rc = cli.cmd_add_dep("E0-F1-S1-T1", "E0-F1-S1-T2")

        assert rc == 0
        out = capsys.readouterr().out
        assert '"wired": true' in out
