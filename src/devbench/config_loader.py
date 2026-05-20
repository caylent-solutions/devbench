"""YAML configuration loader with deterministic path and value precedence.

Config file path precedence (first match wins):
1. ``explicit_path`` argument passed to ``resolve_config_path``
2. ``DEVBENCH_CONFIG_PATH`` environment variable
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
        checkout_directory: my-checkout  # optional -- relative to DEVBENCH_WORKSPACE_ROOT
        merge_strategy: squash           # optional -- overrides top-level merge_strategy

    merge_strategy: squash               # optional -- default merge strategy for all repos
    max_executor_retries: <integer>      # optional -- max executor retries per work unit on judge failure
    use_bedrock: false                   # optional -- route LLM calls via AWS Bedrock
    bedrock_region: <aws-region-string>  # optional -- AWS region for Bedrock (env var override applied by config.py)
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

    backlog:                             # optional -- backlog lifecycle settings (issue #189, #194)
      default_status_for_new_work_units: in-queue  # 'draft' or 'in-queue' (default 'in-queue')
      bulk_update_confirm_threshold: 10  # optional -- prompt threshold for bulk set-status (default 10, AC-194-4)
      bulk_update_audit_path: logs/bulk-updates.log  # optional -- audit log path for bulk updates (AC-194-7)

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
    ALLOWED_AGENT_MODEL_SHORT_NAMES,
    ANTHROPIC_AGENT_MODEL_PATTERN,
    BEDROCK_AGENT_MODEL_PATTERN,
    DEFAULT_STOP_HOOK_MAX_BLOCKS,
    DEFAULT_STOP_HOOK_STALE_TASK_MINUTES,
    DEFAULT_STOP_HOOK_WINDOW_SECONDS,
    DEFAULT_TOKEN_COST_PER_M_INPUT,
    DEFAULT_TOKEN_COST_PER_M_OUTPUT,
    QUOTA_HANDLING_DEFAULT_AUDIT_COMMENT_ON_RESUME,
    QUOTA_HANDLING_DEFAULT_AUDIT_COMMENT_ON_WAIT,
    QUOTA_HANDLING_DEFAULT_BACKOFF_INITIAL_SECONDS,
    QUOTA_HANDLING_DEFAULT_BACKOFF_JITTER,
    QUOTA_HANDLING_DEFAULT_BACKOFF_MAX_SECONDS,
    QUOTA_HANDLING_DEFAULT_BACKOFF_MULTIPLIER,
    QUOTA_HANDLING_DEFAULT_DETECT_MODES,
    QUOTA_HANDLING_DEFAULT_ENABLED,
    QUOTA_HANDLING_DEFAULT_LOG_STRUCTURED_EVENTS,
    QUOTA_HANDLING_DEFAULT_MAX_WAIT_SECONDS,
    QUOTA_HANDLING_DEFAULT_ON_EXHAUSTION,
    QUOTA_HANDLING_DEFAULT_ON_EXHAUSTION_TIMEOUT,
    QUOTA_HANDLING_DEFAULT_POLL_INTERVAL_SECONDS,
    QUOTA_HANDLING_DEFAULT_RECOVERY_PROBE_ENABLED,
    QUOTA_HANDLING_DEFAULT_RECOVERY_PROBE_REQUEST_SIZE_TOKENS,
    QUOTA_HANDLING_DEFAULT_RECOVERY_PROBE_TIMEOUT_SECONDS,
    QUOTA_HANDLING_DEFAULT_RESUME_STRATEGY,
    STATUS_DRAFT,
    STATUS_IN_QUEUE,
)

_BACKLOG_DEFAULT_STATUS: str = STATUS_IN_QUEUE
_VALID_DEFAULT_STATUSES: frozenset[str] = frozenset({STATUS_IN_QUEUE, STATUS_DRAFT})
_BACKLOG_DEFAULT_BULK_UPDATE_CONFIRM_THRESHOLD: int = 10
_BACKLOG_DEFAULT_BULK_UPDATE_AUDIT_PATH: str = "logs/bulk-updates.log"

# ---------------------------------------------------------------------------
# Audit-row string constants for auto_finalize / auto_merge skill steps.
# Pinned here so SKILL.md prose and tests reference the same literals.
# ---------------------------------------------------------------------------
AUTO_FINALIZE_SKIPPED_LOCAL_ONLY: str = "[AUTO_FINALIZE_SKIPPED] local_only=true"
AUTO_MERGE_SKIPPED_NO_CI_WATCHER: str = "[AUTO_MERGE_SKIPPED] no_ci_watcher"
BATCH_PR_CREATED_AUDIT_PREFIX: str = "[BATCH_PR_CREATED]"
BATCH_PR_MERGED_AUDIT_PREFIX: str = "[BATCH_PR_MERGED]"


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
        local_only: When ``True``, the target repo(s) are treated as
            local-only -- they have no ``origin`` git remote, are never
            pushed, never produce PRs, and never run CI. ``ensure_branch``
            creates the work-unit branch off the local default branch
            (no ``git fetch origin``). Requires ``defer_pr: true``,
            forbids ``pause_before_merge: true``, and requires every
            entry in ``repos:`` to set an explicit ``default_branch:``
            (no ``origin/HEAD`` fallback). Defaults to ``False``.
        auto_finalize: When ``True``, the orchestrate skill automatically
            invokes ``devbench git-ops-finalize <repo>`` once all work
            units for the repo are terminal. Requires ``defer_pr: true``.
            Incompatible with ``local_only: true``. A marker file at
            ``<workspace>/.devbench/auto-finalize-fired-<repo>.marker``
            prevents duplicate invocations. Defaults to ``False``.
        auto_merge: When ``True``, the orchestrate skill automatically
            invokes ``gh pr merge --<merge_strategy>`` once the post-E7
            CI watcher reports GREEN. Requires ``auto_finalize: true``
            AND ``defer_pr: true``. Incompatible with ``local_only: true``.
            When the E7 watcher is absent, emits
            ``[AUTO_MERGE_SKIPPED] no_ci_watcher`` and skips. A marker
            file at ``<workspace>/.devbench/auto-merge-fired-<repo>.marker``
            prevents duplicate invocations. Defaults to ``False``.
    """

    update_submodule: bool = False
    single_branch: str | None = None
    defer_pr: bool = False
    pause_before_merge: bool | None = None
    inline_orphan_cleanup: bool | None = None
    ci_failure_retry: bool | None = None
    orphan_patterns: list[str] = field(default_factory=list)
    pr_review_resolution: PrReviewResolutionConfig = field(default_factory=PrReviewResolutionConfig)
    local_only: bool = False
    auto_finalize: bool = False
    auto_merge: bool = False


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
class ValidateConfig:
    """Per-backlog opt-in toggles for additional ``validate-backlog`` rules.

    Existing rules (1-19) run unconditionally. Rules added here are gated so
    pre-existing backlogs see no behaviour change until they explicitly opt
    in. See ``docs/backlog-contract.md`` for the full rule list.

    Attributes:
        check_orphan_path_tokens: Rule 20. When ``True``, validate-backlog
            scans every Task's ``## Acceptance Criteria`` and
            ``## Definition of Done`` sections for backtick-quoted
            path-shaped tokens, and emits an integrity error for any token
            that does not appear in the Task's ``## Changes Manifest``
            (after path normalisation). A token followed by ``(ref)`` is
            treated as a declared read-only reference and skipped. Catches
            spec drift where AC/DoD prose restates a path that disagrees
            with the Manifest. Default ``False`` (opt-in).
    """

    check_orphan_path_tokens: bool = False


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
class HookTailConfig:
    """``devbench hook-tail`` column-cap settings (issue #134).

    Each field is ``None`` when absent from YAML; ``config.py`` resolves
    env > YAML > default for the four module-level ``HOOK_TAIL_*``
    constants. ``EVENT_WIDTH`` is intrinsic to the arrow format and stays
    a hook_tail.py-local constant; only the four below are operator-
    tunable.

    Attributes:
        agent_width: Column width for the agent name (default 12).
        tool_width: Column width for the tool name (default 8).
        description_max: Max chars for the description column (default
            120; bumped from 100 in the release that introduces this
            block so multi-word agent descriptions are less likely to
            truncate mid-clause).
        stdout_preview_max: Max chars for the result-preview column
            after ``|`` (default 80).
    """

    agent_width: int | None = None
    tool_width: int | None = None
    description_max: int | None = None
    stdout_preview_max: int | None = None


@dataclass
class OrchestrateConfig:
    """Orchestrator runtime tuning (issue #144).

    ``max_cascade_depth`` caps the depth of recovery-of-a-recovery
    chains. When a proposal would land at depth >= this cap, the source
    task transitions to ``NEEDS_OPERATOR_ATTENTION`` instead of
    materialising another recovery layer.

    Field is ``None`` when absent from YAML; ``config.py`` resolves
    env > YAML > default for the module-level ``MAX_CASCADE_DEPTH``
    constant.
    """

    max_cascade_depth: int | None = None


@dataclass(frozen=True)
class BacklogConfig:
    """Backlog lifecycle settings loaded from the ``backlog:`` YAML section.

    Controls behaviour that applies across all work units in the backlog,
    such as what lifecycle status new work units receive on creation and
    confirmation thresholds for bulk operations.

    Attributes:
        default_status_for_new_work_units: Lifecycle status written into the
            ``## Status:`` line of every newly created work-unit file.
            Accepted values: ``STATUS_DRAFT`` (``'draft'``) or
            ``STATUS_IN_QUEUE`` (``'in-queue'``), imported from
            ``devbench.constants``. Defaults to ``STATUS_IN_QUEUE`` for
            backwards compatibility -- existing workspaces without the config
            key see no behaviour change (AC-189-9). Set to ``STATUS_DRAFT``
            (``'draft'``) to require explicit human promotion before the
            orchestrator picks up a new task (AC-189-8).
        bulk_update_confirm_threshold: Number of work units above which
            ``devbench set-status`` with selector flags prompts for
            confirmation before applying a bulk status change. Must be >= 0.
            Zero means always prompt. Defaults to 10 (AC-194-4).
        bulk_update_audit_path: Workspace-relative path to the file where
            bulk-update audit rows are appended. Each invocation of
            ``devbench set-status`` with selector flags writes one
            ``[BULK_STATUS_UPDATE]`` row. Defaults to
            ``'logs/bulk-updates.log'`` (AC-194-7).
    """

    default_status_for_new_work_units: str = _BACKLOG_DEFAULT_STATUS
    bulk_update_confirm_threshold: int = _BACKLOG_DEFAULT_BULK_UPDATE_CONFIRM_THRESHOLD
    bulk_update_audit_path: str = _BACKLOG_DEFAULT_BULK_UPDATE_AUDIT_PATH


def _parse_backlog_config(path: Path, backlog_raw: dict) -> BacklogConfig:
    """Parse and validate the ``backlog:`` YAML section into a ``BacklogConfig``.

    Args:
        path: Config file path (used in error messages).
        backlog_raw: Raw ``backlog`` dict from YAML (already schema-validated
            for unknown keys). May be an empty dict when the section is absent.

    Returns:
        ``BacklogConfig`` populated from *backlog_raw*.

    Raises:
        ValueError: If ``default_status_for_new_work_units`` is set to a
            value that is not in ``_VALID_DEFAULT_STATUSES``.
        ValueError: If ``bulk_update_confirm_threshold`` is negative.
    """
    raw_status = backlog_raw.get(
        "default_status_for_new_work_units",
        _BACKLOG_DEFAULT_STATUS,
    )
    if raw_status not in _VALID_DEFAULT_STATUSES:
        valid_sorted = ", ".join(sorted(_VALID_DEFAULT_STATUSES))
        raise ValueError(
            f"Config file '{path}': backlog.default_status_for_new_work_units "
            f"must be one of [{valid_sorted}]; got {raw_status!r}. "
            f"Use {STATUS_DRAFT!r} to require explicit promotion before execution, "
            f"or {STATUS_IN_QUEUE!r} (the default) for the legacy behaviour."
        )
    raw_threshold = backlog_raw.get(
        "bulk_update_confirm_threshold",
        _BACKLOG_DEFAULT_BULK_UPDATE_CONFIRM_THRESHOLD,
    )
    threshold = int(raw_threshold)
    if threshold < 0:
        raise ValueError(
            f"Config file '{path}': backlog.bulk_update_confirm_threshold "
            f"must be >= 0; got {threshold!r}. "
            "Set to 0 to always prompt, or a positive integer to prompt only "
            "when the expansion exceeds that count."
        )
    raw_audit_path = backlog_raw.get(
        "bulk_update_audit_path",
        _BACKLOG_DEFAULT_BULK_UPDATE_AUDIT_PATH,
    )
    return BacklogConfig(
        default_status_for_new_work_units=raw_status,
        bulk_update_confirm_threshold=threshold,
        bulk_update_audit_path=str(raw_audit_path),
    )


# ---------------------------------------------------------------------------
# QuotaHandlingConfig -- spec section 4.5.6, AC-193-19, AC-193-20
# ---------------------------------------------------------------------------


@dataclass
class QuotaBackoffConfig:
    """Exponential-backoff parameters for recovery probe retries (spec section 4.5.6).

    All fields default to the values from constants.py so the dataclass can be
    instantiated with no arguments to obtain spec-correct defaults.

    Attributes:
        initial_seconds: Starting backoff interval in seconds.
        max_seconds: Maximum backoff interval in seconds.
        multiplier: Exponent base applied after each failed probe.
        jitter: Fractional jitter (0.0 to 1.0) added/subtracted from each
            backoff duration.
    """

    initial_seconds: float = QUOTA_HANDLING_DEFAULT_BACKOFF_INITIAL_SECONDS
    max_seconds: float = QUOTA_HANDLING_DEFAULT_BACKOFF_MAX_SECONDS
    multiplier: float = QUOTA_HANDLING_DEFAULT_BACKOFF_MULTIPLIER
    jitter: float = QUOTA_HANDLING_DEFAULT_BACKOFF_JITTER


@dataclass
class QuotaRecoveryProbeConfig:
    """Recovery probe sub-configuration (spec section 4.5.6).

    Controls the minimal Anthropic API completion used to detect quota recovery.
    All fields default to spec-correct values from constants.py.

    Attributes:
        enabled: When ``True`` (default), the recovery probe is activated after
            the initial reset_at sleep.  When ``False``, only the sleep is used.
        request_size_tokens: Maximum output tokens requested in the probe call.
            Default 1 (cheapest valid response).
        timeout_seconds: Timeout in seconds for the probe HTTP request.
            Default 10.0.
        backoff: Exponential-backoff parameters for probe retries.
    """

    enabled: bool = QUOTA_HANDLING_DEFAULT_RECOVERY_PROBE_ENABLED
    request_size_tokens: int = QUOTA_HANDLING_DEFAULT_RECOVERY_PROBE_REQUEST_SIZE_TOKENS
    timeout_seconds: float = QUOTA_HANDLING_DEFAULT_RECOVERY_PROBE_TIMEOUT_SECONDS
    backoff: QuotaBackoffConfig = field(default_factory=QuotaBackoffConfig)


@dataclass
class QuotaHandlingConfig:
    """Quota-wait-and-resume configuration (spec section 4.5.6).

    All fields default to the spec-correct values from constants.py.  The
    entire ``quota_handling:`` YAML section may be omitted; in that case
    ``load_runtime_config`` constructs this dataclass with all defaults.

    Attributes:
        enabled: Top-level toggle.  When ``False``, quota detection is
            disabled entirely.
        detect_modes: Ordered list of quota-signal detection mode names.
            Each maps to a ``QuotaExhaustedError`` subclass in
            ``devbench.quota``.
        on_exhaustion: Action on first quota detection -- ``'wait'``,
            ``'fail'``, or ``'drain'``.
        poll_interval_seconds: Recovery probe poll interval in seconds
            (min 30, max 3600).
        max_wait_seconds: Maximum seconds to wait before triggering
            ``on_exhaustion_timeout`` (min 1).
        on_exhaustion_timeout: Action when ``max_wait_seconds`` is exceeded --
            ``'drain'``, ``'fail'``, or ``'keep_waiting'``.
        resume_strategy: Resume strategy after quota restoration --
            ``'continue_current_wu'``, ``'restart_wu'``, or
            ``'drain_and_resume'``.
        audit_comment_on_wait: When ``True``, writes a ``[QUOTA_WAITING]``
            audit comment to the in-flight WU at the start of a wait.
        audit_comment_on_resume: When ``True``, writes a ``[QUOTA_RESUMED]``
            audit comment after quota is restored.
        log_structured_events: When ``True``, emits structured log events for
            quota-wait lifecycle transitions.
        recovery_probe: Recovery probe sub-configuration.

    Note:
        Pause / resume **notifications** moved to the unified
        :class:`NotificationsConfig` block in PR #202.  The
        ``quota_handling.notify_on_pause`` / ``notify_on_resume`` yaml
        fields and their ``QuotaNotifyConfig`` dataclass were removed.
        Set ``notifications.events.quota_pause: true`` /
        ``notifications.events.quota_resume: true`` to receive Slack
        pings on those events.
    """

    enabled: bool = QUOTA_HANDLING_DEFAULT_ENABLED
    detect_modes: list[str] = field(default_factory=lambda: list(QUOTA_HANDLING_DEFAULT_DETECT_MODES))
    on_exhaustion: str = QUOTA_HANDLING_DEFAULT_ON_EXHAUSTION
    poll_interval_seconds: int = QUOTA_HANDLING_DEFAULT_POLL_INTERVAL_SECONDS
    max_wait_seconds: int = QUOTA_HANDLING_DEFAULT_MAX_WAIT_SECONDS
    on_exhaustion_timeout: str = QUOTA_HANDLING_DEFAULT_ON_EXHAUSTION_TIMEOUT
    resume_strategy: str = QUOTA_HANDLING_DEFAULT_RESUME_STRATEGY
    audit_comment_on_wait: bool = QUOTA_HANDLING_DEFAULT_AUDIT_COMMENT_ON_WAIT
    audit_comment_on_resume: bool = QUOTA_HANDLING_DEFAULT_AUDIT_COMMENT_ON_RESUME
    log_structured_events: bool = QUOTA_HANDLING_DEFAULT_LOG_STRUCTURED_EVENTS
    recovery_probe: QuotaRecoveryProbeConfig = field(default_factory=QuotaRecoveryProbeConfig)


def _parse_quota_backoff_config(raw: dict) -> QuotaBackoffConfig:
    """Parse a ``recovery_probe.backoff`` sub-dict into a ``QuotaBackoffConfig``.

    Args:
        raw: Raw YAML dict (already schema-validated). May be empty.

    Returns:
        ``QuotaBackoffConfig`` with fields set from *raw*; absent fields use
        the spec-correct constants from ``devbench.constants``.
    """
    defaults = QuotaBackoffConfig()
    return QuotaBackoffConfig(
        initial_seconds=float(raw.get("initial_seconds", defaults.initial_seconds)),
        max_seconds=float(raw.get("max_seconds", defaults.max_seconds)),
        multiplier=float(raw.get("multiplier", defaults.multiplier)),
        jitter=float(raw.get("jitter", defaults.jitter)),
    )


def _parse_quota_recovery_probe_config(raw: dict) -> QuotaRecoveryProbeConfig:
    """Parse a ``recovery_probe`` sub-dict into a ``QuotaRecoveryProbeConfig``.

    Args:
        raw: Raw YAML dict (already schema-validated). May be empty.

    Returns:
        ``QuotaRecoveryProbeConfig`` with fields set from *raw*; absent fields
        use the spec-correct constants from ``devbench.constants``.
    """
    defaults = QuotaRecoveryProbeConfig()
    backoff_raw = raw.get("backoff") or {}
    return QuotaRecoveryProbeConfig(
        enabled=bool(raw.get("enabled", defaults.enabled)),
        request_size_tokens=int(raw.get("request_size_tokens", defaults.request_size_tokens)),
        timeout_seconds=float(raw.get("timeout_seconds", defaults.timeout_seconds)),
        backoff=_parse_quota_backoff_config(backoff_raw),
    )


def _parse_quota_handling_config(raw: dict) -> QuotaHandlingConfig:
    """Parse the ``quota_handling:`` YAML section into a ``QuotaHandlingConfig``.

    Called by ``load_runtime_config`` after JSON Schema validation.  All fields
    are optional in the YAML; absent fields use constants from
    ``devbench.constants``.  The section may be omitted entirely; in that case
    *raw* is an empty dict and all defaults apply.

    Args:
        raw: Raw ``quota_handling`` dict from YAML (already schema-validated).
            May be an empty dict when the section is absent.

    Returns:
        ``QuotaHandlingConfig`` populated from *raw*.
    """
    defaults = QuotaHandlingConfig()

    probe_raw = raw.get("recovery_probe") or {}
    recovery_probe = _parse_quota_recovery_probe_config(probe_raw)

    return QuotaHandlingConfig(
        enabled=bool(raw.get("enabled", defaults.enabled)),
        detect_modes=list(raw.get("detect_modes", defaults.detect_modes)),
        on_exhaustion=raw.get("on_exhaustion", defaults.on_exhaustion),
        poll_interval_seconds=int(raw.get("poll_interval_seconds", defaults.poll_interval_seconds)),
        max_wait_seconds=int(raw.get("max_wait_seconds", defaults.max_wait_seconds)),
        on_exhaustion_timeout=raw.get("on_exhaustion_timeout", defaults.on_exhaustion_timeout),
        resume_strategy=raw.get("resume_strategy", defaults.resume_strategy),
        audit_comment_on_wait=bool(raw.get("audit_comment_on_wait", defaults.audit_comment_on_wait)),
        audit_comment_on_resume=bool(raw.get("audit_comment_on_resume", defaults.audit_comment_on_resume)),
        log_structured_events=bool(raw.get("log_structured_events", defaults.log_structured_events)),
        recovery_probe=recovery_probe,
    )


# ---------------------------------------------------------------------------
# Notifications (Slack + generic webhook) -- spec / PR #202
# ---------------------------------------------------------------------------


@dataclass
class NotificationsSlackConfig:
    """Slack endpoint for the notifications dispatcher.

    One shared webhook URL is used for every enabled event; the
    payload itself carries an ``<!here>`` mention so the same payload
    works whether the webhook is bound to a one-person private DM
    channel or a shared team channel.

    Attributes:
        enabled: Endpoint-level toggle.  When ``False``, no Slack POST
            happens even if the master ``notifications.enabled`` is
            ``True`` and the per-event toggle is on.  Default
            ``False`` -- the operator opts in explicitly.
        webhook_url: Slack incoming webhook URL (channel-scoped).
            ``None`` disables Slack notifications regardless of the
            ``enabled`` flag.
    """

    enabled: bool = False
    webhook_url: str | None = None


@dataclass
class NotificationsEventsConfig:
    """Per-event toggles for the notifications dispatcher.

    Every field defaults to ``False`` so the dispatcher is silent
    until the operator opts in.  Field names match the
    ``EVENT_*`` constants in :mod:`devbench.notifications`.
    """

    work_unit_done: bool = False
    work_unit_blocked_operator: bool = False
    work_unit_blocked_runtime_degradation: bool = False
    work_unit_blocked_held: bool = False
    work_unit_blocked_on_held: bool = False
    work_unit_blocked_auto_clearing: bool = False
    work_unit_blocked_awaiting_dependency: bool = False
    work_unit_blocked_amendment_recovery: bool = False
    work_unit_materialised: bool = False
    work_unit_promoted: bool = False
    pr_opened: bool = False
    pr_merged: bool = False
    ci_failure: bool = False
    # Issue #219: fires on CIResult.GREEN inside the finalize path so
    # operators running ``git_ops.auto_merge: false`` get an explicit
    # "PR ready for manual merge" Slack signal.  Default ``False`` --
    # existing workspaces stay silent on upgrade.
    ci_pass: bool = False
    orchestrator_stop: bool = False
    orchestrator_auto_restart: bool = False
    quota_pause: bool = False
    quota_resume: bool = False


@dataclass
class NotificationsConfig:
    """Operator-facing notification dispatcher configuration.

    The default-constructed value has ``enabled=False`` and every
    event toggle off, so omitting the ``notifications:`` yaml block
    means "no notifications", matching the spec's opt-in posture.

    Endpoints live in their own nested sub-blocks (today: ``slack``;
    future: ``discord``, ``teams``, ``generic_webhook``, etc.) so the
    schema accommodates additional notification transports without
    touching the per-event toggle surface.

    Attributes:
        enabled: Master switch.  When ``False``, no event fires
            regardless of per-event toggles.  Default ``False``.
        timeout_seconds: Per-POST HTTP timeout.  Default 10.
        events: Per-event toggle struct.
        slack: Slack endpoint config (enabled flag + webhook URL).
    """

    enabled: bool = False
    timeout_seconds: float = 10.0
    events: NotificationsEventsConfig = field(default_factory=NotificationsEventsConfig)
    slack: NotificationsSlackConfig = field(default_factory=NotificationsSlackConfig)


def _validate_webhook_url(label: str, value: object) -> str | None:
    """Validate a webhook URL field at config-load time.

    Returns the URL unchanged when valid, ``None`` when *value* is
    null / empty.  Raises ``ValueError`` for any non-string,
    non-``https://`` value.  CLAUDE.md "fail-fast at config-load"
    catches typos and credential-injection attempts before any HTTP
    traffic.
    """
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label}: must be a string or null, got {type(value).__name__}")
    if not value.startswith("https://"):
        raise ValueError(f"{label}: must start with 'https://' (got {value[:20]!r}...)")
    return value


def _parse_notifications_config(raw: dict) -> NotificationsConfig:
    """Parse a ``notifications:`` yaml block into a :class:`NotificationsConfig`.

    Schema validation in ``load_runtime_config`` already rejects
    unknown keys; this function applies the value-level checks
    (URL scheme) so a malformed value fails fast at config-load time,
    not on first dispatch attempt.
    """
    defaults = NotificationsConfig()

    slack_raw = raw.get("slack") or {}
    slack = NotificationsSlackConfig(
        enabled=bool(slack_raw.get("enabled", defaults.slack.enabled)),
        webhook_url=_validate_webhook_url("notifications.slack.webhook_url", slack_raw.get("webhook_url")),
    )

    events_raw = raw.get("events") or {}
    events = NotificationsEventsConfig(
        work_unit_done=bool(events_raw.get("work_unit_done", defaults.events.work_unit_done)),
        work_unit_blocked_operator=bool(
            events_raw.get("work_unit_blocked_operator", defaults.events.work_unit_blocked_operator)
        ),
        work_unit_blocked_runtime_degradation=bool(
            events_raw.get(
                "work_unit_blocked_runtime_degradation",
                defaults.events.work_unit_blocked_runtime_degradation,
            )
        ),
        work_unit_blocked_held=bool(events_raw.get("work_unit_blocked_held", defaults.events.work_unit_blocked_held)),
        work_unit_blocked_on_held=bool(
            events_raw.get("work_unit_blocked_on_held", defaults.events.work_unit_blocked_on_held)
        ),
        work_unit_blocked_auto_clearing=bool(
            events_raw.get(
                "work_unit_blocked_auto_clearing",
                defaults.events.work_unit_blocked_auto_clearing,
            )
        ),
        work_unit_blocked_awaiting_dependency=bool(
            events_raw.get(
                "work_unit_blocked_awaiting_dependency",
                defaults.events.work_unit_blocked_awaiting_dependency,
            )
        ),
        work_unit_blocked_amendment_recovery=bool(
            events_raw.get(
                "work_unit_blocked_amendment_recovery",
                defaults.events.work_unit_blocked_amendment_recovery,
            )
        ),
        work_unit_materialised=bool(events_raw.get("work_unit_materialised", defaults.events.work_unit_materialised)),
        work_unit_promoted=bool(events_raw.get("work_unit_promoted", defaults.events.work_unit_promoted)),
        pr_opened=bool(events_raw.get("pr_opened", defaults.events.pr_opened)),
        pr_merged=bool(events_raw.get("pr_merged", defaults.events.pr_merged)),
        ci_failure=bool(events_raw.get("ci_failure", defaults.events.ci_failure)),
        ci_pass=bool(events_raw.get("ci_pass", defaults.events.ci_pass)),
        orchestrator_stop=bool(events_raw.get("orchestrator_stop", defaults.events.orchestrator_stop)),
        orchestrator_auto_restart=bool(
            events_raw.get("orchestrator_auto_restart", defaults.events.orchestrator_auto_restart)
        ),
        quota_pause=bool(events_raw.get("quota_pause", defaults.events.quota_pause)),
        quota_resume=bool(events_raw.get("quota_resume", defaults.events.quota_resume)),
    )

    return NotificationsConfig(
        enabled=bool(raw.get("enabled", defaults.enabled)),
        timeout_seconds=float(raw.get("timeout_seconds", defaults.timeout_seconds)),
        events=events,
        slack=slack,
    )


@dataclass
class RepoConfig:
    """Per-repository configuration.

    Attributes:
        default_branch: Explicit default branch to use for this repo.
            When ``None``, branch consumers fall back to ``origin/HEAD``.
        checkout_directory: Path relative to ``DEVBENCH_WORKSPACE_ROOT`` where
            the repo is checked out.  Must not be absolute or contain ``..``.
            When ``None``, defaults to the repo short-name (the part after
            the ``/`` in ``org/repo``).
        merge_strategy: Per-repo PR merge strategy override.  When ``None``,
            the top-level ``RuntimeConfig.merge_strategy`` is used.
        resolved_checkout_path: Absolute filesystem path to the repo
            checkout, populated by ``load_runtime_config``. Equal to
            ``<DEVBENCH_WORKSPACE_ROOT>/<checkout_directory or repo_short_name>``
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
class ReviewTeamModelsConfig:
    """Per-judge model overrides for the four review_team agents (ADR-25).

    Every field defaults to ``None``; the corresponding judge runs on the
    model declared in its ``.md`` frontmatter when its field is ``None``.
    Operators set fields to opt-in per-judge to manage Sonnet / Opus / Bedrock
    quota independently.

    Attributes:
        code_reviewer: Override for ``plugin/devbench/agents/review_team/code-reviewer.md``.
        test_reviewer: Override for ``plugin/devbench/agents/review_team/test-reviewer.md``.
        doc_reviewer: Override for ``plugin/devbench/agents/review_team/doc-reviewer.md``.
        changes_manifest: Override for ``plugin/devbench/agents/review_team/changes-manifest.md``.
    """

    code_reviewer: str | None = None
    test_reviewer: str | None = None
    doc_reviewer: str | None = None
    changes_manifest: str | None = None


@dataclass
class AgentModelsConfig:
    """Per-agent model overrides for the work-agents in the devbench plugin (ADR-25).

    Each field corresponds to one ``.md`` file under ``plugin/devbench/agents/``.
    When a field is ``None`` (the default), the agent runs on the model
    declared in its frontmatter. When set, ``devbench.plugin_shadow`` rewrites
    the frontmatter ``model:`` line in a workspace-local shadow copy and the
    Agent SDK / ``claude --plugin-dir`` is pointed at the shadow.

    Operators set this so they can manage Sonnet / Opus / Bedrock quota
    separately (e.g. drive ``executor`` on opus when sonnet quota is exhausted).
    ``config.py`` merges ``JUDGE_AGENT_MODEL_*`` env vars over the YAML values
    after this dataclass is constructed.

    Attributes:
        executor: Override for ``plugin/devbench/agents/executor.md``.
        blocker_resolver: Override for ``plugin/devbench/agents/blocker-resolver.md``.
        manifest_amender: Override for ``plugin/devbench/agents/manifest-amender.md``.
        security_reviewer: Override for ``plugin/devbench/agents/security-reviewer.md``.
        task_factory: Override for ``plugin/devbench/agents/task-factory.md``.
        review_supervisor: Override for ``plugin/devbench/agents/review-supervisor.md``.
        review_team: Nested overrides for the four review_team judges.
    """

    executor: str | None = None
    blocker_resolver: str | None = None
    manifest_amender: str | None = None
    security_reviewer: str | None = None
    task_factory: str | None = None
    review_supervisor: str | None = None
    review_team: ReviewTeamModelsConfig = field(default_factory=ReviewTeamModelsConfig)


def validate_agent_model_value(
    source: str,
    agent_label: str,
    value: str,
    use_bedrock: bool,
) -> None:
    """Validate one agent override value against the use_bedrock toggle.

    Per ADR-25 the override must match the same channel as
    ``use_bedrock``: short names + Anthropic API ids are accepted only when
    ``use_bedrock`` is False; Bedrock ARNs are accepted only when it is True.
    Fail fast with a clear actionable message; the SDK's downstream error
    would otherwise surface as a generic 401/404 at first invocation.

    Used by both the YAML loader (``source`` is the config file path) and
    ``config.py`` after ``JUDGE_AGENT_MODEL_*`` env var merging (``source``
    is the env var name) so YAML and env-supplied values get the same
    fail-fast treatment.

    Haiku is unconditionally rejected for every per-agent field (case-insensitive
    substring match so both the short name ``haiku``, full Anthropic ids like
    ``claude-haiku-4-5-20251001``, and Bedrock ARNs containing ``haiku`` are
    all caught). The rejection raises a ``ValueError`` whose message names the
    offending field, the rejected value, and references
    caylent-solutions/devbench#198 so an operator who sees the error can find
    the rationale. There is no override path; the only way to use haiku is to
    edit the canonical constants module locally.

    Args:
        source: Human-readable origin of the value (file path or env var
            name) included in the error message.
        agent_label: Dotted label of the agent (``executor``,
            ``review_team.code_reviewer``).
        value: The string value to validate.
        use_bedrock: Currently-resolved use_bedrock flag.

    Raises:
        ValueError: When *value* contains ``haiku`` (case-insensitive), or when
            *value* does not match the format implied by *use_bedrock*.
    """
    if "haiku" in value.lower():
        raise ValueError(
            f"{source}: agents.{agent_label} = {value!r} is rejected. "
            "Haiku is not permitted for any work agent -- under load the Claude "
            "Agent SDK was repeatedly observed to silently drop the Agent tool from "
            "haiku's tool list, causing RUNTIME_DEGRADATION failures. Use 'sonnet' "
            "or 'opus' instead. See caylent-solutions/devbench#198."
        )
    if use_bedrock:
        if not BEDROCK_AGENT_MODEL_PATTERN.match(value):
            raise ValueError(
                f"{source}: agents.{agent_label} = {value!r} is not a valid Bedrock "
                "model id while use_bedrock: true. Expected pattern "
                "'us.anthropic.claude-<name>-<ver>-v<N>' (e.g. "
                "'us.anthropic.claude-opus-4-7-v1')."
            )
        return
    if value in ALLOWED_AGENT_MODEL_SHORT_NAMES:
        return
    if ANTHROPIC_AGENT_MODEL_PATTERN.match(value):
        return
    short = ", ".join(sorted(ALLOWED_AGENT_MODEL_SHORT_NAMES))
    raise ValueError(
        f"{source}: agents.{agent_label} = {value!r} is not a valid Anthropic API "
        f"model id while use_bedrock: false. Accepted short names: {short}. Accepted "
        "full ids: 'claude-<name>-<digits>(-...)' (e.g. 'claude-opus-4-7')."
    )


def _parse_agent_models_config(
    path: Path,
    raw: object,
    use_bedrock: bool,
) -> AgentModelsConfig:
    """Parse the ``agents`` YAML section into an ``AgentModelsConfig``.

    The JSON Schema already rejects unknown keys + wrong types; this parser
    cross-validates each value against ``use_bedrock`` so an inconsistent
    config fails at load time, not at first agent invocation.

    Args:
        path: Config file path (used in error messages).
        raw: Raw ``agents`` value from YAML (already schema-validated).
        use_bedrock: Top-level ``use_bedrock`` flag from the same YAML.

    Returns:
        ``AgentModelsConfig`` with every supplied override populated and
        every absent field left at ``None``.
    """
    if not isinstance(raw, dict):
        return AgentModelsConfig()

    top_fields = (
        "executor",
        "blocker_resolver",
        "manifest_amender",
        "security_reviewer",
        "task_factory",
        "review_supervisor",
    )
    kwargs: dict[str, str] = {}
    for key in top_fields:
        value = raw.get(key)
        if value is None:
            continue
        validate_agent_model_value(f"Config file '{path}'", key, value, use_bedrock)
        kwargs[key] = value

    review_team_raw = raw.get("review_team") or {}
    review_team_kwargs: dict[str, str] = {}
    for key in ("code_reviewer", "test_reviewer", "doc_reviewer", "changes_manifest"):
        value = review_team_raw.get(key)
        if value is None:
            continue
        validate_agent_model_value(f"Config file '{path}'", f"review_team.{key}", value, use_bedrock)
        review_team_kwargs[key] = value
    review_team = ReviewTeamModelsConfig(**review_team_kwargs)

    return AgentModelsConfig(review_team=review_team, **kwargs)


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
        backlog: Backlog lifecycle settings (default status for new WUs).
        allowed_orgs: List of permitted GitHub organisations.
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
            split silently when an operator set ``DEVBENCH_LOG_FILE`` to
            different values in different shells. ``None`` (the
            default) means callers must supply ``DEVBENCH_LOG_FILE``
            explicitly or rely on the workspace-local convention
            ``logs/orchestrator.log``.
        quota_handling: Quota-wait-and-resume configuration loaded from the
            ``quota_handling:`` YAML section (spec section 4.5.6). All fields
            default to spec-correct values; the entire section may be omitted
            from devbench.yaml and the orchestrator will behave as if
            ``enabled: true`` with all other spec defaults applied.
    """

    repos: dict[str, RepoConfig] = field(default_factory=dict)
    timeouts: TimeoutConfig = field(default_factory=TimeoutConfig)
    limits: LimitConfig = field(default_factory=LimitConfig)
    git_ops: GitOpsConfig = field(default_factory=GitOpsConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    stop_hook: StopHookConfig = field(default_factory=StopHookConfig)
    hook_tail: HookTailConfig = field(default_factory=HookTailConfig)
    orchestrate: OrchestrateConfig = field(default_factory=OrchestrateConfig)
    backlog: BacklogConfig = field(default_factory=BacklogConfig)
    manifest_amendment: AmendmentConfig = field(default_factory=AmendmentConfig)
    task_factory: TaskFactoryConfig = field(default_factory=TaskFactoryConfig)
    agent_models: AgentModelsConfig = field(default_factory=AgentModelsConfig)
    validate: ValidateConfig = field(default_factory=ValidateConfig)
    debug: DebugConfig = field(default_factory=DebugConfig)
    quota_handling: QuotaHandlingConfig = field(default_factory=QuotaHandlingConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    allowed_orgs: list[str] = field(default_factory=list)
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
    """Return config file path using precedence: explicit > DEVBENCH_CONFIG_PATH > default.

    Args:
        explicit_path: Path from the ``--config`` CLI argument, or ``None``.
        env: Environment variable mapping (typically ``os.environ``).
        workspace_root: Absolute path to the workspace root
            (value of ``DEVBENCH_WORKSPACE_ROOT``).

    Returns:
        Resolved config file path.  The path may not exist on disk -- callers
        are responsible for checking existence.
    """
    if explicit_path:
        return Path(explicit_path)
    env_path = env.get("DEVBENCH_CONFIG_PATH", "")
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
        workspace_root: Absolute path to ``DEVBENCH_WORKSPACE_ROOT`` for
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


def _validate_auto_finalize_auto_merge(
    path: Path,
    defer_pr: bool,
    local_only: bool,
    auto_finalize: bool,
    auto_merge: bool,
) -> None:
    """Validate cross-field constraints for auto_finalize and auto_merge.

    Extracted from ``load_runtime_config`` to keep that function's branch
    count within ruff's PLR0912 threshold (12).

    Raises:
        ValueError: On any invalid combination.
    """
    if auto_finalize and not defer_pr:
        raise ValueError(
            f"Config file '{path}': git_ops.auto_finalize: true requires git_ops.defer_pr: true. "
            "auto_finalize triggers git-ops-finalize which pushes the deferred single branch; "
            "without defer_pr there is no deferred branch to finalize."
        )
    if auto_finalize and local_only:
        raise ValueError(
            f"Config file '{path}': git_ops.auto_finalize: true is incompatible with "
            "git_ops.local_only: true. Local-only repos have no remote to push to; "
            "git-ops-finalize cannot create a PR. "
            "The skill would emit [AUTO_FINALIZE_SKIPPED] local_only=true, "
            "so setting auto_finalize: true alongside local_only: true is a configuration error."
        )
    if auto_merge and not auto_finalize:
        raise ValueError(
            f"Config file '{path}': git_ops.auto_merge: true requires git_ops.auto_finalize: true. "
            "auto_merge merges the PR created by auto_finalize; "
            "without auto_finalize there is no PR to merge."
        )


def _schema_error_message(path: Path, exc: jsonschema.ValidationError) -> str:
    """Format a schema validation error with the dotted field path for actionable diagnostics.

    When the failing field has a known location (``exc.absolute_path`` is non-empty), the
    message includes the dotted path so the operator knows exactly which config key to fix.
    Example: ``quota_handling.on_exhaustion: 'never' is not one of ['wait', 'fail', 'drain']``

    Args:
        path: Config file path (used as context prefix).
        exc: The jsonschema ``ValidationError`` whose ``.absolute_path`` and ``.message``
             are extracted.

    Returns:
        Formatted error string suitable for wrapping in ``ValueError``.
    """
    field_path = ".".join(str(p) for p in exc.absolute_path)
    detail = f"{field_path}: {exc.message}" if field_path else exc.message
    return f"Config file '{path}' failed schema validation: {detail}"


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
            "Create it or set DEVBENCH_CONFIG_PATH to point to its location. "
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
        raise ValueError(_schema_error_message(path, exc)) from exc

    allowed_orgs: list[str] = raw.get("allowed_orgs") or []
    workspace_root_raw = _env.get("DEVBENCH_WORKSPACE_ROOT", "")
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
    local_only = bool(git_ops_raw.get("local_only", False))
    if local_only and not defer_pr:
        raise ValueError(
            f"Config file '{path}': git_ops.local_only: true requires git_ops.defer_pr: true. "
            "Local-only repos have no remote to push to; PR creation is meaningless. "
            "Set git_ops.defer_pr: true (and git_ops.single_branch: <name>) alongside local_only."
        )
    auto_finalize = bool(git_ops_raw.get("auto_finalize", False))
    auto_merge = bool(git_ops_raw.get("auto_merge", False))
    _validate_auto_finalize_auto_merge(path, defer_pr, local_only, auto_finalize, auto_merge)
    git_ops = GitOpsConfig(
        update_submodule=bool(git_ops_raw.get("update_submodule", False)),
        single_branch=single_branch_raw,
        defer_pr=defer_pr,
        pause_before_merge=pause_before_merge,
        inline_orphan_cleanup=bool(inline_cleanup_raw) if inline_cleanup_raw is not None else None,
        ci_failure_retry=bool(ci_failure_retry_raw) if ci_failure_retry_raw is not None else None,
        orphan_patterns=list(git_ops_raw.get("orphan_patterns") or []),
        pr_review_resolution=pr_review_resolution,
        local_only=local_only,
        auto_finalize=auto_finalize,
        auto_merge=auto_merge,
    )
    if local_only:
        missing_default_branch = [repo_name for repo_name, repo_cfg in repos.items() if not repo_cfg.default_branch]
        if missing_default_branch:
            raise ValueError(
                f"Config file '{path}': git_ops.local_only: true requires every entry in "
                f"repos: to set an explicit default_branch:. Missing on: "
                f"{', '.join(sorted(missing_default_branch))}. There is no origin to fall "
                "back to in local-only mode."
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

    # Populate AgentModelsConfig from YAML agents block (ADR-25). Cross-
    # validates each non-None value against the top-level use_bedrock flag so
    # an inconsistent config fails at load time, not at first invocation.
    agent_models = _parse_agent_models_config(path, raw.get("agents"), bool(raw.get("use_bedrock", False)))

    # Populate ValidateConfig from YAML validate block. All toggles default
    # to False so existing backlogs see no behaviour change.
    validate_raw = raw.get("validate") or {}
    default_validate = ValidateConfig()
    validate_cfg = ValidateConfig(
        check_orphan_path_tokens=bool(
            validate_raw.get("check_orphan_path_tokens", default_validate.check_orphan_path_tokens)
        ),
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

    # Populate HookTailConfig from YAML hook_tail block (issue #134).
    # JSONSchema enforces minimum:1 + additionalProperties:false at parse
    # time; absent fields stay None so config.py applies the env > default
    # fallback chain.
    hook_tail_raw = raw.get("hook_tail") or {}
    hook_tail = HookTailConfig(
        agent_width=(int(hook_tail_raw["agent_width"]) if "agent_width" in hook_tail_raw else None),
        tool_width=(int(hook_tail_raw["tool_width"]) if "tool_width" in hook_tail_raw else None),
        description_max=(int(hook_tail_raw["description_max"]) if "description_max" in hook_tail_raw else None),
        stdout_preview_max=(
            int(hook_tail_raw["stdout_preview_max"]) if "stdout_preview_max" in hook_tail_raw else None
        ),
    )

    # Populate OrchestrateConfig from YAML orchestrate block (issue #144).
    # Schema enforces minimum:1; absent field stays None so config.py
    # applies the env > default fallback chain.
    orchestrate_raw = raw.get("orchestrate") or {}
    orchestrate = OrchestrateConfig(
        max_cascade_depth=(
            int(orchestrate_raw["max_cascade_depth"]) if "max_cascade_depth" in orchestrate_raw else None
        ),
    )

    # Populate BacklogConfig from YAML backlog block (issue #189).
    # Schema enforces enum on default_status_for_new_work_units and
    # additionalProperties: false. We re-validate at runtime so that
    # _parse_backlog_config can emit a clear, actionable error message
    # that names both the invalid value and the allowed values.
    backlog_raw = raw.get("backlog") or {}
    backlog = _parse_backlog_config(path, backlog_raw)

    # Populate QuotaHandlingConfig from YAML quota_handling block (spec 4.5.6).
    # JSON Schema validation enforces enum values, integer/float ranges, and
    # additionalProperties: false before this call; _parse_quota_handling_config
    # therefore performs no re-validation -- it only maps YAML to the dataclass.
    quota_handling_raw = raw.get("quota_handling") or {}
    quota_handling = _parse_quota_handling_config(quota_handling_raw)

    # Populate NotificationsConfig from YAML notifications block (PR #202).
    # JSON Schema validation already enforces shape; _parse_notifications_config
    # applies value-level checks (URL scheme, Slack user-id pattern).
    notifications_raw = raw.get("notifications") or {}
    notifications = _parse_notifications_config(notifications_raw)

    return RuntimeConfig(
        repos=repos,
        timeouts=timeouts,
        limits=limits,
        git_ops=git_ops,
        report=report,
        stop_hook=stop_hook,
        hook_tail=hook_tail,
        orchestrate=orchestrate,
        backlog=backlog,
        manifest_amendment=manifest_amendment,
        task_factory=task_factory,
        agent_models=agent_models,
        validate=validate_cfg,
        debug=debug,
        quota_handling=quota_handling,
        notifications=notifications,
        allowed_orgs=allowed_orgs,
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
