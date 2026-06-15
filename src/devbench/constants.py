"""Centralized constants for the judges system.

All structural string constants, regex patterns, markdown format definitions,
section headers, and display limits live here. Source files import from this
module instead of embedding literals inline.

Operational parameters that vary by environment (timeouts, thresholds, paths)
live in ``config.py`` with environment-variable overrides.

Session-management constants (SESSION_*) are defined in the session-management
region below and consumed by ``src/devbench/session.py``.
"""

import re

# ---------------------------------------------------------------------------
# Markdown section headers
# ---------------------------------------------------------------------------
COMMENTS_SECTION_HEADER: str = "## Comments"
STATUS_SECTION_PREFIX: str = "## Status:"
STATUS_SUMMARY_SECTION_HEADER: str = "## Status Summary"
STATUS_SUMMARY_TABLE_HEADER: str = (
    "| Epic | Title | Done | In Progress | In Queue | Blocked | Declined | Draft |\n"
    "|------|-------|------|-------------|----------|---------|----------|-------|\n"
)
# Pre-compiled pattern to strip the Status Summary section from BACKLOG.md content.
# Matches from the header up to (but not including) the next ## heading or end of string.
STRIP_SUMMARY_RE = re.compile(
    r"## Status Summary\n.*?(?=\n## |\Z)",
    re.DOTALL,
)

# ---------------------------------------------------------------------------
# Work-unit markdown format patterns
# ---------------------------------------------------------------------------
STATUS_LINE_RE = re.compile(r"^(##\s*Status:\s*)(.+)$", re.MULTILINE)
TABLE_ROW_RE = re.compile(r"^\|([^|]*)\|", re.MULTILINE)

# Backlog parser patterns
BACKLOG_STATUS_RE = re.compile(r"^##\s+Status:\s*(.+)$", re.MULTILINE)
BACKLOG_REPO_RE = re.compile(r"^-\s+\*?\*?Repo:?\*?\*?\s*`([^`]+)`", re.MULTILINE)
BACKLOG_LOCAL_PATH_RE = re.compile(r"^-\s+\*?\*?Local path:?\*?\*?\s*`([^`]+)`", re.MULTILINE)
BACKLOG_BRANCH_RE = re.compile(r"^-\s+\*?\*?Branch:?\*?\*?\s*`([^`]+)`", re.MULTILINE)
BACKLOG_AC_RE = re.compile(r"^-\s+\[[^\]]*\]\s+(AC-\S+.*)", re.MULTILINE)
BACKLOG_SECTION_RE = re.compile(r"^##\s+", re.MULTILINE)
BACKLOG_DEP_TABLE_ROW_RE = re.compile(
    r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|$",
    re.MULTILINE,
)
BACKLOG_INDEX_TABLE_ROW_RE = re.compile(
    r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|"
    r"\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|$",
    re.MULTILINE,
)

# ---------------------------------------------------------------------------
# Security review patterns
# ---------------------------------------------------------------------------
SECURITY_ALERT_CATEGORIES: list[tuple[str, str]] = [
    ("code-scanning", "repos/{repo}/code-scanning/alerts?state=open"),
    ("dependabot", "repos/{repo}/dependabot/alerts?state=open"),
    ("secret-scanning", "repos/{repo}/secret-scanning/alerts?state=open"),
]

# ---------------------------------------------------------------------------
# Backlog status display values (used in CLI status summaries)
# ---------------------------------------------------------------------------
DISPLAY_STATUS_VALUES: list[str] = [
    "In Queue",
    "In Progress",
    "In Review",
    "Done",
    "Blocked",
    "Proposed",
    "Declined",
    "Hold",
]

# Backlog manager recognized status labels (title-case, as in markdown tables)
TABLE_STATUS_VALUES: frozenset[str] = frozenset(
    {"In Queue", "In Progress", "In Review", "Done", "Blocked", "Proposed", "Declined", "Hold"}
)

# ---------------------------------------------------------------------------
# Traceability matrix format
# ---------------------------------------------------------------------------
TRACEABILITY_MATRIX_HEADER: str = "| Spec Ref | Test Ref | Verified At |\n| --- | --- | --- |\n"

# ---------------------------------------------------------------------------
# Comment entry format template
# ---------------------------------------------------------------------------
COMMENT_ENTRY_TEMPLATE: str = "[{timestamp}] [{agent_id}] [{action}] {message}\n"

# ---------------------------------------------------------------------------
# Orchestrator templates
# ---------------------------------------------------------------------------
BRANCH_NAME_TEMPLATE: str = "backlog/{unit_id}"
PR_BODY_TEMPLATE: str = "Automated PR for work unit {unit_id}\n\n{description}"

# ---------------------------------------------------------------------------
# Display / preview truncation limits
# ---------------------------------------------------------------------------
ERROR_OUTPUT_PREVIEW_CHARS: int = 1000
RAW_RESPONSE_PREVIEW_CHARS: int = 500

# ---------------------------------------------------------------------------
# LLM response format prompt
# ---------------------------------------------------------------------------
LLM_RESPONSE_FORMAT_INSTRUCTIONS: str = (
    "# Response Format\n"
    "Respond with ONLY valid JSON (no markdown fences, no preamble):\n"
    '{"verdict": "pass" or "fail", "reasoning": "...", "feedback": "...", '
    '"evidence": ["item1", "item2"]}\n'
    "- verdict: pass or fail\n"
    "- reasoning: detailed explanation of your decision\n"
    "- feedback: if fail, specific actionable instructions to fix each issue. "
    "if pass, empty string.\n"
    "- evidence: list of specific items you checked\n"
)

# ---------------------------------------------------------------------------
# Separator used in status display
# ---------------------------------------------------------------------------
STATUS_SEPARATOR_WIDTH: int = 40

# Pad width applied to every label in the ``devbench status`` Backlog Status
# Summary so count values right-align to a single column regardless of label
# length.  The current longest label is ``Blocked (amendment-recovery)`` /
# ``Blocked (runtime-degradation)`` (29 chars); the chosen width keeps at
# least one space between every label and its count and leaves a few chars
# of headroom for future Blocked sub-bucket labels.  Used by ``cmd_status``
# in ``src/devbench/cli.py`` (issue #201).
STATUS_SUMMARY_LABEL_WIDTH: int = 32

# ---------------------------------------------------------------------------
# Dependency "none" sentinel
# ---------------------------------------------------------------------------
DEPENDENCY_NONE_VALUE: str = "none"

# ---------------------------------------------------------------------------
# Work-unit lifecycle status strings (canonical lowercase-hyphenated form).
# These are the values written into work-unit files and BACKLOG.md rows.
# The display / title-case variants live on WorkUnitStatus in work_unit.py.
# ---------------------------------------------------------------------------
STATUS_IN_QUEUE: str = "in-queue"
STATUS_IN_PROGRESS: str = "in-progress"
STATUS_IN_REVIEW: str = "in-review"
STATUS_DONE: str = "done"
STATUS_BLOCKED: str = "blocked"
STATUS_PROPOSED: str = "proposed"
STATUS_DECLINED: str = "declined"
# Held units are deliberately deferred (under debate, awaiting external
# decision). The orchestrator's next-query and parallel-candidate scan
# both skip held units the same way they skip declined ones, but unlike
# declined a held unit is non-terminal -- parent rollups do NOT count
# held children as complete; an operator must explicitly unhold to
# return the unit to the in-queue lifecycle.
STATUS_HOLD: str = "hold"
# Draft units are pre-queue planning artefacts: the spec is authored and
# reviewed but the unit has not yet been accepted into the execution queue.
# Unlike declined (terminal) or hold (deferred), draft is a pre-lifecycle
# state -- the orchestrator's parallel-candidate scan skips draft units.
STATUS_DRAFT: str = "draft"

# Ordered mapping from any accepted input form to the canonical write form.
# Used by BacklogManager._set_status() for validation and normalisation.
VALID_STATUSES: dict[str, str] = {
    STATUS_IN_QUEUE: STATUS_IN_QUEUE,
    STATUS_IN_PROGRESS: STATUS_IN_PROGRESS,
    STATUS_IN_REVIEW: STATUS_IN_REVIEW,
    STATUS_DONE: STATUS_DONE,
    STATUS_BLOCKED: STATUS_BLOCKED,
    STATUS_PROPOSED: STATUS_PROPOSED,
    STATUS_DECLINED: STATUS_DECLINED,
    STATUS_HOLD: STATUS_HOLD,
    STATUS_DRAFT: STATUS_DRAFT,
}

# ---------------------------------------------------------------------------
# Epic placeholder ID
# ---------------------------------------------------------------------------
EPIC_PLACEHOLDER_ID: str = "--"

# ---------------------------------------------------------------------------
# Subdirectory name under the backlog root where work-unit files live.
# ---------------------------------------------------------------------------
BACKLOG_SUBDIR: str = "backlog"

# All values that mean "no dependency" in the BACKLOG.md dependencies column.
# Includes DEPENDENCY_NONE_VALUE, EPIC_PLACEHOLDER_ID, the separator used in
# some tables, and empty string.
DEPENDENCY_NONE_VALUES: frozenset[str] = frozenset(
    {
        DEPENDENCY_NONE_VALUE,  # "none"
        EPIC_PLACEHOLDER_ID,  # "--"
        "---",
        "",
    }
)

# ---------------------------------------------------------------------------
# Required review judge names for the done-gate check (4.1)
# ---------------------------------------------------------------------------
REVIEW_JUDGE_NAMES: frozenset[str] = frozenset(
    {
        "code_review",
        "test_review",
        "doc_review",
        "changes_manifest",
    }
)

SECURITY_JUDGE_NAMES: frozenset[str] = frozenset({"security_review"})

ALL_REQUIRED_JUDGE_NAMES: frozenset[str] = REVIEW_JUDGE_NAMES | SECURITY_JUDGE_NAMES

# Workflow agents that legitimately write audit-only verdicts via
# ``log-verdict``. They are NOT counted by the done-gate's
# ``_last_round_all_passed`` (only ``ALL_REQUIRED_JUDGE_NAMES`` is); the
# entries land in the work-unit Comments section as audit metadata.
# Mirrored in ``plugin/devbench-orchestrate/scripts/guard-verdict-format.sh``'s
# ``KNOWN_JUDGES`` array; both lists must stay in sync.
WORKFLOW_AGENT_JUDGE_NAMES: frozenset[str] = frozenset(
    {
        "executor",
        "blocker_resolver",
        "manifest_amender",
        "task_factory",
    }
)

# Optional specialty judges. These are NOT part of the always-on core 5
# (``ALL_REQUIRED_JUDGE_NAMES``); each is toggled per-backlog via the YAML
# ``optional_judges`` block and is auto-required for a unit only when both the
# toggle is on AND the unit's ``## Verification`` contract makes the judge
# applicable (deterministically, never authored by hand). ``iac_review`` is the
# evidence-verifying IaC judge: applicable when
# ``verification.unit_requires_iac_judge`` is true for the unit. Mirrored in
# ``plugin/devbench-orchestrate/scripts/guard-verdict-format.sh``'s
# ``KNOWN_JUDGES`` + ``CANONICAL_REVIEWER_JUDGES`` arrays; all lists must stay
# in sync (enforced by ``infra/scripts/release_acceptance.py`` condition (e)).
OPTIONAL_JUDGE_NAMES: frozenset[str] = frozenset({"iac_review"})

# Full allowlist consumed by ``cmd_log_verdict``. Strictly broader than
# ``ALL_REQUIRED_JUDGE_NAMES`` -- the canonical 5 reviewers satisfy the
# done-gate unconditionally; the optional specialty judges satisfy it only when
# applicable+enabled; the workflow-agent names are audit-only.
KNOWN_JUDGE_NAMES: frozenset[str] = ALL_REQUIRED_JUDGE_NAMES | WORKFLOW_AGENT_JUDGE_NAMES | OPTIONAL_JUDGE_NAMES

# ---------------------------------------------------------------------------
# Non-verdict agent comment format template
# ---------------------------------------------------------------------------
COMMENT_AGENT_TEMPLATE: str = "[{timestamp}] [agent/{name}] {message}\n"

# ---------------------------------------------------------------------------
# TDD Cycle Log section header and entry format template
# ---------------------------------------------------------------------------
TDD_CYCLE_LOG_SECTION_HEADER: str = "## TDD Cycle Log"
TDD_ENTRY_TEMPLATE: str = "- [{phase}] {timestamp} -- {message}\n"
VALID_TDD_PHASES: frozenset[str] = frozenset({"RED", "GREEN", "REFACTOR"})

# ---------------------------------------------------------------------------
# Epic ID regex -- matches top-level epic IDs such as "E200", "E1", etc.
# A row is an epic row when its ID is exactly E<digits> with no hyphen suffix.
# ---------------------------------------------------------------------------
EPIC_ID_RE = re.compile(r"^E\d+$")

# ---------------------------------------------------------------------------
# Operational parameter defaults
# ---------------------------------------------------------------------------
DEFAULT_MAX_RETRY_ATTEMPTS: int = 10
DEFAULT_GITHUB_CHECK_TIMEOUT_SECONDS: int = 600
DEFAULT_STOP_HOOK_MAX_BLOCKS: int = 5
DEFAULT_STOP_HOOK_WINDOW_SECONDS: int = 180
DEFAULT_STOP_HOOK_STALE_TASK_MINUTES: int = 120
# Hook-tail column caps (issue #134). Operator-tunable via env vars
# (JUDGE_HOOK_TAIL_*) or YAML (`hook_tail.*` block). DESCRIPTION_MAX bumped
# from 100 -> 120 in the same release that introduces the configurability.
DEFAULT_HOOK_TAIL_AGENT_WIDTH: int = 12
DEFAULT_HOOK_TAIL_TOOL_WIDTH: int = 8
DEFAULT_HOOK_TAIL_DESCRIPTION_MAX: int = 120
DEFAULT_HOOK_TAIL_STDOUT_PREVIEW_MAX: int = 80
# Recovery-cascade depth cap (issue #144). Operator-tunable via
# `orchestrate.max_cascade_depth` YAML or
# `JUDGE_ORCHESTRATE_MAX_CASCADE_DEPTH` env var. When a proposal would
# land at depth >= this cap, the source task transitions to
# NEEDS_OPERATOR_ATTENTION instead of materialising another recovery
# layer. Default of 2 reflects the bounded cascade depth needed for
# typical recovery chains.
DEFAULT_MAX_CASCADE_DEPTH: int = 2
# Workflow-registration race defence (issue #114). When `gh pr checks`
# returns "no checks reported" right after a PR is created, devbench
# cannot tell "repo has no CI configured" apart from "GitHub Actions
# has not yet enqueued the workflow for this commit". The retry loop
# (12 retries x 5s = 60s default coverage) handles the race; the
# zero-workflow-files fast path skips the wait when the repo legitimately
# has no CI. Both knobs override via JUDGE_CHECK_REGISTRATION_RETRIES /
# JUDGE_CHECK_REGISTRATION_DELAY_SECONDS env vars.
DEFAULT_CHECK_REGISTRATION_RETRIES: int = 12
DEFAULT_CHECK_REGISTRATION_DELAY_SECONDS: int = 5
# Recency cap for the "recent recovery audit comment" heuristic in the
# 3-state blocked-task classifier (AWAITING_AUTO_RECOVERY signal #3).
# Tasks whose most recent [BLOCKED] audit-comment timestamp is older
# than this window fall through to NEEDS_OPERATOR_ATTENTION, even when
# the agent-tag and body-pattern would otherwise match. 30 minutes
# covers the gap between manifest-amender FAIL and blocker-resolver's
# proposal write under normal orchestrator iteration cadence; tune via
# JUDGE_BLOCKED_RECOVERY_WINDOW_SECONDS for slower / debugging runs.
DEFAULT_BLOCKED_RECOVERY_WINDOW_SECONDS: int = 1800
# When ``True``, ``cmd_git_ops`` runs ``cleanup_tracked_orphans`` inline as a
# devbench-authored chore commit instead of emitting a backlog cleanup task.
# Eliminates the orphan-cleanup-cascade pathology where multiple parent tasks
# emit duplicate cleanup proposals, the cleanup tasks themselves get blocked by
# the manifest amender on predecessor staging, and the orchestrator loops
# without converging. Operators can fall back to the legacy proposal flow via
# ``DEVBENCH_DISABLE_INLINE_ORPHAN_CLEANUP=1`` when an audit work-unit is
# required for compliance reporting; the legacy path still runs but with
# cross-task de-duplication so duplicate proposals cannot land.
DEFAULT_INLINE_ORPHAN_CLEANUP_ENABLED: bool = True
# Canonical commit message used by the inline orphan-cleanup path. Stable
# string so post-merge audit tooling can grep for the chore commits without
# parsing free-form prose. Keep in sync with the documented contract in
# ``docs/backlog-contract.md``'s orphan-cleanup section.
DEVBENCH_INLINE_CLEANUP_COMMIT_MESSAGE: str = (
    "chore(cleanup): untrack devbench-managed orphan paths and update .gitignore"
)
# Issue #115: CI-failure feedback log byte cap. ``cmd_git_ops`` writes the
# trimmed failing-job log to ``<workspace>/.devbench/ci-failures/<id>-<n>.log``
# so the executor retry loop has a stable filesystem path to read; the cap
# keeps the feedback payload bounded so a runaway-log job cannot consume the
# entire executor context window. Override via ``JUDGE_CI_FAILURE_LOG_BYTES``
# when a backlog's CI legitimately produces longer relevant tails.
DEFAULT_CI_FAILURE_LOG_BYTES: int = 32768
# Issue #115: CI-failure executor retry path. Default ``True`` so every
# backlog benefits without per-shell setup; set
# ``JUDGE_CI_FAILURE_RETRY_ENABLED=0`` (or ``false`` / ``no`` / ``off``) or
# ``git_ops.ci_failure_retry: false`` in YAML to opt out. ``cmd_git_ops``
# returns rc=2 on CI failure (signalling executor retry) until the shared
# retry budget (``MAX_RETRY_ATTEMPTS``) is exhausted, at which point rc=1
# BLOCKED applies. The retry budget is shared with the existing review-judge
# retry budget so total per-task work stays bounded.
DEFAULT_CI_FAILURE_RETRY_ENABLED: bool = True
# Issue #116: opt-in toggle for the PR review-comment polling path. Default
# False; both this AND a non-empty ``JUDGE_PR_REVIEW_AGENTS`` (or
# ``JUDGE_PR_REVIEW_DECISION_BLOCKS=1``) are required for the phase to
# activate. Allows the phase to ship without changing default merge cadence.
DEFAULT_PR_REVIEW_RESOLUTION_ENABLED: bool = False
# Issue #101: pause-before-merge mode. Default False so existing single-PR
# and multi-PR flows are unchanged. When True, ``cmd_git_ops`` pushes the PR
# and waits for green CI then transitions to ``in-review`` instead of
# merging; the orchestrator's loop reconciles ``in-review`` tasks via
# ``cmd_check_merge`` on the next iteration. Mutually exclusive with
# ``defer_pr: true`` and ``single_branch: <name>`` (validated at config load).
DEFAULT_PAUSE_BEFORE_MERGE: bool = False
# Issue #116: PR review-comment poll/settle window. After ``wait_for_checks``
# returns True, ``cmd_git_ops`` polls ``gh pr view`` for late-arriving
# REQUEST_CHANGES reviews and bot comments for up to this many seconds before
# merging. Event-driven loop (poll + condition), not a blanket sleep -- the
# helper exits early on the first signal. Override via
# ``JUDGE_PR_REVIEW_SETTLE_SECONDS``.
DEFAULT_PR_REVIEW_SETTLE_SECONDS: int = 60
# Issue #116: poll cadence inside the settle window. Smaller values reduce
# observed latency at the cost of more ``gh`` API calls; the default 5 s
# matches the ``CHECK_REGISTRATION_DELAY_SECONDS`` cadence already used by
# ``wait_for_checks`` so operators see one consistent rhythm. Override via
# ``JUDGE_PR_REVIEW_POLL_INTERVAL``.
DEFAULT_PR_REVIEW_POLL_INTERVAL: int = 5
# Issue #116: when ``True`` (the default), a PR with ``reviewDecision ==
# CHANGES_REQUESTED`` blocks the merge regardless of the bot allowlist. When
# ``False``, only allowlisted bot comments block the merge. Repos without
# review bots leave both signals empty and the entire phase is a no-op
# (zero regression for existing behaviour). Override via
# ``JUDGE_PR_REVIEW_DECISION_BLOCKS``.
DEFAULT_PR_REVIEW_DECISION_BLOCKS: bool = True
# Issue #116: comma-separated allowlist of GitHub login names whose review
# comments block the merge until resolved. Empty default means the phase is
# a no-op for repos without review bots. Override via
# ``JUDGE_PR_REVIEW_AGENTS`` (e.g. ``github-copilot[bot],amazon-q-developer[bot]``).
DEFAULT_PR_REVIEW_AGENTS: tuple[str, ...] = ()
# ---------------------------------------------------------------------------
# Per-model token pricing (issue #223). Each model id maps to a ``ModelRates``
# carrying the four scalar rates that devbench charges against. The cache
# multipliers + correction_factor are optional per-model overrides; when None
# the report falls back to the top-level ``ReportConfig`` multipliers.
#
# The legacy scalar fields (``DEFAULT_TOKEN_COST_PER_M_INPUT`` /
# ``DEFAULT_TOKEN_COST_PER_M_OUTPUT`` / ``DEFAULT_TOKEN_COST_DISCOUNT``) were
# removed in the same commit per CLAUDE.md "Complete Replacement of
# Superseded Code". Existing workspaces that set the old keys get a clear
# fail-fast error at config-load time pointing at the new ``report.models``
# block; see ``docs/model-pricing.md``.
# ---------------------------------------------------------------------------


def _opt_float(value: float | None) -> float | None:
    """Return ``float(value)`` when ``value`` is not None, else ``None``.

    Helper for ``ModelRates.__init__``: keeps the per-multiplier "unset"
    sentinel intact (so the runtime falls back to the top-level
    ``ReportConfig`` defaults) while still coercing JSON / YAML ints to
    float for the arithmetic path.
    """
    return None if value is None else float(value)


class ModelRates:
    """Per-model cost rates (issue #223).

    The four scalar fields are the canonical Anthropic-published rates
    expressed in USD per 1M tokens (input + output) and as multipliers
    relative to ``input`` (cache read / write 5min / write 1hr). All cache
    multiplier fields are optional -- when None the per-window report falls
    back to the top-level ``ReportConfig`` defaults so operators on
    standard Anthropic pricing only need to override the two scalar rates.

    ``correction_factor`` is the per-model contract correction; defaults to
    1.0 (no correction). Computed cost is multiplied by this value AFTER
    all other factors so operators can tune individual models without
    distorting cost for other models in a multi-model run.

    The class is a plain attribute container (not a frozen dataclass) so
    ``cli.py::cmd_cost_calibrate`` can construct mutated copies via
    ``replace``-style helpers without dataclass plumbing.
    """

    __slots__ = (
        "cache_read_multiplier",
        "cache_write_1hr_multiplier",
        "cache_write_5min_multiplier",
        "correction_factor",
        "input",
        "output",
    )

    def __init__(self, **kwargs: float | None) -> None:
        # **kwargs (rather than named ``input=``/``output=``) so the
        # attribute names match the YAML keys verbatim without shadowing
        # the builtin ``input()`` in this scope.  Callers always pass
        # keyword arguments (``ModelRates(input=5.0, output=25.0)``); the
        # init validates required keys and rejects unknown ones so type
        # safety is preserved at the boundary.  ``float | None`` covers
        # both the required scalar fields (always float) and the optional
        # multiplier fields (None when unset).
        allowed = {
            "input",
            "output",
            "cache_read_multiplier",
            "cache_write_5min_multiplier",
            "cache_write_1hr_multiplier",
            "correction_factor",
        }
        unknown = set(kwargs) - allowed
        if unknown:
            raise TypeError(f"ModelRates got unexpected keyword argument(s): {sorted(unknown)}")
        for required in ("input", "output"):
            if kwargs.get(required) is None:
                raise TypeError(f"ModelRates requires keyword arguments 'input' and 'output'; missing {required!r}")
        # All four numeric inputs may already be float; the float(...) cast
        # ensures ints coming through JSON or YAML are normalised to the
        # arithmetic domain ``_compute_cost`` expects.  The conditional
        # ``None`` preserves the "unset" sentinel on the optional fields.
        self.input = float(kwargs["input"] or 0.0)
        self.output = float(kwargs["output"] or 0.0)
        self.cache_read_multiplier = _opt_float(kwargs.get("cache_read_multiplier"))
        self.cache_write_5min_multiplier = _opt_float(kwargs.get("cache_write_5min_multiplier"))
        self.cache_write_1hr_multiplier = _opt_float(kwargs.get("cache_write_1hr_multiplier"))
        self.correction_factor = float(kwargs.get("correction_factor") or 1.0)

    def __repr__(self) -> str:
        return (
            f"ModelRates(input={self.input}, output={self.output}, "
            f"cache_read_multiplier={self.cache_read_multiplier}, "
            f"cache_write_5min_multiplier={self.cache_write_5min_multiplier}, "
            f"cache_write_1hr_multiplier={self.cache_write_1hr_multiplier}, "
            f"correction_factor={self.correction_factor})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ModelRates):
            return NotImplemented
        return (
            self.input == other.input
            and self.output == other.output
            and self.cache_read_multiplier == other.cache_read_multiplier
            and self.cache_write_5min_multiplier == other.cache_write_5min_multiplier
            and self.cache_write_1hr_multiplier == other.cache_write_1hr_multiplier
            and self.correction_factor == other.correction_factor
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.input,
                self.output,
                self.cache_read_multiplier,
                self.cache_write_5min_multiplier,
                self.cache_write_1hr_multiplier,
                self.correction_factor,
            )
        )


# Default per-model rates table -- single source of truth.  Lifted verbatim
# from ``docs/model-pricing.md`` Standard pricing table.  Operators who
# leave ``report.models`` absent get this table plus DEFAULT_FALLBACK_MODEL_RATES
# for any model id observed at runtime that isn't in the table (sentinel key
# ``"<unknown>"``).
#
# Keys match the literal ``model`` strings emitted by Claude Code in the
# transcript ``message.model`` field (e.g. ``claude-opus-4-7``, NOT
# ``us.anthropic.claude-opus-4-7-v1``).
DEFAULT_MODEL_RATES: dict[str, ModelRates] = {
    "claude-opus-4-8": ModelRates(input=5.0, output=25.0),
    "claude-opus-4-7": ModelRates(input=5.0, output=25.0),
    "claude-opus-4-6": ModelRates(input=5.0, output=25.0),
    "claude-opus-4-5": ModelRates(input=5.0, output=25.0),
    "claude-opus-4-1": ModelRates(input=15.0, output=75.0),
    "claude-opus-4": ModelRates(input=15.0, output=75.0),
    "claude-sonnet-4-6": ModelRates(input=3.0, output=15.0),
    "claude-sonnet-4-5": ModelRates(input=3.0, output=15.0),
    "claude-sonnet-4": ModelRates(input=3.0, output=15.0),
    "claude-haiku-4-5": ModelRates(input=1.0, output=5.0),
    "claude-haiku-3-5": ModelRates(input=0.80, output=4.0),
    "claude-haiku-3": ModelRates(input=0.25, output=1.25),
}

# Rates applied to the ``"<unknown>"`` aggregation bucket: any transcript
# message whose ``model`` field is missing, or any model id that does not
# appear in the loaded ``report.models`` table. Default mirrors Opus 4.8 list
# so devbench errs on the conservative (over-report) side -- under-reporting
# is the operator-pain failure mode #223 is filed against.
DEFAULT_FALLBACK_MODEL_RATES: ModelRates = ModelRates(input=5.0, output=25.0)

# Marker written idempotently by ``cmd_claim`` when the target repo declared
# in a work-unit file cannot be resolved. The classifier in
# ``backlog/proposal.py`` detects this marker at OPERATOR_ACTION_REQUIRED
# priority so the operator sees the block without automation attempting a
# recovery cycle. Format: ``[BLOCKED_TARGET_REPO_UNRESOLVED] <repo-name>``.
BLOCKED_TARGET_REPO_UNRESOLVED_MARKER: str = "[BLOCKED_TARGET_REPO_UNRESOLVED]"

# Em-dash (U+2014). Prohibited in work-unit markdown files by the
# validate-backlog Check 10 (manager.py). Any CLI writer that accepts
# free-form agent text must reject em-dash at the input boundary so the
# validator can trust its own data.
EM_DASH: str = "\u2014"

# Minimum number of completed tasks required before per-window pace numbers
# (avg_minutes, est_hours) are considered statistically meaningful. Below this
# threshold the report renders "n/a (N=X samples)" rather than projecting from
# a single completion that could swing wildly with the next task. 3 is small
# enough to be responsive once a session has produced a few completions but
# large enough to avoid the N=1 fragility seen in production reports.
MIN_PACE_SAMPLES: int = 3

# Default number of most recently completed tasks to average for the "Recent
# pace" projection. Used when at least this many completions exist; otherwise
# the report falls back to All-time pace. Overridable via
# `report.recent_pace_tasks` YAML or `JUDGE_REPORT_RECENT_PACE_TASKS` env.
DEFAULT_RECENT_PACE_TASKS: int = 10

# Whitespace columns between the two side-by-side tables rendered by
# `devbench report` (Backlog state on left, Window stats on right). Pure
# rendering affordance -- kept as a constant so the gap is consistent and
# editable from one place.
SIDE_BY_SIDE_GAP_CHARS: int = 4

# Timeout defaults (seconds)
DEFAULT_GH_API_TIMEOUT: int = 30
DEFAULT_TEST_TIMEOUT: int = 300
DEFAULT_SECURITY_FETCH_TIMEOUT: int = 120
DEFAULT_LLM_TIMEOUT: int = 300
DEFAULT_COMMAND_TIMEOUT: int = 120
DEFAULT_ORCHESTRATOR_POLL_INTERVAL: int = 10

# Threshold / limit defaults
DEFAULT_ALERT_SUMMARY_LIMIT: int = 10
DEFAULT_OUTPUT_TRUNCATION_LIMIT: int = 2000
DEFAULT_LLM_EVIDENCE_TRUNCATION: int = 15000
DEFAULT_LLM_FILE_CONTEXT_LIMIT: int = 5
DEFAULT_LLM_FILE_PREVIEW_CHARS: int = 3000

# ---------------------------------------------------------------------------
# Logging defaults
# ---------------------------------------------------------------------------
LOG_FORMAT: str = "%(asctime)s [%(name)s] %(levelname)s %(message)s"
LOG_DATE_FORMAT: str = "%Y-%m-%dT%H:%M:%SZ"
DEFAULT_LOG_LEVEL: str = "INFO"
DEFAULT_LOG_SUBDIR: str = "logs"
DEFAULT_LOG_FILENAME: str = "orchestrator.log"

# ---------------------------------------------------------------------------
# Comment timestamp format (used in work-unit Comments entries)
# ---------------------------------------------------------------------------
COMMENT_TIMESTAMP_FORMAT: str = "%Y-%m-%d %H:%M UTC"

# ---------------------------------------------------------------------------
# Git message templates for finalize operations
# ---------------------------------------------------------------------------
FINALIZE_COMMIT_TEMPLATE: str = "finalize: {branch}"
FINALIZE_PR_TITLE_TEMPLATE: str = "feat: {branch}"

# ---------------------------------------------------------------------------
# Plugin path (relative to package root)
# ---------------------------------------------------------------------------
DEFAULT_PLUGIN_SUBPATH: str = "plugin/devbench-orchestrate"

# ---------------------------------------------------------------------------
# Subprocess error exit code (Unix convention for command-not-found / timeout)
# ---------------------------------------------------------------------------
SUBPROCESS_ERROR_EXIT_CODE: int = 127

# ---------------------------------------------------------------------------
# Deterministic per-unit verification-gate ordering seed.
# ``devbench verify-ac`` pins ``pytest-randomly``'s seed (and ``PYTHONHASHSEED``)
# so a unit's ``## Verification`` pytest gate yields the same verdict on every
# run -- an order-dependent sibling test can no longer block an unrelated unit
# non-deterministically by the wall-clock seed it happened to draw. Fail-safe
# default: a fixed, arbitrary non-negative integer. Operators override via
# ``DEVBENCH_VERIFY_AC_PYTEST_SEED`` (env) when they want to rotate the pinned
# seed for a one-off reproduction. The orthogonal randomized-order signal is a
# separate scheduled / CI gate, never a per-unit block.
DEFAULT_VERIFY_AC_PYTEST_SEED: int = 0

# Exit code emitted by ``cmd_start`` when the orchestrator's SDK subprocess
# exited via ``NO_ACTIONABLE`` purely because every remaining blocker
# classifies as ``BlockedTaskState.RUNTIME_DEGRADATION`` (the SDK lost
# Agent-tool access mid-session, recoverable by a fresh subprocess) and
# there are zero IN_PROGRESS / IN_REVIEW tasks and zero
# OPERATOR_ACTION_REQUIRED blockers. The wrapping ``make start`` loop
# treats this code as "auto-restart" up to ``DEVBENCH_MAX_AUTO_RESTARTS``
# times. Any other exit (0 = clean, anything-else = real failure) is
# passed through unchanged.
ORCHESTRATOR_RESTART_EXIT_CODE: int = 42

# Exit code emitted by ``cmd_claim`` when the target repo declared in a
# work-unit file cannot be resolved by ``resolve_repo`` (unknown org/name or
# short-name alias). The claim aborts before any lock is acquired; the
# work-unit is set to ``blocked`` with a ``[BLOCKED_TARGET_REPO_UNRESOLVED]``
# marker. Value 44 is distinct from all other devbench exit codes.
CLAIM_BLOCKED_PRECLAIM: int = 44
assert CLAIM_BLOCKED_PRECLAIM == 44, "CLAIM_BLOCKED_PRECLAIM must equal 44 (spec Section 5)"

# Exit code emitted by ``cmd_get_diff`` in defer-PR mode when staged and
# unstaged changes are both empty AND no task-attributed commit exists on the
# current branch (i.e. ``git log --grep '^<task-id>:'`` returns no results).
# Caller responsibilities: surface this as a diagnosable blocker with the
# verbatim diagnostic message; do NOT treat as success (rc 0) or generic
# failure (rc 1). Value 45 is distinct from all other devbench exit codes.
GET_DIFF_NO_ATTRIBUTABLE: int = 45

# Audit-log template written to ``logs/orchestrator.log`` when
# ``cmd_start`` triggers an auto-restart. The token + task-id list let
# operators grep history for restart frequency without parsing
# free-form prose. Format: ``[ORCHESTRATOR_AUTO_RESTART]
# reason=runtime_degradation tasks=<id1,id2,...>``.
ORCHESTRATOR_AUTO_RESTART_AUDIT_PREFIX: str = "[ORCHESTRATOR_AUTO_RESTART] reason=runtime_degradation tasks="

# Issue #262 (E10-F2-S1): per-message inactivity timeout.
#
# ``cmd_start._run`` wraps each ``receive_response()`` iteration in
# ``asyncio.wait_for(..., timeout=ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS)``
# and resets the timer on every received message.  On ``TimeoutError`` it logs
# ``ORCHESTRATOR_INACTIVITY_TIMEOUT_AUDIT_PREFIX`` and issues an in-session
# continuation (counting against the E10-F1-S3 stall budget).
#
# Override via env ``DEVBENCH_ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS`` (float).
# Unset-safe: the constant is the default when the env var is absent.
# A value <= 0 disables the wrap entirely.
DEFAULT_ORCHESTRATOR_INACTIVITY_TIMEOUT_SECONDS: float = 300.0

# Verbatim audit-log prefix emitted by ``cmd_start`` when the per-message
# inactivity timeout fires (spec Section 2 Goal 2, Section 7).
ORCHESTRATOR_INACTIVITY_TIMEOUT_AUDIT_PREFIX: str = "[ORCHESTRATOR_INACTIVITY_TIMEOUT]"

# Issue #262 (E10-F1-S3): bounded in-session continuation budget.
#
# ``cmd_start._run`` increments a per-stall counter each time a non-terminal
# ResultMessage is observed and resets it to zero on any non-ResultMessage
# (i.e. genuine tool-call / progress).  When the counter reaches this cap,
# ``cmd_start`` logs ``ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_AUDIT_PREFIX``
# and exits with ``ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE``
# rather than looping forever.
#
# Override via env ``DEVBENCH_ORCHESTRATOR_MAX_TURN_END_CONTINUATIONS`` (int).
# Unset-safe: the constant is the default when the env var is absent.
DEFAULT_ORCHESTRATOR_MAX_TURN_END_CONTINUATIONS: int = 5

# Verbatim audit-log prefix emitted by ``cmd_start`` when the per-stall
# continuation counter is exhausted (spec Section 2 Goal 3, Section 7).
# Consumed by operators and automation via ``grep`` on the orchestrator log.
ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_AUDIT_PREFIX: str = "[ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED]"

# Exit code emitted by ``cmd_start`` when the in-session continuation budget
# is exhausted (spec Section 4 E10-F1-S3 AC-2, Section 14).  Value 43 is
# distinct from all other devbench exit codes:
#   42 = ORCHESTRATOR_RESTART_EXIT_CODE  (auto-restart signal)
#   44 = CLAIM_BLOCKED_PRECLAIM          (unresolvable target repo)
#   45 = GET_DIFF_NO_ATTRIBUTABLE        (no task-attributed commit)
#  127 = SUBPROCESS_ERROR_EXIT_CODE      (command-not-found / timeout)
# The wrapping ``make start`` loop must NOT treat this as an auto-restart
# because the distinct value (43 != 42) prevents misclassification.
ORCHESTRATOR_TURN_END_CONTINUATIONS_EXHAUSTED_EXIT_CODE: int = 43

# Fatal, non-retryable SDK error handling (orchestrator runaway prevention).
# A hard error such as ``model_not_found`` (the selected model does not exist /
# the account lacks access) recurs identically every turn; feeding it into the
# turn-end continuation loop re-prompts it forever. ``_run`` detects these via
# ``detect_fatal_sdk_error`` and exits fast on the FIRST occurrence with this
# distinct exit code, logging ``ORCHESTRATOR_FATAL_ERROR_AUDIT_PREFIX`` plus the
# offending model/error and a remediation line. 46 is distinct from 42/43/44/45/127.
ORCHESTRATOR_FATAL_ERROR_EXIT_CODE: int = 46
ORCHESTRATOR_FATAL_ERROR_AUDIT_PREFIX: str = "[ORCHESTRATOR_FATAL_ERROR] reason="
# Non-retryable SDK error classes. Matched case-insensitively against an SDK
# message's ``error`` field (AssistantMessage.error) or a ResultMessage error
# subtype. These cannot be resolved by retrying -- only by operator action
# (fix the configured model / credentials), so the orchestrator must fail fast
# rather than continuation-loop. Quota / rate-limit errors are deliberately
# EXCLUDED: those route to the quota wait-and-resume path, not here.
FATAL_SDK_ERROR_CODES: frozenset[str] = frozenset(
    {
        "model_not_found",
        "authentication_error",
        "permission_error",
        "invalid_request_error",
        "not_found_error",
    }
)

# Within-claim convergence bound (repeated-identical-failure churn prevention).
#
# Neither the continuation budget (catches no-activity stalls) nor the cascade
# circuit-breaker (counts claim re-queue cycles) bounds a single in-progress
# claim that repeats the SAME unresolvable failure for hours while staying
# "busy" (emitting tool-use, so the inactivity budget resets every turn). The
# convergence tracker bounds it: it watches for a REPEATED IDENTICAL failing
# AC-verify / TDD-RED / test signature within ONE claim and, when the same
# signature recurs >= ``DEFAULT_MAX_WITHIN_CLAIM_ATTEMPTS`` times, BLOCKs the
# unit with the ``CLAIM_NOT_CONVERGING`` audit marker. CRITICAL: keyed on the
# REPEATED IDENTICAL signature (not raw duration) so a genuinely-progressing
# long live run -- which emits a DIFFERENT signal each round, or no repeated
# failure -- is never killed. A distinct new signature resets the per-signature
# counts (variation = progress).
#
# Override via env ``DEVBENCH_ORCHESTRATOR_MAX_WITHIN_CLAIM_ATTEMPTS`` (int).
# Unset-safe: the constant is the default when the env var is absent.
DEFAULT_MAX_WITHIN_CLAIM_ATTEMPTS: int = 4

# Secondary wall-clock backstop for a single claim, in seconds. Set well ABOVE
# the observed-legit maximum (a 3.5h-class live firehose apply) so it only ever
# fires on a claim that is genuinely stuck for an implausibly long time WITHOUT
# a repeated-identical signature having already tripped the primary bound.
# Default 6 hours (21600s). Override via
# ``DEVBENCH_ORCHESTRATOR_MAX_CLAIM_WALL_CLOCK_SECONDS`` (float). A value <= 0
# disables the wall-clock backstop (the repeated-signature bound still applies).
DEFAULT_MAX_CLAIM_WALL_CLOCK_SECONDS: float = 21600.0

# Whether the within-claim convergence bound is active. Default True (on), per
# the operator decision that a busy-but-not-converging unit must BLOCK rather
# than churn. Override via env ``DEVBENCH_ORCHESTRATOR_WITHIN_CLAIM_CONVERGENCE_CHECK``.
DEFAULT_WITHIN_CLAIM_CONVERGENCE_CHECK: bool = True

# Verbatim audit-log marker written to the unit's Comments + emitted to the
# orchestrator log when the within-claim convergence bound trips. Names the
# recurring failure so an operator/loop can act deterministically.
CLAIM_NOT_CONVERGING_MARKER: str = "[CLAIM_NOT_CONVERGING]"

# Aggregate safety valve for block-and-continue (TDI: claim-not-converging must
# not halt the whole session).
#
# A single non-converging claim is BLOCKED with ``CLAIM_NOT_CONVERGING_MARKER``
# and the orchestrate session CONTINUES to its next in-queue unit -- one bad
# module no longer abandons the rest of a session's scope. This constant bounds
# how many DISTINCT units may each hit the convergence bound in ONE session
# before the session halts for operator attention, so a systemically-broken run
# (e.g. an environment defect every unit hits) still stops fast instead of
# blocking the entire backlog one unit at a time.
#
# Override via env ``DEVBENCH_ORCHESTRATOR_MAX_NON_CONVERGING_CLAIMS`` (int >= 1)
# or YAML ``orchestrate.max_non_converging_claims``. Unset-safe: the constant is
# the default when neither is set. Default 3: more than one defect is tolerated
# in a sweep, but a session that cannot converge three distinct units is treated
# as systemically broken.
DEFAULT_MAX_NON_CONVERGING_CLAIMS: int = 3

# TDI: in-process quota-resume cap.
#
# When ``quota_handling.on_exhaustion == "wait"`` and a quota wait recovers,
# ``cmd_start`` re-opens a FRESH ``ClaudeSDKClient`` session and re-runs the
# orchestrate skill on the remaining backlog rather than exiting (a single
# quota window must not permanently end an unattended ``--daemon`` run, which
# has no external ``make start`` restart wrapper).  This constant caps the
# number of consecutive in-process resumes so a pathological quota loop can
# never spin forever.
#
# Override via env ``DEVBENCH_MAX_QUOTA_RESUMES`` (int).  Unset-safe: the
# constant is the default when the env var is absent or unparseable.  The
# default is intentionally high so legitimate overnight runs that cross many
# quota windows are never cut short by the cap.
DEFAULT_MAX_QUOTA_RESUMES: int = 1000

# Verbatim audit-log prefix emitted by ``cmd_start`` each time it resumes the
# orchestrate skill in-process after a quota wait recovered.  Format:
# ``[ORCHESTRATOR_QUOTA_RESUME] resume=<n> max=<cap>``.  Consumed by operators
# and automation via ``grep`` on the orchestrator log.
ORCHESTRATOR_QUOTA_RESUME_AUDIT_PREFIX: str = "[ORCHESTRATOR_QUOTA_RESUME]"

# Verbatim audit-log prefix emitted by ``cmd_start`` when the in-process
# quota-resume cap (:data:`DEFAULT_MAX_QUOTA_RESUMES` /
# ``DEVBENCH_MAX_QUOTA_RESUMES``) is exhausted, so the run stops instead of
# resuming again.  Format: ``[ORCHESTRATOR_QUOTA_RESUMES_EXHAUSTED] max=<cap>``.
ORCHESTRATOR_QUOTA_RESUMES_EXHAUSTED_AUDIT_PREFIX: str = "[ORCHESTRATOR_QUOTA_RESUMES_EXHAUSTED]"

# ---------------------------------------------------------------------------
# Token arithmetic
# ---------------------------------------------------------------------------
TOKENS_PER_MILLION: int = 1_000_000

# ---------------------------------------------------------------------------
# AWS / Bedrock defaults
# ---------------------------------------------------------------------------
DEFAULT_BEDROCK_REGION: str = "us-east-1"

# ---------------------------------------------------------------------------
# GitHub security feature constants
# ---------------------------------------------------------------------------
CODEQL_STATE: str = "configured"
CODEQL_QUERY_SUITE: str = "default"
SECURITY_FEATURE_ENABLED: str = "enabled"

# ---------------------------------------------------------------------------
# Report watch interval default (seconds)
# ---------------------------------------------------------------------------
DEFAULT_REPORT_WATCH_INTERVAL: int = 3

# ---------------------------------------------------------------------------
# Report window detection
# ---------------------------------------------------------------------------
# Gap (in minutes) between consecutive log entries that signals an orchestrator
# restart, used to identify the "current session" boundary in `devbench report`.
# 30 minutes is generous enough to span a long single task (which can take
# 17+ minutes) without misclassifying mid-task quiet as a session boundary.
DEFAULT_SESSION_GAP_MINUTES: int = 30

# Logger name to filter out of session-boundary detection. The log_setup
# logger fires once per CLI invocation including every `devbench report --watch`
# tick, which would otherwise look like noise that resets the session boundary.
LOG_NOISE_LOGGER_NAME: str = "judges.log_setup"

# ---------------------------------------------------------------------------
# Anthropic prompt-caching multipliers (relative to base input rate).
# Universal across Anthropic-served Claude models.
# Source: https://platform.claude.com/docs/en/about-claude/pricing (2026-04-16).
# Override per-deployment via `report.cache_*_multiplier` in devbench.yaml.
# ---------------------------------------------------------------------------
DEFAULT_CACHE_READ_MULTIPLIER: float = 0.10
DEFAULT_CACHE_WRITE_5MIN_MULTIPLIER: float = 1.25
DEFAULT_CACHE_WRITE_1HR_MULTIPLIER: float = 2.0
# Data-residency premium when usage.inference_geo is set (US-only inference;
# applies to Opus 4.7, Opus 4.6, Sonnet 4.6+).
DEFAULT_DATA_RESIDENCY_MULTIPLIER: float = 1.10
# Fast-mode premium when usage.speed == "fast" (Opus 4.6 only at the time of
# this snapshot). Counted but not applied per-call in v1.
DEFAULT_FAST_MODE_MULTIPLIER: float = 6.0

# ---------------------------------------------------------------------------
# Report table render widths (characters)
# ---------------------------------------------------------------------------
REPORT_METRIC_COLUMN_WIDTH: int = 60
REPORT_VALUE_COLUMN_WIDTH: int = 16

# ---------------------------------------------------------------------------
# Time unit conversions
# ---------------------------------------------------------------------------
MS_PER_SECOND: int = 1000
SECONDS_PER_MINUTE: int = 60
SECONDS_PER_HOUR: int = 3600
PERCENT_MULTIPLIER: int = 100

# ---------------------------------------------------------------------------
# Backlog index column count
# The BACKLOG.md table has 7 data columns. Splitting a pipe-delimited row
# by "|" produces 9 cells (empty leading cell + 7 data + empty trailing cell).
# ---------------------------------------------------------------------------
BACKLOG_INDEX_CELL_COUNT: int = 9

# ---------------------------------------------------------------------------
# Per-agent model overrides (Option A shadow-plugin-dir, ADR-25)
# ---------------------------------------------------------------------------
# Workspace-relative directory holding the materialised shadow plugin tree.
# When operators set ``agents.<name>: <model>`` in ``devbench.yaml`` the
# canonical marketplace plugin cannot be edited; instead, a shadow tree is
# built under ``<workspace>/<PLUGIN_SHADOW_DIR_NAME>/devbench/`` that mirrors
# the canonical via symlinks for every file except the overridden agent .md
# files. ``cmd_start`` and ``devbench prepare-plugin-shadow`` both point the
# Claude Agent SDK / ``claude --plugin-dir`` at this path so non-interactive
# and interactive modes share one mechanism.
PLUGIN_SHADOW_DIR_NAME: str = ".devbench/plugin-shadow"

# Filename of the owner sentinel written by ``cmd_start`` inside the shadow
# plugin tree at ``<workspace>/<PLUGIN_SHADOW_DIR_NAME>/devbench/<filename>``.
# The sentinel records the SET of orchestrator PIDs that currently own the
# materialised tree (one line per PID); dead PIDs are pruned on every read.
# ``clear_shadow_plugin`` refuses to delete the tree while ANY recorded PID
# is alive (raising ``RuntimeError``) so a concurrent ``devbench
# prepare-plugin-shadow`` invocation cannot clear a running orchestrator's
# plugin files out from under it. Multi-owner so two concurrent
# ``devbench start`` sessions can SHARE one identical shadow (each registers
# as an additional owner) instead of the second session aborting. Lives
# inside the tree so ``rmtree`` of the tree removes the sentinel atomically
# when a clean rebuild is allowed.
SHADOW_PID_SENTINEL_FILENAME: str = ".pid"

# Filename of the overrides-fingerprint marker written inside the shadow tree
# alongside the owner sentinel. Records a stable hash of the per-agent model
# overrides the shadow was built for, so ``materialise_shadow_plugin`` can tell
# whether an existing shadow is up-to-date for the requested overrides (reuse,
# register an additional owner, skip clear+rebuild) or stale (rebuild only when
# no live owner remains; otherwise fail fast). Lives inside the tree so a
# clean rebuild removes it atomically with the rest of the shadow.
SHADOW_OVERRIDES_FINGERPRINT_FILENAME: str = ".overrides-fingerprint"

# Short-name model aliases accepted in ``agents.*`` YAML values when
# ``use_bedrock: false``. Mirrors the convenience short forms the Anthropic
# SDK accepts.
ALLOWED_AGENT_MODEL_SHORT_NAMES: frozenset[str] = frozenset({"opus", "sonnet"})

# Full Anthropic model id pattern (``claude-opus-4-7``, ``claude-sonnet-4-6``,
# ``claude-sonnet-4-6-20250514``). Accepted when ``use_bedrock: false``.
# Note: ids containing ``haiku`` are rejected by ``validate_agent_model_value()``
# even though they would otherwise match this pattern.
ANTHROPIC_AGENT_MODEL_PATTERN: re.Pattern[str] = re.compile(r"^claude-[a-z0-9]+(-[a-z0-9]+)+$")

# AWS Bedrock model id pattern (``us.anthropic.claude-opus-4-7-v1``). Accepted
# only when ``use_bedrock: true``; rejected otherwise.
BEDROCK_AGENT_MODEL_PATTERN: re.Pattern[str] = re.compile(r"^us\.anthropic\.claude-[a-z0-9-]+-v[0-9]+$")

# ---------------------------------------------------------------------------
# Session-management constants (spec 4.4.1 named sessions, issue #192)
# Consumed exclusively by ``src/devbench/session.py``; defined here so no
# literal values appear inline in that module.
# ---------------------------------------------------------------------------
# Workspace-relative path to the base directory holding all session state dirs.
# Full path is ``<workspace_root>/<SESSION_SESSIONS_BASE_DIR>``.
# Consumed by ``src/devbench/log_setup.py`` (per-session log routing) and
# ``src/devbench/session.py`` (registry and per-session state directories).
SESSION_SESSIONS_BASE_DIR: str = ".devbench/sessions"

# Workspace-relative path to the session registry JSON file.
# Full path is ``<workspace_root>/<SESSION_REGISTRY_PATH>``.
SESSION_REGISTRY_PATH: str = ".devbench/sessions/registry.json"

# Filename of the exclusive flock file created under ``<workspace_root>/.devbench/``.
# Full path is ``<workspace_root>/.devbench/<SESSION_BACKLOG_LOCK_NAME>``.
SESSION_BACKLOG_LOCK_NAME: str = "BACKLOG.lock"

# Default timeout in seconds for acquiring the BACKLOG.lock via ``fcntl.flock``.
# Raises ``TimeoutError`` when the lock cannot be acquired within this window.
SESSION_DEFAULT_FLOCK_TIMEOUT_SECONDS: int = 30

# Filename of the PID file written inside each session's state directory
# (``<workspace_root>/.devbench/sessions/<name>/<SESSION_PID_FILENAME>``).
SESSION_PID_FILENAME: str = "pid"

# Suffix appended to the registry JSON path for the intermediate temp file
# used during atomic registry writes (write-then-rename pattern).
SESSION_REGISTRY_TMP_SUFFIX: str = ".tmp"

# Default session name used when --name is not supplied to ``cmd_start``.
# Consumed by ``src/devbench/session.py`` and ``cmd_start``; defined here so
# no literal strings appear inline in those modules.
SESSION_DEFAULT_NAME: str = "default"

# Filename written inside a session's state directory to record the ISO-8601
# timestamp at which the session was started.
# Full path: ``<workspace_root>/.devbench/sessions/<name>/<SESSION_STARTED_AT_FILENAME>``.
SESSION_STARTED_AT_FILENAME: str = "started_at"

# Filename written inside a session's state directory to record the identity
# (username / process tag) of the agent that started the session.
# Full path: ``<workspace_root>/.devbench/sessions/<name>/<SESSION_STARTED_BY_FILENAME>``.
SESSION_STARTED_BY_FILENAME: str = "started_by"

# Poll interval (seconds) between non-blocking flock attempts in
# :func:`devbench.session.flock_backlog`.  A sub-second value keeps the
# effective wait latency low without busy-spinning.  Callers use
# ``min(SESSION_FLOCK_POLL_INTERVAL_SECONDS, remaining)`` so the deadline is
# never overshot.  Override via ``DEVBENCH_SESSION_FLOCK_POLL_INTERVAL``
# env var if the default 0.1 s is too coarse or too fine for a given deployment.
SESSION_FLOCK_POLL_INTERVAL_SECONDS: float = 0.1

# Filename of the drain signal file written inside a per-session state directory.
# Full path: ``<workspace_root>/.devbench/sessions/<name>/<SESSION_DRAIN_SIGNAL_FILENAME>``.
# Consumed by ``src/devbench/drain.py`` (resolve_drain_signal_path) and
# ``src/devbench/cli.py`` (_session_drain_state_str).
SESSION_DRAIN_SIGNAL_FILENAME: str = "drain.signal"

# Relative path (from workspace root) of the orchestrator restart marker
# file written by ``cmd_start`` on every startup.  Issue #215: bounds the
# audit-row scan window in
# ``devbench.backlog.proposal._has_runtime_degradation_signal`` so RUNTIME_DEGRADATION
# classification clears on operator-driven restart.  The file contains a
# single ISO 8601 UTC timestamp string.
LAST_RESTART_MARKER_PATH: str = ".devbench/last-restart"

# ---------------------------------------------------------------------------
# Bounded skill iterate-until-perfect mechanism (spec section 4.6.0, issue #204)
# The four onboarding skills (create-spec, spec-to-backlog, bootstrap-environment,
# configure-devbench) each run a bounded self-critique loop. Iteration state is
# persisted per skill so a max-iterations exhaustion is observable as an audit
# row rather than buried in skill prose. Consumed by ``src/devbench/skill_state.py``
# and the four SKILL.md files in ``plugin/devbench-orchestrate/skills/``.
# ---------------------------------------------------------------------------

# Maximum number of self-critique iterations a skill may run before emitting
# the [SKILL_MAX_ITERATIONS_REACHED] audit row and exiting non-zero.
SKILL_MAX_ITERATIONS: int = 5

# Quality threshold (count of unresolved items) below which the skill is
# considered converged. Zero means "no unresolved items".
SKILL_QUALITY_THRESHOLD: int = 0

# Workspace-relative path to the base directory holding per-skill checkpoint
# files. Full path: ``<workspace_root>/.devbench/<SKILL_STATE_DIR_NAME>/<skill>.json``.
SKILL_STATE_DIR_NAME: str = ".devbench/skill-state"

# ---------------------------------------------------------------------------
# Supervise: interactive `claude` CLI orchestrator under a detached `screen`
# daemon (spec devbench-supervise-screen-orchestrator, FR-17, Section 5.5).
#
# The supervise path runs the orchestrator as an interactive `claude` session
# billed against the Claude Code subscription's rolling 5-hour windows (NOT the
# Anthropic API). State lives under ``<workspace_root>/.devbench/supervise/``,
# parallel to (and intentionally separate from, D-8) the SDK session tree under
# ``.devbench/sessions/``. Consumed by ``src/devbench/supervise.py`` and the
# ``cmd_supervise`` verb wiring in ``src/devbench/cli.py``.
# ---------------------------------------------------------------------------

# Workspace-relative base directory for all supervise per-session state.
# Full path: ``<workspace_root>/<SUPERVISE_BASE_DIR>/<name>/``.
SUPERVISE_BASE_DIR: str = ".devbench/supervise"

# Workspace-relative path to the supervise registry JSON file (a JSON array
# mirroring SESSION_REGISTRY_PATH). Full path:
# ``<workspace_root>/<SUPERVISE_REGISTRY_PATH>``.
SUPERVISE_REGISTRY_PATH: str = ".devbench/supervise/registry.json"

# Filename of the per-session state JSON written inside each supervise session's
# state directory (``<workspace>/.devbench/supervise/<name>/<SUPERVISE_STATE_FILENAME>``).
SUPERVISE_STATE_FILENAME: str = "state.json"

# Filename of the redacted PTY transcript log written inside each supervise
# session's state directory. Created mode 0600 (FR-24).
SUPERVISE_PTY_LOG_FILENAME: str = "pty.log"

# Filename of the stop-request control file the operator-facing ``stop`` verb
# writes and the in-screen ``__run`` supervisor polls (Section 4.2, Section 5.5).
SUPERVISE_STOP_REQUEST_FILENAME: str = "stop.request"

# Filename of the structured supervisor log the in-screen ``__run`` supervisor
# writes (Section 7.2). Carries the ``[SUPERVISE_STATE]`` transition lines.
SUPERVISE_SUPERVISOR_LOG_FILENAME: str = "supervisor.log"

# Suffix appended to the supervise registry path for the intermediate temp file
# used during atomic registry writes (write-then-rename pattern).
SUPERVISE_REGISTRY_TMP_SUFFIX: str = ".tmp"

# Default ``screen`` session-name prefix (Section 5.1, ``screen_name_prefix``).
# The screen for session ``N`` is ``<prefix><N>``. Override via
# ``DEVBENCH_SUPERVISE_SCREEN_NAME_PREFIX`` env or ``supervise.screen_name_prefix``.
SUPERVISE_SCREEN_NAME_PREFIX_DEFAULT: str = "devbench-supervise-"

# Default supervise session name when ``--name`` is not supplied (Section 4.0, FR-2).
SUPERVISE_DEFAULT_NAME: str = "default"

# Default effort level for the interactive session (Section 5.4, FR-19, D-11).
# One of low|medium|high|xhigh|max; ``xhigh`` is the operator default.
SUPERVISE_EFFORT_DEFAULT: str = "xhigh"

# Valid effort levels accepted by ``--effort`` / ``supervise.effort`` (Section 1.9,
# Section 5.2). ``max`` is session-only; all are accepted by the resolver.
SUPERVISE_VALID_EFFORT_LEVELS: frozenset[str] = frozenset({"low", "medium", "high", "xhigh", "max"})

# Valid resume modes for ``supervise.restart.resume_mode`` (Section 5.1, 5.2).
SUPERVISE_VALID_RESUME_MODES: frozenset[str] = frozenset({"continue", "resume"})

# The billing channel a supervise session always reports (Section 0.2, FR-9).
SUPERVISE_BILLING_CHANNEL: str = "subscription"

# Always-deny environment variables stripped from EVERY supervise session env
# (Section 3.6.1, FR-21). These route inference to API/Bedrock billing and
# defeat the subscription-billing goal; they are NON-REMOVABLE -- a config that
# tries to whitelist any of these via ``supervise.env.deny_vars`` fails fast.
# Consumed by ``EnvSanitizer`` and the ``_parse_supervise_config`` guard.
SUPERVISE_ALWAYS_DENY_ENV_VARS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_URL",
    "ANTHROPIC_BASE_URL",
    "DEVBENCH_USE_BEDROCK",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
)

# Default ADDITIONAL deny vars for ``supervise.env.deny_vars`` (Section 5.1).
# The always-deny set above is layered on top and cannot be removed.
SUPERVISE_DEFAULT_EXTRA_DENY_VARS: tuple[str, ...] = ("AWS_PROFILE", "AWS_SESSION_TOKEN")

# Default per-field timeouts (seconds) for ``supervise.timeouts`` (Section 5.1).
# Every value is overridable via ``DEVBENCH_SUPERVISE_*`` env or YAML.
SUPERVISE_TIMEOUT_READY_PROMPT_SECONDS_DEFAULT: int = 120
SUPERVISE_TIMEOUT_IDLE_SECONDS_DEFAULT: int = 1800
SUPERVISE_TIMEOUT_COMMAND_ACK_SECONDS_DEFAULT: int = 60
SUPERVISE_TIMEOUT_GRACEFUL_STOP_SECONDS_DEFAULT: int = 900
# The graceful ``stop`` verb polls the registry on this cadence (event-driven,
# bounded by ``graceful_stop_seconds``) while waiting for the in-screen ``__run``
# supervisor to drain and reach a terminal; the read-only ``attach`` follow uses
# the SAME cadence as its bounded ``tail -F`` re-read interval (Section 4.2, 4.7).
SUPERVISE_TIMEOUT_POLL_INTERVAL_SECONDS_DEFAULT: int = 2
# Safety timeout (seconds) bounding the short, non-interactive ``subprocess.run``
# command invocations the supervisor shells out to: ``screen -ls`` (live-session
# listing), ``screen -S <name> -X quit`` (teardown), and ``<tool> --version`` (the
# claude/screen audit probe). These are command-invocation hang guards, NOT the
# enumerated PTY/quota operational timeouts above; per FR-19 / Section 7.4 every
# operational value -- including these -- is a config field with a documented
# default, never a hardcoded literal in the supervisor.
SUPERVISE_TIMEOUT_COMMAND_INVOCATION_SECONDS_DEFAULT: int = 30

# Default bounded auto-restart attempts on the exit-42-equivalent (Section 5.1,
# FR-12, Section 4.3). Override via ``DEVBENCH_SUPERVISE_RESTART_MAX_ATTEMPTS``.
SUPERVISE_RESTART_MAX_ATTEMPTS_DEFAULT: int = 5

# Default resume mode for ``supervise.restart.resume_mode`` (Section 5.1).
SUPERVISE_RESUME_MODE_DEFAULT: str = "continue"

# Default detection-pattern regexes (Section 5.1, 6.3, FR-29). These are
# CONFIG defaults; every one is overridable via YAML so a CLI prompt-text change
# is fixed without code change. The quota_limit / quota_wait_prompt patterns are
# PLACEHOLDERS pending DI-5 (QUOTA-VERIFICATION-TODO.md) -- the in-session-wait
# path is best-effort until a real quota event is captured. ``reset_at`` mirrors
# ``quota._RESET_AT_RE``. Keys map to ``SuperviseDetectionPatternsConfig`` fields.
SUPERVISE_DETECTION_PATTERNS_DEFAULT: dict[str, str] = {
    # The box-drawing bar (U+2502) and the verbatim Unicode apostrophe (U+2019,
    # which appears in the real CLI line "You've hit your limit") are written as
    # explicit escapes so the source stays ASCII; see ``quota._QUOTA_MARKERS``.
    "ready_prompt": "(?m)^\\s*(>|│\\s*>)\\s*$",
    "working_prompt": r"(?i)(esc to interrupt|tokens|thinking)",
    "quota_limit": "(?i)(You(\u2019ve|'ve| have) hit your limit|rate.?limit.*(exceeded|reached|resets))",
    "quota_wait_prompt": r"(?i)(wait.*reset|retry.*later|press.*to wait)",
    "reset_at": r"resets\s+(\d{1,2}):(\d{2})(am|pm)\s+\(UTC\)",
    "circuit_breaker": r"\[CIRCUIT_BREAKER\]|cascade depth exceeded",
    "harness_block": r"\[HARNESS_INTEGRITY\]",
    "crash": r"(?i)(panic|fatal error|traceback \(most recent call last\))",
}

# Default log-tail marker sets (Section 5.1, FR-14, FR-29). These are devbench's
# OWN log markers (Section 1.6), stable across CLI versions. Keys map to
# ``SuperviseLogTailConfig`` fields.
SUPERVISE_LOG_TAIL_ORCHESTRATOR_LOG_RELPATH_DEFAULT: str = "logs/orchestrator.log"
SUPERVISE_LOG_TAIL_MARKERS_CLEAN_DEFAULT: tuple[str, ...] = (
    "ALL_DONE",
    "NO_ACTIONABLE",
    "[ORCHESTRATOR_TERMINAL_EXIT]",
)
SUPERVISE_LOG_TAIL_MARKERS_QUOTA_DEFAULT: tuple[str, ...] = (
    "[QUOTA_WAITING]",
    "[QUOTA_POLLING]",
    "[ORCHESTRATOR_QUOTA_RESUME]",
)
SUPERVISE_LOG_TAIL_MARKERS_FAULT_DEFAULT: tuple[str, ...] = (
    "[ORCHESTRATOR_STOP_REASON]",
    "[ORCHESTRATOR_FATAL_ERROR]",
    "[HARNESS_INTEGRITY]",
)
SUPERVISE_LOG_TAIL_MARKERS_RESTART_DEFAULT: tuple[str, ...] = ("[ORCHESTRATOR_AUTO_RESTART]",)

# Default redaction regexes applied to every chunk written to ``pty.log``
# (Section 3.6.3, FR-24). Override via ``supervise.logging.redact_patterns``.
SUPERVISE_LOG_PTY_LOG_RELPATH_DEFAULT: str = "pty.log"
SUPERVISE_LOG_REDACT_PATTERNS_DEFAULT: tuple[str, ...] = (
    r"sk-ant-[A-Za-z0-9_-]+",
    r"AKIA[0-9A-Z]{16}",
    r"(?i)aws_secret[^\s]*",
    r"Bearer\s+[A-Za-z0-9._-]+",
)

# Default injectable-command registry (Section 5.3, FR-28). A name->literal map;
# new operator capabilities are added by adding a config entry, NO code change.
# ``quota_wait_choice`` is a PLACEHOLDER pending DI-5. Override/extend via
# ``supervise.injectable_commands``.
SUPERVISE_INJECTABLE_COMMANDS_DEFAULT: dict[str, str] = {
    "orchestrate": "/devbench-orchestrate:orchestrate",
    "effort_xhigh": "/effort xhigh",
    "model_opus": "/model opus",
    "quota_wait_choice": "1",
    "drain_now": "/exit",
}

# The set of supervise lifecycle states (Section 4.8, FR-27). Persisted in
# ``state.json``'s ``state`` field and surfaced by ``status``/``info``.
SUPERVISE_STATE_STARTING: str = "starting"
SUPERVISE_STATE_RUNNING: str = "running"
SUPERVISE_STATE_QUOTA_WAITING: str = "quota-waiting"
SUPERVISE_STATE_QUOTA_RESUMED: str = "quota-resumed"
SUPERVISE_STATE_DRAINING: str = "draining"
SUPERVISE_STATE_COMPLETED_CLEAN: str = "completed-clean"
SUPERVISE_STATE_FAULTED: str = "faulted"
SUPERVISE_STATE_RESTARTING: str = "restarting"
SUPERVISE_STATE_STOPPED: str = "stopped"
SUPERVISE_VALID_STATES: frozenset[str] = frozenset(
    {
        SUPERVISE_STATE_STARTING,
        SUPERVISE_STATE_RUNNING,
        SUPERVISE_STATE_QUOTA_WAITING,
        SUPERVISE_STATE_QUOTA_RESUMED,
        SUPERVISE_STATE_DRAINING,
        SUPERVISE_STATE_COMPLETED_CLEAN,
        SUPERVISE_STATE_FAULTED,
        SUPERVISE_STATE_RESTARTING,
        SUPERVISE_STATE_STOPPED,
    }
)

# Reconcile-only display states surfaced by ``supervise info`` (Section 4.5,
# FR-11) -- they are NOT persisted lifecycle states. A live screen with no
# registry entry is shown ``unknown``; a registry entry with no live screen is
# shown ``stale``. (A stale entry hit by ``stop`` is reconciled to the persisted
# ``stopped`` state with the ``SUPERVISE_EXIT_REASON_STALE_RECONCILED`` reason.)
SUPERVISE_INFO_STATE_UNKNOWN: str = "unknown"
SUPERVISE_INFO_STATE_STALE: str = "stale"

# Exit reasons the operator-facing ``stop`` verb records (Section 4.2, FR-5).
SUPERVISE_EXIT_REASON_GRACEFUL_STOP: str = "graceful-stop"
SUPERVISE_EXIT_REASON_HARD_STOP: str = "hard-stop"
SUPERVISE_EXIT_REASON_STALE_RECONCILED: str = "stale-screen-reconciled"

# The six operator-facing supervise sub-verbs + the hidden internal ``__run``
# sub-verb (D-10) that the screen runs in the foreground. ``__run`` is NOT in
# ``supervise --help``. Consumed by ``cmd_supervise`` (FR-1).
SUPERVISE_SUBVERBS: tuple[str, ...] = ("start", "stop", "restart", "status", "info", "attach")
SUPERVISE_INTERNAL_RUN_SUBVERB: str = "__run"

# Session-name grammar (ADR-23, FR-2): non-empty, leading alphanumeric, then
# alphanumerics, hyphen, underscore. Path-traversal ``..`` is rejected separately.
SUPERVISE_SESSION_NAME_PATTERN: str = r"^[A-Za-z0-9][A-Za-z0-9_-]*$"

# Non-zero exit code the supervisor (``__run``) returns for every FAULT outcome
# (Section 4.6, FR-13). Reuses the orchestrator's fatal-error code (46) so the
# supervised fault and the SDK-path fatal error share a single non-zero class;
# the specific cause is carried in the registry ``exit_reason`` field (and the
# operator-visible ``[SUPERVISE_FAULT]`` audit line), not in the numeric code.
SUPERVISE_FAULT_EXIT_CODE: int = ORCHESTRATOR_FATAL_ERROR_EXIT_CODE

# Structured audit-line prefixes the in-screen ``__run`` supervisor writes to its
# per-session ``supervisor.log`` (Section 7.2). ``[SUPERVISE_STATE]`` records each
# state-machine transition; ``[SUPERVISE_FAULT]`` records a classified fault;
# ``[SUPERVISE_RESTART]`` records each bounded auto-restart relaunch (FR-12).
SUPERVISE_STATE_AUDIT_PREFIX: str = "[SUPERVISE_STATE]"
SUPERVISE_FAULT_AUDIT_PREFIX: str = "[SUPERVISE_FAULT]"
SUPERVISE_RESTART_AUDIT_PREFIX: str = "[SUPERVISE_RESTART]"

# Audit-row tag emitted when a skill exhausts its iteration budget without
# reaching SKILL_QUALITY_THRESHOLD. Operator-visible signal that the skill
# needs human attention.
SKILL_AUDIT_MAX_ITERATIONS_REACHED: str = "[SKILL_MAX_ITERATIONS_REACHED]"

# Audit-row tag emitted when a skill converges (unresolved count <= threshold).
SKILL_AUDIT_QUALITY_THRESHOLD_REACHED: str = "[SKILL_QUALITY_THRESHOLD_REACHED]"

# ---------------------------------------------------------------------------
# Quota handling constants (issue #234, #254)
# ---------------------------------------------------------------------------
# Model used to probe whether the quota has recovered after a rate-limit wait.
# Updated to Opus 4.8 under issue #254 (the probe must succeed on the
# recovery path; using the same model that was rate-limited confirms the
# subscription tier has refreshed).
RECOVERY_PROBE_MODEL: str = "claude-opus-4-8"

# Master toggle for the quota wait-and-resume feature (issue #234).
# When True, detect_quota_error is called on each SDK surface and a detected
# QuotaExhaustedError triggers the wait-and-resume path instead of raising.
# Operators can disable via DEVBENCH_QUOTA_HANDLING_ENABLED=0 (or false/no/off)
# in the environment; the integration layer reads this constant as the default
# when no env override is present.
QUOTA_HANDLING_DEFAULT_ENABLED: bool = True

# ---------------------------------------------------------------------------
# Skills Workflow fan-out constants (issue #266, E12-F1-S1-T1)
# ---------------------------------------------------------------------------
# Environment variable name for the Workflow-tool fan-out master toggle.
# Resolution: env var > YAML skills.use_workflow > DEFAULT_SKILLS_USE_WORKFLOW (false).
# When false (the default) both skills fall back to the existing Agent / single-agent
# path byte-for-byte; no behavior change is required from the operator to upgrade.
DEVBENCH_SKILLS_USE_WORKFLOW_ENV: str = "DEVBENCH_SKILLS_USE_WORKFLOW"

# Environment variable name for the Workflow chunk size.
# Resolution: env var > YAML skills.workflow_chunk_size > DEFAULT_SKILLS_WORKFLOW_CHUNK_SIZE.
# Provider rate-limiting has been observed near 15 concurrent agents (spec Section 1);
# 3-4 is the recommended mitigation chunk size.
DEVBENCH_SKILLS_WORKFLOW_CHUNK_SIZE_ENV: str = "DEVBENCH_SKILLS_WORKFLOW_CHUNK_SIZE"

# Environment variable name for the adversarial-review size/complexity gate (E12-F2).
# Resolution: env var > YAML skills.adversarial_review_threshold > DEFAULT_SKILLS_ADVERSARIAL_REVIEW_THRESHOLD.
DEVBENCH_SKILLS_ADVERSARIAL_REVIEW_THRESHOLD_ENV: str = "DEVBENCH_SKILLS_ADVERSARIAL_REVIEW_THRESHOLD"

# Unset-safe default: Workflow fan-out is opt-in; absent env and YAML defaults to false.
DEFAULT_SKILLS_USE_WORKFLOW: bool = False

# Default chunk size chosen to stay well under the ~15-concurrent-agent rate limit
# observed from the provider (spec Section 1).
DEFAULT_SKILLS_WORKFLOW_CHUNK_SIZE: int = 3

# Default adversarial-review threshold consumed by E12-F2.
# When the spec produces more than this many work units the adversarial pass is triggered.
DEFAULT_SKILLS_ADVERSARIAL_REVIEW_THRESHOLD: int = 10

# ---------------------------------------------------------------------------
# Auto-resolve engine constants (issue #263, E11-F1-S1)
# ---------------------------------------------------------------------------
# Environment variable name for the auto-resolve enabled flag.
# Resolution: env var > YAML > DEFAULT_AUTO_RESOLVE_ENABLED (false).
DEVBENCH_AUTO_RESOLVE_ENABLED_ENV: str = "DEVBENCH_AUTO_RESOLVE_ENABLED"

# Environment variable name for the auto-resolve max-attempts override.
# Resolution: env var > YAML > DEFAULT_AUTO_RESOLVE_MAX_ATTEMPTS.
DEVBENCH_AUTO_RESOLVE_MAX_ATTEMPTS_ENV: str = "DEVBENCH_AUTO_RESOLVE_MAX_ATTEMPTS"

# Unset-safe default: advise-only mode is preserved by default (opt-in).
DEFAULT_AUTO_RESOLVE_ENABLED: bool = False

# Maximum number of auto-apply attempts before the engine escalates to the
# operator. Consumed by E11-F1-S2 (bounded auto-apply with escalation).
DEFAULT_AUTO_RESOLVE_MAX_ATTEMPTS: int = 3

# Verbatim audit string logged on every successful auto-apply (spec Section 7).
# Field order: [AUTO_RESOLVED] task_id=<id> signature=<sig> remediation=<verb>.
AUTO_RESOLVE_AUDIT_STRING: str = "[AUTO_RESOLVED]"

# Verbatim audit string logged when the per-(task_id, signature) budget is
# exhausted and the engine escalates to operator advise-only mode (E11-F1-S2, spec Section 7).
AUTO_RESOLVE_ESCALATED_STRING: str = "[AUTO_RESOLVE_ESCALATED]"

# Non-destructive whitelist: only these remediation verbs may be auto-applied.
# Derived from the E7-F3 seven-bucket remediation matrix (spec Section 4 E11-F1-S1 AC-2).
# re-queue and set-status in-queue are semantically equivalent paths for the same action.
AUTO_RESOLVE_WHITELIST: frozenset[str] = frozenset(
    {
        "re-queue",
        "set-status in-queue",
        "reconcile-cascade",
        "restart-signal",
    }
)

# Hard-excluded destructive verbs that MUST NEVER be auto-applied (spec Section 4 E11-F1-S1 AC-2).
# This set is enforced independently of the whitelist so a whitelist expansion
# cannot accidentally admit a destructive verb.
AUTO_RESOLVE_DESTRUCTIVE_VERBS: frozenset[str] = frozenset(
    {
        "decline",
        "mark-done",
        "force-status",
    }
)

# ---------------------------------------------------------------------------
# Canonical verdict audit-line regex (public export, E8-F2-S1-T3)
# ---------------------------------------------------------------------------
# Matches a verdict audit entry written by a review-judge agent.  The line
# shape is anchored at column 0:
#
#   [YYYY-MM-DD HH:MM UTC] [judge/<name>] [REVIEW_PASS|REVIEW_FAIL|REVIEW_REJECTED] ...
#
# Named groups:
#   judge  -- the judge name (e.g. "code_review", "security_review")
#   action -- the verdict token (e.g. "REVIEW_PASS")
#
# Lines that start with an agent/ prefix, orchestrator audit entries,
# indented lines, or lines that omit the timestamp bracket do NOT match.
#
# Note: manager.py keeps a private copy (_CANONICAL_VERDICT_RE) for
# historical reasons; a future DRY extraction may unify them.
CANONICAL_VERDICT_RE: re.Pattern[str] = re.compile(
    r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC\] \[judge/(?P<judge>[^\]]+)\] \[(?P<action>[^\]]+)\]"
)
