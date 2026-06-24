"""supervise start waits for the NEW daemon to reach running, not the stale record.

Tracked issue: ``supervise-start-returns-early-prints-stale-record``.

When ``supervise start --name <N>`` is invoked over a PRIOR record (stopped /
faulted) from an earlier run, the command spawned a fresh daemon asynchronously
(``screen -dmS``) and then immediately read the registry -- which still held the
STALE prior record -- and printed it with EXIT 0. So an operator could not trust
``supervise start``'s stdout or exit code to reflect the real launch outcome.

These tests pin: ``start`` waits (bounded) for a FRESH record (started after the
launch began) to reach ``running`` and prints THAT; it fails fast (non-zero) if
the new daemon faults during startup; and a pre-existing stopped/faulted record is
never surfaced as the launch result.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench import cli
from devbench.config_loader import SuperviseConfig
from devbench.constants import (
    SUPERVISE_STATE_FAULTED,
    SUPERVISE_STATE_RUNNING,
    SUPERVISE_STATE_STOPPED,
)
from devbench.supervise import SuperviseRegistry, new_session_state

pytestmark = pytest.mark.unit


def _seed_prior_record(workspace: Path, *, name: str, state: str, pid: int, age_seconds: int) -> None:
    """Write a prior (older) registry record in *state* for *name*."""
    reg = SuperviseRegistry(workspace)
    st = new_session_state(
        name=name,
        pid=pid,
        screen_name=f"devbench-{name}",
        model="claude-opus-4-8",
        effort="xhigh",
        started_by="tester",
    )
    st.state = state
    st.started_at = datetime.now(UTC) - timedelta(seconds=age_seconds)
    if state == SUPERVISE_STATE_FAULTED:
        st.exit_reason = "prior fault"
    reg.write_state(st)


def _preflight_patches(workspace: Path):
    """Patches that take cmd_supervise start past preflight without real screen/claude."""
    return [
        patch("devbench.cli.WORKSPACE_ROOT", workspace),
        patch("devbench.cli._supervise_runtime_config", return_value=SuperviseConfig()),
        patch(
            "devbench.cli._supervise_preflight",
            return_value=("/usr/bin/claude", "/usr/bin/screen", "claude-opus-4-8", "xhigh", "subscription"),
        ),
        patch("devbench.cli._supervise_start_under_flock", return_value=0),
    ]


class TestStartWaitsForFreshRunning:
    """start blocks until a FRESH record reaches running, then prints the new pid."""

    def test_prior_stopped_record_is_not_printed_as_launch_result(
        self, workspace: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _seed_prior_record(workspace, name="telemetry", state=SUPERVISE_STATE_STOPPED, pid=111, age_seconds=3600)

        reg = SuperviseRegistry(workspace)
        reads = {"n": 0}
        real_read = reg.read_state

        def _staged_read(name: str):
            reads["n"] += 1
            if reads["n"] >= 3:
                fresh = new_session_state(
                    name=name,
                    pid=222,
                    screen_name=f"devbench-{name}",
                    model="claude-opus-4-8",
                    effort="xhigh",
                    started_by="tester",
                )
                fresh.state = SUPERVISE_STATE_RUNNING
                fresh.started_at = datetime.now(UTC)
                reg.write_state(fresh)
            return real_read(name)

        patches = _preflight_patches(workspace)
        with patches[0], patches[1], patches[2], patches[3]:
            with (
                patch("devbench.cli.SuperviseRegistry", return_value=reg),
                patch.object(reg, "read_state", side_effect=_staged_read),
                patch("devbench.cli._block_until_readable", lambda **kw: None),
            ):
                rc = cli._cmd_supervise_start(_make_args("telemetry"))

        out = capsys.readouterr().out
        assert rc == 0, "start must succeed once the new daemon reaches running"
        assert "pid=222" in out, f"start must report the NEW daemon's pid; got: {out!r}"
        assert "pid=111" not in out, "start must NOT print the stale prior record"
        assert "state=running" in out

    def test_startup_fault_fails_fast(self, workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """If the new daemon FAULTS during startup, start exits non-zero with the reason."""
        _seed_prior_record(workspace, name="telemetry", state=SUPERVISE_STATE_STOPPED, pid=111, age_seconds=3600)

        reg = SuperviseRegistry(workspace)
        real_read = reg.read_state

        def _staged_read(name: str):
            fresh = new_session_state(
                name=name,
                pid=222,
                screen_name=f"devbench-{name}",
                model="claude-opus-4-8",
                effort="xhigh",
                started_by="tester",
            )
            fresh.state = SUPERVISE_STATE_FAULTED
            fresh.started_at = datetime.now(UTC)
            fresh.exit_reason = "startup fault"
            reg.write_state(fresh)
            return real_read(name)

        patches = _preflight_patches(workspace)
        with patches[0], patches[1], patches[2], patches[3]:
            with (
                patch("devbench.cli.SuperviseRegistry", return_value=reg),
                patch.object(reg, "read_state", side_effect=_staged_read),
                patch("devbench.cli._block_until_readable", lambda **kw: None),
            ):
                rc = cli._cmd_supervise_start(_make_args("telemetry"))

        err = capsys.readouterr().err
        assert rc != 0, "a startup fault must fail fast (non-zero)"
        assert "startup fault" in err or "fault" in err.lower()

    def test_timeout_fails_fast_when_no_fresh_record(self, workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """If no fresh running record appears within the budget, start fails fast."""
        _seed_prior_record(workspace, name="telemetry", state=SUPERVISE_STATE_STOPPED, pid=111, age_seconds=3600)

        reg = SuperviseRegistry(workspace)
        patches = _preflight_patches(workspace)
        clock = {"t": 0.0}

        def _now() -> float:
            clock["t"] += 1000.0
            return clock["t"]

        with patches[0], patches[1], patches[2], patches[3]:
            with (
                patch("devbench.cli.SuperviseRegistry", return_value=reg),
                patch("devbench.cli._block_until_readable", lambda **kw: None),
                patch("devbench.cli.time.monotonic", _now),
            ):
                rc = cli._cmd_supervise_start(_make_args("telemetry"))

        out = capsys.readouterr().out
        assert rc != 0, "start must fail fast when the new daemon never reaches running"
        assert "pid=111" not in out, "start must never print the stale prior record"


def _make_args(name: str):
    """Build a minimal _SuperviseArgs for start."""
    from devbench.cli import _SuperviseArgs

    return _SuperviseArgs(name=name)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / ".devbench").mkdir(parents=True, exist_ok=True)
    return tmp_path
