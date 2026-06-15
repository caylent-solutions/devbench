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

The quota wait-and-resume adapter, restart loop, and the read-only verbs land in
later phases per ``IMPLEMENTATION-PLAN.md``.

All path/string literals are sourced from :mod:`devbench.constants`; no strings
are hard-coded in this module.
"""

from __future__ import annotations

import contextlib
import json
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
    SUPERVISE_ALWAYS_DENY_ENV_VARS,
    SUPERVISE_BASE_DIR,
    SUPERVISE_BILLING_CHANNEL,
    SUPERVISE_EFFORT_DEFAULT,
    SUPERVISE_PTY_LOG_FILENAME,
    SUPERVISE_REGISTRY_PATH,
    SUPERVISE_REGISTRY_TMP_SUFFIX,
    SUPERVISE_SCREEN_NAME_PREFIX_DEFAULT,
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
    from devbench.config_loader import SuperviseDetectionPatternsConfig

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
