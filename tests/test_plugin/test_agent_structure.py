"""Unit tests for plugin agent directory structure."""

from __future__ import annotations

from pathlib import Path

import pytest

AGENTS_DIR = (
    Path(__file__).parent.parent.parent
    / "plugin"
    / "devbench"
    / "agents"
)

REVIEW_TEAM_DIR = AGENTS_DIR / "review_team"


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
