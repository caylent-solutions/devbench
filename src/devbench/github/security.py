"""GitHub security scanning integration.

Enables and queries CodeQL, Dependabot, and secret scanning
on allowed repositories. Internal structure splits the responsibilities
into three single-responsibility helper layers per SRP / E213 hardening:

- ``_fetch_*`` -- subprocess + ``gh api`` calls (one reason to change:
  the GitHub API surface).
- ``_parse_*_alerts`` -- JSON -> ``SecurityFinding`` translation per
  alert category (one reason to change: GitHub's alert payload schemas).
- ``get_security_report`` -- aggregation: orchestrates fetch + parse
  and produces a single ``SecurityReport`` (one reason to change: the
  caller-facing report shape).
"""

import json
import logging
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field

from devbench.config import ALLOWED_REPOS, GH_API_TIMEOUT, get_gh_token, validate_repo
from devbench.constants import (
    CODEQL_QUERY_SUITE,
    CODEQL_STATE,
    SECURITY_ALERT_CATEGORIES,
    SECURITY_FEATURE_ENABLED,
)

logger = logging.getLogger(__name__)


class SecurityFetchError(RuntimeError):
    """Raised when the security-fetch layer cannot produce a clean report.

    Carries only sanitised context (category, repo) to satisfy CLAUDE.md
    "Generic for authentication failures... no internal API details
    exposed". The original cause stays attached via ``__cause__`` so
    DEBUG-level logging keeps the diagnostic detail without leaking it
    into judge feedback.
    """


_GH_KWARG_KEY_RE: re.Pattern[str] = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_\[\]]*$")
_GH_KWARG_FORBIDDEN_VALUE_CHARS: tuple[str, ...] = (
    "\n",
    "\r",
    "\x00",
    "`",
    "$(",
)


def _validate_gh_kwargs(kwargs: dict[str, str]) -> None:
    """Reject ``gh api`` kwarg keys/values that look like injection candidates.

    The subprocess layer already invokes ``gh`` with ``shell=False``, so
    these validations are defence-in-depth: they harden the layer
    against any future caller that forwards user-shaped data into
    kwargs. Keys must match a conservative identifier regex (with
    ``[]`` allowed for ``security_and_analysis[...]`` nested syntax).
    Values must be ``str`` and must not carry control characters or
    common shell-substitution markers; reject with a descriptive
    ``ValueError`` on violation.
    """
    for key, value in kwargs.items():
        if not _GH_KWARG_KEY_RE.match(key):
            raise ValueError(f"gh api kwarg key {key!r} is not a valid identifier")
        if not isinstance(value, str):
            raise ValueError(f"gh api kwarg value for {key!r} must be str, got {type(value).__name__}")
        for forbidden in _GH_KWARG_FORBIDDEN_VALUE_CHARS:
            if forbidden in value:
                raise ValueError(f"gh api kwarg value for {key!r} contains forbidden token {forbidden!r}")


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

    source: str
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

    Returns ``(exit_code, output)``. Validates each kwarg
    key/value pair before extending the argv (TD-4 defence-in-depth).
    Raises ``ValueError`` on suspicious input rather than silently
    forwarding it to ``subprocess.run``.
    """
    _validate_gh_kwargs(kwargs)
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
        env={**os.environ, **env_with_token},
        check=False,
    )
    return result.returncode, result.stdout


def enable_security_features(repo: str) -> dict[str, bool]:
    """Enable all security features on a repository.

    Returns dict of feature -> enabled status.
    """
    validate_repo(repo)
    results: dict[str, bool] = {}

    rc, _ = _gh_api(f"repos/{repo}/vulnerability-alerts", "PUT")
    results["dependabot_alerts"] = rc == 0

    rc, _ = _gh_api(f"repos/{repo}/automated-security-fixes", "PUT")
    results["automated_fixes"] = rc == 0

    rc, output = _gh_api(
        f"repos/{repo}/code-scanning/default-setup",
        "PATCH",
        state=CODEQL_STATE,
        query_suite=CODEQL_QUERY_SUITE,
    )
    results["codeql"] = rc == 0
    if rc != 0:
        logger.warning("CodeQL setup failed for %s: %s", repo, output)

    rc, _ = _gh_api(
        f"repos/{repo}",
        "PATCH",
        **{
            "security_and_analysis[secret_scanning][status]": SECURITY_FEATURE_ENABLED,
            "security_and_analysis[secret_scanning_push_protection][status]": SECURITY_FEATURE_ENABLED,
        },
    )
    results["secret_scanning"] = rc == 0

    logger.info("Security features for %s: %s", repo, results)
    return results


def _fetch_alerts(category: str, endpoint: str, repo: str) -> list[dict] | None:
    """Fetch and JSON-decode the alerts payload for one category.

    Returns ``None`` when the endpoint replied non-zero (the feature is
    disabled on this repo or the caller lacks scope -- both are
    legitimate "no data" outcomes). Raises :class:`SecurityFetchError`
    with a sanitised message when the JSON response is malformed; the
    original ``json.JSONDecodeError`` is preserved via ``__cause__``
    for DEBUG-level logging without leaking the raw payload to judge
    feedback (TD-2).
    """
    rc, output = _gh_api(endpoint)
    if rc != 0:
        return None
    try:
        decoded = json.loads(output)
    except json.JSONDecodeError as exc:
        logger.debug(
            "security_fetch: json decode failed for %s on %s: %s",
            category,
            repo,
            exc,
        )
        raise SecurityFetchError(f"Failed to parse {category!r} alerts for {repo!r}: malformed JSON response") from exc
    if not isinstance(decoded, list):
        raise SecurityFetchError(f"Failed to parse {category!r} alerts for {repo!r}: payload is not a JSON array")
    return decoded


def _parse_code_scanning_alert(alert: dict) -> SecurityFinding:
    """Translate one ``code-scanning`` alert payload into a SecurityFinding."""
    return SecurityFinding(
        source="codeql",
        rule_id=_require_field(alert, "rule", "id"),
        severity=_require_field(alert, "rule", "severity"),
        description=_require_field(alert, "rule", "description"),
        url=_require_field(alert, "html_url"),
    )


def _parse_dependabot_alert(alert: dict) -> SecurityFinding:
    """Translate one ``dependabot`` alert payload into a SecurityFinding."""
    return SecurityFinding(
        source="dependabot",
        rule_id=_require_field(alert, "security_advisory", "ghsa_id"),
        severity=_require_field(alert, "security_advisory", "severity"),
        description=_require_field(alert, "security_advisory", "summary"),
        url=_require_field(alert, "html_url"),
    )


def _parse_secret_scanning_alert(alert: dict) -> SecurityFinding:
    """Translate one ``secret-scanning`` alert payload into a SecurityFinding."""
    secret_type = _require_field(alert, "secret_type")
    return SecurityFinding(
        source="secret-scanning",
        rule_id=secret_type,
        severity="critical",
        description=f"Secret detected: {secret_type}",
        url=_require_field(alert, "html_url"),
    )


_ALERT_PARSERS: dict[str, Callable[[dict], SecurityFinding]] = {
    "code-scanning": _parse_code_scanning_alert,
    "dependabot": _parse_dependabot_alert,
    "secret-scanning": _parse_secret_scanning_alert,
}


def _set_category_enabled_flag(report: SecurityReport, category: str) -> None:
    """Mark the matching ``<category>_enabled`` flag on ``report``.

    Replaces the prior ``setattr(report, _enabled_flag_map[category],
    True)`` pattern (TD-1) with an explicit if/elif ladder so the type
    checker can narrow the field assignment to a known dataclass
    attribute and so a future category-set extension fails fast at
    code-edit time rather than silently growing the dict.
    """
    if category == "code-scanning":
        report.codeql_enabled = True
    elif category == "dependabot":
        report.dependabot_enabled = True
    elif category == "secret-scanning":
        report.secret_scanning_enabled = True
    else:
        raise SecurityFetchError(f"Unknown alert category {category!r}")


def get_security_report(repo: str) -> SecurityReport:
    """Get all open security findings for a repository.

    Aggregates per-category fetch + parse calls into a single
    :class:`SecurityReport`. Disabled features (non-zero ``gh api``
    response) are skipped silently; malformed JSON for an enabled
    feature raises :class:`SecurityFetchError` so the caller fails
    fast rather than presenting an empty report as a clean result.
    """
    validate_repo(repo)
    report = SecurityReport(repo=repo)

    for category, endpoint_template in SECURITY_ALERT_CATEGORIES:
        endpoint = endpoint_template.format(repo=repo)
        alerts = _fetch_alerts(category, endpoint, repo)
        if alerts is None:
            continue
        _set_category_enabled_flag(report, category)
        parser = _ALERT_PARSERS[category]
        for alert in alerts:
            report.findings.append(parser(alert))

    return report


def setup_all_repos() -> dict[str, dict[str, bool]]:
    """Enable security features on all allowed repositories."""
    results = {}
    for repo in sorted(ALLOWED_REPOS):
        results[repo] = enable_security_features(repo)
    return results
