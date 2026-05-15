"""Drain signal helpers for devbench.

Provides the DRAIN_SIGNAL_NAME constant, DrainState dataclass, and four
helper functions to write, cancel, read, and consume the drain signal
file that the orchestrator uses to coordinate graceful shutdown.

The write path (request_drain) uses a tmp-then-rename pattern so readers
never observe a partial file. consume_drain is not POSIX-atomic (read then
unlink).

Raised exceptions are documented on each public function. No function
silently swallows an exception.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Module-level constant
# ---------------------------------------------------------------------------

#: Relative path (from workspace root) of the drain signal file.
#: Spec section 4.3.1.
DRAIN_SIGNAL_NAME: str = ".devbench/drain.signal"


# ---------------------------------------------------------------------------
# DrainState dataclass
# ---------------------------------------------------------------------------


@dataclass
class DrainState:
    """Parsed contents of the drain signal file.

    Attributes:
        requested_at: UTC-aware datetime when drain was requested.
        requested_by: Identity of the requester (USER / USERNAME env var, or
            "unknown" when neither is set).
        reason: Optional free-form reason string; defaults to empty string.
    """

    requested_at: datetime
    requested_by: str
    reason: str = field(default="")

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-serialisable dict.

        Returns:
            dict with string keys ``requested_at`` (ISO 8601), ``requested_by``,
            and ``reason``.
        """
        return {
            "requested_at": self.requested_at.isoformat(),
            "requested_by": self.requested_by,
            "reason": self.reason,
        }

    def __str__(self) -> str:
        """Return a human-readable one-line summary suitable for ``devbench drain --status`` output.

        Returns:
            A string of the form
            ``"drain pending: requested_by=<user> at=<ISO-8601> reason=<reason-or-none>"``.
        """
        reason_part = self.reason if self.reason else "(none)"
        return (
            f"drain pending: requested_by={self.requested_by} at={self.requested_at.isoformat()} reason={reason_part}"
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DrainState:
        """Deserialise from a dict produced by :meth:`to_dict`.

        Args:
            data: Dict containing ``requested_at`` (ISO 8601 string) and
                ``requested_by`` (str). ``reason`` is optional.

        Returns:
            A :class:`DrainState` instance.

        Raises:
            KeyError: ``requested_at`` or ``requested_by`` is absent from *data*.
            ValueError: ``requested_at`` is not a valid ISO 8601 datetime string.
        """
        raw_at: str = data["requested_at"]
        try:
            requested_at = datetime.fromisoformat(raw_at)
        except ValueError:
            raise ValueError(f"requested_at '{raw_at}' is not a valid ISO 8601 datetime") from None

        # Ensure the datetime is timezone-aware and normalised to UTC.
        requested_at = requested_at.replace(tzinfo=UTC) if requested_at.tzinfo is None else requested_at.astimezone(UTC)

        return cls(
            requested_at=requested_at,
            requested_by=data["requested_by"],
            reason=data.get("reason", ""),
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _signal_path(workspace: Path) -> Path:
    """Return the absolute path of the drain signal file for *workspace*."""
    return workspace / DRAIN_SIGNAL_NAME


def _current_user() -> str:
    """Return the current OS user name, or ``"unknown"`` when undetectable."""
    return os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def request_drain(workspace: Path, *, reason: str = "") -> Path:
    """Write the drain signal file atomically.

    Creates the parent directory (``<workspace>/.devbench``) if it does not
    already exist, then writes the serialised :class:`DrainState` to a
    temporary file and renames it to the canonical signal path. The rename
    is atomic on POSIX systems, so readers never observe a partial file.

    If an existing signal file is present it is overwritten.

    Args:
        workspace: Root directory of the devbench workspace.
        reason: Optional free-form reason for requesting the drain.

    Returns:
        Absolute :class:`~pathlib.Path` of the signal file that was written.

    Raises:
        OSError: The write or rename step fails (e.g. disk full, permission
            denied). The temporary file is cleaned up before re-raising.
    """
    signal = _signal_path(workspace)
    signal.parent.mkdir(parents=True, exist_ok=True)

    state = DrainState(
        requested_at=datetime.now(tz=UTC),
        requested_by=_current_user(),
        reason=reason,
    )
    payload = json.dumps(state.to_dict(), indent=2)

    tmp = signal.parent / "drain.tmp"
    try:
        tmp.write_text(payload, encoding="utf-8")
    except Exception:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise

    tmp.rename(signal)
    return signal


def cancel_drain(workspace: Path) -> bool:
    """Remove the drain signal file if it exists.

    Args:
        workspace: Root directory of the devbench workspace.

    Returns:
        ``True`` if the signal file existed and was removed; ``False`` if
        the signal file was not present (idempotent).

    Raises:
        OSError: The unlink step fails for a reason other than the file being
            absent (e.g. permission denied).
    """
    signal = _signal_path(workspace)
    if not signal.exists():
        return False
    signal.unlink()
    return True


def read_drain_state(workspace: Path) -> DrainState | None:
    """Read and parse the drain signal file without removing it.

    Args:
        workspace: Root directory of the devbench workspace.

    Returns:
        A :class:`DrainState` if the signal file exists; ``None`` otherwise.

    Raises:
        ValueError: The signal file contains invalid JSON, a non-dict JSON
            root, or an unparseable ``requested_at`` value.
        KeyError: The signal file is missing a required field.
    """
    signal = _signal_path(workspace)
    if not signal.exists():
        return None

    raw = signal.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"drain signal file contains invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"drain signal file must contain a JSON object, got {type(data).__name__}")

    return DrainState.from_dict(data)


def consume_drain(workspace: Path) -> DrainState | None:
    """Read the drain signal file and remove it (read then unlink; not POSIX-atomic).

    This is the canonical way for the orchestrator to acknowledge a drain
    request. The operation reads the signal, then unlinks the file. If
    parsing fails the file is left in place so the caller can inspect it.

    Args:
        workspace: Root directory of the devbench workspace.

    Returns:
        A :class:`DrainState` if the signal file existed; ``None`` otherwise.

    Raises:
        ValueError: The signal file contains invalid JSON, a non-dict JSON
            root, or an unparseable ``requested_at`` value.
        KeyError: The signal file is missing a required field.
        OSError: The unlink step fails for a reason other than the file being
            absent.
    """
    state = read_drain_state(workspace)
    if state is None:
        return None

    _signal_path(workspace).unlink()
    return state
