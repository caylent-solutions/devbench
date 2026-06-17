"""Pre-sync (warm-up) of each configured target repo environment at start.

TDI #016: a COLD ``uv`` environment makes the FIRST ``uv run pytest ...`` in a
checkout spend minutes syncing dependencies from ``uv.lock``. When that sync
exceeds the per-attempt test timeout the attempt is recorded as a test failure
and (compounded) trips the within-claim convergence tracker, falsely blocking a
structurally-correct unit.

The cleanest agnostic fix is to PROVISION each configured repo's environment
ONCE at orchestrator start -- before the orchestrate loop claims any work -- so
no claim ever pays the cold-sync cost inside a timed attempt.

``_presync_target_environments`` is a pure function driven with an injected
command-runner double, so these tests never shell out.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from devbench import cli
from devbench.cli import _presync_target_environments
from devbench.config_loader import RepoConfig


class _RecordingRunner:
    """Injected command-runner double recording every (cmd, cwd) invocation."""

    def __init__(self, *, returncode: int = 0, stderr: str = "") -> None:
        self.calls: list[tuple[list[str], Path | None]] = []
        self._returncode = returncode
        self._stderr = stderr

    def __call__(
        self,
        cmd: list[str],
        cwd: Path | None = None,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
    ) -> tuple[int, str, str]:
        self.calls.append((cmd, cwd))
        return self._returncode, "", self._stderr


def _repo(name: str, path: Path) -> RepoConfig:
    return RepoConfig(validated_repo=name, resolved_checkout_path=path)


# ---------------------------------------------------------------------------
# _presync_target_environments: runs the command once per configured repo
# ---------------------------------------------------------------------------


class TestPresyncTargetEnvironments:
    def test_runs_command_once_per_configured_repo(self, tmp_path: Path) -> None:
        a = tmp_path / "repo-a"
        b = tmp_path / "repo-b"
        a.mkdir()
        b.mkdir()
        repos = {"org/repo-a": _repo("org/repo-a", a), "org/repo-b": _repo("org/repo-b", b)}
        runner = _RecordingRunner()

        _presync_target_environments(repos, command=["uv", "sync"], runner=runner, timeout=900)

        # Exactly one provisioning invocation per configured repo, each cwd'd to
        # that repo's resolved checkout path.
        assert len(runner.calls) == 2
        assert {cwd for _, cwd in runner.calls} == {a, b}
        assert all(cmd == ["uv", "sync"] for cmd, _ in runner.calls)

    def test_runs_before_any_claim_using_configured_command(self, tmp_path: Path) -> None:
        a = tmp_path / "repo-a"
        a.mkdir()
        repos = {"org/repo-a": _repo("org/repo-a", a)}
        runner = _RecordingRunner()

        _presync_target_environments(repos, command=["uv", "sync", "--frozen"], runner=runner, timeout=900)

        assert runner.calls == [(["uv", "sync", "--frozen"], a)]

    def test_warm_env_is_a_fast_noop_success(self, tmp_path: Path) -> None:
        # A warm env: ``uv sync`` returns 0 quickly. The helper succeeds and does
        # NOT raise -- the idempotent provisioning command is the no-op-fast path.
        a = tmp_path / "repo-a"
        a.mkdir()
        repos = {"org/repo-a": _repo("org/repo-a", a)}
        runner = _RecordingRunner(returncode=0)

        # Must not raise.
        _presync_target_environments(repos, command=["uv", "sync"], runner=runner, timeout=900)
        assert len(runner.calls) == 1

    def test_failed_presync_fails_fast(self, tmp_path: Path) -> None:
        # A real provisioning failure must surface loudly at start (fail-fast),
        # not silently inside a timed claim attempt.
        a = tmp_path / "repo-a"
        a.mkdir()
        repos = {"org/repo-a": _repo("org/repo-a", a)}
        runner = _RecordingRunner(returncode=1, stderr="No solution found for uv.lock")

        with pytest.raises(cli.PresyncError) as exc:
            _presync_target_environments(repos, command=["uv", "sync"], runner=runner, timeout=900)
        assert "org/repo-a" in str(exc.value)

    def test_no_repos_is_a_noop(self) -> None:
        runner = _RecordingRunner()
        _presync_target_environments({}, command=["uv", "sync"], runner=runner, timeout=900)
        assert runner.calls == []

    def test_skips_repo_without_resolved_path(self, tmp_path: Path) -> None:
        # A repo with no resolved checkout path cannot be provisioned; the helper
        # raises rather than silently running the command in an unknown cwd.
        repos = {"org/repo-a": RepoConfig(validated_repo="org/repo-a", resolved_checkout_path=None)}
        runner = _RecordingRunner()
        with pytest.raises(cli.PresyncError):
            _presync_target_environments(repos, command=["uv", "sync"], runner=runner, timeout=900)


# ---------------------------------------------------------------------------
# Config resolvers (env > YAML > default)
# ---------------------------------------------------------------------------


class TestResolvePresyncEnvironment:
    def test_default_when_unset(self, monkeypatch: Any) -> None:
        from devbench.constants import DEFAULT_PRESYNC_ENVIRONMENT

        monkeypatch.delenv("DEVBENCH_ORCHESTRATOR_PRESYNC_ENVIRONMENT", raising=False)
        monkeypatch.setattr(cli.RUNTIME_CONFIG.orchestrate, "presync_environment", None)
        assert cli._resolve_presync_environment() == DEFAULT_PRESYNC_ENVIRONMENT

    def test_env_disables(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_PRESYNC_ENVIRONMENT", "off")
        assert cli._resolve_presync_environment() is False

    def test_env_overrides_yaml(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(cli.RUNTIME_CONFIG.orchestrate, "presync_environment", False)
        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_PRESYNC_ENVIRONMENT", "on")
        assert cli._resolve_presync_environment() is True

    def test_yaml_overrides_default(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("DEVBENCH_ORCHESTRATOR_PRESYNC_ENVIRONMENT", raising=False)
        monkeypatch.setattr(cli.RUNTIME_CONFIG.orchestrate, "presync_environment", False)
        assert cli._resolve_presync_environment() is False


class TestResolvePresyncCommand:
    def test_default_when_unset(self, monkeypatch: Any) -> None:
        from devbench.constants import DEFAULT_PRESYNC_COMMAND

        monkeypatch.delenv("DEVBENCH_ORCHESTRATOR_PRESYNC_COMMAND", raising=False)
        monkeypatch.setattr(cli.RUNTIME_CONFIG.orchestrate, "presync_command", None)
        assert cli._resolve_presync_command() == list(DEFAULT_PRESYNC_COMMAND)

    def test_env_override_is_tokenised(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_PRESYNC_COMMAND", "uv sync --frozen")
        assert cli._resolve_presync_command() == ["uv", "sync", "--frozen"]

    def test_yaml_overrides_default(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("DEVBENCH_ORCHESTRATOR_PRESYNC_COMMAND", raising=False)
        monkeypatch.setattr(cli.RUNTIME_CONFIG.orchestrate, "presync_command", ["make", "deps"])
        assert cli._resolve_presync_command() == ["make", "deps"]


class TestResolvePresyncTimeout:
    def test_default_when_unset(self, monkeypatch: Any) -> None:
        from devbench.constants import DEFAULT_PRESYNC_TIMEOUT_SECONDS

        monkeypatch.delenv("DEVBENCH_ORCHESTRATOR_PRESYNC_TIMEOUT_SECONDS", raising=False)
        assert cli._resolve_presync_timeout_seconds() == DEFAULT_PRESYNC_TIMEOUT_SECONDS

    def test_env_override(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_PRESYNC_TIMEOUT_SECONDS", "1800")
        assert cli._resolve_presync_timeout_seconds() == 1800


class TestPresyncDefaultsAreSane:
    def test_default_command_is_uv_sync(self) -> None:
        from devbench.constants import DEFAULT_PRESYNC_COMMAND

        assert DEFAULT_PRESYNC_COMMAND[0] == "uv"
        assert "sync" in DEFAULT_PRESYNC_COMMAND

    def test_default_enabled(self) -> None:
        from devbench.constants import DEFAULT_PRESYNC_ENVIRONMENT

        assert DEFAULT_PRESYNC_ENVIRONMENT is True

    def test_default_timeout_is_generous(self) -> None:
        from devbench.constants import DEFAULT_PRESYNC_TIMEOUT_SECONDS

        # A cold sync can take minutes; the warm-up budget must be generous.
        assert DEFAULT_PRESYNC_TIMEOUT_SECONDS >= 300


# ---------------------------------------------------------------------------
# cmd_start wiring: _run_presync_if_enabled gates + delegates
# ---------------------------------------------------------------------------


class TestRunPresyncIfEnabled:
    def test_disabled_is_a_noop_returning_none(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_PRESYNC_ENVIRONMENT", "off")
        called = False

        def _boom(*_a: Any, **_k: Any) -> None:
            nonlocal called
            called = True

        monkeypatch.setattr(cli, "_presync_target_environments", _boom)
        assert cli._run_presync_if_enabled() is None
        assert called is False, "pre-sync must not run when the feature is disabled"

    def test_enabled_delegates_to_presync_then_returns_none(self, monkeypatch: Any, tmp_path: Path) -> None:
        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_PRESYNC_ENVIRONMENT", "on")
        repos = {"org/repo-a": _repo("org/repo-a", tmp_path / "a")}
        monkeypatch.setattr(cli.RUNTIME_CONFIG, "repos", repos)
        seen: dict[str, Any] = {}

        def _capture(passed_repos: Any, *, command: Any, runner: Any, timeout: Any) -> None:
            seen["repos"] = passed_repos
            seen["command"] = command
            seen["timeout"] = timeout

        monkeypatch.setattr(cli, "_presync_target_environments", _capture)
        assert cli._run_presync_if_enabled() is None
        assert seen["repos"] is repos
        assert seen["command"][0] == "uv"

    def test_no_repos_is_a_noop(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_PRESYNC_ENVIRONMENT", "on")
        monkeypatch.setattr(cli.RUNTIME_CONFIG, "repos", {})
        called = False

        def _boom(*_a: Any, **_k: Any) -> None:
            nonlocal called
            called = True

        monkeypatch.setattr(cli, "_presync_target_environments", _boom)
        assert cli._run_presync_if_enabled() is None
        assert called is False

    def test_presync_failure_returns_rc_1(self, monkeypatch: Any, tmp_path: Path) -> None:
        monkeypatch.setenv("DEVBENCH_ORCHESTRATOR_PRESYNC_ENVIRONMENT", "on")
        monkeypatch.setattr(cli.RUNTIME_CONFIG, "repos", {"org/repo-a": _repo("org/repo-a", tmp_path / "a")})

        def _fail(*_a: Any, **_k: Any) -> None:
            raise cli.PresyncError("pre-sync of 'org/repo-a' failed")

        monkeypatch.setattr(cli, "_presync_target_environments", _fail)
        assert cli._run_presync_if_enabled() == 1
