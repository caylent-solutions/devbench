"""A fix unit re-runs the PARENT unit's failing gate, not just its narrow diagnostic.

Tracked issue: ``fix-unit-validates-narrow-diagnostic-not-parent-full-gate``.

An auto-generated fix unit used to pass review by validating ONLY its own narrow
ACs -- a scoped pytest over the file it owns. It never re-ran the PARENT unit's
failing verification directive, so a fix that merely RELOCATED the failure (traded
one error class for another) could reach ``done`` while the parent's actual gate
still failed.

The corrected contract: when ``build_escalation_proposal`` is given the parent's
failing verification directive, the materialised fix unit's ``## Verification``
carries a directive that re-runs the PARENT's exact gate command (with the
parent's expect-exit), so ``verify-ac`` on the fix unit genuinely executes the
parent gate. A fix that relocates the failure cannot reach done.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench import verification
from devbench.backlog.proposal import (
    Proposal,
    _parent_rerun_ac_id,
    _relabel_verify_directive,
    build_escalation_proposal,
    generate_draft_md,
    materialise_proposal,
)

from .test_proposal import _build_workspace

pytestmark = pytest.mark.unit

_SOURCE = "E0-F1-S1-T1"
_PARENT_DIRECTIVE = (
    "- VERIFY AC-2 | type=command | tool=terragrunt | cmd=`terragrunt run --all -- validate` | expect-exit=0"
)
_PARENT_COMMAND = "terragrunt run --all -- validate"


def _build_with_parent_gate() -> Proposal | None:
    return build_escalation_proposal(
        source_task_id=_SOURCE,
        attributed_files=["providers/aws/kms-key/main.tf"],
        manifest_files=["scripts/run.py"],
        suggested_ids=["E0-F1-S1-T2"],
        generated_at="2026-06-16T00:00:00Z",
        rejection_reason="AC-2 full-subtree validate fails due to a cross-unit defect",
        parent_verify_directive=_PARENT_DIRECTIVE,
    )


class TestParentGateThreadedIntoFixUnit:
    """The parent's failing directive is carried onto each fix task."""

    def test_proposed_task_carries_parent_directive(self) -> None:
        proposal = _build_with_parent_gate()
        assert proposal is not None
        task = proposal.proposed_tasks[0]
        assert task.parent_verify_directive == _PARENT_DIRECTIVE

    def test_backward_compatible_without_parent_directive(self) -> None:
        """Omitting the parent directive still builds a proposal (default None)."""
        proposal = build_escalation_proposal(
            source_task_id=_SOURCE,
            attributed_files=["providers/aws/kms-key/main.tf"],
            manifest_files=["scripts/run.py"],
            suggested_ids=["E0-F1-S1-T2"],
            generated_at="2026-06-16T00:00:00Z",
            rejection_reason="no parent directive supplied",
        )
        assert proposal is not None
        assert proposal.proposed_tasks[0].parent_verify_directive is None

    def test_roundtrips_through_dict(self) -> None:
        proposal = _build_with_parent_gate()
        assert proposal is not None
        restored = Proposal.from_dict(proposal.to_dict())
        assert restored.proposed_tasks[0].parent_verify_directive == _PARENT_DIRECTIVE


class TestFixUnitVerificationRunsParentGate:
    """The materialised fix unit's ## Verification executes the PARENT gate command."""

    def test_draft_verification_includes_parent_command(self) -> None:
        proposal = _build_with_parent_gate()
        assert proposal is not None
        draft_md = generate_draft_md(
            proposal.proposed_tasks[0],
            repo="caylent-solutions/example",
            source_task_id=_SOURCE,
            generated_at="2026-06-16T00:00:00Z",
        )
        items = verification.parse_verification_section(draft_md)
        commands = [item.command for item in items if item.command]
        assert any(_PARENT_COMMAND in (cmd or "") for cmd in commands), (
            "the fix unit's Verification must re-run the PARENT's failing gate command, "
            f"so verify-ac executes it; got commands: {commands}"
        )

    def test_parent_directive_from_verificationitem_raw_form(self) -> None:
        """The parent directive in VerificationItem.raw form (no '- ' bullet) wires correctly.

        The production escalate path passes VerificationItem.raw, which is
        'VERIFY AC-2 | ...' (no leading list bullet). The relabel must not
        duplicate the VERIFY keyword and must run the parent command.
        """
        raw_directive = "VERIFY AC-2 | type=command | cmd=`terragrunt run --all -- validate` | expect-exit=0"
        proposal = build_escalation_proposal(
            source_task_id=_SOURCE,
            attributed_files=["providers/aws/kms-key/main.tf"],
            manifest_files=["scripts/run.py"],
            suggested_ids=["E0-F1-S1-T2"],
            generated_at="2026-06-16T00:00:00Z",
            rejection_reason="raw-form parent directive",
            parent_verify_directive=raw_directive,
        )
        assert proposal is not None
        draft_md = generate_draft_md(
            proposal.proposed_tasks[0],
            repo="caylent-solutions/example",
            source_task_id=_SOURCE,
            generated_at="2026-06-16T00:00:00Z",
        )
        # Parses without error (no duplicate VERIFY keyword) and runs the parent command.
        items = verification.parse_verification_section(draft_md)
        commands = [item.command for item in items if item.command]
        assert any(_PARENT_COMMAND in (cmd or "") for cmd in commands)
        assert "VERIFY VERIFY" not in draft_md, "the VERIFY keyword must not be duplicated"

    def test_materialised_fix_unit_reruns_parent_gate(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        proposal = _build_with_parent_gate()
        assert proposal is not None
        drafts = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        assert len(drafts) == 1
        content = drafts[0].read_text(encoding="utf-8")
        items = verification.parse_verification_section(content)
        commands = [item.command for item in items if item.command]
        assert any(_PARENT_COMMAND in (cmd or "") for cmd in commands), (
            "the materialised fix unit must carry a VERIFY directive running the parent's gate"
        )


class TestParentGateHelperEdges:
    """Edge cases of the parent-gate helper functions."""

    def test_parent_rerun_ac_id_none_when_no_rerun_ac(self) -> None:
        """No AC asserting a re-run -> None (the directive stays narrow)."""
        assert _parent_rerun_ac_id(["AC-1 the file is corrected", "AC-2 lint passes"]) is None

    def test_parent_rerun_ac_id_skips_lines_without_ac_id(self) -> None:
        """A line carrying no AC-N id is skipped; the re-run AC is still found."""
        acs = ["a prose line with no ac id", "AC-FIX-2 re-running the parent gate passes"]
        assert _parent_rerun_ac_id(acs) == "AC-FIX-2"

    def test_relabel_directive_without_fields_relabels_ac_only(self) -> None:
        """A directive with no '|' fields (just 'VERIFY AC-2') is relabelled cleanly."""
        out = _relabel_verify_directive("VERIFY AC-2", "AC-FIX-2")
        assert out == "- VERIFY AC-FIX-2"

    def test_relabel_directive_strips_leading_bullet(self) -> None:
        """A '- VERIFY ...' bullet form is relabelled without duplicating VERIFY."""
        out = _relabel_verify_directive("- VERIFY AC-2 | type=judge", "AC-FIX-2")
        assert out == "- VERIFY AC-FIX-2 | type=judge"
        assert "VERIFY VERIFY" not in out


class TestParentFailingVerifyDirectiveExtraction:
    """cli._parent_failing_verify_directive selects the parent's failing gate line."""

    def _write_parent(self, tmp_path: Path, verification_block: str) -> Path:
        wu = tmp_path / "E0-F1-S1-T1.md"
        wu.write_text(
            "# E0-F1-S1-T1: Parent\n\n## Status: blocked\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-2 live validate passes\n\n"
            f"## Verification\n\n{verification_block}\n\n## Comments\n",
            encoding="utf-8",
        )
        return wu

    def test_prefers_infra_directive(self, tmp_path: Path) -> None:
        from devbench import cli

        wu = self._write_parent(
            tmp_path,
            "- VERIFY AC-1 | type=command | cmd=`uv run pytest` | expect-exit=0\n"
            "- VERIFY AC-2 | type=command | tool=terragrunt | cmd=`terragrunt run --all -- validate` | expect-exit=0",
        )
        directive = cli._parent_failing_verify_directive(wu)
        assert directive is not None
        assert "terragrunt run --all -- validate" in directive

    def test_falls_back_to_first_executable(self, tmp_path: Path) -> None:
        from devbench import cli

        wu = self._write_parent(tmp_path, "- VERIFY AC-1 | type=command | cmd=`uv run pytest tests/` | expect-exit=0")
        directive = cli._parent_failing_verify_directive(wu)
        assert directive is not None
        assert "uv run pytest tests/" in directive

    def test_none_when_no_executable_directive(self, tmp_path: Path) -> None:
        from devbench import cli

        wu = self._write_parent(tmp_path, "- VERIFY AC-2 | type=judge")
        assert cli._parent_failing_verify_directive(wu) is None

    def test_none_when_file_missing(self, tmp_path: Path) -> None:
        from devbench import cli

        assert cli._parent_failing_verify_directive(tmp_path / "absent.md") is None
