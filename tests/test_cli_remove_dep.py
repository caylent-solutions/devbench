"""CLI-layer tests for the remove-dep verb (inverse of add-dep)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench import cli

# ---------------------------------------------------------------------------
# Shared workspace helpers (mirrors test_cli_add_dep.py conventions)
# ---------------------------------------------------------------------------

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


def _patched(workspace: Path):
    return (
        patch("devbench.cli.WORKSPACE_ROOT", workspace),
        patch("devbench.cli.BACKLOG_ROOT", workspace / "backlog"),
        patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
    )


# ---------------------------------------------------------------------------
# Happy path: removes an existing edge
# ---------------------------------------------------------------------------


class TestCmdRemoveDepHappyPath:
    def test_remove_existing_edge_returns_rc0_and_removed_true(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        workspace = _build_two_task_workspace(tmp_path)
        p1, p2, p3 = _patched(workspace)

        with p1, p2, p3:
            rc_wire = cli.cmd_add_dep("E0-F1-S1-T1", "E0-F1-S1-T2")
            assert rc_wire == 0
            capsys.readouterr()  # drain add-dep output
            rc = cli.cmd_remove_dep("E0-F1-S1-T1", "E0-F1-S1-T2", "--reason", "no longer needed")

        assert rc == 0
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["blocked"] == "E0-F1-S1-T1"
        assert payload["blocker"] == "E0-F1-S1-T2"
        assert payload["removed"] is True
        assert payload["reason"] == "no longer needed"


# ---------------------------------------------------------------------------
# Idempotent no-op
# ---------------------------------------------------------------------------


class TestCmdRemoveDepNoOp:
    def test_remove_nonexistent_edge_returns_rc0_and_removed_false(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        workspace = _build_two_task_workspace(tmp_path)
        p1, p2, p3 = _patched(workspace)

        with p1, p2, p3:
            rc = cli.cmd_remove_dep("E0-F1-S1-T1", "E0-F1-S1-T2")

        assert rc == 0
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["removed"] is False


# ---------------------------------------------------------------------------
# Fail-fast on unknown ids / bad format
# ---------------------------------------------------------------------------


class TestCmdRemoveDepFailFast:
    def test_unknown_blocker_returns_rc1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        workspace = _build_two_task_workspace(tmp_path)
        p1, p2, p3 = _patched(workspace)

        with p1, p2, p3:
            rc = cli.cmd_remove_dep("E0-F1-S1-T1", "E9-F9-S9-T9")

        assert rc == 1
        err = capsys.readouterr().err
        assert "E9-F9-S9-T9" in err

    def test_bad_id_format_returns_rc1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        workspace = _build_two_task_workspace(tmp_path)
        p1, p2, p3 = _patched(workspace)

        with p1, p2, p3:
            rc = cli.cmd_remove_dep("not-an-id", "E0-F1-S1-T2")

        assert rc == 1
        err = capsys.readouterr().err
        assert "format" in err

    def test_em_dash_reason_rejected(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        workspace = _build_two_task_workspace(tmp_path)
        p1, p2, p3 = _patched(workspace)

        with p1, p2, p3:
            rc = cli.cmd_remove_dep("E0-F1-S1-T1", "E0-F1-S1-T2", "--reason", "drop — done")

        assert rc == 1
        err = capsys.readouterr().err
        assert "em-dash" in err
