"""Logging configuration for the judges system.

Sets up dual output: stderr (for real-time visibility in Claude Code) and a
persistent log file (for history and review). Logging deliberately goes to
stderr, not stdout, so commands like ``devbench report`` and ``devbench
get-diff`` can be piped or redirected without log lines polluting the data
stream.

The log file path resolves with this precedence (matches
``cli._resolve_log_file_path`` so the orchestrator-as-writer and the
report-as-reader cannot diverge):

1. ``JUDGE_LOG_FILE`` environment variable.
2. ``log_file`` field in ``backlog/config/devbench.yaml`` (resolved
   relative to ``JUDGE_WORKSPACE_ROOT``).
3. ``<JUDGE_WORKSPACE_ROOT>/logs/orchestrator.log`` convention.
4. ``<devbench source tree>/logs/orchestrator.log`` legacy fallback for
   invocations outside any workspace (test fixtures, local dev).
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
    explicit = os.environ.get("JUDGE_LOG_FILE", "").strip()
    if explicit:
        return Path(explicit)
    workspace = os.environ.get("JUDGE_WORKSPACE_ROOT", "").strip()
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


def setup_logging(level: int | None = None) -> Path:
    """Configure logging with stdout and file handlers.

    The log level is resolved from (in order):
    1. The ``level`` argument if provided.
    2. The ``JUDGE_LOG_LEVEL`` environment variable (standard level names,
       e.g. ``DEBUG``, ``INFO``, ``WARNING``).
    3. ``INFO`` as the default.

    Returns the path to the log file.

    Safe to call multiple times -- only configures on the first call.
    """
    if _state[0]:
        return _resolve_log_file()

    if level is None:
        env_level = os.environ.get("JUDGE_LOG_LEVEL", DEFAULT_LOG_LEVEL).upper()
        level = getattr(logging, env_level, logging.INFO)

    log_file = _resolve_log_file()
    log_file.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clear any existing handlers (prevents duplicates on re-import)
    root_logger.handlers.clear()

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    # Stderr handler -- real-time visibility in Claude Code terminal without
    # polluting stdout for commands that emit data (report, get-diff, status).
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(level)
    stderr_handler.setFormatter(formatter)
    root_logger.addHandler(stderr_handler)

    # File handler -- persistent log for review
    file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    _state[0] = True
    logging.getLogger("judges.log_setup").info("Logging to stderr and %s", log_file)
    return log_file
