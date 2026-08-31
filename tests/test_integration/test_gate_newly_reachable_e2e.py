"""Hermetic journey suite for the newly-reachable-paths gate (E8-F2-S1-T1).

Spec Section 10 requires one hermetic journey suite per gate at
``tests/test_integration/test_gate_<name>_e2e.py``, driving the real CLI
over scratch git fixture repositories. This module is that suite for
``newly_reachable_paths`` (spec 4.9(a), 5.3).

The decisive case is spec AC-21: a ``[NEWLY_REACHABLE]`` marker written by
``uv run devbench log-newly-reachable`` must still be present in what a
review judge actually receives. Judges never read the work-unit file
directly; they read ``read-unit --strip-comments`` output, which truncates
everything from the ``## Comments`` marker onward (``cli.cmd_read_unit``).
PR #320's retired convention wrote its record into ``## Comments`` via
``log-comment``, which is precisely why finding 320-D01 and decision C-07
moved the record into the audit section (``## TDD Cycle Log``, via
``BacklogManager._append_audit_marker_before_comments``). This suite proves
the survival property end to end against the real, unmocked
``cli.cmd_log_newly_reachable`` and ``cli.cmd_read_unit`` implementations,
and separately proves the assertion is not a stub by pinning the FAILING
shape too: a marker hand-placed below ``## Comments`` (the retired
convention's shape) is provably absent from ``read-unit --strip-comments``
output.

This module also pins the mechanism E8-F1-S1-T1 landed in
``devbench.backlog.proposal``: ``generate_draft_md`` appends the
newly-reachable-paths acceptance-criterion line to a drafted task only when
``ProposedTask.task_type`` resolves to ``constants.TASK_TYPE_BEHAVIOR_FIX``,
and never appends a Definition-of-Done line for any task type (spec 4.9a,
1.3 S1, findings 320-D04/C-06). That mechanism is already covered at the
unit level in ``tests/test_backlog/test_proposal.py``
(``TestNewlyReachableTaskTypeKeying``); this module adds the
``tests/test_integration`` placement Section 10 requires, calling the same
real, unmocked ``generate_draft_md`` function directly rather than driving
it through the one registered CLI verb that reaches it
(``materialise-proposal`` -> ``cmd_materialise_proposal`` ->
``materialise_proposal`` -> ``generate_draft_md``), because that verb also
requires a persisted proposal JSON, a resolved source task and a live
backlog index -- machinery unrelated to the task-type-keying invariant
under test here. The taxonomy tuple, the ``ProposedTask`` factory and the
Definition-of-Done assertion this module's taxonomy cases need are the
SAME ones ``TestNewlyReachableTaskTypeKeying`` needs, so both suites import
them from ``tests/test_integration/conftest.py`` rather than each hand-typing
its own copy (see ``TestJourneyTaskTypeTaxonomyGatesAcceptanceCriterion``
below).

Fixture idiom: the scratch git fixture-repo factory
(``init_scratch_repo``/``write_scratch_file``/``commit_scratch_repo``) is
imported from ``tests/test_tdd_gate.py`` per this task's Definition of
Ready and Approach, exactly as ``tests/test_integration/test_gate_write_path_e2e.py``,
``tests/test_integration/test_gate_ancestry_e2e.py`` and
``tests/test_integration/test_tdd_red_gate_e2e.py`` already do -- not
re-derived here. ``_NewlyReachableJourneyFixtures`` below is this module's
own thin layer (a backlog-file seeder plus the
``log-newly-reachable``/``read-unit`` patch surface), following the same
shape ``test_gate_write_path_e2e.py``'s ``_WritePathJourneyFixtures`` sets
for its own ``log-waiver``/``read-unit`` pair, rather than a second
hand-rolled copy of that class's helpers.

"Real CLI" here means the actual, unmocked
``devbench.cli.cmd_log_newly_reachable`` and ``devbench.cli.cmd_read_unit``
implementations -- the same functions the ``devbench`` executable
dispatches to. ``devbench.cli.BacklogParser`` (returning a ``MagicMock``
parser serving the seeded unit fixture directly), ``devbench.cli.REPO_LOCAL_PATHS``,
``devbench.cli.BACKLOG_ROOT`` and ``devbench.cli.WORKSPACE_ROOT`` are
patched -- the same seam ``_WritePathJourneyFixtures._patch_backlog_write``
uses for its own marker-writer/``read-unit`` pair. No journey mocks
``cmd_log_newly_reachable``, ``cmd_read_unit``, ``compose_newly_reachable_record``,
``BacklogManager._append_audit_marker_before_comments`` or
``generate_draft_md``.

The disabled-status journey (AC-E8-T3-004) is the one case in this module
with no dedicated ``check-newly-reachable-paths`` CLI verb to drive:
``log-newly-reachable`` is a marker-RECORDING verb (spec 4.9(a)), not a
gate-CHECK verb, and never consults gate configuration at all -- unlike
``check-ancestry``/``check-reachability``/``check-shared-file-impact``/
``check-write-path``, which each read ``gates.<name>.enabled`` through the
shared ``cli._load_gate_config_or_report`` helper before doing any
gate-specific work (``check-fixture-consistency`` is the one exception:
``cmd_check_fixture_consistency`` never calls that helper at all -- its
disabled path is keyed off ``RUNTIME_CONFIG.gates.fixture_consistency.canonical_sources``
being empty, and it prints ``_gate_disabled_line`` directly). That helper
is itself real, unmocked, gate-name-generic production code
(``resolve_gate_config`` raises ``ValueError`` for any name outside
``constants.GATE_NAMES``, and ``newly_reachable_paths`` is one of the eight
declared names) -- calling it directly with ``"newly_reachable_paths"``
proves the spec 4.1/5.2 disabled line renders correctly for this gate's
name using the exact formatter (``cli._gate_disabled_line``) every other
gate command already relies on, without fabricating a CLI verb that was
never shipped.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from test_integration.conftest import (
    NEWLY_REACHABLE_TASK_TYPE_AC_LINE_EXPECTATIONS,
    NEWLY_REACHABLE_TASK_TYPE_TAXONOMY,
    assert_no_newly_reachable_definition_of_done_line,
    make_newly_reachable_keying_task,
)
from test_tdd_gate import commit_scratch_repo, init_scratch_repo, write_scratch_file

from devbench import cli
from devbench.backlog.proposal import generate_draft_md
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType
from devbench.constants import GATE_STATUS_DISABLED


class _NewlyReachableJourneyFixtures:
    """Shared helpers for the ``log-newly-reachable``/``read-unit`` journeys below.

    Mirrors ``test_gate_write_path_e2e.py``'s ``_WritePathJourneyFixtures``
    shape: a scratch work-unit ``.md`` seeder carrying a real ``##
    TDD Cycle Log`` / ``## Comments`` ordering (the ordering
    ``read-unit --strip-comments`` relies on to keep an audit marker
    written into ``## TDD Cycle Log`` visible) plus the patch surface both
    verbs need.
    """

    _REPO = "caylent-solutions/devbench"
    _UNIT_ID = "E9-F1-S1-T1"

    def _make_unit(self, unit_id: str = _UNIT_ID) -> WorkUnit:
        return WorkUnit(
            id=unit_id,
            title="Newly-reachable-paths journey task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path(f"backlog/{unit_id}.md"),
            repo=self._REPO,
            dependencies=[],
        )

    def _seed_backlog_file(self, tmp_path: Path, unit_id: str = _UNIT_ID) -> tuple[Path, Path]:
        """Write a scratch work-unit ``.md`` with a real-shape section ordering.

        Returns ``(backlog_root, wu_file)``. ``## Task Type: behavior-fix``
        is declared (the taxonomy value this gate's mechanism keys off,
        spec 4.9a) even though ``log-newly-reachable``/``read-unit``
        themselves never read it -- so the fixture reads as a realistic
        behavior-fix task rather than an untyped stub.
        """
        backlog_root = tmp_path / "backlog"
        backlog_root.mkdir(exist_ok=True)
        wu_file = backlog_root / f"{unit_id}.md"
        wu_file.write_text(
            f"# {unit_id}: Newly-reachable-paths journey task\n\n"
            "## Status: in-progress\n\n"
            "## Task Type: behavior-fix\n\n"
            "## Changes Manifest\n\n"
            "| File | Change |\n|------|--------|\n| `src/reducers/permissionReducer.ts` | modify |\n\n"
            "## TDD Cycle Log\n\n## Comments\n",
            encoding="utf-8",
        )
        return backlog_root, wu_file

    @contextlib.contextmanager
    def _patch_backlog(self, unit: WorkUnit, repo_path: Path, backlog_root: Path) -> Iterator[None]:
        """Patch surface for ``log-newly-reachable``/``read-unit``.

        ``devbench.cli.BACKLOG_ROOT``/``WORKSPACE_ROOT`` resolve the
        work-unit ``.md`` file itself; ``devbench.cli.REPO_LOCAL_PATHS``
        resolves ``read-unit``'s ``repo_path`` field. Neither verb touches
        git, so no ``work_unit_scope``/reachability-style patch layer is
        needed here.
        """
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {self._REPO: repo_path}),
            patch("devbench.cli.BACKLOG_ROOT", backlog_root.parent),
            patch("devbench.cli.WORKSPACE_ROOT", backlog_root.parent),
        ):
            yield


class TestJourneyMarkerSurvivesStripCommentsEvidenceFetch(_NewlyReachableJourneyFixtures):
    """AC-21 / AC-E8-T3-001: a marker written by the real ``log-newly-reachable``
    verb is present in the real ``read-unit --strip-comments`` output, above
    the ``## Comments`` boundary that fetch truncates at."""

    def test_newly_reachable_marker_survives_strip_comments(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = init_scratch_repo(tmp_path)
        write_scratch_file(repo, "src/reducers/permissionReducer.ts", "export const noop = () => undefined;\n")
        commit_scratch_repo(repo, "seed repo for newly-reachable journey")
        backlog_root, wu_file = self._seed_backlog_file(tmp_path)
        unit = self._make_unit()

        with self._patch_backlog(unit, repo, backlog_root):
            logged = cli.cmd_log_newly_reachable(
                unit.id,
                "--path",
                "src/reducers/permissionReducer.ts",
                "--method",
                "manual",
                "--result",
                "verified",
            )
        capsys.readouterr()
        assert logged == 0
        content_after_log = wu_file.read_text(encoding="utf-8")
        tdd_idx = content_after_log.find("## TDD Cycle Log")
        marker_idx = content_after_log.find("[NEWLY_REACHABLE]")
        comments_idx = content_after_log.find("## Comments")
        assert tdd_idx != -1
        assert marker_idx != -1
        assert tdd_idx < marker_idx < comments_idx, (
            f"marker must sit inside ## TDD Cycle Log, above ## Comments: {content_after_log!r}"
        )
        assert "[NEWLY_REACHABLE] src/reducers/permissionReducer.ts manual verified" in content_after_log

        with self._patch_backlog(unit, repo, backlog_root):
            read_result = cli.cmd_read_unit("--strip-comments", unit.id)
        captured = capsys.readouterr()
        assert read_result == 0
        payload = json.loads(captured.out)
        assert "## Comments" not in payload["content"]
        assert "[NEWLY_REACHABLE] src/reducers/permissionReducer.ts manual verified" in payload["content"], (
            f"the marker must survive the judge Evidence fetch (AC-21); stripped content was: {payload['content']!r}"
        )


class TestJourneyMarkerBelowCommentsIsStripped(_NewlyReachableJourneyFixtures):
    """AC-E8-T3-002 / AC-E8-T3-003 (finding 320-D01): the mutation-shape
    regression pin. A marker hand-placed BELOW ``## Comments`` -- the
    retired PR #320 free-text convention's insertion point -- is absent
    from ``read-unit --strip-comments`` output. This is the concrete
    demonstration that the AC-21 assertion above is not a stub: it fails
    exactly the way a regression that relocates the marker back into
    ``## Comments`` would fail."""

    def test_marker_placed_below_comments_is_absent_from_strip_comments_output(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = init_scratch_repo(tmp_path)
        write_scratch_file(repo, "README.md", "placeholder repo for the mutation-shape journey\n")
        commit_scratch_repo(repo, "seed repo")
        backlog_root, wu_file = self._seed_backlog_file(tmp_path)
        unit = self._make_unit()

        # Hand-write the marker below ## Comments (the retired free-text
        # `log-comment`-into-`## Comments` convention's shape), never through
        # `cmd_log_newly_reachable` (which always writes above the boundary).
        content = wu_file.read_text(encoding="utf-8")
        mutated = content + "[NEWLY_REACHABLE] src/legacy/never_logged.py manual verified\n"
        wu_file.write_text(mutated, encoding="utf-8")

        with self._patch_backlog(unit, repo, backlog_root):
            read_result = cli.cmd_read_unit("--strip-comments", unit.id)
        captured = capsys.readouterr()
        assert read_result == 0
        payload = json.loads(captured.out)
        assert "[NEWLY_REACHABLE] src/legacy/never_logged.py manual verified" not in payload["content"], (
            f"a marker written below ## Comments must never survive --strip-comments: {payload['content']!r}"
        )
        assert "## Comments" not in payload["content"]


class TestJourneyGateDisabledStatusLine(_NewlyReachableJourneyFixtures):
    """AC-E8-T3-004 (spec 4.1, 5.2): with no ``gates:`` key configured, the
    shared disabled-status formatter every gate command relies on
    (``cli._load_gate_config_or_report``, backed by ``resolve_gate_config``)
    prints exactly ``{"gate": "newly_reachable_paths", "status": "disabled"}``
    and returns 0 for this gate's name -- asserted against the named
    ``constants.GATE_STATUS_DISABLED`` constant, not a hard-coded string.
    See this module's docstring for why this is the real surface to drive:
    ``log-newly-reachable`` itself never consults gate configuration."""

    def test_gate_disabled_prints_status_line_and_exits_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # A real scratch git fixture repo (Section 10's "hermetic journey ...
        # over scratch git fixture repos" framing, matching every sibling
        # journey in this module and directory) even though this particular
        # call never inspects the repo's contents -- config resolution alone
        # decides the disabled outcome.
        init_scratch_repo(tmp_path)
        cfg_path = tmp_path / "devbench.yaml"
        cfg_path.write_text(f"repos:\n  {self._REPO}:\n    default_branch: main\n", encoding="utf-8")
        monkeypatch.setenv("DEVBENCH_CONFIG_PATH", str(cfg_path))
        monkeypatch.delenv("DEVBENCH_GATE_NEWLY_REACHABLE_PATHS_ENABLED", raising=False)

        result = cli._load_gate_config_or_report("newly_reachable_paths", self._REPO)

        assert result == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == cli._gate_disabled_line("newly_reachable_paths")
        payload = json.loads(captured.out)
        assert payload == {"gate": "newly_reachable_paths", "status": GATE_STATUS_DISABLED}


class TestJourneyLogNewlyReachableErrorContract(_NewlyReachableJourneyFixtures):
    """AC-E8-T3-005 (spec 4.9, Section 7): the ``log-newly-reachable`` verb's
    error contract -- a usage error exits 2 naming the offending argument, an
    unknown unit id exits 1 naming the id -- driven through the real CLI
    over a real scratch fixture."""

    def test_omitting_method_exits_2_naming_the_argument(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = init_scratch_repo(tmp_path)
        write_scratch_file(repo, "README.md", "placeholder repo\n")
        commit_scratch_repo(repo, "seed repo")
        backlog_root, wu_file = self._seed_backlog_file(tmp_path)
        unit = self._make_unit()

        with self._patch_backlog(unit, repo, backlog_root):
            result = cli.cmd_log_newly_reachable(
                unit.id, "--path", "src/reducers/permissionReducer.ts", "--result", "verified"
            )

        captured = capsys.readouterr()
        assert result == 2
        assert "--method" in captured.err
        assert "[NEWLY_REACHABLE" not in wu_file.read_text(encoding="utf-8")

    def test_unknown_unit_id_exits_1_naming_the_id(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = []
        with patch("devbench.cli.BacklogParser", return_value=mock_parser):
            result = cli.cmd_log_newly_reachable(
                "GHOST-UNIT-404",
                "--path",
                "src/x.py",
                "--method",
                "manual",
                "--result",
                "verified",
            )

        captured = capsys.readouterr()
        assert result == 1
        assert "GHOST-UNIT-404" in captured.err


class TestJourneyTaskTypeTaxonomyGatesAcceptanceCriterion:
    """AC-E8-T3-006 (spec 4.9a, 1.3 S1): the newly-reachable-paths
    acceptance-criterion line ``generate_draft_md`` (E8-F1-S1-T1) appends is
    keyed on ``ProposedTask.task_type`` resolving to
    ``constants.TASK_TYPE_BEHAVIOR_FIX`` -- present for that type alone
    across the full six-type taxonomy, and no Definition-of-Done line is
    ever appended for any type.

    The taxonomy tuple, the ``ProposedTask`` factory
    (``make_newly_reachable_keying_task``) and the Definition-of-Done
    assertion (``assert_no_newly_reachable_definition_of_done_line``) are
    shared with ``tests/test_backlog/test_proposal.py``'s
    ``TestNewlyReachableTaskTypeKeying`` via
    ``tests/test_integration/conftest.py``, rather than hand-copied here --
    both suites pin the same invariant against the same real, unmocked
    ``generate_draft_md``.
    """

    @pytest.mark.parametrize(("task_type", "expect_ac_line"), NEWLY_REACHABLE_TASK_TYPE_AC_LINE_EXPECTATIONS)
    def test_ac_line_present_for_behavior_fix_only(self, task_type: str, expect_ac_line: bool) -> None:
        md = generate_draft_md(
            make_newly_reachable_keying_task(task_type),
            repo="caylent-solutions/devbench",
            source_task_id="E9-F1-S1-T1",
            generated_at="2026-08-30T00:00:00Z",
        )
        assert ("log-newly-reachable" in md) is expect_ac_line, (
            f"task_type={task_type!r}: expected AC-line presence={expect_ac_line}, draft was: {md!r}"
        )

    @pytest.mark.parametrize("task_type", NEWLY_REACHABLE_TASK_TYPE_TAXONOMY)
    def test_no_definition_of_done_line_appended_for_any_task_type(self, task_type: str) -> None:
        md = generate_draft_md(
            make_newly_reachable_keying_task(task_type),
            repo="caylent-solutions/devbench",
            source_task_id="E9-F1-S1-T1",
            generated_at="2026-08-30T00:00:00Z",
        )
        assert_no_newly_reachable_definition_of_done_line(md)


class TestJourneyAttributionNeverExpandsBeyondTheNamedPath(_NewlyReachableJourneyFixtures):
    """AC-E8-T3-007 (spec 4.3, AC-9): ``log-newly-reachable`` never resolves
    or filters by the unit's Changes-Manifest scope -- it records exactly the
    ``--path`` value the caller names, whether or not that path is inside
    the seeded Manifest, and never silently adds attribution for any OTHER
    file. A file outside the unit's resolved scope (here, the seeded
    Manifest names ``src/reducers/permissionReducer.ts`` only) never appears
    in this suite's attributed-findings assertions."""

    def test_out_of_manifest_path_is_recorded_verbatim_with_no_extra_attribution(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = init_scratch_repo(tmp_path)
        write_scratch_file(repo, "src/legacy/unrelated_module.ts", "export const legacy = true;\n")
        commit_scratch_repo(repo, "seed an out-of-manifest file the marker will name")
        backlog_root, wu_file = self._seed_backlog_file(tmp_path)
        unit = self._make_unit()

        with self._patch_backlog(unit, repo, backlog_root):
            result = cli.cmd_log_newly_reachable(
                unit.id,
                "--path",
                "src/legacy/unrelated_module.ts",
                "--method",
                "manual",
                "--result",
                "verified",
            )
        capsys.readouterr()

        assert result == 0, "log-newly-reachable must never reject a path outside the unit's own Manifest scope"
        content = wu_file.read_text(encoding="utf-8")
        assert "[NEWLY_REACHABLE] src/legacy/unrelated_module.ts manual verified" in content
        assert content.count("[NEWLY_REACHABLE]") == 1, (
            f"exactly one marker for the one named path, no implicit scope-derived attribution added: {content!r}"
        )
        assert "src/reducers/permissionReducer.ts" not in content.split("[NEWLY_REACHABLE]", 1)[1], (
            "the in-Manifest file must never be silently attributed alongside the out-of-Manifest marker"
        )
