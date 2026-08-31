"""Hermetic round-trip journey suite for the layout_geometry gate (E10-F2-S1-T1).

Spec `integration-reality-gates-hardening.md` section 10 requires one
hermetic per-gate journey suite at
`tests/test_integration/test_gate_<name>_e2e.py`, driving the real CLI over
scratch git fixture repos and covering the block, pass, disabled and waiver
cases. This module is that suite for `layout_geometry` (spec 4.9c; AC-22).

Unlike the gates that ship a dedicated `check-<gate>` CLI verb (`ancestry`,
`write_path_audit`, `reachability`, `shared_file_impact`,
`fixture_consistency`), `layout_geometry` has no verb of its own. Its
`[LAYOUT-AC]`-tag grammar rule (`devbench.backlog.manager.BacklogManager.
_check_layout_ac_grammar`, wired into `BacklogManager.validate` as Check
29) is enforced entirely inside `validate-backlog`
(`devbench.cli.cmd_validate_backlog`), and that call is UNCONDITIONAL: it
runs whether or not `gates.layout_geometry` is present in a workspace's
config at all. The `gates.layout_geometry` config block (E10-F1-S1-T2)
governs only the gate's `devbench gates` status line; whether a `log-waiver`
exception for this gate requires `--operator` is decided entirely by
`constants.GATE_TIERS` (`layout_geometry` is judge-evidence tier), which
`cmd_log_waiver` consults directly -- it never reads `resolve_gate_config`
at all, so the config block plays no role in the waiver route (round 2 of
this module corrects a round-1 docstring claim to the contrary; the waiver
journey below succeeds with no `gates.layout_geometry` block present in its
own scratch config, which is itself evidence for the corrected claim). The
disabled journey below (`TestJourneyDisabledConfigImposesNothingOnUntagged
Backlog`) proves AC-TEST-004's literal requirement -- an untagged backlog
validates clean with the gate absent from config -- and additionally
confirms `devbench gates` reports the gate `disabled` under that same
config, so the "gate imposes nothing" story is not resting on an
unverified assumption about the config file's shape.

"Real CLI, as a subprocess" (AC-TEST-007; this module's round-2 fix):
every journey below spawns the actual `devbench` CLI entry point
(`devbench.cli.main`) via `_LayoutJourneyFixtures._run_devbench`, never
importing or calling `devbench.cli.cmd_*` in-process and never patching
any `devbench.cli` module global or mocking `BacklogParser`.

Round-3 fix (this revision): the CLI subprocess is now spawned as
`sys.executable -m devbench.cli <verb>` -- the interpreter ALREADY running
this test process -- instead of a nested `uv run devbench` invocation.
Round 2's `uv run devbench` re-resolved the project through the package
manager on every call and inherited `os.environ.copy()` wholesale; that
combination let a second, older `devbench` checkout elsewhere on this
workspace's ambient `PATH` (with no layout gate at all) win the
resolution in some environments, producing a fixture-order-independent
but environment-dependent failure that stayed invisible when the suite
ran alone and surfaced only inside a whole-repo `make validate` run. Using
`sys.executable` removes the re-resolution step entirely: the child
necessarily imports `devbench` from exactly the same `sys.path` this test
process itself already resolved it from, which
`_pin_child_resolves_build_under_test` (an autouse, module-scoped fixture)
proves for the exact minimal environment every journey uses, rather than
assuming it. The child environment is no longer `os.environ.copy()`
either: `_base_child_env` builds an explicit, minimal environment
(`PATH`, `HOME`, plus each journey's own `DEVBENCH_WORKSPACE_ROOT` /
`DEVBENCH_CONFIG_PATH`) so nothing ambient beyond what a real invocation
genuinely needs can leak into the child. Each subprocess gets its own
`DEVBENCH_WORKSPACE_ROOT` and `DEVBENCH_CONFIG_PATH` environment variables
pointing at that journey's scratch git fixture repo and scratch
`devbench.yaml` -- the same resolution path `devbench.config` uses for a
real operator invocation, so `WORKSPACE_ROOT`, `BACKLOG_ROOT`,
`BACKLOG_INDEX` and `REPO_LOCAL_PATHS` are all freshly (re-)derived inside
the child process from real, on-disk fixture state rather than patched in
the parent test process. This is a stronger fidelity guarantee than an
in-process `cli.cmd_*()` call patching module globals can offer, since a
fresh process also re-executes `devbench.config`'s own import-time
config-loading and validation path. No network access occurs in any
journey (`validate-backlog`, `gates`, `log-waiver` and `read-unit` all
resolve purely from local scratch-repo and scratch-config state); no
journey uses a time-based wait.

Fixture idiom: the scratch git fixture-repo factory
(`init_scratch_repo`/`write_scratch_file`/`commit_scratch_repo`) is
imported from `tests/test_tdd_gate.py` per this task's Definition of Ready
and the established precedent every sibling module in this directory sets.
The full-backlog validation fixture builder (`make_index`/`make_task`) is
imported from `tests/test_backlog/test_manager.py`'s `_ValidateRuleHarness`
-- the SAME harness `TestLayoutAcGrammar` (that module) already uses to
build a fully valid, zero-error work unit around the tagged AC line under
test -- rather than hand-rolling a second, narrower work-unit template here
that would (as this task's own reviewers flagged on the sibling unit) drift
from the one the grammar rule's own unit tests already validate against.
The waiver and read-unit journeys instead write a real, fully self-
contained work-unit file by hand (`_LayoutJourneyFixtures.
_write_waiver_unit_file`), because those two CLI verbs parse a work unit
via `BacklogParser.parse_work_unit_file`, which requires an `ID: Title`
top-level heading that `_ValidateRuleHarness.make_task` does not emit
(that harness's fixtures are consumed only by `BacklogManager.validate`,
which reads sections directly rather than through `parse_work_unit_file`).
`LAYOUT_AC_TAG` and `LAYOUT_GEOMETRY_KEYWORDS` are imported from
`devbench.constants` -- the single source of truth E10-F1-S1-T1 shipped --
never re-typed as literal strings in this module.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from test_backlog.test_manager import _ValidateRuleHarness
from test_tdd_gate import commit_scratch_repo, init_scratch_repo, write_scratch_file

import devbench
from devbench.constants import LAYOUT_AC_TAG, LAYOUT_GEOMETRY_KEYWORDS

H = _ValidateRuleHarness

# The repo every fixture backlog row declares. Must resolve through the
# real `devbench.config.ALLOWED_REPOS` allow-list (`cmd_read_unit`/
# `cmd_log_waiver` both call `resolve_repo`/`validate_repo` against it) --
# the same real, already-allow-listed repo every sibling gate e2e module in
# this directory uses for the identical reason.
_REPO = "caylent-solutions/devbench"

# A deterministic, non-hard-coded member of the real keyword vocabulary
# (spec 4.9c) -- picked by sorting `LAYOUT_GEOMETRY_KEYWORDS` rather than
# quoting a literal, so a future addition/removal of a keyword can never
# silently desync this module's fixtures from the shipped constant.
_KEYWORD = sorted(LAYOUT_GEOMETRY_KEYWORDS)[0]

# The env var this suite's own CLI-subprocess timeout is driven by (spec
# "Timeout Requirements": no hard-coded timeout values). Defaults to 60s,
# comfortably above the sub-second cost each `sys.executable -m
# devbench.cli <verb>` invocation showed when measured against this
# fixture shape.
_CLI_SUBPROCESS_TIMEOUT_SECONDS: int = int(os.environ.get("DEVBENCH_TEST_CLI_SUBPROCESS_TIMEOUT_SECONDS", "60"))

# `tests/test_integration/test_gate_layout_roundtrip_e2e.py` -> `tests` ->
# the devbench project root that owns `pyproject.toml` and `src/devbench`.
# Used only by the pin fixture below to prove THIS test process itself
# imports the build under test, independent of any subprocess concern.
_PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]


def _base_child_env() -> dict[str, str]:
    """The minimal, explicit environment every child CLI subprocess needs.

    Deliberately NOT ``os.environ.copy()``. Round-2 of this module spawned
    the CLI via a nested ``uv run devbench <verb>`` inheriting the WHOLE
    ambient environment; the package manager re-resolved the project on
    every call and the inherited environment let a second, older
    ``devbench`` checkout elsewhere on this workspace's ambient ``PATH``
    (with no layout gate at all) win the resolution in some environments,
    producing a failure that stayed invisible in an isolated run of this
    module and surfaced only inside a whole-repo ``make validate`` run
    (round-2 code_review FAIL, AC-CYCLE-002). Only three values survive
    from the ambient environment, each proven necessary rather than
    assumed: ``PATH`` (so the child's own ``git`` subprocess calls inside
    ``BacklogManager`` resolve a real git binary), ``HOME`` (git's global
    config/excludes lookup), and ``DEVBENCH_CLAUDE_MODEL`` (``devbench.
    config`` requires this at MODULE-IMPORT time via ``_require_env``,
    unconditionally, for every CLI command including the four this suite
    drives -- confirmed by running the child with it omitted, which fails
    every journey at import with ``devbench: DEVBENCH_CLAUDE_MODEL
    environment variable is not set`` before the layout gate ever runs;
    this suite makes no assertion that depends on its VALUE, only its
    presence). Every devbench-specific value the layout gate itself reads
    (``DEVBENCH_WORKSPACE_ROOT``, ``DEVBENCH_CONFIG_PATH``) is set
    explicitly per journey by the caller, never inherited.
    """
    path = os.environ.get("PATH", "").strip()
    home = os.environ.get("HOME", "").strip()
    claude_model = os.environ.get("DEVBENCH_CLAUDE_MODEL", "").strip()
    assert path, (
        "PATH is not set in the ambient test environment; cannot build the minimal child environment the "
        "devbench CLI subprocess needs to resolve a real git binary."
    )
    assert home, (
        "HOME is not set in the ambient test environment; cannot build the minimal child environment the "
        "devbench CLI subprocess needs for git's global config/excludes lookup."
    )
    assert claude_model, (
        "DEVBENCH_CLAUDE_MODEL is not set in the ambient test environment; devbench.config requires it at "
        "module-import time for every CLI command, independent of the layout gate under test."
    )
    return {"PATH": path, "HOME": home, "DEVBENCH_CLAUDE_MODEL": claude_model}


@pytest.fixture(scope="module", autouse=True)
def _pin_child_resolves_build_under_test() -> None:
    """Fail fast, once per module run, if the child interpreter every
    journey below spawns would resolve a DIFFERENT ``devbench`` install
    than this test process itself imports (round-3 code_review FAIL fix,
    AC-CYCLE-002).

    A second, older ``devbench`` checkout elsewhere on this workspace's
    ambient environment (verified to ship no layout gate at all) is
    exactly the mis-resolution ``_base_child_env`` above exists to
    eliminate. Because every real journey spawns ``sys.executable -m
    devbench.cli`` from THIS interpreter with that same minimal
    environment, the child's ``devbench`` package resolution is
    structurally pinned to whatever this interpreter's own ``sys.path``
    resolves -- the identical resolution this module already used to
    import ``devbench.constants`` above. This fixture PROVES that pin
    holds for the exact minimal environment the journeys use (a probe
    subprocess, not an assumption) instead of trusting it silently, and
    raises a named, actionable diagnostic rather than letting a
    mis-resolved child degrade into an opaque per-journey content-
    assertion failure.
    """
    probe = subprocess.run(
        [sys.executable, "-c", "import devbench, sys; sys.stdout.write(devbench.__file__)"],
        capture_output=True,
        text=True,
        env=_base_child_env(),
        check=False,
        timeout=_CLI_SUBPROCESS_TIMEOUT_SECONDS,
    )
    assert probe.returncode == 0 and probe.stdout, (
        f"PIN PROBE FAILED: could not import devbench in the child interpreter under the minimal environment "
        f"every journey below uses: rc={probe.returncode} stdout={probe.stdout!r} stderr={probe.stderr!r}"
    )
    child_devbench_file = Path(probe.stdout).resolve()
    parent_devbench_file = Path(devbench.__file__).resolve()
    assert child_devbench_file == parent_devbench_file, (
        "PIN FAILURE (AC-CYCLE-002): the devbench CLI subprocess this suite spawns would resolve a DIFFERENT "
        f"devbench install than this test process itself imports. child={child_devbench_file} "
        f"parent={parent_devbench_file}. Every journey below would silently exercise the wrong build; fix the "
        "child interpreter's site-packages resolution (PATH / sys.executable) before trusting any journey result."
    )
    assert _PROJECT_ROOT in parent_devbench_file.parents, (
        f"the devbench package THIS test process imports ({parent_devbench_file}) is not under the project root "
        f"this suite expects ({_PROJECT_ROOT}); this test process itself is not running against the build under "
        "test, independent of any subprocess concern."
    )


class _LayoutJourneyFixtures:
    """Journey-level helpers shared by every class in this module."""

    _REPO = _REPO

    def _write_gate_config(self, tmp_path: Path, *, gates_block: str = "", dir_name: str = "cfgdir") -> Path:
        """Write a scratch `devbench.yaml` resolved via `DEVBENCH_CONFIG_PATH`.

        `gates_block` defaults to empty -- an absent `gates:` key entirely,
        the D-17 built-in-disabled default every declared gate (including
        `layout_geometry`) loads into.
        """
        cfg_dir = tmp_path / dir_name
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_path = cfg_dir / "devbench.yaml"
        cfg_path.write_text(f"repos:\n  {self._REPO}:\n    default_branch: main\n{gates_block}", encoding="utf-8")
        return cfg_path

    def _run_devbench(self, workspace_root: Path, config_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
        """Spawn the real `devbench` CLI entry point (`devbench.cli.main`) as a subprocess.

        Invoked as ``sys.executable -m devbench.cli`` -- the interpreter
        already running this test process -- rather than a nested package-
        manager invocation, so the child's `devbench` package resolution
        cannot re-derive to a different install (round-3 fix; see
        `_pin_child_resolves_build_under_test` and `_base_child_env`).
        `DEVBENCH_WORKSPACE_ROOT` and `DEVBENCH_CONFIG_PATH` point the
        child process's own fresh import of `devbench.config` at *this*
        journey's scratch fixture state; no `devbench.cli` module global is
        patched and no collaborator of the CLI under test is mocked
        (AC-TEST-007).
        """
        env = _base_child_env()
        env["DEVBENCH_WORKSPACE_ROOT"] = str(workspace_root)
        env["DEVBENCH_CONFIG_PATH"] = str(config_path)
        return subprocess.run(
            [sys.executable, "-m", "devbench.cli", *args],
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=_CLI_SUBPROCESS_TIMEOUT_SECONDS,
        )

    def _write_waiver_unit_file(self, backlog_root: Path, unit_id: str, *, status: str = "in-progress") -> Path:
        """Write a real, fully self-contained work-unit `.md` file.

        Carries the `ID: Title` heading `BacklogParser.parse_work_unit_file`
        requires (`_ValidateRuleHarness.make_task`'s fixtures deliberately
        omit it -- see the module docstring), a real `## Target Repository`
        block so `resolve_repo`/`validate_repo` succeed, and a real
        `## TDD Cycle Log`/`## Comments` pair -- the `log-waiver` audit-
        marker insertion point (spec 4.3 evidence-horizon rule) and the
        boundary `read-unit --strip-comments` truncates at.
        """
        backlog_root.mkdir(parents=True, exist_ok=True)
        wu_file = backlog_root / f"{unit_id}.md"
        wu_file.write_text(
            f"# {unit_id}: Layout round-trip journey task\n\n"
            f"## Status: {status}\n\n"
            f"## Target Repository\n\n"
            f"- **Repo:** `{self._REPO}`\n\n"
            f"## TDD Cycle Log\n\n"
            f"## Comments\n",
            encoding="utf-8",
        )
        return wu_file

    def _write_waiver_index(self, repo: Path, unit_id: str, *, status: str = "in-progress") -> Path:
        return H.make_index(
            repo,
            f"| {unit_id} | Waiver journey task | Task | {status} | none | {self._REPO} | `backlog/{unit_id}.md` |\n",
        )


# ---------------------------------------------------------------------------
# Pass journey (spec 4.9c, AC-22; AC-TEST-001).
# ---------------------------------------------------------------------------


class TestJourneyPassCleanTaggedBulletValidatesWithZeroErrors(_LayoutJourneyFixtures):
    """AC-TEST-001: a scratch backlog whose Task carries a correctly tagged
    AC line validates with exit code 0 and empty error output from the
    real `devbench validate-backlog` subprocess."""

    def test_correctly_tagged_ac_line_validates_clean_through_real_cli(self, tmp_path: Path) -> None:
        repo = init_scratch_repo(tmp_path)
        backlog_dir = repo / "backlog"
        backlog_dir.mkdir()
        unit_id = "EX-F1-S1-T1"
        ac_block = f"- [ ] AC-UI-001 {LAYOUT_AC_TAG} Verify behavior involving {_KEYWORD} at 320px.\n"
        H.make_task(
            backlog_dir, unit_id, self._REPO, "| `docs/notes.md` | new |\n", ac_block=ac_block, task_type="docs"
        )
        H.make_index(
            repo,
            f"| {unit_id} | Pass journey task | Task | in-queue | none | {self._REPO} | `backlog/{unit_id}.md` |\n",
        )
        commit_scratch_repo(repo, "seed pass-journey fixture backlog")

        cfg_path = self._write_gate_config(tmp_path)
        result = self._run_devbench(repo, cfg_path, "validate-backlog")

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "FAILED" not in result.stdout, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert LAYOUT_AC_TAG not in result.stdout, (
            f"a clean tagged unit must raise no layout finding: stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert result.stderr == "", (
            f"validate-backlog must write nothing to stderr on a clean pass: stdout={result.stdout!r} "
            f"stderr={result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# Block journeys (spec 4.9c, AC-14; AC-TEST-002, AC-TEST-003).
# ---------------------------------------------------------------------------


class TestJourneyBlockMisplacedTagNamesOffendingUnitOnly(_LayoutJourneyFixtures):
    """AC-TEST-002: a `[LAYOUT-AC]` tag placed in `## Description` instead
    of on an AC line produces a non-zero exit and an error naming the
    offending work-unit id, while a correctly tagged sibling Task in the
    same backlog is not reported."""

    def test_tag_misplaced_in_description_blocks_and_spares_clean_sibling(self, tmp_path: Path) -> None:
        repo = init_scratch_repo(tmp_path)
        backlog_dir = repo / "backlog"
        backlog_dir.mkdir()

        offending_id = "EX-F1-S1-T1"
        H.make_task(
            backlog_dir,
            offending_id,
            self._REPO,
            "| `docs/notes-a.md` | new |\n",
            ac_block="- [ ] AC-UI-001 Verify basic task behavior.\n",
            description_body=(
                f"This unit adds {LAYOUT_AC_TAG} handling for {_KEYWORD} sizing, but the tag is misplaced "
                "here instead of on the AC line.\n"
            ),
            task_type="docs",
        )
        clean_id = "EX-F1-S1-T2"
        H.make_task(
            backlog_dir,
            clean_id,
            self._REPO,
            "| `docs/notes-b.md` | new |\n",
            ac_block=f"- [ ] AC-UI-001 {LAYOUT_AC_TAG} Verify behavior involving {_KEYWORD} at 320px.\n",
            task_type="docs",
        )
        H.make_index(
            repo,
            f"| {offending_id} | Misplaced tag task | Task | in-queue | none | {self._REPO} | "
            f"`backlog/{offending_id}.md` |\n"
            f"| {clean_id} | Clean sibling task | Task | in-queue | none | {self._REPO} | `backlog/{clean_id}.md` |\n",
        )
        commit_scratch_repo(repo, "seed misplacement-block fixture backlog")

        cfg_path = self._write_gate_config(tmp_path)
        result = self._run_devbench(repo, cfg_path, "validate-backlog")

        assert result.returncode == 1, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert offending_id in result.stdout, f"offending unit id missing from output: stdout={result.stdout!r}"
        assert "## Description" in result.stdout, f"the offending section must be named: stdout={result.stdout!r}"
        assert clean_id not in result.stdout, (
            f"a correctly tagged sibling Task must not be reported alongside the misplaced-tag finding: "
            f"stdout={result.stdout!r}"
        )


class TestJourneyBlockUnknownKeywordNamesOffendingLine(_LayoutJourneyFixtures):
    """AC-TEST-003: a tagged AC line naming a keyword absent from
    `LAYOUT_GEOMETRY_KEYWORDS` produces a non-zero exit with the offending
    unit id and the offending line quoted, while a correctly tagged
    sibling Task is not reported."""

    def test_unknown_keyword_blocks_and_quotes_the_offending_line(self, tmp_path: Path) -> None:
        repo = init_scratch_repo(tmp_path)
        backlog_dir = repo / "backlog"
        backlog_dir.mkdir()

        offending_id = "EX-F1-S1-T1"
        offending_line = f"- [ ] AC-UI-001 {LAYOUT_AC_TAG} Verify totally unrelated numeric parsing behavior."
        H.make_task(
            backlog_dir,
            offending_id,
            self._REPO,
            "| `docs/notes-a.md` | new |\n",
            ac_block=offending_line + "\n",
            task_type="docs",
        )

        clean_id = "EX-F1-S1-T2"
        H.make_task(
            backlog_dir,
            clean_id,
            self._REPO,
            "| `docs/notes-b.md` | new |\n",
            ac_block=f"- [ ] AC-UI-001 {LAYOUT_AC_TAG} Verify behavior involving {_KEYWORD} at 320px.\n",
            task_type="docs",
        )
        H.make_index(
            repo,
            f"| {offending_id} | Unknown keyword task | Task | in-queue | none | {self._REPO} | "
            f"`backlog/{offending_id}.md` |\n"
            f"| {clean_id} | Clean sibling task | Task | in-queue | none | {self._REPO} | `backlog/{clean_id}.md` |\n",
        )
        commit_scratch_repo(repo, "seed unknown-keyword-block fixture backlog")

        cfg_path = self._write_gate_config(tmp_path)
        result = self._run_devbench(repo, cfg_path, "validate-backlog")

        assert result.returncode == 1, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert offending_id in result.stdout, f"offending unit id missing from output: stdout={result.stdout!r}"
        assert offending_line in result.stdout, f"offending AC line must be quoted verbatim: stdout={result.stdout!r}"
        assert clean_id not in result.stdout, (
            f"a correctly tagged sibling Task must not be reported alongside the unknown-keyword finding: "
            f"stdout={result.stdout!r}"
        )


# ---------------------------------------------------------------------------
# Disabled journey (spec 4.1; AC-TEST-004).
# ---------------------------------------------------------------------------


class TestJourneyDisabledConfigImposesNothingOnUntaggedBacklog(_LayoutJourneyFixtures):
    """AC-TEST-004: with `gates.layout_geometry` absent from the scratch
    workspace config entirely, an untagged backlog validates clean and no
    layout finding is emitted -- and `devbench gates` independently reports
    the gate `disabled` under that same config, confirming the config
    really carries no `layout_geometry` block rather than merely being
    unread by this assertion."""

    def test_untagged_backlog_validates_clean_and_gate_reports_disabled(self, tmp_path: Path) -> None:
        cfg_path = self._write_gate_config(tmp_path, gates_block="")

        gates_result = self._run_devbench(tmp_path, cfg_path, "gates")
        assert gates_result.returncode == 0, f"stdout={gates_result.stdout!r} stderr={gates_result.stderr!r}"
        layout_row = next(
            (line for line in gates_result.stdout.splitlines() if line.startswith("layout_geometry")), None
        )
        assert layout_row is not None, f"no layout_geometry row in gates output: stdout={gates_result.stdout!r}"
        assert "disabled" in layout_row, (
            f"expected layout_geometry disabled, got row: {layout_row!r} (full stdout={gates_result.stdout!r})"
        )

        repo = init_scratch_repo(tmp_path, dir_name="untagged_repo")
        backlog_dir = repo / "backlog"
        backlog_dir.mkdir()
        unit_id = "EX-F1-S1-T1"
        H.make_task(
            backlog_dir,
            unit_id,
            self._REPO,
            "| `docs/notes.md` | new |\n",
            ac_block="- [ ] AC-UI-001 Verify basic task behavior with no layout tagging at all.\n",
            task_type="docs",
        )
        H.make_index(
            repo,
            f"| {unit_id} | Untagged task | Task | in-queue | none | {self._REPO} | `backlog/{unit_id}.md` |\n",
        )
        commit_scratch_repo(repo, "seed disabled-journey fixture backlog")

        validate_result = self._run_devbench(repo, cfg_path, "validate-backlog")
        assert validate_result.returncode == 0, f"stdout={validate_result.stdout!r} stderr={validate_result.stderr!r}"
        assert LAYOUT_AC_TAG not in validate_result.stdout, (
            f"an untagged backlog must never raise a layout finding: stdout={validate_result.stdout!r}"
        )


# ---------------------------------------------------------------------------
# Waiver journey (spec 4.9, PM-5; AC-TEST-005).
# ---------------------------------------------------------------------------


class TestJourneyWaiverSurvivesEvidenceFetchWithNoOperatorRequired(_LayoutJourneyFixtures):
    """AC-TEST-005 (pass half): a `log-waiver` run for `layout_geometry`
    with a non-empty reason -- and no `--operator` flag, since
    `layout_geometry` is judge-evidence tier (spec 3.6) -- records a
    `[GATE_WAIVER layout_geometry]` marker that survives `read-unit
    --strip-comments`'s Evidence fetch."""

    def test_waiver_with_no_operator_flag_survives_strip_comments_evidence_fetch(self, tmp_path: Path) -> None:
        repo = init_scratch_repo(tmp_path)
        unit_id = "EX-F1-S1-T1"
        wu_file = self._write_waiver_unit_file(repo / "backlog", unit_id)
        self._write_waiver_index(repo, unit_id)
        commit_scratch_repo(repo, "seed waiver-journey fixture backlog")

        cfg_path = self._write_gate_config(tmp_path, gates_block="")
        waiver_result = self._run_devbench(
            repo,
            cfg_path,
            "log-waiver",
            "test_review",
            unit_id,
            "--gate",
            "layout_geometry",
            "--target",
            f"AC-UI-001:{_KEYWORD}",
            "--reason",
            "manually reviewed the layout construct against the real DOM at 320px",
        )
        assert waiver_result.returncode == 0, (
            f"log-waiver for a judge-evidence gate must succeed without --operator: "
            f"stdout={waiver_result.stdout!r} stderr={waiver_result.stderr!r}"
        )
        content_after_waiver = wu_file.read_text(encoding="utf-8")
        assert "[GATE_WAIVER layout_geometry]" in content_after_waiver, (
            f"marker not written to disk: file content={content_after_waiver!r} "
            f"(waiver stdout={waiver_result.stdout!r} stderr={waiver_result.stderr!r})"
        )
        assert _KEYWORD in content_after_waiver, f"file content={content_after_waiver!r}"

        read_result = self._run_devbench(repo, cfg_path, "read-unit", "--strip-comments", unit_id)
        assert read_result.returncode == 0, f"stdout={read_result.stdout!r} stderr={read_result.stderr!r}"
        payload = json.loads(read_result.stdout)
        assert "[GATE_WAIVER layout_geometry]" in payload["content"], (
            f"waiver marker missing from rendered read-unit evidence: payload={payload!r} stderr={read_result.stderr!r}"
        )
        assert _KEYWORD in payload["content"], f"payload={payload!r}"
        assert "## Comments" not in payload["content"], (
            f"read-unit --strip-comments must truncate before '## Comments' (evidence-horizon rule): "
            f"payload={payload!r}"
        )


class TestJourneyWaiverEmptyReasonIsRejected(_LayoutJourneyFixtures):
    """AC-TEST-005 (reject half): an empty `--reason` is rejected with a
    usage exit (rc 2), no success payload on stdout, and an actionable
    `--reason`/`ERROR` message on stderr. No work-unit file exists in this
    fixture at all, so there is nothing on disk for a marker-absence
    assertion to check; the usage-exit and empty-stdout assertions below
    are the evidence that the rejection happened."""

    def test_empty_reason_rejected_with_usage_exit(self, tmp_path: Path) -> None:
        cfg_path = self._write_gate_config(tmp_path, gates_block="")
        result = self._run_devbench(
            tmp_path,
            cfg_path,
            "log-waiver",
            "test_review",
            "EX-F1-S1-T1",
            "--gate",
            "layout_geometry",
            "--target",
            f"AC-UI-001:{_KEYWORD}",
            "--reason",
            "",
        )
        assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert result.stdout == "", (
            f"a rejected usage call must never print a success payload to stdout: stdout={result.stdout!r} "
            f"stderr={result.stderr!r}"
        )
        assert "--reason" in result.stderr, f"stderr={result.stderr!r}"
        assert "ERROR" in result.stderr, f"stderr={result.stderr!r}"


# ---------------------------------------------------------------------------
# Round-trip journey (spec 4.9c, AC-22; AC-TEST-006).
# ---------------------------------------------------------------------------


class TestJourneyRoundTripKeywordSetSurvivesAuthoring(_LayoutJourneyFixtures):
    """AC-TEST-006/AC-22: a fixture spec file's layout construct is
    decomposed into a fixture backlog Task carrying a `[LAYOUT-AC]`-tagged
    AC line built from that construct's own wording. The keyword set the
    real, unmocked `validate-backlog` subprocess recognises on the
    authored AC line as written to disk is proven to equal the singleton
    set `{_KEYWORD}` the fixture spec's construct carries, via two
    independent halves driven through the SAME production parse path
    (spec 4.9c's 319-D regression this whole gate exists to close):

    - Positive half: the authored line, re-read off disk after the git
      commit round trip, validates clean -- `_KEYWORD`'s presence is
      sufficient for production to accept it.
    - Negative half: the identical line with ONLY `_KEYWORD` replaced by an
      unrelated, non-keyword phrase is rejected by the same production
      parser, naming the same unit id and quoting the corrupted line
      verbatim -- proving `_KEYWORD`'s presence (not merely the tag's
      presence) is what production actually keyed its positive verdict on,
      i.e. the recognised keyword set really is `{_KEYWORD}` and not
      vacuously "the tag is present" regardless of content. Round 1 of
      this journey compared two calls to the same test-local re-
      implementation of the production matcher against each other, which
      held trivially regardless of production behaviour; round 2 removes
      that duplicated matcher entirely and drives both halves through the
      real CLI instead.
    """

    # A fixed, deliberately keyword-free phrase substituted for `_KEYWORD`
    # in the negative half below, simulating an authoring round trip that
    # lost the keyword. Not derived from `LAYOUT_GEOMETRY_KEYWORDS` (it
    # must NOT be a keyword), but checked against every current member as
    # a fixture-hygiene precondition so a future keyword addition can never
    # silently turn this negative fixture into an accidental positive.
    _CORRUPTED_KEYWORD_PHRASE = "an entirely unrelated numeric parsing detail"

    def test_keyword_set_round_trips_from_fixture_spec_through_authored_ac_line(self, tmp_path: Path) -> None:
        # Normalized identically to production's own membership check
        # (`_check_layout_ac_tagged_bullet`: `re.sub(r"\s+", "",
        # stripped.lower())` then substring membership) so this precondition
        # stays honest against a future keyword whose normalized form only
        # appears once whitespace/case are folded away.
        normalized_corrupted_phrase = re.sub(r"\s+", "", self._CORRUPTED_KEYWORD_PHRASE.lower())
        for member in LAYOUT_GEOMETRY_KEYWORDS:
            assert member not in normalized_corrupted_phrase, (
                f"fixture precondition: the corrupted-keyword phrase must name no real keyword under production's "
                f"own normalisation, but {member!r} is a substring of the normalized phrase "
                f"{normalized_corrupted_phrase!r} (raw phrase={self._CORRUPTED_KEYWORD_PHRASE!r})"
            )

        construct = (
            f"The dropdown menu component must use {_KEYWORD} sizing so its popover width tracks its "
            "longest option label."
        )

        repo = init_scratch_repo(tmp_path)
        write_scratch_file(
            repo,
            "spec/dropdown-menu-sizing.md",
            f"# Fixture Spec: Dropdown Menu Sizing\n\n## Layout Requirement\n\n{construct}\n",
        )
        backlog_dir = repo / "backlog"
        backlog_dir.mkdir()
        unit_id = "EX-F1-S1-T1"
        ac_block = f"- [ ] AC-UI-001 {LAYOUT_AC_TAG} {construct}\n"
        wu_file = H.make_task(
            backlog_dir, unit_id, self._REPO, "| `docs/notes.md` | new |\n", ac_block=ac_block, task_type="docs"
        )
        H.make_index(
            repo,
            f"| {unit_id} | Round-trip journey task | Task | in-queue | none | {self._REPO} | "
            f"`backlog/{unit_id}.md` |\n",
        )
        commit_scratch_repo(repo, "seed round-trip fixture spec plus decomposed backlog Task")

        # Round trip: re-read the AUTHORED Task file off disk (not the
        # in-memory `ac_block` string) so both halves below exercise
        # exactly what production will read.
        authored_content = wu_file.read_text(encoding="utf-8")
        authored_ac_line = next(line for line in authored_content.splitlines() if line.startswith("- [ ] AC-UI-001"))
        assert _KEYWORD in authored_ac_line, (
            f"fixture precondition: the authored line must literally carry {_KEYWORD!r}: {authored_ac_line!r}"
        )

        cfg_path = self._write_gate_config(tmp_path)

        # Positive half: production accepts the authored, round-tripped line.
        pass_result = self._run_devbench(repo, cfg_path, "validate-backlog")
        assert pass_result.returncode == 0, f"stdout={pass_result.stdout!r} stderr={pass_result.stderr!r}"
        assert LAYOUT_AC_TAG not in pass_result.stdout, (
            f"the round-tripped tagged line must parse clean under validate-backlog: stdout={pass_result.stdout!r}"
        )

        # Negative half: the SAME line with only the keyword substituted is
        # rejected by the same production parser, over a second scratch
        # repo differing only in that substitution.
        corrupted_line = authored_ac_line.replace(_KEYWORD, self._CORRUPTED_KEYWORD_PHRASE)
        assert corrupted_line != authored_ac_line, "fixture precondition: substitution must change the line"
        corrupted_repo = init_scratch_repo(tmp_path, dir_name="repo_corrupted")
        corrupted_backlog_dir = corrupted_repo / "backlog"
        corrupted_backlog_dir.mkdir()
        H.make_task(
            corrupted_backlog_dir,
            unit_id,
            self._REPO,
            "| `docs/notes.md` | new |\n",
            ac_block=corrupted_line + "\n",
            task_type="docs",
        )
        H.make_index(
            corrupted_repo,
            f"| {unit_id} | Round-trip journey task (corrupted) | Task | in-queue | none | {self._REPO} | "
            f"`backlog/{unit_id}.md` |\n",
        )
        commit_scratch_repo(corrupted_repo, "seed corrupted round-trip fixture backlog")

        fail_result = self._run_devbench(corrupted_repo, cfg_path, "validate-backlog")
        assert fail_result.returncode == 1, f"stdout={fail_result.stdout!r} stderr={fail_result.stderr!r}"
        assert unit_id in fail_result.stdout, f"stdout={fail_result.stdout!r}"
        assert corrupted_line in fail_result.stdout, (
            f"the corrupted line must be quoted verbatim in the rejection: stdout={fail_result.stdout!r}"
        )
