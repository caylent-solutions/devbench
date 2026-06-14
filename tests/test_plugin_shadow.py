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
    _fingerprint_path,
    _is_pid_alive,
    _live_owner_pids,
    _overrides_fingerprint,
    _read_fingerprint,
    _read_owner_pids,
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
    "agents/iac-deploy-reviewer.md",
    "agents/review_team/code-reviewer.md",
    "agents/review_team/test-reviewer.md",
    "agents/review_team/doc-reviewer.md",
    "agents/review_team/changes-manifest.md",
)

# Non-agent plugin files that must remain symlinks in the materialised shadow.
_NON_AGENT_FILES: tuple[str, ...] = (
    ".claude-plugin/plugin.json",
    "hooks/hooks.json",
    "scripts/guard-bash.sh",
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
        default_model = "sonnet" if "review-supervisor" in rel or rel.endswith("/executor.md") else "opus"
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
        cfg = AgentModelsConfig(executor="opus", manifest_amender="sonnet")
        out = _collect_overrides(cfg)
        assert out == {
            "agents/executor.md": "opus",
            "agents/manifest-amender.md": "sonnet",
        }

    def test_iac_deploy_reviewer_field(self) -> None:
        cfg = AgentModelsConfig(iac_deploy_reviewer="opus")
        out = _collect_overrides(cfg)
        assert out == {"agents/iac-deploy-reviewer.md": "opus"}

    def test_review_team_fields(self) -> None:
        cfg = AgentModelsConfig(
            review_team=ReviewTeamModelsConfig(code_reviewer="opus", test_reviewer="sonnet"),
        )
        out = _collect_overrides(cfg)
        assert out == {
            "agents/review_team/code-reviewer.md": "opus",
            "agents/review_team/test-reviewer.md": "sonnet",
        }

    def test_mixed_top_and_review_team(self) -> None:
        cfg = AgentModelsConfig(
            executor="opus",
            blocker_resolver="sonnet",
            manifest_amender="opus",
            security_reviewer="sonnet",
            task_factory="sonnet",
            review_supervisor="opus",
            review_team=ReviewTeamModelsConfig(
                code_reviewer="opus",
                test_reviewer="opus",
                doc_reviewer="sonnet",
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

    def test_executor_override_writes_real_files_and_symlinks_non_agents(self, tmp_path: Path) -> None:
        plugin_dir = _build_synthetic_plugin(tmp_path)
        workspace = tmp_path / "ws"
        cfg = AgentModelsConfig(executor="opus")

        shadow_root = materialise_shadow_plugin(plugin_dir, workspace, cfg)

        assert shadow_root == shadow_plugin_path(workspace)
        assert shadow_root.is_dir()
        # EVERY agent .md is a real file (never a symlink) so the Claude Agent
        # SDK registers every agent type. Overridden agents carry the rewritten
        # model; non-overridden agents preserve their canonical model line.
        for rel in _ALL_AGENT_FILES:
            materialised = shadow_root / rel
            assert materialised.is_file()
            assert not materialised.is_symlink()
        executor = shadow_root / "agents" / "executor.md"
        assert "model: opus\n" in executor.read_text(encoding="utf-8")
        # review-supervisor was NOT overridden -- its canonical model (sonnet in
        # the fixture) must survive verbatim, proving non-overridden agents are
        # copied (not rewritten, not symlinked).
        supervisor = shadow_root / "agents" / "review-supervisor.md"
        assert "model: sonnet\n" in supervisor.read_text(encoding="utf-8")
        # The optional iac_review judge must materialise as a real file too --
        # the registration bug this guards against (it was previously symlinked
        # and therefore never registered).
        iac = shadow_root / "agents" / "iac-deploy-reviewer.md"
        assert iac.is_file() and not iac.is_symlink()
        assert "model: opus\n" in iac.read_text(encoding="utf-8")
        # Non-agent plugin files must still be symlinks to the canonical tree.
        for rel in _NON_AGENT_FILES:
            link = shadow_root / rel
            assert link.is_symlink()
            assert link.resolve() == (plugin_dir / rel).resolve()

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
        # Sibling judge is a real file with its canonical model (opus in the
        # fixture) -- every agent .md is materialised, overridden or not.
        sibling = shadow_root / "agents" / "review_team" / "test-reviewer.md"
        assert not sibling.is_symlink()
        assert "model: opus\n" in sibling.read_text(encoding="utf-8")

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
        # Registration-bug guard: every agent .md in the REAL canonical tree
        # must materialise as a real file, not a symlink -- in particular the
        # optional iac_review judge, which is absent from the synthetic fixture's
        # historical override maps. A symlinked agent is not registered by the SDK.
        for agent_md in sorted((canonical / "agents").rglob("*.md")):
            rel = agent_md.relative_to(canonical)
            materialised = shadow_root / rel
            assert materialised.is_file(), f"missing agent file in shadow: {rel}"
            assert not materialised.is_symlink(), f"agent file must be a real file, not a symlink: {rel}"
        iac = shadow_root / "agents" / "iac-deploy-reviewer.md"
        assert iac.is_file() and not iac.is_symlink()


# ---------------------------------------------------------------------------
# PID sentinel (ADR-25 sentinel-protected lifecycle)
# ---------------------------------------------------------------------------


def _build_shadow_for_sentinel_tests(tmp_path: Path) -> Path:
    """Create a minimal canonical plugin + materialise a shadow at tmp_path/ws.

    Returns the workspace path. Uses the executor: opus override so the
    shadow gets built (the only shape that matters for sentinel tests).

    ``materialise_shadow_plugin`` auto-registers the *current* (test) process
    as an owner. Sentinel tests want to drive ownership explicitly, so the
    auto-registered owner sentinel is removed here, leaving a shadow tree with
    no recorded owner -- the clean starting state these tests assume.
    """
    plugin_dir = _build_synthetic_plugin(tmp_path)
    workspace = tmp_path / "ws"
    cfg = AgentModelsConfig(executor="opus")
    shadow_root = materialise_shadow_plugin(plugin_dir, workspace, cfg)
    assert shadow_root is not None
    sentinel = _sentinel_path(workspace)
    if sentinel.exists():
        sentinel.unlink()
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
    """``write_pid_sentinel`` registers an owner PID inside the shadow tree (multi-owner)."""

    def test_round_trip(self, tmp_path: Path) -> None:
        workspace = _build_shadow_for_sentinel_tests(tmp_path)
        write_pid_sentinel(workspace, 12345)
        assert _read_owner_pids(workspace) == {12345}

    def test_accumulates_multiple_live_owners(self, tmp_path: Path) -> None:
        # Two distinct LIVE owners both register and both survive: the second
        # session shares the shadow rather than evicting the first (the
        # multi-owner property the concurrent-session fix relies on).
        import os

        workspace = _build_shadow_for_sentinel_tests(tmp_path)
        write_pid_sentinel(workspace, os.getpid())
        write_pid_sentinel(workspace, os.getppid())
        assert {os.getpid(), os.getppid()} <= _read_owner_pids(workspace)

    def test_prunes_dead_owners_on_write(self, tmp_path: Path) -> None:
        # A dead PID already recorded is pruned when a new owner registers, so
        # the sentinel does not accumulate stale PIDs across runs.
        import os

        workspace = _build_shadow_for_sentinel_tests(tmp_path)
        _sentinel_path(workspace).write_text(f"{2**30}\n", encoding="utf-8")
        write_pid_sentinel(workspace, os.getpid())
        assert _read_owner_pids(workspace) == {os.getpid()}

    def test_no_tmp_file_left_behind(self, tmp_path: Path) -> None:
        workspace = _build_shadow_for_sentinel_tests(tmp_path)
        write_pid_sentinel(workspace, 1)
        tmp = _sentinel_path(workspace).parent / (SHADOW_PID_SENTINEL_FILENAME + ".tmp")
        assert not tmp.exists()

    def test_raises_when_shadow_root_missing(self, tmp_path: Path) -> None:
        workspace = tmp_path / "no-shadow"
        with pytest.raises(FileNotFoundError, match="shadow plugin root"):
            write_pid_sentinel(workspace, 1)


class TestReadOwnerPids:
    """``_read_owner_pids`` returns the owner set; empty when absent, raises on corrupt."""

    def test_returns_empty_when_absent(self, tmp_path: Path) -> None:
        assert _read_owner_pids(tmp_path) == set()

    def test_returns_set_when_present(self, tmp_path: Path) -> None:
        workspace = _build_shadow_for_sentinel_tests(tmp_path)
        write_pid_sentinel(workspace, 99999)
        assert _read_owner_pids(workspace) == {99999}

    def test_raises_on_corrupt(self, tmp_path: Path) -> None:
        workspace = _build_shadow_for_sentinel_tests(tmp_path)
        sentinel = _sentinel_path(workspace)
        sentinel.write_text("not-a-pid", encoding="utf-8")
        with pytest.raises(ValueError):
            _read_owner_pids(workspace)

    def test_live_owner_pids_prunes_dead(self, tmp_path: Path) -> None:
        import os

        workspace = _build_shadow_for_sentinel_tests(tmp_path)
        _sentinel_path(workspace).write_text(f"{os.getpid()}\n{2**30}\n", encoding="utf-8")
        assert _live_owner_pids(workspace) == {os.getpid()}


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
    """``clear_shadow_plugin`` fails fast when ANY live PID owns the shadow."""

    def test_refuses_when_sentinel_pid_alive(self, tmp_path: Path) -> None:
        import os

        workspace = _build_shadow_for_sentinel_tests(tmp_path)
        write_pid_sentinel(workspace, os.getpid())
        with pytest.raises(RuntimeError, match=f"PID {os.getpid()}"):
            clear_shadow_plugin(workspace)
        # Tree + sentinel both survive.
        assert (workspace / PLUGIN_SHADOW_DIR_NAME).exists()
        assert _read_owner_pids(workspace) == {os.getpid()}

    def test_refuses_when_any_owner_alive_among_several(self, tmp_path: Path) -> None:
        # Stray-clear guard with multiple owners: one dead, one live -> still
        # refused (AC-4). The live sibling must not lose its shadow.
        import os

        workspace = _build_shadow_for_sentinel_tests(tmp_path)
        _sentinel_path(workspace).write_text(f"{2**30}\n{os.getpid()}\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match=f"PID {os.getpid()}"):
            clear_shadow_plugin(workspace)
        assert (workspace / PLUGIN_SHADOW_DIR_NAME).exists()

    def test_succeeds_when_all_owners_dead(self, tmp_path: Path) -> None:
        workspace = _build_shadow_for_sentinel_tests(tmp_path)
        write_pid_sentinel(workspace, 2**30)
        assert clear_shadow_plugin(workspace) is True
        assert not (workspace / PLUGIN_SHADOW_DIR_NAME).exists()

    def test_succeeds_when_no_sentinel(self, tmp_path: Path) -> None:
        # Existing shadow without a sentinel (e.g. a workspace materialised
        # before cmd_start had a chance to write the sentinel, or a
        # workspace that ran under a pre-sentinel devbench build) clears
        # cleanly because the guard only fires when a live owner exists.
        workspace = _build_shadow_for_sentinel_tests(tmp_path)
        assert not _sentinel_path(workspace).exists()  # precondition
        assert clear_shadow_plugin(workspace) is True
        assert not (workspace / PLUGIN_SHADOW_DIR_NAME).exists()

    def test_corrupt_sentinel_propagates_value_error(self, tmp_path: Path) -> None:
        workspace = _build_shadow_for_sentinel_tests(tmp_path)
        _sentinel_path(workspace).write_text("garbage", encoding="utf-8")
        with pytest.raises(ValueError):
            clear_shadow_plugin(workspace)


class TestMaterialiseShadowPluginReentrant:
    """Reentrancy / multi-session reuse (concurrent-session fix)."""

    def test_second_session_reuses_identical_shadow_no_rebuild(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # AC-1 / AC-2: a sibling session whose PID is already a live owner with
        # the SAME overrides REUSES the existing shadow -- no clear, no rebuild,
        # no RuntimeError. Regression: on the pre-fix code the unconditional
        # clear raised because the sentinel named a live process.
        import os

        plugin_dir = _build_synthetic_plugin(tmp_path)
        workspace = tmp_path / "ws"
        cfg = AgentModelsConfig(executor="opus")
        shadow_root = materialise_shadow_plugin(plugin_dir, workspace, cfg)
        assert shadow_root is not None

        # Simulate a LIVE sibling owner already holding the shadow.
        sibling_pid = os.getppid()
        write_pid_sentinel(workspace, sibling_pid)

        # Assert the rebuild path is NOT taken: monkeypatch clear_shadow_plugin
        # to fail the test if called, and rglob (the rebuild walk) likewise.
        import devbench.plugin_shadow as ps

        def _no_clear(_ws: Path) -> bool:
            raise AssertionError("clear_shadow_plugin must not be called on the reuse path")

        monkeypatch.setattr(ps, "clear_shadow_plugin", _no_clear)

        # Second concurrent session, identical overrides -> reuse.
        reused = materialise_shadow_plugin(plugin_dir, workspace, AgentModelsConfig(executor="opus"))
        assert reused == shadow_root
        # Both the sibling and this process are recorded owners.
        assert {sibling_pid, os.getpid()} <= _read_owner_pids(workspace)

    def test_reuse_registers_additional_owner(self, tmp_path: Path) -> None:
        # AC-1: the reusing session registers as an ADDITIONAL owner without
        # evicting the sibling.
        import os

        plugin_dir = _build_synthetic_plugin(tmp_path)
        workspace = tmp_path / "ws"
        materialise_shadow_plugin(plugin_dir, workspace, AgentModelsConfig(executor="opus"))
        sibling_pid = os.getppid()
        write_pid_sentinel(workspace, sibling_pid)
        materialise_shadow_plugin(plugin_dir, workspace, AgentModelsConfig(executor="opus"))
        assert {sibling_pid, os.getpid()} <= _read_owner_pids(workspace)

    def test_fingerprint_mismatch_with_live_owner_fails_fast(self, tmp_path: Path) -> None:
        # AC-3: overrides differ AND a live owner holds the shadow -> fail fast
        # naming the owner; the existing shadow is NOT cleared.
        import os

        plugin_dir = _build_synthetic_plugin(tmp_path)
        workspace = tmp_path / "ws"
        materialise_shadow_plugin(plugin_dir, workspace, AgentModelsConfig(executor="opus"))
        write_pid_sentinel(workspace, os.getpid())
        fp_before = _read_fingerprint(workspace)
        with pytest.raises(RuntimeError, match=f"PID {os.getpid()}"):
            materialise_shadow_plugin(plugin_dir, workspace, AgentModelsConfig(executor="sonnet"))
        # Shadow + fingerprint unchanged: no clobber under the live sibling.
        assert _read_fingerprint(workspace) == fp_before

    def test_fingerprint_mismatch_no_live_owner_rebuilds(self, tmp_path: Path) -> None:
        # When overrides differ but NO live owner remains (the orchestrator
        # exited), the stale shadow is cleared + rebuilt for the new overrides.
        plugin_dir = _build_synthetic_plugin(tmp_path)
        workspace = tmp_path / "ws"
        materialise_shadow_plugin(plugin_dir, workspace, AgentModelsConfig(executor="opus"))
        # Replace the live owner with a dead PID -> stale, reclaimable.
        _sentinel_path(workspace).write_text(f"{2**30}\n", encoding="utf-8")
        fp_opus = _read_fingerprint(workspace)
        materialise_shadow_plugin(plugin_dir, workspace, AgentModelsConfig(executor="sonnet"))
        assert _read_fingerprint(workspace) != fp_opus

    def test_fingerprint_written_on_build(self, tmp_path: Path) -> None:
        plugin_dir = _build_synthetic_plugin(tmp_path)
        workspace = tmp_path / "ws"
        materialise_shadow_plugin(plugin_dir, workspace, AgentModelsConfig(executor="opus"))
        assert _fingerprint_path(workspace).is_file()
        assert _read_fingerprint(workspace) == _overrides_fingerprint({"agents/executor.md": "opus"})

    def test_overrides_fingerprint_is_order_independent(self) -> None:
        a = _overrides_fingerprint({"agents/executor.md": "opus", "agents/task-factory.md": "sonnet"})
        b = _overrides_fingerprint({"agents/task-factory.md": "sonnet", "agents/executor.md": "opus"})
        assert a == b

    def test_build_auto_registers_current_process(self, tmp_path: Path) -> None:
        import os

        plugin_dir = _build_synthetic_plugin(tmp_path)
        workspace = tmp_path / "ws"
        materialise_shadow_plugin(plugin_dir, workspace, AgentModelsConfig(executor="opus"))
        assert os.getpid() in _read_owner_pids(workspace)
