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
        # No exception.
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
