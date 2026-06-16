"""Tests for the verify-ac per-directive timeout override and the generic default.

Tracked issue: ``verify-ac-test-timeout-default-too-short-for-live-terratests``.

The generic ``DEFAULT_TEST_TIMEOUT`` of 300s killed any long-running ``type=command`` /
``type=terratest`` directive at 5 minutes. These tests pin two backlog-agnostic
behaviours:

1. The generic default is raised to a sensible long-operation budget (not a
   tools-telemetry-specific value): it must accommodate a live-test-class AC.
2. A VERIFY directive may declare a per-AC ``timeout=<seconds>`` that overrides the
   global default, so any backlog can derive the bound from the test's own declared
   timeout. A malformed (non-integer / non-positive) ``timeout=`` fails fast.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbench import cli, verification

pytestmark = pytest.mark.unit


class TestGenericDefaultTestTimeout:
    """DEFAULT_TEST_TIMEOUT is a sensible generic long-operation budget, not 300s."""

    def test_default_test_timeout_accommodates_long_operations(self) -> None:
        """The generic default is no longer the 5-minute value that killed live tests.

        It must be large enough that a live-test-class AC (terraform apply +
        idempotency re-plan + cleanup destroy) is not bounded to 5 minutes by
        default. We assert it is at least a 60-minute generic long-op budget.
        """
        from devbench.constants import DEFAULT_TEST_TIMEOUT

        assert DEFAULT_TEST_TIMEOUT != 300, (
            "DEFAULT_TEST_TIMEOUT must no longer be the 5-minute value that spuriously "
            "killed long-running live-test ACs."
        )
        assert DEFAULT_TEST_TIMEOUT >= 3600, (
            "DEFAULT_TEST_TIMEOUT must be a generic long-operation budget (>= 3600s) so a "
            "live-test-class AC is not bounded to 5 minutes by default."
        )

    def test_default_test_timeout_is_positive_int(self) -> None:
        from devbench.constants import DEFAULT_TEST_TIMEOUT

        assert isinstance(DEFAULT_TEST_TIMEOUT, int)
        assert DEFAULT_TEST_TIMEOUT > 0


class TestPerDirectiveTimeoutParsing:
    """A VERIFY directive may declare a per-AC ``timeout=<seconds>`` override."""

    def test_timeout_field_defaults_to_none(self) -> None:
        """A directive without ``timeout=`` carries ``timeout is None`` (use global default)."""
        item = verification.parse_verification_item(" AC-1 | type=command | cmd=`exit 0` | expect-exit=0")
        assert item.timeout is None

    def test_timeout_field_parsed_as_int(self) -> None:
        """A directive with ``timeout=5400`` parses the override as an int."""
        item = verification.parse_verification_item(
            " AC-3 | type=terratest | cmd=`make tf-test` | expect-exit=0 | timeout=5400"
        )
        assert item.timeout == 5400

    def test_non_integer_timeout_fails_fast(self) -> None:
        """A non-integer ``timeout=`` raises ValueError (fail fast, no silent ignore)."""
        with pytest.raises(ValueError, match="timeout"):
            verification.parse_verification_item(" AC-3 | type=command | cmd=`exit 0` | timeout=not-a-number")

    def test_non_positive_timeout_fails_fast(self) -> None:
        """A zero/negative ``timeout=`` raises ValueError (a non-positive bound is meaningless)."""
        with pytest.raises(ValueError, match="timeout"):
            verification.parse_verification_item(" AC-3 | type=command | cmd=`exit 0` | timeout=0")


_REPO = "caylent-solutions/devbench"


def _make_unit() -> object:
    from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

    return WorkUnit(
        id="E1-F1-S1-T1",
        title="Verify AC timeout",
        status=WorkUnitStatus.IN_PROGRESS,
        unit_type=WorkUnitType.TASK,
        file_path=Path("backlog/E1-F1-S1-T1.md"),
        repo=_REPO,
        dependencies=[],
    )


def _write_unit(workspace_root: Path, verification_block: str) -> Path:
    backlog = workspace_root / "backlog"
    backlog.mkdir(parents=True, exist_ok=True)
    wu = backlog / "E1-F1-S1-T1.md"
    wu.write_text(
        "# E1-F1-S1-T1: Verify AC timeout\n\n"
        "## Status: in-progress\n\n"
        "## Acceptance Criteria\n\n- [ ] AC-1: works\n\n"
        f"## Verification\n\n{verification_block}\n\n"
        "## Comments\n",
        encoding="utf-8",
    )
    return wu


class TestPerDirectiveTimeoutApplied:
    """The parsed per-AC ``timeout=`` is the bound actually passed to the runner."""

    def _run_capture_timeout(self, workspace_root: Path, repo_path: Path) -> int:
        """Patch ``_run_verification_item`` to record the resolved ``timeout`` kwarg."""
        unit = _make_unit()
        parser = MagicMock()
        parser.parse_index.return_value = [unit]
        with (
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch("devbench.cli.BACKLOG_ROOT", workspace_root / "backlog"),
            patch("devbench.cli.WORKSPACE_ROOT", workspace_root),
            patch("devbench.cli.REPO_LOCAL_PATHS", {_REPO: repo_path}),
            patch("devbench.cli._run_verification_item") as run_item,
        ):
            run_item.return_value = verification.EvidenceRecord(
                ac_ids=["AC-3"],
                vtype="command",
                command="exit 0",
                exit_code=0,
                tool=None,
                started_at="2026-06-16 00:00 UTC",
                finished_at="2026-06-16 00:00 UTC",
                artifact="x",
            )
            cli.cmd_verify_ac("E1-F1-S1-T1")
            assert run_item.call_count == 1
            return run_item.call_args.kwargs["timeout"]

    def test_directive_timeout_overrides_global_default(self, tmp_path: Path) -> None:
        """A directive declaring ``timeout=5400`` runs with a 5400s bound, not the global default."""
        repo = tmp_path / "repo"
        repo.mkdir()
        workspace = tmp_path / "ws"
        _write_unit(workspace, "- VERIFY AC-3 | type=command | cmd=`exit 0` | expect-exit=0 | timeout=5400")
        resolved = self._run_capture_timeout(workspace, repo)
        assert resolved == 5400

    def test_directive_without_timeout_uses_global_default(self, tmp_path: Path) -> None:
        """A directive with no ``timeout=`` runs with the global ``TEST_TIMEOUT`` default."""
        from devbench.config import TEST_TIMEOUT

        repo = tmp_path / "repo"
        repo.mkdir()
        workspace = tmp_path / "ws"
        _write_unit(workspace, "- VERIFY AC-3 | type=command | cmd=`exit 0` | expect-exit=0")
        resolved = self._run_capture_timeout(workspace, repo)
        assert resolved == TEST_TIMEOUT
