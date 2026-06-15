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

Each named script carries a DEFAULT exit code (``clean``/``no_actionable`` -> 0,
``crash`` -> 1, ``quota`` -> ``STUB_CLAUDE_QUOTA_EXIT_CODE`` (default 1), ``restart``
-> 42); ``STUB_CLAUDE_EXIT_CODE`` overrides the default for the single-script form.

Multi-launch sequences (auto-restart / quota-resume): the supervisor RELAUNCHES the
same ``claude`` invocation across exit-42 / quota events. To drive those flows
deterministically, ``STUB_CLAUDE_SCRIPT_SEQUENCE`` is a comma-separated list of scripts;
each launch consumes the NEXT entry (the last entry repeats once the list is exhausted),
the position tracked in ``STUB_CLAUDE_STATE_FILE``. Example
``STUB_CLAUDE_SCRIPT_SEQUENCE=restart,clean`` exits 42 on the first launch then ALL_DONE
on the relaunch, so a test can assert a bounded auto-restart that RECOVERS.

Every behavioral knob is an environment variable so a test parametrizes the stub
without editing it (CLAUDE.md: input-driven, no hardcoded test data).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Default exit code per named script (the single-script ``STUB_CLAUDE_EXIT_CODE``
#: env var overrides it; a sequence entry uses the default for its script).
_SCRIPT_DEFAULT_EXIT_CODE = {
    "clean": 0,
    "no_actionable": 0,
    "crash": 1,
    "restart": 42,
}


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


def _script_exit_code(script: str) -> int:
    """Return the exit code for *script*.

    ``quota`` reads ``STUB_CLAUDE_QUOTA_EXIT_CODE`` (default 1: a quota-classified
    non-zero exit drives the poll-restart path 4.9b). Other scripts use the
    per-script default table.
    """
    if script == "quota":
        return int(os.environ.get("STUB_CLAUDE_QUOTA_EXIT_CODE", "1"))
    return _SCRIPT_DEFAULT_EXIT_CODE.get(script, 0)


def _resolve_launch() -> tuple[str, int]:
    """Resolve this launch's (script, exit_code) from the single or sequence form.

    Single form: ``STUB_CLAUDE_SCRIPT`` (default ``clean``) with ``STUB_CLAUDE_EXIT_CODE``
    overriding the per-script default when set. Sequence form: the next entry of
    ``STUB_CLAUDE_SCRIPT_SEQUENCE`` (position persisted in ``STUB_CLAUDE_STATE_FILE``);
    the last entry repeats once the list is exhausted. The sequence form ignores
    ``STUB_CLAUDE_EXIT_CODE`` so each entry uses its own per-script exit code.
    """
    sequence = os.environ.get("STUB_CLAUDE_SCRIPT_SEQUENCE", "").strip()
    if sequence:
        scripts = [s.strip() for s in sequence.split(",") if s.strip()]
        index = _next_sequence_index(len(scripts))
        script = scripts[min(index, len(scripts) - 1)]
        return script, _script_exit_code(script)

    script = os.environ.get("STUB_CLAUDE_SCRIPT", "clean")
    override = os.environ.get("STUB_CLAUDE_EXIT_CODE")
    exit_code = int(override) if override is not None else _script_exit_code(script)
    return script, exit_code


def _next_sequence_index(length: int) -> int:
    """Return this launch's 0-based sequence index, advancing the state file.

    The state file holds the count of launches consumed so far; this launch reads
    it as its index and writes back ``index + 1``. A missing/blank/corrupt file is
    treated as the first launch (index 0) -- a fresh sequence, not a silent error,
    because each test uses a fresh temp state file.
    """
    state_file = os.environ.get("STUB_CLAUDE_STATE_FILE")
    if not state_file:
        return 0
    state_path = Path(state_file)
    try:
        index = int(state_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        index = 0
    state_path.write_text(str(index + 1), encoding="utf-8")
    return index


def main() -> int:
    """Run the stub interactive session; return the configured exit code.

    The ``idle`` script models a session that stays alive AFTER the kickoff (it
    emits no terminal sentinel) and exits 0 only when it receives the drain command
    (``STUB_CLAUDE_DRAIN_COMMAND``, default ``/exit``). This drives the graceful-stop
    path (Section 4.2): the supervisor injects ``/exit`` and reads to the child EOF.
    Every other script emits its terminal sentinel immediately on the kickoff and
    exits with its resolved code.
    """
    ready_prompt = os.environ.get("STUB_CLAUDE_READY_PROMPT", "> ")
    kickoff = os.environ.get("STUB_CLAUDE_KICKOFF", "/devbench-orchestrate:orchestrate")
    drain_command = os.environ.get("STUB_CLAUDE_DRAIN_COMMAND", "/exit")
    script, exit_code = _resolve_launch()

    _emit(ready_prompt)

    kickoff_seen = False
    for raw in sys.stdin:
        line = raw.rstrip("\n")
        _emit(f"[stub-claude] received: {line}")
        stripped = line.strip()
        if script == "idle":
            # Stay alive until the drain command arrives (the operator stop path).
            if kickoff_seen and stripped == drain_command:
                return exit_code
            if stripped == kickoff:
                kickoff_seen = True
            continue
        if stripped == kickoff:
            _emit(_terminal_output(script))
            return exit_code

    # stdin closed before the kickoff/drain arrived: exit cleanly so the supervisor
    # observes an EOF rather than hanging (the supervisor's timeout still bounds
    # this path).
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
