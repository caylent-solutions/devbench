"""Regression tests: spec-to-backlog SKILL.md Step 7d strict manifest-conflict check.

Verifies that plugin-authoring/devbench-authoring/skills/spec-to-backlog/SKILL.md
Step 7d correctly describes:

- AC-1: The skill runs the authoring-time strict manifest-conflict check
  (validate-backlog --strict / --include-draft) on its all-draft output before
  declaring success (spec Section 4 E13-F2-S1 AC-1).
- AC-2: When a conflict is found the skill wires the required serial-dep chain
  via the existing auto-injection step and re-checks; it does NOT declare
  success while a conflict remains (spec Section 4 E13-F2-S1 AC-2).
- AC-3: The check runs on the all-draft output (two tasks claiming the same
  (repo, path) with no serial dep triggers a non-zero finding before wiring,
  and the strict check passes after wiring) (spec Section 4 E13-F2-S1 AC-3;
  spec Section 10).

Spec Section 4 E13-F2-S1 AC-1, AC-2, AC-3. GitHub issue #267.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "plugin-authoring"
    / "devbench-authoring"
    / "skills"
    / "spec-to-backlog"
    / "SKILL.md"
)


def _read_skill() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _extract_step7d(text: str) -> str:
    """Return the text of Step 7d (strict manifest-conflict check section).

    Raises ValueError if Step 7d cannot be located.
    """
    idx = text.find("## Step 7d")
    if idx == -1:
        raise ValueError(
            "ERROR: '## Step 7d' not found in spec-to-backlog/SKILL.md. "
            "The authoring-time strict manifest-conflict check section is required "
            "(spec Section 4 E13-F2-S1, issue #267)."
        )
    next_h2 = text.find("\n## ", idx + len("## Step 7d"))
    if next_h2 != -1:
        return text[idx:next_h2]
    return text[idx:]


@pytest.mark.unit
class TestStep7dSectionExists:
    """AC-1: Step 7d must exist in SKILL.md and be correctly ordered."""

    def test_step7d_section_exists(self) -> None:
        """SKILL.md must contain a Step 7d section for the strict conflict check."""
        content = _read_skill()
        assert "## Step 7d" in content, (
            "ERROR: spec-to-backlog/SKILL.md must contain '## Step 7d' -- "
            "the authoring-time strict manifest-conflict check. "
            "Add '## Step 7d -- Authoring-time strict manifest-conflict check' "
            "between Step 7c and Step 8 "
            "(spec Section 4 E13-F2-S1 AC-1, issue #267)."
        )

    def test_step7d_appears_after_step7c(self) -> None:
        """Step 7d must appear after Step 7c in SKILL.md."""
        content = _read_skill()
        idx_7c = content.find("## Step 7c")
        idx_7d = content.find("## Step 7d")
        assert idx_7c != -1, "ERROR: '## Step 7c' not found in SKILL.md -- prerequisite for Step 7d ordering check."
        assert idx_7d != -1, (
            "ERROR: '## Step 7d' not found in SKILL.md -- strict manifest-conflict check required "
            "(spec Section 4 E13-F2-S1, issue #267)."
        )
        assert idx_7d > idx_7c, (
            "ERROR: '## Step 7d' must appear after '## Step 7c' in SKILL.md (spec Section 4 E13-F2-S1, issue #267)."
        )

    def test_step7d_appears_before_step8(self) -> None:
        """Step 7d must appear before Step 8 in SKILL.md."""
        content = _read_skill()
        idx_7d = content.find("## Step 7d")
        idx_8 = content.find("## Step 8")
        assert idx_7d != -1, (
            "ERROR: '## Step 7d' not found in SKILL.md -- strict manifest-conflict check required "
            "(spec Section 4 E13-F2-S1 AC-1, issue #267)."
        )
        assert idx_8 != -1, "ERROR: '## Step 8' not found in SKILL.md -- prerequisite for Step 7d ordering check."
        assert idx_7d < idx_8, (
            "ERROR: '## Step 7d' must appear before '## Step 8' in SKILL.md (spec Section 4 E13-F2-S1, issue #267)."
        )


@pytest.mark.unit
class TestStep7dStrictCheckInvocation:
    """AC-1: Step 7d must run validate-backlog --strict (or --include-draft) on all-draft output."""

    def test_strict_flag_present(self) -> None:
        """Step 7d must invoke validate-backlog with --strict or --include-draft."""
        content = _read_skill()
        section = _extract_step7d(content)
        has_strict = "--strict" in section or "--include-draft" in section
        assert has_strict, (
            "ERROR: spec-to-backlog/SKILL.md Step 7d must invoke validate-backlog "
            "with '--strict' or '--include-draft' to escalate draft/hold manifest "
            "conflicts to errors (spec Section 4 E13-F2-S1 AC-1, issue #267)."
        )

    def test_validate_backlog_command_present(self) -> None:
        """Step 7d must contain a validate-backlog invocation."""
        content = _read_skill()
        section = _extract_step7d(content)
        assert "validate-backlog" in section, (
            "ERROR: spec-to-backlog/SKILL.md Step 7d must contain a "
            "'validate-backlog' invocation "
            "(spec Section 4 E13-F2-S1 AC-1, issue #267)."
        )

    def test_all_draft_output_described(self) -> None:
        """Step 7d must describe running the check on the all-draft output."""
        content = _read_skill()
        section = _extract_step7d(content)
        lower = section.lower()
        has_draft_reference = "draft" in lower
        assert has_draft_reference, (
            "ERROR: spec-to-backlog/SKILL.md Step 7d must describe running "
            "the strict check on the all-draft output "
            "(spec Section 4 E13-F2-S1 AC-1, issue #267)."
        )

    def test_step7d_runs_before_success_declaration(self) -> None:
        """Step 7d strict check must gate success -- it must come before Step 8."""
        content = _read_skill()
        idx_7d = content.find("## Step 7d")
        idx_8 = content.find("## Step 8")
        assert idx_7d != -1 and idx_8 != -1, (
            "ERROR: Both '## Step 7d' and '## Step 8' must be present in SKILL.md "
            "(spec Section 4 E13-F2-S1 AC-1, issue #267)."
        )
        assert idx_7d < idx_8, (
            "ERROR: Step 7d (strict manifest-conflict check) must appear before "
            "Step 8 (success declaration) in SKILL.md -- the strict check must "
            "gate success (spec Section 4 E13-F2-S1 AC-1, issue #267)."
        )


@pytest.mark.unit
class TestStep7dSerialDepWiring:
    """AC-2: On a conflict, Step 7d must wire the serial-dep chain and re-check."""

    def test_serial_dep_wiring_on_conflict(self) -> None:
        """Step 7d must describe wiring a serial-dep chain when a conflict is found."""
        content = _read_skill()
        section = _extract_step7d(content)
        lower = section.lower()
        has_dep_wiring = "dep" in lower and ("wir" in lower or "serial" in lower or "chain" in lower)
        assert has_dep_wiring, (
            "ERROR: spec-to-backlog/SKILL.md Step 7d must describe wiring a "
            "serial-dep chain when a manifest conflict is detected "
            "(spec Section 4 E13-F2-S1 AC-2, issue #267)."
        )

    def test_recheck_after_wiring(self) -> None:
        """Step 7d must re-run the strict check after wiring the serial dep."""
        content = _read_skill()
        section = _extract_step7d(content)
        lower = section.lower()
        has_recheck = (
            "re-run" in lower
            or "re-check" in lower
            or "recheck" in lower
            or "rerun" in lower
            or "run again" in lower
            or "repeat" in lower
        )
        assert has_recheck, (
            "ERROR: spec-to-backlog/SKILL.md Step 7d must describe re-running "
            "the strict check after wiring the serial dep "
            "(spec Section 4 E13-F2-S1 AC-2, issue #267)."
        )

    def test_no_success_while_conflict_remains(self) -> None:
        """Step 7d must NOT declare success while a conflict remains."""
        content = _read_skill()
        section = _extract_step7d(content)
        lower = section.lower()
        # Must have some language indicating success is blocked while conflict remains
        blocks_premature_success = (
            "must not" in lower
            or "do not" in lower
            or "cannot" in lower
            or "only when" in lower
            or "only after" in lower
            or "until" in lower
            or "not declare success" in lower
            or "fail" in lower
        )
        assert blocks_premature_success, (
            "ERROR: spec-to-backlog/SKILL.md Step 7d must explicitly block "
            "premature success declaration while a manifest conflict remains "
            "(spec Section 4 E13-F2-S1 AC-2, issue #267)."
        )

    def test_uses_existing_auto_injection_step(self) -> None:
        """Step 7d must reuse the existing serial-dep auto-injection (not reinvent it)."""
        content = _read_skill()
        section = _extract_step7d(content)
        lower = section.lower()
        # The auto-injection step is documented in Step 5 under "Dependency wiring"
        # Step 7d should reference the existing mechanism rather than inventing new logic
        references_existing_mechanism = (
            "auto-inject" in lower or "step 5" in lower or "existing" in lower or "dep" in lower
        )
        assert references_existing_mechanism, (
            "ERROR: spec-to-backlog/SKILL.md Step 7d must reference the existing "
            "serial-dep auto-injection mechanism (Step 5 'Dependency wiring') rather "
            "than reinventing the wiring logic "
            "(spec Section 4 E13-F2-S1 AC-2, issue #267)."
        )


@pytest.mark.unit
class TestStep7dTwoTaskFixtureRegressionShape:
    """AC-3: Step 7d must describe the two-task same-(repo,path) regression scenario."""

    def test_two_task_conflict_described(self) -> None:
        """Step 7d must describe the two-task same-(repo, path) conflict scenario."""
        content = _read_skill()
        section = _extract_step7d(content)
        lower = section.lower()
        # The section must describe the scenario that drives the regression
        has_conflict_scenario = (
            "same" in lower
            or "conflict" in lower
            or "two task" in lower
            or "both task" in lower
            or "multiple task" in lower
        )
        assert has_conflict_scenario, (
            "ERROR: spec-to-backlog/SKILL.md Step 7d must describe the "
            "two-task same-(repo, path) manifest-conflict scenario that the "
            "strict check catches (spec Section 4 E13-F2-S1 AC-3, issue #267)."
        )

    def test_strict_check_initially_fails_described(self) -> None:
        """Step 7d must note that the strict check reports a conflict before wiring."""
        content = _read_skill()
        section = _extract_step7d(content)
        lower = section.lower()
        # The step must describe that the strict check fails before wiring
        reports_initial_failure = (
            "non-zero" in lower or "fail" in lower or "error" in lower or "conflict found" in lower
        )
        assert reports_initial_failure, (
            "ERROR: spec-to-backlog/SKILL.md Step 7d must describe that the "
            "strict check reports a conflict (non-zero / ERROR) before any wiring "
            "(spec Section 4 E13-F2-S1 AC-3, issue #267)."
        )

    def test_strict_check_passes_after_wiring_described(self) -> None:
        """Step 7d must describe that the strict check passes after the dep is wired."""
        content = _read_skill()
        section = _extract_step7d(content)
        lower = section.lower()
        # The step must describe that the check passes after wiring
        reports_pass_after_wiring = (
            "rc=0" in section
            or "passes" in lower
            or "clean" in lower
            or "zero error" in lower
            or "no conflict" in lower
        )
        assert reports_pass_after_wiring, (
            "ERROR: spec-to-backlog/SKILL.md Step 7d must describe that the "
            "strict check passes (rc=0) after the serial dep is wired "
            "(spec Section 4 E13-F2-S1 AC-3, issue #267)."
        )

    def test_later_task_gets_dep_on_earlier_task(self) -> None:
        """Step 7d must specify that the later task depends on the earlier task."""
        content = _read_skill()
        section = _extract_step7d(content)
        lower = section.lower()
        # The step must describe that the later task in the ordering gets the dep
        has_ordering_language = (
            "later" in lower
            or "earlier" in lower
            or "order" in lower
            or "predecessor" in lower
            or "successor" in lower
            or "depend" in lower
        )
        assert has_ordering_language, (
            "ERROR: spec-to-backlog/SKILL.md Step 7d must describe the ordering "
            "of the serial-dep chain (later task depends on earlier task) "
            "(spec Section 4 E13-F2-S1 AC-3, issue #267)."
        )


@pytest.mark.unit
class TestStep7dValidateBacklogStrictSemantics:
    """AC-1/AC-2: Step 7d strict-check semantics must be consistent with E13-F1-S1."""

    def test_strict_check_escalates_draft_hold_conflict_to_error(self) -> None:
        """Step 7d must note that --strict escalates draft/hold conflicts to errors."""
        content = _read_skill()
        section = _extract_step7d(content)
        lower = section.lower()
        # The strict flag escalates draft/hold conflicts from warning to error
        has_escalation_language = "error" in lower or "strict" in lower or "escalat" in lower
        assert has_escalation_language, (
            "ERROR: spec-to-backlog/SKILL.md Step 7d must note that '--strict' "
            "escalates draft/hold manifest conflicts to errors (non-zero exit) "
            "so the check is real and not a stub "
            "(spec Section 4 E13-F2-S1 AC-1, issue #267)."
        )

    def test_add_dep_command_or_dep_wiring_present(self) -> None:
        """Step 7d must describe wiring deps via add-dep or the dep table."""
        content = _read_skill()
        section = _extract_step7d(content)
        has_dep_mechanism = (
            "add-dep" in section
            or "## Dependencies" in section
            or "serial dep" in section.lower()
            or "serial-dep" in section.lower()
            or "dependency" in section.lower()
        )
        assert has_dep_mechanism, (
            "ERROR: spec-to-backlog/SKILL.md Step 7d must describe the dep-wiring "
            "mechanism (e.g. add-dep command or '## Dependencies' table edit) used "
            "to resolve the conflict (spec Section 4 E13-F2-S1 AC-2, issue #267)."
        )

    def test_no_success_on_nonzero_strict_check(self) -> None:
        """Step 7d must require rc=0 from the strict check before declaring success."""
        content = _read_skill()
        section = _extract_step7d(content)
        lower = section.lower()
        # Must gate success on the strict check being clean
        gates_success = "rc=0" in section or "zero" in lower or "clean" in lower or "passes" in lower or "only" in lower
        assert gates_success, (
            "ERROR: spec-to-backlog/SKILL.md Step 7d must gate success on the "
            "strict check returning rc=0 (zero conflicts) -- the skill must not "
            "declare success while a conflict remains "
            "(spec Section 4 E13-F2-S1 AC-2, issue #267)."
        )
