"""End-to-end tests for the task-factory proposal lifecycle.

Scenarios covered:

- A blocker-resolver proposal with two proposed tasks is written, materialised,
  promoted, and visible as in-queue in the BACKLOG.md index.
- A proposal is rejected, its draft archived, and the BACKLOG.md row removed.
- Re-blocking a source task whose prior proposed tasks are unresolved is a no-op
  (materialise raises ProposalError); the skip prevents duplicate proposals.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from devbench import cli
from devbench.backlog import proposal as proposal_mod
from devbench.backlog.proposal import (
    Proposal,
    ProposedTask,
    _append_backlog_row,
    _render_backlog_row,
    list_proposals,
    materialise_proposal,
    reject_proposal,
    write_proposal,
)

_SOURCE_ROW = (
    "| E0-F1-S1-T1 | Source Task | Task | blocked | None "
    "| caylent-solutions/example | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |"
)

_BACKLOG_TEMPLATE = (
    "# Backlog\n\n"
    "## Status Summary\n\n"
    "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
    "|------|-------|------|-------------|----------|---------|\n"
    "| E0 | Example Epic | 0 | 0 | 1 | 1 |\n\n"
    "## Full Work Unit Index\n\n"
    "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
    "|----|-------|------|--------|--------------|------|-----------|\n"
    f"{_SOURCE_ROW}\n"
)

_SOURCE_TEMPLATE = """\
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

- [ ] AC-TEST-001 cover

## Changes Manifest

| File | Change |
|------|--------|
| `tests/test_example.py` | new unit tests |

## Definition of Done

- [ ] AC complete
"""


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "BACKLOG.md").write_text(_BACKLOG_TEMPLATE)
    story_dir = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
    story_dir.mkdir(parents=True)
    (story_dir / "E0-F1-S1-T1.md").write_text(_SOURCE_TEMPLATE)
    return tmp_path


def _fake_config_with_status(status: str) -> object:
    """Return a RuntimeConfig-like object with the given default_status_for_new_work_units.

    Used to monkeypatch ``proposal_mod._get_runtime_config`` in tests that
    need to control the status written into new draft files by
    ``materialise_proposal``.
    """
    from devbench.config_loader import BacklogConfig, RuntimeConfig

    cfg = RuntimeConfig.__new__(RuntimeConfig)
    object.__setattr__(cfg, "backlog", BacklogConfig(default_status_for_new_work_units=status))
    return cfg


def _proposal(source_id: str = "E0-F1-S1-T1") -> Proposal:
    return Proposal(
        source_task_id=source_id,
        generated_at="2026-04-18T03:25:00Z",
        rejection_reason="unrelated scope",
        proposed_tasks=[
            ProposedTask(
                suggested_id="E0-F1-S1-T2",
                title="Fix scenario A",
                files_to_own=["src/fix_a.py"],
                linked_scenarios=["SC-01"],
                suggested_acs=["AC-FUNC-001 fix A"],
                suggested_approach=(
                    "Context: SC-01 fails because src/fix_a.py raises on an unexpected input. "
                    "Scope: src/fix_a.py and its unit test. "
                    "TDD approach: 1. RED -- reproduce SC-01 in a unit test. "
                    "2. GREEN -- minimal fix in fix_a.py. 3. REFACTOR -- no-op. "
                    "Verify: make lint && make format-check && make test-unit all exit zero."
                ),
            ),
            ProposedTask(
                suggested_id="E0-F1-S1-T3",
                title="Fix scenario B",
                files_to_own=["src/fix_b.py"],
                linked_scenarios=["SC-02"],
                suggested_acs=["AC-FUNC-002 fix B"],
                suggested_approach=(
                    "Context: SC-02 fails because src/fix_b.py mishandles the path resolve step. "
                    "Scope: src/fix_b.py and its unit test. "
                    "TDD approach: 1. RED -- reproduce SC-02 in a unit test. "
                    "2. GREEN -- minimal fix in fix_b.py. 3. REFACTOR -- no-op. "
                    "Verify: make lint && make format-check && make test-unit all exit zero."
                ),
            ),
        ],
    )


class TestTaskFactoryLifecycleHappyPath:
    def test_generate_and_promote_all(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        proposal = _proposal()
        write_proposal(workspace, proposal)

        drafts = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        assert len(drafts) == 2
        backlog_text = (workspace / "BACKLOG.md").read_text()
        assert "E0-F1-S1-T2" in backlog_text
        assert "E0-F1-S1-T3" in backlog_text
        # list_proposals surfaces the pending proposal.
        assert list_proposals(workspace)

        # Promote every draft from the CLI (--all-from).
        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", workspace / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
        ):
            rc = cli.cmd_promote_proposal("--all-from", "E0-F1-S1-T1")
        assert rc == 0
        backlog_text = (workspace / "BACKLOG.md").read_text()
        # Proposed rows flipped to in-queue.
        assert "| E0-F1-S1-T2 | Fix scenario A | Task | in-queue " in backlog_text
        assert "| E0-F1-S1-T3 | Fix scenario B | Task | in-queue " in backlog_text

    def test_source_auto_requeues_when_all_promoted_deps_complete(self, tmp_path: Path) -> None:
        """End-to-end: block -> promote -> mark_done all deps -> source auto-requeues.

        This is the lifecycle the marker-based auto-requeue was designed for.
        The source task starts blocked; task-factory generates two drafts;
        the operator promotes both; as each draft is later marked done via
        the standard lifecycle, the source's blocked state clears without
        manual intervention once every draft is terminal.

        Covers:
          1. Promotion writes one ``[BLOCKED_PENDING_PROPOSAL]`` marker per draft.
          2. Partial completion (first draft done, second still in-queue)
             keeps the source blocked.
          3. Full completion auto-flips the source to ``in-queue`` and
             writes an ``[AUTO_UNBLOCKED]`` audit comment naming both drafts.
        """
        from devbench.backlog.manager import BacklogManager
        from devbench.constants import ALL_REQUIRED_JUDGE_NAMES

        workspace = _workspace(tmp_path)
        proposal = _proposal()
        write_proposal(workspace, proposal)
        materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )

        # Promote both drafts (the default --all-from path).
        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", workspace / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
        ):
            rc = cli.cmd_promote_proposal("--all-from", "E0-F1-S1-T1")
        assert rc == 0

        source_path = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        source_text = source_path.read_text()

        # (1) Both markers landed on the source.
        assert source_text.count("[BLOCKED_PENDING_PROPOSAL]") == 2
        assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2" in source_text
        assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T3" in source_text
        # Source is still blocked; promotion does not flip its status.
        assert "## Status: blocked" in source_text

        # Simulate the standard lifecycle of the first promoted draft: add
        # review-pass entries so mark_done satisfies the done-gate, then
        # mark it done.
        mgr = BacklogManager()
        draft_paths = {
            "E0-F1-S1-T2": workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T2.md",
            "E0-F1-S1-T3": workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T3.md",
        }

        def _stamp_reviews(draft: Path) -> None:
            # Declare an exempt Task Type so this auto-requeue-cascade test
            # exercises only the judges-passed done-gate, not the separate
            # FR-4.5/FR-4.6 task-type invariant BacklogManager.mark_done()
            # now also enforces directly (E4-F4-S1-T2 round 3: the check
            # used to live only in cli.py's cmd_mark_done wrapper; a
            # materialised draft with no ``## Task Type:`` section would
            # otherwise default to the strictest gated type and this
            # unrelated cascade test would fail for the wrong reason).
            content = draft.read_text()
            content = content.replace("## Status: in-queue\n", "## Status: in-queue\n\n## Task Type: chore\n", 1)
            review_block = "\n".join(
                f"[2026-04-19 14:00 UTC] [judge/{judge}] [REVIEW_PASS] ok" for judge in ALL_REQUIRED_JUDGE_NAMES
            )
            draft.write_text(content + "\n" + review_block + "\n")

        # (2) Partial: mark first draft done -- source stays blocked.
        _stamp_reviews(draft_paths["E0-F1-S1-T2"])
        mgr.mark_done(draft_paths["E0-F1-S1-T2"], workspace / "BACKLOG.md", "E0-F1-S1-T2")
        after_first = source_path.read_text()
        assert "## Status: blocked" in after_first
        assert "[AUTO_UNBLOCKED]" not in after_first

        # (3) Full: mark second draft done -- source auto-flips to in-queue.
        _stamp_reviews(draft_paths["E0-F1-S1-T3"])
        mgr.mark_done(draft_paths["E0-F1-S1-T3"], workspace / "BACKLOG.md", "E0-F1-S1-T3")
        after_second = source_path.read_text()
        assert "## Status: in-queue" in after_second
        assert "[AUTO_UNBLOCKED]" in after_second
        # Audit comment names both promoted drafts.
        unblocked_line_idx = after_second.index("[AUTO_UNBLOCKED]")
        tail = after_second[unblocked_line_idx:]
        assert "E0-F1-S1-T2" in tail
        assert "E0-F1-S1-T3" in tail


class TestTaskFactoryLifecycleRejectPath:
    def test_reject_archives_draft_and_removes_row(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        proposal = _proposal()
        write_proposal(workspace, proposal)
        materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        archive = reject_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
            reason="not needed; behavior is intentional",
        )
        assert archive is not None and archive.is_file()
        assert archive.parent == workspace / ".devbench" / "rejected-proposals"
        backlog_text = (workspace / "BACKLOG.md").read_text()
        assert "E0-F1-S1-T2" not in backlog_text
        source_md = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        assert "[PROPOSAL_REJECTED]" in source_md.read_text()


class TestUnmaterialisedRejectLifecycle:
    """ADR-08 slice E end-to-end: un-materialised JSON -> report panel -> reject -> archive + comment."""

    def test_reject_unmaterialised_full_lifecycle(self, tmp_path: Path) -> None:
        """JSON on disk is visible in the report panel, then rejected via ``--unmaterialised``.

        This verifies slices B/C (surface in status + report), E (reject), and audit comment
        compose cleanly. No draft .md is ever created; the JSON goes straight from written
        to archived.
        """
        import devbench.reporting.report as report_mod
        from devbench.backlog.proposal import (
            PROPOSAL_DIR_NAME,
            REJECTED_PROPOSAL_DIR_NAME,
            ProposalTaskState,
            classify_proposed_task,
        )

        workspace = _workspace(tmp_path)
        proposal = _proposal()
        write_proposal(workspace, proposal)

        # Precondition: both proposed tasks classify as UNMATERIALISED.
        for task in proposal.proposed_tasks:
            state = classify_proposed_task(workspace / "backlog", workspace, task.suggested_id)
            assert state is ProposalTaskState.UNMATERIALISED

        # Report panel renders the entries.
        with patch.object(report_mod, "BACKLOG_ROOT", workspace / "backlog"):
            lines = report_mod._unmaterialised_proposals_listing()
        assert any("E0-F1-S1-T2" in line for line in lines)
        assert any("E0-F1-S1-T3" in line for line in lines)

        # CLI count reflects both un-materialised tasks.
        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", workspace / "backlog"),
        ):
            assert cli._count_unmaterialised_proposed_tasks() == 2

        # Reject the entire JSON via the new --unmaterialised form.
        archive = reject_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            unmaterialised_source_id="E0-F1-S1-T1",
            reason="redundant with existing in-flight work",
        )

        # Archive was written under rejected-proposals/ with the -unmaterialised- infix.
        assert archive is not None and archive.is_file()
        assert archive.parent == workspace / REJECTED_PROPOSAL_DIR_NAME
        assert "unmaterialised" in archive.name

        # Live JSON is gone.
        assert not (workspace / PROPOSAL_DIR_NAME / "E0-F1-S1-T1.json").exists()

        # Audit comment landed on the source task.
        source_md = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        src_text = source_md.read_text()
        assert "[PROPOSAL_JSON_REJECTED]" in src_text
        assert "redundant with existing in-flight work" in src_text

        # Panel is empty on the next report render.
        with patch.object(report_mod, "BACKLOG_ROOT", workspace / "backlog"):
            assert report_mod._unmaterialised_proposals_listing() == []
        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", workspace / "backlog"),
        ):
            assert cli._count_unmaterialised_proposed_tasks() == 0

        # And no pending proposals remain on disk.
        assert list_proposals(workspace) == []


class TestAffectedTaskIdsLifecycle:
    """ADR-10 end-to-end: multi-target wiring + cascade across source + 2 siblings."""

    def test_promote_wires_all_three_and_cascade_unblocks_each(self, tmp_path: Path) -> None:
        """Source + 2 peers all get markers; cascade flips all 3 when the fix completes."""
        from devbench.backlog.manager import BacklogManager
        from devbench.backlog.proposal import promote_proposal

        # Build a workspace with three blocked tasks (source + 2 peers) sharing a bug.
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n"
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n"
            "| E0 | Example Epic | 0 | 0 | 0 | 3 |\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Src | Task | blocked | None | caylent-solutions/example | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
            "| E0-F1-S1-T3 | Peer1 | Task | blocked | None | caylent-solutions/example | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T3.md` |\n"
            "| E0-F1-S1-T4 | Peer2 | Task | blocked | None | caylent-solutions/example | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T4.md` |\n"
        )
        story = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        story.mkdir(parents=True)
        for tid in ("E0-F1-S1-T1", "E0-F1-S1-T3", "E0-F1-S1-T4"):
            (story / f"{tid}.md").write_text(_SOURCE_TEMPLATE.replace("E0-F1-S1-T1", tid))

        proposal = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="2026-04-20T00:00:00Z",
            rejection_reason="shared bug blocking three tasks",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="Fix shared bug",
                    files_to_own=["src/fix.py"],
                    linked_scenarios=["SC-01"],
                    suggested_acs=["AC-FUNC-001 fix the bug"],
                    suggested_approach=(
                        "Context: ADR-10 end-to-end lifecycle fixture. "
                        "Scope: src/fix.py plus unit test. "
                        "TDD approach: 1. RED -- add failing test. "
                        "2. GREEN -- minimal fix. 3. REFACTOR -- none. "
                        "Verify: make lint && make test-unit exit zero."
                    ),
                )
            ],
            affected_task_ids=["E0-F1-S1-T3", "E0-F1-S1-T4"],
        )
        write_proposal(tmp_path, proposal)
        materialise_proposal(
            workspace_root=tmp_path,
            backlog_root=tmp_path / "backlog",
            backlog_index=tmp_path / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )

        result = promote_proposal(
            workspace_root=tmp_path,
            backlog_root=tmp_path / "backlog",
            backlog_index=tmp_path / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
        )
        assert result.wired_targets == ["E0-F1-S1-T1", "E0-F1-S1-T3", "E0-F1-S1-T4"]
        for tid in result.wired_targets:
            assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2" in (story / f"{tid}.md").read_text()

        # Transition the fix to done; cascade should flip all three blocked peers.
        mgr = BacklogManager()
        t2 = story / "E0-F1-S1-T2.md"
        mgr.force_status(t2, tmp_path / "BACKLOG.md", "E0-F1-S1-T2", "done")

        # The cascade runs inside force_status / _set_status. All three peers
        # must now be in-queue with [AUTO_UNBLOCKED] audit.
        for tid in ("E0-F1-S1-T1", "E0-F1-S1-T3", "E0-F1-S1-T4"):
            text = (story / f"{tid}.md").read_text()
            assert "## Status: in-queue" in text, f"{tid} did not auto-unblock"
            assert "[AUTO_UNBLOCKED]" in text, f"{tid} missing AUTO_UNBLOCKED audit"


class TestAutoAcceptProposalsLifecycle:
    """ADR-11 end-to-end: flag=true causes sweep-proposals to auto-promote every draft."""

    def test_auto_accept_promotes_every_draft_end_to_end(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Materialise via sweep-proposals with flag=true; every draft ends at in-queue.

        Since AC-189-8, materialise_proposal writes the configured default status
        directly into the draft. When default_status_for_new_work_units='in-queue'
        (the backwards-compatible default), the draft is created at in-queue
        immediately and the auto-cascade promote loop finds no 'proposed' tasks to
        promote -- the task is already at its target status without an explicit
        promote step.
        """
        from unittest.mock import MagicMock, patch

        from devbench import cli

        workspace = _workspace(tmp_path)
        proposal = _proposal()
        write_proposal(workspace, proposal)

        # Patch materialise_proposal's config getter so drafts land at 'in-queue'.
        monkeypatch.setattr(proposal_mod, "_get_runtime_config", lambda: _fake_config_with_status("in-queue"))

        # Source-task row is already in BACKLOG.md via _workspace; its file exists.
        # Build a RUNTIME_CONFIG mock with auto_accept_proposals=True and the
        # BacklogParser resolving the source unit so the sweep reaches materialise.
        unit = MagicMock()
        unit.id = "E0-F1-S1-T1"
        unit.repo = "caylent-solutions/example"
        parser = MagicMock()
        parser.parse_index.return_value = [unit]
        runtime_cfg = MagicMock()
        runtime_cfg.task_factory.auto_accept_proposals = True
        runtime_cfg.task_factory.enabled = True

        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", workspace / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli.RUNTIME_CONFIG", runtime_cfg),
        ):
            rc = cli.cmd_sweep_proposals()
        assert rc == 0

        # Both drafts landed at in-queue (materialised directly, no explicit promote step).
        story = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        t2 = (story / "E0-F1-S1-T2.md").read_text()
        t3 = (story / "E0-F1-S1-T3.md").read_text()
        assert "## Status: in-queue" in t2, "T2 must be at in-queue (materialised directly)"
        assert "## Status: in-queue" in t3, "T3 must be at in-queue (materialised directly)"
        # Verify that both draft files were created (materialise_proposal ran).
        assert (story / "E0-F1-S1-T2.md").exists()
        assert (story / "E0-F1-S1-T3.md").exists()


class TestTaskFactoryLifecycleSkipWhenUnresolved:
    def test_second_materialise_skipped_when_prior_unresolved(self, tmp_path: Path) -> None:
        """The _has_unresolved_proposals guard fires when BACKLOG.md already has 'proposed' rows.

        The guard specifically checks for rows with Status='proposed'. With AC-189-8,
        materialise_proposal writes the config-driven default status (in-queue or draft)
        rather than 'proposed'. To verify the guard still fires when 'proposed' rows exist
        (e.g., rows written by an older version of devbench or via direct injection), we
        seed BACKLOG.md directly with a 'proposed' row before calling materialise.
        """
        workspace = _workspace(tmp_path)
        # Seed BACKLOG.md with a 'proposed' row to simulate a prior unresolved materialise.
        # This is necessary because materialise_proposal now writes the config-driven default
        # status (in-queue) rather than 'proposed', so a second materialise call would not
        # be blocked unless we inject 'proposed' rows directly.
        _append_backlog_row(
            workspace / "BACKLOG.md",
            _render_backlog_row(
                "E0-F1-S1-T2",
                "Prior unresolved task",
                "proposed",
                "caylent-solutions/example",
                "backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md",
            ),
        )

        # Second proposal (different IDs) should be skipped because prior proposals are unresolved.
        second = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="2026-04-18T05:00:00Z",
            rejection_reason="another round",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T5",
                    title="Second-round fix",
                    files_to_own=["src/fix_c.py"],
                    linked_scenarios=["SC-03"],
                    suggested_acs=["AC-FUNC-003 fix"],
                    suggested_approach=(
                        "Context: second-round fix after prior proposal. "
                        "Scope: src/fix_c.py and its unit test. "
                        "TDD approach: RED reproduces, GREEN patches, REFACTOR no-op. "
                        "Verify: make lint && make test-unit both exit zero."
                    ),
                )
            ],
        )
        try:
            materialise_proposal(
                workspace_root=workspace,
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                proposal=second,
                repo="caylent-solutions/example",
            )
        except Exception as exc:
            assert "unresolved" in str(exc).lower()
        else:
            raise AssertionError("expected ProposalError due to unresolved prior proposals")
