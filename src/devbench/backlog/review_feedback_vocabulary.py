"""Per-judge controlled vocabulary for review-judge rejection feedback.

Issue #156: every review-judge rejection JSON written via
``cmd_log_rejection_feedback`` must use category codes from the judge's
fixed vocabulary. Layer 1 schema validation rejects any code not in the
corresponding judge's set; the executor / done-gate / status panel /
report all key off these tokens.

The literal strings are part of the public contract -- agents emit them,
the executor resolves them, and the report aggregates them. Adding a new
code is a backlog change; renaming or removing one is breaking.

The ``manifest_amender`` set mirrors ``AMENDER_REJECTION_CATEGORIES``
from ``devbench.backlog.amendment`` so the legacy amender path and the
new shared review-failures path share a single taxonomy.
"""

from __future__ import annotations

from typing import Final

JUDGE_CATEGORIES: Final[dict[str, frozenset[str]]] = {
    "code_review": frozenset(
        {
            "MAKE_VALIDATE_FAILURE",
            "HARDCODED_URL",
            "MISSING_AC_EVIDENCE",
            "SOLID_VIOLATION",
            "SECURITY_BYPASS_ANNOTATION",
            "SCOPE_VIOLATION",
            "MANIFEST_TODO_UNFILLED",
            "AGENT_LOG_CONTRADICTS_DIFF",
            "NEWLY_REACHABLE_PATH_UNVERIFIED",
            "UNREACHABLE_ARTIFACT",
        }
    ),
    "test_review": frozenset(
        {
            "GIT_COMPLETENESS",
            "STUB_TEST",
            "COVERAGE_REGRESSION",
            "TDD_CYCLE_MISSING",
            "DRY_VIOLATION",
            "FIXTURE_CATALOG_MISMATCH",
        }
    ),
    "doc_review": frozenset(
        {
            "README_SYNC",
            "CHANGELOG_SYNC",
            "API_DOCS_STALE",
            "EVIDENCE_BASED_CLAIM",
            "CONFIG_DOCS",
        }
    ),
    "changes_manifest": frozenset(
        {
            "SCOPE_GAP",
            "MANIFEST_MISMATCH",
            "STAGING_GAP",
            "OUT_OF_SCOPE_FILES",
        }
    ),
    "security_review": frozenset(
        {
            "SECRET_LEAK",
            "UNAUTHORIZED_DEP",
            "SCOPE_VIOLATION",
        }
    ),
    "manifest_amender": frozenset(
        {
            "SCOPE",
            "APPROACH_AUTH",
            "JUSTIFICATION_COHERENCE",
            "PRE_FILTER",
            "OTHER",
        }
    ),
}


# Severity ordering used by the executor-feedback collector to sort
# rejection payloads when injecting them into the next executor turn.
# Higher ordinal = higher priority. Same order as documented in
# ``plugin/devbench-orchestrate/agents/executor.md`` rejection-feedback resolution
# protocol.
JUDGE_SEVERITY_ORDER: Final[dict[str, int]] = {
    "security_review": 60,
    "code_review": 50,
    "test_review": 40,
    "changes_manifest": 30,
    "doc_review": 20,
    "manifest_amender": 10,
}


def is_known_judge(judge: str) -> bool:
    """Return True iff *judge* is one of the names with a controlled vocabulary."""
    return judge in JUDGE_CATEGORIES


def is_valid_code(judge: str, code: str) -> bool:
    """Return True iff *code* is in *judge*'s controlled vocabulary."""
    return code in JUDGE_CATEGORIES.get(judge, frozenset())
