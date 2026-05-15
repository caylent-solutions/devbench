"""Tests for ``devbench.plugin_shadow`` (ADR-25, per-agent model overrides).

The module owns workspace-local shadow-plugin materialisation. Coverage gate
is 100% line + branch under ``make test-coverage-new``; every public helper
plus every private branch is exercised here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.config_loader import AgentModelsConfig, ReviewTeamModelsConfig
from devbench.constants import PLUGIN_SHADOW_DIR_NAME, SHADOW_PID_SENTINEL_FILENAME
from devbench.plugin_shadow import (
    _atomic_write,
    _collect_overrides,
    _is_pid_alive,
    _read_sentinel_pid,
    _rewrite_agent_model,
    _sentinel_path,
    clear_shadow_plugin,
    materialise_shadow_plugin,
    shadow_plugin_path,
    write_pid_sentinel,
)

# Identical-shape fragments of the canonical plugin tree. Pure fixture --
# nothing here depends on the real plugin so the tests cannot be polluted
# by a real-world plugin layout change. Real-tree behaviour is covered by
# the integration test at the end of this file.
_AGENT_MARKDOWN_TEMPLATE = (
    "---\n"
    "name: {name}\n"
    "description: {name} agent description.\n"
    "model: {model}\n"
    "tools: Bash\n"
    "---\n\n"
    "## Evidence\n\nbody content\n"
)

_ALL_AGENT_FILES: tuple[str, ...] = (
    "agents/executor.md",
    "agents/blocker-resolver.md",
    "agents/manifest-amender.md",
    "agents/security-reviewer.md",
    "agents/task-factory.md",
    "agents/review-supervisor.md",
    "agents/review_team/code-reviewer.md",
    "agents/review_team/test-reviewer.md",
    "agents/review_team/doc-reviewer.md",
    "agents/review_team/changes-manifest.md",
)


def _build_synthetic_plugin(root: Path) -> Path:
    """Materialise a fake canonical plugin tree under *root*.

    Returns the canonical plugin dir (``<root>/plugin/devbench``).
    """
    plugin_dir = root / "plugin" / "devbench"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / ".claude-plugin").mkdir()
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text('{"name": "devbench"}', encoding="utf-8")
    (plugin_dir / "hooks").mkdir()
    (plugin_dir / "hooks" / "hooks.json").write_text("{}", encoding="utf-8")
    (plugin_dir / "scripts").mkdir()
    (plugin_dir / "scripts" / "guard-bash.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (plugin_dir / "agents").mkdir()
    (plugin_dir / "agents" / "review_team").mkdir()
    for rel in _ALL_AGENT_FILES:
        target = plugin_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        name = target.stem
        if "review-supervisor" in rel:
            default_model = "haiku"
        elif rel.endswith("/executor.md"):
            default_model = "sonnet"
        else:
            default_model = "opus"
        target.write_text(
            _AGENT_MARKDOWN_TEMPLATE.format(name=name, model=default_model),
            encoding="utf-8",
        )
    return plugin_dir


class TestShadowPluginPath:
    """``shadow_plugin_path`` is a pure path computation."""

    def test_returns_workspace_relative_path(self, tmp_path: Path) -> None:
        path = shadow_plugin_path(tmp_path)
        assert path == tmp_path / ".devbench" / "plugin-shadow" / "devbench"

    def test_returns_same_path_regardless_of_existence(self, tmp_path: Path) -> None:
        # Function does not consult the filesystem.
        first = shadow_plugin_path(tmp_path)
        second = shadow_plugin_path(tmp_path)
        assert first == second
        assert not first.exists()


class TestClearShadowPlugin:
    """``clear_shadow_plugin`` is idempotent and returns whether work happened."""

    def test_returns_false_when_absent(self, tmp_path: Path) -> None:
        assert clear_shadow_plugin(tmp_path) is False

    def test_removes_existing_tree(self, tmp_path: Path) -> None:
        shadow = tmp_path / ".devbench" / "plugin-shadow" / "devbench" / "agents"
        shadow.mkdir(parents=True)
        (shadow / "executor.md").write_text("body", encoding="utf-8")
        assert clear_shadow_plugin(tmp_path) is True
        assert not (tmp_path / ".devbench" / "plugin-shadow").exists()

    def test_idempotent(self, tmp_path: Path) -> None:
        (tmp_path / ".devbench" / "plugin-shadow").mkdir(parents=True)
        assert clear_shadow_plugin(tmp_path) is True
        assert clear_shadow_plugin(tmp_path) is False


class TestRewriteAgentModel:
    """``_rewrite_agent_model`` is a pure substitution + validation."""

    def test_replaces_first_model_line(self) -> None:
        content = "---\nname: x\nmodel: sonnet\ntools: Bash\n---\nbody\n"
        out = _rewrite_agent_model(content, "opus")
        assert "model: opus\n" in out
        assert "model: sonnet" not in out

    def test_does_not_touch_body_text_mentioning_model(self) -> None:
        # Anchored regex must not match a body line that happens to start with
        # the word "model:" -- only the first frontmatter line.
        content = "---\nname: x\nmodel: sonnet\n---\n\nmodel: this-is-just-prose-do-not-rewrite\n"
        out = _rewrite_agent_model(content, "opus")
        assert "model: opus\n" in out
        assert "this-is-just-prose-do-not-rewrite" in out  # body untouched

    def test_raises_when_no_model_line(self) -> None:
        with pytest.raises(ValueError, match="no 'model:' frontmatter line"):
            _rewrite_agent_model("---\nname: x\n---\nbody\n", "opus")


class TestCollectOverrides:
    """``_collect_overrides`` flattens AgentModelsConfig into {rel_path: model}."""

    def test_empty_config_returns_empty(self) -> None:
        assert _collect_overrides(AgentModelsConfig()) == {}

    def test_top_level_fields(self) -> None:
        cfg = AgentModelsConfig(executor="opus", manifest_amender="haiku")
        out = _collect_overrides(cfg)
        assert out == {
            "agents/executor.md": "opus",
            "agents/manifest-amender.md": "haiku",
        }

    def test_review_team_fields(self) -> None:
        cfg = AgentModelsConfig(
            review_team=ReviewTeamModelsConfig(code_reviewer="opus", test_reviewer="haiku"),
        )
        out = _collect_overrides(cfg)
        assert out == {
            "agents/review_team/code-reviewer.md": "opus",
            "agents/review_team/test-reviewer.md": "haiku",
        }

    def test_mixed_top_and_review_team(self) -> None:
        cfg = AgentModelsConfig(
            executor="opus",
            blocker_resolver="sonnet",
            manifest_amender="opus",
            security_reviewer="haiku",
            task_factory="sonnet",
            review_supervisor="opus",
            review_team=ReviewTeamModelsConfig(
                code_reviewer="opus",
                test_reviewer="opus",
                doc_reviewer="haiku",
                changes_manifest="sonnet",
            ),
        )
        out = _collect_overrides(cfg)
        assert len(out) == 10
        assert out["agents/executor.md"] == "opus"
        assert out["agents/review_team/changes-manifest.md"] == "sonnet"


class TestAtomicWrite:
    """``_atomic_write`` writes via temp-then-rename and creates parents."""

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "c.md"
        _atomic_write(target, "hello\n")
        assert target.read_text(encoding="utf-8") == "hello\n"

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        target = tmp_path / "x.md"
        target.write_text("old", encoding="utf-8")
        _atomic_write(target, "new")
        assert target.read_text(encoding="utf-8") == "new"
        assert not (tmp_path / "x.md.tmp").exists()


class TestMaterialiseShadowPlugin:
    """Integration-level tests against a synthetic canonical tree."""

    def test_no_overrides_removes_shadow_and_returns_none(self, tmp_path: Path) -> None:
        plugin_dir = _build_synthetic_plugin(tmp_path)
        workspace = tmp_path / "ws"
        # Pre-existing shadow should be cleaned up.
        existing = workspace / ".devbench" / "plugin-shadow" / "devbench"
        existing.mkdir(parents=True)
        (existing / "stale.md").write_text("leftover", encoding="utf-8")

        result = materialise_shadow_plugin(plugin_dir, workspace, AgentModelsConfig())

        assert result is None
        assert not (workspace / ".devbench" / "plugin-shadow").exists()

    def test_missing_canonical_raises_filenotfound(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="Canonical plugin directory not found"):
            materialise_shadow_plugin(
                tmp_path / "does-not-exist",
                tmp_path / "ws",
                AgentModelsConfig(executor="opus"),
            )

    def test_executor_override_writes_real_file_and_symlinks_rest(self, tmp_path: Path) -> None:
        plugin_dir = _build_synthetic_plugin(tmp_path)
        workspace = tmp_path / "ws"
        cfg = AgentModelsConfig(executor="opus")

        shadow_root = materialise_shadow_plugin(plugin_dir, workspace, cfg)

        assert shadow_root == shadow_plugin_path(workspace)
        assert shadow_root.is_dir()
        # Executor was overridden -- must be a real file with the rewritten model line.
        executor = shadow_root / "agents" / "executor.md"
        assert not executor.is_symlink()
        assert "model: opus\n" in executor.read_text(encoding="utf-8")
        # Every other plugin file must be a symlink to the canonical.
        for rel in _ALL_AGENT_FILES:
            if rel == "agents/executor.md":
                continue
            assert (shadow_root / rel).is_symlink()
            assert (shadow_root / rel).resolve() == (plugin_dir / rel).resolve()
        assert (shadow_root / ".claude-plugin" / "plugin.json").is_symlink()
        assert (shadow_root / "hooks" / "hooks.json").is_symlink()
        assert (shadow_root / "scripts" / "guard-bash.sh").is_symlink()

    def test_review_team_override(self, tmp_path: Path) -> None:
        plugin_dir = _build_synthetic_plugin(tmp_path)
        workspace = tmp_path / "ws"
        cfg = AgentModelsConfig(
            review_team=ReviewTeamModelsConfig(code_reviewer="opus"),
        )

        shadow_root = materialise_shadow_plugin(plugin_dir, workspace, cfg)
        assert shadow_root is not None
        rewritten = shadow_root / "agents" / "review_team" / "code-reviewer.md"
        assert not rewritten.is_symlink()
        assert "model: opus\n" in rewritten.read_text(encoding="utf-8")
        # Sibling judge stays symlinked.
        assert (shadow_root / "agents" / "review_team" / "test-reviewer.md").is_symlink()

    def test_rebuilds_idempotently(self, tmp_path: Path) -> None:
        plugin_dir = _build_synthetic_plugin(tmp_path)
        workspace = tmp_path / "ws"
        cfg = AgentModelsConfig(executor="opus")

        first = materialise_shadow_plugin(plugin_dir, workspace, cfg)
        second = materialise_shadow_plugin(plugin_dir, workspace, cfg)

        assert first == second
        executor = (second or Path()) / "agents" / "executor.md"
        assert "model: opus\n" in executor.read_text(encoding="utf-8")

    def test_overrides_typo_rejected(self, tmp_path: Path) -> None:
        plugin_dir = _build_synthetic_plugin(tmp_path)
        # Delete one agent file so the override targets a missing path.
        (plugin_dir / "agents" / "executor.md").unlink()
        workspace = tmp_path / "ws"
        cfg = AgentModelsConfig(executor="opus")

        with pytest.raises(ValueError, match="does not exist under canonical plugin"):
            materialise_shadow_plugin(plugin_dir, workspace, cfg)

    def test_rewrite_failure_raises_through_materialise(self, tmp_path: Path) -> None:
        plugin_dir = _build_synthetic_plugin(tmp_path)
        # Replace executor with one lacking a model: line.
        (plugin_dir / "agents" / "executor.md").write_text("---\nname: x\n---\nbody\n", encoding="utf-8")
        workspace = tmp_path / "ws"
        cfg = AgentModelsConfig(executor="opus")

        with pytest.raises(ValueError, match="no 'model:' frontmatter line"):
            materialise_shadow_plugin(plugin_dir, workspace, cfg)

    def test_clears_stale_shadow_before_rebuild(self, tmp_path: Path) -> None:
        plugin_dir = _build_synthetic_plugin(tmp_path)
        workspace = tmp_path / "ws"
        stale = workspace / ".devbench" / "plugin-shadow" / "devbench" / "stale-marker.md"
        stale.parent.mkdir(parents=True)
        stale.write_text("delete-me", encoding="utf-8")

        materialise_shadow_plugin(plugin_dir, workspace, AgentModelsConfig(executor="opus"))

        assert not stale.exists()

    def test_against_real_canonical_plugin(self, tmp_path: Path) -> None:
        """Smoke test against the real canonical plugin shipped with this package.

        Guards against drift between ``_AGENT_FILES`` / ``_REVIEW_TEAM_FILES``
        in ``plugin_shadow`` and the actual plugin layout. The synthetic
        fixture cannot catch a rename in the real tree.
        """
        import devbench
        from devbench.constants import DEFAULT_PLUGIN_SUBPATH

        canonical = Path(devbench.__file__).parent.parent.parent / DEFAULT_PLUGIN_SUBPATH
        if not canonical.is_dir():
            pytest.skip("Canonical plugin not present in this environment")

        workspace = tmp_path / "real-ws"
        cfg = AgentModelsConfig(executor="opus")

        shadow_root = materialise_shadow_plugin(canonical, workspace, cfg)

        assert shadow_root is not None
        executor = shadow_root / "agents" / "executor.md"
        assert "model: opus\n" in executor.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# PID sentinel (ADR-25 sentinel-protected lifecycle)
# ---------------------------------------------------------------------------


def _build_shadow_for_sentinel_tests(tmp_path: Path) -> Path:
    """Create a minimal canonical plugin + materialise a shadow at tmp_path/ws.

    Returns the shadow_root path. Uses the executor: opus override so the
    shadow gets built (the only shape that matters for sentinel tests).
    """
    plugin_dir = _build_synthetic_plugin(tmp_path)
    workspace = tmp_path / "ws"
    cfg = AgentModelsConfig(executor="opus")
    shadow_root = materialise_shadow_plugin(plugin_dir, workspace, cfg)
    assert shadow_root is not None
    return workspace


class TestSentinelPath:
    """``_sentinel_path`` lives inside the shadow tree (so rmtree removes it)."""

    def test_path_format(self, tmp_path: Path) -> None:
        path = _sentinel_path(tmp_path)
        assert path == tmp_path / PLUGIN_SHADOW_DIR_NAME / "devbench" / SHADOW_PID_SENTINEL_FILENAME

    def test_pure_path_function(self, tmp_path: Path) -> None:
        # Function does not touch the filesystem.
        assert not _sentinel_path(tmp_path).exists()


class TestWritePidSentinel:
    """``write_pid_sentinel`` records the orchestrator PID inside the shadow tree."""

    def test_round_trip(self, tmp_path: Path) -> None:
        workspace = _build_shadow_for_sentinel_tests(tmp_path)
        write_pid_sentinel(workspace, 12345)
        assert _read_sentinel_pid(workspace) == 12345

    def test_overwrites_previous(self, tmp_path: Path) -> None:
        workspace = _build_shadow_for_sentinel_tests(tmp_path)
        write_pid_sentinel(workspace, 1)
        write_pid_sentinel(workspace, 2)
        assert _read_sentinel_pid(workspace) == 2

    def test_no_tmp_file_left_behind(self, tmp_path: Path) -> None:
        workspace = _build_shadow_for_sentinel_tests(tmp_path)
        write_pid_sentinel(workspace, 1)
        tmp = _sentinel_path(workspace).parent / (SHADOW_PID_SENTINEL_FILENAME + ".tmp")
        assert not tmp.exists()

    def test_raises_when_shadow_root_missing(self, tmp_path: Path) -> None:
        workspace = tmp_path / "no-shadow"
        with pytest.raises(FileNotFoundError, match="shadow plugin root"):
            write_pid_sentinel(workspace, 1)


class TestReadSentinelPid:
    """``_read_sentinel_pid`` returns None when absent and raises on corrupt."""

    def test_returns_none_when_absent(self, tmp_path: Path) -> None:
        assert _read_sentinel_pid(tmp_path) is None

    def test_returns_int_when_present(self, tmp_path: Path) -> None:
        workspace = _build_shadow_for_sentinel_tests(tmp_path)
        write_pid_sentinel(workspace, 99999)
        assert _read_sentinel_pid(workspace) == 99999

    def test_raises_on_corrupt(self, tmp_path: Path) -> None:
        workspace = _build_shadow_for_sentinel_tests(tmp_path)
        sentinel = _sentinel_path(workspace)
        sentinel.write_text("not-a-pid", encoding="utf-8")
        with pytest.raises(ValueError):
            _read_sentinel_pid(workspace)


class TestIsPidAlive:
    """``_is_pid_alive`` distinguishes live from dead PIDs."""

    def test_current_process_is_alive(self) -> None:
        import os

        assert _is_pid_alive(os.getpid()) is True

    def test_dead_pid_returns_false(self) -> None:
        # PID 1 is init (always alive on Linux). Use a clearly-out-of-range
        # PID instead. On Linux PIDs are bounded by /proc/sys/kernel/pid_max
        # (default 4_194_304). Picking a value above that guarantees
        # ProcessLookupError without any race with a real PID.
        assert _is_pid_alive(2**30) is False

    def test_permission_error_treated_as_alive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # When the calling uid lacks permission to signal the PID, os.kill
        # raises PermissionError -- the process exists (owned by another
        # uid) and must be treated as alive. The sentinel guard MUST refuse
        # to clear in that case.
        import os as _os

        def _raises_permission_error(*_args: object, **_kwargs: object) -> None:
            raise PermissionError("simulated cross-uid signal denial")

        monkeypatch.setattr(_os, "kill", _raises_permission_error)
        assert _is_pid_alive(1234) is True


class TestClearShadowPluginRefusesWhileRunning:
    """``clear_shadow_plugin`` fails fast when a live PID owns the shadow."""

    def test_refuses_when_sentinel_pid_alive(self, tmp_path: Path) -> None:
        import os

        workspace = _build_shadow_for_sentinel_tests(tmp_path)
        write_pid_sentinel(workspace, os.getpid())
        with pytest.raises(RuntimeError, match=f"PID {os.getpid()}"):
            clear_shadow_plugin(workspace)
        # Tree + sentinel both survive.
        assert (workspace / PLUGIN_SHADOW_DIR_NAME).exists()
        assert _read_sentinel_pid(workspace) == os.getpid()

    def test_succeeds_when_sentinel_pid_dead(self, tmp_path: Path) -> None:
        workspace = _build_shadow_for_sentinel_tests(tmp_path)
        write_pid_sentinel(workspace, 2**30)
        assert clear_shadow_plugin(workspace) is True
        assert not (workspace / PLUGIN_SHADOW_DIR_NAME).exists()

    def test_succeeds_when_no_sentinel(self, tmp_path: Path) -> None:
        # Existing shadow without a sentinel (e.g. a workspace materialised
        # before cmd_start had a chance to write the sentinel, or a
        # workspace that ran under a pre-sentinel devbench build) clears
        # cleanly because the guard only fires when a sentinel exists.
        workspace = _build_shadow_for_sentinel_tests(tmp_path)
        assert not _sentinel_path(workspace).exists()  # precondition
        assert clear_shadow_plugin(workspace) is True
        assert not (workspace / PLUGIN_SHADOW_DIR_NAME).exists()

    def test_corrupt_sentinel_propagates_value_error(self, tmp_path: Path) -> None:
        workspace = _build_shadow_for_sentinel_tests(tmp_path)
        _sentinel_path(workspace).write_text("garbage", encoding="utf-8")
        with pytest.raises(ValueError):
            clear_shadow_plugin(workspace)

    def test_materialise_refuses_to_rebuild_under_live_orchestrator(self, tmp_path: Path) -> None:
        # The materialiser's "rebuild" path goes through clear_shadow_plugin;
        # a live sentinel must block the rebuild too.
        import os

        plugin_dir = _build_synthetic_plugin(tmp_path)
        workspace = tmp_path / "ws"
        materialise_shadow_plugin(plugin_dir, workspace, AgentModelsConfig(executor="opus"))
        write_pid_sentinel(workspace, os.getpid())
        with pytest.raises(RuntimeError, match=f"PID {os.getpid()}"):
            materialise_shadow_plugin(plugin_dir, workspace, AgentModelsConfig(executor="haiku"))
