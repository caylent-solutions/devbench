"""Security review judge that checks GitHub security scan results.

Gathers CodeQL, Dependabot, and secret-scanning alerts via GitHub API,
plus the git diff, then delegates the full review to the LLM which
evaluates severity and recommends fixes.
"""

import json
import os
import subprocess
from pathlib import Path

from devbench.config import ALERT_SUMMARY_LIMIT, SECURITY_FETCH_TIMEOUT, get_gh_token, validate_repo
from devbench.constants import SECURITY_ALERT_CATEGORIES
from devbench.judges.base import BaseJudge, JudgeResult
from devbench.prompts import load_prompt

_SECURITY_REVIEW_SYSTEM_PROMPT = load_prompt("security_review")


class SecurityReviewJudge(BaseJudge):
    """Reviews security scan results from GitHub Advanced Security features."""

    def __init__(self) -> None:
        super().__init__("security_review")

    def evaluate(self, work_unit_path: Path, repo_path: Path, **kwargs: object) -> JudgeResult:
        """Evaluate security by gathering alerts and delegating to the LLM."""
        repo: str = str(kwargs.get("repo", ""))
        if not repo:
            raise ValueError("SecurityReviewJudge requires 'repo' keyword argument (e.g. 'owner/name').")

        validate_repo(repo)

        work_unit_content = self._read_file(work_unit_path)
        token = get_gh_token()

        # Gather security alert evidence
        alert_details = self._gather_alert_evidence(repo, token, repo_path)
        diff = self._get_diff(repo_path)

        evidence_sections: dict[str, str] = {
            "Work Unit": work_unit_content,
        }
        if alert_details:
            evidence_sections["GitHub Security Alerts"] = alert_details
        if diff:
            evidence_sections["Git Diff"] = diff

        return self._llm_evaluate(
            system_prompt=_SECURITY_REVIEW_SYSTEM_PROMPT,
            evidence_sections=evidence_sections,
            cwd=repo_path,
        )

    def _gather_alert_evidence(self, repo: str, token: str, repo_path: Path) -> str:
        """Fetch and summarize security alerts from all categories."""
        alert_checks = [
            (category, api_template.format(repo=repo))
            for category, api_template in SECURITY_ALERT_CATEGORIES
        ]

        all_alert_details: list[str] = []
        for category, api_path in alert_checks:
            alerts = self._fetch_alerts(api_path, token, repo_path)
            if alerts:
                details = self._summarize_alerts(alerts, category)
                all_alert_details.append(f"## {category} ({len(alerts)} alert(s))\n{details}")
            else:
                all_alert_details.append(f"## {category}\nNo open alerts.")

        return "\n\n".join(all_alert_details)

    def _fetch_alerts(self, api_path: str, token: str, cwd: Path) -> list[dict[str, object]]:
        """Fetch alerts from the GitHub API using the ``gh`` CLI."""
        if not token:
            self.logger.warning("No GitHub token available; skipping alert fetch for %s", api_path)
            return []

        env = {**os.environ, "GH_TOKEN": token}
        try:
            result = subprocess.run(
                ["gh", "api", api_path, "--paginate"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=SECURITY_FETCH_TIMEOUT,
                env=env,
            )
        except FileNotFoundError:
            self.logger.warning("gh CLI not found; skipping alert fetch for %s", api_path)
            return []

        if result.returncode != 0:
            self.logger.warning(
                "Failed to fetch alerts from %s (exit %d): %s",
                api_path, result.returncode, result.stderr.strip(),
            )
            return []

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            self.logger.warning("Non-JSON response from %s", api_path)
            return []

        if not isinstance(data, list):
            self.logger.warning("Expected list from %s, got %s", api_path, type(data).__name__)
            return []

        return data

    def _summarize_alerts(self, alerts: list[dict[str, object]], category: str) -> str:
        """Build a concise summary of alerts for LLM context."""
        lines: list[str] = []
        for alert in alerts[:ALERT_SUMMARY_LIMIT]:
            if category == "code-scanning":
                rule = alert.get("rule", {})
                rule_id = rule.get("id", "unknown") if isinstance(rule, dict) else "unknown"
                desc = rule.get("description", "") if isinstance(rule, dict) else ""
                lines.append(f"  - [{rule_id}] {desc}")
            elif category == "dependabot":
                dep = alert.get("dependency", {})
                pkg = dep.get("package", {}).get("name", "unknown") if isinstance(dep, dict) else "unknown"
                severity = alert.get("security_advisory", {})
                sev_level = severity.get("severity", "unknown") if isinstance(severity, dict) else "unknown"
                lines.append(f"  - {pkg} (severity: {sev_level})")
            elif category == "secret-scanning":
                secret_type = alert.get("secret_type_display_name", "unknown")
                lines.append(f"  - {secret_type}")

        if len(alerts) > ALERT_SUMMARY_LIMIT:
            lines.append(f"  ... and {len(alerts) - ALERT_SUMMARY_LIMIT} more")
        return "\n".join(lines)

    def _get_diff(self, repo_path: Path) -> str:
        """Return the combined diff of all changes: staged, unstaged, and committed."""
        parts: list[str] = []

        # Staged changes
        rc, stdout, _ = self._run_command(["git", "diff", "--cached"], cwd=repo_path)
        if rc == 0 and stdout.strip():
            parts.append(stdout)

        # Unstaged changes
        rc, stdout, _ = self._run_command(["git", "diff"], cwd=repo_path)
        if rc == 0 and stdout.strip():
            parts.append(stdout)

        # All committed branch changes vs default branch
        default_branch = self._get_default_branch(repo_path)
        rc, stdout, _ = self._run_command(["git", "diff", default_branch], cwd=repo_path)
        if rc == 0 and stdout.strip():
            parts.append(stdout)

        return "\n".join(parts)
