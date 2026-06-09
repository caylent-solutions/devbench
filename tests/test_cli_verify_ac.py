"""Tests for ``devbench verify-ac`` -- the deterministic AC evidence runner (Workstream B).

Exercises a STUB target repo whose verification commands exit 0 and exit 1 to prove:

- the runner captures the REAL tool exit code (never self-reported);
- the evidence ledger JSON is written and loadable by the same helpers the gate uses;
- per-AC artifacts are written and trimmed to the configured byte cap;
- ``judge`` / ``deferred`` items are skipped (no command executed, no record);
- a missing ``## Verification`` section yields an empty ledger and exit 0;
- a malformed directive fails fast (exit 1);
- the attempt counter advances and the latest pointer resolves the newest attempt;
- the deterministic TDD genuine-RED gate is invoked from this Python code path and
  prefers the tool-captured RED exit code.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbench import cli, verification

pytestmark = pytest.mark.unit

_REPO = "caylent-solutions/devbench"


def _make_unit() -> object:
    from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

    return WorkUnit(
        id="E1-F1-S1-T1",
        title="Verify AC runner",
        status=WorkUnitStatus.IN_PROGRESS,
        unit_type=WorkUnitType.TASK,
        file_path=Path("backlog/E1-F1-S1-T1.md"),
        repo=_REPO,
        dependencies=[],
    )


def _write_unit(workspace_root: Path, verification_block: str) -> Path:
    backlog = workspace_root / "backlog"
    backlog.mkdir(parents=True, exist_ok=True)
    section = f"## Verification\n\n{verification_block}\n\n" if verification_block else ""
    wu = backlog / "E1-F1-S1-T1.md"
    wu.write_text(
        "# E1-F1-S1-T1: Verify AC runner\n\n"
        "## Status: in-progress\n\n"
        "## Acceptance Criteria\n\n- [ ] AC-1: works\n\n"
        f"{section}"
        "## Comments\n",
        encoding="utf-8",
    )
    return wu


def _run(workspace_root: Path, repo_path: Path, unit_id: str = "E1-F1-S1-T1") -> int:
    unit = _make_unit()
    parser = MagicMock()
    parser.parse_index.return_value = [unit]
    with (
        patch("devbench.cli.BacklogParser", return_value=parser),
        patch("devbench.cli.BACKLOG_ROOT", workspace_root / "backlog"),
        patch("devbench.cli.WORKSPACE_ROOT", workspace_root),
        patch("devbench.cli.REPO_LOCAL_PATHS", {_REPO: repo_path}),
    ):
        return cli.cmd_verify_ac(unit_id)


@pytest.fixture
def repo_path(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


class TestRealExitCodeCapture:
    """The runner records the subprocess's real exit code, never a self-reported one."""

    def test_passing_command_records_exit_zero(self, tmp_path: Path, repo_path: Path) -> None:
        workspace = tmp_path / "ws"
        _write_unit(workspace, "- VERIFY AC-1 | type=command | cmd=`exit 0` | expect-exit=0")
        rc = _run(workspace, repo_path)
        assert rc == 0
        records = verification.read_latest_evidence_ledger(workspace, "E1-F1-S1-T1")
        assert len(records) == 1
        assert records[0].exit_code == 0
        assert records[0].ac_ids == ["AC-1"]

    def test_failing_command_records_real_nonzero_exit_and_fails(self, tmp_path: Path, repo_path: Path) -> None:
        workspace = tmp_path / "ws"
        # The command genuinely exits 7 -- the runner must capture 7, not 0.
        _write_unit(workspace, "- VERIFY AC-1 | type=command | cmd=`exit 7` | expect-exit=0")
        rc = _run(workspace, repo_path)
        assert rc == 1
        records = verification.read_latest_evidence_ledger(workspace, "E1-F1-S1-T1")
        assert len(records) == 1
        assert records[0].exit_code == 7

    def test_mixed_pass_and_fail_one_attempt(self, tmp_path: Path, repo_path: Path) -> None:
        workspace = tmp_path / "ws"
        _write_unit(
            workspace,
            "- VERIFY AC-1 | type=command | cmd=`exit 0` | expect-exit=0\n"
            "- VERIFY AC-2 | type=command | cmd=`exit 1` | expect-exit=0",
        )
        rc = _run(workspace, repo_path)
        assert rc == 1
        records = verification.read_latest_evidence_ledger(workspace, "E1-F1-S1-T1")
        by_ac = {tuple(r.ac_ids): r.exit_code for r in records}
        assert by_ac[("AC-1",)] == 0
        assert by_ac[("AC-2",)] == 1

    def test_nonzero_expect_exit_is_honoured(self, tmp_path: Path, repo_path: Path) -> None:
        """A command authored to expect a non-zero exit passes when it matches."""
        workspace = tmp_path / "ws"
        _write_unit(workspace, "- VERIFY AC-1 | type=command | cmd=`exit 3` | expect-exit=3")
        rc = _run(workspace, repo_path)
        assert rc == 0
        records = verification.read_latest_evidence_ledger(workspace, "E1-F1-S1-T1")
        assert records[0].exit_code == 3


class TestLedgerAndArtifacts:
    """The ledger JSON is written/loadable and per-AC artifacts are trimmed."""

    def test_ledger_json_written_and_loadable(self, tmp_path: Path, repo_path: Path) -> None:
        workspace = tmp_path / "ws"
        _write_unit(workspace, "- VERIFY AC-1 | type=command | cmd=`echo hi` | expect-exit=0")
        rc = _run(workspace, repo_path)
        assert rc == 0
        attempt = verification.latest_attempt_number(workspace, "E1-F1-S1-T1")
        assert attempt == 1
        ledger = verification.evidence_attempt_dir(workspace, "E1-F1-S1-T1", attempt) / "evidence.json"
        assert ledger.is_file()
        data = json.loads(ledger.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert data[0]["command"] == "echo hi"
        assert data[0]["exit_code"] == 0

    def test_artifact_written_with_command_output(self, tmp_path: Path, repo_path: Path) -> None:
        workspace = tmp_path / "ws"
        _write_unit(workspace, "- VERIFY AC-1 | type=command | cmd=`echo HELLO_FROM_AC` | expect-exit=0")
        rc = _run(workspace, repo_path)
        assert rc == 0
        artifact = verification.evidence_attempt_dir(workspace, "E1-F1-S1-T1", 1) / "AC-1.log"
        assert artifact.is_file()
        assert "HELLO_FROM_AC" in artifact.read_text(encoding="utf-8")

    def test_artifact_trimmed_to_byte_cap(self, tmp_path: Path, repo_path: Path) -> None:
        """A large output is tail-trimmed to the configured byte cap."""
        workspace = tmp_path / "ws"
        # Emit far more than the cap; the artifact keeps only the trailing cap bytes.
        _write_unit(
            workspace,
            "- VERIFY AC-1 | type=command | cmd=`for i in $(seq 1 5000); do echo LINE$i; done` | expect-exit=0",
        )
        with patch("devbench.config.CI_FAILURE_LOG_BYTES", 256):
            rc = _run(workspace, repo_path)
        assert rc == 0
        artifact = verification.evidence_attempt_dir(workspace, "E1-F1-S1-T1", 1) / "AC-1.log"
        text = artifact.read_text(encoding="utf-8")
        assert len(text) <= 256
        # Tail-biased: the LAST line survives, the first does not.
        assert "LINE5000" in text
        assert "LINE1\n" not in text


class TestSkippedItems:
    """judge and deferred items are not executed and produce no evidence record."""

    def test_judge_and_deferred_skipped(self, tmp_path: Path, repo_path: Path) -> None:
        workspace = tmp_path / "ws"
        _write_unit(
            workspace,
            "- VERIFY AC-1 | type=judge\n"
            '- VERIFY AC-2 | type=deferred | owner=operator | reason="prod apply is operator-only"\n'
            "- VERIFY AC-3 | type=command | cmd=`exit 0` | expect-exit=0",
        )
        rc = _run(workspace, repo_path)
        assert rc == 0
        records = verification.read_latest_evidence_ledger(workspace, "E1-F1-S1-T1")
        # Only the executable command produced a record.
        assert [r.ac_ids for r in records] == [["AC-3"]]


class TestExecutableWithoutCommand:
    """An executable directive lacking cmd= is a hard failure, never a silent pass."""

    def test_missing_command_records_failure(self, tmp_path: Path, repo_path: Path) -> None:
        from devbench.constants import SUBPROCESS_ERROR_EXIT_CODE

        workspace = tmp_path / "ws"
        # type=apply with no cmd= -- parses, but cannot prove anything.
        _write_unit(workspace, "- VERIFY AC-1 | type=apply | expect-exit=0")
        rc = _run(workspace, repo_path)
        assert rc == 1
        records = verification.read_latest_evidence_ledger(workspace, "E1-F1-S1-T1")
        assert len(records) == 1
        assert records[0].exit_code == SUBPROCESS_ERROR_EXIT_CODE


class TestNoVerificationSection:
    """Back-compat: a unit with no ## Verification section produces an empty ledger, exit 0."""

    def test_empty_section_exits_zero_with_empty_ledger(self, tmp_path: Path, repo_path: Path) -> None:
        workspace = tmp_path / "ws"
        _write_unit(workspace, "")
        rc = _run(workspace, repo_path)
        assert rc == 0
        records = verification.read_latest_evidence_ledger(workspace, "E1-F1-S1-T1")
        assert records == []


class TestMalformedDirective:
    """A malformed VERIFY directive fails fast with a non-zero exit."""

    def test_unknown_type_exits_one(self, tmp_path: Path, repo_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        workspace = tmp_path / "ws"
        _write_unit(workspace, "- VERIFY AC-1 | type=teleport | cmd=`exit 0`")
        rc = _run(workspace, repo_path)
        assert rc == 1
        assert "malformed" in capsys.readouterr().err


class TestAttemptAdvances:
    """Re-running advances the attempt counter; the latest pointer follows the newest."""

    def test_second_run_writes_attempt_two(self, tmp_path: Path, repo_path: Path) -> None:
        workspace = tmp_path / "ws"
        _write_unit(workspace, "- VERIFY AC-1 | type=command | cmd=`exit 0` | expect-exit=0")
        assert _run(workspace, repo_path) == 0
        assert _run(workspace, repo_path) == 0
        assert verification.latest_attempt_number(workspace, "E1-F1-S1-T1") == 2
        # Both attempt directories exist; the gate loads attempt 2 via the pointer.
        assert verification.evidence_attempt_dir(workspace, "E1-F1-S1-T1", 1).is_dir()
        assert verification.evidence_attempt_dir(workspace, "E1-F1-S1-T1", 2).is_dir()


class TestUnitNotFoundAndRepoResolution:
    """Resolution failure paths exit non-zero with a clear error."""

    def test_unit_not_found(self, tmp_path: Path, repo_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        rc = _run(tmp_path / "ws", repo_path, unit_id="E9-F9-S9-T9")
        assert rc == 1
        assert "not found" in capsys.readouterr().err

    def test_missing_work_unit_file(self, tmp_path: Path, repo_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        # Index returns the unit, but no .md file exists on disk.
        workspace = tmp_path / "ws"
        (workspace / "backlog").mkdir(parents=True)
        rc = _run(workspace, repo_path)
        assert rc == 1
        assert "file not found" in capsys.readouterr().err

    def test_no_repo_path_configured(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        workspace = tmp_path / "ws"
        _write_unit(workspace, "- VERIFY AC-1 | type=command | cmd=`exit 0` | expect-exit=0")
        unit = _make_unit()
        parser = MagicMock()
        parser.parse_index.return_value = [unit]
        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli.BACKLOG_ROOT", workspace / "backlog"),
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.REPO_LOCAL_PATHS", {}),
        ):
            rc = cli.cmd_verify_ac("E1-F1-S1-T1")
        assert rc == 1
        assert "No local path configured" in capsys.readouterr().err


class TestTddGateFromVerifyAc:
    """The deterministic TDD genuine-RED gate is invoked from verify-ac (was dead code)."""

    def _git_repo(self, tmp_path: Path) -> Path:
        import subprocess

        repo = tmp_path / "gitrepo"
        repo.mkdir()
        for args in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "t@e.com"],
            ["git", "config", "user.name", "T"],
        ):
            subprocess.run(args, cwd=repo, check=True, capture_output=True)
        # Initial commit so ``git diff HEAD`` has a base to diff against.
        (repo / "README.md").write_text("# repo\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True, capture_output=True)
        return repo

    def _write_unit_with_red(self, workspace: Path, red_exit: int, verification_block: str) -> Path:
        backlog = workspace / "backlog"
        backlog.mkdir(parents=True, exist_ok=True)
        wu = backlog / "E1-F1-S1-T1.md"
        wu.write_text(
            "# E1-F1-S1-T1: Verify AC runner\n\n"
            "## Status: in-progress\n\n"
            "## Acceptance Criteria\n\n- [ ] AC-1: works\n\n"
            f"## Verification\n\n{verification_block}\n\n"
            "## TDD Cycle Log\n\n"
            f"- [RED] tests/test_x.py -- Command: pytest. Exit: {red_exit}. Failures: 1 failed\n\n"
            "## Comments\n",
            encoding="utf-8",
        )
        return wu

    def test_tdd_gate_rejects_when_red_exit_zero_and_no_prod_change(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A self-reported RED Exit: 0 with an empty diff trips the genuine-RED gate."""
        workspace = tmp_path / "ws"
        repo = self._git_repo(tmp_path)
        self._write_unit_with_red(workspace, 0, "- VERIFY AC-1 | type=command | cmd=`exit 0` | expect-exit=0")
        rc = _run(workspace, repo)
        assert rc == 1
        assert "TDD genuine-RED gate" in capsys.readouterr().err

    def test_tool_captured_red_exit_overrides_self_reported(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A tool=red record with a non-zero exit overrides a self-reported Exit: 0.

        The work unit self-reports ``Exit: 0`` (which would trip the gate), but the
        verify-ac run captures a genuine RED exit of 1 via a ``tool=red`` directive,
        so the gate must NOT reject on the exit-code rule. A production file change
        also exists so the empty-diff rule does not fire.
        """
        import subprocess

        workspace = tmp_path / "ws"
        repo = self._git_repo(tmp_path)
        # Stage a production-file change so check 2 (empty diff) passes.
        (repo / "src_prod.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        self._write_unit_with_red(
            workspace,
            0,
            "- VERIFY AC-1 | type=command | tool=red | cmd=`exit 1` | expect-exit=1",
        )
        rc = _run(workspace, repo)
        assert rc == 0
        records = verification.read_latest_evidence_ledger(workspace, "E1-F1-S1-T1")
        assert records[0].tool == "red"
        assert records[0].exit_code == 1


class TestRedExitHelpers:
    """Direct unit tests for the TDD-gate splicing helpers."""

    def test_splice_replaces_last_red_exit_token(self) -> None:
        content = (
            "## TDD Cycle Log\n"
            "- [RED] tests/a.py -- Command: pytest. Exit: 0. Failures: 1 failed\n"
            "- [RED] tests/b.py -- Command: pytest. Exit: 0. Failures: 2 failed\n"
        )
        spliced = cli._splice_red_exit_code(content, 5)
        # Only the LAST RED entry's exit token is rewritten.
        assert "Exit: 5. Failures: 2 failed" in spliced
        assert "Exit: 0. Failures: 1 failed" in spliced

    def test_splice_no_red_token_returns_content_unchanged(self) -> None:
        content = "## TDD Cycle Log\n(no RED entry)\n"
        assert cli._splice_red_exit_code(content, 9) == content

    def test_tool_captured_red_exit_picks_last_red_record(self) -> None:
        records = [
            verification.EvidenceRecord(ac_ids=["AC-1"], vtype="command", command="x", exit_code=0, tool="red"),
            verification.EvidenceRecord(ac_ids=["AC-2"], vtype="command", command="y", exit_code=4, tool="red"),
            verification.EvidenceRecord(ac_ids=["AC-3"], vtype="apply", command="z", exit_code=0, tool="terraform"),
        ]
        assert cli._tool_captured_red_exit(records) == 4

    def test_tool_captured_red_exit_none_when_no_red(self) -> None:
        records = [
            verification.EvidenceRecord(ac_ids=["AC-1"], vtype="apply", command="z", exit_code=0, tool="terraform"),
        ]
        assert cli._tool_captured_red_exit(records) is None
