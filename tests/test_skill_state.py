"""Tests for devbench.skill_state -- bounded skill iterate-until-perfect mechanism (#204).

Covers :class:`SkillState`, :func:`read_checkpoint`, :func:`write_checkpoint`,
and :func:`emit_audit`. Verifies the atomic-write contract (tmp + rename),
the fail-fast reads (no silent recovery), and the audit-row format.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from devbench.skill_state import (
    SkillState,
    _checkpoint_path,
    emit_audit,
    read_checkpoint,
    write_checkpoint,
)

_SKILL_NAMES = ["create-spec", "spec-to-backlog", "bootstrap-environment", "configure-devbench"]

_AUDIT_LINE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z \[skill_state\] "
    r"\[SKILL_[A-Z_]+\] skill=\S+( [a-z_]+=\S+)*$"
)


@pytest.mark.unit
class TestCheckpointRoundTrip:
    """write_checkpoint + read_checkpoint round-trip the SkillState exactly."""

    @pytest.mark.parametrize("skill_name", _SKILL_NAMES)
    def test_read_returns_none_when_checkpoint_absent(self, tmp_path: Path, skill_name: str) -> None:
        """First-call semantics: no file on disk -> None, no exception."""
        assert read_checkpoint(skill_name, tmp_path) is None

    @pytest.mark.parametrize("skill_name", _SKILL_NAMES)
    def test_write_then_read_round_trips_state(self, tmp_path: Path, skill_name: str) -> None:
        state = SkillState(iteration=2, unresolved_count=7, started_at="2026-05-18T13:00:00Z")
        write_checkpoint(skill_name, state, tmp_path)

        result = read_checkpoint(skill_name, tmp_path)
        assert result == state

    def test_checkpoint_path_lives_under_skill_state_dir(self, tmp_path: Path) -> None:
        """The on-disk file is at <workspace>/.devbench/skill-state/<skill>.json."""
        write_checkpoint(
            "create-spec",
            SkillState(iteration=1, unresolved_count=3, started_at="2026-05-18T13:00:00Z"),
            tmp_path,
        )
        expected = tmp_path / ".devbench" / "skill-state" / "create-spec.json"
        assert expected.exists()


@pytest.mark.unit
class TestAtomicWrite:
    """write_checkpoint uses tmp-then-rename so a reader never sees a partial file."""

    def test_tmp_file_is_not_left_behind(self, tmp_path: Path) -> None:
        """After a successful write, only the final .json exists, no .tmp leftover."""
        state = SkillState(iteration=1, unresolved_count=0, started_at="2026-05-18T13:00:00Z")
        write_checkpoint("create-spec", state, tmp_path)

        skill_state_dir = tmp_path / ".devbench" / "skill-state"
        files = sorted(p.name for p in skill_state_dir.iterdir())
        assert files == ["create-spec.json"], f"expected only the final file, got {files}"

    def test_write_creates_missing_parent_directories(self, tmp_path: Path) -> None:
        """First write creates the .devbench/skill-state/ directory tree."""
        state = SkillState(iteration=0, unresolved_count=5, started_at="2026-05-18T13:00:00Z")
        write_checkpoint("spec-to-backlog", state, tmp_path)
        assert (tmp_path / ".devbench" / "skill-state").is_dir()

    def test_existing_checkpoint_is_replaced_in_place(self, tmp_path: Path) -> None:
        """Second write atomically replaces the previous state (no partial overlay)."""
        first = SkillState(iteration=1, unresolved_count=10, started_at="2026-05-18T13:00:00Z")
        second = SkillState(iteration=2, unresolved_count=4, started_at="2026-05-18T13:01:00Z")
        write_checkpoint("create-spec", first, tmp_path)
        write_checkpoint("create-spec", second, tmp_path)

        assert read_checkpoint("create-spec", tmp_path) == second


@pytest.mark.unit
class TestReadFailFast:
    """read_checkpoint raises on malformed JSON or missing fields -- no silent recovery."""

    def test_malformed_json_raises(self, tmp_path: Path) -> None:
        path = _checkpoint_path("create-spec", tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ not json", encoding="utf-8")

        with pytest.raises(json.JSONDecodeError):
            read_checkpoint("create-spec", tmp_path)

    def test_missing_field_raises_keyerror(self, tmp_path: Path) -> None:
        path = _checkpoint_path("create-spec", tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"iteration": 1}), encoding="utf-8")

        with pytest.raises(KeyError):
            read_checkpoint("create-spec", tmp_path)


@pytest.mark.unit
class TestEmitAudit:
    """emit_audit appends a single well-formed line to the orchestrator log."""

    @pytest.mark.parametrize("skill_name", _SKILL_NAMES)
    def test_max_iterations_audit_line_format(self, tmp_path: Path, skill_name: str) -> None:
        """[SKILL_MAX_ITERATIONS_REACHED] line matches the documented regex."""
        emit_audit(skill_name, "[SKILL_MAX_ITERATIONS_REACHED]", {"unresolved": "3"}, tmp_path)

        log_path = tmp_path / "logs" / "orchestrator.log"
        line = log_path.read_text(encoding="utf-8").rstrip("\n")
        assert _AUDIT_LINE_RE.match(line), f"audit line does not match expected format: {line!r}"
        assert f"skill={skill_name}" in line
        assert "unresolved=3" in line

    @pytest.mark.parametrize("skill_name", _SKILL_NAMES)
    def test_quality_threshold_audit_line_format(self, tmp_path: Path, skill_name: str) -> None:
        """[SKILL_QUALITY_THRESHOLD_REACHED] line matches the documented regex."""
        emit_audit(skill_name, "[SKILL_QUALITY_THRESHOLD_REACHED]", {}, tmp_path)

        log_path = tmp_path / "logs" / "orchestrator.log"
        line = log_path.read_text(encoding="utf-8").rstrip("\n")
        assert _AUDIT_LINE_RE.match(line)
        assert f"skill={skill_name}" in line

    def test_two_emits_append_two_lines(self, tmp_path: Path) -> None:
        """Repeated emit_audit calls append; nothing is overwritten."""
        emit_audit("create-spec", "[SKILL_QUALITY_THRESHOLD_REACHED]", {}, tmp_path)
        emit_audit("spec-to-backlog", "[SKILL_MAX_ITERATIONS_REACHED]", {"unresolved": "1"}, tmp_path)

        contents = (tmp_path / "logs" / "orchestrator.log").read_text(encoding="utf-8")
        lines = [line for line in contents.splitlines() if line]
        assert len(lines) == 2
        assert "skill=create-spec" in lines[0]
        assert "skill=spec-to-backlog" in lines[1]

    def test_invalid_tag_raises(self, tmp_path: Path) -> None:
        """Tags that don't match [SKILL_*] are rejected -- fail fast."""
        with pytest.raises(ValueError, match=r"audit tag must look like \[SKILL_\*\]"):
            emit_audit("create-spec", "NOT_A_TAG", {}, tmp_path)

    def test_whitespace_field_key_raises(self, tmp_path: Path) -> None:
        """Field keys must be whitespace-free so the audit grammar stays parseable."""
        with pytest.raises(ValueError, match="whitespace-free"):
            emit_audit("create-spec", "[SKILL_MAX_ITERATIONS_REACHED]", {"bad key": "x"}, tmp_path)

    def test_empty_field_key_raises(self, tmp_path: Path) -> None:
        """Empty field keys are also rejected."""
        with pytest.raises(ValueError, match="non-empty"):
            emit_audit("create-spec", "[SKILL_MAX_ITERATIONS_REACHED]", {"": "x"}, tmp_path)
