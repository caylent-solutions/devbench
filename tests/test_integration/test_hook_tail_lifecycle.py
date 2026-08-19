"""End-to-end tests for the ``devbench hook-tail`` CLI command.

These tests spawn the command as a subprocess (mirrors the pattern in
``test_watch_against_live_log.py``) so we exercise the full argument-parsing
+ header-rendering + follow-loop + exit-code contract, not just the pure
formatter.

Follow mode is covered by the unit tests via threading; these integration
tests exclusively use ``--no-follow`` so they are deterministic and fast.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _entry(**overrides) -> dict:
    """Build a well-formed hook-logs.jsonl record."""
    base = {
        "timestamp": "2026-04-19T03:51:00Z",
        "event": "PreToolUse",
        "input": {
            "agent_type": "devbench-orchestrate:executor",
            "tool_name": "Bash",
            "tool_input": {"description": "Run tests"},
        },
    }
    base.update(overrides)
    return base


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")


def _run_hook_tail(
    workspace: Path,
    *extra_args: str,
) -> subprocess.CompletedProcess[str]:
    """Invoke ``python -m devbench.cli hook-tail`` under a clean env."""
    env = os.environ.copy()
    env["DEVBENCH_WORKSPACE_ROOT"] = str(workspace)
    env["DEVBENCH_LOG_FILE"] = str(workspace / "orchestrator.log")
    env["DEVBENCH_CLAUDE_MODEL"] = env.get("DEVBENCH_CLAUDE_MODEL", "test-model")
    # Force color off so the tests can assert on plain substrings without
    # having to strip ANSI escapes from the output.
    env["NO_COLOR"] = "1"
    # hook-tail renders in the OS local zone when neither --tz nor
    # display_timezone is set; pin the child's TZ so the wall-clock
    # assertions below hold on non-UTC hosts (the --tz tests cover zone
    # conversion explicitly).
    env["TZ"] = "UTC"
    return subprocess.run(
        [sys.executable, "-m", "devbench.cli", "hook-tail", *extra_args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=30,
    )


@pytest.fixture
def populated_workspace(tmp_path: Path) -> Path:
    """Build a tmp workspace with a hook-logs.jsonl the command can read."""
    entries = [
        _entry(timestamp="2026-04-19T00:00:00Z", event="UserPromptSubmit"),
        _entry(
            timestamp="2026-04-19T00:00:05Z",
            event="PreToolUse",
            input={
                "agent_type": "devbench-orchestrate:executor",
                "tool_name": "Bash",
                "tool_input": {"description": "Run make validate"},
            },
        ),
        _entry(
            timestamp="2026-04-19T00:00:12Z",
            event="PostToolUse",
            input={
                "agent_type": "devbench-orchestrate:executor",
                "tool_name": "Bash",
                "tool_input": {"description": "Run make validate"},
                "tool_response": {"stdout": "All checks passed\n========= 42 passed =========\n"},
            },
        ),
    ]
    _write_jsonl(tmp_path / "hook-logs.jsonl", entries)
    return tmp_path


class TestHookTailDefaultPath:
    """The command reads $DEVBENCH_WORKSPACE_ROOT/hook-logs.jsonl when no path is given."""

    def test_prints_header_with_workspace_path(self, populated_workspace: Path) -> None:
        result = _run_hook_tail(populated_workspace, "--no-follow", "--from-start")
        assert result.returncode == 0, f"stderr:\n{result.stderr}"
        assert "devbench hook-tail" in result.stdout
        assert str(populated_workspace / "hook-logs.jsonl") in result.stdout

    def test_prints_formatted_rows_for_each_entry(self, populated_workspace: Path) -> None:
        result = _run_hook_tail(populated_workspace, "--no-follow", "--from-start")
        assert result.returncode == 0
        assert "00:00:00" in result.stdout
        assert "00:00:05" in result.stdout
        assert "00:00:12" in result.stdout
        # Event glyphs
        assert "U>" in result.stdout
        assert "->" in result.stdout
        assert "<-" in result.stdout

    def test_stdout_preview_rendered_for_post_tool_use(self, populated_workspace: Path) -> None:
        result = _run_hook_tail(populated_workspace, "--no-follow", "--from-start")
        assert result.returncode == 0
        # Last non-empty stdout line of the PostToolUse entry is the test-summary line.
        assert "42 passed" in result.stdout

    def test_seek_to_eof_default_produces_only_header(self, populated_workspace: Path) -> None:
        result = _run_hook_tail(populated_workspace, "--no-follow")
        assert result.returncode == 0
        # Exactly the two header lines; no row output because we sought to EOF.
        non_header = [line for line in result.stdout.splitlines() if line and not line.startswith("# ")]
        assert non_header == []


class TestHookTailExplicitPath:
    """An explicit path argument overrides the default workspace location."""

    def test_explicit_path_read(self, tmp_path: Path, populated_workspace: Path) -> None:
        custom = tmp_path / "elsewhere" / "custom.jsonl"
        custom.parent.mkdir(parents=True)
        _write_jsonl(
            custom,
            [_entry(timestamp="2026-04-19T09:09:09Z", event="PreToolUse")],
        )
        result = _run_hook_tail(populated_workspace, str(custom), "--no-follow", "--from-start")
        assert result.returncode == 0
        assert str(custom) in result.stdout
        assert "09:09:09" in result.stdout
        # The default workspace hook log was NOT read.
        assert "00:00:00" not in result.stdout


class TestHookTailTimezoneOverride:
    """--tz converts UTC timestamps to the target zone."""

    def test_utc_override_keeps_timestamp(self, populated_workspace: Path) -> None:
        result = _run_hook_tail(populated_workspace, "--no-follow", "--from-start", "--tz", "UTC")
        assert result.returncode == 0
        assert "UTC" in result.stdout
        assert "00:00:00" in result.stdout

    def test_new_york_override_applies_offset(self, populated_workspace: Path) -> None:
        result = _run_hook_tail(populated_workspace, "--no-follow", "--from-start", "--tz", "America/New_York")
        assert result.returncode == 0
        assert "America/New_York" in result.stdout
        # 2026-04-19 is EDT; 00:00:00 UTC -> 2026-04-18 20:00:00 EDT.
        assert "20:00:00" in result.stdout

    def test_invalid_tz_exits_2(self, populated_workspace: Path) -> None:
        result = _run_hook_tail(populated_workspace, "--tz", "Not/AZone")
        assert result.returncode == 2
        assert "unknown timezone" in result.stderr
        assert "Not/AZone" in result.stderr

    def test_missing_tz_value_exits_2(self, populated_workspace: Path) -> None:
        result = _run_hook_tail(populated_workspace, "--tz")
        assert result.returncode == 2
        assert "--tz requires a value" in result.stderr


class TestHookTailMalformed:
    """Malformed JSON entries render a sentinel row instead of crashing."""

    def test_bad_json_line_renders_sentinel(self, tmp_path: Path) -> None:
        log = tmp_path / "hook-logs.jsonl"
        log.write_text(
            "not valid json at all\n" + json.dumps(_entry(timestamp="2026-04-19T11:22:33Z")) + "\n",
            encoding="utf-8",
        )
        result = _run_hook_tail(tmp_path, "--no-follow", "--from-start")
        assert result.returncode == 0
        assert "bad-json" in result.stdout
        assert "11:22:33" in result.stdout


class TestHookTailArgumentHygiene:
    """Unknown flags and bogus positionals exit 2 cleanly."""

    def test_unknown_flag_exits_2(self, populated_workspace: Path) -> None:
        result = _run_hook_tail(populated_workspace, "--nope")
        assert result.returncode == 2
        assert "unknown flag" in result.stderr

    def test_two_positionals_exits_2(self, populated_workspace: Path) -> None:
        result = _run_hook_tail(populated_workspace, "/tmp/a", "/tmp/b")
        assert result.returncode == 2
        assert "unexpected positional argument" in result.stderr

    def test_missing_file_with_no_follow_exits_1(self, tmp_path: Path) -> None:
        # No hook-logs.jsonl in this tmp workspace.
        result = _run_hook_tail(tmp_path, "--no-follow")
        assert result.returncode == 1
        assert "file not found" in result.stderr
