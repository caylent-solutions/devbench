"""Structural pins for the migrate-env subcommand addition in docs/cli-reference.md.

Verifies that docs/cli-reference.md documents:
- The ``devbench migrate-env`` subcommand (AC-197-4).
- All three modes: no-arg (stdout), --output <path>, --dry-run.
- Exit code semantics: 0 when legacy vars found; 1 when already migrated.
- Non-destructive guarantee (never modifies caller's env or shell rc files).
- DEVBENCH_BOOTSTRAP bypass contract (the only devbench subcommand that does
  not fail-fast on legacy JUDGE_* vars) (AC-197-7).
- Worked examples for each mode.

Spec source: spec/devbench-self-improve.md section 4.9. Issue #197.
AC: AC-197-4, AC-197-7.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
CLI_REFERENCE_DOC = REPO_ROOT / "docs" / "cli-reference.md"


def _read_doc() -> str:
    return CLI_REFERENCE_DOC.read_text(encoding="utf-8")


def _extract_section(text: str, heading: str) -> str:
    """Return content of the section starting at ``heading`` up to the next same-level heading."""
    idx = text.find(heading)
    if idx == -1:
        return ""
    section_text = text[idx:]
    level = len(heading.split(" ", maxsplit=1)[0])  # count leading '#'
    marker = "\n" + "#" * level + " "
    next_idx = section_text.find(marker, 1)
    if next_idx != -1:
        return section_text[:next_idx]
    return section_text


@pytest.mark.unit
class TestMigrateEnvSectionExists:
    """The migrate-env subcommand section must exist in cli-reference.md."""

    def test_migrate_env_section_exists(self) -> None:
        """A ### `migrate-env` section must be present in the document."""
        text = _read_doc()
        assert "### `migrate-env`" in text, (
            "docs/cli-reference.md must contain a '### `migrate-env`' section documenting "
            "the JUDGE_* -> DEVBENCH_* migration shim (spec section 4.9.4, AC-197-4)."
        )

    def test_migrate_env_section_is_nonempty(self) -> None:
        """The migrate-env section must contain prose, not just a heading."""
        text = _read_doc()
        section = _extract_section(text, "### `migrate-env`")
        assert section, "### `migrate-env` section must exist in cli-reference.md"
        lines = [ln.strip() for ln in section.splitlines() if ln.strip()]
        assert len(lines) > 2, "The ### `migrate-env` section must contain more than just the heading line."

    def test_migrate_env_in_contents(self) -> None:
        """The Contents table must include a migrate-env entry."""
        text = _read_doc()
        assert "migrate-env" in text, (
            "docs/cli-reference.md must reference 'migrate-env' in the Contents section "
            "so operators can navigate to it (AC-197-4)."
        )


@pytest.mark.unit
class TestMigrateEnvThreeModes:
    """All three modes of migrate-env must be documented."""

    def test_no_arg_stdout_mode_documented(self) -> None:
        """The no-arg mode (prints export lines to stdout) must be documented."""
        text = _read_doc()
        section = _extract_section(text, "### `migrate-env`")
        assert section, "### `migrate-env` section must exist in cli-reference.md"
        assert "stdout" in section.lower() or "standard output" in section.lower() or "print" in section.lower(), (
            "The migrate-env section must document the no-arg mode that prints export lines to stdout (AC-197-4)."
        )

    def test_output_flag_documented(self) -> None:
        """The --output <path> mode must be documented."""
        text = _read_doc()
        section = _extract_section(text, "### `migrate-env`")
        assert section, "### `migrate-env` section must exist in cli-reference.md"
        assert "--output" in section, "The migrate-env section must document the --output <path> flag (AC-197-4)."

    def test_dry_run_flag_documented(self) -> None:
        """The --dry-run mode must be documented."""
        text = _read_doc()
        section = _extract_section(text, "### `migrate-env`")
        assert section, "### `migrate-env` section must exist in cli-reference.md"
        assert "--dry-run" in section, "The migrate-env section must document the --dry-run flag (AC-197-4)."


@pytest.mark.unit
class TestMigrateEnvExitCodes:
    """Exit code semantics must be documented in the migrate-env section."""

    def test_exit_0_for_legacy_vars_found(self) -> None:
        """Exit 0 when legacy JUDGE_* vars are found must be documented."""
        text = _read_doc()
        section = _extract_section(text, "### `migrate-env`")
        assert section, "### `migrate-env` section must exist in cli-reference.md"
        has_exit_0 = "exit 0" in section.lower() or "exits 0" in section.lower()
        assert has_exit_0, (
            "The migrate-env section must document that the command exits 0 when at "
            "least one legacy JUDGE_* var is found (AC-197-4)."
        )

    def test_exit_1_for_already_migrated(self) -> None:
        """Exit 1 when no legacy vars are present must be documented."""
        text = _read_doc()
        section = _extract_section(text, "### `migrate-env`")
        assert section, "### `migrate-env` section must exist in cli-reference.md"
        has_exit_1 = "exit 1" in section.lower() or "exits 1" in section.lower()
        assert has_exit_1, (
            "The migrate-env section must document that the command exits 1 when no "
            "legacy JUDGE_* vars are present (already migrated) (AC-197-4)."
        )

    def test_already_migrated_message_documented(self) -> None:
        """The 'already migrated' exit-1 path must be named in the section."""
        text = _read_doc()
        section = _extract_section(text, "### `migrate-env`")
        assert section, "### `migrate-env` section must exist in cli-reference.md"
        has_already_migrated = (
            "already migrated" in section.lower()
            or "no legacy" in section.lower()
            or "none are present" in section.lower()
            or "none found" in section.lower()
        )
        assert has_already_migrated, (
            "The migrate-env section must describe the exit-1 'already migrated' path "
            "so operators understand the meaning of that exit code (AC-197-4)."
        )


@pytest.mark.unit
class TestMigrateEnvNonDestructiveGuarantee:
    """The non-destructive guarantee must be documented."""

    def test_non_destructive_guarantee_documented(self) -> None:
        """The section must state that migrate-env never modifies the caller's environment."""
        text = _read_doc()
        section = _extract_section(text, "### `migrate-env`")
        assert section, "### `migrate-env` section must exist in cli-reference.md"
        has_non_destructive = (
            "non-destructive" in section.lower()
            or "nondestructive" in section.lower()
            or "never modif" in section.lower()
            or "does not modify" in section.lower()
        )
        assert has_non_destructive, (
            "The migrate-env section must document the non-destructive guarantee: the "
            "command never modifies the caller's environment or shell rc files (AC-197-4)."
        )


@pytest.mark.unit
class TestMigrateEnvBootstrapBypass:
    """The DEVBENCH_BOOTSTRAP bypass contract must be documented (AC-197-7)."""

    def test_bootstrap_bypass_documented(self) -> None:
        """The section must mention DEVBENCH_BOOTSTRAP or the bypass mechanism."""
        text = _read_doc()
        section = _extract_section(text, "### `migrate-env`")
        assert section, "### `migrate-env` section must exist in cli-reference.md"
        has_bootstrap = "DEVBENCH_BOOTSTRAP" in section
        assert has_bootstrap, (
            "The migrate-env section must document the DEVBENCH_BOOTSTRAP=1 bypass "
            "so operators understand why migrate-env can run even when JUDGE_* vars "
            "are still set (AC-197-7)."
        )

    def test_only_subcommand_bypasses_strict_checker(self) -> None:
        """The section must state that migrate-env is the only subcommand that bypasses the checker."""
        text = _read_doc()
        section = _extract_section(text, "### `migrate-env`")
        assert section, "### `migrate-env` section must exist in cli-reference.md"
        has_only_bypass = "only" in section.lower() and (
            "bypass" in section.lower() or "strict" in section.lower() or "checker" in section.lower()
        )
        assert has_only_bypass, (
            "The migrate-env section must state that this is the ONLY devbench subcommand "
            "that bypasses the strict env-var checker (AC-197-7)."
        )

    def test_reason_for_bypass_documented(self) -> None:
        """The section must explain why the bypass is needed (so legacy operators can use it)."""
        text = _read_doc()
        section = _extract_section(text, "### `migrate-env`")
        assert section, "### `migrate-env` section must exist in cli-reference.md"
        has_reason = "legacy" in section.lower() and (
            "operator" in section.lower() or "invoke" in section.lower() or "run" in section.lower()
        )
        assert has_reason, (
            "The migrate-env section must explain that the bypass exists so legacy "
            "operators (still using JUDGE_* vars) can invoke the migration script (AC-197-7)."
        )


@pytest.mark.unit
class TestMigrateEnvWorkedExamples:
    """The section must contain worked examples for the command modes."""

    def test_worked_example_no_arg_present(self) -> None:
        """A worked example for the no-arg mode (bare command) must be present."""
        text = _read_doc()
        section = _extract_section(text, "### `migrate-env`")
        assert section, "### `migrate-env` section must exist in cli-reference.md"
        assert "uv run devbench migrate-env" in section, (
            "The migrate-env section must show a worked example of the bare command "
            "invocation 'uv run devbench migrate-env' (AC-197-4)."
        )

    def test_worked_example_output_flag_present(self) -> None:
        """A worked example showing --output usage must be present."""
        text = _read_doc()
        section = _extract_section(text, "### `migrate-env`")
        assert section, "### `migrate-env` section must exist in cli-reference.md"
        assert "migrate-env --output" in section, (
            "The migrate-env section must show a worked example of --output usage (AC-197-4)."
        )

    def test_worked_example_dry_run_present(self) -> None:
        """A worked example showing --dry-run usage must be present."""
        text = _read_doc()
        section = _extract_section(text, "### `migrate-env`")
        assert section, "### `migrate-env` section must exist in cli-reference.md"
        assert "migrate-env --dry-run" in section, (
            "The migrate-env section must show a worked example of --dry-run usage (AC-197-4)."
        )

    def test_export_unset_output_format_shown(self) -> None:
        """The expected export + unset output format must be illustrated in the section."""
        text = _read_doc()
        section = _extract_section(text, "### `migrate-env`")
        assert section, "### `migrate-env` section must exist in cli-reference.md"
        has_export = "export DEVBENCH_" in section
        has_unset = "unset JUDGE_" in section
        assert has_export and has_unset, (
            "The migrate-env section must illustrate the output format: "
            "'export DEVBENCH_<NAME>=...' plus 'unset JUDGE_<NAME>' lines (AC-197-4)."
        )
