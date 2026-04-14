"""Unit tests for plugin agent directory structure."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

AGENTS_DIR = (
    Path(__file__).parent.parent.parent
    / "plugin"
    / "devbench"
    / "agents"
)

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
        assert REVIEW_TEAM_DIR.is_dir(), (
            f"Expected directory not found: {REVIEW_TEAM_DIR}"
        )

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
            f"review_team/ contents mismatch.\n"
            f"  Expected: {sorted(expected)}\n"
            f"  Actual:   {sorted(actual)}"
        )


@pytest.mark.unit
class TestReviewSupervisorFrontmatter:
    """AC-2: review-supervisor.md exists with correct frontmatter."""

    _SUPERVISOR_PATH = AGENTS_DIR / "review-supervisor.md"

    def test_review_supervisor_file_exists(self) -> None:
        """review-supervisor.md must exist at agents/review-supervisor.md."""
        assert self._SUPERVISOR_PATH.exists(), (
            f"review-supervisor.md not found at {self._SUPERVISOR_PATH}"
        )

    def test_review_supervisor_frontmatter_valid(self) -> None:
        """AC-2: Frontmatter must contain name: review-supervisor and tools: Bash, Agent(...)."""
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
        assert "tools:" in frontmatter, (
            f"Frontmatter must contain a tools: field. Got:\n{frontmatter}"
        )
        assert "Bash" in frontmatter, (
            f"Frontmatter tools must include Bash. Got:\n{frontmatter}"
        )
        assert "Agent" in frontmatter, (
            f"Frontmatter tools must include Agent(...). Got:\n{frontmatter}"
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
        assert (AGENTS_DIR / "security-reviewer.md").exists(), (
            "security-reviewer.md must remain at agents/ root"
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
class TestReviewTeamModelUpgrade:
    """AC-11: All four review_team agents must use model: sonnet."""

    @pytest.mark.parametrize("agent_filename", REVIEW_TEAM_AGENTS)
    def test_review_team_agent_uses_sonnet_model(self, agent_filename: str) -> None:
        """AC-11: Each review_team agent must declare model: sonnet in frontmatter."""
        agent_path = REVIEW_TEAM_DIR / agent_filename
        assert agent_path.exists(), f"Agent file not found: {agent_path}"
        content = agent_path.read_text()

        lines = content.splitlines()
        assert lines[0].strip() == "---", (
            f"{agent_filename} must start with --- frontmatter delimiter"
        )
        end_idx = next(
            (i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
            None,
        )
        assert end_idx is not None, f"{agent_filename} frontmatter closing --- not found"
        frontmatter = "\n".join(lines[1:end_idx])

        assert re.search(r"^model:\s*sonnet\s*$", frontmatter, re.MULTILINE), (
            f"{agent_filename} must declare 'model: sonnet' in frontmatter.\n"
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
    def test_reviewer_instructs_log_comment_before_log_verdict(
        self, agent_filename: str
    ) -> None:
        """AC-1, AC-3: Each reviewer prompt must instruct log-comment before log-verdict."""
        agent_path = REVIEW_TEAM_DIR / agent_filename
        content = agent_path.read_text()

        assert "log-comment" in content, (
            f"{agent_filename} must instruct the agent to call log-comment "
            "for each finding or confirmation before logging the verdict."
        )
        assert "log-verdict" in content, (
            f"{agent_filename} must instruct the agent to call log-verdict."
        )

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

        assert '"verdict"' in content, (
            f"{agent_filename} must include JSON envelope format with 'verdict' field."
        )
        assert '"summary"' in content, (
            f"{agent_filename} must include JSON envelope format with 'summary' field."
        )
        assert '"findings"' in content, (
            f"{agent_filename} must include JSON envelope format with 'findings' array."
        )

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
        ), (
            f"{agent_filename} must instruct the agent that the JSON envelope is "
            "the last thing output in the response."
        )


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
            "review-supervisor.md must instruct parsing the reviewer JSON envelope "
            "to extract verdicts and summaries."
        )

    def test_supervisor_fail_branch_logs_findings_as_comments(self) -> None:
        """AC-6: Supervisor FAIL branch must relay individual findings via log-comment."""
        content = self._SUPERVISOR_PATH.read_text()
        assert "log-comment" in content, (
            "review-supervisor.md must use log-comment to relay reviewer findings "
            "in the FAIL branch."
        )
