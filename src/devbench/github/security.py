"""GitHub security scanning integration.

Enables and queries CodeQL, Dependabot, and secret scanning
on allowed repositories.
"""

import json
import logging
import subprocess
from dataclasses import dataclass, field

from devbench.config import GH_API_TIMEOUT, REPO_CONFIGS, get_gh_token, validate_repo
from devbench.constants import SECURITY_ALERT_CATEGORIES

logger = logging.getLogger(__name__)


def _require_field(data: dict, *keys: str) -> str:
    """Extract a nested field from a dict, raising RuntimeError if missing."""
    current: object = data
    for key in keys:
        if not isinstance(current, dict):
            raise RuntimeError(f"Expected dict at key path {keys}, got {type(current).__name__}")
        current = current.get(key)
        if current is None:
            raise RuntimeError(f"Required field '{'.'.join(keys)}' missing from GitHub API response")
    return str(current)


@dataclass
class SecurityFinding:
    """A single security finding from GitHub scanning."""

    source: str  # "codeql", "dependabot", "secret-scanning"
    rule_id: str
    severity: str
    description: str
    url: str


@dataclass
class SecurityReport:
    """Aggregated security findings for a repository."""

    repo: str
    findings: list[SecurityFinding] = field(default_factory=list)
    codeql_enabled: bool = False
    dependabot_enabled: bool = False
    secret_scanning_enabled: bool = False

    @property
    def is_clean(self) -> bool:
        return len(self.findings) == 0


def _gh_api(endpoint: str, method: str = "GET", **kwargs: str) -> tuple[int, str]:
    """Execute a GitHub API call via gh CLI.

    Returns (exit_code, output).
    """
    token = get_gh_token()
    cmd = ["gh", "api", endpoint, "-X", method]
    for key, value in kwargs.items():
        cmd.extend(["-f", f"{key}={value}"])

    env_with_token = {"GH_TOKEN": token}
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=GH_API_TIMEOUT,
        env={**__import__("os").environ, **env_with_token},
    )
    return result.returncode, result.stdout


def enable_security_features(repo: str) -> dict[str, bool]:
    """Enable all security features on a repository.

    Returns dict of feature -> enabled status.
    """
    validate_repo(repo)
    results: dict[str, bool] = {}

    # Dependabot vulnerability alerts
    rc, _ = _gh_api(f"repos/{repo}/vulnerability-alerts", "PUT")
    results["dependabot_alerts"] = rc == 0

    # Automated security fixes
    rc, _ = _gh_api(f"repos/{repo}/automated-security-fixes", "PUT")
    results["automated_fixes"] = rc == 0

    # CodeQL default setup
    rc, output = _gh_api(
        f"repos/{repo}/code-scanning/default-setup",
        "PATCH",
        state="configured",
        query_suite="default",
    )
    results["codeql"] = rc == 0
    if rc != 0:
        logger.warning("CodeQL setup failed for %s: %s", repo, output)

    # Secret scanning
    rc, _ = _gh_api(
        f"repos/{repo}",
        "PATCH",
        **{
            "security_and_analysis[secret_scanning][status]": "enabled",
            "security_and_analysis[secret_scanning_push_protection][status]": "enabled",
        },
    )
    results["secret_scanning"] = rc == 0

    logger.info("Security features for %s: %s", repo, results)
    return results


def get_security_report(repo: str) -> SecurityReport:
    """Get all open security findings for a repository."""
    validate_repo(repo)
    report = SecurityReport(repo=repo)

    _enabled_flag_map: dict[str, str] = {
        "code-scanning": "codeql_enabled",
        "dependabot": "dependabot_enabled",
        "secret-scanning": "secret_scanning_enabled",
    }

    for category, endpoint_template in SECURITY_ALERT_CATEGORIES:
        endpoint = endpoint_template.format(repo=repo)
        rc, output = _gh_api(endpoint)
        if rc != 0:
            continue

        setattr(report, _enabled_flag_map[category], True)

        try:
            alerts = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Failed to parse {category} alerts for {repo}: {exc}"
            ) from exc

        for alert in alerts:
            if category == "code-scanning":
                report.findings.append(
                    SecurityFinding(
                        source="codeql",
                        rule_id=_require_field(alert, "rule", "id"),
                        severity=_require_field(alert, "rule", "severity"),
                        description=_require_field(alert, "rule", "description"),
                        url=_require_field(alert, "html_url"),
                    )
                )
            elif category == "dependabot":
                report.findings.append(
                    SecurityFinding(
                        source="dependabot",
                        rule_id=_require_field(alert, "security_advisory", "ghsa_id"),
                        severity=_require_field(alert, "security_advisory", "severity"),
                        description=_require_field(alert, "security_advisory", "summary"),
                        url=_require_field(alert, "html_url"),
                    )
                )
            elif category == "secret-scanning":
                secret_type = _require_field(alert, "secret_type")
                report.findings.append(
                    SecurityFinding(
                        source="secret-scanning",
                        rule_id=secret_type,
                        severity="critical",
                        description=f"Secret detected: {secret_type}",
                        url=_require_field(alert, "html_url"),
                    )
                )

    return report


def setup_all_repos() -> dict[str, dict[str, bool]]:
    """Enable security features on all configured repositories."""
    results = {}
    for repo in sorted(REPO_CONFIGS):
        results[repo] = enable_security_features(repo)
    return results
