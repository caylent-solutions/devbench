"""Tests for judges.blocker_resolver module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from testing import make_llm_pass_result

from devbench.judges.base import Verdict
from devbench.judges.blocker_resolver import BlockerResolverJudge


class TestBlockerResolverInit:
    """Test initialization."""

    def test_name(self) -> None:
        judge = BlockerResolverJudge()
        assert judge.name == "blocker_resolver"


class TestEvaluate:
    """Test evaluate delegates to LLM."""

    def test_passes_when_no_blockers(self, tmp_path: Path) -> None:
        wu_file = tmp_path / "wu.md"
        wu_file.write_text("# Task\n\n## Status: In Queue\n\n## Comments\n")

        judge = BlockerResolverJudge()
        result = judge.evaluate(wu_file, tmp_path)
        assert result.verdict is Verdict.PASS
        assert "No blockers" in result.reasoning

    def test_delegates_to_llm_when_blockers_exist(self, tmp_path: Path) -> None:
        wu_file = tmp_path / "wu.md"
        wu_file.write_text(
            "## Blocked By\n\n- dependency: E0-F1-S1-T1 must complete\n\n## Comments\n"
        )

        judge = BlockerResolverJudge()
        with patch.object(judge, "_llm_evaluate", return_value=make_llm_pass_result("blocker_resolver")):
            result = judge.evaluate(wu_file, tmp_path)
        assert result.verdict is Verdict.PASS

    def test_sends_work_unit_to_llm(self, tmp_path: Path) -> None:
        wu_file = tmp_path / "wu.md"
        wu_file.write_text(
            "## Blocked By\n\n- technical: Need API key\n\n## Comments\n"
        )

        judge = BlockerResolverJudge()
        with patch.object(judge, "_llm_evaluate", return_value=make_llm_pass_result("blocker_resolver")) as mock_llm:
            judge.evaluate(wu_file, tmp_path)

        evidence = mock_llm.call_args.kwargs["evidence_sections"]
        assert "Work Unit" in evidence
