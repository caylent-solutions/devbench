"""Hermetic shared-file-impact journey suite (E5-F3-S1-T1).

`cmd_check_shared_file_impact` computes a trustworthy verdict (E5-F1, E5-F2) but,
before this task, nothing in the system required it to run (finding 318-D15) and
it carried no `docs/cli-reference.md` entry at all (spec Section 8). This module
proves the now-wired gate end to end over real, hermetic git fixture repos: block, pass,
disabled, waiver, stale-record and attribution journeys, plus this gate's own
adversarial fixtures -- a pre-existing failure that must not block versus an
introduced one that must, a corrupt baseline that must loud-fail, and an
auto-derived registry that must yield the expected shared set.

Fixture idiom: the git-fixture factory (`_shared_file_impact_git_fixture`), the
config-fixture writer (`_write_shared_file_impact_gate_config`), the
`RuntimeConfig` builder (`_shared_file_impact_runtime_cfg`), the work-unit
builder (`_shared_file_impact_unit`) and the Manifest-seeding helpers
(`_seed_scope_backlog`, `_seed_gate_done_path_backlog`) are all
imported from `tests/test_cli.py` -- the SAME scratch-git-fixture factory
`tests/test_cli.py::TestCheckSharedFileImpactBaseline` already exercises at the
unit level, and the same shape `tests/test_tdd_gate.py`'s own scratch-repo
factory uses (real `git init`/`commit`/`branch`, never a mocked subprocess) --
never hand-rolled again here.

"Real CLI" means the actual, unmocked `devbench.cli.cmd_check_shared_file_impact`,
`devbench.cli.cmd_mark_done` and `devbench.cli.cmd_log_waiver` implementations --
the same functions the `devbench` executable dispatches to. No journey mocks
`_evaluate_shared_file_gate`, `_derive_shared_file_registry`, or any other
gate-internal function; the only subprocess boundary crossed is the real `git`
and `pytest` binaries invoked against the real, on-disk fixture repos this
module builds.

Two config sources are patched for every `check-shared-file-impact` call,
mirroring `cmd_check_shared_file_impact`'s own two-source read (its `patterns`
field has no project/env layer of its own -- spec 4.1 -- so it is read directly
off `RUNTIME_CONFIG.gates.repos`, while `enabled`/`auto_derive_registry`/
`fan_in_threshold` are read through `_load_gate_config_or_report`'s real,
on-disk config-file load): `DEVBENCH_CONFIG_PATH` (env var, real YAML file) for
the latter, `devbench.cli.RUNTIME_CONFIG` (module patch) for the former. A
separate `devbench.config.RUNTIME_CONFIG` patch backs `mark-done`/`log-waiver`
calls, since `BacklogManager._check_gate_pass_done_invariant` reads gate config
through that module instead.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from test_cli import (
    _SHARED_FILE_IMPACT_REPO,
    _seed_gate_done_path_backlog,
    _shared_file_impact_git_fixture,
    _shared_file_impact_runtime_cfg,
    _shared_file_impact_unit,
    _write_shared_file_impact_gate_config,
)

from devbench import cli

_REPO = _SHARED_FILE_IMPACT_REPO


class _JourneyFixtures:
    """Shared `check-shared-file-impact` / `mark-done` / `log-waiver` runners.

    Mirrors `tests/test_integration/test_gate_reachability_e2e.py::_JourneyFixtures`'s
    role for the reachability gate: one place that knows the full patch surface
    each of the three real CLI entry points needs, so individual journey test
    methods only assemble a fixture repo and a Changes Manifest, never a patch
    list of their own.
    """

    REPO = _REPO

    def _run(
        self,
        unit_id: str,
        repo: Path,
        backlog_root: Path,
        backlog_index: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        workspace: Path,
        patterns: tuple[str, ...] = ("tests/test_suite.py",),
        enabled: bool = True,
        auto_derive_registry: bool = False,
        fan_in_threshold: int = 3,
    ) -> int:
        unit = _shared_file_impact_unit(unit_id)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        cfg_path = _write_shared_file_impact_gate_config(
            workspace, enabled=enabled, auto_derive_registry=auto_derive_registry, fan_in_threshold=fan_in_threshold
        )
        monkeypatch.setenv("DEVBENCH_CONFIG_PATH", str(cfg_path))
        # The env layer is highest-precedence (spec 4.1, D-15): an ambient
        # DEVBENCH_GATE_SHARED_FILE_IMPACT_ENABLED left set by the host shell must
        # never leak into a journey that relies on the project-layer config above.
        monkeypatch.delenv("DEVBENCH_GATE_SHARED_FILE_IMPACT_ENABLED", raising=False)
        runtime_cfg = _shared_file_impact_runtime_cfg(
            patterns=patterns,
            default_branch="main",
            auto_derive_registry=auto_derive_registry,
            fan_in_threshold=fan_in_threshold,
        )
        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {self.REPO: repo}),
            patch("devbench.cli.RUNTIME_CONFIG", runtime_cfg),
            patch("devbench.cli.WORKSPACE_ROOT", workspace),
            patch("devbench.cli.BACKLOG_ROOT", backlog_root.parent),
            patch("devbench.work_unit_scope.BACKLOG_ROOT", backlog_root),
            patch("devbench.work_unit_scope.BACKLOG_INDEX", backlog_index),
        ):
            return cli.cmd_check_shared_file_impact(unit_id)

    def _mark_done_patches(
        self,
        repo: Path,
        backlog_root: Path,
        backlog_index: Path,
        unit_id: str,
        *,
        gate_enabled: bool,
    ) -> tuple[Any, ...]:
        from devbench.config_loader import GatesConfig, GateSharedFileImpactConfig, RepoConfig, RuntimeConfig

        unit = _shared_file_impact_unit(unit_id)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        runtime_config = RuntimeConfig(
            repos={self.REPO: RepoConfig(resolved_checkout_path=repo)},
            gates=GatesConfig(shared_file_impact=GateSharedFileImpactConfig(enabled=gate_enabled)),
        )
        return (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {self.REPO: repo}),
            patch("devbench.cli.BACKLOG_ROOT", backlog_root.parent),
            patch("devbench.cli.WORKSPACE_ROOT", backlog_root.parent),
            patch("devbench.cli.BACKLOG_INDEX", backlog_index),
            patch("devbench.work_unit_scope.BACKLOG_ROOT", backlog_root),
            patch("devbench.work_unit_scope.BACKLOG_INDEX", backlog_index),
            patch("devbench.config.RUNTIME_CONFIG", runtime_config),
        )

    def _mark_done(
        self,
        unit_id: str,
        repo: Path,
        backlog_root: Path,
        backlog_index: Path,
        *,
        gate_enabled: bool = True,
    ) -> int:
        patches = self._mark_done_patches(repo, backlog_root, backlog_index, unit_id, gate_enabled=gate_enabled)
        with contextlib.ExitStack() as stack:
            for one_patch in patches:
                stack.enter_context(one_patch)
            return cli.cmd_mark_done(unit_id)

    def _log_waiver(
        self,
        unit_id: str,
        repo: Path,
        backlog_root: Path,
        backlog_index: Path,
        *,
        target: str,
        reason: str,
    ) -> int:
        patches = self._mark_done_patches(repo, backlog_root, backlog_index, unit_id, gate_enabled=True)
        with contextlib.ExitStack() as stack:
            for one_patch in patches:
                stack.enter_context(one_patch)
            return cli.cmd_log_waiver(
                "code_review",
                unit_id,
                "--gate",
                "shared_file_impact",
                "--target",
                target,
                "--reason",
                reason,
                "--operator",
            )


class TestJourneyBlocksIntroducedFailure(_JourneyFixtures):
    """Block journey (spec 4.6, AC-14, AC-16): a new full-suite failure the unit's
    own diff introduces makes both `check-shared-file-impact` and the `mark-done`
    gate it feeds refuse, with no `[GATE_PASS shared_file_impact]` record written."""

    def test_introduced_failure_blocks_check_and_mark_done(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        workspace = tmp_path / "workspace"
        base_content = "def test_ok():\n    assert True\n"
        feature_content = "def test_ok():\n    assert True\n\n\ndef test_new_fail():\n    assert False\n"
        repo, _base_sha = _shared_file_impact_git_fixture(
            tmp_path, base_test_content=base_content, feature_test_content=feature_content
        )
        backlog_root, backlog_index, wu_file = _seed_gate_done_path_backlog(
            tmp_path,
            unit_id,
            self.REPO,
            ("tests/test_suite.py",),
            "End to end shared-file-impact cycle test",
        )

        checked = self._run(unit_id, repo, backlog_root, backlog_index, monkeypatch, workspace=workspace)
        err = capsys.readouterr().err
        assert checked == 1
        assert "tests/test_suite.py::test_new_fail" in err
        assert "[GATE_PASS shared_file_impact]" not in wu_file.read_text(encoding="utf-8")

        blocked = self._mark_done(unit_id, repo, backlog_root, backlog_index)
        assert blocked == 1
        assert "## Status: done" not in wu_file.read_text(encoding="utf-8")


class TestJourneyPassWritesGateRecordAndUnblocksDone(_JourneyFixtures):
    """Pass journey (spec 4.2/4.6, AC-3, AC-16): a no-match run writes exactly one
    `[GATE_PASS shared_file_impact]` record, and `mark-done` then proceeds."""

    def test_no_match_run_passes_writes_record_and_unblocks_done(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        workspace = tmp_path / "workspace"
        repo, _base_sha = _shared_file_impact_git_fixture(
            tmp_path, base_test_content="def test_ok():\n    assert True\n", feature_test_content=None
        )
        # The Manifest-listed file must exist on disk: mark-done's generic scope-hash
        # recompute (`BacklogManager._git_blob_hash`) requires `git hash-object` to
        # succeed against it, unlike `work_unit_scope`'s tolerant absent-file marker.
        (repo / "src").mkdir(parents=True, exist_ok=True)
        (repo / "src" / "unrelated.py").write_text("# unrelated to the shared-file pattern\n", encoding="utf-8")
        backlog_root, backlog_index, wu_file = _seed_gate_done_path_backlog(
            tmp_path,
            unit_id,
            self.REPO,
            ("src/unrelated.py",),
            "End to end shared-file-impact cycle test",
        )

        checked = self._run(
            unit_id,
            repo,
            backlog_root,
            backlog_index,
            monkeypatch,
            workspace=workspace,
            patterns=("tests/test_suite.py",),
        )
        capsys.readouterr()
        content_after_check = wu_file.read_text(encoding="utf-8")
        assert checked == 0
        assert content_after_check.count("[GATE_PASS shared_file_impact]") == 1

        done = self._mark_done(unit_id, repo, backlog_root, backlog_index)
        assert done == 0
        assert "## Status: done" in wu_file.read_text(encoding="utf-8")


class TestJourneyDisabledGateSelfReports(_JourneyFixtures):
    """Disabled journey (spec 4.1, AC-4): with the gate disabled, `check-shared-file
    -impact` self-reports the exact disabled status line and exits 0, and
    `mark-done` imposes nothing for the gate."""

    def test_disabled_gate_status_line_and_mark_done_unblocked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        workspace = tmp_path / "workspace"
        repo, _base_sha = _shared_file_impact_git_fixture(
            tmp_path, base_test_content="def test_ok():\n    assert True\n", feature_test_content=None
        )
        backlog_root, backlog_index, wu_file = _seed_gate_done_path_backlog(
            tmp_path,
            unit_id,
            self.REPO,
            ("tests/test_suite.py",),
            "End to end shared-file-impact cycle test",
        )

        checked = self._run(unit_id, repo, backlog_root, backlog_index, monkeypatch, workspace=workspace, enabled=False)
        out = capsys.readouterr().out.strip()
        assert checked == 0
        assert json.loads(out) == {"gate": "shared_file_impact", "status": "disabled"}
        assert "[GATE_PASS shared_file_impact]" not in wu_file.read_text(encoding="utf-8")

        done = self._mark_done(unit_id, repo, backlog_root, backlog_index, gate_enabled=False)
        assert done == 0
        assert "## Status: done" in wu_file.read_text(encoding="utf-8")


class TestJourneyOperatorWaiverUnblocksDoneOnly(_JourneyFixtures):
    """Waiver journey (spec 3.6, 4.9): unlike reachability, `check-shared-file-impact`
    itself never reads `[GATE_WAIVER shared_file_impact]` markers -- the ONLY waiver
    interaction this gate has is the generic whole-gate `mark-done` bypass. An
    operator-attributed waiver unblocks `mark-done` even though a rerun of
    `check-shared-file-impact` itself still blocks."""

    def test_operator_waiver_unblocks_mark_done_while_check_still_blocks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        workspace = tmp_path / "workspace"
        base_content = "def test_ok():\n    assert True\n"
        feature_content = "def test_ok():\n    assert True\n\n\ndef test_new_fail():\n    assert False\n"
        repo, _base_sha = _shared_file_impact_git_fixture(
            tmp_path, base_test_content=base_content, feature_test_content=feature_content
        )
        backlog_root, backlog_index, wu_file = _seed_gate_done_path_backlog(
            tmp_path,
            unit_id,
            self.REPO,
            ("tests/test_suite.py",),
            "End to end shared-file-impact cycle test",
        )

        checked = self._run(unit_id, repo, backlog_root, backlog_index, monkeypatch, workspace=workspace)
        capsys.readouterr()
        assert checked == 1, "the introduced failure must still block a bare check-shared-file-impact run"

        waived = self._log_waiver(
            unit_id,
            repo,
            backlog_root,
            backlog_index,
            target="tests/test_suite.py",
            reason="regression tracked separately, unblocking done pending the follow-up fix",
        )
        assert waived == 0
        assert "[GATE_WAIVER shared_file_impact]" in wu_file.read_text(encoding="utf-8")

        done = self._mark_done(unit_id, repo, backlog_root, backlog_index)
        assert done == 0
        assert "## Status: done" in wu_file.read_text(encoding="utf-8")

        checked_again = self._run(unit_id, repo, backlog_root, backlog_index, monkeypatch, workspace=workspace)
        capsys.readouterr()
        assert checked_again == 1, (
            "the operator waiver satisfies mark-done alone -- it never makes a rerun of "
            "check-shared-file-impact itself pass, since this gate adopts no per-target waivers"
        )


class TestJourneyStaleRecordRefused(_JourneyFixtures):
    """Stale-record journey (spec 4.2, AC-7): editing an in-scope file after a
    `[GATE_PASS shared_file_impact]` record was captured refuses `mark-done`
    with the exact stale-record wording."""

    def test_in_scope_edit_after_pass_record_stales_and_blocks_done(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        workspace = tmp_path / "workspace"
        repo, _base_sha = _shared_file_impact_git_fixture(
            tmp_path, base_test_content="def test_ok():\n    assert True\n", feature_test_content=None
        )
        backlog_root, backlog_index, wu_file = _seed_gate_done_path_backlog(
            tmp_path,
            unit_id,
            self.REPO,
            ("src/unrelated.py",),
            "End to end shared-file-impact cycle test",
        )

        checked = self._run(
            unit_id,
            repo,
            backlog_root,
            backlog_index,
            monkeypatch,
            workspace=workspace,
            patterns=("tests/test_suite.py",),
        )
        capsys.readouterr()
        assert checked == 0
        assert "[GATE_PASS shared_file_impact]" in wu_file.read_text(encoding="utf-8")

        (repo / "src" / "unrelated.py").parent.mkdir(parents=True, exist_ok=True)
        (repo / "src" / "unrelated.py").write_text("# edited after the record was captured\n", encoding="utf-8")

        stale = self._mark_done(unit_id, repo, backlog_root, backlog_index)
        err = capsys.readouterr().err
        assert stale == 1
        assert "ERROR: gate 'shared_file_impact' record is stale (scope changed since it ran)" in err
        assert "## Status: done" not in wu_file.read_text(encoding="utf-8")


class TestJourneyAttributionStaysInScope(_JourneyFixtures):
    """Attribution journey (spec 4.3, D-7, AC-9): a NEW full-suite failure outside the
    unit's own Changes Manifest is visible in the repo-wide result but never blocks
    and never prevents the `[GATE_PASS shared_file_impact]` record / `mark-done`."""

    def test_out_of_scope_new_failure_passes_and_unblocks_done(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        workspace = tmp_path / "workspace"
        base_content = "def test_ok():\n    assert True\n"
        feature_content = "def test_ok():\n    assert True\n\n\ndef test_new_fail():\n    assert False\n"
        repo, _base_sha = _shared_file_impact_git_fixture(
            tmp_path, base_test_content=base_content, feature_test_content=feature_content
        )
        # The unit's own Changes Manifest names a DIFFERENT file from the one the new
        # failure actually lives in ("tests/test_suite.py"); it still matches the
        # configured pattern (so the gate triggers), but the failure is outside scope.
        # The Manifest-listed file must exist on disk: mark-done's generic scope-hash
        # recompute (`BacklogManager._git_blob_hash`) requires `git hash-object` to
        # succeed against it, unlike `work_unit_scope`'s tolerant absent-file marker.
        (repo / "src").mkdir(parents=True, exist_ok=True)
        (repo / "src" / "shared_module.py").write_text("VALUE = 1\n", encoding="utf-8")
        backlog_root, backlog_index, wu_file = _seed_gate_done_path_backlog(
            tmp_path,
            unit_id,
            self.REPO,
            ("src/shared_module.py",),
            "End to end shared-file-impact cycle test",
        )

        checked = self._run(
            unit_id,
            repo,
            backlog_root,
            backlog_index,
            monkeypatch,
            workspace=workspace,
            patterns=("src/shared_module.py",),
        )
        out = capsys.readouterr().out
        assert checked == 0
        assert "tests/test_suite.py::test_new_fail" in out, "the out-of-scope failure is still visible in the result"
        assert "[GATE_PASS shared_file_impact]" in wu_file.read_text(encoding="utf-8")

        done = self._mark_done(unit_id, repo, backlog_root, backlog_index)
        assert done == 0
        assert "## Status: done" in wu_file.read_text(encoding="utf-8")


class TestJourneyPreExistingVsIntroducedFailure(_JourneyFixtures):
    """Adversarial fixture pair (issue #13 AC2, AC-18): a failure already present at
    the branch point must never block, while one the unit's own diff introduces
    always does -- the two fixtures share everything except which commit carries the
    new failing test, isolating the attribution rule itself."""

    def test_pre_existing_failure_does_not_block(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        workspace = tmp_path / "workspace"
        base_content = "def test_ok():\n    assert True\n\n\ndef test_pre_existing_fail():\n    assert False\n"
        repo, _base_sha = _shared_file_impact_git_fixture(
            tmp_path,
            base_test_content=base_content,
            feature_test_content=None,
            feature_extra_path="tests/test_feature.py",
            feature_extra_content="def test_feature_addition():\n    assert True\n",
        )
        backlog_root, backlog_index, wu_file = _seed_gate_done_path_backlog(
            tmp_path,
            unit_id,
            self.REPO,
            ("tests/test_suite.py", "tests/test_feature.py"),
            "End to end shared-file-impact cycle test",
        )

        checked = self._run(
            unit_id,
            repo,
            backlog_root,
            backlog_index,
            monkeypatch,
            workspace=workspace,
            patterns=("tests/test_suite.py",),
        )
        capsys.readouterr()
        assert checked == 0, "a failure already present at the branch point must never block"
        assert "[GATE_PASS shared_file_impact]" in wu_file.read_text(encoding="utf-8")

    def test_introduced_failure_blocks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        workspace = tmp_path / "workspace"
        base_content = "def test_ok():\n    assert True\n"
        feature_content = "def test_ok():\n    assert True\n\n\ndef test_new_fail():\n    assert False\n"
        repo, _base_sha = _shared_file_impact_git_fixture(
            tmp_path, base_test_content=base_content, feature_test_content=feature_content
        )
        backlog_root, backlog_index, wu_file = _seed_gate_done_path_backlog(
            tmp_path,
            unit_id,
            self.REPO,
            ("tests/test_suite.py",),
            "End to end shared-file-impact cycle test",
        )

        checked = self._run(
            unit_id,
            repo,
            backlog_root,
            backlog_index,
            monkeypatch,
            workspace=workspace,
            patterns=("tests/test_suite.py",),
        )
        err = capsys.readouterr().err
        assert checked == 1, "a failure the unit's own diff introduces must always block"
        assert "tests/test_suite.py::test_new_fail" in err
        assert "[GATE_PASS shared_file_impact]" not in wu_file.read_text(encoding="utf-8")


class TestJourneyCorruptBaselineIsLoud(_JourneyFixtures):
    """Adversarial fixture (spec 4.6, finding 318-D2): a stored baseline that fails
    to parse is a loud `ERROR:`, never a silent re-bootstrap -- and never reaches a
    verdict `mark-done` could later trust."""

    def test_corrupt_baseline_blocks_check_and_mark_done(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        workspace = tmp_path / "workspace"
        repo, base_sha = _shared_file_impact_git_fixture(
            tmp_path, base_test_content="def test_ok():\n    assert True\n", feature_test_content=None
        )
        baseline_dir = workspace / ".devbench" / "test-baselines" / self.REPO.replace("/", "__")
        baseline_dir.mkdir(parents=True)
        baseline_file = baseline_dir / f"{base_sha}.json"
        corrupt_bytes = b"{not valid json"
        baseline_file.write_bytes(corrupt_bytes)

        backlog_root, backlog_index, wu_file = _seed_gate_done_path_backlog(
            tmp_path,
            unit_id,
            self.REPO,
            ("tests/test_suite.py",),
            "End to end shared-file-impact cycle test",
        )

        checked = self._run(
            unit_id,
            repo,
            backlog_root,
            backlog_index,
            monkeypatch,
            workspace=workspace,
            patterns=("tests/test_suite.py",),
        )
        err = capsys.readouterr().err
        assert checked == 1
        assert "ERROR: shared-file baseline" in err
        assert "is corrupt" in err
        assert baseline_file.read_bytes() == corrupt_bytes, "a corrupt baseline must never be silently rewritten"
        assert "[GATE_PASS shared_file_impact]" not in wu_file.read_text(encoding="utf-8")

        blocked = self._mark_done(unit_id, repo, backlog_root, backlog_index)
        assert blocked == 1
        assert "## Status: done" not in wu_file.read_text(encoding="utf-8")


class TestJourneyAutoDeriveRegistryYieldsExpectedSharedSet(_JourneyFixtures):
    """Adversarial/auto-derive fixture (spec 4.6, issue #13 AC4): with
    `gates.shared_file_impact.auto_derive_registry` enabled and no hand-maintained
    `patterns` at all, a file imported by more than `fan_in_threshold` distinct
    modules is derived as shared and triggers the gate; a file imported by FEWER
    modules than the threshold is not."""

    def _write_fan_in_fixture(self, repo: Path, *, importer_count: int) -> None:
        (repo / "src").mkdir(parents=True, exist_ok=True)
        (repo / "src" / "shared_module.py").write_text("VALUE = 1\n", encoding="utf-8")
        for i in range(importer_count):
            (repo / "src" / f"consumer_{i}.py").write_text("import shared_module\n", encoding="utf-8")

    def test_fan_in_above_threshold_is_derived_and_triggers_the_gate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        workspace = tmp_path / "workspace"
        repo, _base_sha = _shared_file_impact_git_fixture(
            tmp_path, base_test_content="def test_ok():\n    assert True\n", feature_test_content=None
        )
        # fan_in_threshold defaults to 3 (strictly-greater-than): 4 importers crosses it.
        self._write_fan_in_fixture(repo, importer_count=4)
        backlog_root, backlog_index, wu_file = _seed_gate_done_path_backlog(
            tmp_path,
            unit_id,
            self.REPO,
            ("src/shared_module.py",),
            "End to end shared-file-impact cycle test",
        )

        checked = self._run(
            unit_id,
            repo,
            backlog_root,
            backlog_index,
            monkeypatch,
            workspace=workspace,
            patterns=(),
            auto_derive_registry=True,
            fan_in_threshold=3,
        )
        out = capsys.readouterr().out
        assert checked == 0
        payload_lines = out.splitlines()
        payload = json.loads("\n".join(payload_lines[1:]))
        assert payload["matched_files"] == ["src/shared_module.py"]
        assert "[GATE_PASS shared_file_impact]" in wu_file.read_text(encoding="utf-8")

        done = self._mark_done(unit_id, repo, backlog_root, backlog_index)
        assert done == 0
        assert "## Status: done" in wu_file.read_text(encoding="utf-8")

    def test_fan_in_at_or_below_threshold_is_not_derived_and_never_triggers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        workspace = tmp_path / "workspace"
        repo, _base_sha = _shared_file_impact_git_fixture(
            tmp_path, base_test_content="def test_ok():\n    assert True\n", feature_test_content=None
        )
        # Exactly at the threshold (3): the strictly-greater-than comparison excludes it.
        self._write_fan_in_fixture(repo, importer_count=3)
        backlog_root, backlog_index, _wu_file = _seed_gate_done_path_backlog(
            tmp_path,
            unit_id,
            self.REPO,
            ("src/shared_module.py",),
            "End to end shared-file-impact cycle test",
        )

        with patch("devbench.cli.run_command") as mock_run:
            checked = self._run(
                unit_id,
                repo,
                backlog_root,
                backlog_index,
                monkeypatch,
                workspace=workspace,
                patterns=(),
                auto_derive_registry=True,
                fan_in_threshold=3,
            )
        out = capsys.readouterr().out
        assert checked == 0
        mock_run.assert_not_called()
        payload_lines = out.splitlines()
        payload = json.loads("\n".join(payload_lines[1:]))
        assert payload["shared_file_impact"] is False
        assert "src/shared_module.py" not in payload.get("derived_registry", [])
