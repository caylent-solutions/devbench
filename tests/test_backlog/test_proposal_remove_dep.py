"""Tests for proposal.remove_dep -- the inverse of add_dep.

``remove_dep`` cuts a cross-task dependency edge so it disappears from every
reader: the ``## Dependencies`` table row for the blocker is removed (collapsing
to the canonical ``| none | | |`` row when the table empties), and the open
``[BLOCKED_PENDING_PROPOSAL] <blocker>`` marker is stripped so
``_has_open_proposal_marker`` / ``_comments_have_marker`` / the add_dep
reverse-cycle guard no longer treat the edge as live.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.backlog.proposal import (
    ProposalError,
    _comments_have_marker,
    _dep_row_has_task,
    add_dep,
    remove_dep,
)

# ---------------------------------------------------------------------------
# Shared workspace helpers (mirrors test_proposal_add_dep.py conventions)
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


def _build_three_task_workspace(
    tmp_path: Path,
    t1_status: str = "blocked",
    t2_status: str = "in-queue",
    t3_status: str = "in-queue",
) -> Path:
    """Build a minimal workspace with T1, T2 and T3 tasks; no cross-deps initially."""
    index = tmp_path / "BACKLOG.md"
    index.write_text(
        _BACKLOG_HEADER + f"| E0-F1-S1-T1 | Task1 | Task | {t1_status} | None | caylent-solutions/example |"
        " `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
        f"| E0-F1-S1-T2 | Task2 | Task | {t2_status} | None | caylent-solutions/example |"
        " `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md` |\n"
        f"| E0-F1-S1-T3 | Task3 | Task | {t3_status} | None | caylent-solutions/example |"
        " `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T3.md` |\n"
    )
    story = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
    story.mkdir(parents=True)
    (story / "E0-F1-S1-T1.md").write_text(_TASK_TEMPLATE.format(task_id="E0-F1-S1-T1", status=t1_status))
    (story / "E0-F1-S1-T2.md").write_text(_TASK_TEMPLATE.format(task_id="E0-F1-S1-T2", status=t2_status))
    (story / "E0-F1-S1-T3.md").write_text(_TASK_TEMPLATE.format(task_id="E0-F1-S1-T3", status=t3_status))
    return tmp_path


def _task_file(workspace: Path, task_id: str) -> Path:
    return workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / f"{task_id}.md"


# ---------------------------------------------------------------------------
# (a) removes a dep-table row
# ---------------------------------------------------------------------------


class TestRemoveDepRemovesRow:
    """remove_dep deletes the blocker's Dependencies-table row from the blocked file."""

    def test_dep_row_is_removed(self, tmp_path: Path) -> None:
        workspace = _build_three_task_workspace(tmp_path)
        backlog_root = workspace / "backlog"
        backlog_index = workspace / "BACKLOG.md"

        add_dep(
            backlog_root=backlog_root,
            backlog_index=backlog_index,
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
        )
        blocked_file = _task_file(workspace, "E0-F1-S1-T1")
        assert _dep_row_has_task(blocked_file, "E0-F1-S1-T2") is True

        removed = remove_dep(
            backlog_root=backlog_root,
            backlog_index=backlog_index,
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
        )

        assert removed is True
        assert _dep_row_has_task(blocked_file, "E0-F1-S1-T2") is False

    def test_only_targeted_row_removed_when_multiple_deps(self, tmp_path: Path) -> None:
        """Removing one edge must leave a second, unrelated dep row intact."""
        workspace = _build_three_task_workspace(tmp_path)
        backlog_root = workspace / "backlog"
        backlog_index = workspace / "BACKLOG.md"

        # T1 depends on both T2 and T3.
        add_dep(
            backlog_root=backlog_root,
            backlog_index=backlog_index,
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
        )
        add_dep(
            backlog_root=backlog_root,
            backlog_index=backlog_index,
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T3",
        )
        blocked_file = _task_file(workspace, "E0-F1-S1-T1")

        remove_dep(
            backlog_root=backlog_root,
            backlog_index=backlog_index,
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
        )

        assert _dep_row_has_task(blocked_file, "E0-F1-S1-T2") is False
        assert _dep_row_has_task(blocked_file, "E0-F1-S1-T3") is True
        # The remaining-table must NOT have collapsed to the placeholder row.
        assert "| none |" not in blocked_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# (b) closes the marker so it is no longer seen as open + reverse add_dep
#     no longer reports a cycle
# ---------------------------------------------------------------------------


class TestRemoveDepClosesMarker:
    """remove_dep strips the [BLOCKED_PENDING_PROPOSAL] marker so every reader
    stops treating the edge as live."""

    def test_comments_have_marker_returns_false_after_remove(self, tmp_path: Path) -> None:
        workspace = _build_three_task_workspace(tmp_path)
        backlog_root = workspace / "backlog"
        backlog_index = workspace / "BACKLOG.md"

        add_dep(
            backlog_root=backlog_root,
            backlog_index=backlog_index,
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
        )
        blocked_file = _task_file(workspace, "E0-F1-S1-T1")
        assert _comments_have_marker(blocked_file, "E0-F1-S1-T2") is True

        remove_dep(
            backlog_root=backlog_root,
            backlog_index=backlog_index,
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
        )

        assert _comments_have_marker(blocked_file, "E0-F1-S1-T2") is False

    def test_has_open_proposal_marker_returns_false_after_remove(self, tmp_path: Path) -> None:
        """The cli-level open-marker walker no longer sees the stripped marker."""
        from devbench.backlog.parser import BacklogParser
        from devbench.cli import _has_open_proposal_marker

        workspace = _build_three_task_workspace(tmp_path)
        backlog_root = workspace / "backlog"
        backlog_index = workspace / "BACKLOG.md"

        add_dep(
            backlog_root=backlog_root,
            backlog_index=backlog_index,
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
        )
        blocked_file = _task_file(workspace, "E0-F1-S1-T1")
        parser = BacklogParser(backlog_root=backlog_root, backlog_index=backlog_index)
        units_by_id = {u.id: u for u in parser.parse_index()}
        assert _has_open_proposal_marker(blocked_file.read_text(encoding="utf-8"), units_by_id) is True

        remove_dep(
            backlog_root=backlog_root,
            backlog_index=backlog_index,
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
        )

        assert _has_open_proposal_marker(blocked_file.read_text(encoding="utf-8"), units_by_id) is False

    def test_reverse_add_dep_no_longer_reports_cycle(self, tmp_path: Path) -> None:
        """After remove_dep cuts T1 -> T2, wiring the reverse edge T2 -> T1 must
        succeed (the cycle guard no longer sees the stripped edge)."""
        workspace = _build_three_task_workspace(tmp_path, t1_status="blocked", t2_status="blocked")
        backlog_root = workspace / "backlog"
        backlog_index = workspace / "BACKLOG.md"

        add_dep(
            backlog_root=backlog_root,
            backlog_index=backlog_index,
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
        )
        # Reverse edge would currently be a cycle.
        with pytest.raises(ProposalError, match="add-dep would create a cycle"):
            add_dep(
                backlog_root=backlog_root,
                backlog_index=backlog_index,
                blocked_task_id="E0-F1-S1-T2",
                blocker_task_id="E0-F1-S1-T1",
            )

        # Cut the original edge.
        remove_dep(
            backlog_root=backlog_root,
            backlog_index=backlog_index,
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
        )

        # Now the reverse edge must wire cleanly without a cycle error.
        wrote = add_dep(
            backlog_root=backlog_root,
            backlog_index=backlog_index,
            blocked_task_id="E0-F1-S1-T2",
            blocker_task_id="E0-F1-S1-T1",
        )
        assert wrote is True

    def test_dep_removed_audit_comment_appended(self, tmp_path: Path) -> None:
        """remove_dep records a [DEP_REMOVED] audit comment naming both tasks + reason."""
        workspace = _build_three_task_workspace(tmp_path)
        backlog_root = workspace / "backlog"
        backlog_index = workspace / "BACKLOG.md"

        add_dep(
            backlog_root=backlog_root,
            backlog_index=backlog_index,
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
        )
        remove_dep(
            backlog_root=backlog_root,
            backlog_index=backlog_index,
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
            reason="edge no longer required",
        )

        content = _task_file(workspace, "E0-F1-S1-T1").read_text(encoding="utf-8")
        assert "[DEP_REMOVED]" in content
        assert "E0-F1-S1-T2" in content
        assert "edge no longer required" in content


# ---------------------------------------------------------------------------
# (c) idempotent no-op on a non-existent edge
# ---------------------------------------------------------------------------


class TestRemoveDepIdempotent:
    """Removing a non-existent edge is a clean no-op returning False, not an error."""

    def test_remove_nonexistent_edge_returns_false(self, tmp_path: Path) -> None:
        workspace = _build_three_task_workspace(tmp_path)
        backlog_root = workspace / "backlog"
        backlog_index = workspace / "BACKLOG.md"

        # No edge was ever wired between T1 and T2.
        removed = remove_dep(
            backlog_root=backlog_root,
            backlog_index=backlog_index,
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
        )
        assert removed is False

    def test_double_remove_is_idempotent(self, tmp_path: Path) -> None:
        """First remove returns True, a second remove of the same edge returns False."""
        workspace = _build_three_task_workspace(tmp_path)
        backlog_root = workspace / "backlog"
        backlog_index = workspace / "BACKLOG.md"

        add_dep(
            backlog_root=backlog_root,
            backlog_index=backlog_index,
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
        )
        first = remove_dep(
            backlog_root=backlog_root,
            backlog_index=backlog_index,
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
        )
        second = remove_dep(
            backlog_root=backlog_root,
            backlog_index=backlog_index,
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
        )
        assert first is True
        assert second is False


# ---------------------------------------------------------------------------
# (d) fail-fast on unknown ids
# ---------------------------------------------------------------------------


class TestRemoveDepFailFast:
    """remove_dep raises ProposalError when either task id is not in the index."""

    @pytest.mark.parametrize(
        ("blocked", "blocker", "missing"),
        [
            ("E0-F1-S1-T1", "E9-F9-S9-T9", "E9-F9-S9-T9"),
            ("E9-F9-S9-T9", "E0-F1-S1-T2", "E9-F9-S9-T9"),
        ],
    )
    def test_unknown_id_raises(self, tmp_path: Path, blocked: str, blocker: str, missing: str) -> None:
        workspace = _build_three_task_workspace(tmp_path)
        with pytest.raises(ProposalError, match=missing):
            remove_dep(
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                blocked_task_id=blocked,
                blocker_task_id=blocker,
            )

    def test_same_blocked_and_blocker_raises(self, tmp_path: Path) -> None:
        workspace = _build_three_task_workspace(tmp_path)
        with pytest.raises(ProposalError, match="same task"):
            remove_dep(
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                blocked_task_id="E0-F1-S1-T1",
                blocker_task_id="E0-F1-S1-T1",
            )


# ---------------------------------------------------------------------------
# (e) collapse-to-| none | | | when the last dep row is removed
# ---------------------------------------------------------------------------


class TestRemoveDepCollapsesToNoneRow:
    """When the last dep row is removed the table collapses to the canonical
    placeholder row so the Dependencies table is never left header-only."""

    def test_collapses_to_none_row(self, tmp_path: Path) -> None:
        workspace = _build_three_task_workspace(tmp_path)
        backlog_root = workspace / "backlog"
        backlog_index = workspace / "BACKLOG.md"

        add_dep(
            backlog_root=backlog_root,
            backlog_index=backlog_index,
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
        )
        blocked_file = _task_file(workspace, "E0-F1-S1-T1")
        # add_dep replaced the | none | | | placeholder with the real row.
        assert "| none |" not in blocked_file.read_text(encoding="utf-8")

        remove_dep(
            backlog_root=backlog_root,
            backlog_index=backlog_index,
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
        )

        content = blocked_file.read_text(encoding="utf-8")
        assert "| none | | |" in content
        # The blocker row must be gone.
        assert _dep_row_has_task(blocked_file, "E0-F1-S1-T2") is False
