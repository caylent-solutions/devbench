"""Claude Code agent executor.

Spawns Claude Code CLI agents to execute individual work units.
"""

import logging
import os
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from devbench.config import (
    CLAUDE_MODEL,
    EXECUTOR_MAX_TURNS,
    EXECUTOR_TIMEOUT,
    REPO_LOCAL_PATHS,
    resolve_repo,
    validate_repo,
)
from devbench.constants import STATUS_BLOCKED, STATUS_IN_REVIEW
from devbench.prompts import load_prompt

logger = logging.getLogger(__name__)


class ExecutionStatus(Enum):
    """Status of a Claude Code agent execution."""

    IN_REVIEW = STATUS_IN_REVIEW
    BLOCKED = STATUS_BLOCKED
    FAILED = "failed"


@dataclass
class ExecutionResult:
    """Result of a Claude Code agent execution."""

    status: ExecutionStatus
    output: str
    blocker: str = ""


_EXECUTOR_PROMPT_TEMPLATE = load_prompt("executor")


def _build_prompt(work_unit_path: Path, feedback: str = "") -> str:
    """Build the prompt to send to Claude Code for executing a work unit."""
    prompt = _EXECUTOR_PROMPT_TEMPLATE.format(work_unit_path=work_unit_path)

    if feedback:
        prompt += "\n\nIMPORTANT: Previous attempt was rejected by judges. Fix these issues:\n" + feedback

    return prompt


def _is_nested_claude() -> bool:
    """Detect if we're running inside a Claude Code session."""
    return bool(os.environ.get("CLAUDE_CONTEXT"))


def execute(
    work_unit_path: Path,
    repo: str,
    feedback: str = "",
    timeout_seconds: int | None = None,
) -> ExecutionResult:
    """Spawn a Claude Code agent to execute a work unit.

    Args:
        work_unit_path: Path to the work unit .md file.
        repo: Repository identifier (e.g., "caylent-solutions/git-repo").
        feedback: Optional feedback from previous judge review.
        timeout_seconds: Max time for agent execution. Uses EXECUTOR_TIMEOUT config if None.

    Returns:
        ExecutionResult with status and output.

    Raises:
        RuntimeError: If called from inside a Claude Code session (nested CLI is not supported).
    """
    if _is_nested_claude():
        raise RuntimeError(
            "Cannot spawn nested Claude CLI from inside a Claude Code session. "
            "In interactive mode, the orchestrating Claude session should implement "
            "work units directly using its built-in tools (Read, Write, Edit, Bash), "
            "then call 'python3 -m judges.cli review <unit-id>' for judge evaluation."
        )

    effective_timeout = timeout_seconds if timeout_seconds is not None else EXECUTOR_TIMEOUT
    repo = resolve_repo(repo)
    validate_repo(repo)
    repo_path = REPO_LOCAL_PATHS.get(repo)
    if repo_path is None:
        raise ValueError(f"No local path configured for repo: {repo}")

    prompt = _build_prompt(work_unit_path, feedback)

    cmd = [
        "claude",
        "--print",
        "--dangerously-skip-permissions",
        "--model",
        CLAUDE_MODEL,
        "--max-turns",
        str(EXECUTOR_MAX_TURNS),
        prompt,
    ]

    logger.info("Spawning Claude Code agent for %s in %s", work_unit_path.name, repo_path)

    try:
        result = subprocess.run(
            cmd,
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=effective_timeout,
            env={**os.environ, "CLAUDE_NO_TELEMETRY": "1"},
        )

        output = result.stdout
        if result.returncode != 0:
            logger.warning(
                "Claude Code agent exited with code %d for %s",
                result.returncode,
                work_unit_path.name,
            )
            if "blocked" in output.lower() or "blocker" in output.lower():
                return ExecutionResult(
                    status=ExecutionStatus.BLOCKED,
                    output=output,
                    blocker=_extract_blocker(output),
                )
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                output=output,
            )

        # Check if the work unit status was updated to in-review
        work_unit_content = work_unit_path.read_text(encoding="utf-8")
        if f"## Status: {STATUS_IN_REVIEW}" in work_unit_content:
            return ExecutionResult(
                status=ExecutionStatus.IN_REVIEW,
                output=output,
            )

        # Agent completed but didn't set status — treat as in-review
        logger.warning(
            "Agent didn't set in-review status for %s, treating as in-review",
            work_unit_path.name,
        )
        return ExecutionResult(
            status=ExecutionStatus.IN_REVIEW,
            output=output,
        )

    except subprocess.TimeoutExpired:
        logger.error(
            "Claude Code agent timed out after %ds for %s",
            effective_timeout,
            work_unit_path.name,
        )
        return ExecutionResult(
            status=ExecutionStatus.FAILED,
            output=f"Agent timed out after {effective_timeout} seconds",
        )


def _extract_blocker(output: str) -> str:
    """Extract blocker description from agent output."""
    for line in output.splitlines():
        lower = line.lower()
        if "blocked" in lower or "blocker" in lower:
            return line.strip()
    raise RuntimeError("Could not extract blocker description from agent output")
