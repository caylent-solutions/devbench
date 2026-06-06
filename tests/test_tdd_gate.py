"""Tests for the deterministic TDD genuine-RED gate (tdd_gate module).

Covers:
- reject: RED exit code 0 (test did not fail before the change)
- reject: behavior fix with zero production source files changed
- pass: genuine RED (exit code non-zero, production files changed)
- pass: test-only Task Type header exempts empty production-file check
- pass: coverage-only Task Type header exempts empty production-file check
"""

from __future__ import annotations

import pytest

from devbench.tdd_gate import (
    TDD_CYCLE_MISSING_EMPTY_PROD,
    TDD_CYCLE_MISSING_ZERO_EXIT,
    TaskTypeHeader,
    TddGateResult,
    check_tdd_gate,
    extract_red_exit_code,
    extract_task_type,
    parse_production_files,
)

# ---------------------------------------------------------------------------
# Fixtures: TDD log comment fragments
# ---------------------------------------------------------------------------

_GENUINE_RED_COMMENT = (
    "- [RED] 2026-01-01T00:00:00+00:00 -- Tests: tests/test_foo.py. "
    "Command: make test-unit. Exit: 1. Failures: 3 failed, 0 passed. "
    "Output snippet: FAILED tests/test_foo.py::test_bar"
)

_FAKE_RED_ZERO_EXIT_COMMENT = (
    "- [RED] 2026-01-01T00:00:00+00:00 -- Tests: tests/test_foo.py. "
    "Command: make test-unit. Exit: 0. Failures: 0 failed, 5 passed. "
    "Output snippet: all passed"
)

_NONZERO_EXIT_COMMENT = (
    "- [RED] 2026-01-01T00:00:00+00:00 -- Tests: tests/test_x.py. "
    "Command: make test-unit. Exit: 2. Failures: 1 failed, 0 passed."
)

# ---------------------------------------------------------------------------
# Fixtures: diff output fragments
# ---------------------------------------------------------------------------

_DIFF_WITH_PROD_FILES = """\
diff --git a/src/devbench/tdd_gate.py b/src/devbench/tdd_gate.py
new file mode 100644
index 0000000..abc1234
--- /dev/null
+++ b/src/devbench/tdd_gate.py
@@ -0,0 +1,10 @@
+\"\"\"TDD gate module.\"\"\"
"""

_DIFF_WITH_ONLY_TEST_FILES = """\
diff --git a/tests/test_tdd_gate.py b/tests/test_tdd_gate.py
new file mode 100644
index 0000000..abc1234
--- /dev/null
+++ b/tests/test_tdd_gate.py
@@ -0,0 +1,5 @@
+\"\"\"Tests.\"\"\"
"""

_DIFF_WITH_MIXED_FILES = """\
diff --git a/src/devbench/tdd_gate.py b/src/devbench/tdd_gate.py
new file mode 100644
index 0000000..abc1234
--- /dev/null
+++ b/src/devbench/tdd_gate.py
@@ -0,0 +1,5 @@
+pass
diff --git a/tests/test_tdd_gate.py b/tests/test_tdd_gate.py
new file mode 100644
index 0000000..def5678
--- /dev/null
+++ b/tests/test_tdd_gate.py
@@ -0,0 +1,5 @@
+pass
"""

_DIFF_EMPTY = "(no changes)"


# ---------------------------------------------------------------------------
# Work unit content helpers
# ---------------------------------------------------------------------------

_WU_TEMPLATE = """\
# E0-F1-S1-T1: Sample Task

## Status: in-progress
{task_type_line}
## TDD Cycle Log

{tdd_entries}

## Comments
"""


def _make_wu(tdd_entries: str = "", task_type: str = "") -> str:
    task_type_line = f"\n## Task Type: {task_type}\n" if task_type else ""
    return _WU_TEMPLATE.format(task_type_line=task_type_line, tdd_entries=tdd_entries)


# ---------------------------------------------------------------------------
# Unit tests: extract_red_exit_code
# ---------------------------------------------------------------------------


class TestExtractRedExitCode:
    """Verify extraction of EXIT code from a [RED] TDD log entry."""

    def test_extracts_exit_1_from_genuine_red(self) -> None:
        result = extract_red_exit_code(_GENUINE_RED_COMMENT)
        assert result == 1

    def test_extracts_exit_0_from_fake_red(self) -> None:
        result = extract_red_exit_code(_FAKE_RED_ZERO_EXIT_COMMENT)
        assert result == 0

    def test_extracts_exit_2_from_nonzero_comment(self) -> None:
        result = extract_red_exit_code(_NONZERO_EXIT_COMMENT)
        assert result == 2

    def test_returns_none_when_no_exit_token(self) -> None:
        result = extract_red_exit_code("- [RED] 2026-01-01T00:00:00+00:00 -- no exit code here")
        assert result is None

    def test_returns_none_when_tdd_section_empty(self) -> None:
        result = extract_red_exit_code("")
        assert result is None

    def test_extracts_from_full_wu_content_with_red_entry(self) -> None:
        content = _make_wu(tdd_entries=_GENUINE_RED_COMMENT)
        result = extract_red_exit_code(content)
        assert result == 1

    def test_extracts_last_red_entry_when_multiple_present(self) -> None:
        entries = f"{_FAKE_RED_ZERO_EXIT_COMMENT}\n\n{_GENUINE_RED_COMMENT}"
        result = extract_red_exit_code(entries)
        assert result == 1


# ---------------------------------------------------------------------------
# Unit tests: parse_production_files
# ---------------------------------------------------------------------------


class TestParseProductionFiles:
    """Verify extraction of production (non-test) file paths from diff output."""

    def test_returns_empty_set_for_no_changes(self) -> None:
        result = parse_production_files(_DIFF_EMPTY)
        assert result == set()

    def test_returns_empty_set_for_test_only_diff(self) -> None:
        result = parse_production_files(_DIFF_WITH_ONLY_TEST_FILES)
        assert result == set()

    def test_returns_prod_file_from_pure_prod_diff(self) -> None:
        result = parse_production_files(_DIFF_WITH_PROD_FILES)
        assert result == {"src/devbench/tdd_gate.py"}

    def test_returns_only_prod_files_from_mixed_diff(self) -> None:
        result = parse_production_files(_DIFF_WITH_MIXED_FILES)
        assert result == {"src/devbench/tdd_gate.py"}

    def test_excludes_paths_under_tests_directory(self) -> None:
        diff = "diff --git a/tests/unit/test_foo.py b/tests/unit/test_foo.py\n"
        result = parse_production_files(diff)
        assert result == set()

    @pytest.mark.parametrize(
        "path",
        [
            "tests/test_foo.py",
            "tests/unit/test_bar.py",
            "tests/functional/test_baz.py",
            "tests/fixtures/sample.yaml",
        ],
    )
    def test_excludes_all_tests_subdirectories(self, path: str) -> None:
        diff = f"diff --git a/{path} b/{path}\n"
        result = parse_production_files(diff)
        assert result == set()

    def test_includes_non_test_path_with_test_in_name(self) -> None:
        """A file named 'test' not under tests/ is considered production."""
        diff = "diff --git a/src/devbench/test_helpers.py b/src/devbench/test_helpers.py\n"
        result = parse_production_files(diff)
        assert "src/devbench/test_helpers.py" in result


# ---------------------------------------------------------------------------
# Unit tests: extract_task_type
# ---------------------------------------------------------------------------


class TestExtractTaskType:
    """Verify extraction of ## Task Type: header from work unit content."""

    def test_returns_none_when_header_absent(self) -> None:
        content = _make_wu()
        result = extract_task_type(content)
        assert result is None

    def test_returns_test_only_for_test_only_header(self) -> None:
        content = _make_wu(task_type="test-only")
        result = extract_task_type(content)
        assert result is TaskTypeHeader.TEST_ONLY

    def test_returns_coverage_only_for_coverage_only_header(self) -> None:
        content = _make_wu(task_type="coverage-only")
        result = extract_task_type(content)
        assert result is TaskTypeHeader.COVERAGE_ONLY

    def test_raises_for_unknown_task_type_value(self) -> None:
        content = _make_wu(task_type="not-valid")
        with pytest.raises(ValueError, match=r"unknown.*Task Type.*not-valid"):
            extract_task_type(content)


# ---------------------------------------------------------------------------
# Unit tests: check_tdd_gate -- reject paths
# ---------------------------------------------------------------------------


class TestCheckTddGateRejectRedExitZero:
    """Gate rejects when the recorded RED exit code is 0."""

    def test_rejects_with_verbatim_message_when_exit_is_zero(self) -> None:
        wu_content = _make_wu(tdd_entries=_FAKE_RED_ZERO_EXIT_COMMENT)
        result = check_tdd_gate(
            wu_content=wu_content,
            diff_output=_DIFF_WITH_PROD_FILES,
        )
        assert result.passed is False
        assert result.rejection_code == "TDD_CYCLE_MISSING"
        assert result.message == TDD_CYCLE_MISSING_ZERO_EXIT

    def test_rejects_even_when_production_files_are_present(self) -> None:
        wu_content = _make_wu(tdd_entries=_FAKE_RED_ZERO_EXIT_COMMENT)
        result = check_tdd_gate(
            wu_content=wu_content,
            diff_output=_DIFF_WITH_PROD_FILES,
        )
        assert result.passed is False
        assert TDD_CYCLE_MISSING_ZERO_EXIT in result.message


class TestCheckTddGateRejectEmptyProductionFiles:
    """Gate rejects when a behavior fix changed zero production source files."""

    def test_rejects_with_verbatim_message_when_no_prod_files(self) -> None:
        wu_content = _make_wu(tdd_entries=_GENUINE_RED_COMMENT)
        result = check_tdd_gate(
            wu_content=wu_content,
            diff_output=_DIFF_WITH_ONLY_TEST_FILES,
        )
        assert result.passed is False
        assert result.rejection_code == "TDD_CYCLE_MISSING"
        assert result.message == TDD_CYCLE_MISSING_EMPTY_PROD

    def test_rejects_when_diff_is_empty(self) -> None:
        wu_content = _make_wu(tdd_entries=_GENUINE_RED_COMMENT)
        result = check_tdd_gate(
            wu_content=wu_content,
            diff_output=_DIFF_EMPTY,
        )
        assert result.passed is False
        assert result.message == TDD_CYCLE_MISSING_EMPTY_PROD


# ---------------------------------------------------------------------------
# Unit tests: check_tdd_gate -- pass paths
# ---------------------------------------------------------------------------


class TestCheckTddGatePass:
    """Gate passes for a genuine RED with production files changed."""

    def test_passes_genuine_red_with_prod_files(self) -> None:
        wu_content = _make_wu(tdd_entries=_GENUINE_RED_COMMENT)
        result = check_tdd_gate(
            wu_content=wu_content,
            diff_output=_DIFF_WITH_PROD_FILES,
        )
        assert result.passed is True
        assert result.rejection_code is None
        assert result.message == ""

    def test_passes_genuine_red_with_mixed_diff(self) -> None:
        wu_content = _make_wu(tdd_entries=_GENUINE_RED_COMMENT)
        result = check_tdd_gate(
            wu_content=wu_content,
            diff_output=_DIFF_WITH_MIXED_FILES,
        )
        assert result.passed is True

    @pytest.mark.parametrize("exit_code", [1, 2, 127])
    def test_passes_any_nonzero_exit_code(self, exit_code: int) -> None:
        comment = (
            f"- [RED] 2026-01-01T00:00:00+00:00 -- Tests: tests/test_foo.py. "
            f"Command: make test-unit. Exit: {exit_code}. Failures: 1 failed."
        )
        wu_content = _make_wu(tdd_entries=comment)
        result = check_tdd_gate(
            wu_content=wu_content,
            diff_output=_DIFF_WITH_PROD_FILES,
        )
        assert result.passed is True


# ---------------------------------------------------------------------------
# Unit tests: check_tdd_gate -- exemptions
# ---------------------------------------------------------------------------


class TestCheckTddGateExemptions:
    """test-only and coverage-only Task Type headers exempt the prod-file check."""

    @pytest.mark.parametrize("task_type", ["test-only", "coverage-only"])
    def test_exempts_empty_prod_files_for_test_and_coverage_only(self, task_type: str) -> None:
        wu_content = _make_wu(
            tdd_entries=_GENUINE_RED_COMMENT,
            task_type=task_type,
        )
        result = check_tdd_gate(
            wu_content=wu_content,
            diff_output=_DIFF_WITH_ONLY_TEST_FILES,
        )
        assert result.passed is True, f"Expected pass for task_type={task_type!r}"

    @pytest.mark.parametrize("task_type", ["test-only", "coverage-only"])
    def test_exemption_does_not_override_zero_exit_check(self, task_type: str) -> None:
        """EXIT 0 is always rejected, even for test-only/coverage-only tasks."""
        wu_content = _make_wu(
            tdd_entries=_FAKE_RED_ZERO_EXIT_COMMENT,
            task_type=task_type,
        )
        result = check_tdd_gate(
            wu_content=wu_content,
            diff_output=_DIFF_WITH_ONLY_TEST_FILES,
        )
        assert result.passed is False
        assert result.message == TDD_CYCLE_MISSING_ZERO_EXIT


# ---------------------------------------------------------------------------
# Unit tests: TddGateResult dataclass
# ---------------------------------------------------------------------------


class TestTddGateResult:
    """Verify TddGateResult structure contracts."""

    def test_passed_result_has_no_rejection_code(self) -> None:
        r = TddGateResult(passed=True, rejection_code=None, message="")
        assert r.passed is True
        assert r.rejection_code is None

    def test_failed_result_carries_code_and_message(self) -> None:
        r = TddGateResult(passed=False, rejection_code="TDD_CYCLE_MISSING", message=TDD_CYCLE_MISSING_ZERO_EXIT)
        assert r.passed is False
        assert r.rejection_code == "TDD_CYCLE_MISSING"
        assert "exit code was 0" in r.message


# ---------------------------------------------------------------------------
# Unit tests: verbatim rejection strings
# ---------------------------------------------------------------------------


class TestVerbatimRejectionStrings:
    """Confirm the rejection strings match the spec exactly."""

    def test_zero_exit_rejection_string(self) -> None:
        assert TDD_CYCLE_MISSING_ZERO_EXIT == (
            "TDD_CYCLE_MISSING: recorded RED exit code was 0 (test did not fail before the change)"
        )

    def test_empty_prod_rejection_string(self) -> None:
        assert TDD_CYCLE_MISSING_EMPTY_PROD == ("TDD_CYCLE_MISSING: behavior-fix changed zero production source files")
