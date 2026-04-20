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

from devbench import cli
from devbench.backlog.proposal import (
    Proposal,
    ProposedTask,
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
            review_block = "\n".join(
                f"[2026-04-19 14:00 UTC] [judge/{judge}] [REVIEW_PASS] ok" for judge in ALL_REQUIRED_JUDGE_NAMES
            )
            draft.write_text(draft.read_text() + "\n" + review_block + "\n")

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


class TestTaskFactoryLifecycleSkipWhenUnresolved:
    def test_second_materialise_skipped_when_prior_unresolved(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        # First materialise leaves two rows in `proposed` status.
        first = _proposal()
        write_proposal(workspace, first)
        materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=first,
            repo="caylent-solutions/example",
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
