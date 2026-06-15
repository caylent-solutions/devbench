"""LogTailDetector: hybrid detection via the orchestrator log markers (FR-14, FR-29).

Spec Section 1.6 / 4.9 / 1.9: detection is HYBRID -- the supervisor tails the
orchestrator's OWN log for the deterministic markers (ALL_DONE / NO_ACTIONABLE /
[ORCHESTRATOR_TERMINAL_EXIT] / [QUOTA_*] / [ORCHESTRATOR_STOP_REASON] /
[ORCHESTRATOR_FATAL_ERROR] / [HARNESS_INTEGRITY] / [ORCHESTRATOR_AUTO_RESTART])
in addition to the PTY patterns, so detection does not rely on screen-scraping
alone. These markers are stable across CLI versions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.config_loader import SuperviseLogTailConfig
from devbench.supervise import LogTailDetector, LogTailKind


def _detector(tmp_path: Path) -> tuple[LogTailDetector, Path]:
    log_path = tmp_path / "logs" / "orchestrator.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")
    detector = LogTailDetector(log_path=log_path, config=SuperviseLogTailConfig())
    return detector, log_path


@pytest.mark.unit
class TestLogTailFindsEachMarkerKind:
    """Each configured marker family is detected and classified (FR-14)."""

    def test_clean_marker(self, tmp_path: Path) -> None:
        detector, log_path = _detector(tmp_path)
        log_path.write_text("some line\nALL_DONE backlog complete\n", encoding="utf-8")
        hit = detector.poll()
        assert hit is not None
        assert hit.kind is LogTailKind.CLEAN
        assert "ALL_DONE" in hit.line

    def test_terminal_exit_marker_is_clean(self, tmp_path: Path) -> None:
        detector, log_path = _detector(tmp_path)
        log_path.write_text("[ORCHESTRATOR_TERMINAL_EXIT] reason=clean\n", encoding="utf-8")
        hit = detector.poll()
        assert hit is not None
        assert hit.kind is LogTailKind.CLEAN

    def test_quota_marker(self, tmp_path: Path) -> None:
        detector, log_path = _detector(tmp_path)
        log_path.write_text("[QUOTA_WAITING] reason=claude-code-cli reset_at=unknown\n", encoding="utf-8")
        hit = detector.poll()
        assert hit is not None
        assert hit.kind is LogTailKind.QUOTA

    def test_fault_marker(self, tmp_path: Path) -> None:
        detector, log_path = _detector(tmp_path)
        log_path.write_text("[ORCHESTRATOR_STOP_REASON] reason=premature-turn-end\n", encoding="utf-8")
        hit = detector.poll()
        assert hit is not None
        assert hit.kind is LogTailKind.FAULT

    def test_restart_marker(self, tmp_path: Path) -> None:
        detector, log_path = _detector(tmp_path)
        log_path.write_text("[ORCHESTRATOR_AUTO_RESTART] reason=runtime_degradation tasks=2\n", encoding="utf-8")
        hit = detector.poll()
        assert hit is not None
        assert hit.kind is LogTailKind.RESTART


@pytest.mark.unit
class TestLogTailIncremental:
    """poll() consumes only NEW bytes since the last poll (true tailing)."""

    def test_only_new_lines_detected(self, tmp_path: Path) -> None:
        detector, log_path = _detector(tmp_path)
        log_path.write_text("warming up\n", encoding="utf-8")
        assert detector.poll() is None  # nothing actionable yet

        with log_path.open("a", encoding="utf-8") as fh:
            fh.write("still working\n")
        assert detector.poll() is None

        with log_path.open("a", encoding="utf-8") as fh:
            fh.write("ALL_DONE\n")
        hit = detector.poll()
        assert hit is not None
        assert hit.kind is LogTailKind.CLEAN

    def test_already_seen_marker_not_redetected(self, tmp_path: Path) -> None:
        detector, log_path = _detector(tmp_path)
        log_path.write_text("ALL_DONE\n", encoding="utf-8")
        assert detector.poll() is not None
        # A second poll with no new bytes returns None (the marker was consumed).
        assert detector.poll() is None


@pytest.mark.unit
class TestLogTailMissingFile:
    """An absent log file is not actionable (poll returns None, no crash)."""

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        detector = LogTailDetector(
            log_path=tmp_path / "logs" / "orchestrator.log",
            config=SuperviseLogTailConfig(),
        )
        assert detector.poll() is None


@pytest.mark.unit
class TestLogTailPrecedence:
    """Fault takes precedence over clean within a single poll batch (fail-fast)."""

    def test_fault_wins_over_clean_in_same_batch(self, tmp_path: Path) -> None:
        detector, log_path = _detector(tmp_path)
        # Both a clean and a fault marker in one batch: the fault must surface so a
        # crash is never masked by an earlier clean-looking line.
        log_path.write_text("ALL_DONE\n[ORCHESTRATOR_FATAL_ERROR] reason=model_not_found\n", encoding="utf-8")
        hit = detector.poll()
        assert hit is not None
        assert hit.kind is LogTailKind.FAULT
