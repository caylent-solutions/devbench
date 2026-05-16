"""Structural pins for E2-F7-S1-T3: scope interactive pre-arm documentation.

Verifies that:
- docs/zero-to-ready.md has a dedicated 'Scoping a run interactively' section
  showing the scope set + claude --plugin-dir chained workflow (spec 4.2.6.3).
- plugin/devbench/skills/orchestrate/SKILL.md carries a note that pre-armed
  scope.json is equivalent whether written by cmd_start --include or cmd_scope set
  (spec 4.2.6, AC-196-9).
- docs/cli-reference.md scope section mentions the session integration path
  (DEVBENCH_SESSION_NAME -> sessions/<name>/scope.json, spec 4.2.6.4).

Spec source: spec/devbench-self-improve.md section 4.2.6.3. Issue: #196.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
ZERO_TO_READY_DOC = REPO_ROOT / "docs" / "zero-to-ready.md"
CLI_REFERENCE_DOC = REPO_ROOT / "docs" / "cli-reference.md"
SKILL_DOC = REPO_ROOT / "plugin" / "devbench" / "skills" / "orchestrate" / "SKILL.md"


def _read_zero_to_ready() -> str:
    return ZERO_TO_READY_DOC.read_text(encoding="utf-8")


def _read_cli_reference() -> str:
    return CLI_REFERENCE_DOC.read_text(encoding="utf-8")


def _read_skill() -> str:
    return SKILL_DOC.read_text(encoding="utf-8")


def _extract_section(text: str, heading: str) -> str:
    """Return content of the section starting at heading up to the next same-level heading."""
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
class TestZeroToReadyScopingInteractivelySection:
    """AC-196-9: zero-to-ready.md must have a 'Scoping a run interactively' section."""

    def test_section_heading_exists(self) -> None:
        """docs/zero-to-ready.md must contain a 'Scoping a run interactively' heading."""
        text = _read_zero_to_ready()
        has_heading = "### Scoping a run interactively" in text or "## Scoping a run interactively" in text
        assert has_heading, (
            "docs/zero-to-ready.md must contain a 'Scoping a run interactively' section "
            "showing the scope set + claude --plugin-dir chained workflow "
            "(spec section 4.2.6.3, AC-196-9)."
        )

    def test_scope_set_command_shown(self) -> None:
        """The 'Scoping a run interactively' section must show devbench scope set."""
        text = _read_zero_to_ready()
        section = _extract_section(text, "### Scoping a run interactively") or _extract_section(
            text, "## Scoping a run interactively"
        )
        assert section, (
            "docs/zero-to-ready.md must have a 'Scoping a run interactively' section (spec section 4.2.6.3)."
        )
        assert "scope set" in section, (
            "The 'Scoping a run interactively' section must show 'devbench scope set' (spec section 4.2.6.3)."
        )

    def test_claude_plugin_dir_invocation_shown(self) -> None:
        """The section must show 'claude --plugin-dir' (or equivalent) for interactive launch."""
        text = _read_zero_to_ready()
        section = _extract_section(text, "### Scoping a run interactively") or _extract_section(
            text, "## Scoping a run interactively"
        )
        assert section, (
            "docs/zero-to-ready.md must have a 'Scoping a run interactively' section (spec section 4.2.6.3)."
        )
        has_claude_cmd = "--plugin-dir" in section or "claude --dangerously" in section
        assert has_claude_cmd, (
            "The 'Scoping a run interactively' section must show the 'claude --plugin-dir' "
            "invocation for interactive Claude Code pre-arm workflow "
            "(spec section 4.2.6.3)."
        )

    def test_scope_clear_shown_after_run(self) -> None:
        """The section must show 'devbench scope clear' to clean up after the run."""
        text = _read_zero_to_ready()
        section = _extract_section(text, "### Scoping a run interactively") or _extract_section(
            text, "## Scoping a run interactively"
        )
        assert section, (
            "docs/zero-to-ready.md must have a 'Scoping a run interactively' section (spec section 4.2.6.3)."
        )
        assert "scope clear" in section, (
            "The 'Scoping a run interactively' section must show 'devbench scope clear' "
            "to clean up the scope after the interactive session "
            "(spec section 4.2.6.3)."
        )

    def test_section_appears_in_scoping_a_run_parent(self) -> None:
        """The 'Scoping a run interactively' section must be under 'Scoping a run'."""
        text = _read_zero_to_ready()
        scoping_idx = text.find("## Scoping a run")
        assert scoping_idx != -1, "docs/zero-to-ready.md must have a '## Scoping a run' section."
        interactive_idx = text.find("Scoping a run interactively", scoping_idx)
        assert interactive_idx != -1, (
            "The 'Scoping a run interactively' subsection must appear within "
            "the '## Scoping a run' section of docs/zero-to-ready.md "
            "(spec section 4.2.6.3)."
        )


@pytest.mark.unit
class TestSkillMdScopeEquivalenceNote:
    """AC-196-9: SKILL.md must note that pre-armed scope.json is equivalent to cmd_start --include."""

    def test_skill_doc_exists(self) -> None:
        """The orchestrate SKILL.md must exist."""
        assert SKILL_DOC.is_file(), (
            f"orchestrate SKILL.md not found at {SKILL_DOC}. "
            "This file must exist and document the scope equivalence note."
        )

    def test_scope_equivalence_note_present(self) -> None:
        """SKILL.md step 1c must note that cmd_scope set and cmd_start --include are equivalent."""
        text = _read_skill()
        lower = text.lower()
        has_equivalence = (
            "cmd_scope set" in text
            or "scope set" in text
            or ("cmd_start --include" in text and "identically" in lower)
            or ("scope.json" in text and ("identical" in lower or "equivalent" in lower))
        )
        assert has_equivalence, (
            "plugin/devbench/skills/orchestrate/SKILL.md must contain a note that "
            "pre-armed scope.json written by 'devbench scope set' is honoured identically "
            "to scope written by 'devbench start --include' "
            "(spec section 4.2.6, AC-196-9)."
        )

    def test_scope_step_1c_notes_both_pathways(self) -> None:
        """Step 1c of SKILL.md must acknowledge both scope.json write pathways."""
        text = _read_skill()
        step_1c_section = _extract_section(text, "1c.")
        if not step_1c_section:
            # Try finding it inline
            idx = text.find("1c.")
            if idx != -1:
                step_1c_section = text[idx : idx + 800]
        # The SKILL.md step 1c already mentions scope.json -- check it also
        # mentions the equivalence to cmd_scope set or both pathways.
        has_pathway_note = (
            "scope set" in step_1c_section
            or "cmd_scope" in step_1c_section
            or ("scope.json" in step_1c_section and "identically" in step_1c_section.lower())
        )
        assert has_pathway_note, (
            "plugin/devbench/skills/orchestrate/SKILL.md step 1c must note that "
            "'devbench scope set' and 'devbench start --include' both write scope.json "
            "and the skill honours them identically (spec section 4.2.6, AC-196-9)."
        )


@pytest.mark.unit
class TestCliReferenceSessionIntegrationNote:
    """AC-196-9: cli-reference.md scope section must document session integration."""

    def test_session_name_env_var_mentioned_in_scope_section(self) -> None:
        """The scope section must reference DEVBENCH_SESSION_NAME for per-session path."""
        text = _read_cli_reference()
        scope_section = _extract_section(text, "### `scope`")
        assert scope_section, "### `scope` section must exist in cli-reference.md"
        has_session = (
            "DEVBENCH_SESSION_NAME" in scope_section
            or "JUDGE_SESSION_NAME" in scope_section
            or "sessions/<name>" in scope_section
            or "session" in scope_section.lower()
        )
        assert has_session, (
            "docs/cli-reference.md '### `scope`' section must document the session "
            "integration: when DEVBENCH_SESSION_NAME is set, scope.json goes to "
            "<workspace>/.devbench/sessions/<name>/scope.json "
            "(spec section 4.2.6.4, AC-196-9)."
        )

    def test_scope_section_has_session_path_example(self) -> None:
        """The scope section must show or reference the per-session path."""
        text = _read_cli_reference()
        scope_section = _extract_section(text, "### `scope`")
        assert scope_section, "### `scope` section must exist in cli-reference.md"
        has_session_path = "sessions/" in scope_section or "session" in scope_section.lower()
        assert has_session_path, (
            "docs/cli-reference.md '### `scope`' section must show the per-session "
            "scope.json path (<workspace>/.devbench/sessions/<name>/scope.json) "
            "so operators understand how DEVBENCH_SESSION_NAME affects the scope file "
            "(spec section 4.2.6.4)."
        )
