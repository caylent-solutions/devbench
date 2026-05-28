"""Tests for ``devbench.plugin_helpers.code_standards_template`` (#230).

The helper emits the canonical ``### Code Standards`` block with three
substitutable placeholders. Tests cover happy paths, the workspace
override mechanism, and the fail-fast guard on a non-existent
``workspace_root``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.plugin_helpers import code_standards_template as cst


@pytest.mark.unit
class TestEmitCodeStandardsBlock:
    """Exercises the public helper end-to-end."""

    def test_emits_canonical_string_starts_with_header(self, tmp_path: Path) -> None:
        block = cst.emit_code_standards_block(tmp_path)
        assert block.startswith("### Code Standards")

    def test_emits_all_six_subsections(self, tmp_path: Path) -> None:
        block = cst.emit_code_standards_block(tmp_path)
        for subsection in (
            "#### Critical Rules (Violation = Automatic Rejection)",
            "#### Architecture Principles",
            "#### Testing Rules",
            "#### Git Rules",
            "#### Security Rules",
            "#### Error Handling Contract",
        ):
            assert subsection in block, f"missing subsection: {subsection!r}"

    def test_substitutes_workspace_claude_md(self, tmp_path: Path) -> None:
        block = cst.emit_code_standards_block(tmp_path)
        assert str(tmp_path / "CLAUDE.md") in block
        assert "<WORKSPACE_CLAUDE_MD>" not in block

    def test_task_specific_error_paths_appended(self, tmp_path: Path) -> None:
        block = cst.emit_code_standards_block(
            tmp_path,
            task_specific_error_paths=[
                "merge conflict marker remaining after staging = ERROR",
                "make target exit non-zero = ERROR",
            ],
        )
        assert "- merge conflict marker remaining after staging = ERROR" in block
        assert "- make target exit non-zero = ERROR" in block
        assert "<TASK_SPECIFIC_ERROR_PATHS>" not in block

    def test_no_task_specific_paths_renders_none(self, tmp_path: Path) -> None:
        block = cst.emit_code_standards_block(tmp_path)
        # Find the Task-specific section and confirm it renders ``(none)``.
        marker = "Task-specific error paths for this work unit:"
        assert marker in block
        tail = block.split(marker, 1)[1]
        assert "(none)" in tail

    def test_carve_outs_rendered_as_bullets(self, tmp_path: Path) -> None:
        block = cst.emit_code_standards_block(
            tmp_path,
            repo_specific_carve_outs={
                "src/kanon_cli/repo/": "vendored repo-tool fork",
            },
        )
        assert "- `src/kanon_cli/repo/` -- vendored repo-tool fork" in block
        assert "<REPO_CARVE_OUTS>" not in block

    def test_no_carve_outs_renders_none(self, tmp_path: Path) -> None:
        block = cst.emit_code_standards_block(tmp_path)
        marker = "Repo-specific carve-outs:"
        assert marker in block
        tail = block.split(marker, 1)[1].split("####", 1)[0]
        assert "(none)" in tail

    def test_idempotent_for_same_inputs(self, tmp_path: Path) -> None:
        first = cst.emit_code_standards_block(tmp_path, task_specific_error_paths=["foo"])
        second = cst.emit_code_standards_block(tmp_path, task_specific_error_paths=["foo"])
        assert first == second

    def test_nonexistent_workspace_raises(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"
        with pytest.raises(FileNotFoundError, match="workspace_root does not exist"):
            cst.emit_code_standards_block(missing)

    def test_workspace_override_used_when_present(self, tmp_path: Path) -> None:
        """A ``code-standards-canonical.md`` at the workspace root overrides the default."""
        override = (
            "### Code Standards\n\n"
            "Custom workspace standards.\n\n"
            "Workspace CLAUDE: <WORKSPACE_CLAUDE_MD>\n\n"
            "Carve-outs:\n\n<REPO_CARVE_OUTS>\n\n"
            "Errors:\n\n<TASK_SPECIFIC_ERROR_PATHS>\n"
        )
        (tmp_path / "code-standards-canonical.md").write_text(override, encoding="utf-8")
        block = cst.emit_code_standards_block(
            tmp_path,
            task_specific_error_paths=["custom-error"],
        )
        assert "Custom workspace standards." in block
        assert str(tmp_path / "CLAUDE.md") in block
        assert "- custom-error" in block
        # The shipped canonical body's distinctive opening line is NOT present.
        assert "These are checked by the LLM review judges" not in block


@pytest.mark.unit
class TestCanonicalBodyExcludingErrorContract:
    """The trimmed canonical body is used by the drift detector."""

    def test_starts_with_header(self) -> None:
        body = cst.canonical_body_excluding_error_contract()
        assert body.startswith("### Code Standards")

    def test_omits_error_handling_contract(self) -> None:
        body = cst.canonical_body_excluding_error_contract()
        assert "#### Error Handling Contract" not in body
        # Task-specific marker also gone (it lives under Error Handling Contract).
        assert "<TASK_SPECIFIC_ERROR_PATHS>" not in body

    def test_keeps_other_subsections(self) -> None:
        body = cst.canonical_body_excluding_error_contract()
        for subsection in (
            "#### Critical Rules (Violation = Automatic Rejection)",
            "#### Architecture Principles",
            "#### Testing Rules",
            "#### Git Rules",
            "#### Security Rules",
        ):
            assert subsection in body, f"missing subsection: {subsection!r}"
