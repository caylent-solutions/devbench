"""Configuration module for the judges system.

Centralizes all configuration values, repo validation, and credential access.
All environment-specific values are read from environment variables with defaults.
"""

import json
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Repository allow-list
# ---------------------------------------------------------------------------
# When set, restricts all GitHub operations to this org only.
# Unset or empty to allow any org in the allow-list.
ALLOWED_GH_ORG: str = os.environ.get("JUDGE_GH_ORG", "")

ALLOWED_REPOS: frozenset[str] = frozenset(
    {
        "caylent-solutions/git-repo",
        "caylent-solutions/caylent-private-rpm",
        "caylent-solutions/rpm-claude-marketplaces",
        "caylent-solutions/rpm-claude-marketplaces-install",
    }
)

_WORKSPACE_ROOT = Path(os.environ.get("JUDGE_WORKSPACE_ROOT", "/workspaces/general-agent-env"))
WORKSPACE_ROOT: Path = _WORKSPACE_ROOT

REPO_LOCAL_PATHS: dict[str, Path] = {repo: _WORKSPACE_ROOT / repo.split("/", maxsplit=1)[1] for repo in ALLOWED_REPOS}

# Short name -> full name mapping for backlog compatibility.
# The backlog table uses short names (e.g., "git-repo") while the allow-list
# uses fully-qualified names (e.g., "caylent-solutions/git-repo").
REPO_SHORT_TO_FULL: dict[str, str] = {repo.split("/", maxsplit=1)[1]: repo for repo in ALLOWED_REPOS}


def resolve_repo(short_or_full: str) -> str:
    """Resolve a short repo name to its fully-qualified form.

    If the name is already fully-qualified, it is returned as-is.
    Raises ``ValueError`` if the name cannot be resolved.
    """
    if short_or_full in ALLOWED_REPOS:
        return short_or_full
    full = REPO_SHORT_TO_FULL.get(short_or_full)
    if full is not None:
        return full
    raise ValueError(
        f"Repository '{short_or_full}' is not recognised. "
        f"Allowed: {sorted(ALLOWED_REPOS)} or short names: {sorted(REPO_SHORT_TO_FULL)}"
    )


# ---------------------------------------------------------------------------
# Backlog paths
# ---------------------------------------------------------------------------
BACKLOG_ROOT: Path = Path(os.environ.get("JUDGE_BACKLOG_ROOT", str(_WORKSPACE_ROOT / "backlog")))
BACKLOG_INDEX: Path = Path(os.environ.get("JUDGE_BACKLOG_INDEX", str(_WORKSPACE_ROOT / "BACKLOG.md")))

# ---------------------------------------------------------------------------
# Operational parameters
# ---------------------------------------------------------------------------
MAX_RETRY_ATTEMPTS: int = int(os.environ.get("JUDGE_MAX_RETRIES", "10"))
GITHUB_CHECK_TIMEOUT_SECONDS: int = int(os.environ.get("JUDGE_GH_TIMEOUT", "600"))
_claude_model = os.environ.get("JUDGE_CLAUDE_MODEL", "")
if not _claude_model:
    raise RuntimeError(
        "JUDGE_CLAUDE_MODEL environment variable is not set. "
        "Set it to a valid model identifier (e.g. us.anthropic.claude-sonnet-4-6-v1)."
    )
CLAUDE_MODEL: str = _claude_model
USE_BEDROCK: bool = os.environ.get("JUDGE_USE_BEDROCK", "").lower() in ("1", "true", "yes")
BEDROCK_REGION: str = os.environ.get("JUDGE_BEDROCK_REGION", os.environ.get("AWS_REGION", "us-east-1"))

# ---------------------------------------------------------------------------
# Timeouts — all values in seconds
# ---------------------------------------------------------------------------
GH_API_TIMEOUT: int = int(os.environ.get("JUDGE_GH_API_TIMEOUT", "30"))
TEST_TIMEOUT: int = int(os.environ.get("JUDGE_TEST_TIMEOUT", "300"))
SECURITY_FETCH_TIMEOUT: int = int(os.environ.get("JUDGE_SECURITY_FETCH_TIMEOUT", "120"))
LLM_TIMEOUT: int = int(os.environ.get("JUDGE_LLM_TIMEOUT", "300"))
COMMAND_TIMEOUT: int = int(os.environ.get("JUDGE_COMMAND_TIMEOUT", "120"))

# ---------------------------------------------------------------------------
# Thresholds and limits
# ---------------------------------------------------------------------------
ALERT_SUMMARY_LIMIT: int = int(os.environ.get("JUDGE_ALERT_SUMMARY_LIMIT", "10"))
OUTPUT_TRUNCATION_LIMIT: int = int(os.environ.get("JUDGE_OUTPUT_TRUNCATION", "2000"))
LLM_EVIDENCE_TRUNCATION: int = int(os.environ.get("JUDGE_LLM_EVIDENCE_TRUNCATION", "15000"))

# ---------------------------------------------------------------------------
# LLM context limits
# ---------------------------------------------------------------------------
LLM_FILE_CONTEXT_LIMIT: int = int(os.environ.get("JUDGE_LLM_FILE_CONTEXT_LIMIT", "5"))
LLM_FILE_PREVIEW_CHARS: int = int(os.environ.get("JUDGE_LLM_FILE_PREVIEW_CHARS", "3000"))

# ---------------------------------------------------------------------------
# Claude executor
# ---------------------------------------------------------------------------
EXECUTOR_TIMEOUT: int = int(os.environ.get("JUDGE_EXECUTOR_TIMEOUT", "1800"))
EXECUTOR_MAX_TURNS: int = int(os.environ.get("JUDGE_EXECUTOR_MAX_TURNS", "50"))

# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
ORCHESTRATOR_POLL_INTERVAL: int = int(os.environ.get("JUDGE_ORCHESTRATOR_POLL_INTERVAL", "10"))

# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------
GH_TOKEN_FILE: Path = Path(os.environ.get("JUDGE_GH_TOKEN_FILE", str(Path.home() / ".gh_token_env")))
CLAUDE_CREDENTIALS_FILE: Path = Path(
    os.environ.get("JUDGE_CLAUDE_CREDENTIALS_FILE", str(Path.home() / ".claude" / ".credentials.json"))
)


def validate_repo(repo: str) -> None:
    """Raise ``ValueError`` if *repo* is not in the allow-list or wrong org.

    When ``JUDGE_GH_ORG`` is set, also validates that the repo belongs
    to the specified organization.
    """
    if ALLOWED_GH_ORG and "/" in repo:
        org = repo.split("/", maxsplit=1)[0]
        if org != ALLOWED_GH_ORG:
            raise ValueError(
                f"Repository '{repo}' belongs to org '{org}', "
                f"but JUDGE_GH_ORG restricts access to '{ALLOWED_GH_ORG}'."
            )
    if repo not in ALLOWED_REPOS:
        raise ValueError(f"Repository '{repo}' is not allowed. Allowed repositories: {sorted(ALLOWED_REPOS)}")


def get_anthropic_api_key() -> str:
    """Return an Anthropic API key for LLM judge evaluation.

    Reads the Claude Code OAuth token from the credentials file at
    ``CLAUDE_CREDENTIALS_FILE``.  The token has ``user:inference`` scope
    and is accepted by the Anthropic Python SDK as an ``api_key``.

    Raises ``RuntimeError`` if the credentials file is missing, unreadable,
    or does not contain a valid access token.
    """
    if not CLAUDE_CREDENTIALS_FILE.is_file():
        raise RuntimeError(
            f"Claude credentials file not found at '{CLAUDE_CREDENTIALS_FILE}'. "
            "Ensure Claude Code is authenticated (run 'claude' to login)."
        )

    try:
        raw = CLAUDE_CREDENTIALS_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Failed to read Claude credentials from '{CLAUDE_CREDENTIALS_FILE}': {exc}"
        ) from exc

    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        raise RuntimeError(
            f"Unexpected credentials structure in '{CLAUDE_CREDENTIALS_FILE}': "
            "missing 'claudeAiOauth' object."
        )

    token = oauth.get("accessToken", "").strip()
    if not token:
        raise RuntimeError(
            f"No access token found in '{CLAUDE_CREDENTIALS_FILE}'. "
            "Re-authenticate Claude Code (run 'claude' to login)."
        )

    return token


def get_gh_token() -> str:
    """Return the GitHub token, reading from file first then env var.

    Raises ``RuntimeError`` if the token cannot be obtained from either source.
    """
    if GH_TOKEN_FILE.is_file():
        token = GH_TOKEN_FILE.read_text().strip()
        if token:
            return token

    token = os.environ.get("GH_TOKEN", "").strip()
    if token:
        return token

    raise RuntimeError(
        f"GitHub token not found. Provide it via the file at '{GH_TOKEN_FILE}' or the GH_TOKEN environment variable."
    )
