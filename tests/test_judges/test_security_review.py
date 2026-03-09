"""Tests for judges.security_review module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from testing import make_llm_pass_result

from devbench.judges.base import Verdict
from devbench.judges.security_review import SecurityReviewJudge


class TestSecurityReviewInit:
    """Test initialization."""

    def test_name(self) -> None:
        judge = SecurityReviewJudge()
        assert judge.name == "security_review"


class TestEvaluate:
    """Test evaluate delegates to LLM with security evidence."""

    def test_requires_repo_kwarg(self, tmp_path: Path) -> None:
        judge = SecurityReviewJudge()
        wu_file = tmp_path / "wu.md"
        wu_file.write_text("# Task\n")
        with pytest.raises(ValueError, match="requires 'repo'"):
            judge.evaluate(wu_file, tmp_path)

    def test_validates_repo_allowlist(self, tmp_path: Path) -> None:
        judge = SecurityReviewJudge()
        wu_file = tmp_path / "wu.md"
        wu_file.write_text("# Task\n")
        with pytest.raises(ValueError, match="not allowed"):
            judge.evaluate(wu_file, tmp_path, repo="evil-org/evil-repo")

    def test_passes_when_llm_passes(self, tmp_path: Path) -> None:
        wu_file = tmp_path / "wu.md"
        wu_file.write_text("# Security task\n\n## Status: In Queue\n")

        judge = SecurityReviewJudge()
        with patch.object(judge, "_fetch_alerts", return_value=[]):
            with patch("devbench.judges.security_review.get_gh_token", return_value="test-token"):
                with patch.object(judge, "_llm_evaluate", return_value=make_llm_pass_result("security_review")):
                    with patch.object(judge, "_get_diff", return_value=""):
                        result = judge.evaluate(wu_file, tmp_path, repo="caylent-solutions/git-repo")

        assert result.verdict is Verdict.PASS

    def test_sends_alert_evidence_to_llm(self, tmp_path: Path) -> None:
        wu_file = tmp_path / "wu.md"
        wu_file.write_text("# Task\n")

        judge = SecurityReviewJudge()
        llm_pass = make_llm_pass_result("security_review")
        with patch.object(judge, "_fetch_alerts", return_value=[]):
            with patch("devbench.judges.security_review.get_gh_token", return_value="tok"):
                with patch.object(judge, "_llm_evaluate", return_value=llm_pass) as mock_llm:
                    with patch.object(judge, "_get_diff", return_value=""):
                        judge.evaluate(wu_file, tmp_path, repo="caylent-solutions/git-repo")

        evidence = mock_llm.call_args.kwargs["evidence_sections"]
        assert "Work Unit" in evidence
        assert "GitHub Security Alerts" in evidence


class TestSummarizeAlerts:
    """Test _summarize_alerts formats alert details."""

    def test_summarizes_code_scanning(self) -> None:
        judge = SecurityReviewJudge()
        alerts: list[dict[str, object]] = [{"rule": {"id": "xss-001", "description": "XSS vulnerability"}}]
        summary = judge._summarize_alerts(alerts, "code-scanning")
        assert "xss-001" in summary

    def test_summarizes_dependabot(self) -> None:
        judge = SecurityReviewJudge()
        alerts = [
            {
                "dependency": {"package": {"name": "lodash"}},
                "security_advisory": {"severity": "high"},
            }
        ]
        summary = judge._summarize_alerts(alerts, "dependabot")
        assert "lodash" in summary
        assert "high" in summary

    def test_truncates_at_limit(self) -> None:
        judge = SecurityReviewJudge()
        alerts: list[dict[str, object]] = [{"secret_type_display_name": f"Secret-{i}"} for i in range(15)]
        summary = judge._summarize_alerts(alerts, "secret-scanning")
        assert "and 5 more" in summary


class TestFetchAlerts:
    """Test _fetch_alerts method."""

    def test_returns_list_on_success(self, tmp_path: Path) -> None:
        judge = SecurityReviewJudge()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps([{"id": 1}, {"id": 2}])

        with patch("devbench.judges.security_review.subprocess.run", return_value=mock_result):
            alerts = judge._fetch_alerts("repos/org/repo/alerts", "token", tmp_path)

        assert len(alerts) == 2

    def test_returns_empty_on_failure(self, tmp_path: Path) -> None:
        judge = SecurityReviewJudge()
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "error"

        with patch("devbench.judges.security_review.subprocess.run", return_value=mock_result):
            alerts = judge._fetch_alerts("repos/org/repo/alerts", "token", tmp_path)
        assert alerts == []

    def test_returns_empty_on_bad_json(self, tmp_path: Path) -> None:
        judge = SecurityReviewJudge()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not json"

        with patch("devbench.judges.security_review.subprocess.run", return_value=mock_result):
            alerts = judge._fetch_alerts("repos/org/repo/alerts", "token", tmp_path)
        assert alerts == []

    def test_returns_empty_on_non_list_json(self, tmp_path: Path) -> None:
        judge = SecurityReviewJudge()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"message": "not found"}'

        with patch("devbench.judges.security_review.subprocess.run", return_value=mock_result):
            alerts = judge._fetch_alerts("repos/org/repo/alerts", "token", tmp_path)
        assert alerts == []

    def test_returns_empty_when_no_token(self, tmp_path: Path) -> None:
        judge = SecurityReviewJudge()
        alerts = judge._fetch_alerts("repos/org/repo/alerts", "", tmp_path)
        assert alerts == []

    def test_returns_empty_when_gh_missing(self, tmp_path: Path) -> None:
        judge = SecurityReviewJudge()
        with patch("devbench.judges.security_review.subprocess.run", side_effect=FileNotFoundError):
            alerts = judge._fetch_alerts("repos/org/repo/alerts", "token", tmp_path)
        assert alerts == []


class TestGetDiff:
    """Test _get_diff method."""

    def test_includes_staged_diff(self, tmp_path: Path) -> None:
        judge = SecurityReviewJudge()

        def side_effect(cmd, cwd):
            if "--cached" in cmd:
                return (0, "staged changes", "")
            return (0, "", "")

        with patch.object(judge, "_get_default_branch", return_value="main"):
            with patch.object(judge, "_run_command", side_effect=side_effect):
                diff = judge._get_diff(tmp_path)
        assert "staged changes" in diff

    def test_includes_unstaged_diff(self, tmp_path: Path) -> None:
        judge = SecurityReviewJudge()

        def side_effect(cmd, cwd):
            if cmd == ["git", "diff"]:
                return (0, "unstaged changes", "")
            return (0, "", "")

        with patch.object(judge, "_get_default_branch", return_value="main"):
            with patch.object(judge, "_run_command", side_effect=side_effect):
                diff = judge._get_diff(tmp_path)
        assert "unstaged changes" in diff

    def test_returns_empty_when_all_fail(self, tmp_path: Path) -> None:
        judge = SecurityReviewJudge()
        with patch.object(judge, "_get_default_branch", return_value="main"):
            with patch.object(judge, "_run_command", return_value=(1, "", "error")):
                diff = judge._get_diff(tmp_path)
        assert diff == ""
