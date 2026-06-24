"""Phase 6: pin the DEFERRED supervise acceptance criteria (AC-23..29, AC-34).

The supervise spec (Section 10.1) splits its 34 ACs into an executable set
(AC-1..AC-22, AC-30..AC-33), proven by named ``pytest`` commands in CI, and a
DEFERRED set (AC-23..AC-29, AC-34) that genuinely cannot run in the
orchestrator/CI environment because each requires a real subscription login, a
live ``claude``, a real ``screen``, or a real 5-hour-window quota event.

Phase 6 is "done" only when the deferred set is FIRST-CLASS and traceable: this
module pins, against the spec text, that each deferred AC is explicitly marked
``DEFERRED`` with a ``verify: deferred`` directive and a stated live-only reason
(so an operator reviewing the feature can see exactly which ACs await a live run
and why). This is a real assertion -- it fails if a deferred AC is silently
dropped, or if a deferred AC is reclassified as executable without a CI command.

It also pins that the in-CI integration harness (the dummy-backlog clean run +
the subscription-billing env hygiene check) exists, so the deferred live ACs
validate ONLY the genuinely-live surface (Section 10.1 DoD<->AC agreement).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SPEC = REPO_ROOT / "spec" / "devbench-supervise-screen-orchestrator" / "devbench-supervise-screen-orchestrator.md"

DEFERRED_ACS: tuple[str, ...] = ("AC-23", "AC-24", "AC-25", "AC-26", "AC-27", "AC-28", "AC-29", "AC-34")


def _spec_text() -> str:
    return SPEC.read_text(encoding="utf-8")


@pytest.mark.integration
class TestDeferredAcsArePinned:
    """Every live-only AC is explicitly DEFERRED with a verify: deferred directive."""

    @pytest.mark.parametrize("ac", DEFERRED_ACS)
    def test_ac_is_marked_deferred(self, ac: str) -> None:
        text = _spec_text()
        line = _ac_line(text, ac)
        assert line is not None, f"{ac} must appear in the spec Section 10.1 AC list"
        assert "DEFERRED" in line, f"{ac} must be marked DEFERRED (it requires a live run)"
        assert "verify: deferred" in line, (
            f"{ac} must carry a 'verify: deferred' directive so it is not mistaken for an executable AC"
        )

    @pytest.mark.parametrize("ac", DEFERRED_ACS)
    def test_ac_states_a_live_only_reason(self, ac: str) -> None:
        text = _spec_text()
        line = _ac_line(text, ac)
        assert line is not None
        reasons = ("subscription", "live", "screen", "quota", "concurrent", "human", "session")
        assert any(token in line.lower() for token in reasons), (
            f"{ac} must state a live-only reason (subscription / live claude / screen / quota / ...)"
        )


@pytest.mark.integration
class TestExecutableAcsCoverNonLiveSurface:
    """The in-CI integration harness covers the non-live surface (DoD<->AC)."""

    def test_dummy_backlog_integration_module_exists(self) -> None:
        module = REPO_ROOT / "tests" / "test_integration" / "test_supervise_dummy_backlog_integration.py"
        assert module.is_file(), (
            "the dummy-backlog integration harness must exist so the deferred live "
            "ACs validate only the genuinely-live surface (Section 10.1)."
        )

    def test_dummy_backlog_fixture_exists(self) -> None:
        fixture = REPO_ROOT / "tests" / "fixtures" / "supervise" / "dummy-backlog"
        assert (fixture / "BACKLOG.md").is_file(), (
            "the dummy backlog fixture (Section 10.0) must exist for the integration layer."
        )


def _ac_line(text: str, ac: str) -> str | None:
    """Return the single Section 10.1 checklist line for *ac* (``- [ ] AC-N: ...``)."""
    marker = f"- [ ] {ac}:"
    idx = text.find(marker)
    if idx == -1:
        return None
    end = text.find("\n", idx)
    return text[idx:end] if end != -1 else text[idx:]
