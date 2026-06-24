"""AC-15 FUNCTIONAL: a stub-claude quota prompt drives ``quota-waiting`` then resume.

Against the REAL ``stub-claude.py`` scripted to print a quota-limit prompt + a provider
reset line then exit (the poll-restart path 4.9b), the REAL ``pexpect`` supervisor:

- classifies the quota surface (NOT a fault) and transitions to ``quota-waiting``,
- PERSISTS ``state=quota-waiting`` with the parsed ``expected-resume`` and
  ``resumes-used`` so ``supervise status`` surfaces the holding state (FR-10, FR-16),
- delegates the wait to the SHARED ``quota.wait_for_reset`` (here a test-shortened
  window) and, on recovery, relaunches and reaches ``running`` / clean completion,
- NEVER exits non-zero for the quota event (Section 4.9, FR-13).

The wait window is shortened by injecting a fast stand-in for the SHARED
``quota.wait_for_reset`` (AC-15 explicitly allows a "test-shortened window"); the
stand-in asserts the registry already reflects ``quota-waiting`` + the expected resume
time at the instant the wait begins, proving the status surfacing is observable DURING
the wait (Goal G-3). That the supervisor uses the REAL shared primitive is asserted
separately by AC-32 (``test_supervise_quota_reuse.py``).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from functional.harness import functional_supervise_config, stub_sequence_env, supervised_stub

from devbench import cli
from devbench.supervise import SuperviseRegistry, format_status_line


@pytest.mark.functional
class TestStubQuotaWaitAndResume:
    """AC-15: quota prompt -> quota-waiting (status surfaced) -> resume -> clean."""

    def test_quota_surfaces_waiting_then_resumes_clean(self, tmp_path: Path) -> None:
        config = functional_supervise_config()
        state_file = tmp_path / "stub-seq.state"
        stub_env = stub_sequence_env(
            sequence="quota,clean",
            state_file=state_file,
            STUB_CLAUDE_QUOTA_RESET_LINE="resets 8:00am (UTC)",
        )

        captured: dict[str, object] = {}

        def fast_wait_for_reset(*, reset_at, poll_interval_seconds, max_wait_seconds):
            waiting = SuperviseRegistry(tmp_path).read_state("q1")
            captured["state_during_wait"] = waiting.state if waiting else None
            captured["expected_resume_during_wait"] = waiting.expected_resume if waiting else None
            captured["reset_at"] = reset_at
            return True

        with (
            supervised_stub(workspace_root=tmp_path, config=config, stub_env=stub_env),
            patch("devbench.quota.wait_for_reset", fast_wait_for_reset),
        ):
            rc = cli.cmd_supervise("__run", "--name", "q1", "--model", "claude-opus-4-8")

        assert rc == 0
        assert captured["state_during_wait"] == "quota-waiting"
        assert isinstance(captured["expected_resume_during_wait"], datetime)
        assert isinstance(captured["reset_at"], datetime)
        final = SuperviseRegistry(tmp_path).read_state("q1")
        assert final is not None
        assert final.state == "completed-clean"
        assert final.resumes_used == 1

    def test_status_line_shows_expected_resume_for_quota_waiting(self, tmp_path: Path) -> None:
        config = functional_supervise_config()
        state_file = tmp_path / "stub-seq.state"
        stub_env = stub_sequence_env(
            sequence="quota,clean",
            state_file=state_file,
            STUB_CLAUDE_QUOTA_RESET_LINE="resets 8:00am (UTC)",
        )
        captured_line: dict[str, str] = {}

        def fast_wait_for_reset(*, reset_at, poll_interval_seconds, max_wait_seconds):
            waiting = SuperviseRegistry(tmp_path).read_state("q2")
            assert waiting is not None
            captured_line["line"] = format_status_line(waiting, max_resumes=1000, in_progress=None)
            return True

        with (
            supervised_stub(workspace_root=tmp_path, config=config, stub_env=stub_env),
            patch("devbench.quota.wait_for_reset", fast_wait_for_reset),
        ):
            rc = cli.cmd_supervise("__run", "--name", "q2", "--model", "claude-opus-4-8")

        assert rc == 0
        line = captured_line["line"]
        assert "state=quota-waiting" in line
        assert "expected-resume=" in line
        assert "resumes-used=0/1000" in line
