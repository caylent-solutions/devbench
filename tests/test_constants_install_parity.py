"""Tests for the shared INSTALL_PARITY_SHORT_REVISION_CHARS constant.

devbench.constants is the single shared home for the install-parity
short-revision (git-abbrev) length (issue #301, AC-FIX-001). Consumers such
as devbench.reporting.report and devbench.cli import this constant instead
of defining a private duplicate.
"""

from __future__ import annotations

import pytest


class TestInstallParityShortRevisionCharsConstant:
    """INSTALL_PARITY_SHORT_REVISION_CHARS is exported from constants.py with
    the correct type, value, and module-level placement (AC-T4-1..AC-T4-3).
    """

    @pytest.mark.unit
    def test_install_parity_short_revision_chars_is_importable(self) -> None:
        """INSTALL_PARITY_SHORT_REVISION_CHARS is importable from devbench.constants without error."""
        import devbench.constants as _c

        assert hasattr(_c, "INSTALL_PARITY_SHORT_REVISION_CHARS")

    @pytest.mark.unit
    def test_install_parity_short_revision_chars_is_module_level(self) -> None:
        """INSTALL_PARITY_SHORT_REVISION_CHARS is defined at module scope in devbench.constants."""
        import devbench.constants as _c

        assert "INSTALL_PARITY_SHORT_REVISION_CHARS" in vars(_c)

    @pytest.mark.unit
    def test_install_parity_short_revision_chars_is_int_not_bool(self) -> None:
        """INSTALL_PARITY_SHORT_REVISION_CHARS is a plain int, not a bool (bool is an int subclass)."""
        from devbench.constants import INSTALL_PARITY_SHORT_REVISION_CHARS

        assert isinstance(INSTALL_PARITY_SHORT_REVISION_CHARS, int)
        assert not isinstance(INSTALL_PARITY_SHORT_REVISION_CHARS, bool)

    @pytest.mark.unit
    def test_install_parity_short_revision_chars_value(self) -> None:
        """INSTALL_PARITY_SHORT_REVISION_CHARS equals the git short-revision abbreviation length, 7."""
        from devbench.constants import INSTALL_PARITY_SHORT_REVISION_CHARS

        assert INSTALL_PARITY_SHORT_REVISION_CHARS == 7
