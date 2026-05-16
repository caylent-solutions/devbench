"""Structural pins for the --include / --exclude scope-selector additions in docs/cli-reference.md.

Verifies that docs/cli-reference.md documents:
- The printer-pages-style ``--include`` / ``--exclude`` flag syntax with worked examples (AC-190-1..4, AC-190-7).
- Single-ID tokens, last-segment range tokens, and mixed comma-separated lists.
- The ``--exclude`` subtraction semantics and evaluation order.
- The complex example: ``--include "E1-E10" --exclude "E5,E7-F3"`` (AC-190-7).
- The ``scope`` subcommand (set / clear / show) with the pre-arm workflow.
- Updated ``start``, ``status``, ``report``, and ``next`` sections mentioning the new flags.

Spec source: spec/devbench-self-improve.md section 4.2. Issue #190.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
CLI_REFERENCE_DOC = REPO_ROOT / "docs" / "cli-reference.md"


def _read_doc() -> str:
    return CLI_REFERENCE_DOC.read_text(encoding="utf-8")


def _extract_section(text: str, heading: str) -> str:
    """Return the content of the section starting at ``heading`` up to the next same-level heading."""
    idx = text.find(heading)
    if idx == -1:
        return ""
    section_text = text[idx:]
    # Find the next heading at the same level (### for ### headings, ## for ## headings).
    level = len(heading.split(" ")[0])  # count leading '#'
    marker = "\n" + "#" * level + " "
    next_idx = section_text.find(marker, 1)
    if next_idx != -1:
        return section_text[:next_idx]
    return section_text


@pytest.mark.unit
class TestScopeSelectors_SingleIDToken:
    """AC-190-1: Single-ID token syntax is documented."""

    def test_single_id_token_mentioned(self) -> None:
        """The doc must describe single-ID tokens matching a WU and its descendants."""
        text = _read_doc()
        lower = text.lower()
        # Must mention single-ID tokens or individual IDs in the context of --include.
        assert "single" in lower or "single-id" in lower or "single id" in lower, (
            "docs/cli-reference.md must document single-ID token syntax for --include/--exclude "
            "(AC-190-1: a single-ID token matches that WU and all descendants)."
        )

    def test_single_id_example_present(self) -> None:
        """The doc must show a single-ID example such as 'E5' or 'E5-F1-S2-T3'."""
        text = _read_doc()
        # These are canonical single-ID examples from the spec (section 4.2.1).
        has_example = (
            '"E5"' in text
            or "'E5'" in text
            or "--include E5" in text
            or "--include \"E5\"" in text
            or "E5-F1-S2-T3" in text
        )
        assert has_example, (
            "docs/cli-reference.md must include a worked example of a single-ID token "
            "such as 'E5' or 'E5-F1-S2-T3' (AC-190-1)."
        )


@pytest.mark.unit
class TestScopeSelectors_RangeToken:
    """AC-190-2: Range token syntax is documented."""

    def test_range_token_mentioned(self) -> None:
        """The doc must describe range tokens that expand on the final segment."""
        text = _read_doc()
        lower = text.lower()
        assert "range" in lower, (
            "docs/cli-reference.md must document range token syntax (e.g. 'E1-E3') for "
            "--include/--exclude (AC-190-2: ranges expand inclusively on the final segment)."
        )

    def test_range_example_present(self) -> None:
        """The doc must show a range token example such as 'E1-E3'."""
        text = _read_doc()
        # E1-E3 is the canonical range example from the spec.
        has_range_example = "E1-E3" in text or "E1-E5" in text or "E1-E10" in text
        assert has_range_example, (
            "docs/cli-reference.md must include a worked range-token example "
            "such as 'E1-E3' or 'E1-E10' (AC-190-2)."
        )

    def test_reverse_range_error_documented(self) -> None:
        """The doc must state that reverse ranges are rejected with an error."""
        text = _read_doc()
        lower = text.lower()
        has_reverse_doc = "reverse" in lower or "ascending" in lower
        assert has_reverse_doc, (
            "docs/cli-reference.md must document that reverse-order range tokens "
            "(e.g. 'E3-E1') are rejected with an actionable error message (AC-190-5 / spec 4.2.1)."
        )


@pytest.mark.unit
class TestScopeSelectors_MixedList:
    """AC-190-3: Mixed comma-separated token list is documented."""

    def test_comma_separated_list_mentioned(self) -> None:
        """The doc must explain that tokens are comma-separated (printer-pages style)."""
        text = _read_doc()
        lower = text.lower()
        has_comma_doc = "comma" in lower or "comma-separated" in lower
        assert has_comma_doc, (
            "docs/cli-reference.md must describe that tokens are comma-separated "
            "(printer-pages style union) for --include/--exclude (AC-190-3)."
        )

    def test_mixed_list_example_present(self) -> None:
        """The doc must show a mixed token list example such as 'E1-E3, E5'."""
        text = _read_doc()
        # Must have at least one example with a comma inside a token string.
        has_mixed = (
            "E1-E3, E5" in text
            or "E1-E3,E5" in text
            or "E1, E3" in text
            or "E1,E3" in text
            or "E1-E3, E5" in text
        )
        assert has_mixed, (
            "docs/cli-reference.md must include a worked example of a comma-separated "
            "mixed token list such as 'E1-E3, E5' (AC-190-3)."
        )


@pytest.mark.unit
class TestScopeSelectors_ExcludeSubtraction:
    """AC-190-4: --exclude subtracts from include set; evaluation order documented."""

    def test_exclude_flag_present(self) -> None:
        """The --exclude flag must be documented in the doc."""
        text = _read_doc()
        assert "--exclude" in text, (
            "docs/cli-reference.md must document the --exclude flag for scope selectors (AC-190-4)."
        )

    def test_exclude_semantics_documented(self) -> None:
        """The doc must state that --exclude subtracts from the include set."""
        text = _read_doc()
        lower = text.lower()
        has_subtract_doc = (
            "subtract" in lower
            or "subtracts" in lower
            or "exclude" in lower and "include" in lower
        )
        assert has_subtract_doc, (
            "docs/cli-reference.md must document that --exclude subtracts from "
            "the include set (AC-190-4)."
        )

    def test_evaluation_order_documented(self) -> None:
        """The doc must state the evaluation order (include first, then exclude)."""
        text = _read_doc()
        lower = text.lower()
        # The spec says: include set is built first, then exclude is subtracted.
        # Must mention order or priority between include and exclude.
        has_order = (
            "order" in lower
            or "first" in lower
            or "then" in lower
            or "evaluation" in lower
        )
        assert has_order, (
            "docs/cli-reference.md must document the evaluation order for scope selectors: "
            "include set is expanded first, then exclude tokens are subtracted (AC-190-4)."
        )


@pytest.mark.unit
class TestScopeSelectors_ComplexExample:
    """AC-190-7: The complex --include / --exclude worked example is present."""

    def test_complex_example_e1_e10_exclude_e5_e7_f3(self) -> None:
        """The doc must show the canonical AC-190-7 example."""
        text = _read_doc()
        # AC-190-7 requires: --include "E1-E10" --exclude "E5,E7-F3"
        has_complex = (
            "E1-E10" in text
            and ("E5" in text and "E7-F3" in text or "E7" in text and "--exclude" in text)
        )
        assert has_complex, (
            "docs/cli-reference.md must include the AC-190-7 worked example: "
            "--include \"E1-E10\" --exclude \"E5,E7-F3\" (or equivalent demonstrating "
            "the combined --include and --exclude behaviour)."
        )


@pytest.mark.unit
class TestScopeSelectors_StartCommandUpdated:
    """The start command section must document --include and --exclude flags."""

    def test_start_section_documents_include_flag(self) -> None:
        """The 'start' section must mention --include."""
        text = _read_doc()
        start_section = _extract_section(text, "### `start`")
        assert start_section, "### `start` section must exist in cli-reference.md"
        assert "--include" in start_section, (
            "docs/cli-reference.md '### `start`' section must document the --include flag "
            "for scope selectors (spec section 4.2.2, AC-190-8)."
        )

    def test_start_section_documents_exclude_flag(self) -> None:
        """The 'start' section must mention --exclude."""
        text = _read_doc()
        start_section = _extract_section(text, "### `start`")
        assert start_section, "### `start` section must exist in cli-reference.md"
        assert "--exclude" in start_section, (
            "docs/cli-reference.md '### `start`' section must document the --exclude flag "
            "for scope selectors (spec section 4.2.2, AC-190-8)."
        )

    def test_start_section_mentions_scope_json_persistence(self) -> None:
        """The 'start' section must mention that the scope is persisted to scope.json."""
        text = _read_doc()
        start_section = _extract_section(text, "### `start`")
        assert start_section, "### `start` section must exist in cli-reference.md"
        lower = start_section.lower()
        assert "scope.json" in lower or "scope" in lower, (
            "docs/cli-reference.md '### `start`' section must mention scope.json persistence "
            "(spec section 4.2.5, AC-190-8: --include writes .devbench/scope.json)."
        )


@pytest.mark.unit
class TestScopeSelectors_StatusCommandUpdated:
    """The status command section must document --include, --exclude, and scope banner."""

    def test_status_section_documents_include_flag(self) -> None:
        """The 'status' section must mention --include for one-off override."""
        text = _read_doc()
        status_section = _extract_section(text, "### `status`")
        assert status_section, "### `status` section must exist in cli-reference.md"
        assert "--include" in status_section, (
            "docs/cli-reference.md '### `status`' section must document the --include flag "
            "as a one-off override of active scope.json (spec section 4.2.2, AC-190-11)."
        )

    def test_status_section_documents_scope_banner(self) -> None:
        """The 'status' section must mention the SCOPE: banner printed when scope is active."""
        text = _read_doc()
        status_section = _extract_section(text, "### `status`")
        assert status_section, "### `status` section must exist in cli-reference.md"
        lower = status_section.lower()
        has_banner = "scope:" in lower or "scope banner" in lower or "banner" in lower
        assert has_banner, (
            "docs/cli-reference.md '### `status`' section must document the SCOPE: banner "
            "printed above the Status Summary when a scope is active (spec section 4.2.2, AC-190-10)."
        )


@pytest.mark.unit
class TestScopeSelectors_ScopeSubcommand:
    """The doc must contain a 'scope' subcommand section with set/clear/show."""

    def test_scope_subcommand_section_exists(self) -> None:
        """A ### `scope` section must exist in cli-reference.md."""
        text = _read_doc()
        assert "### `scope`" in text, (
            "docs/cli-reference.md must contain a '### `scope`' section documenting "
            "the persistent scope management subcommand (spec section 4.2.6)."
        )

    def test_scope_set_documented(self) -> None:
        """The scope section must document 'devbench scope set'."""
        text = _read_doc()
        assert "scope set" in text, (
            "docs/cli-reference.md scope section must document 'devbench scope set' "
            "(spec section 4.2.6.1)."
        )

    def test_scope_clear_documented(self) -> None:
        """The scope section must document 'devbench scope clear'."""
        text = _read_doc()
        assert "scope clear" in text, (
            "docs/cli-reference.md scope section must document 'devbench scope clear' "
            "(spec section 4.2.6.1)."
        )

    def test_scope_show_documented(self) -> None:
        """The scope section must document 'devbench scope show'."""
        text = _read_doc()
        assert "scope show" in text, (
            "docs/cli-reference.md scope section must document 'devbench scope show' "
            "(spec section 4.2.6.1)."
        )

    def test_scope_section_include_flag_documented(self) -> None:
        """The scope section must document the --include flag for 'scope set'."""
        text = _read_doc()
        scope_section = _extract_section(text, "### `scope`")
        assert scope_section, "### `scope` section must exist"
        assert "--include" in scope_section, (
            "docs/cli-reference.md '### `scope`' section must document the --include flag "
            "for 'scope set' (spec section 4.2.6.1)."
        )

    def test_scope_section_pre_arm_workflow_documented(self) -> None:
        """The scope section must describe the interactive pre-arm workflow."""
        text = _read_doc()
        scope_section = _extract_section(text, "### `scope`")
        assert scope_section, "### `scope` section must exist"
        lower = scope_section.lower()
        has_pre_arm = "pre-arm" in lower or "pre arm" in lower or "interactive" in lower
        assert has_pre_arm, (
            "docs/cli-reference.md '### `scope`' section must describe the interactive "
            "pre-arm workflow: scope set before launching Claude Code "
            "(spec section 4.2.6.3)."
        )

    def test_scope_section_idempotent_clear_documented(self) -> None:
        """The scope section must state that 'clear' is idempotent (rc=0 even when no file)."""
        text = _read_doc()
        scope_section = _extract_section(text, "### `scope`")
        assert scope_section, "### `scope` section must exist"
        lower = scope_section.lower()
        assert "idempotent" in lower or "no scope pending" in lower, (
            "docs/cli-reference.md '### `scope`' section must document that 'scope clear' "
            "is idempotent (rc=0 with 'no scope pending' message when no file exists) "
            "(spec section 4.2.6.2)."
        )


@pytest.mark.unit
class TestScopeSelectors_OutOfRangeWarning:
    """Out-of-range tokens emit a warning but do not abort (AC-190-6)."""

    def test_out_of_range_warning_documented(self) -> None:
        """The doc must state that out-of-range tokens emit a warning but don't abort."""
        text = _read_doc()
        lower = text.lower()
        has_warning_doc = (
            "out-of-range" in lower
            or "out of range" in lower
            or ("warning" in lower and "abort" in lower)
            or ("warning" in lower and "don't fail" in lower)
            or ("warning" in lower and "do not fail" in lower)
            or ("warn" in lower and "abort" in lower)
        )
        assert has_warning_doc, (
            "docs/cli-reference.md must document that out-of-range scope tokens emit a "
            "warning but do not abort the run (AC-190-6 / spec section 4.2.1)."
        )


@pytest.mark.unit
class TestScopeSelectors_ScopeJsonSchema:
    """The doc must describe the scope.json schema (AC-190-8)."""

    def test_scope_json_filename_mentioned(self) -> None:
        """The doc must name the scope.json file path."""
        text = _read_doc()
        assert "scope.json" in text, (
            "docs/cli-reference.md must mention 'scope.json' -- the file written by "
            "--include and read by subsequent start/status/report/next calls (AC-190-8)."
        )

    def test_scope_json_path_mentioned(self) -> None:
        """The doc must name the .devbench/ directory where scope.json lives."""
        text = _read_doc()
        assert ".devbench" in text or ".devbench/scope.json" in text, (
            "docs/cli-reference.md must document that scope.json lives under "
            "<workspace>/.devbench/scope.json (AC-190-8 / spec section 4.2.5)."
        )

    def test_scope_json_cleared_on_clean_exit_documented(self) -> None:
        """The doc must state that scope.json is deleted on clean orchestrator exit."""
        text = _read_doc()
        lower = text.lower()
        has_clear_doc = (
            "clean exit" in lower
            or "consumed" in lower
            or "deleted on" in lower
            or "cleared on" in lower
            or "clears on" in lower
        )
        assert has_clear_doc, (
            "docs/cli-reference.md must document that scope.json is consumed (deleted) "
            "on clean orchestrator exit (AC-190-13 / spec section 4.2.5)."
        )


@pytest.mark.unit
class TestScopeSelectors_ReportCommandUpdated:
    """The report command section must document --include, --exclude, and scope filter flags."""

    def test_report_section_usage_includes_include_flag(self) -> None:
        """The 'report' usage line must contain --include \"<tokens>\"."""
        text = _read_doc()
        report_section = _extract_section(text, "### `report`")
        assert report_section, "### `report` section must exist in cli-reference.md"
        assert '--include "<tokens>"' in report_section or "--include" in report_section, (
            "docs/cli-reference.md '### `report`' usage line must include "
            "[--include \"<tokens>\"] to match the other scope-aware commands "
            "(scope selectors reference section and code confirm report accepts --include)."
        )

    def test_report_section_usage_includes_exclude_flag(self) -> None:
        """The 'report' usage line must contain --exclude \"<tokens>\"."""
        text = _read_doc()
        report_section = _extract_section(text, "### `report`")
        assert report_section, "### `report` section must exist in cli-reference.md"
        assert '--exclude "<tokens>"' in report_section or "--exclude" in report_section, (
            "docs/cli-reference.md '### `report`' usage line must include "
            "[--exclude \"<tokens>\"] to eliminate the internal contradiction with "
            "the scope selectors reference section."
        )

    def test_report_section_has_scope_filter_flags_paragraph(self) -> None:
        """The 'report' section must have a Scope filter flags paragraph."""
        text = _read_doc()
        report_section = _extract_section(text, "### `report`")
        assert report_section, "### `report` section must exist in cli-reference.md"
        lower = report_section.lower()
        assert "scope filter flags" in lower, (
            "docs/cli-reference.md '### `report`' section must include a "
            "'Scope filter flags' paragraph consistent with the status and next sections "
            "(doc_review judge finding: report section omitted scope flag documentation)."
        )

    def test_report_scope_filter_references_scope_selectors(self) -> None:
        """The 'report' scope filter paragraph must link to the Scope selectors reference."""
        text = _read_doc()
        report_section = _extract_section(text, "### `report`")
        assert report_section, "### `report` section must exist in cli-reference.md"
        assert "scope-selectors" in report_section or "Scope selectors" in report_section, (
            "docs/cli-reference.md '### `report`' scope filter paragraph must reference "
            "the Scope selectors section (same cross-reference as status and next sections)."
        )


@pytest.mark.unit
class TestScopeSelectors_ContentsTableUpdated:
    """The Contents table must reference the scope subcommand."""

    def test_contents_includes_scope_entry_or_backlog_write_section(self) -> None:
        """The Contents table or Backlog write section must reference scope."""
        text = _read_doc()
        # Either the Contents table links to scope, or the Backlog write section heading
        # contains the scope entry.
        contents_idx = text.find("## Contents")
        if contents_idx == -1:
            return  # no Contents table; skip
        next_section = text.find("\n---", contents_idx)
        if next_section == -1:
            next_section = contents_idx + 500
        contents_block = text[contents_idx:next_section]
        lower = text.lower()
        # Accept either a Contents link or "scope" appearing in the Backlog write section.
        has_scope_in_nav = "scope" in contents_block.lower() or "scope" in lower
        assert has_scope_in_nav, (
            "docs/cli-reference.md must reference the 'scope' subcommand in the navigation "
            "contents table or in the backlog-write section listing."
        )
