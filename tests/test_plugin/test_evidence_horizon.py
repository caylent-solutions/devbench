"""Evidence-horizon structural pin over the review_team judge prompts (PM-6).

Systemic fact S2 (spec `integration-reality-gates-hardening.md` Section 1.3):
``read-unit --strip-comments`` removes ``## Comments`` -- and everything
after it -- before a judge ever sees a work unit (``cmd_read_unit``,
``src/devbench/cli.py``). Every review_team prompt's Evidence block runs
exactly that command as its sole source of work-unit content (``!`uv run
devbench read-unit --strip-comments $ARGUMENTS```` in each prompt's
frontmatter). Section 3.6's trust model states the rule this module pins:
"Every rubric item may reference only surfaces present in that judge's
Evidence block (PM-6 evidence-horizon rule)." A rubric line that tells a
judge to look for evidence in a section the fetch strips is unfulfillable by
construction -- the judge cannot see what was never in its own Evidence
block, findings 320-D01, 319-D4 and C-07 are three prior instances of
exactly this defect (spec 4.3).

:func:`find_evidence_horizon_violations` is the single scanner implementation
shared by the seeded-violation case, the seeded-clean case and the
shipped-prompt case (Approach step 3): a violation fires when rubric text
quotes a backtick-fenced, ``## ``-prefixed work-unit section name (the DoR
grammar, e.g. `` `## Comments` ``) whose normalized key is absent from
``RETAINED_SECTION_KEYS`` -- the level-2 section keys that survive
``read-unit --strip-comments``.

``RETAINED_SECTION_KEYS`` is derived empirically (AC-E2-F3-S1-T2-3), never a
hand-maintained duplicate list: :func:`_strip_comments_via_real_cli` runs the
actual production ``devbench.cli.cmd_read_unit`` function in-process against
the repo's own canonical work-unit schema (``backlog/templates/task.md``),
with only the backlog-index lookup mocked out, so any future change to the
strip algorithm (or to the template's section set) is picked up the next
time this module runs -- no second registration step.

The scanned surface (``_discover_review_team_prompts``) is a directory walk
over ``plugin/devbench-orchestrate/agents/review_team/*.md`` (ADR-33's
flattened review-team prompt set), not a hard-coded file list
(AC-E2-F3-S1-T2-4): a newly added judge prompt under that directory is
scanned automatically.

Source: E2-F3-S1-T2. Spec Section 1.3 S2, 3.6, 4.3; AC-10;
AC-E2-F3-S1-T2-1 through -5.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbench import cli
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType

REPO_ROOT = Path(__file__).parent.parent.parent

REVIEW_TEAM_DIR = REPO_ROOT / "plugin" / "devbench-orchestrate" / "agents" / "review_team"

# The repo's own canonical work-unit schema (AC-E2-F3-S1-T2-3): every
# generated task follows this section layout, so it is the fixture the
# retained-section set is derived from -- not a hand-typed duplicate list.
TASK_TEMPLATE_PATH = REPO_ROOT / "backlog" / "templates" / "task.md"

_FIXTURE_UNIT_ID = "EVIDENCE-HORIZON-FIXTURE"
_FIXTURE_REPO = "caylent-solutions/devbench"

# Matches a level-2 markdown heading line ("## <rest of line>").
_LEVEL2_HEADING_RE = re.compile(r"^## (.+)$", re.MULTILINE)

# DoR grammar: a backtick-fenced, "## "-prefixed work-unit section name
# quoted in rubric text (e.g. `` `## Comments` ``). Exactly two hashes then
# a space excludes a level-3 heading reference such as `` `### Approach` ``
# quoted alongside it.
_SECTION_REFERENCE_RE = re.compile(r"`(## [^`\n]+)`")


def _normalize_section_key(raw: str) -> str:
    """Normalize a work-unit section heading to its comparable key.

    Some headings carry a per-instance value after a colon on the same
    physical line (``## Status: in-queue``, ``## Task Type: test-only``);
    rubric text instead quotes the bare key with the colon but no value
    (e.g. `` `## Task Type:` ``). Both forms normalize to the same key so
    comparison never depends on which value happened to be present when
    either string was captured.
    """
    return raw.split(":", 1)[0].strip()


def _extract_level2_section_keys(markdown: str) -> frozenset[str]:
    """Return the set of level-2 (``## ``) section keys present in *markdown*."""
    return frozenset(_normalize_section_key(match.group(1)) for match in _LEVEL2_HEADING_RE.finditer(markdown))


def _strip_comments_via_real_cli(unit_id: str, wu_file: Path, repo: str) -> str:
    """Return the ``content`` field the real ``cmd_read_unit(--strip-comments)`` produces for *wu_file*.

    Invokes ``devbench.cli.cmd_read_unit`` in-process -- the exact function
    every review_team prompt's Evidence block runs via ``uv run devbench
    read-unit --strip-comments $ARGUMENTS`` -- with only the backlog-index
    lookup mocked out (``BacklogParser``, ``REPO_LOCAL_PATHS``) so the real
    strip algorithm runs against *wu_file* unmodified.
    """
    unit = WorkUnit(
        id=unit_id,
        title="Evidence horizon fixture",
        status=WorkUnitStatus.IN_PROGRESS,
        unit_type=WorkUnitType.TASK,
        file_path=wu_file,
        repo=repo,
        dependencies=[],
    )
    mock_parser = MagicMock()
    mock_parser.parse_index.return_value = [unit]

    captured = io.StringIO()
    with (
        patch("devbench.cli.BacklogParser", return_value=mock_parser),
        patch("devbench.cli.REPO_LOCAL_PATHS", {repo: wu_file.parent}),
        contextlib.redirect_stdout(captured),
    ):
        exit_code = cli.cmd_read_unit("--strip-comments", unit_id)
    assert exit_code == 0, f"cmd_read_unit --strip-comments {unit_id} exited {exit_code}, expected 0"
    payload = json.loads(captured.getvalue())
    return str(payload["content"])


def _derive_retained_section_keys() -> frozenset[str]:
    """Return the level-2 section keys that survive ``read-unit --strip-comments`` (AC-E2-F3-S1-T2-3).

    Derived empirically (Approach step 1) by feeding the repo's own
    canonical task template through the real strip algorithm, rather than a
    hand-typed list of "safe" section names that could silently drift from
    the production behaviour it is supposed to mirror.
    """
    stripped_content = _strip_comments_via_real_cli(_FIXTURE_UNIT_ID, TASK_TEMPLATE_PATH, _FIXTURE_REPO)
    return _extract_level2_section_keys(stripped_content)


RETAINED_SECTION_KEYS: frozenset[str] = _derive_retained_section_keys()


@dataclass(frozen=True)
class EvidenceHorizonViolation:
    """One rubric line that quotes a work-unit section the judge's Evidence fetch strips (S2)."""

    file: str
    line: int
    section: str
    text: str


def find_evidence_horizon_violations(
    prompt_text: str, retained_section_keys: frozenset[str], *, source: str
) -> list[EvidenceHorizonViolation]:
    """Scan *prompt_text* for a rubric line quoting a section outside the judge's Evidence horizon.

    A "work-unit section reference" (DoR grammar) is a backtick-fenced,
    ``## ``-prefixed heading name appearing anywhere in *prompt_text* (for
    example `` `## Comments` ``). Returns one :class:`EvidenceHorizonViolation`
    per occurrence whose normalized key is absent from *retained_section_keys*
    -- i.e. a section ``read-unit --strip-comments`` removes before the
    judge's Evidence block is assembled (spec 1.3 S2; PM-6 evidence-horizon
    rule, 3.6).
    """
    violations: list[EvidenceHorizonViolation] = []
    for lineno, line in enumerate(prompt_text.splitlines(), start=1):
        for match in _SECTION_REFERENCE_RE.finditer(line):
            heading_text = match.group(1)[len("## ") :]
            section = _normalize_section_key(heading_text)
            if section in retained_section_keys:
                continue
            violations.append(EvidenceHorizonViolation(file=source, line=lineno, section=section, text=line.strip()))
    return violations


def _discover_review_team_prompts() -> list[Path]:
    """Directory walk over the review_team prompts (AC-E2-F3-S1-T2-4; ADR-33's flattened prompt set)."""
    assert REVIEW_TEAM_DIR.is_dir(), f"expected review_team directory: {REVIEW_TEAM_DIR}"
    prompts = sorted(REVIEW_TEAM_DIR.glob("*.md"))
    assert prompts, f"expected at least one prompt file under {REVIEW_TEAM_DIR}"
    return prompts


@pytest.mark.unit
def test_comments_section_never_survives_the_strip() -> None:
    """Precondition (spec 1.3 S2): `## Comments` is always outside the retained horizon."""
    assert "Comments" not in RETAINED_SECTION_KEYS, (
        f"`## Comments` must never appear in the retained-section set; got: {sorted(RETAINED_SECTION_KEYS)}"
    )


@pytest.mark.unit
def test_seeded_violation_reports_prompt_line_and_section() -> None:
    """AC-E2-F3-S1-T2-1 / AC-10: a rubric line referencing `## Comments` is reported by file, line and section."""
    synthetic = (
        "## SYNTHETIC RUBRIC\n"
        "1. Some earlier, unrelated rule.\n"
        "2. Accept a documented exception recorded in the task's `## Comments` section.\n"
    )

    violations = find_evidence_horizon_violations(synthetic, RETAINED_SECTION_KEYS, source="synthetic.md")

    assert len(violations) == 1, f"expected exactly one seeded violation, got: {violations}"
    violation = violations[0]
    assert violation.file == "synthetic.md"
    assert violation.line == 3
    assert violation.section == "Comments"


@pytest.mark.unit
def test_seeded_reference_to_a_retained_section_produces_zero_findings() -> None:
    """AC-E2-F3-S1-T2-1 counterpart: a reference to a section the fetch retains is not a violation."""
    synthetic = "1. Check the work unit's `## Acceptance Criteria` for completeness.\n"
    assert "Acceptance Criteria" in RETAINED_SECTION_KEYS, (
        f"fixture precondition: 'Acceptance Criteria' must be retained; got: {sorted(RETAINED_SECTION_KEYS)}"
    )

    violations = find_evidence_horizon_violations(synthetic, RETAINED_SECTION_KEYS, source="synthetic.md")

    assert violations == [], f"a reference to a retained section must not be a violation, got: {violations}"


@pytest.mark.unit
@pytest.mark.parametrize("prompt_path", _discover_review_team_prompts(), ids=lambda p: p.name)
def test_shipped_review_team_prompt_has_no_evidence_horizon_violations(prompt_path: Path) -> None:
    """AC-E2-F3-S1-T2-2 / AC-10: every shipped review_team prompt stays within its own Evidence horizon."""
    text = prompt_path.read_text(encoding="utf-8")

    violations = find_evidence_horizon_violations(text, RETAINED_SECTION_KEYS, source=prompt_path.name)

    assert violations == [], (
        f"{prompt_path.name} quotes a work-unit section stripped by `read-unit --strip-comments` "
        f"before this judge's Evidence block is assembled (spec 1.3 S2): {violations}"
    )
