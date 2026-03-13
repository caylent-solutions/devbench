"""YAML configuration loader with deterministic path and value precedence.

Config file path precedence (first match wins):
1. ``explicit_path`` argument passed to ``resolve_config_path``
2. ``JUDGE_CONFIG_PATH`` environment variable
3. Default path: ``<WORKSPACE_ROOT>/backlog/config/devbench.yaml``

Config value precedence:
- YAML values override code defaults.  Environment variable overrides are applied
  by ``config.py``, not by this module.

This module is parse/validate only — it does not read environment variables.
All env-var-driven defaults for operational parameters (timeouts, limits, model
identifiers, region) are applied by ``config.py``.  Optional fields in the
dataclasses default to ``None``; callers are responsible for substituting
environment-driven values when ``None`` is encountered.

YAML schema::

    repos:                               # required — at least one entry
      org/repo:                          # key must be "org/repo" format
        default_branch: main2            # optional — omit to fall back to origin/HEAD
        checkout_directory: my-checkout  # optional — relative to JUDGE_WORKSPACE_ROOT
        merge_strategy: squash           # optional — overrides top-level merge_strategy

    merge_strategy: squash               # optional — default merge strategy for all repos
    max_retries: <integer>               # optional — max retry attempts
    use_bedrock: false                   # optional — route LLM calls via AWS Bedrock
    bedrock_region: <aws-region-string>  # optional — AWS region for Bedrock (env var override applied by config.py)
    judge_model: <model-id>              # optional — model for judge agents (env var override applied by config.py)
    executor_model: <model-id>           # optional — model for executor agent (env var override applied by config.py)
    allowed_orgs:                        # optional — permitted GitHub organisations
      - caylent-solutions

    timeouts:                            # optional — all values in seconds; env var overrides applied by config.py
      gh_api: <integer>
      test: <integer>
      security_fetch: <integer>
      llm: <integer>
      command: <integer>
      executor: <integer>
      executor_max_turns: <integer>
      orchestrator_poll_interval: <integer>
      github_check: <integer>

    limits:                              # optional — threshold values; env var overrides applied by config.py
      alert_summary: <integer>
      output_truncation: <integer>
      llm_evidence_truncation: <integer>
      llm_file_context: <integer>
      llm_file_preview_chars: <integer>

Example config file (``backlog/config/devbench.yaml``)::

    repos:
      caylent-solutions/devbench:
        default_branch: main2
        checkout_directory: devbench
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import jsonschema
import yaml

__all__ = [
    "LimitConfig",
    "RepoConfig",
    "RuntimeConfig",
    "TimeoutConfig",
    "get_configured_default_branch",
    "get_repo_local_path",
    "load_runtime_config",
    "resolve_config_path",
]

# Relative path from WORKSPACE_ROOT to the default config file location.
DEFAULT_CONFIG_SUBPATH: str = "backlog/config/devbench.yaml"

# Load the JSON Schema once at module import time.
_SCHEMA_PATH: Path = Path(__file__).parent / "config-schema.json"
with _SCHEMA_PATH.open(encoding="utf-8") as _f:
    _SCHEMA: dict = json.load(_f)


@dataclass
class TimeoutConfig:
    """Timeout values (in seconds) for various operations.

    Fields default to ``None`` when not specified in YAML.  ``config.py``
    applies environment-variable-driven defaults for any ``None`` field.

    Attributes:
        gh_api: GitHub API call timeout.
        test: Test suite run timeout.
        security_fetch: Security advisory fetch timeout.
        llm: LLM API call timeout.
        command: Shell command execution timeout.
        executor: Executor agent overall timeout.
        executor_max_turns: Maximum number of executor turns.
        orchestrator_poll_interval: Orchestrator polling interval.
        github_check: GitHub check status polling timeout.
    """

    gh_api: int | None = None
    test: int | None = None
    security_fetch: int | None = None
    llm: int | None = None
    command: int | None = None
    executor: int | None = None
    executor_max_turns: int | None = None
    orchestrator_poll_interval: int | None = None
    github_check: int | None = None


@dataclass
class LimitConfig:
    """Threshold and limit values.

    Fields default to ``None`` when not specified in YAML.  ``config.py``
    applies environment-variable-driven defaults for any ``None`` field.

    Attributes:
        alert_summary: Maximum number of security alert summaries to include.
        output_truncation: Character limit for command output truncation.
        llm_evidence_truncation: Character limit for LLM evidence content truncation.
        llm_file_context: Maximum number of files included in LLM context.
        llm_file_preview_chars: Character limit for per-file LLM preview.
    """

    alert_summary: int | None = None
    output_truncation: int | None = None
    llm_evidence_truncation: int | None = None
    llm_file_context: int | None = None
    llm_file_preview_chars: int | None = None


@dataclass
class RepoConfig:
    """Per-repository configuration.

    Attributes:
        default_branch: Explicit default branch to use for this repo.
            When ``None``, branch consumers fall back to ``origin/HEAD``.
        checkout_directory: Path relative to ``JUDGE_WORKSPACE_ROOT`` where
            the repo is checked out.  Must not be absolute or contain ``..``.
            When ``None``, defaults to the repo short-name (the part after
            the ``/`` in ``org/repo``).
        merge_strategy: Per-repo PR merge strategy override.  When ``None``,
            the top-level ``RuntimeConfig.merge_strategy`` is used.
    """

    default_branch: str | None = None
    checkout_directory: str | None = None
    merge_strategy: str | None = None


@dataclass
class RuntimeConfig:
    """Merged runtime configuration loaded from the YAML config file.

    Optional fields default to ``None`` when not specified in YAML.
    ``config.py`` applies environment-variable-driven defaults for any
    ``None`` field before exposing configuration to the rest of the system.

    Attributes:
        repos: Mapping of fully-qualified ``org/repo`` names to their
            per-repository configuration.
        timeouts: Timeout values for various operations.
        limits: Threshold and limit values.
        allowed_orgs: List of permitted GitHub organisations.
        judge_model: Model identifier used by judge agents.
        executor_model: Model identifier used by the executor agent.
        use_bedrock: Whether to route LLM calls through AWS Bedrock.
        bedrock_region: AWS region for Bedrock API calls.
        merge_strategy: Default PR merge strategy for all repos.
        max_retries: Maximum number of retry attempts.
    """

    repos: dict[str, RepoConfig] = field(default_factory=dict)
    timeouts: TimeoutConfig = field(default_factory=TimeoutConfig)
    limits: LimitConfig = field(default_factory=LimitConfig)
    allowed_orgs: list[str] = field(default_factory=list)
    judge_model: str | None = None
    executor_model: str | None = None
    use_bedrock: bool = False
    bedrock_region: str | None = None
    merge_strategy: str | None = None
    max_retries: int | None = None


def resolve_config_path(
    explicit_path: str | None,
    env: Mapping[str, str],
    workspace_root: Path,
) -> Path:
    """Return config file path using precedence: explicit > JUDGE_CONFIG_PATH > default.

    Args:
        explicit_path: Path from the ``--config`` CLI argument, or ``None``.
        env: Environment variable mapping (typically ``os.environ``).
        workspace_root: Absolute path to the workspace root
            (value of ``JUDGE_WORKSPACE_ROOT``).

    Returns:
        Resolved config file path.  The path may not exist on disk — callers
        are responsible for checking existence.
    """
    if explicit_path:
        return Path(explicit_path)
    env_path = env.get("JUDGE_CONFIG_PATH", "")
    if env_path:
        return Path(env_path)
    return workspace_root / DEFAULT_CONFIG_SUBPATH


def _parse_repo_config(path: Path, repo_name: str, repo_data: object) -> RepoConfig:
    """Parse and validate a single repo entry from raw YAML.

    Args:
        path: Config file path (used in error messages).
        repo_name: The ``org/repo`` key.
        repo_data: Raw value from YAML (may be None or a dict after schema validation).

    Returns:
        ``RepoConfig`` populated from *repo_data*.

    Raises:
        ValueError: If *checkout_directory* is absolute or contains ``..``.
    """
    if not isinstance(repo_data, dict):
        return RepoConfig()

    default_branch: str | None = repo_data.get("default_branch")
    repo_merge_strategy: str | None = repo_data.get("merge_strategy")

    raw_checkout = repo_data.get("checkout_directory")
    if raw_checkout is None:
        return RepoConfig(
            default_branch=default_branch,
            merge_strategy=repo_merge_strategy,
        )

    if Path(raw_checkout).is_absolute():
        raise ValueError(
            f"Config file '{path}': repos.{repo_name}.checkout_directory "
            f"must be a relative path, got absolute path '{raw_checkout}'."
        )
    if ".." in Path(raw_checkout).parts:
        raise ValueError(
            f"Config file '{path}': repos.{repo_name}.checkout_directory "
            f"must not contain parent traversal ('..'), got '{raw_checkout}'."
        )
    return RepoConfig(
        default_branch=default_branch,
        checkout_directory=raw_checkout,
        merge_strategy=repo_merge_strategy,
    )


def _parse_repos(
    path: Path, repos_raw: dict, allowed_orgs: list[str]
) -> dict[str, RepoConfig]:
    """Build the repos mapping from the raw YAML ``repos`` block.

    When *allowed_orgs* is non-empty, every repo key's organisation component
    must appear in *allowed_orgs*.

    Args:
        path: Config file path (used in error messages).
        repos_raw: Raw ``repos`` dict from YAML (already schema-validated).
        allowed_orgs: Permitted GitHub organisations.  Empty list means any org.

    Returns:
        Mapping of ``org/repo`` → ``RepoConfig``.

    Raises:
        ValueError: If a repo key's org is not in *allowed_orgs*.
    """
    repos: dict[str, RepoConfig] = {}
    for repo_key, repo_data in repos_raw.items():
        repo_name = str(repo_key)
        if allowed_orgs:
            org = repo_name.split("/", maxsplit=1)[0]
            if org not in allowed_orgs:
                raise ValueError(
                    f"Config file '{path}': repo '{repo_name}' belongs to org '{org}', "
                    f"which is not in allowed_orgs: {allowed_orgs}."
                )
        repos[repo_name] = _parse_repo_config(path, repo_name, repo_data)
    return repos


def load_runtime_config(path: Path, _env: Mapping[str, str]) -> RuntimeConfig:
    """Load YAML at *path*, validate against JSON Schema, and return a ``RuntimeConfig``.

    Value precedence: YAML values override code defaults.  The ``_env`` argument
    is accepted for API compatibility; this function does not read env vars.

    Optional fields not present in YAML are set to ``None``.  ``config.py``
    applies environment-variable-driven defaults for any ``None`` field.

    Args:
        path: Path to the YAML config file.  Must exist.
        _env: Environment variable mapping (accepted for API compatibility; not read).

    Returns:
        ``RuntimeConfig`` populated from the YAML file.

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError: If the YAML is malformed or does not conform to the schema.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"DevBench config file not found at '{path}'. "
            "Create it or set JUDGE_CONFIG_PATH to point to its location. "
            f"Expected schema: repos map with at least one 'org/repo' entry."
        )

    raw_text = path.read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(raw_text) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in config file '{path}': {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(
            f"Config file '{path}' must be a YAML mapping at the top level, "
            f"got {type(raw).__name__}."
        )

    # JSON Schema validation — catches unknown keys, type errors, and enum violations.
    try:
        jsonschema.validate(raw, _SCHEMA)
    except jsonschema.ValidationError as exc:
        raise ValueError(
            f"Config file '{path}' failed schema validation: {exc.message}"
        ) from exc

    allowed_orgs: list[str] = raw.get("allowed_orgs") or []
    repos = _parse_repos(path, raw.get("repos") or {}, allowed_orgs)

    # Populate TimeoutConfig from YAML timeouts block (absent keys yield None).
    timeouts_raw = raw.get("timeouts") or {}
    timeouts = TimeoutConfig(
        gh_api=timeouts_raw.get("gh_api"),
        test=timeouts_raw.get("test"),
        security_fetch=timeouts_raw.get("security_fetch"),
        llm=timeouts_raw.get("llm"),
        command=timeouts_raw.get("command"),
        executor=timeouts_raw.get("executor"),
        executor_max_turns=timeouts_raw.get("executor_max_turns"),
        orchestrator_poll_interval=timeouts_raw.get("orchestrator_poll_interval"),
        github_check=timeouts_raw.get("github_check"),
    )

    # Populate LimitConfig from YAML limits block (absent keys yield None).
    limits_raw = raw.get("limits") or {}
    limits = LimitConfig(
        alert_summary=limits_raw.get("alert_summary"),
        output_truncation=limits_raw.get("output_truncation"),
        llm_evidence_truncation=limits_raw.get("llm_evidence_truncation"),
        llm_file_context=limits_raw.get("llm_file_context"),
        llm_file_preview_chars=limits_raw.get("llm_file_preview_chars"),
    )

    return RuntimeConfig(
        repos=repos,
        timeouts=timeouts,
        limits=limits,
        allowed_orgs=allowed_orgs,
        judge_model=raw.get("judge_model") or None,
        executor_model=raw.get("executor_model") or None,
        use_bedrock=bool(raw.get("use_bedrock", False)),
        bedrock_region=raw.get("bedrock_region") or None,
        merge_strategy=raw.get("merge_strategy") or None,
        max_retries=raw.get("max_retries") or None,
    )


def get_repo_local_path(repo: str, runtime_config: RuntimeConfig, workspace_root: Path) -> Path:
    """Return the local filesystem path for *repo*.

    Resolution order:
    1. ``repos.<repo>.checkout_directory`` resolved relative to *workspace_root*.
    2. ``workspace_root / <repo-short-name>`` (the part after the ``/`` in ``org/repo``).

    Pure function — no subprocess calls, no I/O.

    Args:
        repo: Fully-qualified repository name (e.g. ``'org/repo'``).
        runtime_config: Loaded runtime configuration.
        workspace_root: Absolute path to the workspace root.

    Returns:
        Absolute path to the local checkout directory.
    """
    repo_config = runtime_config.repos.get(repo)
    if repo_config and repo_config.checkout_directory:
        return workspace_root / repo_config.checkout_directory
    short_name = repo.split("/", maxsplit=1)[1] if "/" in repo else repo
    return workspace_root / short_name


def get_configured_default_branch(repo: str, runtime_config: RuntimeConfig) -> str | None:
    """Return YAML-configured default branch for *repo*, or ``None`` if absent.

    Pure function — no subprocess calls, no I/O.

    Args:
        repo: Fully-qualified repository name (e.g. ``'org/repo'``).
        runtime_config: Loaded runtime configuration.

    Returns:
        The configured ``default_branch`` string, or ``None`` when the repo
        is not in the config or has no ``default_branch`` set.
    """
    repo_config = runtime_config.repos.get(repo)
    if repo_config and repo_config.default_branch:
        return repo_config.default_branch
    return None
