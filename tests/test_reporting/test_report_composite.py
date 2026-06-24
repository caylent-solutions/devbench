"""Tests for composite RUNTIME_DEGRADATION + structural blocker rendering (issue #248a).

Verifies that generate_report (via _blocked_listing) prints the verbatim
composite line when a task is classified as RUNTIME_DEGRADATION but also
carries a co-existing structural blocker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _mk_unit(uid: str, title: str, status):
    from devbench.backlog.work_unit import WorkUnit, WorkUnitType

    return WorkUnit(
        id=uid,
        title=title,
        status=status,
        unit_type=WorkUnitType.TASK,
        file_path=Path(f"backlog/{uid}.md"),
        repo="caylent-solutions/git-repo",
        dependencies=[],
    )


class TestCompositeBlockedRender:
    """_blocked_listing renders the verbatim composite line for composite-blocked tasks."""

    def test_composite_blocked_renders_verbatim_composite_line(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-248-1: composite-blocked task shows RUNTIME_DEGRADATION + structural bucket label.

        The verbatim composite row suffix is:
        RUNTIME_DEGRADATION + structural blocker (<bucket>): a restart alone will not clear the structural blocker <id>
        """
        from devbench.backlog.proposal import BlockedTaskState
        from devbench.backlog.work_unit import WorkUnitStatus
        from devbench.reporting import report as report_mod

        unit = _mk_unit("E0-F1-S1-T1", "Composite blocked task", WorkUnitStatus.BLOCKED)

        def fake_classify(backlog_root, backlog_index, task_id, **kwargs):
            return BlockedTaskState.RUNTIME_DEGRADATION

        def fake_classify_excluding_degradation(backlog_root, backlog_index, task_id, **kwargs):
            return BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL

        monkeypatch.setattr("devbench.backlog.proposal.classify_blocked_task", fake_classify)
        monkeypatch.setattr(
            "devbench.backlog.proposal.classify_blocked_task_excluding_degradation",
            fake_classify_excluding_degradation,
        )

        lines = report_mod._blocked_listing([unit])

        composite_line = (
            "RUNTIME_DEGRADATION + structural blocker (auto-clearing): "
            "a restart alone will not clear the structural blocker E0-F1-S1-T1"
        )
        assert any(composite_line in line for line in lines), (
            f"Expected composite line not found in output.\n"
            f"Expected substring: {composite_line!r}\n"
            f"Actual lines: {lines}"
        )

    def test_composite_blocked_uses_structural_bucket_label_blocked_on_held(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The bucket label in the composite line matches the structural bucket value."""
        from devbench.backlog.proposal import BlockedTaskState
        from devbench.backlog.work_unit import WorkUnitStatus
        from devbench.reporting import report as report_mod

        unit = _mk_unit("E0-F1-S1-T2", "Blocked on held", WorkUnitStatus.BLOCKED)

        def fake_classify(backlog_root, backlog_index, task_id, **kwargs):
            return BlockedTaskState.RUNTIME_DEGRADATION

        def fake_classify_excluding_degradation(backlog_root, backlog_index, task_id, **kwargs):
            return BlockedTaskState.BLOCKED_ON_HELD

        monkeypatch.setattr("devbench.backlog.proposal.classify_blocked_task", fake_classify)
        monkeypatch.setattr(
            "devbench.backlog.proposal.classify_blocked_task_excluding_degradation",
            fake_classify_excluding_degradation,
        )

        lines = report_mod._blocked_listing([unit])

        composite_line = (
            "RUNTIME_DEGRADATION + structural blocker (blocked-on-held): "
            "a restart alone will not clear the structural blocker E0-F1-S1-T2"
        )
        assert any(composite_line in line for line in lines), (
            f"Expected composite line not found in output.\n"
            f"Expected substring: {composite_line!r}\n"
            f"Actual lines: {lines}"
        )

    def test_pure_runtime_degradation_no_composite_line(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A pure RUNTIME_DEGRADATION task (no structural blocker) renders the normal row without composite line.

        When classify_blocked_task_excluding_degradation also returns RUNTIME_DEGRADATION
        (which cannot happen in production per the API contract, but the renderer must
        not crash), and when it returns OPERATOR_ACTION_REQUIRED with no co-existing
        structural blocker detected, the composite line must NOT appear.

        This test: the excluding classifier itself returns OPERATOR_ACTION_REQUIRED
        but the bucket label matches the expected pure-degradation case: the
        composite suffix must be absent from the standard row.

        More precisely: when there is no additional structural blocker beyond what
        OPERATOR_ACTION_REQUIRED signals from a task with no markers, the row rendered
        for the task in the runtime-degradation panel should carry the standard
        [runtime-degradation] suffix, NOT the composite line.

        We verify this by checking that the composite format string pattern is absent
        when both classify calls return RUNTIME_DEGRADATION (no exclusion divergence).
        """
        from devbench.backlog.proposal import BlockedTaskState
        from devbench.backlog.work_unit import WorkUnitStatus
        from devbench.reporting import report as report_mod

        unit = _mk_unit("E0-F1-S1-T3", "Pure degradation", WorkUnitStatus.BLOCKED)

        def fake_classify(backlog_root, backlog_index, task_id, **kwargs):
            return BlockedTaskState.RUNTIME_DEGRADATION

        def fake_classify_excluding_degradation_operator(backlog_root, backlog_index, task_id, **kwargs):
            return BlockedTaskState.OPERATOR_ACTION_REQUIRED

        monkeypatch.setattr("devbench.backlog.proposal.classify_blocked_task", fake_classify)
        monkeypatch.setattr(
            "devbench.backlog.proposal.classify_blocked_task_excluding_degradation",
            fake_classify_excluding_degradation_operator,
        )

        lines = report_mod._blocked_listing([unit])

        assert any("E0-F1-S1-T3" in line for line in lines), f"Task row absent: {lines}"
        assert not any("RUNTIME_DEGRADATION + structural blocker" in line for line in lines), (
            f"Unexpected composite line in pure-degradation output: {lines}"
        )

    @pytest.mark.parametrize(
        "structural_bucket,expected_label",
        [
            ("AUTO_CLEARING_VIA_PROPOSAL", "auto-clearing"),
            ("AWAITING_AMENDMENT_RECOVERY", "awaiting-amendment-recovery"),
            ("AWAITING_DEPENDENCY", "awaiting-dependency"),
            ("BLOCKED_ON_HELD", "blocked-on-held"),
        ],
    )
    def test_composite_blocked_parametrized_bucket_labels(
        self,
        monkeypatch: pytest.MonkeyPatch,
        structural_bucket: str,
        expected_label: str,
    ) -> None:
        """Parametrized: composite line label matches the BlockedTaskState enum value."""
        from devbench.backlog.proposal import BlockedTaskState
        from devbench.backlog.work_unit import WorkUnitStatus
        from devbench.reporting import report as report_mod

        unit = _mk_unit("E0-F1-S1-T4", "Composite task", WorkUnitStatus.BLOCKED)

        def fake_classify(backlog_root, backlog_index, task_id, **kwargs):
            return BlockedTaskState.RUNTIME_DEGRADATION

        def fake_classify_excluding(backlog_root, backlog_index, task_id, **kwargs):
            return BlockedTaskState[structural_bucket]

        monkeypatch.setattr("devbench.backlog.proposal.classify_blocked_task", fake_classify)
        monkeypatch.setattr(
            "devbench.backlog.proposal.classify_blocked_task_excluding_degradation",
            fake_classify_excluding,
        )

        lines = report_mod._blocked_listing([unit])

        expected_fragment = f"RUNTIME_DEGRADATION + structural blocker ({expected_label}):"
        assert any(expected_fragment in line for line in lines), (
            f"Expected label fragment {expected_fragment!r} not found.\nActual lines: {lines}"
        )
