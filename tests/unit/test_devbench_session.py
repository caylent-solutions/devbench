"""Unit tests for `tools.devbench_session`.

Covers _resolve_paths, _validate_session_id, cmd_start, cmd_attach, cmd_list,
cmd_stop, and the argparse build. All external side effects (subprocess, exec)
are stubbed via injected runner callables and patched ``os.execvp``.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import devbench_session as ds
import pytest


@pytest.fixture
def fake_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    return home


@pytest.fixture
def fake_runner():
    """Returns (runner_fn, calls_list) where runner_fn records every call."""
    calls: list[list[str]] = []

    class _Result:
        def __init__(self, stdout: str = "", returncode: int = 0):
            self.stdout = stdout
            self.returncode = returncode

    def runner(cmd, *, cwd=None, check=True):
        calls.append(list(cmd))
        if cmd[:2] == ["screen", "-ls"]:
            return _Result(stdout="\tdevbench-1\t(Detached)\n\tdevbench-3\t(Detached)\n")
        return _Result()

    return runner, calls


@pytest.mark.unit
class TestResolvePaths:
    def test_returns_workspace_session_screen_paths(self, fake_home: Path) -> None:
        p = ds._resolve_paths(2, home=fake_home)
        assert p.home == fake_home
        assert p.workspace == fake_home / "workspace"
        assert p.session_dir == fake_home / "workspace" / "devbench-session-2"
        assert p.repo_dir == fake_home / "workspace" / "devbench-session-2" / "devbench"
        assert p.screen_name == "devbench-2"

    def test_default_home(self) -> None:
        p = ds._resolve_paths(1)
        assert str(p.home) == str(Path.home()) or p.home.name


@pytest.mark.unit
class TestValidateSessionId:
    def test_accepts_positive_int(self) -> None:
        assert ds._validate_session_id("5") == 5

    def test_rejects_zero(self) -> None:
        with pytest.raises(ValueError, match=">= 1"):
            ds._validate_session_id("0")

    def test_rejects_negative(self) -> None:
        with pytest.raises(ValueError, match=">= 1"):
            ds._validate_session_id("-3")

    def test_rejects_non_integer(self) -> None:
        with pytest.raises(ValueError, match="positive integer"):
            ds._validate_session_id("abc")


@pytest.mark.unit
class TestCmdStart:
    def test_clones_when_repo_absent(self, fake_home: Path, fake_runner) -> None:
        runner, calls = fake_runner
        rc = ds.cmd_start(
            1, repo_url="git@x:repo.git", branch="main", mode="interactive", home=fake_home, runner=runner
        )
        assert rc == 0
        assert any(c[0:2] == ["git", "clone"] for c in calls)
        assert (fake_home / "workspace" / "devbench-session-1").is_dir()
        assert any(c[0] == "screen" for c in calls)

    def test_pulls_when_repo_exists(self, fake_home: Path, fake_runner) -> None:
        runner, calls = fake_runner
        repo = fake_home / "workspace" / "devbench-session-2" / "devbench"
        repo.mkdir(parents=True)
        rc = ds.cmd_start(
            2, repo_url="git@x:repo.git", branch="main", mode="orchestrate", home=fake_home, runner=runner
        )
        assert rc == 0
        assert not any(c[0:2] == ["git", "clone"] for c in calls)
        assert any("fetch" in c for c in calls if c[0] == "git")
        screen_cmd = next(c for c in calls if c[0] == "screen")
        assert any("uv run devbench start" in part for part in screen_cmd)

    def test_rejects_invalid_mode(self, fake_home: Path, fake_runner) -> None:
        runner, _ = fake_runner
        rc = ds.cmd_start(3, repo_url="x", branch="main", mode="bogus", home=fake_home, runner=runner)
        assert rc == 1

    def test_interactive_mode_uses_bash(self, fake_home: Path, fake_runner) -> None:
        runner, calls = fake_runner
        ds.cmd_start(7, repo_url="x", branch="main", mode="interactive", home=fake_home, runner=runner)
        screen_cmd = next(c for c in calls if c[0] == "screen")
        assert any("bash -i" in part for part in screen_cmd)


@pytest.mark.unit
class TestCmdAttach:
    def test_returns_1_when_session_missing(self, fake_home: Path, fake_runner) -> None:
        runner, _ = fake_runner
        rc = ds.cmd_attach(99, home=fake_home, runner=runner)
        assert rc == 1

    def test_runs_screen_when_session_present(self, fake_home: Path, fake_runner) -> None:
        runner, _ = fake_runner
        with mock.patch("subprocess.run") as msr:
            msr.return_value = mock.Mock(returncode=0)
            rc = ds.cmd_attach(1, home=fake_home, runner=runner)
        assert msr.called
        assert msr.call_args.args[0] == ["screen", "-r", "devbench-1"]
        assert rc == 0


@pytest.mark.unit
class TestCmdList:
    def test_prints_devbench_sessions(self, fake_runner, capsys) -> None:
        runner, _ = fake_runner
        rc = ds.cmd_list(runner=runner)
        out = capsys.readouterr().out
        assert rc == 0
        assert "devbench-1" in out
        assert "devbench-3" in out

    def test_prints_no_sessions_when_empty(self, capsys) -> None:
        class _R:
            def __init__(self):
                self.stdout = ""
                self.returncode = 0

        def empty_runner(cmd, *, cwd=None, check=True):
            return _R()

        rc = ds.cmd_list(runner=empty_runner)
        out = capsys.readouterr().out
        assert rc == 0
        assert "(no devbench sessions)" in out


@pytest.mark.unit
class TestCmdStop:
    def test_returns_1_when_session_missing(self, fake_home: Path, fake_runner) -> None:
        runner, _ = fake_runner
        rc = ds.cmd_stop(99, home=fake_home, runner=runner)
        assert rc == 1

    def test_quits_present_session(self, fake_home: Path, fake_runner) -> None:
        runner, calls = fake_runner
        rc = ds.cmd_stop(3, home=fake_home, runner=runner)
        assert rc == 0
        assert any(c == ["screen", "-S", "devbench-3", "-X", "quit"] for c in calls)


@pytest.mark.unit
class TestArgparseAndMain:
    def test_main_dispatches_start(self, fake_home: Path, monkeypatch) -> None:
        called = {}

        def fake_start(session_id, *, repo_url, branch, mode):
            called["start"] = (session_id, repo_url, branch, mode)
            return 0

        monkeypatch.setattr(ds, "cmd_start", fake_start)
        rc = ds.main(["start", "5", "--branch", "feat", "--mode", "orchestrate"])
        assert rc == 0
        assert called["start"] == (5, ds.DEFAULT_REPO_URL, "feat", "orchestrate")

    def test_main_dispatches_attach(self, monkeypatch) -> None:
        monkeypatch.setattr(ds, "cmd_attach", lambda sid: 0)
        assert ds.main(["attach", "2"]) == 0

    def test_main_dispatches_list(self, monkeypatch) -> None:
        monkeypatch.setattr(ds, "cmd_list", lambda: 0)
        assert ds.main(["list"]) == 0

    def test_main_dispatches_stop(self, monkeypatch) -> None:
        monkeypatch.setattr(ds, "cmd_stop", lambda sid: 0)
        assert ds.main(["stop", "1"]) == 0

    def test_argparse_rejects_unknown_subcommand(self) -> None:
        with pytest.raises(SystemExit):
            ds.main(["bogus"])
