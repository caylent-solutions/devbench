"""Workstream C integration: optional iac_review judge dispatch + done-gate agreement.

The optional ``iac_review`` judge is an evidence-verifying IaC/deploy reviewer
(``plugin/devbench-orchestrate/agents/iac-deploy-reviewer.md``) dispatched as a
solo step (7b) in the orchestrate skill -- exactly like the security-reviewer --
but ONLY when BOTH:

  (a) the operator enabled it (``optional_judges.iac_review: true``), AND
  (b) the unit's ``## Verification`` contract contains an infrastructure item
      (``verification.unit_requires_iac_judge`` is true).

This is an integration-style test: it exercises the real predicate
(``verification.unit_requires_iac_judge``) and the real done-gate required-set
(``BacklogManager._required_judge_set``) against on-disk work-unit content, and
pins the SKILL.md + agent prompt wiring so the conditional dispatch and the
step-9 done-gate agree by construction. ``iac_review`` is required for a unit
with an infra Verification item and absent for a non-infra unit.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from devbench import verification
from devbench.backlog.manager import BacklogManager
from devbench.config_loader import RepoConfig, RuntimeConfig
from devbench.constants import ALL_REQUIRED_JUDGE_NAMES

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent / "plugin" / "devbench-orchestrate"
SKILL_PATH = PLUGIN_ROOT / "skills" / "orchestrate" / "SKILL.md"
IAC_AGENT_PATH = PLUGIN_ROOT / "agents" / "iac-deploy-reviewer.md"
SECURITY_AGENT_PATH = PLUGIN_ROOT / "agents" / "security-reviewer.md"

_INFRA_UNIT = (
    "# E0-F1-S1-T1: Provision the data-lake landing bucket\n\n"
    "## Status: in-review\n\n"
    "## Acceptance Criteria\n\n"
    "- [x] AC-3: a real `terragrunt apply` provisions the bucket and the terratest passes\n\n"
    "## Verification\n\n"
    "- VERIFY AC-3 | type=terratest | tool=terragrunt | cmd=`make tf-test UNIT=sandbox/000/data-lake/000` | "
    "expect-exit=0\n\n"
    "## Comments\n\n"
)

_NON_INFRA_UNIT = (
    "# E0-F1-S2-T1: Add a pure-Python parser helper\n\n"
    "## Status: in-review\n\n"
    "## Acceptance Criteria\n\n"
    "- [x] AC-1: the unit test for the helper passes\n\n"
    "## Verification\n\n"
    "- VERIFY AC-1 | type=command | cmd=`pytest -q tests/test_helper.py` | expect-exit=0\n\n"
    "## Comments\n\n"
)


def _cfg(*, iac_enabled: bool) -> RuntimeConfig:
    """RuntimeConfig whose optional_judges.iac_review matches *iac_enabled*."""
    return RuntimeConfig(
        repos={"org/repo": RepoConfig()},
        optional_judges={"iac_review": iac_enabled},
    )


@pytest.mark.integration
class TestIacJudgeApplicabilityPredicate:
    """The deterministic predicate that both dispatch and the done-gate consult."""

    def test_infra_unit_requires_iac_judge(self) -> None:
        assert verification.unit_requires_iac_judge(_INFRA_UNIT) is True

    def test_non_infra_unit_does_not_require_iac_judge(self) -> None:
        assert verification.unit_requires_iac_judge(_NON_INFRA_UNIT) is False


@pytest.mark.integration
class TestIacReviewRequiredSetMatchesDispatch:
    """The done-gate required set agrees with the SKILL's conditional dispatch.

    iac_review is required (and therefore dispatched) iff enabled AND the unit
    has an infra Verification item; otherwise the required set is exactly the
    always-on core 5.
    """

    def test_iac_required_for_infra_unit_when_enabled(self, tmp_path: Path) -> None:
        wu = tmp_path / "infra.md"
        wu.write_text(_INFRA_UNIT, encoding="utf-8")
        with patch("devbench.config.RUNTIME_CONFIG", _cfg(iac_enabled=True)):
            required = BacklogManager._required_judge_set(wu.read_text(encoding="utf-8"))
        assert "iac_review" in required
        assert required == ALL_REQUIRED_JUDGE_NAMES | {"iac_review"}

    def test_iac_not_required_for_non_infra_unit_when_enabled(self, tmp_path: Path) -> None:
        wu = tmp_path / "non_infra.md"
        wu.write_text(_NON_INFRA_UNIT, encoding="utf-8")
        with patch("devbench.config.RUNTIME_CONFIG", _cfg(iac_enabled=True)):
            required = BacklogManager._required_judge_set(wu.read_text(encoding="utf-8"))
        assert "iac_review" not in required
        assert required == ALL_REQUIRED_JUDGE_NAMES

    def test_iac_not_required_for_infra_unit_when_disabled(self, tmp_path: Path) -> None:
        """Operator opt-in: an infra unit does NOT require iac_review when the toggle is off."""
        wu = tmp_path / "infra.md"
        wu.write_text(_INFRA_UNIT, encoding="utf-8")
        with patch("devbench.config.RUNTIME_CONFIG", _cfg(iac_enabled=False)):
            required = BacklogManager._required_judge_set(wu.read_text(encoding="utf-8"))
        assert "iac_review" not in required
        assert required == ALL_REQUIRED_JUDGE_NAMES


@pytest.mark.integration
class TestSkillConditionalDispatchWiring:
    """Pin the SKILL.md step-7b conditional dispatch so the wiring cannot regress."""

    def test_skill_file_exists(self) -> None:
        assert SKILL_PATH.is_file(), f"orchestrate SKILL.md missing at {SKILL_PATH}"

    @pytest.mark.parametrize(
        "fragment",
        [
            "devbench-orchestrate:iac-deploy-reviewer",
            "optional_judges.iac_review",
            "unit_requires_iac_judge",
            "review-token",
            "test_iac_review_dispatch.py",
        ],
    )
    def test_skill_contains_dispatch_fragment(self, fragment: str) -> None:
        content = SKILL_PATH.read_text(encoding="utf-8")
        assert fragment in content, (
            f"SKILL.md is missing iac_review dispatch fragment: {fragment!r}. "
            "Step 7b must dispatch iac-deploy-reviewer only when enabled AND the unit "
            "requires the iac judge, agreeing with the step-9 done-gate by construction."
        )

    def test_skill_dispatch_runs_after_security_before_git_ops(self) -> None:
        """The solo iac_review step must sit between security-review (7) and git-ops (8)."""
        content = SKILL_PATH.read_text(encoding="utf-8")
        sec_pos = content.find("proceed immediately to step 7b")
        iac_pos = content.find("7b.")
        gitops_pos = content.find("\n8. `uv run devbench git-ops")
        assert sec_pos >= 0, "security-review PASS must route to step 7b"
        assert 0 <= sec_pos < iac_pos < gitops_pos, (
            "iac_review (7b) must run after security-review PASS and before git-ops (8)."
        )

    def test_supervisor_does_not_dispatch_iac_review(self) -> None:
        """review-supervisor only dispatches its four review_team members, never iac_review."""
        content = SKILL_PATH.read_text(encoding="utf-8")
        assert "never dispatches `iac_review`" in content, (
            "SKILL.md must clarify the review-supervisor never dispatches iac_review."
        )


@pytest.mark.integration
class TestIacAgentIsOptionalSibling:
    """The agent file is a sibling of security-reviewer.md, not a review_team member."""

    def test_iac_agent_exists(self) -> None:
        assert IAC_AGENT_PATH.is_file(), f"iac-deploy-reviewer.md missing at {IAC_AGENT_PATH}"

    def test_iac_agent_sibling_of_security_reviewer(self) -> None:
        """Both live at agents/ root (siblings); neither is inside review_team/."""
        assert IAC_AGENT_PATH.parent == SECURITY_AGENT_PATH.parent, (
            "iac-deploy-reviewer.md must be a sibling of security-reviewer.md at the agents/ root "
            "so the supervisor's review_team/ auto-discovery does not make it mandatory."
        )
        assert IAC_AGENT_PATH.parent.name == "agents", "iac-deploy-reviewer.md must live directly under agents/."

    def test_iac_agent_logs_canonical_iac_review_verdict(self) -> None:
        content = IAC_AGENT_PATH.read_text(encoding="utf-8")
        assert "log-verdict iac_review" in content, (
            "iac-deploy-reviewer.md must log its verdict under the canonical underscored name 'iac_review'."
        )
