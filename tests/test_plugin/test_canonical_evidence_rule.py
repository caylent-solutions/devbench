"""Doc-presence tests for the CANONICAL EVIDENCE RULE in the authoring skills.

Pins the canonical repo-evidence rule (TDI: authoring skills lack a canonical
evidence rule for repo-derived counts -- vendored contamination AND depth-blind
globs whipsawed a scope decision twice in one day). These are content
assertions over the live skill sources, mirroring the existing
``tests/test_plugin/test_workflow_patterns_doc.py`` and
``tests/test_plugin/test_authoring_strict_gate.py`` families.

The rule, per the issue's Section 3, requires for every repo-derived count:
  1. tracked-files-first via ``git ls-files '<full-depth ** glob>'``;
  2. ALWAYS exclude the canonical vendored/generated dirs;
  3. print 5 sample matched paths with every count and eyeball them;
  4. an "evidence soundness" self-critique rubric item that re-derives each
     load-bearing count a second independent way (dual derivation).

AC-1: both SKILL.md files + the shared patterns doc contain the rule with the
exclusion list, the ``git ls-files`` directive, and the sample-paths
requirement.
AC-2: the create-spec rubric contains the evidence-soundness dual-derivation
item (and spec-to-backlog carries the parallel rubric item).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent

CREATE_SPEC_SKILL = REPO_ROOT / "plugin-authoring" / "devbench-authoring" / "skills" / "create-spec" / "SKILL.md"
SPEC_TO_BACKLOG_SKILL = (
    REPO_ROOT / "plugin-authoring" / "devbench-authoring" / "skills" / "spec-to-backlog" / "SKILL.md"
)
SHARED_DOC = REPO_ROOT / "plugin-authoring" / "devbench-authoring" / "docs" / "workflow-authoring-patterns.md"

SKILL_FILES = [
    pytest.param(CREATE_SPEC_SKILL, id="create-spec"),
    pytest.param(SPEC_TO_BACKLOG_SKILL, id="spec-to-backlog"),
]

CANONICAL_EXCLUSION_DIRS = [
    ".terraform/",
    "node_modules/",
    ".venv/",
    "vendor/",
    "__pycache__/",
    "dist/",
    ".git/",
]


def _read(path: Path) -> str:
    assert path.is_file(), f"Required doc file does not exist: {path}"
    return path.read_text(encoding="utf-8")


@pytest.mark.unit
class TestCanonicalEvidenceRuleInBothSkills:
    """AC-1: both SKILL.md files contain the canonical evidence rule."""

    @pytest.mark.parametrize("skill_path", SKILL_FILES)
    def test_git_ls_files_directive_present(self, skill_path: Path) -> None:
        """The tracked-files-first ``git ls-files`` directive must be present (rule 1)."""
        text = _read(skill_path)
        assert "git ls-files" in text, (
            f"{skill_path.relative_to(REPO_ROOT)} must mandate tracked-files-first repo counts "
            "via `git ls-files '<full-depth ** glob>'` (canonical evidence rule 1)."
        )

    @pytest.mark.parametrize("skill_path", SKILL_FILES)
    def test_full_depth_glob_over_depth1_called_out(self, skill_path: Path) -> None:
        """The rule must call out the full-depth ``**`` glob and warn against a depth-1 glob (rule 1)."""
        text = _read(skill_path)
        assert "full-depth" in text, (
            f"{skill_path.relative_to(REPO_ROOT)} must require a full-depth `**` glob so a nested "
            "layout cannot be missed (canonical evidence rule 1)."
        )
        assert "depth-1 glob" in text or "depth-blind glob" in text, (
            f"{skill_path.relative_to(REPO_ROOT)} must warn against a depth-1 / depth-blind glob "
            "that a nested layout can hide from (canonical evidence rule 1)."
        )

    @pytest.mark.parametrize("skill_path", SKILL_FILES)
    @pytest.mark.parametrize("exclusion_dir", CANONICAL_EXCLUSION_DIRS)
    def test_canonical_exclusion_dir_present(self, skill_path: Path, exclusion_dir: str) -> None:
        """Every canonical vendored/generated dir must appear in the exclusion list (rule 2)."""
        text = _read(skill_path)
        assert exclusion_dir in text, (
            f"{skill_path.relative_to(REPO_ROOT)} must list the canonical vendored/generated dir "
            f"'{exclusion_dir}' in the evidence-rule exclusion list (canonical evidence rule 2)."
        )

    @pytest.mark.parametrize("skill_path", SKILL_FILES)
    def test_ready_made_exclusion_form_present(self, skill_path: Path) -> None:
        """A ready-made ``grep -v`` exclusion form must be shown (rule 2)."""
        text = _read(skill_path)
        assert "grep -v" in text, (
            f"{skill_path.relative_to(REPO_ROOT)} must show a ready-made `grep -v` exclusion form "
            "for the vendored/generated dirs (canonical evidence rule 2)."
        )

    @pytest.mark.parametrize("skill_path", SKILL_FILES)
    def test_sample_paths_requirement_present(self, skill_path: Path) -> None:
        """The 5-sample-matched-paths requirement must be present (rule 3)."""
        text = _read(skill_path)
        lower = text.lower()
        assert "5 sample" in lower or "5 sample matched paths" in lower, (
            f"{skill_path.relative_to(REPO_ROOT)} must require printing 5 sample matched paths with "
            "every count (canonical evidence rule 3)."
        )
        assert "eyeball" in lower, (
            f"{skill_path.relative_to(REPO_ROOT)} must require eyeballing the sample paths against "
            "the claimed layout (canonical evidence rule 3)."
        )

    @pytest.mark.parametrize("skill_path", SKILL_FILES)
    def test_references_pattern_7_shared_doc(self, skill_path: Path) -> None:
        """Each skill must cross-reference Pattern 7 in the shared patterns doc (DRY)."""
        text = _read(skill_path)
        assert "Canonical Repo-Evidence Collection" in text, (
            f"{skill_path.relative_to(REPO_ROOT)} must cite Pattern 7 (Canonical Repo-Evidence Collection) by name."
        )
        assert "docs/workflow-authoring-patterns.md" in text, (
            f"{skill_path.relative_to(REPO_ROOT)} must link back to docs/workflow-authoring-patterns.md "
            "for the full generic form (DRY)."
        )


@pytest.mark.unit
class TestEvidenceSoundnessRubricItem:
    """AC-2: the self-critique rubrics carry the evidence-soundness dual-derivation item."""

    def test_create_spec_rubric_has_evidence_soundness_item(self) -> None:
        """create-spec self-critique rubric must add an 'Evidence soundness (C4)' item."""
        text = _read(CREATE_SPEC_SKILL)
        assert "Evidence soundness (C4)" in text, (
            "create-spec SKILL.md self-critique rubric must add an 'Evidence soundness (C4)' item."
        )

    def test_create_spec_rubric_item_requires_dual_derivation(self) -> None:
        """The create-spec evidence-soundness item must require a second independent re-derivation."""
        text = _read(CREATE_SPEC_SKILL)
        lower = text.lower()
        assert "re-derive" in lower or "re-derived" in lower, (
            "create-spec SKILL.md evidence-soundness item must require re-deriving each load-bearing "
            "count a second independent way (canonical evidence rule 4)."
        )
        assert "material delta" in lower, (
            "create-spec SKILL.md evidence-soundness item must state that a material delta between the "
            "two derivations blocks convergence until reconciled (canonical evidence rule 4)."
        )

    def test_spec_to_backlog_rubric_has_evidence_soundness_item(self) -> None:
        """spec-to-backlog must carry the parallel 'Evidence soundness (E4)' rubric item."""
        text = _read(SPEC_TO_BACKLOG_SKILL)
        assert "Evidence soundness (E4)" in text, (
            "spec-to-backlog SKILL.md must add an 'Evidence soundness (E4)' rubric item."
        )

    def test_spec_to_backlog_rubric_item_requires_dual_derivation(self) -> None:
        """The spec-to-backlog evidence-soundness item must require a second independent re-derivation."""
        text = _read(SPEC_TO_BACKLOG_SKILL)
        lower = text.lower()
        assert "re-derive" in lower or "re-derived" in lower, (
            "spec-to-backlog SKILL.md evidence-soundness item must require re-deriving each "
            "load-bearing count a second independent way (canonical evidence rule 4)."
        )
        assert "material delta" in lower, (
            "spec-to-backlog SKILL.md evidence-soundness item must state that a material delta blocks "
            "convergence until reconciled (canonical evidence rule 4)."
        )


@pytest.mark.unit
class TestCanonicalEvidenceRuleInSharedDoc:
    """AC-1: the shared patterns doc defines Pattern 7 with all four rule parts."""

    def test_pattern_7_heading_present(self) -> None:
        """The shared doc must define Pattern 7 as a Markdown heading."""
        lines = _read(SHARED_DOC).splitlines()
        heading_lines = [line for line in lines if line.startswith("#")]
        combined = "\n".join(heading_lines).lower()
        assert "canonical repo-evidence collection" in combined, (
            "workflow-authoring-patterns.md must define Pattern 7 "
            "(Canonical Repo-Evidence Collection) as a Markdown heading."
        )

    def test_git_ls_files_directive_present(self) -> None:
        """The shared doc must carry the tracked-files-first directive (rule 1)."""
        text = _read(SHARED_DOC)
        assert "git ls-files" in text, "Pattern 7 must mandate tracked-files-first `git ls-files` counts (rule 1)."

    @pytest.mark.parametrize("exclusion_dir", CANONICAL_EXCLUSION_DIRS)
    def test_canonical_exclusion_dir_present(self, exclusion_dir: str) -> None:
        """The shared doc must list every canonical vendored/generated dir (rule 2)."""
        text = _read(SHARED_DOC)
        assert exclusion_dir in text, (
            f"Pattern 7 must list the canonical vendored/generated dir '{exclusion_dir}' (rule 2)."
        )

    def test_sample_paths_requirement_present(self) -> None:
        """The shared doc must carry the 5-sample-paths requirement (rule 3)."""
        lower = _read(SHARED_DOC).lower()
        assert "5 sample" in lower and "eyeball" in lower, (
            "Pattern 7 must require printing and eyeballing 5 sample matched paths with every count (rule 3)."
        )

    def test_dual_derivation_requirement_present(self) -> None:
        """The shared doc must carry the dual-derivation evidence-soundness step (rule 4)."""
        lower = _read(SHARED_DOC).lower()
        assert "dual derivation" in lower or "re-derive" in lower or "re-derived" in lower, (
            "Pattern 7 must require re-deriving every load-bearing count a second independent way (rule 4)."
        )
        assert "material delta" in lower, (
            "Pattern 7 must state that a material delta between derivations blocks convergence (rule 4)."
        )


@pytest.mark.unit
class TestNoEmDashInChangedFiles:
    """Code standard: no em-dash (U+2014) in the changed authoring docs."""

    @pytest.mark.parametrize(
        "doc_path",
        [
            pytest.param(CREATE_SPEC_SKILL, id="create-spec"),
            pytest.param(SPEC_TO_BACKLOG_SKILL, id="spec-to-backlog"),
            pytest.param(SHARED_DOC, id="shared-doc"),
        ],
    )
    def test_no_em_dash(self, doc_path: Path) -> None:
        """The doc must not contain em-dash characters (U+2014); use '--'."""
        text = _read(doc_path)
        assert "—" not in text, (
            f"{doc_path.relative_to(REPO_ROOT)} must not contain em-dash (U+2014); use '--' (double hyphen)."
        )
