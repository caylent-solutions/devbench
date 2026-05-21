"""Unit tests for the guard-review-supervisor-scope PreToolUse hook (issue #118).

The hook enforces read-only scope on the ``devbench-orchestrate:review-supervisor``
agent. It blocks two classes of escalation:

1. **Bash mutations** -- destructive shell commands (rm, git commit, sed -i,
   `>` redirection, etc.) executed via the Bash tool. Existing rule, sanity-
   tested here for regression coverage.
2. **Agent-tool subagent spawn** (issue #118) -- Agent-tool invocations
   whose ``subagent_type`` is not in the canonical review_team allowlist
   (``devbench-orchestrate:code_review`` / ``devbench-orchestrate:test_review`` /
   ``devbench-orchestrate:doc_review`` / ``devbench-orchestrate:changes_manifest``). New branch.

Both paths share the operator override env var
``DEVBENCH_ALLOW_REVIEW_SUPERVISOR_MUTATIONS=1``.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).parent.parent.parent
    / "plugin"
    / "devbench-orchestrate"
    / "scripts"
    / "guard-review-supervisor-scope.sh"
)


def _run_hook(payload: dict, env: dict | None = None) -> subprocess.CompletedProcess:
    """Invoke the hook with the given JSON payload + env."""
    # these vars at source time (AC-197-9) and all hooks source _hook_lib.sh.
    runtime_env = {k: v for k, v in os.environ.items() if k not in ("DEVBENCH_WORKSPACE_ROOT", "DEVBENCH_LOG_FILE")}
    if env:
        runtime_env.update(env)
    return subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=runtime_env,
    )


def _agent_payload(subagent_type: str) -> dict:
    """Build a minimal PreToolUse Agent payload from the supervisor."""
    return {
        "agent_type": "devbench-orchestrate:review-supervisor",
        "tool_name": "Agent",
        "tool_input": {
            "subagent_type": subagent_type,
            "description": "test",
            "prompt": "test",
        },
    }


def _bash_payload(command: str) -> dict:
    """Build a minimal PreToolUse Bash payload from the supervisor."""
    return {
        "agent_type": "devbench-orchestrate:review-supervisor",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }


class TestNonSupervisorAgentTypeIsNoOp:
    """The hook only fires for the review-supervisor agent."""

    def test_executor_agent_passes_through(self) -> None:
        payload = {
            "agent_type": "devbench-orchestrate:executor",
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m 'wip'"},
        }
        rc = _run_hook(payload).returncode
        assert rc == 0


class TestAgentToolAllowlist:
    """Issue #118: review-supervisor may only spawn the four review_team subagents."""

    @pytest.mark.parametrize(
        "subagent_type",
        [
            "devbench-orchestrate:code_review",
            "devbench-orchestrate:test_review",
            "devbench-orchestrate:doc_review",
            "devbench-orchestrate:changes_manifest",
        ],
    )
    def test_review_team_subagents_allowed(self, subagent_type: str) -> None:
        result = _run_hook(_agent_payload(subagent_type))
        assert result.returncode == 0, (
            f"expected supervisor->{subagent_type} to be allowed; got stderr: {result.stderr}"
        )

    @pytest.mark.parametrize(
        "subagent_type",
        [
            "devbench-orchestrate:executor",
            "devbench-orchestrate:blocker_resolver",
            "devbench-orchestrate:task_factory",
            "devbench-orchestrate:manifest_amender",
            "devbench-orchestrate:security_review",
            "devbench:git-ops",
            "claude",
            "",
        ],
    )
    def test_non_review_team_subagents_blocked(self, subagent_type: str) -> None:
        result = _run_hook(_agent_payload(subagent_type))
        assert result.returncode == 2
        assert "review-supervisor attempted to spawn subagent_type" in result.stderr
        assert subagent_type in result.stderr

    def test_override_env_var_unblocks_subagent_spawn(self) -> None:
        result = _run_hook(
            _agent_payload("devbench-orchestrate:executor"),
            env={"DEVBENCH_ALLOW_REVIEW_SUPERVISOR_MUTATIONS": "1"},
        )
        assert result.returncode == 0
        assert "ALLOWED via DEVBENCH_ALLOW_REVIEW_SUPERVISOR_MUTATIONS=1" in result.stderr


class TestBashMutationsStillBlocked:
    """Regression: the existing Bash branch still blocks worktree mutations."""

    def test_git_commit_blocked(self) -> None:
        result = _run_hook(_bash_payload("git commit -m 'review-supervisor escalation'"))
        assert result.returncode == 2
        assert "review-supervisor agent attempted git mutation" in result.stderr

    def test_rm_blocked(self) -> None:
        result = _run_hook(_bash_payload("rm /tmp/some-file"))
        assert result.returncode == 2
        assert "review-supervisor agent attempted rm" in result.stderr

    def test_log_comment_allowed(self) -> None:
        # Reviewers must be able to record their findings.
        result = _run_hook(_bash_payload("uv run devbench log-comment review_supervisor E0-T1 'finding'"))
        assert result.returncode == 0
