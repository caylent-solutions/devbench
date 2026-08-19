"""`add_dep` must refuse an edge that would create a dependency cycle.

`add_dep`'s fail-fast list checked that both tasks exist and that the blocker
is non-terminal, but never that the new edge keeps the graph acyclic. Two
callers exercised that gap in the same run:

- the task factory, which wires each promoted child as a dependency of the
  parent that proposed it -- a cycle when the child transitively depends on
  that parent;
- an operator following the Manifest Conflict Rule remedy, which prints an
  `add-dep` command per conflicting pair without knowing the reverse edge
  already exists.

Both produced `dependency cycle detected` from validate-backlog long after
the fact, with nothing in the work-unit file naming the edge that caused it.
Refusing at write time names the offending chain while the caller still has
the context to choose the other direction.
"""

from pathlib import Path

import pytest

from devbench.backlog.proposal import ProposalError, add_dep


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


@pytest.mark.unit
class TestAddDepCycleGuard:
    def test_direct_reverse_edge_is_refused(self, tmp_path: Path, monkeypatch) -> None:
        """A already depends on B; wiring B -> A closes a two-node cycle."""
        root, index = _backlog(
            tmp_path,
            [("E1-F1-S1-T1", "in-queue"), ("E1-F1-S1-T2", "in-queue")],
            {"E1-F1-S1-T1": "E1-F1-S1-T2"},
        )
        monkeypatch.setattr("devbench.config.WORKSPACE_ROOT", tmp_path)

        with pytest.raises(ProposalError, match="cycle"):
            add_dep(
                backlog_root=root,
                backlog_index=index,
                blocked_task_id="E1-F1-S1-T2",
                blocker_task_id="E1-F1-S1-T1",
            )

    def test_transitive_cycle_is_refused(self, tmp_path: Path, monkeypatch) -> None:
        """T1 -> T2 -> T3; wiring T3 -> T1 closes a three-node cycle."""
        root, index = _backlog(
            tmp_path,
            [("E1-F1-S1-T1", "in-queue"), ("E1-F1-S1-T2", "in-queue"), ("E1-F1-S1-T3", "in-queue")],
            {"E1-F1-S1-T1": "E1-F1-S1-T2", "E1-F1-S1-T2": "E1-F1-S1-T3"},
        )
        monkeypatch.setattr("devbench.config.WORKSPACE_ROOT", tmp_path)

        with pytest.raises(ProposalError, match="cycle"):
            add_dep(
                backlog_root=root,
                backlog_index=index,
                blocked_task_id="E1-F1-S1-T3",
                blocker_task_id="E1-F1-S1-T1",
            )

    def test_acyclic_edge_is_still_wired(self, tmp_path: Path, monkeypatch) -> None:
        """The guard must not reject a legitimate edge."""
        root, index = _backlog(
            tmp_path,
            [("E1-F1-S1-T1", "in-queue"), ("E1-F1-S1-T2", "in-queue")],
            {},
        )
        monkeypatch.setattr("devbench.config.WORKSPACE_ROOT", tmp_path)

        assert (
            add_dep(
                backlog_root=root,
                backlog_index=index,
                blocked_task_id="E1-F1-S1-T1",
                blocker_task_id="E1-F1-S1-T2",
            )
            is True
        )
