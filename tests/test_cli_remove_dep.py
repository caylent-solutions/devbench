"""CLI-layer tests for the remove-dep verb (inverse of add-dep)."""

from __future__ import annotations

import json
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


_CONTAINER_TEMPLATE = """\
# {unit_id}: Container

## Status: {status}

## Description

A placeholder container.
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


def _build_task_with_container_dep_workspace(tmp_path: Path) -> Path:
    """Build a workspace with a story container and a Task that lists it as a dependency.

    Mirrors the TDI-001 self-block: ``E0-F1-S1-T1`` carries a ``## Dependencies``
    row for its parent story ``E0-F1-S1``. The story container is present both in
    the index and as a file on disk so ``remove-dep`` can resolve it.
    """
    index = tmp_path / "BACKLOG.md"
    index.write_text(
        _BACKLOG_HEADER + "| E0-F1-S1 | Story | Story | in-queue | None | caylent-solutions/example |"
        " `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1.md` |\n"
        "| E0-F1-S1-T1 | Task1 | Task | blocked | E0-F1-S1 | caylent-solutions/example |"
        " `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
    )
    story_dir = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
    story_dir.mkdir(parents=True)
    (story_dir / "E0-F1-S1.md").write_text(_CONTAINER_TEMPLATE.format(unit_id="E0-F1-S1", status="in-queue"))
    task_body = _TASK_TEMPLATE.format(task_id="E0-F1-S1-T1", status="blocked").replace(
        "| none | | |", "| E0-F1-S1 | Story | in-queue |"
    )
    (story_dir / "E0-F1-S1-T1.md").write_text(task_body)
    return tmp_path


def _patched(workspace: Path):
    return (
        patch("devbench.cli.WORKSPACE_ROOT", workspace),
        patch("devbench.cli.BACKLOG_ROOT", workspace / "backlog"),
        patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
    )


class TestCmdRemoveDepHappyPath:
    def test_remove_existing_edge_returns_rc0_and_removed_true(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        workspace = _build_two_task_workspace(tmp_path)
        p1, p2, p3 = _patched(workspace)

        with p1, p2, p3:
            rc_wire = cli.cmd_add_dep("E0-F1-S1-T1", "E0-F1-S1-T2")
            assert rc_wire == 0
            capsys.readouterr()
            rc = cli.cmd_remove_dep("E0-F1-S1-T1", "E0-F1-S1-T2", "--reason", "no longer needed")

        assert rc == 0
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["blocked"] == "E0-F1-S1-T1"
        assert payload["blocker"] == "E0-F1-S1-T2"
        assert payload["removed"] is True
        assert payload["reason"] == "no longer needed"


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


class TestCmdRemoveDepContainerEdge:
    def test_remove_container_blocker_edge_returns_rc0_and_removed_true(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """remove-dep cuts a Task->parent-story (container) dependency row."""
        workspace = _build_task_with_container_dep_workspace(tmp_path)
        p1, p2, p3 = _patched(workspace)

        with p1, p2, p3:
            rc = cli.cmd_remove_dep("E0-F1-S1-T1", "E0-F1-S1", "--reason", "self-ancestor self-block")

        assert rc == 0
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["blocked"] == "E0-F1-S1-T1"
        assert payload["blocker"] == "E0-F1-S1"
        assert payload["removed"] is True

        task_file = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        body = task_file.read_text(encoding="utf-8")
        assert "| E0-F1-S1 | Story" not in body
        assert "| none | | |" in body

    def test_container_blocker_id_passes_format_validation(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A container blocker id must not be rejected by the id-format gate.

        Before the fix, the shared parser regex ``E<N>-F<N>-S<N>-T<N>`` rejected
        every non-Task id, so the call failed with a 'format' error (rc=1).
        """
        workspace = _build_task_with_container_dep_workspace(tmp_path)
        p1, p2, p3 = _patched(workspace)

        with p1, p2, p3:
            rc = cli.cmd_remove_dep("E0-F1-S1-T1", "E0-F1-S1")

        err = capsys.readouterr().err
        assert "does not match" not in err
        assert rc == 0

    @pytest.mark.parametrize(
        "blocker_id",
        ["E0", "E0-F1", "E0-F1-S1", "E0-F1-S1-T2"],
    )
    def test_parser_accepts_canonical_container_and_task_blocker_shapes(self, blocker_id: str) -> None:
        """remove-dep's argv parser accepts every canonical id shape as the blocker.

        Epic / Feature / Story (container) ids are now valid blocker operands so
        an operator can cut a non-Task dependency edge; the Task shape remains
        valid. The parser returns the ids unchanged with no usage error.
        """
        blocked, blocker, reason = cli._parse_dep_edge_argv(
            ("E0-F1-S1-T1", blocker_id), "remove-dep", allow_container_blocker=True
        )
        assert blocked == "E0-F1-S1-T1"
        assert blocker == blocker_id
        assert reason == ""

    @pytest.mark.parametrize("bad_id", ["not-an-id", "E", "EF1", "E0-X1", "foo-E0-F1-S1-T1"])
    def test_parser_still_rejects_malformed_ids(self, bad_id: str, capsys: pytest.CaptureFixture[str]) -> None:
        """Genuinely malformed ids are still rejected with a format error."""
        blocked, _blocker, _reason = cli._parse_dep_edge_argv(
            ("E0-F1-S1-T1", bad_id), "remove-dep", allow_container_blocker=True
        )
        assert blocked is None
        assert "format" in capsys.readouterr().err

    def test_add_dep_parser_still_rejects_container_blocker(self, capsys: pytest.CaptureFixture[str]) -> None:
        """add-dep must NOT accept a container blocker (would re-create a self-block).

        The default ``allow_container_blocker=False`` path keeps the strict
        Task-only blocker shape, so wiring a new container edge is impossible.
        """
        blocked, _blocker, _reason = cli._parse_dep_edge_argv(("E0-F1-S1-T1", "E0-F1-S1"), "add-dep")
        assert blocked is None
        assert "format" in capsys.readouterr().err
