"""Deterministic TDD genuine-RED gate (issue #257).

Parses the ``log-tdd RED`` comment from a work-unit file and rejects
``TDD_CYCLE_MISSING`` when:

1. The recorded RED exit code is 0 (the test passed before the
   implementation was written -- not a genuine RED).
2. The diff contains zero production source files changed on a
   behavior-fix task (the implementation changed nothing outside
   ``tests/``).

Exception: when the work unit carries a ``## Task Type: test-only``
or ``## Task Type: coverage-only`` header, rule 2 is waived.  Rule 1
(exit-code check) always applies regardless of Task Type.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

# ---------------------------------------------------------------------------
# Public constants: verbatim rejection strings (spec AC-257-1)
# ---------------------------------------------------------------------------

TDD_CYCLE_MISSING_ZERO_EXIT: str = (
    "TDD_CYCLE_MISSING: recorded RED exit code was 0 (test did not fail before the change)"
)

TDD_CYCLE_MISSING_EMPTY_PROD: str = "TDD_CYCLE_MISSING: behavior-fix changed zero production source files"

# ---------------------------------------------------------------------------
# Valid Task Type header values (spec Section 4 E6.F2.S1)
# ---------------------------------------------------------------------------

_VALID_TASK_TYPES: frozenset[str] = frozenset({"test-only", "coverage-only"})

# ---------------------------------------------------------------------------
# Compiled regexes
# ---------------------------------------------------------------------------

_RED_ENTRY_RE: re.Pattern[str] = re.compile(
    r"-\s*\[RED\]\s+\S+\s+--\s+(.+)",
)

_EXIT_CODE_RE: re.Pattern[str] = re.compile(r"\bExit:\s*(\d+)")

_DIFF_FILE_RE: re.Pattern[str] = re.compile(r"^diff --git a/.+ b/(.+)$", re.MULTILINE)

_TASK_TYPE_RE: re.Pattern[str] = re.compile(r"^##\s+Task\s+Type:\s+(.+)$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Enums and result types
# ---------------------------------------------------------------------------


class TaskTypeHeader(Enum):
    """Recognised values for the ``## Task Type:`` optional header."""

    TEST_ONLY = "test-only"
    COVERAGE_ONLY = "coverage-only"


@dataclass(frozen=True)
class TddGateResult:
    """Outcome of a ``check_tdd_gate`` call.

    Attributes:
        passed: ``True`` when the gate is satisfied; ``False`` when it rejects.
        rejection_code: Code string (e.g. ``"TDD_CYCLE_MISSING"``) when
            ``passed`` is ``False``; ``None`` otherwise.
        message: Full verbatim rejection message when ``passed`` is ``False``;
            empty string otherwise.
    """

    passed: bool
    rejection_code: str | None
    message: str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_red_exit_code(text: str) -> int | None:
    """Return the exit code from the LAST ``[RED]`` TDD log entry in ``text``.

    Scans ``text`` for all ``- [RED] ... -- ...`` lines, extracts the
    ``Exit: <N>`` token from each, and returns the integer from the final
    match.  Returns ``None`` when no RED entry or no Exit token is found.

    Args:
        text: Raw text -- may be the full work-unit file, the TDD Cycle Log
            section only, or any substring containing ``[RED]`` entries.

    Returns:
        The integer exit code recorded in the last RED entry, or ``None``.
    """
    exit_code: int | None = None
    for match in _RED_ENTRY_RE.finditer(text):
        message_body = match.group(1)
        exit_match = _EXIT_CODE_RE.search(message_body)
        if exit_match is not None:
            exit_code = int(exit_match.group(1))
    return exit_code


def parse_production_files(diff_output: str) -> set[str]:
    """Return the set of production source file paths from a ``git diff`` output.

    Production files are defined as files whose path does NOT start with
    ``tests/`` (Appendix D-6 production rule).  Only the ``b/``-side path
    from ``diff --git a/... b/...`` lines is inspected.

    Args:
        diff_output: Combined output of ``devbench get-diff`` or a raw
            ``git diff``.  The sentinel ``"(no changes)"`` is handled
            gracefully by returning an empty set.

    Returns:
        Set of repo-relative paths of changed production files.
    """
    if not diff_output or diff_output.strip() == "(no changes)":
        return set()

    prod_files: set[str] = set()
    for match in _DIFF_FILE_RE.finditer(diff_output):
        path = match.group(1).strip()
        if not path.startswith("tests/"):
            prod_files.add(path)
    return prod_files


def extract_task_type(wu_content: str) -> TaskTypeHeader | None:
    """Return the ``TaskTypeHeader`` enum value from ``wu_content``, or ``None``.

    Parses the optional ``## Task Type: <value>`` header from work-unit
    Markdown content.

    Args:
        wu_content: Full text of the work-unit ``.md`` file.

    Returns:
        A ``TaskTypeHeader`` enum member when a known value is present,
        ``None`` when the header is absent.

    Raises:
        ValueError: When the header is present but carries an unknown value.
            Message format: ``"unknown Task Type value '<value>'; allowed: ..."``
    """
    match = _TASK_TYPE_RE.search(wu_content)
    if match is None:
        return None

    raw_value = match.group(1).strip()
    if raw_value not in _VALID_TASK_TYPES:
        allowed = ", ".join(sorted(_VALID_TASK_TYPES))
        raise ValueError(
            f"unknown Task Type value {raw_value!r}; allowed: {allowed}. "
            "validate-backlog will reject this work-unit file."
        )
    return TaskTypeHeader(raw_value)


def check_tdd_gate(
    wu_content: str,
    diff_output: str,
) -> TddGateResult:
    """Run the deterministic genuine-RED gate for a work unit.

    Applies two checks in order:

    1. **Exit-code check (always applied):** if the last recorded RED
       entry has ``Exit: 0``, the gate rejects with
       :data:`TDD_CYCLE_MISSING_ZERO_EXIT`.

    2. **Production-file check (waived for test-only / coverage-only):**
       if the diff contains zero production source files (files outside
       ``tests/``), the gate rejects with :data:`TDD_CYCLE_MISSING_EMPTY_PROD`
       unless the work unit carries a ``## Task Type: test-only`` or
       ``## Task Type: coverage-only`` header.

    Args:
        wu_content: Full text of the work-unit ``.md`` file, including the
            TDD Cycle Log and any optional ``## Task Type:`` header.
        diff_output: Combined output of ``devbench get-diff`` for this work
            unit (staged + unstaged + untracked hunks).

    Returns:
        A :class:`TddGateResult` with ``passed=True`` when both checks pass,
        or ``passed=False`` with the appropriate rejection code and verbatim
        message when either check fails.
    """
    # Check 1: exit code must be non-zero.
    exit_code = extract_red_exit_code(wu_content)
    if exit_code is not None and exit_code == 0:
        return TddGateResult(
            passed=False,
            rejection_code="TDD_CYCLE_MISSING",
            message=TDD_CYCLE_MISSING_ZERO_EXIT,
        )

    # Check 2: production files changed (unless exempt).
    task_type = extract_task_type(wu_content)
    is_exempt = task_type in (TaskTypeHeader.TEST_ONLY, TaskTypeHeader.COVERAGE_ONLY)

    if not is_exempt:
        prod_files = parse_production_files(diff_output)
        if not prod_files:
            return TddGateResult(
                passed=False,
                rejection_code="TDD_CYCLE_MISSING",
                message=TDD_CYCLE_MISSING_EMPTY_PROD,
            )

    return TddGateResult(passed=True, rejection_code=None, message="")
