"""Regression tests for the ``make validate`` coverage gate boundary.

The ``test-coverage`` Makefile target gates the coverage floor with coverage.py's
own CLI (``coverage report --fail-under=N --precision=2``) rather than
pytest-cov's ``--cov-fail-under``. pytest-cov compares the *rounded* total for
its pass/fail DECISION (``should_fail_under``) but the *raw* float for its
printed MESSAGE, so at a total that rounds up to exactly the floor (for example
97.9955% -> 98.00%) it prints ``FAIL ... not reached`` yet exits 0 -- a red
line on a green run. coverage.py's CLI uses the same rounded comparison for both
the decision and the message, so message and exit always agree.

These tests drive the real gate command against synthetic coverage data files
crafted to land exactly on the rounds-up boundary and below it, asserting that:

- at the rounds-up boundary the gate exits 0 and prints no failure line, and
- below the floor the gate exits non-zero with a failure line.

They are independent of devbench's own current coverage percentage so they stay
stable as the suite grows.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from coverage import Coverage

pytestmark = pytest.mark.unit

#: Floor used by the gate (mirrors the Makefile ``--fail-under`` value).
_FLOOR = 98
#: 440 of 449 statements covered -> 97.9955% which rounds to 98.00% at
#: precision 2: the exact "rounds up to the floor" boundary that triggered the
#: misleading pytest-cov FAIL message while the gate decision was a pass.
_TOTAL_STATEMENTS = 449
_COVERED_AT_BOUNDARY = 440
#: Well below the floor: 400 of 449 -> ~89%, an unambiguous failure.
_COVERED_BELOW = 400

#: Substrings that indicate a failure was reported in the gate output. Includes
#: both coverage.py's CLI phrasing and pytest-cov's, so the boundary test fails
#: loudly if either ever emits a failure line on a passing run.
_FAILURE_MARKERS = ("Coverage failure", "FAIL ", "not reached")


def _write_boundary_data(data_file: Path, source: Path, covered_count: int) -> None:
    """Write a synthetic coverage data file covering *covered_count* statements.

    *source* is a generated module of ``_TOTAL_STATEMENTS`` one-line statements;
    the data file records the first *covered_count* of those lines as executed.
    """
    statements = "".join(f"x{i} = {i}\n" for i in range(_TOTAL_STATEMENTS))
    source.write_text(statements, encoding="utf-8")
    cov = Coverage(data_file=str(data_file))
    data = cov.get_data()
    data.add_lines({str(source.resolve()): list(range(1, covered_count + 1))})
    data.write()


def _run_gate(data_file: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run the exact gate command the Makefile uses against *data_file*."""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "report",
            f"--data-file={data_file}",
            f"--fail-under={_FLOOR}",
            "--precision=2",
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ},
    )


def test_gate_at_rounds_up_boundary_passes_with_no_failure_line(tmp_path: Path) -> None:
    """A total that rounds up to exactly the floor exits 0 and prints no failure line."""
    source = tmp_path / "boundary_module.py"
    data_file = tmp_path / ".coverage_boundary"
    _write_boundary_data(data_file, source, _COVERED_AT_BOUNDARY)

    result = _run_gate(data_file, tmp_path)
    combined = result.stdout + result.stderr

    # The displayed total is exactly the floor at precision 2.
    assert "98.00%" in combined
    # Decision: pass.
    assert result.returncode == 0
    # Message: no failure line, so the printed output matches the exit code.
    for marker in _FAILURE_MARKERS:
        assert marker not in combined


def test_gate_below_floor_fails_with_nonzero_exit(tmp_path: Path) -> None:
    """A total below the floor exits non-zero and reports the failure."""
    source = tmp_path / "below_module.py"
    data_file = tmp_path / ".coverage_below"
    _write_boundary_data(data_file, source, _COVERED_BELOW)

    result = _run_gate(data_file, tmp_path)
    combined = result.stdout + result.stderr

    assert result.returncode != 0
    assert "Coverage failure" in combined
