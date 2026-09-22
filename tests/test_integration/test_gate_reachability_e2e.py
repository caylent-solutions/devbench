"""Hermetic reachability journey suite (E3-F2-S1-T2).

The reachability gate (`cmd_check_reachability`, `cmd_mark_done`'s
`_check_gate_pass_done_invariant`, `cmd_log_waiver`) is already covered at
the unit level in `tests/test_cli.py::TestCmdCheckReachability`,
`TestCmdCheckReachabilityOrphanChain`,
`TestCmdCheckReachabilityWritesGatePass`,
`TestCmdCheckReachabilityWaivedTarget` and
`TestReachabilityDonePathEndToEnd`, each of which already drives the same
unmocked `cmd_*` functions over the same real git fixture repos via the
same `_ReachabilityCmdFixtures` helpers this module imports. What this
module adds is the `tests/test_integration` placement spec Section 10
requires per gate, one dedicated test class per journey (rather than one
method per assertion inside a shared class), and the route-split-orphan
and lazy-import adversarial fixtures spec Section 2 G9 and issue #10 AC4
demand at journey level: block, pass, disabled, waiver, stale-record,
attribution and adversarial (route-split / lazy-import / prose / substring
/ plumbing-failure) journeys.

No production code is added by this task (Task Type: test-only): every
journey below asserts behaviour E3-F1 and E3-F2-S1-T1 already shipped, so a
journey that fails here is a genuine defect in the gate, not a missing
feature.

Fixture idiom: `_ReachabilityCmdFixtures`, `_seed_scope_backlog` and
`_seed_reachability_done_path_backlog` are imported from `tests/test_cli.py`
(not hand-copied) -- the shared git-fixture factory, config-fixture writer
and Manifest-seeding helpers that already back the gate's unit-level
coverage. `_JourneyFixtures` below adds only the `mark-done` / `log-waiver`
patch-and-call helpers this module needs on top of that shared base; it does
not re-derive `_ReachabilityCmdFixtures`'s own git-fixture or config-fixture
logic.

"Real CLI" here means the actual, unmocked `devbench.cli.cmd_check_reachability`,
`devbench.cli.cmd_mark_done` and `devbench.cli.cmd_log_waiver` implementations
-- the same functions the `devbench` executable dispatches to. Rather than
enumerate the full patch surface here (it differs between the
`check-reachability` and `mark-done` paths -- see each helper's own
docstring/body for the authoritative set), the two call paths are:
`check-reachability` calls go through the inherited
`_ReachabilityCmdFixtures._run`, which patches `devbench.cli.BacklogParser`,
`devbench.cli.REPO_LOCAL_PATHS`, `devbench.cli.BACKLOG_ROOT`,
`devbench.work_unit_scope.BACKLOG_ROOT` and
`devbench.work_unit_scope.BACKLOG_INDEX`, and additionally sets the
env-layer overrides `DEVBENCH_CONFIG_PATH` (pointed at a real, on-disk
gate-config file) and clears `DEVBENCH_GATE_REACHABILITY_ENABLED` -- it is
this env layer, not a `RUNTIME_CONFIG` patch, that resolves
`gates.reachability` for every journey, including the disabled one.
`mark-done` calls go through this module's own
`_JourneyFixtures._mark_done_patches`, which separately patches
`devbench.cli.BacklogParser`, `devbench.cli.REPO_LOCAL_PATHS`,
`devbench.cli.BACKLOG_ROOT`, `devbench.cli.WORKSPACE_ROOT`,
`devbench.cli.BACKLOG_INDEX`, `devbench.work_unit_scope.BACKLOG_ROOT`,
`devbench.work_unit_scope.BACKLOG_INDEX` and `devbench.config.RUNTIME_CONFIG`
directly, since `mark-done` has no env-config-file entry point of its own.
`devbench.cli.WORKSPACE_ROOT` and `devbench.cli.BACKLOG_INDEX` are patched
only by `_mark_done_patches`; `_run` never touches them. Confined to
`TestJourneyGitGrepPlumbingFailureIsLoud` alone, `devbench.cli.run_command`
is faked at the subprocess boundary (every `cli.run_command` call is
intercepted; an assertion inside the fake fails loudly if it is ever called
with anything other than the expected `git grep` invocation) to return an
rc>=2 result for that call -- the same pattern `tests/test_cli.py`'s own
`TestCmdCheckReachability::test_git_grep_failure_exits_loud_with_stderr`
already uses. No journey mocks `cmd_check_reachability`,
`_is_reachable_from_entry_points`, `_search_reachability_importers` or any
other gate-internal function.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from test_cli import _ReachabilityCmdFixtures, _seed_reachability_done_path_backlog, _seed_scope_backlog

from devbench import cli


class _JourneyFixtures(_ReachabilityCmdFixtures):
    """Journey-level helpers layered on `_ReachabilityCmdFixtures`.

    Adds `mark-done` and `log-waiver` runners using the identical patch
    surface `tests/test_cli.py::TestReachabilityDonePathEndToEnd._patches`
    already exercises for the single done-path cycle test, so a journey can
    drive `check-reachability` (inherited `_run`), `log-waiver` and
    `mark-done` back to back against one on-disk fixture without
    re-deriving that patch set per journey class.
    """

    def _seed_done_path(self, tmp_path: Path, unit_id: str, manifest_file: str) -> tuple[Path, Path, Path]:
        """Seed a Manifest + Task Type: test-only + all-judges-pass work unit.

        Delegates entirely to `tests/test_cli.py::_seed_reachability_done_path_backlog`
        so the `## Target Repository` section, the exempt `Task Type`
        (this task's own type) and the all-five-judges-pass `## Comments`
        block stay defined in exactly one place.
        """
        return _seed_reachability_done_path_backlog(tmp_path, unit_id, self._REPO, manifest_file)

    def _mark_done_patches(
        self,
        repo: Path,
        backlog_root: Path,
        backlog_index: Path,
        unit_id: str,
        *,
        gate_enabled: bool,
    ) -> tuple[Any, ...]:
        from devbench.config_loader import GateReachabilityConfig, GatesConfig, RepoConfig, RuntimeConfig

        unit = self._make_unit(unit_id)
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        runtime_config = RuntimeConfig(
            repos={self._REPO: RepoConfig(resolved_checkout_path=repo)},
            gates=GatesConfig(reachability=GateReachabilityConfig(enabled=gate_enabled)),
        )
        return (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {self._REPO: repo}),
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
                "reachability",
                "--target",
                target,
                "--reason",
                reason,
                "--operator",
            )


class TestJourneyBlocksUnreferencedArtifact(_JourneyFixtures):
    """Block journey (spec 4.4, AC-14, AC-16): an added, unreferenced
    artifact makes both `check-reachability` and the `mark-done` gate it
    feeds refuse, with no `[GATE_PASS reachability]` record written."""

    def test_orphan_artifact_blocks_check_and_mark_done(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        repo = self._git_repo(tmp_path)
        (repo / "src").mkdir()
        (repo / "src/Orphan.tsx").write_text("export default function Impl() { return null; }\n", encoding="utf-8")
        backlog_root, backlog_index, wu_file = self._seed_done_path(tmp_path, unit_id, "src/Orphan.tsx")

        checked = self._run(unit_id, repo, backlog_root, backlog_index, monkeypatch)
        out = capsys.readouterr().out
        assert checked == 1
        assert "[POTENTIALLY UNREACHABLE] src/Orphan.tsx" in out
        assert "[GATE_PASS reachability]" not in wu_file.read_text(encoding="utf-8")

        blocked = self._mark_done(unit_id, repo, backlog_root, backlog_index)
        assert blocked == 1
        assert "## Status: done" not in wu_file.read_text(encoding="utf-8")


class TestJourneyPassWritesGateRecordAndUnblocksDone(_JourneyFixtures):
    """Pass journey (spec 4.2/4.4, AC-16): a referenced artifact clears the
    gate, a single `[GATE_PASS reachability]` record is persisted, and
    `mark-done` then proceeds."""

    def test_referenced_artifact_passes_writes_record_and_unblocks_done(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        repo = self._git_repo(tmp_path)
        (repo / "src").mkdir()
        (repo / "src/Wired.tsx").write_text("export function Wired() { return null; }\n", encoding="utf-8")
        (repo / "src/App.tsx").write_text(
            "import { Wired } from './Wired';\nexport function App() { return Wired; }\n", encoding="utf-8"
        )
        backlog_root, backlog_index, wu_file = self._seed_done_path(tmp_path, unit_id, "src/Wired.tsx")

        checked = self._run(unit_id, repo, backlog_root, backlog_index, monkeypatch)
        capsys.readouterr()
        content_after_check = wu_file.read_text(encoding="utf-8")
        assert checked == 0
        assert content_after_check.count("[GATE_PASS reachability]") == 1

        done = self._mark_done(unit_id, repo, backlog_root, backlog_index)
        assert done == 0
        assert "## Status: done" in wu_file.read_text(encoding="utf-8")


class TestJourneyDisabledGateSelfReports(_JourneyFixtures):
    """Disabled journey (spec 4.1 final bullet, AC-4): with no `gates:` key,
    `check-reachability` self-reports disabled and exits 0, and `mark-done`
    imposes nothing for the gate."""

    def test_disabled_gate_status_line_and_mark_done_unblocked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        repo = self._git_repo(tmp_path)
        (repo / "src").mkdir()
        (repo / "src/Whatever.tsx").write_text("export default function Impl() { return null; }\n", encoding="utf-8")
        backlog_root, backlog_index, wu_file = self._seed_done_path(tmp_path, unit_id, "src/Whatever.tsx")

        checked = self._run(unit_id, repo, backlog_root, backlog_index, monkeypatch, gates_block="")
        out = capsys.readouterr().out.strip()
        assert checked == 0
        assert json.loads(out) == {"gate": "reachability", "status": "disabled"}
        assert "[GATE_PASS reachability]" not in wu_file.read_text(encoding="utf-8")

        done = self._mark_done(unit_id, repo, backlog_root, backlog_index, gate_enabled=False)
        assert done == 0
        assert "## Status: done" in wu_file.read_text(encoding="utf-8")


class TestJourneyOperatorWaiverUnblocks(_JourneyFixtures):
    """Waiver journey (spec 4.9, Section 2 G7): an operator-attributed
    `log-waiver` unblocks an otherwise-orphaned artifact, both in
    `check-reachability`'s own `[WAIVED]` output line and in `mark-done`."""

    def test_operator_waiver_shows_waived_output_and_unblocks_done(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        repo = self._git_repo(tmp_path)
        (repo / "src").mkdir()
        (repo / "src/Orphan.tsx").write_text("export default function Impl() { return null; }\n", encoding="utf-8")
        backlog_root, backlog_index, wu_file = self._seed_done_path(tmp_path, unit_id, "src/Orphan.tsx")

        waived = self._log_waiver(
            unit_id,
            repo,
            backlog_root,
            backlog_index,
            target="src/Orphan.tsx",
            reason="reviewed manually, safe pending route wiring",
        )
        assert waived == 0
        assert "[GATE_WAIVER reachability]" in wu_file.read_text(encoding="utf-8")

        checked = self._run(unit_id, repo, backlog_root, backlog_index, monkeypatch)
        out = capsys.readouterr().out
        assert checked == 0
        assert "[WAIVED] src/Orphan.tsx -- reviewed manually, safe pending route wiring" in out

        done = self._mark_done(unit_id, repo, backlog_root, backlog_index)
        assert done == 0
        assert "## Status: done" in wu_file.read_text(encoding="utf-8")


class TestJourneyStaleRecordRefused(_JourneyFixtures):
    """Stale-record journey (spec 4.2 AC-7): editing an in-scope file after
    a `[GATE_PASS reachability]` record was captured refuses `mark-done`
    with the exact stale-record wording."""

    def test_in_scope_edit_after_pass_record_stales_and_blocks_done(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        repo = self._git_repo(tmp_path)
        (repo / "src").mkdir()
        (repo / "src/Wired.tsx").write_text("export function Wired() { return null; }\n", encoding="utf-8")
        (repo / "src/App.tsx").write_text(
            "import { Wired } from './Wired';\nexport function App() { return Wired; }\n", encoding="utf-8"
        )
        backlog_root, backlog_index, wu_file = self._seed_done_path(tmp_path, unit_id, "src/Wired.tsx")

        checked = self._run(unit_id, repo, backlog_root, backlog_index, monkeypatch)
        capsys.readouterr()
        assert checked == 0
        assert "[GATE_PASS reachability]" in wu_file.read_text(encoding="utf-8")

        (repo / "src/Wired.tsx").write_text("export function Wired() { return 'changed'; }\n", encoding="utf-8")

        stale = self._mark_done(unit_id, repo, backlog_root, backlog_index)
        err = capsys.readouterr().err
        assert stale == 1
        assert "ERROR: gate 'reachability' record is stale (scope changed since it ran)" in err
        assert "## Status: done" not in wu_file.read_text(encoding="utf-8")


class TestJourneyAttributionStaysInScope(_JourneyFixtures):
    """Attribution journey (spec 4.3, AC-9): a pre-existing, unreferenced
    file outside this unit's Changes Manifest is never named in the gate's
    output, even though it would independently be an orphan."""

    def test_out_of_manifest_orphan_never_named(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        repo = self._git_repo(tmp_path)
        (repo / "src").mkdir()
        (repo / "src/InScope.tsx").write_text("export function InScope() { return null; }\n", encoding="utf-8")
        (repo / "src/App.tsx").write_text(
            "import { InScope } from './InScope';\nexport function App() { return InScope; }\n", encoding="utf-8"
        )
        (repo / "src/PreExistingOrphan.tsx").write_text(
            "export default function Impl() { return null; }\n", encoding="utf-8"
        )
        backlog_root, backlog_index = _seed_scope_backlog(tmp_path, unit_id=unit_id, files=("src/InScope.tsx",))

        result = self._run(unit_id, repo, backlog_root, backlog_index, monkeypatch)
        out = capsys.readouterr().out

        assert result == 0
        assert "PreExistingOrphan" not in out


class TestJourneyRouteSplitOrphanNotFalselyCleared(_JourneyFixtures):
    """Adversarial journey (Section 2 G9, issue #10 AC4): an artifact
    referenced only from a route-split registry module that no entry point
    itself reaches is reported `[POTENTIALLY UNREACHABLE via orphan-chain]`,
    not falsely cleared as `[OK]`."""

    def test_route_split_registry_referrer_stays_orphan_chain(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        repo = self._git_repo(tmp_path)
        (repo / "src/routes").mkdir(parents=True)
        (repo / "src/Widget.tsx").write_text("export function Widget() { return null; }\n", encoding="utf-8")
        (repo / "src/routes/registry.tsx").write_text(
            "import { Widget } from '../Widget';\n"
            "// route-split registry table; nothing imports this module itself\n"
            "export const routeTable = { widget: Widget };\n",
            encoding="utf-8",
        )
        backlog_root, backlog_index = _seed_scope_backlog(tmp_path, unit_id=unit_id, files=("src/Widget.tsx",))

        result = self._run(unit_id, repo, backlog_root, backlog_index, monkeypatch)
        out = capsys.readouterr().out

        assert result == 1
        assert "[POTENTIALLY UNREACHABLE via orphan-chain] src/Widget.tsx" in out
        assert "src/routes/registry.tsx" in out
        assert "[OK] src/Widget.tsx" not in out


class TestJourneyLazyImportIsNotAFalsePositive(_JourneyFixtures):
    """Adversarial journey (issue #10 AC4): an artifact reached only through
    a lazy/deferred import from a genuine entry point is CLEARED, so the
    orphan-chain rule does not manufacture a false positive on a legitimate
    code shape."""

    def test_lazy_import_from_entry_point_clears_candidate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        repo = self._git_repo(tmp_path)
        (repo / "src").mkdir()
        (repo / "src/Widget.tsx").write_text("export function Widget() { return null; }\n", encoding="utf-8")
        (repo / "src/App.tsx").write_text(
            "const Widget = React.lazy(() => import('./Widget'));\nexport function App() { return Widget; }\n",
            encoding="utf-8",
        )
        backlog_root, backlog_index = _seed_scope_backlog(tmp_path, unit_id=unit_id, files=("src/Widget.tsx",))

        result = self._run(unit_id, repo, backlog_root, backlog_index, monkeypatch)
        out = capsys.readouterr().out

        assert result == 0
        assert "[OK] src/Widget.tsx" in out
        assert "src/App.tsx" in out


class TestJourneyProseMentionDoesNotClearOrphan(_JourneyFixtures):
    """Register 315-D02 shape at journey level: a mention of the symbol in
    markdown prose can never clear an orphan (spec 4.4 bullet 1)."""

    def test_markdown_prose_reference_does_not_clear_orphan(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        repo = self._git_repo(tmp_path)
        (repo / "src").mkdir()
        (repo / "src/Orphan.tsx").write_text("export default function Impl() { return null; }\n", encoding="utf-8")
        (repo / "docs").mkdir()
        (repo / "docs/architecture.md").write_text("See Orphan for background on this change.\n", encoding="utf-8")
        backlog_root, backlog_index = _seed_scope_backlog(tmp_path, unit_id=unit_id, files=("src/Orphan.tsx",))

        result = self._run(unit_id, repo, backlog_root, backlog_index, monkeypatch)
        out = capsys.readouterr().out

        assert result == 1
        assert "[POTENTIALLY UNREACHABLE] src/Orphan.tsx" in out


class TestJourneySubstringDoesNotClearOrphan(_JourneyFixtures):
    """Register 315-D01 shape at journey level: a substring match (not a
    word-boundary identifier reference) can never clear an orphan (spec 4.4
    bullet 1)."""

    def test_substring_match_does_not_clear_orphan(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        repo = self._git_repo(tmp_path)
        (repo / "src").mkdir()
        (repo / "src/Card.tsx").write_text("export default function Impl() { return null; }\n", encoding="utf-8")
        (repo / "src/Other.tsx").write_text("// Cardinal points\nconst discardCards = 1;\n", encoding="utf-8")
        backlog_root, backlog_index = _seed_scope_backlog(tmp_path, unit_id=unit_id, files=("src/Card.tsx",))

        result = self._run(unit_id, repo, backlog_root, backlog_index, monkeypatch)
        out = capsys.readouterr().out

        assert result == 1
        assert "[POTENTIALLY UNREACHABLE] src/Card.tsx" in out


class TestJourneyGitGrepPlumbingFailureIsLoud(_JourneyFixtures):
    """Plumbing-failure journey (spec 4.4 bullet 3, Section 7): a genuine
    repo state that makes `git grep` exit rc>=2 is not hermetically
    reproducible, so this journey fakes the `git grep` subprocess boundary
    (`patch("devbench.cli.run_command", ...)`, which intercepts every
    `cli.run_command` call; an assertion inside the fake fails loudly if it
    is ever invoked with anything other than `["git", "grep", ...]`) to
    return rc>=2. The gate must still exit 1, emit the loud
    `ERROR: git grep failed:` sentence on stderr, and print no status line
    on stdout, rather than a silent "no importers" verdict."""

    def test_git_grep_rc_ge_2_exits_loud_with_stderr(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        repo = self._git_repo(tmp_path)
        (repo / "src").mkdir()
        (repo / "src/Widget.tsx").write_text("export function Widget() { return null; }\n", encoding="utf-8")
        backlog_root, backlog_index = _seed_scope_backlog(tmp_path, unit_id=unit_id, files=("src/Widget.tsx",))

        def fake_run_command(cmd: list[str], **_kwargs: object) -> tuple[int, str, str]:
            assert cmd[:2] == ["git", "grep"], f"unexpected subprocess routed through the mock: {cmd}"
            return (2, "", "fatal: forced pathspec failure for the journey rc>=2 test")

        with patch("devbench.cli.run_command", side_effect=fake_run_command):
            result = self._run(unit_id, repo, backlog_root, backlog_index, monkeypatch)

        captured = capsys.readouterr()
        assert result == 1
        assert "ERROR: git grep failed:" in captured.err
        assert "forced pathspec failure" in captured.err
        assert captured.out == ""
