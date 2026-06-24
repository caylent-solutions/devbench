"""Issue #141 regression: ``find_matching_pending_proposal`` scanner.

Pin the on-disk dedup-scan behaviour: matches by ``fix_signature`` only,
skips proposals whose source task is in a terminal state (``done`` /
``declined``), returns None when no match exists, handles malformed
JSON / missing dirs gracefully.
"""

from __future__ import annotations

import json
from pathlib import Path

from devbench.backlog.proposal import find_matching_pending_proposal


def _seed_proposal(workspace_root: Path, source_id: str, signature: str) -> Path:
    """Write a minimal valid proposal JSON to ``.devbench/proposals/<id>.json``."""
    proposals_dir = workspace_root / ".devbench" / "proposals"
    proposals_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "source_task_id": source_id,
        "generated_at": "2026-05-02T00:00:00Z",
        "rejection_reason": "test fixture",
        "proposed_tasks": [],
        "fix_signature": signature,
    }
    path = proposals_dir / f"{source_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _seed_source_task(workspace_root: Path, source_id: str, status: str) -> None:
    """Write a minimal work-unit markdown carrying the ``## Status:`` line."""
    backlog = workspace_root / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
    backlog.mkdir(parents=True, exist_ok=True)
    (backlog / f"{source_id}.md").write_text(f"# {source_id}: fixture\n\n## Status: {status}\n", encoding="utf-8")


class TestFindMatchingPendingProposal:
    """Pin the scanner contract."""

    def test_returns_match_when_signature_present(self, tmp_path: Path) -> None:
        sig = "abc123" * 10
        _seed_source_task(tmp_path, "E0-F1-S1-T1", status="in-queue")
        path = _seed_proposal(tmp_path, "E0-F1-S1-T1", sig)

        result = find_matching_pending_proposal(tmp_path, sig)

        assert result is not None
        assert result.source_task_id == "E0-F1-S1-T1"
        assert result.fix_signature == sig
        assert result.proposal_path == path.resolve()

    def test_returns_none_when_no_match(self, tmp_path: Path) -> None:
        _seed_source_task(tmp_path, "E0-F1-S1-T1", status="in-queue")
        _seed_proposal(tmp_path, "E0-F1-S1-T1", "different-sig")

        result = find_matching_pending_proposal(tmp_path, "missing-sig")
        assert result is None

    def test_skips_terminal_state_done(self, tmp_path: Path) -> None:
        sig = "term-sig"
        _seed_source_task(tmp_path, "E0-F1-S1-T1", status="done")
        _seed_proposal(tmp_path, "E0-F1-S1-T1", sig)

        result = find_matching_pending_proposal(tmp_path, sig)
        assert result is None

    def test_skips_terminal_state_declined(self, tmp_path: Path) -> None:
        sig = "decl-sig"
        _seed_source_task(tmp_path, "E0-F1-S1-T1", status="declined")
        _seed_proposal(tmp_path, "E0-F1-S1-T1", sig)

        result = find_matching_pending_proposal(tmp_path, sig)
        assert result is None

    def test_finds_in_progress_match_among_multiple(self, tmp_path: Path) -> None:
        """A pending in-queue proposal beats a declined one for the same
        signature -- the scanner returns the first non-terminal match."""
        sig = "shared-sig"
        _seed_source_task(tmp_path, "E0-F1-S1-T1", status="declined")
        _seed_proposal(tmp_path, "E0-F1-S1-T1", sig)
        _seed_source_task(tmp_path, "E0-F1-S1-T2", status="in-queue")
        _seed_proposal(tmp_path, "E0-F1-S1-T2", sig)

        result = find_matching_pending_proposal(tmp_path, sig)
        assert result is not None
        assert result.source_task_id == "E0-F1-S1-T2"

    def test_empty_signature_never_matches(self, tmp_path: Path) -> None:
        """The empty string is the legacy default for proposals authored
        before the dedup feature shipped. Querying with empty signature
        must NOT match every legacy-signature proposal."""
        _seed_source_task(tmp_path, "E0-F1-S1-T1", status="in-queue")
        _seed_proposal(tmp_path, "E0-F1-S1-T1", "")

        result = find_matching_pending_proposal(tmp_path, "")
        assert result is None

    def test_no_proposals_dir_returns_none(self, tmp_path: Path) -> None:
        result = find_matching_pending_proposal(tmp_path, "any-sig")
        assert result is None

    def test_malformed_json_skipped(self, tmp_path: Path) -> None:
        proposals_dir = tmp_path / ".devbench" / "proposals"
        proposals_dir.mkdir(parents=True)
        (proposals_dir / "bad.json").write_text("{not valid json", encoding="utf-8")

        sig = "valid-sig"
        _seed_source_task(tmp_path, "E0-F1-S1-T1", status="in-queue")
        _seed_proposal(tmp_path, "E0-F1-S1-T1", sig)

        result = find_matching_pending_proposal(tmp_path, sig)
        assert result is not None
        assert result.source_task_id == "E0-F1-S1-T1"

    def test_signature_stable_after_unrelated_disk_edit(self, tmp_path: Path) -> None:
        """An operator hand-edit that doesn't touch fix_signature must
        leave the dedup match working."""
        sig = "stable-sig"
        _seed_source_task(tmp_path, "E0-F1-S1-T1", status="in-queue")
        path = _seed_proposal(tmp_path, "E0-F1-S1-T1", sig)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["rejection_reason"] = "operator hand-edit -- expanded the explanation text"
        path.write_text(json.dumps(payload), encoding="utf-8")

        result = find_matching_pending_proposal(tmp_path, sig)
        assert result is not None
        assert result.fix_signature == sig

    def test_proposal_with_empty_source_task_id_skipped(self, tmp_path: Path) -> None:
        """A malformed proposal whose source_task_id is empty string must be
        skipped by the scanner (would otherwise crash _source_task_in_terminal_state).
        Filename sorts BEFORE the good proposal so the bad-record code path
        is actually exercised (sorted glob order matters)."""
        proposals_dir = tmp_path / ".devbench" / "proposals"
        proposals_dir.mkdir(parents=True)
        sig = "valid-sig"
        bad_path = proposals_dir / "AAA-broken.json"
        bad_path.write_text(
            json.dumps(
                {
                    "source_task_id": "",
                    "generated_at": "2026-05-02T00:00:00Z",
                    "rejection_reason": "fixture",
                    "proposed_tasks": [],
                    "fix_signature": sig,
                }
            ),
            encoding="utf-8",
        )
        _seed_source_task(tmp_path, "E0-F1-S1-T1", status="in-queue")
        _seed_proposal(tmp_path, "E0-F1-S1-T1", sig)

        result = find_matching_pending_proposal(tmp_path, sig)
        assert result is not None
        assert result.source_task_id == "E0-F1-S1-T1"

    def test_no_backlog_dir_treated_as_non_terminal(self, tmp_path: Path) -> None:
        """When the workspace has no `backlog/` directory at all,
        ``_source_task_in_terminal_state`` returns False so the scanner
        treats the proposal as still in flight (best-effort fallback)."""
        sig = "no-backlog-sig"
        proposals_dir = tmp_path / ".devbench" / "proposals"
        proposals_dir.mkdir(parents=True)
        proposal = proposals_dir / "E0-F1-S1-T1.json"
        proposal.write_text(
            json.dumps(
                {
                    "source_task_id": "E0-F1-S1-T1",
                    "generated_at": "2026-05-02T00:00:00Z",
                    "rejection_reason": "fixture",
                    "proposed_tasks": [],
                    "fix_signature": sig,
                }
            ),
            encoding="utf-8",
        )
        result = find_matching_pending_proposal(tmp_path, sig)
        assert result is not None
        assert result.source_task_id == "E0-F1-S1-T1"

    def test_source_task_with_no_status_line_treated_as_non_terminal(self, tmp_path: Path) -> None:
        """A source-task markdown that lacks a `## Status:` line returns
        False from terminal-state check (defensive fallback)."""
        sig = "no-status-sig"
        proposals_dir = tmp_path / ".devbench" / "proposals"
        proposals_dir.mkdir(parents=True)
        (proposals_dir / "E0-F1-S1-T1.json").write_text(
            json.dumps(
                {
                    "source_task_id": "E0-F1-S1-T1",
                    "generated_at": "2026-05-02T00:00:00Z",
                    "rejection_reason": "fixture",
                    "proposed_tasks": [],
                    "fix_signature": sig,
                }
            ),
            encoding="utf-8",
        )
        backlog = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        backlog.mkdir(parents=True)
        (backlog / "E0-F1-S1-T1.md").write_text("# E0-F1-S1-T1: fixture\n\nNo status line here.\n", encoding="utf-8")

        result = find_matching_pending_proposal(tmp_path, sig)
        assert result is not None
        assert result.source_task_id == "E0-F1-S1-T1"

    def test_malformed_json_sorts_first_skipped(self, tmp_path: Path) -> None:
        """Pin lines 694-695: when the malformed JSON file sorts BEFORE
        the good proposal in glob order, the json.JSONDecodeError branch
        must trigger (the existing test_malformed_json_skipped happens
        to read the good proposal first because it sorts earlier)."""
        proposals_dir = tmp_path / ".devbench" / "proposals"
        proposals_dir.mkdir(parents=True)
        (proposals_dir / "AAA-malformed.json").write_text("{this is not valid json", encoding="utf-8")
        sig = "valid-sig"
        _seed_source_task(tmp_path, "E0-F1-S1-T1", status="in-queue")
        _seed_proposal(tmp_path, "E0-F1-S1-T1", sig)

        result = find_matching_pending_proposal(tmp_path, sig)
        assert result is not None
        assert result.source_task_id == "E0-F1-S1-T1"

    def test_source_task_unreadable_treated_as_non_terminal(self, tmp_path: Path) -> None:
        """Pin lines 726-727: OSError reading the source-task markdown is
        swallowed, so the scanner falls through to "not terminal" and the
        proposal is still treated as a legitimate match. Achieved by
        making the source-task markdown unreadable (chmod 000).
        """
        sig = "unreadable-sig"
        _seed_source_task(tmp_path, "E0-F1-S1-T1", status="in-queue")
        _seed_proposal(tmp_path, "E0-F1-S1-T1", sig)
        md_path = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        original_mode = md_path.stat().st_mode
        try:
            md_path.chmod(0o000)
            result = find_matching_pending_proposal(tmp_path, sig)
            assert result is not None
            assert result.source_task_id == "E0-F1-S1-T1"
        finally:
            md_path.chmod(original_mode)

    def test_backlog_dir_exists_but_source_task_not_found(self, tmp_path: Path) -> None:
        """Pin line 734: backlog dir is present but doesn't contain a file
        named ``<source_task_id>.md`` -- the rglob loop completes with
        zero matches and the helper returns False (non-terminal)."""
        sig = "missing-md-sig"
        proposals_dir = tmp_path / ".devbench" / "proposals"
        proposals_dir.mkdir(parents=True)
        (proposals_dir / "E0-F1-S1-T1.json").write_text(
            json.dumps(
                {
                    "source_task_id": "E0-F1-S1-T1",
                    "generated_at": "2026-05-02T00:00:00Z",
                    "rejection_reason": "fixture",
                    "proposed_tasks": [],
                    "fix_signature": sig,
                }
            ),
            encoding="utf-8",
        )
        backlog = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        backlog.mkdir(parents=True)
        (backlog / "OTHER-T1.md").write_text("# OTHER-T1\n\n## Status: in-queue\n", encoding="utf-8")

        result = find_matching_pending_proposal(tmp_path, sig)
        assert result is not None
        assert result.source_task_id == "E0-F1-S1-T1"
