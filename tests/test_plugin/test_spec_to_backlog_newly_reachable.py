"""Structural pin tests for E8-F1-S1-T3.

Verifies that `plugin-authoring/devbench-authoring/skills/spec-to-backlog/SKILL.md`'s
two newly-reachable-paths surfaces -- Step 1b item 13 (the canonical `## Definition of
Done` section description) and the Step 5b per-task self-critique checklist item 12 --
key off the `## Task Type: behavior-fix` taxonomy `validate-backlog` rule 21 already
validates, rather than the retired title-starts-with-"Fix" heuristic. That heuristic
originated in the E1 cherry-pick reject-list (upstream PR caylent-solutions/devbench#320,
spec 4.14) and was never carried into `src/devbench/backlog/proposal.py` on this repo's
mainline (per `CHANGELOG.md`'s `[Unreleased]` entry: "`_is_bug_fix_shaped` and the
`\"fix \"` title heuristic ... were never carried over and remain absent"). SKILL.md was
the surface that still carried the stale prose; E8-F1-S1-T1 (commit b35fd53f) made
`proposal.py` emit `NEWLY_REACHABLE_PATHS_AC_ITEM` into `## Acceptance Criteria` keyed on
`TASK_TYPE_BEHAVIOR_FIX`, and this task rewrites SKILL.md to match.

Also verifies the requirement is drafted as an `## Acceptance Criteria` item -- not a
`## Definition of Done` checkbox -- per spec 1.3 S1 (findings 320-D04, C-06): DoD
checkboxes are auto-ticked records, not gates. The wording must name the
`uv run devbench log-newly-reachable <unit-id> --path <p> --method <m> --result <r>`
verb, matching `proposal.py`'s shipped `NEWLY_REACHABLE_PATHS_AC_ITEM` constant rather
than the retired `[NEWLY_REACHABLE]`-into-`## Comments` free-text convention.

Section/item extraction reuses `SKILL_PATH`, `_section_text` and `_line_for_item`
from `tests/test_plugin/test_rubric_numbering.py` (the sibling module that already
parses the same SKILL.md structure) rather than re-implementing them, so the two
modules cannot drift into independent parsers of the same file. `_section_text`
there scopes on `##`/`###` headings only; Step 1b's own heading is bold prose
(`**Step 1b -- ...**`), not a `##`/`###` heading, so item 13 is instead scoped via
the enclosing `## Step 1` heading, under which item 13 is the sole rubric line
numbered 13 (verified against SKILL.md's Step 1 section, which contains exactly
one numbered list: the 15 canonical sections).

See `docs/newly-reachable-paths.md` for the full rationale.
"""

from __future__ import annotations

import pytest
from test_plugin.test_rubric_numbering import SKILL_PATH, _line_for_item, _section_text

RETIRED_TITLE_HEURISTIC = 'title starts with "Fix"'
LOG_NEWLY_REACHABLE_VERB = "uv run devbench log-newly-reachable <unit-id> --path <p> --method <m> --result <r>"


def _read_skill() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


@pytest.mark.unit
class TestStep1bItem13KeysOffTaskType:
    """AC-CODE-001: Step 1b item 13 keys off `## Task Type: behavior-fix`, names the
    `log-newly-reachable` verb, drafts the requirement under Acceptance Criteria, and
    no longer carries the retired title heuristic or the Definition-of-Done carrier."""

    def _item_13(self) -> str:
        # Step 1b's own heading is bold prose, not a `##`/`###` heading, so scope
        # on the enclosing `## Step 1` heading instead; item 13 is the sole
        # rubric line numbered 13 within that section.
        section = _section_text(_read_skill(), "Step 1 --")
        return _line_for_item(section, 13)

    def test_item_13_does_not_retain_title_heuristic(self) -> None:
        item = self._item_13()
        assert RETIRED_TITLE_HEURISTIC not in item, (
            f'Step 1b item 13 still quotes the retired title-starts-with-"Fix" heuristic: {item!r}'
        )

    def test_item_13_keys_off_task_type_behavior_fix(self) -> None:
        item = self._item_13()
        assert "## Task Type:" in item and "behavior-fix" in item, (
            f"Step 1b item 13 must key the newly-reachable-paths requirement off "
            f"'## Task Type: behavior-fix', found: {item!r}"
        )

    def test_item_13_names_log_newly_reachable_verb(self) -> None:
        item = self._item_13()
        assert LOG_NEWLY_REACHABLE_VERB in item, (
            f"Step 1b item 13 must name the exact verb {LOG_NEWLY_REACHABLE_VERB!r}, found: {item!r}"
        )

    def test_item_13_places_requirement_under_acceptance_criteria(self) -> None:
        item = self._item_13()
        assert "## Acceptance Criteria" in item, (
            f"Step 1b item 13 must direct the newly-reachable-paths requirement to "
            f"'## Acceptance Criteria', found: {item!r}"
        )

    def test_item_13_no_longer_instructs_a_dod_checklist_item_for_the_requirement(self) -> None:
        item = self._item_13()
        assert "[NEWLY_REACHABLE]" not in item, (
            f"Step 1b item 13 must not instruct logging the requirement via the retired "
            f"free-text '[NEWLY_REACHABLE]' comment convention, found: {item!r}"
        )


@pytest.mark.unit
class TestValidationChecklistItem12KeysOffTaskType:
    """AC-CODE-002: the Step 5b per-task self-critique checklist item 12 is rewritten
    to match item 13's grammar -- keyed off `## Task Type: behavior-fix`, checking the
    Acceptance Criteria surface, not the retired title heuristic or DoD surface."""

    def _item_12(self) -> str:
        section = _section_text(_read_skill(), "5b -- Self-critique at per-Task granularity")
        return _line_for_item(section, 12)

    def test_item_12_does_not_retain_title_heuristic(self) -> None:
        item = self._item_12()
        assert RETIRED_TITLE_HEURISTIC not in item, (
            f'Validation checklist item 12 still quotes the retired title-starts-with-"Fix" heuristic: {item!r}'
        )

    def test_item_12_keys_off_task_type_behavior_fix(self) -> None:
        item = self._item_12()
        assert "## Task Type:" in item and "behavior-fix" in item, (
            f"Validation checklist item 12 must key off '## Task Type: behavior-fix', found: {item!r}"
        )

    def test_item_12_checks_acceptance_criteria_not_definition_of_done(self) -> None:
        item = self._item_12()
        assert "## Acceptance Criteria" in item, (
            f"Validation checklist item 12 must check '## Acceptance Criteria' for the requirement, found: {item!r}"
        )

    def test_item_12_cites_step_1b_item_13(self) -> None:
        item = self._item_12()
        assert "Step 1b item 13" in item, (
            f"Validation checklist item 12 must cite 'Step 1b item 13' as the source of the "
            f"requirement it checks, found: {item!r}"
        )
