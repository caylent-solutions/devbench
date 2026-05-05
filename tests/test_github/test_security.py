"""Tests for judges.github_security module."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from devbench.config import ALLOWED_REPOS
from devbench.github.security import SecurityFinding, SecurityReport


class TestSecurityFinding:
    """Test SecurityFinding dataclass."""

    def test_creation(self) -> None:
        finding = SecurityFinding(
            source="codeql",
            rule_id="py/sql-injection",
            severity="high",
            description="SQL injection vulnerability",
            url="https://github.com/org/repo/security/code-scanning/1",
        )
        assert finding.source == "codeql"
        assert finding.rule_id == "py/sql-injection"
        assert finding.severity == "high"
        assert finding.description == "SQL injection vulnerability"
        assert "code-scanning" in finding.url

    def test_different_sources(self) -> None:
        for source in ("codeql", "dependabot", "secret-scanning"):
            finding = SecurityFinding(
                source=source,
                rule_id="rule-1",
                severity="medium",
                description="desc",
                url="https://example.com",
            )
            assert finding.source == source


class TestSecurityReport:
    """Test SecurityReport dataclass and is_clean property."""

    def test_is_clean_with_no_findings(self) -> None:
        report = SecurityReport(repo="org/repo")
        assert report.is_clean is True
        assert report.findings == []

    def test_is_clean_false_with_findings(self) -> None:
        finding = SecurityFinding(
            source="codeql",
            rule_id="rule-1",
            severity="high",
            description="vuln",
            url="https://example.com",
        )
        report = SecurityReport(repo="org/repo", findings=[finding])
        assert report.is_clean is False
        assert len(report.findings) == 1

    def test_default_feature_flags(self) -> None:
        report = SecurityReport(repo="org/repo")
        assert report.codeql_enabled is False
        assert report.dependabot_enabled is False
        assert report.secret_scanning_enabled is False

    def test_feature_flags_set(self) -> None:
        report = SecurityReport(
            repo="org/repo",
            codeql_enabled=True,
            dependabot_enabled=True,
            secret_scanning_enabled=True,
        )
        assert report.codeql_enabled is True
        assert report.dependabot_enabled is True
        assert report.secret_scanning_enabled is True

    def test_is_clean_after_adding_finding(self) -> None:
        report = SecurityReport(repo="org/repo")
        assert report.is_clean is True

        report.findings.append(
            SecurityFinding(
                source="secret-scanning",
                rule_id="github_token",
                severity="critical",
                description="Exposed token",
                url="https://example.com",
            )
        )
        assert report.is_clean is False


class TestSetupAllRepos:
    """Test setup_all_repos only touches allowed repos."""

    def test_setup_all_repos_calls_enable_for_each_allowed(self) -> None:
        from devbench.github.security import setup_all_repos

        with patch("devbench.github.security.enable_security_features") as mock_enable:
            mock_enable.return_value = {
                "dependabot_alerts": True,
                "automated_fixes": True,
                "codeql": True,
                "secret_scanning": True,
            }
            results = setup_all_repos()

        # Verify all calls used allowed repos
        assert mock_enable.call_count == len(ALLOWED_REPOS)

        called_repos = {call.args[0] for call in mock_enable.call_args_list}
        assert called_repos == ALLOWED_REPOS

        # Every repo should have results
        assert len(results) == len(ALLOWED_REPOS)
        for repo, features in results.items():
            assert repo in ALLOWED_REPOS
            assert features["codeql"] is True

    def test_setup_all_repos_does_not_call_disallowed(self) -> None:
        from devbench.github.security import setup_all_repos

        with patch("devbench.github.security.enable_security_features") as mock_enable:
            mock_enable.return_value = {}
            setup_all_repos()

        called_repos = {call.args[0] for call in mock_enable.call_args_list}
        assert "some-other-org/evil-repo" not in called_repos


class TestEnableSecurityFeatures:
    """Test enable_security_features validates repo."""

    def test_raises_for_disallowed_repo(self) -> None:
        from devbench.github.security import enable_security_features

        with pytest.raises(ValueError, match="not allowed"):
            enable_security_features("evil-org/bad-repo")

    def test_calls_gh_api_for_allowed_repo(self) -> None:
        from devbench.github.security import enable_security_features

        with patch("devbench.github.security._gh_api") as mock_api:
            mock_api.return_value = (0, "")
            results = enable_security_features("caylent-solutions/git-repo")

        # Should have made multiple API calls for different features
        assert mock_api.call_count >= 3
        assert isinstance(results, dict)

    def test_handles_codeql_failure(self) -> None:
        from devbench.github.security import enable_security_features

        def api_side_effect(endpoint, method="GET", **kwargs):
            if "code-scanning" in endpoint:
                return (1, "error")
            return (0, "")

        with patch("devbench.github.security._gh_api", side_effect=api_side_effect):
            results = enable_security_features("caylent-solutions/git-repo")

        assert results["codeql"] is False
        assert results["dependabot_alerts"] is True


class TestRequireField:
    """Test _require_field helper function."""

    def test_raises_when_intermediate_key_is_not_dict(self) -> None:
        """Line 23: raises RuntimeError when intermediate value is not a dict."""
        from devbench.github.security import _require_field

        data = {"rule": "not_a_dict"}
        with pytest.raises(RuntimeError, match="Expected dict"):
            _require_field(data, "rule", "id")

    def test_raises_when_key_is_missing(self) -> None:
        """Line 26: raises RuntimeError when required key is missing."""
        from devbench.github.security import _require_field

        data = {"rule": {"severity": "high"}}
        with pytest.raises(RuntimeError, match="Required field"):
            _require_field(data, "rule", "id")

    def test_returns_value_when_present(self) -> None:
        """Verify happy path returns the value as string."""
        from devbench.github.security import _require_field

        data = {"rule": {"id": "py/sql-injection"}}
        result = _require_field(data, "rule", "id")
        assert result == "py/sql-injection"


class TestGhApi:
    """Test _gh_api helper function."""

    def test_calls_subprocess_with_token(self) -> None:
        from unittest.mock import MagicMock

        from devbench.github.security import _gh_api

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"ok": true}'

        with patch("devbench.github.security.get_gh_token", return_value="test-tok"):
            with patch("devbench.github.security.subprocess.run", return_value=mock_result) as mock_run:
                rc, output = _gh_api("repos/org/repo", "GET")

        assert rc == 0
        assert output == '{"ok": true}'
        call_args = mock_run.call_args
        assert "gh" in call_args[0][0][0]

    def test_passes_kwargs_as_fields(self) -> None:
        from unittest.mock import MagicMock

        from devbench.github.security import _gh_api

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""

        with patch("devbench.github.security.get_gh_token", return_value="tok"):
            with patch("devbench.github.security.subprocess.run", return_value=mock_result) as mock_run:
                _gh_api("endpoint", "PATCH", state="configured")

        cmd = mock_run.call_args[0][0]
        assert "-f" in cmd
        assert "state=configured" in cmd


class TestGetSecurityReport:
    """Test get_security_report validates repo and returns a report."""

    def test_raises_for_disallowed_repo(self) -> None:
        from devbench.github.security import get_security_report

        with pytest.raises(ValueError, match="not allowed"):
            get_security_report("evil-org/bad-repo")

    def test_returns_clean_report_on_no_alerts(self) -> None:
        from devbench.github.security import get_security_report

        with patch("devbench.github.security._gh_api") as mock_api:
            mock_api.return_value = (0, "[]")
            report = get_security_report("caylent-solutions/git-repo")

        assert isinstance(report, SecurityReport)
        assert report.is_clean is True
        assert report.repo == "caylent-solutions/git-repo"

    def test_returns_findings_when_alerts_present(self) -> None:
        import json

        from devbench.github.security import get_security_report

        codeql_alerts = json.dumps(
            [
                {
                    "rule": {"id": "py/sql-injection", "severity": "high", "description": "SQL injection"},
                    "html_url": "https://github.com/org/repo/security/1",
                }
            ]
        )
        dependabot_alerts = json.dumps([])
        secret_alerts = json.dumps([])

        responses = iter(
            [
                (0, codeql_alerts),
                (0, dependabot_alerts),
                (0, secret_alerts),
            ]
        )

        with patch("devbench.github.security._gh_api", side_effect=lambda *a, **kw: next(responses)):
            report = get_security_report("caylent-solutions/git-repo")

        assert report.is_clean is False
        assert len(report.findings) == 1
        assert report.findings[0].source == "codeql"

    def test_handles_api_failure_gracefully(self) -> None:
        from devbench.github.security import get_security_report

        with patch("devbench.github.security._gh_api", return_value=(1, "")):
            report = get_security_report("caylent-solutions/git-repo")

        assert report.is_clean is True
        assert report.codeql_enabled is False

    def test_raises_on_json_decode_error(self) -> None:
        from devbench.github.security import get_security_report

        with patch("devbench.github.security._gh_api", return_value=(0, "not-json")):
            with pytest.raises(RuntimeError, match="Failed to parse"):
                get_security_report("caylent-solutions/git-repo")

    def test_handles_dependabot_alerts(self) -> None:
        import json

        from devbench.github.security import get_security_report

        dependabot_data = json.dumps(
            [
                {
                    "security_advisory": {"ghsa_id": "GHSA-1234", "severity": "high", "summary": "vuln"},
                    "html_url": "https://example.com/1",
                }
            ]
        )

        def api_side_effect(endpoint, method="GET", **kwargs):
            if "dependabot" in endpoint:
                return (0, dependabot_data)
            if "secret-scanning" in endpoint:
                return (0, "[]")
            return (1, "")  # codeql fails

        with patch("devbench.github.security._gh_api", side_effect=api_side_effect):
            report = get_security_report("caylent-solutions/git-repo")

        assert report.dependabot_enabled is True
        assert len(report.findings) == 1
        assert report.findings[0].source == "dependabot"

    def test_handles_secret_scanning_alerts(self) -> None:
        import json

        from devbench.github.security import get_security_report

        secret_data = json.dumps(
            [
                {
                    "secret_type": "github_token",
                    "html_url": "https://example.com/2",
                }
            ]
        )

        def api_side_effect(endpoint, method="GET", **kwargs):
            if "secret-scanning" in endpoint:
                return (0, secret_data)
            return (0, "[]")

        with patch("devbench.github.security._gh_api", side_effect=api_side_effect):
            report = get_security_report("caylent-solutions/git-repo")

        assert report.secret_scanning_enabled is True
        assert any(f.source == "secret-scanning" for f in report.findings)


class TestValidateGhKwargs:
    """TD-4: ``_validate_gh_kwargs`` rejects suspicious keys/values before subprocess invocation."""

    def test_valid_identifier_key_accepted(self) -> None:
        from devbench.github.security import _validate_gh_kwargs

        _validate_gh_kwargs({"state": "configured"})

    def test_bracketed_key_accepted(self) -> None:
        # The Secret Scanning PATCH calls use security_and_analysis[...] syntax.
        from devbench.github.security import _validate_gh_kwargs

        _validate_gh_kwargs({"security_and_analysis[secret_scanning][status]": "enabled"})

    def test_invalid_key_rejected(self) -> None:
        from devbench.github.security import _validate_gh_kwargs

        with pytest.raises(ValueError, match="not a valid identifier"):
            _validate_gh_kwargs({"bad key": "value"})

    def test_non_str_value_rejected(self) -> None:
        from typing import Any, cast

        from devbench.github.security import _validate_gh_kwargs

        # The runtime contract is "values must be str"; the cast is a
        # test-only escape hatch that proves the runtime check fires
        # without leaving a `# type: ignore` annotation in the source.
        bad_input = cast(dict[str, str], {"key": cast(Any, 42)})
        with pytest.raises(ValueError, match="must be str"):
            _validate_gh_kwargs(bad_input)

    def test_newline_in_value_rejected(self) -> None:
        from devbench.github.security import _validate_gh_kwargs

        with pytest.raises(ValueError, match="forbidden token"):
            _validate_gh_kwargs({"key": "value\nwith newline"})

    def test_shell_substitution_rejected(self) -> None:
        from devbench.github.security import _validate_gh_kwargs

        with pytest.raises(ValueError, match="forbidden token"):
            _validate_gh_kwargs({"key": "value$(rm -rf /)"})


class TestSecurityFetchErrorWrapping:
    """TD-2: malformed JSON from gh api wraps the exception in a generic SecurityFetchError."""

    def test_malformed_json_raises_generic_error(self) -> None:
        from devbench.github.security import _fetch_alerts

        with patch("devbench.github.security._gh_api", return_value=(0, "not-json")):
            with pytest.raises(Exception) as exc_info:
                _fetch_alerts("code-scanning", "repos/x/code-scanning/alerts", "x/repo")
        msg = str(exc_info.value)
        assert "code-scanning" in msg
        assert "x/repo" in msg
        # The original cause must be preserved for DEBUG-level diagnostics.
        assert exc_info.value.__cause__ is not None

    def test_non_array_response_raises(self) -> None:
        from devbench.github.security import _fetch_alerts

        with patch("devbench.github.security._gh_api", return_value=(0, "{}")):
            with pytest.raises(Exception, match="not a JSON array"):
                _fetch_alerts("dependabot", "repos/x/dependabot/alerts", "x/repo")

    def test_non_zero_response_returns_none(self) -> None:
        from devbench.github.security import _fetch_alerts

        with patch("devbench.github.security._gh_api", return_value=(1, "")):
            assert _fetch_alerts("dependabot", "repos/x/dependabot/alerts", "x/repo") is None


class TestSetCategoryEnabledFlag:
    """TD-1: explicit if/elif ladder for the per-category enabled flag."""

    def test_unknown_category_raises(self) -> None:
        from devbench.github.security import SecurityReport, _set_category_enabled_flag

        report = SecurityReport(repo="x/y")
        with pytest.raises(Exception, match="Unknown alert category"):
            _set_category_enabled_flag(report, "not-a-real-category")

    def test_each_known_category_sets_correct_flag(self) -> None:
        from devbench.github.security import SecurityReport, _set_category_enabled_flag

        for category, attr in (
            ("code-scanning", "codeql_enabled"),
            ("dependabot", "dependabot_enabled"),
            ("secret-scanning", "secret_scanning_enabled"),
        ):
            report = SecurityReport(repo="x/y")
            _set_category_enabled_flag(report, category)
            assert getattr(report, attr) is True
