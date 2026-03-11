"""YAML configuration loader with deterministic path and value precedence.

Config file path precedence (first match wins):
1. ``explicit_path`` argument passed to ``resolve_config_path``
2. ``JUDGE_CONFIG_PATH`` environment variable
3. Default path: ``<WORKSPACE_ROOT>/backlog/config/devbench.yaml``

Config value precedence:
- Environment variables override YAML values; YAML values override code defaults.

YAML schema::

    repos:                         # required — at least one entry
      org/repo:                    # key must be "org/repo" format
        default_branch: main2      # optional — omit to fall back to origin/HEAD

Example config file (``backlog/config/devbench.yaml``)::

    repos:
      caylent-solutions/devbench:
        default_branch: main2
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Relative path from WORKSPACE_ROOT to the default config file location.
DEFAULT_CONFIG_SUBPATH: str = "backlog/config/devbench.yaml"


@dataclass
class RepoConfig:
    """Per-repository configuration.

    Attributes:
        default_branch: Explicit default branch to use for this repo.
            When ``None``, branch consumers fall back to ``origin/HEAD``.
    """

    default_branch: str | None = None


@dataclass
class RuntimeConfig:
    """Merged runtime configuration loaded from the YAML config file.

    Attributes:
        repos: Mapping of fully-qualified ``org/repo`` names to their
            per-repository configuration.
    """

    repos: dict[str, RepoConfig] = field(default_factory=dict)


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


def load_runtime_config(path: Path, env: Mapping[str, str]) -> RuntimeConfig:  # noqa: ARG001
    """Load YAML at *path*, validate the schema, and return a ``RuntimeConfig``.

    Value precedence: environment variables > YAML values > code defaults.
    The ``env`` argument is accepted for future per-key overrides; current
    implementation uses it for schema validation context only.

    Args:
        path: Path to the YAML config file.  Must exist.
        env: Environment variable mapping (typically ``os.environ``).

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

    repos_raw = raw.get("repos")
    if not repos_raw or not isinstance(repos_raw, dict):
        raise ValueError(
            f"Config file '{path}' must contain a 'repos' mapping with at least one "
            "entry. Example:\n  repos:\n    org/repo:\n      default_branch: main"
        )

    repos: dict[str, RepoConfig] = {}
    for _repo_key, repo_data in repos_raw.items():
        repo_name = str(_repo_key)
        if "/" not in repo_name:
            raise ValueError(
                f"Config file '{path}': repo key '{repo_name}' must be in "
                "'org/repo' format (e.g. 'caylent-solutions/devbench')."
            )
        default_branch: str | None = None
        if isinstance(repo_data, dict):
            raw_branch = repo_data.get("default_branch")
            if raw_branch is not None:
                if not isinstance(raw_branch, str):
                    raise ValueError(
                        f"Config file '{path}': repos.{repo_name}.default_branch "
                        f"must be a string, got {type(raw_branch).__name__}."
                    )
                default_branch = raw_branch
        repos[repo_name] = RepoConfig(default_branch=default_branch)

    return RuntimeConfig(repos=repos)


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
