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
# ALLOWED_GH_ORG is the legacy single-org restriction (now deprecated).
# ALLOWED_GH_ORGS is the new list sourced from RUNTIME_CONFIG.allowed_orgs
# and is populated after RUNTIME_CONFIG is loaded below.

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
# Allowed orgs — sourced from YAML allowed_orgs, with JUDGE_GH_ORG as deprecated fallback.
# ---------------------------------------------------------------------------
_allowed_gh_orgs: list[str] = list(RUNTIME_CONFIG.allowed_orgs)
_legacy_gh_org: str = os.environ.get("JUDGE_GH_ORG", "")
if _legacy_gh_org:
    _log.warning(
        "JUDGE_GH_ORG is deprecated. Add '%s' to allowed_orgs in your devbench.yaml instead.",
        _legacy_gh_org,
    )
    if _legacy_gh_org not in _allowed_gh_orgs:
        _allowed_gh_orgs.append(_legacy_gh_org)
ALLOWED_GH_ORGS: list[str] = _allowed_gh_orgs

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

# ---------------------------------------------------------------------------
# USE_BEDROCK / BEDROCK_REGION — env var overrides YAML value.
# ---------------------------------------------------------------------------
_use_bedrock_env = os.environ.get("JUDGE_USE_BEDROCK", "")
if _use_bedrock_env:
    USE_BEDROCK: bool = _use_bedrock_env.lower() in ("1", "true", "yes")
else:
    USE_BEDROCK = RUNTIME_CONFIG.use_bedrock

_bedrock_region_resolved: str = os.environ.get(
    "JUDGE_BEDROCK_REGION",
    os.environ.get("AWS_REGION", RUNTIME_CONFIG.bedrock_region or ""),
)
if USE_BEDROCK and not _bedrock_region_resolved:
    raise RuntimeError(
        "BEDROCK_REGION is required when use_bedrock is true. "
        "Set JUDGE_BEDROCK_REGION, AWS_REGION, or bedrock_region in devbench.yaml."
    )
BEDROCK_REGION: str = _bedrock_region_resolved

# ---------------------------------------------------------------------------
# Model identifiers — resolution order per model field:
# 1. ANTHROPIC_MODEL env var (overrides both CLAUDE_MODEL and EXECUTOR_MODEL silently)
# 2. JUDGE_CLAUDE_MODEL env var (deprecated fallback — populates both, emits WARNING)
# 3. YAML judge_model / executor_model field
# 4. Auth-dependent default env var:
#    - JUDGE_DEFAULT_MODEL_BEDROCK when USE_BEDROCK is True
#    - JUDGE_DEFAULT_MODEL_DIRECT when USE_BEDROCK is False
#    If the default env var is also absent, RuntimeError is raised (fail-fast).
# ---------------------------------------------------------------------------
_anthropic_model_override: str = os.environ.get("ANTHROPIC_MODEL", "")
_judge_claude_model_legacy: str = os.environ.get("JUDGE_CLAUDE_MODEL", "")


def _resolve_default_model(use_bedrock: bool) -> str:
    """Return the auth-dependent default model from environment variables.

    Raises:
        RuntimeError: When neither the Bedrock nor Direct default env var is set.
    """
    if use_bedrock:
        default = os.environ.get("JUDGE_DEFAULT_MODEL_BEDROCK", "")
        if not default:
            raise RuntimeError(
                "JUDGE_DEFAULT_MODEL_BEDROCK environment variable is not set. "
                "Set it to the AWS Bedrock model ID to use when judge_model/executor_model "
                "are absent from devbench.yaml and ANTHROPIC_MODEL is not set."
            )
        return default
    default = os.environ.get("JUDGE_DEFAULT_MODEL_DIRECT", "")
    if not default:
        raise RuntimeError(
            "JUDGE_DEFAULT_MODEL_DIRECT environment variable is not set. "
            "Set it to the Anthropic model ID to use when judge_model/executor_model "
            "are absent from devbench.yaml and ANTHROPIC_MODEL is not set."
        )
    return default


if _anthropic_model_override:
    # ANTHROPIC_MODEL silently overrides both models (Claude CLI convention).
    CLAUDE_MODEL: str = _anthropic_model_override
    EXECUTOR_MODEL: str = _anthropic_model_override
elif _judge_claude_model_legacy:
    # JUDGE_CLAUDE_MODEL is deprecated; it still populates both constants as a fallback.
    _log.warning(
        "JUDGE_CLAUDE_MODEL is deprecated. Use judge_model/executor_model in devbench.yaml "
        "or ANTHROPIC_MODEL env var instead."
    )
    CLAUDE_MODEL = _judge_claude_model_legacy
    EXECUTOR_MODEL = _judge_claude_model_legacy
else:
    _default_model: str = (
        RUNTIME_CONFIG.judge_model or _resolve_default_model(USE_BEDROCK)
    )
    _default_executor_model: str = (
        RUNTIME_CONFIG.executor_model or _resolve_default_model(USE_BEDROCK)
    )
    CLAUDE_MODEL = _default_model
    EXECUTOR_MODEL = _default_executor_model


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
# Git ops behaviour flags — sourced from YAML git_ops block.
# ---------------------------------------------------------------------------
UPDATE_SUBMODULE: bool = RUNTIME_CONFIG.git_ops.update_submodule

# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------
GH_TOKEN_FILE: Path = Path(os.environ.get("JUDGE_GH_TOKEN_FILE", str(Path.home() / ".gh_token_env")))
CLAUDE_CREDENTIALS_FILE: Path = Path(
    os.environ.get("JUDGE_CLAUDE_CREDENTIALS_FILE", str(Path.home() / ".claude" / ".credentials.json"))
)


def validate_repo(repo: str) -> None:
    """Raise ``ValueError`` if *repo* is not in the allow-list or from a disallowed org.

    When ``allowed_orgs`` is non-empty (from YAML or the deprecated ``JUDGE_GH_ORG``),
    also validates that the repo's org is in that list.
    """
    if ALLOWED_GH_ORGS and "/" in repo:
        org = repo.split("/", maxsplit=1)[0]
        if org not in ALLOWED_GH_ORGS:
            raise ValueError(
                f"Repository '{repo}' belongs to org '{org}', "
                f"which is not in allowed_orgs: {ALLOWED_GH_ORGS}."
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
