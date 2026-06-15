"""attach --screen is fail-fast-disabled until DI-4 (AC-33, Section 3.6.5, FR-26).

``supervise attach --screen`` MUST exit 2 with the documented message while DI-4
is unconfirmed; ``supervise attach`` (no flags) is the read-only PTY-log follow.
Phase 2 lands the ``--screen`` gate; the read-only follow body lands in Phase 4,
so here we only assert the gate fires (the no-flag path is asserted reachable).
"""

from __future__ import annotations

import pytest

from devbench import cli


@pytest.mark.unit
class TestAttachScreenGated:
    """AC-33: --screen fails fast (exit 2) with the documented message."""

    def test_screen_flag_fails_fast(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.cmd_supervise("attach", "--name", "nightly", "--screen")
        assert rc == 2
        err = capsys.readouterr().err
        assert "--screen attach is not enabled" in err
