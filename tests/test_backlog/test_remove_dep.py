"""`remove_dep` deletes a dependency edge that `add_dep` can only ever add.

`add_dep` is additive-only, so an edge wired in the wrong direction could not
be corrected: re-wiring the reverse fails the cycle guard while the erroneous
row is still present, and the documented remedy was an operator hand-edit of
the work-unit file. That remedy is unreachable from inside a run --
`guard-work-unit-write.sh` blocks executor-tier writes to `backlog/**/*.md` --
so a backlog carrying one reversed edge stalled every unit behind it with no
automation path. Observed live: two independent work units blocked on exactly
this, with 47 further units waiting behind them.

Removal is deliberately narrower than addition. It touches the row, the index
cell and the pending-proposal marker, and it does NOT touch status: deciding a
unit is now claimable is the cascade's job, and doing it here would re-queue a
unit that is blocked for some other reason too.
"""

from pathlib import Path

import pytest

from devbench.backlog.proposal import ProposalError, add_dep, remove_dep


def _backlog(tmp_path: Path, rows: list[tuple[str, str]], deps: dict[str, str]) -> tuple[Path, Path]:
    """Write BACKLOG.md + one file per (id, status), with `deps` as declared edges."""
    root = tmp_path / "backlog"
    root.mkdir(parents=True, exist_ok=True)
    index_rows = "\n".join(
        f"| {uid} | Title {uid} | Task | {status} | {deps.get(uid, 'None')} | org/repo | `backlog/{uid}.md` |"
        for uid, status in rows
    )
    index = tmp_path / "BACKLOG.md"
    index.write_text(
        "# Backlog\n\n## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|----|-------|------|--------|--------------|------|----------|\n"
        f"{index_rows}\n",
        encoding="utf-8",
    )
    for uid, status in rows:
        dep_row = f"| {deps[uid]} | Title | in-queue |\n" if uid in deps else "| none | | |\n"
        (root / f"{uid}.md").write_text(
            f"# {uid}: Title {uid}\n\n## Status: {status}\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n"
            f"{dep_row}\n## Comments\n",
            encoding="utf-8",
        )
    return root, index


def _dep_section(path: Path) -> str:
    body = path.read_text(encoding="utf-8")
    start = body.index("## Dependencies")
    end = body.find("\n## ", start + 1)
    return body[start : end if end != -1 else len(body)]


def _index_dep_cell(index: Path, task_id: str) -> str:
    for line in index.read_text(encoding="utf-8").splitlines():
        cells = line.split("|")
        if len(cells) >= 6 and cells[1].strip() == task_id:
            return cells[5].strip()
    raise AssertionError(f"no index row for {task_id}")


@pytest.mark.unit
class TestRemoveDep:
    def test_declared_row_is_removed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root, index = _backlog(
            tmp_path,
            [("E1-F1-S1-T1", "blocked"), ("E1-F1-S1-T2", "in-queue")],
            {"E1-F1-S1-T1": "E1-F1-S1-T2"},
        )
        monkeypatch.setattr("devbench.config.WORKSPACE_ROOT", tmp_path)

        removed = remove_dep(
            backlog_root=root,
            backlog_index=index,
            blocked_task_id="E1-F1-S1-T1",
            blocker_task_id="E1-F1-S1-T2",
        )

        assert removed is True
        assert "E1-F1-S1-T2" not in _dep_section(root / "E1-F1-S1-T1.md")

    def test_emptied_table_falls_back_to_the_none_row(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A Dependencies table with no rows at all is not a valid table."""
        root, index = _backlog(
            tmp_path,
            [("E1-F1-S1-T1", "blocked"), ("E1-F1-S1-T2", "in-queue")],
            {"E1-F1-S1-T1": "E1-F1-S1-T2"},
        )
        monkeypatch.setattr("devbench.config.WORKSPACE_ROOT", tmp_path)

        remove_dep(
            backlog_root=root,
            backlog_index=index,
            blocked_task_id="E1-F1-S1-T1",
            blocker_task_id="E1-F1-S1-T2",
        )

        assert "| none | | |" in _dep_section(root / "E1-F1-S1-T1.md")

    def test_index_dependency_cell_is_synced(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The index must never keep an edge the work-unit file has dropped."""
        root, index = _backlog(
            tmp_path,
            [("E1-F1-S1-T1", "blocked"), ("E1-F1-S1-T2", "in-queue")],
            {"E1-F1-S1-T1": "E1-F1-S1-T2"},
        )
        monkeypatch.setattr("devbench.config.WORKSPACE_ROOT", tmp_path)

        remove_dep(
            backlog_root=root,
            backlog_index=index,
            blocked_task_id="E1-F1-S1-T1",
            blocker_task_id="E1-F1-S1-T2",
        )

        assert _index_dep_cell(index, "E1-F1-S1-T1").lower() in ("none", "")

    def test_sibling_dependencies_survive(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Removing one edge must not disturb the others."""
        root, index = _backlog(
            tmp_path,
            [("E1-F1-S1-T1", "blocked"), ("E1-F1-S1-T2", "in-queue"), ("E1-F1-S1-T3", "in-queue")],
            {"E1-F1-S1-T1": "E1-F1-S1-T2"},
        )
        monkeypatch.setattr("devbench.config.WORKSPACE_ROOT", tmp_path)
        add_dep(
            backlog_root=root,
            backlog_index=index,
            blocked_task_id="E1-F1-S1-T1",
            blocker_task_id="E1-F1-S1-T3",
        )

        remove_dep(
            backlog_root=root,
            backlog_index=index,
            blocked_task_id="E1-F1-S1-T1",
            blocker_task_id="E1-F1-S1-T2",
        )

        section = _dep_section(root / "E1-F1-S1-T1.md")
        assert "E1-F1-S1-T2" not in section
        assert "E1-F1-S1-T3" in section
        assert "E1-F1-S1-T3" in _index_dep_cell(index, "E1-F1-S1-T1")

    def test_pending_proposal_marker_is_cleared(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The marker is a second, independent edge channel; leaving it keeps the block."""
        from devbench.backlog.manager import BacklogManager

        root, index = _backlog(
            tmp_path,
            [("E1-F1-S1-T1", "blocked"), ("E1-F1-S1-T2", "in-queue")],
            {},
        )
        monkeypatch.setattr("devbench.config.WORKSPACE_ROOT", tmp_path)
        add_dep(
            backlog_root=root,
            backlog_index=index,
            blocked_task_id="E1-F1-S1-T1",
            blocker_task_id="E1-F1-S1-T2",
        )
        wu = root / "E1-F1-S1-T1.md"
        assert "E1-F1-S1-T2" in BacklogManager()._extract_pending_proposal_markers(wu)

        remove_dep(
            backlog_root=root,
            backlog_index=index,
            blocked_task_id="E1-F1-S1-T1",
            blocker_task_id="E1-F1-S1-T2",
        )

        assert BacklogManager()._extract_pending_proposal_markers(wu) == set()

    def test_removal_is_audited(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Deleting an edge must leave a trace, since the row itself is gone."""
        root, index = _backlog(
            tmp_path,
            [("E1-F1-S1-T1", "blocked"), ("E1-F1-S1-T2", "in-queue")],
            {"E1-F1-S1-T1": "E1-F1-S1-T2"},
        )
        monkeypatch.setattr("devbench.config.WORKSPACE_ROOT", tmp_path)

        remove_dep(
            backlog_root=root,
            backlog_index=index,
            blocked_task_id="E1-F1-S1-T1",
            blocker_task_id="E1-F1-S1-T2",
            reason="edge was wired in the wrong direction",
        )

        body = (root / "E1-F1-S1-T1.md").read_text(encoding="utf-8")
        assert "[WU_UNWIRED]" in body
        assert "E1-F1-S1-T2" in body
        assert "edge was wired in the wrong direction" in body

    def test_status_is_left_alone(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Deciding a unit is claimable belongs to the cascade, not to this call."""
        root, index = _backlog(
            tmp_path,
            [("E1-F1-S1-T1", "blocked"), ("E1-F1-S1-T2", "in-queue")],
            {"E1-F1-S1-T1": "E1-F1-S1-T2"},
        )
        monkeypatch.setattr("devbench.config.WORKSPACE_ROOT", tmp_path)

        remove_dep(
            backlog_root=root,
            backlog_index=index,
            blocked_task_id="E1-F1-S1-T1",
            blocker_task_id="E1-F1-S1-T2",
        )

        assert "## Status: blocked" in (root / "E1-F1-S1-T1.md").read_text(encoding="utf-8")

    def test_absent_edge_is_a_no_op(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root, index = _backlog(
            tmp_path,
            [("E1-F1-S1-T1", "blocked"), ("E1-F1-S1-T2", "in-queue")],
            {},
        )
        monkeypatch.setattr("devbench.config.WORKSPACE_ROOT", tmp_path)

        assert (
            remove_dep(
                backlog_root=root,
                backlog_index=index,
                blocked_task_id="E1-F1-S1-T1",
                blocker_task_id="E1-F1-S1-T2",
            )
            is False
        )

    def test_repeat_call_is_idempotent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root, index = _backlog(
            tmp_path,
            [("E1-F1-S1-T1", "blocked"), ("E1-F1-S1-T2", "in-queue")],
            {"E1-F1-S1-T1": "E1-F1-S1-T2"},
        )
        monkeypatch.setattr("devbench.config.WORKSPACE_ROOT", tmp_path)

        def _call() -> bool:
            return remove_dep(
                backlog_root=root,
                backlog_index=index,
                blocked_task_id="E1-F1-S1-T1",
                blocker_task_id="E1-F1-S1-T2",
            )

        assert _call() is True
        assert _call() is False

    def test_self_edge_is_refused(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root, index = _backlog(tmp_path, [("E1-F1-S1-T1", "blocked")], {})
        monkeypatch.setattr("devbench.config.WORKSPACE_ROOT", tmp_path)

        with pytest.raises(ProposalError, match="cannot be the same"):
            remove_dep(
                backlog_root=root,
                backlog_index=index,
                blocked_task_id="E1-F1-S1-T1",
                blocker_task_id="E1-F1-S1-T1",
            )

    def test_unknown_blocked_task_is_refused(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root, index = _backlog(tmp_path, [("E1-F1-S1-T1", "blocked")], {})
        monkeypatch.setattr("devbench.config.WORKSPACE_ROOT", tmp_path)

        with pytest.raises(ProposalError, match="not found"):
            remove_dep(
                backlog_root=root,
                backlog_index=index,
                blocked_task_id="E9-F9-S9-T9",
                blocker_task_id="E1-F1-S1-T1",
            )

    def test_dangling_blocker_can_still_be_removed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The blocker need not exist: a dangling edge is the case most needing removal.

        `validate-backlog` flags a marker pointing at an unknown ID precisely so
        an operator can clear it, so requiring the target to exist would make
        this command unusable for the fault it is meant to repair.
        """
        root, index = _backlog(
            tmp_path,
            [("E1-F1-S1-T1", "blocked")],
            {"E1-F1-S1-T1": "E9-F9-S9-T9"},
        )
        monkeypatch.setattr("devbench.config.WORKSPACE_ROOT", tmp_path)

        removed = remove_dep(
            backlog_root=root,
            backlog_index=index,
            blocked_task_id="E1-F1-S1-T1",
            blocker_task_id="E9-F9-S9-T9",
        )

        assert removed is True
        assert "E9-F9-S9-T9" not in _dep_section(root / "E1-F1-S1-T1.md")

    def test_removal_lets_the_reverse_edge_be_wired(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The whole point: correcting a reversed edge, which add_dep alone cannot do."""
        root, index = _backlog(
            tmp_path,
            [("E1-F1-S1-T1", "blocked"), ("E1-F1-S1-T2", "in-queue")],
            {"E1-F1-S1-T1": "E1-F1-S1-T2"},
        )
        monkeypatch.setattr("devbench.config.WORKSPACE_ROOT", tmp_path)

        # Before removal the reverse edge closes a cycle and is refused.
        with pytest.raises(ProposalError, match="cycle"):
            add_dep(
                backlog_root=root,
                backlog_index=index,
                blocked_task_id="E1-F1-S1-T2",
                blocker_task_id="E1-F1-S1-T1",
            )

        remove_dep(
            backlog_root=root,
            backlog_index=index,
            blocked_task_id="E1-F1-S1-T1",
            blocker_task_id="E1-F1-S1-T2",
        )

        assert (
            add_dep(
                backlog_root=root,
                backlog_index=index,
                blocked_task_id="E1-F1-S1-T2",
                blocker_task_id="E1-F1-S1-T1",
            )
            is True
        )


@pytest.mark.unit
class TestRemoveDepEdgeCases:
    def test_missing_dependencies_section_is_refused(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A file with no Dependencies section cannot be edited blindly."""
        root, index = _backlog(tmp_path, [("E1-F1-S1-T1", "blocked")], {})
        monkeypatch.setattr("devbench.config.WORKSPACE_ROOT", tmp_path)
        wu = root / "E1-F1-S1-T1.md"
        wu.write_text("# E1-F1-S1-T1: T\n\n## Status: blocked\n\n## Comments\n", encoding="utf-8")

        with pytest.raises(ProposalError, match="no '## Dependencies' section"):
            remove_dep(
                backlog_root=root,
                backlog_index=index,
                blocked_task_id="E1-F1-S1-T1",
                blocker_task_id="E1-F1-S1-T2",
            )

    def test_malformed_index_row_is_left_alone(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A truncated index row has no Dependencies cell to edit; skip rather than corrupt it."""
        from devbench.backlog.proposal import _remove_dependency_from_index

        index = tmp_path / "BACKLOG.md"
        index.write_text(
            "# Backlog\n\n## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|----------|\n"
            "| E1-F1-S1-T1 | Title | Task |\n",
            encoding="utf-8",
        )
        before = index.read_text(encoding="utf-8")
        monkeypatch.setattr("devbench.config.WORKSPACE_ROOT", tmp_path)

        _remove_dependency_from_index(index, "E1-F1-S1-T1", "E1-F1-S1-T2")

        assert index.read_text(encoding="utf-8") == before


@pytest.mark.unit
class TestCmdRemoveDep:
    """The CLI wrapper's contract: same JSON keys as add-dep, `unwired` in place of `wired`."""

    @staticmethod
    def _patched(root: Path, index: Path, tmp_path: Path):
        from unittest.mock import patch

        return (
            patch("devbench.cli.BACKLOG_ROOT", root),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
        )

    def test_removes_the_edge_and_reports_unwired(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import json

        from devbench import cli

        root, index = _backlog(
            tmp_path,
            [("E1-F1-S1-T1", "blocked"), ("E1-F1-S1-T2", "in-queue")],
            {"E1-F1-S1-T1": "E1-F1-S1-T2"},
        )
        monkeypatch.setattr("devbench.config.WORKSPACE_ROOT", tmp_path)
        a, b, c = self._patched(root, index, tmp_path)
        with a, b, c:
            rc = cli.cmd_remove_dep("E1-F1-S1-T1", "E1-F1-S1-T2", "--reason", "wrong direction")

        assert rc == 0
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload == {
            "blocked": "E1-F1-S1-T1",
            "blocker": "E1-F1-S1-T2",
            "unwired": True,
            "reason": "wrong direction",
        }

    def test_absent_edge_is_rc_zero_with_unwired_false(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An honest no-op is not a failure."""
        import json

        from devbench import cli

        root, index = _backlog(tmp_path, [("E1-F1-S1-T1", "blocked"), ("E1-F1-S1-T2", "in-queue")], {})
        monkeypatch.setattr("devbench.config.WORKSPACE_ROOT", tmp_path)
        a, b, c = self._patched(root, index, tmp_path)
        with a, b, c:
            rc = cli.cmd_remove_dep("E1-F1-S1-T1", "E1-F1-S1-T2")

        assert rc == 0
        assert json.loads(capsys.readouterr().out.strip())["unwired"] is False

    def test_unknown_blocked_task_exits_non_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import json

        from devbench import cli

        root, index = _backlog(tmp_path, [("E1-F1-S1-T1", "blocked")], {})
        monkeypatch.setattr("devbench.config.WORKSPACE_ROOT", tmp_path)
        a, b, c = self._patched(root, index, tmp_path)
        with a, b, c:
            rc = cli.cmd_remove_dep("E9-F9-S9-T9", "E1-F1-S1-T1")

        assert rc == 1
        payload = json.loads(capsys.readouterr().out.strip())
        assert payload["unwired"] is False
        assert "not found" in payload["reason"]

    def test_bad_id_format_is_rejected(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        from devbench import cli

        assert cli.cmd_remove_dep("not-an-id", "E1-F1-S1-T1") == 1
        assert "remove-dep" in capsys.readouterr().err

    def test_em_dash_in_reason_is_rejected(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Same prohibited-character contract every audit-writing verb enforces."""
        from devbench import cli

        assert cli.cmd_remove_dep("E1-F1-S1-T1", "E1-F1-S1-T2", "--reason", "a — b") == 1


@pytest.mark.unit
class TestOperatorInputTagHelper:
    def test_unreadable_file_does_not_claim_the_unit_is_free(self, tmp_path: Path) -> None:
        """Guessing "unblocked" would re-queue a unit nobody can read."""
        from devbench import cli

        assert cli._has_unresolved_operator_input_tag(tmp_path / "missing.md") is False
