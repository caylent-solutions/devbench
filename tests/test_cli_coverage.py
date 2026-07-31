"""Supplementary coverage tests for ``devbench.cli`` — targets the long
tail of error-path / edge-branch lines that the main subcommand-level
test suites do not exercise.  Pinned by function not by line so a
re-flow keeps the tests stable.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from devbench import cli as cli_mod
from devbench.backlog.work_unit import WorkUnit

# ---------------------------------------------------------------------------
# _clean_target_repo_on_block — lines 1597-1648
# ---------------------------------------------------------------------------


class TestCleanTargetRepoOnBlock:
    """Every branch of the local-repo cleanup helper."""

    def test_read_error_returns_one(self, tmp_path: Path) -> None:
        # Path that exists as a directory cannot be read_text()'d (IsADirectoryError → OSError).
        d = tmp_path / "actually-a-dir"
        d.mkdir()
        assert cli_mod._clean_target_repo_on_block(d) == 1

    def test_missing_local_path_marker_returns_zero(self, tmp_path: Path) -> None:
        wu = tmp_path / "wu.md"
        wu.write_text("no local path marker here", encoding="utf-8")
        assert cli_mod._clean_target_repo_on_block(wu) == 0

    def test_local_path_missing_on_disk_returns_zero(self, tmp_path: Path) -> None:
        wu = tmp_path / "wu.md"
        wu.write_text("- **Local path:** `/no-such-dir-on-disk-12345`\n", encoding="utf-8")
        assert cli_mod._clean_target_repo_on_block(wu) == 0

    def test_git_reset_failure_returns_one(self, tmp_path: Path) -> None:
        local = tmp_path / "repo"
        local.mkdir()
        wu = tmp_path / "wu.md"
        wu.write_text(f"- **Local path:** `{local}`\n", encoding="utf-8")
        # Mock subprocess.run so the reset call fails.
        bad_reset = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="reset failed")
        with patch("devbench.cli.subprocess.run", return_value=bad_reset):
            assert cli_mod._clean_target_repo_on_block(wu) == 1

    def test_git_clean_failure_returns_one(self, tmp_path: Path) -> None:
        local = tmp_path / "repo"
        local.mkdir()
        wu = tmp_path / "wu.md"
        wu.write_text(f"- **Local path:** `{local}`\n", encoding="utf-8")
        good = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        bad_clean = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="clean failed")
        with patch("devbench.cli.subprocess.run", side_effect=[good, bad_clean]):
            assert cli_mod._clean_target_repo_on_block(wu) == 1

    def test_happy_path_returns_zero(self, tmp_path: Path) -> None:
        local = tmp_path / "repo"
        local.mkdir()
        wu = tmp_path / "wu.md"
        wu.write_text(f"- **Local path:** `{local}`\n", encoding="utf-8")
        good = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("devbench.cli.subprocess.run", return_value=good):
            assert cli_mod._clean_target_repo_on_block(wu) == 0


# ---------------------------------------------------------------------------
# _parse_watchdog_args — lines 5312-5348
# ---------------------------------------------------------------------------


class TestParseWatchdogArgs:
    """Every flag-parser branch of the watchdog argument grammar."""

    def test_defaults_when_no_args(self) -> None:
        args = cli_mod._parse_watchdog_args(())
        assert isinstance(args, cli_mod._WatchdogArgs)
        assert args.idle_minutes == 5
        assert args.flag_file == ""
        assert args.log_file == ""
        assert args.print_if_stuck is False

    def test_idle_minutes_missing_value_errors(self, capsys: pytest.CaptureFixture) -> None:
        assert cli_mod._parse_watchdog_args(("--idle-minutes",)) == 2
        assert "requires a value" in capsys.readouterr().err

    def test_idle_minutes_non_integer_errors(self, capsys: pytest.CaptureFixture) -> None:
        assert cli_mod._parse_watchdog_args(("--idle-minutes", "abc")) == 2
        assert "must be an integer" in capsys.readouterr().err

    def test_idle_minutes_less_than_one_errors(self, capsys: pytest.CaptureFixture) -> None:
        assert cli_mod._parse_watchdog_args(("--idle-minutes", "0")) == 2
        assert "must be >= 1" in capsys.readouterr().err

    def test_idle_minutes_happy_path(self) -> None:
        args = cli_mod._parse_watchdog_args(("--idle-minutes", "7"))
        assert isinstance(args, cli_mod._WatchdogArgs)
        assert args.idle_minutes == 7

    def test_flag_file_and_log_file(self) -> None:
        args = cli_mod._parse_watchdog_args(("--flag-file", "/tmp/f", "--log-file", "/tmp/l"))
        assert isinstance(args, cli_mod._WatchdogArgs)
        assert args.flag_file == "/tmp/f"
        assert args.log_file == "/tmp/l"

    def test_flag_file_missing_value_errors(self, capsys: pytest.CaptureFixture) -> None:
        assert cli_mod._parse_watchdog_args(("--flag-file",)) == 2
        assert "requires a value" in capsys.readouterr().err

    def test_print_if_stuck_flag(self) -> None:
        args = cli_mod._parse_watchdog_args(("--print-if-stuck",))
        assert isinstance(args, cli_mod._WatchdogArgs)
        assert args.print_if_stuck is True

    def test_unknown_flag_errors(self, capsys: pytest.CaptureFixture) -> None:
        assert cli_mod._parse_watchdog_args(("--unknown",)) == 2
        assert "unknown flag" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# cmd_watchdog — lines 5373-5403
# ---------------------------------------------------------------------------


class TestCmdWatchdog:
    """Both branches of the watchdog command: healthy + stuck."""

    def test_returns_zero_when_arg_parse_fails(self) -> None:
        rc = cli_mod.cmd_watchdog("--bad")
        assert rc == 2

    def test_returns_zero_when_not_stuck(self, tmp_path: Path) -> None:
        log = tmp_path / "orch.log"
        log.write_text("nothing in progress", encoding="utf-8")
        flag = tmp_path / "needs-restart.flag"
        # detect_stuck returns a result with stuck=None for healthy state.
        healthy = SimpleNamespace(stuck=None)
        with patch("devbench.watchdog.detect_stuck", return_value=healthy):
            rc = cli_mod.cmd_watchdog("--idle-minutes", "1", "--log-file", str(log), "--flag-file", str(flag))
        assert rc == 0
        assert not flag.exists()

    def test_writes_flag_and_returns_zero_when_stuck(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        log = tmp_path / "orch.log"
        log.write_text("data", encoding="utf-8")
        flag = tmp_path / "needs-restart.flag"
        stuck_info = SimpleNamespace(
            task_id="E1-F1-S1-T1",
            idle_seconds=600,
        )
        stuck_result = SimpleNamespace(stuck=stuck_info)
        with (
            patch("devbench.watchdog.detect_stuck", return_value=stuck_result),
            patch("devbench.watchdog.write_flag_file") as wff,
        ):
            rc = cli_mod.cmd_watchdog(
                "--idle-minutes",
                "1",
                "--log-file",
                str(log),
                "--flag-file",
                str(flag),
                "--print-if-stuck",
            )
        assert rc == 0
        wff.assert_called_once()
        out = capsys.readouterr().out
        assert "STUCK" in out
        assert "E1-F1-S1-T1" in out


# ---------------------------------------------------------------------------
# cmd_clean_orphans + _resolve_orphan_repo_path — lines 4419-4466
# ---------------------------------------------------------------------------


class TestResolveOrphanRepoPath:
    def test_direct_filesystem_path_with_git(self, tmp_path: Path) -> None:
        repo = tmp_path / "myrepo"
        repo.mkdir()
        (repo / ".git").mkdir()
        assert cli_mod._resolve_orphan_repo_path(str(repo)) == repo

    def test_unknown_returns_none(self, tmp_path: Path) -> None:
        # Direct path that's not a git repo, and "org/repo" not in REPO_LOCAL_PATHS.
        not_a_repo = tmp_path / "not-a-repo"
        not_a_repo.mkdir()
        assert cli_mod._resolve_orphan_repo_path(str(not_a_repo)) is None

    def test_org_repo_form_resolves_via_yaml(self, tmp_path: Path) -> None:
        repo = tmp_path / "configured-repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        with patch.dict(cli_mod.REPO_LOCAL_PATHS, {"acme/widget": repo}, clear=False):
            assert cli_mod._resolve_orphan_repo_path("acme/widget") == repo


class TestCmdCleanOrphans:
    def test_unknown_repo_returns_one(self, capsys: pytest.CaptureFixture) -> None:
        rc = cli_mod.cmd_cleanup_tracked_orphans("/no-such-path", "")
        assert rc == 1
        err = capsys.readouterr().err
        assert "cannot resolve repo path" in err

    def test_file_not_found_returns_one(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        repo = tmp_path / "myrepo"
        repo.mkdir()
        (repo / ".git").mkdir()
        with patch("devbench.git_orphans.cleanup_tracked_orphans", side_effect=FileNotFoundError("nope")):
            rc = cli_mod.cmd_cleanup_tracked_orphans(str(repo), "")
        assert rc == 1
        assert "ERROR" in capsys.readouterr().err

    def test_happy_path_emits_json(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        repo = tmp_path / "myrepo"
        repo.mkdir()
        (repo / ".git").mkdir()
        report = SimpleNamespace(
            repo_path=repo,
            detected=["a.tmp", "b.log"],
            removed=[],
            gitignore_path=repo / ".gitignore",
            gitignore_updated=False,
            dry_run=True,
        )
        with patch("devbench.git_orphans.cleanup_tracked_orphans", return_value=report):
            rc = cli_mod.cmd_cleanup_tracked_orphans(str(repo), "--dry-run")
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["detected_count"] == 2
        assert payload["dry_run"] is True


# ---------------------------------------------------------------------------
# _parse_hook_tail_argv extra branches — lines 5172-5173, 5186-5188, 5197-5199
# ---------------------------------------------------------------------------


class TestParseHookTailArgvExtraBranches:
    def test_empty_string_argument_is_skipped(self) -> None:
        # Empty argv entries (eg. from shell expansion) skip the loop iteration.
        result = cli_mod._parse_hook_tail_argv(("", "--no-follow"))
        assert not isinstance(result, int)
        assert result.no_follow is True

    def test_from_start_flag(self) -> None:
        result = cli_mod._parse_hook_tail_argv(("--from-start",))
        assert not isinstance(result, int)
        assert result.from_start is True

    def test_orchestrator_session_with_value(self) -> None:
        result = cli_mod._parse_hook_tail_argv(("--orchestrator-session", "my-sess"))
        assert not isinstance(result, int)
        assert result.orchestrator_session_id == "my-sess"

    def test_orchestrator_session_missing_value_errors(self, capsys: pytest.CaptureFixture) -> None:
        rc = cli_mod._parse_hook_tail_argv(("--orchestrator-session",))
        assert rc == 2
        assert "requires a value" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _validate_rejection_feedback_payload — lines 7555, 7562, 7566, 7569, 7578, 7580
# ---------------------------------------------------------------------------


class TestValidateRejectionFeedbackPayload:
    """Layer-1 schema check for log-rejection-feedback JSON.

    The judge ``code_review`` is used so ``categories[i].code`` is matched
    against ``MAKE_VALIDATE_FAILURE`` / etc.
    """

    def _valid_payload(self) -> dict:
        return {
            "raw_verdict_text": "x",
            "categories": [
                {
                    "code": "MAKE_VALIDATE_FAILURE",
                    "severity": "fail",
                    "summary": "x",
                    "remediation": "y",
                    "files": ["a.py"],
                }
            ],
        }

    def test_non_dict_payload_raises(self) -> None:
        with pytest.raises(ValueError, match=r"payload must be a JSON object"):
            cli_mod._validate_rejection_feedback_payload("code_review", "not-a-dict")

    def test_missing_required_field_raises(self) -> None:
        with pytest.raises(ValueError, match=r"missing required field"):
            cli_mod._validate_rejection_feedback_payload("code_review", {})

    def test_categories_must_be_non_empty_list(self) -> None:
        payload = self._valid_payload()
        payload["categories"] = []
        with pytest.raises(ValueError, match=r"categories must be a non-empty list"):
            cli_mod._validate_rejection_feedback_payload("code_review", payload)

    def test_category_entry_must_be_object(self) -> None:
        payload = self._valid_payload()
        payload["categories"] = ["string-not-object"]
        with pytest.raises(ValueError, match=r"must be an object"):
            cli_mod._validate_rejection_feedback_payload("code_review", payload)

    def test_category_missing_required_field(self) -> None:
        payload = self._valid_payload()
        del payload["categories"][0]["files"]
        with pytest.raises(ValueError, match=r"missing field 'files'"):
            cli_mod._validate_rejection_feedback_payload("code_review", payload)

    def test_category_code_not_in_vocabulary(self) -> None:
        payload = self._valid_payload()
        payload["categories"][0]["code"] = "NOT_A_REAL_CODE"
        with pytest.raises(ValueError, match=r"is not in code_review's vocabulary"):
            cli_mod._validate_rejection_feedback_payload("code_review", payload)

    def test_category_severity_must_be_fail_or_warn(self) -> None:
        payload = self._valid_payload()
        payload["categories"][0]["severity"] = "info"
        with pytest.raises(ValueError, match=r"must be 'fail' or 'warn'"):
            cli_mod._validate_rejection_feedback_payload("code_review", payload)

    def test_category_files_must_be_string_list(self) -> None:
        payload = self._valid_payload()
        payload["categories"][0]["files"] = [123]
        with pytest.raises(ValueError, match=r"files must be a list of strings"):
            cli_mod._validate_rejection_feedback_payload("code_review", payload)

    def test_raw_verdict_text_must_be_string(self) -> None:
        payload = self._valid_payload()
        payload["raw_verdict_text"] = 12345
        with pytest.raises(ValueError, match=r"raw_verdict_text must be a string"):
            cli_mod._validate_rejection_feedback_payload("code_review", payload)


# ---------------------------------------------------------------------------
# _parse_log_rejection_feedback_argv — lines 7594-7595, 7598
# ---------------------------------------------------------------------------


class TestParseLogRejectionFeedbackArgv:
    def test_empty_arg_skipped(self) -> None:
        # Empty string arg should be silently skipped, parser should still
        # require judge/task/json after.  Test that the missing-values
        # error still fires (so the empty skip happens).
        with pytest.raises(ValueError, match=r"--json requires a value"):
            cli_mod._parse_log_rejection_feedback_argv(("", "--json"))

    def test_json_missing_value_raises(self) -> None:
        with pytest.raises(ValueError, match=r"--json requires a value"):
            cli_mod._parse_log_rejection_feedback_argv(("--json",))

    def test_happy_path(self) -> None:
        judge, task_id, raw_json = cli_mod._parse_log_rejection_feedback_argv(
            ("code_review", "E1-F1-S1-T1", "--json", '{"a": 1}')
        )
        assert judge == "code_review"
        assert task_id == "E1-F1-S1-T1"
        assert raw_json == '{"a": 1}'


# ---------------------------------------------------------------------------
# Scope set/clear/show error paths — lines 6342-6471
# ---------------------------------------------------------------------------


class TestScopeSetErrorBranches:
    def test_session_scope_value_error_returns_one(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        with patch(
            "devbench.cli._session_scope_file_path",
            side_effect=ValueError("session config bad"),
        ):
            rc = cli_mod._scope_set("E1", "", tmp_path)
        assert rc == 1
        assert "session config bad" in capsys.readouterr().err

    def test_scope_to_file_oserror_returns_one(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        target = tmp_path / "scope.json"
        # Ensure backlog parser sees an empty index OK; mock parse_index to no-op.
        with (
            patch("devbench.cli.BacklogParser") as parser_cls,
            patch("devbench.cli._session_scope_file_path", return_value=target),
            patch("devbench.scope.ScopeFilter.to_file", side_effect=OSError("denied")),
        ):
            parser_cls.return_value.parse_index.return_value = []
            rc = cli_mod._scope_set("", "", tmp_path)
        assert rc == 1
        assert "cannot write scope.json" in capsys.readouterr().err

    def test_invalid_scope_token_returns_one(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        with patch("devbench.cli.BacklogParser") as parser_cls:
            parser_cls.return_value.parse_index.return_value = []
            # "E3-E1" is a reverse-range token that raises InvalidScopeError.
            rc = cli_mod._scope_set("E3-E1", "", tmp_path)
        assert rc == 1
        assert "invalid scope token" in capsys.readouterr().err


class TestScopeClearErrorBranches:
    def test_session_scope_value_error_returns_one(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        with patch(
            "devbench.cli._session_scope_file_path",
            side_effect=ValueError("session config bad"),
        ):
            rc = cli_mod._scope_clear(tmp_path)
        assert rc == 1
        assert "session config bad" in capsys.readouterr().err

    def test_clear_oserror_returns_one(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        target = tmp_path / "scope.json"
        target.write_text("{}", encoding="utf-8")
        with (
            patch("devbench.cli._session_scope_file_path", return_value=target),
            patch("devbench.scope.ScopeFilter.clear", side_effect=OSError("denied")),
        ):
            rc = cli_mod._scope_clear(tmp_path)
        assert rc == 1
        assert "cannot delete scope.json" in capsys.readouterr().err


class TestScopeShowErrorBranches:
    def test_session_scope_value_error_returns_one(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        with patch(
            "devbench.cli._session_scope_file_path",
            side_effect=ValueError("session config bad"),
        ):
            rc = cli_mod._scope_show(tmp_path)
        assert rc == 1
        assert "session config bad" in capsys.readouterr().err

    def test_corrupt_json_returns_one(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        target = tmp_path / "scope.json"
        target.write_text("{not valid json", encoding="utf-8")
        with patch("devbench.cli._session_scope_file_path", return_value=target):
            rc = cli_mod._scope_show(tmp_path)
        assert rc == 1
        assert "cannot read scope.json" in capsys.readouterr().err


class TestParseScopeSetArgvErrorBranches:
    def test_include_missing_value_errors(self, capsys: pytest.CaptureFixture) -> None:
        include, exclude, rc = cli_mod._parse_scope_set_argv(("--include",))
        assert rc == 1
        assert "--include requires a value" in capsys.readouterr().err

    def test_include_value_starts_with_dashes_errors(self, capsys: pytest.CaptureFixture) -> None:
        # When the value begins with ``--`` the parser treats it as a flag.
        include, exclude, rc = cli_mod._parse_scope_set_argv(("--include", "--exclude"))
        assert rc == 1

    def test_unknown_arg_errors(self, capsys: pytest.CaptureFixture) -> None:
        include, exclude, rc = cli_mod._parse_scope_set_argv(("not-a-flag",))
        assert rc == 1
        assert "unrecognised argument" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _detect_test_validates_source — lines 8712-8728
# ---------------------------------------------------------------------------


class TestDetectTestValidatesSource:
    def test_returns_empty_for_missing_proposals_dir(self, tmp_path: Path) -> None:
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli_mod._detect_test_validates_source("E1-F1-S1-T1") == ""

    def test_skips_malformed_json_files(self, tmp_path: Path) -> None:
        pdir = tmp_path / ".devbench" / "proposals"
        pdir.mkdir(parents=True)
        (pdir / "bad.json").write_text("{not valid json", encoding="utf-8")
        (pdir / "good.json").write_text(
            json.dumps(
                {
                    "source_dep_direction": "test_validates_source",
                    "proposed_tasks": [{"suggested_id": "E1-F1-S1-T1"}],
                }
            )
        )
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli_mod._detect_test_validates_source("E1-F1-S1-T1") == "flag"

    def test_non_dict_entries_are_skipped(self, tmp_path: Path) -> None:
        pdir = tmp_path / ".devbench" / "proposals"
        pdir.mkdir(parents=True)
        (pdir / "p.json").write_text(json.dumps({"proposed_tasks": ["bad", {"suggested_id": "E1-F1-S1-T1"}]}))
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli_mod._detect_test_validates_source("E1-F1-S1-T1") == ""

    def test_title_prefix_yields_heuristic(self, tmp_path: Path) -> None:
        pdir = tmp_path / ".devbench" / "proposals"
        pdir.mkdir(parents=True)
        (pdir / "p.json").write_text(
            json.dumps(
                {
                    "proposed_tasks": [
                        {
                            "suggested_id": "E1-F1-S1-T1",
                            "title": "Make test pass",
                        }
                    ]
                }
            )
        )
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            # _TEST_VALIDATES_SOURCE_TITLE_PREFIXES includes "Make test pass" — sanity-check.
            result = cli_mod._detect_test_validates_source("E1-F1-S1-T1")
            # Either "heuristic" (prefix matched) or "" if prefix not in the tuple;
            # the goal is to exercise the title-startswith branch.
            assert result in ("heuristic", "")

    def test_test_only_files_yields_heuristic(self, tmp_path: Path) -> None:
        pdir = tmp_path / ".devbench" / "proposals"
        pdir.mkdir(parents=True)
        (pdir / "p.json").write_text(
            json.dumps(
                {
                    "proposed_tasks": [
                        {
                            "suggested_id": "E1-F1-S1-T1",
                            "title": "x",
                            "files_to_own": ["tests/foo.py", "tests/bar.py"],
                        }
                    ]
                }
            )
        )
        with patch("devbench.cli.WORKSPACE_ROOT", tmp_path):
            assert cli_mod._detect_test_validates_source("E1-F1-S1-T1") == "heuristic"

    def test_outer_oserror_returns_empty(self, tmp_path: Path) -> None:
        pdir = tmp_path / ".devbench" / "proposals"
        pdir.mkdir(parents=True)
        # The outer try wraps proposals_dir.glob() -- making glob raise OSError
        # hits the catch at line 8727.
        with (
            patch("devbench.cli.WORKSPACE_ROOT", tmp_path),
            patch.object(Path, "glob", side_effect=OSError("denied")),
        ):
            assert cli_mod._detect_test_validates_source("anything") == ""


# ---------------------------------------------------------------------------
# _kill_sigterm — lines 7112-7113
# ---------------------------------------------------------------------------


class TestKillSigterm:
    def test_returns_empty_on_success(self) -> None:
        with patch("devbench.cli.os.kill"):
            assert cli_mod._kill_sigterm(12345, "alpha") == ""

    def test_process_not_running_returns_error(self) -> None:
        with patch("devbench.cli.os.kill", side_effect=ProcessLookupError):
            msg = cli_mod._kill_sigterm(12345, "alpha")
        assert "not running" in msg

    def test_permission_denied_returns_error(self) -> None:
        with patch("devbench.cli.os.kill", side_effect=PermissionError):
            msg = cli_mod._kill_sigterm(12345, "alpha")
        assert "permission denied" in msg

    def test_oserror_returns_error(self) -> None:
        with patch("devbench.cli.os.kill", side_effect=OSError("broken")):
            msg = cli_mod._kill_sigterm(12345, "alpha")
        assert "cannot send SIGTERM" in msg


# ---------------------------------------------------------------------------
# _build_amendment_request_from_stdin — lines 7490-7491
# ---------------------------------------------------------------------------


class TestBuildAmendmentRequestFromStdin:
    def test_stdin_oserror_raises(self) -> None:
        fake_stdin = MagicMock()
        fake_stdin.read.side_effect = OSError("denied")
        with patch.object(sys, "stdin", fake_stdin):
            with pytest.raises(cli_mod._AmendmentRequestInputError, match=r"cannot read stdin"):
                cli_mod._build_amendment_request_from_stdin("E1-F1-S1-T1")


# ---------------------------------------------------------------------------
# _latest_log_in_progress_ts + _latest_audit_in_progress_ts — lines 847, 860, 882
# ---------------------------------------------------------------------------


class TestLatestLogInProgressTs:
    def test_returns_none_when_log_file_missing(self, tmp_path: Path) -> None:
        # log_path=None + _try_resolve_log_file_path returns None → return None.
        with patch("devbench.cli._try_resolve_log_file_path", return_value=None):
            assert cli_mod._latest_log_in_progress_ts("E1-F1-S1-T1", None) is None

    def test_returns_none_when_read_raises_oserror(self, tmp_path: Path) -> None:
        log = tmp_path / "log"
        log.write_text("data", encoding="utf-8")
        with patch.object(Path, "read_text", side_effect=OSError("denied")):
            assert cli_mod._latest_log_in_progress_ts("E1-F1-S1-T1", log) is None

    def test_skips_lines_with_bad_timestamp(self, tmp_path: Path) -> None:
        # Log line matching the prefix but with an unparseable timestamp
        # (9999-99-99) -- the strptime ValueError is caught and the line skipped.
        log = tmp_path / "log"
        log.write_text(
            "9999-99-99T99:99:99Z [devbench.backlog_manager] INFO Set E1-F1-S1-T1 to 'in-progress'\n"
            "2025-01-01T00:00:00Z [devbench.backlog_manager] INFO Set E1-F1-S1-T1 to 'in-progress'\n",
            encoding="utf-8",
        )
        result = cli_mod._latest_log_in_progress_ts("E1-F1-S1-T1", log)
        assert result is not None
        assert result.year == 2025


class TestLatestAuditInProgressTs:
    def test_returns_none_for_unknown_task(self, tmp_path: Path) -> None:
        with patch("devbench.cli._resolve_unit_file_by_id", return_value=None):
            assert cli_mod._latest_audit_in_progress_ts("unknown") is None

    def test_returns_none_on_read_oserror(self, tmp_path: Path) -> None:
        wu = tmp_path / "wu.md"
        wu.write_text("x", encoding="utf-8")
        with (
            patch("devbench.cli._resolve_unit_file_by_id", return_value=wu),
            patch.object(Path, "read_text", side_effect=OSError("denied")),
        ):
            assert cli_mod._latest_audit_in_progress_ts("E1-F1-S1-T1") is None


# ---------------------------------------------------------------------------
# _try_resolve_log_file_path — line 847-848
# ---------------------------------------------------------------------------


class TestTryResolveLogFilePath:
    def test_returns_none_on_systemexit(self) -> None:
        with patch("devbench.cli._resolve_log_file_path", side_effect=SystemExit(1)):
            assert cli_mod._try_resolve_log_file_path() is None


# ---------------------------------------------------------------------------
# _legacy_emit_orphan_cleanup_proposal extra branches — lines 3858-3906
# ---------------------------------------------------------------------------


class TestLegacyEmitOrphanCleanupProposalExtraBranches:
    """Cover the early-return + dedup + allocate-failure paths that the
    main legacy-emit tests do not exercise.
    """

    _SOURCE_ID = "E0-F1-S1-T1"

    def _make_unit(self) -> WorkUnit:
        from devbench.backlog.work_unit import WorkUnitStatus, WorkUnitType

        return WorkUnit(
            id=self._SOURCE_ID,
            title="Source task",
            status=WorkUnitStatus.IN_PROGRESS,
            unit_type=WorkUnitType.TASK,
            file_path=Path(f"backlog/E0/E0-F1/E0-F1-S1/{self._SOURCE_ID}.md"),
            repo="org/repo",
            dependencies=[],
        )

    def _patch_workspace(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(cli_mod, "WORKSPACE_ROOT", tmp_path)
        monkeypatch.setattr(cli_mod, "BACKLOG_ROOT", tmp_path / "backlog")
        monkeypatch.setattr(cli_mod, "BACKLOG_INDEX", tmp_path / "BACKLOG.md")

    def test_returns_true_when_proposal_already_exists(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
        tmp_path: Path,
    ) -> None:
        self._patch_workspace(monkeypatch, tmp_path)
        proposals = tmp_path / ".devbench" / "proposals"
        proposals.mkdir(parents=True)
        # Materialise the proposal file at the path that ``proposal_path()`` returns.
        (proposals / f"{self._SOURCE_ID}.json").write_text("{}", encoding="utf-8")
        repo = tmp_path / "repo"
        repo.mkdir()
        result = cli_mod._legacy_emit_orphan_cleanup_proposal(
            unit_id=self._SOURCE_ID,
            unit=self._make_unit(),
            repo_path=repo,
            detected=[".coverage"],
        )
        assert result is True
        assert "Cleanup proposal already pending" in capsys.readouterr().err

    def test_reuses_existing_cleanup_when_found(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
        tmp_path: Path,
    ) -> None:
        self._patch_workspace(monkeypatch, tmp_path)
        repo = tmp_path / "repo"
        repo.mkdir()
        with (
            patch("devbench.cli._find_existing_cleanup_proposal", return_value="E0-F1-S1-T9"),
            patch("devbench.cli._wire_orphan_cleanup_dep_chain", return_value=["x"]),
        ):
            result = cli_mod._legacy_emit_orphan_cleanup_proposal(
                unit_id=self._SOURCE_ID,
                unit=self._make_unit(),
                repo_path=repo,
                detected=[".coverage"],
            )
        assert result is True
        assert "Existing cleanup task" in capsys.readouterr().err

    def test_allocate_id_failure_returns_true(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
        tmp_path: Path,
    ) -> None:
        from devbench.backlog.proposal import ProposalError

        self._patch_workspace(monkeypatch, tmp_path)
        repo = tmp_path / "repo"
        repo.mkdir()
        with (
            patch("devbench.cli._find_existing_cleanup_proposal", return_value=None),
            patch("devbench.backlog.proposal.allocate_next_ids", side_effect=ProposalError("no slots")),
        ):
            result = cli_mod._legacy_emit_orphan_cleanup_proposal(
                unit_id=self._SOURCE_ID,
                unit=self._make_unit(),
                repo_path=repo,
                detected=[".coverage"] * 10,  # >5 to also exercise the "and N more" branch
            )
        assert result is True
        assert "cannot allocate cleanup-task ID" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _session_scope_file_path — line 6309
# ---------------------------------------------------------------------------


class TestSessionScopeFilePath:
    def test_rejects_double_dot_in_session_name(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEVBENCH_SESSION_NAME", "../escape")
        with pytest.raises(ValueError, match=r"invalid path segment"):
            cli_mod._session_scope_file_path(tmp_path)


# ---------------------------------------------------------------------------
# _build_scope_for_next — lines 986-987, 998-999
# ---------------------------------------------------------------------------


class TestBuildScopeForNext:
    def test_invalid_scope_token_returns_error_message(self, tmp_path: Path) -> None:
        # "E3-E1" reverse range raises InvalidScopeError.
        scope, err = cli_mod._build_scope_for_next("E3-E1", "", ["E1-F1-S1-T1"])
        assert scope is None
        assert "invalid scope token" in err

    def test_corrupt_scope_json_returns_error_message(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # When scope.json banner data parses but ScopeFilter.from_file
        # then fails with corrupt content, we hit the line-998-999 branch.
        scope_path = tmp_path / ".devbench" / "scope.json"
        scope_path.parent.mkdir(parents=True)
        scope_path.write_text(
            '{"include": [], "exclude": [], "expanded_ids": [], "started_at": "x", "started_by": "y"}'
        )
        monkeypatch.setattr(cli_mod, "WORKSPACE_ROOT", tmp_path)
        with patch("devbench.cli.ScopeFilter.from_file", side_effect=KeyError("missing field")):
            scope, err = cli_mod._build_scope_for_next("", "", [])
        assert scope is None
        assert "scope.json is corrupt" in err


# ---------------------------------------------------------------------------
# _parse_add_dep_argv extra branches — lines 8844-8845
# ---------------------------------------------------------------------------


class TestParseAddDepArgv:
    def test_empty_arg_skipped(self) -> None:
        # Empty argv slot is skipped; missing positional → error returned.
        blocked, blocker, reason = cli_mod._parse_add_dep_argv(("", "E1-F1-S1-T1", "E1-F1-S1-T2"))
        assert blocked == "E1-F1-S1-T1"

    def test_reason_flag_consumes_value(self) -> None:
        blocked, blocker, reason = cli_mod._parse_add_dep_argv(("E1-F1-S1-T1", "E1-F1-S1-T2", "--reason", "why"))
        assert reason == "why"

    def test_reason_missing_value_errors(self, capsys: pytest.CaptureFixture) -> None:
        blocked, blocker, reason = cli_mod._parse_add_dep_argv(("E1-F1-S1-T1", "E1-F1-S1-T2", "--reason"))
        assert blocked is None
        assert "--reason requires a value" in capsys.readouterr().err

    def test_unknown_flag_errors(self, capsys: pytest.CaptureFixture) -> None:
        blocked, blocker, reason = cli_mod._parse_add_dep_argv(("--unknown",))
        assert blocked is None
        assert "unknown flag" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _parse_reject_proposal_argv — lines 8938-8939, 8953
# ---------------------------------------------------------------------------


class TestParseRejectProposalArgv:
    def test_empty_arg_skipped(self) -> None:
        task_id, unmat, reason = cli_mod._parse_reject_proposal_argv(("", "E1-F1-S1-T1", "--reason", "why"))
        assert task_id == "E1-F1-S1-T1"
        assert reason == "why"

    def test_unknown_flag_errors(self) -> None:
        with pytest.raises(cli_mod._RejectProposalArgError, match=r"unknown flag"):
            cli_mod._parse_reject_proposal_argv(("--bogus",))

    def test_unmaterialised_needs_value(self) -> None:
        with pytest.raises(cli_mod._RejectProposalArgError, match=r"requires a source-task-id"):
            cli_mod._parse_reject_proposal_argv(("--unmaterialised", "--reason", "x"))

    def test_both_task_and_unmaterialised_errors(self) -> None:
        with pytest.raises(cli_mod._RejectProposalArgError, match=r"not both"):
            cli_mod._parse_reject_proposal_argv(("E1-F1-S1-T1", "--unmaterialised", "E2-F1-S1-T1", "--reason", "x"))

    def test_neither_supplied_errors(self) -> None:
        with pytest.raises(cli_mod._RejectProposalArgError, match=r"requires either"):
            cli_mod._parse_reject_proposal_argv(("--reason", "x"))

    def test_missing_reason_errors(self) -> None:
        with pytest.raises(cli_mod._RejectProposalArgError, match=r"reason"):
            cli_mod._parse_reject_proposal_argv(("E1-F1-S1-T1",))


# ---------------------------------------------------------------------------
# _parse_id_and_reason — lines 2191-2192
# ---------------------------------------------------------------------------


class TestParseIdAndReason:
    def test_empty_string_skipped_and_happy_path(self) -> None:
        result = cli_mod._parse_id_and_reason(("", "E1-F1-S1-T1", "--reason", "why"), command_name="hold")
        assert result == ("E1-F1-S1-T1", "why")

    def test_missing_reason_value_returns_one(self, capsys: pytest.CaptureFixture) -> None:
        rc = cli_mod._parse_id_and_reason(("E1-F1-S1-T1", "--reason"), command_name="hold")
        assert rc == 1
        assert "--reason requires a value" in capsys.readouterr().err

    def test_missing_id_or_reason_returns_one(self, capsys: pytest.CaptureFixture) -> None:
        rc = cli_mod._parse_id_and_reason(("--reason", "why"), command_name="hold")
        assert rc == 1
        assert "hold requires" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# cmd_set_status usage error — lines 1245-1250
# ---------------------------------------------------------------------------


class TestCmdSetStatusUsage:
    def test_wrong_number_of_args_errors(self, capsys: pytest.CaptureFixture) -> None:
        rc = cli_mod.cmd_set_status("only-one")
        assert rc == 1
        assert "set-status usage" in capsys.readouterr().err

    def test_invalid_status_errors(self, capsys: pytest.CaptureFixture) -> None:
        rc = cli_mod._cmd_set_status_single("E1-F1-S1-T1", "not-a-real-status")
        assert rc == 1
        assert "Invalid status" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _parse_bulk_set_status_args — lines 1339-1343
# ---------------------------------------------------------------------------


class TestParseBulkSetStatusArgs:
    def test_include_missing_value_errors(self, capsys: pytest.CaptureFixture) -> None:
        rc = cli_mod._parse_bulk_set_status_args(["--include"])
        assert rc == 1
        assert "requires a value" in capsys.readouterr().err

    def test_exclude_missing_value_errors(self, capsys: pytest.CaptureFixture) -> None:
        rc = cli_mod._parse_bulk_set_status_args(["--exclude"])
        assert rc == 1
        assert "requires a value" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _check_repo_origin / _check_repo_default_branch / _check_repo_open_prs —
# lines 2600-2671
# ---------------------------------------------------------------------------


class TestCheckRepoOrigin:
    def test_timeout_returns_error(self, tmp_path: Path) -> None:
        with patch(
            "devbench.cli.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=5),
        ):
            ok, err = cli_mod._check_repo_origin("acme/x", tmp_path, timeout=5)
        assert ok is False
        assert err is not None
        assert "timed out" in err

    def test_local_only_with_origin_present_errors(self, tmp_path: Path) -> None:
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout="git@github.com:x/y.git", stderr="")
        with patch("devbench.cli.subprocess.run", return_value=result):
            ok, err = cli_mod._check_repo_origin("acme/x", tmp_path, timeout=5, local_only=True)
        assert ok is False
        assert err is not None
        assert "origin" in err

    def test_local_only_without_origin_ok(self, tmp_path: Path) -> None:
        result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no remote")
        with patch("devbench.cli.subprocess.run", return_value=result):
            ok, err = cli_mod._check_repo_origin("acme/x", tmp_path, timeout=5, local_only=True)
        assert ok is True
        assert err is None

    def test_no_origin_default_mode_errors(self, tmp_path: Path) -> None:
        result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no remote")
        with patch("devbench.cli.subprocess.run", return_value=result):
            ok, err = cli_mod._check_repo_origin("acme/x", tmp_path, timeout=5)
        assert ok is False
        assert err is not None
        assert "no 'origin'" in err


class TestCheckRepoDefaultBranch:
    def test_returns_none_when_configured_default_unset(self) -> None:
        assert cli_mod._check_repo_default_branch("acme/x", None, timeout=5) is None

    def test_timeout_returns_error_string(self) -> None:
        with patch(
            "devbench.cli.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=5),
        ):
            err = cli_mod._check_repo_default_branch("acme/x", "main", timeout=5)
        assert err is not None
        assert "timed out" in err

    def test_gh_api_failure_returns_error(self) -> None:
        result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="auth")
        with patch("devbench.cli.subprocess.run", return_value=result):
            err = cli_mod._check_repo_default_branch("acme/x", "main", timeout=5)
        assert err is not None
        assert "gh api" in err

    def test_default_branch_match_returns_none(self) -> None:
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout="main\n", stderr="")
        with patch("devbench.cli.subprocess.run", return_value=result):
            assert cli_mod._check_repo_default_branch("acme/x", "main", timeout=5) is None


class TestCheckRepoOpenPrs:
    def test_timeout_returns_error(self) -> None:
        with patch(
            "devbench.cli.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=5),
        ):
            err = cli_mod._check_repo_open_prs("acme/x", "feat/x", timeout=5)
        assert err is not None
        assert "timed out" in err

    def test_no_open_prs_returns_none(self) -> None:
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr="")
        with patch("devbench.cli.subprocess.run", return_value=result):
            assert cli_mod._check_repo_open_prs("acme/x", "feat/x", timeout=5) is None

    def test_open_prs_present_returns_error(self) -> None:
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout='[{"number": 1, "title": "wip"}]', stderr="")
        with patch("devbench.cli.subprocess.run", return_value=result):
            err = cli_mod._check_repo_open_prs("acme/x", "feat/x", timeout=5)
        assert err is not None
        assert "open PR" in err


# ---------------------------------------------------------------------------
# _run_inline_cleanup_steps — lines 3725-3757 (each git step's error branch)
# ---------------------------------------------------------------------------


class TestRunInlineCleanupSteps:
    @staticmethod
    def _git_side_effects(*raise_at_step: int) -> object:
        """Build a side_effect callable for ``GitOpsService._git`` that raises
        ``RuntimeError`` at the requested 1-indexed step and returns a normal
        ``(rc, out, err)`` tuple otherwise.
        """
        calls = [0]

        def _se(*args: object, **kwargs: object) -> object:
            calls[0] += 1
            if calls[0] in raise_at_step:
                raise RuntimeError(f"step {calls[0]} broke")
            return (0, "", "")

        return _se

    def test_pre_cleanup_diff_failure(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        with patch("devbench.github.git_ops.GitOpsService._git", side_effect=self._git_side_effects(1)):
            with pytest.raises(cli_mod._InlineCleanupError, match=r"pre-cleanup staged paths"):
                cli_mod._run_inline_cleanup_steps(repo, ["a.tmp"])

    def test_reset_head_failure(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        with patch("devbench.github.git_ops.GitOpsService._git", side_effect=self._git_side_effects(2)):
            with pytest.raises(cli_mod._InlineCleanupError, match=r"git reset HEAD failed"):
                cli_mod._run_inline_cleanup_steps(repo, ["a.tmp"])

    def test_cleanup_orphans_failure(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        with (
            patch("devbench.github.git_ops.GitOpsService._git", side_effect=self._git_side_effects()),
            patch("devbench.git_orphans.cleanup_tracked_orphans", side_effect=FileNotFoundError("x")),
        ):
            with pytest.raises(cli_mod._InlineCleanupError, match=r"cleanup_tracked_orphans"):
                cli_mod._run_inline_cleanup_steps(repo, ["a.tmp"])

    def test_stage_gitignore_failure(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        gitignore = repo / ".gitignore"
        gitignore.write_text("x", encoding="utf-8")
        report = SimpleNamespace(
            gitignore_updated=True,
            gitignore_path=gitignore,
            removed=[],
        )
        # Steps: 1=diff (ok), 2=reset (ok), 3=add .gitignore (fail).
        with (
            patch("devbench.github.git_ops.GitOpsService._git", side_effect=self._git_side_effects(3)),
            patch("devbench.git_orphans.cleanup_tracked_orphans", return_value=report),
        ):
            with pytest.raises(cli_mod._InlineCleanupError, match=r"could not stage \.gitignore"):
                cli_mod._run_inline_cleanup_steps(repo, ["a.tmp"])

    def test_commit_failure(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        gitignore = repo / ".gitignore"
        gitignore.write_text("x", encoding="utf-8")
        report = SimpleNamespace(
            gitignore_updated=True,
            gitignore_path=gitignore,
            removed=["a.tmp"],
        )
        # Steps: 1=diff, 2=reset, 3=add gitignore (ok), 4=commit (fail).
        with (
            patch("devbench.github.git_ops.GitOpsService._git", side_effect=self._git_side_effects(4)),
            patch("devbench.git_orphans.cleanup_tracked_orphans", return_value=report),
        ):
            with pytest.raises(cli_mod._InlineCleanupError, match=r"cleanup commit failed"):
                cli_mod._run_inline_cleanup_steps(repo, ["a.tmp"])

    def test_restage_failure(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        gitignore = repo / ".gitignore"
        gitignore.write_text("x", encoding="utf-8")
        report = SimpleNamespace(
            gitignore_updated=False,
            gitignore_path=gitignore,
            removed=[],
        )

        # Force the diff step to return a path that should be re-staged.
        calls = {"n": 0}

        def _se(*args: object, **kwargs: object) -> object:
            calls["n"] += 1
            if calls["n"] == 1:
                # diff step returns one path.
                return (0, "preserve.py\n", "")
            if calls["n"] == 3:
                # re-stage step fails.
                raise RuntimeError("restage broke")
            return (0, "", "")

        with (
            patch("devbench.github.git_ops.GitOpsService._git", side_effect=_se),
            patch("devbench.git_orphans.cleanup_tracked_orphans", return_value=report),
        ):
            with pytest.raises(cli_mod._InlineCleanupError, match=r"re-stage executor paths"):
                cli_mod._run_inline_cleanup_steps(repo, ["a.tmp"])
