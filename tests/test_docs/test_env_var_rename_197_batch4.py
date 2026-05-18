"""Structural pins for AC-197-8: JUDGE_* -> DEVBENCH_* rename in canonical docs (batch 4).

Verifies that docs/acceptance-criteria-canonical.md and docs/backlog-contract.md use
DEVBENCH_* as the canonical and ONLY name for every operational env var.

No per-var backwards-compatibility footnotes may appear in any of these docs.

Spec source: spec/devbench-self-improve.md section 4.9. Issue: #197.
AC: AC-197-8.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
ACCEPTANCE_CRITERIA_DOC = REPO_ROOT / "docs" / "acceptance-criteria-canonical.md"
BACKLOG_CONTRACT_DOC = REPO_ROOT / "docs" / "backlog-contract.md"


def _read_doc(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# docs/acceptance-criteria-canonical.md
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAcceptanceCriteriaCanonicalNoLegacyJudgeVars:
    """docs/acceptance-criteria-canonical.md must not reference any JUDGE_* operational env vars.

    Note: JUDGE_AGENT_ROLE is an ADR-15 / AC-197-13 exempt identifier. It must NOT
    be renamed and must NOT be flagged by these tests.
    """

    def test_no_judge_claude_model(self) -> None:
        """JUDGE_CLAUDE_MODEL must not appear; use DEVBENCH_CLAUDE_MODEL."""
        text = _read_doc(ACCEPTANCE_CRITERIA_DOC)
        assert "JUDGE_CLAUDE_MODEL" not in text, (
            "docs/acceptance-criteria-canonical.md still contains JUDGE_CLAUDE_MODEL. "
            "Rename to DEVBENCH_CLAUDE_MODEL (AC-197-8)."
        )

    def test_no_judge_workspace_root(self) -> None:
        """JUDGE_WORKSPACE_ROOT must not appear; use DEVBENCH_WORKSPACE_ROOT."""
        text = _read_doc(ACCEPTANCE_CRITERIA_DOC)
        assert "JUDGE_WORKSPACE_ROOT" not in text, (
            "docs/acceptance-criteria-canonical.md still contains JUDGE_WORKSPACE_ROOT. "
            "Rename to DEVBENCH_WORKSPACE_ROOT (AC-197-8)."
        )

    def test_devbench_claude_model_present(self) -> None:
        """DEVBENCH_CLAUDE_MODEL must appear in acceptance-criteria-canonical.md examples."""
        text = _read_doc(ACCEPTANCE_CRITERIA_DOC)
        assert "DEVBENCH_CLAUDE_MODEL" in text, (
            "docs/acceptance-criteria-canonical.md must use DEVBENCH_CLAUDE_MODEL "
            "in AC-FINAL-009 command examples (AC-197-8)."
        )

    def test_devbench_workspace_root_present(self) -> None:
        """DEVBENCH_WORKSPACE_ROOT must appear in acceptance-criteria-canonical.md examples."""
        text = _read_doc(ACCEPTANCE_CRITERIA_DOC)
        assert "DEVBENCH_WORKSPACE_ROOT" in text, (
            "docs/acceptance-criteria-canonical.md must use DEVBENCH_WORKSPACE_ROOT "
            "in AC-FINAL-009 command examples (AC-197-8)."
        )

    def test_no_legacy_judge_operational_vars(self) -> None:
        """No JUDGE_* operational env vars (other than concept names) may appear."""
        exempt: frozenset[str] = frozenset(
            {
                "KNOWN_JUDGE_NAMES",
                "REVIEW_JUDGE_NAMES",
                "SECURITY_JUDGE_NAMES",
                "ALL_REQUIRED_JUDGE_NAMES",
                "WORKFLOW_AGENT_JUDGE_NAMES",
                "JUDGE_AGENT_ROLE",
                "JUDGE_VERDICT",
            }
        )
        text = _read_doc(ACCEPTANCE_CRITERIA_DOC)
        all_tokens = re.findall(r"\bJUDGE_[A-Z_]+", text)
        violations = [t for t in all_tokens if t not in exempt]
        assert not violations, (
            "docs/acceptance-criteria-canonical.md contains JUDGE_* operational env vars "
            "that must be renamed to DEVBENCH_*: "
            f"{sorted(set(violations))} (AC-197-8)."
        )


# ---------------------------------------------------------------------------
# docs/backlog-contract.md
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBacklogContractNoLegacyJudgeVars:
    """docs/backlog-contract.md must not reference any JUDGE_* operational env vars.

    Note: JUDGE_AGENT_ROLE is an ADR-15 / AC-197-13 exempt identifier. It must NOT
    be renamed and must NOT be flagged by these tests.
    """

    def test_no_judge_workspace_root(self) -> None:
        """JUDGE_WORKSPACE_ROOT must not appear; use DEVBENCH_WORKSPACE_ROOT."""
        text = _read_doc(BACKLOG_CONTRACT_DOC)
        assert "JUDGE_WORKSPACE_ROOT" not in text, (
            "docs/backlog-contract.md still contains JUDGE_WORKSPACE_ROOT. "
            "Rename to DEVBENCH_WORKSPACE_ROOT (AC-197-8)."
        )

    def test_no_judge_claude_model(self) -> None:
        """JUDGE_CLAUDE_MODEL must not appear; use DEVBENCH_CLAUDE_MODEL."""
        text = _read_doc(BACKLOG_CONTRACT_DOC)
        assert "JUDGE_CLAUDE_MODEL" not in text, (
            "docs/backlog-contract.md still contains JUDGE_CLAUDE_MODEL. Rename to DEVBENCH_CLAUDE_MODEL (AC-197-8)."
        )

    def test_devbench_workspace_root_present(self) -> None:
        """DEVBENCH_WORKSPACE_ROOT must appear in backlog-contract.md."""
        text = _read_doc(BACKLOG_CONTRACT_DOC)
        assert "DEVBENCH_WORKSPACE_ROOT" in text, (
            "docs/backlog-contract.md must use DEVBENCH_WORKSPACE_ROOT as the canonical "
            "workspace-root env var (AC-197-8)."
        )

    def test_no_legacy_judge_operational_vars(self) -> None:
        """No JUDGE_* operational env vars (other than concept names) may appear."""
        exempt: frozenset[str] = frozenset(
            {
                "KNOWN_JUDGE_NAMES",
                "REVIEW_JUDGE_NAMES",
                "SECURITY_JUDGE_NAMES",
                "ALL_REQUIRED_JUDGE_NAMES",
                "WORKFLOW_AGENT_JUDGE_NAMES",
                "JUDGE_AGENT_ROLE",
                "JUDGE_VERDICT",
            }
        )
        text = _read_doc(BACKLOG_CONTRACT_DOC)
        all_tokens = re.findall(r"\bJUDGE_[A-Z_]+", text)
        violations = [t for t in all_tokens if t not in exempt]
        assert not violations, (
            "docs/backlog-contract.md contains JUDGE_* operational env vars "
            "that must be renamed to DEVBENCH_*: "
            f"{sorted(set(violations))} (AC-197-8)."
        )


# ---------------------------------------------------------------------------
# Cross-doc: no backwards-compatibility footnotes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNoBackwardsCompatibilityFootnotesInBatch4:
    """No per-var backwards-compatibility footnotes may appear in the batch-4 docs."""

    @pytest.mark.parametrize(
        "doc_path",
        [ACCEPTANCE_CRITERIA_DOC, BACKLOG_CONTRACT_DOC],
        ids=["acceptance-criteria-canonical", "backlog-contract"],
    )
    def test_no_backwards_compat_footnotes(self, doc_path: Path) -> None:
        """The rename is hard; no per-var env-var backwards compatibility notes allowed.

        Searches for patterns that indicate a JUDGE_* env var is being described as
        still accepted alongside DEVBENCH_*. CLI flag deprecation notes are not
        env-var backwards-compat notes and are not flagged.
        """
        text = _read_doc(doc_path)
        lines = text.splitlines()
        violations: list[str] = []
        for line in lines:
            lower = line.lower()
            has_judge_ref = "judge_" in lower
            has_bc_language = (
                "still accepts" in lower
                or "also accepts" in lower
                or "legacy name" in lower
                or ("backwards compat" in lower and has_judge_ref)
                or ("backward compat" in lower and has_judge_ref)
                or ("backwards-compat" in lower and has_judge_ref)
                or ("backward-compat" in lower and has_judge_ref)
            )
            if has_bc_language and has_judge_ref:
                violations.append(line.strip())
        assert not violations, (
            f"{doc_path.name} contains per-var backwards-compatibility notes for "
            f"JUDGE_* env vars. The rename is hard; compatibility notes are forbidden "
            f"(AC-197-8 / spec section 4.9.3). Violations:\n" + "\n".join(f"  {v}" for v in violations)
        )
