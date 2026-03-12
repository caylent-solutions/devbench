"""Logging configuration for the judges system.

Sets up dual output: stdout (for real-time visibility in Claude Code)
and a persistent log file (for history and review).

The log file path is configurable via the ``JUDGE_LOG_FILE`` environment
variable. Defaults to ``judges/logs/orchestrator.log``.
"""

import logging
import os
import sys
from pathlib import Path

_LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

_DEFAULT_LOG_DIR = Path(__file__).resolve().parent / "logs"
_DEFAULT_LOG_FILE = str(_DEFAULT_LOG_DIR / "orchestrator.log")

_configured = False


def setup_logging(level: int | None = None) -> Path:
    """Configure logging with stdout and file handlers.

    The log level is resolved from (in order):
    1. The ``level`` argument if provided.
    2. The ``JUDGE_LOG_LEVEL`` environment variable (standard level names,
       e.g. ``DEBUG``, ``INFO``, ``WARNING``).
    3. ``INFO`` as the default.

    Returns the path to the log file.

    Safe to call multiple times — only configures on the first call.
    """
    global _configured  # noqa: PLW0603
    if _configured:
        return Path(os.environ.get("JUDGE_LOG_FILE", _DEFAULT_LOG_FILE))

    if level is None:
        env_level = os.environ.get("JUDGE_LOG_LEVEL", "INFO").upper()
        level = getattr(logging, env_level, logging.INFO)

    log_file = Path(os.environ.get("JUDGE_LOG_FILE", _DEFAULT_LOG_FILE))
    log_file.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Clear any existing handlers (prevents duplicates on re-import)
    root_logger.handlers.clear()

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT)

    # Stdout handler — real-time visibility in Claude Code terminal
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(level)
    stdout_handler.setFormatter(formatter)
    root_logger.addHandler(stdout_handler)

    # File handler — persistent log for review
    file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    _configured = True
    logging.getLogger("judges.log_setup").info("Logging to stdout and %s", log_file)
    return log_file
