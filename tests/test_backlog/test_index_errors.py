"""Tests for the shared backlog-index error diagnostic (issue #305).

``devbench report`` produced an actionable message for a missing work-unit
file or a malformed index; ``devbench status`` let the same exceptions escape
as a raw traceback. Both read the same index through the same parser, so an
operator running both saw a crash and a clean error for one condition. The
handler is shared so the two cannot disagree again.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.backlog.index_errors import exit_with_index_error


class TestExitWithIndexError:
    def test_missing_file_names_the_actual_path_and_exits_1(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """The message must blame the missing work-unit file, not the index."""
        exc = FileNotFoundError(2, "No such file or directory")
        exc.filename = str(tmp_path / "backlog" / "E1-F1-S1-T1.md")
        with pytest.raises(SystemExit) as raised:
            exit_with_index_error("status", tmp_path / "BACKLOG.md", exc)
        assert raised.value.code == 1
        err = capsys.readouterr().err
        assert "devbench status: cannot read" in err
        assert "E1-F1-S1-T1.md" in err
        assert str(tmp_path / "BACKLOG.md") in err
        assert "transient writer-window race; re-run" in err
        assert "devbench validate-backlog" in err

    def test_malformed_index_points_at_validate_backlog(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """A parse failure will not fix itself on re-run, so the hint differs."""
        with pytest.raises(SystemExit) as raised:
            exit_with_index_error("status", tmp_path / "BACKLOG.md", ValueError("no work-unit rows found"))
        assert raised.value.code == 1
        err = capsys.readouterr().err
        assert "devbench status: cannot parse" in err
        assert "no work-unit rows found" in err
        assert "devbench validate-backlog" in err
        assert "writer-window race" not in err

    @pytest.mark.parametrize("command", ["status", "report"])
    def test_command_name_is_used_verbatim(
        self, command: str, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        with pytest.raises(SystemExit):
            exit_with_index_error(command, tmp_path / "BACKLOG.md", ValueError("boom"))
        assert f"devbench {command}: cannot parse" in capsys.readouterr().err

    def test_missing_filename_attribute_falls_back_to_the_message(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """FileNotFoundError raised without a filename must still name something."""
        with pytest.raises(SystemExit):
            exit_with_index_error("status", tmp_path / "BACKLOG.md", FileNotFoundError("BACKLOG.md is gone"))
        assert "BACKLOG.md is gone" in capsys.readouterr().err


class TestStatusAndReportAgree:
    """The regression: one condition, two commands, one diagnostic shape."""

    @pytest.mark.parametrize("exc_factory", [lambda: FileNotFoundError("x"), lambda: ValueError("x")])
    def test_both_commands_exit_1_with_a_devbench_prefixed_message(
        self, exc_factory, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        messages = {}
        for command in ("status", "report"):
            with pytest.raises(SystemExit) as raised:
                exit_with_index_error(command, tmp_path / "BACKLOG.md", exc_factory())
            assert raised.value.code == 1
            messages[command] = capsys.readouterr().err
        # Same shape, differing only in the command name.
        assert messages["status"].replace("status", "CMD") == messages["report"].replace("report", "CMD")
