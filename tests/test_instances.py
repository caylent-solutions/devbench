"""Tests for the instance-discovery + lifecycle helpers (#209).

The ``instances`` module owns PID-file IO, instance-id generation,
liveness checks, and per-host enumeration.  Tests exercise each
public function against a tmp filesystem plus, for liveness, the
test process itself.
"""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path

import pytest

from devbench import instances
from devbench.instances import (
    INSTANCE_SEARCH_ROOTS_ENV,
    ORCHESTRATOR_PID_FILENAME,
    _resolve_search_roots,
    discover_instances,
    is_pid_alive,
    make_instance_id,
    pid_file_path,
    read_pid_file,
    remove_pid_file,
    resolve_instance,
    write_pid_file,
)

REPO_ROOT = Path(__file__).parent.parent
CONFIGURE_DEVBENCH_SKILL = (
    REPO_ROOT / "plugin-authoring" / "devbench-authoring" / "skills" / "configure-devbench" / "SKILL.md"
)
CLI_REFERENCE_DOC = REPO_ROOT / "docs" / "cli-reference.md"


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
        # PID 2**31-1 is never assigned in practice; if it ever IS assigned,
        # this test is wrong only transiently.
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
        # Same pid in two pid files -> dedup'd to one.
        out = discover_instances([tmp_path])
        assert len(out) == 1

    def test_env_var_overrides_search_roots(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ws = tmp_path / "alpha"
        ws.mkdir()
        write_pid_file(ws, os.getpid(), model="x")
        monkeypatch.setenv(INSTANCE_SEARCH_ROOTS_ENV, str(tmp_path))
        out = discover_instances()  # no explicit roots
        assert len(out) == 1

    def test_discover_finds_daemon_outside_home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """obs-spec G-2 worked example: no env var, workspace outside $HOME."""
        home_dir = tmp_path / "home"
        workspace_dir = tmp_path / "workspace"
        home_dir.mkdir()
        workspace_dir.mkdir()
        write_pid_file(workspace_dir, os.getpid(), session="default", mode="daemon", model="x")
        monkeypatch.setenv("HOME", str(home_dir))
        monkeypatch.setenv("DEVBENCH_WORKSPACE_ROOT", str(workspace_dir))
        monkeypatch.delenv(INSTANCE_SEARCH_ROOTS_ENV, raising=False)
        out = discover_instances()  # no explicit roots -- exercises default resolution
        assert len(out) == 1
        assert out[0].pid == os.getpid()
        assert out[0].workspace == str(workspace_dir)


@pytest.mark.unit
class TestResolveSearchRoots:
    """Covers obs-spec FR-D2 / OD-2: the default branch of _resolve_search_roots."""

    def test_default_roots_include_workspace_root(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        home_dir = tmp_path / "home"
        workspace_dir = tmp_path / "workspace"
        home_dir.mkdir()
        workspace_dir.mkdir()
        monkeypatch.setenv("HOME", str(home_dir))
        monkeypatch.setenv("DEVBENCH_WORKSPACE_ROOT", str(workspace_dir))
        monkeypatch.delenv(INSTANCE_SEARCH_ROOTS_ENV, raising=False)
        roots = _resolve_search_roots(None)
        assert Path(home_dir) in roots
        assert Path(workspace_dir) in roots

    def test_env_var_precedence_unchanged(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        home_dir = tmp_path / "home"
        workspace_dir = tmp_path / "workspace"
        env_root = tmp_path / "configured"
        home_dir.mkdir()
        workspace_dir.mkdir()
        env_root.mkdir()
        monkeypatch.setenv("HOME", str(home_dir))
        monkeypatch.setenv("DEVBENCH_WORKSPACE_ROOT", str(workspace_dir))
        monkeypatch.setenv(INSTANCE_SEARCH_ROOTS_ENV, str(env_root))
        roots = _resolve_search_roots(None)
        assert roots == [Path(env_root)]
        assert Path(workspace_dir) not in roots

    def test_workspace_under_home_not_duplicated(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        home_dir = tmp_path / "home"
        workspace_dir = home_dir / "projects" / "ws"
        home_dir.mkdir()
        workspace_dir.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(home_dir))
        monkeypatch.setenv("DEVBENCH_WORKSPACE_ROOT", str(workspace_dir))
        monkeypatch.delenv(INSTANCE_SEARCH_ROOTS_ENV, raising=False)
        roots = _resolve_search_roots(None)
        assert roots == [Path(home_dir)]

    def test_explicit_roots_bypass_env_and_workspace(self, tmp_path: Path) -> None:
        explicit = [tmp_path / "explicit"]
        assert _resolve_search_roots(explicit) == explicit


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
class TestConfigureDevbenchSkillDefaultText:
    """Pins the configure-devbench SKILL.md launcher-command description to
    the three-tier default (obs-spec OD-2).

    doc_review round-2 finding: the skill template still said the lifecycle
    commands walk PID files under DEVBENCH_INSTANCE_SEARCH_ROOTS "(default
    `~`)" after the default changed to $HOME plus DEVBENCH_WORKSPACE_ROOT.
    """

    def test_skill_exists(self) -> None:
        assert CONFIGURE_DEVBENCH_SKILL.is_file(), f"configure-devbench SKILL.md missing at {CONFIGURE_DEVBENCH_SKILL}"

    def test_skill_does_not_claim_home_tilde_default(self) -> None:
        text = CONFIGURE_DEVBENCH_SKILL.read_text(encoding="utf-8")
        assert "(default `~`)" not in text, (
            "configure-devbench SKILL.md must not claim the "
            "DEVBENCH_INSTANCE_SEARCH_ROOTS default is '~'; the default is now "
            "$HOME plus DEVBENCH_WORKSPACE_ROOT (obs-spec OD-2)."
        )

    def test_skill_names_workspace_root_in_default(self) -> None:
        text = CONFIGURE_DEVBENCH_SKILL.read_text(encoding="utf-8")
        assert "DEVBENCH_WORKSPACE_ROOT" in text.split("DEVBENCH_INSTANCE_SEARCH_ROOTS", 1)[1][:120], (
            "configure-devbench SKILL.md must name DEVBENCH_WORKSPACE_ROOT as "
            "part of the default search-root resolution, immediately after "
            "naming DEVBENCH_INSTANCE_SEARCH_ROOTS (obs-spec OD-2)."
        )


@pytest.mark.unit
class TestCliReferenceInstancesDocAccuracy:
    """Pins docs/cli-reference.md's Instances section to what cmd_instances
    actually does (doc_review round-2 findings).

    Finding 1 (FAIL): the section claimed table output is "the TTY default",
    but cmd_instances performs no isatty() check -- the table is the
    unconditional default and only --json changes the format.

    Finding 2 (WARN): the section enumerated only the 6 table columns for
    --json, omitting the 2 additional keys (workspace_name, model) that the
    --json payload actually carries.
    """

    def _instances_section(self) -> str:
        text = CLI_REFERENCE_DOC.read_text(encoding="utf-8")
        marker = "## Instances (per-host discovery)"
        start = text.index(marker)
        end = text.index("\n---\n", start + len(marker))
        return text[start:end]

    def test_does_not_claim_tty_conditional_default(self) -> None:
        section = self._instances_section()
        assert "TTY default" not in section, (
            "docs/cli-reference.md Instances section must not claim table "
            "output is 'the TTY default': cmd_instances performs no "
            "isatty() check, so table output is the unconditional default "
            "and only --json changes the format."
        )

    def test_json_payload_keys_all_enumerated(self) -> None:
        section = self._instances_section()
        # The full --json payload (src/devbench/cli.py cmd_instances) carries
        # 8 keys; workspace_name and model are not among the 6 table columns
        # and must be explicitly enumerated so a reader building a consumer
        # against the doc does not miss them.
        for key in ("workspace_name", "model"):
            assert key in section, (
                f"docs/cli-reference.md Instances section must enumerate the "
                f"--json payload key {key!r}; the current text only lists "
                f"the 6 table columns, which omits fields the --json payload "
                f"actually carries."
            )


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
        # Should not raise.
        remove_pid_file(tmp_path / "no-such-workspace")


@pytest.mark.unit
class TestNoEmDashInInstancesModule:
    """Pins the em-dash source-hygiene fix (workspace CLAUDE.md; spec AC-19).

    A post-run review (spec Section 1 G9) found two U+2014 characters
    surviving in instances.py docstrings: the branch's em-dash gate only
    scans work-unit ``.md`` files, so this pair of source em-dashes went
    undetected. This test fails loudly if either em-dash -- or any future
    one -- returns to the module.
    """

    def test_no_em_dash_in_instances_module(self) -> None:
        source = inspect.getsource(instances)
        assert "\u2014" not in source, (
            "src/devbench/instances.py must not contain the em-dash character "
            "(U+2014); use '--' (double hyphen) instead."
        )
