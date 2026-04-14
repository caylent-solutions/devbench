"""Unit tests for the greeting utility."""

import pytest

from devbench.utils.greeting import get_greeting


@pytest.mark.unit
class TestGetGreeting:
    """Tests for get_greeting utility function."""

    def test_returns_hello_with_name(self) -> None:
        """get_greeting returns 'Hello, <name>!' for the given name. AC-1, AC-DOC-1"""
        assert get_greeting("World") == "Hello, World!"

    def test_returns_hello_with_different_name(self) -> None:
        """get_greeting works for any name string."""
        assert get_greeting("Alice") == "Hello, Alice!"
