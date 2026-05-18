"""Structural pins for AC-197-8: JUDGE_* -> DEVBENCH_* rename in canonical docs (batch 2).

Verifies that docs/architecture.md, docs/model-pricing.md, and
docs/manual-blockers.md use DEVBENCH_* as the canonical and ONLY name for every
operational env var.

No per-var backwards-compatibility footnotes may appear in any of these docs.

Spec source: spec/devbench-self-improve.md section 4.9. Issue: #197.
AC: AC-197-8.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
ARCHITECTURE_DOC = REPO_ROOT / "docs" / "architecture.md"
MODEL_PRICING_DOC = REPO_ROOT / "docs" / "model-pricing.md"
MANUAL_BLOCKERS_DOC = REPO_ROOT / "docs" / "manual-blockers.md"


def _read_doc(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# docs/architecture.md
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestArchitectureNoLegacyJudgeVars:
    """docs/architecture.md must not reference any JUDGE_* operational env vars.

    Note: JUDGE_AGENT_ROLE is an ADR-15 / AC-197-13 exempt identifier (it names
    the orchestrator-tier bypass indicator, not an operational env var in the
    JUDGE_* namespace that was renamed). It must NOT be renamed and must NOT
    be flagged by these tests.
    """

    def test_no_judge_workspace_root(self) -> None:
        """JUDGE_WORKSPACE_ROOT must not appear; use DEVBENCH_WORKSPACE_ROOT."""
        text = _read_doc(ARCHITECTURE_DOC)
        assert "JUDGE_WORKSPACE_ROOT" not in text, (
            "docs/architecture.md still contains JUDGE_WORKSPACE_ROOT. Rename to DEVBENCH_WORKSPACE_ROOT (AC-197-8)."
        )

    def test_no_judge_config_path(self) -> None:
        """JUDGE_CONFIG_PATH must not appear; use DEVBENCH_CONFIG_PATH."""
        text = _read_doc(ARCHITECTURE_DOC)
        assert "JUDGE_CONFIG_PATH" not in text, (
            "docs/architecture.md still contains JUDGE_CONFIG_PATH. Rename to DEVBENCH_CONFIG_PATH (AC-197-8)."
        )

    def test_no_judge_log_file(self) -> None:
        """JUDGE_LOG_FILE must not appear; use DEVBENCH_LOG_FILE."""
        text = _read_doc(ARCHITECTURE_DOC)
        assert "JUDGE_LOG_FILE" not in text, (
            "docs/architecture.md still contains JUDGE_LOG_FILE. Rename to DEVBENCH_LOG_FILE (AC-197-8)."
        )

    def test_no_judge_display_timezone(self) -> None:
        """JUDGE_DISPLAY_TIMEZONE must not appear; use DEVBENCH_DISPLAY_TIMEZONE."""
        text = _read_doc(ARCHITECTURE_DOC)
        assert "JUDGE_DISPLAY_TIMEZONE" not in text, (
            "docs/architecture.md still contains JUDGE_DISPLAY_TIMEZONE. "
            "Rename to DEVBENCH_DISPLAY_TIMEZONE (AC-197-8)."
        )

    def test_no_judge_report_timezone(self) -> None:
        """JUDGE_REPORT_TIMEZONE must not appear; use DEVBENCH_REPORT_TIMEZONE."""
        text = _read_doc(ARCHITECTURE_DOC)
        assert "JUDGE_REPORT_TIMEZONE" not in text, (
            "docs/architecture.md still contains JUDGE_REPORT_TIMEZONE. Rename to DEVBENCH_REPORT_TIMEZONE (AC-197-8)."
        )

    def test_no_judge_orchestrate_max_cascade_depth(self) -> None:
        """JUDGE_ORCHESTRATE_MAX_CASCADE_DEPTH must not appear; use DEVBENCH_ prefix."""
        text = _read_doc(ARCHITECTURE_DOC)
        assert "JUDGE_ORCHESTRATE_MAX_CASCADE_DEPTH" not in text, (
            "docs/architecture.md still contains JUDGE_ORCHESTRATE_MAX_CASCADE_DEPTH. "
            "Rename to DEVBENCH_ORCHESTRATE_MAX_CASCADE_DEPTH (AC-197-8)."
        )

    def test_no_judge_orchestrator_session_id(self) -> None:
        """JUDGE_ORCHESTRATOR_SESSION_ID must not appear; use DEVBENCH_ORCHESTRATOR_SESSION_ID."""
        text = _read_doc(ARCHITECTURE_DOC)
        assert "JUDGE_ORCHESTRATOR_SESSION_ID" not in text, (
            "docs/architecture.md still contains JUDGE_ORCHESTRATOR_SESSION_ID. "
            "Rename to DEVBENCH_ORCHESTRATOR_SESSION_ID (AC-197-8)."
        )

    def test_no_judge_ci_failure_retry_enabled(self) -> None:
        """JUDGE_CI_FAILURE_RETRY_ENABLED must not appear; use DEVBENCH_ prefix."""
        text = _read_doc(ARCHITECTURE_DOC)
        assert "JUDGE_CI_FAILURE_RETRY_ENABLED" not in text, (
            "docs/architecture.md still contains JUDGE_CI_FAILURE_RETRY_ENABLED. "
            "Rename to DEVBENCH_CI_FAILURE_RETRY_ENABLED (AC-197-8)."
        )

    def test_no_judge_pr_review_resolution_enabled(self) -> None:
        """JUDGE_PR_REVIEW_RESOLUTION_ENABLED must not appear; use DEVBENCH_ prefix."""
        text = _read_doc(ARCHITECTURE_DOC)
        assert "JUDGE_PR_REVIEW_RESOLUTION_ENABLED" not in text, (
            "docs/architecture.md still contains JUDGE_PR_REVIEW_RESOLUTION_ENABLED. "
            "Rename to DEVBENCH_PR_REVIEW_RESOLUTION_ENABLED (AC-197-8)."
        )

    def test_no_judge_pr_review_settle_seconds(self) -> None:
        """JUDGE_PR_REVIEW_SETTLE_SECONDS must not appear; use DEVBENCH_ prefix."""
        text = _read_doc(ARCHITECTURE_DOC)
        assert "JUDGE_PR_REVIEW_SETTLE_SECONDS" not in text, (
            "docs/architecture.md still contains JUDGE_PR_REVIEW_SETTLE_SECONDS. "
            "Rename to DEVBENCH_PR_REVIEW_SETTLE_SECONDS (AC-197-8)."
        )

    @pytest.mark.parametrize(
        "legacy_var",
        [
            "JUDGE_CHECK_REGISTRATION_RETRIES",
            "JUDGE_CHECK_REGISTRATION_DELAY_SECONDS",
            "JUDGE_STOP_MAX_BLOCKS",
            "JUDGE_STOP_WINDOW_SECONDS",
            "JUDGE_STOP_STALE_MINUTES",
        ],
    )
    def test_no_stop_hook_legacy_vars(self, legacy_var: str) -> None:
        """Stop-hook JUDGE_* operational env vars must not appear in architecture.md."""
        text = _read_doc(ARCHITECTURE_DOC)
        assert legacy_var not in text, (
            f"docs/architecture.md still contains {legacy_var}. "
            f"Rename to DEVBENCH_{legacy_var.removeprefix('JUDGE_')} (AC-197-8)."
        )

    def test_devbench_workspace_root_present(self) -> None:
        """DEVBENCH_WORKSPACE_ROOT must appear in architecture.md."""
        text = _read_doc(ARCHITECTURE_DOC)
        assert "DEVBENCH_WORKSPACE_ROOT" in text, (
            "docs/architecture.md must use DEVBENCH_WORKSPACE_ROOT as the canonical workspace-root env var (AC-197-8)."
        )

    def test_devbench_config_path_present(self) -> None:
        """DEVBENCH_CONFIG_PATH must appear in architecture.md."""
        text = _read_doc(ARCHITECTURE_DOC)
        assert "DEVBENCH_CONFIG_PATH" in text, (
            "docs/architecture.md must document DEVBENCH_CONFIG_PATH as the config-path env var (AC-197-8)."
        )

    def test_judge_agent_role_unchanged(self) -> None:
        """JUDGE_AGENT_ROLE must still appear in architecture.md (ADR-15 exempt, AC-197-13)."""
        text = _read_doc(ARCHITECTURE_DOC)
        assert "JUDGE_AGENT_ROLE" in text, (
            "docs/architecture.md must still reference JUDGE_AGENT_ROLE -- "
            "this identifier is exempt from the rename per AC-197-13 / ADR-15 "
            "(it names the orchestrator-tier bypass indicator, not an operational "
            "env var in the renamed namespace)."
        )


# ---------------------------------------------------------------------------
# docs/model-pricing.md
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestModelPricingNoLegacyJudgeVars:
    """docs/model-pricing.md must not reference any JUDGE_* operational env vars."""

    def test_no_judge_claude_model(self) -> None:
        """JUDGE_CLAUDE_MODEL must not appear; use DEVBENCH_CLAUDE_MODEL."""
        text = _read_doc(MODEL_PRICING_DOC)
        assert "JUDGE_CLAUDE_MODEL" not in text, (
            "docs/model-pricing.md still contains JUDGE_CLAUDE_MODEL. Rename to DEVBENCH_CLAUDE_MODEL (AC-197-8)."
        )

    def test_no_judge_display_timezone(self) -> None:
        """JUDGE_DISPLAY_TIMEZONE must not appear; use DEVBENCH_DISPLAY_TIMEZONE."""
        text = _read_doc(MODEL_PRICING_DOC)
        assert "JUDGE_DISPLAY_TIMEZONE" not in text, (
            "docs/model-pricing.md still contains JUDGE_DISPLAY_TIMEZONE. "
            "Rename to DEVBENCH_DISPLAY_TIMEZONE (AC-197-8)."
        )

    def test_no_judge_report_timezone(self) -> None:
        """JUDGE_REPORT_TIMEZONE must not appear; use DEVBENCH_REPORT_TIMEZONE."""
        text = _read_doc(MODEL_PRICING_DOC)
        assert "JUDGE_REPORT_TIMEZONE" not in text, (
            "docs/model-pricing.md still contains JUDGE_REPORT_TIMEZONE. Rename to DEVBENCH_REPORT_TIMEZONE (AC-197-8)."
        )

    def test_no_judge_report_token_cost_discount(self) -> None:
        """JUDGE_REPORT_TOKEN_COST_DISCOUNT must not appear; use DEVBENCH_ prefix."""
        text = _read_doc(MODEL_PRICING_DOC)
        assert "JUDGE_REPORT_TOKEN_COST_DISCOUNT" not in text, (
            "docs/model-pricing.md still contains JUDGE_REPORT_TOKEN_COST_DISCOUNT. "
            "Rename to DEVBENCH_REPORT_TOKEN_COST_DISCOUNT (AC-197-8)."
        )

    @pytest.mark.parametrize(
        "legacy_var",
        [
            "JUDGE_REPORT_CACHE_READ_MULTIPLIER",
            "JUDGE_REPORT_CACHE_WRITE_5MIN_MULTIPLIER",
            "JUDGE_REPORT_CACHE_WRITE_1HR_MULTIPLIER",
            "JUDGE_REPORT_DATA_RESIDENCY_MULTIPLIER",
        ],
    )
    def test_no_report_multiplier_legacy_vars(self, legacy_var: str) -> None:
        """Report-multiplier JUDGE_* env vars must not appear in model-pricing.md."""
        text = _read_doc(MODEL_PRICING_DOC)
        assert legacy_var not in text, (
            f"docs/model-pricing.md still contains {legacy_var}. "
            f"Rename to DEVBENCH_{legacy_var.removeprefix('JUDGE_')} (AC-197-8)."
        )

    def test_devbench_claude_model_present(self) -> None:
        """DEVBENCH_CLAUDE_MODEL must appear in model-pricing.md."""
        text = _read_doc(MODEL_PRICING_DOC)
        assert "DEVBENCH_CLAUDE_MODEL" in text, (
            "docs/model-pricing.md must use DEVBENCH_CLAUDE_MODEL as the canonical model env var reference (AC-197-8)."
        )

    def test_devbench_report_token_cost_discount_present(self) -> None:
        """DEVBENCH_REPORT_TOKEN_COST_DISCOUNT must appear in model-pricing.md."""
        text = _read_doc(MODEL_PRICING_DOC)
        assert "DEVBENCH_REPORT_TOKEN_COST_DISCOUNT" in text, (
            "docs/model-pricing.md must use DEVBENCH_REPORT_TOKEN_COST_DISCOUNT "
            "as the canonical discount env var (AC-197-8)."
        )


# ---------------------------------------------------------------------------
# docs/manual-blockers.md
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestManualBlockersNoLegacyJudgeVars:
    """docs/manual-blockers.md must not reference any JUDGE_* operational env vars."""

    def test_no_judge_workspace_root(self) -> None:
        """JUDGE_WORKSPACE_ROOT must not appear; use DEVBENCH_WORKSPACE_ROOT."""
        text = _read_doc(MANUAL_BLOCKERS_DOC)
        assert "JUDGE_WORKSPACE_ROOT" not in text, (
            "docs/manual-blockers.md still contains JUDGE_WORKSPACE_ROOT. Rename to DEVBENCH_WORKSPACE_ROOT (AC-197-8)."
        )

    def test_no_judge_claude_model(self) -> None:
        """JUDGE_CLAUDE_MODEL must not appear; use DEVBENCH_CLAUDE_MODEL."""
        text = _read_doc(MANUAL_BLOCKERS_DOC)
        assert "JUDGE_CLAUDE_MODEL" not in text, (
            "docs/manual-blockers.md still contains JUDGE_CLAUDE_MODEL. Rename to DEVBENCH_CLAUDE_MODEL (AC-197-8)."
        )

    def test_devbench_workspace_root_present(self) -> None:
        """DEVBENCH_WORKSPACE_ROOT must appear in manual-blockers.md command examples."""
        text = _read_doc(MANUAL_BLOCKERS_DOC)
        assert "DEVBENCH_WORKSPACE_ROOT" in text, (
            "docs/manual-blockers.md must use DEVBENCH_WORKSPACE_ROOT in command examples (AC-197-8)."
        )

    def test_devbench_claude_model_present(self) -> None:
        """DEVBENCH_CLAUDE_MODEL must appear in manual-blockers.md command examples."""
        text = _read_doc(MANUAL_BLOCKERS_DOC)
        assert "DEVBENCH_CLAUDE_MODEL" in text, (
            "docs/manual-blockers.md must use DEVBENCH_CLAUDE_MODEL in command examples (AC-197-8)."
        )


# ---------------------------------------------------------------------------
# Cross-doc: no backwards-compatibility footnotes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNoBackwardsCompatibilityFootnotesInBatch2:
    """No per-var backwards-compatibility footnotes may appear in the batch-2 docs."""

    @pytest.mark.parametrize(
        "doc_path",
        [ARCHITECTURE_DOC, MODEL_PRICING_DOC, MANUAL_BLOCKERS_DOC],
        ids=["architecture", "model-pricing", "manual-blockers"],
    )
    def test_no_backwards_compat_footnotes(self, doc_path: Path) -> None:
        """The rename is hard; no per-var env-var 'Backwards compatibility' notes allowed.

        Searches for patterns that indicate a JUDGE_* env var is being described as
        still accepted alongside DEVBENCH_*. CLI flag deprecation notes are not
        env-var backwards-compat notes and are not flagged.
        """
        text = _read_doc(doc_path)
        lines = text.splitlines()
        violations: list[str] = []
        for line in lines:
            lower = line.lower()
            has_judge_ref = "judge_" in lower
            has_bc_language = (
                "still accepts" in lower
                or "also accepts" in lower
                or "legacy name" in lower
                or ("backwards compat" in lower and has_judge_ref)
                or ("backward compat" in lower and has_judge_ref)
                or ("backwards-compat" in lower and has_judge_ref)
                or ("backward-compat" in lower and has_judge_ref)
            )
            if has_bc_language and has_judge_ref:
                violations.append(line.strip())
        assert not violations, (
            f"{doc_path.name} contains per-var backwards-compatibility notes for "
            f"JUDGE_* env vars. The rename is hard; compatibility notes are forbidden "
            f"(AC-197-8 / spec section 4.9.3). Violations:\n" + "\n".join(f"  {v}" for v in violations)
        )
