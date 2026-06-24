"""Structural pins for claude-opus-4-8 content in docs/model-pricing.md (issue #254b)."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
PRICING_DOC = REPO_ROOT / "docs" / "model-pricing.md"


@pytest.mark.unit
class TestOpus48PricingDocBlock:
    """AC-254-3 and AC-254b-1: the pricing doc contains the 4.8 block, table row,
    and worked examples using the 4.8 model id with unchanged dollar figures."""

    def test_pricing_doc_exists(self) -> None:
        assert PRICING_DOC.is_file(), "docs/model-pricing.md must exist -- it is the canonical pricing reference."

    def test_opus_4_8_table_row_present(self) -> None:
        """AC-254-3: the standard pricing table must have a claude-opus-4-8 / Opus 4.8 row."""
        text = PRICING_DOC.read_text(encoding="utf-8")
        assert "Claude Opus 4.8" in text, (
            "docs/model-pricing.md must contain a 'Claude Opus 4.8' row in the standard pricing table (AC-254-3)."
        )

    def test_opus_4_8_table_row_appears_before_4_7(self) -> None:
        """AC-254-3: the 4.8 row must be listed first (above 4.7) in the table."""
        text = PRICING_DOC.read_text(encoding="utf-8")
        pos_4_8 = text.find("Claude Opus 4.8")
        pos_4_7 = text.find("Claude Opus 4.7")
        assert pos_4_8 != -1, "Claude Opus 4.8 row not found in pricing table."
        assert pos_4_7 != -1, "Claude Opus 4.7 row not found in pricing table."
        assert pos_4_8 < pos_4_7, "The Claude Opus 4.8 row must appear before the 4.7 row (first in the table)."

    def test_opus_4_8_input_rate_is_five_dollars(self) -> None:
        """AC-254-3 + D-254-1: input rate for Opus 4.8 must be $5 (same as 4.7)."""
        text = PRICING_DOC.read_text(encoding="utf-8")
        lines = text.splitlines()
        opus_4_8_rows = [line for line in lines if "Claude Opus 4.8" in line]
        assert opus_4_8_rows, "No table row containing 'Claude Opus 4.8' found."
        assert "$5" in opus_4_8_rows[0], (
            "The Claude Opus 4.8 table row must show $5 for input (D-254-1 pricing decision)."
        )

    def test_opus_4_8_output_rate_is_twenty_five_dollars(self) -> None:
        """AC-254-3 + D-254-1: output rate for Opus 4.8 must be $25 (same as 4.7)."""
        text = PRICING_DOC.read_text(encoding="utf-8")
        lines = text.splitlines()
        opus_4_8_rows = [line for line in lines if "Claude Opus 4.8" in line]
        assert opus_4_8_rows, "No table row containing 'Claude Opus 4.8' found."
        assert "$25" in opus_4_8_rows[0], (
            "The Claude Opus 4.8 table row must show $25 for output (D-254-1 pricing decision)."
        )

    def test_opus_4_7_row_retained(self) -> None:
        """AC-254-3: adding 4.8 must not remove the 4.7 row."""
        text = PRICING_DOC.read_text(encoding="utf-8")
        assert "Claude Opus 4.7" in text, "The Claude Opus 4.7 row must be retained after adding 4.8."

    def test_opus_4_6_row_retained(self) -> None:
        """AC-254-3: adding 4.8 must not remove the 4.6 row."""
        text = PRICING_DOC.read_text(encoding="utf-8")
        assert "Claude Opus 4.6" in text, "The Claude Opus 4.6 row must be retained after adding 4.8."

    def test_opus_4_5_row_retained(self) -> None:
        """AC-254-3: adding 4.8 must not remove the 4.5 row."""
        text = PRICING_DOC.read_text(encoding="utf-8")
        assert "Claude Opus 4.5" in text, "The Claude Opus 4.5 row must be retained after adding 4.8."

    def test_one_million_context_note_includes_4_8(self) -> None:
        """AC-254-3: the 1M-context note must mention Opus 4.8 alongside 4.7 and 4.6."""
        text = PRICING_DOC.read_text(encoding="utf-8")
        assert "1M-token context" in text or "1M context" in text or "1M-context" in text, (
            "A 1M-context note was not found in docs/model-pricing.md."
        )
        lines = text.splitlines()
        one_m_lines = [line for line in lines if "1M" in line and "context" in line]
        assert any("4.8" in line for line in one_m_lines), "The 1M-context note must mention Opus 4.8 (AC-254-3)."

    def test_picking_defaults_block_includes_4_8(self) -> None:
        """AC-254b-1: the 'Picking your defaults' section must have a 4.8 YAML snippet."""
        text = PRICING_DOC.read_text(encoding="utf-8")
        assert "claude-opus-4-8" in text, (
            "docs/model-pricing.md must contain a 'claude-opus-4-8' model id in the "
            "Picking your defaults section (AC-254b-1)."
        )

    def test_worked_example_uses_4_8_model_id(self) -> None:
        """AC-254b-1: the worked example must cite the 4.8 model id."""
        text = PRICING_DOC.read_text(encoding="utf-8")
        assert "claude-opus-4-8" in text, "The worked example must use 'claude-opus-4-8' model id (AC-254b-1)."
        assert "83.66" in text, "The worked example dollar figures must be unchanged (83.66 actual spend)."
        assert "39.57" in text, "The worked example dollar figures must be unchanged (39.57 reported cost)."

    def test_no_em_dash_in_doc(self) -> None:
        """Code standard: no em-dash character (U+2014) in any file."""
        text = PRICING_DOC.read_text(encoding="utf-8")
        assert "\u2014" not in text, (
            "docs/model-pricing.md must not contain em-dash characters (U+2014). Use '--' (double hyphen) instead."
        )
