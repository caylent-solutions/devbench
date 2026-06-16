"""sessions --cleanup auto-recovers a dead session's orphaned in-progress unit.

Tracked issue: ``dead-session-leaves-claimed-unit-stuck-in-progress-no-auto-recovery``.

When an orchestrator daemon dies without a clean SIGTERM stop, the unit it had set
to ``in-progress`` ([WU_CLAIMED] session=<name>) is left in-progress indefinitely.
``sessions --cleanup`` removed the stale registry entry but left the claimed unit
stuck, blocking dependents and tripping the Stop hook on every later session.

The corrected contract: ``sessions --cleanup`` re-queues any ``in-progress`` unit
whose latest [WU_CLAIMED] audit names a now-dead session, emitting an explicit
[REQUEUED_AFTER_DEAD_SESSION] audit comment. The recovery cross-checks pid liveness
via the registry so it never re-queues a unit a LIVE session is actively working.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench import cli
from devbench.session import Session, SessionRegistry

pytestmark = pytest.mark.unit

_DEAD_PID = 99999999


def _write_unit(backlog_dir: Path, unit_id: str, *, status: str, claimed_by: str | None) -> Path:
    """Write a work-unit .md whose Comments carry a [WU_CLAIMED] session= audit."""
    story_dir = backlog_dir
    story_dir.mkdir(parents=True, exist_ok=True)
    claim_line = ""
    if claimed_by is not None:
        claim_line = (
            f"- [2026-06-15 18:44 UTC] [agent/orchestrator] [WU_CLAIMED] "
            f"Set {unit_id} to 'in-progress' session={claimed_by}\n"
        )
    wu = story_dir / f"{unit_id}.md"
    wu.write_text(
        f"# {unit_id}: Orphan candidate\n\n## Status: {status}\n\n## Comments\n\n{claim_line}",
        encoding="utf-8",
    )
    return wu


def _write_index(workspace: Path, rows: list[tuple[str, str]]) -> Path:
    """Write BACKLOG.md with one Full Work Unit Index row per (unit_id, status)."""
    index = workspace / "BACKLOG.md"
    body = [
        "# Backlog\n",
        "## Full Work Unit Index\n",
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |",
        "|-----|-------|------|--------|-------------|------|-----------|",
    ]
    for unit_id, status in rows:
        body.append(f"| {unit_id} | Orphan candidate | Task | {status} | None | git-repo | `backlog/{unit_id}.md` |")
    index.write_text("\n".join(body) + "\n", encoding="utf-8")
    return index


def _register_session(workspace: Path, *, name: str, pid: int, scope: list[str]) -> None:
    reg = SessionRegistry(workspace)
    state_dir = workspace / ".devbench" / "sessions" / name
    state_dir.mkdir(parents=True, exist_ok=True)
    sessions = reg.load()
    sessions.append(
        Session(
            name=name,
            pid=pid,
            scope=scope,
            started_at=datetime(2026, 6, 15, 18, 0, 0, tzinfo=UTC),
            started_by="tester",
            state_dir=state_dir,
        )
    )
    reg.save(sessions)


def _run_cleanup(workspace: Path, backlog_dir: Path) -> int:
    with (
        patch("devbench.cli.WORKSPACE_ROOT", workspace),
        patch("devbench.cli.BACKLOG_ROOT", backlog_dir),
        patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
        # The dead pid must look dead; the live pid is os.getpid().
        patch("devbench.session.os.kill", side_effect=_fake_kill),
    ):
        return cli.cmd_sessions("--cleanup")


def _fake_kill(pid: int, sig: int) -> None:
    if pid == _DEAD_PID:
        raise ProcessLookupError


class TestDeadSessionOrphanRecovery:
    """sessions --cleanup re-queues a dead session's orphaned in-progress unit."""

    def test_orphan_in_progress_unit_is_requeued(self, tmp_path: Path) -> None:
        workspace = tmp_path
        (workspace / ".devbench").mkdir(parents=True, exist_ok=True)
        backlog_dir = workspace / "backlog"
        _write_unit(backlog_dir, "E1-F1-S1-T2", status="in-progress", claimed_by="serial")
        _write_index(workspace, [("E1-F1-S1-T2", "in-progress")])
        _register_session(workspace, name="serial", pid=_DEAD_PID, scope=["E1-F1-S1-T2"])

        rc = _run_cleanup(workspace, backlog_dir)
        assert rc == 0

        wu_text = (backlog_dir / "E1-F1-S1-T2.md").read_text(encoding="utf-8")
        # The orphaned unit must no longer be in-progress.
        assert "## Status: in-queue" in wu_text, "the orphaned in-progress unit must be re-queued"
        # An explicit recovery audit marker must be written.
        assert "[REQUEUED_AFTER_DEAD_SESSION]" in wu_text
        index_text = (workspace / "BACKLOG.md").read_text(encoding="utf-8")
        assert "| E1-F1-S1-T2 | Orphan candidate | Task | in-queue |" in index_text

    def test_live_session_unit_is_not_requeued(self, tmp_path: Path) -> None:
        """A unit a LIVE session holds in scope is never re-queued (liveness cross-check)."""
        workspace = tmp_path
        (workspace / ".devbench").mkdir(parents=True, exist_ok=True)
        backlog_dir = workspace / "backlog"
        # T2 is in-progress claimed by a LIVE session; T3 by a dead one.
        _write_unit(backlog_dir, "E1-F1-S1-T2", status="in-progress", claimed_by="live")
        _write_unit(backlog_dir, "E1-F1-S1-T3", status="in-progress", claimed_by="dead")
        _write_index(workspace, [("E1-F1-S1-T2", "in-progress"), ("E1-F1-S1-T3", "in-progress")])
        _register_session(workspace, name="live", pid=os.getpid(), scope=["E1-F1-S1-T2"])
        _register_session(workspace, name="dead", pid=_DEAD_PID, scope=["E1-F1-S1-T3"])

        rc = _run_cleanup(workspace, backlog_dir)
        assert rc == 0

        live_text = (backlog_dir / "E1-F1-S1-T2.md").read_text(encoding="utf-8")
        dead_text = (backlog_dir / "E1-F1-S1-T3.md").read_text(encoding="utf-8")
        # The live session's unit is untouched.
        assert "## Status: in-progress" in live_text
        assert "[REQUEUED_AFTER_DEAD_SESSION]" not in live_text
        # The dead session's unit is recovered.
        assert "## Status: in-queue" in dead_text
        assert "[REQUEUED_AFTER_DEAD_SESSION]" in dead_text

    def test_no_dead_sessions_leaves_in_progress_untouched(self, tmp_path: Path) -> None:
        """With no dead sessions, an in-progress unit claimed by a live session is left alone."""
        workspace = tmp_path
        (workspace / ".devbench").mkdir(parents=True, exist_ok=True)
        backlog_dir = workspace / "backlog"
        _write_unit(backlog_dir, "E1-F1-S1-T2", status="in-progress", claimed_by="live")
        _write_index(workspace, [("E1-F1-S1-T2", "in-progress")])
        _register_session(workspace, name="live", pid=os.getpid(), scope=["E1-F1-S1-T2"])

        rc = _run_cleanup(workspace, backlog_dir)
        assert rc == 0

        wu_text = (backlog_dir / "E1-F1-S1-T2.md").read_text(encoding="utf-8")
        assert "## Status: in-progress" in wu_text
        assert "[REQUEUED_AFTER_DEAD_SESSION]" not in wu_text

    def test_in_progress_unit_with_no_claim_audit_is_left_alone(self, tmp_path: Path) -> None:
        """An in-progress unit with no [WU_CLAIMED] session= audit is not re-queued.

        Attribution is by claim audit; a unit with no recorded claiming session
        cannot be attributed to the dead session, so it is left untouched.
        """
        workspace = tmp_path
        (workspace / ".devbench").mkdir(parents=True, exist_ok=True)
        backlog_dir = workspace / "backlog"
        _write_unit(backlog_dir, "E1-F1-S1-T2", status="in-progress", claimed_by=None)
        _write_index(workspace, [("E1-F1-S1-T2", "in-progress")])
        _register_session(workspace, name="dead", pid=_DEAD_PID, scope=["E1-F1-S1-T2"])

        rc = _run_cleanup(workspace, backlog_dir)
        assert rc == 0
        wu_text = (backlog_dir / "E1-F1-S1-T2.md").read_text(encoding="utf-8")
        assert "## Status: in-progress" in wu_text
        assert "[REQUEUED_AFTER_DEAD_SESSION]" not in wu_text


class TestDeadSessionRecoveryUnit:
    """Direct unit coverage of the recovery helper's guard branches."""

    def test_empty_dead_set_is_noop(self) -> None:
        from devbench import cli

        assert cli._recover_orphaned_units_from_dead_sessions(dead_session_names=set(), surviving_sessions=[]) == []

    def test_unreadable_backlog_returns_empty(self, tmp_path: Path) -> None:
        from unittest.mock import patch

        from devbench import cli

        # BACKLOG_ROOT/INDEX point at a non-existent tree -> parse raises -> [].
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "missing"),
            patch("devbench.cli.BACKLOG_INDEX", tmp_path / "missing" / "BACKLOG.md"),
        ):
            result = cli._recover_orphaned_units_from_dead_sessions(dead_session_names={"dead"}, surviving_sessions=[])
        assert result == []


class TestDeadSessionRecoveryConstant:
    """The recovery audit marker is a constant (no hard-coded literal)."""

    def test_requeued_after_dead_session_prefix_is_importable(self) -> None:
        from devbench.constants import REQUEUED_AFTER_DEAD_SESSION_AUDIT_PREFIX

        assert REQUEUED_AFTER_DEAD_SESSION_AUDIT_PREFIX.startswith("[REQUEUED_AFTER_DEAD_SESSION]")
