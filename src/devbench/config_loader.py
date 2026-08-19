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
environment-driven values when ``None`` is encountered.  ``resolve_gate_config``
follows the same rule: its env-layer parameter is an already-resolved
``bool | None`` the caller computes via ``config.resolve_gate_env_override``
(which reads ``DEVBENCH_GATE_<NAME>_ENABLED`` through the existing
``_resolve_bool`` chain) -- this module still never reads ``os.environ``
itself.

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
      orchestrator_poll_interval: <integer>
      github_check: <integer>
      orchestrator_inactivity: <integer>

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

import dataclasses
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
    BRANCH_NAME_TEMPLATE,
    DEFAULT_FALLBACK_MODEL_RATES,
    DEFAULT_STOP_HOOK_MAX_BLOCKS,
    DEFAULT_STOP_HOOK_STALE_TASK_MINUTES,
    DEFAULT_STOP_HOOK_WINDOW_SECONDS,
    GATE_AUTO_DERIVE_REGISTRY_DEFAULT,
    GATE_ENABLED_DEFAULT,
    GATE_EXTRACT_SOURCE_LITERALS_DEFAULT,
    GATE_FIELD_DEFAULTS,
    GATE_PROVENANCE_BUILTIN,
    GATE_PROVENANCE_ENV,
    GATE_PROVENANCE_PROJECT,
    GATE_PROVENANCE_REPO,
    STATUS_DRAFT,
    STATUS_IN_QUEUE,
    ModelRates,
)
from devbench.constants import (
    GATE_NAMES as _GATE_NAMES_ORDERED,
)
from devbench.source_classification import ENTRY_POINT_STEMS

_BACKLOG_DEFAULT_STATUS: str = STATUS_IN_QUEUE
_VALID_DEFAULT_STATUSES: frozenset[str] = frozenset({STATUS_IN_QUEUE, STATUS_DRAFT})
_BACKLOG_DEFAULT_BULK_UPDATE_CONFIRM_THRESHOLD: int = 10
_BACKLOG_DEFAULT_BULK_UPDATE_AUDIT_PATH: str = "logs/bulk-updates.log"

# Skills plugin configuration defaults (issue #221 E1-E10).
_SKILLS_DEFAULT_FAN_OUT_THRESHOLD: int = 10
_SKILLS_DEFAULT_MAX_ITERATIONS: int = 5

# Quota wait-and-resume configuration (issue #236, spec S5.2, FR-2.9).
_QUOTA_HANDLING_VALID_ON_EXHAUSTION: frozenset[str] = frozenset({"wait", "fail", "drain"})
_QUOTA_HANDLING_VALID_ON_EXHAUSTION_TIMEOUT: frozenset[str] = frozenset({"drain", "fail", "keep_waiting"})
_QUOTA_HANDLING_VALID_RESUME_STRATEGY: frozenset[str] = frozenset(
    {"continue_current_wu", "restart_wu", "drain_and_resume"}
)
_QUOTA_HANDLING_POLL_INTERVAL_MIN: int = 30
_QUOTA_HANDLING_POLL_INTERVAL_MAX: int = 3600
_QUOTA_HANDLING_MAX_WAIT_MIN: int = 1

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
        orchestrator_poll_interval: Orchestrator polling interval.
        github_check: GitHub check status polling timeout.
        orchestrator_inactivity: Orchestrator SDK message inactivity timeout
            (spec FR-17, db-262) -- bounds how long ``cmd_start._run`` waits
            for the next SDK message before disposing the turn as hung.
    """

    gh_api: int | None = None
    test: int | None = None
    security_fetch: int | None = None
    llm: int | None = None
    command: int | None = None
    orchestrator_poll_interval: int | None = None
    github_check: int | None = None
    orchestrator_inactivity: int | None = None


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
        branch_prefix: Top-level task-branch prefix, overridden per-repo by
            ``RepoConfig.branch_prefix``.  When set, task branches are named
            ``backlog/<prefix>/<unit-id-lower>`` instead of
            ``backlog/<unit-id-lower>``.  Also namespaces ``single_branch``
            as ``<prefix>/<single_branch>`` when both are set.  Namespaces
            branches by workspace so multiple devbench work groups sharing
            one downstream repo (each independently numbering tasks from
            ``E1-F1-S1-T1``) never collide on branch names.  Defaults to
            ``None`` (no prefix, original behaviour).
        provenance_path: Path (relative to the repo working tree, or
            absolute) to a JSON provenance map that
            ``GitOpsService.compose_finalize_pr_body`` reads to compose the
            ``git-ops-finalize`` PR body: title, per-epic summary and one
            closing-keyword line per mapped issue (spec 4.13; D-17).
            Overridden per-invocation by ``git-ops-finalize --provenance
            <path>``.  Defaults to ``None``, which preserves the plain PR
            body ``git-ops-finalize`` has always produced.  No
            ``DEVBENCH_*`` environment override exists for this key --
            consistent with its sibling path/name settings
            ``single_branch`` and ``branch_prefix``, both of which are
            YAML-only.
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
    branch_prefix: str | None = None
    provenance_path: str | None = None


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

    Issue #223: the legacy scalar fields (``token_cost_per_million_input``,
    ``token_cost_per_million_output``, ``token_cost_discount``) were removed
    in favour of a per-model rate table (``models`` + ``default_model``).
    Existing workspaces that set the old fields get a clear fail-fast error
    at config-load time pointing at the new ``report.models`` block.

    Attributes:
        models: Mapping of model id (e.g. ``claude-opus-4-7``) to its
            ``ModelRates``.  When empty, every observed model id is priced
            against ``default_model``.  Operators typically populate this
            block from ``docs/model-pricing.md``'s Standard pricing table.
        default_model: Rates applied to the ``"<unknown>"`` bucket -- any
            transcript message whose ``model`` field is missing OR any
            model id not present in ``models``.  Defaults to
            ``DEFAULT_FALLBACK_MODEL_RATES`` when absent from YAML.
        display_timezone: IANA timezone name for displaying report timestamps.
            ``None`` means use the host's system local timezone.
        cache_read_multiplier: Cost multiplier for cache-read tokens, relative
            to the base input rate.  ``None`` means use the constant default.
            Applied to a model id only when that ``ModelRates`` does not
            override ``cache_read_multiplier`` itself.
        cache_write_5min_multiplier: Cost multiplier for 5-minute prompt-cache
            write tokens, relative to the base input rate.
        cache_write_1hr_multiplier: Cost multiplier for 1-hour prompt-cache
            write tokens, relative to the base input rate.
        data_residency_multiplier: Cost multiplier when usage.inference_geo
            is set (US-only inference). Applied per-call (issue #124).
        fast_mode_multiplier: Cost multiplier when usage.speed == 'fast'
            (Opus 5 / Opus 4.8 fast-mode premium, issue #233). Applied
            per-call (issue #124).
        recent_pace_tasks: Number of most recently completed tasks to average
            for the "Recent pace" projection. ``None`` falls back to
            ``DEFAULT_RECENT_PACE_TASKS``.
    """

    models: dict[str, ModelRates] = dataclasses.field(default_factory=dict)
    default_model: ModelRates = dataclasses.field(default_factory=lambda: DEFAULT_FALLBACK_MODEL_RATES)
    display_timezone: str | None = None
    cache_read_multiplier: float | None = None
    cache_write_5min_multiplier: float | None = None
    cache_write_1hr_multiplier: float | None = None
    data_residency_multiplier: float | None = None
    fast_mode_multiplier: float | None = None
    recent_pace_tasks: int | None = None


@dataclass(frozen=True)
class ValidateConfig:
    """Per-backlog opt-in toggles for additional ``validate-backlog`` rules.

    Existing rules (1-19) run unconditionally. Rules here are individually
    toggleable. See ``docs/backlog-contract.md`` for the full rule list.

    Attributes:
        check_orphan_path_tokens: Rule 20. When ``True``, validate-backlog
            scans every Task's ``## Acceptance Criteria`` and
            ``## Definition of Done`` sections for backtick-quoted
            path-shaped tokens, and emits an integrity error for any token
            that does not appear in the Task's ``## Changes Manifest``
            (after path normalisation). A token followed by ``(ref)`` is
            treated as a declared read-only reference and skipped. Catches
            spec drift where AC/DoD prose restates a path that disagrees
            with the Manifest. Default ``True`` (set ``false`` to opt out).
    """

    check_orphan_path_tokens: bool = True


@dataclass(frozen=True)
class FixtureCanonicalSource:
    """One canonical fixture/dataset file a workspace designates as authoritative.

    Attributes:
        path: Repo-relative path to the canonical fixture file (JSON or YAML).
        identifier_field: Key name within each canonical record whose values
            other fixtures must reference (SKU, product id, PO number, etc.).
        expected_count: Optional exact expected number of distinct
            ``identifier_field`` values. Set on a backfill task to assert
            full dataset coverage; ``None`` skips the coverage check.
    """

    path: str
    identifier_field: str
    expected_count: int | None = None


@dataclass(frozen=True)
class FixtureScanTarget:
    """One mock/fixture file to cross-reference against a canonical source.

    Attributes:
        path: Repo-relative path to the fixture file to scan (JSON or YAML).
        identifier_field: Key name within each scanned record holding the
            identifier literal(s) to cross-reference.
        canonical_source: ``path`` of the ``FixtureCanonicalSource`` this
            target checks against. ``None`` is valid only when exactly one
            canonical source is configured (it is inferred in that case).
        allow_missing: Identifier values explicitly permitted to be absent
            from the canonical source -- the opt-out/scoping mechanism for
            fixtures that intentionally model a not-found/empty-state edge
            case (caylent-solutions/devbench-internal-backlog#17 AC3).
    """

    path: str
    identifier_field: str
    canonical_source: str | None = None
    allow_missing: frozenset[str] = dataclasses.field(default_factory=frozenset)


@dataclass(frozen=True)
class FixtureConsistencyConfig:
    """Per-backlog ``gates.fixture_consistency:`` configuration (spec 4.1, 4.7).

    Opt-in surface for ``devbench check-fixture-consistency``, one of the
    eight gates nested under ``GatesConfig``. Absent ``canonical_sources``
    (the default for every workspace that does not configure this block)
    makes the check a no-op, since devbench cannot infer a target repo's
    fixture-file layout on its own -- independent of ``enabled``, which
    exists purely for uniformity with the other seven gates (D-2: every
    gate carries the same ``enabled`` toggle shape).

    Attributes:
        enabled: Uniform gate toggle (D-2, D-17). Default
            ``constants.GATE_ENABLED_DEFAULT`` (``False``). The resolved
            value consumed by gate commands is computed by the four-layer
            precedence resolver, ``resolve_gate_config``; this dataclass
            only models the raw parsed project-level value.
        canonical_sources: Designated canonical fixture/dataset file(s).
        scan: Mock/fixture files to cross-reference against a canonical
            source.
        extract_source_literals: Reserved for a future literal-extraction
            scanning mode (spec 4.7 hardening). Default
            ``constants.GATE_EXTRACT_SOURCE_LITERALS_DEFAULT`` (``False``);
            not yet consumed by ``check_fixture_consistency``.
    """

    enabled: bool = GATE_ENABLED_DEFAULT
    canonical_sources: tuple[FixtureCanonicalSource, ...] = ()
    scan: tuple[FixtureScanTarget, ...] = ()
    extract_source_literals: bool = GATE_EXTRACT_SOURCE_LITERALS_DEFAULT


@dataclass(frozen=True)
class TaskFactoryConfig:
    """Per-backlog task-factory configuration.

    Controls whether the orchestrator invokes blocker-resolver + task-factory
    after an amendment reject to materialise draft work-unit files for the
    out-of-scope production fixes the amender surfaced. A materialised
    draft's initial ``## Status:`` is always governed by
    ``BacklogConfig.default_status_for_new_work_units`` (default
    ``in-queue``), independent of both fields below.

    Attributes:
        enabled: Whether the task-factory loop runs. Defaults to ``True``
            (D-11, ADR-32, superseding the PR #202 shipped auto-promote-by-
            default posture, not ADR-11): the loop is on for every backlog
            unless explicitly disabled. Requires
            ``manifest_amendment.enabled: true`` (task-factory runs from
            the amendment-reject path); see ``load_runtime_config`` for the
            defaults-versus-amendment interaction contract when
            ``manifest_amendment.enabled`` is explicitly ``false``.
        auto_accept_proposals: Governs two independent auto-promote paths.
            (1) ``devbench write-proposal`` itself: when ``True``, it
            synchronously also calls ``materialise-proposal`` (and
            ``promote-proposal`` for any of the just-written ids that
            happen to already sit at legacy status ``proposed``) inside the
            same invocation, so drafts land immediately instead of waiting
            for the next ``sweep-proposals`` tick; when ``False`` (the
            default), ``write-proposal`` only persists the JSON and
            materialisation happens later via ``sweep-proposals`` or a
            manual ``materialise-proposal`` call. (2) ``devbench
            sweep-proposals``: when ``True``, it also auto-promotes any
            draft explicitly sitting at status ``proposed`` (a
            legacy/hand-edited-draft case -- the normal materialise path
            has not written that status since AC-189-8 shipped); when
            ``False``, such orphaned ``proposed`` drafts wait for an
            explicit ``promote-proposal``/``reject-proposal`` decision.
            Neither path affects the initial ``## Status:`` value written
            into a freshly materialised draft -- that is always
            ``BacklogConfig.default_status_for_new_work_units``. Default
            ``False`` (D-11, ADR-32). Only takes effect when ``enabled`` is
            true.
    """

    enabled: bool = True
    auto_accept_proposals: bool = False


@dataclass(frozen=True)
class AmendmentConfig:
    """Per-backlog Changes Manifest amendment workflow configuration.

    Loaded from the ``manifest_amendment`` YAML section (defaults on).
    Consumed by the Layer 1 PreFilter in ``devbench.backlog.amendment``.

    Attributes:
        enabled: Whether the amendment workflow is active for this backlog.
            Default ``True`` -- set ``false`` to opt out.
        allowed_reasons: Set of amendment reasons this backlog accepts.
            Requests whose reason is not in this set are rejected by the
            pre-filter. Default ``{"tdd_green_production_fix",
            "doc_sync_review_fix"}`` (db-327 Leg A1).
        max_requests_per_execution: Upper bound on amendments applied to a
            single task during one executor run; prevents amendment loops.
    """

    enabled: bool = True
    allowed_reasons: frozenset[str] = field(
        default_factory=lambda: frozenset({"tdd_green_production_fix", "doc_sync_review_fix"})
    )
    max_requests_per_execution: int = 1


# The eight integration-reality gates (spec 4.1; caylent-solutions/devbench-internal-backlog#10..#17).
# Domain vocabulary, single-sourced from the ordered ``constants.GATE_NAMES``
# tuple (also consumed for `devbench gates` row order and the resolver's
# built-in-default lookup); this frozenset exists only for O(1) membership
# checks.
GATE_NAMES: frozenset[str] = frozenset(_GATE_NAMES_ORDERED)


@dataclass(frozen=True)
class GateEnabledConfig:
    """Project-level tunables for a gate with no tunable beyond ``enabled``.

    Shared by the five gates whose spec-4.1 tunable set is exactly
    ``{enabled}``: ancestry, write_path_audit, newly_reachable_paths,
    composition_root, layout_geometry. ``reachability`` moved to its own
    :class:`GateReachabilityConfig` (spec 4.4 bullet 2, issue #10 AC2) once
    it gained the ``entry_points`` tunable.

    Attributes:
        enabled: Whether this gate is enabled at the project level.
            Default ``constants.GATE_ENABLED_DEFAULT`` (``False``; D-17:
            every gate disabled at the built-in level). The resolved value
            consumed by gate commands is computed by the four-layer
            precedence resolver, ``resolve_gate_config``; this dataclass
            only models the raw parsed project-level value.
    """

    enabled: bool = GATE_ENABLED_DEFAULT


@dataclass(frozen=True)
class GateReachabilityConfig:
    """Project-level ``gates.reachability:`` tunables (spec 4.1, 4.4; issue #10 AC2).

    Attributes:
        enabled: Whether this gate is enabled at the project level.
            Default ``constants.GATE_ENABLED_DEFAULT`` (``False``). The
            resolved value consumed by gate commands is computed by the
            four-layer precedence resolver, ``resolve_gate_config``; this
            dataclass only models the raw parsed project-level value.
        entry_points: Repo-relative paths seeding the transitive
            reachability walk (issue #10 AC2, spec 4.4 bullet 2). The empty
            tuple -- the default, and also what an explicit empty YAML list
            parses to -- means "not overridden at the project level"; both
            cases resolve to the ``source_classification``-derived built-in
            default substituted by ``resolve_gate_config`` (AC-FUNC-006),
            never here. This dataclass only models the raw parsed
            project-level value; no per-repo override layer exists for this
            field (this campaign configures a single target repo, spec
            Section 9).
    """

    enabled: bool = GATE_ENABLED_DEFAULT
    entry_points: tuple[str, ...] = ()


@dataclass(frozen=True)
class GateSharedFileImpactConfig:
    """Project-level ``gates.shared_file_impact:`` tunables (spec 4.1, 4.6).

    Attributes:
        enabled: Whether this gate is enabled at the project level.
            Default ``constants.GATE_ENABLED_DEFAULT`` (``False``).
        auto_derive_registry: Reserved for the future auto-derived fan-in
            registry successor to the hand-maintained per-repo glob list
            (v1 is hand-maintained only, via ``gates.repos.<org/repo>
            .shared_file_impact.patterns``). Default
            ``constants.GATE_AUTO_DERIVE_REGISTRY_DEFAULT`` (``False``);
            not yet consumed by ``check-shared-file-impact``.
    """

    enabled: bool = GATE_ENABLED_DEFAULT
    auto_derive_registry: bool = GATE_AUTO_DERIVE_REGISTRY_DEFAULT


@dataclass(frozen=True)
class GateEnabledOverride:
    """Per-repo override for a gate with no tunable beyond ``enabled``.

    Attributes:
        enabled: ``None`` means "not overridden for this repo -- inherit
            the project-level value"; ``True``/``False`` explicitly flips
            the gate for this repo only (D-15 field-wise merge).
    """

    enabled: bool | None = None


@dataclass(frozen=True)
class GateSharedFileImpactOverride:
    """Per-repo override for ``gates.repos.<org/repo>.shared_file_impact``.

    The migrated home of PR #318's retired per-repo glob-pattern key
    (spec 4.1 migration): a repo's shared-file glob registry is now
    ``gates.repos.<org/repo>.shared_file_impact.patterns``.

    Attributes:
        enabled: ``None`` means "not overridden for this repo -- inherit
            the project-level value"; ``True``/``False`` explicitly flips
            the gate for this repo only.
        patterns: Glob patterns (fnmatch-style, matched against POSIX
            paths relative to the repo root) identifying shared/high-fan-in
            composition-root files for this repo. Empty tuple when unset,
            which means the gate never triggers on this repo's diffs even
            when enabled.
    """

    enabled: bool | None = None
    patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class GateRepoOverrides:
    """Per-repo gate overrides nested under ``gates.repos.<org/repo>`` (spec 4.1, D-15).

    Every field is ``None`` when the repo does not override that
    particular gate -- absence, not a zero-value object, so the (future)
    four-layer resolver can distinguish "not overridden" from "explicitly
    set to the built-in default value" during field-wise merge.

    Attributes:
        reachability: Override for the reachability gate, or ``None``.
        ancestry: Override for the ancestry gate, or ``None``.
        shared_file_impact: Override for the shared-file-impact gate
            (carries ``patterns`` in addition to ``enabled``), or ``None``.
        fixture_consistency: Override for the fixture-consistency gate, or
            ``None``.
        write_path_audit: Override for the write-path-audit gate, or
            ``None``.
        newly_reachable_paths: Override for the newly-reachable-paths
            gate, or ``None``.
        composition_root: Override for the composition-root gate, or
            ``None``.
        layout_geometry: Override for the layout-geometry gate, or
            ``None``.
    """

    reachability: GateEnabledOverride | None = None
    ancestry: GateEnabledOverride | None = None
    shared_file_impact: GateSharedFileImpactOverride | None = None
    fixture_consistency: GateEnabledOverride | None = None
    write_path_audit: GateEnabledOverride | None = None
    newly_reachable_paths: GateEnabledOverride | None = None
    composition_root: GateEnabledOverride | None = None
    layout_geometry: GateEnabledOverride | None = None


@dataclass(frozen=True)
class GatesConfig:
    """Unified ``gates:`` configuration tree (spec 4.1; D-2, D-15, D-17).

    Replaces the ad-hoc per-PR opt-in surfaces #318 (a per-repo glob-pattern
    key nested under ``repos:``) and #322 (a bare top-level opt-in block)
    shipped -- both REMOVED by this same change, with zero remaining
    references (spec 4.1 Migration; complete replacement). Every gate is
    disabled by default at this built-in level (D-17); ``resolve_gate_config``
    is the ONLY read path that resolves the full built-in -> project ->
    per-repo -> env four-layer precedence (D-15) -- this dataclass models
    the raw parsed project + per-repo-override layers only; no other
    module reads its fields directly (AC-27).

    Attributes:
        reachability: check-reachability gate tunables, including
            ``entry_points`` (spec 4.4 bullet 2, issue #10 AC2).
        ancestry: check-ancestry gate tunables.
        shared_file_impact: check-shared-file-impact gate tunables.
        fixture_consistency: check-fixture-consistency gate tunables.
        write_path_audit: write-path-audit gate tunables.
        newly_reachable_paths: newly-reachable-paths gate tunables.
        composition_root: composition-root gate tunables.
        layout_geometry: layout-geometry gate tunables.
        repos: Optional per-repo override map, keyed by ``org/repo``. Every
            key must already be present in the top-level ``repos:``
            mapping -- an override naming an unconfigured repo is a
            load-time error (AC-E2-F1-S1-T1-2).
    """

    reachability: GateReachabilityConfig = field(default_factory=GateReachabilityConfig)
    ancestry: GateEnabledConfig = field(default_factory=GateEnabledConfig)
    shared_file_impact: GateSharedFileImpactConfig = field(default_factory=GateSharedFileImpactConfig)
    fixture_consistency: FixtureConsistencyConfig = field(default_factory=FixtureConsistencyConfig)
    write_path_audit: GateEnabledConfig = field(default_factory=GateEnabledConfig)
    newly_reachable_paths: GateEnabledConfig = field(default_factory=GateEnabledConfig)
    composition_root: GateEnabledConfig = field(default_factory=GateEnabledConfig)
    layout_geometry: GateEnabledConfig = field(default_factory=GateEnabledConfig)
    repos: dict[str, GateRepoOverrides] = field(default_factory=dict)


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


@dataclass
class SkillsConfig:
    """Plugin-skill configuration loaded from the ``skills:`` YAML section.

    Controls how the bundled spec-to-backlog and create-spec skills resolve
    operator-facing knobs (exemplar paths, fan-out and iteration budgets).
    Every field is optional; when a workspace omits the section entirely
    each skill falls back to defaults baked into its SKILL.md prompt.

    Attributes:
        exemplar_backlog_path: Absolute or workspace-relative path to a
            representative ``BACKLOG.md`` the ``spec-to-backlog`` skill
            consults to internalise the project's quality bar. ``None``
            (the default) means the skill uses the canonical-section list
            embedded in its prompt as the sole quality reference (issue
            #221 E1).
        exemplar_spec_path: Absolute or workspace-relative path to a
            representative spec file the ``create-spec`` skill consults
            for its quality bar. ``None`` falls back to the 16-section
            structural skeleton embedded in the prompt (E2).
        fan_out_threshold: When the Epic decomposition produces strictly
            more than this many leaf tasks, the spec-to-backlog skill
            fans the per-task authoring out across one sub-Agent per
            Feature instead of writing tasks serially. Defaults to 10.
        max_iterations: Maximum self-critique iterations per skill
            invocation before emitting a ``[SKILL_MAX_ITERATIONS_REACHED]``
            audit comment with the unresolved rubric items. Defaults to 5.
    """

    exemplar_backlog_path: str | None = None
    exemplar_spec_path: str | None = None
    fan_out_threshold: int = _SKILLS_DEFAULT_FAN_OUT_THRESHOLD
    max_iterations: int = _SKILLS_DEFAULT_MAX_ITERATIONS


@dataclass
class QuotaHandlingConfig:
    """Quota wait-and-resume configuration (issue #236, spec S5.2).

    Loaded from the ``quota_handling:`` YAML section. Default is ON per
    spec S5.2. Operators opt out via ``quota_handling: {enabled: false}``
    to restore the legacy non-zero exit behaviour (#193 AC-4, spec AC-24).
    The quota core (detection, wait, probe, checkpoint, resume strategies)
    is delivered separately; this dataclass is the config surface the
    dispatcher reads to decide what that core does on each quota signal.

    Attributes:
        enabled: Master toggle. ``True`` (the default) enables
            wait-and-resume. ``False`` restores the legacy non-zero exit.
        on_exhaustion: What to do when a quota signal is detected. One of
            ``"wait"`` (default), ``"fail"`` (non-zero exit), ``"drain"``
            (request a drain and return).
        poll_interval_seconds: Polling cadence between recovery probes.
            Must be in ``[30, 3600]``. Default 60.
        max_wait_seconds: Maximum total wait in seconds. Must be >= 1.
            Default 18000 (5 hours).
        on_exhaustion_timeout: Action when ``max_wait_seconds`` elapses
            without recovery. One of ``"drain"`` (default), ``"fail"``,
            ``"keep_waiting"``.
        resume_strategy: How to resume after recovery. One of
            ``"continue_current_wu"`` (default), ``"restart_wu"``,
            ``"drain_and_resume"``.
        audit_comment_on_wait: When ``True`` (default), appends a
            ``[QUOTA_WAITING]`` comment to the in-progress work unit.
        audit_comment_on_resume: When ``True`` (default), appends a
            ``[QUOTA_RESUMED]`` comment after recovery.
        log_structured_events: When ``True`` (default), emits structured
            log events on quota transitions.
    """

    enabled: bool = True
    on_exhaustion: str = "wait"
    poll_interval_seconds: int = 60
    max_wait_seconds: int = 18000
    on_exhaustion_timeout: str = "drain"
    resume_strategy: str = "continue_current_wu"
    audit_comment_on_wait: bool = True
    audit_comment_on_resume: bool = True
    log_structured_events: bool = True


def _parse_model_rates(model_id: str, raw: object, source: str) -> ModelRates:
    """Parse one ``report.models.<id>`` entry into a ``ModelRates``.

    Issue #223.  The schema (``config-schema.json``) enforces shape with
    ``additionalProperties: false`` per model entry; this runtime helper
    validates ranges and converts the raw dict into the dataclass.  Raises
    ``ValueError`` with the offending model id and source path so operators
    see exactly which entry tripped the check.
    """
    if not isinstance(raw, dict):
        raise ValueError(
            f"Config file '{source}': report.models.{model_id!r} must be a mapping; got {type(raw).__name__}."
        )
    if "input" not in raw or "output" not in raw:
        raise ValueError(
            f"Config file '{source}': report.models.{model_id!r} missing required field "
            "(both 'input' and 'output' are mandatory per model entry)."
        )
    input_rate = float(raw["input"])
    output_rate = float(raw["output"])
    if input_rate < 0 or output_rate < 0:
        raise ValueError(
            f"Config file '{source}': report.models.{model_id!r} rates must be non-negative; "
            f"got input={input_rate}, output={output_rate}."
        )
    correction = float(raw.get("correction_factor", 1.0))
    if correction <= 0:
        raise ValueError(
            f"Config file '{source}': report.models.{model_id!r} correction_factor must be > 0; got {correction}."
        )
    return ModelRates(
        input=input_rate,
        output=output_rate,
        cache_read_multiplier=(float(raw["cache_read_multiplier"]) if "cache_read_multiplier" in raw else None),
        cache_write_5min_multiplier=(
            float(raw["cache_write_5min_multiplier"]) if "cache_write_5min_multiplier" in raw else None
        ),
        cache_write_1hr_multiplier=(
            float(raw["cache_write_1hr_multiplier"]) if "cache_write_1hr_multiplier" in raw else None
        ),
        correction_factor=correction,
    )


def _parse_report_models(raw: object, source: str) -> dict[str, ModelRates]:
    """Parse the ``report.models`` block (issue #223).

    Returns an empty mapping when the block is absent OR explicitly empty.
    Operators with an empty block fall back entirely to
    ``report.default_model`` for every observed model id, which is a valid
    minimal configuration for workspaces that only ever run one model.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"Config file '{source}': report.models must be a mapping of model-id -> rates; got {type(raw).__name__}."
        )
    return {model_id: _parse_model_rates(model_id, entry, source) for model_id, entry in raw.items()}


def _parse_default_model_rates(raw: object, source: str) -> ModelRates:
    """Parse the ``report.default_model`` block (issue #223).

    Falls back to ``DEFAULT_FALLBACK_MODEL_RATES`` when absent.  Operators
    on standard Anthropic pricing typically leave this unset; the default
    matches Opus 5 list pricing (issue #233) so an unknown-model bucket errs
    toward over-reporting cost rather than under-reporting.
    """
    if raw is None:
        return DEFAULT_FALLBACK_MODEL_RATES
    return _parse_model_rates("<default_model>", raw, source)


def _parse_skills_config(path: Path, skills_raw: dict) -> SkillsConfig:
    """Parse and validate the ``skills:`` YAML section into a ``SkillsConfig``.

    Args:
        path: Config file path (used in error messages).
        skills_raw: Raw ``skills`` dict from YAML (already schema-validated
            for unknown keys and types). May be an empty dict when the
            section is absent.

    Returns:
        ``SkillsConfig`` populated from *skills_raw*.

    Raises:
        ValueError: If ``fan_out_threshold`` or ``max_iterations`` is
            present but not a positive integer (the schema enforces
            ``minimum: 1``; this is the defensive runtime re-check).
    """
    exemplar_backlog = skills_raw.get("exemplar_backlog_path") or None
    exemplar_spec = skills_raw.get("exemplar_spec_path") or None
    fan_out_raw = skills_raw.get("fan_out_threshold", _SKILLS_DEFAULT_FAN_OUT_THRESHOLD)
    max_iter_raw = skills_raw.get("max_iterations", _SKILLS_DEFAULT_MAX_ITERATIONS)
    fan_out = int(fan_out_raw)
    if fan_out < 1:
        raise ValueError(f"Config file '{path}': skills.fan_out_threshold must be >= 1; got {fan_out_raw!r}.")
    max_iter = int(max_iter_raw)
    if max_iter < 1:
        raise ValueError(f"Config file '{path}': skills.max_iterations must be >= 1; got {max_iter_raw!r}.")
    return SkillsConfig(
        exemplar_backlog_path=str(exemplar_backlog) if exemplar_backlog else None,
        exemplar_spec_path=str(exemplar_spec) if exemplar_spec else None,
        fan_out_threshold=fan_out,
        max_iterations=max_iter,
    )


def _parse_quota_handling_config(path: Path, raw: dict) -> QuotaHandlingConfig:
    """Parse and validate the ``quota_handling:`` YAML section.

    Args:
        path: Config file path (used in error messages).
        raw: Raw ``quota_handling`` dict from YAML (already schema-validated
            for unknown keys and enum membership). May be an empty dict when
            the section is absent -- an absent block yields the full S5.2
            default set, never a partial or ``None`` object.

    Returns:
        ``QuotaHandlingConfig`` populated from *raw*.

    Raises:
        ValueError: When an enum field has an invalid value or a range
            field is out of bounds. Rejection happens here, at config-load
            time, never deferred to dispatch time (FR-2.9).
    """
    defaults = QuotaHandlingConfig()

    on_exhaustion = raw.get("on_exhaustion", defaults.on_exhaustion)
    if on_exhaustion not in _QUOTA_HANDLING_VALID_ON_EXHAUSTION:
        valid = ", ".join(sorted(_QUOTA_HANDLING_VALID_ON_EXHAUSTION))
        raise ValueError(
            f"Config file '{path}': quota_handling.on_exhaustion {on_exhaustion!r} is not one of [{valid}]."
        )

    on_exhaustion_timeout = raw.get("on_exhaustion_timeout", defaults.on_exhaustion_timeout)
    if on_exhaustion_timeout not in _QUOTA_HANDLING_VALID_ON_EXHAUSTION_TIMEOUT:
        valid = ", ".join(sorted(_QUOTA_HANDLING_VALID_ON_EXHAUSTION_TIMEOUT))
        raise ValueError(
            f"Config file '{path}': quota_handling.on_exhaustion_timeout {on_exhaustion_timeout!r} "
            f"is not one of [{valid}]."
        )

    resume_strategy = raw.get("resume_strategy", defaults.resume_strategy)
    if resume_strategy not in _QUOTA_HANDLING_VALID_RESUME_STRATEGY:
        valid = ", ".join(sorted(_QUOTA_HANDLING_VALID_RESUME_STRATEGY))
        raise ValueError(
            f"Config file '{path}': quota_handling.resume_strategy {resume_strategy!r} is not one of [{valid}]."
        )

    poll_interval_seconds = int(raw.get("poll_interval_seconds", defaults.poll_interval_seconds))
    if not (_QUOTA_HANDLING_POLL_INTERVAL_MIN <= poll_interval_seconds <= _QUOTA_HANDLING_POLL_INTERVAL_MAX):
        raise ValueError(
            f"Config file '{path}': quota_handling.poll_interval_seconds {poll_interval_seconds!r} "
            f"must be in [{_QUOTA_HANDLING_POLL_INTERVAL_MIN}, {_QUOTA_HANDLING_POLL_INTERVAL_MAX}]."
        )

    max_wait_seconds = int(raw.get("max_wait_seconds", defaults.max_wait_seconds))
    if max_wait_seconds < _QUOTA_HANDLING_MAX_WAIT_MIN:
        raise ValueError(
            f"Config file '{path}': quota_handling.max_wait_seconds {max_wait_seconds!r} "
            f"must be >= {_QUOTA_HANDLING_MAX_WAIT_MIN}."
        )

    return QuotaHandlingConfig(
        enabled=bool(raw.get("enabled", defaults.enabled)),
        on_exhaustion=on_exhaustion,
        poll_interval_seconds=poll_interval_seconds,
        max_wait_seconds=max_wait_seconds,
        on_exhaustion_timeout=on_exhaustion_timeout,
        resume_strategy=resume_strategy,
        audit_comment_on_wait=bool(raw.get("audit_comment_on_wait", defaults.audit_comment_on_wait)),
        audit_comment_on_resume=bool(raw.get("audit_comment_on_resume", defaults.audit_comment_on_resume)),
        log_structured_events=bool(raw.get("log_structured_events", defaults.log_structured_events)),
    )


def _parse_task_factory_config(
    path: Path, task_factory_raw: dict, manifest_amendment: AmendmentConfig
) -> TaskFactoryConfig:
    """Parse and validate the ``task_factory:`` YAML section (ADR-32, D-11).

    Args:
        path: Config file path (used in error messages).
        task_factory_raw: Raw ``task_factory`` dict from YAML (already
            schema-validated for unknown keys and value types). May be an
            empty dict when the section is absent -- an absent block yields
            the on-by-default ``TaskFactoryConfig()`` defaults.
        manifest_amendment: The already-resolved ``AmendmentConfig`` for this
            load, needed for the cross-field interaction check below.

    Returns:
        ``TaskFactoryConfig`` populated from *task_factory_raw*.

    Raises:
        ValueError: When ``task_factory.enabled`` is EXPLICITLY ``true`` in
            the YAML while ``manifest_amendment.enabled`` resolves ``false``.
            Task-factory runs from the amendment-reject path, so this is a
            real contradiction the operator must fix.

    Interaction contract (ADR-32): a DEFAULTED-on ``enabled`` (the key was
    omitted from *task_factory_raw*) against an explicitly disabled
    ``manifest_amendment.enabled: false`` is NOT a contradiction -- it is the
    spec Section 0 B-8 migration case, an existing backlog that predates the
    D-11 defaults flip and never mentions ``task_factory``. That combination
    downgrades ``enabled`` to ``False`` silently instead of bricking
    config-load; the loop has nothing to do without the amendment workflow it
    runs from either way.
    """
    defaults = TaskFactoryConfig()
    enabled_explicit = "enabled" in task_factory_raw
    enabled = bool(task_factory_raw.get("enabled", defaults.enabled))
    if enabled and not manifest_amendment.enabled:
        if enabled_explicit:
            raise ValueError(
                f"Config file '{path}': task_factory.enabled: true requires manifest_amendment.enabled: true. "
                "Task-factory runs from the amendment-reject path; it has nothing to do when amendments are off."
            )
        enabled = False
    return TaskFactoryConfig(
        enabled=enabled,
        auto_accept_proposals=bool(task_factory_raw.get("auto_accept_proposals", defaults.auto_accept_proposals)),
    )


def _parse_fixture_consistency_config(path: Path, raw: dict) -> FixtureConsistencyConfig:
    """Parse and validate the ``gates.fixture_consistency:`` YAML section (spec 4.1, 4.7).

    Args:
        path: Config file path (used in error messages).
        raw: Raw ``gates.fixture_consistency`` dict from YAML (already
            schema-validated for unknown keys and types). May be an empty
            dict when the section is absent -- that is the default,
            opt-out state (the check becomes a no-op regardless of
            ``enabled``).

    Returns:
        A populated ``FixtureConsistencyConfig``.

    Raises:
        ValueError: If ``enabled`` or ``extract_source_literals`` is not a
            boolean; if a ``canonical_sources`` entry has a non-positive
            ``expected_count``; if any ``scan`` entry names a
            ``canonical_source`` that does not match a configured
            ``canonical_sources[].path``; or if a ``scan`` entry omits
            ``canonical_source`` while more than one ``canonical_sources``
            entry is configured (ambiguous target).
    """
    defaults = FixtureConsistencyConfig()
    enabled = _parse_gate_enabled_field(path, "gates.fixture_consistency", raw, defaults.enabled)

    extract_source_literals = raw.get("extract_source_literals", defaults.extract_source_literals)
    if not isinstance(extract_source_literals, bool):
        raise ValueError(
            f"Config file '{path}': gates.fixture_consistency.extract_source_literals must be a "
            f"boolean (true/false), got {type(extract_source_literals).__name__} "
            f"({extract_source_literals!r})."
        )

    canonical_raw = raw.get("canonical_sources") or []
    canonical_sources: list[FixtureCanonicalSource] = []
    for entry in canonical_raw:
        expected_count = entry.get("expected_count")
        if expected_count is not None:
            expected_count = int(expected_count)
            if expected_count < 1:
                raise ValueError(
                    f"Config file '{path}': gates.fixture_consistency.canonical_sources entry "
                    f"'{entry.get('path')}' has expected_count={expected_count!r}; must be >= 1."
                )
        canonical_sources.append(
            FixtureCanonicalSource(
                path=str(entry["path"]),
                identifier_field=str(entry["identifier_field"]),
                expected_count=expected_count,
            )
        )

    canonical_paths = {source.path for source in canonical_sources}
    scan_raw = raw.get("scan") or []
    scan_targets: list[FixtureScanTarget] = []
    for entry in scan_raw:
        canonical_source = entry.get("canonical_source") or None
        if canonical_source is None:
            if len(canonical_sources) == 1:
                canonical_source = canonical_sources[0].path
            elif len(canonical_sources) > 1:
                raise ValueError(
                    f"Config file '{path}': gates.fixture_consistency.scan entry '{entry.get('path')}' "
                    "does not set canonical_source, and more than one canonical_sources entry is "
                    f"configured ({sorted(canonical_paths)}); the target is ambiguous. Set "
                    "canonical_source to one of the configured canonical_sources[].path values."
                )
        elif canonical_source not in canonical_paths:
            raise ValueError(
                f"Config file '{path}': gates.fixture_consistency.scan entry '{entry.get('path')}' sets "
                f"canonical_source={canonical_source!r}, which does not match any configured "
                f"canonical_sources[].path ({sorted(canonical_paths)})."
            )
        scan_targets.append(
            FixtureScanTarget(
                path=str(entry["path"]),
                identifier_field=str(entry["identifier_field"]),
                canonical_source=canonical_source,
                allow_missing=frozenset(str(v) for v in entry.get("allow_missing") or []),
            )
        )

    return FixtureConsistencyConfig(
        enabled=enabled,
        canonical_sources=tuple(canonical_sources),
        scan=tuple(scan_targets),
        extract_source_literals=extract_source_literals,
    )


def _parse_gate_enabled_field(path: Path, key: str, raw: dict, default: bool = GATE_ENABLED_DEFAULT) -> bool:
    """Parse and validate one gate's ``enabled`` field, shared by every gate parser.

    Args:
        path: Config file path (used in error messages).
        key: Dotted YAML key prefix (e.g. ``gates.reachability`` or
            ``gates.repos.org/repo.ancestry``) used to build the error
            message and to disambiguate which gate/override is at fault.
        raw: The gate's own raw dict (already schema-validated for type
            when reached through ``load_runtime_config``; re-validated
            here so a direct call bypassing the schema layer still fails
            fast, matching every other ``_parse_*_config`` helper in this
            module).
        default: Value to return when ``enabled`` is absent from *raw*.

    Returns:
        The validated boolean value, or *default* when absent.

    Raises:
        ValueError: If ``enabled`` is present but not a boolean.
    """
    if "enabled" not in raw:
        return default
    value = raw["enabled"]
    if not isinstance(value, bool):
        raise ValueError(
            f"Config file '{path}': {key}.enabled must be a boolean (true/false), got "
            f"{type(value).__name__} ({value!r})."
        )
    return value


def _parse_simple_gate_enabled(path: Path, key: str, gate_raw: object) -> GateEnabledConfig:
    """Parse one of the five enabled-only gates (spec 4.1: everything except
    reachability, shared_file_impact and fixture_consistency).

    Args:
        path: Config file path (used in error messages).
        key: Dotted YAML key for this gate (e.g. ``gates.ancestry``).
        gate_raw: Raw value from YAML for this gate -- ``None`` when the
            gate key is absent from ``gates:``, otherwise a dict (schema-
            validated).

    Returns:
        ``GateEnabledConfig`` populated from *gate_raw*, or the built-in
        default (``enabled=False``) when *gate_raw* is ``None``.

    Raises:
        ValueError: If *gate_raw* is present but not a mapping, or its
            ``enabled`` field is not a boolean.
    """
    if gate_raw is None:
        return GateEnabledConfig()
    if not isinstance(gate_raw, dict):
        raise ValueError(f"Config file '{path}': {key} must be a mapping, got {type(gate_raw).__name__}.")
    return GateEnabledConfig(enabled=_parse_gate_enabled_field(path, key, gate_raw))


def _parse_reachability_entry_points(path: Path, raw: object) -> tuple[str, ...]:
    """Parse and validate ``gates.reachability.entry_points`` (spec 4.1, 4.4; AC-5, AC-FUNC-005).

    Args:
        path: Config file path (used in error messages).
        raw: Raw value from YAML for ``entry_points`` -- ``None`` when the
            key is absent from the ``gates.reachability`` block.

    Returns:
        A tuple of the configured repo-relative paths, in the order
        supplied. The empty tuple when *raw* is ``None`` (absent) --
        ``resolve_gate_config`` substitutes the
        ``source_classification``-derived built-in default in that case
        (AC-FUNC-006), never this function.

    Raises:
        ValueError: If *raw* is present but not a list; if any element is
            not a string; if any element is an empty string; if any
            element is an absolute path; or if any element contains a
            parent-traversal (``..``) segment.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(
            f"Config file '{path}': gates.reachability.entry_points must be a list of repo-relative "
            f"path strings, got {type(raw).__name__} ({raw!r})."
        )
    entry_points: list[str] = []
    for element in raw:
        if not isinstance(element, str):
            raise ValueError(
                f"Config file '{path}': gates.reachability.entry_points must contain only strings; "
                f"found {type(element).__name__} ({element!r})."
            )
        if not element:
            raise ValueError(f"Config file '{path}': gates.reachability.entry_points must not contain an empty string.")
        # code_review round-2 MISSING_AC_EVIDENCE finding: an absolute path or a
        # `..` escape defeats `cli._reachability_missing_entry_point`'s existence
        # guard, because `repo_path / element` DISCARDS `repo_path` for an
        # absolute right operand (pathlib's documented `/` behaviour), letting
        # `.is_file()` pass for a file outside the checkout that can then never
        # match `_matches_reachability_entry_point`'s repo-relative comparison --
        # exactly the false-orphan-from-an-empty-root-set outcome the Error
        # Handling Contract's missing-entry-point bullet exists to prevent.
        # Rejected here, at the single parse boundary, so no caller can ever
        # observe an unsafe path (spec 4.4's own "a list of repo-relative
        # paths" contract).
        if Path(element).is_absolute():
            raise ValueError(
                f"Config file '{path}': gates.reachability.entry_points must contain only repo-relative "
                f"paths, got absolute path {element!r}."
            )
        if ".." in Path(element).parts:
            raise ValueError(
                f"Config file '{path}': gates.reachability.entry_points must not contain parent "
                f"traversal ('..'), got {element!r}."
            )
        entry_points.append(element)
    return tuple(entry_points)


def _parse_reachability_gate(path: Path, gate_raw: object) -> GateReachabilityConfig:
    """Parse the project-level ``gates.reachability:`` YAML section (spec 4.1, 4.4; issue #10 AC2).

    Args:
        path: Config file path (used in error messages).
        gate_raw: Raw value from YAML -- ``None`` when the key is absent,
            otherwise a dict (schema-validated).

    Returns:
        ``GateReachabilityConfig`` populated from *gate_raw*, or the
        built-in default (``enabled=False``, ``entry_points=()``) when
        *gate_raw* is ``None``.

    Raises:
        ValueError: If *gate_raw* is present but not a mapping; if its
            ``enabled`` field is not a boolean; or per
            :func:`_parse_reachability_entry_points`'s ``entry_points``
            validation.
    """
    defaults = GateReachabilityConfig()
    if gate_raw is None:
        return defaults
    if not isinstance(gate_raw, dict):
        raise ValueError(f"Config file '{path}': gates.reachability must be a mapping, got {type(gate_raw).__name__}.")
    enabled = _parse_gate_enabled_field(path, "gates.reachability", gate_raw, defaults.enabled)
    entry_points = _parse_reachability_entry_points(path, gate_raw.get("entry_points"))
    return GateReachabilityConfig(enabled=enabled, entry_points=entry_points)


def _parse_shared_file_impact_gate(path: Path, gate_raw: object) -> GateSharedFileImpactConfig:
    """Parse the project-level ``gates.shared_file_impact:`` YAML section.

    Args:
        path: Config file path (used in error messages).
        gate_raw: Raw value from YAML -- ``None`` when the key is absent,
            otherwise a dict (schema-validated).

    Returns:
        ``GateSharedFileImpactConfig`` populated from *gate_raw*.

    Raises:
        ValueError: If *gate_raw* is present but not a mapping, or
            ``enabled``/``auto_derive_registry`` is not a boolean.
    """
    defaults = GateSharedFileImpactConfig()
    if gate_raw is None:
        return defaults
    if not isinstance(gate_raw, dict):
        raise ValueError(
            f"Config file '{path}': gates.shared_file_impact must be a mapping, got {type(gate_raw).__name__}."
        )
    enabled = _parse_gate_enabled_field(path, "gates.shared_file_impact", gate_raw, defaults.enabled)
    auto_derive_registry = gate_raw.get("auto_derive_registry", defaults.auto_derive_registry)
    if not isinstance(auto_derive_registry, bool):
        raise ValueError(
            f"Config file '{path}': gates.shared_file_impact.auto_derive_registry must be a boolean "
            f"(true/false), got {type(auto_derive_registry).__name__} ({auto_derive_registry!r})."
        )
    return GateSharedFileImpactConfig(enabled=enabled, auto_derive_registry=auto_derive_registry)


def _parse_gate_override_enabled(path: Path, key: str, raw: dict) -> GateEnabledOverride:
    """Parse one per-repo enabled-only gate override.

    Args:
        path: Config file path (used in error messages).
        key: Dotted YAML key for this override (e.g.
            ``gates.repos.org/repo.reachability``).
        raw: The override's own raw dict.

    Returns:
        ``GateEnabledOverride`` with ``enabled=None`` when the override
        dict omits ``enabled`` (not overridden, inherit project level).

    Raises:
        ValueError: If ``enabled`` is present but not a boolean.
    """
    if "enabled" not in raw:
        return GateEnabledOverride()
    value = raw["enabled"]
    if not isinstance(value, bool):
        raise ValueError(
            f"Config file '{path}': {key}.enabled must be a boolean (true/false), got "
            f"{type(value).__name__} ({value!r})."
        )
    return GateEnabledOverride(enabled=value)


def _parse_gate_override_shared_file_impact(path: Path, key: str, raw: dict) -> GateSharedFileImpactOverride:
    """Parse the per-repo ``gates.repos.<org/repo>.shared_file_impact`` override.

    Args:
        path: Config file path (used in error messages).
        key: Dotted YAML key for this override.
        raw: The override's own raw dict.

    Returns:
        ``GateSharedFileImpactOverride`` with ``enabled=None`` when the
        override omits ``enabled`` (not overridden) and ``patterns=()``
        when omitted.

    Raises:
        ValueError: If ``enabled`` is present but not a boolean.
    """
    enabled: bool | None = None
    if "enabled" in raw:
        value = raw["enabled"]
        if not isinstance(value, bool):
            raise ValueError(
                f"Config file '{path}': {key}.enabled must be a boolean (true/false), got "
                f"{type(value).__name__} ({value!r})."
            )
        enabled = value
    patterns = tuple(raw.get("patterns") or ())
    return GateSharedFileImpactOverride(enabled=enabled, patterns=patterns)


def _parse_one_gate_repo_override(path: Path, repo_name: str, raw: dict) -> GateRepoOverrides:
    """Parse the full set of gate overrides configured for one repo.

    Args:
        path: Config file path (used in error messages).
        repo_name: The ``org/repo`` key this override block belongs to.
        raw: Raw dict of ``{gate_name: gate_override_dict}`` for this repo.

    Returns:
        ``GateRepoOverrides`` with only the explicitly-configured gate
        fields populated; every other field stays ``None``.

    Raises:
        ValueError: If a key is not one of the eight declared gate names,
            or a gate's override value is not a mapping.
    """
    reachability: GateEnabledOverride | None = None
    ancestry: GateEnabledOverride | None = None
    shared_file_impact: GateSharedFileImpactOverride | None = None
    fixture_consistency: GateEnabledOverride | None = None
    write_path_audit: GateEnabledOverride | None = None
    newly_reachable_paths: GateEnabledOverride | None = None
    composition_root: GateEnabledOverride | None = None
    layout_geometry: GateEnabledOverride | None = None

    for gate_name, gate_raw in raw.items():
        if gate_name not in GATE_NAMES:
            raise ValueError(
                f"Config file '{path}': gates.repos.{repo_name}.{gate_name} is not a recognised gate "
                f"name; valid gate names are {sorted(GATE_NAMES)}."
            )
        key = f"gates.repos.{repo_name}.{gate_name}"
        if not isinstance(gate_raw, dict):
            raise ValueError(f"Config file '{path}': {key} must be a mapping, got {type(gate_raw).__name__}.")
        if gate_name == "reachability":
            reachability = _parse_gate_override_enabled(path, key, gate_raw)
        elif gate_name == "ancestry":
            ancestry = _parse_gate_override_enabled(path, key, gate_raw)
        elif gate_name == "shared_file_impact":
            shared_file_impact = _parse_gate_override_shared_file_impact(path, key, gate_raw)
        elif gate_name == "fixture_consistency":
            fixture_consistency = _parse_gate_override_enabled(path, key, gate_raw)
        elif gate_name == "write_path_audit":
            write_path_audit = _parse_gate_override_enabled(path, key, gate_raw)
        elif gate_name == "newly_reachable_paths":
            newly_reachable_paths = _parse_gate_override_enabled(path, key, gate_raw)
        elif gate_name == "composition_root":
            composition_root = _parse_gate_override_enabled(path, key, gate_raw)
        else:
            layout_geometry = _parse_gate_override_enabled(path, key, gate_raw)

    return GateRepoOverrides(
        reachability=reachability,
        ancestry=ancestry,
        shared_file_impact=shared_file_impact,
        fixture_consistency=fixture_consistency,
        write_path_audit=write_path_audit,
        newly_reachable_paths=newly_reachable_paths,
        composition_root=composition_root,
        layout_geometry=layout_geometry,
    )


def _parse_gate_repo_overrides(
    path: Path, repos_raw: dict, configured_repos: dict[str, RepoConfig]
) -> dict[str, GateRepoOverrides]:
    """Parse the optional ``gates.repos:`` per-repo override map.

    Args:
        path: Config file path (used in error messages).
        repos_raw: Raw ``gates.repos`` dict from YAML. May be empty when
            the section is absent.
        configured_repos: The already-parsed top-level ``repos:`` mapping
            -- every override key must already be a member of this dict.

    Returns:
        Mapping of ``org/repo`` -> ``GateRepoOverrides``.

    Raises:
        ValueError: If an override names a repo absent from
            *configured_repos* (AC-E2-F1-S1-T1-2).
    """
    overrides: dict[str, GateRepoOverrides] = {}
    for repo_key, repo_gate_raw in repos_raw.items():
        repo_name = str(repo_key)
        if repo_name not in configured_repos:
            raise ValueError(
                f"Config file '{path}': gates.repos.{repo_name} overrides a repo that is not "
                f"configured under the top-level repos: mapping ({sorted(configured_repos)}). Add "
                f"'{repo_name}' to repos: or remove this override."
            )
        if not isinstance(repo_gate_raw, dict):
            raise ValueError(
                f"Config file '{path}': gates.repos.{repo_name} must be a mapping, got {type(repo_gate_raw).__name__}."
            )
        overrides[repo_name] = _parse_one_gate_repo_override(path, repo_name, repo_gate_raw)
    return overrides


def _parse_gates_config(path: Path, gates_raw: dict, repos: dict[str, RepoConfig]) -> GatesConfig:
    """Parse and validate the ``gates:`` YAML section (spec 4.1; D-2, D-15, D-17).

    Modelled on ``_parse_task_factory_config``: schema validation already
    rejects unknown keys and gross type errors via ``additionalProperties:
    false``; this function re-validates independently (defense in depth,
    matching every other ``_parse_*_config`` helper in this module) so a
    direct call bypassing the schema layer still fails fast with a message
    naming the offending key.

    Args:
        path: Config file path (used in error messages).
        gates_raw: Raw ``gates`` dict from YAML. May be an empty dict when
            the section is absent -- that yields the all-disabled built-in
            tree (AC-E2-F1-S1-T1-4).
        repos: The already-parsed top-level ``repos:`` mapping, needed to
            validate ``gates.repos`` override keys.

    Returns:
        ``GatesConfig`` populated from *gates_raw*.

    Raises:
        ValueError: If ``gates_raw`` names a key that is not one of the
            eight declared gates (plus the optional ``repos`` override
            map); if any gate's ``enabled`` (or other tunable) is the
            wrong type; or if ``gates.repos`` overrides a repo absent from
            *repos*.
    """
    unknown = set(gates_raw) - GATE_NAMES - {"repos"}
    if unknown:
        raise ValueError(
            f"Config file '{path}': gates section names unknown gate(s) {sorted(unknown)}; valid "
            f"gate names are {sorted(GATE_NAMES)} (plus the optional 'repos' override map)."
        )
    return GatesConfig(
        reachability=_parse_reachability_gate(path, gates_raw.get("reachability")),
        ancestry=_parse_simple_gate_enabled(path, "gates.ancestry", gates_raw.get("ancestry")),
        shared_file_impact=_parse_shared_file_impact_gate(path, gates_raw.get("shared_file_impact")),
        fixture_consistency=_parse_fixture_consistency_config(path, gates_raw.get("fixture_consistency") or {}),
        write_path_audit=_parse_simple_gate_enabled(path, "gates.write_path_audit", gates_raw.get("write_path_audit")),
        newly_reachable_paths=_parse_simple_gate_enabled(
            path, "gates.newly_reachable_paths", gates_raw.get("newly_reachable_paths")
        ),
        composition_root=_parse_simple_gate_enabled(path, "gates.composition_root", gates_raw.get("composition_root")),
        layout_geometry=_parse_simple_gate_enabled(path, "gates.layout_geometry", gates_raw.get("layout_geometry")),
        repos=_parse_gate_repo_overrides(path, gates_raw.get("repos") or {}, repos),
    )


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
    # Quota wait-and-resume lifecycle (spec FR-2.10, ADR-24).  Schema keys
    # landed in E2-F2-S1-T1; these fields close the gap so the dispatcher
    # can actually observe them via NotificationsConfig.events.
    quota_waiting: bool = False
    quota_resumed: bool = False


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
        quota_waiting=bool(events_raw.get("quota_waiting", defaults.events.quota_waiting)),
        quota_resumed=bool(events_raw.get("quota_resumed", defaults.events.quota_resumed)),
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
        branch_prefix: Per-repo task-branch prefix override.  When ``None``,
            the top-level ``GitOpsConfig.branch_prefix`` is used.  Inserted
            between ``backlog/`` and the lowercased unit ID (e.g. prefix
            ``wg_004`` yields ``backlog/wg_004/e1-f1-s1-t1``).  Use this to
            avoid task-branch collisions when multiple devbench workspaces
            push to the same shared repo.
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
    branch_prefix: str | None = None
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
        code_reviewer: Override for ``plugin/devbench-orchestrate/agents/review_team/code-reviewer.md``.
        test_reviewer: Override for ``plugin/devbench-orchestrate/agents/review_team/test-reviewer.md``.
        doc_reviewer: Override for ``plugin/devbench-orchestrate/agents/review_team/doc-reviewer.md``.
        changes_manifest: Override for ``plugin/devbench-orchestrate/agents/review_team/changes-manifest.md``.
    """

    code_reviewer: str | None = None
    test_reviewer: str | None = None
    doc_reviewer: str | None = None
    changes_manifest: str | None = None


@dataclass
class AgentModelsConfig:
    """Per-agent model overrides for the work-agents in the devbench plugin (ADR-25).

    Each field corresponds to one ``.md`` file under ``plugin/devbench-orchestrate/agents/``.
    When a field is ``None`` (the default), the agent runs on the model
    declared in its frontmatter. When set, ``devbench.plugin_shadow`` rewrites
    the frontmatter ``model:`` line in a workspace-local shadow copy and the
    Agent SDK / ``claude --plugin-dir`` is pointed at the shadow.

    Operators set this so they can manage Sonnet / Opus / Bedrock quota
    separately (e.g. drive ``executor`` on opus when sonnet quota is exhausted).
    ``config.py`` merges ``JUDGE_AGENT_MODEL_*`` env vars over the YAML values
    after this dataclass is constructed.

    Attributes:
        executor: Override for ``plugin/devbench-orchestrate/agents/executor.md``.
        blocker_resolver: Override for ``plugin/devbench-orchestrate/agents/blocker-resolver.md``.
        manifest_amender: Override for ``plugin/devbench-orchestrate/agents/manifest-amender.md``.
        security_reviewer: Override for ``plugin/devbench-orchestrate/agents/security-reviewer.md``.
        task_factory: Override for ``plugin/devbench-orchestrate/agents/task-factory.md``.
        review_supervisor: Override for ``plugin/devbench-orchestrate/agents/review-supervisor.md``.
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
                "model id while use_bedrock: true. Expected a cross-region "
                "inference-profile id: 'us.anthropic.claude-<name>' (e.g. "
                "'us.anthropic.claude-opus-5'), optionally with a dated version "
                "suffix (e.g. 'us.anthropic.claude-sonnet-4-5-20250929-v1:0'). "
                "Run 'aws bedrock list-inference-profiles' to see the ids enabled "
                "in your account and region."
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
        quota_handling: Quota wait-and-resume configuration (issue #236,
            spec S5.2). Absent YAML block yields the full default set.
        gates: Unified configuration tree for the eight integration-reality
            gates (spec 4.1; D-2, D-15, D-17), including the migrated
            ``gates.fixture_consistency`` opt-in canonical-fixture
            cross-reference configuration for ``devbench
            check-fixture-consistency``. Every gate is disabled by default;
            absent ``gates:`` yields the all-disabled built-in tree.
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
    quota_handling: QuotaHandlingConfig = field(default_factory=QuotaHandlingConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    manifest_amendment: AmendmentConfig = field(default_factory=AmendmentConfig)
    task_factory: TaskFactoryConfig = field(default_factory=TaskFactoryConfig)
    agent_models: AgentModelsConfig = field(default_factory=AgentModelsConfig)
    validate: ValidateConfig = field(default_factory=ValidateConfig)
    gates: GatesConfig = field(default_factory=GatesConfig)
    debug: DebugConfig = field(default_factory=DebugConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    allowed_orgs: list[str] = field(default_factory=list)
    use_bedrock: bool = False
    bedrock_region: str | None = None
    merge_strategy: str | None = "squash"
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


def _validate_branch_prefix(path: Path, key: str, value: str) -> None:
    """Validate a ``branch_prefix`` value shared by repo- and top-level config.

    Args:
        path: Config file path (used in error messages).
        key: Dotted YAML key the value came from (used in error messages).
        value: Raw prefix string to validate.

    Raises:
        ValueError: If *value* is empty, has leading/trailing ``/``, or
            contains a parent-traversal (``..``) segment.
    """
    if not value.strip("/"):
        raise ValueError(f"Config file '{path}': {key} must not be empty when set, got '{value}'.")
    if value != value.strip("/"):
        raise ValueError(f"Config file '{path}': {key} must not have leading or trailing '/', got '{value}'.")
    if ".." in Path(value).parts:
        raise ValueError(f"Config file '{path}': {key} must not contain parent traversal ('..'), got '{value}'.")


def _parse_branch_prefix(path: Path, key: str, raw_value: str | None) -> str | None:
    """Parse and validate an optional ``branch_prefix`` YAML value.

    Args:
        path: Config file path (used in error messages).
        key: Dotted YAML key the value came from (used in error messages).
        raw_value: Raw value from YAML (``None``/falsy means unset).

    Returns:
        The validated prefix string, or ``None`` when unset.
    """
    branch_prefix: str | None = raw_value or None
    if branch_prefix is not None:
        _validate_branch_prefix(path, key, branch_prefix)
    return branch_prefix


def _parse_repo_config(path: Path, repo_name: str, repo_data: object) -> RepoConfig:
    """Parse and validate a single repo entry from raw YAML.

    Args:
        path: Config file path (used in error messages).
        repo_name: The ``org/repo`` key.
        repo_data: Raw value from YAML (may be None or a dict after schema validation).

    Returns:
        ``RepoConfig`` populated from *repo_data*.

    Raises:
        ValueError: If *checkout_directory* is absolute or contains ``..``,
            or if *branch_prefix* is empty or has leading/trailing ``/``
            or a parent-traversal segment.
    """
    if not isinstance(repo_data, dict):
        return RepoConfig()

    default_branch: str | None = repo_data.get("default_branch")
    repo_merge_strategy: str | None = repo_data.get("merge_strategy")

    repo_branch_prefix = _parse_branch_prefix(path, f"repos.{repo_name}.branch_prefix", repo_data.get("branch_prefix"))

    raw_checkout = repo_data.get("checkout_directory")
    if raw_checkout is None:
        return RepoConfig(
            default_branch=default_branch,
            merge_strategy=repo_merge_strategy,
            branch_prefix=repo_branch_prefix,
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
        branch_prefix=repo_branch_prefix,
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
    Example: ``merge_strategy: 'never' is not one of ['merge', 'squash', 'rebase']``

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
        orchestrator_poll_interval=timeouts_raw.get("orchestrator_poll_interval"),
        github_check=timeouts_raw.get("github_check"),
        orchestrator_inactivity=timeouts_raw.get("orchestrator_inactivity"),
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
    branch_prefix_raw = _parse_branch_prefix(path, "git_ops.branch_prefix", git_ops_raw.get("branch_prefix"))
    provenance_path_raw = git_ops_raw.get("provenance_path") or None
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
        branch_prefix=branch_prefix_raw,
        provenance_path=provenance_path_raw,
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

    # Populate ReportConfig from YAML report block.  Issue #223: per-model
    # pricing replaces the legacy scalar token_cost_per_million_* +
    # token_cost_discount fields.  Operators with the old keys get a
    # fail-fast error pointing at the new ``report.models`` block.
    report_raw = raw.get("report") or {}
    legacy_report_keys = {
        "token_cost_per_million_input",
        "token_cost_per_million_output",
        "token_cost_discount",
    }
    legacy_present = sorted(legacy_report_keys & set(report_raw.keys()))
    if legacy_present:
        raise ValueError(
            "Config file '"
            + str(path)
            + "' contains removed report fields: "
            + ", ".join(legacy_present)
            + ". These were retired in issue #223 (per-model cost pricing). Replace with a "
            + "`report.models` block listing per-model rates; see docs/model-pricing.md for the "
            + "default rate table. Each model id maps to {input, output, "
            + "[cache_read_multiplier], [cache_write_5min_multiplier], [cache_write_1hr_multiplier], "
            + "[correction_factor]}. `report.default_model` is applied to any observed model id "
            + "not present in `report.models`."
        )
    report = ReportConfig(
        models=_parse_report_models(report_raw.get("models"), str(path)),
        default_model=_parse_default_model_rates(report_raw.get("default_model"), str(path)),
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

    # Populate TaskFactory config from YAML task_factory block (ADR-32, D-11).
    # See _parse_task_factory_config for the defaults-versus-amendment
    # interaction contract.
    task_factory = _parse_task_factory_config(path, raw.get("task_factory") or {}, manifest_amendment)

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

    # Populate GatesConfig from YAML gates block (spec 4.1; D-2, D-15, D-17).
    # Absent block -> default-constructed all-disabled tree (AC-E2-F1-S1-T1-4).
    # Nests the migrated gates.fixture_consistency surface (superseding the
    # removed top-level fixture_consistency: block, caylent-solutions/
    # devbench-internal-backlog#17).
    gates_raw = raw.get("gates") or {}
    gates = _parse_gates_config(path, gates_raw, repos)

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

    # Populate QuotaHandlingConfig from YAML quota_handling block (issue
    # #236, spec S5.2). Schema enforces enum membership, range bounds, and
    # additionalProperties: false; _parse_quota_handling_config re-validates
    # at runtime so a bypassed schema layer still fails fast with a message
    # naming the field. An absent block yields the full default set.
    quota_handling_raw = raw.get("quota_handling") or {}
    quota_handling = _parse_quota_handling_config(path, quota_handling_raw)

    # Populate SkillsConfig from YAML skills block (issue #221 E1-E10).
    # JSON Schema validates types + minimums; _parse_skills_config
    # re-validates at runtime to emit clearer messages naming the field.
    skills_raw = raw.get("skills") or {}
    skills = _parse_skills_config(path, skills_raw)

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
        quota_handling=quota_handling,
        skills=skills,
        manifest_amendment=manifest_amendment,
        task_factory=task_factory,
        agent_models=agent_models,
        validate=validate_cfg,
        gates=gates,
        debug=debug,
        notifications=notifications,
        allowed_orgs=allowed_orgs,
        use_bedrock=bool(raw.get("use_bedrock", False)),
        bedrock_region=raw.get("bedrock_region") or None,
        merge_strategy=raw.get("merge_strategy") or "squash",
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


def get_effective_merge_strategy(repo: str, runtime_config: RuntimeConfig) -> str | None:
    """Return the YAML-configured merge strategy for *repo*.

    Resolution: per-repo ``repos.<org/repo>.merge_strategy`` override, else the
    top-level ``merge_strategy``, else ``None``.  Pure function -- no env reads,
    no I/O.  Environment-variable precedence (``DEVBENCH_MERGE_STRATEGY``) is the
    caller's responsibility (see ``config.resolve_merge_strategy``).

    Args:
        repo: Fully-qualified repository name (e.g. ``'org/repo'``).
        runtime_config: Loaded runtime configuration.

    Returns:
        The configured merge-strategy string (``'merge'`` / ``'squash'`` /
        ``'rebase'``), or ``None`` when neither per-repo nor top-level sets one.
    """
    repo_config = runtime_config.repos.get(repo)
    if repo_config and repo_config.merge_strategy:
        return repo_config.merge_strategy
    if runtime_config.merge_strategy:
        return runtime_config.merge_strategy
    return None


@dataclass(frozen=True)
class ResolvedGateConfig:
    """Fully-resolved effective configuration for one gate (spec 4.1, D-15; AC-27).

    Returned exclusively by :func:`resolve_gate_config`, the ONLY
    sanctioned read path for gate configuration -- every gate command
    consumes this object rather than ``RuntimeConfig.gates`` directly, so a
    second, potentially divergent interpretation of the four-layer
    precedence model can never exist.

    Attributes:
        gate: The resolved gate's name (one of ``GATE_NAMES``).
        values: Resolved field values by field name. Always includes
            ``"enabled"``, plus any gate-specific tunable(s) declared for
            *gate* in ``constants.GATE_FIELD_DEFAULTS`` (for example
            ``"auto_derive_registry"`` for ``shared_file_impact``) or merged
            by a gate-specific step in this function (``"entry_points"``
            for ``reachability``, spec 4.4 bullet 2). Every value is a
            ``bool`` except ``reachability``'s ``entry_points``, which is a
            ``tuple[str, ...]``.
        provenance: The layer that set each field in ``values`` -- one of
            ``constants.GATE_PROVENANCE_BUILTIN`` / ``_PROJECT`` /
            ``_REPO`` / ``_ENV`` (spec 4.1; rendered as the ``devbench
            gates`` provenance column).
    """

    gate: str
    values: Mapping[str, bool | tuple[str, ...]]
    provenance: Mapping[str, str]


def _merge_gate_project_layer(
    gate: str, runtime_config: RuntimeConfig
) -> tuple[dict[str, bool | tuple[str, ...]], dict[str, str]]:
    """Merge the built-in and project-level layers for *gate*, field-wise.

    Generic over every gate's field set (``constants.GATE_FIELD_DEFAULTS``)
    so adding a ninth gate later requires only a new
    ``GATE_FIELD_DEFAULTS`` entry and a matching attribute on
    ``GatesConfig``/``GateRepoOverrides`` -- not a resolver change.

    Args:
        gate: Gate name; must already be validated as a member of
            ``GATE_NAMES`` by the caller.
        runtime_config: Loaded runtime configuration.

    Returns:
        A ``(values, provenance)`` pair covering every field declared for
        *gate*, seeded from the built-in layer and overridden field-wise by
        any project-level value that differs from the built-in default.
    """
    defaults = GATE_FIELD_DEFAULTS[gate]
    values: dict[str, bool | tuple[str, ...]] = dict(defaults)
    provenance: dict[str, str] = dict.fromkeys(defaults, GATE_PROVENANCE_BUILTIN)

    project_gate = getattr(runtime_config.gates, gate)
    for field_name, default_value in defaults.items():
        project_value = getattr(project_gate, field_name)
        if project_value != default_value:
            values[field_name] = project_value
            provenance[field_name] = GATE_PROVENANCE_PROJECT
    return values, provenance


def _merge_gate_repo_layer(
    gate: str,
    repo: str,
    runtime_config: RuntimeConfig,
    values: dict[str, bool | tuple[str, ...]],
    provenance: dict[str, str],
) -> None:
    """Field-wise merge *repo*'s override for *gate* over *values*/*provenance*, in place.

    A field absent from the override (``None``, or the field does not
    exist at all on the override dataclass -- e.g. ``auto_derive_registry``
    has no override field on ``GateSharedFileImpactOverride``) is left
    untouched: the AC-E2-F1-S1-T2-2 inheritance guarantee that flipping
    ``enabled`` for one repo never resets that repo's other tunables to
    the built-in default.

    Args:
        gate: Gate name; must already be validated as a member of
            ``GATE_NAMES`` by the caller.
        repo: Fully-qualified repository name (``org/repo``).
        runtime_config: Loaded runtime configuration.
        values: The project-layer-merged values dict to update in place.
        provenance: The project-layer-merged provenance dict to update in
            place.
    """
    repo_overrides = runtime_config.gates.repos.get(repo)
    if repo_overrides is None:
        return
    gate_override = getattr(repo_overrides, gate)
    if gate_override is None:
        return
    for field_name in GATE_FIELD_DEFAULTS[gate]:
        override_value = getattr(gate_override, field_name, None)
        if override_value is not None:
            values[field_name] = override_value
            provenance[field_name] = GATE_PROVENANCE_REPO


#: ``reachability.entry_points``'s built-in default (spec 4.4 bullet 2,
#: D-17; AC-FUNC-006): the source_classification-derived entry-point-stem
#: convention (``main``, ``app``, ``index``, ``__init__``, ...), sorted for
#: a deterministic resolved value. Not itself a repo-relative path list --
#: these are bare filename-stem conventions that
#: ``cli._matches_reachability_entry_point`` matches against a candidate
#: importer's own basename stem, so an operator who enables the gate
#: without configuring ``entry_points`` still walks a non-empty,
#: repo-agnostic graph instead of an always-empty one. An explicit
#: project-level ``entry_points`` value (spec 4.4's "a list of
#: repo-relative paths" contract) replaces this default wholesale. Derived
#: from the shared ``source_classification.ENTRY_POINT_STEMS`` set rather
#: than a new frozenset declared in this module (DoR bullet 3).
_REACHABILITY_ENTRY_POINTS_BUILTIN_DEFAULT: tuple[str, ...] = tuple(sorted(ENTRY_POINT_STEMS))


def _merge_reachability_entry_points(
    runtime_config: RuntimeConfig,
    values: dict[str, bool | tuple[str, ...]],
    provenance: dict[str, str],
) -> None:
    """Merge the project layer for reachability's ``entry_points`` field, in place (spec 4.4 bullet 2).

    Not folded into the generic ``GATE_FIELD_DEFAULTS``-driven merge in
    :func:`_merge_gate_project_layer`/:func:`_merge_gate_repo_layer`: those
    are typed (and, per D-17, populated) for boolean tunables only --
    structural, list-valued fields have no built-in default to merge
    against there (see ``constants.GATE_FIELD_DEFAULTS``'s own docstring).
    ``entry_points`` is the first gate-specific tunable that DOES carry a
    real built-in default (:data:`_REACHABILITY_ENTRY_POINTS_BUILTIN_DEFAULT`,
    AC-FUNC-006), so it gets its own narrow merge step here rather than
    widening the generic mechanism for a single field.

    No per-repo override layer is merged: this campaign configures a
    single target repo (spec Section 9) and no acceptance criterion
    requires a per-repo ``entry_points`` override, so
    ``GateRepoOverrides.reachability`` (``GateEnabledOverride``) carries no
    ``entry_points`` field at all -- there is no override layer to read.

    Args:
        runtime_config: Loaded runtime configuration.
        values: The already project/repo/env-merged ``values`` dict for the
            ``enabled`` field, updated in place with ``entry_points``.
        provenance: Companion provenance dict, updated in place.
    """
    project_value = runtime_config.gates.reachability.entry_points
    if project_value:
        values["entry_points"] = project_value
        provenance["entry_points"] = GATE_PROVENANCE_PROJECT
    else:
        values["entry_points"] = _REACHABILITY_ENTRY_POINTS_BUILTIN_DEFAULT
        provenance["entry_points"] = GATE_PROVENANCE_BUILTIN


def resolve_gate_config(
    gate: str,
    repo: str,
    runtime_config: RuntimeConfig,
    env_enabled_override: bool | None = None,
) -> ResolvedGateConfig:
    """Resolve *gate*'s fully-effective configuration for *repo*.

    THE single read path for gate configuration (spec 4.1, D-15; AC-27):
    every gate command must call this function instead of reading
    ``runtime_config.gates`` directly, so a second interpretation of the
    four-layer precedence model can never silently diverge from this one
    (pinned by ``tests/test_config_loader.py::TestResolveGateConfigSingleReadPathPin``).

    Merges four layers field-wise, in ascending precedence (D-15):

    1. Built-in defaults (``constants.GATE_FIELD_DEFAULTS``; D-17: every
       gate disabled, every tunable at its documented default).
    2. Project level (``runtime_config.gates.<gate>``).
    3. Per-repo override (``runtime_config.gates.repos[repo].<gate>``),
       field-wise merged OVER the project level -- flipping ``enabled``
       for one repo never resets that repo's other tunables to the
       built-in default; they keep inheriting the project-level value.
    4. Environment (*env_enabled_override*) -- the already-resolved
       ``DEVBENCH_GATE_<NAME>_ENABLED`` value (see
       ``devbench.config.resolve_gate_env_override``, which applies the
       existing ``_resolve_bool`` parsing/failure semantics; this module
       does not read environment variables itself, matching every other
       parse/validate-only function here). Workspace-wide and highest
       precedence. Only ``enabled`` has an env layer (spec Section 7);
       gate-specific tunables have none.

    ``reachability``'s ``entry_points`` field (spec 4.4 bullet 2, issue #10
    AC2) is merged by an additional, gate-specific step
    (:func:`_merge_reachability_entry_points`) after the four generic
    layers above: built-in default
    (:data:`_REACHABILITY_ENTRY_POINTS_BUILTIN_DEFAULT`, source_classification-
    derived) or project-level override, with provenance recorded the same
    way (AC-FUNC-006).

    Pure function -- no I/O, no env reads; directly testable with an
    in-memory ``RuntimeConfig`` and a plain ``bool | None``.

    Args:
        gate: Gate name; must be one of ``GATE_NAMES``.
        repo: Fully-qualified repository name (``org/repo``) whose
            per-repo override layer to apply. A repo absent from
            ``runtime_config.gates.repos`` simply has no override layer
            (not an error -- most repos never override any gate).
        runtime_config: Loaded runtime configuration.
        env_enabled_override: The caller-resolved
            ``DEVBENCH_GATE_<NAME>_ENABLED`` value for *gate* -- ``True``/
            ``False`` when the operator set the env var, ``None`` (the
            default) when unset, falling through to the per-repo/project/
            built-in layers.

    Returns:
        The resolved, frozen ``ResolvedGateConfig`` with per-field
        provenance.

    Raises:
        ValueError: If *gate* is not one of ``GATE_NAMES``.
    """
    if gate not in GATE_NAMES:
        raise ValueError(f"resolve_gate_config: unknown gate {gate!r}; valid gate names are {sorted(GATE_NAMES)}.")

    values, provenance = _merge_gate_project_layer(gate, runtime_config)
    _merge_gate_repo_layer(gate, repo, runtime_config, values, provenance)
    if env_enabled_override is not None:
        values["enabled"] = env_enabled_override
        provenance["enabled"] = GATE_PROVENANCE_ENV
    if gate == "reachability":
        _merge_reachability_entry_points(runtime_config, values, provenance)

    return ResolvedGateConfig(gate=gate, values=values, provenance=provenance)


def get_effective_branch_prefix(repo: str, runtime_config: RuntimeConfig) -> str | None:
    """Return the effective task-branch prefix for *repo*.

    Resolution: per-repo ``repos.<org/repo>.branch_prefix`` override, else the
    top-level ``git_ops.branch_prefix``, else ``None`` (no prefix, original
    ``backlog/<unit-id>`` naming).  Pure function -- no env reads, no I/O.

    Args:
        repo: Fully-qualified repository name (e.g. ``'org/repo'``).
        runtime_config: Loaded runtime configuration.

    Returns:
        The configured branch-prefix string, or ``None`` when neither
        per-repo nor top-level sets one.
    """
    repo_config = runtime_config.repos.get(repo)
    if repo_config and repo_config.branch_prefix:
        return repo_config.branch_prefix
    if runtime_config.git_ops.branch_prefix:
        return runtime_config.git_ops.branch_prefix
    return None


def format_branch_name(unit_id: str, branch_prefix: str | None = None) -> str:
    """Build the canonical task-branch name for *unit_id*.

    Produces ``backlog/<unit-id-lower>`` when *branch_prefix* is unset
    (original behaviour, unchanged), or ``backlog/<prefix>/<unit-id-lower>``
    when set -- namespacing the branch so multiple devbench workspaces
    sharing one downstream repo cannot collide on task-branch names.

    Args:
        unit_id: Work-unit ID (e.g. ``'E1-F1-S1-T1'``); case-folded to lower.
        branch_prefix: Effective prefix from :func:`get_effective_branch_prefix`,
            or ``None``.

    Returns:
        The branch name to create/push/match against.
    """
    unit_slug = unit_id.lower()
    if branch_prefix:
        return BRANCH_NAME_TEMPLATE.format(unit_id=f"{branch_prefix}/{unit_slug}")
    return BRANCH_NAME_TEMPLATE.format(unit_id=unit_slug)


def format_single_branch_name(single_branch: str, branch_prefix: str | None = None) -> str:
    """Namespace ``git_ops.single_branch`` by *branch_prefix*, same rationale as :func:`format_branch_name`.

    Single-branch (accumulator) mode shares one branch across every work
    unit instead of one per unit, but the collision risk is identical: two
    devbench workspaces sharing a downstream repo could configure the same
    ``single_branch`` name. Prefixing it the same way per-unit branches are
    prefixed closes that gap.

    Args:
        single_branch: The configured ``git_ops.single_branch`` value.
        branch_prefix: Effective prefix from :func:`get_effective_branch_prefix`,
            or ``None``.

    Returns:
        ``single_branch`` unchanged when *branch_prefix* is unset (original
        behaviour), or ``<prefix>/<single_branch>`` when set.
    """
    if branch_prefix:
        return f"{branch_prefix}/{single_branch}"
    return single_branch
