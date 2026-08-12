"""Per-host devbench orchestrator instance discovery + lifecycle (#209).

Each daemon-mode (and foreground) ``devbench start`` writes a PID file
at ``<workspace>/.devbench/orchestrator.pid`` containing JSON metadata.
The helpers here read / write / walk those files for instance
enumeration and targeted lifecycle commands (``devbench instances``,
``devbench stop <id>``, ``devbench drain <id>``, ``devbench restart <id>``,
``devbench tail <id>``, ``devbench status <id>``).

Instance ID format: ``<workspace_basename>-<pid-suffix>`` (e.g.
``kanon-deps-work-2281``).  Operators can also pass the raw PID to any
lifecycle command; ``resolve_instance`` handles both forms.

PID files are best-effort metadata -- a missing / corrupt / non-object
payload is treated as no instance; a stale entry (PID dead) is filtered
out by the liveness check.  No correctness gate depends on the PID
file being present.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from devbench.utils.io import atomic_write_text

ORCHESTRATOR_PID_FILENAME = "orchestrator.pid"
INSTANCE_SEARCH_ROOTS_ENV = "DEVBENCH_INSTANCE_SEARCH_ROOTS"


@dataclass(frozen=True)
class Instance:
    """One running devbench orchestrator, as discovered from a PID file."""

    instance_id: str
    pid: int
    workspace: str
    workspace_name: str
    session: str
    mode: str  # "daemon" | "foreground"
    started_at: str  # ISO-8601 UTC
    model: str
    pid_file: str


def make_instance_id(workspace: Path, pid: int) -> str:
    """Return ``<workspace_basename>-<last-4-digits-of-pid>``.

    Short enough for operators to type but stable per-(workspace, pid)
    so two orchestrators on the same host never collide on id.  Two
    distinct workspaces hosting orchestrators that happen to share the
    same pid suffix still differ on the basename prefix.
    """
    suffix = f"{pid:04d}"[-4:]
    return f"{workspace.name}-{suffix}"


def pid_file_path(workspace: Path) -> Path:
    """Return the canonical PID file path for *workspace*."""
    return workspace / ".devbench" / ORCHESTRATOR_PID_FILENAME


def write_pid_file(
    workspace: Path,
    pid: int,
    *,
    session: str = "default",
    mode: str = "daemon",
    model: str = "",
) -> Path:
    """Atomic-write the PID file with instance metadata."""
    pid_path = pid_file_path(workspace)
    payload = {
        "instance_id": make_instance_id(workspace, pid),
        "pid": pid,
        "workspace": str(workspace),
        "workspace_name": workspace.name,
        "session": session,
        "mode": mode,
        "started_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": model,
        "host": socket.gethostname(),
    }
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(pid_path, json.dumps(payload, indent=2, sort_keys=True))
    return pid_path


def read_pid_file(pid_path: Path) -> Instance | None:
    """Parse one PID file into an :class:`Instance`.

    Returns ``None`` when the file is missing / not JSON / wrong shape.
    """
    if not pid_path.is_file():
        return None
    try:
        data = json.loads(pid_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return Instance(
            instance_id=str(data["instance_id"]),
            pid=int(data["pid"]),
            workspace=str(data["workspace"]),
            workspace_name=str(data.get("workspace_name") or Path(str(data["workspace"])).name),
            session=str(data.get("session", "default")),
            mode=str(data.get("mode", "daemon")),
            started_at=str(data.get("started_at", "")),
            model=str(data.get("model", "")),
            pid_file=str(pid_path),
        )
    except (KeyError, TypeError, ValueError):
        return None


def is_pid_alive(pid: int) -> bool:
    """Return True iff *pid* refers to a live process.

    Uses ``os.kill(pid, 0)`` -- sends no signal, only checks the kernel's
    process table.  ``PermissionError`` means the process exists but
    signaling is denied (still alive).  Any other error means dead.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _resolve_search_roots(roots: list[Path] | None) -> list[Path]:
    """Resolve the effective search roots for instance discovery (obs-spec FR-D2).

    Three-tier resolution:

    1. Explicit *roots* (caller-supplied) always wins, unchanged.
    2. ``DEVBENCH_INSTANCE_SEARCH_ROOTS`` (colon-separated), when set, is
       returned verbatim -- this precedence is byte-identical to the
       pre-existing behavior and MUST NOT change (obs-spec OAC-3).
    3. Otherwise the default is ``$HOME`` plus the current
       ``DEVBENCH_WORKSPACE_ROOT`` (when set and not already under
       ``$HOME``), so `devbench instances` finds a workspace's own daemon
       without requiring the env-var override (obs-spec B-2 / OD-2).

    A workspace root that does not exist on disk is not special-cased
    here; it falls through the existing ``root.is_dir()`` guard in
    :func:`discover_instances` exactly as any other nonexistent root does.
    """
    if roots is not None:
        return roots
    env = os.environ.get(INSTANCE_SEARCH_ROOTS_ENV, "")
    if env:
        return [Path(p).expanduser() for p in env.split(":") if p]
    home = Path.home()
    default_roots = [home]
    workspace_root = os.environ.get("DEVBENCH_WORKSPACE_ROOT", "")
    if workspace_root and not Path(workspace_root).is_relative_to(home):
        default_roots.append(Path(workspace_root))
    return default_roots


def discover_instances(search_roots: list[Path] | None = None) -> list[Instance]:
    """Walk *search_roots* for orchestrator PID files; return live instances.

    Permission-denied directories are skipped silently.  Stale PID
    files (dead processes) are filtered out.  De-duplicates by PID
    in case a PID file appears under two search roots.
    """
    roots = _resolve_search_roots(search_roots)
    instances: list[Instance] = []
    seen_pids: set[int] = set()
    for root in roots:
        if not root.is_dir():
            continue
        try:
            iterator = root.rglob(f".devbench/{ORCHESTRATOR_PID_FILENAME}")
        except (OSError, PermissionError):
            continue
        for pid_file in iterator:
            try:
                inst = read_pid_file(pid_file)
            except (OSError, PermissionError):
                continue
            if inst is None or not is_pid_alive(inst.pid) or inst.pid in seen_pids:
                continue
            seen_pids.add(inst.pid)
            instances.append(inst)
    return instances


def resolve_instance(token: str, search_roots: list[Path] | None = None) -> Instance | None:
    """Resolve *token* (instance_id or raw PID) to an :class:`Instance`.

    Returns ``None`` if no live instance matches.  Operators can pass
    either form to every lifecycle command.
    """
    instances = discover_instances(search_roots)
    for inst in instances:
        if inst.instance_id == token:
            return inst
    try:
        pid = int(token)
    except ValueError:
        return None
    for inst in instances:
        if inst.pid == pid:
            return inst
    return None


def remove_pid_file(workspace: Path) -> None:
    """Best-effort delete of the workspace's PID file.

    Called from the orchestrator's clean-exit path so a fresh start
    doesn't trip the alive-check on a stale entry.  Missing-file and
    permission-denied are non-fatal.
    """
    import contextlib

    pid_path = pid_file_path(workspace)
    with contextlib.suppress(FileNotFoundError, PermissionError, OSError):
        pid_path.unlink()
