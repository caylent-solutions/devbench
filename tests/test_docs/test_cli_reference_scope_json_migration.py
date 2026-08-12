"""Structural pins for the scope.json legacy list-shape migration in docs/cli-reference.md.

Verifies that docs/cli-reference.md documents the issue #270 read-side migration shipped by
E7-F1-S1-T1:

- The ``devbench status`` section no longer claims universal rejection of non-object
  scope.json shapes; it distinguishes the migrated legacy list shape from every other
  shape (AC-E7-F1-S1-T2-1).
- The 'scope.json persistence' section gains a 'Legacy list-shape migration (issue #270)'
  subsection naming all three read paths that perform the migration -- ScopeFilter.from_file,
  the status/report/next scope-banner reader, and ``devbench scope show`` (AC-E7-F1-S1-T2-2).
- That subsection documents the operator-visible signal: exactly one INFO line naming the
  migrated file, and a write failure during migration is never swallowed -- qualified per
  path (propagates as OSError on the from_file/scope-banner paths; caught and reported on
  stderr with exit 1 by ``devbench scope show``) (AC-E7-F1-S1-T2-3).
- That subsection documents provenance sourcing: started_at / started_by read from sibling
  session-state files when present, or the explicit "unknown" sentinel when absent, never
  fabricated (AC-E7-F1-S1-T2-4).
- The empty-list edge case is reconciled against docs/multi-session-runs.md's
  absent-is-unscoped invariant (AC-E7-F1-S1-T2-5).

Source: E7-F1-S1-T1 (src/devbench/scope.py, src/devbench/cli.py). Issue #270.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
CLI_REFERENCE_DOC = REPO_ROOT / "docs" / "cli-reference.md"

_HEADING_PREFIX_RE = re.compile(r"^(#{1,6}) ")
_HEADING_LINE_RE = re.compile(r"^(#{1,6}) ", re.MULTILINE)


def _read_doc() -> str:
    return CLI_REFERENCE_DOC.read_text(encoding="utf-8")


def _extract_enumeration_sentence(migration_section: str) -> str:
    """Return the read-path enumeration sentence from the migration subsection.

    The sentence starts at the distinctive phrase "The read paths that perform
    this migration are" and ends at the first ``". "`` (period-space)
    boundary. Every token this test module later asserts membership of
    (``ScopeFilter.from_file``, ``devbench status``, ``devbench report``,
    ``devbench next``, ``devbench scope show``) is *also* present in the
    adjacent write-failure bullet, so asserting against the whole subsection
    text lets a reverted or truncated enumeration sentence pass vacuously
    (test_review REVIEW_FAIL, AC-E7-F1-S1-T2-2). Scoping the assertion to
    just this sentence closes that hole: dropping any one path from the
    enumeration, or reverting the whole sentence to the stale two-path form,
    changes the substring this helper returns and fails the assertions that
    check it.
    """
    start_phrase = "The read paths that perform this migration are"
    start = migration_section.find(start_phrase)
    assert start != -1, (
        "The migration subsection must contain the read-path enumeration "
        f"sentence starting with {start_phrase!r} (AC-E7-F1-S1-T2-2)."
    )
    end = migration_section.find(". ", start)
    assert end != -1, "Could not find the end of the read-path enumeration sentence."
    return migration_section[start : end + 1]


def _extract_section(text: str, heading: str) -> str:
    """Return the content of the section starting at ``heading``.

    ``heading`` must be the full markdown heading line, including its
    leading ``#`` markers (e.g. ``"#### Legacy list-shape migration (issue
    #270)"``), not a bare title. The heading's level is derived from that
    prefix, and the returned section is bounded at the next heading whose
    level is the same as or higher (fewer ``#`` characters) than the
    starting heading -- not merely the next heading at the exact same
    level -- so a subsection nested under a shallower parent heading is
    still cut off correctly at the parent's next sibling. Passing a bare
    title (no ``#`` prefix) raises ``ValueError`` immediately instead of
    silently deriving a bogus level and returning the wrong slice of the
    document -- the failure mode this helper previously had.
    """
    match = _HEADING_PREFIX_RE.match(heading)
    if match is None:
        raise ValueError(
            f"heading must be a full markdown heading starting with '#' markers "
            f"(e.g. '#### {heading}'), got: {heading!r}"
        )
    level = len(match.group(1))
    idx = text.find(heading)
    if idx == -1:
        return ""
    section_text = text[idx:]
    for candidate in _HEADING_LINE_RE.finditer(section_text):
        if candidate.start() == 0:
            continue
        if len(candidate.group(1)) <= level:
            return section_text[: candidate.start()]
    return section_text


@pytest.mark.unit
class TestStatusSectionMigrationSentence:
    """AC-E7-F1-S1-T2-1: the status section no longer claims universal rejection."""

    def test_universal_rejection_sentence_removed(self) -> None:
        """The old blanket-rejection sentence must no longer appear verbatim."""
        text = _read_doc()
        assert "a scope file in any other shape is rejected rather than guessed at" not in text, (
            "docs/cli-reference.md must no longer claim that a scope file in any other "
            "shape is rejected rather than guessed at -- issue #270's legacy list shape "
            "is now migrated in place (AC-E7-F1-S1-T2-1)."
        )

    def test_status_section_mentions_legacy_list_migration(self) -> None:
        """The 'devbench status' scope.json sentence must mention the legacy list migration."""
        text = _read_doc()
        status_section = _extract_section(text, "### `status`")
        assert status_section, "### `status` section must exist in cli-reference.md"
        lower = status_section.lower()
        assert "legacy" in lower and "list" in lower and "migrat" in lower, (
            "docs/cli-reference.md '### `status`' section must state that a legacy "
            "list-shaped scope.json payload (issue #270) is migrated in place "
            "(AC-E7-F1-S1-T2-1)."
        )
        assert "270" in status_section, (
            "docs/cli-reference.md '### `status`' section must cite issue #270 for the "
            "legacy list-shape migration (AC-E7-F1-S1-T2-1)."
        )

    def test_status_section_states_other_shapes_still_raise(self) -> None:
        """The status section must still state that every OTHER non-object shape raises."""
        text = _read_doc()
        status_section = _extract_section(text, "### `status`")
        assert status_section, "### `status` section must exist in cli-reference.md"
        lower = status_section.lower()
        # A distinctive full phrase, not the generic tokens "other" / "raise" in isolation:
        # inverting the claim to "all other shapes are also migrated" still contains the
        # word "other" but drops this exact phrase.
        assert "non-object shape still raises" in lower, (
            "docs/cli-reference.md '### `status`' section must state that every OTHER "
            "non-object scope.json shape still raises with the pre-existing message text "
            "naming the file path (AC-E7-F1-S1-T2-1)."
        )


@pytest.mark.unit
class TestPersistenceSectionMigrationSubsection:
    """AC-E7-F1-S1-T2-2: the persistence section gains the migration subsection."""

    def test_migration_subsection_exists(self) -> None:
        """A 'Legacy list-shape migration (issue #270)' subsection heading must exist."""
        text = _read_doc()
        # Must match the actual '#### ' heading line, not merely the bare title -- the
        # bare title also appears inside the cross-reference link text added to the
        # '### `status`' section ('[Legacy list-shape migration (issue #270)](#...)'),
        # so a bare-substring check here would still pass with the subsection deleted.
        assert "#### Legacy list-shape migration (issue #270)" in text, (
            "docs/cli-reference.md must contain a '#### Legacy list-shape migration "
            "(issue #270)' subsection heading under 'scope.json persistence' "
            "(AC-E7-F1-S1-T2-2)."
        )

    def test_migration_subsection_is_nested_under_persistence(self) -> None:
        """The migration subsection must live inside the 'scope.json persistence' section."""
        text = _read_doc()
        persistence_section = _extract_section(text, "### scope.json persistence")
        assert persistence_section, "### scope.json persistence section must exist in cli-reference.md"
        assert "#### Legacy list-shape migration (issue #270)" in persistence_section, (
            "The '#### Legacy list-shape migration (issue #270)' subsection heading must "
            "be nested under 'scope.json persistence' so the persistence contract reads "
            "as one narrative (AC-E7-F1-S1-T2-2)."
        )

    def test_migration_subsection_names_scope_filter_from_file(self) -> None:
        """The read-path enumeration SENTENCE must name ScopeFilter.from_file.

        Scoped to the enumeration sentence itself (not the whole subsection):
        ``ScopeFilter.from_file`` is also named in the adjacent write-failure
        bullet, so a whole-section membership check still passes if the
        enumeration sentence is reverted or truncated to drop this path
        (test_review REVIEW_FAIL, AC-E7-F1-S1-T2-2).
        """
        text = _read_doc()
        migration_section = _extract_section(text, "#### Legacy list-shape migration (issue #270)")
        assert migration_section, "Legacy list-shape migration subsection must exist"
        enumeration_sentence = _extract_enumeration_sentence(migration_section)
        assert "ScopeFilter.from_file" in enumeration_sentence, (
            "The read-path enumeration sentence itself must name ScopeFilter.from_file "
            "as a read path that performs the migration (AC-E7-F1-S1-T2-2)."
        )

    def test_migration_subsection_names_status_report_next(self) -> None:
        """The read-path enumeration SENTENCE must name status, report and next.

        Scoped to the enumeration sentence itself: ``devbench status`` /
        ``devbench report`` / ``devbench next`` are also named in the adjacent
        write-failure bullet, so a whole-section membership check still
        passes if any of the three is dropped from the enumeration sentence,
        or if the sentence is reverted to the stale two-path form that only
        says "the CLI scope-banner reader" without naming the three commands
        (test_review REVIEW_FAIL, AC-E7-F1-S1-T2-2).
        """
        text = _read_doc()
        migration_section = _extract_section(text, "#### Legacy list-shape migration (issue #270)")
        assert migration_section, "Legacy list-shape migration subsection must exist"
        enumeration_sentence = _extract_enumeration_sentence(migration_section)
        assert "devbench status" in enumeration_sentence, (
            "The read-path enumeration sentence itself must name 'devbench status' as a "
            "consumer of the migrating scope-banner reader (AC-E7-F1-S1-T2-2)."
        )
        assert "devbench report" in enumeration_sentence, (
            "The read-path enumeration sentence itself must name 'devbench report' as a "
            "consumer of the migrating scope-banner reader (AC-E7-F1-S1-T2-2)."
        )
        assert "devbench next" in enumeration_sentence, (
            "The read-path enumeration sentence itself must name 'devbench next' as a "
            "consumer of the migrating scope-banner reader (AC-E7-F1-S1-T2-2)."
        )

    def test_migration_subsection_names_scope_show_as_third_path(self) -> None:
        """The read-path enumeration SENTENCE must name 'devbench scope show' as the
        third migrating read path, and present all three as exhaustive.

        src/devbench/cli.py's ``_scope_show`` also calls
        ``_read_and_migrate_scope_payload``, so an enumeration naming only
        ``ScopeFilter.from_file`` and the status/report/next scope-banner reader is
        incomplete against the shipped code (doc_review API_DOCS_STALE finding).
        Scoped to the enumeration sentence itself: ``devbench scope show`` is
        also named in the adjacent write-failure bullet, so a whole-section
        membership check still passes if the enumeration sentence is
        reverted to the stale two-path form (test_review REVIEW_FAIL).
        """
        text = _read_doc()
        migration_section = _extract_section(text, "#### Legacy list-shape migration (issue #270)")
        assert migration_section, "Legacy list-shape migration subsection must exist"
        enumeration_sentence = _extract_enumeration_sentence(migration_section)
        assert "devbench scope show" in enumeration_sentence, (
            "The read-path enumeration sentence itself must name 'devbench scope show' "
            "as the third migrating read path (src/devbench/cli.py _scope_show), "
            "alongside ScopeFilter.from_file and the status/report/next scope-banner "
            "reader (AC-E7-F1-S1-T2-2)."
        )
        lower_sentence = enumeration_sentence.lower()
        assert "all three delegate" in lower_sentence, (
            "The read-path enumeration sentence itself must present the enumeration of "
            "three read paths as exhaustive ('all three delegate to the same shared "
            "migration routine'), not the stale two-path 'both delegate' phrasing "
            "(AC-E7-F1-S1-T2-2)."
        )

    def test_migration_subsection_states_no_reader_still_rejects(self) -> None:
        """The subsection must state, behaviourally, that no reader rejects the legacy shape."""
        text = _read_doc()
        migration_section = _extract_section(text, "#### Legacy list-shape migration (issue #270)")
        assert migration_section, "Legacy list-shape migration subsection must exist"
        lower = migration_section.lower()
        # Behavioural claim about the system ("no reader rejects ..."), not
        # documentation-about-documentation phrasing ("... is documented as still
        # rejecting ..."), which leaks AC phrasing into an operator-facing page
        # (doc_review README_SYNC/style WARN).
        assert "no reader rejects the legacy list shape" in lower, (
            "The migration subsection must state behaviourally that no scope.json "
            "reader rejects the legacy list shape (not that none is 'documented as' "
            "rejecting it) (AC-E7-F1-S1-T2-2)."
        )
        assert "is documented as" not in lower, (
            "The migration subsection must not phrase the no-rejection claim in terms "
            "of what is or is not documented -- that leaks acceptance-criteria language "
            "into an operator-facing reference page."
        )


@pytest.mark.unit
class TestOperatorVisibleSignal:
    """AC-E7-F1-S1-T2-3: the single INFO line and propagating write failure are documented."""

    def test_single_info_line_documented(self) -> None:
        """The subsection must state that migration emits exactly one INFO line."""
        text = _read_doc()
        migration_section = _extract_section(text, "#### Legacy list-shape migration (issue #270)")
        assert migration_section, "Legacy list-shape migration subsection must exist"
        lower = migration_section.lower()
        # Full phrase: downgrading to "emits INFO lines" (plural, no count) would still
        # contain the bare tokens "info" and "one" (elsewhere in the section) but not
        # this exact claim.
        assert "emits exactly one info line" in lower, (
            "The migration subsection must document that migration emits exactly one "
            "INFO line naming the migrated file (AC-E7-F1-S1-T2-3)."
        )

    def test_info_line_names_the_file(self) -> None:
        """The subsection must state the INFO line names the migrated file."""
        text = _read_doc()
        migration_section = _extract_section(text, "#### Legacy list-shape migration (issue #270)")
        assert migration_section, "Legacy list-shape migration subsection must exist"
        lower = migration_section.lower()
        assert "naming the migrated file" in lower, (
            "The migration subsection must state that the single INFO line names the migrated file (AC-E7-F1-S1-T2-3)."
        )

    def test_write_failure_propagates_documented(self) -> None:
        """The subsection must state a write failure during migration is never swallowed."""
        text = _read_doc()
        migration_section = _extract_section(text, "#### Legacy list-shape migration (issue #270)")
        assert migration_section, "Legacy list-shape migration subsection must exist"
        lower = migration_section.lower()
        assert "never swallowed" in lower, (
            "The migration subsection must explicitly state a write failure during "
            "migration is never swallowed (AC-E7-F1-S1-T2-3, fail-fast posture)."
        )
        assert "propagates as an" in lower and "oserror" in lower, (
            "The migration subsection must document that the write failure propagates "
            "as an OSError on the paths where it does (AC-E7-F1-S1-T2-3)."
        )

    def test_write_failure_claim_is_qualified_per_path(self) -> None:
        """The propagation claim must be scoped per read path, not a blanket claim.

        doc_review API_DOCS_STALE finding: 'a write failure ... propagates as an
        OSError -- it is never swallowed' is contradicted on the ``devbench scope
        show`` path (src/devbench/cli.py:8474 catches OSError and returns 1). The
        claim must distinguish the ScopeFilter.from_file / scope-banner paths (where
        OSError propagates) from ``devbench scope show`` (which catches it and
        reports on stderr with exit code 1).
        """
        text = _read_doc()
        migration_section = _extract_section(text, "#### Legacy list-shape migration (issue #270)")
        assert migration_section, "Legacy list-shape migration subsection must exist"
        lower = migration_section.lower()
        assert "devbench scope show" in migration_section, (
            "The write-failure bullet must name 'devbench scope show' explicitly so the "
            "propagation claim is not stated as a blanket, path-agnostic claim "
            "(AC-E7-F1-S1-T2-3)."
        )
        assert "exit code 1" in lower or "exits 1" in lower or "return 1" in lower, (
            "The write-failure bullet must state that devbench scope show reports the "
            "failure and exits 1 rather than letting the OSError propagate "
            "(AC-E7-F1-S1-T2-3)."
        )


@pytest.mark.unit
class TestProvenanceSourcing:
    """AC-E7-F1-S1-T2-4: provenance sourcing rules are documented."""

    def test_sibling_session_state_files_documented(self) -> None:
        """The subsection must state provenance is read from sibling session-state files."""
        text = _read_doc()
        migration_section = _extract_section(text, "#### Legacy list-shape migration (issue #270)")
        assert migration_section, "Legacy list-shape migration subsection must exist"
        lower = migration_section.lower()
        assert "started_at" in migration_section and "started_by" in migration_section, (
            "The migration subsection must name started_at and started_by as the "
            "provenance fields sourced from sibling session-state files "
            "(AC-E7-F1-S1-T2-4)."
        )
        assert "read from the sibling session-state files" in lower, (
            "The migration subsection must describe the sibling session-state files as "
            "the source of true provenance, using this distinctive phrase rather than "
            "the bare token 'sibling' in isolation (AC-E7-F1-S1-T2-4)."
        )

    def test_unknown_sentinel_documented(self) -> None:
        """The subsection must document the explicit 'unknown' sentinel when siblings are absent."""
        text = _read_doc()
        migration_section = _extract_section(text, "#### Legacy list-shape migration (issue #270)")
        assert migration_section, "Legacy list-shape migration subsection must exist"
        assert '"unknown"' in migration_section or "'unknown'" in migration_section, (
            "The migration subsection must document the explicit 'unknown' sentinel "
            "recorded for started_at/started_by when sibling files are absent "
            "(AC-E7-F1-S1-T2-4)."
        )

    def test_never_fabricated_documented(self) -> None:
        """The subsection must state provenance is never fabricated from clock or current user."""
        text = _read_doc()
        migration_section = _extract_section(text, "#### Legacy list-shape migration (issue #270)")
        assert migration_section, "Legacy list-shape migration subsection must exist"
        lower = migration_section.lower()
        assert "never fabricated from the migration" in lower, (
            "The migration subsection must state, as a distinctive phrase, that "
            "started_at/started_by are never fabricated from the migration's own "
            "clock or the current user (AC-E7-F1-S1-T2-4)."
        )
        assert "clock" in lower, (
            "The migration subsection must explicitly rule out the migration's own "
            "clock as a provenance source (AC-E7-F1-S1-T2-4)."
        )
        assert "current user" in lower or "current os user" in lower, (
            "The migration subsection must explicitly rule out the current user as a "
            "provenance source (AC-E7-F1-S1-T2-4)."
        )


@pytest.mark.unit
class TestEmptyListReconciliation:
    """AC-E7-F1-S1-T2-5: the empty-list edge case is reconciled with the unscoped invariant."""

    def test_empty_list_case_mentioned(self) -> None:
        """The subsection must address the empty-array migration case."""
        text = _read_doc()
        migration_section = _extract_section(text, "#### Legacy list-shape migration (issue #270)")
        assert migration_section, "Legacy list-shape migration subsection must exist"
        lower = migration_section.lower()
        assert "migrated file whose array is empty" in lower, (
            "The migration subsection must address the empty-list ([]) migration edge "
            "case with this distinctive phrase, not the bare token 'empty' in isolation "
            "(AC-E7-F1-S1-T2-5)."
        )

    def test_absent_is_unscoped_invariant_referenced(self) -> None:
        """The subsection must reference the absent-is-unscoped invariant from multi-session-runs.md."""
        text = _read_doc()
        migration_section = _extract_section(text, "#### Legacy list-shape migration (issue #270)")
        assert migration_section, "Legacy list-shape migration subsection must exist"
        # Verbatim quote of the multi-session-runs.md invariant, not just the loose
        # tokens "unscoped" and "absent" appearing anywhere in the section.
        assert (
            "An unscoped session writes no `scope.json`: absent is the unscoped "
            "signal every reader honours." in migration_section
        ), (
            "The migration subsection must quote docs/multi-session-runs.md's "
            "absent-is-unscoped invariant verbatim when reconciling the empty-list "
            "migration case against it (AC-E7-F1-S1-T2-5)."
        )
        assert "multi-session-runs" in text, (
            "docs/cli-reference.md must reference multi-session-runs.md when "
            "reconciling the empty-list edge case (AC-E7-F1-S1-T2-5)."
        )

    def test_migration_never_creates_file_for_unscoped_session(self) -> None:
        """The subsection must state migration repairs an existing file, never creates one."""
        text = _read_doc()
        migration_section = _extract_section(text, "#### Legacy list-shape migration (issue #270)")
        assert migration_section, "Legacy list-shape migration subsection must exist"
        lower = migration_section.lower()
        assert "repairs an already-present, corrupt legacy file" in lower, (
            "The migration subsection must state, as a distinctive phrase, that "
            "migration repairs an already-present corrupt legacy file rather than "
            "creating one for an unscoped session (AC-E7-F1-S1-T2-5)."
        )
        assert "never creates a" in lower and "for an unscoped session" in lower, (
            "The migration subsection must explicitly state migration never creates a "
            "scope.json for an unscoped session (AC-E7-F1-S1-T2-5)."
        )


@pytest.mark.unit
class TestNoEmDashIntroduced:
    """REFACTOR requirement: no em-dash characters (U+2014) in the new subsection."""

    def test_migration_subsection_has_no_em_dash(self) -> None:
        """The new migration subsection must use -- rather than U+2014 em-dashes."""
        text = _read_doc()
        migration_section = _extract_section(text, "#### Legacy list-shape migration (issue #270)")
        assert migration_section, "Legacy list-shape migration subsection must exist"
        assert "\u2014" not in migration_section, (
            "docs/cli-reference.md's new migration subsection must not contain an "
            "em-dash (U+2014) character -- use '--' instead, matching repo convention."
        )
