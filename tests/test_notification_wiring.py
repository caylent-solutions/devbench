"""Lifecycle notification wiring (operator-block Slack-gap spec, AC-1/2/3/9).

Behaviour tests proving that the events the keystone coverage test pins as
*reachable* actually FIRE from the lifecycle paths the spec wires:

- AC-3 / G3: ``materialise_proposal`` fires ``work_unit_materialised`` once per
  draft created; every promote entry point (``promote_proposal`` and the three
  CLI ``promote`` paths) fires ``work_unit_promoted`` once per unit.
- AC-1 / AC-9 / G1: a ``blocked`` status write through the SHARED write surface
  (``force_status`` / the orchestrator ``set-status blocked`` / the done-gate
  refusal) routes through ``notify_blocked_classification_transition`` -- not
  only ``mark_blocked``.
- AC-2 / G2: a unit that leaves ``blocked`` has its transition-cache entry
  invalidated so a genuine re-block into the same class re-notifies.
- AC-8: a notifier failure never breaks the status write (best-effort guard).

Every notify helper is patched at the module attribute the dispatcher actually
invokes; ``is_event_enabled`` is patched so the tests do not depend on
``RUNTIME_CONFIG`` disk state.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench.backlog import proposal as proposal_mod
from devbench.backlog.manager import BacklogManager
from devbench.backlog.proposal import (
    materialise_proposal,
    promote_all_from_source,
    promote_proposal,
    write_proposal,
)
from devbench.constants import STATUS_BLOCKED, STATUS_IN_QUEUE
from devbench.notifications import NOTIFICATION_STATE_FILENAME

# ---------------------------------------------------------------------------
# Minimal workspace fixtures (workspace-agnostic, AC-10)
# ---------------------------------------------------------------------------

_SOURCE_ROW = (
    "| E0-F1-S1-T1 | Source Task | Task | blocked | None "
    "| caylent-solutions/example | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |"
)

_BACKLOG_TEMPLATE = (
    "# Backlog\n\n"
    "## Status Summary\n\n"
    "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
    "|------|-------|------|-------------|----------|---------|\n"
    "| E0 | Example Epic | 0 | 0 | 1 | 0 |\n\n"
    "## Full Work Unit Index\n\n"
    "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
    "|----|-------|------|--------|--------------|------|-----------|\n"
    f"{_SOURCE_ROW}\n"
)

_SOURCE_TASK_TEMPLATE = """\
# E0-F1-S1-T1: Source Task

## Status: blocked

## Target Repository

- **Repo:** `caylent-solutions/example`

## Description

original source task.

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| none | | |

## Acceptance Criteria

- [ ] AC-TEST-001 cover the edge case

## Changes Manifest

| File | Change |
|------|--------|
| `tests/test_example.py` | new unit tests |

## Definition of Done

- [ ] all AC complete
"""

_INDEX_HEADER = (
    "# Backlog\n\n"
    "## Full Work Unit Index\n\n"
    "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
    "|-----|-------|------|--------|-------------|------|-----------|\n"
)


def _build_proposal_workspace(tmp_path: Path) -> Path:
    """Create a minimal workspace with one blocked source task."""
    (tmp_path / "BACKLOG.md").write_text(_BACKLOG_TEMPLATE)
    story_dir = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
    story_dir.mkdir(parents=True)
    (story_dir / "E0-F1-S1-T1.md").write_text(_SOURCE_TASK_TEMPLATE)
    return tmp_path


def _sample_proposal(*, task_ids: list[str]) -> proposal_mod.Proposal:
    tasks = [
        proposal_mod.ProposedTask(
            suggested_id=tid,
            title=f"Proposed Task {i}",
            files_to_own=[f"src/{tid}.py"],
            linked_scenarios=[f"SC-{i:02d}"],
            suggested_acs=[f"AC-FUNC-{i:03d} fix the scenario"],
            suggested_approach=(
                f"Context: Scenario SC-{i:02d} failed against the current implementation. "
                "Scope: One production file and its companion unit test. "
                f"TDD approach: 1. RED -- Reproduce SC-{i:02d} locally in a unit test. "
                "2. GREEN -- Apply the minimal fix in the production module. "
                "3. REFACTOR -- Clean up without changing behaviour. "
                "Verify: make lint && make format-check && make test-unit && make test-integration "
                "all exit zero."
            ),
        )
        for i, tid in enumerate(task_ids, start=1)
    ]
    return proposal_mod.Proposal(
        source_task_id="E0-F1-S1-T1",
        generated_at="2026-04-18T03:25:00Z",
        rejection_reason="scope creep fixes are unrelated to source task",
        proposed_tasks=tasks,
    )


def _patch_runtime_config_in_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``materialise_proposal``'s config lookup to 'in-queue' new-WU status."""
    from devbench.config_loader import BacklogConfig, RuntimeConfig

    fake_config = RuntimeConfig.__new__(RuntimeConfig)
    object.__setattr__(fake_config, "backlog", BacklogConfig(default_status_for_new_work_units="in-queue"))
    monkeypatch.setattr(proposal_mod, "_get_runtime_config", lambda: fake_config)


def _make_task_wu(tmp_path: Path, unit_id: str, status: str = "in-queue") -> Path:
    wu = tmp_path / f"{unit_id}.md"
    wu.write_text(f"# {unit_id}: Test Task\n\n## Status: {status}\n\n## Comments\n\n")
    return wu


def _make_task_index(tmp_path: Path, unit_id: str, status: str = "in-queue") -> Path:
    index = tmp_path / "BACKLOG.md"
    index.write_text(
        _INDEX_HEADER + f"| {unit_id} | Test Task | Task | {status} | None | git-repo | `backlog/{unit_id}.md` |\n"
    )
    return index


def _cache_path(workspace_root: Path) -> Path:
    return workspace_root / ".devbench" / NOTIFICATION_STATE_FILENAME


# ---------------------------------------------------------------------------
# AC-3 / G3: materialise + promote fire their events
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMaterialiseFiresEvent:
    """``materialise_proposal`` fires ``work_unit_materialised`` per draft (AC-3)."""

    def test_one_materialised_ping_per_draft(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_runtime_config_in_queue(monkeypatch)
        workspace = _build_proposal_workspace(tmp_path)
        proposal = _sample_proposal(task_ids=["E0-F1-S1-T2", "E0-F1-S1-T3"])

        with patch("devbench.notifications.notify_work_unit_materialised") as materialised:
            drafts = materialise_proposal(
                workspace_root=workspace,
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                proposal=proposal,
                repo="caylent-solutions/example",
            )

        assert len(drafts) == 2
        assert materialised.call_count == 2
        materialised_ids = {call.args[0] for call in materialised.call_args_list}
        assert materialised_ids == {"E0-F1-S1-T2", "E0-F1-S1-T3"}
        # Source task id carried through for the payload's "From source" field.
        assert all(call.args[2] == "E0-F1-S1-T1" for call in materialised.call_args_list)


@pytest.mark.unit
class TestPromoteFiresEvent:
    """Every promote entry point fires ``work_unit_promoted`` once per unit (AC-3)."""

    def test_promote_proposal_fires_once(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_runtime_config_in_queue(monkeypatch)
        workspace = _build_proposal_workspace(tmp_path)
        proposal = _sample_proposal(task_ids=["E0-F1-S1-T2"])
        write_proposal(workspace, proposal)
        materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        with patch("devbench.notifications.notify_work_unit_promoted") as promoted:
            promote_proposal(
                workspace_root=workspace,
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                task_id="E0-F1-S1-T2",
            )
        promoted.assert_called_once()
        assert promoted.call_args.args[0] == "E0-F1-S1-T2"

    def test_promote_all_from_source_fires_per_unit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The bulk path loops ``promote_proposal`` -> one ping per unit, no double-fire."""
        _patch_runtime_config_in_queue(monkeypatch)
        workspace = _build_proposal_workspace(tmp_path)
        proposal = _sample_proposal(task_ids=["E0-F1-S1-T2", "E0-F1-S1-T3"])
        write_proposal(workspace, proposal)
        materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        with patch("devbench.notifications.notify_work_unit_promoted") as promoted:
            promote_all_from_source(
                workspace_root=workspace,
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                source_task_id="E0-F1-S1-T1",
            )
        assert promoted.call_count == 2
        promoted_ids = {call.args[0] for call in promoted.call_args_list}
        assert promoted_ids == {"E0-F1-S1-T2", "E0-F1-S1-T3"}


# ---------------------------------------------------------------------------
# AC-1 / AC-9 / G1: blocked notification routes off the shared write surface
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBlockedRoutesOffWriteSurface:
    """Any ``blocked`` write -- not only ``mark_blocked`` -- reaches the dispatcher."""

    def test_force_status_blocked_routes_through_dispatcher(self, tmp_path: Path) -> None:
        """AC-9: ``force_status <id> blocked`` (the orchestrator / done-gate-refusal
        path) reaches ``notify_blocked_classification_transition`` -- it no longer
        only fires from ``mark_blocked``."""
        wu = _make_task_wu(tmp_path, "E0-F1-S1-T1")
        index = _make_task_index(tmp_path, "E0-F1-S1-T1")
        with patch("devbench.notifications.notify_blocked_classification_transition") as dispatch:
            BacklogManager().force_status(wu, index, "E0-F1-S1-T1", STATUS_BLOCKED)
        dispatch.assert_called_once()
        # classification arg (index 3) is the BlockedTaskState name; a bare
        # blocked task with no co-blocker classifies OPERATOR_ACTION_REQUIRED.
        assert dispatch.call_args.args[0] == "E0-F1-S1-T1"
        assert dispatch.call_args.args[3] == "OPERATOR_ACTION_REQUIRED"

    def test_mark_blocked_still_routes_through_dispatcher(self, tmp_path: Path) -> None:
        """The original ``mark_blocked`` path still fires exactly one ping (no regression,
        no double-fire now that the notification lives on the write surface)."""
        wu = _make_task_wu(tmp_path, "E0-F1-S1-T1")
        index = _make_task_index(tmp_path, "E0-F1-S1-T1")
        with patch("devbench.notifications.notify_blocked_classification_transition") as dispatch:
            BacklogManager().mark_blocked(wu, index, "E0-F1-S1-T1", "done-gate refused mark-done")
        dispatch.assert_called_once()
        # The reason flows from the [BLOCKED] audit comment written just before
        # the status flip, so the Slack context carries this call's reason.
        assert "done-gate refused mark-done" in dispatch.call_args.args[2]

    def test_operator_blocked_ping_fires_once_at_block_time(self, tmp_path: Path) -> None:
        """AC-1: with the toggle enabled, the per-class operator helper fires once."""
        wu = _make_task_wu(tmp_path, "E0-F1-S1-T1")
        index = _make_task_index(tmp_path, "E0-F1-S1-T1")
        with (
            patch("devbench.notifications.is_event_enabled", return_value=True),
            patch("devbench.notifications.notify_work_unit_blocked_operator") as operator_ping,
        ):
            BacklogManager().force_status(wu, index, "E0-F1-S1-T1", STATUS_BLOCKED)
        operator_ping.assert_called_once()
        assert operator_ping.call_args.args[0] == "E0-F1-S1-T1"

    def test_notifier_failure_does_not_break_status_write(self, tmp_path: Path) -> None:
        """AC-8: a notifier exception is swallowed; the status write still lands."""
        wu = _make_task_wu(tmp_path, "E0-F1-S1-T1")
        index = _make_task_index(tmp_path, "E0-F1-S1-T1")
        with patch(
            "devbench.notifications.notify_blocked_classification_transition",
            side_effect=OSError("slack down"),
        ):
            BacklogManager().force_status(wu, index, "E0-F1-S1-T1", STATUS_BLOCKED)
        # Status write succeeded despite the notifier raising.
        assert "## Status: blocked" in wu.read_text()


# ---------------------------------------------------------------------------
# AC-2 / G2: leave-blocked invalidates the cache so a re-block re-notifies
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLeaveBlockedInvalidatesCache:
    """A unit that exits ``blocked`` and re-enters the same class re-fires (AC-2)."""

    def test_set_status_out_of_blocked_drops_cache_entry(self, tmp_path: Path) -> None:
        wu = _make_task_wu(tmp_path, "E0-F1-S1-T1")
        index = _make_task_index(tmp_path, "E0-F1-S1-T1")
        mgr = BacklogManager()

        # First block writes the cache entry (toggle on so it fires + caches).
        with (
            patch("devbench.notifications.is_event_enabled", return_value=True),
            patch("devbench.notifications.notify_work_unit_blocked_operator"),
        ):
            mgr.force_status(wu, index, "E0-F1-S1-T1", STATUS_BLOCKED)
        cache = json.loads(_cache_path(tmp_path).read_text(encoding="utf-8"))
        assert cache == {"E0-F1-S1-T1": "OPERATOR_ACTION_REQUIRED"}

        # Requeue OUT of blocked -> the write-surface hook invalidates the entry.
        mgr.force_status(wu, index, "E0-F1-S1-T1", STATUS_IN_QUEUE)
        cache = json.loads(_cache_path(tmp_path).read_text(encoding="utf-8"))
        assert "E0-F1-S1-T1" not in cache

    def test_reblock_after_leaving_refires_operator_ping(self, tmp_path: Path) -> None:
        """blocked(OPERATOR) -> in-queue -> blocked(OPERATOR) fires the ping twice."""
        wu = _make_task_wu(tmp_path, "E0-F1-S1-T1")
        index = _make_task_index(tmp_path, "E0-F1-S1-T1")
        mgr = BacklogManager()
        with (
            patch("devbench.notifications.is_event_enabled", return_value=True),
            patch("devbench.notifications.notify_work_unit_blocked_operator") as operator_ping,
        ):
            mgr.force_status(wu, index, "E0-F1-S1-T1", STATUS_BLOCKED)
            assert operator_ping.call_count == 1
            mgr.force_status(wu, index, "E0-F1-S1-T1", STATUS_IN_QUEUE)
            mgr.force_status(wu, index, "E0-F1-S1-T1", STATUS_BLOCKED)
            assert operator_ping.call_count == 2
