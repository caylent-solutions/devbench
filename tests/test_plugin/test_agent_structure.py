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
        """AC-2 / ADR-28: Frontmatter must name review-supervisor with tools: Bash and NO Agent tool.

        The review pipeline was flattened (ADR-28): the orchestrate skill now
        dispatches the four review_team reviewers directly, and the supervisor is
        demoted to an inert deprecation stub. Its frontmatter MUST drop the
        ``Agent(...)`` tool -- declaring it was the literal SDK violation (a
        sub-agent cannot spawn sub-agents) that caused the runtime degradation.
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
        assert "Agent" not in frontmatter, (
            f"review-supervisor.md frontmatter must NOT include the Agent tool after ADR-28 "
            f"flattened the review pipeline (a sub-agent cannot spawn sub-agents). Got:\n{frontmatter}"
        )


@pytest.mark.unit
class TestReviewSupervisorDeprecated:
    """ADR-28: review-supervisor.md is a demoted, inert deprecation stub.

    The Step-0 Agent-tool self-check (issue #183) and the whole dispatch /
    aggregation body are removed: the orchestrate skill now dispatches the four
    review_team reviewers directly (first-level), so the supervisor no longer
    spawns anything, self-checks anything, or logs verdicts. The file is kept
    only so config / plugin-shadow / activity references continue to resolve.
    """

    _SUPERVISOR_PATH = AGENTS_DIR / "review-supervisor.md"

    def test_supervisor_marked_deprecated(self) -> None:
        content = self._SUPERVISOR_PATH.read_text()
        assert "DEPRECATED" in content or "deprecated" in content.lower(), (
            "review-supervisor.md must declare itself deprecated (ADR-28)."
        )

    def test_supervisor_states_it_must_not_be_invoked(self) -> None:
        content = self._SUPERVISOR_PATH.read_text().lower()
        assert "do not invoke" in content or "must not be invoked" in content, (
            "review-supervisor.md must state it must not be invoked."
        )

    def test_supervisor_no_longer_dispatches_reviewers(self) -> None:
        """The stub must not instruct spawning reviewers via the Agent tool."""
        content = self._SUPERVISOR_PATH.read_text()
        assert "Step 0:" not in content, (
            "review-supervisor.md must not retain the Step 0 Agent-tool self-check; "
            "the supervisor no longer dispatches reviewers (ADR-28)."
        )
        assert "Invoke All Reviewers" not in content, (
            "review-supervisor.md must not retain the parallel-dispatch instruction."
        )

    def test_supervisor_references_adr_28(self) -> None:
        content = self._SUPERVISOR_PATH.read_text()
        assert "ADR-28" in content, (
            "review-supervisor.md must reference ADR-28 so readers can trace why it was retired."
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
class TestIacDeployReviewerAgent:
    """Workstream C: the optional iac_review judge agent file.

    ``iac-deploy-reviewer.md`` is the optional evidence-verifying IaC judge. It
    is placed as a SIBLING of ``security-reviewer.md`` at the ``agents/`` root --
    deliberately OUTSIDE ``review_team/`` so the review-supervisor's
    ``ls review_team/*.md`` auto-discovery does NOT make it a mandatory reviewer.
    Its frontmatter ``name:`` is hyphenated (``iac-deploy-reviewer``) but maps to
    the canonical underscored done-gate judge name ``iac_review``.
    """

    _IAC_PATH = AGENTS_DIR / "iac-deploy-reviewer.md"

    def test_iac_deploy_reviewer_file_exists(self) -> None:
        """iac-deploy-reviewer.md must exist at agents/ root."""
        assert self._IAC_PATH.exists(), f"iac-deploy-reviewer.md not found at {self._IAC_PATH}"

    def test_iac_deploy_reviewer_not_in_review_team(self) -> None:
        """The agent must NOT live inside review_team/ -- that would make it a
        mandatory reviewer via the supervisor's ls auto-discovery."""
        assert not (REVIEW_TEAM_DIR / "iac-deploy-reviewer.md").exists(), (
            "iac-deploy-reviewer.md must NOT be placed inside review_team/; "
            "doing so makes the optional judge mandatory via the supervisor's auto-discovery."
        )

    def test_iac_deploy_reviewer_frontmatter_valid(self) -> None:
        """Frontmatter must declare name, model, and Bash tools (mirrors security-reviewer)."""
        content = self._IAC_PATH.read_text(encoding="utf-8")
        lines = content.splitlines()
        assert lines[0].strip() == "---", "iac-deploy-reviewer.md must start with --- frontmatter delimiter"
        end_idx = next(
            (i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
            None,
        )
        assert end_idx is not None, "iac-deploy-reviewer.md frontmatter closing --- not found"
        frontmatter = "\n".join(lines[1:end_idx])

        assert "name: iac-deploy-reviewer" in frontmatter, (
            f"Frontmatter must contain 'name: iac-deploy-reviewer'. Got:\n{frontmatter}"
        )
        assert re.search(r"^model:\s*opus\s*$", frontmatter, re.MULTILINE), (
            f"iac-deploy-reviewer.md must declare 'model: opus' (ADR-25 judge default). Got:\n{frontmatter}"
        )
        assert "tools: Bash" in frontmatter, f"Frontmatter tools must be Bash. Got:\n{frontmatter}"

    def test_iac_deploy_reviewer_maps_to_canonical_iac_review_verdict(self) -> None:
        """The agent must log its verdict under the canonical underscored judge
        name ``iac_review`` (the done-gate parser only recognises that form),
        never the hyphenated frontmatter name in a log-verdict call."""
        content = self._IAC_PATH.read_text(encoding="utf-8")
        assert re.search(r"log-verdict\s+iac_review\b", content), (
            "iac-deploy-reviewer.md must call 'log-verdict iac_review ...' -- the canonical "
            "underscored judge name the done-gate parser recognises."
        )
        assert "log-verdict iac-deploy-reviewer" not in content, (
            "iac-deploy-reviewer.md must NOT pass the hyphenated frontmatter name to log-verdict; "
            "the done-gate only recognises the underscored 'iac_review'."
        )

    def test_iac_deploy_reviewer_has_token_requirement_section(self) -> None:
        """The agent must carry the H3 DEVBENCH_REVIEW_ROUND_TOKEN requirement
        section (mirrors security-reviewer.md), since iac_review is a canonical
        reviewer verdict subject to the default-deny token guard."""
        content = self._IAC_PATH.read_text(encoding="utf-8")
        assert "DEVBENCH_REVIEW_ROUND_TOKEN" in content, (
            "iac-deploy-reviewer.md must document the DEVBENCH_REVIEW_ROUND_TOKEN requirement (H3)."
        )
        assert "Token requirement" in content, (
            "iac-deploy-reviewer.md must include the H3 'Token requirement' section like security-reviewer.md."
        )

    def test_iac_deploy_reviewer_reads_evidence_ledger(self) -> None:
        """The judge verifies tool-captured evidence -- it must read the
        evidence ledger written by verify-ac, not provision anything."""
        content = self._IAC_PATH.read_text(encoding="utf-8")
        assert ".devbench/evidence/" in content, (
            "iac-deploy-reviewer.md must read the .devbench/evidence/<id>/<attempt>/ ledger."
        )
        assert "evidence.json" in content, "iac-deploy-reviewer.md must reference the evidence.json ledger file."

    def test_iac_deploy_reviewer_holds_no_aws_credentials(self) -> None:
        """The judge must explicitly state it holds NO AWS credentials and
        provisions nothing -- it is evidence-verifying only."""
        content = self._IAC_PATH.read_text(encoding="utf-8")
        assert "No AWS credentials" in content or "NO AWS credentials" in content, (
            "iac-deploy-reviewer.md must explicitly state it holds no AWS credentials."
        )

    @pytest.mark.parametrize(
        "tool_token",
        [
            "terraform",
            "tofu",
            "terragrunt",
            "terratest",
            "cdktf",
            "cdk",
            "cloudformation",
            "sam",
            "smoke",
        ],
    )
    def test_iac_deploy_reviewer_covers_full_tool_matrix(self, tool_token: str) -> None:
        """The rubric must cover the full common IaC tool matrix so evidence for
        any supported tool is meaningfully verified."""
        content = self._IAC_PATH.read_text(encoding="utf-8").lower()
        assert tool_token in content, (
            f"iac-deploy-reviewer.md rubric must cover '{tool_token}' from the IaC tool matrix."
        )


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
    """ADR-28: the demoted supervisor must not carry any REVIEW_PASS/REVIEW_FAIL verdict tokens.

    It logs no verdicts at all now, so the regression pin is simply that the
    legacy uppercase verdict tokens do not reappear in the stub.
    """

    _SUPERVISOR_PATH = AGENTS_DIR / "review-supervisor.md"

    def test_supervisor_no_review_fail_token(self) -> None:
        """The stub must not use REVIEW_FAIL as a verdict token."""
        content = self._SUPERVISOR_PATH.read_text()
        assert "REVIEW_FAIL" not in content, "review-supervisor.md must not use the 'REVIEW_FAIL' verdict token."

    def test_supervisor_no_review_pass_token(self) -> None:
        """The stub must not use REVIEW_PASS as a verdict token."""
        content = self._SUPERVISOR_PATH.read_text()
        assert "REVIEW_PASS" not in content, "review-supervisor.md must not use the 'REVIEW_PASS' verdict token."

    def test_supervisor_logs_no_verdicts(self) -> None:
        """The demoted stub must not contain any log-verdict instruction."""
        content = self._SUPERVISOR_PATH.read_text()
        assert "log-verdict" not in content, (
            "review-supervisor.md must not instruct any log-verdict call -- the four review_team "
            "reviewers self-log their canonical verdicts after ADR-28."
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


_SKILL_PATH = (
    Path(__file__).parent.parent.parent / "plugin" / "devbench-orchestrate" / "skills" / "orchestrate" / "SKILL.md"
)

_REVIEW_TEAM_AGENT_TYPES = [
    "devbench-orchestrate:code-reviewer",
    "devbench-orchestrate:test-reviewer",
    "devbench-orchestrate:doc-reviewer",
    "devbench-orchestrate:changes-manifest",
]


@pytest.mark.unit
class TestSkillDispatchesReviewTeamDirectly:
    """ADR-28: SKILL.md step 5 dispatches the four review_team reviewers directly.

    The orchestrate skill (main thread) is the dispatcher, not review-supervisor,
    because the Claude Agent SDK forbids a sub-agent from spawning sub-agents.
    Step 5 must name each reviewer agent_type and inject the per-round token.
    """

    def _step5_region(self) -> str:
        """Return SKILL.md text from step 5 up to step 6 (the dispatch region)."""
        content = _SKILL_PATH.read_text()
        start = content.find("\n5. ")
        assert start >= 0, "SKILL.md must declare a step 5."
        end = content.find("\n6. ", start)
        return content[start:end] if end > start else content[start:]

    @pytest.mark.parametrize("agent_type", _REVIEW_TEAM_AGENT_TYPES)
    def test_step5_dispatches_each_reviewer_agent_type(self, agent_type: str) -> None:
        region = self._step5_region()
        assert agent_type in region, (
            f"SKILL.md step 5 must dispatch '{agent_type}' directly (ADR-28 flattened pipeline)."
        )

    def test_step5_injects_round_token(self) -> None:
        region = self._step5_region()
        assert "DEVBENCH_REVIEW_ROUND_TOKEN" in region, (
            "SKILL.md step 5 must inject DEVBENCH_REVIEW_ROUND_TOKEN into each reviewer sub-agent."
        )

    def test_step5_is_fail_closed_on_canonical_verdict_lines(self) -> None:
        """Pass/fail must be derived from canonical verdict lines, not reviewer prose/JSON."""
        region = self._step5_region().lower()
        assert "review_pass" in region or "canonical verdict" in region, (
            "SKILL.md step 5 must determine pass/fail from the canonical verdict lines for the round."
        )

    def test_step5_does_not_invoke_review_supervisor(self) -> None:
        region = self._step5_region()
        assert "Invoke `review-supervisor`" not in region and "invoke review-supervisor" not in region.lower(), (
            "SKILL.md step 5 must NOT invoke review-supervisor -- it dispatches the reviewers directly (ADR-28)."
        )


@pytest.mark.unit
class TestReviewTeamTokenRequirement:
    """ADR-28: each review_team reviewer is the direct token consumer and must
    carry the H3 DEVBENCH_REVIEW_ROUND_TOKEN requirement section (mirrors
    security-reviewer.md), since the orchestrate skill now injects the token
    into each reviewer directly rather than via the supervisor.
    """

    @pytest.mark.parametrize("agent_filename", REVIEW_TEAM_AGENTS)
    def test_reviewer_has_token_requirement_section(self, agent_filename: str) -> None:
        content = (REVIEW_TEAM_DIR / agent_filename).read_text()
        assert "Token requirement" in content, (
            f"{agent_filename} must include the H3 'Token requirement' section (ADR-28 direct dispatch)."
        )
        assert "DEVBENCH_REVIEW_ROUND_TOKEN" in content, (
            f"{agent_filename} must document the DEVBENCH_REVIEW_ROUND_TOKEN requirement (H3)."
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
class TestSkillIacReviewConditionalDispatch:
    """Workstream C: SKILL.md must dispatch the optional iac_review judge as a
    solo step (like security-reviewer) gated on BOTH the enablement toggle AND
    the deterministic per-unit infra-applicability predicate.
    """

    _SKILL_PATH = (
        Path(__file__).parent.parent.parent / "plugin" / "devbench-orchestrate" / "skills" / "orchestrate" / "SKILL.md"
    )

    def test_skill_has_iac_review_dispatch_step(self) -> None:
        """SKILL.md must declare a step 7b that invokes the iac-deploy-reviewer agent."""
        content = self._SKILL_PATH.read_text()
        assert "7b." in content, "SKILL.md must declare a step 7b for the optional iac_review dispatch."
        assert "devbench-orchestrate:iac-deploy-reviewer" in content, (
            "SKILL.md step 7b must invoke the devbench-orchestrate:iac-deploy-reviewer agent."
        )

    def test_skill_iac_dispatch_gated_on_enablement_toggle(self) -> None:
        """The dispatch must be gated on the optional_judges.iac_review enablement toggle."""
        content = self._SKILL_PATH.read_text()
        assert "optional_judges.iac_review" in content or "optional_judges" in content, (
            "SKILL.md step 7b must gate the dispatch on optional_judges.iac_review being enabled."
        )

    def test_skill_iac_dispatch_gated_on_unit_requires_iac_judge(self) -> None:
        """The dispatch must be gated on the deterministic infra-applicability predicate."""
        content = self._SKILL_PATH.read_text()
        assert "unit_requires_iac_judge" in content, (
            "SKILL.md step 7b must gate the dispatch on verification.unit_requires_iac_judge "
            "so dispatch agrees with the step-9 done-gate by construction."
        )

    def test_skill_iac_dispatch_injects_review_round_token(self) -> None:
        """The solo dispatch must inject DEVBENCH_REVIEW_ROUND_TOKEN like security-reviewer."""
        content = self._SKILL_PATH.read_text()
        heading_pos = content.find("7b.")
        assert heading_pos >= 0
        # The token must be referenced in the iac dispatch region (between 7b and step 8).
        next_step_pos = content.find("\n8. ", heading_pos)
        region = content[heading_pos:next_step_pos] if next_step_pos > heading_pos else content[heading_pos:]
        assert "DEVBENCH_REVIEW_ROUND_TOKEN" in region, (
            "SKILL.md step 7b must inject DEVBENCH_REVIEW_ROUND_TOKEN into the iac-deploy-reviewer environment."
        )

    def test_skill_supervisor_does_not_dispatch_iac_review(self) -> None:
        """review-supervisor must NOT dispatch iac_review -- the orchestrate skill does (solo step)."""
        content = self._SKILL_PATH.read_text()
        assert "never dispatches `iac_review`" in content or "never dispatch iac_review" in content.lower(), (
            "SKILL.md Standards must clarify that review-supervisor never dispatches iac_review; "
            "the orchestrate skill dispatches it as a solo step."
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
