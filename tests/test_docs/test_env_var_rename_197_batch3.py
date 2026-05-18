"""Structural pins for AC-197-8: JUDGE_* -> DEVBENCH_* rename in canonical docs (batch 3).

Verifies that docs/cross-backlog-dependencies.md, docs/remote-ec2-setup.md, and
docs/operational-work.md use DEVBENCH_* as the canonical and ONLY name for every
operational env var.

No per-var backwards-compatibility footnotes may appear in any of these docs.

Spec source: spec/devbench-self-improve.md section 4.9. Issue: #197.
AC: AC-197-8.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
CROSS_BACKLOG_DOC = REPO_ROOT / "docs" / "cross-backlog-dependencies.md"
REMOTE_EC2_DOC = REPO_ROOT / "docs" / "remote-ec2-setup.md"
OPERATIONAL_WORK_DOC = REPO_ROOT / "docs" / "operational-work.md"


def _read_doc(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# docs/cross-backlog-dependencies.md
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCrossBacklogNoLegacyJudgeVars:
    """docs/cross-backlog-dependencies.md must not reference any JUDGE_* operational env vars."""

    def test_no_judge_workspace_root(self) -> None:
        """JUDGE_WORKSPACE_ROOT must not appear; use DEVBENCH_WORKSPACE_ROOT."""
        text = _read_doc(CROSS_BACKLOG_DOC)
        assert "JUDGE_WORKSPACE_ROOT" not in text, (
            "docs/cross-backlog-dependencies.md still contains JUDGE_WORKSPACE_ROOT. "
            "Rename to DEVBENCH_WORKSPACE_ROOT (AC-197-8)."
        )

    def test_no_judge_claude_model(self) -> None:
        """JUDGE_CLAUDE_MODEL must not appear; use DEVBENCH_CLAUDE_MODEL."""
        text = _read_doc(CROSS_BACKLOG_DOC)
        assert "JUDGE_CLAUDE_MODEL" not in text, (
            "docs/cross-backlog-dependencies.md still contains JUDGE_CLAUDE_MODEL. "
            "Rename to DEVBENCH_CLAUDE_MODEL (AC-197-8)."
        )

    def test_devbench_workspace_root_present(self) -> None:
        """DEVBENCH_WORKSPACE_ROOT must appear in cross-backlog-dependencies.md examples."""
        text = _read_doc(CROSS_BACKLOG_DOC)
        assert "DEVBENCH_WORKSPACE_ROOT" in text, (
            "docs/cross-backlog-dependencies.md must use DEVBENCH_WORKSPACE_ROOT in command examples (AC-197-8)."
        )

    def test_devbench_claude_model_present(self) -> None:
        """DEVBENCH_CLAUDE_MODEL must appear in cross-backlog-dependencies.md examples."""
        text = _read_doc(CROSS_BACKLOG_DOC)
        assert "DEVBENCH_CLAUDE_MODEL" in text, (
            "docs/cross-backlog-dependencies.md must use DEVBENCH_CLAUDE_MODEL in command examples (AC-197-8)."
        )


# ---------------------------------------------------------------------------
# docs/remote-ec2-setup.md
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRemoteEc2NoLegacyJudgeVars:
    """docs/remote-ec2-setup.md must not reference any JUDGE_* operational env vars."""

    def test_no_judge_workspace_root(self) -> None:
        """JUDGE_WORKSPACE_ROOT must not appear; use DEVBENCH_WORKSPACE_ROOT."""
        text = _read_doc(REMOTE_EC2_DOC)
        assert "JUDGE_WORKSPACE_ROOT" not in text, (
            "docs/remote-ec2-setup.md still contains JUDGE_WORKSPACE_ROOT. "
            "Rename to DEVBENCH_WORKSPACE_ROOT (AC-197-8)."
        )

    def test_no_judge_claude_model(self) -> None:
        """JUDGE_CLAUDE_MODEL must not appear; use DEVBENCH_CLAUDE_MODEL."""
        text = _read_doc(REMOTE_EC2_DOC)
        assert "JUDGE_CLAUDE_MODEL" not in text, (
            "docs/remote-ec2-setup.md still contains JUDGE_CLAUDE_MODEL. Rename to DEVBENCH_CLAUDE_MODEL (AC-197-8)."
        )

    def test_no_judge_orchestrator_session_id(self) -> None:
        """JUDGE_ORCHESTRATOR_SESSION_ID must not appear; use DEVBENCH_ORCHESTRATOR_SESSION_ID."""
        text = _read_doc(REMOTE_EC2_DOC)
        assert "JUDGE_ORCHESTRATOR_SESSION_ID" not in text, (
            "docs/remote-ec2-setup.md still contains JUDGE_ORCHESTRATOR_SESSION_ID. "
            "Rename to DEVBENCH_ORCHESTRATOR_SESSION_ID (AC-197-8)."
        )

    def test_no_judge_log_file(self) -> None:
        """JUDGE_LOG_FILE must not appear; use DEVBENCH_LOG_FILE."""
        text = _read_doc(REMOTE_EC2_DOC)
        assert "JUDGE_LOG_FILE" not in text, (
            "docs/remote-ec2-setup.md still contains JUDGE_LOG_FILE. Rename to DEVBENCH_LOG_FILE (AC-197-8)."
        )

    def test_devbench_workspace_root_present(self) -> None:
        """DEVBENCH_WORKSPACE_ROOT must appear in remote-ec2-setup.md examples."""
        text = _read_doc(REMOTE_EC2_DOC)
        assert "DEVBENCH_WORKSPACE_ROOT" in text, (
            "docs/remote-ec2-setup.md must use DEVBENCH_WORKSPACE_ROOT in command examples (AC-197-8)."
        )

    def test_devbench_claude_model_present(self) -> None:
        """DEVBENCH_CLAUDE_MODEL must appear in remote-ec2-setup.md examples."""
        text = _read_doc(REMOTE_EC2_DOC)
        assert "DEVBENCH_CLAUDE_MODEL" in text, (
            "docs/remote-ec2-setup.md must use DEVBENCH_CLAUDE_MODEL in command examples (AC-197-8)."
        )

    def test_devbench_orchestrator_session_id_present(self) -> None:
        """DEVBENCH_ORCHESTRATOR_SESSION_ID must appear in remote-ec2-setup.md examples."""
        text = _read_doc(REMOTE_EC2_DOC)
        assert "DEVBENCH_ORCHESTRATOR_SESSION_ID" in text, (
            "docs/remote-ec2-setup.md must use DEVBENCH_ORCHESTRATOR_SESSION_ID in command examples (AC-197-8)."
        )

    def test_devbench_log_file_present(self) -> None:
        """DEVBENCH_LOG_FILE must appear in remote-ec2-setup.md."""
        text = _read_doc(REMOTE_EC2_DOC)
        assert "DEVBENCH_LOG_FILE" in text, (
            "docs/remote-ec2-setup.md must use DEVBENCH_LOG_FILE as the canonical log-file env var (AC-197-8)."
        )


# ---------------------------------------------------------------------------
# docs/operational-work.md
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOperationalWorkNoLegacyJudgeVars:
    """docs/operational-work.md must not reference any JUDGE_* operational env vars."""

    def test_no_judge_vars_present(self) -> None:
        """No JUDGE_* operational env vars may appear in operational-work.md."""
        import re

        text = _read_doc(OPERATIONAL_WORK_DOC)
        # operational-work.md currently has no JUDGE_* vars; assert none are present
        matches = re.findall(r"\bJUDGE_[A-Z_]+", text)
        assert not matches, (
            f"docs/operational-work.md contains JUDGE_* references that must be renamed: "
            f"{sorted(set(matches))} (AC-197-8)."
        )


# ---------------------------------------------------------------------------
# Cross-doc: no backwards-compatibility footnotes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNoBackwardsCompatibilityFootnotesInBatch3:
    """No per-var backwards-compatibility footnotes may appear in the batch-3 docs."""

    @pytest.mark.parametrize(
        "doc_path",
        [CROSS_BACKLOG_DOC, REMOTE_EC2_DOC, OPERATIONAL_WORK_DOC],
        ids=["cross-backlog-dependencies", "remote-ec2-setup", "operational-work"],
    )
    def test_no_backwards_compat_footnotes(self, doc_path: Path) -> None:
        """The rename is hard; no per-var env-var 'Backwards compatibility' notes allowed.

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
