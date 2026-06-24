"""Structural pins for skill and agent content against the E9 change set (AC-E9-2).

Verifies:
- Model examples across skills and agents use claude-opus-4-8 (not 4.7).
- Required new-capability text is present in each enumerated file.
- The stale-reference grep over plugin*/**/*.md returns zero non-historical hits.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent


CONFIGURE_DEVBENCH_SKILL = (
    REPO_ROOT / "plugin-authoring" / "devbench-authoring" / "skills" / "configure-devbench" / "SKILL.md"
)
SPEC_TO_BACKLOG_SKILL = (
    REPO_ROOT / "plugin-authoring" / "devbench-authoring" / "skills" / "spec-to-backlog" / "SKILL.md"
)
CREATE_SPEC_SKILL = REPO_ROOT / "plugin-authoring" / "devbench-authoring" / "skills" / "create-spec" / "SKILL.md"
ORCHESTRATE_SKILL = REPO_ROOT / "plugin" / "devbench-orchestrate" / "skills" / "orchestrate" / "SKILL.md"
TEST_REVIEWER_AGENT = REPO_ROOT / "plugin" / "devbench-orchestrate" / "agents" / "review_team" / "test-reviewer.md"
REVIEW_SUPERVISOR_AGENT = REPO_ROOT / "plugin" / "devbench-orchestrate" / "agents" / "review-supervisor.md"
SECURITY_REVIEWER_AGENT = REPO_ROOT / "plugin" / "devbench-orchestrate" / "agents" / "security-reviewer.md"


def _read(path: Path) -> str:
    assert path.is_file(), f"Required skill/agent file does not exist: {path}"
    return path.read_text(encoding="utf-8")


@pytest.mark.unit
class TestModelExamplesAreOpus48:
    """AC-E9-2a: every skill/agent that has a model-id example uses claude-opus-4-8."""

    def test_configure_devbench_uses_4_8_not_4_7(self) -> None:
        text = _read(CONFIGURE_DEVBENCH_SKILL)
        assert "claude-opus-4-7" not in text, (
            "configure-devbench SKILL.md must not reference claude-opus-4-7 as a model example; "
            "update to claude-opus-4-8 (issue #254)."
        )
        assert "claude-opus-4-8" in text, (
            "configure-devbench SKILL.md must contain at least one claude-opus-4-8 model example "
            "(issue #254, AC-E9-2a)."
        )

    def test_spec_to_backlog_no_stale_model_ref(self) -> None:
        text = _read(SPEC_TO_BACKLOG_SKILL)
        assert "claude-opus-4-7" not in text, (
            "spec-to-backlog SKILL.md must not reference claude-opus-4-7; "
            "if a model id example is needed, use claude-opus-4-8 (issue #254)."
        )

    def test_create_spec_no_stale_model_ref(self) -> None:
        text = _read(CREATE_SPEC_SKILL)
        assert "claude-opus-4-7" not in text, (
            "create-spec SKILL.md must not reference claude-opus-4-7; "
            "if a model id example is needed, use claude-opus-4-8 (issue #254)."
        )

    def test_orchestrate_no_stale_model_ref(self) -> None:
        text = _read(ORCHESTRATE_SKILL)
        assert "claude-opus-4-7" not in text, (
            "orchestrate SKILL.md must not reference claude-opus-4-7; "
            "if a model id example is needed, use claude-opus-4-8 (issue #254)."
        )

    def test_test_reviewer_no_stale_model_ref(self) -> None:
        text = _read(TEST_REVIEWER_AGENT)
        assert "claude-opus-4-7" not in text, "test-reviewer.md must not reference claude-opus-4-7 (issue #254)."

    def test_review_supervisor_no_stale_model_ref(self) -> None:
        text = _read(REVIEW_SUPERVISOR_AGENT)
        assert "claude-opus-4-7" not in text, "review-supervisor.md must not reference claude-opus-4-7 (issue #254)."

    def test_security_reviewer_no_stale_model_ref(self) -> None:
        text = _read(SECURITY_REVIEWER_AGENT)
        assert "claude-opus-4-7" not in text, "security-reviewer.md must not reference claude-opus-4-7 (issue #254)."


@pytest.mark.unit
class TestConfigureDevbenchCapabilities:
    """Required new-capability text in configure-devbench SKILL.md."""

    def test_quota_handling_section_present(self) -> None:
        """Issue #236: configure-devbench must document the quota_handling section."""
        text = _read(CONFIGURE_DEVBENCH_SKILL)
        assert "quota_handling" in text and ("enabled" in text), (
            "configure-devbench SKILL.md must include a quota_handling section with field documentation (issue #236)."
        )

    def test_quota_handling_step_documents_on_exhaustion(self) -> None:
        """The quota_handling block must explain on_exhaustion."""
        text = _read(CONFIGURE_DEVBENCH_SKILL)
        assert "on_exhaustion" in text, (
            "configure-devbench SKILL.md must document the quota_handling.on_exhaustion field (issue #236)."
        )

    def test_task_factory_default_true_documented(self) -> None:
        """Issue #259: task_factory section must document enabled with default: true."""
        text = _read(CONFIGURE_DEVBENCH_SKILL)
        assert "task_factory section" in text or "task_factory" in text, (
            "configure-devbench SKILL.md must contain a task_factory section (issue #259)."
        )
        step_marker = "## Step 8 -- task_factory section"
        assert step_marker in text, (
            "configure-devbench SKILL.md must have a '## Step 8 -- task_factory section' heading (issue #259)."
        )
        step_start = text.find(step_marker)
        next_section = text.find("\n## ", step_start + len(step_marker))
        step_text = text[step_start:next_section] if next_section != -1 else text[step_start:]
        assert "default: true" in step_text or "[default: true]" in step_text or "default true" in step_text.lower(), (
            "configure-devbench SKILL.md task_factory section (Step 8) must show "
            "'default: true' for the enabled field (issue #259)."
        )

    def test_emit_all_sections_documented(self) -> None:
        """Issue #260: Step 20 must state that ALL sections are emitted in the final YAML."""
        text = _read(CONFIGURE_DEVBENCH_SKILL)
        assert (
            "emit" in text.lower()
            or "all sections" in text.lower()
            or "every section" in text.lower()
            or ("present in the emitted" in text)
        ), (
            "configure-devbench SKILL.md Step 20 must state that every section is present in "
            "the emitted YAML (issue #260, AC-260-1)."
        )


@pytest.mark.unit
class TestSpecToBacklogCapabilities:
    """Required new-capability text in spec-to-backlog SKILL.md (issue #240b)."""

    def test_c1_target_repo_resolves_rubric_present(self) -> None:
        """AC-240b-1 C1: Target repo resolves rubric item must be present."""
        text = _read(SPEC_TO_BACKLOG_SKILL)
        assert "C1" in text or "Target repo resolves" in text or "AC-240b-1" in text, (
            "spec-to-backlog SKILL.md must document the C1 impossibility rubric item "
            "(Target repo resolves) from issue #240b."
        )

    def test_c3_manifest_multi_repo_rubric_present(self) -> None:
        """AC-240b-1 C3: Manifest multi-repo prefixes resolve rubric item must be present."""
        text = _read(SPEC_TO_BACKLOG_SKILL)
        assert "C3" in text or "multi-repo prefix" in text or "AC-240b-1" in text, (
            "spec-to-backlog SKILL.md must document the C3 impossibility rubric item "
            "(Manifest multi-repo prefixes resolve) from issue #240b."
        )

    def test_c6_title_matches_index_rubric_present(self) -> None:
        """AC-240b-1 C6: Title matches index rubric item must be present."""
        text = _read(SPEC_TO_BACKLOG_SKILL)
        assert "C6" in text or "Title matches index" in text, (
            "spec-to-backlog SKILL.md must document the C6 impossibility rubric item "
            "(Title matches index) from issue #240b."
        )

    def test_c7_canonical_path_shape_rubric_present(self) -> None:
        """AC-240b-1 C7: Canonical path shape rubric item must be present."""
        text = _read(SPEC_TO_BACKLOG_SKILL)
        assert "C7" in text or "Canonical path shape" in text, (
            "spec-to-backlog SKILL.md must document the C7 impossibility rubric item "
            "(Canonical path shape) from issue #240b."
        )


@pytest.mark.unit
class TestCreateSpecCapabilities:
    """Required new-capability text in create-spec SKILL.md."""

    def test_headless_mode_documented(self) -> None:
        """Issue #256: create-spec must document the headless --answers-file mode."""
        text = _read(CREATE_SPEC_SKILL)
        assert "headless" in text.lower() or "--answers-file" in text, (
            "create-spec SKILL.md must document the headless --answers-file mode (issue #256)."
        )

    def test_answers_file_schema_documented(self) -> None:
        """Issue #256: the answers-file YAML schema must be described."""
        text = _read(CREATE_SPEC_SKILL)
        assert "answers" in text.lower() and "yaml" in text.lower(), (
            "create-spec SKILL.md must document the YAML answers-file schema (issue #256)."
        )


@pytest.mark.unit
class TestOrchestrateSkillCapabilities:
    """Required new-capability text in orchestrate SKILL.md."""

    def test_quota_integration_documented(self) -> None:
        """Issue #236: orchestrate SKILL.md must reference quota wait-and-resume."""
        text = _read(ORCHESTRATE_SKILL)
        assert "quota" in text.lower() or "QUOTA" in text, (
            "orchestrate SKILL.md must document quota wait-and-resume integration (issue #236)."
        )

    def test_operator_mode_handoff_documented(self) -> None:
        """Issue #242: orchestrate SKILL.md must document operator-mode amendment handoff."""
        text = _read(ORCHESTRATE_SKILL)
        assert "operator.mode" in text or "operator_mode" in text or "operator-mode" in text, (
            "orchestrate SKILL.md must document the operator-mode amendment handoff (issue #242)."
        )

    def test_backlog_assistant_handoff_documented(self) -> None:
        """Issue #246: orchestrate SKILL.md must document backlog-assistant handoff."""
        text = _read(ORCHESTRATE_SKILL)
        assert "backlog.assistant" in text or "backlog_assistant" in text or "backlog-assistant" in text, (
            "orchestrate SKILL.md must document the backlog-assistant handoff (issue #246)."
        )

    def test_per_round_token_documented(self) -> None:
        """H3/ADR-29: orchestrate SKILL.md must document the file-based per-round token."""
        text = _read(ORCHESTRATE_SKILL)
        assert "review-token" in text and "review-round-token" in text, (
            "orchestrate SKILL.md must document the file-based per-round token (H3/ADR-29): "
            "the 'review-token' CLI verb and the .devbench/review-round-token file."
        )

    def test_h4_fail_closed_self_check_documented(self) -> None:
        """H4: orchestrate SKILL.md must document H4 fail-closed self-check."""
        text = _read(ORCHESTRATE_SKILL)
        assert "H4" in text or "fail-closed" in text or "fail closed" in text, (
            "orchestrate SKILL.md must document the H4 fail-closed self-check."
        )


@pytest.mark.unit
class TestTestReviewerCapabilities:
    """Required new-capability text in test-reviewer.md (issue #257)."""

    def test_genuine_red_gate_documented(self) -> None:
        """Issue #257: test-reviewer must document the deterministic genuine-RED gate."""
        text = _read(TEST_REVIEWER_AGENT)
        assert (
            "tdd_gate" in text.lower()
            or "TDD_GATE" in text
            or "genuine-RED" in text
            or ("deterministic" in text and "gate" in text.lower())
        ), "test-reviewer.md must document the deterministic genuine-RED gate (issue #257)."

    def test_tdd_gate_command_present(self) -> None:
        """Issue #257: the gate command must appear in test-reviewer.md."""
        text = _read(TEST_REVIEWER_AGENT)
        assert "check_tdd_gate" in text or "tdd_gate" in text.lower(), (
            "test-reviewer.md must include the check_tdd_gate invocation (issue #257)."
        )


@pytest.mark.unit
class TestReviewSupervisorCapabilities:
    """Required new-capability text in review-supervisor.md (H3)."""

    def test_supervisor_deprecated_documented(self) -> None:
        """ADR-28: review-supervisor is a deprecated inert stub; the review fan-out + per-round
        token now live in the orchestrate skill, so the supervisor no longer carries a live token
        requirement -- it must instead declare its deprecation."""
        text = _read(REVIEW_SUPERVISOR_AGENT)
        assert "ADR-28" in text and ("deprecated" in text.lower()), (
            "review-supervisor.md must declare itself deprecated (ADR-28); the round-token "
            "requirement moved to the orchestrate skill + the four review_team reviewers."
        )


@pytest.mark.unit
class TestSecurityReviewerCapabilities:
    """Required new-capability text in security-reviewer.md (H3)."""

    def test_round_token_requirement_documented(self) -> None:
        """H3/ADR-29: security-reviewer must document the file-based per-round token requirement."""
        text = _read(SECURITY_REVIEWER_AGENT)
        assert "review-round-token" in text or "review-token" in text, (
            "security-reviewer.md must document the file-based per-round token requirement (H3/ADR-29)."
        )


@pytest.mark.unit
class TestStaleReferenceGrep:
    """AC-E9-2: the stale-reference grep must return zero non-historical hits."""

    _STALE_PATTERN = re.compile(r"claude-opus-4-7|opt-in", re.IGNORECASE)

    _HISTORICAL_MARKERS = (
        "pr_review_resolution",
        "DEVBENCH_PR_REVIEW_RESOLUTION_ENABLED",
        "issue #116",
        "previous model",
        "4.7 row",
        "Claude Opus 4.7",
        "Opus 4.7",
    )

    def _iter_plugin_md_files(self):
        for pattern in ("plugin/**/*.md", "plugin-authoring/**/*.md"):
            yield from REPO_ROOT.glob(pattern)

    def test_no_stale_claude_4_7_refs(self) -> None:
        """No non-historical claude-opus-4-7 references in plugin*/**/*.md."""
        hits: list[str] = []
        for md_file in self._iter_plugin_md_files():
            text = md_file.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if re.search(r"claude-opus-4-7", line, re.IGNORECASE):
                    is_historical = any(marker in line for marker in self._HISTORICAL_MARKERS)
                    if not is_historical:
                        rel = md_file.relative_to(REPO_ROOT)
                        hits.append(f"{rel}:{lineno}: {line.strip()}")
        assert not hits, (
            "Stale claude-opus-4-7 references found in plugin*/**/*.md "
            "(non-historical; update to claude-opus-4-8 or mark as historical):\n" + "\n".join(hits)
        )

    def test_no_stale_opt_in_refs(self) -> None:
        """No non-historical 'opt-in' references in plugin*/**/*.md."""
        hits: list[str] = []
        for md_file in self._iter_plugin_md_files():
            text = md_file.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if re.search(r"\bopt-in\b", line, re.IGNORECASE):
                    is_historical = any(marker in line for marker in self._HISTORICAL_MARKERS)
                    if not is_historical:
                        rel = md_file.relative_to(REPO_ROOT)
                        hits.append(f"{rel}:{lineno}: {line.strip()}")
        assert not hits, (
            "Stale 'opt-in' references found in plugin*/**/*.md "
            "(non-historical; use descriptive wording instead):\n" + "\n".join(hits)
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [
        CONFIGURE_DEVBENCH_SKILL,
        SPEC_TO_BACKLOG_SKILL,
        CREATE_SPEC_SKILL,
        ORCHESTRATE_SKILL,
        TEST_REVIEWER_AGENT,
        REVIEW_SUPERVISOR_AGENT,
        SECURITY_REVIEWER_AGENT,
    ],
    ids=[
        "configure-devbench",
        "spec-to-backlog",
        "create-spec",
        "orchestrate",
        "test-reviewer",
        "review-supervisor",
        "security-reviewer",
    ],
)
def test_no_em_dash_in_skill_or_agent(path: Path) -> None:
    """Code standard: no em-dash (U+2014) in any skill or agent file."""
    text = path.read_text(encoding="utf-8")
    assert "\u2014" not in text, (
        f"{path.relative_to(REPO_ROOT)} must not contain em-dash characters (U+2014). Use '--' (double hyphen) instead."
    )
