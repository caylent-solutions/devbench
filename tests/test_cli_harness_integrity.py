"""Startup harness-integrity check (trust-gap fix).

``devbench start`` runs a config-gated check against the devbench checkout that
detects uncommitted edits under the devbench package source (``src/devbench/**``)
-- the signature of a prior orchestrate self-edit or an unreviewed manual
change. ``orchestrate.harness_integrity_check`` selects ``off`` / ``warn`` (the
default) / ``fail``.

These tests drive the pure helper ``cli._check_harness_integrity`` directly
against a throwaway git repo so they never touch the real devbench checkout.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from devbench import cli


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _make_devbench_repo(tmp_path: Path, *, dirty: bool) -> Path:
    """Create a throwaway git repo shaped like the devbench checkout.

    Commits a clean ``src/devbench/cli.py`` + ``pyproject.toml``; when *dirty*
    is True leaves an uncommitted edit under ``src/devbench/`` so the check sees
    a self-edit signature.
    """
    repo = tmp_path / "devbench-checkout"
    pkg = repo / "src" / "devbench"
    pkg.mkdir(parents=True)
    (pkg / "cli.py").write_text("# clean\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname = 'devbench'\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "initial")
    if dirty:
        (pkg / "cli.py").write_text("# self-edited mid-run\n", encoding="utf-8")
    return repo


@pytest.mark.unit
class TestHarnessIntegrityCheck:
    def test_off_returns_none_even_when_dirty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _make_devbench_repo(tmp_path, dirty=True)
        monkeypatch.setattr(cli, "_devbench_repo_root", lambda: repo)
        assert cli._check_harness_integrity("off") is None
        assert "HARNESS" not in capsys.readouterr().err

    def test_warn_returns_none_and_warns_when_dirty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _make_devbench_repo(tmp_path, dirty=True)
        monkeypatch.setattr(cli, "_devbench_repo_root", lambda: repo)
        rc = cli._check_harness_integrity("warn")
        assert rc is None
        err = capsys.readouterr().err
        assert "[HARNESS_INTEGRITY]" in err
        assert "src/devbench/cli.py" in err

    def test_warn_silent_when_clean(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _make_devbench_repo(tmp_path, dirty=False)
        monkeypatch.setattr(cli, "_devbench_repo_root", lambda: repo)
        assert cli._check_harness_integrity("warn") is None
        assert "[HARNESS_INTEGRITY]" not in capsys.readouterr().err

    def test_fail_returns_nonzero_when_dirty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = _make_devbench_repo(tmp_path, dirty=True)
        monkeypatch.setattr(cli, "_devbench_repo_root", lambda: repo)
        rc = cli._check_harness_integrity("fail")
        assert rc is not None and rc != 0
        err = capsys.readouterr().err
        assert "[HARNESS_INTEGRITY]" in err

    def test_fail_returns_none_when_clean(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = _make_devbench_repo(tmp_path, dirty=False)
        monkeypatch.setattr(cli, "_devbench_repo_root", lambda: repo)
        assert cli._check_harness_integrity("fail") is None

    def test_non_git_checkout_does_not_crash(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = tmp_path / "no-git"
        (repo / "src" / "devbench").mkdir(parents=True)
        monkeypatch.setattr(cli, "_devbench_repo_root", lambda: repo)
        assert cli._check_harness_integrity("warn") is None
        assert cli._check_harness_integrity("fail") is None


@pytest.mark.unit
class TestOrchestratorStartupGates:
    """``_check_orchestrator_startup_gates`` chains the hook check, the integrity check, then pre-sync."""

    def test_hook_check_failure_short_circuits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from pathlib import Path as _Path

        monkeypatch.setattr(cli, "_check_guard_hooks_registered", lambda _p: 7)
        monkeypatch.setattr(
            cli,
            "_check_harness_integrity",
            lambda _m: (_ for _ in ()).throw(AssertionError("integrity must not run")),
        )
        monkeypatch.setattr(
            cli,
            "_run_presync_if_enabled",
            lambda: (_ for _ in ()).throw(AssertionError("pre-sync must not run")),
        )
        assert cli._check_orchestrator_startup_gates(_Path("/tmp/plugin")) == 7

    def test_integrity_failure_short_circuits_presync(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from pathlib import Path as _Path

        monkeypatch.setattr(cli, "_check_guard_hooks_registered", lambda _p: None)
        monkeypatch.setattr(cli, "_check_harness_integrity", lambda _m: 9)
        monkeypatch.setattr(
            cli,
            "_run_presync_if_enabled",
            lambda: (_ for _ in ()).throw(AssertionError("pre-sync must not run")),
        )
        assert cli._check_orchestrator_startup_gates(_Path("/tmp/plugin")) == 9

    def test_presync_failure_returned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from pathlib import Path as _Path

        monkeypatch.setattr(cli, "_check_guard_hooks_registered", lambda _p: None)
        monkeypatch.setattr(cli, "_check_harness_integrity", lambda _m: None)
        monkeypatch.setattr(cli, "_run_presync_if_enabled", lambda: 1)
        assert cli._check_orchestrator_startup_gates(_Path("/tmp/plugin")) == 1

    def test_all_pass_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from pathlib import Path as _Path

        monkeypatch.setattr(cli, "_check_guard_hooks_registered", lambda _p: None)
        monkeypatch.setattr(cli, "_check_harness_integrity", lambda _m: None)
        monkeypatch.setattr(cli, "_run_presync_if_enabled", lambda: None)
        assert cli._check_orchestrator_startup_gates(_Path("/tmp/plugin")) is None
