"""Cross-unit-defect escalation -> auto-decomposed fix proposal.

When a unit blocks with NEEDS_ESCALATION and its live-AC failure is attributed
to files OUTSIDE its own Changes Manifest, ``build_escalation_proposal`` builds
a ``Proposal`` with one ``proposed_tasks`` entry per out-of-scope file (each with
a concrete manifest + a corrective AC), so the standard proposal pipeline can
materialise + dep-wire the fix. When no attributed file is out-of-scope it
returns ``None`` so the caller emits the deterministic ``[ESCALATION_NO_PROPOSAL]``
marker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.backlog.proposal import (
    _SUGGESTED_APPROACH_MIN_CHARS,
    ESCALATION_NO_PROPOSAL_MARKER,
    ESCALATION_PROPOSAL_WRITTEN_MARKER,
    Proposal,
    ProposedTask,
    build_escalation_proposal,
    materialise_proposal,
    write_proposal,
)

SOURCE_ID = "E0-F1-S1-T1"
MANIFEST_FILES = ["scripts/run_terratest.py", "scripts/run_terratest_test.py"]
OUT_OF_SCOPE = ["providers/aws/kms-key/main.tf", "scripts/terratest_sweep.py"]


def _build(
    *,
    attributed_files: list[str],
    manifest_files: list[str] | None = None,
    suggested_ids: list[str] | None = None,
) -> Proposal | None:
    return build_escalation_proposal(
        source_task_id=SOURCE_ID,
        attributed_files=attributed_files,
        manifest_files=manifest_files if manifest_files is not None else MANIFEST_FILES,
        suggested_ids=suggested_ids if suggested_ids is not None else ["E0-F1-S1-T2", "E0-F1-S1-T3"],
        generated_at="2026-06-14T00:00:00Z",
        rejection_reason="AC-2 live test fails due to defects in two already-done units' files",
    )


class TestBuildEscalationProposal:
    def test_one_proposed_task_per_out_of_scope_file(self) -> None:
        proposal = _build(attributed_files=OUT_OF_SCOPE)
        assert proposal is not None
        assert proposal.source_task_id == SOURCE_ID
        assert len(proposal.proposed_tasks) == 2
        owned = {f for t in proposal.proposed_tasks for f in t.files_to_own}
        assert owned == set(OUT_OF_SCOPE)

    def test_each_task_has_concrete_manifest_and_corrective_ac(self) -> None:
        proposal = _build(attributed_files=OUT_OF_SCOPE)
        assert proposal is not None
        for task in proposal.proposed_tasks:
            assert len(task.files_to_own) == 1
            assert task.suggested_acs, "each fix task must carry at least one corrective AC"
            assert len(task.suggested_approach.strip()) >= _SUGGESTED_APPROACH_MIN_CHARS

    def test_in_scope_files_excluded(self) -> None:
        proposal = _build(attributed_files=[*OUT_OF_SCOPE, "scripts/run_terratest.py"])
        assert proposal is not None
        owned = {f for t in proposal.proposed_tasks for f in t.files_to_own}
        assert owned == set(OUT_OF_SCOPE)
        assert "scripts/run_terratest.py" not in owned

    def test_no_out_of_scope_returns_none(self) -> None:
        assert _build(attributed_files=["scripts/run_terratest.py"]) is None

    def test_empty_attribution_returns_none(self) -> None:
        assert _build(attributed_files=[]) is None

    def test_allocated_ids_used_in_order(self) -> None:
        proposal = _build(
            attributed_files=OUT_OF_SCOPE,
            suggested_ids=["E0-F1-S1-T7", "E0-F1-S1-T8"],
        )
        assert proposal is not None
        assert [t.suggested_id for t in proposal.proposed_tasks] == ["E0-F1-S1-T7", "E0-F1-S1-T8"]

    def test_too_few_ids_raises(self) -> None:
        with pytest.raises(ValueError, match="suggested_ids"):
            _build(attributed_files=OUT_OF_SCOPE, suggested_ids=["E0-F1-S1-T2"])

    def test_corrective_ac_references_failing_rerun(self) -> None:
        proposal = _build(attributed_files=OUT_OF_SCOPE)
        assert proposal is not None
        joined = " ".join(ac for t in proposal.proposed_tasks for ac in t.suggested_acs)
        assert SOURCE_ID in joined


class TestEscalationProposalRoundTripsAndMaterialises:
    """The built proposal must survive to_dict/from_dict and materialise cleanly."""

    def _build_ws(self, tmp_path: Path) -> Path:
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text(
            "# Backlog\n\n## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n"
            "| E0 | Example | 0 | 0 | 0 | 1 |\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source | Task | blocked | None | caylent-solutions/example | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n",
            encoding="utf-8",
        )
        story = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        story.mkdir(parents=True)
        (story / "E0-F1-S1-T1.md").write_text(
            "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n"
            "## Target Repository\n\n- **Repo:** `caylent-solutions/example`\n\n"
            "## Description\n\nx\n\n## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n"
            "| none | | |\n\n## Acceptance Criteria\n\n- [ ] AC-1 something\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n"
            "| `scripts/run_terratest.py` | modify |\n\n## Definition of Done\n\n- [ ] done\n",
            encoding="utf-8",
        )
        return tmp_path

    def test_round_trip_through_dict(self) -> None:
        proposal = _build(attributed_files=OUT_OF_SCOPE)
        assert proposal is not None
        rebuilt = Proposal.from_dict(proposal.to_dict())
        assert rebuilt.source_task_id == proposal.source_task_id
        assert [t.suggested_id for t in rebuilt.proposed_tasks] == [t.suggested_id for t in proposal.proposed_tasks]

    def test_write_and_materialise(self, tmp_path: Path) -> None:
        ws = self._build_ws(tmp_path)
        proposal = _build(attributed_files=OUT_OF_SCOPE)
        assert proposal is not None
        path = write_proposal(ws, proposal)
        assert path == ws / ".devbench" / "proposals" / f"{SOURCE_ID}.json"
        drafts = materialise_proposal(
            workspace_root=ws,
            backlog_root=ws / "backlog",
            backlog_index=ws / "BACKLOG.md",
            proposal=proposal,
            repo="caylent-solutions/example",
        )
        assert len(drafts) == 2
        for draft in drafts:
            assert draft.is_file()


class TestEscalationMarkers:
    def test_markers_are_distinct(self) -> None:
        assert ESCALATION_PROPOSAL_WRITTEN_MARKER != ESCALATION_NO_PROPOSAL_MARKER
        assert ESCALATION_PROPOSAL_WRITTEN_MARKER.startswith("[ESCALATION")
        assert ESCALATION_NO_PROPOSAL_MARKER.startswith("[ESCALATION")


class TestCmdEscalateProposal:
    """CLI dispatch: ``escalate-proposal`` reads stdin, writes proposal, marks unit."""

    def _build_ws(self, tmp_path: Path) -> Path:
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text(
            "# Backlog\n\n## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n"
            "| E0 | Example | 0 | 0 | 0 | 1 |\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source | Task | blocked | None | caylent-solutions/example | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n",
            encoding="utf-8",
        )
        story = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        story.mkdir(parents=True)
        (story / "E0-F1-S1-T1.md").write_text(
            "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n"
            "## Target Repository\n\n- **Repo:** `caylent-solutions/example`\n\n"
            "## Description\n\nx\n\n## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n"
            "| none | | |\n\n## Acceptance Criteria\n\n- [ ] AC-1 something\n\n"
            "## Changes Manifest\n\n| File | Change |\n|------|--------|\n"
            "| `scripts/run_terratest.py` | modify |\n\n## Definition of Done\n\n- [ ] done\n\n## Comments\n",
            encoding="utf-8",
        )
        return tmp_path

    def _run(self, ws: Path, payload: dict, monkeypatch, capsys):  # type: ignore[no-untyped-def]
        import io

        from devbench import cli

        monkeypatch.setattr(cli, "WORKSPACE_ROOT", ws)
        monkeypatch.setattr(cli, "BACKLOG_ROOT", ws / "backlog")
        monkeypatch.setattr(cli, "BACKLOG_INDEX", ws / "BACKLOG.md")
        monkeypatch.setattr("sys.stdin", io.StringIO(__import__("json").dumps(payload)))
        rc = cli.cmd_escalate_proposal("E0-F1-S1-T1")
        return rc, capsys.readouterr()

    def test_out_of_scope_writes_proposal_and_marker(self, tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        ws = self._build_ws(tmp_path)
        rc, captured = self._run(
            ws,
            {"attributed_files": OUT_OF_SCOPE},
            monkeypatch,
            capsys,
        )
        assert rc == 0, captured.err
        assert (ws / ".devbench" / "proposals" / "E0-F1-S1-T1.json").is_file()
        wu = (ws / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md").read_text(encoding="utf-8")
        assert ESCALATION_PROPOSAL_WRITTEN_MARKER in wu

    def test_in_scope_only_emits_no_proposal_marker(self, tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        ws = self._build_ws(tmp_path)
        rc, captured = self._run(
            ws,
            {"attributed_files": ["scripts/run_terratest.py"]},
            monkeypatch,
            capsys,
        )
        assert rc == 0, captured.err
        assert not (ws / ".devbench" / "proposals" / "E0-F1-S1-T1.json").exists()
        wu = (ws / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md").read_text(encoding="utf-8")
        assert ESCALATION_NO_PROPOSAL_MARKER in wu

    def test_bad_stdin_returns_1(self, tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        import io

        from devbench import cli

        ws = self._build_ws(tmp_path)
        monkeypatch.setattr(cli, "WORKSPACE_ROOT", ws)
        monkeypatch.setattr(cli, "BACKLOG_ROOT", ws / "backlog")
        monkeypatch.setattr(cli, "BACKLOG_INDEX", ws / "BACKLOG.md")
        monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
        rc = cli.cmd_escalate_proposal("E0-F1-S1-T1")
        assert rc == 1
        assert "not valid JSON" in capsys.readouterr().err

    def test_unknown_unit_returns_1(self, tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        ws = self._build_ws(tmp_path)
        import io

        from devbench import cli

        monkeypatch.setattr(cli, "WORKSPACE_ROOT", ws)
        monkeypatch.setattr(cli, "BACKLOG_ROOT", ws / "backlog")
        monkeypatch.setattr(cli, "BACKLOG_INDEX", ws / "BACKLOG.md")
        monkeypatch.setattr("sys.stdin", io.StringIO('{"attributed_files": ["x/y.tf"]}'))
        rc = cli.cmd_escalate_proposal("E0-F1-S1-T9")
        assert rc == 1
        assert "not found" in capsys.readouterr().err

    def test_duplicate_proposal_returns_1(self, tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        ws = self._build_ws(tmp_path)
        proposals = ws / ".devbench" / "proposals"
        proposals.mkdir(parents=True)
        (proposals / "E0-F1-S1-T1.json").write_text("{}", encoding="utf-8")
        rc, captured = self._run(ws, {"attributed_files": OUT_OF_SCOPE}, monkeypatch, capsys)
        assert rc == 1
        assert "already exists" in captured.err

    def test_non_list_attributed_files_returns_1(self, tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        ws = self._build_ws(tmp_path)
        rc, captured = self._run(ws, {"attributed_files": "x/y.tf"}, monkeypatch, capsys)
        assert rc == 1
        assert "must be a list" in captured.err

    def test_non_string_entry_returns_1(self, tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        ws = self._build_ws(tmp_path)
        rc, captured = self._run(ws, {"attributed_files": [123]}, monkeypatch, capsys)
        assert rc == 1
        assert "must be strings" in captured.err

    def test_empty_stdin_returns_1(self, tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        import io

        from devbench import cli

        ws = self._build_ws(tmp_path)
        monkeypatch.setattr(cli, "WORKSPACE_ROOT", ws)
        monkeypatch.setattr(cli, "BACKLOG_ROOT", ws / "backlog")
        monkeypatch.setattr(cli, "BACKLOG_INDEX", ws / "BACKLOG.md")
        monkeypatch.setattr("sys.stdin", io.StringIO("   "))
        rc = cli.cmd_escalate_proposal("E0-F1-S1-T1")
        assert rc == 1
        assert "required on stdin" in capsys.readouterr().err

    def test_non_object_stdin_returns_1(self, tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        import io

        from devbench import cli

        ws = self._build_ws(tmp_path)
        monkeypatch.setattr(cli, "WORKSPACE_ROOT", ws)
        monkeypatch.setattr(cli, "BACKLOG_ROOT", ws / "backlog")
        monkeypatch.setattr(cli, "BACKLOG_INDEX", ws / "BACKLOG.md")
        monkeypatch.setattr("sys.stdin", io.StringIO("[1, 2]"))
        rc = cli.cmd_escalate_proposal("E0-F1-S1-T1")
        assert rc == 1
        assert "must be a JSON object" in capsys.readouterr().err

    def test_malformed_manifest_returns_1(self, tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        ws = self._build_ws(tmp_path)
        wu = ws / "backlog" / "E0" / "E0-F1" / "E0-F1-S1" / "E0-F1-S1-T1.md"
        wu.write_text(
            "# E0-F1-S1-T1: Source\n\n## Status: blocked\n\n## Description\n\nno manifest section here\n",
            encoding="utf-8",
        )
        rc, captured = self._run(ws, {"attributed_files": OUT_OF_SCOPE}, monkeypatch, capsys)
        assert rc == 1
        assert "Changes Manifest" in captured.err

    def test_build_error_surfaced(self, tmp_path, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        ws = self._build_ws(tmp_path)
        monkeypatch.setattr(cli, "allocate_next_ids", lambda *a, **k: [])
        rc, captured = self._run(ws, {"attributed_files": OUT_OF_SCOPE}, monkeypatch, capsys)
        assert rc == 1
        assert "cannot build escalation proposal" in captured.err


class TestResolveEscalationContext:
    """Direct coverage of the escalation context resolver's failure branches."""

    def test_missing_index_raises(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        monkeypatch.setattr(cli, "BACKLOG_ROOT", tmp_path / "backlog")
        monkeypatch.setattr(cli, "BACKLOG_INDEX", tmp_path / "MISSING.md")
        with pytest.raises(cli._ProposalInputError, match="cannot read backlog index"):
            cli._resolve_escalation_context("E0-F1-S1-T1")

    def test_unknown_unit_raises(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n"
            "| E0 | Example | 0 | 0 | 1 | 0 |\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Other | Task | in-queue | None | caylent-solutions/example | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n",
            encoding="utf-8",
        )
        story = tmp_path / "backlog" / "E0" / "E0-F1" / "E0-F1-S1"
        story.mkdir(parents=True)
        (story / "E0-F1-S1-T1.md").write_text(
            "# E0-F1-S1-T1: Other\n\n## Status: in-queue\n\n## Description\n\nx\n\n"
            "## Dependencies\n\n| ID | Title | Status |\n|----|-------|--------|\n| none | | |\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-1 x\n\n## Changes Manifest\n\n| File | Change |\n"
            "|------|--------|\n| `a.py` | x |\n\n## Definition of Done\n\n- [ ] done\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(cli, "BACKLOG_ROOT", tmp_path / "backlog")
        monkeypatch.setattr(cli, "BACKLOG_INDEX", tmp_path / "BACKLOG.md")
        with pytest.raises(cli._ProposalInputError, match="not found in backlog"):
            cli._resolve_escalation_context("E0-F1-S1-T9")

    def test_missing_file_raises(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        (tmp_path / "BACKLOG.md").write_text(
            "# Backlog\n\n## Status Summary\n\n"
            "| Epic | Title | Done | In Progress | In Queue | Blocked |\n"
            "|------|-------|------|-------------|----------|---------|\n"
            "| E0 | Example | 0 | 0 | 0 | 1 |\n\n"
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|----|-------|------|--------|--------------|------|-----------|\n"
            "| E0-F1-S1-T1 | Source | Task | blocked | None | caylent-solutions/example | "
            "`backlog/E0/E0-F1/E0-F1-S1/E0-F1-S1-T1.md` |\n",
            encoding="utf-8",
        )
        (tmp_path / "backlog").mkdir()
        monkeypatch.setattr(cli, "BACKLOG_ROOT", tmp_path / "backlog")
        monkeypatch.setattr(cli, "BACKLOG_INDEX", tmp_path / "BACKLOG.md")
        with pytest.raises(cli._ProposalInputError):
            cli._resolve_escalation_context("E0-F1-S1-T1")

    def test_unresolvable_file_raises(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from types import SimpleNamespace

        from devbench import cli

        unit = SimpleNamespace(id="E0-F1-S1-T1", repo="caylent-solutions/example")
        monkeypatch.setattr(cli, "_find_unit", lambda _units, _uid: unit)
        monkeypatch.setattr(cli, "_resolve_unit_file", lambda _unit: None)
        monkeypatch.setattr(cli, "BacklogParser", lambda **kwargs: SimpleNamespace(parse_index=lambda: [unit]))
        with pytest.raises(cli._ProposalInputError, match="work-unit file not found"):
            cli._resolve_escalation_context("E0-F1-S1-T1")


class TestWriteEscalationProposalDirect:
    """Direct coverage of ``_write_escalation_proposal`` failure branches."""

    def test_build_value_error_surfaced(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        monkeypatch.setattr(cli, "WORKSPACE_ROOT", tmp_path)
        monkeypatch.setattr(cli, "BACKLOG_ROOT", tmp_path / "backlog")
        monkeypatch.setattr(cli, "allocate_next_ids", lambda *a, **k: [])
        with pytest.raises(cli._ProposalInputError, match="cannot build escalation proposal"):
            cli._write_escalation_proposal(
                source_task_id="E0-F1-S1-T1",
                attributed_files=OUT_OF_SCOPE,
                manifest_files=["scripts/run_terratest.py"],
                out_of_scope=OUT_OF_SCOPE,
            )

    def test_duplicate_write_surfaced(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from devbench import cli

        monkeypatch.setattr(cli, "WORKSPACE_ROOT", tmp_path)
        monkeypatch.setattr(cli, "BACKLOG_ROOT", tmp_path / "backlog")
        monkeypatch.setattr(cli, "allocate_next_ids", lambda *a, **k: ["E0-F1-S1-T2", "E0-F1-S1-T3"])
        proposals = tmp_path / ".devbench" / "proposals"
        proposals.mkdir(parents=True)
        (proposals / "E0-F1-S1-T1.json").write_text("{}", encoding="utf-8")
        with pytest.raises(cli._ProposalInputError, match="already exists"):
            cli._write_escalation_proposal(
                source_task_id="E0-F1-S1-T1",
                attributed_files=OUT_OF_SCOPE,
                manifest_files=["scripts/run_terratest.py"],
                out_of_scope=OUT_OF_SCOPE,
            )


_ = ProposedTask
