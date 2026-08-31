"""Shared Step B.reject pointer-integrity helpers for the doc <-> manifest-amender.md
structural-sync regression tests (E3-F2-S1-T9).

Background: two sibling regression suites --
``test_orchestrate_skill_reject_recipe_sync.py`` (E3-F2-S1-T7) and
``test_manifest_amendments_doc_reject_recipe_sync.py`` (E3-F2-S1-T8) -- each
independently hand-copied ``MANIFEST_AMENDER_PATH``,
``STEP_B_REJECT_FENCE_PATTERN``, the ``manifest_amender_text`` fixture, and
the two pointer-integrity assertions that confirm
``plugin/devbench-orchestrate/agents/manifest-amender.md``'s
``**Step B.reject**`` section still exists and still contains a fenced
```bash block with a ``git`` invocation. Both ``code_review`` and
``test_review`` flagged the duplication: the same source-of-truth pointer
was independently re-verified in two files, so a future structural change
to ``manifest-amender.md``'s ``**Step B.reject**`` section would need the
same fix applied twice.

This module defines each shared piece exactly once. Each downstream test
module keeps only its document-specific extraction pattern and
per-document assertions (which necessarily differ -- SKILL.md's step-4c
bullet and docs/manifest-amendments.md's item 7 name different source
documents with different failure messages), and delegates the
pointer-integrity checks to `assert_manifest_amender_file_exists`,
`assert_step_b_reject_section_exists` and
`assert_step_b_reject_section_has_git_invocation` below via thin
per-file test methods, so the same assertion logic runs once per suite
without being hand-copied.

``STEP_B_REJECT_FENCE_PATTERN`` deliberately pins only that SOME ``git``
invocation survives in the fenced block, not any specific subcommand
(restore/checkout/clean today, something else after a future rewrite) --
tightening it to a specific command would make this shared helper itself
the next stale copy the sibling modules had to be fixed for.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from devbench.backlog.proposal import ProposedTask
from devbench.constants import (
    TASK_TYPE_BEHAVIOR_FIX,
    TASK_TYPE_CHORE,
    TASK_TYPE_DOCS,
    TASK_TYPE_FEATURE,
    TASK_TYPE_REFACTOR,
    TASK_TYPE_TEST_ONLY,
    VALID_TASK_TYPES,
)

MANIFEST_AMENDER_PATH = (
    Path(__file__).resolve().parent.parent.parent / "plugin" / "devbench-orchestrate" / "agents" / "manifest-amender.md"
)

STEP_B_REJECT_FENCE_PATTERN = re.compile(
    r"\*\*Step B\.reject\*\*.*?```bash\n(?P<code>.*?)```",
    re.DOTALL,
)


@pytest.fixture(scope="module")
def manifest_amender_text() -> str:
    return MANIFEST_AMENDER_PATH.read_text(encoding="utf-8")


def assert_manifest_amender_file_exists() -> None:
    """Shared pointer-integrity check: the source-of-truth file exists."""
    assert MANIFEST_AMENDER_PATH.is_file(), f"manifest-amender.md missing at {MANIFEST_AMENDER_PATH}"


def assert_step_b_reject_section_exists(manifest_amender_text: str) -> None:
    """Shared pointer-integrity check: the '**Step B.reject**' section exists.

    Both SKILL.md's step-4c bullet and docs/manifest-amendments.md's item 7
    point at this section as their single source of truth for the
    git-cleanup recipe; if it is renamed or removed, both pointers go
    stale and must be updated in the same change.
    """
    assert "**Step B.reject**" in manifest_amender_text, (
        "manifest-amender.md no longer contains a '**Step B.reject**' "
        "section. Other docs point at this section as the single source "
        "of truth for the git-cleanup recipe; if it is renamed or "
        "removed, those pointers go stale and must be updated in the "
        "same change."
    )


def assert_step_b_reject_section_has_git_invocation(manifest_amender_text: str) -> None:
    """Shared pointer-integrity check: the section's fenced block still has a git call.

    See `assert_step_b_reject_section_exists` -- a section that survives
    with an empty or command-free body would leave the downstream
    pointers misleading even though the heading itself still exists.
    """
    match = STEP_B_REJECT_FENCE_PATTERN.search(manifest_amender_text)
    assert match is not None, (
        "Could not locate a fenced ```bash block following the "
        "'**Step B.reject**' header in manifest-amender.md via the "
        f"extraction pattern {STEP_B_REJECT_FENCE_PATTERN.pattern!r}. "
        "Either the section's structure changed or the pattern needs "
        "updating to track it."
    )
    code_block = match.group("code")
    assert re.search(r"\bgit\b", code_block), (
        "The '**Step B.reject**' fenced code block in manifest-amender.md "
        "contains no 'git' invocation. Other docs point at this section "
        "as the single source of truth for the git-cleanup recipe; a "
        "section that survives with an empty or command-free body would "
        "leave those pointers misleading even though the "
        "'**Step B.reject**' heading itself still exists."
    )


# ---------------------------------------------------------------------------
# Shared newly-reachable-paths task-type-keying helpers (E8-F2-S1-T1)
#
# `tests/test_backlog/test_proposal.py`'s `TestNewlyReachableTaskTypeKeying`
# and `tests/test_integration/test_gate_newly_reachable_e2e.py`'s
# `TestJourneyTaskTypeTaxonomyGatesAcceptanceCriterion` both call the same
# real, unmocked `generate_draft_md` over the same six-value task-type
# taxonomy to pin the same invariant (spec 4.9a, 1.3 S1): the
# newly-reachable-paths acceptance-criterion line is appended only when
# `ProposedTask.task_type` resolves to `constants.TASK_TYPE_BEHAVIOR_FIX`,
# and no Definition-of-Done line is ever appended for any task type. Prior
# to this extraction each suite independently hand-typed the taxonomy
# tuple, the `ProposedTask` factory and the Definition-of-Done assertion
# body (code_review and test_review both flagged the duplicate as
# byte-identical). Defining each piece exactly once here means a future
# change to the taxonomy or to `generate_draft_md`'s section structure
# needs the fix applied in one place, not two.
# ---------------------------------------------------------------------------

NEWLY_REACHABLE_TASK_TYPE_TAXONOMY: tuple[str, ...] = tuple(sorted(VALID_TASK_TYPES))

# Explicit per-type expectation table (deliberately NOT derived from
# `task_type == TASK_TYPE_BEHAVIOR_FIX`): the newly-reachable-paths
# acceptance-criterion line is a keyed exception granted to exactly one
# task type today, and that grant must stay an explicit, reviewable
# decision rather than an implicit formula that would silently re-derive
# the "right" answer for a future seventh task type. If `VALID_TASK_TYPES`
# (`devbench.constants`) ever grows a new member without a matching entry
# here, `_ac_line_expected_for` below raises instead of defaulting, so the
# taxonomy and the expectation table can never silently drift apart.
_NEWLY_REACHABLE_TASK_TYPE_AC_LINE_EXPECTED_BY_TYPE: dict[str, bool] = {
    TASK_TYPE_BEHAVIOR_FIX: True,
    TASK_TYPE_FEATURE: False,
    TASK_TYPE_REFACTOR: False,
    TASK_TYPE_TEST_ONLY: False,
    TASK_TYPE_DOCS: False,
    TASK_TYPE_CHORE: False,
}


def _ac_line_expected_for(task_type: str) -> bool:
    """Look up the explicit AC-line expectation for `task_type`, raising
    loudly (rather than defaulting) when `VALID_TASK_TYPES` has outpaced
    `_NEWLY_REACHABLE_TASK_TYPE_AC_LINE_EXPECTED_BY_TYPE`.
    """
    try:
        return _NEWLY_REACHABLE_TASK_TYPE_AC_LINE_EXPECTED_BY_TYPE[task_type]
    except KeyError as exc:
        raise AssertionError(
            f"No newly-reachable AC-line expectation registered for task type {task_type!r}. "
            "devbench.constants.VALID_TASK_TYPES gained a member that "
            "_NEWLY_REACHABLE_TASK_TYPE_AC_LINE_EXPECTED_BY_TYPE "
            "(tests/test_integration/conftest.py) does not yet cover -- add an "
            "explicit True/False entry for it before this suite can run."
        ) from exc


NEWLY_REACHABLE_TASK_TYPE_AC_LINE_EXPECTATIONS: tuple[tuple[str, bool], ...] = tuple(
    (task_type, _ac_line_expected_for(task_type)) for task_type in NEWLY_REACHABLE_TASK_TYPE_TAXONOMY
)


def make_newly_reachable_keying_task(task_type: str) -> ProposedTask:
    """Shared `ProposedTask` factory for the newly-reachable task-type-keying
    parametrized suites (see module docstring above). Only the resulting
    draft's task-type-keyed content ever needed to differ between the two
    suites; every other field is incidental to that assertion, so both now
    build from this single factory instead of two near-identical copies.
    """
    return ProposedTask(
        suggested_id="E0-F1-S1-T9",
        title="Fix the newly-reachable-paths journey exporter crash",
        files_to_own=["src/exporter.py"],
        linked_scenarios=[],
        suggested_acs=["AC-FIX-001 exporter no longer crashes"],
        suggested_approach="Fix the crash and enumerate what it newly unlocks.",
        task_type=task_type,
    )


def assert_no_newly_reachable_definition_of_done_line(md: str) -> None:
    """Shared assertion: no Definition-of-Done line ever mentions the
    newly-reachable-paths mechanism, for ANY task type (spec 1.3 S1,
    findings 320-D04/C-06: a DoD checkbox is auto-ticked on the done
    transition and is never a gate, so this mechanism must never live
    there).
    """
    dod_section = md.split("## Definition of Done", maxsplit=1)[1].split("## TDD Cycle Log", maxsplit=1)[0]
    assert "newly-reachable" not in dod_section.lower(), (
        f"Definition of Done section must never mention newly-reachable-paths: {dod_section!r}"
    )
    assert "log-newly-reachable" not in dod_section, (
        f"Definition of Done section must never reference log-newly-reachable: {dod_section!r}"
    )
