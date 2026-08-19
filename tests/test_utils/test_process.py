"""Tests for devbench.utils.process.run_command standalone utility."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from devbench.utils.process import run_command


class TestRunCommandSuccess:
    """run_command returns (returncode, stdout, stderr) for normal processes."""

    def test_run_command_returns_stdout_stderr_rc(self) -> None:
        """
        Given: A command that succeeds
        When: run_command is called
        Then: Returns (0, stdout, stderr) tuple with correct values
        Spec: AC-1
        """
        rc, stdout, stderr = run_command(["echo", "hello"])
        assert rc == 0
        assert "hello" in stdout
        assert stderr == ""

    def test_run_command_returns_nonzero_rc_on_failure(self) -> None:
        """
        Given: A command that exits with a nonzero code
        When: run_command is called
        Then: Returns the nonzero returncode
        Spec: AC-1
        """
        rc, stdout, stderr = run_command(["false"])
        assert rc != 0

    def test_run_command_captures_stderr(self) -> None:
        """
        Given: A command that writes to stderr
        When: run_command is called
        Then: stderr is captured and returned in the third element
        Spec: AC-1
        """
        rc, stdout, stderr = run_command(["sh", "-c", "echo err >&2"])
        assert "err" in stderr

    def test_run_command_passes_cwd(self, tmp_path: Path) -> None:
        """
        Given: A cwd argument is provided
        When: run_command is called
        Then: The subprocess runs in the specified directory
        Spec: AC-1
        """
        rc, stdout, _ = run_command(["pwd"], cwd=tmp_path)
        assert rc == 0
        assert str(tmp_path) in stdout

    def test_run_command_uses_default_timeout_when_none(self) -> None:
        """
        Given: No explicit timeout is passed
        When: run_command is called
        Then: subprocess.run is called with the configured COMMAND_TIMEOUT default
        Spec: AC-1, AC-4
        """
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("devbench.utils.process.subprocess.run", return_value=mock_result) as mock_run:
            with patch("devbench.utils.process.COMMAND_TIMEOUT", 42):
                run_command(["echo", "test"])

        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] == 42

    def test_run_command_uses_explicit_timeout_when_provided(self) -> None:
        """
        Given: An explicit timeout is passed
        When: run_command is called
        Then: subprocess.run is called with that timeout (not the default)
        Spec: AC-1, AC-4
        """
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("devbench.utils.process.subprocess.run", return_value=mock_result) as mock_run:
            run_command(["echo", "test"], timeout=99)

        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] == 99


class TestRunCommandFileNotFound:
    """run_command returns (127, '', '<cmd>: command not found') when exe is missing."""

    def test_run_command_handles_file_not_found(self) -> None:
        """
        Given: A command whose executable does not exist
        When: run_command is called
        Then: Returns (127, '', '<cmd>: command not found') without raising
        Spec: AC-4
        """
        rc, stdout, stderr = run_command(["nonexistent_command_xyz_abc"])
        assert rc == 127
        assert stdout == ""
        assert "command not found" in stderr
        assert "nonexistent_command_xyz_abc" in stderr

    def test_run_command_file_not_found_does_not_raise(self) -> None:
        """
        Given: subprocess.run raises FileNotFoundError
        When: run_command is called
        Then: No exception propagates to the caller
        Spec: AC-4
        """
        with patch("devbench.utils.process.subprocess.run", side_effect=FileNotFoundError):
            rc, stdout, stderr = run_command(["missing"])
        assert rc == 127
        assert stdout == ""


class TestRunCommandEnv:
    """run_command accepts an optional env mapping and forwards it verbatim."""

    def test_env_forwarded_to_subprocess_run(self) -> None:
        """
        Given: An explicit env mapping is passed
        When: run_command is called
        Then: subprocess.run receives that exact mapping as its env kwarg
        Spec: AC-E17-F2-S1-T2-1, AC-E17-F2-S1-T2-2
        """
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        injected_env = {"FOO": "bar"}
        with patch("devbench.utils.process.subprocess.run", return_value=mock_result) as mock_run:
            run_command(["echo", "test"], env=injected_env)

        _, kwargs = mock_run.call_args
        assert kwargs["env"] == injected_env
        assert kwargs["env"] is injected_env

    def test_env_defaults_to_none_when_omitted(self) -> None:
        """
        Given: No env argument is passed
        When: run_command is called
        Then: subprocess.run receives env=None so the child inherits the parent environment
        Spec: AC-E17-F2-S1-T2-1, AC-E17-F2-S1-T2-2
        """
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("devbench.utils.process.subprocess.run", return_value=mock_result) as mock_run:
            run_command(["echo", "test"])

        _, kwargs = mock_run.call_args
        assert kwargs["env"] is None

    def test_injected_env_var_visible_in_real_subprocess(self) -> None:
        """
        Given: A real (non-mocked) subprocess is spawned with an injected env var
        When: run_command is called with env={...}
        Then: The child process genuinely sees the injected variable's value
        Spec: AC-E17-F2-S1-T2-3
        """
        marker_value = "devbench-env-passthrough-marker"
        injected_env = {"DEVBENCH_TEST_ENV_MARKER": marker_value}
        cmd = [sys.executable, "-c", "import os; print(os.environ['DEVBENCH_TEST_ENV_MARKER'])"]

        rc, stdout, stderr = run_command(cmd, env=injected_env)

        assert rc == 0, f"subprocess failed: stderr={stderr!r}"
        assert stdout.strip() == marker_value

    def test_injected_env_does_not_leak_unrelated_parent_vars(self) -> None:
        """
        Given: An env mapping that does NOT include PATH
        When: run_command is called with that restricted env
        Then: The child process sees exactly the mapping supplied (not the parent's PATH),
              proving env replaces rather than merges with os.environ
        Spec: AC-E17-F2-S1-T2-3
        """
        assert "DEVBENCH_TEST_ISOLATED_MARKER" not in os.environ
        injected_env = {"DEVBENCH_TEST_ISOLATED_MARKER": "isolated"}
        cmd = [sys.executable, "-c", "import os; print('PATH' in os.environ)"]

        rc, stdout, stderr = run_command(cmd, env=injected_env)

        assert rc == 0, f"subprocess failed: stderr={stderr!r}"
        assert stdout.strip() == "False"


class TestRunCommandTimeout:
    """run_command returns (127, '', '<cmd>: timed out after Ns') on timeout."""

    def test_run_command_handles_timeout(self) -> None:
        """
        Given: subprocess.run raises TimeoutExpired
        When: run_command is called
        Then: Returns (127, '', '<cmd>: timed out after <N>s') without raising
        Spec: AC-4
        """
        exc = subprocess.TimeoutExpired(cmd=["sleep", "100"], timeout=5)
        with patch("devbench.utils.process.subprocess.run", side_effect=exc):
            rc, stdout, stderr = run_command(["sleep", "100"], timeout=5)
        assert rc == 127
        assert stdout == ""
        assert "timed out after" in stderr
        assert "5s" in stderr

    def test_run_command_timeout_message_contains_command(self) -> None:
        """
        Given: A command that times out
        When: run_command is called
        Then: The error message includes the command string
        Spec: AC-4
        """
        exc = subprocess.TimeoutExpired(cmd=["make", "test"], timeout=30)
        with patch("devbench.utils.process.subprocess.run", side_effect=exc):
            rc, stdout, stderr = run_command(["make", "test"], timeout=30)
        assert "make test" in stderr

    def test_run_command_timeout_does_not_raise(self) -> None:
        """
        Given: subprocess.run raises TimeoutExpired
        When: run_command is called
        Then: No exception propagates to the caller
        Spec: AC-4
        """
        exc = subprocess.TimeoutExpired(cmd=["hang"], timeout=1)
        with patch("devbench.utils.process.subprocess.run", side_effect=exc):
            rc, _, _ = run_command(["hang"], timeout=1)
        assert rc == 127
