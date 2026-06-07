"""Auto-resolve engine for non-destructive whitelisted remediations.

Issue #263, spec Section 4 E11-F1-S1 and E11-F1-S2.

This module exposes the engine entry point ``apply_auto_resolve`` and a
reusable whitelist membership predicate ``is_whitelisted``.

Behaviour:
- When ``config.enabled`` is ``False``, the engine returns the
  ``advise_only_payload`` byte-for-byte unchanged (AC-3).
- When a destructive verb is supplied, a ``ValueError`` is raised
  immediately -- the guard fires before the enabled check so the
  invariant holds regardless of configuration (AC-2, error-handling
  contract from the work-unit spec).
- When ``config.enabled`` is ``True`` and the remediation passes the
  whitelist guard, the engine enforces a per-(task_id, signature)
  budget from ``config.max_attempts`` (E11-F1-S2 AC-1):
  - If the budget is not yet exhausted, the engine logs the verbatim
    ``[AUTO_RESOLVED]`` audit string and increments the counter.
  - If the budget is exhausted, the engine logs ``[AUTO_RESOLVE_ESCALATED]``
    and returns advise-only without incrementing further (AC-2).
- When the caller supplies a composite classification (primary_blocker_state
  is RUNTIME_DEGRADATION AND structural_blocker_state is not None), the
  engine short-circuits to advise-only without consuming budget (AC-3).
"""

from __future__ import annotations

import sys

from devbench.backlog.proposal import BlockedTaskState
from devbench.config_loader import AutoResolveConfig
from devbench.constants import (
    AUTO_RESOLVE_AUDIT_STRING,
    AUTO_RESOLVE_DESTRUCTIVE_VERBS,
    AUTO_RESOLVE_ESCALATED_STRING,
    AUTO_RESOLVE_WHITELIST,
)

__all__ = [
    "AUTO_RESOLVE_ESCALATED_STRING",
    "AutoResolveConfig",
    "apply_auto_resolve",
    "is_whitelisted",
]

# Module-level per-(task_id, signature) apply-attempt counter.
# Keys are (task_id, signature) tuples; values are cumulative apply counts.
_apply_counts: dict[tuple[str, str], int] = {}


def is_whitelisted(remediation: str) -> bool:
    """Return ``True`` when *remediation* is in the non-destructive whitelist.

    This is the single authoritative predicate for whitelist membership.
    E11-F1-S2 and E11-F2 import and reuse this function rather than
    duplicating the check inline (DRY principle).

    A remediation that appears in ``AUTO_RESOLVE_DESTRUCTIVE_VERBS`` is
    never whitelisted -- the two sets are guaranteed disjoint by the
    constant definitions in ``constants.py``.

    Args:
        remediation: The remediation verb to classify (e.g. ``"re-queue"``).

    Returns:
        ``True`` iff *remediation* is in ``AUTO_RESOLVE_WHITELIST``.
    """
    return remediation in AUTO_RESOLVE_WHITELIST


def _guard_destructive(remediation: str) -> None:
    """Raise ``ValueError`` when *remediation* is a destructive verb.

    This guard is called before the enabled-gate so the invariant
    holds regardless of configuration: destructive verbs are NEVER
    auto-applied, even when ``auto_resolve.enabled`` is ``True``.

    Args:
        remediation: The remediation verb to check.

    Raises:
        ValueError: When *remediation* is in ``AUTO_RESOLVE_DESTRUCTIVE_VERBS``.
    """
    if remediation in AUTO_RESOLVE_DESTRUCTIVE_VERBS:
        allowed = ", ".join(sorted(AUTO_RESOLVE_WHITELIST))
        raise ValueError(
            f"ERROR: auto-resolve cannot apply destructive verb {remediation!r}. "
            f"Destructive verbs ({', '.join(sorted(AUTO_RESOLVE_DESTRUCTIVE_VERBS))}) "
            f"are hard-excluded and can never be auto-applied. "
            f"Non-destructive whitelist: {allowed}."
        )


def _is_composite_block(
    primary_blocker_state: BlockedTaskState | None,
    structural_blocker_state: BlockedTaskState | None,
) -> bool:
    """Return ``True`` when the classification is a composite RUNTIME_DEGRADATION block.

    A composite block occurs when the primary classification is RUNTIME_DEGRADATION
    AND a co-existing structural blocker is also present. In this case a simple
    restart will not clear the structural issue, so auto-apply is unsafe.

    Args:
        primary_blocker_state: The primary blocked-task classification, or ``None``
            when no classification was supplied.
        structural_blocker_state: The underlying structural bucket discovered by
            ``classify_blocked_task_excluding_degradation``, or ``None`` when
            either no classification was supplied or no structural blocker exists.

    Returns:
        ``True`` iff auto-apply should be suppressed due to composite block.
    """
    return primary_blocker_state is BlockedTaskState.RUNTIME_DEGRADATION and structural_blocker_state is not None


def _budget_exhausted(task_id: str, signature: str, max_attempts: int) -> bool:
    """Return ``True`` when the per-(task_id, signature) budget is exhausted.

    Args:
        task_id: The task identifier.
        signature: The blocker-signature hash or label.
        max_attempts: Maximum allowed apply attempts from ``AutoResolveConfig``.

    Returns:
        ``True`` iff the current count is at or above ``max_attempts``.
    """
    key = (task_id, signature)
    return _apply_counts.get(key, 0) >= max_attempts


def _increment_budget(task_id: str, signature: str) -> int:
    """Increment and return the new per-(task_id, signature) apply count.

    Args:
        task_id: The task identifier.
        signature: The blocker-signature hash or label.

    Returns:
        The new apply count after incrementing.
    """
    key = (task_id, signature)
    _apply_counts[key] = _apply_counts.get(key, 0) + 1
    return _apply_counts[key]


def apply_auto_resolve(
    *,
    task_id: str,
    signature: str,
    remediation: str,
    advise_only_payload: str,
    config: AutoResolveConfig,
    primary_blocker_state: BlockedTaskState | None = None,
    structural_blocker_state: BlockedTaskState | None = None,
) -> str:
    """Apply auto-resolve logic and return the (possibly unchanged) output.

    The function is the single engine entry point. Callers supply the
    advise-only payload produced by the blocker-resolver; the function
    either returns it unchanged (disabled, budget-exhausted, or composite-
    block path) or logs ``[AUTO_RESOLVED]`` and returns the same payload
    (the payload is advisory text, not a command that needs re-writing --
    the actual remediation dispatch is the caller's responsibility).

    Decision order (first match wins):

    1. Destructive-verb guard (``ValueError`` unconditionally).
    2. Disabled gate: return advise-only unchanged.
    3. Composite-block gate: when primary_blocker_state is
       RUNTIME_DEGRADATION AND structural_blocker_state is not None,
       return advise-only without consuming budget.
    4. Whitelist gate: unknown non-destructive verb logs a warning and
       returns advise-only.
    5. Budget gate: if per-(task_id, signature) count is at max_attempts,
       log ``[AUTO_RESOLVE_ESCALATED]`` and return advise-only.
    6. Apply path: increment counter, log ``[AUTO_RESOLVED]``, return payload.

    Args:
        task_id: Canonical task identifier (e.g. ``"E11-F1-S1-T1"``).
        signature: Blocker-signature hash or label for audit traceability.
        remediation: The remediation verb to apply (e.g. ``"re-queue"``).
        advise_only_payload: The current advise-only output from the
            blocker-resolver, returned unchanged on all non-apply paths.
        config: Auto-resolve configuration. When ``config.enabled`` is
            ``False``, the function returns *advise_only_payload* unchanged.
        primary_blocker_state: Optional primary blocked-task classification
            from ``classify_blocked_task``. Used only for composite-block
            detection (E11-F1-S2 AC-3).
        structural_blocker_state: Optional underlying structural bucket from
            ``classify_blocked_task_excluding_degradation``. Used only when
            ``primary_blocker_state`` is ``RUNTIME_DEGRADATION`` to detect
            composite blocks.

    Returns:
        The advise-only payload, unchanged in all cases (the caller is
        responsible for dispatching the actual remediation action).

    Raises:
        ValueError: When *remediation* is in ``AUTO_RESOLVE_DESTRUCTIVE_VERBS``,
            regardless of ``config.enabled``.
    """
    # Hard guard: destructive verbs are rejected unconditionally.
    _guard_destructive(remediation)

    # Disabled path: return advise-only output byte-for-byte unchanged (AC-3).
    if not config.enabled:
        return advise_only_payload

    # Composite-block gate: RUNTIME_DEGRADATION + structural blocker means a
    # restart will not clear the co-existing structural issue. Auto-apply is
    # unsafe; route to advise without consuming budget (E11-F1-S2 AC-3).
    if _is_composite_block(primary_blocker_state, structural_blocker_state):
        return advise_only_payload

    # Whitelist gate: unknown non-destructive verb stays advisory.
    if not is_whitelisted(remediation):
        print(
            f"WARNING: auto-resolve: remediation {remediation!r} is not in the whitelist; "
            "returning advise-only payload unchanged.",
            file=sys.stderr,
        )
        return advise_only_payload

    # Budget gate: if the per-(task_id, signature) count is at max_attempts,
    # log escalation and return advise-only (E11-F1-S2 AC-1, AC-2).
    if _budget_exhausted(task_id, signature, config.max_attempts):
        attempt_count = _apply_counts.get((task_id, signature), 0)
        print(
            f"{AUTO_RESOLVE_ESCALATED_STRING} task_id={task_id} signature={signature} attempts={attempt_count}",
            file=sys.stderr,
        )
        return advise_only_payload

    # Apply path: increment counter and emit audit log (AC-4 from E11-F1-S1).
    _increment_budget(task_id, signature)
    print(
        f"{AUTO_RESOLVE_AUDIT_STRING} task_id={task_id} signature={signature} remediation={remediation}",
        file=sys.stderr,
    )
    return advise_only_payload
