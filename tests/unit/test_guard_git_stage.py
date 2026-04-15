"""Unit tests for the guard-git-stage PreToolUse hook script."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).parent.parent.parent / "plugin" / "devbench" / "scripts" / "guard-git-stage.sh"


def _run_hook(payload: dict, cwd: str | None = None) -> subprocess.CompletedProcess:
    """Invoke the hook script with the given JSON payload on stdin."""
    env = os.environ.copy()
    if cwd is not None:
        env["PWD"] = cwd
    return subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
    )


def _make_payload(command: str) -> dict:
    """Build a minimal PreToolUse Bash hook payload."""
    return {
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }


def _init_git_repo(path: Path) -> None:
    """Initialize a bare git repo for testing."""
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
        cwd=str(path),
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        check=True,
        capture_output=True,
        cwd=str(path),
    )


@pytest.mark.unit
class TestGuardGitStageHook:
    """Tests for guard-git-stage.sh PreToolUse hook."""

    # ------------------------------------------------------------------ AC-1

    def test_script_exists_and_is_executable(self) -> None:
        """AC-1: The script must exist and be executable."""
        assert SCRIPT_PATH.exists(), f"Script not found at {SCRIPT_PATH}"
        assert os.access(SCRIPT_PATH, os.X_OK), f"Script is not executable: {SCRIPT_PATH}"

    # ------------------------------------------------------------------ AC-2: blocks git commit when no staged changes

    def test_git_commit_with_no_staged_changes_exits_2(self) -> None:
        """AC-2: Exit 2 when git commit is attempted but no files are staged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            _init_git_repo(repo_path)
            # Create an unstaged file (not added to index)
            (repo_path / "file.txt").write_text("hello")
            payload = _make_payload("git commit -m 'initial'")
            result = _run_hook(payload, cwd=str(repo_path))
        assert result.returncode == 2
        assert "guard-git-stage" in result.stderr

    def test_git_commit_error_message_is_actionable(self) -> None:
        """AC-2: Error message must be clear and actionable when no staged changes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            _init_git_repo(repo_path)
            payload = _make_payload("git commit -m 'test'")
            result = _run_hook(payload, cwd=str(repo_path))
        assert result.returncode == 2
        stderr_lower = result.stderr.lower()
        # Must mention staging, git add, or similar guidance
        assert any(word in stderr_lower for word in ["stage", "git add", "staged", "nothing"]), (
            f"Error message not actionable: {result.stderr}"
        )

    def test_git_commit_with_no_changes_message_mentions_git_add(self) -> None:
        """AC-2: Error message must tell the user to use git add."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            _init_git_repo(repo_path)
            payload = _make_payload("git commit -m 'test'")
            result = _run_hook(payload, cwd=str(repo_path))
        assert result.returncode == 2
        assert "git add" in result.stderr

    # ------------------------------------------------------------------ AC-3: git commit allowed when staged

    def test_git_commit_with_staged_changes_exits_0(self) -> None:
        """AC-3: Exit 0 when git commit is attempted and files are staged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            _init_git_repo(repo_path)
            # Create and stage a file
            test_file = repo_path / "file.txt"
            test_file.write_text("hello")
            subprocess.run(
                ["git", "add", "file.txt"],
                check=True,
                capture_output=True,
                cwd=str(repo_path),
            )
            payload = _make_payload("git commit -m 'initial'")
            result = _run_hook(payload, cwd=str(repo_path))
        assert result.returncode == 0, f"stderr: {result.stderr}"

    def test_git_commit_amend_with_staged_changes_exits_0(self) -> None:
        """AC-3: Exit 0 for git commit --amend when staged changes exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            _init_git_repo(repo_path)
            # Create initial commit
            (repo_path / "file.txt").write_text("initial")
            subprocess.run(["git", "add", "file.txt"], check=True, capture_output=True, cwd=str(repo_path))
            subprocess.run(
                ["git", "commit", "-m", "initial"],
                check=True,
                capture_output=True,
                cwd=str(repo_path),
            )
            # Stage a new change
            (repo_path / "file2.txt").write_text("update")
            subprocess.run(["git", "add", "file2.txt"], check=True, capture_output=True, cwd=str(repo_path))
            payload = _make_payload("git commit --amend -m 'amended'")
            result = _run_hook(payload, cwd=str(repo_path))
        assert result.returncode == 0, f"stderr: {result.stderr}"

    # ------------------------------------------------------------------ AC-4: non-commit bash commands allowed

    def test_non_commit_git_command_exits_0(self) -> None:
        """AC-4: git status is not a commit command and is always allowed."""
        payload = _make_payload("git status")
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_git_add_command_exits_0(self) -> None:
        """AC-4: git add is not a commit command and is always allowed."""
        payload = _make_payload("git add -A")
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_git_push_command_exits_0(self) -> None:
        """AC-4: git push is not a commit command and is always allowed."""
        payload = _make_payload("git push origin main")
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_ls_command_exits_0(self) -> None:
        """AC-4: Non-git commands are always allowed."""
        payload = _make_payload("ls -la")
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_make_validate_exits_0(self) -> None:
        """AC-4: make validate is not a commit command and is allowed."""
        payload = _make_payload("make validate")
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_pytest_command_exits_0(self) -> None:
        """AC-4: pytest is not a commit command and is always allowed."""
        payload = _make_payload("uv run pytest tests/")
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_empty_command_exits_0(self) -> None:
        """AC-4: Empty/missing command does not crash the hook."""
        payload = {"tool_name": "Bash", "tool_input": {}}
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_uv_run_devbench_command_exits_0(self) -> None:
        """AC-4: devbench CLI commands are not commit commands and are always allowed."""
        payload = _make_payload("uv run devbench log-verdict executor E201-F1-S3-T1 pass")
        result = _run_hook(payload)
        assert result.returncode == 0

    # ------------------------------------------------------------------ edge cases

    @pytest.mark.parametrize(
        "command",
        [
            "git commit -m 'test'",
            "git commit --message 'test'",
            "git commit -am 'test'",
            "git commit --amend",
        ],
    )
    def test_various_git_commit_forms_blocked_when_no_staged(self, command: str) -> None:
        """AC-2: All forms of git commit are intercepted when no staged changes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            _init_git_repo(repo_path)
            payload = _make_payload(command)
            result = _run_hook(payload, cwd=str(repo_path))
        assert result.returncode == 2, (
            f"Expected exit 2 for '{command}' with no staged changes, got {result.returncode}"
        )

    def test_command_containing_git_commit_substring_but_not_git_commit(self) -> None:
        """AC-4: A command that contains 'git commit' as a substring in another context is handled correctly."""
        payload = _make_payload("echo 'git commit -m test'")
        result = _run_hook(payload)
        # This is an echo command, not a real git commit — should be allowed
        assert result.returncode == 0
