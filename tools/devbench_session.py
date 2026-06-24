#!/usr/bin/env python3
"""devbench-session: per-user multi-session devbench launcher.

Installed onto each remote EC2 at /home/<user>/.local/bin/devbench-session.
Each session is its own git clone of devbench under ~/workspace/devbench-session-N
and runs detached in screen so the operator can SSH out without losing it.

Usage:
    devbench-session start <N> [--repo-url URL] [--branch BR] [--mode interactive|orchestrate]
    devbench-session attach <N>
    devbench-session list
    devbench-session stop <N>

Exit codes:
    0  success
    1  application-level error (bad args, session-not-found, clone failed, etc.)
    2  argument parsing error (delegated to argparse)
"""

from __future__ import annotations

import argparse
import dataclasses
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

DEFAULT_REPO_URL = "https://github.com/caylent-solutions/devbench.git"
DEFAULT_BRANCH = "main"
DEFAULT_MODE = "interactive"
SESSION_NAME_PREFIX = "devbench-"
WORKSPACE_DIRNAME = "workspace"
SESSION_DIRNAME_PREFIX = "devbench-session-"


@dataclasses.dataclass(frozen=True)
class SessionPaths:
    """Resolved on-disk paths for a single session."""

    home: Path
    workspace: Path
    session_dir: Path
    repo_dir: Path
    screen_name: str


def _resolve_paths(session_id: int, *, home: Path | None = None) -> SessionPaths:
    home = home if home is not None else Path("~").expanduser()
    workspace = home / WORKSPACE_DIRNAME
    session_dir = workspace / f"{SESSION_DIRNAME_PREFIX}{session_id}"
    repo_dir = session_dir / "devbench"
    return SessionPaths(
        home=home,
        workspace=workspace,
        session_dir=session_dir,
        repo_dir=repo_dir,
        screen_name=f"{SESSION_NAME_PREFIX}{session_id}",
    )


def _validate_session_id(value: str) -> int:
    try:
        n = int(value)
    except ValueError as exc:
        raise ValueError(f"session id must be a positive integer, got {value!r}") from exc
    if n < 1:
        raise ValueError(f"session id must be >= 1, got {n}")
    return n


def _run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def cmd_start(
    session_id: int,
    *,
    repo_url: str,
    branch: str,
    mode: str,
    home: Path | None = None,
    runner: Callable = _run,
) -> int:
    """Clone (or refresh) the session repo and launch detached screen."""
    paths = _resolve_paths(session_id, home=home)
    paths.workspace.mkdir(parents=True, exist_ok=True)
    paths.session_dir.mkdir(parents=True, exist_ok=True)

    if not paths.repo_dir.exists():
        runner(["git", "clone", "--branch", branch, repo_url, str(paths.repo_dir)])
    else:
        runner(["git", "-C", str(paths.repo_dir), "fetch", "origin"])
        runner(["git", "-C", str(paths.repo_dir), "checkout", branch])
        runner(["git", "-C", str(paths.repo_dir), "pull", "--ff-only"])

    if mode not in ("interactive", "orchestrate"):
        print(f"ERROR: --mode must be interactive or orchestrate, got {mode!r}", file=sys.stderr)
        return 1

    devbench_cmd = "uv run devbench start" if mode == "orchestrate" else "bash -i"
    inner = f"cd {paths.repo_dir} && {devbench_cmd}; exec bash"
    screen_cmd = ["screen", "-dmS", paths.screen_name, "bash", "-c", inner]
    runner(screen_cmd)
    print(f"started session {paths.screen_name} at {paths.repo_dir} (mode={mode})")
    return 0


def cmd_attach(session_id: int, *, home: Path | None = None, runner: Callable = _run) -> int:
    paths = _resolve_paths(session_id, home=home)
    if not _screen_exists(paths.screen_name, runner=runner):
        print(f"ERROR: session {paths.screen_name} not found", file=sys.stderr)
        return 1
    completed = subprocess.run(["screen", "-r", paths.screen_name], check=False)
    return completed.returncode


def cmd_list(*, runner: Callable = _run) -> int:
    result = runner(["screen", "-ls"], check=False)
    lines = result.stdout.splitlines() if result.stdout else []
    sessions = [line.strip() for line in lines if SESSION_NAME_PREFIX in line]
    if not sessions:
        print("(no devbench sessions)")
        return 0
    for line in sessions:
        print(line)
    return 0


def cmd_stop(session_id: int, *, home: Path | None = None, runner: Callable = _run) -> int:
    paths = _resolve_paths(session_id, home=home)
    if not _screen_exists(paths.screen_name, runner=runner):
        print(f"ERROR: session {paths.screen_name} not found", file=sys.stderr)
        return 1
    runner(["screen", "-S", paths.screen_name, "-X", "quit"])
    print(f"stopped session {paths.screen_name}")
    return 0


def _screen_exists(screen_name: str, *, runner: Callable) -> bool:
    result = runner(["screen", "-ls"], check=False)
    return screen_name in (result.stdout or "")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="devbench-session",
        description="Per-user multi-session devbench launcher.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    start = sub.add_parser("start", help="Clone main and launch detached screen.")
    start.add_argument("session_id", type=_validate_session_id)
    start.add_argument("--repo-url", default=DEFAULT_REPO_URL)
    start.add_argument("--branch", default=DEFAULT_BRANCH)
    start.add_argument("--mode", default=DEFAULT_MODE, choices=["interactive", "orchestrate"])

    attach = sub.add_parser("attach", help="Re-attach to a session.")
    attach.add_argument("session_id", type=_validate_session_id)

    sub.add_parser("list", help="List active sessions.")

    stop = sub.add_parser("stop", help="Stop a session.")
    stop.add_argument("session_id", type=_validate_session_id)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "start":
        return cmd_start(args.session_id, repo_url=args.repo_url, branch=args.branch, mode=args.mode)
    if args.cmd == "attach":
        return cmd_attach(args.session_id)
    if args.cmd == "list":
        return cmd_list()
    if args.cmd == "stop":
        return cmd_stop(args.session_id)
    raise AssertionError(f"argparse should have caught unknown subcommand {args.cmd!r}")


if __name__ == "__main__":
    sys.exit(main())
