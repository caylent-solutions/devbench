"""Regression test for docs/manifest-amendments.md item 7 <-> manifest-amender.md sync.

Background (E3-F2-S1-T8): item 7 of the ``## Flow`` numbered list in
``docs/manifest-amendments.md`` used to hand-copy the git-cleanup recipe
(``git restore --staged``, ``checkout --``, ``clean -f --``) out of
``plugin/devbench-orchestrate/agents/manifest-amender.md``, and separately
claimed the agent's verify step was "a final ``test -f
.devbench/rejected-requests/...`` assertion" -- both of which are stale
descriptions of the actual recipe (which performs a two-file existence
check, archive and feedback, not a single ``test -f``). That duplication
is itself the defect class: when the recipe in ``manifest-amender.md``
changes, the copy in ``docs/manifest-amendments.md`` goes stale, exactly
the failure mode already fixed once for ``SKILL.md`` in E3-F2-S1-T7 (see
``tests/test_integration/test_orchestrate_skill_reject_recipe_sync.py``,
which this test mirrors).

The durable fix removes the duplication rather than re-arming it: item 7
states that ``manifest-amender.md``'s ``**Step B.reject**`` block is the
single source of truth for the recipe's commands, without naming any git
subcommand itself, and no longer claims the verify step is a single
``test -f`` assertion. This test pins that structural invariant two ways:

1. It regex-extracts item 7 from the shipped ``docs/manifest-amendments.md``
   and asserts it names no git command and no longer describes the verify
   step as a single ``test -f`` assertion -- so a future edit cannot
   silently reintroduce either stale description.
2. It opens ``manifest-amender.md`` and asserts the referenced
   ``**Step B.reject**`` section actually exists there with a fenced bash
   block containing at least one ``git`` invocation, so the pointer cannot
   rot into a dangling reference.

Background (E3-F2-S1-T9): the ``MANIFEST_AMENDER_PATH`` constant,
``STEP_B_REJECT_FENCE_PATTERN`` regex, ``manifest_amender_text`` fixture and
the two ``**Step B.reject**`` pointer-integrity assertions used to be
hand-copied here and in the sibling
``test_orchestrate_skill_reject_recipe_sync.py`` (E3-F2-S1-T7) module. Both
are now defined exactly once in ``tests/test_integration/conftest.py`` and
consumed from there; this module keeps only the
docs/manifest-amendments.md-specific extraction pattern and assertions.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from test_integration.conftest import (
    assert_manifest_amender_file_exists,
    assert_step_b_reject_section_exists,
    assert_step_b_reject_section_has_git_invocation,
)

DOC_PATH = Path(__file__).resolve().parent.parent.parent / "docs" / "manifest-amendments.md"

# Narrow extraction of item 7 of the "## Flow" numbered list: it starts at
# the literal "7. On `reject`:" marker and runs to the start of item 8
# ("8. On post-Layer-3 success..."), which lives on its own line
# immediately after it in the shipped file.
ITEM_7_PATTERN = re.compile(
    r"7\. On `reject`:.*?(?=\n8\. On post-Layer-3 success)",
    re.DOTALL,
)

# Fragments the hand-copied enumeration used to name. If any of these
# reappear in item 7, the duplication (and its drift risk) is back.
FORBIDDEN_GIT_COMMAND_FRAGMENTS = (
    "checkout --",
    "clean -f",
    "git checkout",
    "git clean",
)


@pytest.fixture(scope="module")
def doc_text() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def item_7(doc_text: str) -> str:
    match = ITEM_7_PATTERN.search(doc_text)
    assert match is not None, (
        "Could not locate item 7 of the '## Flow' numbered list in "
        f"docs/manifest-amendments.md via the extraction pattern "
        f"{ITEM_7_PATTERN.pattern!r}. Either the item was removed/renamed, "
        "or the pattern needs updating to track a structural change in the "
        "surrounding numbered list."
    )
    return match.group(0)


@pytest.mark.integration
class TestManifestAmendmentsDocRejectRecipeSync:
    """Pin the no-duplication fix for docs/manifest-amendments.md item 7."""

    def test_doc_file_exists(self) -> None:
        assert DOC_PATH.is_file(), f"docs/manifest-amendments.md missing at {DOC_PATH}"

    def test_manifest_amender_file_exists(self) -> None:
        assert_manifest_amender_file_exists()

    def test_item_7_is_extracted(self, item_7: str) -> None:
        assert item_7.strip(), "Extracted item 7 is unexpectedly empty."

    @pytest.mark.parametrize("forbidden_fragment", FORBIDDEN_GIT_COMMAND_FRAGMENTS)
    def test_item_7_names_no_git_command(self, item_7: str, forbidden_fragment: str) -> None:
        assert forbidden_fragment not in item_7, (
            f"docs/manifest-amendments.md item 7 names the git command "
            f"fragment {forbidden_fragment!r}. Item 7 must describe the "
            "recipe's existence and location without hand-copying its "
            "commands, so it cannot drift when manifest-amender.md's "
            "recipe changes."
        )

    def test_item_7_has_no_bare_restore_staged_outside_pointer_wording(self, item_7: str) -> None:
        # The pointer sentence is allowed to reference the concept of the
        # "revert"/"cleanup" recipe by name, but must not restate the
        # literal `git restore --staged` invocation the recipe uses today.
        assert "restore --staged" not in item_7, (
            "docs/manifest-amendments.md item 7 names the literal "
            "'restore --staged' invocation. Item 7 must point at "
            "manifest-amender.md's Step B.reject block as the single "
            "source of truth instead of hand-copying the command."
        )

    def test_item_7_does_not_claim_single_test_dash_f_assertion(self, item_7: str) -> None:
        assert "test -f .devbench/rejected-requests" not in item_7, (
            "docs/manifest-amendments.md item 7 still describes the "
            "verify step as 'a final test -f .devbench/rejected-requests/... "
            "assertion'. The recipe actually performs two existence checks "
            "(archive and feedback), not a single test -f assertion; this "
            "stale claim must be removed or corrected."
        )

    def test_item_7_points_at_source_of_truth_file(self, item_7: str) -> None:
        assert "plugin/devbench-orchestrate/agents/manifest-amender.md" in item_7, (
            "docs/manifest-amendments.md item 7 no longer names "
            "manifest-amender.md as the location of the git-cleanup "
            "recipe; a reader has no way to find the authoritative recipe."
        )

    def test_item_7_states_source_of_truth(self, item_7: str) -> None:
        assert "source of truth" in item_7, (
            "docs/manifest-amendments.md item 7 must explicitly state that "
            "manifest-amender.md is the single source of truth for the "
            "git-cleanup recipe, not merely mention the file in passing."
        )

    def test_manifest_amender_step_b_reject_section_exists(self, manifest_amender_text: str) -> None:
        assert_step_b_reject_section_exists(manifest_amender_text)

    def test_manifest_amender_step_b_reject_section_has_git_invocation(self, manifest_amender_text: str) -> None:
        assert_step_b_reject_section_has_git_invocation(manifest_amender_text)
