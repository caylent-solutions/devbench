"""Structural pin for CHANGELOG.md's E7-F2-S1-T3 write-path scope-attribution
entry (spec `integration-reality-gates-hardening.md` section 4.3, AC-9,
AC-WP-025).

E7-F2-S1-T3 delivers an operator-visible behaviour change to the
`check-write-path` gate -- a new keyword-only `scope` parameter on
`audit_write_path`, a new `WritePathAudit.attributed_sites` attribute, a
scope-limited itemized-findings BLAME split against a still repo-wide
verdict/RESULTS, a new out-of-scope fallback rendering line, and a new
zero-stdout exit-1 scope-resolution-failure terminal. E7-F2-S1-T3 has landed
on this branch as commit `ac64e7b` (`candidate-release/integration-reality-gates`),
so this module asserts two independent things:

1. That CHANGELOG.md carries an `[Unreleased]` entry describing that change,
   shaped like its neighbours (including the already-landed
   `docs/cli-reference.md` `check-write-path` section from sibling unit
   E7-F2-S1-T4, commit 88d9284, which documents the identical behaviour in
   the same present tense), naming the concrete behaviour the operator needs
   to know about (`TestWritePathScopeAttributionEntryExists` and
   `TestWritePathScopeAttributionEntryContent`).
2. That the API the entry cites actually exists on this tree
   (`TestWritePathScopeAttributionImplementationBinding`) -- `scope` is a
   real keyword-only parameter of `audit_write_path`, and `attributed_sites`
   is a real `WritePathAudit` dataclass field. This closes the evidence gap
   four earlier doc_review rounds identified while E7-F2-S1-T3 was still
   `## Status: blocked` with its production change quarantined off-branch in
   a git stash: at that time this module could only assert that CHANGELOG.md
   quoted itself, never that the quoted API existed, so a wrong entry could
   stay green forever. Landing E7-F2-S1-T3 makes the implementation-binding
   assertion doc_review requested land in the same change as the entry.

This module imports its markdown section/bullet-slicing helpers from the
greenfield shared module `tests/test_docs/_changelog_slicing.py` rather than
duplicating them. Those helpers are generic markdown-structure utilities
with no campaign-specific content, so extracting them leaves this module
with only the E7-F2-S1-T3-specific start phrase and assertions; the sibling
`tests/test_docs/test_changelog_observability.py` pins an unrelated
CHANGELOG campaign and is not imported from or edited by this module.

Note: an earlier draft of the CHANGELOG entry also published a
disclosure-boundary guarantee sentence ("that line discloses the
out-of-scope COUNT alone, never a filename ..."). doc_review required that
sentence removed while E7-F2-S1-T3 was still unshipped, because publishing a
security property the binary did not yet implement was a correctness defect
independent of tense. It is not restored here even though the shipped
`WritePathAudit.render()` now does implement that boundary, because
AC-CHANGELOG-001 does not require it and the entry is already fully accurate
without it.

Note: an earlier draft of this module also carried a dedicated
`TestWritePathScopeAttributionEntryFailsIfPhraseRemoved` class meant to
demonstrate the content assertions' failure-capability via a synthetic
mutation. It was removed during review remediation because its final
assertion (`assert phrase not in bullet.replace(phrase, "")`) is a tautology
over `str.replace` that can never fail and re-invoked no pin assertion
against the mutated text, so it proved nothing beyond what
`TestWritePathScopeAttributionEntryContent`'s assertions already prove
directly (each one calls `extract_bullet`/`read_doc` against the live
CHANGELOG.md and fails if the cited phrase is missing from it).

Source: E7-F2-S1-T5 (CHANGELOG.md). Spec
`integration-reality-gates-hardening.md` section 4.3; AC-9; AC-WP-025.
"""

from __future__ import annotations

import inspect

import pytest
from test_docs._changelog_slicing import (
    extract_bullet,
    normalize_whitespace,
    read_doc,
    section_containing,
)

from devbench.plugin_helpers.permission_flag_writepath import (
    WritePathAudit,
    audit_write_path,
)

_START_PHRASE = "`check-write-path` now attributes its itemized findings"


@pytest.fixture
def bullet() -> str:
    """The E7-F2-S1-T3 CHANGELOG bullet, whitespace-normalized so a phrase
    assertion is not sensitive to exactly where markdown hard-wraps a line."""
    return normalize_whitespace(extract_bullet(read_doc(), _START_PHRASE))


@pytest.mark.unit
class TestWritePathScopeAttributionEntryExists:
    """AC-CHANGELOG-001 / AC-CHANGELOG-003: the entry exists somewhere under a
    ``## [...]`` version section."""

    def test_entry_exists_under_some_version_section(self) -> None:
        text = read_doc()
        owning = section_containing(text, _START_PHRASE)
        assert owning, (
            f"No '## [...]' section of CHANGELOG.md contains a bullet starting "
            f"{_START_PHRASE!r} (spec section 4.3, AC-9, AC-WP-025)."
        )


@pytest.mark.unit
class TestWritePathScopeAttributionEntryContent:
    """AC-CHANGELOG-001 / AC-CHANGELOG-003: the bullet names the delivering
    unit, the spec citations, and the concrete operator-visible behaviour
    change."""

    def test_entry_cites_delivering_unit_and_spec(self, bullet: str) -> None:
        assert "E7-F2-S1-T3" in bullet, "The entry must cite the delivering unit E7-F2-S1-T3."
        assert "integration-reality-gates-hardening.md" in bullet, (
            "The entry must cite 'integration-reality-gates-hardening.md' (spec ref)."
        )
        assert "section 4.3" in bullet, "The entry must cite spec section 4.3."
        assert "AC-9" in bullet, "The entry must cite AC-9."
        assert "AC-WP-025" in bullet, "The entry must cite AC-WP-025."

    def test_entry_names_the_scope_parameter(self, bullet: str) -> None:
        assert "`scope`" in bullet, "The entry must name audit_write_path's new keyword-only `scope` parameter."
        assert "keyword-only" in bullet, "The entry must describe `scope` as keyword-only."

    def test_entry_names_attributed_sites(self, bullet: str) -> None:
        assert "attributed_sites" in bullet, "The entry must name the new WritePathAudit.attributed_sites attribute."

    def test_entry_describes_blame_versus_results_split(self, bullet: str) -> None:
        assert "BLAME" in bullet, "The entry must describe the scope-limited itemized findings as BLAME."
        assert "repo-wide" in bullet, (
            "The entry must describe the verdict/mentions/assignment_sites/findings/status line as staying repo-wide."
        )
        assert "verdict" in bullet, "The entry must name the verdict as staying repo-wide."
        assert "findings" in bullet, "The entry must name the findings count as staying repo-wide."

    def test_entry_names_the_out_of_scope_fallback_line(self, bullet: str) -> None:
        assert "(no assignment/setter sites found within this unit's scope; N found outside scope)" in bullet, (
            "The entry must quote the new third rendering fallback line verbatim."
        )

    def test_entry_names_the_scope_resolution_failure_terminal(self, bullet: str) -> None:
        assert "exit-1" in bullet, "The entry must name the new exit-1 terminal."
        assert "zero-stdout" in bullet or "ZERO bytes to stdout" in bullet, (
            "The entry must describe the new terminal as writing zero stdout bytes."
        )
        assert "scope resolution" in bullet.lower() or "scope-resolution" in bullet.lower(), (
            "The entry must name the failure as a scope-resolution failure."
        )


@pytest.mark.unit
class TestWritePathScopeAttributionImplementationBinding:
    """Binds the CHANGELOG prose to the actually-shipped API on this tree,
    closing the evidence gap doc_review's rounds 1-4 identified while
    E7-F2-S1-T3 was still blocked and quarantined off-branch. Each assertion
    fails if the corresponding API surface regresses, independent of what
    CHANGELOG.md says."""

    def test_scope_is_a_keyword_only_parameter_of_audit_write_path(self) -> None:
        parameters = inspect.signature(audit_write_path).parameters
        assert "scope" in parameters, "audit_write_path must declare a 'scope' parameter."
        assert parameters["scope"].kind is inspect.Parameter.KEYWORD_ONLY, (
            "audit_write_path's 'scope' parameter must be keyword-only, matching the CHANGELOG entry."
        )
        assert parameters["scope"].default is None, "audit_write_path's 'scope' parameter must default to None."

    def test_attributed_sites_is_a_writepathaudit_dataclass_field(self) -> None:
        assert "attributed_sites" in WritePathAudit.__dataclass_fields__, (
            "WritePathAudit must declare an 'attributed_sites' field, matching the CHANGELOG entry."
        )
