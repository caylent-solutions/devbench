"""Skill-content regression tests for the authoring deterministic strict gate.

Pins the G1/G2/G3 hardening of ``spec-to-backlog/SKILL.md`` and the C1/C2/C3
hardening of ``create-spec/SKILL.md`` (authoring-skills-deterministic-strict-gate
spec). These are content assertions over the live skill sources, mirroring the
existing ``tests/test_docs/test_skill_model_refs.py`` and
``tests/test_plugin/test_spec_to_backlog_manifest_conflict_strict.py`` families.

The spec's items:

- G1: ``spec-to-backlog`` must mandate the ``- [ ] AC-N:`` checkbox AC form in
  the skeleton and a per-task rubric item, citing ``_CHECKBOX_RE`` as the reason.
- G2 (keystone): ``spec-to-backlog`` Step 5d and Step 7 must run
  ``validate-backlog --strict`` as the deterministic completion gate.
- G3: ``spec-to-backlog`` serial-dep injection must be verb-aware
  (adds-before-modifies), mirroring the validator's ``_order_conflict_chain``.
- C1: ``create-spec`` must require checkbox-form ACs.
- C2: ``create-spec`` must require a concrete verifying command + expected exit
  per executable AC.
- C3: ``create-spec`` must add a feasibility-against-stated-versions rubric item.

Both skills must remain domain-agnostic (no new reference to a specific stack,
repo, workspace, or domain).
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent

SPEC_TO_BACKLOG_SKILL = (
    REPO_ROOT / "plugin-authoring" / "devbench-authoring" / "skills" / "spec-to-backlog" / "SKILL.md"
)
CREATE_SPEC_SKILL = REPO_ROOT / "plugin-authoring" / "devbench-authoring" / "skills" / "create-spec" / "SKILL.md"


def _read(path: Path) -> str:
    assert path.is_file(), f"Required skill file does not exist: {path}"
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """Return the text of the ``## <heading>`` section up to the next ``## `` heading."""
    idx = text.find(heading)
    assert idx != -1, f"ERROR: heading {heading!r} not found in skill source."
    nxt = text.find("\n## ", idx + len(heading))
    return text[idx:nxt] if nxt != -1 else text[idx:]


# ---------------------------------------------------------------------------
# G1 -- checkbox AC form mandated in spec-to-backlog
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestG1CheckboxACForm:
    """G1: spec-to-backlog mandates the registerable ``- [ ] AC-N:`` checkbox form."""

    def test_skeleton_mandates_checkbox_ac_form(self) -> None:
        """The section-11 AC skeleton must state the ``- [ ] AC-N:`` checkbox form is mandatory."""
        text = _read(SPEC_TO_BACKLOG_SKILL)
        assert "- [ ] AC-N:" in text, "spec-to-backlog SKILL.md must mandate the `- [ ] AC-N:` checkbox AC form (G1)."

    def test_skeleton_cites_checkbox_regex(self) -> None:
        """The skeleton must cite ``_CHECKBOX_RE`` as the reason the form is required."""
        text = _read(SPEC_TO_BACKLOG_SKILL)
        assert "_CHECKBOX_RE" in text, (
            "spec-to-backlog SKILL.md must cite `_CHECKBOX_RE` as the reason the checkbox AC "
            "form is the only registerable form (G1)."
        )

    def test_plain_bullet_ac_forbidden(self) -> None:
        """The skill must explicitly forbid plain-bullet ACs."""
        text = _read(SPEC_TO_BACKLOG_SKILL).lower()
        assert "plain-bullet" in text or "plain bullet" in text, (
            "spec-to-backlog SKILL.md must explicitly forbid plain-bullet ACs (G1)."
        )

    def test_per_task_rubric_item_present(self) -> None:
        """Step 5b must carry a checkbox-AC-form rubric item that FAILs on a plain bullet."""
        text = _read(SPEC_TO_BACKLOG_SKILL)
        rubric = _section(text, "### 5b -- Self-critique at per-Task granularity")
        assert "Checkbox AC form" in rubric and "G1" in rubric, (
            "spec-to-backlog SKILL.md Step 5b must add a 'Checkbox AC form (G1)' rubric item."
        )
        assert "FAIL" in rubric and "- [ ] AC-N:" in rubric, (
            "The checkbox-AC rubric item must specify the `- [ ] AC-N:` form and a FAIL condition."
        )


# ---------------------------------------------------------------------------
# G2 -- deterministic strict gate (keystone)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestG2DeterministicStrictGate:
    """G2: Step 5d and Step 7 run ``validate-backlog --strict`` as the completion gate."""

    def test_step5d_runs_strict(self) -> None:
        """Step 5d's validate gate must invoke ``validate-backlog --strict``."""
        text = _read(SPEC_TO_BACKLOG_SKILL)
        step5d = _section(text, "### 5d -- Post-process + run validate-backlog")
        assert "validate-backlog --strict" in step5d, (
            "spec-to-backlog SKILL.md Step 5d must run `uv run devbench validate-backlog --strict` "
            "as the deterministic gate (G2)."
        )

    def test_step5d_drives_to_zero_findings(self) -> None:
        """Step 5d must drive the strict run to zero findings (not merely run it once)."""
        text = _read(SPEC_TO_BACKLOG_SKILL)
        step5d = _section(text, "### 5d -- Post-process + run validate-backlog")
        lower = step5d.lower()
        assert "zero findings" in lower, (
            "spec-to-backlog SKILL.md Step 5d must iterate the strict gate to zero findings (G2)."
        )
        assert "[BLOCKED]" in step5d, (
            "spec-to-backlog SKILL.md Step 5d must emit a [BLOCKED] audit on non-convergence "
            "rather than shipping known strict ERRORS (G2)."
        )

    def test_step7_runs_strict(self) -> None:
        """The final whole-backlog gate (Step 7) must invoke ``validate-backlog --strict``."""
        text = _read(SPEC_TO_BACKLOG_SKILL)
        step7 = _section(text, "## Step 7 -- Final whole-backlog validation")
        assert "validate-backlog --strict" in step7, (
            "spec-to-backlog SKILL.md Step 7 final gate must run `uv run devbench validate-backlog --strict` (G2)."
        )

    def test_step7_exit_condition_is_strict(self) -> None:
        """Step 7's first exit condition must require the strict run rc=0 with zero findings."""
        text = _read(SPEC_TO_BACKLOG_SKILL)
        step7 = _section(text, "## Step 7 -- Final whole-backlog validation")
        assert "validate-backlog --strict` returns rc=0" in step7, (
            "spec-to-backlog SKILL.md Step 7 exit condition 1 must require `validate-backlog --strict` rc=0 (G2)."
        )

    def test_documents_authoring_vs_orchestrator_split(self) -> None:
        """G2 must note the authoring-time vs orchestrator-time check split."""
        text = _read(SPEC_TO_BACKLOG_SKILL)
        step5d = _section(text, "### 5d -- Post-process + run validate-backlog")
        lower = step5d.lower()
        assert "authoring-time" in lower and "orchestrator-time" in lower, (
            "spec-to-backlog SKILL.md Step 5d must document the authoring-time vs orchestrator-time "
            "split for the strict checks (G2)."
        )

    def test_final_rubric_item_is_strict(self) -> None:
        """The bottom-of-file self-critique rubric item 11 must reference the strict gate."""
        text = _read(SPEC_TO_BACKLOG_SKILL)
        rubric = _section(text, "## Self-critique rubric for spec-to-backlog")
        assert "validate-backlog --strict" in rubric, (
            "spec-to-backlog SKILL.md final rubric (item 11) must reference the "
            "`validate-backlog --strict` deterministic gate (G2)."
        )


# ---------------------------------------------------------------------------
# G3 -- verb-aware serial-dep injection (adds-before-modifies)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestG3VerbAwareSerialDep:
    """G3: serial-dep injection is verb-aware (adds-before-modifies)."""

    def test_step5_dep_wiring_is_verb_aware(self) -> None:
        """Step 5 dependency wiring must describe verb-aware adds-before-modifies ordering."""
        text = _read(SPEC_TO_BACKLOG_SKILL)
        assert "adds-before-modifies" in text, (
            "spec-to-backlog SKILL.md must describe the verb-aware adds-before-modifies serial-dep ordering (G3)."
        )

    def test_step5_cites_adder_is_dependency(self) -> None:
        """The wiring rule must state the adder is the dependency (runs first)."""
        text = _read(SPEC_TO_BACKLOG_SKILL)
        lower = text.lower()
        assert "adder is the dependency" in lower or "adder must run first" in lower, (
            "spec-to-backlog SKILL.md must state that for an add-vs-modify conflict the adder is "
            "the dependency (runs first) (G3)."
        )

    def test_mirrors_validator_helpers(self) -> None:
        """The wiring rule must cite the validator's verb-aware helpers so the two agree."""
        text = _read(SPEC_TO_BACKLOG_SKILL)
        assert "_order_conflict_chain" in text or "_classify_manifest_verb" in text, (
            "spec-to-backlog SKILL.md must cite the validator's verb-aware ordering "
            "(`_order_conflict_chain` / `_classify_manifest_verb`) so authoring-time and "
            "validator-time agree (G3)."
        )

    def test_step7d2_is_verb_aware(self) -> None:
        """Step 7d-2 wiring must also use the verb-aware direction, not pure positional."""
        text = _read(SPEC_TO_BACKLOG_SKILL)
        idx = text.find("### 7d-2")
        assert idx != -1, "spec-to-backlog SKILL.md must retain Step 7d-2."
        nxt = text.find("\n### ", idx + len("### 7d-2"))
        step7d2 = text[idx:nxt] if nxt != -1 else text[idx:]
        assert "adds-before-modifies" in step7d2 or "adder is the dependency" in step7d2.lower(), (
            "spec-to-backlog SKILL.md Step 7d-2 must wire conflicts verb-aware "
            "(adds-before-modifies), falling back to positional only when verbs do not "
            "disambiguate (G3)."
        )

    def test_positional_fallback_preserved(self) -> None:
        """The verb-aware rule must keep a deterministic positional fallback."""
        text = _read(SPEC_TO_BACKLOG_SKILL)
        lower = text.lower()
        assert "fall back" in lower and "do not disambiguate" in lower, (
            "spec-to-backlog SKILL.md must keep the positional fallback for when verbs do not "
            "disambiguate (all-modify / multiple-adders) (G3)."
        )


# ---------------------------------------------------------------------------
# create-spec C1/C2/C3
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateSpecC1C2C3:
    """create-spec C1 (checkbox), C2 (verifying command), C3 (feasibility)."""

    def test_c1_checkbox_ac_form_required(self) -> None:
        """C1: create-spec must require the `- [ ] AC-N:` checkbox AC form."""
        text = _read(CREATE_SPEC_SKILL)
        assert "- [ ] AC-N:" in text and "C1" in text, (
            "create-spec SKILL.md must require the `- [ ] AC-N:` checkbox AC form (C1)."
        )

    def test_c1_rubric_item_present(self) -> None:
        """C1: the self-critique rubric must add a checkbox-AC-form item that FAILs on plain bullets."""
        text = _read(CREATE_SPEC_SKILL)
        rubric = _section(text, "### Self-critique rubric for create-spec")
        assert "Checkbox AC form (C1)" in rubric, "create-spec SKILL.md rubric must add a 'Checkbox AC form (C1)' item."

    def test_c2_verifying_command_required(self) -> None:
        """C2: create-spec must require a concrete verifying command + expected exit per executable AC."""
        text = _read(CREATE_SPEC_SKILL)
        assert "C2" in text, "create-spec SKILL.md must document the C2 verifying-command requirement."
        lower = text.lower()
        assert "expected exit" in lower or "expect-exit" in lower, (
            "create-spec SKILL.md must require the expected exit code per executable AC (C2)."
        )
        assert "copying rather than inventing" in lower or "by **copying**" in lower or "copy" in lower, (
            "create-spec SKILL.md C2 must explain that spec-to-backlog copies the command rather "
            "than inventing it (C2)."
        )

    def test_c2_rubric_item_present(self) -> None:
        """C2: the rubric must add a verifying-command item referencing TDI-001/004."""
        text = _read(CREATE_SPEC_SKILL)
        rubric = _section(text, "### Self-critique rubric for create-spec")
        assert "Verifying command per executable AC (C2)" in rubric, (
            "create-spec SKILL.md rubric must add a 'Verifying command per executable AC (C2)' item."
        )
        assert "TDI-001" in rubric and "TDI-004" in rubric, (
            "The C2 rubric item must reference TDI-001 (path base) and TDI-004 (command-vs-deferred)."
        )

    def test_c3_feasibility_rubric_item_present(self) -> None:
        """C3: the rubric must add a feasibility-against-stated-versions item."""
        text = _read(CREATE_SPEC_SKILL)
        rubric = _section(text, "### Self-critique rubric for create-spec")
        assert "Feasibility against stated tool versions (C3)" in rubric, (
            "create-spec SKILL.md rubric must add a 'Feasibility against stated tool versions (C3)' item."
        )

    def test_c3_guidance_in_authoring_body(self) -> None:
        """C3: the authoring body must instruct the model to check AC feasibility against versions."""
        text = _read(CREATE_SPEC_SKILL)
        lower = text.lower()
        assert "unsatisfiable-by-construction" in lower or "unsatisfiable by construction" in lower, (
            "create-spec SKILL.md must warn that an AC mandating behaviour impossible on the pinned "
            "version is unsatisfiable-by-construction (C3)."
        )


# ---------------------------------------------------------------------------
# AC-6 -- both skills remain domain-agnostic
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSkillsRemainDomainAgnostic:
    """AC-6: the G*/C* edits introduce no coupling to a specific stack, repo, or workspace."""

    # Tokens that would indicate coupling to a concrete workspace/domain. The
    # generic execution-verb vocabulary the skills already ship (terraform,
    # pytest, make, ...) is domain-neutral and intentionally NOT listed here.
    # NOTE: pre-existing worked-example tokens (e.g. the illustrative
    # ``tools-telemetry`` TDI-001 example) predate this change set; AC-6 forbids
    # *new* coupling, so this list is checked against the lines this change set
    # ADDED, not the whole file -- see test_edits_introduce_no_new_coupling.
    _FORBIDDEN_DOMAIN_TOKENS = (
        "tools-telemetry",
        "telemetry-collector",
        "caylent",
        "data-lake",
        "waf-webacl",
        "COLLECTOR_URL",
    )

    @staticmethod
    def _added_lines(skill_path: Path) -> list[str] | None:
        """Return the lines this change set added to ``skill_path`` vs. HEAD.

        Uses ``git diff HEAD`` so only lines introduced by the working-tree
        edits are inspected. Returns ``None`` when git is unavailable or the
        file has no recorded HEAD (the caller then skips the diff-scoped check).
        """
        try:
            diff = subprocess.run(
                ["git", "diff", "--unified=0", "HEAD", "--", str(skill_path)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        except (OSError, subprocess.CalledProcessError):
            return None
        return [line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]

    @pytest.mark.parametrize(
        "skill_path",
        [SPEC_TO_BACKLOG_SKILL, CREATE_SPEC_SKILL],
        ids=["spec-to-backlog", "create-spec"],
    )
    def test_edits_introduce_no_new_coupling(self, skill_path: Path) -> None:
        """AC-6: lines added by this change set introduce no workspace/domain coupling."""
        added = self._added_lines(skill_path)
        if added is None:
            pytest.skip("git diff against HEAD unavailable in this environment")
        hits = sorted({tok for line in added for tok in self._FORBIDDEN_DOMAIN_TOKENS if tok in line})
        assert not hits, (
            f"{skill_path.relative_to(REPO_ROOT)} edits must stay domain-agnostic; "
            f"newly added line(s) reference workspace/domain-specific token(s): {hits} (AC-6)."
        )

    @pytest.mark.parametrize(
        "skill_path",
        [SPEC_TO_BACKLOG_SKILL, CREATE_SPEC_SKILL],
        ids=["spec-to-backlog", "create-spec"],
    )
    def test_application_agnostic_statement_preserved(self, skill_path: Path) -> None:
        """The existing 'application-agnostic' self-description is preserved."""
        text = _read(skill_path)
        assert "application-agnostic" in text, (
            f"{skill_path.relative_to(REPO_ROOT)} must preserve its application-agnostic statement (AC-6)."
        )

    @pytest.mark.parametrize(
        "skill_path",
        [SPEC_TO_BACKLOG_SKILL, CREATE_SPEC_SKILL],
        ids=["spec-to-backlog", "create-spec"],
    )
    def test_no_em_dash(self, skill_path: Path) -> None:
        """Code standard: no em-dash (U+2014) introduced by the edits."""
        text = _read(skill_path)
        assert "—" not in text, (
            f"{skill_path.relative_to(REPO_ROOT)} must not contain em-dash characters (U+2014); use '--'."
        )

    @pytest.mark.parametrize(
        "skill_path",
        [SPEC_TO_BACKLOG_SKILL, CREATE_SPEC_SKILL],
        ids=["spec-to-backlog", "create-spec"],
    )
    def test_optional_exemplar_remains_optional(self, skill_path: Path) -> None:
        """The optional-exemplar contract (no hardcoded default path) is preserved."""
        text = _read(skill_path)
        assert re.search(r"Do NOT default to any hardcoded path", text), (
            f"{skill_path.relative_to(REPO_ROOT)} must keep the optional-exemplar contract "
            "(no hardcoded default path) (AC-6)."
        )
