"""Tests for ``devbench.plugin_helpers.create_spec_answers``.

Issue #256: headless create-spec answers-file parser.

The parser loads a YAML file keyed by Block letter A-G and validates that
all required blocks are present. A missing required block raises
``MissingBlockError`` with the verbatim ``[BLOCKED]`` message that the
headless mode emits before exiting non-zero.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml

from devbench.plugin_helpers.create_spec_answers import (
    BLOCKED_MESSAGE_TEMPLATE,
    REQUIRED_BLOCKS,
    MalformedAnswersError,
    MissingBlockError,
    load_answers_file,
    validate_answers,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_answers(path: Path, data: Any) -> Path:
    """Write a YAML answers file to *path* and return it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data), encoding="utf-8")
    return path


def _minimal_answers() -> dict[str, Any]:
    """Return a minimal valid answers dict (all blocks A-G present)."""
    return {
        "A": "Block A answers",
        "B": "Block B answers",
        "C": "Block C answers",
        "D": "Block D answers",
        "E": "Block E answers",
        "F": "Block F answers",
        "G": "Block G answers",
    }


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConstants:
    """REQUIRED_BLOCKS and BLOCKED_MESSAGE_TEMPLATE are well-formed."""

    def test_required_blocks_is_a_to_g(self) -> None:
        assert REQUIRED_BLOCKS == ["A", "B", "C", "D", "E", "F", "G"]

    def test_blocked_message_template_contains_placeholder(self) -> None:
        # The template must produce the verbatim spec message when formatted.
        msg = BLOCKED_MESSAGE_TEMPLATE.format(block="A")
        assert msg == "[BLOCKED] create-spec headless: missing answer for Block A"


# ---------------------------------------------------------------------------
# load_answers_file
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadAnswersFile:
    """load_answers_file reads a valid YAML answers file."""

    def test_loads_all_blocks(self, tmp_path: Path) -> None:
        data = _minimal_answers()
        answers_path = _write_answers(tmp_path / "answers.yaml", data)
        result = load_answers_file(answers_path)
        assert result == data

    def test_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.yaml"
        with pytest.raises(FileNotFoundError, match=re.escape("does_not_exist.yaml")):
            load_answers_file(missing)

    def test_malformed_yaml_raises_malformed_answers_error(self, tmp_path: Path) -> None:
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("key: [unclosed bracket\n", encoding="utf-8")
        with pytest.raises(MalformedAnswersError, match=re.escape("bad.yaml")):
            load_answers_file(bad_yaml)

    def test_non_dict_yaml_raises_malformed_answers_error(self, tmp_path: Path) -> None:
        list_yaml = tmp_path / "list.yaml"
        list_yaml.write_text("- item1\n- item2\n", encoding="utf-8")
        with pytest.raises(MalformedAnswersError, match="must be a YAML mapping"):
            load_answers_file(list_yaml)


# ---------------------------------------------------------------------------
# validate_answers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateAnswers:
    """validate_answers checks that all required blocks are present."""

    def test_valid_answers_returns_none(self) -> None:
        data = _minimal_answers()
        # No exception -- returns None
        result = validate_answers(data)
        assert result is None

    @pytest.mark.parametrize("missing_block", ["A", "B", "C", "D", "E", "F", "G"])
    def test_missing_block_raises_missing_block_error(self, missing_block: str) -> None:
        data = _minimal_answers()
        del data[missing_block]
        with pytest.raises(MissingBlockError) as exc_info:
            validate_answers(data)
        expected_msg = BLOCKED_MESSAGE_TEMPLATE.format(block=missing_block)
        assert str(exc_info.value) == expected_msg

    def test_missing_block_error_message_verbatim(self) -> None:
        """The exact verbatim message required by spec AC-256-1."""
        data = _minimal_answers()
        del data["C"]
        with pytest.raises(MissingBlockError) as exc_info:
            validate_answers(data)
        assert str(exc_info.value) == "[BLOCKED] create-spec headless: missing answer for Block C"

    def test_extra_keys_are_tolerated(self) -> None:
        """Unknown keys beyond A-G do not raise."""
        data = _minimal_answers()
        data["Z"] = "extra block"
        result = validate_answers(data)
        assert result is None

    def test_empty_string_value_is_valid(self) -> None:
        """Empty string answers are structurally valid (not a missing block)."""
        data = _minimal_answers()
        data["A"] = ""
        result = validate_answers(data)
        assert result is None


# ---------------------------------------------------------------------------
# Round-trip: load_answers_file + validate_answers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRoundTrip:
    """Combined load + validate covers the full headless path."""

    def test_valid_file_passes_full_round_trip(self, tmp_path: Path) -> None:
        answers_path = _write_answers(tmp_path / "answers.yaml", _minimal_answers())
        data = load_answers_file(answers_path)
        result = validate_answers(data)
        assert result is None

    def test_missing_block_in_file_fails_round_trip(self, tmp_path: Path) -> None:
        partial = _minimal_answers()
        del partial["G"]
        answers_path = _write_answers(tmp_path / "partial.yaml", partial)
        data = load_answers_file(answers_path)
        with pytest.raises(MissingBlockError) as exc_info:
            validate_answers(data)
        assert "[BLOCKED] create-spec headless: missing answer for Block G" in str(exc_info.value)

    def test_multiline_block_answers_preserved(self, tmp_path: Path) -> None:
        data = _minimal_answers()
        data["A"] = textwrap.dedent("""\
            1. Project is foo.
            2. Current state: bar.
            3. No behavior changes.
        """)
        answers_path = _write_answers(tmp_path / "multiline.yaml", data)
        loaded = load_answers_file(answers_path)
        assert "Project is foo" in loaded["A"]
        validate_answers(loaded)
