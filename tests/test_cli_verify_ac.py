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
        """A large sentinel-free output is bounded near the cap, keeping head and tail.

        The sentinel-aware trim keeps a leading head window and a trailing tail
        window with an elision marker between, so both the first and the last
        lines of an over-budget log survive (the old slice kept only the tail).
        """
        workspace = tmp_path / "ws"
        # Emit far more than the cap; the artifact keeps a head + tail window.
        _write_unit(
            workspace,
            "- VERIFY AC-1 | type=command | cmd=`for i in $(seq 1 5000); do echo LINE$i; done` | expect-exit=0",
        )
        with patch("devbench.config.CI_FAILURE_LOG_BYTES", 256):
            rc = _run(workspace, repo_path)
        assert rc == 0
        artifact = verification.evidence_attempt_dir(workspace, "E1-F1-S1-T1", 1) / "AC-1.log"
        text = artifact.read_text(encoding="utf-8")
        # Bounded near the cap (head + tail windows plus one elision marker).
        assert len(text) <= 256 + len("[... 999999 bytes elided ...]\n")
        # The LAST line survives (tail window) and the FIRST now survives too
        # (head window) -- the middle is elided.
        assert "LINE5000" in text
        assert "LINE1\n" in text
        assert "bytes elided" in text


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


class TestDeterministicGateSeed:
    """verify-ac runs each executable directive with a pinned pytest ordering seed.

    The per-unit gate must be reproducible: the same code yields the same
    verdict on every run. The runner overlays ``PYTHONHASHSEED`` and (when the
    target repo has ``pytest-randomly``) a fixed ``--randomly-seed`` (via
    ``PYTEST_ADDOPTS``) on the command's environment so an order-dependent
    sibling test cannot non-deterministically block the unit.
    """

    def test_command_env_carries_pinned_randomly_seed_when_plugin_present(
        self, tmp_path: Path, repo_path: Path
    ) -> None:
        workspace = tmp_path / "ws"
        # The command echoes the two pinned env vars; the artifact captures them.
        _write_unit(
            workspace,
            "- VERIFY AC-1 | type=command | "
            'cmd=`echo "SEEN_ADDOPTS=$PYTEST_ADDOPTS"; echo "SEEN_HASHSEED=$PYTHONHASHSEED"` | expect-exit=0',
        )
        with (
            patch("devbench.config.VERIFY_AC_PYTEST_SEED", 24680),
            patch("devbench.verification.pytest_randomly_available", return_value=True),
        ):
            rc = _run(workspace, repo_path)
        assert rc == 0
        artifact = verification.evidence_attempt_dir(workspace, "E1-F1-S1-T1", 1) / "AC-1.log"
        text = artifact.read_text(encoding="utf-8")
        assert "SEEN_ADDOPTS=" in text
        assert "--randomly-seed=24680" in text
        assert "SEEN_HASHSEED=24680" in text

    def test_pythonhashseed_pinned_even_without_plugin(self, tmp_path: Path, repo_path: Path) -> None:
        """With no pytest-randomly the seed flag is NOT injected, but PYTHONHASHSEED still is."""
        workspace = tmp_path / "ws"
        _write_unit(
            workspace,
            "- VERIFY AC-1 | type=command | "
            'cmd=`echo "SEEN_ADDOPTS=[$PYTEST_ADDOPTS]"; echo "SEEN_HASHSEED=$PYTHONHASHSEED"` | expect-exit=0',
        )
        with (
            patch("devbench.config.VERIFY_AC_PYTEST_SEED", 5),
            patch("devbench.verification.pytest_randomly_available", return_value=False),
        ):
            rc = _run(workspace, repo_path)
        assert rc == 0
        text = (verification.evidence_attempt_dir(workspace, "E1-F1-S1-T1", 1) / "AC-1.log").read_text(encoding="utf-8")
        assert "SEEN_HASHSEED=5" in text
        # No --randomly-seed injected (a repo without the plugin would error on it).
        assert "--randomly-seed" not in text

    def test_seed_is_deterministic_across_two_runs(self, tmp_path: Path, repo_path: Path) -> None:
        workspace = tmp_path / "ws"
        _write_unit(
            workspace,
            "- VERIFY AC-1 | type=command | cmd=`echo SEED=$PYTEST_ADDOPTS` | expect-exit=0",
        )
        with (
            patch("devbench.config.VERIFY_AC_PYTEST_SEED", 111),
            patch("devbench.verification.pytest_randomly_available", return_value=True),
        ):
            assert _run(workspace, repo_path) == 0
            first = (verification.evidence_attempt_dir(workspace, "E1-F1-S1-T1", 1) / "AC-1.log").read_text(
                encoding="utf-8"
            )
            assert _run(workspace, repo_path) == 0
            second = (verification.evidence_attempt_dir(workspace, "E1-F1-S1-T1", 2) / "AC-1.log").read_text(
                encoding="utf-8"
            )
        assert "--randomly-seed=111" in first
        assert first == second

    def test_inherits_parent_env_alongside_pinned_seed(self, tmp_path: Path, repo_path: Path) -> None:
        """The overlay does not wipe the inherited environment: PATH still resolves tools."""
        workspace = tmp_path / "ws"
        # ``env`` is resolved via PATH; if PATH were dropped the command would 127.
        _write_unit(workspace, "- VERIFY AC-1 | type=command | cmd=`env >/dev/null && echo OK` | expect-exit=0")
        with (
            patch("devbench.config.VERIFY_AC_PYTEST_SEED", 5),
            patch("devbench.verification.pytest_randomly_available", return_value=True),
        ):
            rc = _run(workspace, repo_path)
        assert rc == 0
        artifact = verification.evidence_attempt_dir(workspace, "E1-F1-S1-T1", 1) / "AC-1.log"
        assert "OK" in artifact.read_text(encoding="utf-8")


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


class TestCommandUsesCoverage:
    """Tracked-issue 001: classify whether a verify-ac command runs coverage.py.

    A command that runs ``pytest --cov`` / ``coverage run`` / ``coverage`` writes
    the coverage SQLite db to the shared default ``.coverage`` file in the
    checkout; repeated/overlapping runs deadlock on its lock and surface as a
    false ``CLAIM_NOT_CONVERGING``. Only coverage commands need an isolated
    ``COVERAGE_FILE``; a non-coverage command must be left untouched so a command
    that legitimately READS an existing ``.coverage`` is not surprised.
    """

    def test_pytest_cov_flag_is_coverage(self) -> None:
        assert cli._command_uses_coverage("uv run pytest --cov=devbench --cov-fail-under=100 tests/") is True

    def test_coverage_run_is_coverage(self) -> None:
        assert cli._command_uses_coverage("coverage run -m pytest && coverage report") is True

    def test_bare_coverage_subcommand_is_coverage(self) -> None:
        assert cli._command_uses_coverage("coverage combine") is True

    def test_plain_pytest_is_not_coverage(self) -> None:
        assert cli._command_uses_coverage("uv run pytest tests/unit/test_foo.py") is False

    def test_non_test_command_is_not_coverage(self) -> None:
        assert cli._command_uses_coverage("terragrunt apply -auto-approve") is False

    def test_discover_substring_is_not_coverage(self) -> None:
        # A word merely CONTAINING 'cov' (e.g. 'discover', 'recover') must not
        # be misclassified as a coverage run.
        assert cli._command_uses_coverage("python -m unittest discover && recover_state.sh") is False


class TestUniqueCoverageFilePath:
    """Tracked-issue 001: a per-invocation unique, non-default COVERAGE_FILE path."""

    def test_path_is_not_the_default(self) -> None:
        assert Path(cli._unique_coverage_file_path()).name != ".coverage"

    def test_repeated_calls_return_distinct_paths(self) -> None:
        paths = {cli._unique_coverage_file_path() for _ in range(5)}
        assert len(paths) == 5, "each invocation must isolate onto its own coverage data file"

    def test_path_is_absolute(self) -> None:
        # A relative path would still land in the checkout cwd and contend; the
        # isolated file must be an absolute temp path outside the checkout.
        assert Path(cli._unique_coverage_file_path()).is_absolute()


class TestCleanupCoverageDataFiles:
    """Tracked-issue 001: the per-invocation coverage data file (and any
    coverage-parallel siblings) is torn down after the run, best-effort.
    """

    def test_removes_primary_and_parallel_siblings(self, tmp_path: Path) -> None:
        primary = tmp_path / "devbench-coverage-x.dat"
        sib1 = tmp_path / "devbench-coverage-x.dat.host.123"
        sib2 = tmp_path / "devbench-coverage-x.dat.host.456"
        for f in (primary, sib1, sib2):
            f.write_text("data", encoding="utf-8")
        cli._cleanup_coverage_data_files(str(primary))
        assert not primary.exists()
        assert not sib1.exists()
        assert not sib2.exists()

    def test_missing_primary_is_safe(self, tmp_path: Path) -> None:
        # coverage.py may not have produced a db (e.g. the command failed before
        # writing one); removing a non-existent file must not raise.
        cli._cleanup_coverage_data_files(str(tmp_path / "never-created.dat"))


class TestVerifyAcCoverageIsolation:
    """Tracked-issue 001: verify-ac runs a coverage command with an isolated
    COVERAGE_FILE so repeated/overlapping coverage runs never contend on the
    shared default ``.coverage`` SQLite db -- and leaves a non-coverage command
    untouched.
    """

    def test_cov_command_runs_with_unique_coverage_file_env(self, tmp_path: Path, repo_path: Path) -> None:
        workspace = tmp_path / "ws"
        # The command echoes COVERAGE_FILE; the artifact captures it. The literal
        # ``--cov`` marks it a coverage command (no real pytest run needed).
        _write_unit(
            workspace,
            '- VERIFY AC-1 | type=command | cmd=`echo "SEEN_COVFILE=[$COVERAGE_FILE]"; true --cov` | expect-exit=0',
        )
        rc = _run(workspace, repo_path)
        assert rc == 0
        text = (verification.evidence_attempt_dir(workspace, "E1-F1-S1-T1", 1) / "AC-1.log").read_text(encoding="utf-8")
        # A COVERAGE_FILE was injected, and it is NOT the shared default.
        assert "SEEN_COVFILE=[" in text
        marker = "SEEN_COVFILE=["
        injected = text[text.index(marker) + len(marker) :].split("]", 1)[0]
        assert injected, "a coverage command must run with COVERAGE_FILE set"
        assert Path(injected).name != ".coverage"
        assert Path(injected).is_absolute()

    def test_non_cov_command_does_not_get_coverage_file(self, tmp_path: Path, repo_path: Path) -> None:
        workspace = tmp_path / "ws"
        _write_unit(
            workspace,
            "- VERIFY AC-1 | type=command | cmd=`echo SEEN_COVFILE=[$COVERAGE_FILE]` | expect-exit=0",
        )
        rc = _run(workspace, repo_path)
        assert rc == 0
        text = (verification.evidence_attempt_dir(workspace, "E1-F1-S1-T1", 1) / "AC-1.log").read_text(encoding="utf-8")
        # No COVERAGE_FILE injected for a non-coverage command (the placeholder
        # stays empty -- the env var is unset).
        assert "SEEN_COVFILE=[]" in text

    def test_two_cov_runs_get_distinct_coverage_files(self, tmp_path: Path, repo_path: Path) -> None:
        workspace = tmp_path / "ws"
        _write_unit(
            workspace,
            '- VERIFY AC-1 | type=command | cmd=`echo "SEEN_COVFILE=[$COVERAGE_FILE]"; true --cov` | expect-exit=0',
        )

        def _injected_path(attempt: int) -> str:
            text = (verification.evidence_attempt_dir(workspace, "E1-F1-S1-T1", attempt) / "AC-1.log").read_text(
                encoding="utf-8"
            )
            marker = "SEEN_COVFILE=["
            return text[text.index(marker) + len(marker) :].split("]", 1)[0]

        assert _run(workspace, repo_path) == 0
        assert _run(workspace, repo_path) == 0
        first, second = _injected_path(1), _injected_path(2)
        assert first and second and first != second, "each coverage run must isolate onto its own data file"
