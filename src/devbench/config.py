"""Configuration module for the judges system.

Centralizes all configuration values, repo validation, and credential access.

Config file path resolution (first match wins):
1. ``--config`` CLI argument  (sets ``JUDGE_CONFIG_PATH`` before module import)
2. ``JUDGE_CONFIG_PATH`` environment variable
3. ``<JUDGE_WORKSPACE_ROOT>/backlog/config/devbench.yaml``

Allowed repositories and per-repo settings are defined exclusively in the YAML
config file (``backlog/config/devbench.yaml`` relative to
``JUDGE_WORKSPACE_ROOT``).  The deprecated env vars ``JUDGE_ALLOWED_REPOS``,
``JUDGE_BACKLOG_ROOT``, and ``JUDGE_BACKLOG_INDEX`` are no longer read.
"""

import json
import logging
import os
from enum import StrEnum
from pathlib import Path

from devbench.config_loader import (
    RuntimeConfig,
    get_repo_local_path,
    load_runtime_config,
    resolve_config_path,
)
from devbench.constants import BACKLOG_SUBDIR

_log = logging.getLogger("devbench.config")

# ---------------------------------------------------------------------------
# Repository allow-list
# ---------------------------------------------------------------------------
# When set, restricts all GitHub operations to this org only.
# Unset or empty to allow any org in the allow-list.
ALLOWED_GH_ORG: str = os.environ.get("JUDGE_GH_ORG", "")

# Absolute path to the workspace root directory containing all repo clones.
_workspace_root = os.environ.get("JUDGE_WORKSPACE_ROOT", "")
if not _workspace_root:
    raise RuntimeError(
        "JUDGE_WORKSPACE_ROOT environment variable is not set. "
        "Set it to the absolute path of your workspace root."
    )
WORKSPACE_ROOT: Path = Path(_workspace_root)

# ---------------------------------------------------------------------------
# YAML config loading
# ---------------------------------------------------------------------------
# Resolve config path and load YAML.  Fails fast if the file cannot be found.
_config_path: Path = resolve_config_path(None, os.environ, WORKSPACE_ROOT)
RUNTIME_CONFIG: RuntimeConfig = load_runtime_config(_config_path, os.environ)

# ---------------------------------------------------------------------------
# Allowed repos — sourced exclusively from YAML config.
# ---------------------------------------------------------------------------
ALLOWED_REPOS: frozenset[str] = frozenset(RUNTIME_CONFIG.repos)

REPO_LOCAL_PATHS: dict[str, Path] = {
    repo: get_repo_local_path(repo, RUNTIME_CONFIG, WORKSPACE_ROOT) for repo in ALLOWED_REPOS
}

# Short name -> full name mapping for backlog compatibility.
# The backlog table uses short names (e.g., "git-repo") while the allow-list
# uses fully-qualified names (e.g., "caylent-solutions/git-repo").
REPO_SHORT_TO_FULL: dict[str, str] = {
    repo.split("/", maxsplit=1)[1]: repo for repo in ALLOWED_REPOS
}


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
# Backlog paths — derived from WORKSPACE_ROOT.
# ---------------------------------------------------------------------------
BACKLOG_ROOT: Path = WORKSPACE_ROOT / BACKLOG_SUBDIR
BACKLOG_INDEX: Path = WORKSPACE_ROOT / "BACKLOG.md"

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


class MergeStrategy(StrEnum):
    """Valid merge strategies for PR merges."""

    MERGE = "merge"
    SQUASH = "squash"
    REBASE = "rebase"

    @property
    def flag(self) -> str:
        """Return the ``gh pr merge`` flag for this strategy."""
        return f"--{self.value}"


# Merge strategy for PRs. Defaults to squash.
_merge_strategy = os.environ.get("JUDGE_MERGE_STRATEGY", "squash")
try:
    MERGE_STRATEGY: MergeStrategy = MergeStrategy(_merge_strategy)
except ValueError:
    raise RuntimeError(
        f"JUDGE_MERGE_STRATEGY must be one of: {', '.join(s.value for s in MergeStrategy)}. Got: {_merge_strategy}"
    ) from None

UPDATE_SUBMODULE: bool = RUNTIME_CONFIG.git_ops.update_submodule
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
