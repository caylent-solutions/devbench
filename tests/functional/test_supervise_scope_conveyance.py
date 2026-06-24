"""AC-30 FUNCTIONAL: scope conveyance resolves the right backlog + config + scope.

The interactive ``supervise start`` path conveys scope DETERMINISTICALLY (Section 5.6,
FR-8, DI-3) by reusing existing devbench mechanisms -- no new scope code:

1. it writes ``<workspace>/.devbench/sessions/<n>/scope.json`` via the SDK path's own
   ``ScopeFilter.to_file`` (the canonical ``{include, exclude, expanded_ids, ...}``
   schema), so an explicit ``--include "E11"`` lands a scope.json whose ``expanded_ids``
   are exactly the E11 subtree;
2. it exports ``DEVBENCH_WORKSPACE_ROOT`` (which backlog/workspace + where the config is),
   ``DEVBENCH_SESSION_NAME=<n>`` (per-session scope/drain routing), and
   ``DEVBENCH_CLAUDE_MODEL`` (the import-time model the in-session subprocesses need)
   into the screen session the ``claude`` child inherits;
3. ``DEVBENCH_SESSION_NAME=<n> devbench next`` then honours the SAME session-routed
   scope.json (the file is the authority, not the kickoff line).

This drives the REAL ``start`` verb (its preflight seams mocked deterministically, the
screen launch mocked to CAPTURE the conveyed env -- no real ``screen`` in CI), asserts
the scope.json schema + the three exported conveyance vars, then runs the REAL ``cmd_next``
against the session-routed scope.json and asserts it returns only the in-scope unit.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from functional.harness import functional_supervise_config

from devbench import cli
from devbench.backlog.work_unit import WorkUnit, WorkUnitStatus, WorkUnitType
from devbench.scope import session_scope_file_path

_BACKLOG_IDS = ["E11-F1-S1-T1", "E11-F1-S1-T2", "E12-F1-S1-T1"]


def _credentials(tmp_path: Path) -> Path:
    """Write a valid subscription-auth credentials file for the start preflight."""
    creds = tmp_path / ".credentials.json"
    creds.write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "t", "scopes": ["user:inference"]}}),
        encoding="utf-8",
    )
    return creds


def _task(unit_id: str) -> WorkUnit:
    """Build a minimal actionable (in-queue, no deps) TASK WorkUnit."""
    return WorkUnit(
        id=unit_id,
        title=f"Task {unit_id}",
        status=WorkUnitStatus.IN_QUEUE,
        unit_type=WorkUnitType.TASK,
        file_path=Path(f"backlog/{unit_id}.md"),
        repo="caylent-solutions/devbench",
        dependencies=[],
    )


@pytest.mark.functional
class TestScopeConveyance:
    """AC-30: scope.json + conveyance env exported; devbench next honours the scope."""

    def test_start_writes_scope_json_and_exports_conveyance_env(self, tmp_path: Path) -> None:
        config = functional_supervise_config()
        creds = _credentials(tmp_path)
        captured: dict[str, dict[str, str]] = {}

        def _fake_launch(*, name, screen_name, env, run_argv, screen_path):
            captured["env"] = dict(env)
            from devbench.constants import SUPERVISE_STATE_RUNNING
            from devbench.supervise import new_session_state

            registry = cli.SuperviseRegistry(tmp_path)
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

        with (
            patch.object(cli, "WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.SUPERVISE_CREDENTIALS_FILE", creds),
            patch("devbench.cli._resolve_orchestrator_model", return_value="claude-opus-4-8"),
            patch("devbench.cli._supervise_backlog_ids", return_value=list(_BACKLOG_IDS)),
            patch("devbench.cli._supervise_runtime_config", return_value=config),
            patch("devbench.cli._supervise_use_bedrock", return_value=False),
            patch("devbench.cli.shutil.which", lambda name: f"/usr/bin/{name}"),
            patch("devbench.cli._supervise_launch_screen", _fake_launch),
            patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True),
        ):
            rc = cli.cmd_supervise("start", "--name", "conv1", "--include", "E11")

        assert rc == 0

        scope_path = session_scope_file_path(tmp_path, "conv1")
        assert scope_path.exists()
        scope = json.loads(scope_path.read_text(encoding="utf-8"))
        assert scope["include"] == ["E11"]
        assert sorted(scope["expanded_ids"]) == ["E11-F1-S1-T1", "E11-F1-S1-T2"]
        assert "E12-F1-S1-T1" not in scope["expanded_ids"]
        assert "started_at" in scope and "started_by" in scope

        env = captured["env"]
        assert env["DEVBENCH_WORKSPACE_ROOT"] == str(tmp_path)
        assert env["DEVBENCH_SESSION_NAME"] == "conv1"
        assert env["DEVBENCH_CLAUDE_MODEL"] == "claude-opus-4-8"
        assert "ANTHROPIC_API_KEY" not in env

    def test_devbench_next_honours_session_routed_scope(self, tmp_path: Path) -> None:
        from devbench.supervise import write_session_scope

        write_session_scope(
            workspace_root=tmp_path,
            session_name="conv1",
            include="E11",
            exclude="",
            backlog_ids=list(_BACKLOG_IDS),
        )

        from devbench.backlog.parser import BacklogParser

        units = [_task("E11-F1-S1-T1"), _task("E11-F1-S1-T2"), _task("E12-F1-S1-T1")]
        parser = BacklogParser(backlog_root=tmp_path, backlog_index=tmp_path / "BACKLOG.md")

        captured_out: list[str] = []
        with (
            patch.object(cli, "WORKSPACE_ROOT", tmp_path),
            patch("devbench.cli.BacklogParser", return_value=parser),
            patch.object(parser, "parse_index", return_value=units),
            patch.dict("os.environ", {"DEVBENCH_SESSION_NAME": "conv1"}, clear=False),
            patch("builtins.print", lambda *a, **k: captured_out.append(" ".join(str(x) for x in a))),
        ):
            rc = cli.cmd_next()

        assert rc == 0
        printed = "\n".join(captured_out)
        emitted = json.loads(printed)
        assert emitted["id"].startswith("E11-")
        assert "E12" not in printed
