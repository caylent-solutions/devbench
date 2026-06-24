"""Tests for the instance-discovery + lifecycle helpers (#209).

The ``instances`` module owns PID-file IO, instance-id generation,
liveness checks, and per-host enumeration.  Tests exercise each
public function against a tmp filesystem plus, for liveness, the
test process itself.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from devbench.instances import (
    INSTANCE_SEARCH_ROOTS_ENV,
    ORCHESTRATOR_PID_FILENAME,
    discover_instances,
    is_pid_alive,
    make_instance_id,
    pid_file_path,
    read_pid_file,
    remove_pid_file,
    resolve_instance,
    write_pid_file,
)


@pytest.mark.unit
class TestMakeInstanceId:
    def test_format_is_workspace_basename_dash_pid_suffix(self, tmp_path: Path) -> None:
        ws = tmp_path / "my-workspace"
        ws.mkdir()
        assert make_instance_id(ws, 12345) == "my-workspace-2345"

    def test_short_pid_is_left_padded(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        assert make_instance_id(ws, 42) == "ws-0042"

    def test_pid_exact_4_digits(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        ws.mkdir()
        assert make_instance_id(ws, 1234) == "ws-1234"


@pytest.mark.unit
class TestWriteAndReadPidFile:
    def test_round_trip(self, tmp_path: Path) -> None:
        ws = tmp_path / "demo"
        ws.mkdir()
        pid_path = write_pid_file(ws, 1234, session="default", mode="daemon", model="us.anthropic.claude-opus-4-7-v1")
        assert pid_path == ws / ".devbench" / ORCHESTRATOR_PID_FILENAME
        assert pid_path.is_file()
        inst = read_pid_file(pid_path)
        assert inst is not None
        assert inst.instance_id == "demo-1234"
        assert inst.pid == 1234
        assert inst.workspace == str(ws)
        assert inst.workspace_name == "demo"
        assert inst.mode == "daemon"
        assert inst.model == "us.anthropic.claude-opus-4-7-v1"

    def test_pid_file_path_helper(self, tmp_path: Path) -> None:
        assert pid_file_path(tmp_path) == tmp_path / ".devbench" / ORCHESTRATOR_PID_FILENAME

    def test_read_missing_returns_none(self, tmp_path: Path) -> None:
        assert read_pid_file(tmp_path / "missing.pid") is None

    def test_read_corrupt_json_returns_none(self, tmp_path: Path) -> None:
        pid_path = tmp_path / ".devbench" / ORCHESTRATOR_PID_FILENAME
        pid_path.parent.mkdir(parents=True)
        pid_path.write_text("not valid json{[", encoding="utf-8")
        assert read_pid_file(pid_path) is None

    def test_read_non_object_payload_returns_none(self, tmp_path: Path) -> None:
        pid_path = tmp_path / ".devbench" / ORCHESTRATOR_PID_FILENAME
        pid_path.parent.mkdir(parents=True)
        pid_path.write_text("[]", encoding="utf-8")
        assert read_pid_file(pid_path) is None

    def test_read_missing_required_fields_returns_none(self, tmp_path: Path) -> None:
        pid_path = tmp_path / ".devbench" / ORCHESTRATOR_PID_FILENAME
        pid_path.parent.mkdir(parents=True)
        pid_path.write_text(json.dumps({"workspace": "/tmp"}), encoding="utf-8")
        assert read_pid_file(pid_path) is None


@pytest.mark.unit
class TestIsPidAlive:
    def test_self_is_alive(self) -> None:
        assert is_pid_alive(os.getpid()) is True

    def test_zero_is_dead(self) -> None:
        assert is_pid_alive(0) is False

    def test_negative_is_dead(self) -> None:
        assert is_pid_alive(-1) is False

    def test_definitely_dead_pid_is_dead(self) -> None:
        assert is_pid_alive(2**31 - 1) is False


@pytest.mark.unit
class TestDiscoverInstances:
    def test_empty_root_returns_empty_list(self, tmp_path: Path) -> None:
        assert discover_instances([tmp_path]) == []

    def test_discovers_workspace_with_live_pid_file(self, tmp_path: Path) -> None:
        ws = tmp_path / "alpha"
        ws.mkdir()
        write_pid_file(ws, os.getpid(), session="default", mode="daemon", model="x")
        out = discover_instances([tmp_path])
        assert len(out) == 1
        assert out[0].instance_id.startswith("alpha-")
        assert out[0].pid == os.getpid()

    def test_filters_out_dead_pids(self, tmp_path: Path) -> None:
        ws = tmp_path / "alpha"
        ws.mkdir()
        write_pid_file(ws, 2**31 - 1, session="default", mode="daemon", model="x")
        assert discover_instances([tmp_path]) == []

    def test_two_workspaces_two_instances_deduped_by_pid(self, tmp_path: Path) -> None:
        ws_a = tmp_path / "alpha"
        ws_b = tmp_path / "beta"
        ws_a.mkdir()
        ws_b.mkdir()
        write_pid_file(ws_a, os.getpid(), model="x")
        write_pid_file(ws_b, os.getpid(), model="x")
        out = discover_instances([tmp_path])
        assert len(out) == 1

    def test_env_var_overrides_search_roots(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ws = tmp_path / "alpha"
        ws.mkdir()
        write_pid_file(ws, os.getpid(), model="x")
        monkeypatch.setenv(INSTANCE_SEARCH_ROOTS_ENV, str(tmp_path))
        out = discover_instances()
        assert len(out) == 1


@pytest.mark.unit
class TestResolveInstance:
    def test_resolves_by_instance_id(self, tmp_path: Path) -> None:
        ws = tmp_path / "alpha"
        ws.mkdir()
        write_pid_file(ws, os.getpid(), model="x")
        inst = read_pid_file(pid_file_path(ws))
        assert inst is not None
        out = resolve_instance(inst.instance_id, [tmp_path])
        assert out is not None
        assert out.pid == os.getpid()

    def test_resolves_by_raw_pid(self, tmp_path: Path) -> None:
        ws = tmp_path / "alpha"
        ws.mkdir()
        write_pid_file(ws, os.getpid(), model="x")
        out = resolve_instance(str(os.getpid()), [tmp_path])
        assert out is not None
        assert out.workspace_name == "alpha"

    def test_unknown_token_returns_none(self, tmp_path: Path) -> None:
        assert resolve_instance("nonexistent-9999", [tmp_path]) is None
        assert resolve_instance("not-a-pid-and-not-an-id", [tmp_path]) is None


@pytest.mark.unit
class TestRemovePidFile:
    def test_removes_existing(self, tmp_path: Path) -> None:
        ws = tmp_path / "alpha"
        ws.mkdir()
        write_pid_file(ws, os.getpid(), model="x")
        assert pid_file_path(ws).is_file()
        remove_pid_file(ws)
        assert not pid_file_path(ws).is_file()

    def test_noop_when_missing(self, tmp_path: Path) -> None:
        remove_pid_file(tmp_path / "no-such-workspace")
