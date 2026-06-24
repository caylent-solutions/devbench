"""PtyDriver launch-command assembly + ready detection (FR-5, FR-7, Section 4.1).

Asserts the EXACT ``claude`` launch argv the supervisor assembles: the model,
effort, ``--dangerously-skip-permissions``, and ``--plugin-dir`` flags are
present and ``-p``/``--print`` is NEVER present (the operator requirement +
billing model). Also covers hybrid ready detection via ``wait_for_ready``.
"""

from __future__ import annotations

import pytest
from fixtures.supervise import FakePexpectChild, _ScriptStep

from devbench.config_loader import SuperviseDetectionPatternsConfig
from devbench.supervise import (
    DetectionPatterns,
    PtyDriver,
    SuperviseReadyTimeoutError,
    build_claude_launch_argv,
)


@pytest.mark.unit
class TestLaunchArgvAssembly:
    """FR-5: the launch argv carries the required flags and NEVER -p/--print."""

    def test_flags_present(self) -> None:
        argv = build_claude_launch_argv(
            claude_path="/usr/local/bin/claude",
            model="claude-opus-4-8",
            effort="xhigh",
            plugin_dir="/ws/.devbench/plugin-shadow/devbench",
        )
        assert argv[0] == "/usr/local/bin/claude"
        assert "--model" in argv
        assert argv[argv.index("--model") + 1] == "claude-opus-4-8"
        assert "--effort" in argv
        assert argv[argv.index("--effort") + 1] == "xhigh"
        assert "--dangerously-skip-permissions" in argv
        assert "--plugin-dir" in argv
        assert argv[argv.index("--plugin-dir") + 1] == "/ws/.devbench/plugin-shadow/devbench"

    def test_no_print_flag(self) -> None:
        argv = build_claude_launch_argv(
            claude_path="claude",
            model="opus",
            effort="xhigh",
            plugin_dir="/ws/plugin",
        )
        assert "-p" not in argv
        assert "--print" not in argv

    def test_resume_flags_for_restart(self) -> None:
        argv = build_claude_launch_argv(
            claude_path="claude",
            model="opus",
            effort="xhigh",
            plugin_dir="/ws/plugin",
            resume_session_id="018f-a1",
        )
        assert "--resume" in argv
        assert argv[argv.index("--resume") + 1] == "018f-a1"
        assert "-p" not in argv

    def test_continue_flag_when_no_session_id(self) -> None:
        argv = build_claude_launch_argv(
            claude_path="claude",
            model="opus",
            effort="xhigh",
            plugin_dir="/ws/plugin",
            resume_continue=True,
        )
        assert "--continue" in argv
        assert "--resume" not in argv

    def test_empty_model_fails_fast(self) -> None:
        with pytest.raises(ValueError, match="model"):
            build_claude_launch_argv(claude_path="claude", model="", effort="xhigh", plugin_dir="/ws")

    def test_empty_effort_fails_fast(self) -> None:
        with pytest.raises(ValueError, match="effort"):
            build_claude_launch_argv(claude_path="claude", model="opus", effort="", plugin_dir="/ws")

    def test_empty_plugin_dir_fails_fast(self) -> None:
        with pytest.raises(ValueError, match="plugin_dir"):
            build_claude_launch_argv(claude_path="claude", model="opus", effort="xhigh", plugin_dir="")


@pytest.mark.unit
class TestReadyDetection:
    """FR-7: wait_for_ready returns on the ready prompt, times out otherwise."""

    def test_ready_prompt_detected(self) -> None:
        child = FakePexpectChild([_ScriptStep(emit="> ")])
        driver = PtyDriver(child=child, patterns=DetectionPatterns(SuperviseDetectionPatternsConfig()))
        driver.wait_for_ready(timeout_seconds=5)

    def test_ready_timeout_raises(self) -> None:
        child = FakePexpectChild([])
        driver = PtyDriver(child=child, patterns=DetectionPatterns(SuperviseDetectionPatternsConfig()))
        with pytest.raises(SuperviseReadyTimeoutError):
            driver.wait_for_ready(timeout_seconds=1)

    def test_eof_before_ready_raises(self) -> None:
        child = FakePexpectChild([_ScriptStep(emit="", eof=True, exitstatus=1)])
        driver = PtyDriver(child=child, patterns=DetectionPatterns(SuperviseDetectionPatternsConfig()))
        with pytest.raises(SuperviseReadyTimeoutError, match="exited before"):
            driver.wait_for_ready(timeout_seconds=1)

    def test_expect_working_timeout_returns_false(self) -> None:
        child = FakePexpectChild([])
        driver = PtyDriver(child=child, patterns=DetectionPatterns(SuperviseDetectionPatternsConfig()))
        assert driver.expect_working(timeout_seconds=1) is False

    def test_expect_working_success_returns_true(self) -> None:
        child = FakePexpectChild([_ScriptStep(emit="esc to interrupt")])
        driver = PtyDriver(child=child, patterns=DetectionPatterns(SuperviseDetectionPatternsConfig()))
        assert driver.expect_working(timeout_seconds=1) is True


@pytest.mark.unit
class TestPtyDriverSlashSubmission:
    """type_text / submit / wait_until_quiescent: the slash-submit primitives.

    A SLASH command opens the Claude Code autocomplete menu the instant ``/`` is
    typed; the trailing newline of a ``sendline`` is swallowed by that menu. The
    driver instead types the literal (no newline), waits for the menu render to
    settle (readiness, not a fixed sleep), then sends a single Enter.
    """

    def test_type_text_sends_payload_without_newline(self) -> None:
        child = FakePexpectChild([])
        driver = PtyDriver(child=child, patterns=DetectionPatterns(SuperviseDetectionPatternsConfig()))
        driver.type_text("/devbench-orchestrate:orchestrate")
        assert child.sent == ["/devbench-orchestrate:orchestrate"]

    def test_submit_sends_single_enter(self) -> None:
        child = FakePexpectChild([])
        driver = PtyDriver(child=child, patterns=DetectionPatterns(SuperviseDetectionPatternsConfig()))
        driver.submit()
        assert child.sent == ["\r"]

    def test_wait_until_quiescent_returns_true_on_quiet_timeout(self) -> None:
        child = FakePexpectChild([])
        driver = PtyDriver(child=child, patterns=DetectionPatterns(SuperviseDetectionPatternsConfig()))
        assert driver.wait_until_quiescent(quiet_seconds=1, max_seconds=8) is True

    def test_wait_until_quiescent_consumes_render_then_settles(self, tmp_path) -> None:
        from devbench.supervise import PtyLogWriter

        log = tmp_path / "pty.log"
        writer = PtyLogWriter(path=log, redact_patterns=())
        child = FakePexpectChild(
            [
                _ScriptStep(emit="menu line one"),
                _ScriptStep(emit="menu line two"),
            ]
        )
        driver = PtyDriver(
            child=child,
            patterns=DetectionPatterns(SuperviseDetectionPatternsConfig()),
            log_writer=writer,
        )
        assert driver.wait_until_quiescent(quiet_seconds=1, max_seconds=8) is True
        writer.close()
        contents = log.read_text(encoding="utf-8")
        assert "menu line one" in contents
        assert "menu line two" in contents

    def test_wait_until_quiescent_returns_false_on_eof(self) -> None:
        child = FakePexpectChild([_ScriptStep(emit="dying", eof=True, exitstatus=1)])
        driver = PtyDriver(child=child, patterns=DetectionPatterns(SuperviseDetectionPatternsConfig()))
        assert driver.wait_until_quiescent(quiet_seconds=1, max_seconds=8) is False

    def test_wait_until_quiescent_returns_false_when_iteration_cap_exhausted(self) -> None:
        child = FakePexpectChild([_ScriptStep(emit=f"chunk {i}") for i in range(4)])
        driver = PtyDriver(child=child, patterns=DetectionPatterns(SuperviseDetectionPatternsConfig()))
        assert driver.wait_until_quiescent(quiet_seconds=1, max_seconds=3) is False


@pytest.mark.unit
class TestPtyDriverTee:
    """The driver tees matched output to the redacted pty.log when configured."""

    def test_tee_writes_to_log(self, tmp_path) -> None:
        from devbench.supervise import PtyLogWriter

        log = tmp_path / "pty.log"
        writer = PtyLogWriter(path=log, redact_patterns=())
        child = FakePexpectChild([_ScriptStep(emit="> ")])
        driver = PtyDriver(
            child=child,
            patterns=DetectionPatterns(SuperviseDetectionPatternsConfig()),
            log_writer=writer,
        )
        driver.wait_for_ready(timeout_seconds=1)
        writer.close()
        assert ">" in log.read_text(encoding="utf-8")


@pytest.mark.unit
class TestPtyDriverReadChunk:
    """read_chunk feeds the event loop: text / EOF+exitstatus / prompt-timeout (Section 4.1 step 8)."""

    def test_read_chunk_returns_text(self) -> None:
        child = FakePexpectChild([_ScriptStep(emit="esc to interrupt -- thinking")])
        driver = PtyDriver(child=child, patterns=DetectionPatterns(SuperviseDetectionPatternsConfig()))
        text, eof, exitstatus = driver.read_chunk(timeout_seconds=1)
        assert eof is False
        assert exitstatus is None
        assert "thinking" in text
        assert "thinking" in driver.last_text()

    def test_read_chunk_eof_carries_exitstatus(self, tmp_path) -> None:
        from devbench.supervise import PtyLogWriter

        log = tmp_path / "pty.log"
        writer = PtyLogWriter(path=log, redact_patterns=())
        child = FakePexpectChild([_ScriptStep(emit="bye", eof=True, exitstatus=7)])
        driver = PtyDriver(
            child=child,
            patterns=DetectionPatterns(SuperviseDetectionPatternsConfig()),
            log_writer=writer,
        )
        text, eof, exitstatus = driver.read_chunk(timeout_seconds=1)
        writer.close()
        assert eof is True
        assert exitstatus == 7
        assert text == "bye"

    def test_read_chunk_timeout_raises(self) -> None:
        from devbench.supervise import SupervisePromptTimeoutError

        child = FakePexpectChild([])
        driver = PtyDriver(child=child, patterns=DetectionPatterns(SuperviseDetectionPatternsConfig()))
        with pytest.raises(SupervisePromptTimeoutError, match="prompt-timeout-idle"):
            driver.read_chunk(timeout_seconds=1)

    def test_last_text_empty_before_any_read(self) -> None:
        child = FakePexpectChild([])
        driver = PtyDriver(child=child, patterns=DetectionPatterns(SuperviseDetectionPatternsConfig()))
        assert driver.last_text() == ""
