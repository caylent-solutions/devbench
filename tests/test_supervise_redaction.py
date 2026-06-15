"""PTY-log redaction + 0600 mode (AC-21, Section 3.6.3, FR-24).

The PtyDriver tees the PTY stream to ``pty.log``; before writing each chunk it
applies the configured redaction patterns so secrets the model echoes never land
on disk, and the file is created mode 0600.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from devbench.constants import SUPERVISE_LOG_REDACT_PATTERNS_DEFAULT
from devbench.supervise import PtyLogWriter


@pytest.mark.unit
class TestRedaction:
    """AC-21: sk-ant / AKIA / Bearer tokens are removed before writing."""

    def test_sk_ant_redacted(self, tmp_path: Path) -> None:
        log = tmp_path / "pty.log"
        writer = PtyLogWriter(path=log, redact_patterns=SUPERVISE_LOG_REDACT_PATTERNS_DEFAULT)
        writer.write("here is sk-ant-abcDEF123_- and more text")
        writer.close()
        contents = log.read_text(encoding="utf-8")
        assert "sk-ant-abcDEF123" not in contents
        assert "more text" in contents

    def test_akia_redacted(self, tmp_path: Path) -> None:
        log = tmp_path / "pty.log"
        writer = PtyLogWriter(path=log, redact_patterns=SUPERVISE_LOG_REDACT_PATTERNS_DEFAULT)
        writer.write("key AKIAIOSFODNN7EXAMPLE end")
        writer.close()
        contents = log.read_text(encoding="utf-8")
        assert "AKIAIOSFODNN7EXAMPLE" not in contents

    def test_bearer_redacted(self, tmp_path: Path) -> None:
        log = tmp_path / "pty.log"
        writer = PtyLogWriter(path=log, redact_patterns=SUPERVISE_LOG_REDACT_PATTERNS_DEFAULT)
        writer.write("Authorization: Bearer abc.def-ghi end")
        writer.close()
        contents = log.read_text(encoding="utf-8")
        assert "abc.def-ghi" not in contents


@pytest.mark.unit
class TestMode0600:
    """AC-21: the pty.log file is created mode 0600 (FR-24)."""

    def test_file_mode_is_0600(self, tmp_path: Path) -> None:
        log = tmp_path / "pty.log"
        writer = PtyLogWriter(path=log, redact_patterns=())
        writer.write("hello")
        writer.close()
        mode = stat.S_IMODE(log.stat().st_mode)
        assert mode == 0o600, f"pty.log must be 0600, got {oct(mode)}"


@pytest.mark.unit
class TestNoRedactionPatterns:
    """With no patterns, content is written verbatim (still 0600)."""

    def test_verbatim(self, tmp_path: Path) -> None:
        log = tmp_path / "pty.log"
        writer = PtyLogWriter(path=log, redact_patterns=())
        writer.write("plain text only")
        writer.close()
        assert log.read_text(encoding="utf-8") == "plain text only"

    def test_invalid_redact_pattern_fails_fast(self, tmp_path: Path) -> None:
        import re

        with pytest.raises(re.error):
            PtyLogWriter(path=tmp_path / "pty.log", redact_patterns=("(unterminated",))
