"""Hermetic journey suite for the write-path audit gate (E7-F2-S1-T2, E7-F2-S1-T3).

The write-path audit gate (`cmd_check_write_path`,
`devbench.plugin_helpers.permission_flag_writepath.audit_write_path`) is
already covered at the unit level in `tests/test_cli.py` (the
`_WritePathCmdFixtures`-based classes) and
`tests/test_plugin_helpers/test_permission_flag_writepath.py`. What this
module adds is the `tests/test_integration` placement spec Section 10
requires per gate: one hermetic journey suite, over scratch git fixture
repos, driving the real `check-write-path` CLI entry point end to end,
covering every journey the campaign owes -- the flagship `default`
classification of an initialState-hardcoded flag (AC-20), the `live` pass
case, the `indeterminate` non-blocking case, the disabled status line
(AC-4), a judge waiver recorded through `log-waiver` that survives the
judge Evidence fetch, the scope-attribution rule (spec 4.3/AC-9/AC-WP-025;
see `TestJourneyAttributionStaysInScope` below), the `load_error` finding
for an undecodable file, and the Rails and Django layouts that must not
block (321-D28, AC-14).

E7-F2-S1-T2 (`Task Type: test-only`) first proved the gate's scope
attribution DIVERGED from spec 4.3/AC-9/AC-WP-025 and escalated the fix
rather than authoring it (that unit's Changes Manifest was this test
module alone). E7-F2-S1-T3 authored the production fix
(`cmd_check_write_path` now resolves the calling unit's own
Changes-Manifest scope via `work_unit_scope.resolve_changed_files` and
threads it into `audit_write_path`'s new keyword-only `scope` parameter)
and inverted `TestJourneyAttributionStaysInScope` (renamed from
`TestJourneyAttributionGapIsEscalatedNotSpecCompliant`) to prove the
spec-conformant direction. Every OTHER journey in this module now also
seeds a real Changes-Manifest scope naming the file(s) it expects to see
named in the rendered findings (`_WritePathCmdFixtures._patch_common`'s
`manifest_files` keyword, E7-F2-S1-T3) -- attribution is no longer
optional plumbing a journey can leave unexercised, since every write this
suite seeds is now subject to the same scope-limited rendering rule.

Fixture idiom: `_WritePathCmdFixtures` is imported from `tests/test_cli.py`
(not hand-copied) -- the shared unit/config/patch fixtures the gate's own
unit-level coverage already exercises, following the same precedent
`tests/test_integration/test_gate_reachability_e2e.py` sets by importing
`_ReachabilityCmdFixtures` from the same module. `_WritePathJourneyFixtures`
below layers only the backlog-file (`log-waiver`/`read-unit`) patch
surface and scratch-git-repo source-writing helpers this module needs on
top of that shared base. The scratch git fixture-repo factory
(`init_scratch_repo`/`write_scratch_file`/`commit_scratch_repo`) is
imported from `tests/test_tdd_gate.py` per this task's Definition of Ready
and Approach, rather than re-derived here; every fixture repo here is a
REAL, committed git repository, matching spec Section 10's "scratch git
fixture repos" framing for every gate journey suite in this campaign --
and, since E7-F2-S1-T3, also the repo `work_unit_scope.resolve_changed_files`
itself needs to hash a non-empty Changes-Manifest scope's files through
`git hash-object`, not merely a convention `audit_write_path`'s own
file-scan happens to share.

"Real CLI" here means the actual, unmocked `devbench.cli.cmd_check_write_path`,
`devbench.cli.cmd_log_waiver` and `devbench.cli.cmd_read_unit`
implementations -- the same functions the `devbench` executable dispatches
to. `devbench.cli.BacklogParser` (returning a `MagicMock` parser serving
the unit fixture directly), `devbench.cli.REPO_LOCAL_PATHS`,
`devbench.cli.BACKLOG_ROOT`, `devbench.cli.WORKSPACE_ROOT`,
`devbench.work_unit_scope.BACKLOG_ROOT` and
`devbench.work_unit_scope.BACKLOG_INDEX` are patched -- the same seam
every sibling gate e2e module in this directory uses (the last two,
independent of `devbench.cli.BacklogParser`, are what let
`work_unit_scope.resolve_changed_files` resolve a real scope against real
fixture data rather than the production backlog, per
`_WritePathCmdFixtures._patch_common`'s own docstring) -- plus the
`DEVBENCH_CONFIG_PATH`/`DEVBENCH_GATE_WRITE_PATH_AUDIT_ENABLED` env layer
`_WritePathCmdFixtures._enable_gate` sets, which is the actual
config-resolution path `cmd_check_write_path` reads `gates.write_path_audit`
through (spec 4.1, D-15). No journey mocks `audit_write_path`,
`_classify`, `_classify_rhs_expression`, `resolve_changed_files` or any
other gate-internal function.

Every assertion below was verified against the shipped
`cmd_check_write_path`/`audit_write_path` implementation (E7-F2-S1-T3).
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from test_cli import _WritePathCmdFixtures
from test_tdd_gate import commit_scratch_repo, init_scratch_repo, write_scratch_file

from devbench import cli
from devbench.backlog.work_unit import WorkUnit


class _WritePathJourneyFixtures(_WritePathCmdFixtures):
    """Journey-level helpers layered on `_WritePathCmdFixtures`.

    Adds a `## Comments`-terminated backlog-file seed and a `log-waiver`/
    `read-unit` patch surface (both of which resolve the work-unit file
    through `devbench.cli.BACKLOG_ROOT`, unlike `cmd_check_write_path`
    itself, which never reads the work-unit file at all -- only its
    resolved `repo`) so a journey can drive `check-write-path`, `log-waiver`
    and `read-unit` back to back against one on-disk fixture without
    re-deriving `_WritePathCmdFixtures`'s own unit/config fixtures.
    """

    def _seed_backlog_file(self, tmp_path: Path, unit_id: str) -> tuple[Path, Path]:
        """Write a scratch work-unit `.md` file with a `## Comments` section.

        Returns `(backlog_root, wu_file)`. `## TDD Cycle Log` (immediately
        before `## Comments`) is the audit-marker insertion point
        `cmd_log_waiver` writes `[GATE_WAIVER <gate>]` records into (spec
        4.3's evidence-horizon rule): present here so the waiver journey's
        marker lands in a real section rather than at end-of-file.
        """
        backlog_root = tmp_path / "backlog"
        backlog_root.mkdir(exist_ok=True)
        wu_file = backlog_root / f"{unit_id}.md"
        wu_file.write_text(
            f"# {unit_id}: Write-path journey task\n\n## Status: in-progress\n\n## TDD Cycle Log\n\n## Comments\n",
            encoding="utf-8",
        )
        return backlog_root, wu_file

    @contextlib.contextmanager
    def _patch_backlog_write(self, unit: WorkUnit, repo_path: Path, backlog_root: Path) -> Iterator[None]:
        """Patch surface for `log-waiver`/`read-unit`: adds `BACKLOG_ROOT`/`WORKSPACE_ROOT`
        (needed to resolve the work-unit `.md` file itself) on top of `_patch_common`'s
        `BacklogParser`/`REPO_LOCAL_PATHS` pair."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {self._REPO: repo_path}),
            patch("devbench.cli.BACKLOG_ROOT", backlog_root.parent),
            patch("devbench.cli.WORKSPACE_ROOT", backlog_root.parent),
        ):
            yield


class TestJourneyFlagshipInitialStateClassifiesDefault(_WritePathJourneyFixtures):
    """Flagship journey (spec Section 10, AC-20; 321-D03): a flag whose only
    assignment is a literal inside an `initialState` object classifies
    `default` through the real `check-write-path` CLI entry point, even
    though the file's path (`src/store/slices/...`) carries live-sounding
    vocabulary the pre-rework classifier used to misread as `live`. The
    spec 5.2 status line is asserted literally, field for field, including
    the `judge-evidence` tier (AC-WP-024; spec 5.2)."""

    def test_initial_state_literal_classifies_default_through_real_cli(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = init_scratch_repo(tmp_path)
        write_scratch_file(
            repo,
            "src/store/slices/permissionSlice.ts",
            "const initialState = {\n  isPremiumEligible: false,\n};\n",
        )
        commit_scratch_repo(repo, "seed initialState literal")
        self._enable_gate(tmp_path, monkeypatch, enabled=True)
        unit = self._make_unit("E1-F1-S1-T1")

        with self._patch_common(unit, repo, manifest_files=("src/store/slices/permissionSlice.ts",)):
            result = cli.cmd_check_write_path(unit.id, "--flag", "isPremiumEligible")

        captured = capsys.readouterr()
        assert result == 1, f"stdout={captured.out!r} stderr={captured.err!r}"
        first_line = json.loads(captured.out.splitlines()[0])
        assert first_line == {
            "gate": "write_path_audit",
            "tier": "judge-evidence",
            "status": "fail",
            "findings": 1,
            "flag": "isPremiumEligible",
            "verdict": "default",
        }
        assert "src/store/slices/permissionSlice.ts:2" in captured.out
        assert "expression_verdict=default" in captured.out


class TestJourneyLiveAndIndeterminateNeverBlock(_WritePathJourneyFixtures):
    """Pass journeys (spec Section 10, AC-14; AC-WP-005/006): a
    runtime-derived write classifies `live` and an unresolved shape
    classifies `indeterminate` with its evidence lines shown -- both exit 0,
    never as a block."""

    def test_runtime_derived_assignment_classifies_live_and_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = init_scratch_repo(tmp_path)
        write_scratch_file(
            repo,
            "src/reducers/permissionReducer.ts",
            "isPremiumEligible = action.payload.value;\n",
        )
        commit_scratch_repo(repo, "seed runtime-derived write")
        self._enable_gate(tmp_path, monkeypatch, enabled=True)
        unit = self._make_unit("E1-F1-S1-T1")

        with self._patch_common(unit, repo, manifest_files=("src/reducers/permissionReducer.ts",)):
            result = cli.cmd_check_write_path(unit.id, "--flag", "isPremiumEligible")

        captured = capsys.readouterr()
        assert result == 0, f"stdout={captured.out!r} stderr={captured.err!r}"
        first_line = json.loads(captured.out.splitlines()[0])
        assert first_line == {
            "gate": "write_path_audit",
            "tier": "judge-evidence",
            "status": "pass",
            "findings": 0,
            "flag": "isPremiumEligible",
            "verdict": "live",
        }
        assert "src/reducers/permissionReducer.ts:1" in captured.out

    def test_unresolved_identifier_classifies_indeterminate_and_passes_with_evidence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = init_scratch_repo(tmp_path)
        write_scratch_file(repo, "src/misc/assign.py", "isPremiumEligible = someUnknownVar\n")
        commit_scratch_repo(repo, "seed unresolved identifier")
        self._enable_gate(tmp_path, monkeypatch, enabled=True)
        unit = self._make_unit("E1-F1-S1-T1")

        with self._patch_common(unit, repo, manifest_files=("src/misc/assign.py",)):
            result = cli.cmd_check_write_path(unit.id, "--flag", "isPremiumEligible")

        captured = capsys.readouterr()
        assert result == 0, f"stdout={captured.out!r} stderr={captured.err!r}"
        first_line = json.loads(captured.out.splitlines()[0])
        assert first_line["status"] == "pass"
        assert first_line["verdict"] == "indeterminate"
        assert first_line["findings"] == 0
        assert "src/misc/assign.py:1" in captured.out
        assert "expression_verdict=indeterminate" in captured.out


class TestJourneyDisabledGateSelfReports(_WritePathJourneyFixtures):
    """Disabled journey (spec 4.1, AC-4): with the gate disabled, the exact
    shipped disabled status line (docs/cli-reference.md check-write-path;
    semantically the spec 4.1 line, rendered with `json.dumps` default
    separators) is printed and the run exits 0 before `--flag` is ever
    audited -- even though the seeded repo carries a genuine write the gate
    would otherwise find."""

    def test_disabled_gate_prints_exact_status_line_and_exits_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = init_scratch_repo(tmp_path)
        write_scratch_file(
            repo,
            "src/reducers/permissionReducer.ts",
            "isPremiumEligible = action.payload.value;\n",
        )
        commit_scratch_repo(repo, "seed write (must never be audited while disabled)")
        self._enable_gate(tmp_path, monkeypatch, enabled=False)
        unit = self._make_unit("E1-F1-S1-T1")

        with self._patch_common(unit, repo):
            result = cli.cmd_check_write_path(unit.id, "--flag", "isPremiumEligible")

        captured = capsys.readouterr()
        assert result == 0
        assert captured.out.strip() == '{"gate": "write_path_audit", "status": "disabled"}'
        assert captured.err == ""


class TestJourneyWaiverSurvivesEvidenceFetch(_WritePathJourneyFixtures):
    """Waiver journey (spec 4.3, 4.9): a `log-waiver` record for
    `write_path_audit` is written into the work unit's `## TDD Cycle Log`
    section, and the `[GATE_WAIVER write_path_audit]` marker still appears
    in `read-unit --strip-comments` output for that unit -- proving it
    survives the judge Evidence fetch (`## Comments` itself is truncated by
    that fetch)."""

    def test_gate_waiver_marker_survives_strip_comments_evidence_fetch(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = init_scratch_repo(tmp_path)
        write_scratch_file(repo, "README.md", "placeholder repo for the waiver journey\n")
        commit_scratch_repo(repo, "seed repo")
        backlog_root, wu_file = self._seed_backlog_file(tmp_path, "E1-F1-S1-T1")
        unit = self._make_unit("E1-F1-S1-T1")

        with self._patch_backlog_write(unit, repo, backlog_root):
            waived = cli.cmd_log_waiver(
                "code_review",
                unit.id,
                "--gate",
                "write_path_audit",
                "--target",
                "isPremiumEligible",
                "--reason",
                "reviewed manually, placeholder provider until the upstream API ships",
            )
        capsys.readouterr()
        assert waived == 0
        wu_content_after_waiver = wu_file.read_text(encoding="utf-8")
        assert "[GATE_WAIVER write_path_audit]" in wu_content_after_waiver
        assert "isPremiumEligible" in wu_content_after_waiver

        with self._patch_backlog_write(unit, repo, backlog_root):
            read_result = cli.cmd_read_unit("--strip-comments", unit.id)
        captured = capsys.readouterr()
        assert read_result == 0
        payload = json.loads(captured.out)
        assert "[GATE_WAIVER write_path_audit]" in payload["content"]
        assert "isPremiumEligible" in payload["content"]
        assert "## Comments" not in payload["content"]


class TestJourneyAttributionStaysInScope(_WritePathJourneyFixtures):
    """Attribution journey (spec 4.3, AC-9, AC-WP-025, E7-F2-S1-T3) -- SPEC
    COMPLIANT, matching the precedent
    `tests/test_integration/test_gate_reachability_e2e.py::TestJourneyAttributionStaysInScope`
    (and the sibling gates' own same-named classes).

    Spec 4.3's attribution rule is unqualified: "gate findings may name only
    files in `ScopeResult.files`" (a repo-wide gate reports repo-wide
    RESULTS but attributes BLAME only within scope). AC-9 is equally
    unqualified: "a file outside the unit's scope never appears in any
    gate's attributed findings." Section 10 lists the attribution case for
    every per-gate journey suite with no `write_path_audit` carve-out. This
    gate's own AC-WP-025 restates the same requirement for this module
    specifically.

    E7-F2-S1-T2 (`test-only`) proved this gap and escalated the production
    fix rather than authoring it (see that unit's own `[NEEDS_ESCALATION]`
    comment and its `write-proposal`); E7-F2-S1-T3 authored the fix
    (`cmd_check_write_path` resolves the unit's own Changes-Manifest scope
    via `work_unit_scope.resolve_changed_files` and threads it into
    `audit_write_path`'s new keyword-only `scope` parameter). This journey
    now proves the SHIPPED, spec-conformant behaviour: a live write outside
    the unit's own scope drives the SAME repo-wide `live` verdict a fully
    unscoped run would reach (spec 4.3's "report repo-wide RESULTS" half),
    but is never named in the rendered findings (spec 4.3's "attribute
    BLAME only within scope" half) -- while a live write that IS in scope
    still surfaces normally.
    """

    def test_out_of_manifest_live_write_never_surfaces_in_scope_write_still_does(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = init_scratch_repo(tmp_path)
        write_scratch_file(
            repo,
            "src/legacy/unrelated_module.ts",
            "isPremiumEligible = action.payload.value;\n",
        )
        write_scratch_file(
            repo,
            "src/reducers/permissionReducer.ts",
            "isPremiumEligible = action.payload.value;\n",
        )
        commit_scratch_repo(repo, "seed an out-of-manifest write alongside an in-manifest write")
        self._enable_gate(tmp_path, monkeypatch, enabled=True)
        unit = self._make_unit("E1-F1-S1-T1")

        with self._patch_common(unit, repo, manifest_files=("src/reducers/permissionReducer.ts",)):
            result = cli.cmd_check_write_path(unit.id, "--flag", "isPremiumEligible")

        captured = capsys.readouterr()
        assert result == 0, f"stdout={captured.out!r} stderr={captured.err!r}"
        first_line = json.loads(captured.out.splitlines()[0])
        # Repo-wide RESULT: the out-of-scope write still drives the overall verdict.
        assert first_line["status"] == "pass"
        assert first_line["verdict"] == "live"
        # Scope-limited BLAME: the out-of-scope write is never named in the findings...
        out_of_scope_lines = [line for line in captured.out.splitlines() if "src/legacy/unrelated_module.ts:1" in line]
        assert not out_of_scope_lines, f"out-of-scope write named in findings: {captured.out}"
        # ...while the in-scope write still surfaces, exactly as an unscoped run would show it.
        in_scope_lines = [line for line in captured.out.splitlines() if "src/reducers/permissionReducer.ts:1" in line]
        assert in_scope_lines, f"in-scope write missing from findings: {captured.out}"
        assert all("expression_verdict=live" in line for line in in_scope_lines)


class TestJourneyRailsAndDjangoLayoutsNeverBlock(_WritePathJourneyFixtures):
    """Adversarial layout journeys (spec 4.8, 321-D28; AC-14): a Rails
    (`app/models`/`app/controllers`) and a Django (`myapp/models.py`/
    `myapp/views.py`) layout each carry a literal-only model-layer write
    alongside a runtime-derived controller/view write. Neither layout
    produces a blocking outcome, and the runtime-derived controller/view
    site classifies `live`."""

    @pytest.mark.parametrize(
        ("case_id", "model_relative", "model_content", "runtime_relative", "runtime_content"),
        [
            pytest.param(
                "rails",
                "app/models/user.rb",
                (
                    "class User < ApplicationRecord\n"
                    "  def initialize(*)\n"
                    "    super\n"
                    "    self.is_premium_eligible = false\n"
                    "  end\n"
                    "end\n"
                ),
                "app/controllers/users_controller.rb",
                (
                    "class UsersController < ApplicationController\n"
                    "  def update\n"
                    "    self.is_premium_eligible = params[:eligible]\n"
                    "  end\n"
                    "end\n"
                ),
                id="rails",
            ),
            pytest.param(
                "django",
                "myapp/models.py",
                "class UserProfile(models.Model):\n    is_premium_eligible = models.BooleanField(default=False)\n",
                "myapp/views.py",
                "def update_eligibility(request):\n    is_premium_eligible = request.POST.get('eligible')\n",
                id="django",
            ),
        ],
    )
    def test_layout_never_blocks_and_runtime_write_classifies_live(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        case_id: str,
        model_relative: str,
        model_content: str,
        runtime_relative: str,
        runtime_content: str,
    ) -> None:
        repo = init_scratch_repo(tmp_path)
        write_scratch_file(repo, model_relative, model_content)
        write_scratch_file(repo, runtime_relative, runtime_content)
        commit_scratch_repo(repo, f"seed {case_id} layout")
        self._enable_gate(tmp_path, monkeypatch, enabled=True)
        unit = self._make_unit("E1-F1-S1-T1")

        with self._patch_common(unit, repo, manifest_files=(runtime_relative,)):
            result = cli.cmd_check_write_path(unit.id, "--flag", "is_premium_eligible")

        captured = capsys.readouterr()
        assert result == 0, f"{case_id} layout must never block: stdout={captured.out!r} stderr={captured.err!r}"
        first_line = json.loads(captured.out.splitlines()[0])
        assert first_line["status"] == "pass"
        assert first_line["verdict"] == "live"
        runtime_lines = [
            line for line in captured.out.splitlines() if line.strip().startswith(f"- {runtime_relative}:")
        ]
        assert runtime_lines, f"{case_id}: no finding line for {runtime_relative} in output: {captured.out}"
        assert any("expression_verdict=live" in line for line in runtime_lines), (
            f"{case_id}: runtime-derived {runtime_relative} write did not classify live: {captured.out}"
        )


class TestJourneyLoadErrorReportsWhileClassifyingReadableSources(_WritePathJourneyFixtures):
    """`load_error` journey (spec 4.8, Section 7; AC-WP-010/011): an
    undecodable file produces a `load_error` finding naming it, while the
    verdict is still computed from the readable sources."""

    def test_undecodable_file_produces_load_error_and_readable_sources_still_classify(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = init_scratch_repo(tmp_path)
        write_scratch_file(
            repo,
            "src/reducers/permissionReducer.ts",
            "isPremiumEligible = action.payload.value;\n",
        )
        undecodable = repo / "src" / "assets" / "binary_blob.py"
        undecodable.parent.mkdir(parents=True, exist_ok=True)
        undecodable.write_bytes(b"\xff\xfe\x00isPremiumEligible = true\x00")
        commit_scratch_repo(repo, "seed load_error fixture alongside a readable live write")
        self._enable_gate(tmp_path, monkeypatch, enabled=True)
        unit = self._make_unit("E1-F1-S1-T1")

        with self._patch_common(unit, repo, manifest_files=("src/reducers/permissionReducer.ts",)):
            result = cli.cmd_check_write_path(unit.id, "--flag", "isPremiumEligible")

        captured = capsys.readouterr()
        assert result == 0, f"stdout={captured.out!r} stderr={captured.err!r}"
        first_line = json.loads(captured.out.splitlines()[0])
        assert first_line["verdict"] == "live"
        assert "load_error src/assets/binary_blob.py:" in captured.out
        assert "src/reducers/permissionReducer.ts:1" in captured.out
        assert "expression_verdict=live" in captured.out
