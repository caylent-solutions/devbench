#!/usr/bin/env python3
"""A fake ``claude`` CLI for the supervise functional layer (Section 10.0).

This executable stands in for the real ``claude`` interactive CLI so the pexpect
supervisor can be driven end to end with NO real subscription, NO network, and a
deterministic, scriptable transcript. It is exercised fully in Phase 5; Phase 2
introduces it and asserts its observable contract (ready prompt + slash-command
echo + scripted terminal output) so the functional layer can build on it.

Behavior (all input-driven, nothing hardcoded into the supervisor):

- Prints a ready prompt (``STUB_CLAUDE_READY_PROMPT``, default ``> ``) so the
  supervisor's ``ready_prompt`` detection fires.
- Reads slash commands line-by-line from stdin and echoes each as
  ``[stub-claude] received: <line>`` so the supervisor's command-ack detection
  fires on the configured ``working_prompt`` pattern.
- After the kickoff line (``STUB_CLAUDE_KICKOFF``, default
  ``/devbench-orchestrate:orchestrate``) is received, emits the scripted terminal
  output named by ``STUB_CLAUDE_SCRIPT`` and exits with ``STUB_CLAUDE_EXIT_CODE``:
    - ``clean``  -> prints ``ALL_DONE`` then exits 0.
    - ``no_actionable`` -> prints ``NO_ACTIONABLE`` then exits 0.
    - ``crash``  -> prints a traceback-like line then exits 1 (or the configured code).
    - ``quota``  -> prints a quota-limit line + a ``resets H:MMam (UTC)`` line,
      then exits with the configured code (the poll-restart path 4.9b input).
    - ``restart`` -> prints the auto-restart marker then exits 42.

Every behavioral knob is an environment variable so a test parametrizes the stub
without editing it (CLAUDE.md: input-driven, no hardcoded test data).
"""

from __future__ import annotations

import os
import sys


def _emit(text: str) -> None:
    """Write *text* + newline to stdout and flush so the PTY sees it at once."""
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _terminal_output(script: str) -> str:
    """Return the terminal transcript line for the named *script*."""
    table = {
        "clean": "ALL_DONE",
        "no_actionable": "NO_ACTIONABLE",
        "crash": "Traceback (most recent call last): fatal error in stub-claude",
        "restart": "[ORCHESTRATOR_AUTO_RESTART] reason=runtime_degradation tasks=1",
    }
    if script == "quota":
        reset_line = os.environ.get("STUB_CLAUDE_QUOTA_RESET_LINE", "resets 8:00am (UTC)")
        return "You've hit your limit\n" + reset_line
    return table.get(script, "ALL_DONE")


def main() -> int:
    """Run the stub interactive session; return the configured exit code."""
    ready_prompt = os.environ.get("STUB_CLAUDE_READY_PROMPT", "> ")
    kickoff = os.environ.get("STUB_CLAUDE_KICKOFF", "/devbench-orchestrate:orchestrate")
    script = os.environ.get("STUB_CLAUDE_SCRIPT", "clean")
    exit_code = int(os.environ.get("STUB_CLAUDE_EXIT_CODE", "0"))

    _emit(ready_prompt)

    for raw in sys.stdin:
        line = raw.rstrip("\n")
        _emit(f"[stub-claude] received: {line}")
        if line.strip() == kickoff:
            _emit(_terminal_output(script))
            return exit_code

    # stdin closed before the kickoff arrived: exit cleanly so the supervisor
    # observes an EOF rather than hanging (the supervisor's timeout still bounds
    # this path).
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
