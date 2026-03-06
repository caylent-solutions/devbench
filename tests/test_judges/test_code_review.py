"""Tests for judges.judges.code_review module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from judges.judges.base import Verdict
from judges.judges.code_review import CodeReviewJudge
from judges.testing import make_llm_fail_result, make_llm_pass_result


class TestCodeReviewJudgeInit:
    """Test CodeReviewJudge initialization."""

    def test_name_is_code_review(self) -> None:
        judge = CodeReviewJudge()
        assert judge.name == "code_review"


class TestEvaluate:
    """Test evaluate delegates to LLM."""

    def test_passes_when_llm_passes(self, tmp_path: Path, tmp_repo_dir: Path, mock_llm_pass: None) -> None:
        wu_file = tmp_path / "wu_pass.md"
        wu_file.write_text(
            "# E0-F1-S1-T1: Task\n\n"
            "## Status: in-queue\n\n"
            "## Acceptance Criteria\n\n"
            "- [ ] AC-FUNC-001: Implement the primary feature\n"
        )

        subprocess.run(
            ["git", "checkout", "-b", "feature/test"],
            cwd=tmp_repo_dir, capture_output=True, check=True,
        )
        src_file = tmp_repo_dir / "src" / "main.py"
        src_file.parent.mkdir(parents=True, exist_ok=True)
        src_file.write_text("def main(): return 42\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp_repo_dir, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "feat"],
            cwd=tmp_repo_dir, capture_output=True, check=True,
        )

        judge = CodeReviewJudge()
        result = judge.evaluate(work_unit_path=wu_file, repo_path=tmp_repo_dir)
        assert result.verdict is Verdict.PASS

    def test_fails_when_llm_fails(self, tmp_path: Path, tmp_repo_dir: Path) -> None:
        wu_file = tmp_path / "wu.md"
        wu_file.write_text("# Task\n\n## Acceptance Criteria\n\n- [ ] AC-1: feature\n")

        subprocess.run(
            ["git", "checkout", "-b", "feature/test2"],
            cwd=tmp_repo_dir, capture_output=True, check=True,
        )
        (tmp_repo_dir / "feature.py").write_text("def feature(): pass\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp_repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "feat"], cwd=tmp_repo_dir, capture_output=True, check=True)

        judge = CodeReviewJudge()
        with patch.object(judge, "_llm_evaluate", return_value=make_llm_fail_result("code_review")):
            result = judge.evaluate(work_unit_path=wu_file, repo_path=tmp_repo_dir)
        assert result.verdict is Verdict.FAIL

    def test_fails_when_no_diff(self, tmp_path: Path, tmp_repo_dir: Path) -> None:
        wu_file = tmp_path / "wu.md"
        wu_file.write_text("## Acceptance Criteria\n\n- [ ] AC-1: feature\n")

        judge = CodeReviewJudge()
        with patch.object(judge, "_get_diff", return_value=""):
            result = judge.evaluate(work_unit_path=wu_file, repo_path=tmp_repo_dir)
        assert result.verdict is Verdict.FAIL
        assert "No code changes" in result.reasoning

    def test_sends_diff_and_work_unit_to_llm(self, tmp_path: Path, tmp_repo_dir: Path) -> None:
        wu_file = tmp_path / "wu.md"
        wu_file.write_text("# Task\n\n## Acceptance Criteria\n\n- [ ] AC-1: feature\n")

        subprocess.run(
            ["git", "checkout", "-b", "feature/test3"],
            cwd=tmp_repo_dir, capture_output=True, check=True,
        )
        (tmp_repo_dir / "feature.py").write_text("def feature(): pass\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp_repo_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "feat"], cwd=tmp_repo_dir, capture_output=True, check=True)

        judge = CodeReviewJudge()
        with patch.object(judge, "_llm_evaluate", return_value=make_llm_pass_result("code_review")) as mock_llm:
            judge.evaluate(work_unit_path=wu_file, repo_path=tmp_repo_dir)

        evidence = mock_llm.call_args.kwargs["evidence_sections"]
        assert "Work Unit" in evidence
        assert "Git Diff" in evidence
