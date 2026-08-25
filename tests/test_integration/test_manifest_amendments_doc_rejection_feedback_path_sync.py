"""Regression test for docs/manifest-amendments.md's Rejection feedback persistence
(issue #154) section <-> persist_rejection_feedback write path sync.

Background (E3-F2-S1-T10): the "Rejection feedback persistence (issue #154)"
section used to describe the write path as
``<workspace>/.devbench/amender-rejections/<task-id>-<n>.json``. That was the
issue #154 v1 (deprecated) path. Issue #156 unified rejection-feedback
persistence with every other review-judge rejection: ``persist_rejection_feedback``
in ``src/devbench/backlog/amendment.py`` now writes to
``<workspace>/.devbench/review-failures/<task-id>-manifest_amender-<n>.json``,
and the legacy ``amender-rejections`` directory is preserved read-only (via
``read_review_failure_files``) for archived runs rather than being an active
write target. The doc's prose had gone stale relative to the shipped code.

This test regex-extracts the section from the shipped
``docs/manifest-amendments.md`` and pins two invariants:

1. The section names the current write path (``review-failures``) and the
   ``manifest_amender`` judge segment used in the filename.
2. The section no longer claims ``amender-rejections`` as the write target.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOC_PATH = Path(__file__).resolve().parent.parent.parent / "docs" / "manifest-amendments.md"

AMENDMENT_MODULE_PATH = Path(__file__).resolve().parent.parent.parent / "src" / "devbench" / "backlog" / "amendment.py"

# Narrow extraction of the "### Rejection feedback persistence (issue #154)"
# section: it starts at the section header and runs to the next "##"-level
# header ("## What the amendment workflow does NOT do"), which follows it
# directly in the shipped file.
REJECTION_FEEDBACK_SECTION_PATTERN = re.compile(
    r"### Rejection feedback persistence \(issue #154\)\n(?P<body>.*?)(?=\n## )",
    re.DOTALL,
)


@pytest.fixture(scope="module")
def doc_text() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def amendment_module_text() -> str:
    return AMENDMENT_MODULE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def rejection_feedback_section(doc_text: str) -> str:
    match = REJECTION_FEEDBACK_SECTION_PATTERN.search(doc_text)
    assert match is not None, (
        "Could not locate the 'Rejection feedback persistence (issue #154)' "
        f"section in docs/manifest-amendments.md via the extraction pattern "
        f"{REJECTION_FEEDBACK_SECTION_PATTERN.pattern!r}. Either the section "
        "was removed/renamed, or the pattern needs updating to track a "
        "structural change in the surrounding document."
    )
    return match.group("body")


@pytest.mark.integration
class TestManifestAmendmentsDocRejectionFeedbackPathSync:
    """Pin the fix for the stale amender-rejections write-path claim."""

    def test_doc_file_exists(self) -> None:
        assert DOC_PATH.is_file(), f"docs/manifest-amendments.md missing at {DOC_PATH}"

    def test_amendment_module_exists(self) -> None:
        assert AMENDMENT_MODULE_PATH.is_file(), f"src/devbench/backlog/amendment.py missing at {AMENDMENT_MODULE_PATH}"

    def test_section_is_extracted(self, rejection_feedback_section: str) -> None:
        assert rejection_feedback_section.strip(), (
            "Extracted Rejection feedback persistence section is unexpectedly empty."
        )

    def test_section_names_review_failures_write_path(self, rejection_feedback_section: str) -> None:
        assert ".devbench/review-failures/" in rejection_feedback_section, (
            "docs/manifest-amendments.md's Rejection feedback persistence section "
            "does not name the '.devbench/review-failures/' write path. This is "
            "the path persist_rejection_feedback() in "
            "src/devbench/backlog/amendment.py actually writes to; the doc must "
            "name it so a reader can find the real write target."
        )

    def test_section_names_manifest_amender_judge_segment(self, rejection_feedback_section: str) -> None:
        assert "manifest_amender" in rejection_feedback_section, (
            "docs/manifest-amendments.md's Rejection feedback persistence section "
            "does not name the 'manifest_amender' judge segment used in the "
            "review-failures filename (<task-id>-manifest_amender-<n>.json). "
            "Without it, a reader cannot construct or recognise the actual "
            "filename persist_rejection_feedback() writes."
        )

    def test_section_does_not_claim_amender_rejections_as_write_target(self, rejection_feedback_section: str) -> None:
        # The stale claim was the single contiguous phrase "writes a structured
        # feedback JSON to `<workspace>/.devbench/amender-rejections/". The
        # corrected doc may still mention amender-rejections elsewhere (as
        # legacy/read-only context), so the assertion targets that specific
        # write-target phrasing rather than the bare substring "amender-rejections".
        stale_write_target_phrase = "writes a structured feedback JSON to `<workspace>/.devbench/amender-rejections/"
        assert stale_write_target_phrase not in rejection_feedback_section, (
            "docs/manifest-amendments.md's Rejection feedback persistence section "
            f"still contains the stale write-target phrase {stale_write_target_phrase!r}. "
            "persist_rejection_feedback() writes to review-failures/, not "
            "amender-rejections/; amender-rejections is legacy read-only."
        )

    def test_section_notes_amender_rejections_is_legacy_read_only(self, rejection_feedback_section: str) -> None:
        assert "read-only" in rejection_feedback_section or "read only" in rejection_feedback_section, (
            "docs/manifest-amendments.md's Rejection feedback persistence section "
            "does not note that the legacy amender-rejections directory is "
            "preserved read-only for archived runs, leaving a reader unable to "
            "tell it is not an active write target."
        )

    def test_amendment_module_defines_persist_rejection_feedback(self, amendment_module_text: str) -> None:
        assert "def persist_rejection_feedback(" in amendment_module_text, (
            "src/devbench/backlog/amendment.py no longer defines "
            "persist_rejection_feedback(). docs/manifest-amendments.md's "
            "Rejection feedback persistence section names this function as the "
            "source of the write path; if it is renamed or removed, the "
            "pointer becomes stale and must be updated in the same change."
        )

    def test_amendment_module_writes_to_review_failures_dir(self, amendment_module_text: str) -> None:
        assert 'REVIEW_FAILURES_DIR_NAME = ".devbench/review-failures"' in amendment_module_text, (
            "src/devbench/backlog/amendment.py no longer defines "
            'REVIEW_FAILURES_DIR_NAME = ".devbench/review-failures". '
            "docs/manifest-amendments.md's Rejection feedback persistence "
            "section names this path as the actual write target; if the "
            "constant's value changes, the doc's claim goes stale."
        )
