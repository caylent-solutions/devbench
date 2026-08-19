"""Rule 28: the per-work-unit ``## Expected Output:`` declaration.

A work unit declares whether executing it is expected to produce a commit.
``commit`` (the default when the section is absent) is the pre-existing
lifecycle: commit, push, PR, CI, merge. ``none`` names a unit that verifies,
decides, or no-ops -- it records evidence in ``## Comments`` and git-ops
completes it without a commit.

Rule 28 cross-checks the declaration against the Changes Manifest at
validate-backlog time, so an authoring mistake fails before execution rather
than surfacing hours later as a blocked unit that already burned four judges.
"""

from pathlib import Path

import pytest

from devbench.backlog.manager import BacklogManager


def _make_backlog(tmp_path: Path, wu_body: str, status: str = "in-queue") -> tuple[Path, Path]:
    """Write BACKLOG.md + one Task file carrying *wu_body*."""
    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir(parents=True, exist_ok=True)
    uid = "E1-F1-S1-T1"
    (tmp_path / "BACKLOG.md").write_text(
        "# Backlog\n\n## Full Work Unit Index\n\n"
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |\n"
        "|----|-------|------|--------|--------------|------|----------|\n"
        f"| {uid} | Title | Task | {status} | None |  | `backlog/{uid}.md` |\n",
        encoding="utf-8",
    )
    wu = backlog_dir / f"{uid}.md"
    wu.write_text(wu_body, encoding="utf-8")
    return tmp_path / "BACKLOG.md", wu


def _wu(manifest_rows: str, expected_output: str | None, task_type: str = "chore") -> str:
    eo = f"\n## Expected Output: {expected_output}\n" if expected_output is not None else ""
    return (
        "# E1-F1-S1-T1: Title\n\n"
        "## Status: in-queue\n"
        f"{eo}\n"
        f"## Task Type: {task_type}\n\n"
        "## Changes Manifest\n\n"
        "| File | Change |\n|------|--------|\n"
        f"{manifest_rows}\n"
        "## Comments\n"
    )


def _errors_for(tmp_path: Path, body: str) -> list[str]:
    index, _ = _make_backlog(tmp_path, body)
    mgr = BacklogManager()
    errors: list[str] = []
    mgr._check_expected_output(mgr._parse_backlog_rows(index), tmp_path, errors)
    return errors


@pytest.mark.unit
class TestExtractExpectedOutput:
    def test_absent_section_returns_none_so_caller_applies_the_default(self):
        assert BacklogManager._extract_expected_output("# T\n\n## Status: in-queue\n") is None

    def test_value_is_normalised_for_case_and_whitespace(self):
        assert BacklogManager._extract_expected_output("## Expected Output:   NONE  \n") == "none"


@pytest.mark.unit
class TestRule28:
    def test_none_with_no_output_sentinel_passes(self, tmp_path: Path):
        errors = _errors_for(tmp_path, _wu("| `<verification-only>` | modify |\n", "none"))
        assert errors == []

    def test_none_with_a_real_path_is_rejected(self, tmp_path: Path):
        """The unit claims it produces no commit while naming a file to change."""
        errors = _errors_for(tmp_path, _wu("| `scripts/foo.py` | modify |\n", "none"))
        assert len(errors) == 1
        assert "scripts/foo.py" in errors[0]
        assert "Expected Output: none" in errors[0]

    def test_none_with_deferred_resolution_sentinel_is_rejected(self, tmp_path: Path):
        """Deferred resolution implies future real paths, so it will produce a commit."""
        rows = "| `<source-drift-fix-targets-determined-at-execution>` | modify |\n"
        errors = _errors_for(tmp_path, _wu(rows, "none"))
        assert len(errors) == 1
        assert "source-drift" in errors[0]

    def test_commit_with_a_real_path_passes(self, tmp_path: Path):
        assert _errors_for(tmp_path, _wu("| `scripts/foo.py` | modify |\n", "commit")) == []

    def test_absent_section_never_fails_a_legacy_unit(self, tmp_path: Path):
        """Backward compatibility: pre-existing backlogs declare nothing."""
        assert _errors_for(tmp_path, _wu("| `<verification-only>` | modify |\n", None)) == []

    def test_unrecognised_value_is_rejected_naming_the_allowed_set(self, tmp_path: Path):
        errors = _errors_for(tmp_path, _wu("| `<verification-only>` | modify |\n", "maybe"))
        assert len(errors) == 1
        assert "commit" in errors[0] and "none" in errors[0]
