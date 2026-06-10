"""Tests for classify_blocked_task_excluding_degradation (issue #248a).

Verifies that the new classifier skips the RUNTIME_DEGRADATION rung and
returns the underlying structural bucket when a composite-blocked task
carries both a degradation signal and a structural blocker.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Shared workspace builders
# ---------------------------------------------------------------------------


def _write_backlog(tmp_path: Path, rows: list[str]) -> None:
    """Write a minimal BACKLOG.md with the given row lines under the standard header."""
    (tmp_path / "BACKLOG.md").write_text(
        "# Backlog\n\n## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|----|-------|------|--------|--------------|------|-----------|\n" + "\n".join(rows) + "\n"
    )


def _make_story_dir(tmp_path: Path) -> Path:
    story_dir = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
    story_dir.mkdir(parents=True)
    return story_dir


def _degradation_comment(ts_str: str) -> str:
    """Return a [BLOCKED] audit line that matches _RUNTIME_DEGRADATION_BODY_RE."""
    return f"[{ts_str}] [agent/orchestrator] [BLOCKED] agent-tool-unavailable: review-supervisor only Bash remains\n"


def _marker_comment(target_id: str) -> str:
    return (
        f"[2026-04-20 00:00 UTC] [agent/task_factory] [PROPOSAL_PROMOTED] {target_id} "
        f"promoted. [BLOCKED_PENDING_PROPOSAL] {target_id}\n"
    )


def _build_composite_workspace(
    tmp_path: Path,
    *,
    degradation_ts: datetime,
    marker_target_id: str,
    marker_target_status: str,
) -> Path:
    """Build a workspace where T1 is both RUNTIME_DEGRADATION and carries a structural blocker.

    T1 has:
    - a recent [BLOCKED] audit row with agent-tool-unavailable payload
    - a [BLOCKED_PENDING_PROPOSAL] marker pointing at marker_target_id

    BACKLOG.md lists both T1 (blocked) and the marker target.
    """
    story_dir = _make_story_dir(tmp_path)
    ts_str = degradation_ts.strftime("%Y-%m-%d %H:%M UTC")

    source_content = (
        "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n"
        "## Description\n\nx\n\n"
        "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
        "## Comments\n\n" + _degradation_comment(ts_str) + _marker_comment(marker_target_id)
    )
    (story_dir / "E0-F1-S1-T1.md").write_text(source_content)
    (story_dir / f"{marker_target_id}.md").write_text(
        f"# {marker_target_id}: Marker\n\n## Status: {marker_target_status}\n"
    )

    _write_backlog(
        tmp_path,
        [
            "| E0-F1-S1-T1 | Source | Task | blocked | None | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |",
            f"| {marker_target_id} | Marker | Task | {marker_target_status} | None | r |"
            f" `backlog/E0/E0-F1/E0-F1-S1/{marker_target_id}.md` |",
        ],
    )
    return tmp_path


def _build_pure_degradation_workspace(tmp_path: Path, *, degradation_ts: datetime) -> Path:
    """Build a workspace where T1 has ONLY a degradation signal -- no structural blocker."""
    story_dir = _make_story_dir(tmp_path)
    ts_str = degradation_ts.strftime("%Y-%m-%d %H:%M UTC")

    source_content = (
        "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n"
        "## Description\n\nx\n\n"
        "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
        "## Comments\n\n" + _degradation_comment(ts_str)
    )
    (story_dir / "E0-F1-S1-T1.md").write_text(source_content)

    _write_backlog(
        tmp_path,
        [
            "| E0-F1-S1-T1 | Source | Task | blocked | None | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |",
        ],
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Tests for classify_blocked_task_excluding_degradation
# ---------------------------------------------------------------------------


class TestClassifyBlockedTaskExcludingDegradation:
    """classify_blocked_task_excluding_degradation skips the RUNTIME_DEGRADATION rung."""

    def test_composite_blocked_returns_structural_bucket_auto_clearing(self, tmp_path: Path) -> None:
        """A task with both a degradation signal and an active marker returns AUTO_CLEARING."""
        from devbench.backlog.proposal import (
            BlockedTaskState,
            classify_blocked_task,
            classify_blocked_task_excluding_degradation,
        )

        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        workspace = _build_composite_workspace(
            tmp_path,
            degradation_ts=now,
            marker_target_id="E0-F1-S1-T2",
            marker_target_status="in-queue",
        )

        # Original classifier returns RUNTIME_DEGRADATION (masking the structural blocker).
        state_original = classify_blocked_task(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
            now=now,
        )
        assert state_original is BlockedTaskState.RUNTIME_DEGRADATION

        # New classifier skips degradation and returns the structural bucket.
        state_excl = classify_blocked_task_excluding_degradation(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
            now=now,
        )
        assert state_excl is BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL

    def test_composite_blocked_returns_structural_bucket_operator_action(self, tmp_path: Path) -> None:
        """A task with degradation + unknown marker target returns OPERATOR_ACTION_REQUIRED."""
        from devbench.backlog.proposal import (
            BlockedTaskState,
            classify_blocked_task_excluding_degradation,
        )

        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        story_dir = _make_story_dir(tmp_path)
        ts_str = now.strftime("%Y-%m-%d %H:%M UTC")

        # T1 carries a degradation signal + a marker pointing at E0-F1-S1-T99 (no backlog row)
        source_content = (
            "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Comments\n\n" + _degradation_comment(ts_str) + _marker_comment("E0-F1-S1-T99")
        )
        (story_dir / "E0-F1-S1-T1.md").write_text(source_content)
        _write_backlog(
            tmp_path,
            [
                "| E0-F1-S1-T1 | Source | Task | blocked | None | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |",
            ],
        )

        state = classify_blocked_task_excluding_degradation(
            tmp_path / "backlog",
            tmp_path / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=tmp_path,
            now=now,
        )
        assert state is BlockedTaskState.OPERATOR_ACTION_REQUIRED

    def test_pure_degradation_no_structural_blocker_returns_operator_action(self, tmp_path: Path) -> None:
        """A task with ONLY a degradation signal -- no marker, no dep -- returns OPERATOR_ACTION_REQUIRED.

        The classifier never returns RUNTIME_DEGRADATION; without a structural
        blocker the result falls through to OPERATOR_ACTION_REQUIRED.
        """
        from devbench.backlog.proposal import (
            BlockedTaskState,
            classify_blocked_task_excluding_degradation,
        )

        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        workspace = _build_pure_degradation_workspace(tmp_path, degradation_ts=now)

        state = classify_blocked_task_excluding_degradation(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
            now=now,
        )
        # The degradation rung is skipped; no structural blocker present; falls to OPERATOR_ACTION_REQUIRED.
        assert state is BlockedTaskState.OPERATOR_ACTION_REQUIRED

    def test_new_classifier_never_returns_runtime_degradation(self, tmp_path: Path) -> None:
        """RUNTIME_DEGRADATION is never returned by the excluding classifier."""
        from devbench.backlog.proposal import (
            BlockedTaskState,
            classify_blocked_task_excluding_degradation,
        )

        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        workspace = _build_pure_degradation_workspace(tmp_path, degradation_ts=now)

        state = classify_blocked_task_excluding_degradation(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
            now=now,
        )
        assert state is not BlockedTaskState.RUNTIME_DEGRADATION

    @pytest.mark.parametrize(
        "marker_target_status,expected_bucket",
        [
            ("in-queue", "AUTO_CLEARING_VIA_PROPOSAL"),
            ("hold", "BLOCKED_ON_HELD"),
        ],
    )
    def test_composite_blocked_parametrized_structural_buckets(
        self,
        tmp_path: Path,
        marker_target_status: str,
        expected_bucket: str,
    ) -> None:
        """Parametrized: structural bucket is determined by the marker target status."""
        from devbench.backlog.proposal import (
            BlockedTaskState,
            classify_blocked_task_excluding_degradation,
        )

        now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
        workspace = _build_composite_workspace(
            tmp_path,
            degradation_ts=now,
            marker_target_id="E0-F1-S1-T2",
            marker_target_status=marker_target_status,
        )

        state = classify_blocked_task_excluding_degradation(
            workspace / "backlog",
            workspace / "BACKLOG.md",
            "E0-F1-S1-T1",
            workspace_root=workspace,
            now=now,
        )
        assert state is BlockedTaskState[expected_bucket]

    def test_existing_buckets_are_preserved(self) -> None:
        """All BlockedTaskState members are present (including INTERRUPTED_ON_STOP, TDI-002)."""
        from devbench.backlog.proposal import BlockedTaskState

        expected_members = {
            "AUTO_CLEARING_VIA_PROPOSAL",
            "AWAITING_AMENDMENT_RECOVERY",
            "AWAITING_DEPENDENCY",
            "HELD",
            "BLOCKED_ON_HELD",
            "OPERATOR_ACTION_REQUIRED",
            "RUNTIME_DEGRADATION",
            "INTERRUPTED_ON_STOP",
        }
        actual_members = {m.name for m in BlockedTaskState}
        assert actual_members == expected_members

    def test_task_in_hold_status_returns_held(self, tmp_path: Path) -> None:
        """A task whose own status is hold returns HELD from the excluding classifier."""
        from devbench.backlog.proposal import (
            BlockedTaskState,
            classify_blocked_task_excluding_degradation,
        )

        story_dir = _make_story_dir(tmp_path)
        (story_dir / "E0-F1-S1-T1.md").write_text(
            "# E0-F1-S1-T1: Source\n\n## Status: hold\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n"
        )
        _write_backlog(
            tmp_path,
            [
                "| E0-F1-S1-T1 | Source | Task | hold | None | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |",
            ],
        )

        state = classify_blocked_task_excluding_degradation(
            tmp_path / "backlog",
            tmp_path / "BACKLOG.md",
            "E0-F1-S1-T1",
        )
        assert state is BlockedTaskState.HELD

    def test_source_file_not_found_returns_operator_action(self, tmp_path: Path) -> None:
        """When the source .md file cannot be found, the classifier returns OPERATOR_ACTION_REQUIRED."""
        from devbench.backlog.proposal import (
            BlockedTaskState,
            classify_blocked_task_excluding_degradation,
        )

        # BACKLOG.md references a file that does not exist on disk.
        _write_backlog(
            tmp_path,
            [
                "| E0-F1-S1-T1 | Source | Task | blocked | None | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |",
            ],
        )

        state = classify_blocked_task_excluding_degradation(
            tmp_path / "backlog",
            tmp_path / "BACKLOG.md",
            "E0-F1-S1-T1",
        )
        assert state is BlockedTaskState.OPERATOR_ACTION_REQUIRED

    def test_all_markers_terminal_returns_auto_clearing(self, tmp_path: Path) -> None:
        """When all markers are terminal, the excluding classifier returns AUTO_CLEARING_VIA_PROPOSAL."""
        from devbench.backlog.proposal import (
            BlockedTaskState,
            classify_blocked_task_excluding_degradation,
        )

        story_dir = _make_story_dir(tmp_path)
        source_content = (
            "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Comments\n\n" + _marker_comment("E0-F1-S1-T2")
        )
        (story_dir / "E0-F1-S1-T1.md").write_text(source_content)
        (story_dir / "E0-F1-S1-T2.md").write_text("# E0-F1-S1-T2: Done\n\n## Status: done\n")
        _write_backlog(
            tmp_path,
            [
                "| E0-F1-S1-T1 | Source | Task | blocked | None | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |",
                "| E0-F1-S1-T2 | Done | Task | done | None | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md` |",
            ],
        )

        state = classify_blocked_task_excluding_degradation(
            tmp_path / "backlog",
            tmp_path / "BACKLOG.md",
            "E0-F1-S1-T1",
        )
        # All markers terminal with no regular dep or recovery signal -> AUTO_CLEARING_VIA_PROPOSAL.
        assert state is BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL

    def test_awaiting_dependency_returned_when_regular_dep_unsatisfied(self, tmp_path: Path) -> None:
        """When a regular dependency is non-terminal, the excluding classifier returns AWAITING_DEPENDENCY."""
        from devbench.backlog.proposal import (
            BlockedTaskState,
            classify_blocked_task_excluding_degradation,
        )

        story_dir = _make_story_dir(tmp_path)
        source_content = (
            "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n"
            "| E0-F1-S1-T2 | Dep | in-queue |\n"
        )
        (story_dir / "E0-F1-S1-T1.md").write_text(source_content)
        (story_dir / "E0-F1-S1-T2.md").write_text("# E0-F1-S1-T2: Dep\n\n## Status: in-queue\n")
        _write_backlog(
            tmp_path,
            [
                "| E0-F1-S1-T1 | Source | Task | blocked | E0-F1-S1-T2 | r |"
                " `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |",
                "| E0-F1-S1-T2 | Dep | Task | in-queue | None | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md` |",
            ],
        )

        state = classify_blocked_task_excluding_degradation(
            tmp_path / "backlog",
            tmp_path / "BACKLOG.md",
            "E0-F1-S1-T1",
        )
        assert state is BlockedTaskState.AWAITING_DEPENDENCY
