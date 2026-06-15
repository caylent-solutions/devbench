"""Interactive ``claude`` CLI orchestrator under a detached ``screen`` daemon.

This module implements the ``devbench supervise`` feature (spec
``devbench-supervise-screen-orchestrator``): it runs the orchestrator as an
interactive ``claude`` session inside a ``screen`` daemon, driven by a
``pexpect`` supervisor, authenticated against the Claude Code subscription so
token consumption draws from the rolling 5-hour windows rather than the
Anthropic API (Section 0.2).

Phase 1 (this commit) lands the persistence substrate only:

- :class:`SuperviseSessionState` -- the per-session state record (Section 5.5).
- :class:`SuperviseRegistry` -- read/write ``.devbench/supervise/registry.json``
  and per-session ``state.json``; PID liveness; stale-reaping. It MIRRORS
  :class:`devbench.session.SessionRegistry`'s file/atomic-write shape but is a
  SEPARATE registry (D-8) so the subscription-billed supervise channel stays
  distinct from the API-billed SDK channel in ``status``/``info``.
- path helpers for the per-session state dir, ``pty.log``, and ``stop.request``.

The pexpect supervisor, env sanitizer, auth verifier, command injector, quota
adapter, and state machine land in later phases per ``IMPLEMENTATION-PLAN.md``.

All path/string literals are sourced from :mod:`devbench.constants`; no strings
are hard-coded in this module.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from devbench.constants import (
    SUPERVISE_BASE_DIR,
    SUPERVISE_BILLING_CHANNEL,
    SUPERVISE_PTY_LOG_FILENAME,
    SUPERVISE_REGISTRY_PATH,
    SUPERVISE_REGISTRY_TMP_SUFFIX,
    SUPERVISE_SCREEN_NAME_PREFIX_DEFAULT,
    SUPERVISE_STATE_FILENAME,
    SUPERVISE_STATE_STARTING,
    SUPERVISE_STOP_REQUEST_FILENAME,
    SUPERVISE_SUPERVISOR_LOG_FILENAME,
)

# ---------------------------------------------------------------------------
# System-dependency preflight (FR-23): `screen` is a system/devcontainer
# dependency, NOT a Python dependency. The supervisor probes for it at launch
# and fails fast with a clear install hint if it is absent.
# ---------------------------------------------------------------------------


class ScreenUnavailableError(Exception):
    """Raised when the ``screen`` system dependency is not on ``PATH`` (FR-23).

    Carries the actionable install hint so the CLI can surface a single,
    clear error and exit non-zero (Section 7.1).
    """


def require_screen() -> str:
    """Return the resolved ``screen`` executable path or fail fast (FR-23).

    ``screen`` is a system/devcontainer dependency, not pip-installable
    (Section 1.8, D-9). When absent, raise :class:`ScreenUnavailableError` with
    a clear install hint (devcontainer / macOS) so the caller exits non-zero
    with an actionable message rather than failing obscurely at launch.

    Returns:
        Absolute path to the ``screen`` executable.

    Raises:
        ScreenUnavailableError: ``screen`` is not on ``PATH``.
    """
    path = shutil.which("screen")
    if path is None:
        raise ScreenUnavailableError(
            "'screen' is not installed. Install it "
            "(devcontainer: 'apt-get install -y screen'; macOS: 'brew install screen') and retry."
        )
    return path


def screen_session_name(name: str, *, prefix: str = SUPERVISE_SCREEN_NAME_PREFIX_DEFAULT) -> str:
    """Return the ``screen`` session name ``<prefix><name>`` (Section 4.1 step 4, FR-6).

    Args:
        name: The supervise session name.
        prefix: The configured ``supervise.screen_name_prefix`` (default
            ``devbench-supervise-``).

    Returns:
        The screen session name (e.g. ``devbench-supervise-nightly``).
    """
    return f"{prefix}{name}"


# ---------------------------------------------------------------------------
# Per-session path helpers
# ---------------------------------------------------------------------------


def _reject_traversal(name: str) -> None:
    """Raise ``ValueError`` when *name* contains a ``..`` path segment.

    Mirrors the session-name traversal guard in ``cli.py`` / ``scope.py`` so a
    crafted ``--name`` cannot escape the ``.devbench/supervise/`` tree.

    Args:
        name: The supervise session name to validate.

    Raises:
        ValueError: *name* contains a ``..`` path segment.
    """
    if ".." in Path(name).parts:
        raise ValueError(
            f"session name contains invalid path segment '..': {name!r}. "
            "Use a simple alphanumeric name without directory traversal."
        )


def supervise_state_dir(workspace_root: Path, name: str) -> Path:
    """Return the per-session state directory ``.devbench/supervise/<name>/``.

    Args:
        workspace_root: Root directory of the devbench workspace.
        name: Supervise session name.

    Returns:
        Absolute path to the session's state directory.

    Raises:
        ValueError: *name* contains a ``..`` path segment.
    """
    _reject_traversal(name)
    return workspace_root / SUPERVISE_BASE_DIR / name


def supervise_state_file_path(workspace_root: Path, name: str) -> Path:
    """Return the per-session ``state.json`` path (Section 5.5)."""
    return supervise_state_dir(workspace_root, name) / SUPERVISE_STATE_FILENAME


def supervise_pty_log_path(workspace_root: Path, name: str) -> Path:
    """Return the per-session redacted ``pty.log`` path (Section 3.6.3, FR-24)."""
    return supervise_state_dir(workspace_root, name) / SUPERVISE_PTY_LOG_FILENAME


def supervise_stop_request_path(workspace_root: Path, name: str) -> Path:
    """Return the per-session ``stop.request`` control-file path (Section 4.2)."""
    return supervise_state_dir(workspace_root, name) / SUPERVISE_STOP_REQUEST_FILENAME


def supervise_supervisor_log_path(workspace_root: Path, name: str) -> Path:
    """Return the per-session ``supervisor.log`` path (Section 7.2)."""
    return supervise_state_dir(workspace_root, name) / SUPERVISE_SUPERVISOR_LOG_FILENAME


# ---------------------------------------------------------------------------
# SuperviseSessionState
# ---------------------------------------------------------------------------


@dataclass
class SuperviseSessionState:
    """All persisted metadata for one supervise session (Section 5.5).

    Serialised to ``.devbench/supervise/<name>/state.json`` and indexed in
    ``.devbench/supervise/registry.json``. ``billing_channel`` is always
    ``"subscription"`` (Section 0.2, FR-9).

    Attributes:
        name: Supervise session name (the ``--name`` value).
        pid: OS process ID of the in-screen ``__run`` supervisor.
        state: Lifecycle state (one of ``SUPERVISE_VALID_STATES``, Section 4.8).
        screen_name: The ``screen`` session name (``<prefix><name>``).
        started_at: UTC-aware datetime when the session was started.
        started_by: OS username of the user who started the session.
        model: Resolved interactive model passed via ``claude --model``.
        effort: Resolved effort level.
        scope: Expanded work-unit IDs for this session (the scope.json
            ``expanded_ids``), recorded for ``status``/``info``.
        billing_channel: Always ``"subscription"`` (FR-9).
        claude_session_id: The captured ``claude`` session id (for ``--resume``).
        claude_path: Resolved ``claude`` executable path (FR-25).
        claude_version: Recorded ``claude --version`` (FR-25).
        screen_path: Resolved ``screen`` executable path (FR-25).
        screen_version: Recorded ``screen --version`` (FR-25).
        last_activity: UTC-aware datetime of the last observed working activity.
        restart_count: Auto-restart attempts consumed (FR-12).
        resumes_used: Quota resumes consumed (FR-15).
        expected_resume: UTC-aware datetime the quota window is expected to
            refresh (FR-16); ``None`` when not quota-waiting.
        exit_reason: Classified exit reason when stopped/errored (FR-13).
    """

    name: str
    pid: int
    state: str
    screen_name: str
    started_at: datetime
    started_by: str
    model: str
    effort: str
    scope: list[str] = field(default_factory=list)
    billing_channel: str = SUPERVISE_BILLING_CHANNEL
    claude_session_id: str | None = None
    claude_path: str | None = None
    claude_version: str | None = None
    screen_path: str | None = None
    screen_version: str | None = None
    last_activity: datetime | None = None
    restart_count: int = 0
    resumes_used: int = 0
    expected_resume: datetime | None = None
    exit_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-serialisable dict.

        Datetimes are written as ISO-8601 strings; ``None`` datetimes stay
        ``None``.
        """

        def _iso(value: datetime | None) -> str | None:
            return value.isoformat() if value is not None else None

        return {
            "name": self.name,
            "pid": self.pid,
            "state": self.state,
            "screen_name": self.screen_name,
            "started_at": self.started_at.isoformat(),
            "started_by": self.started_by,
            "model": self.model,
            "effort": self.effort,
            "scope": list(self.scope),
            "billing_channel": self.billing_channel,
            "claude_session_id": self.claude_session_id,
            "claude_path": self.claude_path,
            "claude_version": self.claude_version,
            "screen_path": self.screen_path,
            "screen_version": self.screen_version,
            "last_activity": _iso(self.last_activity),
            "restart_count": self.restart_count,
            "resumes_used": self.resumes_used,
            "expected_resume": _iso(self.expected_resume),
            "exit_reason": self.exit_reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SuperviseSessionState:
        """Deserialise from a dict produced by :meth:`to_dict`.

        Args:
            data: Dict containing at least the required keys ``name``, ``pid``,
                ``state``, ``screen_name``, ``started_at``, ``started_by``,
                ``model``, ``effort``.

        Returns:
            A :class:`SuperviseSessionState`.

        Raises:
            KeyError: A required field is absent.
            ValueError: A datetime field is not valid ISO-8601.
        """

        def _parse_dt(raw: str | None) -> datetime | None:
            if raw is None:
                return None
            try:
                parsed = datetime.fromisoformat(raw)
            except ValueError:
                raise ValueError(f"datetime {raw!r} is not valid ISO-8601") from None
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)

        started_at = _parse_dt(data["started_at"])
        if started_at is None:
            raise ValueError("started_at is required and must be a valid ISO-8601 string")

        return cls(
            name=data["name"],
            pid=data["pid"],
            state=data["state"],
            screen_name=data["screen_name"],
            started_at=started_at,
            started_by=data["started_by"],
            model=data["model"],
            effort=data["effort"],
            scope=list(data.get("scope", [])),
            billing_channel=data.get("billing_channel", SUPERVISE_BILLING_CHANNEL),
            claude_session_id=data.get("claude_session_id"),
            claude_path=data.get("claude_path"),
            claude_version=data.get("claude_version"),
            screen_path=data.get("screen_path"),
            screen_version=data.get("screen_version"),
            last_activity=_parse_dt(data.get("last_activity")),
            restart_count=int(data.get("restart_count", 0)),
            resumes_used=int(data.get("resumes_used", 0)),
            expected_resume=_parse_dt(data.get("expected_resume")),
            exit_reason=data.get("exit_reason"),
        )


# ---------------------------------------------------------------------------
# SuperviseRegistry
# ---------------------------------------------------------------------------


class SuperviseRegistry:
    """Read/write the supervise registry and per-session ``state.json`` files.

    The registry is a JSON array written to
    ``<workspace_root>/.devbench/supervise/registry.json``. Each element is a
    serialised :class:`SuperviseSessionState`. Writes use a temp-then-rename
    pattern so readers never observe a partial file (mirrors
    :class:`devbench.session.SessionRegistry`).

    This is INTENTIONALLY separate from :class:`SessionRegistry` (D-8): the
    supervise path is the subscription-billed channel and the SDK path is the
    API-billed channel, and ``status``/``info`` must keep these distinct.

    Args:
        workspace_root: Root directory of the devbench workspace.
    """

    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = workspace_root
        self._registry_path = workspace_root / SUPERVISE_REGISTRY_PATH

    # ------------------------------------------------------------------
    # Registry file I/O
    # ------------------------------------------------------------------

    def load(self) -> list[SuperviseSessionState]:
        """Load and deserialise the registry file.

        Returns:
            List of states; empty list when the file is absent.

        Raises:
            ValueError: The file contains invalid JSON or a non-list JSON root.
        """
        if not self._registry_path.exists():
            return []

        raw = self._registry_path.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"supervise registry.json contains invalid JSON: {exc}") from exc

        if not isinstance(data, list):
            raise ValueError(f"supervise registry.json must contain a JSON array, got {type(data).__name__}")

        return [SuperviseSessionState.from_dict(entry) for entry in data]

    def save(self, sessions: list[SuperviseSessionState]) -> None:
        """Serialise and atomically write *sessions* to the registry file.

        Creates ``.devbench/supervise/`` if absent. Write-to-tmp-then-rename so
        readers never see a partial file.

        Args:
            sessions: States to persist.

        Raises:
            OSError: The write or rename step fails; the temp file is cleaned up.
        """
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([s.to_dict() for s in sessions], indent=2)
        tmp = self._registry_path.with_suffix(SUPERVISE_REGISTRY_TMP_SUFFIX)
        try:
            tmp.write_text(payload, encoding="utf-8")
        except Exception:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise
        tmp.replace(self._registry_path)

    def upsert(self, state: SuperviseSessionState) -> None:
        """Insert or replace *state* in the registry by name (atomic save)."""
        sessions = [s for s in self.load() if s.name != state.name]
        sessions.append(state)
        self.save(sessions)

    def remove(self, name: str) -> None:
        """Drop the registry entry for *name* if present (idempotent)."""
        sessions = [s for s in self.load() if s.name != name]
        self.save(sessions)

    # ------------------------------------------------------------------
    # Per-session state.json
    # ------------------------------------------------------------------

    def write_state(self, state: SuperviseSessionState) -> None:
        """Write *state* to ``.devbench/supervise/<name>/state.json`` atomically.

        Also upserts the registry index so listing reflects the session.

        Args:
            state: The session state to persist.

        Raises:
            OSError: A write or rename fails.
            ValueError: ``state.name`` contains a ``..`` path segment.
        """
        state_dir = supervise_state_dir(self._workspace_root, state.name)
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / SUPERVISE_STATE_FILENAME
        payload = json.dumps(state.to_dict(), indent=2)
        tmp = state_file.with_suffix(SUPERVISE_REGISTRY_TMP_SUFFIX)
        try:
            tmp.write_text(payload, encoding="utf-8")
        except Exception:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            raise
        tmp.replace(state_file)
        self.upsert(state)

    def read_state(self, name: str) -> SuperviseSessionState | None:
        """Read the per-session ``state.json`` for *name*.

        Args:
            name: Supervise session name.

        Returns:
            The deserialised state, or ``None`` when the file is absent.

        Raises:
            ValueError: The file is malformed; *name* contains ``..``.
        """
        state_file = supervise_state_file_path(self._workspace_root, name)
        if not state_file.exists():
            return None
        raw = state_file.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"supervise state.json for {name!r} contains invalid JSON: {exc}") from exc
        return SuperviseSessionState.from_dict(data)

    # ------------------------------------------------------------------
    # Liveness + stale reaping (mirrors SessionRegistry)
    # ------------------------------------------------------------------

    def is_alive(self, pid: int) -> bool:
        """Return ``True`` when *pid* refers to a running process.

        Uses ``os.kill(pid, 0)``: success or ``EPERM`` (cross-user) means
        ACTIVE; ``ESRCH`` means STALE; any other ``OSError`` propagates.

        Args:
            pid: OS process ID to check.

        Returns:
            ``True`` if alive (or unqueryable due to permissions), else ``False``.

        Raises:
            OSError: An unexpected OS error (not ESRCH, not EPERM).
        """
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def liveness_of_sessions(self, sessions: list[SuperviseSessionState]) -> dict[str, str]:
        """Return a mapping from session name to ``"ACTIVE"`` or ``"STALE"``."""
        return {s.name: ("ACTIVE" if self.is_alive(s.pid) else "STALE") for s in sessions}

    def cleanup_stale_sessions(self) -> list[str]:
        """Remove state dirs + registry entries for sessions whose PID is dead.

        Returns:
            Sorted list of removed session names.

        Raises:
            OSError: ``is_alive`` raised unexpectedly, or ``rmtree`` failed.
            ValueError: The registry file is malformed (propagated from
                :meth:`load`).
        """
        sessions = self.load()
        surviving: list[SuperviseSessionState] = []
        removed_names: list[str] = []

        for session in sessions:
            if self.is_alive(session.pid):
                surviving.append(session)
            else:
                removed_names.append(session.name)
                state_dir = supervise_state_dir(self._workspace_root, session.name)
                if state_dir.exists():
                    shutil.rmtree(state_dir)

        if removed_names:
            self.save(surviving)

        return sorted(removed_names)


def new_session_state(
    *,
    name: str,
    pid: int,
    screen_name: str,
    model: str,
    effort: str,
    started_by: str,
    scope: list[str] | None = None,
) -> SuperviseSessionState:
    """Construct a fresh ``starting``-state session record (helper for ``start``).

    Args:
        name: Supervise session name.
        pid: OS process ID of the supervisor.
        screen_name: The ``screen`` session name.
        model: Resolved interactive model.
        effort: Resolved effort level.
        started_by: OS username starting the session.
        scope: Expanded work-unit IDs (defaults to empty = whole backlog).

    Returns:
        A :class:`SuperviseSessionState` in the ``starting`` state.
    """
    return SuperviseSessionState(
        name=name,
        pid=pid,
        state=SUPERVISE_STATE_STARTING,
        screen_name=screen_name,
        started_at=datetime.now(UTC),
        started_by=started_by,
        model=model,
        effort=effort,
        scope=list(scope or []),
        billing_channel=SUPERVISE_BILLING_CHANNEL,
    )
