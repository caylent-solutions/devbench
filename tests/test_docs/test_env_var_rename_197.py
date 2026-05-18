"""Structural pins for AC-197-8: JUDGE_* -> DEVBENCH_* rename in canonical docs.

Verifies that docs/cli-reference.md, docs/zero-to-ready.md, and
docs/llm-authentication.md use DEVBENCH_* as the canonical and ONLY name for every
operational env var. Also verifies that docs/llm-authentication.md carries a single
migration banner at the top directing operators to ``devbench migrate-env``.

No per-var backwards-compatibility footnotes may appear in any of these docs.

Spec source: spec/devbench-self-improve.md section 4.9. Issue: #197.
AC: AC-197-8.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
CLI_REFERENCE_DOC = REPO_ROOT / "docs" / "cli-reference.md"
ZERO_TO_READY_DOC = REPO_ROOT / "docs" / "zero-to-ready.md"
LLM_AUTH_DOC = REPO_ROOT / "docs" / "llm-authentication.md"

# Env-var names that refer to the LLM-as-judge concept and must NOT be renamed.
# Per spec section 4.9.6: "NOT renamed (these refer to the LLM-as-judge concept
# which survives the rename intact, and renaming them would lose semantic meaning)".
_JUDGE_CONCEPT_ALLOWLIST: frozenset[str] = frozenset(
    {
        "KNOWN_JUDGE_NAMES",
        "REVIEW_JUDGE_NAMES",
        "SECURITY_JUDGE_NAMES",
        "ALL_REQUIRED_JUDGE_NAMES",
        "WORKFLOW_AGENT_JUDGE_NAMES",
    }
)

# Audit-comment format tokens that reference the LLM-as-judge concept: also exempt.
_AUDIT_FORMAT_ALLOWLIST: frozenset[str] = frozenset(
    {
        "JUDGE_VERDICT",  # appears in [JUDGE_*_VERDICT] audit format lines
        "JUDGE_AGENT_ROLE",  # ADR-15 orchestrator bypass indicator
    }
)


def _read_doc(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _legacy_judge_env_occurrences(text: str) -> list[str]:
    """Return every JUDGE_<WORD> token that is an operational env var (not the exempt set)."""
    # Match all JUDGE_<UPPERCASE_WORD> tokens
    all_matches = re.findall(r"\bJUDGE_[A-Z_]+", text)
    violations: list[str] = []
    for token in all_matches:
        # Skip tokens that refer to the LLM-as-judge concept
        if token in _JUDGE_CONCEPT_ALLOWLIST:
            continue
        # Skip audit comment format patterns (e.g. JUDGE_AGENT_ROLE used in ADR-15 prose)
        skip = False
        for exempt in _AUDIT_FORMAT_ALLOWLIST:
            if exempt in token:
                skip = True
                break
        if not skip:
            violations.append(token)
    return violations


# ---------------------------------------------------------------------------
# docs/cli-reference.md
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCliReferenceNoLegacyJudgeVars:
    """docs/cli-reference.md must not reference any JUDGE_* operational env vars."""

    def test_no_judge_workspace_root(self) -> None:
        """JUDGE_WORKSPACE_ROOT must not appear; use DEVBENCH_WORKSPACE_ROOT."""
        text = _read_doc(CLI_REFERENCE_DOC)
        assert "JUDGE_WORKSPACE_ROOT" not in text, (
            "docs/cli-reference.md still contains JUDGE_WORKSPACE_ROOT. Rename to DEVBENCH_WORKSPACE_ROOT (AC-197-8)."
        )

    def test_no_judge_claude_model(self) -> None:
        """JUDGE_CLAUDE_MODEL must not appear; use DEVBENCH_CLAUDE_MODEL."""
        text = _read_doc(CLI_REFERENCE_DOC)
        assert "JUDGE_CLAUDE_MODEL" not in text, (
            "docs/cli-reference.md still contains JUDGE_CLAUDE_MODEL. Rename to DEVBENCH_CLAUDE_MODEL (AC-197-8)."
        )

    def test_no_judge_config_path(self) -> None:
        """JUDGE_CONFIG_PATH must not appear; use DEVBENCH_CONFIG_PATH."""
        text = _read_doc(CLI_REFERENCE_DOC)
        assert "JUDGE_CONFIG_PATH" not in text, (
            "docs/cli-reference.md still contains JUDGE_CONFIG_PATH. Rename to DEVBENCH_CONFIG_PATH (AC-197-8)."
        )

    def test_no_judge_log_file(self) -> None:
        """JUDGE_LOG_FILE must not appear; use DEVBENCH_LOG_FILE."""
        text = _read_doc(CLI_REFERENCE_DOC)
        assert "JUDGE_LOG_FILE" not in text, (
            "docs/cli-reference.md still contains JUDGE_LOG_FILE. Rename to DEVBENCH_LOG_FILE (AC-197-8)."
        )

    def test_no_judge_blocked_recovery_window_seconds(self) -> None:
        """JUDGE_BLOCKED_RECOVERY_WINDOW_SECONDS must not appear; use DEVBENCH_ prefix."""
        text = _read_doc(CLI_REFERENCE_DOC)
        assert "JUDGE_BLOCKED_RECOVERY_WINDOW_SECONDS" not in text, (
            "docs/cli-reference.md still contains JUDGE_BLOCKED_RECOVERY_WINDOW_SECONDS. "
            "Rename to DEVBENCH_BLOCKED_RECOVERY_WINDOW_SECONDS (AC-197-8)."
        )

    def test_no_judge_orchestrator_session_id(self) -> None:
        """JUDGE_ORCHESTRATOR_SESSION_ID must not appear; use DEVBENCH_ORCHESTRATOR_SESSION_ID."""
        text = _read_doc(CLI_REFERENCE_DOC)
        assert "JUDGE_ORCHESTRATOR_SESSION_ID" not in text, (
            "docs/cli-reference.md still contains JUDGE_ORCHESTRATOR_SESSION_ID. "
            "Rename to DEVBENCH_ORCHESTRATOR_SESSION_ID (AC-197-8)."
        )

    def test_no_judge_display_timezone(self) -> None:
        """JUDGE_DISPLAY_TIMEZONE must not appear; use DEVBENCH_DISPLAY_TIMEZONE."""
        text = _read_doc(CLI_REFERENCE_DOC)
        assert "JUDGE_DISPLAY_TIMEZONE" not in text, (
            "docs/cli-reference.md still contains JUDGE_DISPLAY_TIMEZONE. "
            "Rename to DEVBENCH_DISPLAY_TIMEZONE (AC-197-8)."
        )

    def test_no_judge_ci_failure_retry_enabled(self) -> None:
        """JUDGE_CI_FAILURE_RETRY_ENABLED must not appear; use DEVBENCH_ prefix."""
        text = _read_doc(CLI_REFERENCE_DOC)
        assert "JUDGE_CI_FAILURE_RETRY_ENABLED" not in text, (
            "docs/cli-reference.md still contains JUDGE_CI_FAILURE_RETRY_ENABLED. "
            "Rename to DEVBENCH_CI_FAILURE_RETRY_ENABLED (AC-197-8)."
        )

    def test_no_judge_ci_failure_log_bytes(self) -> None:
        """JUDGE_CI_FAILURE_LOG_BYTES must not appear; use DEVBENCH_ prefix."""
        text = _read_doc(CLI_REFERENCE_DOC)
        assert "JUDGE_CI_FAILURE_LOG_BYTES" not in text, (
            "docs/cli-reference.md still contains JUDGE_CI_FAILURE_LOG_BYTES. "
            "Rename to DEVBENCH_CI_FAILURE_LOG_BYTES (AC-197-8)."
        )

    def test_no_judge_pr_review_resolution_enabled(self) -> None:
        """JUDGE_PR_REVIEW_RESOLUTION_ENABLED must not appear; use DEVBENCH_ prefix."""
        text = _read_doc(CLI_REFERENCE_DOC)
        assert "JUDGE_PR_REVIEW_RESOLUTION_ENABLED" not in text, (
            "docs/cli-reference.md still contains JUDGE_PR_REVIEW_RESOLUTION_ENABLED. "
            "Rename to DEVBENCH_PR_REVIEW_RESOLUTION_ENABLED (AC-197-8)."
        )

    def test_no_judge_pr_review_agents(self) -> None:
        """JUDGE_PR_REVIEW_AGENTS must not appear; use DEVBENCH_ prefix."""
        text = _read_doc(CLI_REFERENCE_DOC)
        assert "JUDGE_PR_REVIEW_AGENTS" not in text, (
            "docs/cli-reference.md still contains JUDGE_PR_REVIEW_AGENTS. "
            "Rename to DEVBENCH_PR_REVIEW_AGENTS (AC-197-8)."
        )

    def test_no_judge_pr_review_decision_blocks(self) -> None:
        """JUDGE_PR_REVIEW_DECISION_BLOCKS must not appear; use DEVBENCH_ prefix."""
        text = _read_doc(CLI_REFERENCE_DOC)
        assert "JUDGE_PR_REVIEW_DECISION_BLOCKS" not in text, (
            "docs/cli-reference.md still contains JUDGE_PR_REVIEW_DECISION_BLOCKS. "
            "Rename to DEVBENCH_PR_REVIEW_DECISION_BLOCKS (AC-197-8)."
        )

    def test_no_judge_pr_review_settle_seconds(self) -> None:
        """JUDGE_PR_REVIEW_SETTLE_SECONDS must not appear; use DEVBENCH_ prefix."""
        text = _read_doc(CLI_REFERENCE_DOC)
        assert "JUDGE_PR_REVIEW_SETTLE_SECONDS" not in text, (
            "docs/cli-reference.md still contains JUDGE_PR_REVIEW_SETTLE_SECONDS. "
            "Rename to DEVBENCH_PR_REVIEW_SETTLE_SECONDS (AC-197-8)."
        )

    def test_no_judge_pr_review_poll_interval(self) -> None:
        """JUDGE_PR_REVIEW_POLL_INTERVAL must not appear; use DEVBENCH_ prefix."""
        text = _read_doc(CLI_REFERENCE_DOC)
        assert "JUDGE_PR_REVIEW_POLL_INTERVAL" not in text, (
            "docs/cli-reference.md still contains JUDGE_PR_REVIEW_POLL_INTERVAL. "
            "Rename to DEVBENCH_PR_REVIEW_POLL_INTERVAL (AC-197-8)."
        )

    def test_no_judge_check_registration_retries(self) -> None:
        """JUDGE_CHECK_REGISTRATION_RETRIES must not appear; use DEVBENCH_ prefix."""
        text = _read_doc(CLI_REFERENCE_DOC)
        assert "JUDGE_CHECK_REGISTRATION_RETRIES" not in text, (
            "docs/cli-reference.md still contains JUDGE_CHECK_REGISTRATION_RETRIES. "
            "Rename to DEVBENCH_CHECK_REGISTRATION_RETRIES (AC-197-8)."
        )

    def test_no_judge_check_registration_delay_seconds(self) -> None:
        """JUDGE_CHECK_REGISTRATION_DELAY_SECONDS must not appear; use DEVBENCH_ prefix."""
        text = _read_doc(CLI_REFERENCE_DOC)
        assert "JUDGE_CHECK_REGISTRATION_DELAY_SECONDS" not in text, (
            "docs/cli-reference.md still contains JUDGE_CHECK_REGISTRATION_DELAY_SECONDS. "
            "Rename to DEVBENCH_CHECK_REGISTRATION_DELAY_SECONDS (AC-197-8)."
        )

    def test_no_judge_agent_model_vars_in_cli_ref(self) -> None:
        """JUDGE_AGENT_MODEL_<NAME> must not appear; use DEVBENCH_AGENT_MODEL_<NAME>."""
        text = _read_doc(CLI_REFERENCE_DOC)
        assert "JUDGE_AGENT_MODEL_" not in text, (
            "docs/cli-reference.md still contains JUDGE_AGENT_MODEL_ references. "
            "Rename to DEVBENCH_AGENT_MODEL_<NAME> (AC-197-8)."
        )

    def test_devbench_workspace_root_present(self) -> None:
        """DEVBENCH_WORKSPACE_ROOT must appear in cli-reference.md."""
        text = _read_doc(CLI_REFERENCE_DOC)
        assert "DEVBENCH_WORKSPACE_ROOT" in text, (
            "docs/cli-reference.md must document DEVBENCH_WORKSPACE_ROOT as the required env var (AC-197-8)."
        )

    def test_devbench_claude_model_present(self) -> None:
        """DEVBENCH_CLAUDE_MODEL must appear in cli-reference.md."""
        text = _read_doc(CLI_REFERENCE_DOC)
        assert "DEVBENCH_CLAUDE_MODEL" in text, (
            "docs/cli-reference.md must document DEVBENCH_CLAUDE_MODEL as the required env var (AC-197-8)."
        )

    @pytest.mark.parametrize(
        "legacy_var",
        [
            "JUDGE_USE_BEDROCK",
            "JUDGE_BEDROCK_REGION",
            "JUDGE_SAFE_PERMISSIONS",
        ],
    )
    def test_no_legacy_judge_vars_parametrized(self, legacy_var: str) -> None:
        """Additional JUDGE_* operational env vars must not appear."""
        text = _read_doc(CLI_REFERENCE_DOC)
        assert legacy_var not in text, (
            f"docs/cli-reference.md still contains {legacy_var}. "
            f"Rename to DEVBENCH_{legacy_var.removeprefix('JUDGE_')} (AC-197-8)."
        )


# ---------------------------------------------------------------------------
# docs/zero-to-ready.md
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestZeroToReadyNoLegacyJudgeVars:
    """docs/zero-to-ready.md must not reference any JUDGE_* operational env vars."""

    def test_no_judge_workspace_root(self) -> None:
        """JUDGE_WORKSPACE_ROOT must not appear; use DEVBENCH_WORKSPACE_ROOT."""
        text = _read_doc(ZERO_TO_READY_DOC)
        assert "JUDGE_WORKSPACE_ROOT" not in text, (
            "docs/zero-to-ready.md still contains JUDGE_WORKSPACE_ROOT. Rename to DEVBENCH_WORKSPACE_ROOT (AC-197-8)."
        )

    def test_no_judge_claude_model(self) -> None:
        """JUDGE_CLAUDE_MODEL must not appear; use DEVBENCH_CLAUDE_MODEL."""
        text = _read_doc(ZERO_TO_READY_DOC)
        assert "JUDGE_CLAUDE_MODEL" not in text, (
            "docs/zero-to-ready.md still contains JUDGE_CLAUDE_MODEL. Rename to DEVBENCH_CLAUDE_MODEL (AC-197-8)."
        )

    def test_no_judge_use_bedrock(self) -> None:
        """JUDGE_USE_BEDROCK must not appear; use DEVBENCH_USE_BEDROCK."""
        text = _read_doc(ZERO_TO_READY_DOC)
        assert "JUDGE_USE_BEDROCK" not in text, (
            "docs/zero-to-ready.md still contains JUDGE_USE_BEDROCK. Rename to DEVBENCH_USE_BEDROCK (AC-197-8)."
        )

    def test_no_judge_bedrock_region(self) -> None:
        """JUDGE_BEDROCK_REGION must not appear; use DEVBENCH_BEDROCK_REGION."""
        text = _read_doc(ZERO_TO_READY_DOC)
        assert "JUDGE_BEDROCK_REGION" not in text, (
            "docs/zero-to-ready.md still contains JUDGE_BEDROCK_REGION. Rename to DEVBENCH_BEDROCK_REGION (AC-197-8)."
        )

    def test_no_judge_safe_permissions(self) -> None:
        """JUDGE_SAFE_PERMISSIONS must not appear; use DEVBENCH_SAFE_PERMISSIONS."""
        text = _read_doc(ZERO_TO_READY_DOC)
        assert "JUDGE_SAFE_PERMISSIONS" not in text, (
            "docs/zero-to-ready.md still contains JUDGE_SAFE_PERMISSIONS. "
            "Rename to DEVBENCH_SAFE_PERMISSIONS (AC-197-8)."
        )

    def test_no_judge_agent_model_vars(self) -> None:
        """JUDGE_AGENT_MODEL_<NAME> must not appear in zero-to-ready.md."""
        text = _read_doc(ZERO_TO_READY_DOC)
        assert "JUDGE_AGENT_MODEL_" not in text, (
            "docs/zero-to-ready.md still contains JUDGE_AGENT_MODEL_ references. "
            "Rename to DEVBENCH_AGENT_MODEL_<NAME> (AC-197-8)."
        )

    def test_devbench_workspace_root_present(self) -> None:
        """DEVBENCH_WORKSPACE_ROOT must appear in zero-to-ready.md launch examples."""
        text = _read_doc(ZERO_TO_READY_DOC)
        assert "DEVBENCH_WORKSPACE_ROOT" in text, (
            "docs/zero-to-ready.md must use DEVBENCH_WORKSPACE_ROOT in launch examples (AC-197-8)."
        )

    def test_devbench_claude_model_present(self) -> None:
        """DEVBENCH_CLAUDE_MODEL must appear in zero-to-ready.md launch examples."""
        text = _read_doc(ZERO_TO_READY_DOC)
        assert "DEVBENCH_CLAUDE_MODEL" in text, (
            "docs/zero-to-ready.md must use DEVBENCH_CLAUDE_MODEL in launch examples (AC-197-8)."
        )

    @pytest.mark.parametrize(
        "legacy_var",
        [
            "JUDGE_LOG_FILE",
            "JUDGE_BLOCKED_RECOVERY_WINDOW_SECONDS",
            "JUDGE_ORCHESTRATOR_SESSION_ID",
            "JUDGE_DISPLAY_TIMEZONE",
            "JUDGE_CI_FAILURE_RETRY_ENABLED",
            "JUDGE_PR_REVIEW_RESOLUTION_ENABLED",
        ],
    )
    def test_no_additional_legacy_vars(self, legacy_var: str) -> None:
        """No additional JUDGE_* operational env vars may appear."""
        text = _read_doc(ZERO_TO_READY_DOC)
        assert legacy_var not in text, (
            f"docs/zero-to-ready.md still contains {legacy_var}. "
            f"Rename to DEVBENCH_{legacy_var.removeprefix('JUDGE_')} (AC-197-8)."
        )

    def test_troubleshooting_section_uses_devbench_workspace_root(self) -> None:
        """The troubleshooting section must reference DEVBENCH_WORKSPACE_ROOT, not legacy name."""
        text = _read_doc(ZERO_TO_READY_DOC)
        # The troubleshooting section specifically should not reference the old name
        troubleshoot_idx = text.lower().find("troubleshoot")
        if troubleshoot_idx == -1:
            return  # no troubleshooting section present -- not a violation
        troubleshoot_text = text[troubleshoot_idx:]
        assert "JUDGE_WORKSPACE_ROOT" not in troubleshoot_text, (
            "docs/zero-to-ready.md troubleshooting section still contains "
            "JUDGE_WORKSPACE_ROOT. Rename to DEVBENCH_WORKSPACE_ROOT (AC-197-8)."
        )


# ---------------------------------------------------------------------------
# docs/llm-authentication.md
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLlmAuthMigrationBanner:
    """docs/llm-authentication.md must carry a single migration banner at the top."""

    def test_migration_banner_present(self) -> None:
        """A migration note naming the rename must appear near the top of the doc."""
        text = _read_doc(LLM_AUTH_DOC)
        # Banner should appear in the first 2000 characters (top of the doc)
        top_section = text[:2000]
        has_banner = "migrate-env" in top_section or ("JUDGE_" in top_section and "DEVBENCH_" in top_section)
        assert has_banner, (
            "docs/llm-authentication.md must carry a migration banner near the top "
            "of the document directing operators to 'devbench migrate-env' to update "
            "their JUDGE_* env vars to DEVBENCH_* (AC-197-8)."
        )

    def test_migration_banner_references_migrate_env_command(self) -> None:
        """The migration banner must reference the 'devbench migrate-env' command."""
        text = _read_doc(LLM_AUTH_DOC)
        top_section = text[:2000]
        assert "migrate-env" in top_section, (
            "docs/llm-authentication.md migration banner must reference 'devbench migrate-env' "
            "so operators know the one-shot migration path (AC-197-8)."
        )

    def test_migration_banner_mentions_rename(self) -> None:
        """The migration banner must mention that JUDGE_* -> DEVBENCH_* is a breaking change."""
        text = _read_doc(LLM_AUTH_DOC)
        top_section = text[:2000]
        # Either explicit BREAKING mention or JUDGE_ -> DEVBENCH_ mention
        has_rename_mention = (
            "JUDGE_" in top_section and "DEVBENCH_" in top_section
        ) or "breaking" in top_section.lower()
        assert has_rename_mention, (
            "docs/llm-authentication.md migration banner must mention the JUDGE_* -> "
            "DEVBENCH_* rename so operators understand it is a breaking change (AC-197-8)."
        )


@pytest.mark.unit
class TestLlmAuthNoLegacyJudgeVars:
    """docs/llm-authentication.md must use DEVBENCH_* for all operational env vars."""

    def test_no_judge_claude_credentials_file(self) -> None:
        """JUDGE_CLAUDE_CREDENTIALS_FILE must not appear in main content; use DEVBENCH_ prefix."""
        text = _read_doc(LLM_AUTH_DOC)
        # Strip the top migration banner (first 2000 chars may legitimately mention it)
        main_content = text[2000:]
        assert "JUDGE_CLAUDE_CREDENTIALS_FILE" not in main_content, (
            "docs/llm-authentication.md configuration table still contains "
            "JUDGE_CLAUDE_CREDENTIALS_FILE. Rename to DEVBENCH_CLAUDE_CREDENTIALS_FILE "
            "(AC-197-8)."
        )

    def test_no_judge_claude_model_in_config_table(self) -> None:
        """JUDGE_CLAUDE_MODEL must not appear in the configuration tables."""
        text = _read_doc(LLM_AUTH_DOC)
        main_content = text[2000:]
        assert "JUDGE_CLAUDE_MODEL" not in main_content, (
            "docs/llm-authentication.md configuration table still contains "
            "JUDGE_CLAUDE_MODEL. Rename to DEVBENCH_CLAUDE_MODEL (AC-197-8)."
        )

    def test_no_judge_llm_timeout(self) -> None:
        """JUDGE_LLM_TIMEOUT must not appear; use DEVBENCH_LLM_TIMEOUT."""
        text = _read_doc(LLM_AUTH_DOC)
        assert "JUDGE_LLM_TIMEOUT" not in text, (
            "docs/llm-authentication.md still contains JUDGE_LLM_TIMEOUT. Rename to DEVBENCH_LLM_TIMEOUT (AC-197-8)."
        )

    def test_no_judge_use_bedrock(self) -> None:
        """JUDGE_USE_BEDROCK must not appear; use DEVBENCH_USE_BEDROCK."""
        text = _read_doc(LLM_AUTH_DOC)
        assert "JUDGE_USE_BEDROCK" not in text, (
            "docs/llm-authentication.md still contains JUDGE_USE_BEDROCK. Rename to DEVBENCH_USE_BEDROCK (AC-197-8)."
        )

    def test_no_judge_bedrock_region(self) -> None:
        """JUDGE_BEDROCK_REGION must not appear; use DEVBENCH_BEDROCK_REGION."""
        text = _read_doc(LLM_AUTH_DOC)
        assert "JUDGE_BEDROCK_REGION" not in text, (
            "docs/llm-authentication.md still contains JUDGE_BEDROCK_REGION. "
            "Rename to DEVBENCH_BEDROCK_REGION (AC-197-8)."
        )

    def test_no_judge_agent_model_vars(self) -> None:
        """JUDGE_AGENT_MODEL_<NAME> must not appear; use DEVBENCH_AGENT_MODEL_<NAME>."""
        text = _read_doc(LLM_AUTH_DOC)
        assert "JUDGE_AGENT_MODEL_" not in text, (
            "docs/llm-authentication.md still contains JUDGE_AGENT_MODEL_ references. "
            "Rename to DEVBENCH_AGENT_MODEL_<NAME> (AC-197-8)."
        )

    def test_devbench_claude_credentials_file_present(self) -> None:
        """DEVBENCH_CLAUDE_CREDENTIALS_FILE must appear in the configuration table."""
        text = _read_doc(LLM_AUTH_DOC)
        assert "DEVBENCH_CLAUDE_CREDENTIALS_FILE" in text, (
            "docs/llm-authentication.md must use DEVBENCH_CLAUDE_CREDENTIALS_FILE in "
            "the configuration table (AC-197-8)."
        )

    def test_devbench_use_bedrock_present(self) -> None:
        """DEVBENCH_USE_BEDROCK must appear in the Bedrock configuration table."""
        text = _read_doc(LLM_AUTH_DOC)
        assert "DEVBENCH_USE_BEDROCK" in text, (
            "docs/llm-authentication.md must use DEVBENCH_USE_BEDROCK in the Bedrock configuration table (AC-197-8)."
        )

    def test_devbench_bedrock_region_present(self) -> None:
        """DEVBENCH_BEDROCK_REGION must appear in the Bedrock configuration table."""
        text = _read_doc(LLM_AUTH_DOC)
        assert "DEVBENCH_BEDROCK_REGION" in text, (
            "docs/llm-authentication.md must use DEVBENCH_BEDROCK_REGION in the Bedrock configuration table (AC-197-8)."
        )

    def test_devbench_llm_timeout_present(self) -> None:
        """DEVBENCH_LLM_TIMEOUT must appear in the configuration tables."""
        text = _read_doc(LLM_AUTH_DOC)
        assert "DEVBENCH_LLM_TIMEOUT" in text, (
            "docs/llm-authentication.md must use DEVBENCH_LLM_TIMEOUT in the configuration tables (AC-197-8)."
        )

    @pytest.mark.parametrize(
        "legacy_var",
        [
            "JUDGE_CLAUDE_CREDENTIALS_FILE",
            "JUDGE_CLAUDE_MODEL",
            "JUDGE_LLM_TIMEOUT",
            "JUDGE_USE_BEDROCK",
            "JUDGE_BEDROCK_REGION",
            "JUDGE_AGENT_MODEL_EXECUTOR",
            "JUDGE_AGENT_MODEL_CODE_REVIEWER",
            "JUDGE_AGENT_MODEL_CHANGES_MANIFEST",
        ],
    )
    def test_no_legacy_vars_parametrized(self, legacy_var: str) -> None:
        """Every JUDGE_* operational env var in llm-authentication.md must be renamed."""
        text = _read_doc(LLM_AUTH_DOC)
        assert legacy_var not in text, (
            f"docs/llm-authentication.md still contains {legacy_var}. "
            f"Rename to DEVBENCH_{legacy_var.removeprefix('JUDGE_')} (AC-197-8)."
        )


@pytest.mark.unit
class TestNoBackwardsCompatibilityFootnotes:
    """No per-var backwards-compatibility footnotes may appear in any of the three docs."""

    @pytest.mark.parametrize(
        "doc_path",
        [CLI_REFERENCE_DOC, ZERO_TO_READY_DOC, LLM_AUTH_DOC],
        ids=["cli-reference", "zero-to-ready", "llm-authentication"],
    )
    def test_no_backwards_compat_footnotes(self, doc_path: Path) -> None:
        """The rename is hard; no per-var env-var 'Backwards compatibility' notes allowed.

        Searches for patterns that indicate a JUDGE_* env var is being described as
        still accepted alongside DEVBENCH_*. CLI flag deprecation notes (e.g. --watch N)
        are not env-var backwards-compat notes and are not flagged.
        """
        text = _read_doc(doc_path)
        # Look for JUDGE_ mentioned alongside backwards-compatibility language
        # (both tokens must appear near each other to be a violation)
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
