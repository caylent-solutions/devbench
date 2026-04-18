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
                suggested_approach="1. RED.\n2. GREEN.\n3. Verify.",
            ),
            ProposedTask(
                suggested_id="E0-F1-S1-T3",
                title="Fix scenario B",
                files_to_own=["src/fix_b.py"],
                linked_scenarios=["SC-02"],
                suggested_acs=["AC-FUNC-002 fix B"],
                suggested_approach="1. RED.\n2. GREEN.",
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
                    suggested_approach="RED then GREEN.",
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
