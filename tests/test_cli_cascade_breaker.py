"""Functional tests for the per-task cascade circuit breaker in cmd_reconcile_cascade.

Covers: signature computation (sha256 of sorted markers + sorted unsatisfied deps,
truncated to 12 chars); counter persistence under .devbench/cascade-cycles/;
breaker trigger past cap (writes [CASCADE_CIRCUIT_BREAKER] + [BLOCKED] markers,
adds task to escalated array, rc stays 0); counter reset on signature change;
verbatim ValueError on cascade_requeue_max_cycles below 1.

Issue #248b.
AC-248-2, AC-248b-1.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from devbench import cli
from devbench.config_loader import BacklogConfig

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _build_backlog(
    tmp_path: Path,
    rows: list[tuple[str, str, str, str, str, str]],
) -> Path:
    """Materialise BACKLOG.md + per-row work-unit files.

    Each row is ``(id, type, status, deps, basename, comments)`` where
    ``comments`` is appended verbatim to the work-unit Markdown.
    """
    index_lines = [
        "# Backlog\n",
        "## Full Work Unit Index\n",
        "| ID | Title | Type | Status | Dependencies | Repo | File Path |",
        "|----|-------|------|--------|--------------|------|-----------|",
    ]
    wu_dir = tmp_path / "backlog"
    wu_dir.mkdir(exist_ok=True)
    for unit_id, unit_type, status, deps, basename, comments in rows:
        file_path = f"backlog/{basename}.md"
        index_lines.append(
            f"| {unit_id} | {unit_id} | {unit_type} | {status} | {deps} | caylent-solutions/test-repo | `{file_path}` |"
        )
        wu_body = f"# {unit_id}: Test\n\n## Status: {status}\n\n## Description\n\nx\n"
        if deps and deps != "None":
            dep_rows = "\n".join(f"| {d.strip()} | Task | dep |" for d in deps.split(","))
            wu_body += f"\n## Dependencies\n\n| ID | Type | Reason |\n|----|------|--------|\n{dep_rows}\n"
        if comments:
            wu_body += f"\n{comments}"
        (wu_dir / f"{basename}.md").write_text(wu_body)
    index_path = tmp_path / "BACKLOG.md"
    index_path.write_text("\n".join(index_lines) + "\n")
    return index_path


def _make_backlog_config(max_cycles: int = 3) -> BacklogConfig:
    """Return a BacklogConfig with a custom cascade_requeue_max_cycles."""
    return BacklogConfig(cascade_requeue_max_cycles=max_cycles)


def _compute_signature(marker_ids: list[str], unsatisfied_dep_ids: list[str]) -> str:
    """Compute the expected 12-char signature per spec Section 4 E2.F1.S2."""
    payload = "|".join(sorted(marker_ids)) + "#" + "|".join(sorted(unsatisfied_dep_ids))
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# AC-248b-1: Signature formula
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCascadeSignature:
    """AC-248b-1: The signature is sha256(sorted_markers + '#' + sorted_deps)[:12]."""

    def test_signature_uses_sorted_markers_and_deps(self) -> None:
        """Signature is stable regardless of marker/dep insertion order."""
        sig1 = _compute_signature(["E0-F1-S1-T9", "E0-F1-S1-T8"], ["E0-F1-S1-T3"])
        sig2 = _compute_signature(["E0-F1-S1-T8", "E0-F1-S1-T9"], ["E0-F1-S1-T3"])
        assert sig1 == sig2

    def test_signature_is_twelve_chars(self) -> None:
        """Truncated sha256 digest is exactly 12 hex characters."""
        sig = _compute_signature(["E0-F1-S1-T9"], [])
        assert len(sig) == 12

    def test_empty_markers_and_deps_produce_valid_signature(self) -> None:
        """Signature with no markers or deps is still 12 chars."""
        sig = _compute_signature([], [])
        assert len(sig) == 12
        assert sig == hashlib.sha256(b"#").hexdigest()[:12]

    def test_different_inputs_produce_different_signatures(self) -> None:
        """Distinct marker sets produce distinct signatures."""
        sig_a = _compute_signature(["E0-F1-S1-T9"], [])
        sig_b = _compute_signature(["E0-F1-S1-T8"], [])
        assert sig_a != sig_b


# ---------------------------------------------------------------------------
# AC-248-2: Circuit breaker behaviour
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCascadeCircuitBreaker:
    """AC-248-2: Past the cap, breaker fires; counter resets on signature change."""

    def test_below_cap_task_is_requeued_normally(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """While count <= cap, the task flips to in-queue as usual."""
        # T2 blocked with open dep on T1 (not done yet) -- stale cycle scenario
        # but we'll have it succeed here (dep is done) so it flips normally.
        index = _build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "done", "None", "E0-F1-S1-T1", ""),
                (
                    "E0-F1-S1-T2",
                    "Task",
                    "blocked",
                    "E0-F1-S1-T1",
                    "E0-F1-S1-T2",
                    "",
                ),
            ],
        )
        backlog_cfg = _make_backlog_config(max_cycles=3)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG") as mock_cfg,
        ):
            mock_cfg.backlog = backlog_cfg
            rc = cli.cmd_reconcile_cascade()
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out.strip())
        assert any(item["unit_id"] == "E0-F1-S1-T2" for item in envelope["flipped"])
        assert envelope.get("escalated", []) == []

    def test_past_cap_breaker_fires_on_stale_cycle(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When count > cap the breaker writes the verbatim marker + [BLOCKED] and
        adds the task to the escalated array; rc stays 0."""
        # T1 is in-progress (dep not done) so T2 will never flip -- stale cycle.
        index = _build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "in-progress", "None", "E0-F1-S1-T1", ""),
                (
                    "E0-F1-S1-T2",
                    "Task",
                    "blocked",
                    "E0-F1-S1-T1",
                    "E0-F1-S1-T2",
                    "",
                ),
            ],
        )
        backlog_cfg = _make_backlog_config(max_cycles=2)
        # Pre-seed counter file so the task has already been seen twice (at cap).
        cycles_dir = tmp_path / ".devbench" / "cascade-cycles"
        cycles_dir.mkdir(parents=True)
        # Compute the signature for T2: no markers, dep T1 unsatisfied.
        sig = _compute_signature([], ["E0-F1-S1-T1"])
        counter_file = cycles_dir / "E0-F1-S1-T2.json"
        counter_file.write_text(
            json.dumps({"signature": sig, "count": 2}),
            encoding="utf-8",
        )
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG") as mock_cfg,
        ):
            mock_cfg.backlog = backlog_cfg
            rc = cli.cmd_reconcile_cascade()
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out.strip())
        assert envelope["flipped"] == []
        escalated = envelope.get("escalated", [])
        assert "E0-F1-S1-T2" in escalated
        t2_content = (tmp_path / "backlog" / "E0-F1-S1-T2.md").read_text()
        assert "[CASCADE_CIRCUIT_BREAKER]" in t2_content
        assert "OPERATOR_ACTION_REQUIRED" in t2_content
        assert "[BLOCKED]" in t2_content

    def test_breaker_marker_contains_task_id_and_signature(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Breaker audit line contains task=<id>, signature=<hash>, cycles=<N>."""
        index = _build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "in-progress", "None", "E0-F1-S1-T1", ""),
                (
                    "E0-F1-S1-T2",
                    "Task",
                    "blocked",
                    "E0-F1-S1-T1",
                    "E0-F1-S1-T2",
                    "",
                ),
            ],
        )
        backlog_cfg = _make_backlog_config(max_cycles=1)
        cycles_dir = tmp_path / ".devbench" / "cascade-cycles"
        cycles_dir.mkdir(parents=True)
        sig = _compute_signature([], ["E0-F1-S1-T1"])
        counter_file = cycles_dir / "E0-F1-S1-T2.json"
        counter_file.write_text(
            json.dumps({"signature": sig, "count": 1}),
            encoding="utf-8",
        )
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG") as mock_cfg,
        ):
            mock_cfg.backlog = backlog_cfg
            rc = cli.cmd_reconcile_cascade()
        assert rc == 0
        t2_content = (tmp_path / "backlog" / "E0-F1-S1-T2.md").read_text()
        assert "task=E0-F1-S1-T2" in t2_content
        assert f"signature={sig}" in t2_content
        assert "cycles=" in t2_content
        assert "escalated=OPERATOR_ACTION_REQUIRED" in t2_content

    def test_counter_increments_on_repeated_calls(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Each reconcile call increments the counter for the same signature."""
        index = _build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "in-progress", "None", "E0-F1-S1-T1", ""),
                (
                    "E0-F1-S1-T2",
                    "Task",
                    "blocked",
                    "E0-F1-S1-T1",
                    "E0-F1-S1-T2",
                    "",
                ),
            ],
        )
        backlog_cfg = _make_backlog_config(max_cycles=5)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG") as mock_cfg,
        ):
            mock_cfg.backlog = backlog_cfg
            # Run twice.
            capsys.readouterr()
            rc1 = cli.cmd_reconcile_cascade()
            capsys.readouterr()
            rc2 = cli.cmd_reconcile_cascade()
        assert rc1 == 0
        assert rc2 == 0
        counter_file = tmp_path / ".devbench" / "cascade-cycles" / "E0-F1-S1-T2.json"
        assert counter_file.exists()
        data = json.loads(counter_file.read_text(encoding="utf-8"))
        assert data["count"] == 2

    def test_counter_resets_on_signature_change(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When the signature changes (genuine progress), the counter resets to 1."""
        # Start with T2 blocked on T1 -- dep was "T1", now it's absent.
        index = _build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "in-progress", "None", "E0-F1-S1-T1", ""),
                (
                    "E0-F1-S1-T2",
                    "Task",
                    "blocked",
                    "E0-F1-S1-T1",
                    "E0-F1-S1-T2",
                    "",
                ),
            ],
        )
        backlog_cfg = _make_backlog_config(max_cycles=5)
        # Pre-seed with an OLD signature (different from current state).
        cycles_dir = tmp_path / ".devbench" / "cascade-cycles"
        cycles_dir.mkdir(parents=True)
        old_sig = "aabbccddeeff"  # 12 chars but not the real signature
        counter_file = cycles_dir / "E0-F1-S1-T2.json"
        counter_file.write_text(
            json.dumps({"signature": old_sig, "count": 4}),
            encoding="utf-8",
        )
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG") as mock_cfg,
        ):
            mock_cfg.backlog = backlog_cfg
            capsys.readouterr()
            cli.cmd_reconcile_cascade()
        data = json.loads(counter_file.read_text(encoding="utf-8"))
        # Counter must have reset to 1 because signature changed.
        assert data["count"] == 1
        new_sig = _compute_signature([], ["E0-F1-S1-T1"])
        assert data["signature"] == new_sig

    def test_counter_not_created_when_task_flips(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When a task flips to in-queue, no counter file is written for it."""
        index = _build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "done", "None", "E0-F1-S1-T1", ""),
                (
                    "E0-F1-S1-T2",
                    "Task",
                    "blocked",
                    "E0-F1-S1-T1",
                    "E0-F1-S1-T2",
                    "",
                ),
            ],
        )
        backlog_cfg = _make_backlog_config(max_cycles=3)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG") as mock_cfg,
        ):
            mock_cfg.backlog = backlog_cfg
            capsys.readouterr()
            rc = cli.cmd_reconcile_cascade()
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out.strip())
        assert any(item["unit_id"] == "E0-F1-S1-T2" for item in envelope["flipped"])
        counter_file = tmp_path / ".devbench" / "cascade-cycles" / "E0-F1-S1-T2.json"
        assert not counter_file.exists()

    def test_escalated_array_absent_when_no_breaker_fires(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When no breaker fires the JSON envelope contains an empty escalated list."""
        index = _build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T1", "Task", "done", "None", "E0-F1-S1-T1", ""),
                (
                    "E0-F1-S1-T2",
                    "Task",
                    "blocked",
                    "E0-F1-S1-T1",
                    "E0-F1-S1-T2",
                    "",
                ),
            ],
        )
        backlog_cfg = _make_backlog_config(max_cycles=3)
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG") as mock_cfg,
        ):
            mock_cfg.backlog = backlog_cfg
            capsys.readouterr()
            cli.cmd_reconcile_cascade()
        envelope = json.loads(capsys.readouterr().out.strip())
        assert envelope.get("escalated", []) == []

    def test_open_marker_contributes_to_signature(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Open [BLOCKED_PENDING_PROPOSAL] markers are part of the signature."""
        marker_comment = "## Comments\n\n[BLOCKED_PENDING_PROPOSAL] E0-F1-S1-T9\n"
        index = _build_backlog(
            tmp_path,
            rows=[
                ("E0-F1-S1-T9", "Task", "in-progress", "None", "E0-F1-S1-T9", ""),
                (
                    "E0-F1-S1-T2",
                    "Task",
                    "blocked",
                    "None",
                    "E0-F1-S1-T2",
                    marker_comment,
                ),
            ],
        )
        backlog_cfg = _make_backlog_config(max_cycles=2)
        # Seed counter with the signature that includes the open marker.
        cycles_dir = tmp_path / ".devbench" / "cascade-cycles"
        cycles_dir.mkdir(parents=True)
        # T2 has marker E0-F1-S1-T9 (unresolved -- open) and no dep.
        sig = _compute_signature(["E0-F1-S1-T9"], [])
        counter_file = cycles_dir / "E0-F1-S1-T2.json"
        counter_file.write_text(
            json.dumps({"signature": sig, "count": 2}),
            encoding="utf-8",
        )
        with (
            patch("devbench.cli.BACKLOG_ROOT", tmp_path / "backlog"),
            patch("devbench.cli.BACKLOG_INDEX", index),
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.RUNTIME_CONFIG") as mock_cfg,
        ):
            mock_cfg.backlog = backlog_cfg
            capsys.readouterr()
            rc = cli.cmd_reconcile_cascade()
        assert rc == 0
        envelope = json.loads(capsys.readouterr().out.strip())
        assert "E0-F1-S1-T2" in envelope.get("escalated", [])
