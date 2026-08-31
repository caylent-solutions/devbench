"""Hermetic journey suite for the composition-root gate (E9-F2-S1-T1).

Companion to caylent-solutions/devbench-internal-backlog#11; source PR
caylent-solutions/devbench#316. Spec `integration-reality-gates-hardening.md`
section 10 requires one hermetic scenario suite per gate,
`tests/test_integration/test_gate_<name>_e2e.py`, driving the real CLI over
scratch git fixture repos and covering the block, pass, disabled, waiver,
stale-record and attribution cases. This module is that suite for
composition_root (spec 4.9(b); AC-9, AC-10, AC-14).

composition_root is a judge-evidence gate (spec 4.2, `constants.GATE_TIERS`),
not machine-blocking: there is no CLI verb that computes a composition-root
pass/fail exit code the way `check-write-path` or `check-reachability` do.
Its "block" and "pass" cases are therefore expressed as evidence facts, not
exit codes -- `TestJourneyBlockAndPassEvidenceDistinguishesCompositionRootCoverage`
below drives the real, unmocked `devbench.cli.cmd_read_unit` and
`devbench.cli.cmd_get_diff` (the two commands `test-reviewer` actually
calls to build its Evidence block) and asserts that surface distinguishes a
fixture unit whose `## Acceptance Criteria` has no composition-root item and
whose only test coverage is an isolated render (everything a judge needs to
raise `test_review:COMPOSITION_ROOT_MISSING`) from the same unit carrying
the AC item and a real `<Provider store={realStore}>` composition-root
test (never block-worthy). The remaining journeys cover the gate's
cross-cutting rules: a `[GATE_WAIVER composition_root]` marker written by
`log-waiver` survives `read-unit --strip-comments` (spec 4.3, AC-10); a
file outside the unit's resolved scope is never attributed (spec 4.3,
AC-9); the disabled config state imposes nothing on the fixture unit's
path to done (spec 4.1); and `scaffold-store-factory` writes its skeleton
once and refuses to overwrite it on a second, real, back-to-back
invocation (spec 4.9).

The `TestStructural*` classes at the top of this module are the regression
net for E9-F1-S1-T1 (spec 4.9(b), decision D-13, finding S1; gate G3): they
parse the shipped `test-reviewer.md` rubric, the `spec-to-backlog` SKILL
and `docs/composition-root-testing.md`, asserting the requirement is keyed
off the task `## Acceptance Criteria` line (never `## Definition of Done`
satisfaction), that the rubric names the structured rejection code
`test_review:COMPOSITION_ROOT_MISSING`, and that no machine-blocking
vocabulary is applied to this judge-evidence gate. Every structural
assertion is proven capable of failing against a mutated copy of the real
surface text, seeded under `tmp_path`, before it is trusted against the
shipped tree (Approach step 2).

Fixture idiom: `_ScaffoldStoreFactoryCmdFixtures` (the shared
scope-resolution + config-fixture helpers already backing the gate's
unit-level coverage in `tests/test_cli.py::TestCmdScaffoldStoreFactory`,
whose own `_patch_common` delegates to `_seed_scope_backlog`) is imported
from `tests/test_cli.py`, not hand-copied; the scratch git fixture-repo factory
(`init_scratch_repo`/`write_scratch_file`/`commit_scratch_repo`) is
imported from `tests/test_tdd_gate.py` per this task's Definition of
Ready, matching the established precedent
`test_gate_write_path_e2e.py`/`test_gate_ancestry_e2e.py`/
`test_gate_newly_reachable_e2e.py` all set. The machine-blocking-vocabulary
scanner (`scan_for_blocking_vocabulary_violations`) is imported from
`tests/test_docs/test_gate_tier_vocabulary.py`, and the rubric-item /
SKILL-doc path constants plus the numbered-item line extractor
(`TEST_REVIEWER_PATH`, `SKILL_PATH`, `COMPOSITION_ROOT_DOC_PATH`,
`_line_for_item`) are imported from `tests/test_plugin/test_rubric_numbering.py`
-- both already-precedented cross-directory imports in this repo
(`pythonpath` includes `tests`) -- rather than re-derived here.
`_CompositionRootJourneyFixtures` below adds only what the block/pass and
waiver journeys need on top of `_ScaffoldStoreFactoryCmdFixtures`: a real
`## Acceptance Criteria` section (neither `_seed_scope_backlog` nor
`_ScaffoldStoreFactoryCmdFixtures._patch_common` writes one) and the
`devbench.cli.BACKLOG_ROOT`/`WORKSPACE_ROOT` patches `cmd_read_unit`/
`cmd_log_waiver` need (which `_ScaffoldStoreFactoryCmdFixtures._patch_common`
never patches, since `cmd_scaffold_store_factory` never reads the raw
work-unit file itself) -- the union of
`test_gate_write_path_e2e.py::_WritePathJourneyFixtures._patch_backlog_write`'s
and `_ScaffoldStoreFactoryCmdFixtures._patch_common`'s own patch surfaces.

"Real CLI" here means the actual, unmocked `devbench.cli.cmd_read_unit`,
`devbench.cli.cmd_get_diff`, `devbench.cli.cmd_log_waiver`,
`devbench.cli.cmd_gates` and `devbench.cli.cmd_scaffold_store_factory`
implementations. `devbench.cli.BacklogParser`, `devbench.cli.REPO_LOCAL_PATHS`,
`devbench.cli.BACKLOG_ROOT`, `devbench.cli.WORKSPACE_ROOT`,
`devbench.cli.get_configured_default_branch`,
`devbench.work_unit_scope.BACKLOG_ROOT` and
`devbench.work_unit_scope.BACKLOG_INDEX` are patched -- the same seam every
sibling gate e2e module in this directory uses -- plus the
`DEVBENCH_CONFIG_PATH` env layer the disabled journey sets, the actual
config-resolution path both `cmd_gates` and every gate read
`gates.composition_root` through (spec 4.1, D-15). No journey mocks
`resolve_changed_files`, `cmd_scaffold_store_factory`'s own detection
helpers, or any other gate-internal function.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from test_cli import _ScaffoldStoreFactoryCmdFixtures
from test_docs.test_gate_tier_vocabulary import BlockingVocabularyViolation, scan_for_blocking_vocabulary_violations
from test_plugin.test_rubric_numbering import COMPOSITION_ROOT_DOC_PATH, SKILL_PATH, TEST_REVIEWER_PATH, _line_for_item
from test_tdd_gate import commit_scratch_repo, init_scratch_repo, write_scratch_file

from devbench import cli
from devbench.backlog.work_unit import WorkUnit
from devbench.constants import GATE_TIER_MACHINE_BLOCKING, GATE_TIERS

# ---------------------------------------------------------------------------
# Structural regression net for E9-F1-S1-T1 (spec 4.9(b), decision D-13,
# finding S1, gate G3). No journey below drives the CLI -- these classes
# parse the shipped surface text, plus a mutated copy of it seeded under
# `tmp_path`, proving each assertion is actually capable of failing before
# it is trusted against the real tree.
# ---------------------------------------------------------------------------

_DOCS_EXCERPT_PATTERN = re.compile(r"A task's `## Acceptance Criteria`.*?test behind it\.", re.DOTALL)

# Phrases that explicitly deny an auto-ticked `## Definition of Done` item
# satisfies the composition-root requirement (decision D-13, finding S1).
# Checked case-insensitively against a source excerpt already confirmed to
# mention both '## Acceptance Criteria' and '## Definition of Done'.
_DOD_NEGATION_MARKERS: tuple[str, ...] = ("never accepted", "does not satisfy")


def _line_containing(text: str, anchor: str, source: str) -> str:
    """Return the first physical line of `text` containing `anchor`, or raise."""
    for line in text.splitlines():
        if anchor in line:
            return line
    raise AssertionError(f"{source}: no line found containing {anchor!r}")


def _docs_excerpt(text: str) -> str:
    match = _DOCS_EXCERPT_PATTERN.search(text)
    if match is None:
        raise AssertionError(
            "docs/composition-root-testing.md: could not locate the AC-keyed/DoD-negation excerpt "
            f"via pattern {_DOCS_EXCERPT_PATTERN.pattern!r}"
        )
    return match.group(0)


def _skill_item13_excerpt(text: str) -> str:
    return _line_containing(text, "Composition-root AC item, NOT a Definition of Done item", "SKILL.md Step 1b item 13")


def _skill_item15_excerpt(text: str) -> str:
    return _line_containing(text, "Composition-root AC item present when required", "SKILL.md Step 5b item 15")


def _test_reviewer_item57_excerpt(text: str) -> str:
    return _line_for_item(text, 57)


def _assert_keyed_off_ac_and_never_dod_satisfied(excerpt: str, source: str) -> None:
    """Raise unless `excerpt` both keys the composition-root requirement off
    `## Acceptance Criteria` AND explicitly denies that a `## Definition of
    Done` item satisfies it (spec 4.9(b), decision D-13, finding S1)."""
    lower = excerpt.lower()
    if "## acceptance criteria" not in lower:
        raise AssertionError(f"{source}: never mentions '## Acceptance Criteria': {excerpt!r}")
    if "definition of done" not in lower:
        raise AssertionError(f"{source}: never mentions '## Definition of Done': {excerpt!r}")
    if not any(marker in lower for marker in _DOD_NEGATION_MARKERS):
        raise AssertionError(
            f"{source}: mentions Definition of Done without an explicit non-satisfaction marker "
            f"{_DOD_NEGATION_MARKERS}: {excerpt!r}"
        )


def _assert_names_composition_root_missing_code(excerpt: str, source: str) -> None:
    if "test_review:composition_root_missing" not in excerpt.lower():
        raise AssertionError(f"{source}: never names test_review:COMPOSITION_ROOT_MISSING: {excerpt!r}")


_STRUCTURAL_SOURCES: tuple[tuple[str, Path, Callable[[str], str]], ...] = (
    ("docs_composition_root_testing", COMPOSITION_ROOT_DOC_PATH, _docs_excerpt),
    ("skill_step1b_item13", SKILL_PATH, _skill_item13_excerpt),
    ("skill_step5b_item15", SKILL_PATH, _skill_item15_excerpt),
    ("test_reviewer_item57", TEST_REVIEWER_PATH, _test_reviewer_item57_excerpt),
)


@pytest.mark.integration
class TestStructuralAcceptanceCriteriaKeyingAndNoDodSatisfaction:
    """AC-E9-F2-S1-T1-2 (spec 4.9(b), decision D-13, finding S1): every
    shipped surface that states the composition-root requirement keys it
    off `## Acceptance Criteria` and explicitly denies `## Definition of
    Done` satisfaction. A mutated copy of each excerpt (negation phrase
    flipped), seeded under `tmp_path`, proves the same assertion function
    can fail before the shipped-tree pass is trusted."""

    @pytest.mark.parametrize(
        ("source", "path", "extract"), _STRUCTURAL_SOURCES, ids=[s[0] for s in _STRUCTURAL_SOURCES]
    )
    def test_shipped_surface_keys_off_ac_and_denies_dod_satisfaction(
        self, source: str, path: Path, extract: Callable[[str], str]
    ) -> None:
        excerpt = extract(path.read_text(encoding="utf-8"))
        _assert_keyed_off_ac_and_never_dod_satisfied(excerpt, source)

    @pytest.mark.parametrize(
        ("source", "path", "extract"), _STRUCTURAL_SOURCES, ids=[s[0] for s in _STRUCTURAL_SOURCES]
    )
    def test_mutated_copy_claiming_dod_satisfaction_fails(
        self, tmp_path: Path, source: str, path: Path, extract: Callable[[str], str]
    ) -> None:
        original_text = path.read_text(encoding="utf-8")
        excerpt = extract(original_text)
        mutated_excerpt = (
            excerpt.replace("never accepted", "always accepted")
            .replace("does NOT satisfy", "does satisfy")
            .replace("does not satisfy", "does satisfy")
        )
        assert mutated_excerpt != excerpt, f"{source}: expected a DoD-negation phrase in the shipped excerpt"
        mutated_full_text = original_text.replace(excerpt, mutated_excerpt)
        mutated_copy = tmp_path / f"{path.name}.mutated.md"
        mutated_copy.write_text(mutated_full_text, encoding="utf-8")

        mutated_excerpt_from_disk = extract(mutated_copy.read_text(encoding="utf-8"))
        with pytest.raises(AssertionError):
            _assert_keyed_off_ac_and_never_dod_satisfied(mutated_excerpt_from_disk, f"mutated-{source}")


@pytest.mark.integration
class TestStructuralRubricNamesCompositionRootMissingCode:
    """Regression net for E9-F1-S1-T1: `test-reviewer.md` rubric item 57
    must name the structured rejection code (`docs/review-feedback-vocabulary.md`)
    a judge emits on failure, not just describe the requirement in prose."""

    def test_shipped_rubric_item_names_the_code(self) -> None:
        excerpt = _line_for_item(TEST_REVIEWER_PATH.read_text(encoding="utf-8"), 57)
        _assert_names_composition_root_missing_code(excerpt, "test-reviewer.md rubric item 57")

    def test_mutated_rubric_item_omitting_the_code_fails(self, tmp_path: Path) -> None:
        original_text = TEST_REVIEWER_PATH.read_text(encoding="utf-8")
        excerpt = _line_for_item(original_text, 57)
        mutated_excerpt = excerpt.replace("`test_review:COMPOSITION_ROOT_MISSING`", "a composition-root finding")
        assert mutated_excerpt != excerpt, "expected the structured code to exist in the shipped rubric item"
        mutated_full_text = original_text.replace(excerpt, mutated_excerpt)
        mutated_copy = tmp_path / "test-reviewer-mutated.md"
        mutated_copy.write_text(mutated_full_text, encoding="utf-8")

        mutated_excerpt_from_disk = _line_for_item(mutated_copy.read_text(encoding="utf-8"), 57)
        with pytest.raises(AssertionError):
            _assert_names_composition_root_missing_code(mutated_excerpt_from_disk, "mutated-test-reviewer.md")


@pytest.mark.integration
class TestStructuralNoMachineBlockingVocabularyAppliedToGate:
    """AC-E9-F2-S1-T1-3 (spec 4.2, gate G3): composition_root's
    judge-evidence tier must never be described with machine-blocking
    vocabulary. Imports the shared scanner from
    `tests/test_docs/test_gate_tier_vocabulary.py` (DRY) instead of
    re-deriving the vocabulary regex; that module's own
    `test_shipped_tree_has_zero_blocking_vocabulary_violations` already
    covers every declared gate repo-wide -- this class is E9-F1-S1-T2's own
    regression net, scoped to the three composition-root surfaces it
    landed."""

    def test_shipped_composition_root_surfaces_carry_zero_blocking_vocabulary(self) -> None:
        assert GATE_TIERS["composition_root"] != GATE_TIER_MACHINE_BLOCKING
        violations: list[BlockingVocabularyViolation] = []
        for path, source in (
            (TEST_REVIEWER_PATH, "test-reviewer.md"),
            (SKILL_PATH, "SKILL.md"),
            (COMPOSITION_ROOT_DOC_PATH, "composition-root-testing.md"),
        ):
            text = path.read_text(encoding="utf-8")
            violations.extend(
                v for v in scan_for_blocking_vocabulary_violations(text, source=source) if v.gate == "composition_root"
            )
        assert violations == [], (
            f"composition_root surfaces must carry zero blocking-vocabulary overclaims: {violations}"
        )

    def test_mutated_test_reviewer_copy_injecting_blocking_vocabulary_is_caught(self, tmp_path: Path) -> None:
        original = TEST_REVIEWER_PATH.read_text(encoding="utf-8")
        anchor = "at least one test MUST render/exercise the component"
        assert anchor in original, "expected rubric item 57's anchor phrase to exist in the shipped rubric"
        mutated = original.replace(
            anchor,
            "the composition_root gate blocks the unit until at least one test MUST render/exercise the component",
        )
        mutated_copy = tmp_path / "test-reviewer-mutated.md"
        mutated_copy.write_text(mutated, encoding="utf-8")

        mutated_violations = [
            v
            for v in scan_for_blocking_vocabulary_violations(
                mutated_copy.read_text(encoding="utf-8"), source="test-reviewer-mutated.md"
            )
            if v.gate == "composition_root"
        ]
        assert mutated_violations, "mutated rubric copy applying blocking vocabulary to composition_root must be caught"

        unmutated_violations = [
            v
            for v in scan_for_blocking_vocabulary_violations(original, source="test-reviewer.md")
            if v.gate == "composition_root"
        ]
        assert unmutated_violations == [], f"shipped rubric must carry zero violations: {unmutated_violations}"


# ---------------------------------------------------------------------------
# Journey fixtures: real CLI over scratch git fixture repos.
# ---------------------------------------------------------------------------


class _CompositionRootJourneyFixtures(_ScaffoldStoreFactoryCmdFixtures):
    """Journey-level helpers layered on `_ScaffoldStoreFactoryCmdFixtures`
    (imported from `tests/test_cli.py`, not hand-copied). See module
    docstring for the full rationale."""

    def _write_full_work_unit(
        self,
        tmp_path: Path,
        unit_id: str,
        *,
        manifest_files: tuple[str, ...],
        acceptance_criteria_body: str,
    ) -> tuple[Path, Path, Path]:
        """Write a scratch `BACKLOG.md` + work-unit `.md` carrying a real
        `## Changes Manifest` (for scope resolution, `_seed_scope_backlog`'s
        shape) PLUS a real `## Acceptance Criteria` section and a
        `## TDD Cycle Log` section (the `log-waiver` audit-marker insertion
        point) -- neither of which `_seed_scope_backlog` nor
        `_ScaffoldStoreFactoryCmdFixtures._patch_common` write. Returns
        `(backlog_root, backlog_index, wu_file)`."""
        backlog_root = tmp_path / "backlog"
        backlog_root.mkdir(exist_ok=True)
        rows = "".join(f"| `{f}` | modify |\n" for f in manifest_files)
        wu_file = backlog_root / f"{unit_id}.md"
        wu_file.write_text(
            f"# {unit_id}: Composition-root journey task\n\n"
            "## Status: in-progress\n\n"
            f"## Changes Manifest\n\n| File | Change |\n|------|--------|\n{rows}\n\n"
            f"## Acceptance Criteria\n\n{acceptance_criteria_body}\n"
            "## TDD Cycle Log\n\n## Comments\n",
            encoding="utf-8",
        )
        backlog_index = tmp_path / "BACKLOG.md"
        backlog_index.write_text(
            "## Full Work Unit Index\n\n"
            "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
            "|-----|-------|------|--------|-------------|------|----------|\n"
            f"| {unit_id} | Composition-root journey task | Task | in-progress | None | {self._REPO} | "
            f"`backlog/{unit_id}.md` |\n",
            encoding="utf-8",
        )
        return backlog_root, backlog_index, wu_file

    @contextlib.contextmanager
    def _patch_scope_and_backlog(
        self, unit: WorkUnit, repo_path: Path, backlog_root: Path, backlog_index: Path
    ) -> Iterator[None]:
        """Patch surface for `read-unit`/`log-waiver`/`get-diff` together --
        the union of `_ScaffoldStoreFactoryCmdFixtures._patch_common`'s own
        surface (`BacklogParser`/`REPO_LOCAL_PATHS`/`work_unit_scope`'s
        independent `BACKLOG_ROOT`/`BACKLOG_INDEX` lookup, spec 4.3) plus
        `devbench.cli.BACKLOG_ROOT`/`WORKSPACE_ROOT` (needed to resolve the
        raw work-unit `.md` file itself, which `cmd_scaffold_store_factory`
        never reads). `get_configured_default_branch` is pinned so
        `cmd_get_diff`'s branch-vs-default hunk resolves deterministically
        against a scratch repo with no `origin` remote (the ensuing
        `git diff origin/main` call fails harmlessly and is silently
        skipped by the `rc == 0` guard in `_append_branch_vs_default_hunk`,
        `src/devbench/cli.py`)."""
        mock_parser = MagicMock()
        mock_parser.parse_index.return_value = [unit]
        with (
            patch("devbench.cli.BacklogParser", return_value=mock_parser),
            patch("devbench.cli.REPO_LOCAL_PATHS", {self._REPO: repo_path}),
            patch("devbench.cli.BACKLOG_ROOT", backlog_root.parent),
            patch("devbench.cli.WORKSPACE_ROOT", backlog_root.parent),
            patch("devbench.work_unit_scope.BACKLOG_ROOT", backlog_root),
            patch("devbench.work_unit_scope.BACKLOG_INDEX", backlog_index),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
        ):
            yield


def _acceptance_criteria_section(content: str) -> str:
    return content.split("## Acceptance Criteria", 1)[1].split("## TDD Cycle Log", 1)[0]


def _has_composition_root_ac_item(content: str) -> bool:
    section = _acceptance_criteria_section(content).lower()
    return "composition root" in section or "composition-root" in section


# ---------------------------------------------------------------------------
# Block and pass journeys (spec 4.9(b), section 10; AC-E9-F2-S1-T1-1).
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestJourneyBlockAndPassEvidenceDistinguishesCompositionRootCoverage(_CompositionRootJourneyFixtures):
    """`composition_root` is judge-evidence: there is no gate CLI verb to
    invoke for a block/pass exit code, so this journey drives the two real
    commands `test-reviewer` actually calls to build its Evidence block --
    `read-unit --strip-comments` and `get-diff` -- and asserts that surface
    distinguishes a fixture unit adding a state-consuming component with
    ONLY an isolated-render test and no composition-root AC item (block:
    everything `test_review:COMPOSITION_ROOT_MISSING` needs is present)
    from the same unit carrying the AC item and a real
    `<Provider store={realStore}>` composition-root test (never
    block-worthy)."""

    _COMPONENT_SOURCE = (
        "import { useSelector } from 'react-redux';\n\n"
        "export function PremiumBadge() {\n"
        "  const isPremiumEligible = useSelector((state) => state.eligibility.isPremiumEligible);\n"
        "  return isPremiumEligible ? <span>Premium</span> : null;\n"
        "}\n"
    )
    _ISOLATED_RENDER_TEST_SOURCE = (
        "import { render } from '@testing-library/react';\n"
        "import { PremiumBadge } from './PremiumBadge';\n\n"
        "test('renders premium badge with hand-supplied props', () => {\n"
        "  render(<PremiumBadge isPremiumEligible={true} />);\n"
        "});\n"
    )
    _COMPOSITION_ROOT_TEST_SOURCE = (
        "import { render } from '@testing-library/react';\n"
        "import { Provider } from 'react-redux';\n"
        "import { realStore } from '../../store';\n"
        "import { PremiumBadge } from './PremiumBadge';\n\n"
        "test('renders premium badge through the real composition root', () => {\n"
        "  render(\n"
        "    <Provider store={realStore}>\n"
        "      <PremiumBadge />\n"
        "    </Provider>\n"
        "  );\n"
        "});\n"
    )

    def _seed_and_read(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        unit_id: str,
        *,
        test_source: str,
        acceptance_criteria_body: str,
    ) -> tuple[str, str]:
        repo = init_scratch_repo(tmp_path)
        write_scratch_file(repo, "README.md", "composition-root block/pass journey baseline\n")
        commit_scratch_repo(repo, "seed baseline")
        # Left untracked (not committed): `get-diff`'s untracked-hunk path
        # renders their full content, scoped to the Changes Manifest, the
        # same evidence surface `test-reviewer` reads.
        write_scratch_file(repo, "src/components/PremiumBadge.tsx", self._COMPONENT_SOURCE)
        write_scratch_file(repo, "src/components/PremiumBadge.test.tsx", test_source)

        unit = self._make_unit(unit_id)
        backlog_root, backlog_index, _wu_file = self._write_full_work_unit(
            tmp_path,
            unit.id,
            manifest_files=("src/components/PremiumBadge.tsx", "src/components/PremiumBadge.test.tsx"),
            acceptance_criteria_body=acceptance_criteria_body,
        )

        with self._patch_scope_and_backlog(unit, repo, backlog_root, backlog_index):
            read_result = cli.cmd_read_unit("--strip-comments", unit.id)
            read_out = capsys.readouterr().out
            diff_result = cli.cmd_get_diff(unit.id)
            diff_out = capsys.readouterr().out

        assert read_result == 0, f"read-unit failed: {read_out!r}"
        assert diff_result == 0, f"get-diff failed: {diff_out!r}"
        content = json.loads(read_out)["content"]
        return content, diff_out

    def test_block_case_no_ac_item_and_only_isolated_render_present(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        content, diff_out = self._seed_and_read(
            tmp_path,
            capsys,
            "E1-F1-S1-T1",
            test_source=self._ISOLATED_RENDER_TEST_SOURCE,
            acceptance_criteria_body="- [ ] AC-UI-001 PremiumBadge renders the premium eligibility badge.\n",
        )

        assert not _has_composition_root_ac_item(content), (
            f"block fixture must carry no composition-root AC item: {content!r}"
        )
        assert "src/components/PremiumBadge.test.tsx" in diff_out, (
            f"in-scope test file missing from evidence: {diff_out!r}"
        )
        assert "realStore" not in diff_out, f"block fixture's evidence must show no real-store test: {diff_out!r}"
        assert "hand-supplied props" in diff_out, f"isolated-render marker missing from evidence: {diff_out!r}"

    def test_pass_case_ac_item_and_composition_root_test_present(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        content, diff_out = self._seed_and_read(
            tmp_path,
            capsys,
            "E1-F1-S1-T2",
            test_source=self._COMPOSITION_ROOT_TEST_SOURCE,
            acceptance_criteria_body=(
                "- [ ] AC-UI-001 PremiumBadge renders the premium eligibility badge.\n"
                "- [ ] AC-COMPOSITION-001 at least one test renders <PremiumBadge> through the app's real "
                "composition root (<Provider store={realStore}>), not solely via an isolated render with a "
                "hand-built store.\n"
            ),
        )

        assert _has_composition_root_ac_item(content), (
            f"pass fixture must carry a composition-root AC item: {content!r}"
        )
        assert "realStore" in diff_out and "Provider" in diff_out, (
            f"pass fixture's evidence must show a real composition-root test: {diff_out!r}"
        )


# ---------------------------------------------------------------------------
# Waiver journey (spec 4.3, AC-10; AC-E9-F2-S1-T1-4).
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestJourneyWaiverSurvivesEvidenceFetch(_CompositionRootJourneyFixtures):
    """A `[GATE_WAIVER composition_root]` marker written by `log-waiver` for
    the `test_review` judge survives `read-unit --strip-comments`'s
    Evidence fetch, not only the raw work-unit file (mirrors
    `test_gate_write_path_e2e.py::TestJourneyWaiverSurvivesEvidenceFetch`'s
    shape for `write_path_audit`)."""

    def test_gate_waiver_marker_survives_strip_comments_evidence_fetch(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repo = init_scratch_repo(tmp_path)
        write_scratch_file(repo, "README.md", "composition-root waiver journey baseline\n")
        commit_scratch_repo(repo, "seed repo")
        unit = self._make_unit("E1-F1-S1-T1")
        backlog_root, backlog_index, wu_file = self._write_full_work_unit(
            tmp_path,
            unit.id,
            manifest_files=(),
            acceptance_criteria_body="- [ ] AC-UI-001 placeholder\n",
        )

        with self._patch_scope_and_backlog(unit, repo, backlog_root, backlog_index):
            waived = cli.cmd_log_waiver(
                "test_review",
                unit.id,
                "--gate",
                "composition_root",
                "--target",
                "PremiumBadge",
                "--reason",
                "smallest-real-ancestor exception documented in Approach per docs/composition-root-testing.md",
            )
        capsys.readouterr()
        assert waived == 0, f"cmd_log_waiver must exit 0 for a valid waiver, got {waived}"
        content_after_waiver = wu_file.read_text(encoding="utf-8")
        assert "[GATE_WAIVER composition_root]" in content_after_waiver
        assert "PremiumBadge" in content_after_waiver

        with self._patch_scope_and_backlog(unit, repo, backlog_root, backlog_index):
            read_result = cli.cmd_read_unit("--strip-comments", unit.id)
        captured = capsys.readouterr()
        assert read_result == 0
        payload = json.loads(captured.out)
        assert "[GATE_WAIVER composition_root]" in payload["content"]
        assert "PremiumBadge" in payload["content"]
        assert "## Comments" not in payload["content"]


# ---------------------------------------------------------------------------
# Attribution journey (spec 4.3, AC-9; AC-E9-F2-S1-T1-5).
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestJourneyAttributionStaysInScope(_CompositionRootJourneyFixtures):
    """A file outside the unit's resolved Changes-Manifest scope never
    appears in the `get-diff` evidence a test-reviewer's composition-root
    check reads, even when that out-of-scope file also carries an
    isolated-render-only test for a state-consuming component (spec 4.3,
    AC-9)."""

    def test_out_of_scope_isolated_render_file_never_surfaces_in_evidence(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit = self._make_unit("E1-F1-S1-T1")
        repo_path = init_scratch_repo(tmp_path)
        write_scratch_file(repo_path, "README.md", "composition-root attribution journey baseline\n")
        commit_scratch_repo(repo_path, "seed baseline")
        self._write(
            repo_path / "src" / "components" / "PremiumBadge.test.tsx",
            (
                "import { render } from '@testing-library/react';\n"
                "import { Provider } from 'react-redux';\n"
                "import { realStore } from '../../store';\n"
                "import { PremiumBadge } from './PremiumBadge';\n\n"
                "test('renders premium badge through the real composition root', () => {\n"
                "  render(<Provider store={realStore}><PremiumBadge /></Provider>);\n"
                "});\n"
            ),
        )
        self._write(
            repo_path / "src" / "legacy" / "OrphanBadge.test.tsx",
            (
                "import { render } from '@testing-library/react';\n"
                "import { OrphanBadge } from './OrphanBadge';\n\n"
                "test('renders orphan badge with hand-supplied props', () => {\n"
                "  render(<OrphanBadge isPremiumEligible={true} />);\n"
                "});\n"
            ),
        )

        with (
            self._patch_common(unit, repo_path, manifest_files=("src/components/PremiumBadge.test.tsx",)),
            patch("devbench.cli.get_configured_default_branch", return_value="main"),
        ):
            result = cli.cmd_get_diff(unit.id)

        captured = capsys.readouterr()
        assert result == 0, f"stdout={captured.out!r} stderr={captured.err!r}"
        assert "src/components/PremiumBadge.test.tsx" in captured.out, (
            "in-scope composition-root evidence missing. resolved scope files="
            f"('src/components/PremiumBadge.test.tsx',) attributed evidence={captured.out!r}"
        )
        assert "src/legacy/OrphanBadge.test.tsx" not in captured.out, (
            "out-of-scope file leaked into composition-root evidence. resolved scope files="
            f"('src/components/PremiumBadge.test.tsx',) out-of-scope file='src/legacy/OrphanBadge.test.tsx' "
            f"attributed evidence={captured.out!r}"
        )


# ---------------------------------------------------------------------------
# Disabled journey (spec 4.1; AC-E9-F2-S1-T1-6).
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestJourneyDisabledGateImposesNothing(_CompositionRootJourneyFixtures):
    """With `gates.composition_root.enabled` false, `devbench gates` reports
    the gate disabled and `scaffold-store-factory` -- the only
    composition-root verb with real CLI-side behaviour -- still runs to
    completion unaffected, proving the judge-evidence gate imposes nothing
    on the fixture unit's path to done (composition_root is never wired
    into `mark-done`'s machine-blocking invariant, `constants.GATE_TIERS`,
    spec 4.1)."""

    def test_disabled_config_reports_disabled_and_scaffold_still_runs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cfg_dir = tmp_path / "cfgdir"
        cfg_dir.mkdir()
        cfg_path = cfg_dir / "devbench.yaml"
        cfg_path.write_text(
            f"repos:\n  {self._REPO}:\n    default_branch: main\ngates:\n  composition_root:\n    enabled: false\n"
        )
        monkeypatch.setenv("DEVBENCH_CONFIG_PATH", str(cfg_path))
        monkeypatch.delenv("DEVBENCH_GATE_COMPOSITION_ROOT_ENABLED", raising=False)

        gates_result = cli.cmd_gates()
        gates_out = capsys.readouterr().out
        assert gates_result == 0
        composition_row = next(line for line in gates_out.splitlines() if line.startswith("composition_root"))
        assert "disabled" in composition_row, f"expected composition_root disabled, got row: {composition_row!r}"

        unit = self._make_unit()
        repo_path = init_scratch_repo(tmp_path, dir_name="scaffold_repo")
        self._write(
            repo_path / "src" / "store" / "index.ts",
            "import { configureStore } from '@reduxjs/toolkit';\nexport const store = configureStore({});\n",
        )
        out_path = tmp_path / "out" / "skeleton.py"
        with self._patch_common(unit, repo_path, manifest_files=("src/store/index.ts",)):
            scaffold_result = cli.cmd_scaffold_store_factory(unit.id, "--out", str(out_path))
        assert scaffold_result == 0, "composition_root's disabled config must impose nothing on scaffold-store-factory"
        assert out_path.exists()


# ---------------------------------------------------------------------------
# Scaffold write-once / refuse-to-overwrite journey (spec 4.9; AC-E9-F2-S1-T1-7).
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestJourneyScaffoldWritesOnceAndRefusesOverwrite(_CompositionRootJourneyFixtures):
    """Running `scaffold-store-factory` twice against the identical `--out`
    path writes the skeleton on the first invocation and refuses --
    unchanged -- on the second, real, back-to-back CLI invocation (not
    merely a pre-seeded existing file, the shape
    `tests/test_cli.py::TestCmdScaffoldStoreFactory::test_existing_out_path_exits_1_and_leaves_file_unchanged`
    already covers at unit level)."""

    def test_second_identical_invocation_refuses_and_leaves_file_byte_identical(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        unit = self._make_unit()
        repo_path = init_scratch_repo(tmp_path)
        self._write(
            repo_path / "src" / "store" / "index.ts",
            "import { configureStore } from '@reduxjs/toolkit';\nexport const store = configureStore({});\n",
        )
        out_path = tmp_path / "out" / "premium_badge_factory_skeleton.py"

        with self._patch_common(unit, repo_path, manifest_files=("src/store/index.ts",)):
            first_result = cli.cmd_scaffold_store_factory(unit.id, "--out", str(out_path))
        capsys.readouterr()
        assert first_result == 0, f"first cmd_scaffold_store_factory invocation must exit 0, got {first_result}"
        assert out_path.exists()
        first_written = out_path.read_text(encoding="utf-8")
        assert "does NOT by itself" in first_written

        with self._patch_common(unit, repo_path, manifest_files=("src/store/index.ts",)):
            second_result = cli.cmd_scaffold_store_factory(unit.id, "--out", str(out_path))
        captured = capsys.readouterr()
        assert second_result == 1, "a second identical invocation must refuse to overwrite the skeleton"
        assert str(out_path) in captured.err
        assert "--force" in captured.err
        assert out_path.read_text(encoding="utf-8") == first_written, (
            "the skeleton must be byte-identical after the refusal"
        )
