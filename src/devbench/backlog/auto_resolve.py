"""Auto-resolve engine for non-destructive whitelisted remediations.

Issue #263, spec Section 4 E11-F1-S1.

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
  whitelist guard, the engine logs the verbatim ``[AUTO_RESOLVED]`` audit
  string with task_id, signature, and remediation to stderr (AC-4).
"""

from __future__ import annotations

import sys

from devbench.config_loader import AutoResolveConfig
from devbench.constants import (
    AUTO_RESOLVE_AUDIT_STRING,
    AUTO_RESOLVE_DESTRUCTIVE_VERBS,
    AUTO_RESOLVE_WHITELIST,
)

__all__ = ["AutoResolveConfig", "apply_auto_resolve", "is_whitelisted"]


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


def apply_auto_resolve(
    *,
    task_id: str,
    signature: str,
    remediation: str,
    advise_only_payload: str,
    config: AutoResolveConfig,
) -> str:
    """Apply auto-resolve logic and return the (possibly unchanged) output.

    The function is the single engine entry point. Callers supply the
    advise-only payload produced by the blocker-resolver; the function
    either returns it unchanged (disabled path) or logs ``[AUTO_RESOLVED]``
    and returns the same payload (the payload is advisory text, not a
    command that needs re-writing -- the actual remediation dispatch is the
    caller's responsibility after this function returns successfully).

    Destructive-verb guard fires unconditionally before any enabled check:
    a destructive verb raises ``ValueError`` regardless of ``config.enabled``.

    Args:
        task_id: Canonical task identifier (e.g. ``"E11-F1-S1-T1"``).
        signature: Blocker-signature hash or label for audit traceability.
        remediation: The remediation verb to apply (e.g. ``"re-queue"``).
        advise_only_payload: The current advise-only output from the
            blocker-resolver, returned unchanged on the disabled path.
        config: Auto-resolve configuration. When ``config.enabled`` is
            ``False``, the function returns *advise_only_payload* unchanged.

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

    # Enabled path with whitelisted remediation: log [AUTO_RESOLVED] (AC-4).
    # The whitelist check is intentionally performed after the enabled gate
    # so that an unknown (non-destructive) verb under a disabled config is a
    # silent no-op rather than raising prematurely.
    if not is_whitelisted(remediation):
        # Unknown non-destructive verb: log a warning but do not raise.
        # The engine stays advisory -- caller decides how to handle unknown verbs.
        print(
            f"WARNING: auto-resolve: remediation {remediation!r} is not in the whitelist; "
            "returning advise-only payload unchanged.",
            file=sys.stderr,
        )
        return advise_only_payload

    # Whitelisted remediation under enabled config: emit audit log (AC-4).
    print(
        f"{AUTO_RESOLVE_AUDIT_STRING} task_id={task_id} signature={signature} remediation={remediation}",
        file=sys.stderr,
    )
    return advise_only_payload
