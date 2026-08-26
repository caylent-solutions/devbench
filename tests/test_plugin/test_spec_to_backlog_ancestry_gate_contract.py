"""Structural pin for E4-F1-S1-T3 (spec-to-backlog/SKILL.md ancestry-gate
template exit contract).

`plugin-authoring/devbench-authoring/skills/spec-to-backlog/SKILL.md`'s
"**Authoring the ancestry-gate task**" block generates the `### Approach`
and `AC-DEP-001` text for every ancestry-gate task `spec-to-backlog`
produces. That block previously taught a two-outcome
`check-ancestry` contract ("ancestor"/"not_ancestor", AC-DEP-001 keyed off
a bare exit 0) that a gate defaulting to disabled (D-17) also satisfies via
exit 0 -- a silent fail-open that let a backlog which never enabled
`gates.ancestry.enabled` self-certify every declared dependency as merged.

This module pins the fixed, four-outcome contract (AC-SKILL-EXIT-001/002),
confirms the retired status tokens are gone (AC-SKILL-EXIT-003), confirms
the dangling "known limitation" cross-reference now resolves to a section
that genuinely exists in `docs/cross-backlog-dependencies.md`
(AC-SKILL-EXIT-004), and confirms the `dependency_ref`/`target_ref`
examples no longer hard-code `origin/` (AC-SKILL-EXIT-005).

Every assertion reads its evidence off disk rather than hand-copying a
frozen snapshot of the template or its sources of truth:
`plugin-authoring/devbench-authoring/skills/spec-to-backlog/SKILL.md`
itself, `docs/cross-backlog-dependencies.md` (the operator-facing twin of
the same contract, already verified in sync with the shipped command by
E4-F1-S1-T1's `doc_review` pass), and `docs/cli-reference.md`'s
`` `check-ancestry` `` section (the doc-synced twin of
`src/devbench/cli.py::cmd_check_ancestry`, also verified by that same
pass). Cross-checking the pinned `status`/`mode` tokens against
`docs/cli-reference.md` -- rather than merely asserting the template's own
prose is internally consistent -- is what lets this pin actually fail if
a future edit teaches the generator tokens the shipped command does not
emit; a prior version of this module named `cli.py` as its source of
truth in this same docstring without ever reading it, so no assertion
could catch that class of drift.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from devbench.cli import _ANCESTRY_GATE_TASK_TITLE_MARKER

REPO_ROOT = Path(__file__).resolve().parents[2]

SKILL_PATH = REPO_ROOT / "plugin-authoring" / "devbench-authoring" / "skills" / "spec-to-backlog" / "SKILL.md"
CROSS_BACKLOG_DOC_PATH = REPO_ROOT / "docs" / "cross-backlog-dependencies.md"
CLI_REFERENCE_PATH = REPO_ROOT / "docs" / "cli-reference.md"

_ANCESTRY_BLOCK_RE = re.compile(
    r"\*\*Authoring the ancestry-gate task\*\*.*?(?=\n\*\*Forbidden patterns\*\*)",
    re.DOTALL,
)
_CLI_REFERENCE_CHECK_ANCESTRY_RE = re.compile(r"### `check-ancestry`.*?(?=\n### `)", re.DOTALL)
_DEPENDENCY_REF_BULLET_RE = re.compile(
    r"- `dependency_ref`.*?\n- `target_ref`.*?\n\n",
    re.DOTALL,
)

# E4-F1-S1-T2 (317-D01, 317-D23; AC-SKILL-001/AC-SKILL-002): the Step 4a
# fan-in bullet and the Step 4b/5b rubric items that check for it. Anchored
# on unique bold-lead-in text so a future rewording that keeps the
# substance intact does not silently widen or narrow the extracted span.
_STEP_4A_GATE_TASK_BULLET_RE = re.compile(
    r"- \*\*Declared work-group dependency -> mandatory gate task\*\*.*?(?=\n\n###)",
    re.DOTALL,
)
_RUBRIC_ITEM_9_RE = re.compile(r"^9\. \*\*Work-group dependency gate present\*\*.*$", re.MULTILINE)
_RUBRIC_ITEM_13_RE = re.compile(r"^13\. \*\*Ancestry gate present and fully wired\*\*.*$", re.MULTILINE)

# E4-F1-S1-T2 review round 4 (doc_review FAIL x2): the Step 4a bullet
# prescribed running `wire-gate` at a point where BACKLOG.md and the task
# files it resolves through do not exist yet (exit 1 there), and never
# documented the forced '## Status: blocked' write. This anchor pulls the
# new "Step 6a -- Wire the ancestry gate" section verbatim so the pins
# below can assert on the real execution-point statement rather than
# hand-copying a frozen snapshot.
_STEP_6A_SECTION_RE = re.compile(
    r"## Step 6a -- Wire the ancestry gate.*?(?=\n---\n\n## Step 7)",
    re.DOTALL,
)


def _extract_section(pattern: re.Pattern[str], text: str, not_found_message: str) -> str:
    """Shared regex-extraction primitive every `_extract_*` helper below
    delegates to (DRY): search `pattern` in `text`, assert a match was
    found (failing loudly with `not_found_message` otherwise -- a renamed
    or removed section must fail the pin, not silently extract an empty
    span), and return the matched span verbatim. Centralising this
    search-assert-return shape means a future extraction only needs a new
    compiled pattern plus a one-line wrapper, not a copy of this logic."""
    match = pattern.search(text)
    assert match, not_found_message
    return match.group(0)


def _extract_step_6a_section(skill_text: str) -> str:
    return _extract_section(
        _STEP_6A_SECTION_RE,
        skill_text,
        "'## Step 6a -- Wire the ancestry gate' section not found in SKILL.md",
    )


# E4-F1-S1-T4 (317-D01, 317-D23): docs/cross-backlog-dependencies.md's own
# "Special case" section is the operator-facing twin of SKILL.md's
# "**Authoring the ancestry-gate task**" block. It must be kept in sync with
# the same shipped, chore-typed, wire-gate-fanned shape rather than the
# retired hand-authored per-root wiring instruction and the retired '(none)'
# Changes Manifest placeholder. Anchored on the section's own heading through
# the next top-level heading so a future rewording that keeps the substance
# intact does not silently widen or narrow the extracted span.
_SPECIAL_CASE_SECTION_RE = re.compile(
    r"## Special case: the producer is another devbench work group's branch"
    r".*?(?=\n## The pattern: anchor a manual blocker in this backlog)",
    re.DOTALL,
)


def _extract_special_case_section(cross_backlog_doc_text: str) -> str:
    """Return the 'Special case' section verbatim (shared extraction, DRY --
    consistent with the module's existing extraction-helper convention)."""
    return _extract_section(
        _SPECIAL_CASE_SECTION_RE,
        cross_backlog_doc_text,
        "'## Special case: the producer is another devbench work group's branch' "
        "section not found in docs/cross-backlog-dependencies.md -- has it been renamed/removed?",
    )


# The squash-aware section heading SKILL.md's cross-reference must resolve
# to, pulled as a literal so this pin can't invent a heading name that has
# drifted from what docs/cross-backlog-dependencies.md actually carries.
SQUASH_AWARE_SECTION_TITLE = "Squash-aware verification (317-D02)"


def _extract_ancestry_gate_block(skill_text: str) -> str:
    """Return the '**Authoring the ancestry-gate task**' block verbatim.

    Single shared extraction so every assertion below parses one slice of
    SKILL.md rather than each test re-deriving its own span (DRY).
    """
    return _extract_section(
        _ANCESTRY_BLOCK_RE,
        skill_text,
        "'**Authoring the ancestry-gate task**' block not found in SKILL.md -- has it been renamed/removed?",
    )


def _extract_dependency_ref_bullets(skill_text: str) -> str:
    """Return just the `dependency_ref`/`target_ref` bullet pair (~line 215-216).

    Scoped narrowly so a hard-coded `origin/` example elsewhere in Step 3
    (e.g. an illustrative operator invocation message) cannot be conflated
    with the generator's own dependency_ref/target_ref examples, which is
    what AC-SKILL-EXIT-005 actually governs.
    """
    return _extract_section(
        _DEPENDENCY_REF_BULLET_RE,
        skill_text,
        "`dependency_ref`/`target_ref` bullet pair not found in SKILL.md",
    )


def _extract_cli_reference_check_ancestry_section(cli_reference_text: str) -> str:
    return _extract_section(
        _CLI_REFERENCE_CHECK_ANCESTRY_RE,
        cli_reference_text,
        "'### `check-ancestry`' section not found in docs/cli-reference.md",
    )


def _extract_step_4a_gate_task_bullet(skill_text: str) -> str:
    return _extract_section(
        _STEP_4A_GATE_TASK_BULLET_RE,
        skill_text,
        "Step 4a 'Declared work-group dependency -> mandatory gate task' bullet not found in SKILL.md",
    )


def _extract_rubric_item_9(skill_text: str) -> str:
    return _extract_section(
        _RUBRIC_ITEM_9_RE,
        skill_text,
        "Step 4b rubric item 9 ('Work-group dependency gate present') not found in SKILL.md",
    )


def _extract_rubric_item_13(skill_text: str) -> str:
    return _extract_section(
        _RUBRIC_ITEM_13_RE,
        skill_text,
        "Step 5b rubric item 13 ('Ancestry gate present and fully wired') not found in SKILL.md",
    )


@pytest.fixture(scope="module")
def skill_text() -> str:
    assert SKILL_PATH.is_file(), f"SKILL.md missing at {SKILL_PATH}"
    return SKILL_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def cross_backlog_doc_text() -> str:
    assert CROSS_BACKLOG_DOC_PATH.is_file(), f"cross-backlog-dependencies.md missing at {CROSS_BACKLOG_DOC_PATH}"
    return CROSS_BACKLOG_DOC_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def cli_reference_text() -> str:
    assert CLI_REFERENCE_PATH.is_file(), f"cli-reference.md missing at {CLI_REFERENCE_PATH}"
    return CLI_REFERENCE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ancestry_block(skill_text: str) -> str:
    return _extract_ancestry_gate_block(skill_text)


@pytest.fixture(scope="module")
def ancestry_block_unwrapped(ancestry_block: str) -> str:
    """`ancestry_block` with hard line-wraps collapsed to single spaces.

    Markdown prose in SKILL.md wraps at ~80-100 columns for readability;
    a multi-word phrase assertion must not be sensitive to exactly where a
    future edit happens to wrap a line. JSON/code-fence substrings (which
    are always authored on one physical line) are unaffected either way,
    so this fixture is used only for prose-phrase assertions.
    """
    return re.sub(r"\s+", " ", ancestry_block)


@pytest.fixture(scope="module")
def dependency_ref_bullets(skill_text: str) -> str:
    return _extract_dependency_ref_bullets(skill_text)


@pytest.fixture(scope="module")
def cli_reference_check_ancestry_section(cli_reference_text: str) -> str:
    return _extract_cli_reference_check_ancestry_section(cli_reference_text)


@pytest.fixture(scope="module")
def step_4a_gate_task_bullet(skill_text: str) -> str:
    return _extract_step_4a_gate_task_bullet(skill_text)


@pytest.fixture(scope="module")
def rubric_item_9(skill_text: str) -> str:
    return _extract_rubric_item_9(skill_text)


@pytest.fixture(scope="module")
def rubric_item_13(skill_text: str) -> str:
    return _extract_rubric_item_13(skill_text)


@pytest.fixture(scope="module")
def step_6a_section(skill_text: str) -> str:
    return _extract_step_6a_section(skill_text)


@pytest.fixture(scope="module")
def special_case_section(cross_backlog_doc_text: str) -> str:
    return _extract_special_case_section(cross_backlog_doc_text)


@pytest.mark.unit
class TestFourOutcomeExitContractDocumented:
    """AC-SKILL-EXIT-001: all four check-ancestry outcomes are documented."""

    def test_strict_and_squash_pr_modes_satisfy_ac_dep_001(self, ancestry_block: str) -> None:
        assert 'mode: "strict"' in ancestry_block
        assert 'mode: "squash-pr"' in ancestry_block
        assert "Mark AC-DEP-001 met" in ancestry_block

    def test_disabled_exit_zero_does_not_satisfy_ac_dep_001(
        self, ancestry_block: str, ancestry_block_unwrapped: str
    ) -> None:
        assert '{"gate": "ancestry", "status": "disabled"}' in ancestry_block, (
            "the ancestry-gate template must document the disabled-gate stdout payload verbatim"
        )
        assert "Do NOT mark AC-DEP-001 met" in ancestry_block_unwrapped, (
            "the ancestry-gate template must state explicitly that the disabled-gate exit 0 output "
            "does NOT satisfy AC-DEP-001 (AC-SKILL-EXIT-001, AC-SKILL-EXIT-002; D-17 gates default to disabled)"
        )
        assert "question was never asked" in ancestry_block_unwrapped

    def test_exit_1_is_blocked_or_evaluation_error(self, ancestry_block: str) -> None:
        assert "Exit 1" in ancestry_block
        assert "BLOCKED" in ancestry_block
        assert "evaluation error" in ancestry_block

    def test_exit_2_is_a_usage_error_not_a_dependency_verdict(self, ancestry_block: str) -> None:
        assert "Exit 2" in ancestry_block
        assert "usage error" in ancestry_block
        assert "not a verdict on the dependency" in ancestry_block


@pytest.mark.unit
class TestAcDep001KeysOffStatusAndModeNotBareExitCode:
    """AC-SKILL-EXIT-002: the generated AC-DEP-001 keys off status/mode."""

    def _ac_bullet(self, ancestry_block: str) -> str:
        idx = ancestry_block.find("## Acceptance Criteria")
        assert idx != -1, "'## Acceptance Criteria' bullet not found in the ancestry-gate block"
        return ancestry_block[idx : idx + 500]

    def test_ac_dep_001_bullet_requires_status_pass_and_a_real_mode(self, ancestry_block: str) -> None:
        ac_bullet = self._ac_bullet(ancestry_block)
        assert 'status: "pass"' in ac_bullet
        assert 'mode: "strict"' in ac_bullet
        assert 'mode: "squash-pr"' in ac_bullet

    def test_ac_dep_001_bullet_does_not_key_off_a_bare_exit_code(self, ancestry_block: str) -> None:
        ac_bullet = self._ac_bullet(ancestry_block)
        assert "exits 0" not in ac_bullet, (
            "AC-DEP-001's generated template still keys off a bare exit code, which a disabled "
            "gate also satisfies (the fail-open this unit exists to close)"
        )
        assert '"disabled"' in ac_bullet
        assert "does NOT satisfy this AC" in ac_bullet or "does not satisfy this AC" in ac_bullet


@pytest.mark.unit
class TestRetiredStatusTokensAbsent:
    """AC-SKILL-EXIT-003: the retired status tokens appear nowhere in the block."""

    @pytest.mark.parametrize("retired_token", ['"ancestor"', '"not_ancestor"'])
    def test_retired_token_absent_from_block(self, ancestry_block: str, retired_token: str) -> None:
        assert retired_token not in ancestry_block, (
            f"retired status token {retired_token!r} still appears in the ancestry-gate template block"
        )


@pytest.mark.unit
class TestKnownLimitationPointerReplacedWithARealSection:
    """AC-SKILL-EXIT-004: the cross-reference resolves to a section that exists."""

    def test_known_limitation_phrase_absent(self, ancestry_block: str) -> None:
        assert "known limitation" not in ancestry_block.lower()

    def test_cross_reference_names_a_section_that_actually_exists_in_the_target_doc(
        self, ancestry_block: str, cross_backlog_doc_text: str
    ) -> None:
        assert "docs/cross-backlog-dependencies.md" in ancestry_block
        assert SQUASH_AWARE_SECTION_TITLE in ancestry_block, (
            f"SKILL.md's cross-reference does not name {SQUASH_AWARE_SECTION_TITLE!r}, the section it "
            "must point readers at"
        )
        assert SQUASH_AWARE_SECTION_TITLE in cross_backlog_doc_text, (
            f"the section {SQUASH_AWARE_SECTION_TITLE!r} that SKILL.md's cross-reference promises does "
            "not exist in docs/cross-backlog-dependencies.md -- a file-existence check alone cannot "
            "catch a dangling pointer to a section that was never written"
        )

    def test_squash_aware_contract_described_with_substantive_tokens(
        self, ancestry_block: str, cross_backlog_doc_text: str
    ) -> None:
        # A bare "squash" substring check would also pass on prose stating
        # the OPPOSITE of the intended contract (e.g. the retired
        # "known limitation" wording, which also contains "squash"). Pin
        # the substantive claim instead: the replacement sentence must name
        # the squash-pr mode as the mechanism that lets the dependency
        # satisfy the gate, and that claim must be true against the doc's
        # own squash-aware section, not merely asserted by SKILL.md.
        assert 'mode: "squash-pr"' in ancestry_block
        assert "two-probe" in ancestry_block.lower()
        assert "still satisfy the gate" in ancestry_block

        section_idx = cross_backlog_doc_text.find(SQUASH_AWARE_SECTION_TITLE)
        assert section_idx != -1
        squash_section = cross_backlog_doc_text[section_idx : section_idx + 900]
        assert '`mode: "squash-pr"`' in squash_section, (
            "docs/cross-backlog-dependencies.md's squash-aware section no longer documents "
            'mode: "squash-pr" as the outcome -- SKILL.md\'s pointer would describe a contract '
            "the doc itself no longer makes"
        )
        assert "squash-merged" in squash_section and "rebased" in squash_section


@pytest.mark.unit
class TestDependencyAndTargetRefExamplesUseTheConfiguredRemote:
    """AC-SKILL-EXIT-005."""

    @pytest.mark.parametrize("hard_coded_example", ["origin/<dependency-branch>", "origin/<default-branch>"])
    def test_hard_coded_origin_example_absent(self, dependency_ref_bullets: str, hard_coded_example: str) -> None:
        assert hard_coded_example not in dependency_ref_bullets, (
            f"dependency_ref/target_ref bullets still hard-code {hard_coded_example!r} instead of the "
            "config-resolved <remote>/ prefix"
        )

    @pytest.mark.parametrize("remote_relative_example", ["<remote>/<dependency-branch>", "<remote>/<default-branch>"])
    def test_remote_relative_example_present(self, dependency_ref_bullets: str, remote_relative_example: str) -> None:
        assert remote_relative_example in dependency_ref_bullets


@pytest.mark.unit
class TestPinnedTokensMatchTheShippedCommand:
    """Cross-checks the template's status/mode tokens against
    docs/cli-reference.md's `check-ancestry` section -- the doc-synced twin
    of `src/devbench/cli.py::cmd_check_ancestry` -- so this pin fails if a
    future edit teaches the generator a token the shipped command does not
    emit, rather than only checking the template is internally consistent."""

    @pytest.mark.parametrize("token", ['"strict"', '"squash-pr"', '"disabled"', '"pass"'])
    def test_template_uses_only_tokens_the_shipped_command_actually_emits(
        self, ancestry_block: str, cli_reference_check_ancestry_section: str, token: str
    ) -> None:
        assert token in cli_reference_check_ancestry_section, (
            f"{token!r} is not documented in docs/cli-reference.md's check-ancestry section -- it is "
            "not a real shipped token for this pin to assert the template uses"
        )
        assert token in ancestry_block, (
            f"the ancestry-gate template does not use {token!r}, a token the shipped command "
            "actually emits per docs/cli-reference.md"
        )


@pytest.mark.unit
class TestGateTaskTypedChoreWithClassifiableManifestRow:
    """AC-SKILL-001 (spec 4.5, 317-D01): the ancestry-gate authoring block
    prescribes `## Task Type: chore` and a non-empty, classifiable
    `## Changes Manifest` row naming the gate report file -- not the
    retired `(none)` placeholder that left the task with zero deliverables.

    E4-F1-S1-T2 code_review round (test_review FAIL --
    COVERAGE_REGRESSION): the template changes this AC governs shipped
    with zero test coverage despite this dedicated pin module existing.
    """

    def test_task_type_chore_prescribed(self, ancestry_block: str) -> None:
        assert "## Task Type: chore" in ancestry_block, (
            "the ancestry-gate authoring block must prescribe '## Task Type: chore' "
            "(317-D01) -- an untyped task defaults to validate-backlog rule 21's "
            "RED-gated 'behavior-fix', which a check-only task can never satisfy"
        )

    def test_changes_manifest_names_the_gate_report_file(self, ancestry_block: str) -> None:
        assert "`docs/gate-reports/E0-F<N>-S1-T1-ancestry.md`" in ancestry_block, (
            "the '## Changes Manifest' bullet must name the gate report file the "
            "task's own Approach writes, so the sole Manifest row is a genuine, "
            "classifiable deliverable"
        )

    def test_retired_none_manifest_instruction_absent(self, ancestry_block: str) -> None:
        retired = "`(none)` -- this task makes no production-code changes, only runs the check"
        assert retired not in ancestry_block, (
            "the retired '(none)' Changes Manifest instruction must not be prescribed for "
            "the gate task -- spec 4.5 requires a real, classifiable deliverable row instead "
            "(317-D01: an empty Manifest deadlocked the generated task at the done transition)"
        )


@pytest.mark.unit
class TestWireGateInvocationReplacesHandAuthoredDependencyRows:
    """AC-SKILL-002 (spec 4.5, 317-D23): the SKILL no longer instructs
    hand-authoring a gate dependency row into every DAG root; it invokes
    `wire-gate <gate-task-id> --blocks-roots`, and the Step 4b / Step 5b
    rubric items that check the wiring check for that invocation instead
    of hand-typed rows."""

    _WIRE_GATE_INVOCATION = "devbench wire-gate E0-F<N>-S1-T1 --blocks-roots"
    _RETIRED_HAND_AUTHOR_INSTRUCTION = "MUST list `E0-F"

    def test_step_4a_invokes_wire_gate(self, step_4a_gate_task_bullet: str) -> None:
        assert self._WIRE_GATE_INVOCATION in step_4a_gate_task_bullet, (
            "Step 4a's gate-task bullet must instruct fanning the gate in via "
            f"'{self._WIRE_GATE_INVOCATION}' rather than hand-authoring a Dependencies "
            "row on every DAG root (317-D23)"
        )

    def test_rubric_item_9_checks_wire_gate_invocation(self, rubric_item_9: str) -> None:
        assert self._WIRE_GATE_INVOCATION in rubric_item_9, (
            "Step 4b rubric item 9 must check for the wire-gate invocation, not a hand-authored Dependencies row"
        )

    def test_rubric_item_13_checks_wire_gate_invocation(self, rubric_item_13: str) -> None:
        assert self._WIRE_GATE_INVOCATION in rubric_item_13, (
            "Step 5b rubric item 13 must check for the wire-gate invocation, not a hand-authored Dependencies row"
        )

    def test_retired_hand_authoring_instruction_absent_from_skill(self, skill_text: str) -> None:
        assert self._RETIRED_HAND_AUTHOR_INSTRUCTION not in skill_text, (
            "the retired 'MUST list `E0-F<N>-S1-T1` in its ## Dependencies table' "
            "hand-authoring instruction must not appear anywhere in SKILL.md -- every "
            "site must invoke wire-gate instead (317-D23, complete replacement)"
        )


@pytest.mark.unit
class TestForwardDependenciesTableIsMachineRead:
    """E4-F1-S1-T2 review round 9 (doc_review FAIL, DOC_SYNC): the
    `### Depends On This` bullet previously claimed '## Dependencies` /
    `### Depends On This` are a documentation-only pair with no CLI
    reader', which is false for the forward `## Dependencies` table --
    `BacklogManager._extract_dep_ids` feeds it into the orchestrator's
    dependency graph and `validate-backlog` rule 17's
    `_check_dep_id_format` rejects malformed rows in it -- and which
    contradicted the SAME file's Step 4a bullet, rubric item 13, and the
    'canonical row shape validate-backlog reads' phrase a few lines below
    this one. Only the CONSISTENCY between the forward and reverse tables
    goes unchecked by any CLI reader, which is why the reverse table
    stays hand-authored; this class pins the corrected claim so a future
    edit cannot silently reintroduce the false 'no CLI reader' assertion
    about the forward table."""

    _RETIRED_FALSE_CLAIM = "are a documentation-only pair with no CLI reader"

    def test_retired_false_claim_absent(self, ancestry_block: str) -> None:
        assert self._RETIRED_FALSE_CLAIM not in ancestry_block, (
            "the false claim that '## Dependencies' has no CLI reader must not appear -- "
            "BacklogManager._extract_dep_ids and validate-backlog rule 17's "
            "_check_dep_id_format both read it"
        )

    def test_forward_table_stated_as_machine_read(self, ancestry_block: str) -> None:
        assert "_extract_dep_ids" in ancestry_block, (
            "the corrected bullet must name _extract_dep_ids as evidence the forward "
            "'## Dependencies' table is machine-read"
        )
        assert "_check_dep_id_format" in ancestry_block, (
            "the corrected bullet must name validate-backlog rule 17's _check_dep_id_format "
            "as evidence the forward '## Dependencies' table is machine-read"
        )

    def test_reverse_table_consistency_check_stated_as_unread(self, ancestry_block: str) -> None:
        assert "no CLI reader cross-checks the forward" in ancestry_block, (
            "the corrected bullet must scope the 'no CLI reader' claim to the "
            "forward/reverse CONSISTENCY check, not to the forward table itself"
        )


@pytest.mark.unit
class TestApproachTemplateWritesTheGateReport:
    """The fenced `### Approach` template the gate task ships with must
    itself contain the gate-report-writing step its own `## Changes
    Manifest` row depends on -- otherwise the prescribed Approach and the
    prescribed Manifest row disagree, which is the exact 'placeholder
    invented to satisfy rule 21' AC-SKILL-001 exists to eliminate
    (doc_review FAIL, DOC_SYNC/self-contradiction)."""

    def test_approach_template_copies_status_line_into_gate_report(self, ancestry_block: str) -> None:
        assert "docs/gate-reports/E0-F<N>-S1-T1-ancestry.md" in ancestry_block
        assert "copy" in ancestry_block.lower() and "status line" in ancestry_block.lower(), (
            "the fenced Approach template must include a step that copies the printed "
            "check-ancestry status line verbatim into the gate report file named in "
            "this task's own '## Changes Manifest' row"
        )

    def test_approach_template_report_step_is_inside_the_fence(self, ancestry_block: str) -> None:
        fence_start = ancestry_block.find("````markdown")
        assert fence_start != -1, "the Approach template must be a fenced markdown block"
        fence_body = ancestry_block[fence_start:]
        assert "docs/gate-reports/E0-F<N>-S1-T1-ancestry.md" in fence_body, (
            "the gate-report-writing step must live INSIDE the fenced Approach template "
            "every generated gate task actually copies, not only in the surrounding prose"
        )


@pytest.mark.unit
class TestWireGateExecutionPointIsAfterStep6:
    """E4-F1-S1-T2 review round 4 (doc_review FAIL, API_DOCS_STALE): the
    Step 4a bullet prescribed running `wire-gate` there, but `wire-gate`
    resolves the whole backlog through `BacklogParser.parse_index` over
    `BACKLOG.md`, which does not exist until Step 6, and eagerly opens
    every indexed unit file, which is not written until Step 5. Run at
    Step 4a the command provably exits 1 (`cannot read backlog index` or
    `gate task '...' not found in backlog`) and writes nothing, while
    Step 4b rubric item 9 and Step 5b rubric item 13 asserted the
    invocation 'has been run' / 'has fanned it into every root' before
    that state was reachable. This class pins that the SKILL now states
    the real execution point explicitly (a dedicated Step 6a, after Step
    6, before Step 7) and that the two rubric items no longer assert
    completed execution at their own, earlier checkpoint."""

    def test_step_4a_declines_to_invoke_wire_gate_at_that_step(self, step_4a_gate_task_bullet: str) -> None:
        assert "do NOT invoke it at this step" in step_4a_gate_task_bullet, (
            "Step 4a must explicitly say it does not invoke wire-gate at that step -- "
            "BACKLOG.md and the task files it resolves do not exist yet"
        )
        assert "Step 6a" in step_4a_gate_task_bullet, (
            "Step 4a must point the reader at Step 6a as the real execution point"
        )
        assert "cannot read backlog index" in step_4a_gate_task_bullet, (
            "Step 4a must name the actual failure mode of running wire-gate too early"
        )

    def test_step_6a_section_exists(self, step_6a_section: str) -> None:
        assert "Wire the ancestry gate" in step_6a_section

    def test_step_6a_runs_after_step_6_and_before_step_7(self, step_6a_section: str) -> None:
        assert "after Step 6" in step_6a_section, (
            "Step 6a must state it runs after Step 6 has written/updated BACKLOG.md"
        )
        assert "before Step 7" in step_6a_section, "Step 6a must state it runs before Step 7's validate-backlog pass"
        assert "uv run devbench wire-gate E0-F<N>-S1-T1 --blocks-roots" in step_6a_section, (
            "Step 6a must contain the actual wire-gate invocation command"
        )

    def test_rubric_item_9_no_longer_asserts_wire_gate_has_already_run(self, rubric_item_9: str) -> None:
        assert "has been run" not in rubric_item_9, (
            "Step 4b rubric item 9 must not assert wire-gate has already run -- no task "
            "file exists at Step 4b, so this could never be true when the item is scored"
        )
        assert "Step 6a" in rubric_item_9, "rubric item 9 must point to Step 6a as the real execution checkpoint"

    def test_rubric_item_13_no_longer_asserts_fan_in_is_already_complete(self, rubric_item_13: str) -> None:
        assert "has fanned it into every root" not in rubric_item_13, (
            "Step 5b rubric item 13 must not assert wire-gate has already fanned the gate "
            "into every root -- BACKLOG.md does not exist at Step 5b, so this could never "
            "be true when the item is scored"
        )
        assert "Step 6a" in rubric_item_13, "rubric item 13 must point to Step 6a as the real execution checkpoint"


@pytest.mark.unit
class TestWireGateStatusSideEffectDocumented:
    """E4-F1-S1-T2 review round 4 (doc_review FAIL, undocumented side
    effect that contradicts the same document): `wire-gate` writes every
    edge through `add_dep`, which force-sets every wired root's status to
    `## Status: blocked` plus a `[BLOCKED_PENDING_PROPOSAL]` marker via
    `_block_wired_target`, unconditionally. SKILL.md never documented this
    while asserting the opposite in the draft-status banner, the Step 5a
    default-status paragraph, the Output Contract's default-status bullet
    and the Step 8 success message + its canonical `set-status` follow-up.
    This class pins that all five sites are now qualified."""

    def test_step_4a_documents_the_forced_blocked_status(self, step_4a_gate_task_bullet: str) -> None:
        assert "## Status: blocked" in step_4a_gate_task_bullet
        assert "[BLOCKED_PENDING_PROPOSAL]" in step_4a_gate_task_bullet
        assert "docs/cli-reference.md#wire-gate" in step_4a_gate_task_bullet

    def test_draft_status_banner_is_qualified(self, skill_text: str) -> None:
        idx = skill_text.find("Default status for new work units")
        assert idx != -1, "the draft-status banner was not found in SKILL.md"
        banner = skill_text[idx : idx + 700]
        assert "Exception" in banner, (
            "the top-of-file draft-status banner must carry an exception noting wire-gate "
            "force-overwrites gate-wired roots to blocked"
        )
        assert "## Status: blocked" in banner

    def test_step_5a_default_status_paragraph_is_qualified(self, skill_text: str) -> None:
        idx = skill_text.find("**Default status**: Before writing each task file")
        assert idx != -1, "the Step 5a 'Default status' paragraph was not found in SKILL.md"
        paragraph = skill_text[idx : idx + 900]
        assert "not final for a DAG-root task" in paragraph, (
            "Step 5a must state that the draft/in-queue status it writes is not final for a gate-wired DAG-root task"
        )
        assert "## Status: blocked" in paragraph

    def test_output_contract_default_status_bullet_is_qualified(self, skill_text: str) -> None:
        idx = skill_text.find("- **Default status**: `draft` for all new work units")
        assert idx != -1, "the Output Contract 'Default status' bullet was not found in SKILL.md"
        bullet = skill_text[idx : idx + 400]
        assert "EXCEPT" in bullet
        assert "## Status: blocked" in bullet

    def test_step_8_success_message_is_qualified(self, skill_text: str) -> None:
        idx = skill_text.find("Backlog written:")
        assert idx != -1, "the Step 8 success message was not found in SKILL.md"
        message = skill_text[idx : idx + 2500]
        assert "EXCEPT" in message
        assert "## Status: blocked" in message
        assert "[BLOCKED_PENDING_PROPOSAL]" in message

    def test_step_8_release_command_carries_a_caution(self, skill_text: str) -> None:
        idx = skill_text.find("Backlog written:")
        assert idx != -1, "the Step 8 success message was not found in SKILL.md"
        message = skill_text[idx : idx + 3500]
        assert "Caution" in message, (
            "the canonical 'devbench set-status --include \"E1\" in-queue' follow-up must "
            "carry a caution that it can silently clear a gate-wired root's forced block"
        )
        assert "force_status" in message


@pytest.mark.unit
class TestAncestryGateTitleMarkerPinnedToCommand:
    """code_review round-5 ADVISORY (non-blocking, no AC violated at
    review time): `src/devbench/cli.py::_ANCESTRY_GATE_TASK_TITLE_MARKER`
    is the exact literal `_is_ancestry_gate_task` matches against a
    unit's title to recognise an existing ancestry-gate task during
    `wire-gate`'s conflict detection. Nothing previously pinned that
    constant against the Title/heading bullet SKILL.md actually
    prescribes, so a future rewording of either side would silently
    downgrade a genuine conflict to 'not_root' and let `wire-gate` skip
    the root at exit 0 instead of failing loudly -- a fail-open on a
    spec-4.9 error path. This class closes that drift risk the same way
    `TestPinnedTokensMatchTheShippedCommand` and
    `TestWireGateInvocationReplacesHandAuthoredDependencyRows` already
    close the other two SKILL-vs-CLI couplings."""

    def test_title_marker_appears_in_the_prescribed_title_bullet(self, ancestry_block: str) -> None:
        assert _ANCESTRY_GATE_TASK_TITLE_MARKER in ancestry_block, (
            f"the ancestry-gate authoring block's Title/heading bullet must contain "
            f"{_ANCESTRY_GATE_TASK_TITLE_MARKER!r}, the exact literal "
            "src/devbench/cli.py::_is_ancestry_gate_task matches against a unit's title -- "
            "otherwise a retitled gate task silently degrades wire-gate's conflict "
            "detection to 'not_root' and the root is skipped at exit 0 instead of failing"
        )


@pytest.mark.unit
class TestCrossBacklogDocMirrorsWireGateTemplate:
    """AC-TEST-001 / AC-CODE-001 / AC-CODE-002 / AC-CODE-003 / AC-DOC-001
    (E4-F1-S1-T4): `docs/cross-backlog-dependencies.md`'s "Special case: the
    producer is another devbench work group's branch" section is the
    operator-facing twin of SKILL.md's "**Authoring the ancestry-gate
    task**" block (E4-F1-S1-T2). Before this unit, that section still
    prescribed the retired hand-authored per-root wiring instruction
    ("every other Task in the tree lists it in `## Dependencies`") and
    never mentioned the `chore` typing or the gate-report Manifest row --
    both of which the shipped `wire-gate` verb and generated gate task now
    require. These assertions read the section off disk and can genuinely
    fail against the pre-fix content.
    """

    _WIRE_GATE_INVOCATION = "devbench wire-gate <gate-task-id> --blocks-roots"
    _RETIRED_HAND_AUTHORED_WIRING = (
        "every other Task in the tree lists it in `## Dependencies` so no work can be claimed until it passes"
    )

    def test_task_type_chore_prescribed(self, special_case_section: str) -> None:
        assert "## Task Type: chore" in special_case_section, (
            "the 'Special case' section must prescribe '## Task Type: chore' for the "
            "generated ancestry-gate task (317-D01)"
        )

    def test_chore_typing_rationale_explained(self, special_case_section: str) -> None:
        assert "behavior-fix" in special_case_section, (
            "the section must explain that a check-only task must not inherit "
            "validate-backlog rule 21's RED-gated 'behavior-fix' default -- an "
            "untyped gate task can never produce the RED evidence that default requires"
        )
        assert "RED" in special_case_section

    def test_gate_report_manifest_row_named(self, special_case_section: str) -> None:
        assert "docs/gate-reports/" in special_case_section and "-ancestry.md" in special_case_section, (
            "the section must name a gate report file (e.g. "
            "docs/gate-reports/<this-task-id>-ancestry.md) as the generated task's "
            "sole '## Changes Manifest' deliverable, in place of the retired '(none)' placeholder"
        )

    def test_retired_none_manifest_convention_absent(self, special_case_section: str) -> None:
        assert "| (none) |" not in special_case_section, (
            "the retired '(none)' Changes Manifest table-row convention must not be "
            "prescribed for the generated ancestry-gate task -- it must name a real "
            "gate report file row instead"
        )
        assert "Manifest: `(none)`" not in special_case_section, (
            "the retired '(none)' Changes Manifest instruction must not be prescribed "
            "for the generated ancestry-gate task"
        )
        assert "retired `(none)`" in special_case_section, (
            "the section must explain that the gate-report Manifest row replaces the "
            "retired '(none)' placeholder convention, not merely omit any mention of it"
        )

    def test_approach_fence_writes_the_gate_report(self, special_case_section: str) -> None:
        fence_start = special_case_section.find("````markdown")
        assert fence_start != -1, "the '### Approach' template must be a fenced markdown block"
        fence_body = special_case_section[fence_start:]
        assert "docs/gate-reports/" in fence_body and "-ancestry.md" in fence_body, (
            "the fenced Approach template must include a final step, INSIDE the fence, "
            "that writes/copies the printed check-ancestry status line into the gate "
            "report file named in the '## Changes Manifest' row above -- otherwise the "
            "prescribed Manifest row and the prescribed Approach disagree"
        )
        assert "status line" in fence_body.lower(), (
            "the fenced Approach template's final step must reference the printed "
            "check-ancestry status line it copies into the gate report file"
        )

    def test_wire_gate_invocation_present_and_linked(self, special_case_section: str) -> None:
        assert self._WIRE_GATE_INVOCATION in special_case_section, (
            "the section must replace the retired hand-authoring instruction with the "
            f"mechanical invocation {self._WIRE_GATE_INVOCATION!r}"
        )
        assert "cli-reference.md#wire-gate" in special_case_section, (
            "the wire-gate invocation must be linked to docs/cli-reference.md#wire-gate"
        )

    def test_retired_hand_authored_wiring_instruction_absent(self, special_case_section: str) -> None:
        assert self._RETIRED_HAND_AUTHORED_WIRING not in special_case_section, (
            "the retired per-root hand-authoring instruction ('every other Task in the "
            "tree lists it in ## Dependencies so no work can be claimed until it "
            "passes') must not appear -- wire-gate fans the gate into DAG roots only, "
            "not every Task in the tree"
        )

    def test_wiring_description_scoped_to_dag_roots_not_every_task(self, special_case_section: str) -> None:
        assert "root of the intra-backlog dependency DAG" in special_case_section, (
            "the section must describe the fan-in as wiring every ROOT of the "
            "intra-backlog dependency DAG, not every Task in the tree"
        )
        assert "not every Task in the tree" in special_case_section, (
            "the section must explicitly disclaim the retired 'every Task in the tree' shape"
        )
        assert "transitively" in special_case_section, (
            "the section must state that non-root Tasks are blocked transitively "
            "through their own DAG ancestry, not via a direct gate-task dependency row"
        )


@pytest.mark.unit
class TestPinReadsArtifactsOffDisk:
    """AC-SKILL-EXIT-006: the pin module reads its artifacts off disk."""

    def test_skill_md_exists(self) -> None:
        assert SKILL_PATH.is_file()

    def test_cross_backlog_dependencies_doc_exists(self) -> None:
        assert CROSS_BACKLOG_DOC_PATH.is_file()

    def test_cli_reference_doc_exists(self) -> None:
        assert CLI_REFERENCE_PATH.is_file()

    def test_block_extraction_does_not_embed_a_copy_of_the_template(self, skill_text: str) -> None:
        # The block is derived via regex against the live file content on
        # every test run; asserting a stable anchor phrase confirms the
        # extraction is reading the real file rather than a frozen literal.
        assert "**Authoring the ancestry-gate task**" in skill_text
