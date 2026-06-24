"""Structural pins for the ``devbench supervise`` documentation (Section 8, FR-31).

FR-31: the supervise feature is "done" only when ``docs/supervise.md``, ADR-31, and
the four edited docs (cli-reference, architecture, execution-modes,
devbench-yaml-reference) ship in the same change as the code, plus the
llm-authentication cross-reference.

These are REAL structural assertions (each fails if the documented surface is
missing): they pin that every doc exists, carries no em-dash, documents the six
verbs / the subscription-billing rationale / the no-API-key requirement / the
``supervise:`` config block, and that the new docs are reachable (no dangling
internal links into the supervise docs).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DOCS = REPO_ROOT / "docs"

SUPERVISE_DOC = DOCS / "supervise.md"
ADR_31 = DOCS / "adr" / "31-interactive-screen-supervisor.md"
CLI_REFERENCE = DOCS / "cli-reference.md"
ARCHITECTURE = DOCS / "architecture.md"
EXECUTION_MODES = DOCS / "execution-modes.md"
YAML_REFERENCE = DOCS / "devbench-yaml-reference.md"
LLM_AUTH = DOCS / "llm-authentication.md"
QUOTA_TODO = REPO_ROOT / "spec" / "devbench-supervise-screen-orchestrator" / "QUOTA-VERIFICATION-TODO.md"

SUPERVISE_VERBS: tuple[str, ...] = ("start", "stop", "restart", "status", "info", "attach")

_ALL_SUPERVISE_DOCS = (
    SUPERVISE_DOC,
    ADR_31,
    CLI_REFERENCE,
    ARCHITECTURE,
    EXECUTION_MODES,
    YAML_REFERENCE,
    LLM_AUTH,
)


@pytest.mark.unit
class TestSuperviseDocsExist:
    """Every doc Section 8 requires must exist and use ASCII -- (no em-dash)."""

    @pytest.mark.parametrize("doc", _ALL_SUPERVISE_DOCS, ids=lambda p: p.name)
    def test_doc_exists(self, doc: Path) -> None:
        assert doc.is_file(), f"{doc} must exist (Section 8 / FR-31)."

    @pytest.mark.parametrize("doc", (SUPERVISE_DOC, ADR_31), ids=lambda p: p.name)
    def test_new_doc_has_no_em_dash(self, doc: Path) -> None:
        assert "\u2014" not in doc.read_text(encoding="utf-8"), (
            f"{doc} must use -- (double hyphen), not the em-dash glyph (U+2014)."
        )


@pytest.mark.unit
class TestSuperviseGuide:
    """docs/supervise.md is the full operator guide (Section 8 row 1)."""

    def test_documents_all_six_verbs(self) -> None:
        text = SUPERVISE_DOC.read_text(encoding="utf-8")
        for verb in SUPERVISE_VERBS:
            assert f"supervise {verb}" in text, f"docs/supervise.md must document 'supervise {verb}'."

    def test_documents_subscription_billing_rationale(self) -> None:
        text = SUPERVISE_DOC.read_text(encoding="utf-8").lower()
        assert "subscription" in text and "5-hour" in text, (
            "docs/supervise.md must explain the subscription (5-hour window) billing rationale."
        )

    def test_documents_no_api_key_requirement(self) -> None:
        text = SUPERVISE_DOC.read_text(encoding="utf-8")
        assert "ANTHROPIC_API_KEY" in text, (
            "docs/supervise.md must state the no-ANTHROPIC_API_KEY preflight requirement (FR-21)."
        )

    def test_documents_preflight_and_quota_and_attach(self) -> None:
        text = SUPERVISE_DOC.read_text(encoding="utf-8").lower()
        assert "screen" in text, "must document the screen preflight requirement (FR-23)."
        assert "quota" in text, "must document quota-wait behaviour."
        assert "read-only" in text, "must document read-only safe-attach (FR-26)."

    def test_documents_version_fragility_troubleshooting(self) -> None:
        text = SUPERVISE_DOC.read_text(encoding="utf-8").lower()
        assert "troubleshoot" in text, "docs/supervise.md must have a troubleshooting section."
        assert "version" in text, "must note the CLI prompt-detection version-fragility."


@pytest.mark.unit
class TestAdr31:
    """docs/adr/31-interactive-screen-supervisor.md records the design decision."""

    def test_records_interactive_over_sdk_and_not_print(self) -> None:
        text = ADR_31.read_text(encoding="utf-8")
        lower = text.lower()
        assert "subscription" in lower, "ADR-31 must record the subscription-billing rationale."
        assert "sdk" in lower, "ADR-31 must contrast the interactive path with the SDK/API path."
        assert "--print" in text or "-p" in text, "ADR-31 must record WHY -p/--print is excluded."

    def test_records_version_fragility_and_log_tail_mitigation(self) -> None:
        lower = ADR_31.read_text(encoding="utf-8").lower()
        assert "pexpect" in lower and "screen" in lower, "ADR-31 must name the screen+pexpect mechanism."
        assert "log" in lower and "tail" in lower, "ADR-31 must record the hybrid log-tail mitigation."

    def test_has_standard_adr_headings(self) -> None:
        lower = ADR_31.read_text(encoding="utf-8").lower()
        assert "**status:**" in lower, "ADR-31 must carry a bold Status metadata line."
        assert "## context" in lower, "ADR-31 must have a Context heading."
        assert "## decision" in lower, "ADR-31 must have a Decision heading."
        assert "## consequences" in lower, "ADR-31 must have a Consequences heading."


@pytest.mark.unit
class TestEditedDocsCoverSupervise:
    """The four edited docs each add the supervise surface (Section 8 rows 2-5)."""

    def test_cli_reference_documents_supervise_verbs(self) -> None:
        text = CLI_REFERENCE.read_text(encoding="utf-8")
        assert "supervise" in text, "docs/cli-reference.md must add the supervise verbs."
        for verb in SUPERVISE_VERBS:
            assert f"supervise {verb}" in text, f"docs/cli-reference.md must document 'supervise {verb}'."

    def test_architecture_documents_interactive_screen_path(self) -> None:
        lower = ARCHITECTURE.read_text(encoding="utf-8").lower()
        assert "supervise" in lower, "docs/architecture.md must add the interactive-screen launch path."
        assert "subscription" in lower, "docs/architecture.md must document the billing rationale."

    def test_execution_modes_adds_supervised_interactive_mode(self) -> None:
        lower = EXECUTION_MODES.read_text(encoding="utf-8").lower()
        assert "supervise" in lower, "docs/execution-modes.md must add the supervised interactive mode."
        assert "subscription" in lower, "docs/execution-modes.md must name the subscription-billed channel."

    def test_yaml_reference_documents_supervise_block(self) -> None:
        text = YAML_REFERENCE.read_text(encoding="utf-8")
        assert "`supervise:`" in text or "## `supervise" in text, (
            "docs/devbench-yaml-reference.md must document the supervise: config block."
        )
        for field in ("screen_name_prefix", "detection_patterns", "injectable_commands"):
            assert field in text, f"docs/devbench-yaml-reference.md must document supervise.{field}."

    def test_llm_authentication_cross_references_supervise(self) -> None:
        text = LLM_AUTH.read_text(encoding="utf-8")
        assert "supervise" in text.lower(), (
            "docs/llm-authentication.md must cross-reference the supervise subscription-billed channel."
        )
        assert "ANTHROPIC_API_KEY" in text, (
            "docs/llm-authentication.md must restate the no-API-key requirement for the supervise path."
        )


@pytest.mark.unit
class TestQuotaVerificationTodoStatus:
    """The QUOTA-VERIFICATION-TODO records the Phase-6 DI outcomes (DI-5 still pending)."""

    def test_todo_exists_and_no_em_dash(self) -> None:
        assert QUOTA_TODO.is_file(), "the QUOTA-VERIFICATION-TODO.md companion file must exist."
        assert "\u2014" not in QUOTA_TODO.read_text(encoding="utf-8"), (
            "QUOTA-VERIFICATION-TODO.md must use -- (double hyphen), not the em-dash glyph (U+2014)."
        )

    def test_records_phase6_di_outcomes(self) -> None:
        text = QUOTA_TODO.read_text(encoding="utf-8")
        assert "Phase 6 status" in text, (
            "QUOTA-VERIFICATION-TODO.md must record the Phase-6 discovery-item outcomes for operator review."
        )
        for di in ("DI-1", "DI-3", "DI-4", "DI-5"):
            assert di in text, f"the Phase-6 status section must account for {di}."

    def test_di5_is_recorded_as_still_pending_a_real_quota_event(self) -> None:
        text = QUOTA_TODO.read_text(encoding="utf-8").lower()
        assert "di-5" in text and "pending" in text, (
            "QUOTA-VERIFICATION-TODO.md must record DI-5 as still PENDING a real quota event."
        )
        assert "ac-29" in text, "the status section must cite AC-29 as the deferred AC tracking DI-5."


@pytest.mark.unit
class TestSuperviseDocLinkIntegrity:
    """Every relative link inside docs/supervise.md resolves to an existing file."""

    def test_all_relative_links_resolve(self) -> None:
        import re

        text = SUPERVISE_DOC.read_text(encoding="utf-8")
        raw_links = re.findall(r"\[(?:[^\]]*)\]\(([^)]+)\)", text)
        dangling: list[str] = []
        for raw in raw_links:
            stripped = raw.strip()
            if stripped.startswith(("#", "http://", "https://", "mailto:")):
                continue
            path_part = stripped.split("#")[0]
            if not path_part:
                continue
            if not (DOCS / path_part).resolve().exists():
                dangling.append(path_part)
        assert not dangling, f"docs/supervise.md has dangling relative links: {dangling}"
