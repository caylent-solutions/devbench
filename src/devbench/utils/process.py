"""Shared subprocess utility for running shell commands.

This module provides a standalone ``run_command`` function that wraps
``subprocess.run`` with consistent error handling for missing executables
and timeouts.  It is intentionally free of judge-specific dependencies so
that non-judge components can use it without inheriting the full judge
contract.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from devbench.config import COMMAND_TIMEOUT
from devbench.constants import SUBPROCESS_ERROR_EXIT_CODE


def run_command(
    cmd: list[str],
    cwd: Path | None = None,
    timeout: int | None = None,
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

    Returns:
        A three-tuple ``(returncode, stdout, stderr)``.  On ``FileNotFoundError``
        or ``TimeoutExpired`` the returncode is ``127`` and stdout is empty;
        the error description is placed in stderr.
    """
    effective_timeout = timeout if timeout is not None else COMMAND_TIMEOUT
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
        )
    except FileNotFoundError:
        return SUBPROCESS_ERROR_EXIT_CODE, "", f"{cmd[0]}: command not found"
    except subprocess.TimeoutExpired:
        return SUBPROCESS_ERROR_EXIT_CODE, "", f"{' '.join(cmd)}: timed out after {effective_timeout}s"
    return result.returncode, result.stdout, result.stderr
