"""Structural pins for AC-197-8: JUDGE_* -> DEVBENCH_* rename in example workspace files.

Verifies that the brownfield multi-repo_single-pr_no-merge example's README,
devbench-commands.txt launcher template, and before/backlog/config/devbench.yaml
use DEVBENCH_* as the canonical and ONLY name for every operational env var.

No per-var backwards-compatibility footnotes may appear in any of these files.

Spec source: spec/devbench-self-improve.md section 4.9. Issue: #197.
AC: AC-197-8.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
EXAMPLE_DIR = REPO_ROOT / "examples" / "backlogs" / "brownfield" / "multi-repo_single-pr_no-merge"
README = EXAMPLE_DIR / "README.md"
COMMANDS_TXT = EXAMPLE_DIR / "before" / "devbench-commands.txt"
DEVBENCH_YAML = EXAMPLE_DIR / "before" / "backlog" / "config" / "devbench.yaml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# README.md
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExampleReadmeNoLegacyJudgeVars:
    """The example README.md must use DEVBENCH_* as the canonical and ONLY name."""

    def test_no_judge_workspace_root(self) -> None:
        """JUDGE_WORKSPACE_ROOT must not appear; use DEVBENCH_WORKSPACE_ROOT."""
        text = _read(README)
        assert "JUDGE_WORKSPACE_ROOT" not in text, (
            "examples README.md still contains JUDGE_WORKSPACE_ROOT. Rename to DEVBENCH_WORKSPACE_ROOT (AC-197-8)."
        )

    def test_no_judge_claude_model(self) -> None:
        """JUDGE_CLAUDE_MODEL must not appear; use DEVBENCH_CLAUDE_MODEL."""
        text = _read(README)
        assert "JUDGE_CLAUDE_MODEL" not in text, (
            "examples README.md still contains JUDGE_CLAUDE_MODEL. Rename to DEVBENCH_CLAUDE_MODEL (AC-197-8)."
        )

    def test_no_judge_use_bedrock(self) -> None:
        """JUDGE_USE_BEDROCK must not appear; use DEVBENCH_USE_BEDROCK."""
        text = _read(README)
        assert "JUDGE_USE_BEDROCK" not in text, (
            "examples README.md still contains JUDGE_USE_BEDROCK. Rename to DEVBENCH_USE_BEDROCK (AC-197-8)."
        )

    def test_devbench_workspace_root_present(self) -> None:
        """DEVBENCH_WORKSPACE_ROOT must appear in the README env-var table."""
        text = _read(README)
        assert "DEVBENCH_WORKSPACE_ROOT" in text, "examples README.md must use DEVBENCH_WORKSPACE_ROOT (AC-197-8)."

    def test_devbench_claude_model_present(self) -> None:
        """DEVBENCH_CLAUDE_MODEL must appear in the README env-var table."""
        text = _read(README)
        assert "DEVBENCH_CLAUDE_MODEL" in text, "examples README.md must use DEVBENCH_CLAUDE_MODEL (AC-197-8)."

    @pytest.mark.parametrize(
        "legacy_var",
        [
            "JUDGE_WORKSPACE_ROOT",
            "JUDGE_CLAUDE_MODEL",
            "JUDGE_USE_BEDROCK",
        ],
    )
    def test_no_legacy_judge_vars_parametrized(self, legacy_var: str) -> None:
        """No JUDGE_* operational env vars may appear in the example README."""
        text = _read(README)
        assert legacy_var not in text, (
            f"examples README.md still contains {legacy_var}. "
            f"Rename to DEVBENCH_{legacy_var.removeprefix('JUDGE_')} (AC-197-8)."
        )


# ---------------------------------------------------------------------------
# before/devbench-commands.txt
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExampleCommandsTxtNoLegacyJudgeVars:
    """The example devbench-commands.txt must use DEVBENCH_* for all operational env vars."""

    def test_no_judge_workspace_root(self) -> None:
        """JUDGE_WORKSPACE_ROOT must not appear; use DEVBENCH_WORKSPACE_ROOT."""
        text = _read(COMMANDS_TXT)
        assert "JUDGE_WORKSPACE_ROOT" not in text, (
            "examples devbench-commands.txt still contains JUDGE_WORKSPACE_ROOT. "
            "Rename to DEVBENCH_WORKSPACE_ROOT (AC-197-8)."
        )

    def test_no_judge_claude_model(self) -> None:
        """JUDGE_CLAUDE_MODEL must not appear; use DEVBENCH_CLAUDE_MODEL."""
        text = _read(COMMANDS_TXT)
        assert "JUDGE_CLAUDE_MODEL" not in text, (
            "examples devbench-commands.txt still contains JUDGE_CLAUDE_MODEL. "
            "Rename to DEVBENCH_CLAUDE_MODEL (AC-197-8)."
        )

    def test_devbench_workspace_root_present(self) -> None:
        """DEVBENCH_WORKSPACE_ROOT must appear in the launcher commands."""
        text = _read(COMMANDS_TXT)
        assert "DEVBENCH_WORKSPACE_ROOT" in text, (
            "examples devbench-commands.txt must use DEVBENCH_WORKSPACE_ROOT (AC-197-8)."
        )

    def test_devbench_claude_model_present(self) -> None:
        """DEVBENCH_CLAUDE_MODEL must appear in the launcher commands."""
        text = _read(COMMANDS_TXT)
        assert "DEVBENCH_CLAUDE_MODEL" in text, (
            "examples devbench-commands.txt must use DEVBENCH_CLAUDE_MODEL (AC-197-8)."
        )

    @pytest.mark.parametrize(
        "legacy_var",
        [
            "JUDGE_WORKSPACE_ROOT",
            "JUDGE_CLAUDE_MODEL",
        ],
    )
    def test_no_legacy_judge_vars_parametrized(self, legacy_var: str) -> None:
        """No JUDGE_* operational env vars may appear in the launcher template."""
        text = _read(COMMANDS_TXT)
        assert legacy_var not in text, (
            f"examples devbench-commands.txt still contains {legacy_var}. "
            f"Rename to DEVBENCH_{legacy_var.removeprefix('JUDGE_')} (AC-197-8)."
        )


# ---------------------------------------------------------------------------
# before/backlog/config/devbench.yaml
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExampleDevbenchYamlNoLegacyJudgeVars:
    """The example devbench.yaml must use DEVBENCH_* in comments and prose."""

    def test_no_judge_claude_model_comment(self) -> None:
        """JUDGE_CLAUDE_MODEL must not appear in yaml prose comments; use DEVBENCH_CLAUDE_MODEL."""
        text = _read(DEVBENCH_YAML)
        assert "JUDGE_CLAUDE_MODEL" not in text, (
            "examples devbench.yaml still contains JUDGE_CLAUDE_MODEL in a comment. "
            "Rename to DEVBENCH_CLAUDE_MODEL (AC-197-8)."
        )

    def test_devbench_claude_model_present(self) -> None:
        """DEVBENCH_CLAUDE_MODEL must appear in the devbench.yaml prose comments."""
        text = _read(DEVBENCH_YAML)
        assert "DEVBENCH_CLAUDE_MODEL" in text, (
            "examples devbench.yaml must reference DEVBENCH_CLAUDE_MODEL in prose comments (AC-197-8)."
        )
