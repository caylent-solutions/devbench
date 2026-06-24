"""Shared subprocess utility for running shell commands.

This module provides a standalone ``run_command`` function that wraps
``subprocess.run`` with consistent error handling for missing executables
and timeouts.  It is intentionally free of judge-specific dependencies so
that non-judge components can use it without inheriting the full judge
contract.
"""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Callable
from pathlib import Path

from devbench.config import COMMAND_TIMEOUT, OUTPUT_TRUNCATION_LIMIT
from devbench.constants import SUBPROCESS_ERROR_EXIT_CODE

_OUTPUT_TRUNCATED_TEMPLATE = "\n[OUTPUT_TRUNCATED: {n} bytes elided, kept head+tail within {max} bytes]\n"


def bound_output(text: str, *, max_bytes: int) -> str:
    """Return *text* bounded to roughly *max_bytes* characters, head + tail kept.

    A runaway command (e.g. ``terragrunt run --all`` emitting hundreds of
    errors) can produce a multi-megabyte capture that, ingested unbounded, can
    wedge transcript processing or balloon memory.  This caps the captured
    output so the firehose can never form: the leading **head** window (the
    run's setup / first errors) and the trailing **tail** window (the final
    summary / exit error) are both retained, with the dropped middle replaced
    by a single ``[OUTPUT_TRUNCATED ...]`` marker that records the elided byte
    count.

    A non-positive *max_bytes* disables bounding (the documented "off"
    sentinel, mirroring :func:`devbench.verification.trim_log`).  Text already
    within budget is returned verbatim, byte-for-byte.

    Args:
        text: The captured stdout or stderr to bound.
        max_bytes: Character budget.  ``<= 0`` returns *text* unchanged.

    Returns:
        *text* unchanged when within budget (or bounding is disabled); otherwise
        ``head + marker + tail`` where head and tail each get roughly half the
        budget and the marker records how many characters were elided.
    """
    if max_bytes <= 0 or len(text) <= max_bytes:
        return text
    tail_budget = max_bytes // 2
    head_budget = max_bytes - tail_budget
    head = text[:head_budget]
    tail = text[len(text) - tail_budget :] if tail_budget else ""
    elided = len(text) - head_budget - tail_budget
    marker = _OUTPUT_TRUNCATED_TEMPLATE.format(n=elided, max=max_bytes)
    return f"{head}{marker}{tail}"


def run_command(
    cmd: list[str],
    cwd: Path | None = None,
    timeout: int | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run *cmd* as a subprocess and return ``(returncode, stdout, stderr)``.

    Args:
        cmd: The command and its arguments, e.g. ``["git", "status"]``.
        cwd: Working directory for the subprocess.  ``None`` inherits the
            current process directory.
        timeout: Maximum seconds to wait.  ``None`` uses the
            ``COMMAND_TIMEOUT`` value from ``devbench.config`` (configured
            via the ``JUDGE_COMMAND_TIMEOUT`` environment variable at module
            import time).
        env: Complete environment for the subprocess.  ``None`` (the default)
            inherits the parent process environment unchanged -- preserving the
            behaviour of every existing caller.  When provided it REPLACES the
            environment, so callers that only want to overlay a few variables
            must pass a full ``{**os.environ, ...}`` mapping.  Used by the
            deterministic per-unit verification gate to pin the pytest ordering
            seed (see ``verification.deterministic_gate_env``).

    Returns:
        A three-tuple ``(returncode, stdout, stderr)``.  On ``FileNotFoundError``
        or ``TimeoutExpired`` the returncode is ``127`` and stdout is empty;
        the error description is placed in stderr.

        Captured stdout and stderr are each bounded to
        :data:`~devbench.config.OUTPUT_TRUNCATION_LIMIT` characters via
        :func:`bound_output` (head + tail retained, middle replaced by an
        ``[OUTPUT_TRUNCATED ...]`` marker).  This is the single chokepoint where
        every executor command's output is captured before it can enter the
        turn transcript, so a runaway-output command (hundreds of errors) cannot
        wedge transcript processing or balloon memory.  The limit is config- and
        env-driven (``DEVBENCH_OUTPUT_TRUNCATION`` > ``limits.output_truncation``
        > default); a non-positive limit disables bounding.
    """
    effective_timeout = timeout if timeout is not None else COMMAND_TIMEOUT
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
            env=env,
        )
    except FileNotFoundError:
        return SUBPROCESS_ERROR_EXIT_CODE, "", f"{cmd[0]}: command not found"
    except subprocess.TimeoutExpired:
        return SUBPROCESS_ERROR_EXIT_CODE, "", f"{' '.join(cmd)}: timed out after {effective_timeout}s"
    stdout = bound_output(result.stdout, max_bytes=OUTPUT_TRUNCATION_LIMIT)
    stderr = bound_output(result.stderr, max_bytes=OUTPUT_TRUNCATION_LIMIT)
    return result.returncode, stdout, stderr


def run_command_in_process_group(
    cmd: list[str],
    cwd: Path | None = None,
    timeout: int | None = None,
    env: dict[str, str] | None = None,
    *,
    on_pgid: Callable[[int], None] | None = None,
    on_complete: Callable[[], None] | None = None,
) -> tuple[int, str, str]:
    """Run *cmd* in its OWN process group; positively attribute that group.

    Behaves like :func:`run_command` (same return tuple, same output bounding
    and timeout/not-found handling) but launches the command as a NEW session
    leader (``start_new_session=True``), so the command and every grandchild it
    spawns (e.g. ``terraform`` under ``go test`` under ``make``) share ONE
    process group whose pgid equals the launched child's pid.  That pgid is the
    handle a ``[CLAIM_NOT_CONVERGING]`` block uses to tear the whole subtree down
    instead of orphaning it to init (Item B of tracked issue 015).

    Attribution is published via the injected callbacks (dependency inversion --
    this module stays free of session/cli coupling):

    - ``on_pgid(pgid)`` is invoked once, immediately after launch, with the
      child's process-group id so the caller can record it to session state.
    - ``on_complete()`` is invoked once the command terminates (success, error,
      OR timeout), so the caller can clear the recorded pgid -- a no-longer-live
      group must never be torn down for a later, unrelated claim.

    On timeout the command's whole process group is signalled (``SIGTERM`` then
    ``SIGKILL``) so the live subtree is reaped, and the standard
    ``SUBPROCESS_ERROR_EXIT_CODE`` timeout tuple is returned (fail-fast, no
    silent hang).

    Args:
        cmd: Command and arguments (run with ``shell=False``).
        cwd: Working directory; ``None`` inherits the caller's.
        timeout: Seconds before the group is killed; ``None`` uses
            ``COMMAND_TIMEOUT``.
        env: Full environment mapping; ``None`` inherits the caller's.
        on_pgid: Called as ``on_pgid(pgid)`` right after launch.
        on_complete: Called with no args once the command terminates.

    Returns:
        ``(returncode, stdout, stderr)`` with stdout/stderr bounded to
        ``OUTPUT_TRUNCATION_LIMIT``; the timeout/not-found tuples match
        :func:`run_command`.
    """
    effective_timeout = timeout if timeout is not None else COMMAND_TIMEOUT
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except FileNotFoundError:
        if on_complete is not None:
            on_complete()
        return SUBPROCESS_ERROR_EXIT_CODE, "", f"{cmd[0]}: command not found"
    if on_pgid is not None:
        on_pgid(os.getpgid(proc.pid))
    try:
        out, err = proc.communicate(timeout=effective_timeout)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        out, err = proc.communicate()
        if on_complete is not None:
            on_complete()
        return SUBPROCESS_ERROR_EXIT_CODE, "", f"{' '.join(cmd)}: timed out after {effective_timeout}s"
    if on_complete is not None:
        on_complete()
    stdout = bound_output(out or "", max_bytes=OUTPUT_TRUNCATION_LIMIT)
    stderr = bound_output(err or "", max_bytes=OUTPUT_TRUNCATION_LIMIT)
    return proc.returncode, stdout, stderr


def _kill_process_group(proc: subprocess.Popen[str]) -> None:
    """Best-effort tear down *proc*'s process group (SIGTERM then SIGKILL).

    Signals the whole group so a grandchild (e.g. ``terraform`` under ``go
    test`` under ``make``) is reaped too. Already-exited groups
    (``ProcessLookupError``) and unsignalable groups (``OSError``) are silently
    tolerated -- this is a teardown best-effort, not a fault path.
    """
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, OSError):
            return
        if proc.poll() is not None:
            return
