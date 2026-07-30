"""Unit tests for plugin agent directory structure."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

AGENTS_DIR = Path(__file__).parent.parent.parent / "plugin" / "devbench-orchestrate" / "agents"

REVIEW_TEAM_DIR = AGENTS_DIR / "review_team"

REVIEW_TEAM_AGENTS = [
    "code-reviewer.md",
    "test-reviewer.md",
    "doc-reviewer.md",
    "changes-manifest.md",
]


@pytest.mark.unit
class TestReviewTeamDirectory:
    """AC-1: review_team/ contains exactly the four expected reviewer agents."""

    def test_review_team_dir_exists(self) -> None:
        """review_team/ directory must exist under agents/."""
        assert REVIEW_TEAM_DIR.is_dir(), f"Expected directory not found: {REVIEW_TEAM_DIR}"

    def test_review_team_dir_contains_exactly_four_agents(self) -> None:
        """AC-1: review_team/ must contain exactly code-reviewer, test-reviewer, doc-reviewer, changes-manifest."""
        expected = {
            "code-reviewer.md",
            "test-reviewer.md",
            "doc-reviewer.md",
            "changes-manifest.md",
        }
        actual = {p.name for p in REVIEW_TEAM_DIR.glob("*.md")}
        assert actual == expected, (
            f"review_team/ contents mismatch.\n  Expected: {sorted(expected)}\n  Actual:   {sorted(actual)}"
        )


@pytest.mark.unit
class TestReviewSupervisorFrontmatter:
    """AC-2: review-supervisor.md exists with correct frontmatter."""

    _SUPERVISOR_PATH = AGENTS_DIR / "review-supervisor.md"

    def test_review_supervisor_file_exists(self) -> None:
        """review-supervisor.md must exist at agents/review-supervisor.md."""
        assert self._SUPERVISOR_PATH.exists(), f"review-supervisor.md not found at {self._SUPERVISOR_PATH}"

    def test_review_supervisor_frontmatter_valid(self) -> None:
        """AC-E4-F1-S1-T2-6: Frontmatter must contain name: review-supervisor
        and tools: Bash only -- post-flatten (ADR-33) review-supervisor is a
        non-spawning aggregator and must declare NO Agent(...) spawn capability.
        """
        content = self._SUPERVISOR_PATH.read_text()

        # Extract frontmatter block between --- delimiters
        lines = content.splitlines()
        assert lines[0].strip() == "---", "review-supervisor.md must start with --- frontmatter delimiter"

        end_idx = next(
            (i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
            None,
        )
        assert end_idx is not None, "review-supervisor.md frontmatter closing --- not found"

        frontmatter = "\n".join(lines[1:end_idx])

        assert "name: review-supervisor" in frontmatter, (
            f"Frontmatter must contain 'name: review-supervisor'. Got:\n{frontmatter}"
        )
        assert "tools:" in frontmatter, f"Frontmatter must contain a tools: field. Got:\n{frontmatter}"
        assert "Bash" in frontmatter, f"Frontmatter tools must include Bash. Got:\n{frontmatter}"
        assert "Agent(" not in frontmatter, (
            f"Frontmatter tools must NOT declare Agent(...) -- review-supervisor is a "
            f"non-spawning aggregator post-flatten (ADR-33). Got:\n{frontmatter}"
        )


@pytest.mark.unit
class TestReviewSupervisorNonSpawningAggregation:
    """AC-E4-F1-S1-T2-6: review-supervisor.md is reduced to a non-spawning
    aggregation role per ADR-33. Post-flatten it never had Agent-tool spawn
    capability in the first place, so the pre-flatten Step 0 self-check
    (which existed to detect that capability silently dropping out of the
    session) is dead prose and must be removed. Instead the agent reads the
    four review_team judges' independently-persisted verdicts and treats
    any absent required verdict as a hard failure naming the absent judge.
    """

    _SUPERVISOR_PATH = AGENTS_DIR / "review-supervisor.md"

    def test_supervisor_no_step_0_self_check_payload(self) -> None:
        """The pre-flatten 'agent-tool-unavailable' self-check payload must
        not survive -- it described detecting a capability review-supervisor
        no longer ever has, so a healthy run must never emit it again (AC-67).
        """
        content = self._SUPERVISOR_PATH.read_text()
        assert "agent-tool-unavailable" not in content, (
            "review-supervisor.md must not retain the pre-flatten Step 0 "
            "'agent-tool-unavailable' self-check payload; post-flatten the "
            "agent never has Agent-tool spawn capability to lose."
        )

    def test_supervisor_reads_persisted_verdicts_not_invoking_judges(self) -> None:
        content = self._SUPERVISOR_PATH.read_text().lower()
        assert "persisted" in content, (
            "review-supervisor.md must describe reading the judges' already-persisted "
            "verdicts rather than invoking the judges itself."
        )

    def test_supervisor_treats_missing_verdict_as_hard_failure(self) -> None:
        content = self._SUPERVISOR_PATH.read_text().lower()
        assert "missing" in content and "hard failure" in content, (
            "review-supervisor.md must state that a missing verdict from any required "
            "judge is a hard failure, never an implicit pass (AC-65)."
        )

    def test_supervisor_names_the_absent_judge(self) -> None:
        content = self._SUPERVISOR_PATH.read_text().lower()
        assert "naming every absent judge" in content or "naming the absent judge" in content, (
            "review-supervisor.md must instruct naming the absent judge(s) by canonical "
            "name, not reporting a generic unattributed failure."
        )

    def test_supervisor_boundary_scan_does_not_claim_discarding(self) -> None:
        """The Step 1 boundary description must not claim the scan discards
        everything already collected the moment it hits ``[REVIEW_REJECTED]``.

        ``BacklogManager._last_round_all_passed`` walks reversed(lines) and
        KEEPS everything collected below the boundary (the current round);
        it only stops collecting further, it does not discard what it
        already gathered. A prompt that instructs "discarding everything
        already collected" would make the aggregator report all four judges
        absent on every work unit that has any prior [REVIEW_REJECTED]
        boundary -- the common retry case -- producing a false hard failure.
        """
        content = self._SUPERVISOR_PATH.read_text().lower()
        assert "discarding everything already collected" not in content, (
            "review-supervisor.md Step 1 must not claim the scan discards everything "
            "already collected when it hits [REVIEW_REJECTED]; the manager keeps "
            "everything collected below that boundary (the current round)."
        )

    def test_supervisor_boundary_scan_states_correct_keep_semantics(self) -> None:
        """The corrected Step 1 wording must state that verdicts collected
        below the [REVIEW_REJECTED] boundary (the current round) count, and
        that only entries above it (a prior round) are excluded.
        """
        content = self._SUPERVISOR_PATH.read_text().lower()
        assert "belong" in content and "prior round" in content, (
            "review-supervisor.md Step 1 must state that entries above the "
            "[REVIEW_REJECTED] boundary belong to a prior round and are never collected, "
            "while entries below it (the current round) count."
        )


@pytest.mark.unit
class TestSecurityReviewerNotInReviewTeam:
    """AC-7: security-reviewer.md must remain at agents/ root, not inside review_team/."""

    def test_security_reviewer_not_in_review_team(self) -> None:
        """AC-7: security-reviewer.md must NOT be inside review_team/."""
        assert not (REVIEW_TEAM_DIR / "security-reviewer.md").exists(), (
            "security-reviewer.md must not be moved into review_team/"
        )

    def test_security_reviewer_at_agents_root(self) -> None:
        """AC-7: security-reviewer.md must exist at agents/ root."""
        assert (AGENTS_DIR / "security-reviewer.md").exists(), "security-reviewer.md must remain at agents/ root"


@pytest.mark.unit
class TestNoStaleFlatAgentPaths:
    """AC-9: Old flat paths for the four moved agents must not exist at agents/ root."""

    @pytest.mark.parametrize(
        "stale_filename",
        [
            "code-reviewer.md",
            "test-reviewer.md",
            "doc-reviewer.md",
            "changes-manifest.md",
        ],
    )
    def test_no_stale_flat_agent_paths_in_plugin(self, stale_filename: str) -> None:
        """AC-9: Moved agent files must not remain at the agents/ root."""
        stale_path = AGENTS_DIR / stale_filename
        assert not stale_path.exists(), (
            f"Stale flat path must be removed: {stale_path}\n"
            f"This file was moved to review_team/ and must not remain at agents/ root."
        )


@pytest.mark.unit
class TestReviewTeamModelDefault:
    """ADR-25: All four review_team agents default to model: opus (judges).

    The four review_team agents are LLM-as-judge agents whose verdicts gate
    a task's done state. A bad verdict costs more than the inference savings,
    so the frontmatter pins them to opus; operators with opus quota pressure
    can drop individual judges to sonnet via the workspace's ``agents:``
    block (ADR-25).
    """

    @pytest.mark.parametrize("agent_filename", REVIEW_TEAM_AGENTS)
    def test_review_team_agent_uses_opus_model(self, agent_filename: str) -> None:
        """ADR-25: Each review_team agent must declare model: opus in frontmatter."""
        agent_path = REVIEW_TEAM_DIR / agent_filename
        assert agent_path.exists(), f"Agent file not found: {agent_path}"
        content = agent_path.read_text()

        lines = content.splitlines()
        assert lines[0].strip() == "---", f"{agent_filename} must start with --- frontmatter delimiter"
        end_idx = next(
            (i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
            None,
        )
        assert end_idx is not None, f"{agent_filename} frontmatter closing --- not found"
        frontmatter = "\n".join(lines[1:end_idx])

        assert re.search(r"^model:\s*opus\s*$", frontmatter, re.MULTILINE), (
            f"{agent_filename} must declare 'model: opus' in frontmatter (ADR-25 default).\n"
            f"Found frontmatter:\n{frontmatter}"
        )


@pytest.mark.unit
class TestReviewSupervisorVerdictFormat:
    """AC-4, AC-5: review-supervisor must use lowercase pass/fail in log-verdict calls."""

    _SUPERVISOR_PATH = AGENTS_DIR / "review-supervisor.md"

    def test_supervisor_no_review_fail_token(self) -> None:
        """AC-4: review-supervisor must not use REVIEW_FAIL as a verdict token."""
        content = self._SUPERVISOR_PATH.read_text()
        assert "REVIEW_FAIL" not in content, (
            "review-supervisor.md must not use 'REVIEW_FAIL' as a verdict token. "
            "Use lowercase 'fail' in log-verdict calls."
        )

    def test_supervisor_no_review_pass_token(self) -> None:
        """AC-5: review-supervisor must not use REVIEW_PASS as a verdict token."""
        content = self._SUPERVISOR_PATH.read_text()
        assert "REVIEW_PASS" not in content, (
            "review-supervisor.md must not use 'REVIEW_PASS' as a verdict token. "
            "Use lowercase 'pass' in log-verdict calls."
        )

    def test_supervisor_fail_branch_uses_lowercase_fail(self) -> None:
        """AC-4: log-verdict calls in review-supervisor must use lowercase 'fail'."""
        content = self._SUPERVISOR_PATH.read_text()
        # Should have at least one log-verdict call with lowercase 'fail'
        assert re.search(r"log-verdict\s+\S+\s+\S+\s+fail\b", content), (
            "review-supervisor.md must contain log-verdict calls using lowercase 'fail'."
        )

    def test_supervisor_pass_branch_uses_lowercase_pass(self) -> None:
        """AC-5: log-verdict calls in review-supervisor must use lowercase 'pass'."""
        content = self._SUPERVISOR_PATH.read_text()
        # Should have at least one log-verdict call with lowercase 'pass'
        assert re.search(r"log-verdict\s+\S+\s+\S+\s+pass\b", content), (
            "review-supervisor.md must contain log-verdict calls using lowercase 'pass'."
        )


@pytest.mark.unit
class TestReviewerLogCommentBeforeLogVerdict:
    """AC-1, AC-3: Reviewers must instruct agents to log-comment before log-verdict."""

    @pytest.mark.parametrize("agent_filename", REVIEW_TEAM_AGENTS)
    def test_reviewer_instructs_log_comment_before_log_verdict(self, agent_filename: str) -> None:
        """AC-1, AC-3: Each reviewer prompt must instruct log-comment before log-verdict."""
        agent_path = REVIEW_TEAM_DIR / agent_filename
        content = agent_path.read_text()

        assert "log-comment" in content, (
            f"{agent_filename} must instruct the agent to call log-comment "
            "for each finding or confirmation before logging the verdict."
        )
        assert "log-verdict" in content, f"{agent_filename} must instruct the agent to call log-verdict."

        log_comment_pos = content.find("log-comment")
        log_verdict_pos = content.find("log-verdict")
        assert log_comment_pos < log_verdict_pos, (
            f"{agent_filename} must instruct log-comment before log-verdict. "
            f"log-comment appears at pos {log_comment_pos}, "
            f"log-verdict appears at pos {log_verdict_pos}."
        )


@pytest.mark.unit
class TestReviewerJsonEnvelope:
    """AC-8: Each reviewer must instruct the agent to output a JSON envelope."""

    @pytest.mark.parametrize("agent_filename", REVIEW_TEAM_AGENTS)
    def test_reviewer_requires_json_envelope(self, agent_filename: str) -> None:
        """AC-8: Each reviewer prompt must specify the JSON envelope output format."""
        agent_path = REVIEW_TEAM_DIR / agent_filename
        content = agent_path.read_text()

        assert '"verdict"' in content, f"{agent_filename} must include JSON envelope format with 'verdict' field."
        assert '"summary"' in content, f"{agent_filename} must include JSON envelope format with 'summary' field."
        assert '"findings"' in content, f"{agent_filename} must include JSON envelope format with 'findings' array."

    @pytest.mark.parametrize("agent_filename", REVIEW_TEAM_AGENTS)
    def test_reviewer_json_envelope_is_last_output(self, agent_filename: str) -> None:
        """AC-8: Reviewer prompt must instruct agent to output JSON as last response content."""
        agent_path = REVIEW_TEAM_DIR / agent_filename
        content = agent_path.read_text()

        # Verify the prompt states the JSON envelope is the last thing output
        assert re.search(
            r"last\s+(thing|content|output)\b",
            content,
            re.IGNORECASE,
        ), f"{agent_filename} must instruct the agent that the JSON envelope is the last thing output in the response."


@pytest.mark.unit
class TestReviewSupervisorUsesJsonEnvelope:
    """AC-6, AC-9: review-supervisor must use reviewer JSON envelope data, not hardcoded strings."""

    _SUPERVISOR_PATH = AGENTS_DIR / "review-supervisor.md"

    def test_supervisor_no_hardcoded_passed_strings(self) -> None:
        """AC-9: Supervisor must not hardcode 'X passed' strings in log-verdict calls."""
        content = self._SUPERVISOR_PATH.read_text()
        hardcoded_patterns = [
            "code-reviewer passed",
            "test-reviewer passed",
            "doc-reviewer passed",
            "changes-manifest passed",
        ]
        for pattern in hardcoded_patterns:
            assert pattern not in content, (
                f"review-supervisor.md must not hardcode '{pattern}' in log-verdict calls. "
                "Use the actual reviewer JSON summary from the envelope."
            )

    def test_supervisor_references_json_envelope(self) -> None:
        """AC-6, AC-9: Supervisor must instruct parsing of reviewer JSON envelope."""
        content = self._SUPERVISOR_PATH.read_text()
        assert re.search(r"\bjson\b", content, re.IGNORECASE), (
            "review-supervisor.md must instruct parsing the reviewer JSON envelope to extract verdicts and summaries."
        )

    def test_supervisor_fail_branch_logs_findings_as_comments(self) -> None:
        """AC-6: Supervisor FAIL branch must relay individual findings via log-comment."""
        content = self._SUPERVISOR_PATH.read_text()
        assert "log-comment" in content, (
            "review-supervisor.md must use log-comment to relay reviewer findings in the FAIL branch."
        )


@pytest.mark.unit
class TestExecutorValidationGateEscalation:
    """Executor prompt must instruct bug-escalation for validation-gate tasks (ADR-06)."""

    _EXECUTOR_PATH = AGENTS_DIR / "executor.md"

    def test_executor_has_bug_escalation_heading(self) -> None:
        """The BUG ESCALATION FOR VALIDATION GATES section must exist in executor.md.

        The orchestrate SKILL step 4a branches on .devbench/proposals/<id>.json file
        existence to decide whether to invoke task-factory. If the executor prompt
        does not teach the agent to emit that file for validation-gate bugs, the
        long-term fix for ADR-06 regresses silently.
        """
        assert self._EXECUTOR_PATH.exists(), f"executor.md not found at {self._EXECUTOR_PATH}"
        content = self._EXECUTOR_PATH.read_text()
        assert "BUG ESCALATION FOR VALIDATION GATES" in content, (
            "executor.md must contain a 'BUG ESCALATION FOR VALIDATION GATES' section "
            "per ADR-06 so validation-gate tasks that surface out-of-scope production "
            "bugs can trigger task-factory via `uv run devbench write-proposal`."
        )

    def test_executor_bug_escalation_names_write_proposal(self) -> None:
        """The bug-escalation section must reference `write-proposal` as the emission CLI."""
        content = self._EXECUTOR_PATH.read_text()
        heading_pos = content.find("BUG ESCALATION FOR VALIDATION GATES")
        assert heading_pos >= 0
        section_body = content[heading_pos:]
        assert "write-proposal" in section_body, (
            "The BUG ESCALATION section must name `uv run devbench write-proposal` as "
            "the command the executor uses to persist the proposal JSON to disk."
        )

    def test_executor_bug_escalation_verifies_proposal_file(self) -> None:
        """The section must instruct the agent to verify the proposal file landed on disk."""
        content = self._EXECUTOR_PATH.read_text()
        heading_pos = content.find("BUG ESCALATION FOR VALIDATION GATES")
        section_body = content[heading_pos:]
        assert "test -f" in section_body and ".devbench/proposals/" in section_body, (
            "The BUG ESCALATION section must instruct the agent to `test -f "
            "$DEVBENCH_WORKSPACE_ROOT/.devbench/proposals/<id>.json` after write-proposal; "
            "the orchestrate SKILL branches on file existence, so a missing file silently "
            "suppresses task-factory."
        )


@pytest.mark.unit
class TestSkillValidationGateEscalationBranch:
    """Orchestrate SKILL must have a step 4a branch that fires task-factory on executor-emitted proposals."""

    _SKILL_PATH = (
        Path(__file__).parent.parent.parent / "plugin" / "devbench-orchestrate" / "skills" / "orchestrate" / "SKILL.md"
    )

    def test_skill_file_exists(self) -> None:
        assert self._SKILL_PATH.exists(), f"orchestrate/SKILL.md not found at {self._SKILL_PATH}"

    def test_skill_has_validation_gate_branch(self) -> None:
        """SKILL.md must contain a step 4a that handles validation-gate bug escalation."""
        content = self._SKILL_PATH.read_text()
        assert "4a." in content, "SKILL.md must declare a step 4a for validation-gate bug escalation."
        assert "Validation-gate bug-escalation" in content or "validation-gate bug-escalation" in content.lower(), (
            "SKILL.md step 4a must name the validation-gate bug-escalation trigger explicitly."
        )

    def test_skill_step_4a_branches_on_proposal_file(self) -> None:
        """Step 4a must branch on `.devbench/proposals/<id>.json` existence (deterministic trigger)."""
        content = self._SKILL_PATH.read_text()
        assert ".devbench/proposals/" in content, (
            "SKILL.md must reference `.devbench/proposals/<id>.json` -- the file-existence trigger."
        )
        assert "test -f" in content, (
            "SKILL.md step 4a must use `test -f` to check for the proposal file; "
            "the trigger must be deterministic, not verdict-word-based."
        )

    def test_skill_step_4a_short_circuits_on_amendment_file(self) -> None:
        """When an amendment file ALSO exists, step 4a must defer to step 4b/4c to avoid double-fire."""
        content = self._SKILL_PATH.read_text()
        assert ".devbench/amendments/" in content, (
            "SKILL.md step 4a must reference `.devbench/amendments/<id>.json` so it knows to "
            "skip the validation-gate branch when the amendment path is already handling the task."
        )


@pytest.mark.unit
class TestSkillSubagentTextIsDiagnostic:
    """SKILL.md must explicitly forbid treating subagent prose as loop-control directives.

    Prior incident: an executor log-comment opened with "Halting orchestration: ..." and
    the orchestrator LLM obeyed that as a control directive instead of following the
    halt-discipline rule. The SKILL now carries explicit language that subagent text is
    diagnostic only, and loop control is owned exclusively by `devbench next` + the
    stop-hook circuit breaker.
    """

    _SKILL_PATH = (
        Path(__file__).parent.parent.parent / "plugin" / "devbench-orchestrate" / "skills" / "orchestrate" / "SKILL.md"
    )

    def test_skill_declares_subagent_text_is_diagnostic(self) -> None:
        """SKILL must declare that subagent text is not control flow."""
        content = self._SKILL_PATH.read_text()
        assert "Subagent text is diagnostic" in content or "subagent text is diagnostic" in content.lower(), (
            "SKILL.md must include a 'Subagent text is diagnostic' section explicitly forbidding "
            "treatment of subagent prose as loop-control directives."
        )

    def test_skill_lists_control_language_patterns(self) -> None:
        """SKILL must enumerate the prose patterns that MUST NOT change loop behavior."""
        content = self._SKILL_PATH.read_text().lower()
        for phrase in ("halt", "halting", "operator action required", "resume orchestration"):
            assert phrase in content, (
                f"SKILL.md must explicitly name {phrase!r} as a prose pattern the orchestrator must ignore."
            )

    def test_skill_names_guard_comment_format_backstop(self) -> None:
        """SKILL must point to the deterministic hook as the floor defense."""
        content = self._SKILL_PATH.read_text()
        assert "guard-comment-format" in content, (
            "SKILL.md must reference guard-comment-format.sh so readers know which component "
            "provides the deterministic backstop for the prose-level rule."
        )

    def test_skill_states_only_halt_triggers(self) -> None:
        """SKILL must explicitly say the ONLY halt triggers are file/exit-code based."""
        content = self._SKILL_PATH.read_text().lower()
        assert "only halt triggers" in content or "only halt trigger" in content, (
            "SKILL.md must explicitly state the ONLY halt triggers so LLMs cannot be persuaded "
            "by prose to consider any other signal a halt."
        )


@pytest.mark.unit
class TestExecutorCommentLanguageDiscipline:
    """Executor prompt must instruct the agent to avoid halt-imperatives in log-comment text."""

    _EXECUTOR_PATH = AGENTS_DIR / "executor.md"

    def test_executor_has_comment_language_discipline_heading(self) -> None:
        """The COMMENT LANGUAGE DISCIPLINE section must exist in executor.md."""
        content = self._EXECUTOR_PATH.read_text()
        assert "COMMENT LANGUAGE DISCIPLINE" in content, (
            "executor.md must contain a 'COMMENT LANGUAGE DISCIPLINE' section that forbids "
            "halt-imperatives in log-comment bodies."
        )

    def test_executor_enumerates_forbidden_phrases(self) -> None:
        """The section must list the forbidden phrases so the agent can self-check before calling log-comment."""
        content = self._EXECUTOR_PATH.read_text().lower()
        heading_pos = content.find("comment language discipline")
        assert heading_pos >= 0
        section_body = content[heading_pos:]
        for phrase in ("halt orchestration", "operator action required", "resume orchestration once"):
            assert phrase in section_body, (
                f"executor.md COMMENT LANGUAGE DISCIPLINE section must list {phrase!r} "
                "so the agent can avoid it BEFORE the hook rejects its call."
            )

    def test_executor_points_at_guard_comment_format(self) -> None:
        """The section must name the hook that enforces the rule so the agent knows the consequence."""
        content = self._EXECUTOR_PATH.read_text()
        heading_pos = content.find("COMMENT LANGUAGE DISCIPLINE")
        section_body = content[heading_pos:]
        assert "guard-comment-format" in section_body, (
            "executor.md COMMENT LANGUAGE DISCIPLINE section must reference guard-comment-format.sh "
            "so the agent knows which hook will reject its call if it violates the rule."
        )

    def test_executor_gives_good_and_bad_example(self) -> None:
        """The section must contain concrete before/after examples for the agent to pattern-match against."""
        content = self._EXECUTOR_PATH.read_text()
        heading_pos = content.find("COMMENT LANGUAGE DISCIPLINE")
        section_body = content[heading_pos:]
        assert "**Bad**" in section_body, "Need a Bad example the hook rejects."
        assert "**Good**" in section_body, "Need a Good example the hook accepts."

    def test_executor_forbids_bypass_annotations_for_this_rule(self) -> None:
        """The section must explicitly tell the agent not to try to bypass the hook."""
        content = self._EXECUTOR_PATH.read_text()
        heading_pos = content.find("COMMENT LANGUAGE DISCIPLINE")
        section_body = content[heading_pos:]
        assert "bypass" in section_body.lower() or "evade" in section_body.lower(), (
            "executor.md must explicitly forbid bypass attempts so the agent does not try "
            "to add noqa-style annotations to get around the hook."
        )


@pytest.mark.unit
class TestReviewSupervisorCanonicalJudgeNames:
    """ADR-08 slice G: supervisor must use underscored canonical judge names in log-verdict."""

    _SUPERVISOR_PATH = AGENTS_DIR / "review-supervisor.md"

    _CANONICAL_JUDGE_NAMES = (
        "code_review",
        "test_review",
        "doc_review",
        "changes_manifest",
        "security_review",
    )

    _HYPHENATED_REVIEWER_NAMES = (
        "code-reviewer",
        "test-reviewer",
        "doc-reviewer",
    )

    def test_supervisor_contains_all_canonical_judge_names(self) -> None:
        """Each underscored name must appear in the supervisor's log-verdict examples."""
        content = self._SUPERVISOR_PATH.read_text()
        for name in self._CANONICAL_JUDGE_NAMES:
            assert name in content, (
                f"review-supervisor.md must reference canonical judge name '{name}' "
                "so the supervisor emits the exact string the done-gate parser looks for."
            )

    def test_supervisor_has_no_hyphenated_log_verdict_calls(self) -> None:
        """Regression pin: no ``log-verdict <hyphenated-name>`` examples in supervisor."""
        content = self._SUPERVISOR_PATH.read_text()
        for name in self._HYPHENATED_REVIEWER_NAMES:
            bad = f"log-verdict {name}"
            assert bad not in content, (
                f"review-supervisor.md must not contain '{bad}'. "
                "Hyphenated reviewer frontmatter names do not match the done-gate parser's "
                "canonical underscored set. Use e.g. 'log-verdict code_review' instead."
            )

    def test_supervisor_has_mapping_or_warning(self) -> None:
        """Prompt must explicitly warn against deriving the judge name from frontmatter."""
        content = self._SUPERVISOR_PATH.read_text().lower()
        assert "frontmatter" in content or "canonical" in content, (
            "review-supervisor.md must contain a caution or mapping that steers the agent "
            "away from the reviewer's frontmatter name toward the canonical underscored form."
        )


@pytest.mark.unit
class TestBlockerResolverSuggestedApproachStructure:
    """ADR-08 slice H: blocker-resolver must require the four-section suggested_approach."""

    _BLOCKER_RESOLVER_PATH = AGENTS_DIR / "blocker-resolver.md"

    def test_prompt_requires_four_sections(self) -> None:
        """The prompt must name the four required sections so produced drafts are not thin."""
        content = self._BLOCKER_RESOLVER_PATH.read_text()
        for label in ("Context", "Scope", "TDD approach", "Verify"):
            assert label in content, (
                f"blocker-resolver.md must name '{label}' as a required section of suggested_approach."
            )


@pytest.mark.unit
class TestTaskFactoryTodoRowRefusal:
    """ADR-08 slice H: task-factory must warn about thin-approach and TODO-row refusal."""

    _TASK_FACTORY_PATH = AGENTS_DIR / "task-factory.md"

    def test_prompt_mentions_todo_row_refusal(self) -> None:
        """The prompt must explain that literal 'TODO -- describe change' rows cause refusal."""
        content = self._TASK_FACTORY_PATH.read_text()
        assert "TODO -- describe change" in content, (
            "task-factory.md must warn that a literal 'TODO -- describe change' Changes Manifest row "
            "will be refused by materialise-proposal so drafts never enter the backlog half-written."
        )

    def test_prompt_mentions_thin_approach_refusal(self) -> None:
        """The prompt must explain that a too-short suggested_approach causes refusal."""
        content = self._TASK_FACTORY_PATH.read_text().lower()
        assert "thin" in content or "too short" in content or "too terse" in content, (
            "task-factory.md must explain that thin/short suggested_approach values "
            "cause materialise-proposal to refuse."
        )


@pytest.mark.unit
class TestExecutorPreFlightAndAmendmentScope:
    """ADR-08 slice I: executor must have pre-flight reset + amendment-scope discipline."""

    _EXECUTOR_PATH = AGENTS_DIR / "executor.md"

    def test_executor_has_preflight_reset_section(self) -> None:
        """Executor must contain a pre-flight reset step so target-repo pollution is cleaned."""
        content = self._EXECUTOR_PATH.read_text().lower()
        assert "pre-flight" in content, (
            "executor.md must contain a 'pre-flight' step that cleans target-repo state "
            "before TDD RED to avoid contaminating the next task's scope."
        )
        assert "target-repo state" in content or "working tree" in content, (
            "executor.md pre-flight step must reference the target-repo working-tree cleanup."
        )

    def test_executor_preflight_references_git_status(self) -> None:
        """The pre-flight step must name the command the executor runs to detect pollution."""
        content = self._EXECUTOR_PATH.read_text()
        assert "git" in content.lower() and "status" in content.lower(), (
            "executor.md pre-flight step must name a git command (status/restore/etc.) "
            "so the agent can execute the cleanup concretely."
        )

    def test_executor_forbids_unrelated_files_in_amendment(self) -> None:
        """Amendment-scope tightening must forbid pulling unrelated dirty files into an amendment."""
        content = self._EXECUTOR_PATH.read_text().lower()
        assert "amendment" in content, "executor.md must reference amendments."
        # The key rule: do not include pre-existing pollution in an amendment request.
        assert "pre-existing" in content or "unrelated" in content, (
            "executor.md amendment section must explicitly forbid including pre-existing / unrelated "
            "dirty files in an amendment request."
        )


@pytest.mark.unit
class TestBlockerResolverAffectedTaskIdsInstruction:
    """ADR-10 regression pin: blocker-resolver + executor prompts document `affected_task_ids`."""

    _BLOCKER_RESOLVER_PATH = AGENTS_DIR / "blocker-resolver.md"
    _EXECUTOR_PATH = AGENTS_DIR / "executor.md"

    def test_blocker_resolver_documents_affected_task_ids(self) -> None:
        content = self._BLOCKER_RESOLVER_PATH.read_text()
        assert "affected_task_ids" in content, (
            "blocker-resolver.md must document the affected_task_ids field so agents know when to populate it."
        )

    def test_blocker_resolver_describes_evidence_rubric(self) -> None:
        """The prompt must tell the agent what evidence qualifies a peer for the field."""
        content = self._BLOCKER_RESOLVER_PATH.read_text().lower()
        # Evidence rubric keywords -- at least one of these three must appear near
        # the affected_task_ids discussion so the agent doesn't speculate.
        assert "evidence" in content, "blocker-resolver.md must require evidence before populating affected_task_ids"
        assert "same failing test" in content or "same production file" in content, (
            "blocker-resolver.md must list concrete shared-blocker evidence examples"
        )

    def test_blocker_resolver_forbids_self_reference(self) -> None:
        """The prompt must warn against listing source_task_id itself in affected_task_ids."""
        content = self._BLOCKER_RESOLVER_PATH.read_text()
        assert "source_task_id" in content and "affected_task_ids" in content
        # Look for the "do not list source" directive somewhere in the same file.
        lowered = content.lower()
        assert "do not list" in lowered or "must not appear" in lowered or "do not speculate" in lowered, (
            "blocker-resolver.md must instruct the agent not to list source_task_id or speculate"
        )

    def test_executor_cross_references_affected_task_ids(self) -> None:
        content = self._EXECUTOR_PATH.read_text()
        assert "affected_task_ids" in content, (
            "executor.md validation-gate section must reference affected_task_ids so "
            "validation-gate-emitted proposals populate it when applicable."
        )


ALL_REVIEW_JUDGE_PATHS = [
    REVIEW_TEAM_DIR / "code-reviewer.md",
    REVIEW_TEAM_DIR / "test-reviewer.md",
    REVIEW_TEAM_DIR / "doc-reviewer.md",
    REVIEW_TEAM_DIR / "changes-manifest.md",
    AGENTS_DIR / "security-reviewer.md",
]


@pytest.mark.unit
class TestReviewJudgesUseGetDiffForScope:
    """ADR-12: all five review judges must use `devbench get-diff` for scope
    and carry the scope-contract line that pins the anti-pattern.

    These tests are regression pins -- they exist so that a future prompt
    edit that reintroduces `git diff origin/main` or drops the
    ADR-12 contract line will fail CI before merging.
    """

    @pytest.mark.parametrize("judge_path", ALL_REVIEW_JUDGE_PATHS, ids=lambda p: p.name)
    def test_every_review_judge_invokes_devbench_get_diff_at_prompt_top(self, judge_path: Path) -> None:
        """Each of the 5 judges must invoke `uv run devbench get-diff $ARGUMENTS`
        before the main body of instructions. Salience at the top is what
        makes the scope contract stick; placing it lower risks the judge
        reading half the rubric before seeing the scope constraint."""
        content = judge_path.read_text(encoding="utf-8")
        invocation = "`uv run devbench get-diff $ARGUMENTS`"
        assert invocation in content, (
            f"{judge_path.name} must invoke `uv run devbench get-diff $ARGUMENTS` "
            "as the authoritative scope source per ADR-12."
        )
        body_markers = ["You are a strict", "You are the "]
        body_positions = [content.find(m) for m in body_markers if m in content]
        assert body_positions, f"{judge_path.name} does not contain a 'You are...' body marker to anchor on."
        body_start = min(body_positions)
        invocation_pos = content.find(invocation)
        assert invocation_pos < body_start, (
            f"{judge_path.name} places `devbench get-diff` at offset {invocation_pos} "
            f"but the instruction body starts at offset {body_start}; "
            "the get-diff invocation MUST appear before the instruction body."
        )

    @pytest.mark.parametrize("judge_path", ALL_REVIEW_JUDGE_PATHS, ids=lambda p: p.name)
    def test_no_review_judge_contains_git_diff_origin_main_antipattern(self, judge_path: Path) -> None:
        """ADR-12 anti-pattern: a judge prompt must never instruct the agent to
        compute its own `git diff origin/main` or `git diff main...HEAD` scope.
        Those views double-count prior tasks on single-branch + defer_pr mode."""
        content = judge_path.read_text(encoding="utf-8")
        forbidden = [
            "git diff origin/main",
            "git diff main...HEAD",
            "git diff main..HEAD",
        ]
        for pattern in forbidden:
            if pattern in content:
                idx = content.find(pattern)
                preamble = content[max(0, idx - 300) : idx]
                preamble_lower = preamble.lower()
                assert "Do NOT" in preamble or "do not run" in preamble_lower, (
                    f"{judge_path.name} contains `{pattern}` outside the ADR-12 anti-pattern warning. "
                    "This is the exact pattern that caused the 2026-04-20 judge misread."
                )

    @pytest.mark.parametrize("judge_path", ALL_REVIEW_JUDGE_PATHS, ids=lambda p: p.name)
    def test_every_review_judge_references_adr_12_scope_contract(self, judge_path: Path) -> None:
        """Each judge prompt must carry the ADR-12 scope-contract line that
        names get-diff as authoritative and warns against raw git."""
        content = judge_path.read_text(encoding="utf-8")
        assert "Scope contract" in content, f"{judge_path.name} must include a **Scope contract:** line per ADR-12."
        assert "ADR-12" in content, f"{judge_path.name} must reference ADR-12 so readers can trace the rationale."
        assert "AUTHORITATIVE" in content, (
            f"{judge_path.name} must state that `devbench get-diff` is the AUTHORITATIVE scope source."
        )


def _collect_all_agent_md_files() -> list[Path]:
    """Return all .md files under plugin/devbench-orchestrate/agents/ recursively.

    Issue #224: agents all live in the orchestrate plugin after the split.
    """
    agents_dir = Path(__file__).parent.parent.parent / "plugin" / "devbench-orchestrate" / "agents"
    return sorted(agents_dir.rglob("*.md"))


def _extract_frontmatter_model(content: str) -> str | None:
    """Extract the 'model:' value from YAML frontmatter, or None if absent."""
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        stripped = line.strip()
        if stripped.startswith("model:"):
            return stripped[len("model:") :].strip()
    return None


_ALL_AGENT_MD_FILES = _collect_all_agent_md_files()


@pytest.mark.unit
class TestNoAgentFrontmatterPinsHaiku:
    """AC-198-6: No shipped agent .md file may declare 'model: haiku' in its frontmatter.

    This is a future-drift guard: if anyone re-pins a frontmatter model
    default to haiku, this test fails immediately (caylent-solutions/devbench#198).
    """

    @pytest.mark.parametrize(
        "agent_path",
        _ALL_AGENT_MD_FILES,
        ids=lambda p: str(p.name),
    )
    def test_agent_frontmatter_model_is_not_haiku(self, agent_path: Path) -> None:
        """AC-198-6: agent frontmatter 'model:' must not be haiku (case-insensitive)."""
        content = agent_path.read_text(encoding="utf-8")
        model_value = _extract_frontmatter_model(content)
        if model_value is None:
            # No model line in frontmatter -- acceptable, uses SDK default.
            return
        assert "haiku" not in model_value.lower(), (
            f"{agent_path.name}: frontmatter declares 'model: {model_value}'. "
            "Haiku is rejected at config-load time (caylent-solutions/devbench#198); "
            "any agent pinned to haiku will cause config-load failure when the "
            "operator's YAML explicitly selects it, and risks SDK Agent-tool "
            "drops under load. Change to 'sonnet' or 'opus'."
        )


@pytest.mark.unit
class TestNoSubAgentDeclaresSpawningCapability:
    """AC-E4-F1-S1-T2-1 / spec AC-63: no sub-agent definition under
    plugin/devbench-orchestrate/agents/ may declare 'Agent(' anywhere in
    its content. This is the systematic, file-by-file regression pin for
    the grep proof the work unit's Definition of Done requires:
    ``grep -rn "Agent(" plugin/devbench-orchestrate/agents/`` must return
    zero hits.
    """

    @pytest.mark.parametrize(
        "agent_path",
        _ALL_AGENT_MD_FILES,
        ids=lambda p: str(p.relative_to(AGENTS_DIR)),
    )
    def test_agent_md_has_no_agent_tool_spawn_declaration(self, agent_path: Path) -> None:
        content = agent_path.read_text(encoding="utf-8")
        assert "Agent(" not in content, (
            f"{agent_path.relative_to(AGENTS_DIR)} must not contain the literal 'Agent(' "
            "substring anywhere -- no sub-agent may declare second-level Agent-tool spawn "
            "capability post-flatten (AC-63)."
        )


@pytest.mark.unit
class TestSkillInvokesReviewJudgesDirectly:
    """AC-E4-F1-S1-T2-2: the orchestrate skill invokes the four review_team
    judges directly as first-level sub-agents (ADR-33's flatten design),
    and states the missing-verdict hard-failure rule explicitly.
    """

    _SKILL_PATH = (
        Path(__file__).parent.parent.parent / "plugin" / "devbench-orchestrate" / "skills" / "orchestrate" / "SKILL.md"
    )

    @pytest.mark.parametrize(
        "judge_slug",
        ["code-reviewer", "test-reviewer", "doc-reviewer", "changes-manifest"],
    )
    def test_skill_invokes_judge_directly(self, judge_slug: str) -> None:
        content = self._SKILL_PATH.read_text()
        invocation = f"devbench-orchestrate:review_team:{judge_slug}"
        assert invocation in content, (
            f"SKILL.md must invoke {invocation!r} directly as a first-level sub-agent "
            "(ADR-33 flatten); the orchestrate skill must not rely on review-supervisor "
            "spawning the judges as second-level sub-agents."
        )

    def test_skill_states_missing_verdict_is_hard_failure(self) -> None:
        content = self._SKILL_PATH.read_text().lower()
        assert "missing verdict" in content and "hard failure" in content, (
            "SKILL.md must state that a missing verdict from any required judge is a "
            "hard failure (AC-65), never an implicit pass."
        )

    def test_skill_describes_judges_as_first_level_subagents(self) -> None:
        content = self._SKILL_PATH.read_text().lower()
        assert "first-level" in content, (
            "SKILL.md must describe the review_team judges as first-level sub-agents "
            "invoked directly by the skill, not second-level spawns from review-supervisor."
        )


# ---------------------------------------------------------------------------
# E4-F4-S1-T1 -- Judge-side detection (spec FR-4.4, AC-56/AC-57/AC-58)
# ---------------------------------------------------------------------------

_FR44_EXACT_NO_GENUINE_RED_MESSAGE = "no genuine RED; fix may be absent or the test does not reproduce the failure"

_FR44_JUDGE_PATHS = [
    REVIEW_TEAM_DIR / "changes-manifest.md",
    REVIEW_TEAM_DIR / "test-reviewer.md",
]


@pytest.mark.unit
class TestChangesManifestTypeContradictionDetection:
    """AC-E4-F4-S1-T1-1 / spec AC-56: the changes_manifest judge prompt must
    instruct REVIEW_FAIL when a task's declared type contradicts its FR-4.1
    Changes Manifest invariant, including the docs-task-touching-src example.
    """

    _PATH = REVIEW_TEAM_DIR / "changes-manifest.md"

    def test_carries_fr41_invariant_table_for_every_task_type(self) -> None:
        """The FR-4.1 invariant table (all six types) must be present so the
        judge can look up which manifest rows each declared type permits."""
        content = self._PATH.read_text(encoding="utf-8")
        for type_name in ("behavior-fix", "feature", "test-only", "refactor", "docs", "chore"):
            assert f"`{type_name}`" in content, (
                f"changes-manifest.md must list task type `{type_name}` in its FR-4.1 "
                "manifest invariant table so the judge can check declared type against "
                "the actual Changes Manifest rows."
            )

    def test_carries_docs_touching_src_example(self) -> None:
        """FR-4.4 names the docs-task-touching-src/ case explicitly as the
        canonical type-contradiction example."""
        content = self._PATH.read_text(encoding="utf-8")
        assert "docs" in content and "src/" in content, (
            "changes-manifest.md must carry the docs-task-touching-src/ type-contradiction "
            "example named in spec FR-4.4 / AC-56."
        )

    def test_instructs_review_fail_on_type_contradiction(self) -> None:
        content = self._PATH.read_text(encoding="utf-8")
        assert "REVIEW_FAIL" in content and "contradict" in content.lower(), (
            "changes-manifest.md must instruct REVIEW_FAIL when the declared task type "
            "contradicts its FR-4.1 manifest invariant."
        )

    def test_refactor_row_has_no_per_row_manifest_invariant(self) -> None:
        """docs/backlog-contract.md 'Task-Type Taxonomy Rule (FR-4.1, rule 21)' records
        `refactor` as having no per-row Manifest invariant; green-green is a TDD Cycle
        Log concern (deferred to E4-F4-S1-T2), not a static shape the manifest judge can
        verdict on from a diff alone."""
        content = self._PATH.read_text(encoding="utf-8")
        assert "no per-row Manifest invariant" in content, (
            "changes-manifest.md's FR-4.1 table must state `refactor` has no per-row "
            "Manifest invariant, matching docs/backlog-contract.md rule 21."
        )
        assert "E4-F4-S1-T2" in content, (
            "changes-manifest.md must note that green-green enforcement for `refactor` "
            "is deferred to E4-F4-S1-T2, not enforced by this judge from Manifest shape."
        )

    def test_cites_correct_classifier_symbols(self) -> None:
        """The prompt must cite the real production/test classifiers
        (`_is_production_source` / `_is_test_source_path`) and the real
        documentation/chore classifiers (`_is_documentation_path` /
        `_is_chore_path`), not `_check_source_test_pairs` (which is Rule 14's
        source-test atomicity check, not a path classifier)."""
        content = self._PATH.read_text(encoding="utf-8")
        assert "_is_production_source" in content, (
            "changes-manifest.md must cite `_is_production_source` as the production-source "
            "classifier (docs/backlog-contract.md 'Classification reuse (AC-47)')."
        )
        assert "_is_test_source_path" in content, (
            "changes-manifest.md must cite `_is_test_source_path` as the test-path classifier."
        )
        assert "_is_documentation_path" in content, (
            "changes-manifest.md must cite `_is_documentation_path` as the docs-row classifier."
        )
        assert "_is_chore_path" in content, (
            "changes-manifest.md must cite `_is_chore_path` as the chore-row classifier."
        )
        assert "_check_source_test_pairs" not in content, (
            "changes-manifest.md must NOT cite `_check_source_test_pairs` (Rule 14's source-test "
            "atomicity RULE) as a path classifier -- it is a rule, not a classifier."
        )


@pytest.mark.unit
class TestZeroProdCheckScopedToGatedTypes:
    """Doc-review regression pin: rules 29 (changes-manifest.md) and 52
    (test-reviewer.md) must scope the zero-production-source-plus-immediately-
    passing-test REVIEW_FAIL to gated task types only (behavior-fix, feature),
    matching SKILL.md step 4d.b and docs/backlog-contract.md rule 21, which
    exempt test-only/refactor/docs/chore tasks from the RED gate entirely."""

    @pytest.mark.parametrize("judge_path", _FR44_JUDGE_PATHS, ids=lambda p: p.name)
    def test_zero_prod_rule_names_gated_types_explicitly(self, judge_path: Path) -> None:
        content = judge_path.read_text(encoding="utf-8")
        lowered = content.lower()
        assert "does not apply to" in lowered, (
            f"{judge_path.name}'s zero-production-source-plus-passing-test rule must state "
            "explicitly which types it does NOT apply to, so test-only, refactor, docs, and "
            "chore tasks are not falsely REVIEW_FAILed for legitimately having zero "
            "production-source rows and no RED_OBSERVED record."
        )
        assert "(gated types only)" in content or "for a gated task" in lowered, (
            f"{judge_path.name} must scope the zero-production-source-plus-passing-test "
            "REVIEW_FAIL to gated tasks (behavior-fix, feature) explicitly."
        )

    @pytest.mark.parametrize("judge_path", _FR44_JUDGE_PATHS, ids=lambda p: p.name)
    def test_zero_prod_rule_excludes_exempt_types(self, judge_path: Path) -> None:
        content = judge_path.read_text(encoding="utf-8")
        for exempt_type in ("test-only", "refactor", "docs", "chore"):
            assert f"`{exempt_type}`" in content, (
                f"{judge_path.name} must name `{exempt_type}` as an exempt type not subject "
                "to the zero-production-source-plus-passing-test REVIEW_FAIL."
            )


@pytest.mark.unit
class TestTestReviewerRedObservedAndWeakTestDetection:
    """AC-E4-F4-S1-T1-2 / spec AC-57: the test_review judge prompt must
    REVIEW_FAIL a gated task with no RED_OBSERVED record, and carry the
    weak-test check tying the recorded failure output to the AC path.
    """

    _PATH = REVIEW_TEAM_DIR / "test-reviewer.md"

    def test_instructs_review_fail_on_missing_red_observed_record(self) -> None:
        content = self._PATH.read_text(encoding="utf-8")
        assert "RED_OBSERVED" in content, "test-reviewer.md must reference the RED_OBSERVED evidence record."
        assert "no RED_OBSERVED" in content or "no `RED_OBSERVED`" in content, (
            "test-reviewer.md must instruct REVIEW_FAIL for a gated task with no RED_OBSERVED record (spec AC-57)."
        )
        assert "REVIEW_FAIL" in content

    def test_carries_weak_test_check(self) -> None:
        content = self._PATH.read_text(encoding="utf-8")
        lowered = content.lower()
        assert "weak-test check" in lowered, "test-reviewer.md must name the weak-test check explicitly (spec FR-4.4)."
        assert "failure-output digest" in lowered or "failure digest" in lowered, (
            "test-reviewer.md must tie the weak-test check to the recorded failure-output digest."
        )
        assert "ac path" in lowered, (
            "test-reviewer.md must state the weak-test check compares the recorded RED "
            "output against the AC path the task exists to fix."
        )

    def test_weak_test_check_names_actual_record_fields_not_summary(self) -> None:
        """Rule 51 must describe the RED_OBSERVED record's real three fields
        (exit_code, test_node_id, failure_digest -- see
        devbench.constants.RED_OBSERVED_RECORD_FIELDS) and must not claim the
        record carries a 'failure summary' or readable 'failure output', since
        the record is a fixed three-field message and failure_digest is a
        hash-shaped identity token, not free text (doc_review FAIL, attempt 3).
        """
        content = self._PATH.read_text(encoding="utf-8")
        lowered = content.lower()
        assert "failure summary" not in lowered, (
            "test-reviewer.md rule 51 must not claim the RED_OBSERVED record carries a "
            "'failure summary' -- the record has no such field."
        )
        assert "recorded `red_observed` failure output" not in lowered, (
            "test-reviewer.md rule 51 must not describe the RED_OBSERVED record itself as "
            "'failure output' -- the record is a fixed exit_code/test_node_id/failure_digest "
            "message, not readable failure text."
        )
        assert "`test_node_id`" in content, (
            "test-reviewer.md rule 51 must name the `test_node_id` field explicitly as the "
            "human-readable field the AC-path comparison is based on."
        )
        assert "`failure_digest`" in content, (
            "test-reviewer.md rule 51 must name the `failure_digest` field explicitly."
        )
        assert "human-readable" in lowered, (
            "test-reviewer.md rule 51 must state that `test_node_id` is the only "
            "human-readable field of the RED_OBSERVED record."
        )
        assert "hash-shaped" in lowered or "identity token" in lowered, (
            "test-reviewer.md rule 51 must describe `failure_digest` as a hash-shaped "
            "identity token computed over the failure output, not the failure output itself."
        )


@pytest.mark.unit
class TestJudgePromptsCarryZeroProdExactMessage:
    """AC-E4-F4-S1-T1-3 / spec AC-58: both changes_manifest and test_review
    prompts must carry the FR-4.4 zero-production-source-plus-immediately-
    passing-test message character-for-character."""

    @pytest.mark.parametrize("judge_path", _FR44_JUDGE_PATHS, ids=lambda p: p.name)
    def test_prompt_carries_exact_message(self, judge_path: Path) -> None:
        content = judge_path.read_text(encoding="utf-8")
        assert _FR44_EXACT_NO_GENUINE_RED_MESSAGE in content, (
            f"{judge_path.name} must carry the exact spec FR-4.4 message "
            f"{_FR44_EXACT_NO_GENUINE_RED_MESSAGE!r} character-for-character."
        )


@pytest.mark.unit
class TestJudgePromptsNeverPassByDefaultWhenUnevaluable:
    """AC-E4-F4-S1-T1-4: both prompts must state that a judge unable to
    evaluate (RED_OBSERVED unreadable, diff unavailable) returns REVIEW_FAIL
    with the cause, never a pass-by-default."""

    @pytest.mark.parametrize("judge_path", _FR44_JUDGE_PATHS, ids=lambda p: p.name)
    def test_prompt_states_unevaluable_review_fails_with_cause(self, judge_path: Path) -> None:
        content = judge_path.read_text(encoding="utf-8")
        lowered = content.lower()
        assert "unable to evaluate" in lowered, (
            f"{judge_path.name} must describe the unable-to-evaluate case (RED_OBSERVED unreadable, diff unavailable)."
        )
        assert "pass-by-default" in lowered, (
            f"{judge_path.name} must state that an unevaluable review REVIEW_FAILs with "
            "the cause, never a pass-by-default."
        )


@pytest.mark.unit
class TestSkillRoutesJudgeEvidenceInputs:
    """AC-E4-F4-S1-T1-5: the orchestrate skill documents that the review leg
    supplies each judge its evidence inputs -- declared type, Changes
    Manifest, diff, and RED_OBSERVED record location -- so no judge is asked
    to verdict without the material to falsify it.
    """

    _SKILL_PATH = (
        Path(__file__).parent.parent.parent / "plugin" / "devbench-orchestrate" / "skills" / "orchestrate" / "SKILL.md"
    )

    def test_skill_documents_judge_evidence_inputs(self) -> None:
        content = self._SKILL_PATH.read_text(encoding="utf-8")
        for term in ("declared type", "Changes Manifest", "RED_OBSERVED"):
            assert term in content, (
                f"SKILL.md must document that the review leg supplies judges the "
                f"evidence input {term!r} (spec FR-4.4 / AC-E4-F4-S1-T1-5)."
            )
        assert "diff" in content.lower()

    def test_skill_states_unevaluable_judge_fails_with_cause(self) -> None:
        content = self._SKILL_PATH.read_text(encoding="utf-8").lower()
        assert "unable to evaluate" in content or "unevaluable" in content, (
            "SKILL.md must state that a judge unable to evaluate REVIEW_FAILs with the cause, never a pass-by-default."
        )
        assert "pass-by-default" in content
