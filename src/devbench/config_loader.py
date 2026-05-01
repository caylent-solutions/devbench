"""YAML configuration loader with deterministic path and value precedence.

Config file path precedence (first match wins):
1. ``explicit_path`` argument passed to ``resolve_config_path``
2. ``JUDGE_CONFIG_PATH`` environment variable
3. Default path: ``<WORKSPACE_ROOT>/backlog/config/devbench.yaml``

Config value precedence:
- YAML values override code defaults.  Environment variable overrides are applied
  by ``config.py``, not by this module.

This module is parse/validate only -- it does not read environment variables.
All env-var-driven defaults for operational parameters (timeouts, limits, model
identifiers, region) are applied by ``config.py``.  Optional fields in the
dataclasses default to ``None``; callers are responsible for substituting
environment-driven values when ``None`` is encountered.

YAML schema::

    repos:                               # required -- at least one entry
      org/repo:                          # key must be "org/repo" format
        default_branch: main2            # optional -- omit to fall back to origin/HEAD
        checkout_directory: my-checkout  # optional -- relative to JUDGE_WORKSPACE_ROOT
        merge_strategy: squash           # optional -- overrides top-level merge_strategy

    merge_strategy: squash               # optional -- default merge strategy for all repos
    max_executor_retries: <integer>      # optional -- max executor retries per work unit on judge failure
    use_bedrock: false                   # optional -- route LLM calls via AWS Bedrock
    bedrock_region: <aws-region-string>  # optional -- AWS region for Bedrock (env var override applied by config.py)
    judge_model: <model-id>              # optional -- model for judge agents (env var override applied by config.py)
    executor_model: <model-id>           # optional -- model for executor agent (env var override applied by config.py)
    allowed_orgs:                        # optional -- permitted GitHub organisations
      - caylent-solutions

    timeouts:                            # optional -- all values in seconds; env var overrides applied by config.py
      gh_api: <integer>
      test: <integer>
      security_fetch: <integer>
      llm: <integer>
      command: <integer>
      executor: <integer>
      executor_max_turns: <integer>
      orchestrator_poll_interval: <integer>
      github_check: <integer>

    limits:                              # optional -- threshold values; env var overrides applied by config.py
      alert_summary: <integer>
      output_truncation: <integer>
      llm_evidence_truncation: <integer>
      llm_file_context: <integer>
      llm_file_preview_chars: <integer>

    git_ops:                             # optional -- git workflow settings
      update_submodule: false            # set true only when repos are git submodules of a parent repo

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

from devbench.constants import (
    ALL_REQUIRED_JUDGE_NAMES,
    DEFAULT_STOP_HOOK_MAX_BLOCKS,
    DEFAULT_STOP_HOOK_STALE_TASK_MINUTES,
    DEFAULT_STOP_HOOK_WINDOW_SECONDS,
    DEFAULT_TOKEN_COST_PER_M_INPUT,
    DEFAULT_TOKEN_COST_PER_M_OUTPUT,
)


def _load_per_judge_retries(raw_value: object) -> dict[str, int]:
    """Validate and return the per-judge retry budget map (issue #122).

    The schema's ``additionalProperties: false`` already rejects unknown
    judge names at the JSONSchema layer, but we re-validate at runtime to
    fail fast with a clear actionable error if the schema layer drifts or
    if a future config flow bypasses validation. Returns an empty dict if
    the YAML field is absent.
    """
    if raw_value is None:
        return {}
    if not isinstance(raw_value, dict):
        raise ValueError(
            f"max_executor_retries_per_judge must be a mapping (judge_name -> int); got {type(raw_value).__name__}."
        )
    result: dict[str, int] = {}
    for key, value in raw_value.items():
        if key not in ALL_REQUIRED_JUDGE_NAMES:
            allowed = ", ".join(sorted(ALL_REQUIRED_JUDGE_NAMES))
            raise ValueError(f"max_executor_retries_per_judge: unknown judge {key!r}. Allowed names: {allowed}.")
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError(f"max_executor_retries_per_judge[{key!r}] must be a positive integer; got {value!r}.")
        result[key] = value
    return result


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
    ci_failure_log_bytes: int | None = None


@dataclass
class PrReviewResolutionConfig:
    """PR review-comment polling configuration (issue #116).

    Defaults to disabled. Operators turn it on per-backlog when target
    repos have asynchronous review bots (Copilot, Q-Dev, internal
    review services) whose comments arrive on a separate timeline from
    the formal CI status checks.

    Attributes:
        enabled: Top-level toggle. When ``False`` (default), the entire
            phase is a no-op and ``cmd_git_ops`` proceeds straight from
            CI-pass to merge.
        agents: GitHub login allowlist whose unresolved review comments
            block the merge until resolved. Empty by default.
        decision_blocks: When ``True`` (default), reviewDecision ==
            CHANGES_REQUESTED hard-blocks the merge regardless of the
            bot allowlist.
        settle_seconds: Total settle-window length in seconds.
        poll_interval: Per-poll cadence in seconds inside the settle
            window.

    All fields default to ``None`` when not specified in YAML;
    ``config.py`` substitutes the constants.py defaults.
    """

    enabled: bool | None = None
    agents: list[str] = field(default_factory=list)
    decision_blocks: bool | None = None
    settle_seconds: int | None = None
    poll_interval: int | None = None


@dataclass
class GitOpsConfig:
    """Git operations workflow settings.

    Attributes:
        update_submodule: When ``True``, update the parent repo's submodule
            reference after each PR merge.  Set to ``True`` only when target
            repos are git submodules of a parent workspace repo.  Defaults
            to ``False`` (opt-in).
        single_branch: When set, all work units use this branch name instead
            of per-unit ``backlog/<id>`` branches.  Enables accumulating
            multiple commits on one branch for a single PR.  Defaults to
            ``None`` (per-unit branches).
        defer_pr: When ``True``, ``git-ops`` commits and stages only --
            it does not push, create a PR, or merge.  Use
            ``git-ops-finalize`` to push and create the PR after all work
            units are complete.  Only meaningful when ``single_branch``
            is set.  Defaults to ``False``.
        pause_before_merge: Issue #101 -- when ``True``, ``cmd_git_ops``
            pushes the PR + waits for green CI, then transitions the
            work unit to ``in-review`` instead of merging. The
            orchestrator's loop reconciles ``in-review`` tasks via
            ``cmd_check_merge`` on the next iteration. Mutually
            exclusive with ``defer_pr: true`` and ``single_branch: <name>``
            (validated at config load).
        inline_orphan_cleanup: When ``True`` (the default), ``cmd_git_ops``
            runs ``cleanup_tracked_orphans`` inline as a chore commit
            before the task's commit when build/state orphan paths are
            detected. ``None`` falls through to the constant default.
        ci_failure_retry: Issue #115 -- when ``True`` (the default),
            ``cmd_git_ops`` returns rc=2 on CI failure to trigger an
            executor retry with the failing-job log as feedback. ``None``
            falls through to the constant default.
        orphan_patterns: Operator override of the built-in orphan-pattern
            fnmatch list. Empty list (default) means use the built-in
            list; non-empty REPLACES it.
        pr_review_resolution: Nested config for the PR review-comment
            polling phase (issue #116).
    """

    update_submodule: bool = False
    single_branch: str | None = None
    defer_pr: bool = False
    pause_before_merge: bool | None = None
    inline_orphan_cleanup: bool | None = None
    ci_failure_retry: bool | None = None
    orphan_patterns: list[str] = field(default_factory=list)
    pr_review_resolution: PrReviewResolutionConfig = field(default_factory=PrReviewResolutionConfig)


@dataclass
class DebugConfig:
    """Diagnostic-tuning knobs.

    Set these only when investigating an orchestrator-cadence problem.
    Production workspaces leave this section absent.

    Attributes:
        check_registration_retries: Issue #114 -- number of times
            ``wait_for_checks`` retries ``gh pr checks`` when "no checks
            reported" contradicts the local workflow-file glob.
        check_registration_delay_seconds: Sleep between check-registration
            retries, in seconds.
        blocked_recovery_window_seconds: Recency cap for the
            AWAITING_AUTO_RECOVERY signal in the 3-state blocked-task
            classifier.

    All fields default to ``None`` when not specified in YAML;
    ``config.py`` substitutes the constants.py defaults.
    """

    check_registration_retries: int | None = None
    check_registration_delay_seconds: int | None = None
    blocked_recovery_window_seconds: int | None = None


@dataclass
class ReportConfig:
    """Report and cost estimation settings.

    Attributes:
        token_cost_per_million_input: Cost per million input tokens in USD.
        token_cost_per_million_output: Cost per million output tokens in USD.
        display_timezone: IANA timezone name for displaying report timestamps.
            ``None`` means use the host's system local timezone.
        cache_read_multiplier: Cost multiplier for cache-read tokens, relative
            to the base input rate. ``None`` means use the constant default.
        cache_write_5min_multiplier: Cost multiplier for 5-minute prompt-cache
            write tokens, relative to the base input rate.
        cache_write_1hr_multiplier: Cost multiplier for 1-hour prompt-cache
            write tokens, relative to the base input rate.
        data_residency_multiplier: Cost multiplier when usage.inference_geo
            is set (US-only inference). Applied per-call (issue #124).
        fast_mode_multiplier: Cost multiplier when usage.speed == 'fast'
            (Opus 4.6 fast-mode premium). Applied per-call (issue #124).
        recent_pace_tasks: Number of most recently completed tasks to average
            for the "Recent pace" projection. ``None`` falls back to
            ``DEFAULT_RECENT_PACE_TASKS``.
        token_cost_discount: Contract discount (correction factor) off
            list-price token cost, as a fraction in ``[0.0, 1.0]``.
            ``final_cost = raw_list_cost * (1 - token_cost_discount)``.
            ``None`` falls back to ``DEFAULT_TOKEN_COST_DISCOUNT`` (``0.0``,
            pay full list).
    """

    token_cost_per_million_input: float = DEFAULT_TOKEN_COST_PER_M_INPUT
    token_cost_per_million_output: float = DEFAULT_TOKEN_COST_PER_M_OUTPUT
    display_timezone: str | None = None
    cache_read_multiplier: float | None = None
    cache_write_5min_multiplier: float | None = None
    cache_write_1hr_multiplier: float | None = None
    data_residency_multiplier: float | None = None
    fast_mode_multiplier: float | None = None
    recent_pace_tasks: int | None = None
    token_cost_discount: float | None = None


@dataclass(frozen=True)
class TaskFactoryConfig:
    """Per-backlog task-factory configuration.

    Controls whether the orchestrator invokes blocker-resolver + task-factory
    after an amendment reject to generate `proposed` work units for the
    out-of-scope production fixes the amender surfaced.

    Attributes:
        enabled: Whether the task-factory loop runs. Defaults to ``False``
            so existing backlogs see no behavior change. Requires
            ``manifest_amendment.enabled: true`` (task-factory runs from
            the amendment-reject path).
        auto_accept_proposals: When ``True``, ``devbench sweep-proposals``
            auto-promotes every task-factory-produced draft to ``in-queue``
            immediately, skipping the human review step. Default ``False``
            preserves pre-ADR-11 behaviour (drafts land at ``proposed``
            and wait for the operator). See ADR-11.
    """

    enabled: bool = False
    auto_accept_proposals: bool = False


@dataclass(frozen=True)
class AmendmentConfig:
    """Per-backlog Changes Manifest amendment workflow configuration.

    Loaded from the ``manifest_amendment`` YAML section (opt-in, defaults off).
    Consumed by the Layer 1 PreFilter in ``devbench.backlog.amendment``.

    Attributes:
        enabled: Whether the amendment workflow is active for this backlog.
            Default ``False`` -- backlogs must explicitly opt in.
        allowed_reasons: Set of amendment reasons this backlog accepts.
            Requests whose reason is not in this set are rejected by the
            pre-filter.
        max_requests_per_execution: Upper bound on amendments applied to a
            single task during one executor run; prevents amendment loops.
    """

    enabled: bool = False
    allowed_reasons: frozenset[str] = field(default_factory=lambda: frozenset({"tdd_green_production_fix"}))
    max_requests_per_execution: int = 1


@dataclass
class StopHookConfig:
    """Stop hook circuit breaker settings.

    Attributes:
        max_blocks: Maximum consecutive stop-hook blocks before circuit breaker trips.
        window_seconds: Time window for counting blocks. Counter resets after this period.
        stale_task_minutes: Minutes before an in-progress task is considered stale.
    """

    max_blocks: int = DEFAULT_STOP_HOOK_MAX_BLOCKS
    window_seconds: int = DEFAULT_STOP_HOOK_WINDOW_SECONDS
    stale_task_minutes: int = DEFAULT_STOP_HOOK_STALE_TASK_MINUTES


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
        resolved_checkout_path: Absolute filesystem path to the repo
            checkout, populated by ``load_runtime_config``. Equal to
            ``<JUDGE_WORKSPACE_ROOT>/<checkout_directory or repo_short_name>``
            after resolution. Consumers MUST read this field instead of
            re-resolving the path inline (E213).
        validated_repo: Canonical ``org/repo`` form for this entry,
            populated by ``load_runtime_config`` from the YAML repos map
            key. Stored verbatim so consumers do not re-validate the
            shape per-call.
    """

    default_branch: str | None = None
    checkout_directory: str | None = None
    merge_strategy: str | None = None
    resolved_checkout_path: Path | None = None
    validated_repo: str | None = None


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
        git_ops: Git operations workflow settings.
        report: Report and cost estimation settings.
        stop_hook: Stop hook circuit breaker settings.
        allowed_orgs: List of permitted GitHub organisations.
        judge_model: Model identifier used by judge agents.
        executor_model: Model identifier used by the executor agent.
        use_bedrock: Whether to route LLM calls through AWS Bedrock.
        bedrock_region: AWS region for Bedrock API calls.
        merge_strategy: Default PR merge strategy for all repos.
        max_executor_retries: Maximum executor retry attempts per work unit
            when judge reviews fail.
        display_timezone: IANA timezone name applied by every devbench
            command that renders timestamps (report, hook-tail, watch).
            ``None`` means OS local timezone. Per-command overrides
            (env vars, CLI flags, or the legacy ``report.display_timezone``)
            take precedence over this top-level setting.
        log_file: Workspace-relative path to the orchestrator's
            structured log file. ``setup_logging`` (the writer) and
            ``cmd_report`` (the reader) both consult this single source
            of truth so they cannot diverge by accident; in earlier
            versions the two were both env-var-driven and could be
            split silently when an operator set ``JUDGE_LOG_FILE`` to
            different values in different shells. ``None`` (the
            default) means callers must supply ``JUDGE_LOG_FILE``
            explicitly or rely on the workspace-local convention
            ``logs/orchestrator.log``.
    """

    repos: dict[str, RepoConfig] = field(default_factory=dict)
    timeouts: TimeoutConfig = field(default_factory=TimeoutConfig)
    limits: LimitConfig = field(default_factory=LimitConfig)
    git_ops: GitOpsConfig = field(default_factory=GitOpsConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    stop_hook: StopHookConfig = field(default_factory=StopHookConfig)
    manifest_amendment: AmendmentConfig = field(default_factory=AmendmentConfig)
    task_factory: TaskFactoryConfig = field(default_factory=TaskFactoryConfig)
    debug: DebugConfig = field(default_factory=DebugConfig)
    allowed_orgs: list[str] = field(default_factory=list)
    judge_model: str | None = None
    executor_model: str | None = None
    use_bedrock: bool = False
    bedrock_region: str | None = None
    merge_strategy: str | None = None
    max_executor_retries: int | None = None
    max_executor_retries_per_judge: dict[str, int] = field(default_factory=dict)
    display_timezone: str | None = None
    log_file: str | None = None


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
        Resolved config file path.  The path may not exist on disk -- callers
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
    path: Path,
    repos_raw: dict,
    allowed_orgs: list[str],
    workspace_root: Path | None = None,
) -> dict[str, RepoConfig]:
    """Build the repos mapping from the raw YAML ``repos`` block.

    When *allowed_orgs* is non-empty, every repo key's organisation component
    must appear in *allowed_orgs*.

    When *workspace_root* is provided (the normal case from
    ``load_runtime_config``), each ``RepoConfig.resolved_checkout_path``
    is populated to ``<workspace_root>/<checkout_directory or repo_short_name>``
    so consumers do not re-resolve the path inline (E213). When it is
    ``None`` the field stays ``None`` -- callers that operate without a
    workspace root (some tests) must tolerate that absence.

    Args:
        path: Config file path (used in error messages).
        repos_raw: Raw ``repos`` dict from YAML (already schema-validated).
        allowed_orgs: Permitted GitHub organisations.  Empty list means any org.
        workspace_root: Absolute path to ``JUDGE_WORKSPACE_ROOT`` for
            populating ``resolved_checkout_path``.

    Returns:
        Mapping of ``org/repo`` → ``RepoConfig`` with ``validated_repo``
        and (when *workspace_root* is set) ``resolved_checkout_path``
        populated.

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
        cfg = _parse_repo_config(path, repo_name, repo_data)
        cfg.validated_repo = repo_name
        if workspace_root is not None:
            checkout_dir = cfg.checkout_directory or repo_name.split("/", maxsplit=1)[-1]
            cfg.resolved_checkout_path = workspace_root / checkout_dir
        repos[repo_name] = cfg
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
        raise ValueError(f"Config file '{path}' must be a YAML mapping at the top level, got {type(raw).__name__}.")

    # JSON Schema validation -- catches unknown keys, type errors, and enum violations.
    try:
        jsonschema.validate(raw, _SCHEMA)
    except jsonschema.ValidationError as exc:
        raise ValueError(f"Config file '{path}' failed schema validation: {exc.message}") from exc

    allowed_orgs: list[str] = raw.get("allowed_orgs") or []
    workspace_root_raw = _env.get("JUDGE_WORKSPACE_ROOT", "")
    workspace_root = Path(workspace_root_raw) if workspace_root_raw else None
    repos = _parse_repos(path, raw.get("repos") or {}, allowed_orgs, workspace_root)

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
        ci_failure_log_bytes=limits_raw.get("ci_failure_log_bytes"),
    )

    # Populate GitOpsConfig from YAML git_ops block (absent keys yield defaults).
    git_ops_raw = raw.get("git_ops") or {}
    single_branch_raw = git_ops_raw.get("single_branch") or None
    defer_pr = bool(git_ops_raw.get("defer_pr", False))
    pause_before_merge_raw = git_ops_raw.get("pause_before_merge")
    pause_before_merge = bool(pause_before_merge_raw) if pause_before_merge_raw is not None else None
    if defer_pr and not single_branch_raw:
        raise ValueError(f"Config file '{path}': git_ops.defer_pr requires git_ops.single_branch to be set.")
    if pause_before_merge and defer_pr:
        raise ValueError(
            f"Config file '{path}': git_ops.pause_before_merge: true is incompatible with "
            "git_ops.defer_pr: true. defer_pr defers PR creation; pause_before_merge pauses "
            "after PR creation. They are mutually exclusive."
        )
    if pause_before_merge and single_branch_raw:
        raise ValueError(
            f"Config file '{path}': git_ops.pause_before_merge: true is incompatible with "
            f"git_ops.single_branch: {single_branch_raw!r}. Single-branch mode puts every "
            "work unit's commits on one branch; there is no per-unit branch to create a PR from."
        )
    pr_resolution_raw = git_ops_raw.get("pr_review_resolution") or {}
    pr_resolution_enabled_raw = pr_resolution_raw.get("enabled")
    pr_resolution_decision_raw = pr_resolution_raw.get("decision_blocks")
    pr_review_resolution = PrReviewResolutionConfig(
        enabled=bool(pr_resolution_enabled_raw) if pr_resolution_enabled_raw is not None else None,
        agents=list(pr_resolution_raw.get("agents") or []),
        decision_blocks=bool(pr_resolution_decision_raw) if pr_resolution_decision_raw is not None else None,
        settle_seconds=pr_resolution_raw.get("settle_seconds"),
        poll_interval=pr_resolution_raw.get("poll_interval"),
    )
    inline_cleanup_raw = git_ops_raw.get("inline_orphan_cleanup")
    ci_failure_retry_raw = git_ops_raw.get("ci_failure_retry")
    git_ops = GitOpsConfig(
        update_submodule=bool(git_ops_raw.get("update_submodule", False)),
        single_branch=single_branch_raw,
        defer_pr=defer_pr,
        pause_before_merge=pause_before_merge,
        inline_orphan_cleanup=bool(inline_cleanup_raw) if inline_cleanup_raw is not None else None,
        ci_failure_retry=bool(ci_failure_retry_raw) if ci_failure_retry_raw is not None else None,
        orphan_patterns=list(git_ops_raw.get("orphan_patterns") or []),
        pr_review_resolution=pr_review_resolution,
    )

    # Populate DebugConfig from YAML debug block (absent keys yield None).
    debug_raw = raw.get("debug") or {}
    debug = DebugConfig(
        check_registration_retries=debug_raw.get("check_registration_retries"),
        check_registration_delay_seconds=debug_raw.get("check_registration_delay_seconds"),
        blocked_recovery_window_seconds=debug_raw.get("blocked_recovery_window_seconds"),
    )

    # Populate ReportConfig from YAML report block.
    report_raw = raw.get("report") or {}
    report = ReportConfig(
        token_cost_per_million_input=float(
            report_raw.get("token_cost_per_million_input", DEFAULT_TOKEN_COST_PER_M_INPUT),
        ),
        token_cost_per_million_output=float(
            report_raw.get("token_cost_per_million_output", DEFAULT_TOKEN_COST_PER_M_OUTPUT),
        ),
        display_timezone=report_raw.get("display_timezone") or None,
        cache_read_multiplier=(
            float(report_raw["cache_read_multiplier"]) if "cache_read_multiplier" in report_raw else None
        ),
        cache_write_5min_multiplier=(
            float(report_raw["cache_write_5min_multiplier"]) if "cache_write_5min_multiplier" in report_raw else None
        ),
        cache_write_1hr_multiplier=(
            float(report_raw["cache_write_1hr_multiplier"]) if "cache_write_1hr_multiplier" in report_raw else None
        ),
        data_residency_multiplier=(
            float(report_raw["data_residency_multiplier"]) if "data_residency_multiplier" in report_raw else None
        ),
        fast_mode_multiplier=(
            float(report_raw["fast_mode_multiplier"]) if "fast_mode_multiplier" in report_raw else None
        ),
        recent_pace_tasks=(int(report_raw["recent_pace_tasks"]) if "recent_pace_tasks" in report_raw else None),
        token_cost_discount=(float(report_raw["token_cost_discount"]) if "token_cost_discount" in report_raw else None),
    )

    # Populate ManifestAmendment config from YAML manifest_amendment block.
    amendment_raw = raw.get("manifest_amendment") or {}
    default_amendment = AmendmentConfig()
    manifest_amendment = AmendmentConfig(
        enabled=bool(amendment_raw.get("enabled", default_amendment.enabled)),
        allowed_reasons=(
            frozenset(amendment_raw["allowed_reasons"])
            if "allowed_reasons" in amendment_raw
            else default_amendment.allowed_reasons
        ),
        max_requests_per_execution=int(
            amendment_raw.get("max_requests_per_execution", default_amendment.max_requests_per_execution)
        ),
    )

    # Populate TaskFactory config from YAML task_factory block. Requires
    # manifest_amendment.enabled when task_factory.enabled is true -- the
    # loop runs after an amendment reject, so it has nothing to do when the
    # amendment workflow itself is off.
    task_factory_raw = raw.get("task_factory") or {}
    default_task_factory = TaskFactoryConfig()
    task_factory = TaskFactoryConfig(
        enabled=bool(task_factory_raw.get("enabled", default_task_factory.enabled)),
        auto_accept_proposals=bool(
            task_factory_raw.get("auto_accept_proposals", default_task_factory.auto_accept_proposals)
        ),
    )
    if task_factory.enabled and not manifest_amendment.enabled:
        raise ValueError(
            f"Config file '{path}': task_factory.enabled: true requires manifest_amendment.enabled: true. "
            "Task-factory runs from the amendment-reject path; it has nothing to do when amendments are off."
        )

    # Populate StopHookConfig from YAML stop_hook block.
    stop_hook_raw = raw.get("stop_hook") or {}
    stop_hook = StopHookConfig(
        max_blocks=int(
            stop_hook_raw.get("max_blocks", DEFAULT_STOP_HOOK_MAX_BLOCKS),
        ),
        window_seconds=int(
            stop_hook_raw.get("window_seconds", DEFAULT_STOP_HOOK_WINDOW_SECONDS),
        ),
        stale_task_minutes=int(
            stop_hook_raw.get("stale_task_minutes", DEFAULT_STOP_HOOK_STALE_TASK_MINUTES),
        ),
    )

    return RuntimeConfig(
        repos=repos,
        timeouts=timeouts,
        limits=limits,
        git_ops=git_ops,
        report=report,
        stop_hook=stop_hook,
        manifest_amendment=manifest_amendment,
        task_factory=task_factory,
        debug=debug,
        allowed_orgs=allowed_orgs,
        judge_model=raw.get("judge_model") or None,
        executor_model=raw.get("executor_model") or None,
        use_bedrock=bool(raw.get("use_bedrock", False)),
        bedrock_region=raw.get("bedrock_region") or None,
        merge_strategy=raw.get("merge_strategy") or None,
        max_executor_retries=raw.get("max_executor_retries") or None,
        max_executor_retries_per_judge=_load_per_judge_retries(raw.get("max_executor_retries_per_judge")),
        display_timezone=raw.get("display_timezone") or None,
        log_file=raw.get("log_file") or None,
    )


def get_repo_local_path(repo: str, runtime_config: RuntimeConfig, workspace_root: Path) -> Path:
    """Return the local filesystem path for *repo*.

    Resolution order:
    1. ``RepoConfig.resolved_checkout_path`` populated by the loader (E213).
    2. ``repos.<repo>.checkout_directory`` resolved relative to *workspace_root*.
    3. ``workspace_root / <repo-short-name>`` (the part after the ``/`` in ``org/repo``).

    Pure function -- no subprocess calls, no I/O.

    Args:
        repo: Fully-qualified repository name (e.g. ``'org/repo'``).
        runtime_config: Loaded runtime configuration.
        workspace_root: Absolute path to the workspace root.

    Returns:
        Absolute path to the local checkout directory.
    """
    repo_config = runtime_config.repos.get(repo)
    if repo_config and repo_config.resolved_checkout_path is not None:
        return repo_config.resolved_checkout_path
    if repo_config and repo_config.checkout_directory:
        return workspace_root / repo_config.checkout_directory
    short_name = repo.split("/", maxsplit=1)[1] if "/" in repo else repo
    return workspace_root / short_name


def get_configured_default_branch(repo: str, runtime_config: RuntimeConfig) -> str | None:
    """Return YAML-configured default branch for *repo*, or ``None`` if absent.

    Pure function -- no subprocess calls, no I/O.

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
