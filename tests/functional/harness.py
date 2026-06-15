"""Deterministic wiring for the stub-claude functional layer (Phase 5, Section 10.0).

The functional tests exercise the REAL ``pexpect`` supervisor against the REAL
executable ``stub-claude.py`` (the fixture in ``tests/fixtures/supervise/``), so the
start / __run / stop / restart / status / info / attach flows run end to end with NO
real ``claude``, NO subscription, NO tokens, and NO ``screen``.

Two seams make this deterministic and CI-safe:

- ``claude`` resolves to the stub executable (``shutil.which`` is patched so
  :func:`devbench.supervise.require_claude` returns the stub path), so the supervisor
  spawns the stub with REAL ``pexpect.spawn`` (``pexpect.spawn`` is NOT mocked -- that
  is the whole point of the functional layer).
- ``screen`` is bypassed: the tests drive the in-screen ``__run`` body
  (``cmd_supervise("__run", ...)``) directly -- the same program ``screen`` would host
  -- because ``screen`` is not installed in CI (Section 10.0 / Phase 5: "screen is
  stubbed or bypassed where it cannot run in CI"). The operator-facing ``start`` verb,
  which DOES shell out to ``screen -dmS``, is covered by the screen-mocked unit tests
  (``test_supervise_run_cli.py``); the functional layer covers the live pexpect loop.

Every stub behaviour is selected by environment variables the stub reads (CLAUDE.md:
input-driven, no hardcoded test data). The harness only wires the seams; each test
scripts one behaviour and asserts the resulting state-machine transition + exit code +
registry state.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator, Mapping
from pathlib import Path
from unittest.mock import patch

from devbench.config_loader import (
    SuperviseConfig,
    SuperviseTimeoutsConfig,
)

#: Absolute path to the executable stub-claude CLI fixture (Section 10.0).
_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "supervise"
STUB_CLAUDE_PATH: Path = (_FIXTURES_DIR / "stub-claude.py").resolve()


def functional_supervise_config(**timeout_overrides: int) -> SuperviseConfig:
    """Return a :class:`SuperviseConfig` with short, test-bounded timeouts.

    The defaults (120s ready / 1800s idle) would make a CI failure hang for minutes;
    the functional layer drives a fast stub, so every wait is bounded to a few seconds.
    Each value remains config-driven (no literal leaks into the supervisor) -- the test
    simply supplies a tighter ``supervise.timeouts`` block, exactly as an operator could.

    Args:
        **timeout_overrides: Any :class:`SuperviseTimeoutsConfig` field to override on
            top of the test-bounded defaults.

    Returns:
        A :class:`SuperviseConfig` whose timeouts are short enough for CI.
    """
    timeouts = SuperviseTimeoutsConfig(
        ready_prompt_seconds=timeout_overrides.get("ready_prompt_seconds", 15),
        idle_seconds=timeout_overrides.get("idle_seconds", 15),
        command_ack_seconds=timeout_overrides.get("command_ack_seconds", 3),
        graceful_stop_seconds=timeout_overrides.get("graceful_stop_seconds", 15),
        poll_interval_seconds=timeout_overrides.get("poll_interval_seconds", 1),
        command_invocation_seconds=timeout_overrides.get("command_invocation_seconds", 10),
    )
    return SuperviseConfig(timeouts=timeouts)


def stub_sequence_env(*, sequence: str, state_file: Path, **extra: str) -> dict[str, str]:
    """Build the stub env for a MULTI-LAUNCH sequence (auto-restart / quota-resume).

    The supervisor relaunches the same ``claude`` invocation across exit-42 / quota
    events; the stub consumes ``STUB_CLAUDE_SCRIPT_SEQUENCE`` one entry per launch,
    tracking position in *state_file*. A test supplies a fresh temp *state_file* so the
    sequence starts at launch 0.

    Args:
        sequence: Comma-separated stub scripts (e.g. ``"restart,clean"``).
        state_file: A fresh temp file the stub uses to track the launch index.
        **extra: Additional ``STUB_CLAUDE_*`` knobs (e.g. a quota reset line).

    Returns:
        The stub env mapping.
    """
    env = {"STUB_CLAUDE_SCRIPT_SEQUENCE": sequence, "STUB_CLAUDE_STATE_FILE": str(state_file)}
    env.update(extra)
    return env


def stub_claude_which(name: str) -> str | None:
    """A ``shutil.which`` replacement that resolves ``claude`` to the stub.

    Every other executable resolves to a conventional ``/usr/bin/<name>`` path so the
    supervisor's version-record / non-claude lookups still succeed without a real binary.
    """
    if name == "claude":
        return str(STUB_CLAUDE_PATH)
    return f"/usr/bin/{name}"


@contextlib.contextmanager
def supervised_stub(
    *,
    workspace_root: Path,
    config: SuperviseConfig,
    stub_env: Mapping[str, str],
) -> Iterator[None]:
    """Patch the CLI seams so a supervise verb runs the REAL stub via REAL pexpect.

    Within the context:

    - ``devbench.cli.WORKSPACE_ROOT`` points at *workspace_root* (the per-session
      registry / pty.log / scope.json all land under it).
    - ``devbench.cli._supervise_runtime_config`` returns *config* (the test-bounded
      ``supervise`` block).
    - ``shutil.which`` resolves ``claude`` to the stub (so ``require_claude`` returns it
      and ``pexpect.spawn`` -- which is NOT mocked -- launches the stub).
    - ``_resolve_plugin_path`` returns a throwaway dir (the stub ignores ``--plugin-dir``).
    - ``_record_tool_version`` is stubbed (no real ``--version`` shell-out is needed).
    - the *stub_env* entries are exported into ``os.environ`` so the spawned stub reads
      its scripted behaviour (the supervisor spawns the child with ``env=dict(os.environ)``).

    Args:
        workspace_root: The tmp workspace the registry / logs live under.
        config: The ``supervise`` config the bodies read.
        stub_env: ``STUB_CLAUDE_*`` environment knobs selecting the stub behaviour.

    Yields:
        ``None`` -- the caller invokes ``cli.cmd_supervise(...)`` inside the context.
    """
    from devbench import cli

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(cli, "WORKSPACE_ROOT", workspace_root))
        stack.enter_context(patch("devbench.cli._supervise_runtime_config", return_value=config))
        stack.enter_context(patch("devbench.cli.shutil.which", stub_claude_which))
        stack.enter_context(patch("devbench.cli._resolve_plugin_path", return_value=workspace_root / "plugin"))
        stack.enter_context(patch("devbench.cli._record_tool_version", return_value="stub-claude 0.0.1"))
        stack.enter_context(patch.dict(os.environ, dict(stub_env), clear=False))
        yield
