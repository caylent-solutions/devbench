"""Logging configuration for the judges system.

Sets up dual output: stderr (for real-time visibility in Claude Code) and a
persistent log file (for history and review). Logging deliberately goes to
stderr, not stdout, so commands like ``devbench report`` and ``devbench
get-diff`` can be piped or redirected without log lines polluting the data
stream.

The log file path resolves with this precedence (matches
``cli._resolve_log_file_path`` so the orchestrator-as-writer and the
report-as-reader cannot diverge):

1. ``DEVBENCH_LOG_FILE`` environment variable.
2. ``log_file`` field in ``backlog/config/devbench.yaml`` (resolved
   relative to ``DEVBENCH_WORKSPACE_ROOT``).
3. ``<DEVBENCH_WORKSPACE_ROOT>/logs/orchestrator.log`` convention.
4. ``<devbench source tree>/logs/orchestrator.log`` legacy fallback for
   invocations outside any workspace (test fixtures, local dev).

Per-session routing (spec 4.4.4, AC-192-14):
When ``DEVBENCH_SESSION_NAME`` is set, ``setup_logging`` attaches a second
``FileHandler`` routing to
``<DEVBENCH_WORKSPACE_ROOT>/<SESSION_SESSIONS_BASE_DIR>/<name>/orchestrator.log``
in addition to the aggregate log above.  Both handlers are active simultaneously
so messages appear in both the per-session log and the global aggregate.
``DEVBENCH_WORKSPACE_ROOT`` MUST be set when ``DEVBENCH_SESSION_NAME`` is set;
if it is absent ``_resolve_session_log_file`` raises ``RuntimeError`` with an
actionable message.
"""

import logging
import os
import sys
from pathlib import Path

from devbench.constants import (
    DEFAULT_LOG_FILENAME,
    DEFAULT_LOG_LEVEL,
    DEFAULT_LOG_SUBDIR,
    LOG_DATE_FORMAT,
    LOG_FORMAT,
    SESSION_SESSIONS_BASE_DIR,
)

_DEFAULT_LOG_DIR = Path(__file__).resolve().parent / DEFAULT_LOG_SUBDIR
_DEFAULT_LOG_FILE = str(_DEFAULT_LOG_DIR / DEFAULT_LOG_FILENAME)

_state = [False]


def _resolve_log_file() -> Path:
    """Compute the log path using the same chain as ``cli._resolve_log_file_path``.

    Single source of truth so the writer (this function, called by
    ``setup_logging``) and the reader (``cmd_report``) cannot disagree.
    Imports ``RUNTIME_CONFIG`` lazily to avoid a circular import: this
    module is imported by ``config.py`` indirectly through ``cli.py``.
    The lazy import absorbs ``ImportError`` / ``RuntimeError`` so the
    logger keeps working even when the config layer is unavailable
    (test fixtures, ``--help`` paths, very early bootstrap).
    """
    explicit = os.environ.get("DEVBENCH_LOG_FILE", "").strip()
    if explicit:
        return Path(explicit)
    workspace = os.environ.get("DEVBENCH_WORKSPACE_ROOT", "").strip()
    configured = ""
    try:
        from devbench.config import RUNTIME_CONFIG

        configured = (RUNTIME_CONFIG.log_file or "").strip()
    except (ImportError, RuntimeError, AttributeError):
        configured = ""
    if configured:
        configured_path = Path(configured)
        if configured_path.is_absolute():
            return configured_path
        if workspace:
            return Path(workspace) / configured_path
        return configured_path
    if workspace:
        return Path(workspace) / DEFAULT_LOG_SUBDIR / DEFAULT_LOG_FILENAME
    return Path(_DEFAULT_LOG_FILE)


def _resolve_session_log_file() -> Path | None:
    """Return the per-session log path when ``DEVBENCH_SESSION_NAME`` is set.

    Returns ``None`` when the env var is absent or empty -- callers must skip
    attaching a session FileHandler in that case.

    Raises:
        RuntimeError: When ``DEVBENCH_SESSION_NAME`` is set but
            ``DEVBENCH_WORKSPACE_ROOT`` is absent or empty.  Both env vars must
            be present together; missing ``DEVBENCH_WORKSPACE_ROOT`` is an
            operator configuration error that must fail loudly.
    """
    session_name = os.environ.get("DEVBENCH_SESSION_NAME", "").strip()
    if not session_name:
        return None
    workspace = os.environ.get("DEVBENCH_WORKSPACE_ROOT", "").strip()
    if not workspace:
        raise RuntimeError(
            "DEVBENCH_WORKSPACE_ROOT must be set when DEVBENCH_SESSION_NAME is set. "
            "The per-session log cannot be routed without a workspace root. "
            "Set DEVBENCH_WORKSPACE_ROOT to the devbench workspace directory."
        )
    return Path(workspace) / SESSION_SESSIONS_BASE_DIR / session_name / DEFAULT_LOG_FILENAME


def setup_logging(level: int | None = None) -> Path:
    """Configure logging with stdout and file handlers.

    The log level is resolved from (in order):
    1. The ``level`` argument if provided.
    2. The ``DEVBENCH_LOG_LEVEL`` environment variable (standard level names,
       e.g. ``DEBUG``, ``INFO``, ``WARNING``).
    3. ``INFO`` as the default.

    When ``DEVBENCH_SESSION_NAME`` is set, attaches a second ``FileHandler``
    routing to the per-session log in addition to the aggregate log
    (spec 4.4.4, AC-192-14).  ``DEVBENCH_WORKSPACE_ROOT`` must also be set in
    that case; see ``_resolve_session_log_file`` for the contract.

    Returns the path to the aggregate log file.

    Safe to call multiple times -- only configures on the first call.

    Raises:
        RuntimeError: Propagated from ``_resolve_session_log_file`` when
            ``DEVBENCH_SESSION_NAME`` is set but ``DEVBENCH_WORKSPACE_ROOT`` is
            missing.
    """
    if _state[0]:
        return _resolve_log_file()

    if level is None:
        env_level = os.environ.get("DEVBENCH_LOG_LEVEL", DEFAULT_LOG_LEVEL).upper()
        level = getattr(logging, env_level, logging.INFO)

    log_file = _resolve_log_file()
    log_file.parent.mkdir(parents=True, exist_ok=True)

    session_log_file = _resolve_session_log_file()

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    root_logger.handlers.clear()

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(level)
    stderr_handler.setFormatter(formatter)
    root_logger.addHandler(stderr_handler)

    file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    if session_log_file is not None and session_log_file != log_file:
        session_log_file.parent.mkdir(parents=True, exist_ok=True)
        session_handler = logging.FileHandler(str(session_log_file), encoding="utf-8")
        session_handler.setLevel(level)
        session_handler.setFormatter(formatter)
        root_logger.addHandler(session_handler)

    _state[0] = True
    logging.getLogger("judges.log_setup").debug("Logging to stderr and %s", log_file)
    return log_file
