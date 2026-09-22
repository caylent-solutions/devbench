"""G3 blocking-vocabulary truthfulness pin (spec `integration-reality-gates-hardening.md`
section 4.2, G3; AC-E2-F2-S2-T1-1 through -6; AC-8).

The overclaim class this test guards against: prose in a judge prompt, a SKILL
file, or a gate doc that tells a judge a gate "blocks" a unit, that a unit
"cannot be marked done", or that a gate "enforces" something, when that
gate's declared tier (``devbench.constants.GATE_TIERS``) is NOT
``machine-blocking``. A machine-blocking gate genuinely blocks ``mark-done``
(``BacklogManager._check_gate_pass_done_invariant``, E2-F2-S1-T2) and may
truthfully use this vocabulary; a judge-evidence gate only informs a judge's
own verdict and must not be described as if it mechanically enforces
anything (spec Section 3.5, G3).

:func:`scan_for_blocking_vocabulary_violations` is the single scanner
implementation shared by the seeded-violation test, the machine-blocking-
acceptance test, and the shipped-tree regression test (Approach step 6):
a violation fires when a declared gate name (``constants.GATE_NAMES``) and a
blocking-vocabulary phrase (``BLOCKING_VOCABULARY_PATTERNS``, a named
constant -- not an inline literal, PM-3) co-occur on the same source line,
AND that gate's tier -- looked up directly from ``constants.GATE_TIERS``,
the single declared tier mapping, never a second hand-maintained list
(AC-E2-F2-S2-T1-4) -- is not ``machine-blocking``.

The scanned surface (``SCANNED_DIRECTORIES``) is expressed as a directory
walk, not a hard-coded file list, per spec 4.2: "scans all judge prompts,
SKILL files and gate docs" --

- ``plugin/devbench-orchestrate/agents/`` -- the review-team prompts
  (``review_team/*.md``) and the executor prompt (``executor.md``), plus the
  remaining single-purpose agent prompts, all clean of gate-tier overclaims
  as of this task.
- ``plugin/devbench-orchestrate/skills/`` -- the SKILL files.
- ``docs/`` -- the gate reference docs (``cli-reference.md``,
  ``devbench-yaml-reference.md``, and any future gate doc).

AC-E2-F2-S2-T1-5 (spec Section 0.2): every judge-evidence gate rubric in the
three swept prompts (``code-reviewer.md``'s BUG-FIX COMPLETENESS,
``test-reviewer.md``'s COMPOSITION-ROOT and LAYOUT rubrics, and
``executor.md``'s BUG-FIX COMPLETENESS section) states the disabled-status-
line semantics: a ``{"gate":"<name>","status":"disabled"}`` line means the
gate is not configured -- neither a pass nor a fail signal.

Source: E2-F2-S2-T1. Spec Section 0.2, 3.5, 3.6, 4.2, G3; AC-8;
AC-E2-F2-S2-T1-1 through -6.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from devbench.constants import GATE_NAMES, GATE_TIER_MACHINE_BLOCKING, GATE_TIERS

REPO_ROOT = Path(__file__).parent.parent.parent

AGENTS_DIR = REPO_ROOT / "plugin" / "devbench-orchestrate" / "agents"
SKILLS_DIR = REPO_ROOT / "plugin" / "devbench-orchestrate" / "skills"
DOCS_DIR = REPO_ROOT / "docs"

CODE_REVIEWER_PROMPT = AGENTS_DIR / "review_team" / "code-reviewer.md"
TEST_REVIEWER_PROMPT = AGENTS_DIR / "review_team" / "test-reviewer.md"
EXECUTOR_PROMPT = AGENTS_DIR / "executor.md"

# Named constant (AC-E2-F2-S2-T1-4): the directories walked for blocking-
# vocabulary overclaims. A directory walk rather than a hard-coded file list
# means a new judge prompt, SKILL file, or gate doc added under any of these
# roots is scanned automatically, with no second registration step.
SCANNED_DIRECTORIES: tuple[Path, ...] = (AGENTS_DIR, SKILLS_DIR, DOCS_DIR)

# Named constant (AC-E2-F2-S2-T1-4; spec G3): the blocking-vocabulary phrase
# list -- "blocks", "cannot be marked done", "enforces" and their
# morphological siblings. Each entry is a regex matched case-insensitively
# against a single scanned line.
BLOCKING_VOCABULARY_PATTERNS: tuple[str, ...] = (
    r"\bblocks\b",
    r"\bblocking\b",
    r"\bblocked\b",
    r"cannot be marked done",
    r"\benforces\b",
    r"\benforced\b",
    r"\benforcing\b",
)

_BLOCKING_VOCABULARY_RE = re.compile("|".join(BLOCKING_VOCABULARY_PATTERNS), re.IGNORECASE)


def _gate_name_pattern(gate: str) -> re.Pattern[str]:
    """Build a whole-token matcher for *gate* that also matches its hyphenated/spaced form.

    ``devbench.constants.GATE_NAMES`` entries are snake_case (e.g.
    ``"composition_root"``), but prose and CLI table output alike also use
    the hyphenated display form (``composition-root``) or, occasionally, a
    plain space. All three spellings must resolve to the same gate.
    """
    parts = [re.escape(part) for part in gate.split("_")]
    token = r"[_\- ]".join(parts)
    return re.compile(rf"\b{token}\b", re.IGNORECASE)


_GATE_NAME_PATTERNS: dict[str, re.Pattern[str]] = {gate: _gate_name_pattern(gate) for gate in GATE_NAMES}

# AC-E2-F2-S2-T1-5 / spec Section 0.2: the exact phrase every judge-evidence
# gate rubric in the three swept prompts must use to describe a disabled
# gate's status line.
DISABLED_LINE_SEMANTICS_PHRASE = "neither a pass nor a fail signal"

# The judge-evidence gates that have a rubric in one of the three swept
# prompts, and which file(s) that rubric lives in (AC-E2-F2-S2-T1-5).
JUDGE_EVIDENCE_GATE_RUBRIC_LOCATIONS: dict[str, tuple[Path, ...]] = {
    "newly_reachable_paths": (CODE_REVIEWER_PROMPT, EXECUTOR_PROMPT),
    "composition_root": (TEST_REVIEWER_PROMPT,),
    "layout_geometry": (TEST_REVIEWER_PROMPT,),
    "write_path_audit": (CODE_REVIEWER_PROMPT,),
}


@dataclass(frozen=True)
class BlockingVocabularyViolation:
    """One line where a blocking-vocabulary phrase names a non-machine-blocking gate."""

    file: str
    line: int
    gate: str
    text: str


def scan_for_blocking_vocabulary_violations(text: str, *, source: str) -> list[BlockingVocabularyViolation]:
    """Scan *text* for blocking vocabulary applied to a non-machine-blocking gate (G3).

    Returns one :class:`BlockingVocabularyViolation` per (line, gate) pair
    where a declared gate name (``GATE_NAMES``) and a blocking-vocabulary
    phrase (``BLOCKING_VOCABULARY_PATTERNS``) co-occur on the same line, AND
    that gate's declared tier (``GATE_TIERS`` -- the single tier source,
    never a second list) is not ``machine-blocking`` (AC-E2-F2-S2-T1-3: a
    machine-blocking gate may legitimately use blocking vocabulary).
    """
    violations: list[BlockingVocabularyViolation] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not _BLOCKING_VOCABULARY_RE.search(line):
            continue
        for gate, pattern in _GATE_NAME_PATTERNS.items():
            if not pattern.search(line):
                continue
            if GATE_TIERS[gate] == GATE_TIER_MACHINE_BLOCKING:
                continue
            violations.append(BlockingVocabularyViolation(file=source, line=lineno, gate=gate, text=line.strip()))
    return violations


def _iter_scanned_files() -> list[Path]:
    files: list[Path] = []
    for directory in SCANNED_DIRECTORIES:
        assert directory.is_dir(), f"expected scanned directory to exist: {directory}"
        files.extend(sorted(directory.rglob("*.md")))
    assert files, "expected at least one markdown file across the scanned directories"
    return files


@pytest.mark.unit
def test_seeded_violation_reports_file_line_and_gate() -> None:
    """AC-E2-F2-S2-T1-1: a seeded overclaim about a judge-evidence gate is reported."""
    synthetic = (
        "# Synthetic prompt\n"
        "\n"
        "If the composition_root evidence is missing, this gate blocks the unit "
        "and the task cannot be marked done.\n"
    )
    assert GATE_TIERS["composition_root"] != GATE_TIER_MACHINE_BLOCKING

    violations = scan_for_blocking_vocabulary_violations(synthetic, source="synthetic.md")

    assert len(violations) == 1, f"expected exactly one seeded violation, got: {violations}"
    violation = violations[0]
    assert violation.file == "synthetic.md"
    assert violation.line == 3
    assert violation.gate == "composition_root"


@pytest.mark.unit
def test_machine_blocking_gate_accepts_blocking_vocabulary() -> None:
    """AC-E2-F2-S2-T1-3: blocking vocabulary about a machine-blocking gate is not a violation."""
    synthetic = "The reachability gate blocks mark-done until a fresh record exists.\n"
    assert GATE_TIERS["reachability"] == GATE_TIER_MACHINE_BLOCKING

    violations = scan_for_blocking_vocabulary_violations(synthetic, source="synthetic.md")

    assert violations == [], f"a machine-blocking gate may use blocking vocabulary, but got: {violations}"


@pytest.mark.unit
def test_shipped_tree_has_zero_blocking_vocabulary_violations() -> None:
    """AC-E2-F2-S2-T1-2 / AC-E2-F2-S2-T1-6: the real prompt/SKILL/doc surface carries no overclaim."""
    all_violations: list[BlockingVocabularyViolation] = []
    for path in _iter_scanned_files():
        text = path.read_text(encoding="utf-8")
        relative = str(path.relative_to(REPO_ROOT))
        all_violations.extend(scan_for_blocking_vocabulary_violations(text, source=relative))

    assert all_violations == [], (
        "shipped tree must carry zero blocking-vocabulary overclaims about a "
        f"non-machine-blocking gate (G3); found: {all_violations}"
    )


@pytest.mark.unit
def test_shipped_tree_judge_evidence_rubrics_state_disabled_line_semantics() -> None:
    """AC-E2-F2-S2-T1-5 (spec Section 0.2): every judge-evidence rubric states disabled-line semantics."""
    for gate, prompts in JUDGE_EVIDENCE_GATE_RUBRIC_LOCATIONS.items():
        assert GATE_TIERS[gate] != GATE_TIER_MACHINE_BLOCKING, f"{gate} must be judge-evidence for this pin to hold"
        gate_json_key = f'"gate":"{gate}"'
        for prompt_path in prompts:
            text = prompt_path.read_text(encoding="utf-8")
            matching_lines = [line for line in text.splitlines() if gate_json_key in line]
            assert matching_lines, (
                f"{prompt_path} must reference the disabled-status-line shape {gate_json_key!r} "
                f"for the {gate} gate (spec Section 0.2, AC-E2-F2-S2-T1-5)."
            )
            assert any(DISABLED_LINE_SEMANTICS_PHRASE in line for line in matching_lines), (
                f"{prompt_path}'s {gate_json_key!r} reference must also state "
                f"{DISABLED_LINE_SEMANTICS_PHRASE!r} (spec Section 0.2, AC-E2-F2-S2-T1-5)."
            )
