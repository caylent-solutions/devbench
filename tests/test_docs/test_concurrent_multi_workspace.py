"""Structural pins for docs/concurrent-multi-workspace.md.

Verifies that docs/concurrent-multi-workspace.md (AC-190-12) documents:
- The two-clone pattern: two workspace clones each running a devbench instance
  with disjoint --include scopes so they claim disjoint work-unit sets.
- Prerequisites: a shared git remote and two separate workspace root directories.
- Step-by-step setup instructions for both clones.
- How to verify disjointness before launching instances.
- Scope examples showing mutually exclusive filters (e.g., "E1-E3" and "E4-E6").
- The segue to true intra-workspace concurrency via named sessions.
- Cross-references to cli-reference.md (scope selectors) and zero-to-ready.md.

Spec source: spec/devbench-self-improve.md section 4.2 / AC-190-12. Issue: #190.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
CONCURRENT_MULTI_WORKSPACE_DOC = REPO_ROOT / "docs" / "concurrent-multi-workspace.md"


def _read_doc() -> str:
    return CONCURRENT_MULTI_WORKSPACE_DOC.read_text(encoding="utf-8")


def _extract_section(text: str, heading: str) -> str:
    """Return content of the section starting at ``heading`` up to the next same-level heading."""
    idx = text.find(heading)
    if idx == -1:
        return ""
    section_text = text[idx:]
    level = len(heading.split(" ", maxsplit=1)[0])
    marker = "\n" + "#" * level + " "
    next_idx = section_text.find(marker, 1)
    if next_idx != -1:
        return section_text[:next_idx]
    return section_text


@pytest.mark.unit
class TestConcurrentMultiWorkspaceDocExists:
    """AC-190-12: The doc file must exist and have a valid top-level heading."""

    def test_doc_file_exists(self) -> None:
        """docs/concurrent-multi-workspace.md must exist."""
        assert CONCURRENT_MULTI_WORKSPACE_DOC.exists(), (
            "docs/concurrent-multi-workspace.md must exist to document the two-clone "
            "pattern for concurrent devbench runs with disjoint --include scopes "
            "(AC-190-12 / spec section 4.2)."
        )

    def test_doc_has_top_level_heading(self) -> None:
        """The doc must have a top-level # heading."""
        text = _read_doc()
        lines = text.splitlines()
        has_h1 = any(line.startswith("# ") for line in lines)
        assert has_h1, "docs/concurrent-multi-workspace.md must have a top-level # heading (AC-190-12)."

    def test_doc_is_not_empty(self) -> None:
        """The doc must have substantial content (more than a stub)."""
        text = _read_doc()
        assert len(text) > 500, (
            "docs/concurrent-multi-workspace.md must have substantial content describing "
            "the two-clone pattern, not just a stub (AC-190-12)."
        )


@pytest.mark.unit
class TestConcurrentMultiWorkspaceTwoClonePattern:
    """AC-190-12: The doc must describe the two-clone / two-workspace pattern."""

    def test_two_clone_pattern_described(self) -> None:
        """The doc must describe using two separate workspace clones."""
        text = _read_doc()
        lower = text.lower()
        has_two_clone = "two" in lower and ("clone" in lower or "workspace" in lower)
        assert has_two_clone, (
            "docs/concurrent-multi-workspace.md must describe the two-clone pattern "
            "-- running two devbench instances against two separate workspace directories "
            "(AC-190-12 / spec section 4.2)."
        )

    def test_disjoint_scopes_explained(self) -> None:
        """The doc must explain that the two instances use disjoint --include filters."""
        text = _read_doc()
        lower = text.lower()
        has_disjoint = "disjoint" in lower or "non-overlapping" in lower or "separate scope" in lower
        assert has_disjoint, (
            "docs/concurrent-multi-workspace.md must explain that the two devbench "
            "instances use disjoint (non-overlapping) --include filters so they claim "
            "disjoint WU sets (AC-190-12)."
        )

    def test_include_flag_present(self) -> None:
        """The doc must show the --include flag."""
        text = _read_doc()
        assert "--include" in text, (
            "docs/concurrent-multi-workspace.md must show the --include flag to "
            "demonstrate how each instance is restricted to its own scope (AC-190-12)."
        )

    def test_scope_examples_are_mutually_exclusive(self) -> None:
        """The doc must include worked examples with mutually exclusive scope tokens."""
        text = _read_doc()
        has_partitioned_examples = (
            ("E1-E3" in text or "E1-E5" in text or "E1-E4" in text)
            and ("E4-E6" in text or "E6-E10" in text or "E5-E10" in text or "E5-E8" in text)
        ) or ("E1-E3" in text and "E4" in text)
        assert has_partitioned_examples, (
            "docs/concurrent-multi-workspace.md must include worked scope examples "
            "showing mutually exclusive filters for the two instances, e.g. "
            "'E1-E3' and 'E4-E6' (AC-190-12 / spec section 4.2)."
        )


@pytest.mark.unit
class TestConcurrentMultiWorkspacePrerequisites:
    """The doc must document prerequisites for the two-clone setup."""

    def test_shared_remote_mentioned(self) -> None:
        """The doc must mention that both clones share the same git remote."""
        text = _read_doc()
        lower = text.lower()
        has_remote = "remote" in lower or "origin" in lower or "shared" in lower
        assert has_remote, (
            "docs/concurrent-multi-workspace.md must mention that both workspace clones "
            "share the same git remote / origin so changes from both instances merge "
            "correctly (AC-190-12)."
        )

    def test_two_workspace_roots_mentioned(self) -> None:
        """The doc must instruct the operator to create two separate workspace root directories."""
        text = _read_doc()
        lower = text.lower()
        has_two_roots = "workspace" in lower and (
            "two" in lower or "second" in lower or "separate" in lower or "clone" in lower
        )
        assert has_two_roots, (
            "docs/concurrent-multi-workspace.md must instruct operators to set up "
            "two separate workspace root directories (one per devbench instance) "
            "(AC-190-12 / spec section 4.2)."
        )

    def test_workspace_root_env_var_mentioned(self) -> None:
        """The doc must reference the DEVBENCH_WORKSPACE_ROOT environment variable."""
        text = _read_doc()
        assert "DEVBENCH_WORKSPACE_ROOT" in text, (
            "docs/concurrent-multi-workspace.md must reference the DEVBENCH_WORKSPACE_ROOT "
            "environment variable, which each instance uses to locate its own workspace "
            "(AC-190-12 / spec section 4.2)."
        )


@pytest.mark.unit
class TestConcurrentMultiWorkspaceSetupSteps:
    """The doc must contain step-by-step setup instructions."""

    def test_setup_steps_or_numbered_list_present(self) -> None:
        """The doc must have numbered steps or a structured How-to section."""
        text = _read_doc()
        has_steps = "1." in text or "Step 1" in text or "step 1" in text or "## Setup" in text or "### Setup" in text
        assert has_steps, (
            "docs/concurrent-multi-workspace.md must contain numbered steps or a "
            "structured setup section showing how to create the two-clone layout "
            "(AC-190-12)."
        )

    def test_git_clone_or_workspace_creation_mentioned(self) -> None:
        """The doc must mention cloning or creating the second workspace directory."""
        text = _read_doc()
        lower = text.lower()
        has_clone_step = "git clone" in lower or "mkdir" in lower or ("create" in lower and "workspace" in lower)
        assert has_clone_step, (
            "docs/concurrent-multi-workspace.md must show how to set up the second "
            "workspace root (e.g. git clone, mkdir, or equivalent) (AC-190-12)."
        )

    def test_devbench_start_command_shown(self) -> None:
        """The doc must show a devbench start (or equivalent) command with --include."""
        text = _read_doc()
        has_start = "devbench start" in text or "uv run devbench start" in text or "make start" in text
        assert has_start, (
            "docs/concurrent-multi-workspace.md must show a 'devbench start --include' "
            "command (or equivalent) for launching each instance with its own scope "
            "(AC-190-12)."
        )


@pytest.mark.unit
class TestConcurrentMultiWorkspaceDisiointVerification:
    """The doc must advise operators on how to verify disjointness before starting."""

    def test_disjointness_verification_mentioned(self) -> None:
        """The doc must advise operators to verify that scopes are disjoint before starting."""
        text = _read_doc()
        lower = text.lower()
        has_verify = (
            "verify" in lower or "check" in lower or "ensure" in lower or "confirm" in lower or "disjoint" in lower
        )
        assert has_verify, (
            "docs/concurrent-multi-workspace.md must advise operators to verify that "
            "the two --include scopes are disjoint before launching instances "
            "(AC-190-12)."
        )

    def test_overlap_warning_mentioned(self) -> None:
        """The doc must warn about scope overlap or claim race risk."""
        text = _read_doc()
        lower = text.lower()
        has_overlap_warning = "overlap" in lower or "race" in lower or "conflict" in lower or "collision" in lower
        assert has_overlap_warning, (
            "docs/concurrent-multi-workspace.md must warn about scope overlap or "
            "claim-race risk if the two --include filters are not disjoint (AC-190-12)."
        )


@pytest.mark.unit
class TestConcurrentMultiWorkspaceSessionsSegue:
    """The doc must segue to true intra-workspace concurrency via named sessions."""

    def test_sessions_segue_present(self) -> None:
        """The doc must mention named sessions as the next step beyond two-clone."""
        text = _read_doc()
        lower = text.lower()
        has_sessions_mention = "session" in lower or "named session" in lower or "--name" in text
        assert has_sessions_mention, (
            "docs/concurrent-multi-workspace.md must mention named sessions (#192) "
            "as the next evolution beyond the two-clone workaround "
            "(spec section 4.2 / E4 segue)."
        )

    def test_intra_workspace_concurrency_mentioned(self) -> None:
        """The doc must contrast two-clone (external) with intra-workspace concurrency."""
        text = _read_doc()
        lower = text.lower()
        has_intra = (
            "intra-workspace" in lower
            or "same workspace" in lower
            or "single workspace" in lower
            or ("within" in lower and "workspace" in lower)
        )
        assert has_intra, (
            "docs/concurrent-multi-workspace.md must contrast the two-clone pattern "
            "(two separate workspace roots) with true intra-workspace concurrency "
            "via named sessions (spec section 4.2)."
        )


@pytest.mark.unit
class TestConcurrentMultiWorkspaceCrossReferences:
    """The doc must cross-reference related documentation."""

    def test_cli_reference_cross_reference_present(self) -> None:
        """The doc must cross-reference docs/cli-reference.md for scope selector syntax."""
        text = _read_doc()
        assert "cli-reference" in text.lower() or "cli-reference.md" in text, (
            "docs/concurrent-multi-workspace.md must cross-reference docs/cli-reference.md "
            "for the full scope-selector syntax (AC-190-12)."
        )

    def test_zero_to_ready_cross_reference_present(self) -> None:
        """The doc must cross-reference docs/zero-to-ready.md."""
        text = _read_doc()
        assert "zero-to-ready" in text.lower(), (
            "docs/concurrent-multi-workspace.md must cross-reference docs/zero-to-ready.md "
            "for the baseline single-instance setup (AC-190-12)."
        )

    def test_table_of_contents_present(self) -> None:
        """The doc must include a Table of contents."""
        text = _read_doc()
        lower = text.lower()
        has_toc = "table of contents" in lower or "## contents" in lower
        assert has_toc, (
            "docs/concurrent-multi-workspace.md must include a Table of contents "
            "section for navigation (documentation standards)."
        )


@pytest.mark.unit
class TestConcurrentMultiWorkspaceNoEmDash:
    """The doc must not contain em-dash characters (U+2014)."""

    def test_no_em_dash(self) -> None:
        """The doc must use -- (double hyphen) instead of the em-dash character."""
        text = _read_doc()
        assert "\u2014" not in text, (
            "docs/concurrent-multi-workspace.md must not contain em-dash (U+2014) "
            "characters. Use -- (double hyphen) instead. "
            "(devbench validate-backlog rule 10 / spec critical rule 8)."
        )
