"""Structural pins for docs/adr/23-named-sessions.md (E4-F7-S1-T2, AC-STRUCT-1).

Verifies that the ADR exists and contains the required structural elements:
- Status and Date headers
- Context section explaining the motivation
- Decision section covering flock, per-session state, and atomic claim
- Alternatives considered section
- Consequences section
- No em-dash characters (U+2014) -- prohibited by devbench coding standards

Spec source: spec/devbench-self-improve.md section 5.2.
Issue: #192.
Companion: tests/test_docs/test_adr_24_quota_wait.py (ADR-24 structural pins).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
ADR_23 = REPO_ROOT / "docs" / "adr" / "23-named-sessions.md"


@pytest.mark.unit
class TestAdr23NamedSessions:
    """Structural pins for ADR-23 (named sessions)."""

    def test_adr_23_file_exists(self) -> None:
        """The ADR file must exist at the canonical path."""
        assert ADR_23.is_file(), (
            "docs/adr/23-named-sessions.md must exist -- spec section 5.2 and E4-F7-S1-T2 mandate this ADR."
        )

    def test_adr_23_has_status_header(self) -> None:
        """ADR-23 must carry an explicit Status line."""
        text = ADR_23.read_text(encoding="utf-8")
        assert "**Status:**" in text, "ADR-23 must have a '**Status:**' header line matching the ADR template."

    def test_adr_23_has_date_header(self) -> None:
        """ADR-23 must carry an explicit Date line."""
        text = ADR_23.read_text(encoding="utf-8")
        assert "**Date:**" in text, "ADR-23 must have a '**Date:**' header line matching the ADR template."

    def test_adr_23_has_context_section(self) -> None:
        """ADR-23 must include a Context section covering the motivation."""
        text = ADR_23.read_text(encoding="utf-8")
        assert "## Context" in text, "ADR-23 must have a '## Context' section explaining why named sessions are needed."

    def test_adr_23_has_decision_section(self) -> None:
        """ADR-23 must include a Decision section."""
        text = ADR_23.read_text(encoding="utf-8")
        assert "## Decision" in text, "ADR-23 must have a '## Decision' section documenting the chosen design."

    def test_adr_23_decision_covers_flock(self) -> None:
        """The Decision section must describe the flock-based claim serialisation."""
        text = ADR_23.read_text(encoding="utf-8")
        assert "flock" in text.lower(), (
            "ADR-23 must mention 'flock' (the BACKLOG.lock serialisation mechanism) "
            "as specified in spec section 4.4.1 and 4.4.2."
        )

    def test_adr_23_decision_covers_per_session_state(self) -> None:
        """The Decision section must describe per-session state directory layout."""
        text = ADR_23.read_text(encoding="utf-8")
        assert "session" in text.lower(), "ADR-23 must discuss the per-session state directory structure."
        # Per spec 4.4.4 the per-session dir holds pid, scope.json, drain.signal, etc.
        assert "pid" in text.lower(), (
            "ADR-23 must reference the PID file used for liveness checks and SIGTERM delivery."
        )

    def test_adr_23_decision_covers_atomic_claim(self) -> None:
        """The Decision section must describe atomic claim arbitration."""
        text = ADR_23.read_text(encoding="utf-8")
        assert "atomic" in text.lower() or "ClaimRaceError" in text, (
            "ADR-23 must cover atomic claim arbitration (ClaimRaceError / BACKLOG.lock)."
        )

    def test_adr_23_has_alternatives_considered_section(self) -> None:
        """ADR-23 must include an Alternatives considered section."""
        text = ADR_23.read_text(encoding="utf-8")
        assert "## Alternatives" in text, "ADR-23 must have an '## Alternatives' section documenting rejected designs."

    def test_adr_23_has_consequences_section(self) -> None:
        """ADR-23 must include a Consequences section."""
        text = ADR_23.read_text(encoding="utf-8")
        assert "## Consequences" in text, "ADR-23 must have a '## Consequences' section covering operator impact."

    def test_adr_23_has_references_section(self) -> None:
        """ADR-23 must include a References section linking to implementation code."""
        text = ADR_23.read_text(encoding="utf-8")
        assert "## References" in text, "ADR-23 must have a '## References' section linking to the implementation."

    def test_adr_23_references_session_py(self) -> None:
        """ADR-23 must reference the session.py implementation module."""
        text = ADR_23.read_text(encoding="utf-8")
        assert "session.py" in text, "ADR-23 must cross-reference src/devbench/session.py as the implementation."

    def test_adr_23_references_issue_192(self) -> None:
        """ADR-23 must reference GitHub issue #192."""
        text = ADR_23.read_text(encoding="utf-8")
        assert "#192" in text, "ADR-23 must reference issue #192 (named sessions) for traceability."

    def test_adr_23_no_em_dash(self) -> None:
        """ADR-23 must not contain the em-dash character (U+2014).

        Per devbench coding standards: use '--' (double hyphen) in docs.
        """
        text = ADR_23.read_text(encoding="utf-8")
        em_dash = "\u2014"
        assert em_dash not in text, (
            "ADR-23 must not contain the em-dash character (U+2014). Use '--' (double hyphen) instead."
        )

    def test_adr_23_mentions_devbench_session_name_env_var(self) -> None:
        """ADR-23 must mention the DEVBENCH_SESSION_NAME environment variable."""
        text = ADR_23.read_text(encoding="utf-8")
        assert "DEVBENCH_SESSION_NAME" in text, (
            "ADR-23 must document the DEVBENCH_SESSION_NAME env var that activates session mode."
        )

    def test_adr_23_mentions_scope_overlap_detection(self) -> None:
        """ADR-23 must mention scope overlap detection (spec section 4.4.3)."""
        text = ADR_23.read_text(encoding="utf-8")
        assert "overlap" in text.lower(), "ADR-23 must document scope overlap detection as a safety property."

    def test_adr_23_mentions_safety_guarantees(self) -> None:
        """The Consequences section must call out safety guarantees."""
        text = ADR_23.read_text(encoding="utf-8")
        # The ADR should mention what's protected: no two sessions can corrupt backlog
        assert "safe" in text.lower() or "corrupt" in text.lower() or "race" in text.lower(), (
            "ADR-23 must discuss the safety guarantees provided by flock serialisation."
        )

    def test_adr_23_minimum_length(self) -> None:
        """ADR-23 must be substantive -- at least 1500 characters."""
        text = ADR_23.read_text(encoding="utf-8")
        assert len(text) >= 1500, (
            f"ADR-23 is too short ({len(text)} chars). "
            "An ADR must provide enough context to be useful to future readers."
        )
