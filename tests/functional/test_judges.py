"""Functional tests for judge evaluate() methods.

These tests exercise judges end-to-end with realistic file structures
and git repositories, using mocked LLM calls and external services.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from testing import make_llm_pass_result

from devbench.github.git_ops import GitOpsJudge
from devbench.judges.base import JudgeResult, Verdict
from devbench.judges.blocker_resolver import BlockerResolverJudge
from devbench.judges.changes_manifest import ChangesManifestJudge
from devbench.judges.code_review import CodeReviewJudge
from devbench.judges.doc_review import DocReviewJudge
from devbench.judges.security_review import SecurityReviewJudge
from devbench.judges.test_review import TestReviewJudge


def _init_repo(path: Path) -> None:
    """Initialize a git repo with an initial commit and origin/HEAD."""
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "branch", "-M", "main"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path, capture_output=True, check=True,
    )
    (path / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "-A"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, capture_output=True, check=True)
    # Set up origin/HEAD so _get_default_branch() works
    subprocess.run(
        ["git", "remote", "add", "origin", path.as_posix()],
        cwd=path, capture_output=True, check=True,
    )
    subprocess.run(["git", "fetch", "origin"], cwd=path, capture_output=True, check=True)
    head_ref = path / ".git" / "refs" / "remotes" / "origin" / "HEAD"
    head_ref.write_text("ref: refs/remotes/origin/main\n")


def _make_wu_file(tmp_path: Path, unit_id: str = "E0-F1-S1-T1") -> Path:
    """Create a work unit .md file with full structure."""
    wu = tmp_path / f"{unit_id}.md"
    wu.write_text(
        f"# {unit_id}: Test Task\n\n"
        "## Status: in-review\n\n"
        "## Description\n\nImplement the feature.\n\n"
        "## Target Repository\n\n"
        "- **Repo:** `caylent-solutions/git-repo`\n\n"
        "## Acceptance Criteria\n\n"
        "- [ ] AC-FUNC-001 Implement feature\n"
        "- [ ] AC-TEST-001 All tests pass\n"
        "- [ ] AC-DOC-001 Update README.md\n\n"
        "## Changes Manifest\n\n"
        "- `src/main.py`\n"
        "- `tests/test_main.py`\n"
        "- `README.md`\n\n"
        "## Comments\n"
    )
    return wu


class TestCodeReviewFunctional:
    """Functional tests for CodeReviewJudge."""

    def test_evaluate_with_clean_diff(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)

        subprocess.run(["git", "checkout", "-b", "feature/test"], cwd=repo, capture_output=True, check=True)
        (repo / "src").mkdir()
        (repo / "src" / "main.py").write_text("def hello():\n    return 'world'\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "Add feature"],
            cwd=repo,
            capture_output=True,
            check=True,
        )

        wu = _make_wu_file(tmp_path)
        judge = CodeReviewJudge()

        with patch.object(judge, "_llm_evaluate", return_value=make_llm_pass_result("code_review")):
            result = judge.evaluate(wu, repo)

        assert isinstance(result, JudgeResult)
        assert result.judge_name == "code_review"
        assert result.verdict is Verdict.PASS

    def test_evaluate_fails_when_llm_rejects(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)

        (repo / "config.py").write_text('API_KEY = "sk-hardcoded-secret-123"\n')
        subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "Add config"],
            cwd=repo,
            capture_output=True,
            check=True,
        )

        wu = _make_wu_file(tmp_path)
        judge = CodeReviewJudge()

        fail_result = JudgeResult(
            judge_name="code_review",
            verdict=Verdict.FAIL,
            reasoning="Hardcoded secret found",
            feedback="Remove API_KEY from code",
            evidence=["config.py contains hardcoded secret"],
        )
        with patch.object(judge, "_llm_evaluate", return_value=fail_result):
            result = judge.evaluate(wu, repo)

        assert result.verdict is Verdict.FAIL


class TestTestReviewFunctional:
    """Functional tests for TestReviewJudge."""

    def test_evaluate_passes_with_tests(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)

        (repo / "tests").mkdir()
        (repo / "tests" / "test_main.py").write_text("def test_hello():\n    assert 1 + 1 == 2\n")

        wu = _make_wu_file(tmp_path)
        judge = TestReviewJudge()

        with patch.object(judge, "_llm_evaluate", return_value=make_llm_pass_result("test_review")):
            with patch.object(judge, "_run_command", return_value=(0, "1 passed, 0 failed\n", "")):
                result = judge.evaluate(wu, repo)

        assert isinstance(result, JudgeResult)
        assert result.verdict is Verdict.PASS


class TestDocReviewFunctional:
    """Functional tests for DocReviewJudge."""

    def test_evaluate_passes_with_docs_updated(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)

        (repo / "README.md").write_text("# Updated Docs\n\nNew feature info.\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "Update docs"],
            cwd=repo,
            capture_output=True,
            check=True,
        )

        wu = _make_wu_file(tmp_path)
        judge = DocReviewJudge()

        with patch.object(judge, "_llm_evaluate", return_value=make_llm_pass_result("doc_review")):
            result = judge.evaluate(wu, repo)

        assert isinstance(result, JudgeResult)
        assert result.verdict is Verdict.PASS


class TestChangesManifestFunctional:
    """Functional tests for ChangesManifestJudge."""

    def test_evaluate_with_matching_manifest(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)

        (repo / "src").mkdir()
        (repo / "src" / "main.py").write_text("print('hello')\n")
        (repo / "README.md").write_text("# Updated\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "Changes"],
            cwd=repo,
            capture_output=True,
            check=True,
        )

        wu = _make_wu_file(tmp_path)
        judge = ChangesManifestJudge()

        with patch.object(judge, "_llm_evaluate", return_value=make_llm_pass_result("changes_manifest")):
            result = judge.evaluate(wu, repo)

        assert isinstance(result, JudgeResult)
        assert result.verdict is Verdict.PASS


class TestSecurityReviewFunctional:
    """Functional tests for SecurityReviewJudge."""

    def test_evaluate_passes_with_no_alerts(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)

        wu = _make_wu_file(tmp_path)
        judge = SecurityReviewJudge()

        with patch.object(judge, "_fetch_alerts", return_value=[]):
            with patch("devbench.judges.security_review.get_gh_token", return_value="tok"):
                with patch.object(judge, "_llm_evaluate", return_value=make_llm_pass_result("security_review")):
                    with patch.object(judge, "_get_diff", return_value=""):
                        result = judge.evaluate(wu, repo, repo="caylent-solutions/git-repo")

        assert result.verdict is Verdict.PASS

    def test_evaluate_fails_when_llm_rejects(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)

        wu = _make_wu_file(tmp_path)
        judge = SecurityReviewJudge()

        fail_result = JudgeResult(
            judge_name="security_review",
            verdict=Verdict.FAIL,
            reasoning="Security vulnerability found",
            feedback="Fix SQL injection",
            evidence=["sql-injection finding"],
        )

        mock_alerts = [{"rule": {"id": "sql", "description": "SQL injection"}}]
        with patch.object(judge, "_fetch_alerts", return_value=mock_alerts):
            with patch("devbench.judges.security_review.get_gh_token", return_value="tok"):
                with patch.object(judge, "_llm_evaluate", return_value=fail_result):
                    with patch.object(judge, "_get_diff", return_value=""):
                        result = judge.evaluate(wu, repo, repo="caylent-solutions/git-repo")

        assert result.verdict is Verdict.FAIL


class TestBlockerResolverFunctional:
    """Functional tests for BlockerResolverJudge."""

    def test_evaluate_passes_when_no_blockers(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)

        wu = _make_wu_file(tmp_path)
        judge = BlockerResolverJudge()
        result = judge.evaluate(wu, repo)

        assert result.verdict is Verdict.PASS
        assert "No blockers" in result.reasoning

    def test_evaluate_with_blocker_delegates_to_llm(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)

        wu = tmp_path / "blocker.md"
        wu.write_text(
            "# E0-F1-S1-T1: Task\n\n"
            "## Status: blocked\n\n"
            "## Blocked By\n\n"
            "- technical: Missing API credentials\n\n"
            "## Comments\n"
        )

        judge = BlockerResolverJudge()

        with patch.object(judge, "_llm_evaluate", return_value=make_llm_pass_result("blocker_resolver")):
            result = judge.evaluate(wu, repo)

        assert isinstance(result, JudgeResult)
        assert result.verdict is Verdict.PASS


class TestGitOpsFunctional:
    """Functional tests for GitOpsJudge."""

    def test_evaluate_returns_pass_noop(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        result = judge.evaluate(tmp_path / "wu.md", tmp_path)
        assert result.verdict is Verdict.PASS

    def test_commit_and_push_runs_git(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)

        (repo / "newfile.txt").write_text("content\n")

        judge = GitOpsJudge()

        with patch.object(judge, "_run_command") as mock_cmd:
            mock_cmd.return_value = (0, "", "")
            judge.commit_and_push(
                "caylent-solutions/git-repo",
                repo,
                "feature/test",
                "test commit",
            )

        assert mock_cmd.call_count == 4
        calls = [c.args[0] for c in mock_cmd.call_args_list]
        assert calls[0] == ["git", "checkout", "-B", "feature/test"]
        assert calls[1] == ["git", "add", "-A"]
        assert calls[2] == ["git", "commit", "-m", "test commit"]
        assert calls[3] == ["git", "push", "origin", "feature/test"]

    def test_create_pr_returns_url(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()

        with patch.object(judge, "_gh", return_value=(0, "https://github.com/org/repo/pull/99\n", "")) as mock_gh:
            url = judge.create_pr(
                "caylent-solutions/git-repo",
                "feature/test",
                "PR Title",
                "PR body",
                repo_path=tmp_path,
            )

        assert "pull/99" in url
        _, kwargs = mock_gh.call_args
        assert kwargs.get("repo") == "caylent-solutions/git-repo"

    def test_merge_pr_succeeds(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        with patch.object(judge, "_gh", return_value=(0, "", "")) as mock_gh:
            judge.merge_pr("caylent-solutions/git-repo", 99, repo_path=tmp_path)

        _, kwargs = mock_gh.call_args
        assert kwargs.get("repo") == "caylent-solutions/git-repo"

    def test_wait_for_checks_returns_bool(self, tmp_path: Path) -> None:
        judge = GitOpsJudge()
        with patch.object(judge, "_gh", return_value=(0, "All checks passed", "")):
            assert judge.wait_for_checks("caylent-solutions/git-repo", 99, repo_path=tmp_path) is True

        with patch.object(judge, "_gh", return_value=(1, "", "failed")):
            assert judge.wait_for_checks("caylent-solutions/git-repo", 99, repo_path=tmp_path) is False

    def test_create_tag(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_repo(repo)

        judge = GitOpsJudge()
        with patch.object(judge, "_run_command", return_value=(0, "", "")) as mock_cmd:
            judge.create_tag(
                "caylent-solutions/git-repo",
                repo,
                "v1.0.0",
                "Release 1.0",
            )
        assert mock_cmd.call_count == 2
