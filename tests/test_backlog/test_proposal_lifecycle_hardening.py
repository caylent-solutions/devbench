"""Issues #143 + #144 regression: TODO/TBD placeholder rejection + cascade-depth limit.

Pin the helpers that gate ``cmd_materialise_proposal`` from emitting drafts
that carry placeholder descriptions or that would push the recovery cascade
past its configured depth cap.
"""

from __future__ import annotations

import pytest

from devbench.backlog.proposal import (
    CascadeDepthError,
    Proposal,
    ProposedTask,
    detect_placeholder_descriptions,
    enforce_cascade_depth,
)


def _make_proposal(
    proposed_tasks: list[ProposedTask] | None = None,
    cascade_depth: int = 0,
) -> Proposal:
    return Proposal(
        source_task_id="E0-F1-S1-T1",
        generated_at="2026-05-02T00:00:00Z",
        rejection_reason="fixture",
        proposed_tasks=proposed_tasks or [],
        cascade_depth=cascade_depth,
    )


def _task(suggested_id: str, suggested_approach: str) -> ProposedTask:
    return ProposedTask(
        suggested_id=suggested_id,
        title=f"title for {suggested_id}",
        files_to_own=["x.py"],
        linked_scenarios=["AC-1"],
        suggested_acs=["AC-1"],
        suggested_approach=suggested_approach,
    )


class TestDetectPlaceholderDescriptions:
    """Issue #143."""

    def test_all_concrete_returns_empty(self) -> None:
        proposal = _make_proposal(
            [
                _task("E0-F1-S1-T2", "Author the foo helper that does X"),
                _task("E0-F1-S1-T3", "Add the bar regression test for Y"),
            ]
        )
        assert detect_placeholder_descriptions(proposal) == []

    @pytest.mark.parametrize(
        "approach",
        [
            "",
            "   ",
            "\t\n",
            "TODO",
            "todo",
            "Todo",
            "TODO -- describe change",
            "TBD",
            "tbd",
            "TBD -- fill in later",
        ],
    )
    def test_placeholder_or_empty_flagged(self, approach: str) -> None:
        proposal = _make_proposal([_task("E0-F1-S1-T2", approach)])
        issues = detect_placeholder_descriptions(proposal)
        assert len(issues) == 1
        assert "E0-F1-S1-T2" in issues[0]

    def test_concrete_text_after_todo_word_not_flagged(self) -> None:
        """A real description that just happens to mention "todo" mid-sentence
        is not a placeholder. The flag fires only when the WHOLE description
        is the placeholder pattern."""
        proposal = _make_proposal([_task("E0-F1-S1-T2", "Audit the codebase for stale todo comments and remove them")])
        assert detect_placeholder_descriptions(proposal) == []

    def test_multiple_offenders_all_reported(self) -> None:
        proposal = _make_proposal(
            [
                _task("E0-F1-S1-T2", "TODO"),
                _task("E0-F1-S1-T3", "Real description"),
                _task("E0-F1-S1-T4", "TBD -- later"),
            ]
        )
        issues = detect_placeholder_descriptions(proposal)
        assert len(issues) == 2
        assert any("E0-F1-S1-T2" in i for i in issues)
        assert any("E0-F1-S1-T4" in i for i in issues)


class TestEnforceCascadeDepth:
    """Issue #144."""

    def test_below_cap_passes(self) -> None:
        enforce_cascade_depth({"cascade_depth": 0}, 3)
        enforce_cascade_depth({"cascade_depth": 1}, 3)
        enforce_cascade_depth({"cascade_depth": 2}, 3)

    def test_at_cap_raises(self) -> None:
        with pytest.raises(CascadeDepthError, match="cascade_depth=3"):
            enforce_cascade_depth({"cascade_depth": 3}, 3)

    def test_above_cap_raises(self) -> None:
        with pytest.raises(CascadeDepthError, match="cascade_depth=10"):
            enforce_cascade_depth({"cascade_depth": 10}, 3)

    def test_missing_cascade_depth_treated_as_zero(self) -> None:
        """Forward-compat: legacy proposals authored before #144 shipped
        carry no cascade_depth field; treat as depth 0 so they always pass
        the cap."""
        enforce_cascade_depth({}, 3)

    def test_invalid_cascade_depth_raises(self) -> None:
        with pytest.raises(CascadeDepthError, match="must be an integer"):
            enforce_cascade_depth({"cascade_depth": "not-a-number"}, 3)

    def test_proposal_dataclass_round_trip_preserves_cascade_depth(self) -> None:
        """The Proposal dataclass + from_dict/to_dict cycle preserves the
        new optional fields. Forward-compat with proposals that omit them."""
        original = _make_proposal(cascade_depth=2)
        data = original.to_dict()
        restored = Proposal.from_dict(data)
        assert restored.cascade_depth == 2

    def test_proposal_dataclass_legacy_payload_defaults_depth_zero(self) -> None:
        """Loading a proposal JSON that predates #144 yields cascade_depth = 0."""
        legacy_payload = {
            "source_task_id": "E0-F1-S1-T1",
            "generated_at": "2026-05-01T00:00:00Z",
            "rejection_reason": "legacy",
            "proposed_tasks": [],
        }
        restored = Proposal.from_dict(legacy_payload)
        assert restored.cascade_depth == 0
        assert restored.fix_signature == ""

    def test_proposal_cascade_depth_invalid_type_rejected(self) -> None:
        """Issue #144 parser: non-int cascade_depth values raise ValueError
        with a clear message naming the bad input.
        """
        import pytest

        bad_payload = {
            "source_task_id": "E0-F1-S1-T1",
            "generated_at": "2026-05-01T00:00:00Z",
            "rejection_reason": "fixture",
            "proposed_tasks": [],
            "cascade_depth": "not-an-int",
        }
        with pytest.raises(ValueError, match="cascade_depth must be a non-negative integer"):
            Proposal.from_dict(bad_payload)

    def test_proposal_cascade_depth_negative_rejected(self) -> None:
        """Issue #144 parser: negative cascade_depth raises ValueError."""
        import pytest

        bad_payload = {
            "source_task_id": "E0-F1-S1-T1",
            "generated_at": "2026-05-01T00:00:00Z",
            "rejection_reason": "fixture",
            "proposed_tasks": [],
            "cascade_depth": -1,
        }
        with pytest.raises(ValueError, match="cascade_depth must be >= 0"):
            Proposal.from_dict(bad_payload)


class TestClassifyBlockedConsidersRegularDeps:
    """Issue #149: ``classify_blocked_task`` considers the source task's
    declared regular dependencies. When the markers are closed/absent AND
    the regular deps are still in flight (non-terminal) and no marker is
    present, the result must be ``AWAITING_DEPENDENCY`` rather than
    ``OPERATOR_ACTION_REQUIRED`` -- the cascade owner / orchestrator will
    requeue the task on the next sweep, no operator action is required.
    """

    def _build(
        self,
        tmp_path,
        *,
        regular_deps: list[tuple[str, str]],
        markers: list[tuple[str, str]],
    ):
        """Materialise a workspace where the blocked source task carries the
        given regular deps + marker entries.

        ``regular_deps`` is a list of ``(dep_id, dep_status)`` tuples that
        end up as Dependencies-table rows on the source task and as backlog
        rows. ``markers`` is the same format but the IDs become
        ``[BLOCKED_PENDING_PROPOSAL]`` markers in Comments.
        """
        from pathlib import Path

        backlog_dir: Path = tmp_path / "backlog"
        story_dir = backlog_dir / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)

        dep_rows = (
            "\n".join(f"| {dep_id} | T | {status} |" for dep_id, status in regular_deps)
            if regular_deps
            else "| none | | |"
        )
        comments = "## Comments\n\n" + "\n".join(
            f"[2026-04-20 00:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] {mid}" for mid, _ in markers
        )
        source = story_dir / "E0-F1-S1-T1.md"
        source.write_text(
            "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n"
            "## Description\n\nx\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-1 fixture\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `x.py` | y |\n\n"
            "## Definition of Done\n\n- [ ] done\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n"
            f"{dep_rows}\n\n"
            f"{comments}\n",
            encoding="utf-8",
        )

        rows = ["| E0-F1-S1-T1 | Source | Task | blocked | None | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |"]
        for dep_id, status in (*regular_deps, *markers):
            (story_dir / f"{dep_id}.md").write_text(f"# {dep_id}: Marker\n\n## Status: {status}\n", encoding="utf-8")
            rows.append(f"| {dep_id} | X | Task | {status} | None | r | `backlog/E0/E0-F1/E0-F1-S1/{dep_id}.md` |")

        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n" + "\n".join(rows) + "\n",
            encoding="utf-8",
        )
        return tmp_path

    def test_markers_absent_and_deps_unsatisfied_is_awaiting_dependency(self, tmp_path) -> None:
        """Case 1: no markers, regular dep still in-progress -> AWAITING_DEPENDENCY."""
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        ws = self._build(
            tmp_path,
            regular_deps=[("E0-F1-S1-T2", "in-progress")],
            markers=[],
        )
        state = classify_blocked_task(ws / "backlog", ws / "BACKLOG.md", "E0-F1-S1-T1")
        assert state is BlockedTaskState.AWAITING_DEPENDENCY

    def test_markers_absent_and_deps_satisfied_is_operator_action_required(self, tmp_path) -> None:
        """Case 2: no markers, all deps terminal -> OPERATOR_ACTION_REQUIRED."""
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        ws = self._build(
            tmp_path,
            regular_deps=[("E0-F1-S1-T2", "done")],
            markers=[],
        )
        state = classify_blocked_task(ws / "backlog", ws / "BACKLOG.md", "E0-F1-S1-T1")
        assert state is BlockedTaskState.OPERATOR_ACTION_REQUIRED

    def test_markers_all_terminal_and_deps_unsatisfied_is_awaiting_dependency(self, tmp_path) -> None:
        """Case 3 (issue #186 fix): all markers terminal AND a regular dep
        still in-progress -> AWAITING_DEPENDENCY.

        Previously this bucketed as OPERATOR_ACTION_REQUIRED because the
        marker-present branch returned early. The fix lets
        ``_classify_with_markers`` return ``None`` when every marker is
        stale-terminal so the classifier falls through to the regular-dep
        check and surfaces the real blocker (an unsatisfied dependency,
        not operator-attention work).
        """
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        ws = self._build(
            tmp_path,
            regular_deps=[("E0-F1-S1-T9", "in-progress")],
            markers=[("E0-F1-S1-T2", "done")],
        )
        state = classify_blocked_task(ws / "backlog", ws / "BACKLOG.md", "E0-F1-S1-T1")
        assert state is BlockedTaskState.AWAITING_DEPENDENCY

    def test_markers_open_keeps_auto_clearing_regardless_of_deps(self, tmp_path) -> None:
        """Case 4: any open marker -> AUTO_CLEARING_VIA_PROPOSAL (unchanged)."""
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        ws = self._build(
            tmp_path,
            regular_deps=[("E0-F1-S1-T9", "done")],
            markers=[("E0-F1-S1-T2", "in-queue")],
        )
        state = classify_blocked_task(ws / "backlog", ws / "BACKLOG.md", "E0-F1-S1-T1")
        assert state is BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL

    def test_regular_deps_helper_returns_false_for_unknown_task(self, tmp_path) -> None:
        """``_regular_deps_unsatisfied`` returns False when the task ID is
        absent from the parsed index. Builds a real index with one task so
        the parser succeeds and the ``target is None`` branch is hit.
        """
        from devbench.backlog.proposal import _regular_deps_unsatisfied

        ws = self._build(
            tmp_path,
            regular_deps=[("E0-F1-S1-T2", "done")],
            markers=[],
        )
        assert _regular_deps_unsatisfied(ws / "backlog", ws / "BACKLOG.md", "E0-F1-S1-T999") is False

    def test_regular_deps_helper_returns_false_when_index_missing(self, tmp_path) -> None:
        """Missing BACKLOG.md is also treated as deps-satisfied (no false alarm)."""
        from devbench.backlog.proposal import _regular_deps_unsatisfied

        assert _regular_deps_unsatisfied(tmp_path / "backlog", tmp_path / "missing.md", "E0-F1-S1-T1") is False
