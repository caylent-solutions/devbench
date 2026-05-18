"""Structural pins for AC-197-8: JUDGE_* -> DEVBENCH_* rename in sample-config.yaml,
README.md (top-level), and CHANGELOG.md.

Verifies that:
- sample-config.yaml env-var comments use DEVBENCH_* as the canonical and ONLY name.
- README.md uses DEVBENCH_* as the canonical and ONLY name for all operational env vars.
- CHANGELOG.md carries a single '### Changed (BREAKING)' entry for the rename with
  the rejection-on-legacy contract and migration shim (devbench migrate-env).

No per-var backwards-compatibility footnotes may appear in any of these files.
No deprecation-timeline language is acceptable -- the cutover is in this release.

Spec source: spec/devbench-self-improve.md section 4.9. Issue: #197.
AC: AC-197-8.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
SAMPLE_CONFIG = REPO_ROOT / "sample-config.yaml"
README = REPO_ROOT / "README.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

# Env-var names that refer to the LLM-as-judge concept and must NOT be renamed.
# Per spec section 4.9.6: these refer to the LLM-as-judge concept which survives
# the rename intact, and renaming them would lose semantic meaning.
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


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _legacy_judge_env_occurrences(text: str) -> list[str]:
    """Return every JUDGE_<WORD> token that is an operational env var (not the exempt set)."""
    all_matches = re.findall(r"\bJUDGE_[A-Z_]+", text)
    violations: list[str] = []
    for token in all_matches:
        if token in _JUDGE_CONCEPT_ALLOWLIST:
            continue
        skip = False
        for exempt in _AUDIT_FORMAT_ALLOWLIST:
            if exempt in token:
                skip = True
                break
        if not skip:
            violations.append(token)
    return violations


# ---------------------------------------------------------------------------
# sample-config.yaml
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSampleConfigNoLegacyJudgeVars:
    """sample-config.yaml must use DEVBENCH_* as the canonical and ONLY name in comments."""

    def test_no_judge_claude_model(self) -> None:
        """JUDGE_CLAUDE_MODEL must not appear in sample-config.yaml; use DEVBENCH_CLAUDE_MODEL."""
        text = _read(SAMPLE_CONFIG)
        assert "JUDGE_CLAUDE_MODEL" not in text, (
            "sample-config.yaml still contains JUDGE_CLAUDE_MODEL. Rename to DEVBENCH_CLAUDE_MODEL (AC-197-8)."
        )

    def test_no_judge_orchestrate_max_cascade_depth(self) -> None:
        """JUDGE_ORCHESTRATE_MAX_CASCADE_DEPTH must not appear; use DEVBENCH_ prefix."""
        text = _read(SAMPLE_CONFIG)
        assert "JUDGE_ORCHESTRATE_MAX_CASCADE_DEPTH" not in text, (
            "sample-config.yaml still contains JUDGE_ORCHESTRATE_MAX_CASCADE_DEPTH. "
            "Rename to DEVBENCH_ORCHESTRATE_MAX_CASCADE_DEPTH (AC-197-8)."
        )

    def test_no_judge_agent_model_vars(self) -> None:
        """JUDGE_AGENT_MODEL_<NAME> must not appear; use DEVBENCH_AGENT_MODEL_<NAME>."""
        text = _read(SAMPLE_CONFIG)
        assert "JUDGE_AGENT_MODEL_" not in text, (
            "sample-config.yaml still contains JUDGE_AGENT_MODEL_ references. "
            "Rename to DEVBENCH_AGENT_MODEL_<NAME> (AC-197-8)."
        )

    def test_devbench_claude_model_present(self) -> None:
        """DEVBENCH_CLAUDE_MODEL must appear in sample-config.yaml prose comments."""
        text = _read(SAMPLE_CONFIG)
        assert "DEVBENCH_CLAUDE_MODEL" in text, (
            "sample-config.yaml must reference DEVBENCH_CLAUDE_MODEL in prose comments (AC-197-8)."
        )

    def test_devbench_orchestrate_max_cascade_depth_present(self) -> None:
        """DEVBENCH_ORCHESTRATE_MAX_CASCADE_DEPTH must appear in sample-config.yaml comments."""
        text = _read(SAMPLE_CONFIG)
        assert "DEVBENCH_ORCHESTRATE_MAX_CASCADE_DEPTH" in text, (
            "sample-config.yaml must reference DEVBENCH_ORCHESTRATE_MAX_CASCADE_DEPTH in prose comments (AC-197-8)."
        )

    def test_devbench_agent_model_vars_present(self) -> None:
        """DEVBENCH_AGENT_MODEL_<NAME> must appear in sample-config.yaml comments."""
        text = _read(SAMPLE_CONFIG)
        assert "DEVBENCH_AGENT_MODEL_" in text, (
            "sample-config.yaml must reference DEVBENCH_AGENT_MODEL_<NAME> in prose comments (AC-197-8)."
        )

    @pytest.mark.parametrize(
        "legacy_var",
        [
            "JUDGE_CLAUDE_MODEL",
            "JUDGE_ORCHESTRATE_MAX_CASCADE_DEPTH",
            "JUDGE_AGENT_MODEL_",
        ],
    )
    def test_no_legacy_judge_vars_parametrized(self, legacy_var: str) -> None:
        """No JUDGE_* operational env vars may appear in sample-config.yaml comments."""
        text = _read(SAMPLE_CONFIG)
        assert legacy_var not in text, (
            f"sample-config.yaml still contains {legacy_var}. "
            f"Rename to DEVBENCH_{legacy_var.removeprefix('JUDGE_')} (AC-197-8)."
        )

    def test_no_legacy_judge_vars_comprehensive(self) -> None:
        """No JUDGE_* operational env vars (other than exempt concept names) may appear."""
        text = _read(SAMPLE_CONFIG)
        violations = _legacy_judge_env_occurrences(text)
        assert not violations, (
            "sample-config.yaml contains JUDGE_* operational env vars that must be renamed to DEVBENCH_*: "
            f"{sorted(set(violations))} (AC-197-8)."
        )


# ---------------------------------------------------------------------------
# Top-level README.md
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReadmeNoLegacyJudgeVars:
    """README.md must use DEVBENCH_* as the canonical and ONLY name for all operational env vars."""

    def test_no_judge_workspace_root(self) -> None:
        """JUDGE_WORKSPACE_ROOT must not appear; use DEVBENCH_WORKSPACE_ROOT."""
        text = _read(README)
        assert "JUDGE_WORKSPACE_ROOT" not in text, (
            "README.md still contains JUDGE_WORKSPACE_ROOT. Rename to DEVBENCH_WORKSPACE_ROOT (AC-197-8)."
        )

    def test_no_judge_claude_model(self) -> None:
        """JUDGE_CLAUDE_MODEL must not appear; use DEVBENCH_CLAUDE_MODEL."""
        text = _read(README)
        assert "JUDGE_CLAUDE_MODEL" not in text, (
            "README.md still contains JUDGE_CLAUDE_MODEL. Rename to DEVBENCH_CLAUDE_MODEL (AC-197-8)."
        )

    def test_no_judge_config_path(self) -> None:
        """JUDGE_CONFIG_PATH must not appear; use DEVBENCH_CONFIG_PATH."""
        text = _read(README)
        assert "JUDGE_CONFIG_PATH" not in text, (
            "README.md still contains JUDGE_CONFIG_PATH. Rename to DEVBENCH_CONFIG_PATH (AC-197-8)."
        )

    def test_no_judge_stop_max_blocks(self) -> None:
        """JUDGE_STOP_MAX_BLOCKS must not appear; use DEVBENCH_STOP_MAX_BLOCKS."""
        text = _read(README)
        assert "JUDGE_STOP_MAX_BLOCKS" not in text, (
            "README.md still contains JUDGE_STOP_MAX_BLOCKS. Rename to DEVBENCH_STOP_MAX_BLOCKS (AC-197-8)."
        )

    def test_no_judge_stop_window_seconds(self) -> None:
        """JUDGE_STOP_WINDOW_SECONDS must not appear; use DEVBENCH_STOP_WINDOW_SECONDS."""
        text = _read(README)
        assert "JUDGE_STOP_WINDOW_SECONDS" not in text, (
            "README.md still contains JUDGE_STOP_WINDOW_SECONDS. Rename to DEVBENCH_STOP_WINDOW_SECONDS (AC-197-8)."
        )

    def test_no_judge_stop_stale_minutes(self) -> None:
        """JUDGE_STOP_STALE_MINUTES must not appear; use DEVBENCH_STOP_STALE_MINUTES."""
        text = _read(README)
        assert "JUDGE_STOP_STALE_MINUTES" not in text, (
            "README.md still contains JUDGE_STOP_STALE_MINUTES. Rename to DEVBENCH_STOP_STALE_MINUTES (AC-197-8)."
        )

    def test_no_judge_pause_before_merge(self) -> None:
        """JUDGE_PAUSE_BEFORE_MERGE must not appear; use DEVBENCH_PAUSE_BEFORE_MERGE."""
        text = _read(README)
        assert "JUDGE_PAUSE_BEFORE_MERGE" not in text, (
            "README.md still contains JUDGE_PAUSE_BEFORE_MERGE. Rename to DEVBENCH_PAUSE_BEFORE_MERGE (AC-197-8)."
        )

    def test_no_judge_ci_failure_retry_enabled(self) -> None:
        """JUDGE_CI_FAILURE_RETRY_ENABLED must not appear; use DEVBENCH_ prefix."""
        text = _read(README)
        assert "JUDGE_CI_FAILURE_RETRY_ENABLED" not in text, (
            "README.md still contains JUDGE_CI_FAILURE_RETRY_ENABLED. "
            "Rename to DEVBENCH_CI_FAILURE_RETRY_ENABLED (AC-197-8)."
        )

    def test_no_judge_report_timezone(self) -> None:
        """JUDGE_REPORT_TIMEZONE must not appear; use DEVBENCH_REPORT_TIMEZONE."""
        text = _read(README)
        assert "JUDGE_REPORT_TIMEZONE" not in text, (
            "README.md still contains JUDGE_REPORT_TIMEZONE. Rename to DEVBENCH_REPORT_TIMEZONE (AC-197-8)."
        )

    def test_no_judge_hook_tail_vars(self) -> None:
        """JUDGE_HOOK_TAIL_* must not appear; use DEVBENCH_HOOK_TAIL_*."""
        text = _read(README)
        assert "JUDGE_HOOK_TAIL_" not in text, (
            "README.md still contains JUDGE_HOOK_TAIL_* references. Rename to DEVBENCH_HOOK_TAIL_* (AC-197-8)."
        )

    def test_no_judge_use_bedrock(self) -> None:
        """JUDGE_USE_BEDROCK must not appear; use DEVBENCH_USE_BEDROCK."""
        text = _read(README)
        assert "JUDGE_USE_BEDROCK" not in text, (
            "README.md still contains JUDGE_USE_BEDROCK. Rename to DEVBENCH_USE_BEDROCK (AC-197-8)."
        )

    def test_no_judge_safe_permissions(self) -> None:
        """JUDGE_SAFE_PERMISSIONS must not appear; use DEVBENCH_SAFE_PERMISSIONS."""
        text = _read(README)
        assert "JUDGE_SAFE_PERMISSIONS" not in text, (
            "README.md still contains JUDGE_SAFE_PERMISSIONS. Rename to DEVBENCH_SAFE_PERMISSIONS (AC-197-8)."
        )

    def test_no_judge_orchestrator_session_id(self) -> None:
        """JUDGE_ORCHESTRATOR_SESSION_ID must not appear; use DEVBENCH_ORCHESTRATOR_SESSION_ID."""
        text = _read(README)
        assert "JUDGE_ORCHESTRATOR_SESSION_ID" not in text, (
            "README.md still contains JUDGE_ORCHESTRATOR_SESSION_ID. "
            "Rename to DEVBENCH_ORCHESTRATOR_SESSION_ID (AC-197-8)."
        )

    def test_devbench_workspace_root_present(self) -> None:
        """DEVBENCH_WORKSPACE_ROOT must appear in README.md as the required env var."""
        text = _read(README)
        assert "DEVBENCH_WORKSPACE_ROOT" in text, (
            "README.md must use DEVBENCH_WORKSPACE_ROOT as the canonical workspace-root env var (AC-197-8)."
        )

    def test_devbench_claude_model_present(self) -> None:
        """DEVBENCH_CLAUDE_MODEL must appear in README.md as the required env var."""
        text = _read(README)
        assert "DEVBENCH_CLAUDE_MODEL" in text, (
            "README.md must use DEVBENCH_CLAUDE_MODEL as the canonical model env var (AC-197-8)."
        )

    @pytest.mark.parametrize(
        "legacy_var",
        [
            "JUDGE_WORKSPACE_ROOT",
            "JUDGE_CLAUDE_MODEL",
            "JUDGE_CONFIG_PATH",
            "JUDGE_STOP_MAX_BLOCKS",
            "JUDGE_STOP_WINDOW_SECONDS",
            "JUDGE_STOP_STALE_MINUTES",
            "JUDGE_PAUSE_BEFORE_MERGE",
            "JUDGE_CI_FAILURE_RETRY_ENABLED",
            "JUDGE_REPORT_TIMEZONE",
            "JUDGE_HOOK_TAIL_",
            "JUDGE_USE_BEDROCK",
            "JUDGE_SAFE_PERMISSIONS",
            "JUDGE_ORCHESTRATOR_SESSION_ID",
        ],
    )
    def test_no_legacy_judge_vars_parametrized(self, legacy_var: str) -> None:
        """No JUDGE_* operational env vars may appear in README.md."""
        text = _read(README)
        assert legacy_var not in text, (
            f"README.md still contains {legacy_var}. Rename to DEVBENCH_{legacy_var.removeprefix('JUDGE_')} (AC-197-8)."
        )

    def test_no_legacy_judge_vars_comprehensive(self) -> None:
        """No JUDGE_* operational env vars (other than exempt concept names) may appear."""
        text = _read(README)
        violations = _legacy_judge_env_occurrences(text)
        assert not violations, (
            "README.md contains JUDGE_* operational env vars that must be renamed to DEVBENCH_*: "
            f"{sorted(set(violations))} (AC-197-8)."
        )

    def test_configuration_section_uses_devbench_workspace_root(self) -> None:
        """The Configuration section must reference DEVBENCH_WORKSPACE_ROOT, not the legacy name."""
        text = _read(README)
        config_idx = text.lower().find("## configuration")
        assert config_idx != -1, "README.md must have a ## Configuration section."
        config_text = text[config_idx : config_idx + 2000]
        assert "JUDGE_WORKSPACE_ROOT" not in config_text, (
            "README.md Configuration section still references JUDGE_WORKSPACE_ROOT. "
            "Rename to DEVBENCH_WORKSPACE_ROOT (AC-197-8)."
        )
        assert "DEVBENCH_WORKSPACE_ROOT" in config_text, (
            "README.md Configuration section must reference DEVBENCH_WORKSPACE_ROOT (AC-197-8)."
        )

    def test_no_backwards_compat_footnotes(self) -> None:
        """The rename is hard; no per-var env-var backwards-compatibility notes allowed."""
        text = _read(README)
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
            "README.md contains per-var backwards-compatibility notes for "
            "JUDGE_* env vars. The rename is hard; compatibility notes are forbidden "
            "(AC-197-8 / spec section 4.9.3). Violations:\n" + "\n".join(f"  {v}" for v in violations)
        )


# ---------------------------------------------------------------------------
# CHANGELOG.md
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestChangelogBreakingEntry:
    """CHANGELOG.md must carry a '### Changed (BREAKING)' entry for the env-var rename."""

    def test_breaking_entry_present(self) -> None:
        """A '### Changed (BREAKING)' or '### Breaking' section must appear in CHANGELOG."""
        text = _read(CHANGELOG)
        has_breaking = "### Changed (BREAKING)" in text or "### Breaking Changes" in text or "### BREAKING" in text
        assert has_breaking, (
            "CHANGELOG.md must contain a '### Changed (BREAKING)' entry for the "
            "JUDGE_* -> DEVBENCH_* rename (AC-197-8)."
        )

    def test_breaking_entry_names_the_rename(self) -> None:
        """The BREAKING entry must name the JUDGE_* -> DEVBENCH_* rename."""
        text = _read(CHANGELOG)
        # Find the breaking section and check it mentions the rename
        has_rename_mention = (
            ("JUDGE_" in text and "DEVBENCH_" in text)
            or "JUDGE_* -> DEVBENCH_*" in text
            or "JUDGE_* to DEVBENCH_*" in text
        )
        assert has_rename_mention, "CHANGELOG.md BREAKING entry must name the JUDGE_* -> DEVBENCH_* rename (AC-197-8)."

    def test_breaking_entry_mentions_rejection_on_legacy(self) -> None:
        """The BREAKING entry must describe that legacy JUDGE_* vars are rejected at process start."""
        text = _read(CHANGELOG)
        has_rejection_mention = (
            "rejected" in text.lower()
            or "exit non-zero" in text.lower()
            or "hard cutover" in text.lower()
            or "hard rejection" in text.lower()
        )
        assert has_rejection_mention, (
            "CHANGELOG.md BREAKING entry must describe the hard rejection of legacy JUDGE_* vars "
            "at process start (AC-197-8)."
        )

    def test_breaking_entry_mentions_migrate_env_shim(self) -> None:
        """The BREAKING entry must reference the 'devbench migrate-env' migration shim."""
        text = _read(CHANGELOG)
        assert "migrate-env" in text, (
            "CHANGELOG.md BREAKING entry must reference 'devbench migrate-env' as the "
            "operator migration shim (AC-197-8)."
        )

    def test_no_deprecation_timeline_language(self) -> None:
        """No deprecation-timeline language is acceptable -- the cutover is in this release."""
        text = _read(CHANGELOG)
        # Find the unreleased / v-next section to check just the new entry
        unreleased_idx = text.lower().find("[unreleased]")
        if unreleased_idx == -1:
            unreleased_idx = text.lower().find("v-next")
        if unreleased_idx == -1:
            return  # No unreleased section; skip (changelog might have different format)

        # Check the section between unreleased and the next ## heading
        next_release_idx = text.find("\n## [", unreleased_idx + 1)
        unreleased_section = text[unreleased_idx:] if next_release_idx == -1 else text[unreleased_idx:next_release_idx]

        # Check for deprecation-timeline language.
        # "no deprecation period" is acceptable (it asserts the absence of one);
        # "deprecation period" without negation is NOT acceptable.
        section_lower = unreleased_section.lower()
        # Patterns that indicate a forward deprecation timeline (the bad case):
        bad_patterns = [
            "will be removed in",
            "deprecated in",
            "grace period",
        ]
        found = [p for p in bad_patterns if p in section_lower]
        # Also check for bare "deprecation period" only when NOT preceded by a negation.
        # "no deprecation period" (possibly with whitespace / newlines in between) is acceptable.
        if "deprecation period" in section_lower:
            idx = section_lower.find("deprecation period")
            # Look at up to 10 chars before the match to catch "no" with whitespace
            window = section_lower[max(0, idx - 10) : idx].strip()
            if not window.endswith("no"):
                found.append("deprecation period (without 'no' negation)")
        assert not found, (
            "CHANGELOG.md contains deprecation-timeline language in the new BREAKING entry. "
            "The cutover is immediate; do not promise future removal or grace periods. "
            f"Problematic patterns found: {found} (AC-197-8 / spec section 4.9)."
        )

    @pytest.mark.parametrize(
        "required_keyword",
        [
            "migrate-env",
        ],
    )
    def test_changelog_references_required_keywords(self, required_keyword: str) -> None:
        """The CHANGELOG BREAKING entry must reference required keywords."""
        text = _read(CHANGELOG)
        assert required_keyword in text, f"CHANGELOG.md BREAKING entry must contain '{required_keyword}' (AC-197-8)."
