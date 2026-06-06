"""Tests for start --help scope flags (issue #249, AC-249-1, AC-249a-1)."""

from __future__ import annotations

import sys
from io import StringIO
from unittest.mock import patch

import pytest

from devbench import cli


@pytest.mark.parametrize(
    "token",
    ["--include", "--exclude", "--name", "--allow-overlap", "--daemon"],
)
def test_start_help_contains_scope_flag_token(token: str) -> None:
    """AC-249-1: start --help output contains all five scope flag tokens."""
    captured = StringIO()
    with (
        patch.object(sys, "argv", ["devbench", "start", "--help"]),
        patch("sys.stdout", captured),
    ):
        exit_code = cli.main()

    assert exit_code == 0, f"start --help exited with code {exit_code}"
    output = captured.getvalue()
    assert token in output, f"start --help output missing token {token!r}; got: {output!r}"


def test_start_help_is_single_description_string() -> None:
    """AC-249a-1: the start _COMMANDS entry is a single (non-tuple) description string."""
    _, _, desc = cli._COMMANDS["start"]
    assert isinstance(desc, str), f"start description must be a str, got {type(desc).__name__}"
    # Confirm all five tokens appear in the single string itself (not just at runtime)
    for token in ("--include", "--exclude", "--name", "--allow-overlap", "--daemon"):
        assert token in desc, f"start description string missing token {token!r}; desc={desc!r}"
