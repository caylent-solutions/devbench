"""Logging configuration for the judges system.

Sets up dual output: stderr (for real-time visibility in Claude Code) and a
persistent log file (for history and review). Logging deliberately goes to
stderr, not stdout, so commands like ``devbench report`` and ``devbench
get-diff`` can be piped or redirected without log lines polluting the data
stream.

The log file path is configurable via the ``JUDGE_LOG_FILE`` environment
variable. Defaults to ``judges/logs/orchestrator.log``.
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
        return Path(os.environ.get("JUDGE_LOG_FILE", _DEFAULT_LOG_FILE))

    if level is None:
        env_level = os.environ.get("JUDGE_LOG_LEVEL", DEFAULT_LOG_LEVEL).upper()
        level = getattr(logging, env_level, logging.INFO)

    log_file = Path(os.environ.get("JUDGE_LOG_FILE", _DEFAULT_LOG_FILE))
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
