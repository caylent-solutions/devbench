"""Drain signal helpers for devbench.

Provides the DRAIN_SIGNAL_NAME constant, DrainState dataclass, and four
helper functions to write, cancel, read, and consume the drain signal
file that the orchestrator uses to coordinate graceful shutdown.

The write path (request_drain) uses a tmp-then-rename pattern so readers
never observe a partial file. consume_drain is not POSIX-atomic (read then
unlink).

When ``DEVBENCH_SESSION_NAME`` is set, all public helpers use the per-session
drain signal path ``<workspace>/.devbench/sessions/<name>/drain.signal``
instead of the workspace-root path (spec 4.4.4, AC-192-7).  Per-session
paths are always constructed relative to the ``workspace`` argument passed to
each public helper -- no additional environment variable is required.

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

from devbench.constants import SESSION_DRAIN_SIGNAL_FILENAME, SESSION_SESSIONS_BASE_DIR

DRAIN_SIGNAL_NAME: str = ".devbench/drain.signal"


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

        requested_at = requested_at.replace(tzinfo=UTC) if requested_at.tzinfo is None else requested_at.astimezone(UTC)

        return cls(
            requested_at=requested_at,
            requested_by=data["requested_by"],
            reason=data.get("reason", ""),
        )


def resolve_drain_signal_path(workspace: Path) -> Path:
    """Return the drain signal path, honouring ``DEVBENCH_SESSION_NAME`` when set.

    When ``DEVBENCH_SESSION_NAME`` is set and non-empty, returns the per-session
    path ``<workspace>/.devbench/sessions/<name>/drain.signal`` (spec 4.4.4).
    The ``workspace`` argument is always the workspace root; per-session paths
    are constructed relative to it.

    When ``DEVBENCH_SESSION_NAME`` is absent or empty, returns the canonical
    workspace-root path ``<workspace>/.devbench/drain.signal``.

    Args:
        workspace: Root directory of the devbench workspace.  Both workspace-root
            and per-session paths are constructed relative to this directory.

    Returns:
        Absolute :class:`~pathlib.Path` of the drain signal file to use.
    """
    session_name = os.environ.get("DEVBENCH_SESSION_NAME", "").strip()
    if not session_name:
        return workspace / DRAIN_SIGNAL_NAME
    return workspace / SESSION_SESSIONS_BASE_DIR / session_name / SESSION_DRAIN_SIGNAL_FILENAME


def _signal_path(workspace: Path) -> Path:
    """Return the absolute path of the drain signal file for *workspace*.

    Delegates to :func:`resolve_drain_signal_path` so that per-session
    routing (``DEVBENCH_SESSION_NAME``) is applied consistently.
    """
    return resolve_drain_signal_path(workspace)


def _both_signal_paths(workspace: Path) -> list[Path]:
    """Return the drain signal paths the reader should scan, in priority order.

    Issue #212: an operator running ``devbench drain`` from a shell typically
    has no ``DEVBENCH_SESSION_NAME`` env var set, so :func:`request_drain`
    writes to ``<workspace>/.devbench/drain.signal``.  The orchestrator's
    ``cmd_start``, however, sets ``DEVBENCH_SESSION_NAME = parsed.name``
    (default ``"default"``) before its drain loop runs, so its
    :func:`read_drain_state` / :func:`consume_drain` look at
    ``<workspace>/.devbench/sessions/default/drain.signal`` and never see the
    operator's signal.  This helper returns both candidate paths so the
    reader can fall through to the workspace-root path when the per-session
    path is empty.

    When ``DEVBENCH_SESSION_NAME`` is unset both paths collapse to the same
    workspace-root path, in which case only one path is returned.

    Args:
        workspace: Root directory of the devbench workspace.

    Returns:
        List of one or two absolute :class:`~pathlib.Path` entries.  The
        first entry is the per-session path (when active); the second is
        always the workspace-root path.
    """
    primary = _signal_path(workspace)
    fallback = workspace / DRAIN_SIGNAL_NAME
    if primary == fallback:
        return [primary]
    return [primary, fallback]


def _parse_drain_signal(signal: Path) -> DrainState:
    """Parse a drain signal file's contents into a :class:`DrainState`.

    Extracted from :func:`read_drain_state` so the two-path scan in that
    function can reuse the parse logic.  Does not check for file existence;
    callers must guard with ``signal.exists()`` first.

    Raises:
        ValueError: invalid JSON or non-dict JSON root or bad ``requested_at``.
        KeyError: missing required field.
    """
    raw = signal.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"drain signal file contains invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"drain signal file must contain a JSON object, got {type(data).__name__}")

    return DrainState.from_dict(data)


def _current_user() -> str:
    """Return the current OS user name, or ``"unknown"`` when undetectable."""
    return os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"


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
    """Remove the drain signal file(s) if any exist.

    Issue #212: scans both the per-session path (when ``DEVBENCH_SESSION_NAME``
    is set) AND the workspace-root path so the orchestrator's clean-exit
    cleanup clears a signal regardless of which writer created it.  When no
    session is active the two paths collapse to one.  Idempotent: returns
    ``False`` when no signal exists at any candidate path.

    Args:
        workspace: Root directory of the devbench workspace.

    Returns:
        ``True`` if at least one signal file existed and was removed;
        ``False`` if no signal files were present at any candidate path.

    Raises:
        OSError: The unlink step fails for a reason other than the file being
            absent (e.g. permission denied).
    """
    removed = False
    for signal in _both_signal_paths(workspace):
        if signal.exists():
            signal.unlink()
            removed = True
    return removed


def read_drain_state(workspace: Path) -> DrainState | None:
    """Read and parse the drain signal file without removing it.

    Issue #212: when ``DEVBENCH_SESSION_NAME`` is set, the per-session path
    is checked first; if it has no signal the reader falls through to the
    workspace-root path so an operator-issued ``devbench drain`` (which writes
    the workspace-root path because the CLI inherits no session env) is still
    observed by the session-scoped orchestrator.

    Args:
        workspace: Root directory of the devbench workspace.

    Returns:
        A :class:`DrainState` for the first existing path in priority order;
        ``None`` if no signal exists at any candidate path.

    Raises:
        ValueError: The signal file contains invalid JSON, a non-dict JSON
            root, or an unparseable ``requested_at`` value.
        KeyError: The signal file is missing a required field.
    """
    for signal in _both_signal_paths(workspace):
        if signal.exists():
            return _parse_drain_signal(signal)
    return None


def consume_drain(workspace: Path) -> DrainState | None:
    """Read the drain signal file and remove it (read then unlink; not POSIX-atomic).

    This is the canonical way for the orchestrator to acknowledge a drain
    request. The operation reads the signal, then unlinks the file. If
    parsing fails the file is left in place so the caller can inspect it.

    Issue #212: scans both the per-session path (when active) and the
    workspace-root path so an operator-issued workspace-root drain is observed
    by the session-scoped orchestrator.  Only the path that actually held the
    signal is unlinked.

    If the file disappears between the read and the unlink (a concurrent
    cancel or a second consumer racing this call), the successfully-read
    :class:`DrainState` is still returned rather than raising
    ``FileNotFoundError`` -- the drain signal was consumed by whoever deleted
    it first, so the outcome is identical from the caller's perspective.

    Args:
        workspace: Root directory of the devbench workspace.

    Returns:
        A :class:`DrainState` if any signal file existed at read time;
        ``None`` if no signal file was present at any candidate path.

    Raises:
        ValueError: The signal file contains invalid JSON, a non-dict JSON
            root, or an unparseable ``requested_at`` value.
        KeyError: The signal file is missing a required field.
        OSError: The unlink step fails for a reason other than the file being
            absent (e.g. permission denied).
    """
    for signal in _both_signal_paths(workspace):
        if signal.exists():
            state = _parse_drain_signal(signal)
            with contextlib.suppress(FileNotFoundError):
                signal.unlink()
            return state
    return None
