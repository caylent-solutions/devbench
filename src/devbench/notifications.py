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

import sys
from datetime import datetime
from typing import Any

# ---------------------------------------------------------------------------
# Event-kind tokens.  Every public ``notify_*`` function dispatches under
# one of these strings; the corresponding ``NotificationsEventsConfig`` field
# uses the same name for grep-ability.
# ---------------------------------------------------------------------------

EVENT_WORK_UNIT_DONE = "work_unit_done"
EVENT_WORK_UNIT_BLOCKED_OPERATOR = "work_unit_blocked_operator"
EVENT_WORK_UNIT_MATERIALISED = "work_unit_materialised"
EVENT_WORK_UNIT_PROMOTED = "work_unit_promoted"
EVENT_PR_OPENED = "pr_opened"
EVENT_PR_MERGED = "pr_merged"
EVENT_CI_FAILURE = "ci_failure"
EVENT_ORCHESTRATOR_STOP = "orchestrator_stop"
EVENT_ORCHESTRATOR_AUTO_RESTART = "orchestrator_auto_restart"
EVENT_QUOTA_PAUSE = "quota_pause"
EVENT_QUOTA_RESUME = "quota_resume"

ALL_EVENTS: tuple[str, ...] = (
    EVENT_WORK_UNIT_DONE,
    EVENT_WORK_UNIT_BLOCKED_OPERATOR,
    EVENT_WORK_UNIT_MATERIALISED,
    EVENT_WORK_UNIT_PROMOTED,
    EVENT_PR_OPENED,
    EVENT_PR_MERGED,
    EVENT_CI_FAILURE,
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

    Args:
        summary: One-line headline used for both the ``text`` top-line
            (required by the Slack incoming-webhook contract for
            mobile preview rendering) and the first block's bold
            header.
        fields: Two-column ``(name, value)`` pairs rendered as a
            section block with up to ten markdown fields.
        context: Optional muted footer line (block-kit ``context``
            element).  ``None`` skips the footer block.
    """
    prefix = f"{SLACK_HERE_MENTION} "
    text_line = f"{prefix}{summary}"
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{prefix}{summary}*"},
        }
    ]
    if fields:
        blocks.append(
            {
                "type": "section",
                "fields": [{"type": "mrkdwn", "text": f"*{name}*\n{value}"} for name, value in fields],
            }
        )
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


def _fire_sample(event_kind: str) -> None:
    """Dispatch a canned payload for *event_kind* with placeholder data."""
    now = datetime.now().astimezone()
    if event_kind == EVENT_WORK_UNIT_DONE:
        notify_work_unit_done("E0-F1-S1-T1", "Sample test task")
    elif event_kind == EVENT_WORK_UNIT_BLOCKED_OPERATOR:
        notify_work_unit_blocked_operator("E0-F1-S1-T1", "Sample test task", "manual notify-test invocation")
    elif event_kind == EVENT_WORK_UNIT_MATERIALISED:
        notify_work_unit_materialised("E0-F1-S1-T2", "Sample materialised task", "E0-F1-S1-T1")
    elif event_kind == EVENT_WORK_UNIT_PROMOTED:
        notify_work_unit_promoted("E0-F1-S1-T2", "Sample promoted task")
    elif event_kind == EVENT_PR_OPENED:
        notify_pr_opened("E0-F1-S1-T1", "acme/widget", "https://github.com/acme/widget/pull/1")
    elif event_kind == EVENT_PR_MERGED:
        notify_pr_merged("E0-F1-S1-T1", "acme/widget", "https://github.com/acme/widget/pull/1")
    elif event_kind == EVENT_CI_FAILURE:
        notify_ci_failure("E0-F1-S1-T1", "acme/widget", "https://github.com/acme/widget/pull/1", 2)
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
