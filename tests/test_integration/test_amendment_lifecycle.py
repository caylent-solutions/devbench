"""Integration tests for the complete amendment lifecycle.

Exercises the full chain: CLI request -> PreFilter -> apply/reject -> Layer 3
post-check -> state transitions. Unlike the unit tests, these tests run the
CLI through the in-process ``cli.main`` entry point so argparse, stdin
handling, and the command registry are all covered end-to-end.
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from devbench import cli
from devbench.backlog.amendment import (
    AMENDMENT_APPLIED_ACTION,
    AMENDMENT_REJECTED_ACTION,
    AmendmentRequest,
    request_path,
    write_request,
)
from devbench.backlog.manifest import parse_manifest

# ---------------------------------------------------------------------------
# Shared fixture content (generic, backlog-agnostic)
# ---------------------------------------------------------------------------

WORK_UNIT_TEMPLATE = """\
# {task_id}: Sample Task

## Status: in-progress

## Description

Example task description.

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| none | | |

## Acceptance Criteria

- [ ] AC-TEST-001 cover the edge case
- [ ] AC-FUNC-001 end-to-end works

## Changes Manifest

| File | Change |
|------|--------|
| `tests/test_example.py` | new unit tests |

## Definition of Done

- [ ] All AC checked
"""

BACKLOG_INDEX_TEMPLATE = """\
# Backlog

## Status Summary

| Epic | Title | Done | In Progress | In Queue | Blocked |
|------|-------|------|-------------|----------|---------|
| EX | Example Epic | 0 | 1 | 0 | 0 |

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
| EX-F1-S1-T1 | Sample Task | Task | in-progress | None | caylent-solutions/example | `backlog/EX-F1-S1-T1.md` |
"""


def _build_workspace(tmp_path: Path) -> Path:
    task_id = "EX-F1-S1-T1"
    (tmp_path / "BACKLOG.md").write_text(BACKLOG_INDEX_TEMPLATE)
    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir()
    (backlog_dir / f"{task_id}.md").write_text(WORK_UNIT_TEMPLATE.format(task_id=task_id), encoding="utf-8")
    return tmp_path


def _build_target_repo(tmp_path: Path, staged: tuple[str, ...] = ()) -> Path:
    """Create a real git repo whose *staged* paths are staged against a baseline commit.

    ``cmd_request_amendment`` runs the Layer 1 pre-filter, which asks git which
    paths are staged and which have any change at all, so the amendment path
    needs a genuine repo rather than a stub.
    """
    repo = tmp_path / "target-repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@ex.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    (repo / "baseline.txt").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "baseline.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True, capture_output=True)
    for rel in staged:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("content\n", encoding="utf-8")
        subprocess.run(["git", "add", rel], cwd=repo, check=True, capture_output=True)
    return repo


@contextlib.contextmanager
def _amendment_cli_context(workspace: Path, repo: Path) -> Iterator[None]:
    """Patch the module globals ``cmd_request_amendment`` resolves the target repo through."""
    with (
        patch("devbench.cli.WORKSPACE_ROOT", workspace),
        patch("devbench.cli.BACKLOG_ROOT", workspace / "backlog"),
        patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
        patch("devbench.cli.REPO_LOCAL_PATHS", {"caylent-solutions/example": repo}),
        patch("devbench.cli.resolve_repo", return_value="caylent-solutions/example"),
        patch("devbench.cli.validate_repo", return_value=None),
    ):
        yield


def _valid_payload() -> dict[str, Any]:
    return {
        "reason": "tdd_green_production_fix",
        "justification": "AC-TEST-001 needs a production fix.",
        "files_to_add": [
            {"path": "src/example/example.py", "change": "minimum fix for AC-TEST-001"},
        ],
        "linked_acs": ["AC-TEST-001"],
    }


# ---------------------------------------------------------------------------
# Full lifecycle: request -> apply -> verify
# ---------------------------------------------------------------------------


class TestAmendmentLifecycleHappyPath:
    def test_request_and_apply_updates_manifest(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        workspace = _build_workspace(tmp_path)
        task_id = "EX-F1-S1-T1"
        repo = _build_target_repo(tmp_path, staged=("src/example/example.py",))

        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_valid_payload())))
        with _amendment_cli_context(workspace, repo):
            rc_request = cli.cmd_request_amendment(task_id)
        assert rc_request == 0, capsys.readouterr().err

        assert request_path(workspace, task_id).exists()

        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
        ):
            rc_apply = cli.cmd_apply_amendment(task_id)
        assert rc_apply == 0, capsys.readouterr().err

        # Manifest now has the new row + audit comment present, request file gone.
        wu_content = (workspace / "backlog" / f"{task_id}.md").read_text(encoding="utf-8")
        rows = parse_manifest(wu_content)
        assert len(rows) == 2
        assert rows[1].file == "src/example/example.py"
        assert AMENDMENT_APPLIED_ACTION in wu_content
        assert not request_path(workspace, task_id).exists()


class TestAmendmentLifecycleRejectPath:
    def test_request_then_reject_blocks_task_and_preserves_manifest(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        workspace = _build_workspace(tmp_path)
        task_id = "EX-F1-S1-T1"
        repo = _build_target_repo(tmp_path, staged=("src/example/example.py",))

        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_valid_payload())))
        with _amendment_cli_context(workspace, repo):
            assert cli.cmd_request_amendment(task_id) == 0, capsys.readouterr().err

        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
        ):
            rc_reject = cli.cmd_reject_amendment(task_id, "files extend beyond the linked AC")
        assert rc_reject == 0, capsys.readouterr().err

        wu_content = (workspace / "backlog" / f"{task_id}.md").read_text(encoding="utf-8")
        rows = parse_manifest(wu_content)
        # Manifest unchanged (still only the original row)
        assert len(rows) == 1
        assert rows[0].file == "tests/test_example.py"
        # Audit + blocked status present
        assert AMENDMENT_REJECTED_ACTION in wu_content
        assert "## Status: blocked" in wu_content
        # Pending request moved to rejected-requests archive (not deleted)
        assert not request_path(workspace, task_id).exists()
        archive_dir = workspace / ".devbench" / "rejected-requests"
        assert archive_dir.is_dir()
        archived = list(archive_dir.glob(f"{task_id}-*.json"))
        assert len(archived) == 1, f"Expected one archived request, got {archived}"


class TestAmendmentLifecycleRejectCleansStagedFiles:
    """The amender reject branch must revert any staged production edits.

    The plan calls for running the git-restore cleanup inside the amender's
    reject bash recipe BEFORE invoking ``reject-amendment`` so the CLI does
    not grow a git responsibility. This test runs that exact recipe against
    a tmp git repo and asserts the tree is clean when it finishes.
    """

    def test_reject_recipe_reverts_staged_and_untracked(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import subprocess as sp

        # Build a tmp repo with one committed file + one staged production
        # edit + one untracked file that also appears in the amendment.
        repo = tmp_path / "repo"
        repo.mkdir()
        sp.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        sp.run(["git", "config", "user.email", "t@ex.com"], cwd=repo, check=True, capture_output=True)
        sp.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
        (repo / "README.md").write_text("# hi\n")
        (repo / "src").mkdir()
        (repo / "src" / "parser.py").write_text("original\n")
        sp.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        sp.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

        # Executor mutates a tracked file AND adds a new untracked one, then
        # stages both (the mutated one changes; the new one is a new-file add).
        (repo / "src" / "parser.py").write_text("prod fix staged\n")
        (repo / "src" / "new_util.py").write_text("new module\n")
        sp.run(["git", "add", "src/parser.py", "src/new_util.py"], cwd=repo, check=True, capture_output=True)

        # Build a minimal devbench workspace with a pending amendment whose
        # files_to_add name both of those paths.
        workspace = _build_workspace(tmp_path)
        task_id = "EX-F1-S1-T1"
        from devbench.backlog.amendment import write_request as _write

        _write(
            workspace,
            _make_request(task_id, ["src/parser.py", "src/new_util.py"]),
        )

        # Run the amender's reject-branch cleanup recipe directly. We expand
        # the file list from the pending request and invoke git restore +
        # checkout + clean for each file.
        import json as _json

        request_data = _json.loads((workspace / ".devbench" / "amendments" / f"{task_id}.json").read_text())
        for entry in request_data["files_to_add"]:
            path = entry["path"]
            # Unstage, restore, and clean so tracked edits revert and
            # untracked additions disappear. Mirrors the recipe the agent
            # prompt now runs.
            sp.run(["git", "-C", str(repo), "restore", "--staged", path], check=False, capture_output=True)
            sp.run(["git", "-C", str(repo), "checkout", "--", path], check=False, capture_output=True)
            sp.run(["git", "-C", str(repo), "clean", "-f", "--", path], check=False, capture_output=True)

        # Now invoke reject-amendment (which blocks the task + archives).
        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
        ):
            rc = cli.cmd_reject_amendment(task_id, "files extend beyond the linked AC")
        assert rc == 0

        # Verify the tmp repo is clean (no staged/unstaged/untracked).
        result = sp.run(
            ["git", "-C", str(repo), "status", "--porcelain=v1"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "", f"repo not clean:\n{result.stdout}"
        # And src/parser.py matches the original committed content.
        assert (repo / "src" / "parser.py").read_text() == "original\n"
        assert not (repo / "src" / "new_util.py").exists()

        # Reject step also blocked the task and archived the request.
        wu_content = (workspace / "backlog" / f"{task_id}.md").read_text(encoding="utf-8")
        assert "## Status: blocked" in wu_content
        assert AMENDMENT_REJECTED_ACTION in wu_content
        archive_dir = workspace / ".devbench" / "rejected-requests"
        assert any(archive_dir.glob(f"{task_id}-*.json"))

        assert repo.is_dir()


def _make_request(task_id: str, paths: list[str]) -> Any:
    """Build an AmendmentRequest whose files_to_add lists the given paths."""
    from devbench.backlog.amendment import AmendmentRequest

    return AmendmentRequest.from_dict(
        {
            "task_id": task_id,
            "requested_at": "2026-04-18T00:00:00+00:00",
            "reason": "tdd_green_production_fix",
            "justification": "sample",
            "files_to_add": [{"path": p, "change": "add"} for p in paths],
            "linked_acs": ["AC-TEST-001"],
        }
    )


class TestAmendmentLifecycleDuplicateRequest:
    def test_second_request_for_same_task_rejected(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        workspace = _build_workspace(tmp_path)
        task_id = "EX-F1-S1-T1"
        repo = _build_target_repo(tmp_path, staged=("src/example/example.py",))

        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_valid_payload())))
        with _amendment_cli_context(workspace, repo):
            assert cli.cmd_request_amendment(task_id) == 0

        # Second call with identical payload must be refused.
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_valid_payload())))
        with _amendment_cli_context(workspace, repo):
            rc = cli.cmd_request_amendment(task_id)
        assert rc == 1


class TestAmendmentLifecycleRollback:
    def test_post_check_failure_rolls_work_unit_file_back(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = _build_workspace(tmp_path)
        task_id = "EX-F1-S1-T1"

        # Create a pending request whose justification introduces an em-dash.
        # Layer 3 post-check catches this and must roll back the write.
        bad_payload = _valid_payload()
        bad_payload["justification"] = "fix needed\u2014see AC-TEST-001"
        rp = request_path(workspace, task_id)
        rp.parent.mkdir(parents=True)
        rp.write_text(json.dumps({"task_id": task_id, "requested_at": "2026-04-18T00:00:00+00:00", **bad_payload}))

        wu_file = workspace / "backlog" / f"{task_id}.md"
        before = wu_file.read_text(encoding="utf-8")

        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
        ):
            rc = cli.cmd_apply_amendment(task_id)
        assert rc == 1

        after = wu_file.read_text(encoding="utf-8")
        assert after == before, "Layer 3 post-check must roll back the work-unit file on em-dash introduction"


class TestAmendmentLifecyclePreFilterDisabled:
    def test_prefilter_rejects_when_feature_disabled(self, tmp_path: Path) -> None:
        """PreFilter.check_enabled rejects when config.enabled is False."""
        from devbench.backlog.amendment import PreFilter
        from devbench.config_loader import AmendmentConfig

        workspace = _build_workspace(tmp_path)
        task_id = "EX-F1-S1-T1"
        backlog_index = workspace / "BACKLOG.md"

        # Construct a request (from_dict, since AmendmentRequest is frozen)
        req = AmendmentRequest.from_dict(
            {"task_id": task_id, "requested_at": "2026-04-18T00:00:00+00:00", **_valid_payload()}
        )

        disabled = AmendmentConfig(enabled=False)
        pf = PreFilter(backlog_index, disabled)
        with pytest.raises(Exception, match="disabled"):
            pf.run_all(req)


class TestAmendmentLifecycleRateLimitAtOrchestration:
    def test_write_request_already_exists_blocks_second_request(self, tmp_path: Path) -> None:
        """The single-pending-request rule doubles as a first-line rate limit:
        no second amendment can be filed until the first is resolved."""
        workspace = _build_workspace(tmp_path)
        task_id = "EX-F1-S1-T1"

        req = AmendmentRequest.from_dict(
            {"task_id": task_id, "requested_at": "2026-04-18T00:00:00+00:00", **_valid_payload()}
        )
        write_request(workspace, req)

        with pytest.raises(Exception, match="already exists"):
            write_request(workspace, req)


class TestAmendmentLifecycleRowRemoval:
    """End-to-end row removal, with the no-diff safety property proven against real git.

    ``AC-FINAL-015`` requires the Changes Manifest to match the files git
    changed exactly, so a declared row whose file has a zero-line diff is a real
    violation the ``changes_manifest`` judge fails a unit for. Before
    ``files_to_remove`` existed the judge's prescribed remedy was
    unimplementable: an amendment could only add.
    """

    def _removal_payload(self, path: str = "tests/test_example.py") -> dict[str, Any]:
        return {
            "reason": "tdd_green_production_fix",
            "justification": "The declared row has a zero-line diff; its work landed elsewhere.",
            "files_to_add": [],
            "files_to_remove": [path],
            "linked_acs": ["AC-TEST-001"],
        }

    def _two_row_workspace(self, tmp_path: Path) -> Path:
        workspace = _build_workspace(tmp_path)
        wu = workspace / "backlog" / "EX-F1-S1-T1.md"
        wu.write_text(
            wu.read_text(encoding="utf-8").replace(
                "| `tests/test_example.py` | new unit tests |",
                "| `tests/test_example.py` | new unit tests |\n| `tests/test_stale.py` | modify |",
            ),
            encoding="utf-8",
        )
        return workspace

    def test_clean_row_is_removed_end_to_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        workspace = self._two_row_workspace(tmp_path)
        task_id = "EX-F1-S1-T1"
        repo = _build_target_repo(tmp_path)

        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(self._removal_payload("tests/test_stale.py"))))
        with _amendment_cli_context(workspace, repo):
            assert cli.cmd_request_amendment(task_id) == 0, capsys.readouterr().err

        with (
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_INDEX", workspace / "BACKLOG.md"),
        ):
            assert cli.cmd_apply_amendment(task_id) == 0, capsys.readouterr().err

        wu_content = (workspace / "backlog" / f"{task_id}.md").read_text(encoding="utf-8")
        assert [r.file for r in parse_manifest(wu_content)] == ["tests/test_example.py"]
        assert AMENDMENT_APPLIED_ACTION in wu_content

    def test_staged_change_blocks_removal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Arm 1 of the safety property: git diff --cached."""
        workspace = self._two_row_workspace(tmp_path)
        repo = _build_target_repo(tmp_path, staged=("tests/test_stale.py",))

        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(self._removal_payload("tests/test_stale.py"))))
        with _amendment_cli_context(workspace, repo):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1")

        assert rc == 1
        assert "tests/test_stale.py" in capsys.readouterr().err
        assert not request_path(workspace, "EX-F1-S1-T1").exists()

    def test_unstaged_tracked_change_blocks_removal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Arm 2: a worktree-only edit is real work that staged-only checks miss."""
        workspace = self._two_row_workspace(tmp_path)
        repo = _build_target_repo(tmp_path, staged=("tests/test_stale.py",))
        subprocess.run(["git", "commit", "-m", "land it"], cwd=repo, check=True, capture_output=True)
        (repo / "tests/test_stale.py").write_text("edited but not staged\n", encoding="utf-8")

        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(self._removal_payload("tests/test_stale.py"))))
        with _amendment_cli_context(workspace, repo):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1")

        assert rc == 1
        assert "tests/test_stale.py" in capsys.readouterr().err

    def test_untracked_file_blocks_removal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Arm 3: an untracked declared file is uncommitted work, not absence of work."""
        workspace = self._two_row_workspace(tmp_path)
        repo = _build_target_repo(tmp_path)
        (repo / "tests").mkdir(parents=True, exist_ok=True)
        (repo / "tests/test_stale.py").write_text("brand new, never added\n", encoding="utf-8")

        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(self._removal_payload("tests/test_stale.py"))))
        with _amendment_cli_context(workspace, repo):
            rc = cli.cmd_request_amendment("EX-F1-S1-T1")

        assert rc == 1
        assert "tests/test_stale.py" in capsys.readouterr().err
