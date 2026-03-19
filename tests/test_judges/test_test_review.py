"""Tests for judges.test_review module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from testing import make_llm_pass_result

from devbench.config_loader import RepoConfig
from devbench.judges.base import Verdict
from devbench.judges.test_review import TestReviewJudge


def _make_repo_config(local_path: Path) -> RepoConfig:
    return RepoConfig(name="org/repo", short_name="repo", local_path=local_path)


class TestTestReviewInit:
    """Test initialization."""

    def test_name(self) -> None:
        judge = TestReviewJudge()
        assert judge.name == "test_review"


class TestEvaluate:
    """Test evaluate delegates to LLM with gathered evidence."""

    def test_passes_when_llm_passes(self, tmp_path: Path) -> None:
        wu_file = tmp_path / "wu.md"
        wu_file.write_text(
            "## Status: In Review\n\n## Comments\n"
            "<!-- [RED] test -->\n<!-- [GREEN] pass -->\n<!-- [REFACTOR] clean -->\n"
        )

        judge = TestReviewJudge()
        with patch.object(judge, "_run_command", return_value=(0, "1 passed", "")):
            with patch.object(judge, "_llm_evaluate", return_value=make_llm_pass_result("test_review")):
                result = judge.evaluate(wu_file, _make_repo_config(tmp_path))

        assert result.verdict is Verdict.PASS

    def test_sends_test_output_to_llm(self, tmp_path: Path) -> None:
        wu_file = tmp_path / "wu.md"
        wu_file.write_text("## Status: In Review\n\n## Comments\n")

        judge = TestReviewJudge()
        with patch.object(judge, "_run_command", return_value=(0, "3 passed", "")):
            with patch.object(judge, "_llm_evaluate", return_value=make_llm_pass_result("test_review")) as mock_llm:
                judge.evaluate(wu_file, _make_repo_config(tmp_path))

        evidence = mock_llm.call_args.kwargs["evidence_sections"]
        assert "Work Unit" in evidence


class TestRunTests:
    """Test _run_tests returns test output."""

    def test_uses_make_test_when_available(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text("test:\n\tpytest\n")
        judge = TestReviewJudge()
        with patch.object(judge, "_run_command") as mock_cmd:
            mock_cmd.return_value = (0, "5 passed\n", "")
            judge._run_tests(tmp_path)
        # First call is make -n test (dry-run check), second is make test
        assert mock_cmd.call_args_list[1].args[0] == ["make", "test"]

    def test_falls_back_to_pytest_without_makefile(self, tmp_path: Path) -> None:
        judge = TestReviewJudge()
        with patch.object(judge, "_get_default_branch", return_value="origin/main"):
            with patch.object(judge, "_run_command", return_value=(0, "5 passed\n", "")) as mock_cmd:
                judge._run_tests(tmp_path)
        last_call_cmd = mock_cmd.call_args_list[-1].args[0]
        assert last_call_cmd[0] == "pytest"

    def test_returns_output(self, tmp_path: Path) -> None:
        judge = TestReviewJudge()
        with patch.object(judge, "_has_make_test_target", return_value=False):
            with patch.object(judge, "_run_command", return_value=(0, "5 passed\n", "")):
                output = judge._run_tests(tmp_path)
        assert "5 passed" in output

    def test_returns_empty_when_no_output(self, tmp_path: Path) -> None:
        judge = TestReviewJudge()
        with patch.object(judge, "_has_make_test_target", return_value=False):
            with patch.object(judge, "_get_default_branch", return_value="origin/main"):
                with patch.object(judge, "_run_command", return_value=(0, "", "")):
                    output = judge._run_tests(tmp_path)
        assert output == ""


class TestHasMakeTestTarget:
    """Test _has_make_test_target detection."""

    def test_returns_true_when_makefile_has_test_target(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text("test:\n\tpytest\n")
        judge = TestReviewJudge()
        with patch.object(judge, "_run_command", return_value=(0, "", "")):
            assert judge._has_make_test_target(tmp_path) is True

    def test_returns_false_without_makefile(self, tmp_path: Path) -> None:
        judge = TestReviewJudge()
        assert judge._has_make_test_target(tmp_path) is False

    def test_returns_false_when_make_dry_run_fails(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text("lint:\n\truff check\n")
        judge = TestReviewJudge()
        with patch.object(judge, "_run_command", return_value=(2, "", "No rule to make target 'test'")):
            assert judge._has_make_test_target(tmp_path) is False


class TestRunTestsFullOutput:
    """Test _run_tests returns full output without tail truncation — AC-8."""

    def test_returns_full_output_not_tail(self, tmp_path: Path) -> None:
        """AC-8: full combined output returned, no tail slice applied."""
        judge = TestReviewJudge()
        long_output = "line\n" * 500  # well over any reasonable tail limit
        with patch.object(judge, "_has_make_test_target", return_value=False):
            with patch.object(judge, "_run_command", return_value=(0, long_output, "")):
                output = judge._run_tests(tmp_path)
        assert output == long_output.strip(), (
            "Expected full output, got only tail portion"
        )


class TestEvidenceIncludesDiff:
    """Test evaluate includes Git Diff evidence section — AC-9."""

    def test_includes_diff_when_non_empty(self, tmp_path: Path) -> None:
        """AC-9: non-empty diff is included as Git Diff evidence section."""
        wu_file = tmp_path / "wu.md"
        wu_file.write_text("## Description\n\nspec")

        judge = TestReviewJudge()
        with (
            patch.object(judge, "_run_command", return_value=(0, "1 passed", "")),
            patch.object(judge, "_get_diff", return_value="diff --git a/test.py"),
            patch.object(judge, "_llm_evaluate", return_value=make_llm_pass_result("test_review")) as mock_llm,
        ):
            judge.evaluate(wu_file, _make_repo_config(tmp_path))

        evidence = mock_llm.call_args.kwargs["evidence_sections"]
        assert "Git Diff" in evidence, "Expected Git Diff in evidence sections"
        assert "diff --git" in evidence["Git Diff"]

    def test_omits_diff_when_empty(self, tmp_path: Path) -> None:
        """AC-9: empty diff is not included as an evidence section."""
        wu_file = tmp_path / "wu.md"
        wu_file.write_text("## Description\n\nspec")

        judge = TestReviewJudge()
        with (
            patch.object(judge, "_run_command", return_value=(0, "1 passed", "")),
            patch.object(judge, "_get_diff", return_value="   "),
            patch.object(judge, "_llm_evaluate", return_value=make_llm_pass_result("test_review")) as mock_llm,
        ):
            judge.evaluate(wu_file, _make_repo_config(tmp_path))

        evidence = mock_llm.call_args.kwargs["evidence_sections"]
        assert "Git Diff" not in evidence, "Empty diff should not appear in evidence"

    def test_work_unit_has_agent_log_stripped(self, tmp_path: Path) -> None:
        """AC-4: work unit content passed to LLM has ## Comments section removed."""
        wu_file = tmp_path / "wu.md"
        wu_file.write_text("## Description\n\nspec\n## Comments\n[REVIEW_FAIL] noise")

        judge = TestReviewJudge()
        with (
            patch.object(judge, "_run_command", return_value=(0, "1 passed", "")),
            patch.object(judge, "_get_diff", return_value=""),
            patch.object(judge, "_llm_evaluate", return_value=make_llm_pass_result("test_review")) as mock_llm,
        ):
            judge.evaluate(wu_file, _make_repo_config(tmp_path))

        evidence = mock_llm.call_args.kwargs["evidence_sections"]
        assert "[REVIEW_FAIL]" not in evidence["Work Unit"], (
            "Agent log should be stripped from Work Unit evidence"
        )


class TestCollectTestFiles:
    """Test _collect_test_files gathers test content for LLM."""

    def test_collects_test_files(self, tmp_path: Path) -> None:
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_sample.py").write_text("def test_sample():\n    assert 1 + 1 == 2\n")

        judge = TestReviewJudge()
        with patch.object(judge, "_get_default_branch", return_value="origin/main"):
            content = judge._collect_test_files(tmp_path)
        assert "test_sample" in content

    def test_returns_empty_when_no_tests(self, tmp_path: Path) -> None:
        judge = TestReviewJudge()
        with patch.object(judge, "_get_default_branch", return_value="origin/main"):
            content = judge._collect_test_files(tmp_path)
        assert content == ""

    def test_adds_truncation_marker_for_large_files(self, tmp_path: Path) -> None:
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        large_content = "def test_big():\n" + "    x = 1\n" * 5000
        (tests_dir / "test_large.py").write_text(large_content)

        judge = TestReviewJudge()
        with patch.object(judge, "_get_default_branch", return_value="origin/main"):
            content = judge._collect_test_files(tmp_path)
        assert "TRUNCATED" in content
        assert "complete on disk" in content
