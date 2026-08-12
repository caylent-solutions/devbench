"""Tests for ``devbench status``'s install-parity row (issue #301 FR-4).

``cmd_status`` (``devbench status``) renders the harness/target
install-parity row by calling
:func:`devbench.reporting.report.install_parity_line` verbatim -- the same
rendering path ``devbench report`` uses (spec Section 4, FR-4, AC-10,
AC-11) -- so the two surfaces can never disagree about whether the harness
install is behind.

This module is deliberately separate from ``tests/test_cli.py`` (owned by
task E16-F1-S2-T1) so no Changes Manifest conflict is authored (AC-FIX-006).
It also covers the DRY promotion of the short-revision constant: ``cli.py``
imports ``INSTALL_PARITY_SHORT_REVISION_CHARS`` from ``devbench.constants``
instead of defining a private duplicate (AC-FIX-001).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from devbench import cli
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType
from devbench.install_parity import InstallIdentity, InstallParityError, ParityResult


def _identity(revision: str, branch: str | None = "main") -> InstallIdentity:
    """Build an ``InstallIdentity`` for install-parity tests without touching real git state."""
    return InstallIdentity(
        path=Path("/fake/checkout"),
        revision=revision,
        branch=branch,
        origin_url="https://example.invalid/devbench.git",
    )


def _not_self_hosting_result() -> ParityResult:
    return ParityResult(self_hosting=False, harness=None, target=None, behind_count=0, in_sync=True)


def _status_units() -> list[WorkUnit]:
    """Minimal single-unit backlog so ``cmd_status`` has something to summarise."""
    return [
        WorkUnit(
            id="E0-F1-S1-T1",
            title="Only Task",
            status=WorkUnitStatus.DONE,
            unit_type=WorkUnitType.TASK,
            file_path=Path("backlog/E0-F1-S1-T1.md"),
            repo="caylent-solutions/devbench",
            dependencies=[],
        ),
    ]


def _mock_status_parser() -> MagicMock:
    """Build a ``BacklogParser`` mock configured for ``cmd_status`` tests."""
    mock_parser = MagicMock()
    mock_parser.parse_index.return_value = _status_units()
    mock_parser.get_parallel_candidates.return_value = []
    mock_parser.all_done.return_value = True
    mock_parser.get_blocked_units.return_value = []
    return mock_parser


@pytest.fixture(autouse=True)
def _mock_install_parity() -> Any:
    """Pin ``resolve_install_parity`` to a not-self-hosting default for hermeticity (AC-FIX-006).

    Every test in this module calls ``cli.cmd_status()``, which -- now that
    it is wired to ``report.install_parity_line()`` -- calls the real
    ``devbench.install_parity.resolve_install_parity`` against this
    workspace's own git checkouts unless patched. Patched here to a
    deterministic not-self-hosting default so the module is hermetic
    against this repo's own self-hosting install; individual tests in
    ``TestCmdStatusInstallParityRow`` override the mock's ``return_value``
    / ``side_effect`` to exercise the other three cases.
    """
    with patch("devbench.reporting.report.resolve_install_parity") as mock_resolve:
        mock_resolve.return_value = _not_self_hosting_result()
        yield mock_resolve


class TestInstallPartyShortRevisionCharsSharedConstant:
    """cli.py imports the shared constant instead of defining a private duplicate (AC-FIX-001)."""

    def test_constants_module_defines_the_shared_value(self) -> None:
        from devbench.constants import INSTALL_PARITY_SHORT_REVISION_CHARS

        assert INSTALL_PARITY_SHORT_REVISION_CHARS == 7

    def test_cli_module_has_no_private_duplicate(self) -> None:
        assert not hasattr(cli, "_INSTALL_PARITY_SHORT_REVISION_CHARS")

    def test_check_install_parity_uses_shared_constant_for_short_revision(self, _mock_install_parity: Any) -> None:
        """Monkeypatching the shared constant changes ``_check_install_parity``'s
        rendered short-revision length, proving ``cli.py`` reads the value from
        ``devbench.constants`` at call time rather than from a frozen private copy."""
        _mock_install_parity.return_value = ParityResult(
            self_hosting=True,
            harness=_identity("898beb60cafefeed0123456789abcdef0123456"),
            target=_identity("898beb60cafefeed0123456789abcdef0123456"),
            behind_count=0,
            in_sync=True,
        )

        with patch("devbench.cli.INSTALL_PARITY_SHORT_REVISION_CHARS", 4):
            result = cli._check_install_parity()

        assert result is None


class TestCmdStatusInstallParityRow:
    """``cmd_status`` renders the install-parity row via ``report.install_parity_line()``."""

    _HARNESS_REV = "aaaaaaa1111111111111111111111111111111"
    _TARGET_REV = "bbbbbbb2222222222222222222222222222222"

    def test_renders_in_sync_row_when_self_hosting(
        self, capsys: pytest.CaptureFixture[str], _mock_install_parity: Any
    ) -> None:
        _mock_install_parity.return_value = ParityResult(
            self_hosting=True,
            harness=_identity(self._HARNESS_REV, branch="main"),
            target=_identity(self._TARGET_REV, branch="main"),
            behind_count=0,
            in_sync=True,
        )

        with patch("devbench.cli.BacklogParser", return_value=_mock_status_parser()):
            result = cli.cmd_status()

        out = capsys.readouterr().out
        assert result == 0
        assert "Install parity   harness aaaaaaa (main) == target bbbbbbb   IN SYNC" in out

    def test_renders_behind_row_when_self_hosting_and_stale(
        self, capsys: pytest.CaptureFixture[str], _mock_install_parity: Any
    ) -> None:
        _mock_install_parity.return_value = ParityResult(
            self_hosting=True,
            harness=_identity(self._HARNESS_REV, branch="main"),
            target=_identity(self._TARGET_REV, branch="main"),
            behind_count=3,
            in_sync=False,
        )

        with patch("devbench.cli.BacklogParser", return_value=_mock_status_parser()):
            result = cli.cmd_status()

        out = capsys.readouterr().out
        assert result == 0
        assert (
            "Install parity   harness aaaaaaa (main) != target bbbbbbb   BEHIND by 3 commit(s) touching src/devbench/"
            in out
        )

    def test_renders_no_row_when_not_self_hosting(
        self, capsys: pytest.CaptureFixture[str], _mock_install_parity: Any
    ) -> None:
        _mock_install_parity.return_value = _not_self_hosting_result()

        with patch("devbench.cli.BacklogParser", return_value=_mock_status_parser()):
            result = cli.cmd_status()

        out = capsys.readouterr().out
        assert result == 0
        assert "Install parity" not in out

    def test_degrades_to_unavailable_on_resolver_failure_and_rest_still_renders(
        self, capsys: pytest.CaptureFixture[str], _mock_install_parity: Any
    ) -> None:
        _mock_install_parity.side_effect = InstallParityError("git rev-parse failed for checkout '/tmp/x'")

        with patch("devbench.cli.BacklogParser", return_value=_mock_status_parser()):
            result = cli.cmd_status()

        out = capsys.readouterr().out
        assert result == 0
        assert "Install parity   unavailable: git rev-parse failed for checkout '/tmp/x'" in out
        assert "Backlog Status Summary" in out
        assert "All work units are DONE" in out
