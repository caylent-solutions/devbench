"""Tests for devbench.prompts module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from devbench.prompts import load_prompt


class TestLoadPrompt:
    """Test prompt loading from disk."""

    def test_load_prompt_file_not_found_raises(self) -> None:
        """load_prompt raises FileNotFoundError for a missing prompt file."""
        with pytest.raises(FileNotFoundError, match="Prompt file not found"):
            load_prompt("nonexistent_prompt_that_does_not_exist")

    def test_load_prompt_returns_content(self, tmp_path: Path) -> None:
        """load_prompt reads and strips a prompt file from the prompts directory."""
        prompt_file = tmp_path / "test_prompt.txt"
        prompt_file.write_text("  Hello, prompt world!  \n", encoding="utf-8")

        with patch("devbench.prompts._PROMPTS_DIR", tmp_path):
            result = load_prompt("test_prompt")

        assert result == "Hello, prompt world!"
