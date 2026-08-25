"""Regression test for the SKILL.md step-4c On-reject bullet <-> manifest-amender.md sync.

Background (E3-F2-S1-T7): SKILL.md's step-4c ``On reject`` bullet used to
hand-copy the git-cleanup recipe (``restore --staged``, ``checkout --``,
``clean -f``) out of ``plugin/devbench-orchestrate/agents/manifest-amender.md``.
That duplication is itself the defect class: when the recipe in
``manifest-amender.md`` changed, the copy in SKILL.md went stale, and a
prior attempt to re-sync the two by re-copying a *new* command list into
SKILL.md was rejected by all four review judges for describing an unlanded
rewrite (see this task's Comments history).

The durable fix removes the duplication rather than re-arming it: SKILL.md
states that the cleanup runs before the CLI invocation and points at
``manifest-amender.md`` as the single source of truth for the recipe's
commands, without naming any git subcommand itself. This test pins that
structural invariant two ways:

1. It regex-extracts the step-4c ``On reject`` bullet from the shipped
   SKILL.md and asserts it names no git command at all -- so a future edit
   cannot silently reintroduce the hand-copied enumeration.
2. It opens ``manifest-amender.md`` and asserts the referenced
   ``Step B.reject`` section actually exists there, so a rename or deletion
   of the source-of-truth section fails the build instead of leaving
   SKILL.md pointing at nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SKILL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "plugin"
    / "devbench-orchestrate"
    / "skills"
    / "orchestrate"
    / "SKILL.md"
)

MANIFEST_AMENDER_PATH = (
    Path(__file__).resolve().parent.parent.parent / "plugin" / "devbench-orchestrate" / "agents" / "manifest-amender.md"
)

# Extraction of the fenced bash block that immediately follows the
# "**Step B.reject**" header. Deliberately does not pin any specific git
# subcommand (restore/checkout/clean today, something else once T6 lands
# its rewrite) -- only that the section's recipe still contains a `git`
# invocation, so the pointer SKILL.md relies on cannot silently degrade to
# an empty or command-free body.
STEP_B_REJECT_FENCE_PATTERN = re.compile(
    r"\*\*Step B\.reject\*\*.*?```bash\n(?P<code>.*?)```",
    re.DOTALL,
)

# Narrow extraction of the step-4c "On `reject`" bullet: it starts at the
# literal marker and runs to the start of the next top-level bullet line
# (the "Either way the agent finishes..." sentence lives on its own bullet
# immediately after it in the shipped file).
REJECT_BULLET_PATTERN = re.compile(
    r"- On `reject`:.*?(?=\n\s*- Either way the agent finishes)",
    re.DOTALL,
)

# Git subcommand tokens the hand-copied enumeration used to name. If any of
# these reappear in the bullet, the duplication (and its drift risk) is
# back.
FORBIDDEN_GIT_COMMAND_FRAGMENTS = (
    "restore --staged",
    "checkout --",
    "clean -f",
    "git restore",
    "git checkout",
    "git clean",
)


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def manifest_amender_text() -> str:
    return MANIFEST_AMENDER_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def reject_bullet(skill_text: str) -> str:
    match = REJECT_BULLET_PATTERN.search(skill_text)
    assert match is not None, (
        "Could not locate the step-4c 'On `reject`' bullet in SKILL.md via "
        f"the extraction pattern {REJECT_BULLET_PATTERN.pattern!r}. Either "
        "the bullet was removed/renamed, or the pattern needs updating to "
        "track a structural change in the surrounding step-4c list."
    )
    return match.group(0)


@pytest.mark.integration
class TestOrchestrateSkillRejectRecipeSync:
    """Pin the no-duplication fix for the step-4c On-reject bullet."""

    def test_skill_file_exists(self) -> None:
        assert SKILL_PATH.is_file(), f"orchestrate SKILL.md missing at {SKILL_PATH}"

    def test_manifest_amender_file_exists(self) -> None:
        assert MANIFEST_AMENDER_PATH.is_file(), f"manifest-amender.md missing at {MANIFEST_AMENDER_PATH}"

    def test_reject_bullet_is_extracted(self, reject_bullet: str) -> None:
        assert reject_bullet.strip(), "Extracted On-reject bullet is unexpectedly empty."

    @pytest.mark.parametrize("forbidden_fragment", FORBIDDEN_GIT_COMMAND_FRAGMENTS)
    def test_reject_bullet_names_no_git_command(self, reject_bullet: str, forbidden_fragment: str) -> None:
        assert forbidden_fragment not in reject_bullet, (
            f"SKILL.md step-4c On-reject bullet names the git command "
            f"fragment {forbidden_fragment!r}. The bullet must describe "
            "the recipe's existence and ordering without hand-copying its "
            "commands, so it cannot drift when manifest-amender.md's "
            "recipe changes."
        )

    def test_reject_bullet_points_at_source_of_truth_file(self, reject_bullet: str) -> None:
        assert "plugin/devbench-orchestrate/agents/manifest-amender.md" in reject_bullet, (
            "SKILL.md step-4c On-reject bullet no longer names "
            "manifest-amender.md as the location of the git-cleanup "
            "recipe; a reader has no way to find the authoritative recipe."
        )

    def test_reject_bullet_states_source_of_truth(self, reject_bullet: str) -> None:
        assert "source of truth" in reject_bullet, (
            "SKILL.md step-4c On-reject bullet must explicitly state that "
            "manifest-amender.md is the single source of truth for the "
            "git-cleanup recipe, not merely mention the file in passing."
        )

    def test_reject_bullet_states_ordering_before_cli_invocation(self, reject_bullet: str) -> None:
        assert "BEFORE the CLI invocation" in reject_bullet, (
            "SKILL.md step-4c On-reject bullet must state that the revert "
            "runs BEFORE the CLI invocation (uv run devbench "
            "reject-amendment), so the ordering guarantee is documented "
            "even though the commands themselves are not."
        )

    def test_manifest_amender_step_b_reject_section_exists(self, manifest_amender_text: str) -> None:
        assert "**Step B.reject**" in manifest_amender_text, (
            "manifest-amender.md no longer contains a '**Step B.reject**' "
            "section. SKILL.md's step-4c bullet points at this file as the "
            "single source of truth for the git-cleanup recipe; if the "
            "section is renamed or removed, that pointer becomes stale and "
            "must be updated in the same change."
        )

    def test_manifest_amender_step_b_reject_section_has_git_invocation(self, manifest_amender_text: str) -> None:
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
            "contains no 'git' invocation. SKILL.md's step-4c bullet points "
            "at this section as the single source of truth for the "
            "git-cleanup recipe; a section that survives with an empty or "
            "command-free body would leave that pointer misleading even "
            "though the '**Step B.reject**' heading itself still exists."
        )
