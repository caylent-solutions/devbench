"""Shared diagnostics for commands that parse the backlog index.

Issue #305: ``devbench report`` handled a missing work-unit file and a
malformed index with actionable messages and a non-zero exit, while
``devbench status`` let the same exceptions escape as a raw traceback. Both
read the same index through the same parser, so an operator running both saw
a crash and a clean error for one underlying condition and could not tell
which reflected reality. A traceback is also poor input for an autonomous
agent: it says nothing about whether to re-run or to repair, which invites
improvised repair of a backlog that may simply have been mid-write.

The handler lives here, rather than in either command, so the two cannot
drift apart again and any further index-reading command inherits the same
behaviour by construction.
"""

from __future__ import annotations

import sys
from pathlib import Path


def exit_with_index_error(command: str, backlog_index: Path, exc: Exception) -> None:
    """Write an actionable diagnostic for an index-parse failure and exit non-zero.

    Distinguishes the two failure shapes the parser raises, because the
    operator's next action differs:

    - ``FileNotFoundError`` names either the index itself or a work-unit
      file it references. The message reports the actual missing path, so
      the diagnostic stops blaming the index when the real cause is a
      transient writer-window race on one work-unit file. SDK-driven
      ``Write`` / ``Edit`` tools operating outside ``BacklogManager`` can
      leave a file momentarily unreadable; the parser already retries once,
      and this tells the caller to re-run if even the retry lost.
    - ``ValueError`` means the index parsed but is malformed, which a re-run
      will not fix, so the message points at ``validate-backlog``.

    Args:
        command: Command name used to prefix the message, e.g. ``"status"``.
        backlog_index: Path to ``BACKLOG.md``, named in the message so the
            operator knows which index was being read.
        exc: The exception raised by ``BacklogParser.parse_index``.

    Raises:
        SystemExit: Always, with code 1. Callers use this as their failure
            path; it never returns.
    """
    if isinstance(exc, FileNotFoundError):
        missing = getattr(exc, "filename", None) or str(exc)
        sys.stderr.write(
            f"devbench {command}: cannot read '{missing}' (referenced by '{backlog_index}'): {exc}\n"
            "  If the missing path is a work-unit md and your orchestrator is\n"
            "  active, this may be a transient writer-window race; re-run.\n"
            "  Otherwise run `devbench validate-backlog` for a full index audit.\n"
        )
    else:
        sys.stderr.write(
            f"devbench {command}: cannot parse '{backlog_index}': {exc}\n"
            "  Run `devbench validate-backlog` for a full list of issues with the index.\n"
        )
    sys.exit(1)
