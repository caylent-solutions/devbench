"""Operator-facing notification dispatcher.

devbench can post a Slack message on every interesting lifecycle
event -- work-unit done, blocked, materialised, promoted; PR opened,
merged, CI failed; orchestrator stop, auto-restart; quota pause,
quota resume.  Each event is independently toggled via
``notifications.events.<event_name>`` in ``devbench.yaml``.  Each
notification endpoint (Slack today; Discord / Teams / generic raw-JSON
later) lives in its own nested config block under ``notifications:``
so future endpoints land as additive change without touching the
event-toggle surface.

Slack incoming webhooks post to a channel, not a user DM.  The
recommended pattern is a private channel ``#devbench-<you>`` with
only the operator as a member, plus a ``<!here>`` mention in every
payload so Slack pushes a desktop + mobile notification even though
the message lands in a channel.  ``<!here>`` notifies every online
member of the channel: in a one-person private channel that's just
the operator, and in a shared channel it notifies the whole team,
so the same payload works for both DM-yourself and team-channel
routing.  See ``docs/slack-notifications.md`` for the end-to-end
operator walkthrough.

This module is a thin payload-builder + dispatcher; HTTP transport
is delegated to :func:`devbench.quota.post_webhook`.  Every public
``notify_*`` function is **best-effort** -- any exception during
delivery is logged to stderr but never propagates, mirroring the
``post_webhook`` contract.  The orchestrator must never crash because
Slack was down.

Each ``notify_*`` function follows the same pattern:

1. Read ``RUNTIME_CONFIG.notifications`` once.
2. Return immediately when the master switch is off or the matching
   event toggle is false (no HTTP calls).
3. For each endpoint whose ``enabled`` flag is true and whose
   ``webhook_url`` is non-null, build the appropriate payload and POST.
4. Catch and log every exception so dispatch is best-effort.

Sensitive data: webhook URLs are credentials.  This module masks all
but the last 8 characters of any URL it logs.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from devbench.utils.io import atomic_write_text

# ---------------------------------------------------------------------------
# Event-kind tokens.  Every public ``notify_*`` function dispatches under
# one of these strings; the corresponding ``NotificationsEventsConfig`` field
# uses the same name for grep-ability.
# ---------------------------------------------------------------------------

EVENT_WORK_UNIT_DONE = "work_unit_done"
EVENT_WORK_UNIT_BLOCKED_OPERATOR = "work_unit_blocked_operator"
EVENT_WORK_UNIT_BLOCKED_RUNTIME_DEGRADATION = "work_unit_blocked_runtime_degradation"
EVENT_WORK_UNIT_BLOCKED_HELD = "work_unit_blocked_held"
EVENT_WORK_UNIT_BLOCKED_ON_HELD = "work_unit_blocked_on_held"
EVENT_WORK_UNIT_BLOCKED_AUTO_CLEARING = "work_unit_blocked_auto_clearing"
EVENT_WORK_UNIT_BLOCKED_AWAITING_DEPENDENCY = "work_unit_blocked_awaiting_dependency"
EVENT_WORK_UNIT_BLOCKED_AMENDMENT_RECOVERY = "work_unit_blocked_amendment_recovery"
EVENT_WORK_UNIT_MATERIALISED = "work_unit_materialised"
EVENT_WORK_UNIT_PROMOTED = "work_unit_promoted"
EVENT_PR_OPENED = "pr_opened"
EVENT_PR_MERGED = "pr_merged"
EVENT_CI_FAILURE = "ci_failure"
# Issue #219 / #220: ``ci_pass`` fires on CIResult.GREEN inside the
# finalize path so operators running ``auto_merge: false`` know the
# batch PR is ready for manual merge.  Sibling event to ``ci_failure``.
EVENT_CI_PASS = "ci_pass"
EVENT_ORCHESTRATOR_STOP = "orchestrator_stop"
EVENT_ORCHESTRATOR_AUTO_RESTART = "orchestrator_auto_restart"
EVENT_QUOTA_PAUSE = "quota_pause"
EVENT_QUOTA_RESUME = "quota_resume"

ALL_EVENTS: tuple[str, ...] = (
    EVENT_WORK_UNIT_DONE,
    EVENT_WORK_UNIT_BLOCKED_OPERATOR,
    EVENT_WORK_UNIT_BLOCKED_RUNTIME_DEGRADATION,
    EVENT_WORK_UNIT_BLOCKED_HELD,
    EVENT_WORK_UNIT_BLOCKED_ON_HELD,
    EVENT_WORK_UNIT_BLOCKED_AUTO_CLEARING,
    EVENT_WORK_UNIT_BLOCKED_AWAITING_DEPENDENCY,
    EVENT_WORK_UNIT_BLOCKED_AMENDMENT_RECOVERY,
    EVENT_WORK_UNIT_MATERIALISED,
    EVENT_WORK_UNIT_PROMOTED,
    EVENT_PR_OPENED,
    EVENT_PR_MERGED,
    EVENT_CI_FAILURE,
    EVENT_CI_PASS,
    EVENT_ORCHESTRATOR_STOP,
    EVENT_ORCHESTRATOR_AUTO_RESTART,
    EVENT_QUOTA_PAUSE,
    EVENT_QUOTA_RESUME,
)


# ---------------------------------------------------------------------------
# Notification-payload constants
# ---------------------------------------------------------------------------

# Slack's broadcast mention that notifies every online member of the channel
# the webhook posts to.  Operators routing to a one-person private DM channel
# get a personal push; operators routing to a shared team channel notify the
# whole online team.  Single payload works for both.
SLACK_HERE_MENTION: str = "<!here>"


# ---------------------------------------------------------------------------
# Classification-transition cache (#207)
# ---------------------------------------------------------------------------
#
# The base ``notify_work_unit_blocked_operator`` fires once at ``mark_blocked``
# time and only when classification == OPERATOR_ACTION_REQUIRED at that exact
# moment.  When a blocked task's classification later transitions into
# OPERATOR_ACTION_REQUIRED (because a dep landed but the task never
# auto-unblocked, or a ``[BLOCKED]`` audit went stale), no ping fires --
# operators silently miss notifications they explicitly enabled.
#
# ``notify_blocked_operator_transition`` closes that gap with a per-workspace
# JSON cache of each task's last-observed classification.  Callers from write
# sites (``mark_blocked``, ``cmd_sync_blocked``, ``cmd_reconcile_cascade``)
# route through this helper; read-only sites (the status / report renderers)
# do not, so classification on every render does not produce duplicate pings.

NOTIFICATION_STATE_FILENAME: str = "notification-state.json"

# ``BlockedTaskState`` enum member name -> Slack event toggle.  Each
# blocked classification gets its own per-event toggle so operators can
# opt in by bucket (issue #209).  Re-using the enum's ``.name`` string
# (e.g. ``"AWAITING_DEPENDENCY"``) keeps the mapping single-source-of-truth
# with the classifier in ``backlog/proposal.py``.
_EVENT_BY_CLASSIFICATION: dict[str, str] = {
    "RUNTIME_DEGRADATION": EVENT_WORK_UNIT_BLOCKED_RUNTIME_DEGRADATION,
    "HELD": EVENT_WORK_UNIT_BLOCKED_HELD,
    "BLOCKED_ON_HELD": EVENT_WORK_UNIT_BLOCKED_ON_HELD,
    "AUTO_CLEARING_VIA_PROPOSAL": EVENT_WORK_UNIT_BLOCKED_AUTO_CLEARING,
    "AWAITING_DEPENDENCY": EVENT_WORK_UNIT_BLOCKED_AWAITING_DEPENDENCY,
    "AWAITING_AMENDMENT_RECOVERY": EVENT_WORK_UNIT_BLOCKED_AMENDMENT_RECOVERY,
    "OPERATOR_ACTION_REQUIRED": EVENT_WORK_UNIT_BLOCKED_OPERATOR,
}


def _resolve_backlog_label() -> str:
    """Return a short label identifying the active workspace / backlog.

    Used by every Slack payload so operators monitoring multiple workspaces
    can tell at a glance which backlog a ping came from (operator request,
    2026-05-19). Reads ``DEVBENCH_WORKSPACE_ROOT`` and returns its basename;
    falls back to ``"unknown"`` if the env var is absent (e.g. during a
    ``devbench notify-test`` smoke check that doesn't bootstrap workspace
    config).  Best-effort: any exception during lookup returns
    ``"unknown"`` so a label-resolution bug cannot suppress a notification.
    """
    try:
        from devbench.config import WORKSPACE_ROOT

        if WORKSPACE_ROOT is not None:
            label = WORKSPACE_ROOT.name
            if label:
                return label
    except (ImportError, RuntimeError, AttributeError):
        pass
    return "unknown"


# ---------------------------------------------------------------------------
# URL masking for log lines (sensitive data)
# ---------------------------------------------------------------------------


def _mask_url(url: str) -> str:
    """Return the trailing 8 characters of *url* with a leading ``...``.

    Webhook URLs are credentials.  CLAUDE.md "Sensitive Data Handling"
    requires masking them in log lines.  Returned form is short enough
    to fit one stderr column but long enough to distinguish two
    webhooks that share a common prefix.
    """
    if not url:
        return "***"
    if len(url) <= 8:
        return "***"
    return f"...{url[-8:]}"


# ---------------------------------------------------------------------------
# Config lookup (lazy, never raises)
# ---------------------------------------------------------------------------


def _load_notifications_config() -> Any:
    """Return the cached ``NotificationsConfig`` or ``None`` on failure.

    Lazy import of ``devbench.config`` so this module stays importable
    in contexts where config-loading hasn't yet succeeded (early CLI
    bootstrap, ``--help`` paths, test fixtures that monkeypatch
    ``RUNTIME_CONFIG`` themselves).  Returns ``None`` when the lookup
    fails -- callers treat that as "notifications disabled" and emit
    no HTTP requests.
    """
    try:
        from devbench.config import RUNTIME_CONFIG

        return RUNTIME_CONFIG.notifications
    except (ImportError, RuntimeError, AttributeError):
        return None


def is_event_enabled(event_kind: str) -> bool:
    """Return ``True`` iff *event_kind* is enabled in the runtime config.

    Centralised check so every dispatcher uses the same gate.  Returns
    ``False`` when the ``notifications:`` block is absent, when the
    master switch is off, or when the specific event toggle is false.
    """
    cfg = _load_notifications_config()
    if cfg is None or not cfg.enabled:
        return False
    return bool(getattr(cfg.events, event_kind, False))


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def _build_slack_payload(
    summary: str,
    fields: list[tuple[str, str]],
    context: str | None,
) -> dict[str, Any]:
    """Build a Slack block-kit payload.

    Every payload is prefixed with the literal ``<!here>`` mention so the
    message triggers a desktop + mobile push for every online member of
    the channel the webhook posts to.  In a one-person private channel
    (the recommended DM-yourself pattern) that's just the operator; in
    a shared channel the whole online team gets pinged.  Single payload,
    both routings.

    Every payload also carries a ``Backlog`` field as the first row of
    the fields block so operators monitoring multiple workspaces can
    tell at a glance which backlog a ping came from (operator request,
    2026-05-19).  The label is resolved from
    :func:`_resolve_backlog_label`.

    Args:
        summary: One-line headline used for both the ``text`` top-line
            (required by the Slack incoming-webhook contract for
            mobile preview rendering) and the first block's bold
            header.
        fields: Two-column ``(name, value)`` pairs rendered as a
            section block with up to ten markdown fields.  The
            ``Backlog`` row is prepended automatically.
        context: Optional muted footer line (block-kit ``context``
            element).  ``None`` skips the footer block.
    """
    prefix = f"{SLACK_HERE_MENTION} "
    text_line = f"{prefix}{summary}"
    enriched_fields: list[tuple[str, str]] = [("Backlog", _resolve_backlog_label())] + list(fields)
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{prefix}{summary}*"},
        },
        {
            "type": "section",
            "fields": [{"type": "mrkdwn", "text": f"*{name}*\n{value}"} for name, value in enriched_fields],
        },
    ]
    if context:
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": context}],
            }
        )
    return {"text": text_line, "blocks": blocks}


# ---------------------------------------------------------------------------
# Dispatch core (best-effort)
# ---------------------------------------------------------------------------


def _dispatch(
    event_kind: str,
    slack_summary: str,
    slack_fields: list[tuple[str, str]],
    slack_context: str | None,
) -> None:
    """POST the payload to every enabled endpoint; never raise.

    Walks the per-endpoint sub-blocks under ``notifications:`` and
    fires a transport-appropriate POST for each one whose
    ``enabled: true`` AND ``webhook_url`` is non-null.  Today the only
    endpoint is Slack; future endpoints (Discord, Teams, generic raw
    JSON) plug in as additional branches without touching event
    callers.

    Outer try / except guards against catastrophic failures
    (config-read errors, payload-build bugs) so a notification bug
    cannot crash the orchestrator.  Inner per-endpoint try / except
    keeps one endpoint's failure from blocking the others.
    """
    if not is_event_enabled(event_kind):
        return
    try:
        from devbench.quota import post_webhook  # lazy import: quota.py imports config which imports this

        cfg = _load_notifications_config()
        if cfg is None:
            return
        timeout = cfg.timeout_seconds
        slack_url = cfg.slack.webhook_url if (cfg.slack is not None and cfg.slack.enabled) else None

        if slack_url:
            slack_payload = _build_slack_payload(slack_summary, slack_fields, slack_context)
            try:
                post_webhook(slack_url, slack_payload, timeout)
            except Exception as exc:
                print(
                    f"[WARN] notifications: slack POST to {_mask_url(slack_url)} failed: {exc!r}",
                    file=sys.stderr,
                )
    except Exception as exc:
        print(
            f"[WARN] notifications: dispatch failed for {event_kind!r}: {exc!r}",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Per-event dispatchers
# ---------------------------------------------------------------------------


def notify_work_unit_done(unit_id: str, title: str) -> None:
    """A work unit transitioned to ``done``."""
    _dispatch(
        EVENT_WORK_UNIT_DONE,
        slack_summary=f":white_check_mark: Work unit done: {unit_id}",
        slack_fields=[("Task", f"`{unit_id}`"), ("Title", title)],
        slack_context=None,
    )


def notify_work_unit_blocked_operator(unit_id: str, title: str, reason: str) -> None:
    """A work unit was classified ``OPERATOR_ACTION_REQUIRED``."""
    _dispatch(
        EVENT_WORK_UNIT_BLOCKED_OPERATOR,
        slack_summary=f":no_entry: Operator action required: {unit_id}",
        slack_fields=[("Task", f"`{unit_id}`"), ("Title", title)],
        slack_context=f"Reason: {reason}",
    )


def notify_work_unit_blocked_runtime_degradation(unit_id: str, title: str, reason: str) -> None:
    """A work unit was classified ``RUNTIME_DEGRADATION`` (#183 -- SDK agent-tool loss)."""
    _dispatch(
        EVENT_WORK_UNIT_BLOCKED_RUNTIME_DEGRADATION,
        slack_summary=f":rotating_light: Runtime degradation: {unit_id}",
        slack_fields=[("Task", f"`{unit_id}`"), ("Title", title)],
        slack_context=f"Reason: {reason}",
    )


def notify_work_unit_blocked_held(unit_id: str, title: str, reason: str) -> None:
    """A work unit was classified ``HELD`` (status is ``hold``)."""
    _dispatch(
        EVENT_WORK_UNIT_BLOCKED_HELD,
        slack_summary=f":pause_button: On hold: {unit_id}",
        slack_fields=[("Task", f"`{unit_id}`"), ("Title", title)],
        slack_context=f"Reason: {reason}",
    )


def notify_work_unit_blocked_on_held(unit_id: str, title: str, reason: str) -> None:
    """A work unit was classified ``BLOCKED_ON_HELD`` (marker target is ``hold``)."""
    _dispatch(
        EVENT_WORK_UNIT_BLOCKED_ON_HELD,
        slack_summary=f":pause_button: Blocked on held dependency: {unit_id}",
        slack_fields=[("Task", f"`{unit_id}`"), ("Title", title)],
        slack_context=f"Reason: {reason}",
    )


def notify_work_unit_blocked_auto_clearing(unit_id: str, title: str, reason: str) -> None:
    """A work unit was classified ``AUTO_CLEARING_VIA_PROPOSAL`` (ADR-07 cascade in flight)."""
    _dispatch(
        EVENT_WORK_UNIT_BLOCKED_AUTO_CLEARING,
        slack_summary=f":hourglass_flowing_sand: Auto-clearing via proposal: {unit_id}",
        slack_fields=[("Task", f"`{unit_id}`"), ("Title", title)],
        slack_context=f"Reason: {reason}",
    )


def notify_work_unit_blocked_awaiting_dependency(unit_id: str, title: str, reason: str) -> None:
    """A work unit was classified ``AWAITING_DEPENDENCY`` (regular dep still in flight)."""
    _dispatch(
        EVENT_WORK_UNIT_BLOCKED_AWAITING_DEPENDENCY,
        slack_summary=f":hourglass: Awaiting dependency: {unit_id}",
        slack_fields=[("Task", f"`{unit_id}`"), ("Title", title)],
        slack_context=f"Reason: {reason}",
    )


def notify_work_unit_blocked_amendment_recovery(unit_id: str, title: str, reason: str) -> None:
    """A work unit was classified ``AWAITING_AMENDMENT_RECOVERY`` (recovery signal present)."""
    _dispatch(
        EVENT_WORK_UNIT_BLOCKED_AMENDMENT_RECOVERY,
        slack_summary=f":hammer_and_wrench: Awaiting amendment recovery: {unit_id}",
        slack_fields=[("Task", f"`{unit_id}`"), ("Title", title)],
        slack_context=f"Reason: {reason}",
    )


# Map ``BlockedTaskState`` enum-member name -> per-class notify_* function NAME.
# Stored as a string and resolved through ``globals()`` at call time so test
# ``patch("devbench.notifications.notify_*")`` patches the same module
# attribute the dispatcher actually invokes; a direct function-object map
# would capture the pre-patch object and bypass the mock.
_NOTIFY_FN_NAME_BY_CLASSIFICATION: dict[str, str] = {
    "RUNTIME_DEGRADATION": "notify_work_unit_blocked_runtime_degradation",
    "HELD": "notify_work_unit_blocked_held",
    "BLOCKED_ON_HELD": "notify_work_unit_blocked_on_held",
    "AUTO_CLEARING_VIA_PROPOSAL": "notify_work_unit_blocked_auto_clearing",
    "AWAITING_DEPENDENCY": "notify_work_unit_blocked_awaiting_dependency",
    "AWAITING_AMENDMENT_RECOVERY": "notify_work_unit_blocked_amendment_recovery",
    "OPERATOR_ACTION_REQUIRED": "notify_work_unit_blocked_operator",
}


def _load_notification_state(state_path: Path) -> dict[str, str]:
    """Read the per-workspace classification cache.

    Treats missing / corrupt / non-object payloads as empty cache (regenerated
    on next write).  The cache is best-effort observability state, never a
    correctness gate -- swallowing decode errors here cannot mis-fire a
    notification, only delay one by at most one classification round.
    """
    if not state_path.is_file():
        return {}
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    # Coerce to str/str: defensively reject non-string keys / values rather
    # than carrying mismatched shapes forward.
    return {str(k): str(v) for k, v in payload.items() if isinstance(k, str)}


def _save_notification_state(state_path: Path, state: dict[str, str]) -> None:
    """Atomic-write the classification cache.

    Caught at the call site: a failure here logs a `[WARN]` and skips,
    matching the dispatcher's best-effort contract -- the orchestrator
    must never crash because notification state could not be persisted.
    """
    state_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(state_path, json.dumps(state, sort_keys=True, indent=2))


def notify_blocked_classification_transition(
    unit_id: str,
    title: str,
    reason: str,
    classification: str,
    workspace_root: Path,
) -> None:
    """Fire the per-class ``notify_work_unit_blocked_<class>`` helper on
    transition into any of the seven blocked classifications (issue #209;
    generalisation of the original operator-only transition path from #207).

    Compares *classification* against the last value observed for *unit_id*
    in the per-workspace cache at
    ``<workspace_root>/.devbench/notification-state.json``.  Cache is the
    sole source of truth for "what was the previous class"; the per-event
    toggle in ``devbench.yaml`` only gates whether a ping fires once a
    transition is detected.  The cache is ALWAYS updated on every call
    regardless of toggle state, so flipping a toggle on later does not
    fire pings for state that was already cached.

    Fire semantics: a ping fires when ``previous != classification`` (a
    real transition) AND the matching per-class toggle is enabled.
    Initial entry (``previous is None``) counts as a transition.  Repeated
    observations of the same class are no-ops.

    Call this from write sites only -- ``mark_blocked``,
    ``cmd_sync_blocked``, ``cmd_reconcile_cascade`` -- never from
    read-only renderers, which classify on every refresh and would
    otherwise spam pings.

    Unknown classifications (not in ``_EVENT_BY_CLASSIFICATION``) are
    treated as "do nothing" -- no cache write, no ping -- so a new bucket
    added to the classifier without a corresponding event mapping fails
    safe.

    Args:
        unit_id: The blocked task id (e.g. ``E10-F2-S1-T3``).
        title: Work-unit title for the Slack payload.
        reason: Human-readable reason; surfaced in the Slack context block.
        classification: The current ``BlockedTaskState`` name returned by
            :func:`classify_blocked_task` (e.g. ``"OPERATOR_ACTION_REQUIRED"``,
            ``"AWAITING_DEPENDENCY"``, ``"AUTO_CLEARING_VIA_PROPOSAL"``).
        workspace_root: The workspace root -- usually
            ``Path(DEVBENCH_WORKSPACE_ROOT)`` -- under which the cache file
            is located.
    """
    event_kind = _EVENT_BY_CLASSIFICATION.get(classification)
    if event_kind is None:
        return

    state_path = workspace_root / ".devbench" / NOTIFICATION_STATE_FILENAME
    state = _load_notification_state(state_path)
    previous = state.get(unit_id)
    transitioned = previous != classification

    state[unit_id] = classification
    try:
        _save_notification_state(state_path, state)
    except OSError as exc:
        print(
            f"[WARN] notification state cache write failed at {state_path}: {exc}",
            file=sys.stderr,
        )

    if transitioned and is_event_enabled(event_kind):
        # Resolve via globals so test-time patches of ``notify_work_unit_blocked_*``
        # in this module are honoured (see _NOTIFY_FN_NAME_BY_CLASSIFICATION).
        notify_fn = globals()[_NOTIFY_FN_NAME_BY_CLASSIFICATION[classification]]
        notify_fn(unit_id, title, reason)


def prune_notification_state_for_unblocked(workspace_root: Path, blocked_unit_ids: set[str]) -> None:
    """Drop cache entries for tasks no longer in the ``blocked`` status.

    Called by ``cmd_sync_blocked`` / ``cmd_reconcile_cascade`` after a
    sweep so a task that exits ``blocked`` and later re-enters it (same
    or different class) fires a fresh ping rather than being silently
    suppressed by a stale cache entry (issue #209).  Best-effort: an I/O
    failure logs ``[WARN]`` and returns; the orchestrator never crashes
    because cache pruning failed.

    Args:
        workspace_root: The workspace root containing ``.devbench/``.
        blocked_unit_ids: Set of unit IDs that ARE currently in blocked
            status.  Any other unit ID in the cache is pruned.
    """
    state_path = workspace_root / ".devbench" / NOTIFICATION_STATE_FILENAME
    state = _load_notification_state(state_path)
    pruned = {unit_id: cls for unit_id, cls in state.items() if unit_id in blocked_unit_ids}
    if pruned == state:
        return
    try:
        _save_notification_state(state_path, pruned)
    except OSError as exc:
        print(
            f"[WARN] notification state cache prune failed at {state_path}: {exc}",
            file=sys.stderr,
        )


def notify_work_unit_materialised(unit_id: str, title: str, source_task_id: str) -> None:
    """A draft work-unit file was written from a proposal."""
    _dispatch(
        EVENT_WORK_UNIT_MATERIALISED,
        slack_summary=f":new: Work unit materialised: {unit_id}",
        slack_fields=[
            ("Task", f"`{unit_id}`"),
            ("Title", title),
            ("From source", f"`{source_task_id}`"),
        ],
        slack_context=None,
    )


def notify_work_unit_promoted(unit_id: str, title: str) -> None:
    """A draft work unit was promoted to ``in-queue``."""
    _dispatch(
        EVENT_WORK_UNIT_PROMOTED,
        slack_summary=f":rocket: Work unit promoted: {unit_id}",
        slack_fields=[("Task", f"`{unit_id}`"), ("Title", title)],
        slack_context=None,
    )


def notify_pr_opened(unit_id: str, repo: str, pr_url: str) -> None:
    """A pull request was opened for a completed work unit."""
    _dispatch(
        EVENT_PR_OPENED,
        slack_summary=f":git: PR opened: {pr_url}",
        slack_fields=[
            ("Task", f"`{unit_id}`"),
            ("Repo", f"`{repo}`"),
        ],
        slack_context=None,
    )


def notify_pr_merged(unit_id: str, repo: str, pr_url: str) -> None:
    """A pull request was merged."""
    _dispatch(
        EVENT_PR_MERGED,
        slack_summary=f":tada: PR merged: {pr_url}",
        slack_fields=[
            ("Task", f"`{unit_id}`"),
            ("Repo", f"`{repo}`"),
        ],
        slack_context=None,
    )


def notify_ci_failure(unit_id: str, repo: str, pr_url: str, attempt: int) -> None:
    """A CI run on a work-unit PR failed."""
    _dispatch(
        EVENT_CI_FAILURE,
        slack_summary=f":x: CI failed (attempt {attempt}): {unit_id}",
        slack_fields=[
            ("Task", f"`{unit_id}`"),
            ("Repo", f"`{repo}`"),
            ("PR", pr_url),
            ("Attempt", str(attempt)),
        ],
        slack_context=None,
    )


def notify_ci_pass(unit_id: str, repo: str, pr_url: str) -> None:
    """CI on the finalize-path batch PR turned GREEN; the PR is ready for
    operator merge (issue #219).  Sibling to :func:`notify_ci_failure`.

    Under ``git_ops.auto_merge: false`` the operator merges the squashed
    PR manually.  This ping is the explicit ready-to-merge signal so the
    operator does not have to poll GitHub.  Gated by
    ``notifications.events.ci_pass``.  Payload shape mirrors
    ``notify_pr_opened`` so the operator sees the same fields they
    expect from other PR-lifecycle pings.
    """
    _dispatch(
        EVENT_CI_PASS,
        slack_summary=f":white_check_mark: CI passed -- PR ready for manual merge: {pr_url}",
        slack_fields=[
            ("Task", f"`{unit_id}`"),
            ("Repo", f"`{repo}`"),
            ("PR", pr_url),
        ],
        slack_context=None,
    )


def notify_orchestrator_stop(reason: str, in_flight_unit_id: str | None) -> None:
    """The orchestrator loop is exiting (clean, drain, or SIGTERM)."""
    fields: list[tuple[str, str]] = [("Reason", reason)]
    if in_flight_unit_id:
        fields.append(("In-flight", f"`{in_flight_unit_id}`"))
    _dispatch(
        EVENT_ORCHESTRATOR_STOP,
        slack_summary=f":octagonal_sign: Orchestrator stopped: {reason}",
        slack_fields=fields,
        slack_context=None,
    )


def notify_orchestrator_auto_restart(blocked_task_ids: list[str]) -> None:
    """The orchestrator hit exit-42 (RUNTIME_DEGRADATION only) and the Makefile loop is restarting."""
    preview = ", ".join(blocked_task_ids[:5]) or "(none)"
    if len(blocked_task_ids) > 5:
        preview += f", +{len(blocked_task_ids) - 5} more"
    _dispatch(
        EVENT_ORCHESTRATOR_AUTO_RESTART,
        slack_summary=":arrows_counterclockwise: Orchestrator auto-restarting",
        slack_fields=[
            ("Reason", "RUNTIME_DEGRADATION-only NO_ACTIONABLE"),
            ("Blocked tasks", preview),
        ],
        slack_context=None,
    )


def notify_quota_pause(reason: str, reset_at: datetime, paused_at: datetime) -> None:
    """The orchestrator detected a quota signal and is sleeping until *reset_at*."""
    _dispatch(
        EVENT_QUOTA_PAUSE,
        slack_summary=f":zzz: Quota pause: {reason}",
        slack_fields=[
            ("Reset at", reset_at.isoformat()),
            ("Paused at", paused_at.isoformat()),
        ],
        slack_context=None,
    )


def notify_quota_resume(resumed_at: datetime, waited_seconds: int) -> None:
    """The orchestrator's recovery probe succeeded; the loop is resuming."""
    _dispatch(
        EVENT_QUOTA_RESUME,
        slack_summary=":sunrise: Quota recovered; orchestrator resuming",
        slack_fields=[
            ("Resumed at", resumed_at.isoformat()),
            ("Waited", f"{waited_seconds}s"),
        ],
        slack_context=None,
    )


# ---------------------------------------------------------------------------
# Self-test driver (used by ``devbench notify-test``)
# ---------------------------------------------------------------------------


def send_test_notification(event_kind: str) -> None:
    """Fire one sample notification for *event_kind*.

    Used by the ``devbench notify-test`` CLI subcommand; ignores the
    per-event toggle so the operator can validate any event regardless
    of yaml state, but still respects ``notifications.enabled`` (the
    master switch) so a fully-disabled config produces no traffic.

    Raises:
        ValueError: When *event_kind* is not one of :data:`ALL_EVENTS`.
    """
    if event_kind not in ALL_EVENTS:
        raise ValueError(f"unknown event {event_kind!r}; expected one of {sorted(ALL_EVENTS)}")
    cfg = _load_notifications_config()
    if cfg is None or not cfg.enabled:
        print(
            "[INFO] notifications.enabled is false; nothing to send. Set notifications.enabled: true in devbench.yaml.",
            file=sys.stderr,
        )
        return

    # Temporarily force the toggle on for the named event so the
    # dispatcher fires regardless of yaml state.  We do this by
    # wrapping the dispatch call in an attribute-patch on the cached
    # events object; restored in the finally block.
    original = getattr(cfg.events, event_kind)
    try:
        setattr(cfg.events, event_kind, True)
        _fire_sample(event_kind)
    finally:
        setattr(cfg.events, event_kind, original)


_BLOCKED_CLASS_SAMPLE_DISPATCH = {
    EVENT_WORK_UNIT_BLOCKED_OPERATOR: notify_work_unit_blocked_operator,
    EVENT_WORK_UNIT_BLOCKED_RUNTIME_DEGRADATION: notify_work_unit_blocked_runtime_degradation,
    EVENT_WORK_UNIT_BLOCKED_HELD: notify_work_unit_blocked_held,
    EVENT_WORK_UNIT_BLOCKED_ON_HELD: notify_work_unit_blocked_on_held,
    EVENT_WORK_UNIT_BLOCKED_AUTO_CLEARING: notify_work_unit_blocked_auto_clearing,
    EVENT_WORK_UNIT_BLOCKED_AWAITING_DEPENDENCY: notify_work_unit_blocked_awaiting_dependency,
    EVENT_WORK_UNIT_BLOCKED_AMENDMENT_RECOVERY: notify_work_unit_blocked_amendment_recovery,
}


def _fire_sample(event_kind: str) -> None:
    """Dispatch a canned payload for *event_kind* with placeholder data."""
    now = datetime.now().astimezone()
    # All seven blocked-class events share the (unit_id, title, reason)
    # signature; dispatch via a dict to keep branch count manageable.
    blocked_fn = _BLOCKED_CLASS_SAMPLE_DISPATCH.get(event_kind)
    if blocked_fn is not None:
        blocked_fn("E0-F1-S1-T1", "Sample test task", "manual notify-test invocation")
        return
    # Issue #219: pr_opened / pr_merged / ci_pass all share the
    # (unit_id, repo, pr_url) signature, so dispatch them via a dict
    # alongside the blocked-class dispatch to keep ``_fire_sample``
    # under ruff PLR0912's 12-branch cap as new events are added.
    pr_url = "https://github.com/acme/widget/pull/1"
    pr_3arg_dispatch: dict[str, Callable[[str, str, str], None]] = {
        EVENT_PR_OPENED: notify_pr_opened,
        EVENT_PR_MERGED: notify_pr_merged,
        EVENT_CI_PASS: notify_ci_pass,
    }
    pr_fn = pr_3arg_dispatch.get(event_kind)
    if pr_fn is not None:
        pr_fn("E0-F1-S1-T1", "acme/widget", pr_url)
        return
    if event_kind == EVENT_WORK_UNIT_DONE:
        notify_work_unit_done("E0-F1-S1-T1", "Sample test task")
    elif event_kind == EVENT_WORK_UNIT_MATERIALISED:
        notify_work_unit_materialised("E0-F1-S1-T2", "Sample materialised task", "E0-F1-S1-T1")
    elif event_kind == EVENT_WORK_UNIT_PROMOTED:
        notify_work_unit_promoted("E0-F1-S1-T2", "Sample promoted task")
    elif event_kind == EVENT_CI_FAILURE:
        notify_ci_failure("E0-F1-S1-T1", "acme/widget", pr_url, 2)
    elif event_kind == EVENT_ORCHESTRATOR_STOP:
        notify_orchestrator_stop("notify-test sample", "E0-F1-S1-T1")
    elif event_kind == EVENT_ORCHESTRATOR_AUTO_RESTART:
        notify_orchestrator_auto_restart(["E0-F1-S1-T2", "E0-F1-S1-T3"])
    elif event_kind == EVENT_QUOTA_PAUSE:
        notify_quota_pause("notify-test sample", now, now)
    elif event_kind == EVENT_QUOTA_RESUME:
        notify_quota_resume(now, 60)
    else:
        # ALL_EVENTS guard above keeps us off this branch in practice;
        # the explicit raise is defensive only for future-event additions
        # so a missing elif branch is loud instead of silent.
        raise ValueError(f"_fire_sample missing branch for event {event_kind!r}")
