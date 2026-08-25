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

# The squash-aware section heading SKILL.md's cross-reference must resolve
# to, pulled as a literal so this pin can't invent a heading name that has
# drifted from what docs/cross-backlog-dependencies.md actually carries.
SQUASH_AWARE_SECTION_TITLE = "Squash-aware verification (317-D02)"


def _extract_ancestry_gate_block(skill_text: str) -> str:
    """Return the '**Authoring the ancestry-gate task**' block verbatim.

    Single shared extraction so every assertion below parses one slice of
    SKILL.md rather than each test re-deriving its own span (DRY).
    """
    match = _ANCESTRY_BLOCK_RE.search(skill_text)
    assert match, "'**Authoring the ancestry-gate task**' block not found in SKILL.md -- has it been renamed/removed?"
    return match.group(0)


def _extract_dependency_ref_bullets(skill_text: str) -> str:
    """Return just the `dependency_ref`/`target_ref` bullet pair (~line 215-216).

    Scoped narrowly so a hard-coded `origin/` example elsewhere in Step 3
    (e.g. an illustrative operator invocation message) cannot be conflated
    with the generator's own dependency_ref/target_ref examples, which is
    what AC-SKILL-EXIT-005 actually governs.
    """
    match = _DEPENDENCY_REF_BULLET_RE.search(skill_text)
    assert match, "`dependency_ref`/`target_ref` bullet pair not found in SKILL.md"
    return match.group(0)


def _extract_cli_reference_check_ancestry_section(cli_reference_text: str) -> str:
    match = _CLI_REFERENCE_CHECK_ANCESTRY_RE.search(cli_reference_text)
    assert match, "'### `check-ancestry`' section not found in docs/cli-reference.md"
    return match.group(0)


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
