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
        assert detector.poll() is None

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
        log_path.write_text("ALL_DONE\n[ORCHESTRATOR_FATAL_ERROR] reason=model_not_found\n", encoding="utf-8")
        hit = detector.poll()
        assert hit is not None
        assert hit.kind is LogTailKind.FAULT


@pytest.mark.unit
class TestLogTailFromEnd:
    """poll() tails from the END at construction: historical markers from PRIOR
    runs in a long-lived log are NEVER re-detected (the false-fault-on-startup
    regression: the workspace logs/orchestrator.log persists across runs and is
    full of old [ORCHESTRATOR_STOP_REASON] markers; seeding the offset to 0 made
    the first poll fault the fresh session on ancient history)."""

    def test_preexisting_markers_not_detected(self, tmp_path: Path) -> None:
        log_path = tmp_path / "logs" / "orchestrator.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            "[ORCHESTRATOR_STOP_REASON] reason=old-run\nALL_DONE old backlog\n",
            encoding="utf-8",
        )
        detector = LogTailDetector(log_path=log_path, config=SuperviseLogTailConfig())
        assert detector.poll() is None
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write("[ORCHESTRATOR_STOP_REASON] reason=new-run\n")
        hit = detector.poll()
        assert hit is not None
        assert hit.kind is LogTailKind.FAULT
        assert "new-run" in hit.line

    def test_rotated_log_reread_from_new_start(self, tmp_path: Path) -> None:
        log_path = tmp_path / "logs" / "orchestrator.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("x" * 200 + "\n", encoding="utf-8")
        detector = LogTailDetector(log_path=log_path, config=SuperviseLogTailConfig())
        assert detector.poll() is None
        log_path.write_text("ALL_DONE fresh\n", encoding="utf-8")
        hit = detector.poll()
        assert hit is not None
        assert hit.kind is LogTailKind.CLEAN


@pytest.mark.unit
class TestLogTailProgressed:
    """progressed() is the progress watchdog's log-growth signal (design point 2).

    It reports whether the orchestrator log GREW since the previous call, using a
    cheap ``stat`` size comparison against a retained offset SEPARATE from poll()'s
    marker offset -- so the watchdog and the marker detector never consume each
    other's growth. It reads NO content (a multi-hundred-MB log must never be read
    whole every poll). ANY appended bytes count as progress, including a benign
    [LONG_OP_HEARTBEAT] line that matches no marker family.
    """

    def test_no_growth_returns_false(self, tmp_path: Path) -> None:
        detector, _log_path = _detector(tmp_path)
        assert detector.progressed() is False
        assert detector.progressed() is False

    def test_growth_returns_true_once_then_false(self, tmp_path: Path) -> None:
        detector, log_path = _detector(tmp_path)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write("[USER] claimed E1-F1-S1-T1\n")
        assert detector.progressed() is True
        assert detector.progressed() is False

    def test_heartbeat_line_counts_as_growth(self, tmp_path: Path) -> None:
        detector, log_path = _detector(tmp_path)
        from devbench.constants import SUPERVISE_LONG_OP_HEARTBEAT_MARKER

        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"{SUPERVISE_LONG_OP_HEARTBEAT_MARKER} verb=verify-ac unit=E1-F1-S1-T1 elapsed=60s\n")
        assert detector.progressed() is True
        assert detector.poll() is None

    def test_progressed_is_independent_of_poll_offset(self, tmp_path: Path) -> None:
        detector, log_path = _detector(tmp_path)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write("ALL_DONE backlog complete\n")
        assert detector.progressed() is True
        hit = detector.poll()
        assert hit is not None
        assert hit.kind is LogTailKind.CLEAN

    def test_missing_file_is_no_progress(self, tmp_path: Path) -> None:
        detector = LogTailDetector(
            log_path=tmp_path / "logs" / "orchestrator.log",
            config=SuperviseLogTailConfig(),
        )
        assert detector.progressed() is False

    def test_truncation_then_growth_is_progress(self, tmp_path: Path) -> None:
        detector, log_path = _detector(tmp_path)
        log_path.write_text("x" * 500, encoding="utf-8")
        assert detector.progressed() is True
        log_path.write_text("y" * 10, encoding="utf-8")
        assert detector.progressed() is True
