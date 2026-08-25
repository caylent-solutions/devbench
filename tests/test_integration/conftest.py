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
