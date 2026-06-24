"""materialise_proposal collision handling.

Tracked issue: ``materialise-proposal-skips-by-id-on-collision-dropping-fix-unit``.

When a proposed fix unit's ``suggested_id`` collides with a PRE-EXISTING UNRELATED
work unit (one this proposal did not author), the old behaviour silently skipped
creation by-id -- the orchestrator-proposed fix unit was never materialised and the
blocked unit it would unblock stayed blocked forever.

The corrected contract: a collision with an unrelated unit must NOT silently no-op.
``materialise_proposal`` allocates the next free id in the task's Story, materialises
the fix unit under that id, and records the remapping so downstream
dependency-wiring (promote-proposal) points the blocked source at the real fix unit.
The legitimate idempotent re-materialise of THIS proposal's own draft (same source
task) is preserved -- it still skips.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.backlog.proposal import (
    _find_draft_file,
    materialise_proposal,
)

from .test_proposal import _build_workspace, _sample_proposal

pytestmark = pytest.mark.unit


def _seed_unrelated_unit(workspace: Path, task_id: str, *, status: str = "done") -> Path:
    """Write a pre-existing UNRELATED unit at ``task_id`` (no proposal provenance)."""
    story_dir = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
    story_dir.mkdir(parents=True, exist_ok=True)
    path = story_dir / f"{task_id}.md"
    path.write_text(
        f"# {task_id}: Pre-existing unrelated unit\n\n"
        f"## Status: {status}\n\n"
        "This unit has nothing to do with the proposal; it merely occupies the id.\n",
        encoding="utf-8",
    )
    return path


class TestMaterialiseProposalCollision:
    """A suggested_id that collides with an unrelated unit is re-homed, never dropped."""

    def test_collision_allocates_free_id_and_materialises(self, tmp_path: Path) -> None:
        """The proposed fix unit is created under a free id, not silently skipped."""
        workspace = _build_workspace(tmp_path)
        unrelated = _seed_unrelated_unit(workspace, "E0-F1-S1-T2", status="done")
        original_unrelated_body = unrelated.read_text(encoding="utf-8")

        proposal = _sample_proposal(task_ids=["E0-F1-S1-T2"])
        drafts = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )

        assert len(drafts) == 1, "the proposed fix unit must be materialised, not silently skipped"
        created = drafts[0]
        assert created.name != "E0-F1-S1-T2.md", "the colliding unrelated unit must not be overwritten"
        assert unrelated.read_text(encoding="utf-8") == original_unrelated_body
        assert created.name == "E0-F1-S1-T3.md"

    def test_collision_repoints_proposal_suggested_id(self, tmp_path: Path) -> None:
        """The in-memory proposal's suggested_id is updated to the allocated id.

        Downstream promote-proposal wiring matches the proposal's suggested_id against
        the materialised draft to add the [BLOCKED_PENDING_PROPOSAL] marker to the
        blocked source; if the id were not re-pointed the wiring would target a
        non-existent unit.
        """
        workspace = _build_workspace(tmp_path)
        _seed_unrelated_unit(workspace, "E0-F1-S1-T2", status="in-progress")

        proposal = _sample_proposal(task_ids=["E0-F1-S1-T2"])
        materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )

        new_id = proposal.proposed_tasks[0].suggested_id
        assert new_id != "E0-F1-S1-T2", "the proposal's suggested_id must be re-pointed to the allocated free id"
        assert _find_draft_file(workspace / "backlog", new_id) is not None, "a draft must exist at the re-pointed id"

    def test_idempotent_re_materialise_of_own_draft_still_skips(self, tmp_path: Path) -> None:
        """Calling materialise twice on the same proposal does not re-home its own drafts.

        The second call must skip (idempotent) -- its drafts cite THIS proposal's
        source task, so they are not a collision with an unrelated unit.
        """
        workspace = _build_workspace(tmp_path)
        proposal = _sample_proposal(task_ids=["E0-F1-S1-T2", "E0-F1-S1-T3"])

        first = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        assert len(first) == 2

        second = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        assert second == [], "re-materialising this proposal's own drafts must skip, not re-home them"
