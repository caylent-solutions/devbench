"""Tests for the reverse-edge cycle guard in proposal.add_dep (issue #253b)."""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.backlog.proposal import ProposalError, add_dep

# ---------------------------------------------------------------------------
# Shared workspace helpers
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


# ---------------------------------------------------------------------------
# Cycle detection via existing dep row
# ---------------------------------------------------------------------------


class TestAddDepCycleViaDepRow:
    """add_dep must reject a reverse edge when the blocker file already lists
    the blocked task as a dep row in its Dependencies table."""

    def test_raises_cycle_error_when_reverse_dep_row_exists(self, tmp_path: Path) -> None:
        """AC-253-2: fail fast with verbatim cycle error and ProposalError when the
        reverse edge exists as a dep row on the blocker's file."""
        workspace = _build_two_task_workspace(tmp_path)

        # Wire T1 -> T2 first (T1 blocked on T2).
        add_dep(
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
        )

        # Now attempt to wire the reverse edge T2 -> T1 (T2 blocked on T1).
        # The blocker file (T1) already has T2 as a dep, so this is a cycle.
        with pytest.raises(ProposalError, match="add-dep would create a cycle"):
            add_dep(
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                blocked_task_id="E0-F1-S1-T2",
                blocker_task_id="E0-F1-S1-T1",
            )

    def test_cycle_error_names_both_tasks(self, tmp_path: Path) -> None:
        """The cycle error message names the blocker and blocked task IDs for clarity."""
        workspace = _build_two_task_workspace(tmp_path)

        add_dep(
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
        )

        with pytest.raises(ProposalError, match="E0-F1-S1-T1"):
            add_dep(
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                blocked_task_id="E0-F1-S1-T2",
                blocker_task_id="E0-F1-S1-T1",
            )


# ---------------------------------------------------------------------------
# Cycle detection via existing marker
# ---------------------------------------------------------------------------


class TestAddDepCycleViaMarker:
    """add_dep must reject a reverse edge when the blocker file already carries
    a [BLOCKED_PENDING_PROPOSAL] marker for the blocked task."""

    def _workspace_with_marker_on_blocker(self, tmp_path: Path) -> Path:
        """Build workspace where T1's file already has a [BLOCKED_PENDING_PROPOSAL] T2 marker,
        but T2's Dependencies table has no row for T1 yet."""
        workspace = _build_two_task_workspace(tmp_path, t1_status="blocked", t2_status="in-queue")
        t1_path = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        # Manually inject a marker onto T1 indicating T1 depends on T2,
        # but without a dep-table row (tests that marker alone is sufficient for detection).
        content = t1_path.read_text(encoding="utf-8")
        content += "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n"
        t1_path.write_text(content, encoding="utf-8")
        return workspace

    def test_raises_cycle_error_when_reverse_marker_exists(self, tmp_path: Path) -> None:
        """AC-253-2: fail fast when the reverse edge exists only as a marker (no dep row)."""
        workspace = self._workspace_with_marker_on_blocker(tmp_path)

        # T1 already has a [BLOCKED_PENDING_PROPOSAL] T2 marker -- attempting
        # to wire T2 blocked-on T1 is a cycle.
        with pytest.raises(ProposalError, match="add-dep would create a cycle"):
            add_dep(
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                blocked_task_id="E0-F1-S1-T2",
                blocker_task_id="E0-F1-S1-T1",
            )

    def test_marker_cycle_error_names_blocker(self, tmp_path: Path) -> None:
        """The cycle error names the blocker task so the operator can diagnose it."""
        workspace = self._workspace_with_marker_on_blocker(tmp_path)

        with pytest.raises(ProposalError, match="E0-F1-S1-T1"):
            add_dep(
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                blocked_task_id="E0-F1-S1-T2",
                blocker_task_id="E0-F1-S1-T1",
            )


# ---------------------------------------------------------------------------
# Precise guard -- non-conflicting calls still succeed
# ---------------------------------------------------------------------------


class TestAddDepNonCyclicSucceeds:
    """The guard must be precise: non-conflicting add_dep calls still succeed."""

    def test_non_conflicting_add_dep_succeeds(self, tmp_path: Path) -> None:
        """AC-FINAL (precision): a fresh dep that does not create a cycle must succeed."""
        workspace = _build_two_task_workspace(tmp_path)

        wrote = add_dep(
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
        )
        assert wrote is True

    def test_idempotent_call_does_not_raise(self, tmp_path: Path) -> None:
        """Repeating the same add_dep call must not raise (not a cycle, a no-op)."""
        workspace = _build_two_task_workspace(tmp_path)

        add_dep(
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
        )
        # Second call must be a no-op (idempotent), not raise a cycle error.
        wrote = add_dep(
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
        )
        assert wrote is False
