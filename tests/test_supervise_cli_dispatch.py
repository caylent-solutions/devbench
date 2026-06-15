"""Tests for the ``supervise`` verb-group registration + dispatch (FR-1, FR-2).

Phase 1 lands the verb surface: registration in ``_COMMANDS`` /
``_VARIADIC_COMMANDS``, the ``cmd_supervise(*argv)`` sub-verb dispatcher, the
``--name`` grammar validation, and the full argument parser. The not-yet-built
sub-verb bodies fail fast with a clear NotImplemented-style error (they do NOT
silently succeed); their real behavior lands in later phases.
"""

from __future__ import annotations

import sys
from io import StringIO
from unittest.mock import patch

import pytest

from devbench import cli
from devbench.constants import SUPERVISE_DEFAULT_NAME, SUPERVISE_SUBVERBS


@pytest.mark.unit
class TestSuperviseRegistration:
    """FR-1: the supervise verb is registered and variadic."""

    def test_supervise_in_commands(self) -> None:
        assert "supervise" in cli._COMMANDS
        handler, min_args, desc = cli._COMMANDS["supervise"]
        assert handler is cli.cmd_supervise
        assert min_args == 0
        assert isinstance(desc, str)

    def test_supervise_is_variadic(self) -> None:
        # The sub-verb + flags need raw argv -- it must own its parsing.
        assert "supervise" in cli._VARIADIC_COMMANDS

    def test_help_lists_all_six_subverbs(self) -> None:
        captured = StringIO()
        with (
            patch.object(sys, "argv", ["devbench", "supervise", "--help"]),
            patch("sys.stdout", captured),
        ):
            exit_code = cli.main()
        assert exit_code == 0
        output = captured.getvalue()
        for sub in SUPERVISE_SUBVERBS:
            assert sub in output, f"supervise --help missing sub-verb {sub!r}"


@pytest.mark.unit
class TestSuperviseSubverbDispatch:
    """FR-1: cmd_supervise dispatches on argv[0]; unknown sub-verb -> exit 2."""

    def test_no_subverb_exits_2_with_usage(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_supervise()
        assert rc == 2
        err = capsys.readouterr().err
        for sub in SUPERVISE_SUBVERBS:
            assert sub in err

    def test_unknown_subverb_exits_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_supervise("frobnicate")
        assert rc == 2
        err = capsys.readouterr().err
        assert "frobnicate" in err
        assert "start" in err  # usage lists the valid sub-verbs

    @pytest.mark.parametrize("sub", ["status", "info"])
    def test_readonly_subverb_lists_empty_registry(self, sub: str, tmp_path) -> None:
        # status/info are read-only listings (Phase 4): on an empty registry they
        # are routed and return 0 (NOT "unknown", NOT NotImplementedError).
        from unittest.mock import patch

        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli._supervise_screen_names", return_value=set()),
        ):
            rc = cli._dispatch_supervise_subverb(sub, [])
        assert rc == 0

    @pytest.mark.parametrize("sub", ["stop", "restart"])
    def test_implemented_subverb_fails_fast_on_missing_session(
        self, sub: str, tmp_path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # stop/restart are now implemented; with no matching session they fail
        # fast (exit 2), never a silent success.
        from unittest.mock import patch

        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            rc = cli._dispatch_supervise_subverb(sub, [])
        assert rc == 2
        assert "no supervise session" in capsys.readouterr().err


@pytest.mark.unit
class TestSuperviseNameValidation:
    """FR-2: --name grammar (ADR-23) + path-traversal rejection."""

    @pytest.mark.parametrize("name", ["default", "nightly", "fast-1", "A_b-2"])
    def test_valid_names_accepted(self, name: str) -> None:
        # Should not raise.
        cli._validate_supervise_name(name)

    @pytest.mark.parametrize("name", ["", "-leading", "_under", "has space", "a/b", "..", "../x", "a..b"])
    def test_invalid_names_rejected(self, name: str) -> None:
        with pytest.raises(ValueError, match="invalid session name"):
            cli._validate_supervise_name(name)


@pytest.mark.unit
class TestSuperviseArgParsing:
    """FR-2: the supervise arg parser maps every documented flag."""

    def test_defaults(self) -> None:
        args = cli._parse_supervise_args([])
        assert args.name == SUPERVISE_DEFAULT_NAME
        assert args.include == ""
        assert args.exclude == ""
        assert args.allow_overlap is False
        assert args.model is None
        assert args.effort is None
        assert args.hard is False
        assert args.screen is False

    def test_all_flags(self) -> None:
        args = cli._parse_supervise_args(
            [
                "--name",
                "nightly",
                "--include",
                "E11",
                "--exclude",
                "E12",
                "--allow-overlap",
                "--model",
                "opus",
                "--effort",
                "xhigh",
            ]
        )
        assert args.name == "nightly"
        assert args.include == "E11"
        assert args.exclude == "E12"
        assert args.allow_overlap is True
        assert args.model == "opus"
        assert args.effort == "xhigh"

    def test_hard_flag(self) -> None:
        assert cli._parse_supervise_args(["--hard"]).hard is True

    def test_screen_flag(self) -> None:
        assert cli._parse_supervise_args(["--screen"]).screen is True

    def test_missing_value_fails_fast(self) -> None:
        with pytest.raises(ValueError, match="requires a value"):
            cli._parse_supervise_args(["--name"])

    def test_unknown_flag_fails_fast(self) -> None:
        with pytest.raises(ValueError, match="unknown flag"):
            cli._parse_supervise_args(["--bogus"])


@pytest.mark.unit
class TestSuperviseSubverbFailFast:
    """The sub-verb bodies fail fast on bad input (no silent success).

    All six operator sub-verbs + ``__run`` are implemented (Phase 4 lands
    ``status``/``info`` + the read-only attach follow); a bad ``--name`` or an
    unknown flag still surfaces as exit 2 before any body runs.
    """

    def test_invalid_name_through_dispatch_returns_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        # A bad --name is caught before any body runs and surfaces as exit 2.
        rc = cli.cmd_supervise("start", "--name", "../escape")
        assert rc == 2
        assert "invalid session name" in capsys.readouterr().err

    def test_unknown_flag_through_dispatch_returns_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        # An unknown flag surfaces as exit 2 (caught during arg parsing).
        rc = cli.cmd_supervise("start", "--bogus")
        assert rc == 2
        assert "unknown flag" in capsys.readouterr().err

    def test_attach_screen_gated_returns_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        # Phase 2: attach --screen is fail-fast-disabled (AC-33) until DI-4.
        rc = cli.cmd_supervise("attach", "--name", "nightly", "--screen")
        assert rc == 2
        assert "--screen attach is not enabled" in capsys.readouterr().err
