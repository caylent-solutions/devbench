"""Phase 6 INTEGRATION: real start -> __run -> clean exit on a dummy backlog.

This is the in-CI surface of the Phase-6 integration layer (Section 10.0, the
"dummy backlog" fixture). The LIVE acceptance criteria (AC-23..29, AC-34) that
require a real subscription login, a live ``claude``, real ``screen``, or a real
quota event are DEFERRED (Section 10.1) and pinned by
``test_supervise_deferred_acs.py``; this module exercises everything about the
integration that CAN run deterministically in CI:

1. The dummy backlog fixture is a real, parseable 1-2-trivial-unit throwaway
   backlog (``tests/fixtures/supervise/dummy-backlog/``) with NO AWS / terraform /
   cloud units -- the supervisor's scope expansion + ``devbench next`` resolve its
   work units exactly as they would a production backlog.
2. ``supervise start -> __run -> /orchestrate -> ALL_DONE`` drives the session to
   ``completed-clean`` exit 0 against the REAL ``pexpect`` supervisor + the REAL
   ``stub-claude`` executable, with the dummy backlog as the workspace
   (``DEVBENCH_WORKSPACE_ROOT``).
3. The SUBSCRIPTION-BILLING assertion that is verifiable WITHOUT a live session
   (the in-CI half of AC-24): the session environment the supervisor hands the
   ``claude`` child carries NO ``ANTHROPIC_API_KEY`` (nor any always-deny
   API/Bedrock-routing var), so the run bills against the subscription, not the
   API (Section 0.2, FR-21). The live ``/proc/<pid>/environ`` inspection half of
   AC-24 remains deferred.

Everything is input-driven (CLAUDE.md): the dummy backlog is a fixture, the stub
behaviour is env-selected, and the timeouts come from the test-bounded
``supervise`` config -- no literal leaks into the supervisor.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from functional.harness import functional_supervise_config, supervised_stub

from devbench import cli
from devbench.backlog.parser import BacklogParser
from devbench.constants import (
    SUPERVISE_AWS_PASSTHROUGH_ENV_VARS,
    SUPERVISE_BILLING_CHANNEL,
    SUPERVISE_BILLING_MODE_SUBSCRIPTION,
    resolve_supervise_deny_vars,
)
from devbench.scope import session_scope_file_path
from devbench.supervise import EnvSanitizer, SuperviseRegistry

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "supervise"
DUMMY_BACKLOG_ROOT: Path = (_FIXTURES_DIR / "dummy-backlog").resolve()


@pytest.mark.integration
class TestDummyBacklogFixtureIsParseable:
    """The dummy backlog fixture is a real, trivial, non-AWS, parseable backlog."""

    def test_backlog_index_and_units_parse(self) -> None:
        parser = BacklogParser(
            backlog_root=DUMMY_BACKLOG_ROOT / "backlog",
            backlog_index=DUMMY_BACKLOG_ROOT / "BACKLOG.md",
        )
        units = parser.parse_index()
        ids = sorted(u.id for u in units)
        assert ids, "the dummy backlog must contain at least one parseable work unit"
        assert len(ids) <= 2, "the dummy backlog is intentionally tiny (1-2 trivial units)"
        from devbench.backlog.work_unit import WorkUnitType

        assert all(u.unit_type is WorkUnitType.TASK for u in units)

    def test_units_are_trivial_and_non_aws(self) -> None:
        forbidden = ("terraform", "terragrunt", "boto3", "bedrock", "tf-test", "terratest", "aws_", "aws cli")
        task_files = sorted((DUMMY_BACKLOG_ROOT / "backlog").glob("E*.md"))
        assert task_files, "the dummy backlog must contain work-unit task files"
        for md in task_files:
            text = md.read_text(encoding="utf-8").lower()
            hits = [token for token in forbidden if token in text]
            assert not hits, f"dummy backlog work unit {md} references forbidden cloud tokens: {hits}"

    def test_backlog_has_no_em_dash(self) -> None:
        em_dash = "\u2014"
        for md in sorted(DUMMY_BACKLOG_ROOT.rglob("*.md")):
            assert em_dash not in md.read_text(encoding="utf-8"), (
                f"{md} must use -- (double hyphen), not the em-dash glyph (U+2014)."
            )


@pytest.mark.integration
class TestDummyBacklogCleanRun:
    """start -> __run -> ALL_DONE -> completed-clean exit 0, with the dummy backlog."""

    def test_run_reaches_completed_clean_against_dummy_backlog(self, tmp_path: Path) -> None:
        workspace = _materialise_dummy_workspace(tmp_path)
        config = functional_supervise_config()
        stub_env = {"STUB_CLAUDE_SCRIPT": "clean", "STUB_CLAUDE_EXIT_CODE": "0"}
        with supervised_stub(workspace_root=workspace, config=config, stub_env=stub_env):
            rc = cli.cmd_supervise("__run", "--name", "dummy1", "--model", "claude-opus-4-8")

        assert rc == 0
        state = SuperviseRegistry(workspace).read_state("dummy1")
        assert state is not None
        assert state.state == "completed-clean"
        assert state.exit_reason == "all-done"
        assert state.billing_channel == SUPERVISE_BILLING_CHANNEL


@pytest.mark.integration
class TestSubscriptionBillingNoApiKeyInSessionEnv:
    """The in-CI half of AC-24: no ANTHROPIC_API_KEY in the session env (FR-21)."""

    def test_env_sanitizer_strips_routing_vars_but_keeps_aws_creds(self) -> None:
        deny = resolve_supervise_deny_vars(SUPERVISE_BILLING_MODE_SUBSCRIPTION)
        polluted = dict.fromkeys(deny, "leaked-secret-value")
        polluted.update({var: f"aws-{var}" for var in SUPERVISE_AWS_PASSTHROUGH_ENV_VARS})
        polluted["PATH"] = "/usr/bin"
        sanitizer = EnvSanitizer(extra_deny_vars=(), billing_mode=SUPERVISE_BILLING_MODE_SUBSCRIPTION)
        session_env = sanitizer.build(
            source_env=polluted,
            workspace_root="/tmp/ws",
            session_name="dummy1",
            import_model="claude-opus-4-8",
        )
        for var in deny:
            assert var not in session_env, f"{var} must be stripped from the supervised session env (FR-21)"
        for var in SUPERVISE_AWS_PASSTHROUGH_ENV_VARS:
            assert session_env.get(var) == f"aws-{var}", f"{var} must pass through to the session env"
        assert session_env["DEVBENCH_WORKSPACE_ROOT"] == "/tmp/ws"
        assert session_env["DEVBENCH_SESSION_NAME"] == "dummy1"
        assert session_env["DEVBENCH_CLAUDE_MODEL"] == "claude-opus-4-8"

    def test_start_writes_scope_json_and_no_api_key_in_screen_env(self, tmp_path: Path) -> None:
        workspace = _materialise_dummy_workspace(tmp_path)
        creds = _write_credentials(tmp_path)
        config = functional_supervise_config()
        captured: dict[str, dict[str, str]] = {}

        def _fake_launch(*, name, screen_name, env, run_argv, screen_path):
            captured["env"] = dict(env)
            from devbench.constants import SUPERVISE_STATE_RUNNING
            from devbench.supervise import new_session_state

            registry = cli.SuperviseRegistry(workspace)
            state = new_session_state(
                name=name,
                pid=1,
                screen_name=screen_name,
                model="claude-opus-4-8",
                effort="xhigh",
                started_by="t",
            )
            state.state = SUPERVISE_STATE_RUNNING
            registry.write_state(state)
            return 0

        backlog_ids = [u.id for u in _parse_workspace_backlog(workspace)]
        with (
            patch.object(cli, "WORKSPACE_ROOT", workspace),
            patch("devbench.cli.SUPERVISE_CREDENTIALS_FILE", creds),
            patch("devbench.cli._resolve_orchestrator_model", return_value="claude-opus-4-8"),
            patch("devbench.cli._supervise_backlog_ids", return_value=backlog_ids),
            patch("devbench.cli._supervise_runtime_config", return_value=config),
            patch("devbench.cli._supervise_use_bedrock", return_value=False),
            patch("devbench.cli.shutil.which", lambda name: f"/usr/bin/{name}"),
            patch("devbench.cli._supervise_launch_screen", _fake_launch),
            patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True),
        ):
            rc = cli.cmd_supervise("start", "--name", "dummy1")

        assert rc == 0
        env = captured["env"]
        for var in resolve_supervise_deny_vars(SUPERVISE_BILLING_MODE_SUBSCRIPTION):
            assert var not in env, f"{var} must never reach the supervised screen session (FR-21)"
        scope_path = session_scope_file_path(workspace, "dummy1")
        assert scope_path.exists()
        scope = json.loads(scope_path.read_text(encoding="utf-8"))
        assert sorted(scope["expanded_ids"]) == sorted(backlog_ids)


def _materialise_dummy_workspace(tmp_path: Path) -> Path:
    """Copy the dummy backlog fixture into a writable tmp workspace.

    The committed fixture is read-only source of truth; the run writes its
    ``.devbench/`` registry / scope.json / pty.log beside the backlog, so the
    test operates on a throwaway copy (CLAUDE.md: never mutate a committed fixture).
    """
    import shutil

    workspace = tmp_path / "dummy-workspace"
    shutil.copytree(DUMMY_BACKLOG_ROOT, workspace)
    return workspace


def _parse_workspace_backlog(workspace: Path):
    parser = BacklogParser(
        backlog_root=workspace / "backlog",
        backlog_index=workspace / "BACKLOG.md",
    )
    return parser.parse_index()


def _write_credentials(tmp_path: Path) -> Path:
    creds = tmp_path / ".credentials.json"
    creds.write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "t", "scopes": ["user:inference"]}}),
        encoding="utf-8",
    )
    return creds
