"""Tests for devbench.utils.process.run_command standalone utility."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from devbench.utils.process import (
    _kill_process_group,
    bound_output,
    run_command,
    run_command_in_process_group,
)


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


class TestRunCommandEnv:
    """run_command forwards an explicit environment to the subprocess."""

    def test_run_command_passes_env_to_subprocess(self) -> None:
        """
        Given: An explicit env mapping is provided
        When: run_command is called
        Then: subprocess.run is called with that exact env
        Spec: AC-2
        """
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        custom_env = {"PYTHONHASHSEED": "7", "PATH": "/usr/bin"}

        with patch("devbench.utils.process.subprocess.run", return_value=mock_result) as mock_run:
            run_command(["echo", "x"], env=custom_env)

        _, kwargs = mock_run.call_args
        assert kwargs["env"] == custom_env

    def test_run_command_defaults_env_to_none(self) -> None:
        """
        Given: No env is provided
        When: run_command is called
        Then: subprocess.run receives env=None (inherits the parent environment)
        Spec: AC-2
        """
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("devbench.utils.process.subprocess.run", return_value=mock_result) as mock_run:
            run_command(["echo", "x"])

        _, kwargs = mock_run.call_args
        assert kwargs["env"] is None

    def test_run_command_env_is_observable_in_child(self) -> None:
        """
        Given: An env mapping carrying a custom variable
        When: run_command runs a real shell that echoes that variable
        Then: The child process sees the provided value
        Spec: AC-2
        """
        env = {"PATH": "/usr/bin:/bin", "DEVBENCH_GATE_PROBE": "deterministic"}
        rc, stdout, _ = run_command(["sh", "-c", "echo $DEVBENCH_GATE_PROBE"], env=env)
        assert rc == 0
        assert "deterministic" in stdout


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


class TestBoundOutput:
    """bound_output truncates runaway captured output, preserving head + tail.

    Item A (tracked issue 015): a unit producing very large command output is
    ingested unbounded and can wedge / balloon memory when it enters the turn
    transcript. ``bound_output`` caps captured output to a configurable max,
    keeping the head and tail with a clear ``[OUTPUT_TRUNCATED ...]`` marker.
    """

    def test_small_output_is_untouched(self) -> None:
        """
        Given: Output well within the configured max size
        When: bound_output is called
        Then: The text is returned verbatim (byte-for-byte, no marker injected)
        """
        text = "line-1\nline-2\nline-3\n"
        assert bound_output(text, max_bytes=10_000) == text
        assert "[OUTPUT_TRUNCATED" not in bound_output(text, max_bytes=10_000)

    def test_output_at_exactly_max_is_untouched(self) -> None:
        """
        Given: Output whose length equals the configured max exactly
        When: bound_output is called
        Then: The text is returned verbatim (boundary is inclusive)
        """
        text = "x" * 500
        assert bound_output(text, max_bytes=500) == text

    def test_large_output_is_truncated_with_marker(self) -> None:
        """
        Given: Output far exceeding the configured max size
        When: bound_output is called
        Then: The result is bounded near max, carries the [OUTPUT_TRUNCATED ...]
              marker, and is strictly smaller than the input
        """
        big = "\n".join(f"error-line-{i}" for i in range(100_000))
        result = bound_output(big, max_bytes=2000)
        assert "[OUTPUT_TRUNCATED" in result
        assert len(result) < len(big)
        # Bounded near the budget (head + tail + marker), not the firehose.
        assert len(result) <= 2000 + 200

    def test_truncation_preserves_head_and_tail(self) -> None:
        """
        Given: Output whose first and last lines are distinctive
        When: bound_output truncates it
        Then: Both the head and the tail of the original survive (so setup
              context AND the final summary/error are both visible)
        """
        first = "HEAD_SENTINEL_FIRST_LINE"
        last = "TAIL_SENTINEL_LAST_LINE"
        middle = "\n".join(f"noise-{i}" for i in range(50_000))
        big = f"{first}\n{middle}\n{last}"
        result = bound_output(big, max_bytes=1500)
        assert first in result
        assert last in result
        assert "noise-25000" not in result  # a mid-stream line was dropped

    def test_marker_reports_elided_byte_count(self) -> None:
        """
        Given: Output that must be truncated
        When: bound_output truncates it
        Then: The marker records how many bytes were elided (a positive count)
        """
        big = "z" * 50_000
        result = bound_output(big, max_bytes=1000)
        assert "[OUTPUT_TRUNCATED" in result
        # The elided byte count in the marker is positive and plausible.
        marker_line = next(ln for ln in result.split("\n") if "[OUTPUT_TRUNCATED" in ln)
        assert any(ch.isdigit() for ch in marker_line)

    def test_non_positive_max_disables_bounding(self) -> None:
        """
        Given: A non-positive max (the documented "disable" sentinel)
        When: bound_output is called with large text
        Then: The text is returned verbatim (bounding disabled)
        """
        big = "q" * 50_000
        assert bound_output(big, max_bytes=0) == big
        assert bound_output(big, max_bytes=-1) == big


class TestRunCommandBoundsOutput:
    """run_command bounds captured stdout/stderr at the configured limit.

    The single chokepoint where every executor command's output is captured
    before it can enter the turn transcript.
    """

    def test_run_command_truncates_large_stdout(self) -> None:
        """
        Given: A subprocess emitting stdout far larger than the configured limit
        When: run_command captures it
        Then: The returned stdout is bounded and carries the truncation marker
        Spec: tracked-issue-015 Item A
        """
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "\n".join(f"out-{i}" for i in range(100_000))
        mock_result.stderr = ""

        with patch("devbench.utils.process.subprocess.run", return_value=mock_result):
            with patch("devbench.utils.process.OUTPUT_TRUNCATION_LIMIT", 2000):
                rc, stdout, stderr = run_command(["make", "validate"])

        assert rc == 0
        assert "[OUTPUT_TRUNCATED" in stdout
        assert len(stdout) < len(mock_result.stdout)

    def test_run_command_truncates_large_stderr(self) -> None:
        """
        Given: A subprocess emitting stderr far larger than the configured limit
        When: run_command captures it
        Then: The returned stderr is bounded and carries the truncation marker
        Spec: tracked-issue-015 Item A
        """
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "\n".join(f"err-{i}" for i in range(100_000))

        with patch("devbench.utils.process.subprocess.run", return_value=mock_result):
            with patch("devbench.utils.process.OUTPUT_TRUNCATION_LIMIT", 2000):
                rc, stdout, stderr = run_command(["terragrunt", "run", "--all", "--", "validate"])

        assert "[OUTPUT_TRUNCATED" in stderr
        assert len(stderr) < len(mock_result.stderr)

    def test_run_command_leaves_small_output_untouched(self) -> None:
        """
        Given: A subprocess whose output is within the configured limit
        When: run_command captures it
        Then: stdout/stderr are returned byte-for-byte (no marker injected)
        Spec: tracked-issue-015 Item A
        """
        rc, stdout, stderr = run_command(["echo", "hello"])
        assert rc == 0
        assert stdout == "hello\n"
        assert "[OUTPUT_TRUNCATED" not in stdout
        assert "[OUTPUT_TRUNCATED" not in stderr

    def test_run_command_uses_configured_limit(self) -> None:
        """
        Given: The output-truncation limit is configured to a specific value
        When: run_command bounds a large output
        Then: bound_output is invoked with that configured limit (config-driven,
              not a hard-coded literal)
        Spec: tracked-issue-015 Item A
        """
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "x" * 50_000
        mock_result.stderr = ""

        with patch("devbench.utils.process.subprocess.run", return_value=mock_result):
            with patch("devbench.utils.process.OUTPUT_TRUNCATION_LIMIT", 1234):
                with patch("devbench.utils.process.bound_output", wraps=bound_output) as spy:
                    run_command(["x"])

        assert spy.call_args_list[0].kwargs.get("max_bytes", None) == 1234 or 1234 in spy.call_args_list[0].args


class TestRunCommandInProcessGroup:
    """run_command_in_process_group launches in its own group + publishes pgid.

    The attribution channel for Item B: the live command's whole subtree shares
    one process group, whose pgid is published so a CLAIM_NOT_CONVERGING block
    can tear exactly that subtree down.
    """

    def test_returns_same_shape_as_run_command(self) -> None:
        """
        Given: A normal command
        When: run_command_in_process_group runs it
        Then: It returns (returncode, stdout, stderr) just like run_command
        """
        rc, stdout, stderr = run_command_in_process_group(["echo", "hi"])
        assert rc == 0
        assert "hi" in stdout
        assert stderr == ""

    def test_publishes_child_process_group_id(self) -> None:
        """
        Given: An on_pgid callback
        When: run_command_in_process_group launches the command
        Then: on_pgid is invoked with a real, positive pgid distinct from the
              caller's own process group (proving the child is isolated)
        """
        published: list[int] = []
        rc, _, _ = run_command_in_process_group(["true"], on_pgid=published.append)
        assert rc == 0
        assert len(published) == 1
        assert published[0] > 1
        assert published[0] != os.getpgrp()

    def test_invokes_on_complete_after_command(self) -> None:
        """
        Given: An on_complete callback
        When: the command terminates
        Then: on_complete is invoked exactly once (so the caller clears the
              recorded pgid for a no-longer-live group)
        """
        completed: list[bool] = []
        run_command_in_process_group(["true"], on_complete=lambda: completed.append(True))
        assert completed == [True]

    def test_truncates_large_output(self) -> None:
        """
        Given: A command emitting output larger than the configured limit
        When: run_command_in_process_group captures it
        Then: stdout is bounded and carries the truncation marker
        """
        script = "import sys; sys.stdout.write('z' * 50000)"
        with patch("devbench.utils.process.OUTPUT_TRUNCATION_LIMIT", 2000):
            rc, stdout, _ = run_command_in_process_group([sys.executable, "-c", script])
        assert rc == 0
        assert "[OUTPUT_TRUNCATED" in stdout
        assert len(stdout) < 50000

    def test_on_complete_runs_when_command_not_found(self) -> None:
        """
        Given: A missing executable
        When: run_command_in_process_group is called
        Then: It returns the 127 not-found tuple AND still clears via on_complete
        """
        completed: list[bool] = []
        rc, stdout, stderr = run_command_in_process_group(
            ["nonexistent_command_xyz_abc"], on_complete=lambda: completed.append(True)
        )
        assert rc == 127
        assert "command not found" in stderr
        assert completed == [True]

    def test_timeout_kills_the_whole_group(self) -> None:
        """
        Given: A command that spawns a grandchild and would outlive its timeout
        When: run_command_in_process_group times out
        Then: It returns the 127 timeout tuple, clears via on_complete, and the
              whole group (grandchild included) is reaped -- not orphaned
        """
        published: list[int] = []
        completed: list[bool] = []
        # A parent that launches a long-sleeping grandchild in the SAME group,
        # then itself sleeps -- mimicking make -> go test -> terraform apply.
        script = (
            "import subprocess, sys, time; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
            "time.sleep(120)"
        )
        rc, _, stderr = run_command_in_process_group(
            [sys.executable, "-c", script],
            timeout=1,
            on_pgid=published.append,
            on_complete=lambda: completed.append(True),
        )
        assert rc == 127
        assert "timed out" in stderr
        assert completed == [True]
        pgid = published[0]
        # Give the kernel a moment to deliver SIGKILL to the group, then confirm
        # the whole group is gone (no orphaned grandchild left running).
        deadline = time.monotonic() + 5.0
        alive = True
        while time.monotonic() < deadline:
            try:
                os.killpg(pgid, 0)  # signal 0 = liveness probe
            except ProcessLookupError:
                alive = False
                break
            time.sleep(0.02)
        # Best-effort final reap in case anything lingers, then assert.
        with contextlib.suppress(ProcessLookupError, OSError):
            os.killpg(pgid, signal.SIGKILL)
        assert alive is False, "timed-out command's process group was orphaned"


class TestKillProcessGroup:
    """The group-teardown helper used by the in-group runner on timeout."""

    def test_sigterm_only_when_group_dies_promptly(self) -> None:
        """
        Given: A group that exits after SIGTERM
        When: _kill_process_group runs
        Then: It signals SIGTERM and does NOT escalate to SIGKILL
        """
        proc = MagicMock()
        proc.pid = 4242
        # The single poll after SIGTERM reports the group exited -> no escalation.
        proc.poll.return_value = 0
        with patch("devbench.utils.process.os.getpgid", return_value=4242):
            with patch("devbench.utils.process.os.killpg") as killpg:
                _kill_process_group(proc)
        sent = [c.args[1] for c in killpg.call_args_list]
        assert sent == [signal.SIGTERM]

    def test_escalates_to_sigkill_when_group_survives_sigterm(self) -> None:
        """
        Given: A group still alive after SIGTERM
        When: _kill_process_group runs
        Then: It escalates to SIGKILL (so a wedged subtree is reaped)
        """
        proc = MagicMock()
        proc.pid = 4242
        proc.poll.return_value = None  # never reports dead
        with patch("devbench.utils.process.os.getpgid", return_value=4242):
            with patch("devbench.utils.process.os.killpg") as killpg:
                _kill_process_group(proc)
        sent = [c.args[1] for c in killpg.call_args_list]
        assert sent == [signal.SIGTERM, signal.SIGKILL]

    def test_already_exited_group_is_a_noop(self) -> None:
        """
        Given: The group already exited (getpgid raises ProcessLookupError)
        When: _kill_process_group runs
        Then: It returns without signalling anything
        """
        proc = MagicMock()
        proc.pid = 4242
        with patch("devbench.utils.process.os.getpgid", side_effect=ProcessLookupError):
            with patch("devbench.utils.process.os.killpg") as killpg:
                _kill_process_group(proc)
        killpg.assert_not_called()

    def test_oserror_on_signal_is_tolerated(self) -> None:
        """
        Given: killpg raises OSError (unsignalable group)
        When: _kill_process_group runs
        Then: It returns without raising
        """
        proc = MagicMock()
        proc.pid = 4242
        proc.poll.return_value = None
        with patch("devbench.utils.process.os.getpgid", return_value=4242):
            with patch("devbench.utils.process.os.killpg", side_effect=OSError):
                _kill_process_group(proc)  # must not raise
