"""Interactive ``claude`` CLI orchestrator under a detached ``screen`` daemon.

This module implements the ``devbench supervise`` feature (spec
``devbench-supervise-screen-orchestrator``): it runs the orchestrator as an
interactive ``claude`` session inside a ``screen`` daemon, driven by a
``pexpect`` supervisor, authenticated against the Claude Code subscription so
token consumption draws from the rolling 5-hour windows rather than the
Anthropic API (Section 0.2).

Phase 1 landed the persistence substrate:

- :class:`SuperviseSessionState` -- the per-session state record (Section 5.5).
- :class:`SuperviseRegistry` -- read/write ``.devbench/supervise/registry.json``
  and per-session ``state.json``; PID liveness; stale-reaping. It MIRRORS
  :class:`devbench.session.SessionRegistry`'s file/atomic-write shape but is a
  SEPARATE registry (D-8) so the subscription-billed supervise channel stays
  distinct from the API-billed SDK channel in ``status``/``info``.
- path helpers for the per-session state dir, ``pty.log``, and ``stop.request``.

Phase 2 (this commit) lands the supervisor core (Section 4.0 decomposition):

- :class:`EnvSanitizer` -- builds the minimized screen env, stripping the
  always-deny API/Bedrock vars (Section 3.6.1, FR-21) so the session bills
  against the subscription, and exporting the three scope-conveyance vars.
- :class:`AuthVerifier` -- subscription-auth + API-key-guard + non-root preflight
  (FR-20, FR-21, Section 3.6.2).
- :class:`DetectionPatterns` -- compiled, config-driven prompt/quota/fault
  regexes (FR-29).
- :class:`PtyLogWriter` -- the redacted ``pty.log`` tee, mode 0600 (FR-24).
- :class:`PtyDriver` -- the ``pexpect`` wrapper: ready detection + ``sendline``.
- :class:`CommandInjector` -- the injectable-command registry sender (FR-28).
- :class:`SupervisorStateMachine` -- the pure lifecycle state machine (FR-27).
- model/effort/scope resolution helpers + the launch-argv assembler (FR-5, FR-8,
  FR-19) and :func:`run_supervised_kickoff` (the launch->ready->kickoff->running
  pipeline the in-screen ``__run`` runs).

Phase 3 (this commit) lands the quota wait-and-resume adapter, the hybrid
log-tail detector, the bounded restart loop, the exit taxonomy classifier, and
the ``__run`` event loop (Section 4.3/4.6/4.9):

- :func:`classify_supervise_outcome` -- the Section 4.6 exit taxonomy (clean ->
  exit 0; fault -> classified non-zero; quota -> NOT an exit).
- :class:`LogTailDetector` -- tails the orchestrator log for the Section 1.6
  terminal/quota/restart markers (hybrid detection alongside the PTY patterns).
- :class:`QuotaWaiter` -- a THIN DRY ADAPTER over the shared quota primitives
  (``quota.wait_for_reset`` / ``quota.detect_quota_error`` /
  ``quota.QuotaCheckpoint`` / ``cli._resolve_max_quota_resumes``); the only new
  logic is the interactive prompt detection + the resume-cap branch (FR-15).
- :class:`RestartBudget` + :func:`build_resume_argv` -- the bounded auto-restart
  loop honoring the exit-42 restart signal (FR-12, Section 4.3).
- :func:`run_supervise_event_loop` -- the ``__run`` event loop wiring all of the
  above onto the launch->running pipeline Phase 2 built (FR-13, FR-27).

The read-only ``status``/``info``/``attach``/``stop`` verbs land in Phase 4 per
``IMPLEMENTATION-PLAN.md``.

All path/string literals are sourced from :mod:`devbench.constants`; no strings
are hard-coded in this module.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import json
import logging
import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pexpect

from devbench.constants import (
    ORCHESTRATOR_RESTART_EXIT_CODE,
    SUPERVISE_ALWAYS_DENY_ENV_VARS,
    SUPERVISE_BASE_DIR,
    SUPERVISE_BILLING_CHANNEL,
    SUPERVISE_EFFORT_DEFAULT,
    SUPERVISE_FAULT_AUDIT_PREFIX,
    SUPERVISE_FAULT_EXIT_CODE,
    SUPERVISE_INFO_STATE_STALE,
    SUPERVISE_INFO_STATE_UNKNOWN,
    SUPERVISE_PTY_LOG_FILENAME,
    SUPERVISE_REGISTRY_PATH,
    SUPERVISE_REGISTRY_TMP_SUFFIX,
    SUPERVISE_RESTART_AUDIT_PREFIX,
    SUPERVISE_SCREEN_NAME_PREFIX_DEFAULT,
    SUPERVISE_STATE_AUDIT_PREFIX,
    SUPERVISE_STATE_COMPLETED_CLEAN,
    SUPERVISE_STATE_DRAINING,
    SUPERVISE_STATE_FAULTED,
    SUPERVISE_STATE_FILENAME,
    SUPERVISE_STATE_QUOTA_RESUMED,
    SUPERVISE_STATE_QUOTA_WAITING,
    SUPERVISE_STATE_RESTARTING,
    SUPERVISE_STATE_RUNNING,
    SUPERVISE_STATE_STARTING,
    SUPERVISE_STATE_STOPPED,
    SUPERVISE_STOP_REQUEST_FILENAME,
    SUPERVISE_SUPERVISOR_LOG_FILENAME,
    SUPERVISE_VALID_EFFORT_LEVELS,
)
from devbench.scope import ScopeFilter, session_scope_file_path

if TYPE_CHECKING:
    from devbench.config_loader import (
        SuperviseConfig,
        SuperviseDetectionPatternsConfig,
        SuperviseLogTailConfig,
        SuperviseRestartConfig,
    )
    from devbench.quota import QuotaExhaustedError

logger = logging.getLogger("devbench.supervise")

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


# ===========================================================================
# Phase 2 -- supervisor core (Section 4.0)
# ===========================================================================
#
# These classes are dependency-injected and free of module-global config: the
# CLI ``cmd_supervise start`` / ``__run`` bodies wire ``RUNTIME_CONFIG.supervise``
# + ``WORKSPACE_ROOT`` into them. This keeps the core unit-testable with the
# ``FakePexpectChild`` double and no real ``claude``/``screen`` (Section 10.0).


# ---------------------------------------------------------------------------
# Fail-fast error taxonomy (FR-30)
# ---------------------------------------------------------------------------


class SuperviseError(Exception):
    """Base class for supervise-specific fail-fast errors (FR-30)."""


class SuperviseApiKeyPresentError(SuperviseError):
    """An always-deny API/Bedrock-routing var is present in the env (FR-21).

    Launching with this set would silently route inference to API/Bedrock
    billing and defeat the subscription-billing goal (Section 0.2).
    """


class SuperviseAuthError(SuperviseError):
    """Subscription auth is absent or invalid (FR-20, Section 3.6.1)."""


class SuperviseRootError(SuperviseError):
    """Refusing to launch ``claude --dangerously-skip-permissions`` as root."""


class SuperviseModelUnsetError(SuperviseError):
    """No interactive model could be resolved (Section 5.4, FR-19, D-3)."""


class SuperviseUnknownCommandError(SuperviseError):
    """An injectable-command name is not in the registry (Section 5.3, FR-28)."""


class SuperviseReadyTimeoutError(SuperviseError):
    """The interactive ready prompt did not appear within the timeout (FR-7)."""


class SupervisePromptTimeoutError(SuperviseError):
    """A required prompt did not arrive within its timeout mid-session (Section 4.6).

    Classified as a FAULT (``prompt-timeout-<phase>``) by the event loop so a
    stalled session exits non-zero rather than spinning forever.
    """


class SuperviseTransitionError(SuperviseError):
    """An illegal state-machine transition was requested (FR-27, Section 4.8)."""


# ---------------------------------------------------------------------------
# claude path resolution (FR-25)
# ---------------------------------------------------------------------------


def require_claude(*, which: Callable[[str], str | None] = shutil.which) -> str:
    """Return the resolved ``claude`` executable path or fail fast (FR-25).

    Args:
        which: Resolver mapping an executable name to its absolute path or
            ``None`` (defaults to :func:`shutil.which`; injectable for tests).

    Returns:
        Absolute path to the ``claude`` executable.

    Raises:
        FileNotFoundError: ``claude`` is not on ``PATH`` (Section 7.1).
    """
    path = which("claude")
    if path is None:
        raise FileNotFoundError("'claude' not found on PATH.")
    return path


# ---------------------------------------------------------------------------
# EnvSanitizer (Section 3.6.1, FR-21)
# ---------------------------------------------------------------------------


class EnvSanitizer:
    """Build the minimized ``screen`` session environment (Section 3.6.1, FR-21).

    Starts from a copy of the operator's environment, removes the non-removable
    always-deny set (:data:`SUPERVISE_ALWAYS_DENY_ENV_VARS`) plus the configured
    additional ``deny_vars``, and exports the three scope-conveyance vars
    (Section 5.6). The always-deny set routes inference to API/Bedrock billing,
    so stripping it is a correctness requirement, not a preference.

    Args:
        extra_deny_vars: Additional env-var names to strip (``supervise.env.deny_vars``).
    """

    def __init__(self, *, extra_deny_vars: tuple[str, ...]) -> None:
        # The always-deny set is layered on top and cannot be removed (FR-21).
        self._deny: frozenset[str] = frozenset(SUPERVISE_ALWAYS_DENY_ENV_VARS) | frozenset(extra_deny_vars)

    def build(
        self,
        *,
        source_env: dict[str, str],
        workspace_root: str,
        session_name: str,
        import_model: str,
    ) -> dict[str, str]:
        """Return the minimized session env with deny vars stripped + exports added.

        Args:
            source_env: The operator's environment (e.g. ``dict(os.environ)``).
            workspace_root: Absolute workspace root; exported as
                ``DEVBENCH_WORKSPACE_ROOT`` (conveys backlog + config identity).
            session_name: Exported as ``DEVBENCH_SESSION_NAME`` (per-session routing).
            import_model: The import-time model exported as ``DEVBENCH_CLAUDE_MODEL``
                (the in-session ``devbench`` subprocesses need it to import
                ``config.py``; it is NOT the interactive billing model, D-3).

        Returns:
            A new dict (the source is never mutated) with every deny var removed
            and the three scope-conveyance vars set.

        Raises:
            ValueError: ``workspace_root`` or ``import_model`` is empty (fail-fast;
                the in-session ``devbench`` would otherwise ``sys.exit(2)``).
        """
        if not workspace_root:
            raise ValueError("workspace_root is required to export DEVBENCH_WORKSPACE_ROOT")
        if not import_model:
            raise ValueError("import_model is required to export DEVBENCH_CLAUDE_MODEL")

        # Build the session env by EXCLUDING every deny var: a comprehension that
        # omits the deny keys cannot, by construction, leave a denied var behind,
        # so the subscription-billing guarantee (Section 0.2, FR-21) holds without
        # a fallible second pass. The AuthVerifier additionally fails fast at
        # preflight if a deny var is even present in the operator env.
        env = {k: v for k, v in source_env.items() if k not in self._deny}
        env["DEVBENCH_WORKSPACE_ROOT"] = workspace_root
        env["DEVBENCH_SESSION_NAME"] = session_name
        env["DEVBENCH_CLAUDE_MODEL"] = import_model
        return env


# ---------------------------------------------------------------------------
# AuthVerifier (Section 3.6.1, 3.6.2, FR-20, FR-21)
# ---------------------------------------------------------------------------


class AuthVerifier:
    """Preflight that confirms the session will bill against the subscription.

    Three independent fail-fast checks (Section 3.6.1, 3.6.2):

    1. No always-deny API/Bedrock-routing var is present in the operator env
       (FR-21) -- else the session would silently route to API billing.
    2. Subscription auth is present: the credentials file parses to a
       ``claudeAiOauth.accessToken`` whose ``scopes`` include ``user:inference``
       (FR-20).
    3. The process is non-root (Section 3.6.2) -- defense in depth around
       ``claude``'s own root refusal.

    Args:
        credentials_file: Path to ``~/.claude/.credentials.json`` (overridable
            via ``DEVBENCH_CLAUDE_CREDENTIALS_FILE``).
    """

    _REQUIRED_SCOPE = "user:inference"

    def __init__(self, *, credentials_file: Path) -> None:
        self._credentials_file = credentials_file

    def verify(self, *, source_env: dict[str, str], euid: int) -> None:
        """Run the three preflight checks; raise on the first failure.

        Args:
            source_env: The operator's environment.
            euid: The effective UID (``os.geteuid()``).

        Raises:
            SuperviseApiKeyPresentError: An always-deny var is present (FR-21).
            SuperviseAuthError: Subscription auth absent/invalid (FR-20).
            SuperviseRootError: Running as root (Section 3.6.2).
        """
        self._assert_no_api_key(source_env)
        self._assert_subscription_auth()
        self._assert_non_root(euid)

    def _assert_no_api_key(self, source_env: dict[str, str]) -> None:
        present = [var for var in SUPERVISE_ALWAYS_DENY_ENV_VARS if source_env.get(var)]
        if present:
            offending = present[0]
            raise SuperviseApiKeyPresentError(
                f"{offending} is set; an interactive supervised session must bill against the "
                "Claude Code subscription, not the API. Unset it and retry."
            )

    def _assert_subscription_auth(self) -> None:
        if not self._credentials_file.is_file():
            raise SuperviseAuthError(
                "Claude Code subscription auth not found. Run 'claude' and complete the browser login, then retry."
            )
        try:
            data = json.loads(self._credentials_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SuperviseAuthError(
                f"Claude Code subscription auth not found or unreadable at "
                f"'{self._credentials_file}': {exc}. Run 'claude' to login, then retry."
            ) from exc
        oauth = data.get("claudeAiOauth")
        if not isinstance(oauth, dict):
            raise SuperviseAuthError(
                f"Claude Code subscription auth not found: '{self._credentials_file}' has no "
                "'claudeAiOauth' object. Run 'claude' to login, then retry."
            )
        token = str(oauth.get("accessToken", "")).strip()
        if not token:
            raise SuperviseAuthError(
                f"Claude Code subscription auth not found: no access token in "
                f"'{self._credentials_file}'. Run 'claude' to login, then retry."
            )
        scopes = oauth.get("scopes")
        if not isinstance(scopes, list) or self._REQUIRED_SCOPE not in scopes:
            raise SuperviseAuthError(
                f"Claude Code subscription auth is missing the '{self._REQUIRED_SCOPE}' scope in "
                f"'{self._credentials_file}'. Re-run 'claude' to login, then retry."
            )

    def _assert_non_root(self, euid: int) -> None:
        if euid == 0:
            raise SuperviseRootError("refusing to launch claude --dangerously-skip-permissions as root.")


# ---------------------------------------------------------------------------
# DetectionPatterns (Section 5.1, 6.3, FR-29)
# ---------------------------------------------------------------------------


class DetectionPatterns:
    """Compiled, config-driven Claude-CLI-output detection regexes (FR-29).

    Every pattern is centralized in ``supervise.detection_patterns`` config so a
    CLI prompt-text change is fixed by editing config, not code (Section 6.3).
    Compilation happens once at construction and fails fast on a bad regex.

    Args:
        config: The ``SuperviseDetectionPatternsConfig`` dataclass.
    """

    def __init__(self, config: SuperviseDetectionPatternsConfig) -> None:
        self.ready_prompt_raw = config.ready_prompt
        self.working_prompt_raw = config.working_prompt
        self.quota_limit_raw = config.quota_limit
        self._ready = re.compile(config.ready_prompt)
        self._working = re.compile(config.working_prompt)
        self._quota_limit = re.compile(config.quota_limit)
        self._quota_wait_prompt = re.compile(config.quota_wait_prompt)
        self._reset_at = re.compile(config.reset_at)
        self._circuit_breaker = re.compile(config.circuit_breaker)
        self._harness_block = re.compile(config.harness_block)
        self._crash = re.compile(config.crash)

    def is_ready_prompt(self, text: str) -> bool:
        """Return ``True`` when *text* matches the interactive ready prompt."""
        return self._ready.search(text) is not None

    def is_working_prompt(self, text: str) -> bool:
        """Return ``True`` when *text* matches the working/activity prompt."""
        return self._working.search(text) is not None

    def is_quota_limit(self, text: str) -> bool:
        """Return ``True`` when *text* matches the usage-limit marker (FR-14)."""
        return self._quota_limit.search(text) is not None

    def is_quota_wait_prompt(self, text: str) -> bool:
        """Return ``True`` when *text* matches the in-session wait/retry prompt."""
        return self._quota_wait_prompt.search(text) is not None

    def match_reset_at(self, text: str) -> re.Match[str] | None:
        """Return the ``reset_at`` regex match (groups H, MM, am/pm) or ``None``."""
        return self._reset_at.search(text)

    def is_circuit_breaker(self, text: str) -> bool:
        """Return ``True`` when *text* matches the circuit-breaker pattern."""
        return self._circuit_breaker.search(text) is not None

    def is_harness_block(self, text: str) -> bool:
        """Return ``True`` when *text* matches the harness-self-edit-block marker."""
        return self._harness_block.search(text) is not None

    def is_crash(self, text: str) -> bool:
        """Return ``True`` when *text* matches the crash pattern."""
        return self._crash.search(text) is not None


# ---------------------------------------------------------------------------
# PtyLogWriter (Section 3.6.3, FR-24)
# ---------------------------------------------------------------------------


class PtyLogWriter:
    """Redact + append the PTY stream to ``pty.log`` (mode 0600, FR-24).

    Each chunk is passed through the configured redaction patterns before being
    written so a secret the model echoes never lands on disk (Section 3.6.3).
    The file is created mode 0600 on first write.

    Args:
        path: Absolute ``pty.log`` path.
        redact_patterns: Regexes whose matches are replaced with a redaction tag.
    """

    _REDACTED = "[REDACTED]"

    def __init__(self, *, path: Path, redact_patterns: tuple[str, ...]) -> None:
        self._path = path
        self._compiled = [re.compile(p) for p in redact_patterns]
        self._fd: int | None = None

    def write(self, chunk: str) -> None:
        """Redact *chunk* and append it to ``pty.log`` (creating it mode 0600)."""
        redacted = chunk
        for pattern in self._compiled:
            redacted = pattern.sub(self._REDACTED, redacted)
        if self._fd is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            # os.open honours the mode only on creation; enforce it regardless so
            # a pre-existing file is tightened to 0600 (FR-24).
            self._path.chmod(0o600)
        os.write(self._fd, redacted.encode("utf-8"))

    def close(self) -> None:
        """Close the underlying file descriptor (idempotent)."""
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None


# ---------------------------------------------------------------------------
# Launch-argv assembly (FR-5, Section 4.1 step 5)
# ---------------------------------------------------------------------------


def build_claude_launch_argv(
    *,
    claude_path: str,
    model: str,
    effort: str,
    plugin_dir: str,
    resume_session_id: str | None = None,
    resume_continue: bool = False,
) -> list[str]:
    """Assemble the interactive ``claude`` launch argv (FR-5, Section 4.1 step 5).

    The argv carries ``--model``, ``--effort``, ``--dangerously-skip-permissions``
    and ``--plugin-dir``. It NEVER carries ``-p``/``--print`` (the operator
    requirement; ``--print`` is non-interactive batch mode and would reintroduce
    the wrong billing/UX model, Section 1.9, Section 12). On restart/resume it
    additionally carries ``--resume <id>`` (when an id was captured) or
    ``--continue`` (FR-12, Section 4.3).

    Args:
        claude_path: Resolved ``claude`` executable path.
        model: Resolved interactive model (the subscription session's model).
        effort: Resolved effort level (default ``xhigh``, D-11).
        plugin_dir: Resolved ``--plugin-dir`` target (shadow or canonical, D-4).
        resume_session_id: When set, adds ``--resume <id>`` (Section 4.3).
        resume_continue: When ``True`` (and no ``resume_session_id``), adds
            ``--continue``.

    Returns:
        The argv list (``argv[0]`` is *claude_path*).

    Raises:
        ValueError: *model*, *effort*, or *plugin_dir* is empty (fail-fast).
    """
    if not model or not model.strip():
        raise ValueError("model is required to assemble the claude launch argv")
    if not effort or not effort.strip():
        raise ValueError("effort is required to assemble the claude launch argv")
    if not plugin_dir:
        raise ValueError("plugin_dir is required to assemble the claude launch argv")

    argv = [claude_path, "--model", model, "--effort", effort, "--dangerously-skip-permissions"]
    if resume_session_id:
        argv += ["--resume", resume_session_id]
    elif resume_continue:
        argv.append("--continue")
    argv += ["--plugin-dir", plugin_dir]
    return argv


# ---------------------------------------------------------------------------
# PtyDriver (Section 4.0, FR-7)
# ---------------------------------------------------------------------------


class PtyDriver:
    """Thin wrapper over a ``pexpect`` child: ready detection + ``sendline``.

    The driver does not spawn the child itself (so it is testable with the
    :class:`FakePexpectChild` double); the caller passes an already-spawned child
    (or the double). Optionally tees the matched output to a :class:`PtyLogWriter`.

    Args:
        child: A ``pexpect.spawn`` instance (or the test double) exposing
            ``expect``/``sendline``/``before``/``after``/``terminate``.
        patterns: The compiled :class:`DetectionPatterns`.
        log_writer: Optional :class:`PtyLogWriter` to tee matched output to.
    """

    def __init__(
        self,
        *,
        child: Any,
        patterns: DetectionPatterns,
        log_writer: PtyLogWriter | None = None,
    ) -> None:
        self.child = child
        self._patterns = patterns
        self._log_writer = log_writer

    def wait_for_ready(self, *, timeout_seconds: int) -> None:
        """Block until the interactive ready prompt appears (FR-7).

        Args:
            timeout_seconds: Max seconds to wait (``supervise.timeouts.ready_prompt_seconds``).

        Raises:
            SuperviseReadyTimeoutError: The prompt did not appear in time, or the
                child reached EOF before becoming ready (fail-fast, Section 4.6).
        """
        try:
            self.child.expect([self._patterns.ready_prompt_raw], timeout=timeout_seconds)
        except pexpect.TIMEOUT as exc:
            raise SuperviseReadyTimeoutError(
                f"claude did not present the ready prompt within {timeout_seconds}s (exit-reason=ready-prompt-timeout)."
            ) from exc
        except pexpect.EOF as exc:
            raise SuperviseReadyTimeoutError(
                "claude exited before presenting the ready prompt (exit-reason=ready-prompt-timeout)."
            ) from exc
        self._tee()

    def sendline(self, payload: str) -> None:
        """Send *payload* + newline to the child."""
        self.child.sendline(payload)

    def expect_working(self, *, timeout_seconds: int) -> bool:
        """Wait for the working/activity prompt (command ack); return success.

        Returns ``True`` when the working prompt appeared, ``False`` on timeout
        (the caller decides whether a missing ack is fatal).
        """
        try:
            self.child.expect([self._patterns.working_prompt_raw], timeout=timeout_seconds)
        except (pexpect.TIMEOUT, pexpect.EOF):
            return False
        self._tee()
        return True

    @property
    def patterns(self) -> DetectionPatterns:
        """The compiled :class:`DetectionPatterns` this driver matches against."""
        return self._patterns

    def read_chunk(self, *, timeout_seconds: int) -> tuple[str, bool, int | None]:
        """Read the next PTY chunk for the event loop (Section 4.1 step 8).

        Returns ``(text, eof, exitstatus)``: ``text`` is the matched output (the
        loop classifies it), ``eof`` is ``True`` when the child exited (carrying
        its ``exitstatus``), and a prompt TIMEOUT raises
        :class:`SupervisePromptTimeoutError` so the loop classifies a stall as a
        fault (Section 4.6, ``prompt-timeout-<phase>``) rather than spinning.

        The wildcard pattern ``.+`` matches any non-empty output chunk so the loop
        can observe arbitrary terminal text (it is the loop, not the driver, that
        decides whether the chunk is terminal).

        Raises:
            SupervisePromptTimeoutError: No output arrived within *timeout_seconds*.
        """
        try:
            self.child.expect([r".+"], timeout=timeout_seconds)
        except pexpect.EOF:
            self._tee()
            return getattr(self.child, "before", "") or "", True, getattr(self.child, "exitstatus", None)
        except pexpect.TIMEOUT as exc:
            raise SupervisePromptTimeoutError(
                f"no PTY activity within {timeout_seconds}s (exit-reason=prompt-timeout-idle)."
            ) from exc
        self._tee()
        self._last_text = getattr(self.child, "after", "") or getattr(self.child, "before", "") or ""
        return self._last_text, False, None

    def last_text(self) -> str:
        """Return the most-recently-read PTY chunk (for reset-time parsing)."""
        return getattr(self, "_last_text", "")

    def _tee(self) -> None:
        """Tee the most-recently-matched output to the redacted ``pty.log``."""
        if self._log_writer is not None:
            before = getattr(self.child, "before", "") or ""
            after = getattr(self.child, "after", "") or ""
            self._log_writer.write(before + after)


# ---------------------------------------------------------------------------
# CommandInjector (Section 5.3, FR-28)
# ---------------------------------------------------------------------------


class CommandInjector:
    """Send a named slash command from the registry through the PTY (FR-28).

    ``send(name)`` looks the literal up in the config registry, ``sendline``s it
    through the :class:`PtyDriver`, and waits for the working-prompt ack. A new
    operator capability is added by adding a registry entry -- NO supervisor code
    change. An unknown name fails fast (Section 5.3).

    Args:
        driver: The :class:`PtyDriver`.
        registry: The ``supervise.injectable_commands`` name->literal map.
        ack_timeout_seconds: ``supervise.timeouts.command_ack_seconds``.
    """

    def __init__(
        self,
        *,
        driver: PtyDriver,
        registry: dict[str, str],
        ack_timeout_seconds: int,
    ) -> None:
        self._driver = driver
        self._registry = dict(registry)
        self._ack_timeout = ack_timeout_seconds

    def send(self, name: str, /, **subst: str) -> str:
        """Send the registry command *name* (with optional ``{placeholder}`` subst).

        ``name`` is positional-only so a ``{placeholder}`` named ``name`` does not
        collide with the registry-key argument.

        Args:
            name: The registry key (e.g. ``"orchestrate"``).
            **subst: Optional ``str.format`` substitutions applied to the literal.

        Returns:
            The literal that was sent (post-substitution).

        Raises:
            SuperviseUnknownCommandError: *name* is not in the registry (FR-28).
        """
        if name not in self._registry:
            raise SuperviseUnknownCommandError(
                f"no injectable command {name!r} in supervise.injectable_commands (known: {sorted(self._registry)})."
            )
        literal = self._registry[name]
        if subst:
            literal = literal.format(**subst)
        self._driver.sendline(literal)
        # Wait for the working-prompt ack; a missing ack is not fatal here (the
        # supervise loop's hybrid log-tail also confirms activity, FR-29).
        self._driver.expect_working(timeout_seconds=self._ack_timeout)
        return literal


# ---------------------------------------------------------------------------
# SupervisorStateMachine (Section 4.8, FR-27)
# ---------------------------------------------------------------------------


# The transition table: (current_state, event) -> next_state (Section 4.8). Only
# the transitions this feature drives are present; an unlisted (state, event)
# pair is an illegal transition and fails fast. ``working-activity`` is a
# self-loop on ``running`` (refreshes last-activity without changing state).
_SUPERVISE_TRANSITIONS: dict[tuple[str, str], str] = {
    (SUPERVISE_STATE_STARTING, "ready"): SUPERVISE_STATE_STARTING,
    (SUPERVISE_STATE_STARTING, "orchestrate-injected"): SUPERVISE_STATE_RUNNING,  # gated on ready (see below)
    (SUPERVISE_STATE_STARTING, "fault"): SUPERVISE_STATE_FAULTED,
    (SUPERVISE_STATE_RUNNING, "working-activity"): SUPERVISE_STATE_RUNNING,
    (SUPERVISE_STATE_RUNNING, "quota-detected"): SUPERVISE_STATE_QUOTA_WAITING,
    (SUPERVISE_STATE_RUNNING, "restart-signal"): SUPERVISE_STATE_RESTARTING,
    (SUPERVISE_STATE_RUNNING, "drain-requested"): SUPERVISE_STATE_DRAINING,
    (SUPERVISE_STATE_RUNNING, "terminal-clean"): SUPERVISE_STATE_COMPLETED_CLEAN,
    (SUPERVISE_STATE_RUNNING, "fault"): SUPERVISE_STATE_FAULTED,
    (SUPERVISE_STATE_RUNNING, "stop-hard"): SUPERVISE_STATE_STOPPED,
    (SUPERVISE_STATE_QUOTA_WAITING, "quota-window-refreshed"): SUPERVISE_STATE_QUOTA_RESUMED,
    (SUPERVISE_STATE_QUOTA_WAITING, "session-exited-on-quota"): SUPERVISE_STATE_RESTARTING,
    (SUPERVISE_STATE_QUOTA_WAITING, "fault"): SUPERVISE_STATE_FAULTED,
    (SUPERVISE_STATE_QUOTA_WAITING, "stop-hard"): SUPERVISE_STATE_STOPPED,
    (SUPERVISE_STATE_QUOTA_RESUMED, "resume-confirmed"): SUPERVISE_STATE_RUNNING,
    (SUPERVISE_STATE_RESTARTING, "restart-launched"): SUPERVISE_STATE_RUNNING,
    (SUPERVISE_STATE_RESTARTING, "fault"): SUPERVISE_STATE_FAULTED,
    (SUPERVISE_STATE_DRAINING, "terminal-clean"): SUPERVISE_STATE_COMPLETED_CLEAN,
    (SUPERVISE_STATE_DRAINING, "stop-hard"): SUPERVISE_STATE_STOPPED,
}

# All events the machine recognizes (used to distinguish "unknown event" from
# "illegal transition" so the error message is precise, Section 4.8).
_SUPERVISE_EVENTS: frozenset[str] = frozenset(event for (_state, event) in _SUPERVISE_TRANSITIONS)

# Terminal states (no outbound transition; map to a final exit, Section 4.8).
_SUPERVISE_TERMINAL_STATES: frozenset[str] = frozenset(
    {SUPERVISE_STATE_COMPLETED_CLEAN, SUPERVISE_STATE_FAULTED, SUPERVISE_STATE_STOPPED}
)


class SupervisorStateMachine:
    """The pure supervised-session lifecycle state machine (FR-27, Section 4.8).

    No I/O: every transition is a lookup in :data:`_SUPERVISE_TRANSITIONS`. An
    unknown event or an illegal ``(state, event)`` pair fails fast so a logic bug
    can never silently corrupt the lifecycle.
    """

    def __init__(self) -> None:
        self.state = SUPERVISE_STATE_STARTING
        # (from_state, to_state, event) tuples for the supervisor.log line.
        self.history: list[tuple[str, str, str]] = []
        # The ``starting -> running`` transition requires BOTH ``ready`` and
        # ``orchestrate-injected`` (Section 4.8, AC-1): a ``ready`` event sets
        # this gate so injecting before the prompt is ready is illegal.
        self._ready_seen = False

    def on_event(self, event: str) -> str:
        """Apply *event* and return the new state (raising on illegality).

        Args:
            event: One of the recognized lifecycle events (Section 4.8).

        Returns:
            The state after the transition.

        Raises:
            SuperviseTransitionError: *event* is unknown, the
                ``(current_state, event)`` transition is not defined, or a
                ``orchestrate-injected`` event arrived before ``ready``.
        """
        if event not in _SUPERVISE_EVENTS:
            raise SuperviseTransitionError(
                f"unknown supervise event {event!r}; valid events: {sorted(_SUPERVISE_EVENTS)}."
            )
        key = (self.state, event)
        if key not in _SUPERVISE_TRANSITIONS:
            raise SuperviseTransitionError(
                f"illegal transition: event {event!r} is not valid from state {self.state!r}."
            )
        if event == "ready":
            self._ready_seen = True
        elif event == "orchestrate-injected" and not self._ready_seen:
            raise SuperviseTransitionError(
                "illegal transition: event 'orchestrate-injected' requires a prior 'ready' "
                f"event from state {self.state!r}."
            )
        new_state = _SUPERVISE_TRANSITIONS[key]
        self.history.append((self.state, new_state, event))
        self.state = new_state
        return new_state

    def is_terminal(self) -> bool:
        """Return ``True`` when the machine is in a terminal state."""
        return self.state in _SUPERVISE_TERMINAL_STATES


# ---------------------------------------------------------------------------
# Model + effort + scope resolution (Section 5.4, 5.6, FR-8, FR-19)
# ---------------------------------------------------------------------------


def resolve_supervise_model(
    *,
    cli_model: str | None,
    supervise_model: str | None,
    orchestrate_model: str | None,
    use_bedrock: bool,
) -> str:
    """Resolve the interactive model: --model > supervise.model > orchestrate.model.

    Mirrors the no-fallback contract (D-3); ``DEVBENCH_CLAUDE_MODEL`` is NOT
    consulted (it routes API billing, Section 0.2/1.2). The resolved value is
    validated via the REUSED ``validate_agent_model_value`` so ``haiku`` is
    rejected exactly as the SDK path rejects it (FR-19).

    Args:
        cli_model: The ``--model`` flag value (or ``None``).
        supervise_model: ``supervise.model`` from devbench.yaml (or ``None``).
        orchestrate_model: ``orchestrate.model`` from devbench.yaml (or ``None``).
        use_bedrock: The resolved ``use_bedrock`` flag (for model-format validation).

    Returns:
        The resolved, validated model string.

    Raises:
        SuperviseModelUnsetError: All three sources are unset/empty.
        ValueError: The resolved model fails ``validate_agent_model_value``
            (e.g. ``haiku``).
    """
    from devbench.config_loader import validate_agent_model_value

    for candidate in (cli_model, supervise_model, orchestrate_model):
        if candidate and candidate.strip():
            model = candidate.strip()
            validate_agent_model_value("supervise.model", "supervise", model, use_bedrock)
            return model
    raise SuperviseModelUnsetError("no model: set --model, supervise.model, or orchestrate.model.")


def resolve_supervise_effort(*, cli_effort: str | None, supervise_effort: str) -> str:
    """Resolve effort: --effort > supervise.effort > xhigh (Section 5.4, D-11).

    Args:
        cli_effort: The ``--effort`` flag value (or ``None``).
        supervise_effort: ``supervise.effort`` from devbench.yaml.

    Returns:
        The resolved effort level (one of :data:`SUPERVISE_VALID_EFFORT_LEVELS`).

    Raises:
        ValueError: The resolved effort is not a valid level (fail-fast).
    """
    effort = (cli_effort or "").strip() or (supervise_effort or "").strip() or SUPERVISE_EFFORT_DEFAULT
    if effort not in SUPERVISE_VALID_EFFORT_LEVELS:
        raise ValueError(f"invalid effort {effort!r}: must be one of {sorted(SUPERVISE_VALID_EFFORT_LEVELS)}.")
    return effort


def write_session_scope(
    *,
    workspace_root: Path,
    session_name: str,
    include: str,
    exclude: str,
    backlog_ids: list[str],
) -> list[str]:
    """Write the per-session ``scope.json`` and return the expanded ids (FR-8).

    Reuses the SDK path's own writer ``ScopeFilter.to_file`` (Section 5.6, step
    4a) at the canonical session-tree path so the orchestrate skill and
    ``devbench next`` read the same file. An empty *include* deterministically
    expands to the WHOLE backlog minus exclusions (AC-31) -- the specified
    default, not a fallback.

    Args:
        workspace_root: Workspace root.
        session_name: The ``--name`` value.
        include: The ``--include`` token string (empty = whole backlog).
        exclude: The ``--exclude`` token string.
        backlog_ids: All work-unit IDs from the parsed backlog.

    Returns:
        The sorted list of expanded work-unit IDs (for the registry record).
    """
    scope = ScopeFilter.parse(include, exclude, backlog_ids)
    scope.to_file(workspace_root, path=session_scope_file_path(workspace_root, session_name))
    return sorted(scope.expanded_ids)


# ---------------------------------------------------------------------------
# Kickoff pipeline (Section 4.1 steps 6-8) -- the heart of __run
# ---------------------------------------------------------------------------


def run_supervised_kickoff(
    *,
    driver: PtyDriver,
    injectable_commands: dict[str, str],
    ready_timeout_seconds: int,
    command_ack_seconds: int,
) -> SupervisorStateMachine:
    """Drive launch -> ready -> orchestrate-injected -> running (Section 4.1).

    Assumes the ``claude`` child has already been spawned and wired into *driver*.
    Waits for the ready prompt (FR-7), injects ``/devbench-orchestrate:orchestrate``
    (FR-8, scope is already authoritative via the exported env + scope.json), and
    advances the state machine to ``running``.

    Args:
        driver: The :class:`PtyDriver` wrapping the spawned child.
        injectable_commands: The ``supervise.injectable_commands`` registry.
        ready_timeout_seconds: ``supervise.timeouts.ready_prompt_seconds``.
        command_ack_seconds: ``supervise.timeouts.command_ack_seconds``.

    Returns:
        The :class:`SupervisorStateMachine` in the ``running`` state.

    Raises:
        SuperviseReadyTimeoutError: The ready prompt never appeared (the caller
            classifies this as a fault and tears down, Section 4.6).
        SuperviseUnknownCommandError: ``orchestrate`` is missing from the registry.
    """
    sm = SupervisorStateMachine()
    driver.wait_for_ready(timeout_seconds=ready_timeout_seconds)
    sm.on_event("ready")
    injector = CommandInjector(
        driver=driver,
        registry=injectable_commands,
        ack_timeout_seconds=command_ack_seconds,
    )
    injector.send("orchestrate")
    sm.on_event("orchestrate-injected")
    return sm


# ===========================================================================
# Phase 3 -- quota wait-and-resume adapter, log-tail, restart, exit taxonomy
# ===========================================================================


# ---------------------------------------------------------------------------
# Exit taxonomy (Section 4.6, FR-13)
# ---------------------------------------------------------------------------


# Markers the event loop hands to :func:`classify_supervise_outcome`. These name
# the DETECTION row of the Section 4.6 table, not a literal CLI string -- the
# literal-to-marker mapping is the loop's job (PTY-pattern / log-tail match), so
# the classifier stays a pure, exhaustively-tested function.
_OUTCOME_MARKER_ALL_DONE = "ALL_DONE"
_OUTCOME_MARKER_NO_ACTIONABLE = "NO_ACTIONABLE"
_OUTCOME_MARKER_TERMINAL_EXIT = "[ORCHESTRATOR_TERMINAL_EXIT]"
_OUTCOME_MARKER_CIRCUIT_BREAKER = "circuit_breaker"
_OUTCOME_MARKER_HARNESS_BLOCK = "harness_block"
_OUTCOME_MARKER_STOP_REASON = "stop_reason"
_OUTCOME_MARKER_PROMPT_TIMEOUT = "prompt_timeout"
_OUTCOME_MARKER_QUOTA_LIMIT = "quota_limit"
_OUTCOME_MARKER_RESTART_CAP = "restart_cap_exhausted"
_OUTCOME_MARKER_QUOTA_CAP = "quota_resume_cap_exhausted"

# Markers that mean "clean terminal" (Section 4.6 rows 1-2). A clean marker only
# yields a clean outcome when the child also exited 0 (defense in depth: a clean
# sentinel cannot launder a non-zero process exit).
_CLEAN_MARKERS: frozenset[str] = frozenset(
    {_OUTCOME_MARKER_ALL_DONE, _OUTCOME_MARKER_NO_ACTIONABLE, _OUTCOME_MARKER_TERMINAL_EXIT}
)


@dataclass(frozen=True)
class SuperviseOutcome:
    """A classified supervised-session outcome (Section 4.6, FR-13).

    Exactly one of the three dispositions holds:

    - clean: ``is_clean`` True, ``exit_code == 0``.
    - fault: ``is_clean``/``is_quota`` False, ``exit_code == SUPERVISE_FAULT_EXIT_CODE``.
    - quota: ``is_quota`` True, ``exit_code is None`` (quota NEVER exits -- the
      caller transitions to ``quota-waiting`` instead of returning a code).

    Attributes:
        exit_code: ``0`` (clean), :data:`SUPERVISE_FAULT_EXIT_CODE` (fault), or
            ``None`` (quota -- not an exit).
        exit_reason: The classified reason recorded in the registry.
        is_clean: True for a clean completion.
        is_quota: True for a quota holding state.
    """

    exit_code: int | None
    exit_reason: str
    is_clean: bool
    is_quota: bool


def _outcome_clean(reason: str) -> SuperviseOutcome:
    return SuperviseOutcome(exit_code=0, exit_reason=reason, is_clean=True, is_quota=False)


def _outcome_fault(reason: str) -> SuperviseOutcome:
    return SuperviseOutcome(exit_code=SUPERVISE_FAULT_EXIT_CODE, exit_reason=reason, is_clean=False, is_quota=False)


def _outcome_quota() -> SuperviseOutcome:
    return SuperviseOutcome(exit_code=None, exit_reason="quota-waiting", is_clean=False, is_quota=True)


# Fault markers whose classified exit-reason is a fixed literal (Section 4.6).
# The dynamic fault reasons (stop-reason-<token>, prompt-timeout-<phase>,
# claude-exit-<code>) are computed in-line; this table covers the static ones so
# the classifier stays a flat lookup rather than a return-per-row cascade.
_STATIC_FAULT_REASONS: dict[str, str] = {
    _OUTCOME_MARKER_CIRCUIT_BREAKER: "circuit-breaker",
    _OUTCOME_MARKER_HARNESS_BLOCK: "harness-self-edit-block",
    _OUTCOME_MARKER_RESTART_CAP: "restart-cap-exhausted",
    _OUTCOME_MARKER_QUOTA_CAP: "quota-resume-cap-exhausted",
}


def classify_supervise_outcome(
    *,
    marker: str | None,
    child_exitstatus: int | None,
    stop_reason: str | None = None,
    phase: str | None = None,
) -> SuperviseOutcome:
    """Classify a supervised-session outcome into the Section 4.6 taxonomy (FR-13).

    A non-zero child exit is ALWAYS a fault, even alongside a clean *marker* (a
    clean sentinel cannot launder a non-zero process exit). Quota is detected
    first among the non-clean markers because it is NOT a fault and must never
    yield a non-zero exit (Section 4.6 last row).

    Args:
        marker: The detection marker (one of the ``_OUTCOME_MARKER_*`` tokens) or
            ``None`` when the only signal is the child's exit status.
        child_exitstatus: The ``claude`` child's exit status (``None`` when the
            outcome was decided before/without a process exit, e.g. a cap or a
            prompt timeout).
        stop_reason: The ``[ORCHESTRATOR_STOP_REASON] reason=<token>`` token,
            required when ``marker == "stop_reason"``.
        phase: The phase a prompt timeout occurred in (e.g. ``"ready"``),
            required when ``marker == "prompt_timeout"``.

    Returns:
        The classified :class:`SuperviseOutcome`.
    """
    # Quota is NOT a fault and must be recognized before the child-exit fault
    # rule (a quota event may also carry a non-zero child exit on path 4.9b).
    if marker == _OUTCOME_MARKER_QUOTA_LIMIT:
        return _outcome_quota()

    # A non-zero child exit is a fault regardless of any clean marker present.
    if child_exitstatus is not None and child_exitstatus != 0:
        return _outcome_fault(f"claude-exit-{child_exitstatus}")

    if marker in _CLEAN_MARKERS:
        reason = "no-actionable" if marker == _OUTCOME_MARKER_NO_ACTIONABLE else "all-done"
        return _outcome_clean(reason)

    fault_reason = _fault_reason_for_marker(marker, stop_reason=stop_reason, phase=phase)
    if fault_reason is not None:
        return _outcome_fault(fault_reason)

    # No marker and a clean (0 / None) child exit: a bare clean process exit.
    return _outcome_clean("all-done")


def _fault_reason_for_marker(marker: str | None, *, stop_reason: str | None, phase: str | None) -> str | None:
    """Return the classified fault reason for a fault *marker*, or ``None``.

    Splits the marker->reason mapping out of :func:`classify_supervise_outcome`
    so that function stays under the project's return-statement ceiling. Dynamic
    reasons (stop-reason / prompt-timeout) embed a token/phase; the rest are a
    table lookup (:data:`_STATIC_FAULT_REASONS`).
    """
    if marker == _OUTCOME_MARKER_STOP_REASON:
        return f"stop-reason-{stop_reason or 'unknown'}"
    if marker == _OUTCOME_MARKER_PROMPT_TIMEOUT:
        return f"prompt-timeout-{phase or 'unknown'}"
    return _STATIC_FAULT_REASONS.get(marker or "")


# ---------------------------------------------------------------------------
# LogTailDetector (Section 1.6, 4.9, FR-14, hybrid detection)
# ---------------------------------------------------------------------------


class LogTailKind(enum.Enum):
    """The disposition a tailed orchestrator-log marker maps to (Section 1.6)."""

    CLEAN = "clean"
    QUOTA = "quota"
    FAULT = "fault"
    RESTART = "restart"


@dataclass(frozen=True)
class LogTailHit:
    """One actionable orchestrator-log marker the tail detector observed."""

    kind: LogTailKind
    line: str


class LogTailDetector:
    """Tail the orchestrator's own log for the Section 1.6 terminal markers (FR-14).

    Detection is HYBRID (Section 1.9): the supervisor watches the deterministic,
    CLI-version-stable devbench log markers in addition to screen-scraping the
    PTY, so a terminal/quota/restart signal is caught even when the on-screen
    wording drifts. :meth:`poll` consumes only the bytes appended since the last
    call (true tailing via a byte offset).

    Fault markers take precedence over clean markers within a single poll batch
    so a crash on the same poll as an earlier clean-looking line is never masked
    (fail-fast).

    Args:
        log_path: Absolute path to the orchestrator log to tail.
        config: The ``supervise.log_tail`` config (marker sets per disposition).
    """

    def __init__(self, *, log_path: Path, config: SuperviseLogTailConfig) -> None:
        self._log_path = log_path
        # Precedence order within a batch: fault, then restart, then quota, then
        # clean (a fault must never be masked by an earlier benign line).
        self._ordered: tuple[tuple[LogTailKind, tuple[str, ...]], ...] = (
            (LogTailKind.FAULT, config.markers_fault),
            (LogTailKind.RESTART, config.markers_restart),
            (LogTailKind.QUOTA, config.markers_quota),
            (LogTailKind.CLEAN, config.markers_clean),
        )
        self._offset = 0

    def poll(self) -> LogTailHit | None:
        """Return the highest-precedence actionable marker in the new log bytes.

        Returns ``None`` when the log is absent, has no new bytes, or the new
        bytes contain no configured marker. The byte offset advances each call so
        a marker is reported at most once.
        """
        if not self._log_path.exists():
            return None
        data = self._log_path.read_text(encoding="utf-8", errors="replace")
        if len(data) <= self._offset:
            # No new bytes (or the file was truncated/rotated to a shorter size:
            # reset the offset so a rotated log is re-read from its new start).
            self._offset = min(self._offset, len(data))
            return None
        new_text = data[self._offset :]
        self._offset = len(data)
        for kind, markers in self._ordered:
            for marker in markers:
                idx = new_text.find(marker)
                if idx != -1:
                    line = new_text[idx:].splitlines()[0]
                    return LogTailHit(kind=kind, line=line)
        return None


# ---------------------------------------------------------------------------
# QuotaWaiter (Section 4.9, FR-14/15/16) -- a THIN ADAPTER over quota.* (DRY)
# ---------------------------------------------------------------------------


class QuotaDecision(enum.Enum):
    """The disposition :meth:`QuotaWaiter.wait_and_decide` returns (Section 4.9)."""

    RESUME = "resume"  # window refreshed + under cap -> relaunch/continue (4.9a/4.9b)
    WAIT = "wait"  # wait did not recover yet -> keep waiting (still NOT an exit)
    FAULT = "fault"  # resume cap exhausted -> faulted (the ONLY quota fault path)


@dataclass(frozen=True)
class QuotaDecisionResult:
    """The outcome of a quota wait (Section 4.9, FR-15/16).

    Attributes:
        action: The :class:`QuotaDecision` the caller acts on.
        expected_resume: The provider-stated reset time surfaced by ``status``
            (FR-16); ``None`` when the reset time was unknown.
        exit_reason: Set only when ``action is FAULT`` (``quota-resume-cap-exhausted``).
    """

    action: QuotaDecision
    expected_resume: datetime | None
    exit_reason: str | None = None


class QuotaWaiter:
    """Interactive-path quota wait-and-resume adapter (Section 4.9, FR-14/15/16).

    This is a THIN ADAPTER over the SHARED quota primitives -- it does NOT
    reimplement the wait loop, the classifier, the resume cap, or the checkpoint
    (FR-15, AC-32). Those are injected so the production wiring
    (:func:`build_quota_waiter`) supplies the REAL ``quota.wait_for_reset`` /
    ``quota.detect_quota_error`` / ``quota.save_checkpoint`` /
    ``cli._resolve_max_quota_resumes`` callables.

    The only genuinely-new logic is (i) recognizing the interactive usage-limit
    PROMPT in screen-scraped PTY text (via the config detection patterns) and
    (ii) the resume-cap branch. Everything about HOW LONG to wait, the cap, the
    checkpoint, and the reset-time parse is the shared code.

    Quota is NEVER a fault except when the resume cap is exhausted: a recovered
    wait yields ``RESUME``; a not-yet-recovered wait yields ``WAIT`` (the caller
    keeps waiting); only ``resumes_used >= cap`` yields ``FAULT``.

    Args:
        patterns: The compiled :class:`DetectionPatterns` (PTY prompt detection).
        poll_interval_seconds: Wait cadence (``quota_handling.poll_interval_seconds``).
        max_wait_seconds: Wait cap (``quota_handling.max_wait_seconds``).
        wait_for_reset: The SHARED wait coroutine factory
            (``quota.wait_for_reset``); awaited inside :meth:`wait_and_decide`.
        detect_quota_error: The SHARED classifier (``quota.detect_quota_error``).
        resolve_max_resumes: The SHARED resume-cap resolver
            (``cli._resolve_max_quota_resumes``); called fresh each decision so
            an env change is honored.
        save_checkpoint: The SHARED checkpoint writer (``quota.save_checkpoint``).
        workspace_root: Workspace root (passed to the checkpoint writer).
        session_name: Session name (recorded in the checkpoint).
        run_wait: Internal hook to run the (async) wait callable to completion;
            defaults to :func:`asyncio.run`. Injectable for tests that supply a
            synchronous ``wait_for_reset`` fake.
    """

    def __init__(
        self,
        *,
        patterns: DetectionPatterns,
        poll_interval_seconds: int,
        max_wait_seconds: int,
        wait_for_reset: Callable[..., Any],
        detect_quota_error: Callable[[object], QuotaExhaustedError | None],
        resolve_max_resumes: Callable[[], int],
        save_checkpoint: Callable[..., None],
        workspace_root: Any,
        session_name: str,
        run_wait: Callable[[Any], bool] | None = None,
    ) -> None:
        self._patterns = patterns
        self._poll_interval = poll_interval_seconds
        self._max_wait = max_wait_seconds
        self._wait_for_reset = wait_for_reset
        self._detect_quota_error = detect_quota_error
        self._resolve_max_resumes = resolve_max_resumes
        self._save_checkpoint = save_checkpoint
        self._workspace_root = workspace_root
        self._session_name = session_name
        self._run_wait = run_wait

    def is_quota_text(self, text: str) -> bool:
        """Return True when *text* matches the interactive usage-limit prompt (FR-14)."""
        return self._patterns.is_quota_limit(text)

    def is_in_session_wait_prompt(self, text: str) -> bool:
        """Return True when *text* offers an in-session wait/retry choice (4.9a)."""
        return self._patterns.is_quota_wait_prompt(text)

    def parse_reset_at(self, text: str) -> datetime | None:
        """Parse the provider-stated reset time from PTY *text* (FR-16).

        Delegates the H:MM(am/pm) (UTC) match to the configured ``reset_at``
        pattern (seeded from ``quota._RESET_AT_RE``), then resolves it to the
        next-future UTC-aware datetime.
        """
        from datetime import timedelta

        from devbench.quota import _convert_to_24h

        match = self._patterns.match_reset_at(text)
        if match is None:
            return None
        raw_hour = int(match.group(1))
        raw_minute = int(match.group(2))
        meridiem = match.group(3).lower()
        if raw_hour < 1 or raw_hour > 12 or raw_minute < 0 or raw_minute > 59:
            return None
        # Reuse the SHARED 12h->24h converter (quota._convert_to_24h) so the
        # interactive path and the SDK path parse reset times identically (DRY).
        hour_24 = _convert_to_24h(raw_hour, meridiem)
        now = datetime.now(UTC)
        candidate = now.replace(hour=hour_24, minute=raw_minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    def classify_exit(self, text: object) -> QuotaExhaustedError | None:
        """Classify a claude exit/output as a quota signal via the SHARED classifier.

        Delegates to the injected ``quota.detect_quota_error`` (no local copy);
        used on path 4.9b to confirm a session that EXITED did so on a quota
        condition rather than a fault.
        """
        return self._detect_quota_error(text)

    def wait_and_decide(self, *, reset_at: datetime | None, resumes_used: int) -> QuotaDecisionResult:
        """Persist a checkpoint, delegate the wait, and decide resume vs fault (FR-15/16).

        The resume cap is checked FIRST (via the SHARED resolver): when
        ``resumes_used >= cap`` no wait is started (a wait would be pointless when
        no resume is permitted afterward) and the result is ``FAULT`` with
        ``quota-resume-cap-exhausted``. Otherwise a :class:`QuotaCheckpoint` is
        persisted (so the expected-resume survives a restart), the SHARED
        ``quota.wait_for_reset`` is awaited, and a recovered wait yields
        ``RESUME`` while a timed-out wait yields ``WAIT`` (keep waiting; still
        never a non-zero exit).

        Args:
            reset_at: The parsed provider reset time (or ``None``).
            resumes_used: Quota resumes already consumed this session.

        Returns:
            A :class:`QuotaDecisionResult`.
        """
        max_resumes = self._resolve_max_resumes()
        if resumes_used >= max_resumes:
            logger.info("%s resumes=%d/%d cap-exhausted", SUPERVISE_FAULT_AUDIT_PREFIX, resumes_used, max_resumes)
            return QuotaDecisionResult(
                action=QuotaDecision.FAULT,
                expected_resume=reset_at,
                exit_reason="quota-resume-cap-exhausted",
            )

        self._persist_checkpoint(reset_at)
        recovered = self._delegate_wait(reset_at)
        if recovered:
            return QuotaDecisionResult(action=QuotaDecision.RESUME, expected_resume=reset_at)
        return QuotaDecisionResult(action=QuotaDecision.WAIT, expected_resume=reset_at)

    def _persist_checkpoint(self, reset_at: datetime | None) -> None:
        """Persist the quota pause via the SHARED ``QuotaCheckpoint`` writer (FR-16)."""
        from devbench.quota import QuotaCheckpoint

        checkpoint = QuotaCheckpoint(
            reason=SUPERVISE_BILLING_CHANNEL,
            reset_at=reset_at,
            saved_at=datetime.now(UTC),
            session_name=self._session_name,
        )
        self._save_checkpoint(checkpoint, self._workspace_root)

    def _delegate_wait(self, reset_at: datetime | None) -> bool:
        """Run the SHARED ``quota.wait_for_reset`` to completion; return recovered.

        The shared primitive is async; the default ``run_wait`` is
        :func:`asyncio.run`. Tests that inject a synchronous ``wait_for_reset``
        fake also inject a synchronous ``run_wait`` (or rely on the fake returning
        a plain bool, in which case ``asyncio.run`` is bypassed).
        """
        result = self._wait_for_reset(
            reset_at=reset_at,
            poll_interval_seconds=self._poll_interval,
            max_wait_seconds=self._max_wait,
        )
        if asyncio.iscoroutine(result):
            runner = self._run_wait or asyncio.run
            return bool(runner(result))
        return bool(result)


def build_quota_waiter(
    *,
    patterns: DetectionPatterns,
    config: SuperviseConfig,
    workspace_root: Path,
    session_name: str,
) -> QuotaWaiter:
    """Wire a :class:`QuotaWaiter` to the REAL shared quota primitives (FR-15, AC-32).

    This is the production wiring: it imports and injects the SHARED
    ``quota.wait_for_reset`` / ``quota.detect_quota_error`` /
    ``quota.save_checkpoint`` and the SHARED ``cli._resolve_max_quota_resumes`` so
    the interactive path provably reuses the SDK path's primitives (no local
    copy). The wait cadence/window fall through to ``quota_handling`` defaults
    (Section 7.4) when the ``supervise.timeouts.quota_*`` overrides are unset.

    Args:
        patterns: The compiled :class:`DetectionPatterns`.
        config: The ``supervise`` config block.
        workspace_root: Workspace root.
        session_name: The session name.

    Returns:
        A production-wired :class:`QuotaWaiter`.
    """
    from devbench import quota
    from devbench.cli import _resolve_max_quota_resumes
    from devbench.config import RUNTIME_CONFIG

    qh = RUNTIME_CONFIG.quota_handling
    poll = config.timeouts.quota_poll_interval_seconds
    poll = poll if poll is not None else qh.poll_interval_seconds
    max_wait = config.timeouts.quota_max_wait_seconds
    max_wait = max_wait if max_wait is not None else qh.max_wait_seconds

    def _resolve_cap() -> int:
        # The supervise override (supervise.quota.max_quota_resumes), when set, is
        # exported into the env the shared resolver reads so a single resolver
        # (env > config > default) owns the precedence (no re-derive).
        override = config.quota.max_quota_resumes
        if override is not None:
            os.environ["DEVBENCH_MAX_QUOTA_RESUMES"] = str(override)
        return _resolve_max_quota_resumes()

    return QuotaWaiter(
        patterns=patterns,
        poll_interval_seconds=poll,
        max_wait_seconds=max_wait,
        wait_for_reset=quota.wait_for_reset,
        detect_quota_error=quota.detect_quota_error,
        resolve_max_resumes=_resolve_cap,
        save_checkpoint=quota.save_checkpoint,
        workspace_root=workspace_root,
        session_name=session_name,
    )


# ---------------------------------------------------------------------------
# Restart loop (Section 4.3, FR-12)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RestartBudget:
    """Bounds auto-restarts by ``supervise.restart.max_attempts`` (FR-12, Section 4.3).

    Attributes:
        max_attempts: Maximum auto-restart relaunches before faulting with
            ``restart-cap-exhausted``. ``0`` disables auto-restart entirely.
    """

    max_attempts: int

    def may_restart(self, *, attempts_used: int) -> bool:
        """Return True when another relaunch is within the bound."""
        return attempts_used < self.max_attempts


def build_resume_argv(
    *,
    claude_path: str,
    model: str,
    effort: str,
    plugin_dir: str,
    restart_config: SuperviseRestartConfig,
    claude_session_id: str | None,
) -> list[str]:
    """Assemble the relaunch argv with resume flags per ``resume_mode`` (Section 4.3).

    ``resume_mode == "resume"`` with a captured *claude_session_id* relaunches via
    ``--resume <id>`` (resuming the exact transcript). ``resume_mode ==
    "continue"`` (or ``"resume"`` with no captured id) relaunches via
    ``--continue`` (the most-recent session in the project). Delegates the argv
    shape to the Phase-2 :func:`build_claude_launch_argv` (no duplication).

    Args:
        claude_path: Resolved ``claude`` path.
        model: Resolved interactive model.
        effort: Resolved effort level.
        plugin_dir: Resolved ``--plugin-dir`` target.
        restart_config: The ``supervise.restart`` config (``resume_mode``).
        claude_session_id: The captured session id (or ``None``).

    Returns:
        The relaunch argv.
    """
    use_resume_id = restart_config.resume_mode == "resume" and bool(claude_session_id)
    return build_claude_launch_argv(
        claude_path=claude_path,
        model=model,
        effort=effort,
        plugin_dir=plugin_dir,
        resume_session_id=claude_session_id if use_resume_id else None,
        resume_continue=not use_resume_id,
    )


# ---------------------------------------------------------------------------
# __run event loop (Section 4.1 step 8, 4.6, 4.8, 4.9)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EventLoopResult:
    """The terminal result of :func:`run_supervise_event_loop` (FR-13, FR-27).

    Attributes:
        exit_code: The supervisor's exit code (``0`` clean, non-zero fault).
        final_state: The state-machine state at termination.
        exit_reason: The classified exit reason (registry + audit).
        restarts_used: Auto-restart relaunches consumed (FR-12).
        resumes_used: Quota resumes consumed (FR-15).
    """

    exit_code: int
    final_state: str
    exit_reason: str
    restarts_used: int = 0
    resumes_used: int = 0


# The combined PTY pattern set the loop watches each iteration, ordered so a
# fault/quota marker is checked before the benign working-activity prompt. Each
# entry maps a DetectionPatterns predicate to the outcome marker the loop's
# quota / fault handling consumes. (A crash surfaces as a non-zero child EOF, not
# as on-screen text, so it is handled by the exit-status path, not here.)
_PTY_WATCH_ORDER: tuple[tuple[str, str], ...] = (
    ("is_quota_limit", _OUTCOME_MARKER_QUOTA_LIMIT),
    ("is_circuit_breaker", _OUTCOME_MARKER_CIRCUIT_BREAKER),
    ("is_harness_block", _OUTCOME_MARKER_HARNESS_BLOCK),
)


def _classify_pty_text(patterns: DetectionPatterns, text: str) -> str | None:
    """Return the outcome marker a PTY *text* chunk matches, or ``None``.

    Checks fault/quota predicates first (so a fault is never masked by the
    working prompt). A bare working-activity match returns ``None`` (the loop
    treats it as ongoing activity, not a terminal).
    """
    for predicate_name, marker in _PTY_WATCH_ORDER:
        if getattr(patterns, predicate_name)(text):
            return marker
    if "ALL_DONE" in text:
        return _OUTCOME_MARKER_ALL_DONE
    if "NO_ACTIONABLE" in text:
        return _OUTCOME_MARKER_NO_ACTIONABLE
    return None


def run_supervise_event_loop(
    *,
    driver: PtyDriver,
    config: SuperviseConfig,
    quota_waiter: Any,
    log_poll: Callable[[], LogTailHit | None],
    relaunch: Callable[..., None],
    state_machine: SupervisorStateMachine | None = None,
) -> EventLoopResult:
    """Drive the post-kickoff supervise event loop to a terminal (FR-13, FR-27).

    Each iteration: poll the hybrid log-tail, then read the PTY for the next
    terminal/quota/restart/working signal, classify it (Section 4.6), and act:

    - clean -> ``completed-clean``, exit 0.
    - fault (crash / circuit-breaker / stop-reason / harness-block / prompt
      timeout) -> ``faulted``, exit non-zero (classified).
    - quota -> ``quota-waiting``; delegate to *quota_waiter*; on RESUME relaunch
      and continue; on FAULT (cap) ``faulted``; on WAIT keep waiting (NEVER an
      exit).
    - restart signal (child exit 42) -> ``restarting``; relaunch within
      ``supervise.restart.max_attempts`` else ``faulted`` (``restart-cap-exhausted``).

    Args:
        driver: The :class:`PtyDriver` wrapping the running child (kickoff done).
        config: The ``supervise`` config block.
        quota_waiter: A :class:`QuotaWaiter` (or compatible) for the quota path.
        log_poll: A zero-arg callable returning the next :class:`LogTailHit` or
            ``None`` (the hybrid log-tail; production passes
            :meth:`LogTailDetector.poll`).
        relaunch: A callable the loop invokes on a restart/quota-resume to
            re-spawn the child and re-run kickoff (keyword args carry the resume
            mode + reason). It must leave *driver* wrapping the fresh child.
        state_machine: The :class:`SupervisorStateMachine` to drive (a fresh one
            in the ``running`` state is created when ``None``).

    Returns:
        The terminal :class:`EventLoopResult`.
    """
    sm = state_machine or _running_state_machine()
    budget = RestartBudget(max_attempts=config.restart.max_attempts)
    idle_timeout = config.timeouts.idle_seconds
    restarts_used = 0
    resumes_used = 0

    while True:
        # Hybrid detection: the deterministic log markers are checked first.
        log_hit = log_poll()
        if log_hit is not None:
            log_result = _handle_log_hit(log_hit, sm, restarts_used, resumes_used)
            if log_result is not None:
                return log_result
            # A non-actionable (advisory) log hit falls through to the PTY read.

        try:
            observation = _observe_pty(driver, idle_timeout)
        except SupervisePromptTimeoutError:
            sm.on_event("fault")
            outcome = classify_supervise_outcome(
                marker=_OUTCOME_MARKER_PROMPT_TIMEOUT, child_exitstatus=None, phase="idle"
            )
            return _terminal(sm, outcome, restarts_used, resumes_used)
        terminal = _handle_pty_observation(
            observation, driver, sm, quota_waiter, budget, relaunch, restarts_used, resumes_used
        )
        result, restarts_used, resumes_used = terminal
        if result is not None:
            return result


def _running_state_machine() -> SupervisorStateMachine:
    """Return a state machine advanced to ``running`` (kickoff already happened)."""
    sm = SupervisorStateMachine()
    sm.on_event("ready")
    sm.on_event("orchestrate-injected")
    return sm


@dataclass(frozen=True)
class _PtyObservation:
    """One PTY read: matched terminal *marker* and/or the child *exitstatus*."""

    marker: str | None
    exitstatus: int | None
    eof: bool


def _observe_pty(driver: PtyDriver, idle_timeout: int) -> _PtyObservation:
    """Read the next PTY chunk and classify it (terminal marker / EOF / activity)."""
    text, eof, exitstatus = driver.read_chunk(timeout_seconds=idle_timeout)
    if eof:
        return _PtyObservation(marker=None, exitstatus=exitstatus, eof=True)
    marker = _classify_pty_text(driver.patterns, text)
    return _PtyObservation(marker=marker, exitstatus=None, eof=False)


def _handle_log_hit(
    hit: LogTailHit,
    sm: SupervisorStateMachine,
    restarts_used: int,
    resumes_used: int,
) -> EventLoopResult | None:
    """Act on a hybrid log-tail hit; return a terminal result or ``None``.

    A CLEAN log marker terminates the loop with a clean exit; a FAULT marker
    terminates with a classified non-zero exit. QUOTA / RESTART log markers are
    advisory here -- their authoritative handling is the PTY path (a quota PTY
    line or a child exit-42) -- so this returns ``None`` (the loop falls through
    to the PTY read). This keeps a single authoritative quota/restart code path.
    """
    if hit.kind is LogTailKind.CLEAN:
        sm.on_event("terminal-clean")
        return _terminal(sm, _outcome_clean("all-done"), restarts_used, resumes_used)
    if hit.kind is LogTailKind.FAULT:
        sm.on_event("fault")
        outcome = classify_supervise_outcome(marker=_OUTCOME_MARKER_STOP_REASON, child_exitstatus=None)
        return _terminal(sm, outcome, restarts_used, resumes_used)
    return None


def _handle_pty_observation(
    observation: _PtyObservation,
    driver: PtyDriver,
    sm: SupervisorStateMachine,
    quota_waiter: Any,
    budget: RestartBudget,
    relaunch: Callable[..., None],
    restarts_used: int,
    resumes_used: int,
) -> tuple[EventLoopResult | None, int, int]:
    """Act on a PTY observation; return (result|None, restarts, resumes)."""
    # A quota PTY marker (path 4.9a/4.9b): NEVER a non-zero exit.
    if observation.marker == _OUTCOME_MARKER_QUOTA_LIMIT:
        return _handle_quota(driver, sm, quota_waiter, relaunch, restarts_used, resumes_used)

    # A child EOF: exit-42 is the restart signal; else classify the exit status.
    if observation.eof:
        if observation.exitstatus == ORCHESTRATOR_RESTART_EXIT_CODE:
            return _handle_restart(sm, budget, relaunch, restarts_used, resumes_used)
        outcome = classify_supervise_outcome(marker=None, child_exitstatus=observation.exitstatus)
        sm.on_event("terminal-clean" if outcome.is_clean else "fault")
        return _terminal(sm, outcome, restarts_used, resumes_used), restarts_used, resumes_used

    # A fault PTY marker observed mid-session (circuit-breaker / harness-block).
    if observation.marker in (_OUTCOME_MARKER_CIRCUIT_BREAKER, _OUTCOME_MARKER_HARNESS_BLOCK):
        sm.on_event("fault")
        outcome = classify_supervise_outcome(marker=observation.marker, child_exitstatus=None)
        return _terminal(sm, outcome, restarts_used, resumes_used), restarts_used, resumes_used

    # A clean PTY marker observed without an EOF (the child will EOF next): record
    # working activity and keep reading until the child actually exits.
    with contextlib.suppress(SuperviseTransitionError):
        sm.on_event("working-activity")
    return None, restarts_used, resumes_used


def _handle_quota(
    driver: PtyDriver,
    sm: SupervisorStateMachine,
    quota_waiter: Any,
    relaunch: Callable[..., None],
    restarts_used: int,
    resumes_used: int,
) -> tuple[EventLoopResult | None, int, int]:
    """Handle a quota PTY marker (Section 4.9): wait, then resume / keep-waiting / fault.

    Quota is a non-terminal HOLDING state (Section 4.8): the only outbound
    transitions are ``quota-resumed``/``restarting`` (resume) or ``faulted``
    (resume cap). A ``WAIT`` decision (the wait window elapsed without the quota
    refreshing) is NOT an exit -- the supervisor stays in ``quota-waiting`` and
    re-delegates the wait to the SHARED ``quota.wait_for_reset`` (event-driven,
    no sleep). The loop is bounded: every re-wait that recovers consumes a resume
    (FR-15), so once the resume cap is reached this faults with
    ``quota-resume-cap-exhausted`` rather than looping forever.
    """
    sm.on_event("quota-detected")
    text = driver.last_text()
    reset_at = quota_waiter.parse_reset_at(text)
    while True:
        decision = quota_waiter.wait_and_decide(reset_at=reset_at, resumes_used=resumes_used)
        if decision.action is QuotaDecision.FAULT:
            sm.on_event("fault")
            outcome = classify_supervise_outcome(marker=_OUTCOME_MARKER_QUOTA_CAP, child_exitstatus=None)
            return _terminal(sm, outcome, restarts_used, resumes_used), restarts_used, resumes_used
        if decision.action is QuotaDecision.RESUME:
            # Poll-restart (4.9b): relaunch with resume flags; the same session id
            # is retained where possible (the in-session-wait path 4.9a is
            # best-effort until DI-5). quota-waiting -> restarting -> running.
            sm.on_event("session-exited-on-quota")
            relaunch(reason="quota-resume", resume=True)
            sm.on_event("restart-launched")
            logger.info("%s reason=quota-resume resumes=%d", SUPERVISE_RESTART_AUDIT_PREFIX, resumes_used + 1)
            return None, restarts_used, resumes_used + 1
        # WAIT: the window has not refreshed yet; re-delegate the bounded wait.
        logger.info("%s state=%s reason=quota-keep-waiting", SUPERVISE_STATE_AUDIT_PREFIX, sm.state)


def _handle_restart(
    sm: SupervisorStateMachine,
    budget: RestartBudget,
    relaunch: Callable[..., None],
    restarts_used: int,
    resumes_used: int,
) -> tuple[EventLoopResult | None, int, int]:
    """Handle a child exit-42 restart signal (Section 4.3): bounded relaunch."""
    if not budget.may_restart(attempts_used=restarts_used):
        sm.on_event("fault")
        outcome = classify_supervise_outcome(marker=_OUTCOME_MARKER_RESTART_CAP, child_exitstatus=None)
        return _terminal(sm, outcome, restarts_used, resumes_used), restarts_used, resumes_used
    sm.on_event("restart-signal")
    relaunch(reason="auto-restart", resume=True)
    sm.on_event("restart-launched")
    logger.info("%s reason=auto-restart attempt=%d", SUPERVISE_RESTART_AUDIT_PREFIX, restarts_used + 1)
    return None, restarts_used + 1, resumes_used


def _terminal(
    sm: SupervisorStateMachine,
    outcome: SuperviseOutcome,
    restarts_used: int,
    resumes_used: int,
) -> EventLoopResult:
    """Build the terminal :class:`EventLoopResult` from a classified *outcome*."""
    if not outcome.is_clean:
        logger.info("%s reason=%s", SUPERVISE_FAULT_AUDIT_PREFIX, outcome.exit_reason)
    logger.info("%s state=%s reason=%s", SUPERVISE_STATE_AUDIT_PREFIX, sm.state, outcome.exit_reason)
    return EventLoopResult(
        exit_code=outcome.exit_code if outcome.exit_code is not None else SUPERVISE_FAULT_EXIT_CODE,
        final_state=sm.state,
        exit_reason=outcome.exit_reason,
        restarts_used=restarts_used,
        resumes_used=resumes_used,
    )


# ===========================================================================
# Phase 4 -- read-only observation helpers (status / info / attach follow)
# ===========================================================================
#
# These are PURE (status-line formatting, screen-ls parsing, info reconcile) or
# I/O-bounded-by-an-injected-predicate (the PTY-log follow) so they are fully
# unit-testable without a live screen, and the CLI ``status``/``info``/``attach``
# verbs only wire them onto the registry. The follow is event-driven (re-read on
# a readiness predicate), never ``time.sleep`` (CLAUDE.md, Section 7.5).


# ---------------------------------------------------------------------------
# status -- per-session line (Section 4.4, FR-9, FR-10)
# ---------------------------------------------------------------------------


def _format_dt(value: datetime | None) -> str:
    """Render a UTC datetime as ``YYYY-MM-DDTHH:MM:SSZ`` or ``(none)`` when unset."""
    if value is None:
        return "(none)"
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_status_line(state: SuperviseSessionState, *, max_resumes: int, in_progress: str | None) -> str:
    """Render the one-line ``status`` view for one session (FR-9, FR-10).

    Always surfaces ``name``, ``state``, ``billing-channel`` (the
    subscription-vs-API audit, Section 0.2), the current claimed work unit
    (``in-progress``, read from the backlog by the caller), ``last-activity``,
    ``screen`` and ``claude-session``. When ``state == quota-waiting`` the line
    additionally carries ``expected-resume`` (the parsed provider reset time) and
    ``resumes-used=<n>/<cap>`` (FR-10/FR-16). A stopped/faulted session surfaces
    its ``exit-reason``.

    Args:
        state: The persisted :class:`SuperviseSessionState`.
        max_resumes: The resolved quota-resume cap (for the ``resumes-used``
            denominator); the caller passes ``cli._resolve_max_quota_resumes()``
            so the cap precedence is owned by the shared resolver (DRY).
        in_progress: The current claimed work-unit id (or ``None``).

    Returns:
        A single status line.
    """
    parts = [
        f"name={state.name}",
        f"state={state.state}",
        f"billing-channel={state.billing_channel}",
        f"in-progress={in_progress if in_progress else '(none)'}",
        f"last-activity={_format_dt(state.last_activity)}",
        f"screen={state.screen_name}",
        f"claude-session={state.claude_session_id if state.claude_session_id else '(none)'}",
    ]
    if state.state == SUPERVISE_STATE_QUOTA_WAITING:
        parts.append(f"expected-resume={_format_dt(state.expected_resume)}")
        parts.append(f"resumes-used={state.resumes_used}/{max_resumes}")
    if state.exit_reason:
        parts.append(f"exit-reason={state.exit_reason}")
    return "  ".join(parts)


# ---------------------------------------------------------------------------
# info -- screen -ls join + reconcile (Section 4.5, FR-11)
# ---------------------------------------------------------------------------


# ``screen -ls`` rows look like ``\t<pid>.<session-name>\t(Detached)``. The
# session name is the dot-suffix of the first whitespace-delimited token. This
# regex centralizes that parse so a screen-output format quirk is fixed in one
# place (mirrors the centralized-detection-patterns principle, Section 6.3).
_SCREEN_LS_ROW_RE = re.compile(r"^\s*\d+\.(?P<name>\S+)\s")


def parse_screen_ls(output: str) -> set[str]:
    """Parse ``screen -ls`` *output* into the set of live screen session names (FR-11).

    Only the ``<pid>.<name>`` socket rows contribute; the header/footer lines
    (``There are screens on:``, ``N Sockets in ...``) are ignored. Returns an
    empty set when no screens are listed.

    Args:
        output: The combined stdout/stderr of ``screen -ls``.

    Returns:
        The set of screen session names (the part after ``<pid>.``).
    """
    names: set[str] = set()
    for line in output.splitlines():
        match = _SCREEN_LS_ROW_RE.match(line)
        if match is not None:
            names.add(match.group("name"))
    return names


@dataclass(frozen=True)
class InfoRow:
    """One reconciled ``supervise info`` row (Section 4.5, FR-11).

    Attributes:
        screen: The ``screen`` session name (``<prefix><name>``).
        name: The supervise session name.
        state: The lifecycle state, or ``unknown`` (orphan screen, no registry
            entry) / ``stale`` (registry entry, no live screen).
        pid: The supervisor PID (``None`` for an orphan screen).
        claude_session: The captured claude session id (``None`` when unknown).
        billing: The billing channel (always ``subscription`` for a known entry).
        attach: The exact ``supervise attach --name N`` command to observe it.
    """

    screen: str
    name: str
    state: str
    pid: int | None
    claude_session: str | None
    billing: str
    attach: str


def _attach_command(name: str) -> str:
    """Return the exact operator command that read-only-attaches *name* (FR-11)."""
    return f"supervise attach --name {name}"


def reconcile_info_rows(
    *,
    sessions: list[SuperviseSessionState],
    screen_names: set[str],
    prefix: str,
) -> list[InfoRow]:
    """Join registry *sessions* with live *screen_names* into ``info`` rows (FR-11).

    Reconciliation (Section 4.5):

    - A registry session whose screen IS live -> its persisted state.
    - A registry session whose screen is ABSENT -> ``state=stale``.
    - A live screen (matching *prefix*) with NO registry entry -> ``state=unknown``
      (an orphan), surfaced so the operator can clean it up.

    Screens not matching *prefix* are ignored (they are not supervise screens).
    Rows are sorted by session name for deterministic output.

    Args:
        sessions: The :class:`SuperviseRegistry` sessions.
        screen_names: The live screen session names (from :func:`parse_screen_ls`).
        prefix: The configured ``supervise.screen_name_prefix``.

    Returns:
        The reconciled rows, sorted by ``name``.
    """
    rows: list[InfoRow] = []
    known_screens = {s.screen_name for s in sessions}

    for state in sessions:
        live = state.screen_name in screen_names
        display_state = state.state if live else SUPERVISE_INFO_STATE_STALE
        rows.append(
            InfoRow(
                screen=state.screen_name,
                name=state.name,
                state=display_state,
                pid=state.pid,
                claude_session=state.claude_session_id,
                billing=state.billing_channel,
                attach=_attach_command(state.name),
            )
        )

    for screen_name in screen_names:
        if screen_name in known_screens or not screen_name.startswith(prefix):
            continue
        orphan_name = screen_name[len(prefix) :]
        rows.append(
            InfoRow(
                screen=screen_name,
                name=orphan_name,
                state=SUPERVISE_INFO_STATE_UNKNOWN,
                pid=None,
                claude_session=None,
                billing=SUPERVISE_BILLING_CHANNEL,
                attach=_attach_command(orphan_name),
            )
        )

    return sorted(rows, key=lambda r: r.name)


# ---------------------------------------------------------------------------
# attach -- read-only PTY-log follow (Section 4.7, FR-26)
# ---------------------------------------------------------------------------


def follow_pty_log(
    log_path: Path,
    *,
    write: Callable[[str], None],
    should_continue: Callable[[], bool],
    wait_for_log: bool = False,
) -> None:
    """Follow the redacted ``pty.log`` read-only, streaming only NEW bytes (FR-26).

    This is the ALWAYS-SAFE attach mechanism (Section 4.7): a pure read of a file
    the ``__run`` supervisor writes. The attaching process's stdin is NEVER wired
    to the ``claude`` TTY, so an observer cannot inject input or steal the PTY.

    The loop is event-driven and bounded by *should_continue* (the CLI passes a
    predicate that returns ``False`` on ``KeyboardInterrupt``); it NEVER uses
    ``time.sleep`` -- each iteration reads any bytes appended since the last
    iteration via a byte offset, then consults *should_continue* to decide whether
    to keep following (CLAUDE.md, Section 7.5).

    Args:
        log_path: Absolute ``pty.log`` path.
        write: Sink for each new chunk of transcript text (the CLI passes a stdout
            writer).
        should_continue: Zero-arg predicate; the loop runs while it returns
            ``True``. It is the readiness gate (the CLI wires it to the live
            terminal so a ``KeyboardInterrupt`` ends the follow).
        wait_for_log: When ``True``, a not-yet-created log is tolerated (the
            ``__run`` supervisor may not have flushed the first chunk); the loop
            keeps polling until it appears. When ``False`` (the default), an
            absent log fails fast (FR-30).

    Raises:
        FileNotFoundError: *log_path* does not exist and ``wait_for_log`` is False.
    """
    if not wait_for_log and not log_path.exists():
        raise FileNotFoundError(f"no PTY transcript log to follow at '{log_path}'")

    offset = 0
    while should_continue():
        if not log_path.exists():
            continue
        data = log_path.read_text(encoding="utf-8", errors="replace")
        if len(data) < offset:
            # Truncated/rotated: re-follow from the new start.
            offset = 0
        if len(data) > offset:
            write(data[offset:])
            offset = len(data)
