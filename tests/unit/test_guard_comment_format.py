"""Unit tests for the guard-comment-format PreToolUse hook script.

The hook intercepts `uv run devbench log-comment <agent> <id> <message>` calls
and rejects (exit 2) any call whose message body contains forbidden control-
language phrases that would otherwise risk being interpreted by the orchestrator
LLM as halt directives. The forbidden-phrase list lives in the hook script as
the single source of truth; these tests verify the contract for each phrase
and for the structural passthroughs (non-log-comment commands, --help, shell
meta-tokens, malformed payloads).

Runtime invariant under test: the orchestrator's loop control belongs ONLY to
`uv run devbench next` and the stop-hook circuit breaker. Subagent log-comment
text is diagnostic narration; the hook prevents that narration from carrying
imperatives directed at the orchestrator's loop.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).parent.parent.parent / "plugin" / "devbench-orchestrate" / "scripts" / "guard-comment-format.sh"
)

FORBIDDEN_PHRASES = [
    "halt orchestration",
    "halting orchestration",
    "halt the loop",
    "halt loop",
    "stop the loop",
    "stop orchestration",
    "abort orchestration",
    "operator action required",
    "resume orchestration once",
    "emergency halt",
    "do not continue",
]


def _clean_env() -> dict[str, str]:
    """Return the process env with legacy DEVBENCH_WORKSPACE_ROOT and DEVBENCH_LOG_FILE stripped.

    _hook_lib.sh rejects legacy JUDGE_* hook vars (AC-197-9). Tests that source
    _hook_lib.sh must not inherit those vars from the pytest process environment.
    """
    return {k: v for k, v in os.environ.items() if k not in ("DEVBENCH_WORKSPACE_ROOT", "DEVBENCH_LOG_FILE")}


def _run_hook(payload: dict) -> subprocess.CompletedProcess:
    """Invoke the hook script with the given JSON payload on stdin."""
    return subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=_clean_env(),
    )


def _make_payload(command: str) -> dict:
    """Build a minimal PreToolUse Bash hook payload."""
    return {
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }


@pytest.mark.unit
class TestGuardCommentFormatStructural:
    """Structural contract: script presence + JSON parsing + scope of interception."""

    def test_script_exists_and_is_executable(self) -> None:
        """The script must exist on disk and be executable."""
        assert SCRIPT_PATH.exists(), f"Script not found at {SCRIPT_PATH}"
        assert os.access(SCRIPT_PATH, os.X_OK), f"Script is not executable: {SCRIPT_PATH}"

    def test_non_log_comment_command_passes_through(self) -> None:
        """Commands that are not `uv run devbench log-comment ...` exit 0 immediately."""
        payload = _make_payload("ls -la")
        result = _run_hook(payload)
        assert result.returncode == 0
        assert result.stderr == ""

    def test_git_command_passes_through(self) -> None:
        """git commands must not be intercepted -- even if they contain forbidden phrases."""
        payload = _make_payload("git commit -m 'halt orchestration of release pipeline'")
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_other_devbench_subcommands_pass_through(self) -> None:
        """Other devbench subcommands (read-unit, log-verdict, next, claim) are not intercepted."""
        for sub in ("read-unit E1-F1-S1-T1", "next", "claim E1-F1-S1-T1", "log-verdict executor E1-F1-S1-T1 pass"):
            payload = _make_payload(f"uv run devbench {sub}")
            result = _run_hook(payload)
            assert result.returncode == 0, f"subcommand {sub!r} was incorrectly intercepted: {result.stderr}"

    def test_empty_command_does_not_crash(self) -> None:
        """A payload with no command field exits 0 cleanly."""
        payload = {"tool_name": "Bash", "tool_input": {}}
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_malformed_json_payload_does_not_crash(self) -> None:
        """A non-JSON stdin payload must not raise; the hook silently passes."""
        result = subprocess.run(
            ["bash", str(SCRIPT_PATH)],
            input="not json at all { unclosed",
            capture_output=True,
            text=True,
            env=_clean_env(),
        )
        assert result.returncode == 0


@pytest.mark.unit
class TestGuardCommentFormatPassthroughs:
    """Edge-case passthroughs: --help, missing args, shell meta-tokens."""

    def test_help_long_flag_passes_through(self) -> None:
        """--help anywhere after log-comment lets the CLI print usage."""
        payload = _make_payload("uv run devbench log-comment --help")
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_help_short_flag_passes_through(self) -> None:
        """-h anywhere after log-comment lets the CLI print usage."""
        payload = _make_payload("uv run devbench log-comment -h")
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_missing_message_arg_passes_through_to_cli(self) -> None:
        """When fewer than 3 positional args, defer to the CLI's own validation."""
        payload = _make_payload("uv run devbench log-comment executor")
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_shell_redirection_does_not_swallow_message(self) -> None:
        """Shell meta-tokens end positional parsing -- the message before > is what gets validated."""
        payload = _make_payload("uv run devbench log-comment executor E1-F1-S1-T1 'clean message' > /tmp/out.log 2>&1")
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_pipe_does_not_swallow_message(self) -> None:
        """Pipes after the message do not cause the message to be misread."""
        payload = _make_payload("uv run devbench log-comment executor E1-F1-S1-T1 'clean message' | tee /tmp/out.log")
        result = _run_hook(payload)
        assert result.returncode == 0


@pytest.mark.unit
class TestGuardCommentFormatRejectsForbiddenPhrases:
    """Each forbidden phrase MUST trigger exit 2 with stderr naming the offending phrase."""

    @pytest.mark.parametrize("phrase", FORBIDDEN_PHRASES)
    def test_each_forbidden_phrase_blocks_quoted_message(self, phrase: str) -> None:
        """Every phrase in the canonical list rejects the call when present in the quoted message."""
        message = f"some context. {phrase} now please."
        payload = _make_payload(f'uv run devbench log-comment executor E1-F1-S1-T1 "{message}"')
        result = _run_hook(payload)
        assert result.returncode == 2, (
            f"Phrase {phrase!r} was NOT rejected; stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert phrase in result.stderr.lower(), (
            f"Stderr did not name the offending phrase {phrase!r}: {result.stderr!r}"
        )

    @pytest.mark.parametrize("phrase", FORBIDDEN_PHRASES)
    def test_case_insensitive_match(self, phrase: str) -> None:
        """The match must be case-insensitive: uppercased / mixed-case phrases also reject."""
        message = f"context. {phrase.upper()} please."
        payload = _make_payload(f'uv run devbench log-comment executor E1-F1-S1-T1 "{message}"')
        result = _run_hook(payload)
        assert result.returncode == 2, f"UPPERCASE phrase {phrase!r} was NOT rejected; stderr={result.stderr!r}"

    def test_phrase_at_message_start_blocks(self) -> None:
        """A forbidden phrase at the very start of the message rejects."""
        message = "Halting orchestration: commit abc1234 included files outside its manifest."
        payload = _make_payload(f'uv run devbench log-comment executor E1-F1-S1-T1 "{message}"')
        result = _run_hook(payload)
        assert result.returncode == 2
        assert "halting orchestration" in result.stderr.lower()

    def test_phrase_at_message_end_blocks(self) -> None:
        """A forbidden phrase at the end of the message rejects."""
        message = "Diagnostic finding logged. Operator action required."
        payload = _make_payload(f'uv run devbench log-comment executor E1-F1-S1-T1 "{message}"')
        result = _run_hook(payload)
        assert result.returncode == 2
        assert "operator action required" in result.stderr.lower()

    def test_error_message_names_rewrite_examples(self) -> None:
        """Error stderr must include rewrite guidance, not just say 'rejected'."""
        message = "Halting orchestration: bad state."
        payload = _make_payload(f'uv run devbench log-comment executor E1-F1-S1-T1 "{message}"')
        result = _run_hook(payload)
        assert result.returncode == 2
        stderr_lower = result.stderr.lower()
        # The error must point the agent to the documented rule and offer
        # at least one rewrite example so the agent can self-correct.
        assert "skill" in stderr_lower or "executor.md" in stderr_lower or "halt-discipline" in stderr_lower
        assert "fix:" in stderr_lower

    def test_error_message_quotes_offending_phrase(self) -> None:
        """The stderr must quote the specific phrase that tripped the rule, not a generic message."""
        message = "Stop the loop until the operator reviews."
        payload = _make_payload(f'uv run devbench log-comment executor E1-F1-S1-T1 "{message}"')
        result = _run_hook(payload)
        assert result.returncode == 2
        assert "'stop the loop'" in result.stderr.lower()


@pytest.mark.unit
class TestGuardCommentFormatAcceptsCleanMessages:
    """Clean diagnostic messages without forbidden phrases pass through."""

    def test_clean_diagnostic_message_passes(self) -> None:
        """A factual diagnostic message with no halt-language exits 0."""
        message = (
            "Pollution detected: commit abc1234 staged 2 files outside its Changes Manifest. "
            "Recommended fix: revert the commit and re-run the source task."
        )
        payload = _make_payload(f'uv run devbench log-comment executor E1-F1-S1-T1 "{message}"')
        result = _run_hook(payload)
        assert result.returncode == 0, f"Clean message was rejected: {result.stderr}"

    def test_message_mentioning_halt_in_a_filename_passes(self) -> None:
        """A non-imperative use of halt-related words in identifiers does not match a forbidden phrase."""
        # 'halt_state.py' contains 'halt' but not any of the imperative phrases
        # in the forbidden list. Substring matching is on whole phrases.
        message = "Updated halt_state.py to record the new shutdown reason field."
        payload = _make_payload(f'uv run devbench log-comment executor E1-F1-S1-T1 "{message}"')
        result = _run_hook(payload)
        assert result.returncode == 0, f"Identifier mention was rejected: {result.stderr}"

    def test_message_mentioning_stop_outside_imperative_passes(self) -> None:
        """The standalone word 'stop' (not 'stop the loop') does not match."""
        message = "The git fault injection scenario simulates an EIO at the stop syscall boundary."
        payload = _make_payload(f'uv run devbench log-comment executor E1-F1-S1-T1 "{message}"')
        result = _run_hook(payload)
        assert result.returncode == 0

    def test_short_acknowledgement_passes(self) -> None:
        """The minimal completion comment used by the standard executor flow exits 0."""
        message = "implementation complete: 12 tests added, all green."
        payload = _make_payload(f'uv run devbench log-comment executor E1-F1-S1-T1 "{message}"')
        result = _run_hook(payload)
        assert result.returncode == 0


@pytest.mark.unit
class TestGuardCommentFormatPhraseListIntegrity:
    """Pin the canonical phrase list so silent drift breaks loudly.

    The hook script and this test must agree on the exact set of phrases. If
    a contributor adds, removes, or renames a phrase in the script, this test
    fails and forces the change to be acknowledged here too.
    """

    def test_script_contains_every_listed_phrase(self) -> None:
        """Every phrase in this test's FORBIDDEN_PHRASES must appear in the script body."""
        script_text = SCRIPT_PATH.read_text()
        for phrase in FORBIDDEN_PHRASES:
            assert f'"{phrase}"' in script_text, (
                f"Phrase {phrase!r} listed in tests but missing from {SCRIPT_PATH.name}; "
                "update the script's FORBIDDEN_PHRASES array OR remove the entry from the test list."
            )

    def test_phrase_list_is_non_empty(self) -> None:
        """A regression where the phrase list is emptied must fail this test."""
        assert len(FORBIDDEN_PHRASES) >= 5, "FORBIDDEN_PHRASES list shrank suspiciously; the hook would no-op."
