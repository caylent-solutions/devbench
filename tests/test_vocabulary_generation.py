"""Tests for src/devbench/vocabulary_generation.py.

Coverage requirement: 100% line coverage on devbench.vocabulary_generation
(AC-FINAL-014).

Every import of ``devbench.vocabulary_generation`` in this file is deferred
into a fixture or a test-function body rather than performed at module
level. This is deliberate (not stylistic): the orchestrator's
``devbench tdd-gate`` RED-verification step stashes only the
production-source Changes Manifest rows and re-runs one named test node id
against the resulting tree. A module-level import here would fail the
entire file's collection (pytest exit code 2, "interrupted by a collection
/ import / syntax error") the moment the production module is stashed,
which ``tdd_gate.observe_red`` rejects outright regardless of which node id
was named. Deferring the import into each fixture/test body means the
production module's absence surfaces as an ordinary test FAILURE for the
specific test that needs it, while every other test in the file still
collects normally.

Covers:
- GUARD_MARKER_START / GUARD_MARKER_END constant values (spec 5.7).
- The generic guard-marker block-replace primitive (``replace_guarded_block``,
  built on ``_find_guard_block``): replaces only the wrapped block and leaves
  surrounding prose byte-identical (AC-E2-F5-S1-T1-1); raises
  ``GuardMarkerError`` naming the file when no marker pair exists
  (AC-E2-F5-S1-T1-4); raises naming the file and the line when an opening
  marker has no matching closing marker (AC-E2-F5-S1-T1-4); supports
  sequential multi-pair replacement via ``search_from`` (used by the doc
  surface, which carries five marker pairs in one file).
- ``render_prompt_sentence`` / ``render_doc_table``: each judge's generated
  content contains exactly that judge's codes from ``JUDGE_CATEGORIES`` and
  no other judge's codes (AC-E2-F5-S1-T1-3); unknown judge raises
  ``ValueError``; a ``CATEGORY_DESCRIPTIONS`` entry out of sync with
  ``JUDGE_CATEGORIES`` raises ``ValueError`` naming the judge.
- ``generate_prompt_file`` / ``generate_doc_file`` / ``generate_all``: end to
  end against a fixture repository tree; writes go through
  ``atomic_write_text`` (AC-E2-F5-S1-T1-6, asserted via a tracking wrapper);
  a second consecutive run produces zero diff (AC-E2-F5-S1-T1-2, AC-11).
- ``all_generated_relative_paths``: single enumeration combining
  ``DOC_RELATIVE_PATH`` and ``PROMPT_TARGETS``' keys, with no duplicates
  (AC-E2-F5-S1-T3-1).
- ``find_drifted_surfaces``: reports no drift on a freshly generated tree;
  reports exactly the surface a hand-edit touched (parametrized off
  ``all_generated_relative_paths`` so a newly added surface is covered
  automatically, AC-E2-F5-S1-T3-4); never mutates the tree it inspects, even
  when it reports drift (AC-E2-F5-S1-T3-2); propagates ``GuardMarkerError``
  for a surface with missing/unterminated guard markers rather than treating
  it as clean (AC-E2-F5-S1-T3-5).
- ``main``: happy path returns 0 and reports every generated path; a
  guard-marker failure in any target surface returns 1 with an ``ERROR:``
  line on stderr naming the file; ``--check`` verifies instead of
  regenerating, exiting 0 on a clean tree and 1 -- naming every drifted
  surface plus a single remediation line built from ``DRIFT_REMEDIATION_COMMAND``
  -- on a drifted one (AC-E2-F5-S1-T3-3).
- ``_repo_root``: resolves to this checkout's actual root.
- Module-internal consistency between this test file's literal fixtures and
  the production module's own constant tables (guards against silent drift
  between the two).
"""

from __future__ import annotations

import re
from pathlib import Path
from types import ModuleType

import pytest

from devbench.backlog.review_feedback_vocabulary import JUDGE_CATEGORIES

# ---------------------------------------------------------------------------
# Literal fixtures independent of devbench.vocabulary_generation (module
# level, so they must not import it -- see file docstring). Cross-checked
# against the production module's own constants in TestModuleConsistency.
# ---------------------------------------------------------------------------

_PROMPT_TARGET_ITEMS: tuple[tuple[str, str], ...] = (
    ("plugin/devbench-orchestrate/agents/review_team/code-reviewer.md", "code_review"),
    ("plugin/devbench-orchestrate/agents/review_team/test-reviewer.md", "test_review"),
    ("plugin/devbench-orchestrate/agents/review_team/doc-reviewer.md", "doc_review"),
    ("plugin/devbench-orchestrate/agents/review_team/changes-manifest.md", "changes_manifest"),
    ("plugin/devbench-orchestrate/agents/security-reviewer.md", "security_review"),
)
_PROMPT_JUDGES: tuple[str, ...] = tuple(judge for _, judge in _PROMPT_TARGET_ITEMS)
_DOC_JUDGES: tuple[str, ...] = _PROMPT_JUDGES
_DOC_RELATIVE_PATH: str = "docs/review-feedback-vocabulary.md"
_GUARD_MARKER_START: str = "<!-- generated:vocabulary -->"
_GUARD_MARKER_END: str = "<!-- /generated:vocabulary -->"


# ---------------------------------------------------------------------------
# RED-gate anchor. Not a fixture-based test: the import happens directly in
# the test body (the "call" phase), so when vocabulary_generation.py is
# stashed this specific node id collects fine and fails with an
# AssertionError-wrapped ModuleNotFoundError classed by pytest as FAILED
# (not a collection ERROR).
# ---------------------------------------------------------------------------


class TestModuleImportability:
    """The module must exist and expose the spec 5.7 guard-marker constants."""

    @pytest.mark.unit
    def test_module_exposes_guard_marker_constants(self) -> None:
        from devbench import vocabulary_generation

        assert vocabulary_generation.GUARD_MARKER_START == _GUARD_MARKER_START
        assert vocabulary_generation.GUARD_MARKER_END == _GUARD_MARKER_END


# ---------------------------------------------------------------------------
# Lazy-import fixture for the rest of the suite (fixture setup runs at test
# setup time, never at collection time, so this cannot break collection of
# the file).
# ---------------------------------------------------------------------------


@pytest.fixture
def vg() -> ModuleType:
    from devbench import vocabulary_generation

    return vocabulary_generation


# ---------------------------------------------------------------------------
# Guard-marker constants (spec 5.7)
# ---------------------------------------------------------------------------


class TestGuardMarkerConstants:
    """The guard-marker strings are the exact spec 5.7 literals."""

    @pytest.mark.unit
    def test_start_marker_value(self, vg: ModuleType) -> None:
        assert vg.GUARD_MARKER_START == _GUARD_MARKER_START

    @pytest.mark.unit
    def test_end_marker_value(self, vg: ModuleType) -> None:
        assert vg.GUARD_MARKER_END == _GUARD_MARKER_END


# ---------------------------------------------------------------------------
# replace_guarded_block / _find_guard_block
# ---------------------------------------------------------------------------


class TestReplaceGuardedBlock:
    """Generic block-replace primitive shared by both surface kinds."""

    @pytest.mark.unit
    def test_replaces_only_wrapped_block_prose_byte_identical(self, vg: ModuleType) -> None:
        """AC-E2-F5-S1-T1-1: content outside the markers is untouched byte for byte."""
        original = (
            "# Heading\n\nSome hand-written prose before.\n\n"
            f"{vg.GUARD_MARKER_START}\nstale content\n{vg.GUARD_MARKER_END}\n\n"
            "Some hand-written prose after.\n"
        )
        new_content, _ = vg.replace_guarded_block(original, "fresh content", source="fixture.md")
        expected = (
            "# Heading\n\nSome hand-written prose before.\n\n"
            f"{vg.GUARD_MARKER_START}\nfresh content\n{vg.GUARD_MARKER_END}\n\n"
            "Some hand-written prose after.\n"
        )
        assert new_content == expected

    @pytest.mark.unit
    def test_second_run_produces_zero_diff(self, vg: ModuleType) -> None:
        """AC-E2-F5-S1-T1-2 / AC-11: rendering twice with the same inner content is idempotent."""
        original = f"before {vg.GUARD_MARKER_START}\nstale\n{vg.GUARD_MARKER_END} after\n"
        once, _ = vg.replace_guarded_block(original, "generated", source="fixture.md")
        twice, _ = vg.replace_guarded_block(once, "generated", source="fixture.md")
        assert once == twice

    @pytest.mark.unit
    def test_missing_markers_raises_naming_file(self, vg: ModuleType) -> None:
        """AC-E2-F5-S1-T1-4: no guard-marker pair -> loud error naming the file."""
        with pytest.raises(vg.GuardMarkerError, match=r"fixture\.md"):
            vg.replace_guarded_block("no markers here at all", "content", source="fixture.md")

    @pytest.mark.unit
    def test_unterminated_marker_raises_naming_file_and_line(self, vg: ModuleType) -> None:
        """AC-E2-F5-S1-T1-4: an opening marker with no closing marker names the file and line."""
        content = f"line one\nline two\n{vg.GUARD_MARKER_START}\nline four unterminated\n"
        with pytest.raises(vg.GuardMarkerError) as excinfo:
            vg.replace_guarded_block(content, "irrelevant", source="fixture.md")
        assert "fixture.md" in str(excinfo.value)
        assert "line 3" in str(excinfo.value)

    @pytest.mark.unit
    def test_sequential_pairs_via_search_from(self, vg: ModuleType) -> None:
        """The doc surface carries multiple marker pairs in one file; each call advances search_from."""
        content = (
            f"## first\n{vg.GUARD_MARKER_START}\nold-a\n{vg.GUARD_MARKER_END}\n\n"
            f"## second\n{vg.GUARD_MARKER_START}\nold-b\n{vg.GUARD_MARKER_END}\n"
        )
        after_first, offset = vg.replace_guarded_block(content, "new-a", source="fixture.md")
        after_second, _ = vg.replace_guarded_block(after_first, "new-b", source="fixture.md", search_from=offset)
        assert "new-a" in after_second
        assert "new-b" in after_second
        assert "old-a" not in after_second
        assert "old-b" not in after_second
        # Headings (hand-written prose) survive untouched.
        assert "## first" in after_second
        assert "## second" in after_second

    @pytest.mark.unit
    def test_reject_duplicate_raises_on_second_start_marker(self, vg: ModuleType) -> None:
        """AC-WP-014: a caller passing ``reject_duplicate=True`` gets a loud error naming
        the file when a second start-marker occurs, instead of the silent
        "first pair wins" behaviour multi-pair surfaces (like the docs table) rely on.
        """
        content = (
            f"before\n{vg.GUARD_MARKER_START}\nfirst pair\n{vg.GUARD_MARKER_END}\n"
            f"between\n{vg.GUARD_MARKER_START}\nsecond pair\n{vg.GUARD_MARKER_END}\nafter\n"
        )
        with pytest.raises(vg.GuardMarkerError, match="more than one") as excinfo:
            vg.replace_guarded_block(content, "irrelevant", source="fixture.md", reject_duplicate=True)
        assert "fixture.md" in str(excinfo.value)

    @pytest.mark.unit
    def test_reject_duplicate_false_default_allows_second_start_marker(self, vg: ModuleType) -> None:
        """The default (``reject_duplicate=False``) leaves the multi-pair, ``search_from``-driven
        replacement flow (the docs surface) unaffected -- a second start marker after the
        located pair is not itself an error.
        """
        content = (
            f"before\n{vg.GUARD_MARKER_START}\nfirst pair\n{vg.GUARD_MARKER_END}\n"
            f"between\n{vg.GUARD_MARKER_START}\nsecond pair\n{vg.GUARD_MARKER_END}\nafter\n"
        )
        new_content, _ = vg.replace_guarded_block(content, "replaced", source="fixture.md")
        assert "replaced" in new_content
        assert vg.GUARD_MARKER_START in new_content

    @pytest.mark.unit
    def test_custom_markers_and_remediation_command_round_trip(self, vg: ModuleType) -> None:
        """A caller with its own marker literals and remediation command (e.g. the SKILL
        Step 3b surface) gets those literals back in both the replaced content and in
        every raised error's text -- never the module's own default vocabulary literals.
        """
        start_marker = "<!-- generated:skill-step-3b -->"
        end_marker = "<!-- /generated:skill-step-3b -->"
        remediation_command = "make generate-skill-step-3b"
        content = f"before\n{start_marker}\nstale\n{end_marker}\nafter\n"

        new_content, offset = vg.replace_guarded_block(
            content,
            "fresh",
            source="SKILL.md",
            start_marker=start_marker,
            end_marker=end_marker,
            remediation_command=remediation_command,
        )
        assert new_content == f"before\n{start_marker}\nfresh\n{end_marker}\nafter\n"
        assert offset == content.index(start_marker) + len(start_marker) + len("\nfresh\n")

        # Missing-marker error names the caller's own marker/command literals, not the
        # module's GUARD_MARKER_START/END/DRIFT_REMEDIATION_COMMAND defaults.
        with pytest.raises(vg.GuardMarkerError) as missing:
            vg.replace_guarded_block(
                "no markers here at all",
                "irrelevant",
                source="SKILL.md",
                start_marker=start_marker,
                end_marker=end_marker,
                remediation_command=remediation_command,
            )
        assert start_marker in str(missing.value)
        assert remediation_command in str(missing.value)
        assert vg.GUARD_MARKER_START not in str(missing.value)

        # Unterminated-marker error also names the caller's own literals and the line.
        unterminated = f"line one\nline two\n{start_marker}\nline four unterminated\n"
        with pytest.raises(vg.GuardMarkerError) as unterminated_error:
            vg.replace_guarded_block(
                unterminated,
                "irrelevant",
                source="SKILL.md",
                start_marker=start_marker,
                end_marker=end_marker,
                remediation_command=remediation_command,
            )
        assert "SKILL.md" in str(unterminated_error.value)
        assert "line 3" in str(unterminated_error.value)
        assert remediation_command in str(unterminated_error.value)


# ---------------------------------------------------------------------------
# render_prompt_sentence
# ---------------------------------------------------------------------------


class TestRenderPromptSentence:
    """Prompt-file sentence rendering (AC-E2-F5-S1-T1-3)."""

    @pytest.mark.unit
    @pytest.mark.parametrize("judge", sorted(_PROMPT_JUDGES))
    def test_contains_every_code_for_judge(self, vg: ModuleType, judge: str) -> None:
        sentence = vg.render_prompt_sentence(judge)
        for code in JUDGE_CATEGORIES[judge]:
            assert f"`{code}`" in sentence

    @pytest.mark.unit
    def test_excludes_codes_unique_to_other_judges(self, vg: ModuleType) -> None:
        """AC-E2-F5-S1-T1-3: no code belonging to another judge (codes unique to one judge only)."""
        sentence = vg.render_prompt_sentence("code_review")
        # GIT_COMPLETENESS is exclusive to test_review; README_SYNC exclusive to doc_review.
        assert "GIT_COMPLETENESS" not in sentence
        assert "README_SYNC" not in sentence

    @pytest.mark.unit
    def test_names_the_judge(self, vg: ModuleType) -> None:
        sentence = vg.render_prompt_sentence("security_review")
        assert "`security_review`" in sentence

    @pytest.mark.unit
    def test_unknown_judge_raises_value_error(self, vg: ModuleType) -> None:
        with pytest.raises(ValueError, match="unknown_judge_xyz"):
            vg.render_prompt_sentence("unknown_judge_xyz")


# ---------------------------------------------------------------------------
# render_doc_table
# ---------------------------------------------------------------------------


class TestRenderDocTable:
    """Docs-table rendering (AC-E2-F5-S1-T1-3)."""

    @pytest.mark.unit
    @pytest.mark.parametrize("judge", sorted(_DOC_JUDGES))
    def test_contains_every_code_for_judge(self, vg: ModuleType, judge: str) -> None:
        table = vg.render_doc_table(judge)
        for code in JUDGE_CATEGORIES[judge]:
            assert f"`{code}`" in table

    @pytest.mark.unit
    def test_excludes_codes_unique_to_other_judges(self, vg: ModuleType) -> None:
        table = vg.render_doc_table("security_review")
        # GIT_COMPLETENESS is exclusive to test_review; CHANGELOG_SYNC exclusive to doc_review.
        assert "GIT_COMPLETENESS" not in table
        assert "CHANGELOG_SYNC" not in table

    @pytest.mark.unit
    def test_has_header_and_separator(self, vg: ModuleType) -> None:
        table = vg.render_doc_table("doc_review")
        lines = table.splitlines()
        assert lines[0] == "| Code | Meaning | Example remediation |"
        assert lines[1] == "|------|---------|---------------------|"

    @pytest.mark.unit
    def test_unknown_judge_raises_value_error(self, vg: ModuleType) -> None:
        with pytest.raises(ValueError, match="unknown_judge_xyz"):
            vg.render_doc_table("unknown_judge_xyz")

    @pytest.mark.unit
    def test_description_drift_raises_value_error(self, vg: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
        """A CATEGORY_DESCRIPTIONS entry out of sync with JUDGE_CATEGORIES fails loudly."""
        stale = {"NOT_A_REAL_CODE": ("meaning", "remediation")}
        monkeypatch.setitem(vg.CATEGORY_DESCRIPTIONS, "doc_review", stale)
        with pytest.raises(ValueError, match="doc_review"):
            vg.render_doc_table("doc_review")


# ---------------------------------------------------------------------------
# CATEGORY_DESCRIPTIONS content pin -- UNREACHABLE_ARTIFACT remediation (D-5)
# ---------------------------------------------------------------------------


class TestUnreachableArtifactRemediationPointsAtLogWaiver:
    """The `devbench-defer-reachability` source-comment escape hatch (spec
    D-5) must never again be the shipped remediation for a `code_review`
    `UNREACHABLE_ARTIFACT` finding. E3-F1-S1-T1 deletes the source-comment
    hatch from `cli.py`; once it lands, the audited `[GATE_WAIVER
    reachability]` marker written by `uv run devbench log-waiver <judge>
    <unit-id> --gate reachability --target <t> --reason <r> --operator` is
    the only supported deferral. This pins the remediation string at the
    CLI verb operators are actually supposed to run, not at the
    source-comment marker E3-F1-S1-T1 is removing.

    `reachability` is declared machine-blocking in `constants.GATE_TIERS`, so
    `_validate_log_waiver_semantics` rejects the invocation with exit 2
    whenever `--operator` is absent (spec Section 3.6: the operator is the
    only waiver authority for a machine-blocking gate). The remediation must
    therefore render `--operator` as required, not as the optional
    `[--operator]` bracket notation that is only correct for the generic
    (non-gate-pinned) `log-waiver` usage line.
    """

    @pytest.mark.unit
    def test_remediation_has_no_defer_comment_marker(self, vg: ModuleType) -> None:
        _meaning, remediation = vg.CATEGORY_DESCRIPTIONS["code_review"]["UNREACHABLE_ARTIFACT"]
        assert "devbench-defer-reachability" not in remediation

    @pytest.mark.unit
    def test_remediation_names_log_waiver_with_reachability_gate_flag(self, vg: ModuleType) -> None:
        _meaning, remediation = vg.CATEGORY_DESCRIPTIONS["code_review"]["UNREACHABLE_ARTIFACT"]
        assert "log-waiver" in remediation
        assert "--gate reachability" in remediation

    @pytest.mark.unit
    def test_remediation_requires_operator_flag_not_optional(self, vg: ModuleType) -> None:
        _meaning, remediation = vg.CATEGORY_DESCRIPTIONS["code_review"]["UNREACHABLE_ARTIFACT"]
        assert "[--operator]" not in remediation
        assert "--gate reachability --target <t> --reason <r> --operator" in remediation


# ---------------------------------------------------------------------------
# CATEGORY_DESCRIPTIONS content pin -- FIXTURE_CATALOG_MISMATCH remediation
# ---------------------------------------------------------------------------


class TestFixtureCatalogMismatchRemediationNamesInFixtureMarker:
    """`gates.fixture_consistency.scan[].allow_missing` is a removed config key
    (spec 4.7 bullet 5, E6-F1-S1-T2): `src/devbench/config_loader.py` fails
    config load fast when it is still present. The `FIXTURE_CATALOG_MISMATCH`
    remediation string must therefore never again point an operator at that
    removed key -- following the old advice would now hard-fail config load.
    The sole production waiver mechanism is the structured in-fixture
    `{"allow_missing": {"reason": "<non-empty reason>"}}` marker attached
    directly to the waived record in the scanned fixture artifact."""

    @pytest.mark.unit
    def test_remediation_does_not_name_the_removed_config_key(self, vg: ModuleType) -> None:
        _meaning, remediation = vg.CATEGORY_DESCRIPTIONS["test_review"]["FIXTURE_CATALOG_MISMATCH"]
        assert "gates.fixture_consistency.scan[].allow_missing" not in remediation

    @pytest.mark.unit
    def test_remediation_names_the_in_fixture_marker(self, vg: ModuleType) -> None:
        _meaning, remediation = vg.CATEGORY_DESCRIPTIONS["test_review"]["FIXTURE_CATALOG_MISMATCH"]
        assert '{"allow_missing": {"reason": "<non-empty reason>"}}' in remediation
        assert "scanned fixture file" in remediation


# ---------------------------------------------------------------------------
# Module-internal consistency
# ---------------------------------------------------------------------------


class TestModuleConsistency:
    """Consistency across the module's own constant tables, and against this
    test file's independent literal fixtures (guards against silent drift).
    """

    @pytest.mark.unit
    def test_doc_judges_match_prompt_target_judges(self, vg: ModuleType) -> None:
        assert set(vg.DOC_JUDGES) == set(vg.PROMPT_TARGETS.values())

    @pytest.mark.unit
    def test_category_descriptions_cover_every_doc_judge(self, vg: ModuleType) -> None:
        assert set(vg.CATEGORY_DESCRIPTIONS) == set(vg.DOC_JUDGES)

    @pytest.mark.unit
    def test_category_descriptions_match_judge_categories_codes(self, vg: ModuleType) -> None:
        for judge in vg.DOC_JUDGES:
            assert set(vg.CATEGORY_DESCRIPTIONS[judge]) == set(JUDGE_CATEGORIES[judge])

    @pytest.mark.unit
    def test_fixture_prompt_targets_match_production(self, vg: ModuleType) -> None:
        assert dict(_PROMPT_TARGET_ITEMS) == vg.PROMPT_TARGETS

    @pytest.mark.unit
    def test_fixture_doc_relative_path_matches_production(self, vg: ModuleType) -> None:
        assert _DOC_RELATIVE_PATH == vg.DOC_RELATIVE_PATH

    @pytest.mark.unit
    def test_fixture_guard_markers_match_production(self, vg: ModuleType) -> None:
        assert _GUARD_MARKER_START == vg.GUARD_MARKER_START
        assert _GUARD_MARKER_END == vg.GUARD_MARKER_END


# ---------------------------------------------------------------------------
# generate_prompt_file / generate_doc_file / generate_all -- fixture repo tree
# ---------------------------------------------------------------------------


def _write_prompt_fixture(path: Path, judge: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "Prose before.\n\n"
        f"Payload shape sentence. {_GUARD_MARKER_START}\nEvery `code` MUST come from the controlled "
        f"vocabulary for `{judge}`: `STALE_CODE`.\n{_GUARD_MARKER_END} Trailing prose.\n",
        encoding="utf-8",
    )


def _write_doc_fixture(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sections = []
    for judge in _DOC_JUDGES:
        sections.append(
            f"## `{judge}`\n\n{_GUARD_MARKER_START}\n| Code | Meaning | Example remediation |\n"
            f"|------|---------|---------------------|\n| `ZZZ_PLACEHOLDER_ROW` | stale | stale |\n"
            f"{_GUARD_MARKER_END}\n"
        )
    path.write_text("# Review-Feedback Vocabulary\n\n" + "\n".join(sections), encoding="utf-8")


def _build_fixture_repo(root: Path) -> Path:
    """Populate ``root`` with a minimal repo tree shaped like the real
    target surfaces (the doc surface plus every prompt-target surface).

    Shared by the ``fixture_repo`` fixture and by
    ``TestFindDriftedSurfaces.test_mutating_each_surface_in_turn_is_reported_individually``,
    which needs one fresh tree per surface rather than a single shared one.
    """
    _write_doc_fixture(root / _DOC_RELATIVE_PATH)
    for relative_path, judge in _PROMPT_TARGET_ITEMS:
        _write_prompt_fixture(root / relative_path, judge)
    return root


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """Build a minimal repo tree shaped like the real target surfaces."""
    return _build_fixture_repo(tmp_path / "repo")


class TestGeneratePromptFile:
    @pytest.mark.unit
    @pytest.mark.parametrize(("relative_path", "judge"), _PROMPT_TARGET_ITEMS)
    def test_writes_generated_sentence_and_preserves_prose(
        self, vg: ModuleType, fixture_repo: Path, relative_path: str, judge: str
    ) -> None:
        target = fixture_repo / relative_path
        vg.generate_prompt_file(target, judge)
        content = target.read_text(encoding="utf-8")
        assert "Prose before." in content
        assert "Trailing prose." in content
        assert "STALE_CODE" not in content
        for code in JUDGE_CATEGORIES[judge]:
            assert f"`{code}`" in content

    @pytest.mark.unit
    def test_missing_marker_raises_naming_actual_file(self, vg: ModuleType, tmp_path: Path) -> None:
        target = tmp_path / "no-markers.md"
        target.write_text("nothing generated here\n", encoding="utf-8")
        with pytest.raises(vg.GuardMarkerError, match=str(target)):
            vg.generate_prompt_file(target, "code_review")


class TestGenerateDocFile:
    @pytest.mark.unit
    def test_writes_every_section_and_preserves_headings(self, vg: ModuleType, fixture_repo: Path) -> None:
        target = fixture_repo / _DOC_RELATIVE_PATH
        vg.generate_doc_file(target)
        content = target.read_text(encoding="utf-8")
        assert "ZZZ_PLACEHOLDER_ROW" not in content
        for judge in _DOC_JUDGES:
            assert f"## `{judge}`" in content
            for code in JUDGE_CATEGORIES[judge]:
                assert f"`{code}`" in content

    @pytest.mark.unit
    def test_second_run_is_zero_diff(self, vg: ModuleType, fixture_repo: Path) -> None:
        """AC-E2-F5-S1-T1-2 / AC-11 end to end on the multi-section doc surface."""
        target = fixture_repo / _DOC_RELATIVE_PATH
        vg.generate_doc_file(target)
        first_pass = target.read_text(encoding="utf-8")
        vg.generate_doc_file(target)
        second_pass = target.read_text(encoding="utf-8")
        assert first_pass == second_pass


class TestGenerateAll:
    @pytest.mark.unit
    def test_writes_doc_and_every_prompt_returns_all_paths(self, vg: ModuleType, fixture_repo: Path) -> None:
        written = vg.generate_all(fixture_repo)
        expected = {fixture_repo / _DOC_RELATIVE_PATH, *(fixture_repo / p for p, _ in _PROMPT_TARGET_ITEMS)}
        assert set(written) == expected
        for path in written:
            assert "ZZZ_PLACEHOLDER_ROW" not in path.read_text(encoding="utf-8")

    @pytest.mark.unit
    def test_second_run_is_zero_diff_across_all_surfaces(self, vg: ModuleType, fixture_repo: Path) -> None:
        vg.generate_all(fixture_repo)
        before = {p: p.read_text(encoding="utf-8") for p in fixture_repo.rglob("*.md")}
        vg.generate_all(fixture_repo)
        after = {p: p.read_text(encoding="utf-8") for p in fixture_repo.rglob("*.md")}
        assert before == after

    @pytest.mark.unit
    def test_uses_atomic_write(self, vg: ModuleType, fixture_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-E2-F5-S1-T1-6: every surface is written through the atomic write helper."""
        calls: list[Path] = []
        real_atomic_write_text = vg.atomic_write_text

        def _tracking_atomic_write_text(path: Path, content: str) -> None:
            calls.append(path)
            real_atomic_write_text(path, content)

        monkeypatch.setattr(vg, "atomic_write_text", _tracking_atomic_write_text)
        written = vg.generate_all(fixture_repo)
        assert set(calls) == set(written)

    @pytest.mark.unit
    def test_missing_marker_in_any_target_raises(self, vg: ModuleType, fixture_repo: Path) -> None:
        broken = fixture_repo / _DOC_RELATIVE_PATH
        broken.write_text("no markers anywhere in this doc\n", encoding="utf-8")
        with pytest.raises(vg.GuardMarkerError):
            vg.generate_all(fixture_repo)


# ---------------------------------------------------------------------------
# all_generated_relative_paths / find_drifted_surfaces
# ---------------------------------------------------------------------------


def _hand_edit_within_guard_markers(vg: ModuleType, content: str) -> str:
    """Insert a byte inside the first guard-marker pair of *content*.

    Shared by every drift test that needs a real (non-guard-marker-breaking)
    mutation: inserting right after the opening marker always lands inside
    an existing pair, so the mutation changes rendered content without
    ever tripping ``GuardMarkerError`` itself.
    """
    marker_index = content.index(vg.GUARD_MARKER_START) + len(vg.GUARD_MARKER_START)
    return content[:marker_index] + "\nHAND_EDITED_ROW\n" + content[marker_index:]


class TestAllGeneratedRelativePaths:
    """AC-E2-F5-S1-T3-1: single enumeration -- the doc surface plus every
    ``PROMPT_TARGETS`` key -- so ``generate_all`` and ``find_drifted_surfaces``
    can never iterate a different file set from one another."""

    @pytest.mark.unit
    def test_matches_doc_relative_path_and_prompt_targets(self, vg: ModuleType) -> None:
        assert vg.all_generated_relative_paths() == (vg.DOC_RELATIVE_PATH, *vg.PROMPT_TARGETS.keys())

    @pytest.mark.unit
    def test_no_duplicate_entries(self, vg: ModuleType) -> None:
        paths = vg.all_generated_relative_paths()
        assert len(paths) == len(set(paths))


class TestFindDriftedSurfaces:
    """AC-E2-F5-S1-T3-1/2/4/5: regenerates every guard-marked surface into a
    scratch directory and diffs it against the committed tree, without ever
    writing to the tree it inspects."""

    @pytest.mark.unit
    def test_freshly_generated_tree_reports_no_drift(self, vg: ModuleType, fixture_repo: Path) -> None:
        vg.generate_all(fixture_repo)
        assert vg.find_drifted_surfaces(fixture_repo) == []

    @pytest.mark.unit
    def test_hand_edited_surface_is_reported(self, vg: ModuleType, fixture_repo: Path) -> None:
        vg.generate_all(fixture_repo)
        doc_path = fixture_repo / vg.DOC_RELATIVE_PATH
        original = doc_path.read_text(encoding="utf-8")
        doc_path.write_text(_hand_edit_within_guard_markers(vg, original), encoding="utf-8")
        assert vg.find_drifted_surfaces(fixture_repo) == [vg.DOC_RELATIVE_PATH]

    @pytest.mark.unit
    def test_mutating_each_surface_in_turn_is_reported_individually(self, vg: ModuleType, tmp_path: Path) -> None:
        """AC-E2-F5-S1-T3-4: loops over ``all_generated_relative_paths()`` (the
        module's own enumeration) rather than a re-typed list in this test
        file, so a surface added to the module later is exercised here
        without a matching test-file edit -- the failure mode a second
        hand-maintained copy of this list in the build file would allow."""
        for relative_path in vg.all_generated_relative_paths():
            repo_root = _build_fixture_repo(tmp_path / relative_path.replace("/", "_"))
            vg.generate_all(repo_root)

            target = repo_root / relative_path
            original = target.read_text(encoding="utf-8")
            mutated = _hand_edit_within_guard_markers(vg, original)
            assert mutated != original, f"Mutation produced no change for {relative_path}"
            target.write_text(mutated, encoding="utf-8")

            drifted = vg.find_drifted_surfaces(repo_root)
            assert drifted == [relative_path], (
                f"Mutating only {relative_path} should report exactly that surface; got {drifted}"
            )

    @pytest.mark.unit
    def test_never_writes_to_the_inspected_tree(self, vg: ModuleType, fixture_repo: Path) -> None:
        """AC-E2-F5-S1-T3-2: every surface's bytes are identical before and
        after a run that itself reports drift."""
        vg.generate_all(fixture_repo)
        doc_path = fixture_repo / vg.DOC_RELATIVE_PATH
        original = doc_path.read_text(encoding="utf-8")
        doc_path.write_text(_hand_edit_within_guard_markers(vg, original), encoding="utf-8")

        relative_paths = vg.all_generated_relative_paths()
        before = {relative_path: (fixture_repo / relative_path).read_bytes() for relative_path in relative_paths}
        drifted = vg.find_drifted_surfaces(fixture_repo)
        after = {relative_path: (fixture_repo / relative_path).read_bytes() for relative_path in relative_paths}

        assert drifted, "Expected the hand-edit to be reported as drift"
        assert before == after

    @pytest.mark.unit
    def test_missing_guard_marker_raises_naming_file(self, vg: ModuleType, fixture_repo: Path) -> None:
        """AC-E2-F5-S1-T3-5: a marker-less surface fails fast rather than being
        silently treated as clean."""
        broken = fixture_repo / vg.DOC_RELATIVE_PATH
        broken.write_text("no markers anywhere in this doc\n", encoding="utf-8")
        with pytest.raises(vg.GuardMarkerError, match=re.escape(vg.DOC_RELATIVE_PATH)):
            vg.find_drifted_surfaces(fixture_repo)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


class TestMain:
    @pytest.mark.unit
    def test_happy_path_returns_zero_and_reports_paths(
        self,
        vg: ModuleType,
        fixture_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(vg, "_repo_root", lambda: fixture_repo)
        exit_code = vg.main()
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "generated:" in out
        assert _DOC_RELATIVE_PATH in out

    @pytest.mark.unit
    def test_guard_marker_failure_returns_one_with_stderr(
        self,
        vg: ModuleType,
        fixture_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        broken = fixture_repo / _DOC_RELATIVE_PATH
        broken.write_text("no markers anywhere\n", encoding="utf-8")
        monkeypatch.setattr(vg, "_repo_root", lambda: fixture_repo)
        exit_code = vg.main()
        assert exit_code == 1
        err = capsys.readouterr().err
        assert err.startswith("ERROR:")
        assert str(broken) in err

    @pytest.mark.unit
    def test_argv_without_check_flag_still_regenerates(
        self,
        vg: ModuleType,
        fixture_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """An empty/non-``--check`` argv must not accidentally enter check mode."""
        monkeypatch.setattr(vg, "_repo_root", lambda: fixture_repo)
        exit_code = vg.main([])
        assert exit_code == 0
        out = capsys.readouterr().out
        assert "generated:" in out


class TestMainCheckMode:
    """AC-E2-F5-S1-T3-3: ``main([CHECK_FLAG])`` verifies without regenerating."""

    @pytest.mark.unit
    def test_clean_tree_returns_zero_without_regenerating(
        self,
        vg: ModuleType,
        fixture_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        vg.generate_all(fixture_repo)
        committed = {p: p.read_bytes() for p in fixture_repo.rglob("*.md")}
        monkeypatch.setattr(vg, "_repo_root", lambda: fixture_repo)
        exit_code = vg.main([vg.CHECK_FLAG])
        assert exit_code == 0
        after = {p: p.read_bytes() for p in fixture_repo.rglob("*.md")}
        assert after == committed

    @pytest.mark.unit
    def test_drifted_tree_returns_one_naming_surface_and_remediation(
        self,
        vg: ModuleType,
        fixture_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        vg.generate_all(fixture_repo)
        doc_path = fixture_repo / vg.DOC_RELATIVE_PATH
        doc_path.write_text(_hand_edit_within_guard_markers(vg, doc_path.read_text(encoding="utf-8")), encoding="utf-8")
        monkeypatch.setattr(vg, "_repo_root", lambda: fixture_repo)
        exit_code = vg.main([vg.CHECK_FLAG])
        assert exit_code == 1
        err = capsys.readouterr().err
        assert vg.DOC_RELATIVE_PATH in err
        assert vg.DRIFT_REMEDIATION_COMMAND in err

    @pytest.mark.unit
    def test_remediation_command_is_a_single_module_constant_printed_once(
        self,
        vg: ModuleType,
        fixture_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """AC-E2-F5-S1-T3-3: the remediation text is not repeated at each
        drifted surface's line, even when more than one surface drifted."""
        vg.generate_all(fixture_repo)
        first_prompt_relative_path = next(iter(vg.PROMPT_TARGETS))
        for relative_path in (vg.DOC_RELATIVE_PATH, first_prompt_relative_path):
            target = fixture_repo / relative_path
            target.write_text(_hand_edit_within_guard_markers(vg, target.read_text(encoding="utf-8")), encoding="utf-8")
        monkeypatch.setattr(vg, "_repo_root", lambda: fixture_repo)
        exit_code = vg.main([vg.CHECK_FLAG])
        assert exit_code == 1
        err = capsys.readouterr().err
        assert err.count(vg.DRIFT_REMEDIATION_COMMAND) == 1, f"Expected exactly one occurrence, got stderr:\n{err}"
        assert vg.DOC_RELATIVE_PATH in err
        assert first_prompt_relative_path in err

    @pytest.mark.unit
    def test_guard_marker_failure_in_check_mode_returns_one_with_stderr(
        self,
        vg: ModuleType,
        fixture_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        broken = fixture_repo / vg.DOC_RELATIVE_PATH
        broken.write_text("no markers anywhere\n", encoding="utf-8")
        monkeypatch.setattr(vg, "_repo_root", lambda: fixture_repo)
        exit_code = vg.main([vg.CHECK_FLAG])
        assert exit_code == 1
        err = capsys.readouterr().err
        assert err.startswith("ERROR:")
        assert vg.DOC_RELATIVE_PATH in err


class TestDriftRemediationCommandConstant:
    """The remediation command is pinned as a single module constant naming
    the regeneration make target (AC-E2-F5-S1-T3-3)."""

    @pytest.mark.unit
    def test_value_names_the_generate_vocabulary_make_target(self, vg: ModuleType) -> None:
        assert vg.DRIFT_REMEDIATION_COMMAND == "make generate-vocabulary"


# ---------------------------------------------------------------------------
# _repo_root
# ---------------------------------------------------------------------------


class TestRepoRoot:
    @pytest.mark.unit
    def test_resolves_to_this_checkout_root(self, vg: ModuleType) -> None:
        root = vg._repo_root()
        assert (root / "Makefile").is_file()
        assert (root / _DOC_RELATIVE_PATH).is_file()
