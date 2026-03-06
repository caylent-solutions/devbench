"""Tests for judges.judges.doc_review module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from judges.judges.base import Verdict
from judges.judges.doc_review import DocReviewJudge
from judges.testing import make_llm_pass_result


class TestDocReviewInit:
    """Test initialization."""

    def test_name(self) -> None:
        judge = DocReviewJudge()
        assert judge.name == "doc_review"


class TestEvaluate:
    """Test evaluate delegates to LLM."""

    def test_passes_when_llm_passes(self, tmp_path: Path) -> None:
        judge = DocReviewJudge()
        wu_file = tmp_path / "wu.md"
        wu_file.write_text("# Task\n\n## Acceptance Criteria\n\n- [ ] AC-FUNC-001 feature\n")

        with patch.object(judge, "_get_default_branch", return_value="main"):
            with patch.object(judge, "_llm_evaluate", return_value=make_llm_pass_result("doc_review")):
                result = judge.evaluate(wu_file, tmp_path)

        assert result.verdict is Verdict.PASS

    def test_sends_work_unit_and_diff_to_llm(self, tmp_path: Path) -> None:
        judge = DocReviewJudge()
        wu_file = tmp_path / "wu.md"
        wu_file.write_text("# Task\n\n## Acceptance Criteria\n")

        with patch.object(judge, "_llm_evaluate", return_value=make_llm_pass_result("doc_review")) as mock_llm:
            with patch.object(judge, "_get_diff", return_value="diff content"):
                judge.evaluate(wu_file, tmp_path)

        evidence = mock_llm.call_args.kwargs["evidence_sections"]
        assert "Work Unit" in evidence
        assert "Git Diff" in evidence


class TestCollectDocFiles:
    """Test _collect_doc_files gathers documentation for LLM context."""

    def test_collects_markdown_files(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# Project\n")
        (tmp_path / "CHANGELOG.md").write_text("# Changes\n")

        judge = DocReviewJudge()
        content = judge._collect_doc_files(tmp_path)
        assert "README.md" in content
        assert "CHANGELOG.md" in content

    def test_returns_empty_when_no_docs(self, tmp_path: Path) -> None:
        judge = DocReviewJudge()
        content = judge._collect_doc_files(tmp_path)
        assert content == ""


class TestGetDiff:
    """Test _get_diff method."""

    def test_includes_staged_diff(self, tmp_path: Path) -> None:
        judge = DocReviewJudge()

        def side_effect(cmd, cwd):
            if "--cached" in cmd:
                return (0, "staged changes", "")
            return (0, "", "")

        with patch.object(judge, "_get_default_branch", return_value="main"):
            with patch.object(judge, "_run_command", side_effect=side_effect):
                diff = judge._get_diff(tmp_path)
        assert "staged changes" in diff

    def test_returns_empty_when_all_fail(self, tmp_path: Path) -> None:
        judge = DocReviewJudge()
        with patch.object(judge, "_get_default_branch", return_value="main"):
            with patch.object(judge, "_run_command", return_value=(1, "", "error")):
                diff = judge._get_diff(tmp_path)
        assert diff == ""
