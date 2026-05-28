"""Structural pins for README.md draft-status mention and docs/devbench-yaml-reference.md.

Verifies that:
- README.md feature list mentions the ``draft`` WorkUnitStatus (AC-189-8 docs leg).
- ``docs/devbench-yaml-reference.md`` exists and documents the
  ``backlog.default_status_for_new_work_units`` config field (AC-189-8).

Spec source: spec/devbench-self-improve.md section 4.1.5.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
README = REPO_ROOT / "README.md"
YAML_REF = REPO_ROOT / "docs" / "devbench-yaml-reference.md"


@pytest.mark.unit
class TestReadmeDraftStatusMention:
    """README.md must mention the draft WorkUnitStatus in the feature overview."""

    def _text(self) -> str:
        return README.read_text(encoding="utf-8")

    def test_readme_exists(self) -> None:
        assert README.is_file(), "README.md must exist at the repo root."

    def test_readme_mentions_draft_status(self) -> None:
        """README must mention 'draft' status as a WorkUnitStatus value."""
        text = self._text()
        assert "draft" in text.lower(), (
            "README.md must mention the 'draft' WorkUnitStatus. Spec 4.1 adds draft as a pre-in-queue gate (AC-189-8)."
        )

    def test_readme_mentions_draft_in_feature_context(self) -> None:
        """README feature overview must describe 'draft' in the context of work unit lifecycle."""
        text = self._text()
        lower = text.lower()
        # draft must appear alongside lifecycle-related terms
        has_lifecycle_context = "draft" in lower and (
            "in-queue" in lower or "lifecycle" in lower or "status" in lower or "promote" in lower
        )
        assert has_lifecycle_context, (
            "README.md must mention 'draft' in the context of the work unit lifecycle "
            "(alongside 'in-queue', 'lifecycle', 'status', or 'promote'). "
            "Spec 4.1 defines draft as the pre-in-queue gate (AC-189-8)."
        )

    def test_readme_draft_section_describes_promote_or_default_status(self) -> None:
        """README must describe either the promote command or the default-status config near the draft mention."""
        text = self._text()
        lower = text.lower()
        has_promote_or_config = "promote" in lower or "default_status_for_new_work_units" in lower
        assert has_promote_or_config, (
            "README.md must mention either the 'promote' subcommand or "
            "'default_status_for_new_work_units' config near the draft status description. "
            "Spec 4.1.4 and 4.1.5 are the operator-facing surfaces for draft (AC-189-8)."
        )


@pytest.mark.unit
class TestYamlReferenceDocExists:
    """docs/devbench-yaml-reference.md must exist and document backlog.default_status_for_new_work_units."""

    def _text(self) -> str:
        return YAML_REF.read_text(encoding="utf-8")

    def test_yaml_reference_doc_exists(self) -> None:
        assert YAML_REF.is_file(), (
            "docs/devbench-yaml-reference.md must exist. "
            "Spec 4.1.5 requires this as the canonical reference for devbench.yaml config fields."
        )

    def test_yaml_reference_documents_backlog_section(self) -> None:
        """The doc must document the 'backlog:' YAML section."""
        text = self._text()
        assert "backlog:" in text or "backlog" in text, (
            "docs/devbench-yaml-reference.md must document the 'backlog:' YAML section "
            "(spec 4.1.5 -- backlog lifecycle settings)."
        )

    def test_yaml_reference_documents_default_status_field(self) -> None:
        """The doc must document the 'default_status_for_new_work_units' field."""
        text = self._text()
        assert "default_status_for_new_work_units" in text, (
            "docs/devbench-yaml-reference.md must document the "
            "'backlog.default_status_for_new_work_units' config field (AC-189-8)."
        )

    def test_yaml_reference_names_valid_values_draft_and_in_queue(self) -> None:
        """The doc must name 'draft' and 'in-queue' as the two accepted values."""
        text = self._text()
        assert "draft" in text, (
            "docs/devbench-yaml-reference.md must list 'draft' as an accepted value "
            "for backlog.default_status_for_new_work_units (AC-189-8)."
        )
        assert "in-queue" in text, (
            "docs/devbench-yaml-reference.md must list 'in-queue' as an accepted value "
            "for backlog.default_status_for_new_work_units (AC-189-9 -- default preserves legacy behaviour)."
        )

    def test_yaml_reference_documents_default_value(self) -> None:
        """The doc must state the default value ('in-queue') for backward compatibility."""
        text = self._text()
        lower = text.lower()
        has_default_mention = "default" in lower and "in-queue" in lower
        assert has_default_mention, (
            "docs/devbench-yaml-reference.md must state that the default value for "
            "'backlog.default_status_for_new_work_units' is 'in-queue' (AC-189-9)."
        )

    def test_yaml_reference_mentions_config_error_on_invalid_value(self) -> None:
        """The doc must warn that invalid values raise a ConfigError at load time."""
        text = self._text()
        lower = text.lower()
        has_error_warning = (
            "error" in lower or "invalid" in lower or "rejected" in lower or "configerror" in lower.replace(" ", "")
        )
        assert has_error_warning, (
            "docs/devbench-yaml-reference.md must warn that invalid values for "
            "'backlog.default_status_for_new_work_units' are rejected at load time "
            "(spec 4.1.5 -- 'Validation: only draft or in-queue accepted; other values raise a clear ConfigError')."
        )

    def test_yaml_reference_has_yaml_code_example(self) -> None:
        """The doc must include a YAML code block example."""
        text = self._text()
        assert "```yaml" in text or "```" in text, (
            "docs/devbench-yaml-reference.md must contain at least one YAML code block "
            "showing how to set 'backlog.default_status_for_new_work_units'."
        )

    def test_yaml_reference_documents_backwards_compat(self) -> None:
        """The doc must state that existing workspaces without the setting are unaffected."""
        text = self._text()
        lower = text.lower()
        has_compat = "backwards" in lower or "backward" in lower or "existing" in lower or "optional" in lower
        assert has_compat, (
            "docs/devbench-yaml-reference.md must state that the backlog section is optional "
            "and existing workspaces are unaffected (AC-189-9)."
        )

    def test_yaml_reference_links_to_config_loader_or_sample_config(self) -> None:
        """The doc must cross-reference config_loader.py docstring or sample-config.yaml."""
        text = self._text()
        has_cross_ref = (
            "config_loader" in text
            or "sample-config.yaml" in text
            or "sample-config" in text
            or "config-schema" in text
        )
        assert has_cross_ref, (
            "docs/devbench-yaml-reference.md must cross-reference 'config_loader.py' or "
            "'sample-config.yaml' as the source of truth for the full YAML schema."
        )
