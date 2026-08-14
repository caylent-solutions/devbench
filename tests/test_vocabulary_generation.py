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
- ``main``: happy path returns 0 and reports every generated path; a
  guard-marker failure in any target surface returns 1 with an ``ERROR:``
  line on stderr naming the file.
- ``_repo_root``: resolves to this checkout's actual root.
- Module-internal consistency between this test file's literal fixtures and
  the production module's own constant tables (guards against silent drift
  between the two).
"""

from __future__ import annotations

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


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """Build a minimal repo tree shaped like the real target surfaces."""
    repo_root = tmp_path / "repo"
    _write_doc_fixture(repo_root / _DOC_RELATIVE_PATH)
    for relative_path, judge in _PROMPT_TARGET_ITEMS:
        _write_prompt_fixture(repo_root / relative_path, judge)
    return repo_root


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


# ---------------------------------------------------------------------------
# _repo_root
# ---------------------------------------------------------------------------


class TestRepoRoot:
    @pytest.mark.unit
    def test_resolves_to_this_checkout_root(self, vg: ModuleType) -> None:
        root = vg._repo_root()
        assert (root / "Makefile").is_file()
        assert (root / _DOC_RELATIVE_PATH).is_file()
