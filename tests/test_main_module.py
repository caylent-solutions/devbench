"""Tests for devbench.__main__ module."""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestMainModule:
    """Test that python -m devbench invokes main()."""

    def test_main_module_calls_main(self) -> None:
        """Running __main__.py calls cli.main and raises SystemExit with its return code."""
        with patch("devbench.cli.main", return_value=0) as mock_main:
            try:
                import runpy

                runpy.run_module("devbench", run_name="__main__", alter_sys=False)
            except SystemExit as exc:
                assert exc.code == 0
            else:
                pytest.fail("Expected SystemExit to be raised")

        mock_main.assert_called_once()
