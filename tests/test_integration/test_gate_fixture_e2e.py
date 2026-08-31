"""Hermetic fixture-consistency journey suite (E6-F2-S1-T2).

Before this task, `mark-done` already required a fresh
`[GATE_PASS fixture_consistency]` record for every unit whose repo had the
gate enabled (spec 4.7's final, done-path sentence; `constants.GATE_TIERS`
declared `fixture_consistency` machine-blocking since E2-F2), but no command
could write that record -- a deadlock for any such unit, not optionality,
whose only route to `done` was an operator waiver or disabling the gate for
the unit's repo. This module proves the now-wired gate end to end
over real, hermetic git fixture repos: block, pass, disabled, waiver,
stale-record and attribution journeys (the AC-14 matrix), plus the four
adversarial fixture shapes spec Section 10 names for this gate -- a typo'd
`identifier_field`, an empty canonical catalog, an in-fixture waiver visible
in `git diff`, and a seeded source literal found by the
`extract_source_literals` mode with `file:line`.

Fixture idiom: `_FixtureGateCmdFixtures` (and `_run_scratch_git`, its own git
plumbing helper) is imported from `tests/test_cli.py` (not hand-copied) --
the shared git-fixture / config-fixture / Manifest-seeding helper
`tests/test_cli.py::TestFixtureGateWritesGatePassRecord` and
`TestMarkDoneRequiresFixtureGatePass` already exercise at the unit level, and
the same scratch-git-repo shape `tests/test_tdd_gate.py`'s own factory uses
(real `git init`/`config`, never a mocked subprocess).

"Real CLI" here means the actual, unmocked `devbench.cli.cmd_check_fixture_consistency`,
`devbench.cli.cmd_mark_done` and `devbench.cli.cmd_log_waiver` implementations
-- the same functions the `devbench` executable dispatches to. No journey
mocks `check_fixture_consistency`, `_fixture_finding_is_attributable`, or any
other gate-internal function; the only subprocess boundary crossed is the
real `git` binary invoked against the real, on-disk fixture repos this module
builds. Every scratch repo lives under `tmp_path` (pytest's own hermetic
per-test temp directory), never inside this checkout.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

import pytest
from test_cli import _FixtureGateCmdFixtures, _run_scratch_git

from devbench import cli


class _JourneyFixtures(_FixtureGateCmdFixtures):
    """Journey-level helpers layered on `_FixtureGateCmdFixtures`.

    Adds a single-call `_run`/`_mark_done`/`_log_waiver` surface so a journey
    can drive `check-fixture-consistency`, `log-waiver` and `mark-done` back
    to back against one on-disk fixture without re-deriving the shared
    `_FixtureGateCmdFixtures._patches` context-manager tuple per call.
    """

    def _run(
        self, unit_id: str, repo: Path, backlog_root: Path, backlog_index: Path, *, runtime_config: Any = None
    ) -> int:
        patches = self._patches(unit_id, repo, backlog_root, backlog_index, runtime_config=runtime_config)
        with contextlib.ExitStack() as stack:
            for one_patch in patches:
                stack.enter_context(one_patch)
            return cli.cmd_check_fixture_consistency(unit_id)

    def _mark_done(
        self, unit_id: str, repo: Path, backlog_root: Path, backlog_index: Path, *, runtime_config: Any = None
    ) -> int:
        patches = self._patches(unit_id, repo, backlog_root, backlog_index, runtime_config=runtime_config)
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
        operator: bool = True,
    ) -> int:
        patches = self._patches(unit_id, repo, backlog_root, backlog_index)
        args = ["code_review", unit_id, "--gate", "fixture_consistency", "--target", target, "--reason", reason]
        if operator:
            args.append("--operator")
        with contextlib.ExitStack() as stack:
            for one_patch in patches:
                stack.enter_context(one_patch)
            return cli.cmd_log_waiver(*args)


class TestJourneyBlocksMissingKey(_JourneyFixtures):
    """Block journey (spec 4.7, AC-14, AC-16): a scan-target fixture referencing an
    identifier absent from the canonical source makes both `check-fixture-consistency`
    and the `mark-done` gate it feeds refuse, with no `[GATE_PASS fixture_consistency]`
    record written."""

    def test_missing_key_blocks_check_and_mark_done(self, tmp_path: Path) -> None:
        unit_id = "E0-F1-S1-T1"
        repo, backlog_root, backlog_index, wu_file = self._seed(tmp_path, unit_id, scan_value="GHOST-SKU")

        checked = self._run(unit_id, repo, backlog_root, backlog_index)
        assert checked == 1
        assert "[GATE_PASS fixture_consistency]" not in wu_file.read_text(encoding="utf-8")

        blocked = self._mark_done(unit_id, repo, backlog_root, backlog_index)
        assert blocked == 1
        assert "## Status: done" not in wu_file.read_text(encoding="utf-8")


class TestJourneyPassWritesGateRecordAndUnblocksDone(_JourneyFixtures):
    """Pass journey (spec 4.2/4.7, AC-16): a consistent fixture clears the gate, a single
    `[GATE_PASS fixture_consistency]` record is persisted, and `mark-done` then proceeds."""

    def test_consistent_fixtures_pass_write_record_and_unblock_done(self, tmp_path: Path) -> None:
        unit_id = "E0-F1-S1-T1"
        repo, backlog_root, backlog_index, wu_file = self._seed(tmp_path, unit_id, scan_value="A1")

        checked = self._run(unit_id, repo, backlog_root, backlog_index)
        assert checked == 0
        content_after_check = wu_file.read_text(encoding="utf-8")
        assert content_after_check.count("[GATE_PASS fixture_consistency]") == 1

        done = self._mark_done(unit_id, repo, backlog_root, backlog_index)
        assert done == 0
        assert "## Status: done" in wu_file.read_text(encoding="utf-8")


class TestJourneyDisabledGateSelfReports(_JourneyFixtures):
    """Disabled journey (spec 4.1, AC-4): with no `canonical_sources` configured,
    `check-fixture-consistency` self-reports disabled and exits 0, and (separately,
    `gates.fixture_consistency.enabled: false`) `mark-done` imposes nothing for the gate."""

    def test_no_canonical_sources_self_reports_disabled(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        repo, backlog_root, backlog_index, wu_file = self._seed(tmp_path, unit_id, scan_value="A1")
        empty_runtime_config = self._runtime_config(repo, canonical_source_paths=())

        checked = self._run(unit_id, repo, backlog_root, backlog_index, runtime_config=empty_runtime_config)
        out = capsys.readouterr().out.strip()
        assert checked == 0
        assert json.loads(out) == {"gate": "fixture_consistency", "status": "disabled"}
        assert "[GATE_PASS fixture_consistency]" not in wu_file.read_text(encoding="utf-8")

        disabled_runtime_config = self._runtime_config(repo, enabled=False, canonical_source_paths=())
        done = self._mark_done(unit_id, repo, backlog_root, backlog_index, runtime_config=disabled_runtime_config)
        assert done == 0
        assert "## Status: done" in wu_file.read_text(encoding="utf-8")


class TestJourneyOperatorWaiverUnblocks(_JourneyFixtures):
    """Waiver journey (spec 4.9): an operator-attributed `log-waiver` unblocks
    `mark-done` even though the check itself still reports the blocking finding."""

    def test_operator_waiver_unblocks_done_despite_block(self, tmp_path: Path) -> None:
        unit_id = "E0-F1-S1-T1"
        repo, backlog_root, backlog_index, wu_file = self._seed(tmp_path, unit_id, scan_value="GHOST-SKU")

        checked = self._run(unit_id, repo, backlog_root, backlog_index)
        assert checked == 1

        waived = self._log_waiver(
            unit_id,
            repo,
            backlog_root,
            backlog_index,
            target="mock_lookup.json",
            reason="reviewed manually, safe pending catalog backfill",
        )
        assert waived == 0
        assert "[GATE_WAIVER fixture_consistency]" in wu_file.read_text(encoding="utf-8")

        done = self._mark_done(unit_id, repo, backlog_root, backlog_index)
        assert done == 0
        assert "## Status: done" in wu_file.read_text(encoding="utf-8")


class TestJourneyStaleRecordRefused(_JourneyFixtures):
    """Stale-record journey (spec 4.2 AC-7): editing an in-scope file after a
    `[GATE_PASS fixture_consistency]` record was captured refuses `mark-done`
    with the exact stale-record wording."""

    def test_in_scope_edit_after_pass_record_stales_and_blocks_done(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        repo, backlog_root, backlog_index, wu_file = self._seed(tmp_path, unit_id, scan_value="A1")

        checked = self._run(unit_id, repo, backlog_root, backlog_index)
        assert checked == 0
        assert "[GATE_PASS fixture_consistency]" in wu_file.read_text(encoding="utf-8")

        (repo / "mock_lookup.json").write_text(json.dumps([{"sku": "A1", "extra": "changed"}]), encoding="utf-8")

        capsys.readouterr()
        stale = self._mark_done(unit_id, repo, backlog_root, backlog_index)
        err = capsys.readouterr().err
        assert stale == 1
        assert "ERROR: gate 'fixture_consistency' record is stale (scope changed since it ran)" in err
        assert "## Status: done" not in wu_file.read_text(encoding="utf-8")


class TestJourneyAttributionStaysInScope(_JourneyFixtures):
    """Attribution journey (spec 4.3, E6-F2-S1-T2 AC-6): a catalog mismatch in a scan
    target OUTSIDE this unit's Changes Manifest is reported (repo-wide) but never
    attributed as blocking for this unit -- the run still passes and persists a
    `[GATE_PASS fixture_consistency]` record."""

    def test_out_of_manifest_mismatch_is_reported_but_never_blocks(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        repo, backlog_root, backlog_index, wu_file = self._seed(tmp_path, unit_id, scan_value="A1")
        # A SECOND scan target, seeded with a genuine mismatch, that is never declared
        # in the unit's Changes Manifest (`_seed` only declares catalog.json and
        # mock_lookup.json).
        (repo / "other_mock_lookup.json").write_text(json.dumps([{"sku": "OUT-OF-SCOPE-GHOST"}]), encoding="utf-8")
        runtime_config = self._runtime_config(repo, scan_target_paths=("mock_lookup.json", "other_mock_lookup.json"))

        result = self._run(unit_id, repo, backlog_root, backlog_index, runtime_config=runtime_config)
        out = capsys.readouterr().out

        assert result == 0
        # Reported repo-wide (spec 4.3): the out-of-scope mismatch's finding is still
        # printed in the report...
        assert "OUT-OF-SCOPE-GHOST" in out
        assert "other_mock_lookup.json" in out
        # ...but never attributed as blocking: the run still passes, and the record
        # still gets written, because the ONLY missing_key finding is out of scope.
        content = wu_file.read_text(encoding="utf-8")
        assert content.count("[GATE_PASS fixture_consistency]") == 1


class TestJourneyApostropheFilenameStillBlocks(_JourneyFixtures):
    """security_review round 5 HIGH (E6-F2-S1-T2): end-to-end reproduction of the exploit
    security_review found -- an IN-SCOPE scan-target fixture whose filename contains an
    apostrophe used to defeat spec 4.3 attribution entirely. Before the
    `FixtureFinding.location` fix, `cli._fixture_finding_location_path`'s regex (anchored
    on the free-text `Fixture '<location>'` message fragment, which `fixture_consistency`
    interpolates into a single-quoted slot with no escaping) truncated its capture at the
    first `'`, so a scan target legitimately named `o'brien.json` parsed to the bogus
    location `o` -- never a member of `scope_files` even though the REAL file,
    `o'brien.json`, IS declared in the unit's own Changes Manifest. The finding silently
    stopped blocking, a `[GATE_PASS fixture_consistency]` record was persisted, and
    `mark-done` reached `done` on an inconsistent fixture catalog. This journey proves
    the fixed, structured-field attribution treats an in-scope apostrophe-named fixture
    IDENTICALLY to an in-scope plain-named one (`TestJourneyBlocksMissingKey` above):
    both block `check-fixture-consistency` and `mark-done`."""

    def test_missing_key_in_apostrophe_named_in_scope_fixture_blocks_check_and_mark_done(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        repo, backlog_root, backlog_index, wu_file = self._seed(
            tmp_path, unit_id, scan_value="A1", extra_manifest_files=("o'brien.json",)
        )
        # A second, apostrophe-named scan target -- declared IN the unit's own Changes
        # Manifest via `extra_manifest_files` above -- carrying a genuine mismatch,
        # mirroring security_review's own `o'brien.json` reproduction exactly.
        (repo / "o'brien.json").write_text(json.dumps([{"sku": "GHOST-SKU"}]), encoding="utf-8")
        runtime_config = self._runtime_config(repo, scan_target_paths=("mock_lookup.json", "o'brien.json"))

        checked = self._run(unit_id, repo, backlog_root, backlog_index, runtime_config=runtime_config)
        out = capsys.readouterr().out

        assert checked == 1, (
            "an apostrophe in an in-scope fixture's filename must never defeat spec 4.3 "
            "attribution -- the finding must block exactly like a plain-named in-scope fixture"
        )
        assert "GHOST-SKU" in out
        assert "o'brien.json" in out
        assert "[GATE_PASS fixture_consistency]" not in wu_file.read_text(encoding="utf-8")

        blocked = self._mark_done(unit_id, repo, backlog_root, backlog_index, runtime_config=runtime_config)
        assert blocked == 1, "mark-done must refuse -- no record was ever written for an apostrophe-blocked run"
        assert "## Status: done" not in wu_file.read_text(encoding="utf-8")


class TestJourneyPathSpellingStillBlocks(_JourneyFixtures):
    """security_review, code_review and test_review (E6-F2-S1-T2): independently
    reproduced end-to-end exploit closed on orchestrator direction, since it reaches the
    SAME end state as `TestJourneyApostropheFilenameStillBlocks` above -- silently
    misattributing an IN-SCOPE finding as out-of-scope, persisting a
    `[GATE_PASS fixture_consistency]` record, and letting `mark-done` reach `done` on an
    inconsistent catalog. Before `fixture_consistency.normalize_repo_relative_path`, a
    scan target's configured `path` compared VERBATIM against the calling unit's
    resolved Changes-Manifest scope, so a scan target declared `./mock_lookup.json` or
    `sub/../mock_lookup.json` -- both naming the SAME in-Manifest file as the row-1
    control (`mock_lookup.json`, already proven blocking by `TestJourneyBlocksMissingKey`
    above) -- silently reached `status: "pass"`; a fourth row nests the identical bug one
    directory deep. This journey proves rows 2-4 of test_review's reproduction table now
    block `check-fixture-consistency` and `mark-done` identically to the row-1 control."""

    def test_leading_dot_slash_still_blocks_and_mark_done_refuses(self, tmp_path: Path) -> None:
        unit_id = "E0-F1-S1-T1"
        repo, backlog_root, backlog_index, wu_file = self._seed(tmp_path, unit_id, scan_value="GHOST-SKU")
        runtime_config = self._runtime_config(repo, scan_target_paths=("./mock_lookup.json",))

        checked = self._run(unit_id, repo, backlog_root, backlog_index, runtime_config=runtime_config)
        assert checked == 1, "row 2 (./mock_lookup.json) must block identically to the row-1 control"
        assert "[GATE_PASS fixture_consistency]" not in wu_file.read_text(encoding="utf-8")

        blocked = self._mark_done(unit_id, repo, backlog_root, backlog_index, runtime_config=runtime_config)
        assert blocked == 1, "mark-done must refuse -- no record was ever written for a dot-slash-blocked run"
        assert "## Status: done" not in wu_file.read_text(encoding="utf-8")

    def test_dot_dot_collapse_still_blocks_and_mark_done_refuses(self, tmp_path: Path) -> None:
        unit_id = "E0-F1-S1-T1"
        repo, backlog_root, backlog_index, wu_file = self._seed(tmp_path, unit_id, scan_value="GHOST-SKU")
        # `sub/` must actually exist on disk: the OS must traverse into it before ".."
        # can walk back out, even though the collapsed logical target never lives there.
        (repo / "sub").mkdir()
        runtime_config = self._runtime_config(repo, scan_target_paths=("sub/../mock_lookup.json",))

        checked = self._run(unit_id, repo, backlog_root, backlog_index, runtime_config=runtime_config)
        assert checked == 1, "row 3 (sub/../mock_lookup.json) must block identically to the row-1 control"
        assert "[GATE_PASS fixture_consistency]" not in wu_file.read_text(encoding="utf-8")

        blocked = self._mark_done(unit_id, repo, backlog_root, backlog_index, runtime_config=runtime_config)
        assert blocked == 1, "mark-done must refuse -- no record was ever written for a dot-dot-blocked run"
        assert "## Status: done" not in wu_file.read_text(encoding="utf-8")

    def test_nested_leading_dot_slash_still_blocks_and_mark_done_refuses(self, tmp_path: Path) -> None:
        unit_id = "E0-F1-S1-T1"
        repo, backlog_root, backlog_index, wu_file = self._seed(
            tmp_path, unit_id, scan_value="A1", extra_manifest_files=("app/norm.json",)
        )
        (repo / "app").mkdir()
        (repo / "app" / "norm.json").write_text(json.dumps([{"sku": "GHOST-SKU"}]), encoding="utf-8")
        runtime_config = self._runtime_config(repo, scan_target_paths=("./app/norm.json",))

        checked = self._run(unit_id, repo, backlog_root, backlog_index, runtime_config=runtime_config)
        assert checked == 1, "row 4 (./app/norm.json) must block identically to the row-1 control"
        assert "[GATE_PASS fixture_consistency]" not in wu_file.read_text(encoding="utf-8")

        blocked = self._mark_done(unit_id, repo, backlog_root, backlog_index, runtime_config=runtime_config)
        assert blocked == 1, "mark-done must refuse -- no record was ever written for a nested-dot-slash-blocked run"
        assert "## Status: done" not in wu_file.read_text(encoding="utf-8")


class TestJourneyTypoedIdentifierFieldExitsLoud(_JourneyFixtures):
    """Adversarial journey (spec Section 10, AC-19): a typo'd `identifier_field` that
    matches zero canonical records exits 1 loudly (`status: "error"`), never a
    misleading pass, and writes no `[GATE_PASS fixture_consistency]` record."""

    def test_typoed_identifier_field_exits_1_loud(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        unit_id = "E0-F1-S1-T1"
        repo, backlog_root, backlog_index, wu_file = self._seed(tmp_path, unit_id, scan_value="A1")
        runtime_config = self._runtime_config(repo, canonical_identifier_field="skuu")

        result = self._run(unit_id, repo, backlog_root, backlog_index, runtime_config=runtime_config)
        captured = capsys.readouterr()
        out, err = captured.out, captured.err

        assert result == 1
        first_line = out.strip().splitlines()[0]
        assert json.loads(first_line) == {
            "gate": "fixture_consistency",
            "tier": "machine-blocking",
            "status": "error",
            "findings": 0,
        }
        assert "identifier field 'skuu' matched zero records" in err
        assert "[GATE_PASS fixture_consistency]" not in wu_file.read_text(encoding="utf-8")


class TestJourneyEmptyCanonicalCatalogExitsLoud(_JourneyFixtures):
    """Adversarial journey (spec Section 10, AC-19): an empty canonical catalog (zero
    records, so the identifier field matches zero records) exits 1 loudly, matching the
    typo'd-field case -- an empty canonical set is mass-false-positive territory, never
    a silent pass."""

    def test_empty_canonical_catalog_exits_1_loud(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        unit_id = "E0-F1-S1-T1"
        repo, backlog_root, backlog_index, wu_file = self._seed(tmp_path, unit_id, scan_value="A1")
        (repo / "catalog.json").write_text(json.dumps([]), encoding="utf-8")
        runtime_config = self._runtime_config(repo)

        result = self._run(unit_id, repo, backlog_root, backlog_index, runtime_config=runtime_config)
        err = capsys.readouterr().err

        assert result == 1
        assert "identifier field 'sku' matched zero records" in err
        assert "[GATE_PASS fixture_consistency]" not in wu_file.read_text(encoding="utf-8")


class TestJourneyInFixtureWaiverVisibleInDiff(_JourneyFixtures):
    """Adversarial journey (spec 4.7 bullet 5, spec Section 10, AC-19): an in-fixture
    `allow_missing` marker suppresses the `missing_key` finding, appears in the fixture
    file's own `git diff` (the waiver lives in the artifact, not workspace config), and
    the run still passes and persists a `[GATE_PASS fixture_consistency]` record."""

    def test_in_fixture_waiver_suppresses_finding_and_is_visible_in_git_diff(self, tmp_path: Path) -> None:
        unit_id = "E0-F1-S1-T1"
        repo, backlog_root, backlog_index, wu_file = self._seed(tmp_path, unit_id, scan_value="A1")
        _run_scratch_git(["add", "-A"], repo)
        _run_scratch_git(["commit", "-m", "baseline fixtures"], repo)

        (repo / "mock_lookup.json").write_text(
            json.dumps([{"sku": "SKU-DOES-NOT-EXIST", "allow_missing": {"reason": "models an empty lookup response"}}]),
            encoding="utf-8",
        )
        diff = _run_scratch_git(["diff", "--", "mock_lookup.json"], repo).stdout
        assert "allow_missing" in diff
        assert "SKU-DOES-NOT-EXIST" in diff

        result = self._run(unit_id, repo, backlog_root, backlog_index)

        assert result == 0
        content = wu_file.read_text(encoding="utf-8")
        assert content.count("[GATE_PASS fixture_consistency]") == 1


class TestJourneySeededSourceLiteralFoundWithFileLine(_JourneyFixtures):
    """Adversarial journey (spec 4.7 bullet 4, spec Section 10, AC-19): with
    `extract_source_literals: true`, a seeded identifier literal in a classified source
    file that is absent from the canonical catalog is flagged with `file:line`, blocking
    the run and the persisted `[GATE_PASS fixture_consistency]` record."""

    def test_seeded_source_literal_blocks_with_file_and_line(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        # `app/routes.py` is declared in the Changes Manifest (`extra_manifest_files`) so the
        # finding it produces is attributable (spec 4.3) and actually blocks -- a seeded
        # literal in a file OUTSIDE the unit's own scope is exactly the
        # TestJourneyAttributionStaysInScope case above, not this one.
        repo, backlog_root, backlog_index, wu_file = self._seed(
            tmp_path, unit_id, scan_value="A1", extra_manifest_files=("app/routes.py",)
        )
        source_path = repo / "app" / "routes.py"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(
            'ROUTE_TABLE = {\n    "name": "orders",\n    "sku": "SKU-SEEDED-LITERAL-GHOST",\n}\n', encoding="utf-8"
        )
        runtime_config = self._runtime_config(repo, extract_source_literals=True)

        result = self._run(unit_id, repo, backlog_root, backlog_index, runtime_config=runtime_config)
        out = capsys.readouterr().out

        assert result == 1
        assert "app/routes.py:3" in out
        assert "SKU-SEEDED-LITERAL-GHOST" not in out
        assert "[GATE_PASS fixture_consistency]" not in wu_file.read_text(encoding="utf-8")


class TestJourneySeededSourceLiteralOutsideScopePasses(_JourneyFixtures):
    """code_review round-1 W3 (E6-F2-S1-T2): the `Source file '<path>:<line>'` half of
    `FixtureFinding.location`-based attribution has no end-to-end journey that would
    fail if `fixture_consistency._MSG_SOURCE_LITERAL_MISSING_KEY` were reworded, because
    `TestJourneySeededSourceLiteralFoundWithFileLine` above declares its seeded-literal
    file IN scope, so a defect here (whether the pre-round-5 regex parse, or a future
    producer that stops populating `location`) would still block that run -- identical
    observable behaviour either way. This journey is the symmetrical, discriminating
    case: the seeded literal's file is deliberately left OUT of the unit's Changes
    Manifest, so a correctly-populated `location` is required for the run to pass.
    Round 5 (security_review HIGH): `location` is now set directly by
    `_check_source_literals` at construction (never recovered from `message`), so this
    journey also guards against a regression that silently stops populating the field --
    `_fixture_finding_is_attributable` treats a `None` location as fail-closed
    (unconditionally attributable/blocking), so this run would block instead of pass if
    that regressed."""

    def test_seeded_source_literal_outside_manifest_scope_still_passes(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit_id = "E0-F1-S1-T1"
        # `app/routes.py` is deliberately NOT in `extra_manifest_files` -- it stays
        # outside the unit's own Changes Manifest scope, unlike
        # `TestJourneySeededSourceLiteralFoundWithFileLine` above.
        repo, backlog_root, backlog_index, wu_file = self._seed(tmp_path, unit_id, scan_value="A1")
        source_path = repo / "app" / "routes.py"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(
            'ROUTE_TABLE = {\n    "name": "orders",\n    "sku": "SKU-OUT-OF-SCOPE-LITERAL-GHOST",\n}\n',
            encoding="utf-8",
        )
        runtime_config = self._runtime_config(repo, extract_source_literals=True)

        result = self._run(unit_id, repo, backlog_root, backlog_index, runtime_config=runtime_config)
        out = capsys.readouterr().out

        assert result == 0
        # Reported repo-wide (spec 4.3): the out-of-scope seeded literal's finding is
        # still printed, with its file:line location...
        assert "app/routes.py:3" in out
        assert "SKU-OUT-OF-SCOPE-LITERAL-GHOST" not in out
        # ...but never attributed as blocking, so the run passes and persists a record.
        content = wu_file.read_text(encoding="utf-8")
        assert content.count("[GATE_PASS fixture_consistency]") == 1
