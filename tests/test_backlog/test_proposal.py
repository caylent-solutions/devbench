"""Tests for devbench.backlog.proposal (task-factory proposal lifecycle)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from devbench.backlog import proposal as proposal_mod
from devbench.backlog.proposal import (
    DRAFT_TEMPLATE,
    LOCK_FILE_NAME,
    PROPOSAL_DIR_NAME,
    REJECTED_PROPOSAL_DIR_NAME,
    Proposal,
    ProposalError,
    ProposedTask,
    _append_backlog_row,
    _append_dependency_to_source,
    _extract_story_id,
    _find_draft_file,
    _find_originating_source_task,
    _find_source_task_file,
    _has_unresolved_proposals,
    _remove_backlog_row,
    _render_backlog_row,
    _rewrite_backlog_status,
    _rewrite_status,
    _story_dir,
    allocate_next_ids,
    delete_proposal,
    generate_draft_md,
    list_proposals,
    materialise_proposal,
    promote_all_from_source,
    promote_proposal,
    proposal_path,
    read_proposal,
    reject_proposal,
    scan_story_for_task_ids,
    write_proposal,
)

# ---------------------------------------------------------------------------
# Shared fixture builders
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
# {task_id}: Source Task

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


def _build_workspace(tmp_path: Path) -> Path:
    """Create a minimal devbench workspace with one blocked source task."""
    (tmp_path / "BACKLOG.md").write_text(_BACKLOG_TEMPLATE)
    story_dir = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
    story_dir.mkdir(parents=True)
    (story_dir / "E0-F1-S1-T1.md").write_text(_SOURCE_TASK_TEMPLATE.format(task_id="E0-F1-S1-T1"))
    return tmp_path


def _sample_proposal(source_task_id: str = "E0-F1-S1-T1", *, task_ids: list[str] | None = None) -> Proposal:
    task_ids = task_ids or ["E0-F1-S1-T2", "E0-F1-S1-T3"]
    tasks = [
        ProposedTask(
            suggested_id=tid,
            title=f"Proposed Task {i}",
            files_to_own=[f"src/{tid}.py"],
            linked_scenarios=[f"SC-{i:02d}"],
            suggested_acs=[f"AC-FUNC-{i:03d} fix the scenario"],
            suggested_approach=(
                f"Context: Scenario SC-{i:02d} failed against the current implementation. "
                f"Scope: One production file and its companion unit test. "
                f"TDD approach: 1. RED -- Reproduce SC-{i:02d} locally in a unit test. "
                "2. GREEN -- Apply the minimal fix in the production module. "
                "3. REFACTOR -- Clean up without changing behaviour. "
                "Verify: make lint && make format-check && make test-unit && make test-integration "
                "all exit zero."
            ),
        )
        for i, tid in enumerate(task_ids, start=1)
    ]
    return Proposal(
        source_task_id=source_task_id,
        generated_at="2026-04-18T03:25:00Z",
        rejection_reason="scope creep fixes are unrelated to source task",
        proposed_tasks=tasks,
    )


# ---------------------------------------------------------------------------
# Proposal dataclass + schema helpers
# ---------------------------------------------------------------------------


class TestProposalDataclass:
    def test_to_dict_roundtrip(self) -> None:
        proposal = _sample_proposal()
        restored = Proposal.from_dict(proposal.to_dict())
        assert restored == proposal

    def test_from_dict_rejects_non_dict(self) -> None:
        with pytest.raises(ValueError, match="must be an object"):
            Proposal.from_dict([1, 2, 3])  # type: ignore[arg-type]

    def test_from_dict_rejects_missing_top_field(self) -> None:
        payload = _sample_proposal().to_dict()
        del payload["source_task_id"]
        with pytest.raises(ValueError, match="missing required field"):
            Proposal.from_dict(payload)

    def test_from_dict_rejects_non_list_tasks(self) -> None:
        payload: dict[str, Any] = _sample_proposal().to_dict()
        payload["proposed_tasks"] = "not a list"
        with pytest.raises(ValueError, match="must be a list"):
            Proposal.from_dict(payload)

    def test_from_dict_rejects_non_dict_task_entry(self) -> None:
        payload = _sample_proposal().to_dict()
        payload["proposed_tasks"][0] = "bad"
        with pytest.raises(ValueError, match="must be an object"):
            Proposal.from_dict(payload)

    def test_from_dict_rejects_missing_task_field(self) -> None:
        payload = _sample_proposal().to_dict()
        del payload["proposed_tasks"][0]["title"]
        with pytest.raises(ValueError, match="missing required field"):
            Proposal.from_dict(payload)


# ---------------------------------------------------------------------------
# Path / lock helpers
# ---------------------------------------------------------------------------


class TestPathHelpers:
    def test_proposal_path(self, tmp_path: Path) -> None:
        assert proposal_path(tmp_path, "E0-F1-S1-T1") == tmp_path / PROPOSAL_DIR_NAME / "E0-F1-S1-T1.json"

    def test_extract_story_id_ok(self) -> None:
        assert _extract_story_id("E0-F1-S1-T3") == "E0-F1-S1"

    def test_extract_story_id_rejects_short(self) -> None:
        with pytest.raises(ProposalError, match="Cannot derive"):
            _extract_story_id("E0-F1")

    def test_story_dir_rejects_bad_story_id(self, tmp_path: Path) -> None:
        with pytest.raises(ProposalError, match="not a valid Story ID"):
            _story_dir(tmp_path, "NOT-A-STORY")

    def test_story_dir_valid(self, tmp_path: Path) -> None:
        path = _story_dir(tmp_path, "E0-F1-S1")
        assert path == tmp_path / "E0" / "E0-F1" / "E0-F1-S1"

    def test_constants_present(self) -> None:
        assert LOCK_FILE_NAME
        assert PROPOSAL_DIR_NAME
        assert REJECTED_PROPOSAL_DIR_NAME
        assert DRAFT_TEMPLATE


# ---------------------------------------------------------------------------
# scan_story_for_task_ids + allocate_next_ids
# ---------------------------------------------------------------------------


class TestScanStoryForTaskIds:
    def test_empty_dir_returns_empty_set(self, tmp_path: Path) -> None:
        assert scan_story_for_task_ids(tmp_path, "E0-F1-S1") == set()

    def test_picks_up_existing_task_files(self, tmp_path: Path) -> None:
        story_dir = tmp_path / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T1.md").write_text("# x\n")
        (story_dir / "E0-F1-S1-T2.md").write_text("# x\n")
        (story_dir / "E0-F1-S1.md").write_text("# story\n")  # not a task
        (story_dir / "README.md").write_text("# other\n")  # not a task
        (story_dir / "ignored.txt").write_text("")  # not .md
        assert scan_story_for_task_ids(tmp_path, "E0-F1-S1") == {"E0-F1-S1-T1", "E0-F1-S1-T2"}


class TestAllocateNextIds:
    def test_zero_count_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ProposalError, match="count must be"):
            allocate_next_ids(tmp_path, tmp_path / "backlog", "E0-F1-S1", 0)

    def test_first_allocation_skips_existing(self, tmp_path: Path) -> None:
        backlog_root = tmp_path / "backlog"
        story_dir = backlog_root / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T5.md").write_text("# x\n")
        (story_dir / "E0-F1-S1-T2.md").write_text("# x\n")
        ids = allocate_next_ids(tmp_path, backlog_root, "E0-F1-S1", 3)
        assert ids == ["E0-F1-S1-T6", "E0-F1-S1-T7", "E0-F1-S1-T8"]

    def test_allocation_in_parallel_threads_disjoint(self, tmp_path: Path) -> None:
        backlog_root = tmp_path / "backlog"
        (backlog_root / "E0" / "E0-F1" / "E0-F1-S1").mkdir(parents=True)

        results: list[list[str]] = [[], []]
        errors: list[BaseException] = []

        def worker(slot: int) -> None:
            try:
                results[slot] = allocate_next_ids(tmp_path, backlog_root, "E0-F1-S1", 3)
                # Now materialise the IDs so the other thread sees them as existing.
                story_dir = backlog_root / "E0" / "E0-F1" / "E0-F1-S1"
                for tid in results[slot]:
                    (story_dir / f"{tid}.md").write_text("# x\n")
            except (AssertionError, RuntimeError, OSError) as exc:
                errors.append(exc)

        # Staged execution: run the two threads sequentially through the lock so the
        # second sees the first's writes and returns disjoint IDs. Concurrency is
        # exercised by the lock-scope exception test below.
        t0 = threading.Thread(target=worker, args=(0,))
        t0.start()
        t0.join()
        t1 = threading.Thread(target=worker, args=(1,))
        t1.start()
        t1.join()

        assert not errors, errors
        assert set(results[0]).isdisjoint(results[1])

    def test_lock_released_on_exception(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        backlog_root = tmp_path / "backlog"
        (backlog_root / "E0" / "E0-F1" / "E0-F1-S1").mkdir(parents=True)

        # Patch scan_story_for_task_ids to raise on the first call; the
        # lock MUST still release so the second call succeeds.
        calls = {"n": 0}
        real_scan = proposal_mod.scan_story_for_task_ids

        def fake_scan(root: Path, story_id: str) -> set[str]:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return real_scan(root, story_id)

        monkeypatch.setattr(proposal_mod, "scan_story_for_task_ids", fake_scan)
        with pytest.raises(RuntimeError, match="boom"):
            allocate_next_ids(tmp_path, backlog_root, "E0-F1-S1", 1)
        # Second call must succeed (lock released).
        ids = allocate_next_ids(tmp_path, backlog_root, "E0-F1-S1", 1)
        assert ids == ["E0-F1-S1-T1"]


# ---------------------------------------------------------------------------
# Proposal I/O
# ---------------------------------------------------------------------------


class TestProposalIO:
    def test_write_reads_back(self, tmp_path: Path) -> None:
        proposal = _sample_proposal()
        written = write_proposal(tmp_path, proposal)
        assert written.exists()
        assert read_proposal(tmp_path, proposal.source_task_id) == proposal

    def test_write_rejects_duplicate(self, tmp_path: Path) -> None:
        proposal = _sample_proposal()
        write_proposal(tmp_path, proposal)
        with pytest.raises(ProposalError, match="already exists"):
            write_proposal(tmp_path, proposal)

    def test_read_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ProposalError, match="No proposal"):
            read_proposal(tmp_path, "E0-F1-S1-T1")

    def test_read_malformed_raises(self, tmp_path: Path) -> None:
        target = proposal_path(tmp_path, "E0-F1-S1-T1")
        target.parent.mkdir(parents=True)
        target.write_text("not json")
        with pytest.raises(ProposalError, match="not valid JSON"):
            read_proposal(tmp_path, "E0-F1-S1-T1")

    def test_read_schema_violation_raises(self, tmp_path: Path) -> None:
        target = proposal_path(tmp_path, "E0-F1-S1-T1")
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps({"source_task_id": "E0-F1-S1-T1"}))
        with pytest.raises(ProposalError, match="missing required"):
            read_proposal(tmp_path, "E0-F1-S1-T1")

    def test_delete_noop_when_absent(self, tmp_path: Path) -> None:
        delete_proposal(tmp_path, "E0-F1-S1-T1")  # no raise

    def test_delete_existing(self, tmp_path: Path) -> None:
        proposal = _sample_proposal()
        written = write_proposal(tmp_path, proposal)
        delete_proposal(tmp_path, proposal.source_task_id)
        assert not written.exists()


# ---------------------------------------------------------------------------
# generate_draft_md
# ---------------------------------------------------------------------------


class TestGenerateDraftMd:
    def test_renders_with_explicit_acs_and_manifest(self) -> None:
        task = ProposedTask(
            suggested_id="E0-F1-S1-T9",
            title="My Task",
            files_to_own=["src/a.py", "src/b.py"],
            linked_scenarios=["SC-01", "SC-02"],
            suggested_acs=["AC-FUNC-001 foo"],
            suggested_approach="Do the thing.",
        )
        md = generate_draft_md(task, repo="acme/example", source_task_id="E0-F1-S1-T1", generated_at="NOW")
        assert "E0-F1-S1-T9" in md
        assert "## Status: proposed" in md
        assert "Do the thing." in md
        assert "- [ ] AC-FUNC-001 foo" in md
        assert "| `src/a.py` | TODO -- describe change |" in md
        assert "SC-01, SC-02" in md
        assert "generated by task-factory on NOW" in md

    def test_fills_fallbacks_when_fields_empty(self) -> None:
        task = ProposedTask(
            suggested_id="E0-F1-S1-T9",
            title="My Task",
            files_to_own=[],
            linked_scenarios=[],
            suggested_acs=[],
            suggested_approach="",
        )
        md = generate_draft_md(task, repo="acme/example", source_task_id="E0-F1-S1-T1", generated_at="NOW")
        assert "human must author approach" in md
        assert "AC-TODO-001 human must author AC" in md
        assert "`TODO` | TODO -- describe change" in md
        assert "(none documented)" in md


# ---------------------------------------------------------------------------
# BACKLOG.md manipulation
# ---------------------------------------------------------------------------


class TestBacklogRowHelpers:
    def test_render_row_shape(self) -> None:
        row = _render_backlog_row("E0-F1-S1-T2", "A", "proposed", "r/x", "backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md")
        assert row.startswith("| E0-F1-S1-T2 | A | Task | proposed ")
        assert row.endswith("E0-F1-S1-T2.md` |\n")

    def test_append_and_remove_row_roundtrip(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        new_row = _render_backlog_row(
            "E0-F1-S1-T7",
            "New Task",
            "proposed",
            "caylent-solutions/example",
            "backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T7.md",
        )
        _append_backlog_row(workspace / "BACKLOG.md", new_row)
        assert "E0-F1-S1-T7" in (workspace / "BACKLOG.md").read_text()
        _remove_backlog_row(workspace / "BACKLOG.md", "E0-F1-S1-T7")
        assert "E0-F1-S1-T7" not in (workspace / "BACKLOG.md").read_text()

    def test_remove_row_raises_when_missing(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        with pytest.raises(ProposalError, match="not found"):
            _remove_backlog_row(workspace / "BACKLOG.md", "DOES-NOT-EXIST")

    def test_append_row_raises_when_marker_missing(self, tmp_path: Path) -> None:
        (tmp_path / "BACKLOG.md").write_text("no marker here\n")
        with pytest.raises(ProposalError, match="has no '## Full Work Unit Index'"):
            _append_backlog_row(tmp_path / "BACKLOG.md", "| X | ... |\n")


class TestRewriteStatus:
    def test_rewrite_md_status(self, tmp_path: Path) -> None:
        md = tmp_path / "task.md"
        md.write_text("# T\n\n## Status: proposed\n\nbody\n")
        _rewrite_status(md, "in-queue")
        assert "## Status: in-queue" in md.read_text()

    def test_rewrite_md_status_missing_line(self, tmp_path: Path) -> None:
        md = tmp_path / "task.md"
        md.write_text("# T\n\nbody\n")
        with pytest.raises(ProposalError, match="no '## Status:' line"):
            _rewrite_status(md, "in-queue")

    def test_rewrite_backlog_status(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        _append_backlog_row(
            workspace / "BACKLOG.md",
            _render_backlog_row(
                "E0-F1-S1-T2",
                "A",
                "proposed",
                "caylent-solutions/example",
                "backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md",
            ),
        )
        _rewrite_backlog_status(workspace / "BACKLOG.md", "E0-F1-S1-T2", "in-queue")
        content = (workspace / "BACKLOG.md").read_text()
        assert "| E0-F1-S1-T2 | A | Task | in-queue " in content

    def test_rewrite_backlog_status_missing_row(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        with pytest.raises(ProposalError, match="not found"):
            _rewrite_backlog_status(workspace / "BACKLOG.md", "DOES-NOT-EXIST", "in-queue")


# ---------------------------------------------------------------------------
# materialise_proposal
# ---------------------------------------------------------------------------


class TestMaterialiseProposal:
    def test_creates_drafts_and_appends_rows(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        proposal = _sample_proposal()
        drafts = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        assert len(drafts) == 2
        for draft in drafts:
            assert draft.is_file()
            assert "## Status: proposed" in draft.read_text()
        backlog = (workspace / "BACKLOG.md").read_text()
        assert "E0-F1-S1-T2" in backlog
        assert "E0-F1-S1-T3" in backlog

    def test_refuses_when_unresolved_proposed_rows_exist(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        # Seed BACKLOG.md with an existing proposed row BEFORE materialising a fresh proposal.
        _append_backlog_row(
            workspace / "BACKLOG.md",
            _render_backlog_row(
                "E0-F1-S1-T99",
                "Stale",
                "proposed",
                "caylent-solutions/example",
                "backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T99.md",
            ),
        )
        with pytest.raises(ProposalError, match="unresolved proposed tasks"):
            materialise_proposal(
                workspace_root=workspace,
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                proposal=_sample_proposal(),
                repo="caylent-solutions/example",
            )

    def test_skips_task_when_draft_file_already_exists(self, tmp_path: Path) -> None:
        """ADR-09: materialise is idempotent. A pre-existing draft is left alone."""
        workspace = _build_workspace(tmp_path)
        story_dir = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        existing_path = story_dir / "E0-F1-S1-T2.md"
        existing_path.write_text("# E0-F1-S1-T2: X\n\n## Status: proposed\n\nexisting\n")

        proposal = _sample_proposal(task_ids=["E0-F1-S1-T2"])
        drafts = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )

        # No draft was created -- the existing file classifies as PROPOSED and is skipped.
        assert drafts == []
        # Existing file content preserved (no overwrite).
        assert "existing" in existing_path.read_text()


class TestMaterialiseProposalIdempotent:
    """ADR-09: materialise_proposal is classify-aware and idempotent."""

    def _two_task_proposal(self) -> Proposal:
        return _sample_proposal(task_ids=["E0-F1-S1-T2", "E0-F1-S1-T3"])

    def test_skips_rejected_task_from_archive(self, tmp_path: Path) -> None:
        """Rejected archive -> classifier returns REJECTED -> materialise does NOT recreate."""
        from devbench.backlog.proposal import REJECTED_PROPOSAL_DIR_NAME

        workspace = _build_workspace(tmp_path)
        # Seed a per-draft reject archive for T2 at the canonical location.
        archive_dir = workspace / REJECTED_PROPOSAL_DIR_NAME
        archive_dir.mkdir(parents=True, exist_ok=True)
        (archive_dir / "E0-F1-S1-T2-20260419T000000Z.md").write_text("archived draft body")

        proposal = _sample_proposal(task_ids=["E0-F1-S1-T2"])
        drafts = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )

        assert drafts == []
        story_dir = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        assert not (story_dir / "E0-F1-S1-T2.md").exists(), "rejected draft must not be resurrected"
        # BACKLOG.md must not have gained a row for the skipped task.
        assert "E0-F1-S1-T2" not in (workspace / "BACKLOG.md").read_text()

    @pytest.mark.parametrize("status_value", ["in-queue", "in-progress", "in-review", "blocked", "done", "declined"])
    def test_skips_promoted_done_declined_states(self, tmp_path: Path, status_value: str) -> None:
        """Any non-PROPOSED / non-UNMATERIALISED state -> skip, no recreation."""
        workspace = _build_workspace(tmp_path)
        story_dir = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        draft = story_dir / "E0-F1-S1-T2.md"
        draft.write_text(f"# E0-F1-S1-T2: X\n\n## Status: {status_value}\n\nbody\n")
        original_body = draft.read_text()

        drafts = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=_sample_proposal(task_ids=["E0-F1-S1-T2"]),
            repo="caylent-solutions/example",
        )
        assert drafts == []
        assert draft.read_text() == original_body

    def test_creates_remaining_tasks_when_others_skipped(self, tmp_path: Path) -> None:
        """Partial state: one task rejected (archive), the other materialises normally."""
        from devbench.backlog.proposal import REJECTED_PROPOSAL_DIR_NAME

        workspace = _build_workspace(tmp_path)
        # Reject T2 via archive; T3 should still materialise on the same call.
        archive_dir = workspace / REJECTED_PROPOSAL_DIR_NAME
        archive_dir.mkdir(parents=True, exist_ok=True)
        (archive_dir / "E0-F1-S1-T2-20260419T000000Z.md").write_text("archived")

        drafts = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=self._two_task_proposal(),
            repo="caylent-solutions/example",
        )

        assert len(drafts) == 1, "only T3 should be materialised; T2 stays rejected"
        assert drafts[0].name == "E0-F1-S1-T3.md"
        story_dir = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        assert not (story_dir / "E0-F1-S1-T2.md").exists()
        assert (story_dir / "E0-F1-S1-T3.md").exists()

    def test_double_call_is_noop(self, tmp_path: Path) -> None:
        """Calling materialise twice on the same fresh JSON: second call creates nothing."""
        workspace = _build_workspace(tmp_path)
        proposal = self._two_task_proposal()

        first = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        assert len(first) == 2

        # Second call: every task now classifies as PROPOSED (draft file has
        # Status: proposed). materialise must skip both and return empty.
        second = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        assert second == []

        # BACKLOG.md has exactly one row per task (no duplicates). Each row
        # mentions the id twice (id cell + file-path cell); count the
        # row-start form to measure row count.
        backlog_text = (workspace / "BACKLOG.md").read_text()
        assert backlog_text.count("| E0-F1-S1-T2 |") == 1
        assert backlog_text.count("| E0-F1-S1-T3 |") == 1

    def test_rejected_draft_does_not_resurrect_on_re_materialise(self, tmp_path: Path) -> None:
        """End-to-end: reject a materialised draft, then re-materialise. Archive stays; no recreation."""
        workspace = _build_workspace(tmp_path)
        proposal = self._two_task_proposal()
        write_proposal(workspace, proposal)
        materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        reject_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
            reason="superseded",
        )
        # T2's draft is archived now. Re-materialise the same JSON.
        result = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        assert result == [], "rejected draft must not be resurrected on re-materialise"
        story_dir = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        assert not (story_dir / "E0-F1-S1-T2.md").exists()


class TestHasUnresolvedProposals:
    def test_missing_file(self, tmp_path: Path) -> None:
        assert _has_unresolved_proposals(tmp_path / "nonexistent") is False

    def test_no_proposed(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        assert _has_unresolved_proposals(workspace / "BACKLOG.md") is False

    def test_present(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        _append_backlog_row(
            workspace / "BACKLOG.md",
            _render_backlog_row(
                "E0-F1-S1-T99",
                "Stale",
                "proposed",
                "caylent-solutions/example",
                "backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T99.md",
            ),
        )
        assert _has_unresolved_proposals(workspace / "BACKLOG.md") is True

    def test_short_line_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "BACKLOG.md").write_text("| a |\n## Full Work Unit Index\n")
        assert _has_unresolved_proposals(tmp_path / "BACKLOG.md") is False

    def test_non_pipe_line_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "BACKLOG.md").write_text("plain line\n")
        assert _has_unresolved_proposals(tmp_path / "BACKLOG.md") is False

    def test_exclude_task_ids_filters_own_proposed_rows(self, tmp_path: Path) -> None:
        """ADR-09: re-materialising a proposal must not see its own proposed rows as blockers."""
        workspace = _build_workspace(tmp_path)
        _append_backlog_row(
            workspace / "BACKLOG.md",
            _render_backlog_row(
                "E0-F1-S1-T2",
                "Mine",
                "proposed",
                "caylent-solutions/example",
                "backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md",
            ),
        )
        # Without the exclude set, this row makes the guard fire.
        assert _has_unresolved_proposals(workspace / "BACKLOG.md") is True
        # Excluding the same id returns False -- the row is ignored.
        assert _has_unresolved_proposals(workspace / "BACKLOG.md", exclude_task_ids=frozenset({"E0-F1-S1-T2"})) is False


# ---------------------------------------------------------------------------
# list_proposals
# ---------------------------------------------------------------------------


class TestListProposals:
    def test_none_when_dir_missing(self, tmp_path: Path) -> None:
        assert list_proposals(tmp_path) == []

    def test_returns_written_proposals(self, tmp_path: Path) -> None:
        p = _sample_proposal()
        write_proposal(tmp_path, p)
        out = list_proposals(tmp_path)
        assert len(out) == 1
        assert out[0] == p

    def test_skips_garbage_files(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        d = tmp_path / PROPOSAL_DIR_NAME
        d.mkdir(parents=True)
        (d / "junk.json").write_text("not json")
        (d / "readme.txt").write_text("skip non-json extension")
        # Also add a valid one so the final output is non-empty.
        p = _sample_proposal()
        write_proposal(tmp_path, p)
        out = list_proposals(tmp_path)
        assert any(pp == p for pp in out)


# ---------------------------------------------------------------------------
# promote_proposal / reject_proposal
# ---------------------------------------------------------------------------


class TestPromoteProposal:
    def test_missing_draft_raises(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        with pytest.raises(ProposalError, match="No draft file"):
            promote_proposal(
                workspace_root=workspace,
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                task_id="E0-F1-S1-T99",
            )

    def test_happy_path_wires_dependency(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        proposal = _sample_proposal(task_ids=["E0-F1-S1-T2"])
        write_proposal(workspace, proposal)
        materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        result = promote_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
        )
        assert result.draft_path.is_file()
        # Draft status flipped.
        assert "## Status: in-queue" in result.draft_path.read_text()
        # Source task was the sole wired target (no affected_task_ids in fixture).
        assert result.wired_targets == ["E0-F1-S1-T1"]
        # Source task now has a dep on the promoted task.
        source = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        assert "| E0-F1-S1-T2 |" in source.read_text()
        # Audit comment was added.
        assert "[PROPOSAL_PROMOTED]" in source.read_text()

    def test_skip_dep_wiring(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        proposal = _sample_proposal(task_ids=["E0-F1-S1-T2"])
        write_proposal(workspace, proposal)
        materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        promote_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
            dep_on_source=False,
        )
        source = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        assert "| E0-F1-S1-T2 |" not in source.read_text()

    def test_promote_writes_blocked_pending_proposal_marker(self, tmp_path: Path) -> None:
        """The wiring write must include the ``[BLOCKED_PENDING_PROPOSAL]`` marker.

        The marker is what ``BacklogManager._auto_requeue_marker_dependents``
        reads on the source task to decide whether a subsequent ``mark_done``
        of the promoted dep should auto-unblock the source. Regressions here
        silently break the auto-requeue cascade without any CI signal.
        """
        workspace = _build_workspace(tmp_path)
        proposal = _sample_proposal(task_ids=["E0-F1-S1-T2"])
        write_proposal(workspace, proposal)
        materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        promote_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
        )
        source = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        source_text = source.read_text()
        assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2" in source_text
        # Both markers should be on the same audit comment line.
        for line in source_text.splitlines():
            if "[PROPOSAL_PROMOTED] E0-F1-S1-T2" in line:
                assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2" in line
                break
        else:
            raise AssertionError("did not find [PROPOSAL_PROMOTED] audit line")

    def test_promote_with_no_dep_on_source_skips_marker(self, tmp_path: Path) -> None:
        """``--no-dep-on-source`` implies no auto-requeue wiring; marker MUST be absent."""
        workspace = _build_workspace(tmp_path)
        proposal = _sample_proposal(task_ids=["E0-F1-S1-T2"])
        write_proposal(workspace, proposal)
        materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        promote_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
            dep_on_source=False,
        )
        source = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        assert "[BLOCKED_PENDING_PROPOSAL]" not in source.read_text()


class TestPromoteCommentAuditSuffix:
    """ADR-11: _append_promote_comment optional audit_suffix kwarg."""

    def test_append_promote_comment_has_no_suffix_when_not_supplied(self, tmp_path: Path) -> None:
        """Back-compat pin: today's byte-identical marker line."""
        from devbench.backlog.proposal import _append_promote_comment

        source = tmp_path / "src.md"
        source.write_text("# E0-F1-S1-T1: X\n\n## Status: blocked\n\n## Description\n\nx\n")
        _append_promote_comment(source, "E0-F1-S1-T1", "E0-F1-S1-T2")
        text = source.read_text()
        assert (
            "[PROPOSAL_PROMOTED] E0-F1-S1-T2 promoted and wired as dependency of E0-F1-S1-T1. "
            "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2"
        ) in text
        assert "auto-accepted" not in text

    def test_append_promote_comment_appends_audit_suffix_when_supplied(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import _append_promote_comment

        source = tmp_path / "src.md"
        source.write_text("# E0-F1-S1-T1: X\n\n## Status: blocked\n\n## Description\n\nx\n")
        _append_promote_comment(
            source,
            "E0-F1-S1-T1",
            "E0-F1-S1-T2",
            audit_suffix="(auto-accepted via task_factory.auto_accept_proposals=true)",
        )
        text = source.read_text()
        assert (
            "[PROPOSAL_PROMOTED] E0-F1-S1-T2 promoted and wired as dependency of E0-F1-S1-T1."
            " (auto-accepted via task_factory.auto_accept_proposals=true) "
            "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2"
        ) in text


class TestPromoteAllFromSource:
    def test_missing_proposal_raises(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        with pytest.raises(ProposalError, match="Cannot resolve"):
            promote_all_from_source(
                workspace_root=workspace,
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                source_task_id="E0-F1-S1-T1",
            )

    def test_promotes_every_task(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        proposal = _sample_proposal()
        write_proposal(workspace, proposal)
        materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        promoted = promote_all_from_source(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            source_task_id="E0-F1-S1-T1",
        )
        assert len(promoted) == 2


class TestProposalAffectedTaskIds:
    """ADR-10: Proposal.affected_task_ids schema."""

    def _base_payload(self) -> dict:
        return {
            "source_task_id": "E0-F1-S1-T1",
            "generated_at": "2026-04-20T00:00:00Z",
            "rejection_reason": "test fixture",
            "proposed_tasks": [],
        }

    def test_from_dict_accepts_missing_field_defaults_empty(self) -> None:
        payload = self._base_payload()
        assert "affected_task_ids" not in payload
        p = Proposal.from_dict(payload)
        assert p.affected_task_ids == []

    def test_from_dict_accepts_empty_list(self) -> None:
        payload = {**self._base_payload(), "affected_task_ids": []}
        assert Proposal.from_dict(payload).affected_task_ids == []

    def test_from_dict_accepts_single_entry(self) -> None:
        payload = {**self._base_payload(), "affected_task_ids": ["E0-F1-S1-T9"]}
        assert Proposal.from_dict(payload).affected_task_ids == ["E0-F1-S1-T9"]

    def test_from_dict_accepts_multiple_entries(self) -> None:
        payload = {**self._base_payload(), "affected_task_ids": ["E0-F2-S1-T1", "E0-F3-S1-T1"]}
        assert Proposal.from_dict(payload).affected_task_ids == ["E0-F2-S1-T1", "E0-F3-S1-T1"]

    def test_from_dict_rejects_non_list(self) -> None:
        payload = {**self._base_payload(), "affected_task_ids": "E0-F1-S1-T9"}
        with pytest.raises(ValueError, match="must be a list"):
            Proposal.from_dict(payload)

    def test_from_dict_rejects_non_string_entry(self) -> None:
        payload = {**self._base_payload(), "affected_task_ids": [123]}
        with pytest.raises(ValueError, match="must be a string"):
            Proposal.from_dict(payload)

    def test_from_dict_rejects_empty_string_entry(self) -> None:
        payload = {**self._base_payload(), "affected_task_ids": [""]}
        with pytest.raises(ValueError, match="empty entry"):
            Proposal.from_dict(payload)

    def test_from_dict_rejects_duplicate_entries(self) -> None:
        payload = {**self._base_payload(), "affected_task_ids": ["E0-F2-S1-T1", "E0-F2-S1-T1"]}
        with pytest.raises(ValueError, match="duplicate entry"):
            Proposal.from_dict(payload)

    def test_from_dict_rejects_source_task_id_duplicated_in_affected(self) -> None:
        payload = {**self._base_payload(), "affected_task_ids": ["E0-F1-S1-T1"]}
        with pytest.raises(ValueError, match="duplicates source_task_id"):
            Proposal.from_dict(payload)

    def test_from_dict_strips_whitespace(self) -> None:
        payload = {**self._base_payload(), "affected_task_ids": ["  E0-F2-S1-T1  "]}
        assert Proposal.from_dict(payload).affected_task_ids == ["E0-F2-S1-T1"]

    def test_to_dict_round_trip_preserves_order(self) -> None:
        original = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="t",
            rejection_reason="r",
            proposed_tasks=[],
            affected_task_ids=["E0-F3-S1-T1", "E0-F2-S1-T1"],
        )
        reloaded = Proposal.from_dict(original.to_dict())
        assert reloaded.affected_task_ids == ["E0-F3-S1-T1", "E0-F2-S1-T1"]

    def test_to_dict_always_emits_key_even_when_empty(self) -> None:
        p = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="t",
            rejection_reason="r",
            proposed_tasks=[],
        )
        out = p.to_dict()
        assert "affected_task_ids" in out
        assert out["affected_task_ids"] == []


class TestPromoteProposalAffectedWiring:
    """ADR-10: promote_proposal wires [source] + affected_task_ids; fail-fast on missing targets."""

    def _build_workspace_with_peers(self, tmp_path: Path, peer_ids: list[str]) -> Path:
        """Build a workspace with source task T1 + one peer task per id in peer_ids."""
        (tmp_path / "BACKLOG.md").write_text(_BACKLOG_TEMPLATE)
        story_dir = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T1.md").write_text(_SOURCE_TASK_TEMPLATE.format(task_id="E0-F1-S1-T1"))
        for peer_id in peer_ids:
            (story_dir / f"{peer_id}.md").write_text(_SOURCE_TASK_TEMPLATE.format(task_id=peer_id))
            _append_backlog_row(
                tmp_path / "BACKLOG.md",
                _render_backlog_row(
                    peer_id,
                    f"Peer {peer_id}",
                    "blocked",
                    "caylent-solutions/example",
                    f"backlog/E0/E0-F1/E0-F1-S1/{peer_id}.md",
                ),
            )
        return tmp_path

    def _materialise_fixture(self, workspace: Path, peer_ids: list[str]) -> None:
        proposal = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="2026-04-20T00:00:00Z",
            rejection_reason="r",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="Fix",
                    files_to_own=["src/x.py"],
                    linked_scenarios=["SC-01"],
                    suggested_acs=["AC-001 fix"],
                    suggested_approach=(
                        "Context: ADR-10 multi-target wiring unit-test fixture. "
                        "Scope: src/x.py and a matching unit test. "
                        "TDD approach: 1. RED -- write failing assertion. "
                        "2. GREEN -- minimal fix. 3. REFACTOR -- no behaviour change. "
                        "Verify: make lint && make test-unit both exit zero."
                    ),
                )
            ],
            affected_task_ids=peer_ids,
        )
        write_proposal(workspace, proposal)
        materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )

    def test_promote_wires_source_only_when_affected_empty(self, tmp_path: Path) -> None:
        """Back-compat: empty affected_task_ids wires only the source task."""
        workspace = self._build_workspace_with_peers(tmp_path, peer_ids=[])
        self._materialise_fixture(workspace, peer_ids=[])

        result = promote_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
        )
        assert result.wired_targets == ["E0-F1-S1-T1"]

    def test_promote_wires_source_plus_affected_when_populated(self, tmp_path: Path) -> None:
        """ADR-10 primary behaviour: marker + dep land on source + every affected entry."""
        workspace = self._build_workspace_with_peers(tmp_path, peer_ids=["E0-F1-S1-T3", "E0-F1-S1-T4"])
        self._materialise_fixture(workspace, peer_ids=["E0-F1-S1-T3", "E0-F1-S1-T4"])

        result = promote_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
        )
        assert result.wired_targets == ["E0-F1-S1-T1", "E0-F1-S1-T3", "E0-F1-S1-T4"]
        # Marker lands on every target's Comments.
        for tid in result.wired_targets:
            text = (workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / f"{tid}.md").read_text()
            assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2" in text

    def test_promote_fails_fast_when_affected_target_missing_from_backlog(self, tmp_path: Path) -> None:
        """Missing peer ID raises before any write so the source is never half-wired."""
        workspace = self._build_workspace_with_peers(tmp_path, peer_ids=[])
        # peer T99 is in affected_task_ids but does NOT exist in backlog.
        self._materialise_fixture(workspace, peer_ids=["E0-F1-S1-T99"])

        with pytest.raises(ProposalError, match=r"E0-F1-S1-T99.*not found in backlog"):
            promote_proposal(
                workspace_root=workspace,
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                task_id="E0-F1-S1-T2",
            )
        # Source task must NOT have been partially wired -- no marker written.
        source_text = (workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md").read_text()
        assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2" not in source_text

    def test_promote_no_dep_on_source_still_wires_affected_deps(self, tmp_path: Path) -> None:
        """`--no-dep-on-source` skips only the source; affected entries still get their row + marker."""
        workspace = self._build_workspace_with_peers(tmp_path, peer_ids=["E0-F1-S1-T3"])
        self._materialise_fixture(workspace, peer_ids=["E0-F1-S1-T3"])

        result = promote_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
            dep_on_source=False,
        )
        assert result.wired_targets == ["E0-F1-S1-T3"]
        # Source did NOT get the marker.
        source = (workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md").read_text()
        assert "[BLOCKED_PENDING_PROPOSAL]" not in source
        # Peer DID get the marker.
        peer = (workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T3.md").read_text()
        assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2" in peer

    def test_promote_wires_in_declared_order(self, tmp_path: Path) -> None:
        """Target list preserves declared order so audit reads naturally."""
        workspace = self._build_workspace_with_peers(tmp_path, peer_ids=["E0-F1-S1-T4", "E0-F1-S1-T3"])
        self._materialise_fixture(workspace, peer_ids=["E0-F1-S1-T4", "E0-F1-S1-T3"])

        result = promote_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
        )
        # Source first, then affected in declared order.
        assert result.wired_targets == ["E0-F1-S1-T1", "E0-F1-S1-T4", "E0-F1-S1-T3"]


class TestAddDepCoreHelper:
    """ADR-10: add_dep() core helper (called by cmd_add_dep)."""

    def _workspace(self, tmp_path: Path) -> Path:
        (tmp_path / "BACKLOG.md").write_text(_BACKLOG_TEMPLATE)
        story_dir = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T1.md").write_text(_SOURCE_TASK_TEMPLATE.format(task_id="E0-F1-S1-T1"))
        (story_dir / "E0-F1-S1-T2.md").write_text(_SOURCE_TASK_TEMPLATE.format(task_id="E0-F1-S1-T2"))
        _append_backlog_row(
            tmp_path / "BACKLOG.md",
            _render_backlog_row(
                "E0-F1-S1-T2",
                "Fix",
                "in-queue",
                "caylent-solutions/example",
                "backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md",
            ),
        )
        return tmp_path

    def test_add_dep_writes_row_and_marker_when_absent(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import add_dep

        workspace = self._workspace(tmp_path)
        wrote = add_dep(
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
            reason="ADR-10 smoke test",
        )
        assert wrote is True
        source_text = (workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md").read_text()
        assert "| E0-F1-S1-T2 |" in source_text
        assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2" in source_text
        assert "[WU_WIRED]" in source_text
        assert "[agent/operator]" in source_text
        assert "ADR-10 smoke test" in source_text

    def test_add_dep_is_idempotent_on_repeated_call(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import add_dep

        workspace = self._workspace(tmp_path)
        first = add_dep(
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
        )
        second = add_dep(
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            blocked_task_id="E0-F1-S1-T1",
            blocker_task_id="E0-F1-S1-T2",
        )
        assert first is True
        assert second is False
        # Marker appears exactly once.
        text = (workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md").read_text()
        assert text.count("[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2") == 1

    def test_add_dep_fails_fast_when_blocker_missing_from_backlog(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import add_dep

        workspace = self._workspace(tmp_path)
        with pytest.raises(ProposalError, match="blocker task 'E0-F1-S1-T99' not found"):
            add_dep(
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                blocked_task_id="E0-F1-S1-T1",
                blocker_task_id="E0-F1-S1-T99",
            )

    def test_add_dep_rejects_self_wire(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import add_dep

        workspace = self._workspace(tmp_path)
        with pytest.raises(ProposalError, match="cannot be the same task"):
            add_dep(
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                blocked_task_id="E0-F1-S1-T1",
                blocker_task_id="E0-F1-S1-T1",
            )

    def test_add_dep_fails_fast_when_blocker_is_done(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import add_dep

        workspace = self._workspace(tmp_path)
        # Flip T2 to done manually.
        t2 = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T2.md"
        t2.write_text(t2.read_text().replace("## Status: blocked", "## Status: done"))
        idx = workspace / "BACKLOG.md"
        idx.write_text(
            idx.read_text().replace(
                "| E0-F1-S1-T2 | Fix | Task | in-queue |",
                "| E0-F1-S1-T2 | Fix | Task | done |",
            )
        )
        with pytest.raises(ProposalError, match="already terminal"):
            add_dep(
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                blocked_task_id="E0-F1-S1-T1",
                blocker_task_id="E0-F1-S1-T2",
            )


class TestRejectProposal:
    def test_empty_reason_raises(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        with pytest.raises(ProposalError, match="non-empty reason"):
            reject_proposal(
                workspace_root=workspace,
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                task_id="E0-F1-S1-T2",
                reason="   ",
            )

    def test_archives_draft_and_removes_row(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        proposal = _sample_proposal(task_ids=["E0-F1-S1-T2"])
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
            reason="wrong direction",
        )
        assert archive is not None and archive.is_file()
        assert "E0-F1-S1-T2" not in (workspace / "BACKLOG.md").read_text()
        # Source task got an audit comment.
        source = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        assert "[PROPOSAL_REJECTED]" in source.read_text()

    def test_rejects_proposal_when_draft_missing(self, tmp_path: Path) -> None:
        """Idempotent: missing draft returns ``None`` without error."""
        workspace = _build_workspace(tmp_path)
        archive = reject_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T99",
            reason="already gone",
        )
        assert archive is None


# ---------------------------------------------------------------------------
# Find helpers used by promote / reject
# ---------------------------------------------------------------------------


class TestFindHelpers:
    def test_find_draft_file_missing(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        assert _find_draft_file(workspace / "backlog", "E0-F1-S1-T99") is None

    def test_find_source_task_file_missing_index(self, tmp_path: Path) -> None:
        assert _find_source_task_file(tmp_path / "backlog", tmp_path / "nonexistent.md", "X") is None

    def test_find_originating_source_task_returns_none_when_nothing(self, tmp_path: Path) -> None:
        assert _find_originating_source_task(tmp_path, "E0-F1-S1-T2") is None


class TestAppendDependencyToSource:
    def test_appends_when_no_none(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        # Overwrite source task with a non-empty Dependencies section (no "none").
        source = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        content = source.read_text().replace(
            "| none | | |",
            "| E0-F1-S1-T4 | some | done |",
        )
        source.write_text(content)
        _append_dependency_to_source(workspace / "backlog", workspace / "BACKLOG.md", "E0-F1-S1-T1", "E0-F1-S1-T9")
        updated = source.read_text()
        assert "| E0-F1-S1-T9 |" in updated

    def test_raises_without_dependencies_section(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        source = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        # Keep a valid parser-readable header but drop the Dependencies section.
        source.write_text(
            "# E0-F1-S1-T1: Source Task\n\n"
            "## Status: blocked\n\n"
            "## Target Repository\n\n- **Repo:** `caylent-solutions/example`\n\n"
            "## Description\n\ntext\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-TEST-001 cover edge\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `a.py` | x |\n\n"
            "## Definition of Done\n\n- [ ] all complete\n"
        )
        with pytest.raises(ProposalError, match="no '## Dependencies'"):
            _append_dependency_to_source(
                workspace / "backlog",
                workspace / "BACKLOG.md",
                "E0-F1-S1-T1",
                "E0-F1-S1-T9",
            )

    def test_raises_when_source_missing(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        with pytest.raises(ProposalError, match="Cannot find source"):
            _append_dependency_to_source(
                workspace / "backlog",
                workspace / "BACKLOG.md",
                "NO-SUCH-TASK",
                "E0-F1-S1-T9",
            )


class TestSourceCommentsSectionAlreadyPresent:
    """When a source task already has a ## Comments section, rejects append to it."""

    def test_reject_appends_to_existing_comments(self, tmp_path: Path) -> None:
        workspace = _build_workspace(tmp_path)
        # Write a source task that already has a ## Comments section.
        source = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        content = source.read_text() + "\n## Comments\n\n[2026-04-17 10:00 UTC] [agent/orchestrator] prior entry\n"
        source.write_text(content)

        proposal = _sample_proposal(task_ids=["E0-F1-S1-T2"])
        write_proposal(workspace, proposal)
        materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        reject_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
            reason="not needed",
        )
        updated = source.read_text()
        assert "prior entry" in updated
        assert "[PROPOSAL_REJECTED]" in updated


class TestAppendPromoteCommentNoExistingComments:
    """_append_promote_comment creates a ## Comments section when missing."""

    def test_creates_comments_when_absent(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import _append_promote_comment

        source_file = tmp_path / "source.md"
        source_file.write_text("# T: X\n\n## Status: blocked\n\n## Description\n\nx\n")
        _append_promote_comment(source_file, "E0-F1-S1-T1", "E0-F1-S1-T2")
        updated = source_file.read_text()
        assert "## Comments" in updated
        assert "[PROPOSAL_PROMOTED]" in updated


# ---------------------------------------------------------------------------
# Proposal-lifecycle observability + cleanup (ADR-08 slices A / E / F / H).
# ---------------------------------------------------------------------------


def _draft_body(status: str, task_id: str = "E0-F1-S1-T1") -> str:
    """Return a minimal draft-file body with the given ## Status value.

    The heading uses ``task_id`` so ``BacklogParser.parse_work_unit_file``
    recovers the same ID the enclosing test is using.
    """
    return f"# {task_id}: X\n\n## Status: {status}\n\n## Description\n\nx\n"


def _seed_draft(backlog_root: Path, task_id: str, status: str) -> Path:
    """Create a draft .md for ``task_id`` with the given status under a story dir."""
    from devbench.backlog.proposal import _extract_story_id, _story_dir

    story_dir = _story_dir(backlog_root, _extract_story_id(task_id))
    story_dir.mkdir(parents=True, exist_ok=True)
    draft = story_dir / f"{task_id}.md"
    draft.write_text(_draft_body(status, task_id=task_id))
    return draft


class TestClassifyProposedTask:
    """Every ``ProposalTaskState`` value has a deterministic fixture scenario."""

    def test_unmaterialised_when_no_draft_and_no_archive(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import (
            ProposalTaskState,
            classify_proposed_task,
        )

        (tmp_path / "backlog").mkdir()
        state = classify_proposed_task(tmp_path / "backlog", tmp_path, "E0-F1-S1-T9")
        assert state is ProposalTaskState.UNMATERIALISED

    def test_rejected_when_archive_entry_exists(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import (
            REJECTED_PROPOSAL_DIR_NAME,
            ProposalTaskState,
            classify_proposed_task,
        )

        (tmp_path / "backlog").mkdir()
        archive_dir = tmp_path / REJECTED_PROPOSAL_DIR_NAME
        archive_dir.mkdir(parents=True)
        (archive_dir / "E0-F1-S1-T9-20260101T000000Z.md").write_text("archived")
        state = classify_proposed_task(tmp_path / "backlog", tmp_path, "E0-F1-S1-T9")
        assert state is ProposalTaskState.REJECTED

    @pytest.mark.parametrize(
        "status,expected_state",
        [
            ("proposed", "PROPOSED"),
            ("done", "DONE"),
            ("declined", "DECLINED"),
            ("in-queue", "PROMOTED"),
            ("in-progress", "PROMOTED"),
            ("in-review", "PROMOTED"),
            ("blocked", "PROMOTED"),
        ],
    )
    def test_draft_status_maps_to_state(self, tmp_path: Path, status: str, expected_state: str) -> None:
        from devbench.backlog.proposal import (
            ProposalTaskState,
            classify_proposed_task,
        )

        backlog_root = tmp_path / "backlog"
        backlog_root.mkdir()
        _seed_draft(backlog_root, "E0-F1-S1-T2", status)
        state = classify_proposed_task(backlog_root, tmp_path, "E0-F1-S1-T2")
        assert state is ProposalTaskState[expected_state]

    def test_malformed_draft_with_no_status_line_treated_as_promoted(self, tmp_path: Path) -> None:
        """Defensive: a draft missing its Status line is still classified (as PROMOTED)."""
        from devbench.backlog.proposal import (
            ProposalTaskState,
            _extract_story_id,
            _story_dir,
            classify_proposed_task,
        )

        backlog_root = tmp_path / "backlog"
        story_dir = _story_dir(backlog_root, _extract_story_id("E0-F1-S1-T2"))
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T2.md").write_text("# header only\n")
        state = classify_proposed_task(backlog_root, tmp_path, "E0-F1-S1-T2")
        assert state is ProposalTaskState.PROMOTED


class TestClassifyBlockedTask:
    """ADR-10: classify_blocked_task distinguishes auto-clearing from needs-attention."""

    def _workspace_with_markers(self, tmp_path: Path, marker_target_status_pairs: list[tuple[str, str]]) -> Path:
        """Build a workspace where E0-F1-S1-T1 carries markers for each (id, status) pair.

        Every marker target gets a corresponding row in BACKLOG.md with the
        given status. The blocked-source task itself (T1) has ``blocked``
        status + one comment line per marker target.
        """
        backlog_dir = tmp_path / "backlog"
        story_dir = backlog_dir / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)

        # Source task with the markers in its Comments section.
        comments_block = "\n## Comments\n\n" + "\n".join(
            f"[2026-04-20 00:00 UTC] [agent/task_factory] [PROPOSAL_PROMOTED] {tid} "
            f"promoted and wired as dependency of E0-F1-S1-T1. [BLOCKED_PENDING_PROPOSAL] {tid}"
            for tid, _ in marker_target_status_pairs
        )
        source_file = story_dir / "E0-F1-S1-T1.md"
        source_file.write_text(
            "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n"
            "## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n" + comments_block
        )

        # Marker target files and BACKLOG.md rows.
        rows = ["| E0-F1-S1-T1 | Source | Task | blocked | None | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |"]
        for tid, status in marker_target_status_pairs:
            (story_dir / f"{tid}.md").write_text(f"# {tid}: X\n\n## Status: {status}\n")
            rows.append(f"| {tid} | Marker | Task | {status} | None | r | `backlog/E0/E0-F1/E0-F1-S1/{tid}.md` |")

        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n" + "\n".join(rows) + "\n"
        )
        return tmp_path

    def test_task_with_all_active_markers_is_auto_clearing(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        workspace = self._workspace_with_markers(
            tmp_path, [("E0-F1-S1-T2", "in-queue"), ("E0-F1-S1-T3", "in-progress")]
        )
        state = classify_blocked_task(workspace / "backlog", workspace / "BACKLOG.md", "E0-F1-S1-T1")
        assert state is BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL

    def test_task_with_mixed_markers_is_auto_clearing(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        workspace = self._workspace_with_markers(tmp_path, [("E0-F1-S1-T2", "done"), ("E0-F1-S1-T3", "in-queue")])
        state = classify_blocked_task(workspace / "backlog", workspace / "BACKLOG.md", "E0-F1-S1-T1")
        assert state is BlockedTaskState.AUTO_CLEARING_VIA_PROPOSAL

    def test_task_with_all_terminal_markers_is_needs_attention(self, tmp_path: Path) -> None:
        """Cascade should already have fired; the task is still blocked = diagnostic signal."""
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        workspace = self._workspace_with_markers(tmp_path, [("E0-F1-S1-T2", "done"), ("E0-F1-S1-T3", "declined")])
        state = classify_blocked_task(workspace / "backlog", workspace / "BACKLOG.md", "E0-F1-S1-T1")
        assert state is BlockedTaskState.NEEDS_OPERATOR_ATTENTION

    def test_task_with_unknown_marker_id_is_needs_attention(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        workspace = self._workspace_with_markers(tmp_path, [("E0-F1-S1-T2", "in-queue")])
        # Add a second marker pointing at an ID with no backlog row.
        source_file = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        source_file.write_text(
            source_file.read_text()
            + "\n[2026-04-20 00:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T99\n"
        )
        state = classify_blocked_task(workspace / "backlog", workspace / "BACKLOG.md", "E0-F1-S1-T1")
        assert state is BlockedTaskState.NEEDS_OPERATOR_ATTENTION

    def test_task_with_no_markers_is_needs_attention(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Reach the empty-markers branch without depending on BacklogParser internals."""
        from devbench.backlog import proposal as proposal_mod
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        source = tmp_path / "fake.md"
        source.write_text("# fake\n\n## Status: blocked\n")
        monkeypatch.setattr(proposal_mod, "_find_source_task_file", lambda *a, **kw: source)

        class _NoMarkers:
            def _extract_pending_proposal_markers(self, _p):
                return set()

            def _parse_backlog_rows(self, _path):
                return []

        monkeypatch.setattr(proposal_mod, "BacklogManager", _NoMarkers)
        state = classify_blocked_task(tmp_path / "backlog", tmp_path / "BACKLOG.md", "E0-F1-S1-T1")
        assert state is BlockedTaskState.NEEDS_OPERATOR_ATTENTION

    def test_task_not_in_backlog_is_needs_attention(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
        )
        state = classify_blocked_task(tmp_path / "backlog", tmp_path / "BACKLOG.md", "E0-F1-S1-T99")
        assert state is BlockedTaskState.NEEDS_OPERATOR_ATTENTION

    def test_missing_backlog_index_is_needs_attention(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """ADR-10: if _parse_backlog_rows raises FileNotFoundError, classifier defaults to needs-attention."""
        from devbench.backlog import proposal as proposal_mod
        from devbench.backlog.proposal import BlockedTaskState, classify_blocked_task

        backlog_dir = tmp_path / "backlog"
        story_dir = backlog_dir / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        source = story_dir / "E0-F1-S1-T1.md"
        source.write_text(
            "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n"
            "## Comments\n\n"
            "[2026-04-20 00:00 UTC] [agent/task_factory] [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n"
        )
        monkeypatch.setattr(proposal_mod, "_find_source_task_file", lambda *a, **kw: source)

        class _Exploding:
            def _extract_pending_proposal_markers(self, _p):
                return {"E0-F1-S1-T2"}

            def _parse_backlog_rows(self, _path):
                raise FileNotFoundError("forced")

        monkeypatch.setattr(proposal_mod, "BacklogManager", _Exploding)
        state = classify_blocked_task(backlog_dir, tmp_path / "BACKLOG.md", "E0-F1-S1-T1")
        assert state is BlockedTaskState.NEEDS_OPERATOR_ATTENTION


class TestAddDepBlockedMissing:
    def test_add_dep_raises_when_blocked_task_missing_from_backlog(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import add_dep

        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T2 | Fix | Task | in-queue | None | r | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md` |\n"
        )
        story = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        story.mkdir(parents=True)
        (story / "E0-F1-S1-T2.md").write_text("# E0-F1-S1-T2: Fix\n\n## Status: in-queue\n\n## Description\n\nx\n")
        with pytest.raises(ProposalError, match=r"blocked task 'E0-F1-S1-T99' not found"):
            add_dep(
                backlog_root=tmp_path / "backlog",
                backlog_index=tmp_path / "BACKLOG.md",
                blocked_task_id="E0-F1-S1-T99",
                blocker_task_id="E0-F1-S1-T2",
            )


class TestManualDepCommentNoCommentsSection:
    def test_append_manual_dep_creates_comments_section_when_missing(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import _append_manual_dep_comment

        f = tmp_path / "x.md"
        f.write_text("# E0-F1-S1-T1: X\n\n## Status: blocked\n\n## Description\n\nno comments section yet\n")
        _append_manual_dep_comment(f, "E0-F1-S1-T1", "E0-F1-S1-T2", "slice I coverage")
        text = f.read_text()
        assert "## Comments" in text
        assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2" in text

    def test_append_manual_dep_appends_to_existing_comments_section(self, tmp_path: Path) -> None:
        """Line 1015: when the file already has ## Comments, the entry is appended to it."""
        from devbench.backlog.proposal import _append_manual_dep_comment

        f = tmp_path / "y.md"
        f.write_text(
            "# E0-F1-S1-T1: X\n\n## Status: blocked\n\n## Description\n\nx\n\n"
            "## Comments\n\n[2026-04-19 09:00 UTC] [agent/other] prior entry\n"
        )
        _append_manual_dep_comment(f, "E0-F1-S1-T1", "E0-F1-S1-T2", "")
        text = f.read_text()
        # Prior entry still there + the new marker.
        assert "prior entry" in text
        assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2" in text
        # Only ONE "## Comments" header (we did not append a duplicate section).
        assert text.count("## Comments") == 1


class TestFindOriginatingProposalMiss:
    def test_find_originating_proposal_returns_none_when_no_match(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import _find_originating_proposal

        assert _find_originating_proposal(tmp_path, "E0-F1-S1-T99") is None


class TestPromoteProposalSourceFileMissing:
    def test_promote_loops_through_missing_source_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Continue-branch coverage when _find_source_task_file returns None during the wire loop."""
        from devbench.backlog import proposal as proposal_mod

        workspace = _build_workspace(tmp_path)
        proposal = _sample_proposal(task_ids=["E0-F1-S1-T2"])
        write_proposal(workspace, proposal)
        materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        # Single-task proposal means the fail-fast loop skips _find_source_task_file
        # (targets[1:] is empty); the wire loop then calls it once for the source.
        # Patch it to return None so the continue branch is exercised.
        monkeypatch.setattr(proposal_mod, "_find_source_task_file", lambda *a, **kw: None)
        result = proposal_mod.promote_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
        )
        assert result.wired_targets == []


class TestMaterialiseProposalThinApproachRefusal:
    """ADR-08 slice H: materialise refuses thin ``suggested_approach`` fail-fast."""

    def _mini_workspace(self, tmp_path: Path, approach: str) -> tuple[Path, Proposal]:
        """Build a tmp workspace with a single proposal carrying ``approach``."""
        from devbench.backlog.proposal import (
            Proposal,
            ProposedTask,
        )

        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        story_dir = backlog_dir / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        (story_dir / "E0-F1-S1-T1.md").write_text(_draft_body("blocked"))
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source | Task | blocked | None | r | `backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
        )
        proposal = Proposal(
            source_task_id="E0-F1-S1-T1",
            generated_at="2026-04-19T00:00:00Z",
            rejection_reason="scope",
            proposed_tasks=[
                ProposedTask(
                    suggested_id="E0-F1-S1-T2",
                    title="Fix",
                    files_to_own=["src/a.py"],
                    linked_scenarios=["SC-01"],
                    suggested_acs=["AC-001 fix"],
                    suggested_approach=approach,
                )
            ],
        )
        return tmp_path, proposal

    def test_empty_approach_raises_proposal_error(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import ProposalError, materialise_proposal

        workspace, proposal = self._mini_workspace(tmp_path, approach="")
        with pytest.raises(ProposalError, match="suggested_approach too terse"):
            materialise_proposal(
                workspace_root=workspace,
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                proposal=proposal,
                repo="r",
            )

    def test_below_threshold_approach_raises(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import ProposalError, materialise_proposal

        workspace, proposal = self._mini_workspace(tmp_path, approach="RED. GREEN. Verify.")
        with pytest.raises(ProposalError, match="suggested_approach too terse"):
            materialise_proposal(
                workspace_root=workspace,
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                proposal=proposal,
                repo="r",
            )

    def test_at_threshold_passes(self, tmp_path: Path) -> None:
        """A single-char-above-threshold approach materialises successfully."""
        from devbench.backlog.proposal import (
            _SUGGESTED_APPROACH_MIN_CHARS,
            materialise_proposal,
        )

        padded = "x" * (_SUGGESTED_APPROACH_MIN_CHARS + 1)
        workspace, proposal = self._mini_workspace(tmp_path, approach=padded)
        drafts = materialise_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            proposal=proposal,
            repo="r",
        )
        assert len(drafts) == 1

    def test_thin_approach_error_names_task_id(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import ProposalError, materialise_proposal

        workspace, proposal = self._mini_workspace(tmp_path, approach="nope")
        with pytest.raises(ProposalError, match="E0-F1-S1-T2"):
            materialise_proposal(
                workspace_root=workspace,
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                proposal=proposal,
                repo="r",
            )


class TestRejectProposalArgumentForms:
    """ADR-08 slice E: the two mutually-exclusive rejection forms."""

    def test_neither_form_supplied_raises(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import ProposalError, reject_proposal

        with pytest.raises(ProposalError, match="exactly one of"):
            reject_proposal(
                workspace_root=tmp_path,
                backlog_root=tmp_path / "backlog",
                backlog_index=tmp_path / "BACKLOG.md",
                reason="r",
            )

    def test_both_forms_supplied_raises(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import ProposalError, reject_proposal

        with pytest.raises(ProposalError, match="not both"):
            reject_proposal(
                workspace_root=tmp_path,
                backlog_root=tmp_path / "backlog",
                backlog_index=tmp_path / "BACKLOG.md",
                task_id="E0-F1-S1-T2",
                unmaterialised_source_id="E0-F1-S1-T1",
                reason="r",
            )

    def test_empty_reason_raises(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import ProposalError, reject_proposal

        with pytest.raises(ProposalError, match="non-empty reason"):
            reject_proposal(
                workspace_root=tmp_path,
                backlog_root=tmp_path / "backlog",
                backlog_index=tmp_path / "BACKLOG.md",
                task_id="E0-F1-S1-T2",
                reason="",
            )


class TestRejectUnmaterialisedProposal:
    """The new ``--unmaterialised <source-id>`` form."""

    def _mini_workspace(self, tmp_path: Path) -> Path:
        """Build a workspace with a source task + one proposal JSON (un-materialised drafts)."""
        from devbench.backlog.proposal import (
            PROPOSAL_DIR_NAME,
            Proposal,
            ProposedTask,
            write_proposal,
        )

        backlog_dir = tmp_path / "backlog"
        story_dir = backlog_dir / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        source_file = story_dir / "E0-F1-S1-T1.md"
        source_file.write_text(_draft_body("blocked", task_id="E0-F1-S1-T1"))
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source | Task | blocked | None | r | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
        )
        (tmp_path / PROPOSAL_DIR_NAME).mkdir(parents=True, exist_ok=True)
        write_proposal(
            tmp_path,
            Proposal(
                source_task_id="E0-F1-S1-T1",
                generated_at="2026-04-19T00:00:00Z",
                rejection_reason="scope",
                proposed_tasks=[
                    ProposedTask(
                        suggested_id="E0-F1-S1-T2",
                        title="Fix",
                        files_to_own=["src/a.py"],
                        linked_scenarios=["SC-01"],
                        suggested_acs=["AC-001 fix"],
                        suggested_approach=(
                            "Context: unit test. Scope: src/a.py. "
                            "TDD approach: 1. RED. 2. GREEN. 3. REFACTOR no-op. "
                            "Verify: make lint && make test-unit exit zero."
                        ),
                    )
                ],
            ),
        )
        return tmp_path

    def test_happy_path_archives_json_and_audits_source(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import (
            PROPOSAL_DIR_NAME,
            REJECTED_PROPOSAL_DIR_NAME,
            reject_proposal,
        )

        workspace = self._mini_workspace(tmp_path)
        archive = reject_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            unmaterialised_source_id="E0-F1-S1-T1",
            reason="redundant with T3",
        )

        # Archive created with the '-unmaterialised-' infix.
        assert archive is not None
        assert archive.is_file()
        assert archive.parent == workspace / REJECTED_PROPOSAL_DIR_NAME
        assert "unmaterialised" in archive.name

        # Live JSON removed.
        assert not (workspace / PROPOSAL_DIR_NAME / "E0-F1-S1-T1.json").exists()

        # Source task has the audit comment.
        source_text = (workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md").read_text()
        assert "[PROPOSAL_JSON_REJECTED]" in source_text
        assert "redundant with T3" in source_text

    def test_refuses_when_any_task_already_materialised(self, tmp_path: Path) -> None:
        """If any proposed task has a draft .md, per-source rejection must refuse."""
        from devbench.backlog.proposal import ProposalError, reject_proposal

        workspace = self._mini_workspace(tmp_path)
        # Materialise the T2 draft out-of-band so it's in PROPOSED state.
        _seed_draft(workspace / "backlog", "E0-F1-S1-T2", "proposed")

        with pytest.raises(ProposalError, match="already in state"):
            reject_proposal(
                workspace_root=workspace,
                backlog_root=workspace / "backlog",
                backlog_index=workspace / "BACKLOG.md",
                unmaterialised_source_id="E0-F1-S1-T1",
                reason="r",
            )

    def test_refuses_when_json_missing(self, tmp_path: Path) -> None:
        from devbench.backlog.proposal import ProposalError, reject_proposal

        (tmp_path / "backlog").mkdir()
        (tmp_path / "BACKLOG.md").write_text("# Backlog\n")
        with pytest.raises(ProposalError, match="No proposal JSON found"):
            reject_proposal(
                workspace_root=tmp_path,
                backlog_root=tmp_path / "backlog",
                backlog_index=tmp_path / "BACKLOG.md",
                unmaterialised_source_id="E0-F1-S1-T1",
                reason="r",
            )

    def test_happy_path_appends_to_existing_comments_section(self, tmp_path: Path) -> None:
        """When the source already has ``## Comments``, the audit line is appended to it."""
        from devbench.backlog.proposal import REJECTED_PROPOSAL_DIR_NAME, reject_proposal

        workspace = self._mini_workspace(tmp_path)
        source_md = workspace / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        existing = source_md.read_text() + "\n## Comments\n\n[2026-04-18 14:00 UTC] [agent/test] prior\n"
        source_md.write_text(existing)

        archive = reject_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            unmaterialised_source_id="E0-F1-S1-T1",
            reason="appended",
        )
        assert archive is not None
        assert archive.parent == workspace / REJECTED_PROPOSAL_DIR_NAME
        final_text = source_md.read_text()
        assert "prior" in final_text
        assert "[PROPOSAL_JSON_REJECTED]" in final_text
        assert "appended" in final_text


class TestRejectProposalMarkerStrip:
    """ADR-08 slice F: per-draft rejection strips the marker + invokes cascade."""

    def _workspace_with_marker(
        self,
        tmp_path: Path,
        *,
        second_draft_status: str = "done",
    ) -> tuple[Path, Path]:
        """Build a workspace where source T1 has markers for T2 + T3.

        T2 is the draft we'll reject; T3 is seeded with ``second_draft_status``.
        Returns (workspace, source_file).
        """
        from devbench.backlog.proposal import (
            PROPOSAL_DIR_NAME,
            Proposal,
            ProposedTask,
            write_proposal,
        )

        backlog_dir = tmp_path / "backlog"
        story_dir = backlog_dir / "E0" / "E0-F1" / "E0-F1-S1"
        story_dir.mkdir(parents=True)
        source_file = story_dir / "E0-F1-S1-T1.md"
        source_file.write_text(
            "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n## Description\n\nx\n\n"
            "## Dependencies\n\n"
            "| ID | Title | Status |\n|----|-------|--------|\n"
            "| E0-F1-S1-T2 | (auto) | proposed |\n"
            "| E0-F1-S1-T3 | (auto) | proposed |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-TEST-001 x\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n| `x.py` | x |\n\n"
            "## Definition of Done\n\n- [ ] AC complete\n\n"
            "## Comments\n\n"
            "[2026-04-19 14:00 UTC] [agent/task_factory] [PROPOSAL_PROMOTED] E0-F1-S1-T2 "
            "promoted and wired as dependency of E0-F1-S1-T1. [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2\n\n"
            "[2026-04-19 14:01 UTC] [agent/task_factory] [PROPOSAL_PROMOTED] E0-F1-S1-T3 "
            "promoted and wired as dependency of E0-F1-S1-T1. [BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T3\n"
        )
        _seed_draft(backlog_dir, "E0-F1-S1-T2", "in-queue")
        _seed_draft(backlog_dir, "E0-F1-S1-T3", second_draft_status)
        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n"
            "## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked | Declined |\n"
            "|------|-------|------|-------------|----------|---------|----------|\n"
            "| E0 | Ex | 0 | 0 | 1 | 1 | 0 |\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0 | Ex | Epic | in-queue | None | r | `backlog/E0.md` |\n"
            "| E0-F1-S1-T1 | Src | Task | blocked | None | r | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n"
            "| E0-F1-S1-T2 | Dep1 | Task | in-queue | None | r | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T2.md` |\n"
            f"| E0-F1-S1-T3 | Dep2 | Task | {second_draft_status} | None | r | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T3.md` |\n"
        )
        (backlog_dir / "E0.md").write_text("# E0: Ex\n\n## Status: in-queue\n")
        # Seed a proposal JSON so _find_originating_source_task resolves T2's source.
        (tmp_path / PROPOSAL_DIR_NAME).mkdir(parents=True, exist_ok=True)
        write_proposal(
            tmp_path,
            Proposal(
                source_task_id="E0-F1-S1-T1",
                generated_at="2026-04-19T00:00:00Z",
                rejection_reason="scope",
                proposed_tasks=[
                    ProposedTask(
                        suggested_id="E0-F1-S1-T2",
                        title="Dep1",
                        files_to_own=["src/a.py"],
                        linked_scenarios=["SC-01"],
                        suggested_acs=["AC-001 fix"],
                        suggested_approach=(
                            "Context: unit test fixture. Scope: a. "
                            "TDD: 1 RED 2 GREEN 3 REFACTOR no-op. "
                            "Verify: make lint && make test-unit all exit zero."
                        ),
                    ),
                    ProposedTask(
                        suggested_id="E0-F1-S1-T3",
                        title="Dep2",
                        files_to_own=["src/b.py"],
                        linked_scenarios=["SC-02"],
                        suggested_acs=["AC-002 fix"],
                        suggested_approach=(
                            "Context: unit test fixture. Scope: b. "
                            "TDD: 1 RED 2 GREEN 3 REFACTOR no-op. "
                            "Verify: make lint && make test-unit all exit zero."
                        ),
                    ),
                ],
            ),
        )
        return tmp_path, source_file

    def test_rejecting_draft_strips_marker_and_cascade_fires(self, tmp_path: Path) -> None:
        """T3 is already done; reject T2 -> cascade sees T1's remaining markers all terminal -> requeue."""
        from devbench.backlog.proposal import reject_proposal

        workspace, source_file = self._workspace_with_marker(tmp_path, second_draft_status="done")

        reject_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
            reason="redundant",
        )

        updated = source_file.read_text()
        # T2 marker stripped.
        assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2" not in updated
        # T3 marker preserved.
        assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T3" in updated
        # Cascade fired: source auto-flipped because remaining marker (T3) is terminal.
        assert "## Status: in-queue" in updated
        assert "[AUTO_UNBLOCKED]" in updated

    def test_rejecting_draft_when_other_marker_still_active_keeps_blocked(self, tmp_path: Path) -> None:
        """T3 is still in-queue (non-terminal); reject T2 -> cascade abstains."""
        from devbench.backlog.proposal import reject_proposal

        workspace, source_file = self._workspace_with_marker(tmp_path, second_draft_status="in-queue")

        reject_proposal(
            workspace_root=workspace,
            backlog_root=workspace / "backlog",
            backlog_index=workspace / "BACKLOG.md",
            task_id="E0-F1-S1-T2",
            reason="redundant",
        )

        updated = source_file.read_text()
        assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T2" not in updated
        assert "[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T3" in updated
        # T3 still non-terminal -> source stays blocked.
        assert "## Status: blocked" in updated
        assert "[AUTO_UNBLOCKED]" not in updated

    def test_strip_marker_on_source_with_no_matching_marker_is_noop(self, tmp_path: Path) -> None:
        """Calling reject for a task whose marker was never on the source is safe."""
        from devbench.backlog.proposal import _strip_pending_proposal_marker

        source_file = tmp_path / "src.md"
        original = "# x\n\n## Comments\n\n[some comment]\n"
        source_file.write_text(original)
        _strip_pending_proposal_marker(source_file, "E0-F1-S1-T99")
        assert source_file.read_text() == original
