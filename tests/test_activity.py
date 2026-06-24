"""Tests for devbench.activity (the `devbench watch` dashboard)."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from devbench import activity
from devbench.activity import (
    DEFAULT_MAX_CLI_EVENTS,
    DEFAULT_MAX_TOOLS,
    IDLE_THRESHOLD_SECONDS,
    MAX_AGENT_TEXT_CHARS,
    MAX_TOOL_SUMMARY_CHARS,
    ActivitySnapshot,
    AmendmentState,
    CliEvent,
    RepoState,
    SubagentActivity,
    ToolCallEvent,
    _ActiveTask,
    _coerce_transcript_timestamp,
    _compute_idle_seconds,
    _extract_latest_text_from_entry,
    _extract_tool_summary,
    _extract_tools_from_entry,
    _find_active_task,
    _find_most_recent_claim,
    _parse_iso_timestamp,
    _parse_log_line,
    _run_git,
    _truncate,
    check_amendment_request,
    collect_snapshot,
    detect_phase,
    discover_session_dir,
    find_active_subagent,
    mode_label,
    parse_orchestrator_recent_cli,
    parse_repo_state,
    parse_subagent_recent_activity,
    render_snapshot,
)
from devbench.config_loader import GitOpsConfig, RuntimeConfig


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def _make_runtime_config(
    *,
    update_submodule: bool = False,
    single_branch: str | None = None,
    defer_pr: bool = False,
) -> RuntimeConfig:
    """Return a minimal RuntimeConfig with optional GitOpsConfig overrides."""
    git_ops = GitOpsConfig(update_submodule=update_submodule, single_branch=single_branch, defer_pr=defer_pr)
    return RuntimeConfig(git_ops=git_ops)


class TestParseIsoTimestamp:
    def test_returns_none_on_empty_string(self) -> None:
        assert _parse_iso_timestamp("") is None

    def test_returns_none_on_garbage(self) -> None:
        assert _parse_iso_timestamp("not a timestamp") is None

    def test_parses_z_suffix(self) -> None:
        dt = _parse_iso_timestamp("2026-04-18T03:00:00Z")
        assert dt is not None
        assert dt.tzinfo is not None
        assert dt == datetime(2026, 4, 18, 3, 0, 0, tzinfo=UTC)

    def test_parses_iso_with_offset(self) -> None:
        dt = _parse_iso_timestamp("2026-04-18T03:00:00+00:00")
        assert dt == datetime(2026, 4, 18, 3, 0, 0, tzinfo=UTC)

    def test_naive_timestamp_gets_utc_tz(self) -> None:
        dt = _parse_iso_timestamp("2026-04-18T03:00:00")
        assert dt is not None
        assert dt.tzinfo is UTC


class TestTruncate:
    def test_no_change_when_under_limit(self) -> None:
        assert _truncate("hello", 10) == "hello"

    def test_truncates_with_ellipsis(self) -> None:
        out = _truncate("hello world", 8)
        assert out == "hello..."
        assert len(out) == 8

    def test_handles_tiny_limit(self) -> None:
        out = _truncate("abcdef", 2)
        assert out == "..."


class TestExtractToolSummary:
    def test_bash_command(self) -> None:
        assert _extract_tool_summary("Bash", {"command": "ls -la"}) == "ls -la"

    def test_read_file_path(self) -> None:
        assert _extract_tool_summary("Read", {"file_path": "/x/y"}) == "/x/y"

    def test_write_file_path(self) -> None:
        assert _extract_tool_summary("Write", {"file_path": "/a/b"}) == "/a/b"

    def test_edit_file_path(self) -> None:
        assert _extract_tool_summary("Edit", {"file_path": "/c"}) == "/c"

    def test_grep_pattern_and_path(self) -> None:
        assert _extract_tool_summary("Grep", {"pattern": "foo", "path": "src"}) == "foo @ src"

    def test_grep_pattern_no_path(self) -> None:
        assert _extract_tool_summary("Grep", {"pattern": "foo"}) == "foo"

    def test_glob_pattern(self) -> None:
        assert _extract_tool_summary("Glob", {"pattern": "**/*.py"}) == "**/*.py"

    def test_unknown_tool_serialises_input(self) -> None:
        out = _extract_tool_summary("MyCustomTool", {"a": 1})
        assert "a" in out

    def test_non_dict_input_returns_empty(self) -> None:
        assert _extract_tool_summary("Bash", None) == ""
        assert _extract_tool_summary("Bash", "raw string") == ""

    def test_truncates_long_commands(self) -> None:
        out = _extract_tool_summary("Bash", {"command": "x" * (MAX_TOOL_SUMMARY_CHARS + 10)})
        assert len(out) == MAX_TOOL_SUMMARY_CHARS

    def test_non_serialisable_fallback_returns_empty(self) -> None:
        class _NotJsonable:
            pass

        out = _extract_tool_summary("CustomTool", {"obj": _NotJsonable()})
        assert out == ""


class TestExtractLatestTextFromEntry:
    def test_returns_none_for_missing_message(self) -> None:
        assert _extract_latest_text_from_entry({}) is None

    def test_returns_none_when_message_not_dict(self) -> None:
        assert _extract_latest_text_from_entry({"message": 42}) is None

    def test_plain_string_content(self) -> None:
        assert _extract_latest_text_from_entry({"message": {"content": "hello"}}) == "hello"

    def test_empty_string_content_returns_none(self) -> None:
        assert _extract_latest_text_from_entry({"message": {"content": ""}}) is None

    def test_returns_none_when_content_not_list_or_string(self) -> None:
        assert _extract_latest_text_from_entry({"message": {"content": 123}}) is None

    def test_joins_multiple_text_blocks(self) -> None:
        entry = {
            "message": {
                "content": [
                    {"type": "text", "text": "first"},
                    {"type": "tool_use", "name": "Bash"},
                    {"type": "text", "text": "second"},
                ]
            }
        }
        assert _extract_latest_text_from_entry(entry) == "first\nsecond"

    def test_skips_blocks_that_are_not_dicts(self) -> None:
        entry = {"message": {"content": [None, {"type": "text", "text": "ok"}]}}
        assert _extract_latest_text_from_entry(entry) == "ok"

    def test_empty_text_block_is_ignored(self) -> None:
        entry = {"message": {"content": [{"type": "text", "text": "   "}]}}
        assert _extract_latest_text_from_entry(entry) is None

    def test_non_string_text_field_is_ignored(self) -> None:
        entry = {"message": {"content": [{"type": "text", "text": 99}]}}
        assert _extract_latest_text_from_entry(entry) is None


class TestExtractToolsFromEntry:
    def test_returns_empty_when_no_timestamp(self) -> None:
        entry = {"message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}}
        assert _extract_tools_from_entry(entry, None) == []

    def test_returns_empty_when_message_missing(self) -> None:
        assert _extract_tools_from_entry({}, datetime.now(tz=UTC)) == []

    def test_returns_empty_when_content_not_list(self) -> None:
        assert _extract_tools_from_entry({"message": {"content": "x"}}, datetime.now(tz=UTC)) == []

    def test_extracts_tool_use_blocks(self) -> None:
        at = datetime(2026, 4, 18, 3, 0, 0, tzinfo=UTC)
        entry = {
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/a"}},
                    {"type": "text", "text": "thinking"},
                ]
            }
        }
        tools = _extract_tools_from_entry(entry, at)
        assert len(tools) == 2
        assert tools[0].tool == "Bash"
        assert tools[1].tool == "Read"

    def test_skips_malformed_tool_use_without_name(self) -> None:
        at = datetime.now(tz=UTC)
        entry = {"message": {"content": [{"type": "tool_use"}]}}
        assert _extract_tools_from_entry(entry, at) == []

    def test_skips_non_dict_blocks(self) -> None:
        at = datetime.now(tz=UTC)
        entry = {"message": {"content": ["not a dict"]}}
        assert _extract_tools_from_entry(entry, at) == []

    def test_message_not_dict(self) -> None:
        assert _extract_tools_from_entry({"message": 5}, datetime.now(tz=UTC)) == []


class TestCoerceTranscriptTimestamp:
    def test_top_level_timestamp_wins(self) -> None:
        dt = _coerce_transcript_timestamp({"timestamp": "2026-04-18T03:00:00Z", "message": {}})
        assert dt == datetime(2026, 4, 18, 3, 0, 0, tzinfo=UTC)

    def test_nested_message_timestamp_fallback(self) -> None:
        dt = _coerce_transcript_timestamp({"message": {"timestamp": "2026-04-18T04:00:00Z"}})
        assert dt == datetime(2026, 4, 18, 4, 0, 0, tzinfo=UTC)

    def test_returns_none_when_neither_present(self) -> None:
        assert _coerce_transcript_timestamp({}) is None
        assert _coerce_transcript_timestamp({"message": {}}) is None

    def test_ignores_non_string_timestamps(self) -> None:
        assert _coerce_transcript_timestamp({"timestamp": 12345}) is None
        assert _coerce_transcript_timestamp({"message": {"timestamp": 9}}) is None

    def test_top_level_bad_then_nested_good(self) -> None:
        dt = _coerce_transcript_timestamp({"timestamp": "bad", "message": {"timestamp": "2026-04-18T05:00:00Z"}})
        assert dt == datetime(2026, 4, 18, 5, 0, 0, tzinfo=UTC)


class TestDiscoverSessionDir:
    def test_returns_none_when_log_missing(self, tmp_path: Path) -> None:
        assert discover_session_dir(tmp_path / "missing.jsonl") is None

    def test_returns_none_when_log_empty(self, tmp_path: Path) -> None:
        log = tmp_path / "hook-logs.jsonl"
        log.write_text("")
        assert discover_session_dir(log) is None

    def test_returns_none_when_no_transcript_path(self, tmp_path: Path) -> None:
        log = tmp_path / "hook-logs.jsonl"
        _write_jsonl(log, [{"event": "PreToolUse", "input": {}}])
        assert discover_session_dir(log) is None

    def test_returns_parent_of_transcript_path(self, tmp_path: Path) -> None:
        log = tmp_path / "hook-logs.jsonl"
        _write_jsonl(
            log,
            [
                {"event": "PreToolUse", "input": {"transcript_path": "/claude/projects/slug/session-1.jsonl"}},
            ],
        )
        assert discover_session_dir(log) == Path("/claude/projects/slug")

    def test_skips_malformed_first_line(self, tmp_path: Path) -> None:
        log = tmp_path / "hook-logs.jsonl"
        log.write_text("not json\n" + json.dumps({"input": {"transcript_path": "/slug/s.jsonl"}}) + "\n")
        assert discover_session_dir(log) == Path("/slug")

    def test_skips_non_dict_entries(self, tmp_path: Path) -> None:
        log = tmp_path / "hook-logs.jsonl"
        log.write_text(json.dumps([1, 2, 3]) + "\n" + json.dumps({"input": {"transcript_path": "/p/s.jsonl"}}) + "\n")
        assert discover_session_dir(log) == Path("/p")

    def test_skips_non_dict_input_block(self, tmp_path: Path) -> None:
        log = tmp_path / "hook-logs.jsonl"
        _write_jsonl(
            log,
            [
                {"input": "not a dict"},
                {"input": {"transcript_path": "/a/b.jsonl"}},
            ],
        )
        assert discover_session_dir(log) == Path("/a")

    def test_skips_blank_lines(self, tmp_path: Path) -> None:
        log = tmp_path / "hook-logs.jsonl"
        log.write_text("\n\n" + json.dumps({"input": {"transcript_path": "/c/d.jsonl"}}) + "\n")
        assert discover_session_dir(log) == Path("/c")


class TestFindActiveSubagent:
    def test_returns_none_when_dir_missing(self, tmp_path: Path) -> None:
        assert find_active_subagent(tmp_path) is None

    def test_returns_single_file(self, tmp_path: Path) -> None:
        sub = tmp_path / "subagents"
        sub.mkdir()
        f = sub / "agent-abc.jsonl"
        f.write_text("{}\n")
        assert find_active_subagent(tmp_path) == f

    def test_ignores_non_matching_names(self, tmp_path: Path) -> None:
        sub = tmp_path / "subagents"
        sub.mkdir()
        (sub / "agent-abc.jsonl").write_text("{}\n")
        (sub / "other.jsonl").write_text("{}\n")
        (sub / "agent-skip.log").write_text("{}\n")
        result = find_active_subagent(tmp_path)
        assert result is not None
        assert result.name == "agent-abc.jsonl"

    def test_ignores_subdirectories(self, tmp_path: Path) -> None:
        sub = tmp_path / "subagents"
        sub.mkdir()
        (sub / "agent-subdir.jsonl").mkdir()
        (sub / "agent-valid.jsonl").write_text("{}\n")
        assert find_active_subagent(tmp_path) == (sub / "agent-valid.jsonl")

    def test_empty_dir_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "subagents").mkdir()
        assert find_active_subagent(tmp_path) is None

    def test_picks_newest_mtime(self, tmp_path: Path) -> None:
        sub = tmp_path / "subagents"
        sub.mkdir()
        f_old = sub / "agent-old.jsonl"
        f_old.write_text("{}\n")
        f_new = sub / "agent-new.jsonl"
        f_new.write_text("{}\n")
        import os

        os.utime(f_old, (1000.0, 1000.0))
        os.utime(f_new, (2000.0, 2000.0))
        assert find_active_subagent(tmp_path) == f_new

    def test_handles_stat_error(self, tmp_path: Path) -> None:
        sub = tmp_path / "subagents"
        sub.mkdir()
        f = sub / "agent-x.jsonl"
        f.write_text("{}\n")

        original_stat = Path.stat

        def _raise(self: Path, *a: Any, **kw: Any) -> Any:
            if self == f:
                raise OSError("permission denied")
            return original_stat(self, *a, **kw)

        with patch.object(Path, "stat", _raise):
            assert find_active_subagent(tmp_path) is None


class TestParseSubagentRecentActivity:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        out = parse_subagent_recent_activity(tmp_path / "missing.jsonl")
        assert out.latest_text is None
        assert out.recent_tools == []
        assert out.subagent_type is None
        assert out.last_activity_at is None

    def test_extracts_latest_text_and_tools(self, tmp_path: Path) -> None:
        f = tmp_path / "agent.jsonl"
        _write_jsonl(
            f,
            [
                {
                    "timestamp": "2026-04-18T03:00:00Z",
                    "message": {"content": [{"type": "text", "text": "thinking"}]},
                    "subagent_type": "devbench-orchestrate:executor",
                },
                {
                    "timestamp": "2026-04-18T03:00:05Z",
                    "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]},
                },
            ],
        )
        out = parse_subagent_recent_activity(f)
        assert out.latest_text == "thinking"
        assert out.subagent_type == "devbench-orchestrate:executor"
        assert len(out.recent_tools) == 1
        assert out.recent_tools[0].tool == "Bash"
        assert out.last_activity_at == datetime(2026, 4, 18, 3, 0, 5, tzinfo=UTC)

    def test_skips_blank_and_malformed_lines(self, tmp_path: Path) -> None:
        f = tmp_path / "agent.jsonl"
        f.write_text(
            "\n"
            "not-json\n"
            + json.dumps(
                {"timestamp": "2026-04-18T03:00:00Z", "message": {"content": [{"type": "text", "text": "ok"}]}}
            )
            + "\n"
        )
        out = parse_subagent_recent_activity(f)
        assert out.latest_text == "ok"

    def test_non_dict_entries_are_skipped(self, tmp_path: Path) -> None:
        f = tmp_path / "agent.jsonl"
        f.write_text("[1,2,3]\n" + json.dumps({"message": {"content": [{"type": "text", "text": "x"}]}}) + "\n")
        out = parse_subagent_recent_activity(f)
        assert out.latest_text == "x"

    def test_trims_to_max_tools(self, tmp_path: Path) -> None:
        f = tmp_path / "agent.jsonl"
        entries = []
        for i in range(10):
            entries.append(
                {
                    "timestamp": f"2026-04-18T03:00:{i:02d}Z",
                    "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": str(i)}}]},
                }
            )
        _write_jsonl(f, entries)
        out = parse_subagent_recent_activity(f, max_tools=3)
        assert len(out.recent_tools) == 3
        assert [e.summary for e in out.recent_tools] == ["7", "8", "9"]

    def test_truncates_very_long_text(self, tmp_path: Path) -> None:
        f = tmp_path / "agent.jsonl"
        long_text = "x" * (MAX_AGENT_TEXT_CHARS + 50)
        _write_jsonl(f, [{"message": {"content": [{"type": "text", "text": long_text}]}}])
        out = parse_subagent_recent_activity(f)
        assert out.latest_text is not None
        assert len(out.latest_text) == MAX_AGENT_TEXT_CHARS
        assert out.latest_text.endswith("...")

    def test_last_activity_at_tracks_max_timestamp(self, tmp_path: Path) -> None:
        f = tmp_path / "agent.jsonl"
        _write_jsonl(
            f,
            [
                {"timestamp": "2026-04-18T03:10:00Z", "message": {"content": "early"}},
                {"timestamp": "2026-04-18T03:05:00Z", "message": {"content": "earlier-in-file"}},
            ],
        )
        out = parse_subagent_recent_activity(f)
        assert out.last_activity_at == datetime(2026, 4, 18, 3, 10, 0, tzinfo=UTC)

    def test_reads_agent_type_alias(self, tmp_path: Path) -> None:
        f = tmp_path / "agent.jsonl"
        _write_jsonl(f, [{"agent_type": "devbench-orchestrate:executor"}])
        out = parse_subagent_recent_activity(f)
        assert out.subagent_type == "devbench-orchestrate:executor"

    def test_reads_agenttype_alias(self, tmp_path: Path) -> None:
        f = tmp_path / "agent.jsonl"
        _write_jsonl(f, [{"agentType": "devbench-orchestrate:review-supervisor"}])
        out = parse_subagent_recent_activity(f)
        assert out.subagent_type == "devbench-orchestrate:review-supervisor"


class TestParseOrchestratorRecentCli:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert parse_orchestrator_recent_cli(tmp_path / "missing.log") == []

    def test_keeps_only_devbench_cli_entries(self, tmp_path: Path) -> None:
        log = tmp_path / "orchestrator.log"
        log.write_text(
            "2026-04-18T03:00:00Z [devbench.cli] INFO Claimed E0-F9-S2-T4 (set to in-progress)\n"
            "2026-04-18T03:00:01Z [devbench.other] INFO unrelated line\n"
            "2026-04-18T03:00:02Z [devbench.cli] INFO TDD RED logged for E0-F9-S2-T4\n"
        )
        events = parse_orchestrator_recent_cli(log)
        assert len(events) == 2
        assert events[0].message.startswith("Claimed")
        assert events[1].message.startswith("TDD RED")

    def test_respects_max_entries(self, tmp_path: Path) -> None:
        log = tmp_path / "orchestrator.log"
        lines = "\n".join(f"2026-04-18T03:00:{i:02d}Z [devbench.cli] INFO line {i}" for i in range(5))
        log.write_text(lines + "\n")
        out = parse_orchestrator_recent_cli(log, max_entries=2)
        assert len(out) == 2
        assert out[0].message == "line 3"
        assert out[1].message == "line 4"

    def test_max_entries_zero_returns_empty(self, tmp_path: Path) -> None:
        log = tmp_path / "orchestrator.log"
        log.write_text("2026-04-18T03:00:00Z [devbench.cli] INFO x\n")
        assert parse_orchestrator_recent_cli(log, max_entries=0) == []

    def test_uses_default_max_entries(self, tmp_path: Path) -> None:
        log = tmp_path / "orchestrator.log"
        lines = "\n".join(
            f"2026-04-18T03:00:{i:02d}Z [devbench.cli] INFO line {i}" for i in range(DEFAULT_MAX_CLI_EVENTS + 2)
        )
        log.write_text(lines + "\n")
        out = parse_orchestrator_recent_cli(log)
        assert len(out) == DEFAULT_MAX_CLI_EVENTS


class TestParseLogLine:
    def test_none_on_empty(self) -> None:
        assert _parse_log_line("") is None

    def test_none_when_brackets_missing(self) -> None:
        assert _parse_log_line("no brackets here") is None

    def test_none_when_bad_timestamp(self) -> None:
        assert _parse_log_line("garbage [devbench.cli] INFO stuff") is None

    def test_non_devbench_cli_filtered(self) -> None:
        assert _parse_log_line("2026-04-18T03:00:00Z [devbench.other] INFO x") is None

    def test_strips_every_level_prefix(self) -> None:
        for level in ("INFO", "WARNING", "ERROR", "DEBUG", "CRITICAL"):
            event = _parse_log_line(f"2026-04-18T03:00:00Z [devbench.cli] {level} message text")
            assert event is not None
            assert event.message == "message text"

    def test_no_level_prefix_keeps_remainder(self) -> None:
        event = _parse_log_line("2026-04-18T03:00:00Z [devbench.cli] bare message")
        assert event is not None
        assert event.message == "bare message"

    def test_line_without_space_after_ts(self) -> None:
        assert _parse_log_line("2026-04-18T03:00:00Z") is None

    def test_malformed_bracket_no_close(self) -> None:
        assert _parse_log_line("2026-04-18T03:00:00Z [incomplete") is None

    def test_remainder_not_starting_with_bracket(self) -> None:
        assert _parse_log_line("2026-04-18T03:00:00Z plain[x]text") is None

    def test_line_with_no_space_skipped(self) -> None:
        assert _parse_log_line("single-word-line") is None


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("# hi\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


class TestParseRepoState:
    def test_missing_dir_returns_error(self, tmp_path: Path) -> None:
        rs = parse_repo_state(tmp_path / "does-not-exist")
        assert rs.error is not None

    def test_non_git_dir_returns_error(self, tmp_path: Path) -> None:
        rs = parse_repo_state(tmp_path)
        assert rs.error is not None and "git status" in rs.error

    def test_clean_repo_empty_lists(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path)
        rs = parse_repo_state(tmp_path)
        assert rs.error is None
        assert rs.staged == []
        assert rs.unstaged == []
        assert rs.untracked == []
        assert rs.head_sha is not None
        assert len(rs.head_sha) == 40

    def test_categorises_status(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path)
        (tmp_path / "README.md").write_text("# modified\n")
        (tmp_path / "new.txt").write_text("new\n")
        staged = tmp_path / "added.py"
        staged.write_text("print('x')\n")
        subprocess.run(["git", "add", "added.py"], cwd=tmp_path, check=True, capture_output=True)

        rs = parse_repo_state(tmp_path)
        assert rs.error is None
        assert "README.md" in rs.unstaged
        assert "new.txt" in rs.untracked
        assert "added.py" in rs.staged

    def test_head_sha_error_yields_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _init_git_repo(tmp_path)

        class _FakeResult:
            def __init__(self, rc: int, stdout: str) -> None:
                self.returncode = rc
                self.stdout = stdout

        original_run = subprocess.run

        def fake_run(cmd: list[str], **kwargs: Any) -> Any:
            if len(cmd) >= 4 and cmd[3] == "rev-parse":
                return _FakeResult(128, "")
            return original_run(cmd, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        rs = parse_repo_state(tmp_path)
        assert rs.head_sha is None

    def test_skips_short_status_lines(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _init_git_repo(tmp_path)

        class _FakeResult:
            def __init__(self, stdout: str) -> None:
                self.returncode = 0
                self.stdout = stdout

        def fake_run(cmd: list[str], **_kw: Any) -> Any:
            if cmd[3] == "status":
                return _FakeResult("M\n?? new.txt\n")
            return _FakeResult("deadbeef\n")

        monkeypatch.setattr(subprocess, "run", fake_run)
        rs = parse_repo_state(tmp_path)
        assert rs.error is None
        assert rs.untracked == ["new.txt"]
        assert rs.staged == []


class TestRunGit:
    def test_timeout_returns_error_rc(self, tmp_path: Path) -> None:
        _init_git_repo(tmp_path)
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=1)):
            rc, out = _run_git(tmp_path, ["status"], timeout=1)
        assert rc == 1
        assert out == ""

    def test_file_not_found_returns_error(self, tmp_path: Path) -> None:
        with patch("subprocess.run", side_effect=FileNotFoundError("git missing")):
            rc, out = _run_git(tmp_path, ["status"], timeout=30)
        assert rc == 1
        assert out == ""


class TestCheckAmendmentRequest:
    def test_no_file_returns_exists_false(self, tmp_path: Path) -> None:
        out = check_amendment_request(tmp_path, "EX-T1")
        assert out.exists is False
        assert out.files_to_add == []

    def test_file_parsed_correctly(self, tmp_path: Path) -> None:
        payload = {
            "task_id": "EX-T1",
            "reason": "tdd_green_production_fix",
            "files_to_add": [{"path": "src/a.py", "change": "fix"}],
        }
        target = tmp_path / ".devbench" / "amendments" / "EX-T1.json"
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps(payload))
        out = check_amendment_request(tmp_path, "EX-T1")
        assert out.exists is True
        assert out.reason == "tdd_green_production_fix"
        assert out.files_to_add == ["src/a.py"]

    def test_malformed_json_returns_exists_true_with_none(self, tmp_path: Path) -> None:
        target = tmp_path / ".devbench" / "amendments" / "EX-T1.json"
        target.parent.mkdir(parents=True)
        target.write_text("not json")
        out = check_amendment_request(tmp_path, "EX-T1")
        assert out.exists is True
        assert out.reason is None
        assert out.files_to_add == []

    def test_top_level_not_dict(self, tmp_path: Path) -> None:
        target = tmp_path / ".devbench" / "amendments" / "EX-T1.json"
        target.parent.mkdir(parents=True)
        target.write_text("[1,2,3]")
        out = check_amendment_request(tmp_path, "EX-T1")
        assert out.exists is True
        assert out.reason is None
        assert out.files_to_add == []

    def test_files_entries_non_dict_filtered(self, tmp_path: Path) -> None:
        target = tmp_path / ".devbench" / "amendments" / "EX-T1.json"
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps({"files_to_add": ["string", {"path": "ok.py", "change": "c"}, {"no_path": True}]}))
        out = check_amendment_request(tmp_path, "EX-T1")
        assert out.files_to_add == ["ok.py"]


class TestDetectPhase:
    def test_executor_subagent_wins(self) -> None:
        assert (
            detect_phase(subagent_type="devbench-orchestrate:executor", recent_cli=[], idle_seconds=0)
            == "executor subagent active"
        )

    def test_review_supervisor_subagent(self) -> None:
        assert (
            detect_phase(subagent_type="devbench-orchestrate:review-supervisor", recent_cli=[], idle_seconds=0)
            == "review-supervisor running"
        )

    def test_security_subagent(self) -> None:
        assert (
            detect_phase(subagent_type="security-reviewer", recent_cli=[], idle_seconds=0)
            == "security-reviewer running"
        )

    def test_git_ops_subagent(self) -> None:
        assert detect_phase(subagent_type="git-ops", recent_cli=[], idle_seconds=0) == "git-ops running"

    def test_blocker_subagent(self) -> None:
        assert (
            detect_phase(subagent_type="blocker-resolver", recent_cli=[], idle_seconds=0) == "blocker-resolver running"
        )

    def test_amender_subagent(self) -> None:
        assert (
            detect_phase(subagent_type="manifest-amender", recent_cli=[], idle_seconds=0) == "manifest-amender running"
        )

    def test_task_factory_subagent(self) -> None:
        assert detect_phase(subagent_type="task-factory", recent_cli=[], idle_seconds=0) == "task-factory running"

    def test_unknown_subagent_falls_to_cli_hint(self) -> None:
        events = [CliEvent(at=datetime.now(tz=UTC), message="AMENDMENT applied for EX-T1")]
        assert (
            detect_phase(subagent_type="something-weird", recent_cli=events, idle_seconds=0)
            == "manifest-amender running"
        )

    def test_idle_when_stale_and_no_cli(self) -> None:
        assert detect_phase(subagent_type=None, recent_cli=[], idle_seconds=IDLE_THRESHOLD_SECONDS + 5) == "idle"

    def test_fallback_idle_when_no_hints(self) -> None:
        events = [CliEvent(at=datetime.now(tz=UTC), message="something unrelated")]
        assert detect_phase(subagent_type=None, recent_cli=events, idle_seconds=0) == "idle"

    def test_cli_hint_security(self) -> None:
        events = [CliEvent(at=datetime.now(tz=UTC), message="security review pending")]
        assert detect_phase(subagent_type=None, recent_cli=events, idle_seconds=0) == "security-reviewer running"

    def test_cli_hint_git_ops_dash(self) -> None:
        events = [CliEvent(at=datetime.now(tz=UTC), message="git-ops finalize")]
        assert detect_phase(subagent_type=None, recent_cli=events, idle_seconds=0) == "git-ops running"

    def test_cli_hint_blocker(self) -> None:
        events = [CliEvent(at=datetime.now(tz=UTC), message="blocker-resolver ran")]
        assert detect_phase(subagent_type=None, recent_cli=events, idle_seconds=0) == "blocker-resolver running"

    def test_cli_hint_review(self) -> None:
        events = [CliEvent(at=datetime.now(tz=UTC), message="review-supervisor ran")]
        assert detect_phase(subagent_type=None, recent_cli=events, idle_seconds=0) == "review-supervisor running"


class TestModeLabel:
    def test_standard_multi_pr(self) -> None:
        assert mode_label(_make_runtime_config()) == "standard multi-PR"

    def test_defer_pr_single_branch(self) -> None:
        cfg = _make_runtime_config(defer_pr=True, single_branch="feat/x")
        assert mode_label(cfg) == "single-branch + defer_pr (branch: feat/x)"

    def test_defer_pr_without_single_branch_flags_invalid(self) -> None:
        cfg = RuntimeConfig(git_ops=GitOpsConfig(defer_pr=True))
        assert "invalid config" in mode_label(cfg)

    def test_pause_before_merge_defensive(self) -> None:
        cfg = _make_runtime_config()
        object.__setattr__(cfg.git_ops, "pause_before_merge", True)
        assert mode_label(cfg) == "multi-PR with pause-before-merge"


class TestComputeIdleSeconds:
    def test_none_returns_zero(self) -> None:
        now = datetime.now(tz=UTC)
        assert _compute_idle_seconds(now, None) == 0

    def test_positive_delta_floored(self) -> None:
        now = datetime(2026, 4, 18, 3, 0, 30, tzinfo=UTC)
        last = datetime(2026, 4, 18, 3, 0, 0, tzinfo=UTC)
        assert _compute_idle_seconds(now, last) == 30

    def test_negative_delta_clamped_to_zero(self) -> None:
        now = datetime(2026, 4, 18, 3, 0, 0, tzinfo=UTC)
        last = datetime(2026, 4, 18, 3, 0, 30, tzinfo=UTC)
        assert _compute_idle_seconds(now, last) == 0


class TestFindMostRecentClaim:
    def test_missing_log_returns_none(self, tmp_path: Path) -> None:
        assert _find_most_recent_claim(tmp_path / "missing.log", "E0-F9-S2-T4") is None

    def test_picks_most_recent(self, tmp_path: Path) -> None:
        log = tmp_path / "o.log"
        log.write_text(
            "2026-04-18T03:00:00Z [devbench.cli] INFO Claimed E0-F9-S2-T4 (set to in-progress)\n"
            "2026-04-18T03:05:00Z [devbench.cli] INFO Claimed E0-F9-S2-T4 (set to in-progress)\n"
            "2026-04-18T03:05:00Z [devbench.cli] INFO Claimed OTHER\n"
        )
        dt = _find_most_recent_claim(log, "E0-F9-S2-T4")
        assert dt == datetime(2026, 4, 18, 3, 5, 0, tzinfo=UTC)

    def test_none_when_no_claim(self, tmp_path: Path) -> None:
        log = tmp_path / "o.log"
        log.write_text("2026-04-18T03:00:00Z [devbench.cli] INFO unrelated\n")
        assert _find_most_recent_claim(log, "E0-F9-S2-T4") is None

    def test_ignores_unparseable_claim_line(self, tmp_path: Path) -> None:
        log = tmp_path / "o.log"
        log.write_text("garbage Claimed E0-F9-S2-T4 whatever\n")
        assert _find_most_recent_claim(log, "E0-F9-S2-T4") is None


_BACKLOG_INDEX_TEMPLATE = """\
# Backlog

## Full Work Unit Index

| ID | Title | Type | Status | Dependencies | Repo | File Path |
|----|-------|------|--------|--------------|------|-----------|
| EX-F1-S1-T1 | Progress Task | Task | in-progress | None | caylent-solutions/example | `backlog/EX-F1-S1-T1.md` |
| EX-F1-S1-T2 | Queued Task | Task | in-queue | None | caylent-solutions/example | `backlog/EX-F1-S1-T2.md` |
"""

_TASK_TEMPLATE = """\
# {task_id}: {title}

## Status: {status}

## Description

desc

## Target Repository

- **Repo:** `caylent-solutions/example`
- **Branch:** `backlog/{task_id_lower}`

## Dependencies

| ID | Title | Status |
|----|-------|--------|
| none | | |

## Acceptance Criteria

- [ ] AC-TEST-001 asserts a thing

## Changes Manifest

| File | Change |
|------|--------|
| `tests/test_example.py` | add tests |

## Definition of Done

- [ ] AC complete
"""


def _write_minimal_backlog(tmp_path: Path, in_progress_status: str = "in-progress") -> Path:
    (tmp_path / "BACKLOG.md").write_text(_BACKLOG_INDEX_TEMPLATE.replace("in-progress", in_progress_status))
    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir()
    (backlog_dir / "EX-F1-S1-T1.md").write_text(
        _TASK_TEMPLATE.format(
            task_id="EX-F1-S1-T1", task_id_lower="ex-f1-s1-t1", title="Progress Task", status=in_progress_status
        ),
        encoding="utf-8",
    )
    (backlog_dir / "EX-F1-S1-T2.md").write_text(
        _TASK_TEMPLATE.format(
            task_id="EX-F1-S1-T2", task_id_lower="ex-f1-s1-t2", title="Queued Task", status="in-queue"
        ),
        encoding="utf-8",
    )
    return tmp_path / "BACKLOG.md"


class TestFindActiveTask:
    def test_missing_backlog_returns_none(self, tmp_path: Path) -> None:
        assert _find_active_task(tmp_path / "missing.md") is None

    def test_in_progress_wins(self, tmp_path: Path) -> None:
        index = _write_minimal_backlog(tmp_path)
        out = _find_active_task(index)
        assert out is not None
        assert out.unit_id == "EX-F1-S1-T1"
        assert out.status_value == "In Progress"

    def test_falls_back_to_in_review_or_blocked(self, tmp_path: Path) -> None:
        index = _write_minimal_backlog(tmp_path, in_progress_status="blocked")
        out = _find_active_task(index)
        assert out is not None
        assert out.unit_id == "EX-F1-S1-T1"
        assert out.status_value == "Blocked"

    def test_returns_none_when_only_queue(self, tmp_path: Path) -> None:
        (tmp_path / "BACKLOG.md").write_text(_BACKLOG_INDEX_TEMPLATE.replace("in-progress", "in-queue"))
        backlog_dir = tmp_path / "backlog"
        backlog_dir.mkdir()
        (backlog_dir / "EX-F1-S1-T1.md").write_text(
            _TASK_TEMPLATE.format(
                task_id="EX-F1-S1-T1", task_id_lower="ex-f1-s1-t1", title="Progress Task", status="in-queue"
            ),
            encoding="utf-8",
        )
        (backlog_dir / "EX-F1-S1-T2.md").write_text(
            _TASK_TEMPLATE.format(
                task_id="EX-F1-S1-T2", task_id_lower="ex-f1-s1-t2", title="Queued Task", status="in-queue"
            ),
            encoding="utf-8",
        )
        assert _find_active_task(tmp_path / "BACKLOG.md") is None

    def test_returns_none_on_parse_error(self, tmp_path: Path) -> None:
        idx = tmp_path / "BACKLOG.md"
        idx.write_text("# header only\n")
        assert _find_active_task(idx) is None


class TestCollectSnapshot:
    def test_minimal_empty_workspace(self, tmp_path: Path) -> None:
        now = datetime(2026, 4, 18, 3, 0, 0, tzinfo=UTC)
        snapshot = collect_snapshot(
            workspace_root=tmp_path,
            backlog_index=tmp_path / "BACKLOG.md",
            runtime_config=_make_runtime_config(),
            orchestrator_log=tmp_path / "missing.log",
            hook_log=tmp_path / "missing.jsonl",
            repo_path_resolver=lambda _: None,
            now=now,
        )
        assert snapshot.active_task_id is None
        assert snapshot.phase == "idle"
        assert snapshot.subagent is None
        assert snapshot.recent_cli == []
        assert snapshot.repo_state is None
        assert snapshot.amendment is None
        assert snapshot.idle_seconds == 0
        assert snapshot.mode_label == "standard multi-PR"

    def test_full_synthetic_run(self, tmp_path: Path) -> None:
        now = datetime(2026, 4, 18, 3, 5, 30, tzinfo=UTC)

        index = _write_minimal_backlog(tmp_path)

        log = tmp_path / "orchestrator.log"
        log.write_text(
            "2026-04-18T03:00:00Z [devbench.cli] INFO Claimed EX-F1-S1-T1 (set to in-progress)\n"
            "2026-04-18T03:05:00Z [devbench.cli] INFO TDD RED logged for EX-F1-S1-T1\n"
        )

        session_dir = tmp_path / "session"
        session_dir.mkdir()
        main_session = session_dir / "main-session.jsonl"
        main_session.write_text("")
        subagents_dir = session_dir / "subagents"
        subagents_dir.mkdir()
        transcript = subagents_dir / "agent-abc.jsonl"
        _write_jsonl(
            transcript,
            [
                {
                    "timestamp": "2026-04-18T03:05:25Z",
                    "subagent_type": "devbench-orchestrate:executor",
                    "message": {
                        "content": [
                            {"type": "text", "text": "thinking about it"},
                            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                        ]
                    },
                }
            ],
        )
        hook_log = tmp_path / "hook-logs.jsonl"
        _write_jsonl(hook_log, [{"input": {"transcript_path": str(main_session)}}])

        amendment_path = tmp_path / ".devbench" / "amendments" / "EX-F1-S1-T1.json"
        amendment_path.parent.mkdir(parents=True)
        amendment_path.write_text(
            json.dumps(
                {
                    "reason": "tdd_green_production_fix",
                    "files_to_add": [{"path": "src/a.py", "change": "c"}],
                }
            )
        )

        repo_dir = tmp_path / "example"
        repo_dir.mkdir()
        _init_git_repo(repo_dir)
        (repo_dir / "new.txt").write_text("new\n")

        snapshot = collect_snapshot(
            workspace_root=tmp_path,
            backlog_index=index,
            runtime_config=_make_runtime_config(defer_pr=True, single_branch="feat/x"),
            orchestrator_log=log,
            hook_log=hook_log,
            repo_path_resolver=lambda _: repo_dir,
            now=now,
        )
        assert snapshot.active_task_id == "EX-F1-S1-T1"
        assert snapshot.mode_label == "single-branch + defer_pr (branch: feat/x)"
        assert snapshot.claimed_at is not None
        assert snapshot.subagent is not None
        assert snapshot.subagent.subagent_type == "devbench-orchestrate:executor"
        assert snapshot.subagent.latest_text == "thinking about it"
        assert snapshot.phase == "executor subagent active"
        assert snapshot.repo_state is not None
        assert "new.txt" in snapshot.repo_state.untracked
        assert snapshot.amendment is not None and snapshot.amendment.exists
        assert snapshot.last_tool_call_at is not None
        assert snapshot.idle_seconds >= 0

    def test_collect_uses_now_default(self, tmp_path: Path) -> None:
        snap = collect_snapshot(
            workspace_root=tmp_path,
            backlog_index=tmp_path / "BACKLOG.md",
            runtime_config=_make_runtime_config(),
            orchestrator_log=tmp_path / "missing.log",
            hook_log=tmp_path / "missing.jsonl",
            repo_path_resolver=lambda _: None,
        )
        assert snap.now.tzinfo is UTC

    def test_cli_event_fresher_than_subagent_updates_last_activity(self, tmp_path: Path) -> None:
        """When a devbench.cli log line is newer than the subagent's last activity,
        ``last_tool_call_at`` promotes to the CLI timestamp."""
        index = _write_minimal_backlog(tmp_path)

        session_dir = tmp_path / "session"
        session_dir.mkdir()
        main_session = session_dir / "main-session.jsonl"
        main_session.write_text("")
        subagents_dir = session_dir / "subagents"
        subagents_dir.mkdir()
        transcript = subagents_dir / "agent-abc.jsonl"
        _write_jsonl(
            transcript,
            [
                {
                    "timestamp": "2026-04-18T03:00:00Z",
                    "message": {"content": [{"type": "text", "text": "x"}]},
                }
            ],
        )
        hook_log = tmp_path / "hook-logs.jsonl"
        _write_jsonl(hook_log, [{"input": {"transcript_path": str(main_session)}}])

        orch_log = tmp_path / "orchestrator.log"
        orch_log.write_text("2026-04-18T03:05:00Z [devbench.cli] INFO Claimed EX-F1-S1-T1 (set to in-progress)\n")

        snapshot = collect_snapshot(
            workspace_root=tmp_path,
            backlog_index=index,
            runtime_config=_make_runtime_config(),
            orchestrator_log=orch_log,
            hook_log=hook_log,
            repo_path_resolver=lambda _: None,
            now=datetime(2026, 4, 18, 3, 6, 0, tzinfo=UTC),
        )
        assert snapshot.last_tool_call_at == datetime(2026, 4, 18, 3, 5, 0, tzinfo=UTC)


def _sample_snapshot(
    *,
    active_task_id: str | None = "EX-F1-S1-T1",
    phase: str = "executor subagent active",
    subagent: SubagentActivity | None = None,
    repo_state: RepoState | None = None,
    amendment: AmendmentState | None = None,
    recent_cli: list[CliEvent] | None = None,
    last_tool_call_at: datetime | None = None,
    claimed_at: datetime | None = None,
    idle_seconds: int = 5,
) -> ActivitySnapshot:
    now = datetime(2026, 4, 18, 3, 5, 30, tzinfo=UTC)
    return ActivitySnapshot(
        now=now,
        mode_label="single-branch + defer_pr (branch: feat/x)",
        active_task_id=active_task_id,
        active_task_title="Sample Task" if active_task_id else None,
        active_task_status="In Progress" if active_task_id else None,
        claimed_at=claimed_at,
        phase=phase,
        last_tool_call_at=last_tool_call_at,
        subagent=subagent,
        recent_cli=recent_cli or [],
        repo_state=repo_state,
        amendment=amendment,
        idle_seconds=idle_seconds,
    )


class TestRenderSnapshot:
    def test_minimal_no_task_idle(self) -> None:
        snap = _sample_snapshot(active_task_id=None, phase="idle", idle_seconds=0)
        out = render_snapshot(snap)
        assert "DevBench activity" in out
        assert "Active task: (none)" in out
        assert "Phase: idle" in out
        assert "Idle for 0s" in out

    def test_includes_active_task_with_claim_age(self) -> None:
        snap = _sample_snapshot(
            claimed_at=datetime(2026, 4, 18, 3, 0, 30, tzinfo=UTC),
            last_tool_call_at=datetime(2026, 4, 18, 3, 5, 0, tzinfo=UTC),
            idle_seconds=30,
        )
        out = render_snapshot(snap)
        assert "claimed 5m ago -- In Progress" in out

    def test_includes_status_without_claim(self) -> None:
        snap = _sample_snapshot(claimed_at=None, last_tool_call_at=None, idle_seconds=0)
        out = render_snapshot(snap)
        assert "status: In Progress" in out

    def test_agent_thinking_panel(self) -> None:
        sub = SubagentActivity(
            transcript_path=None,
            subagent_type="devbench-orchestrate:executor",
            latest_text="first\nsecond",
            recent_tools=[],
        )
        snap = _sample_snapshot(subagent=sub)
        out = render_snapshot(snap)
        assert "Latest agent thinking" in out
        assert "  first" in out
        assert "  second" in out

    def test_recent_tools_panel(self) -> None:
        sub = SubagentActivity(
            transcript_path=None,
            subagent_type="devbench-orchestrate:executor",
            latest_text=None,
            recent_tools=[
                ToolCallEvent(at=datetime(2026, 4, 18, 3, 5, 0, tzinfo=UTC), tool="Bash", summary="ls"),
            ],
        )
        snap = _sample_snapshot(subagent=sub)
        out = render_snapshot(snap)
        assert "Recent tool calls (most recent 1)" in out
        assert "03:05:00  Bash   ls" in out

    def test_recent_cli_panel(self) -> None:
        events = [CliEvent(at=datetime(2026, 4, 18, 3, 4, 0, tzinfo=UTC), message="Claimed EX-T1")]
        snap = _sample_snapshot(recent_cli=events)
        out = render_snapshot(snap)
        assert "Recent devbench CLI calls (last 1)" in out
        assert "03:04:00  Claimed EX-T1" in out

    def test_repo_state_panel(self) -> None:
        rs = RepoState(
            repo_path=Path("/tmp/example"),
            staged=["src/a.py"],
            unstaged=["README.md"],
            untracked=["new.txt"],
            head_sha="abc123",
        )
        snap = _sample_snapshot(repo_state=rs)
        out = render_snapshot(snap)
        assert "Target repo state (example)" in out
        assert "src/a.py" in out
        assert "README.md" in out
        assert "new.txt" in out

    def test_repo_state_error_surfaces(self) -> None:
        rs = RepoState(repo_path=Path("/tmp/example"), error="not a git dir")
        snap = _sample_snapshot(repo_state=rs)
        out = render_snapshot(snap)
        assert "error: not a git dir" in out

    def test_repo_state_missing_path_header(self) -> None:
        rs = RepoState(repo_path=None)
        snap = _sample_snapshot(repo_state=rs)
        out = render_snapshot(snap)
        assert "Target repo state:" in out

    def test_amendment_yes(self) -> None:
        amd = AmendmentState(
            task_id="EX-T1",
            exists=True,
            reason="tdd_green_production_fix",
            files_to_add=["src/a.py", "src/b.py"],
        )
        snap = _sample_snapshot(amendment=amd)
        out = render_snapshot(snap)
        assert "Pending amendment request: yes" in out
        assert "2 file(s); reason=tdd_green_production_fix" in out

    def test_amendment_no(self) -> None:
        amd = AmendmentState(task_id="EX-T1", exists=False)
        snap = _sample_snapshot(amendment=amd)
        out = render_snapshot(snap)
        assert "Pending amendment request: no" in out

    def test_last_activity_minutes(self) -> None:
        snap = _sample_snapshot(
            last_tool_call_at=datetime(2026, 4, 18, 3, 0, 30, tzinfo=UTC),
            idle_seconds=120,
        )
        out = render_snapshot(snap)
        assert "last activity 2m ago" in out

    def test_last_activity_none(self) -> None:
        snap = _sample_snapshot(last_tool_call_at=None)
        out = render_snapshot(snap)
        assert "no activity yet" in out

    def test_last_activity_seconds_under_minute(self) -> None:
        snap = _sample_snapshot(last_tool_call_at=datetime(2026, 4, 18, 3, 5, 20, tzinfo=UTC), idle_seconds=10)
        out = render_snapshot(snap)
        assert "last activity 10s ago" in out

    def test_footer_always_present(self) -> None:
        snap = _sample_snapshot()
        out = render_snapshot(snap)
        assert out.splitlines()[-1].startswith("Idle for ")


class TestActiveTaskDataclass:
    def test_construct(self) -> None:
        t = _ActiveTask(unit_id="EX", title="T", status_value="In Progress", repo="r")
        assert t.unit_id == "EX"

    def test_default_max_tools(self) -> None:
        assert DEFAULT_MAX_TOOLS >= 1

    def test_attribute_access_on_module(self) -> None:
        assert hasattr(activity, "MAX_AGENT_TEXT_CHARS")
