"""Materialise a workspace-local shadow plugin directory for per-agent model overrides.

When operators set ``agents.<name>: <model>`` in ``devbench.yaml`` the canonical
marketplace plugin's agent ``.md`` files cannot be edited safely (the
marketplace install is shared and treated as immutable). Instead a shadow tree
is built under ``<workspace>/.devbench/plugin-shadow/devbench/`` that mirrors
the canonical plugin via symlinks for every file except the agent ``.md``
files whose model is overridden -- those are written as plain files with the
``model:`` frontmatter line rewritten to the operator-supplied value.

Both ``cmd_start`` (non-interactive, via ``ClaudeAgentOptions(plugins=...)``)
and ``devbench prepare-plugin-shadow`` (interactive, via
``claude --plugin-dir``) point at the same materialised path, so the override
behaviour is identical across modes.

The module is intentionally small and side-effect-isolated so 100% line +
branch coverage (Makefile :: ``test-coverage-new``) is achievable.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from devbench.constants import PLUGIN_SHADOW_DIR_NAME, SHADOW_PID_SENTINEL_FILENAME

if TYPE_CHECKING:
    from devbench.config_loader import AgentModelsConfig

# Top-level agent field-name (snake_case, matching AgentModelsConfig) ->
# relative path under the canonical plugin tree (kebab-case file names).
_AGENT_FILES: dict[str, str] = {
    "executor": "agents/executor.md",
    "blocker_resolver": "agents/blocker-resolver.md",
    "manifest_amender": "agents/manifest-amender.md",
    "security_reviewer": "agents/security-reviewer.md",
    "task_factory": "agents/task-factory.md",
    "review_supervisor": "agents/review-supervisor.md",
}

# Nested review_team field-name -> relative path under the canonical plugin tree.
_REVIEW_TEAM_FILES: dict[str, str] = {
    "code_reviewer": "agents/review_team/code-reviewer.md",
    "test_reviewer": "agents/review_team/test-reviewer.md",
    "doc_reviewer": "agents/review_team/doc-reviewer.md",
    "changes_manifest": "agents/review_team/changes-manifest.md",
}

# Matches the ``model: <value>`` line in an agent .md frontmatter block. The
# pattern is anchored at line start (MULTILINE) and consumes the value up to
# end-of-line; ``subn`` replaces only the first match so the file body cannot
# accidentally be mutated.
_MODEL_LINE_RE: re.Pattern[str] = re.compile(r"^model:[ \t]+\S.*$", re.MULTILINE)


def shadow_plugin_path(workspace_root: Path) -> Path:
    """Return the absolute path to the workspace's shadow plugin root.

    The path is deterministic; this function does not check whether the
    directory exists. The trailing ``/devbench`` segment mirrors the
    canonical layout (``plugin/devbench-orchestrate/.claude-plugin/plugin.json``) so the
    plugin loader finds the same metadata under both roots.

    Args:
        workspace_root: Workspace root (the directory that holds
            ``BACKLOG.md`` and ``.devbench/``).

    Returns:
        ``<workspace_root>/<PLUGIN_SHADOW_DIR_NAME>/devbench``.
    """
    return workspace_root / PLUGIN_SHADOW_DIR_NAME / "devbench"


def _sentinel_path(workspace_root: Path) -> Path:
    """Return the absolute path to the shadow plugin's PID sentinel file.

    Lives at ``<workspace_root>/<PLUGIN_SHADOW_DIR_NAME>/devbench/<SHADOW_PID_SENTINEL_FILENAME>``
    so a ``shutil.rmtree`` of the shadow tree removes the sentinel atomically.
    The path is deterministic; this function does not consult the filesystem.
    """
    return shadow_plugin_path(workspace_root) / SHADOW_PID_SENTINEL_FILENAME


def write_pid_sentinel(workspace_root: Path, pid: int) -> None:
    """Record the orchestrator PID that owns the materialised shadow plugin.

    Called by ``cmd_start`` immediately after ``materialise_shadow_plugin``
    returns a non-None path. Atomic: writes to ``.pid.tmp`` then renames over
    the target, so a concurrent reader of the sentinel always sees either the
    prior PID or the new PID, never a half-written value. The shadow root is
    assumed to exist (the materialiser created it).

    Args:
        workspace_root: Workspace whose shadow tree was just materialised.
        pid: Process id of the orchestrator that owns the shadow.

    Raises:
        FileNotFoundError: If the shadow plugin root does not exist (caller
            invoked the function before materialise_shadow_plugin).
    """
    sentinel = _sentinel_path(workspace_root)
    if not sentinel.parent.is_dir():
        raise FileNotFoundError(
            f"Cannot write PID sentinel: shadow plugin root '{sentinel.parent}' "
            "does not exist. Call materialise_shadow_plugin first."
        )
    tmp = sentinel.parent / (sentinel.name + ".tmp")
    tmp.write_text(str(pid), encoding="utf-8")
    tmp.replace(sentinel)


def _read_sentinel_pid(workspace_root: Path) -> int | None:
    """Return the PID stored in the shadow plugin's sentinel file, or None.

    Returns ``None`` when the sentinel does not exist (the common case before
    ``cmd_start`` has materialised a shadow or after a clean rebuild).

    Raises:
        ValueError: If the sentinel exists but does not contain a valid
            integer. Intentional fail-fast: a corrupt sentinel is a sign of
            file-system corruption or operator interference; we surface it
            rather than silently overwriting.
    """
    sentinel = _sentinel_path(workspace_root)
    if not sentinel.exists():
        return None
    raw = sentinel.read_text(encoding="utf-8").strip()
    return int(raw)


def _is_pid_alive(pid: int) -> bool:
    """Return True if *pid* names a live process on this host.

    Uses ``os.kill(pid, 0)`` which sends no signal but raises
    ``ProcessLookupError`` when the PID does not exist. A ``PermissionError``
    means the PID exists but is owned by a different uid -- still alive.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def clear_shadow_plugin(workspace_root: Path) -> bool:
    """Remove the shadow plugin tree under *workspace_root*.

    Idempotent: a second call after the tree is gone returns ``False`` and
    does not raise.

    Refuses to delete the tree when the recorded PID sentinel names a live
    process. Lets ``cmd_start`` rebuild its own shadow at startup (the
    materialise_shadow_plugin path clears + writes its own sentinel), but
    prevents a stray ``devbench prepare-plugin-shadow`` from clearing a
    running orchestrator's plugin files out from under it (the bug that
    silently stops hook telemetry mid-run).

    Args:
        workspace_root: Workspace root whose shadow tree should be removed.

    Returns:
        ``True`` when the shadow tree was present and removed,
        ``False`` when it did not exist.

    Raises:
        RuntimeError: When a sentinel PID is found AND that PID is alive.
            The caller is presumed to be a stray operator action; the
            recommended remedy is to stop the named orchestrator first.
        ValueError: When the sentinel exists but is corrupt (propagated
            from ``_read_sentinel_pid``).
    """
    shadow_root = workspace_root / PLUGIN_SHADOW_DIR_NAME
    if not shadow_root.exists():
        return False
    pid = _read_sentinel_pid(workspace_root)
    if pid is not None and _is_pid_alive(pid):
        raise RuntimeError(
            f"Refusing to clear shadow plugin at '{shadow_root}': "
            f"orchestrator process PID {pid} is alive and using it. "
            f"Stop the orchestrator first (e.g. send SIGTERM to PID {pid}) "
            "before clearing the shadow."
        )
    shutil.rmtree(shadow_root)
    return True


def _collect_overrides(agent_models: AgentModelsConfig) -> dict[str, str]:
    """Return a flat ``{relative_path: new_model}`` map of every override.

    Pure helper; reads only the dataclass. Returns an empty dict when every
    field is ``None`` (the "no overrides configured" case).
    """
    overrides: dict[str, str] = {}
    for field_name, rel_path in _AGENT_FILES.items():
        value: str | None = getattr(agent_models, field_name)
        if value is not None:
            overrides[rel_path] = value
    review_team = agent_models.review_team
    for field_name, rel_path in _REVIEW_TEAM_FILES.items():
        value = getattr(review_team, field_name)
        if value is not None:
            overrides[rel_path] = value
    return overrides


def _rewrite_agent_model(content: str, new_model: str) -> str:
    """Return *content* with the first ``model:`` line replaced by *new_model*.

    Pure function for testability. Fails fast (``ValueError``) if the source
    file has no ``model:`` frontmatter line -- the operator's override would
    otherwise be silently lost.

    Args:
        content: Full text of an agent ``.md`` file (frontmatter + body).
        new_model: Replacement value for the ``model:`` field.

    Returns:
        Rewritten content with exactly one substitution applied.

    Raises:
        ValueError: When the source contains no recognisable ``model:`` line.
    """
    rewritten, n = _MODEL_LINE_RE.subn(f"model: {new_model}", content, count=1)
    if n == 0:
        raise ValueError(
            "Agent markdown has no 'model:' frontmatter line to rewrite; "
            "shadow-plugin override cannot be applied. "
            "Verify the canonical plugin's agent file shape (---\\nmodel: <name>\\n---)."
        )
    return rewritten


def _atomic_write(target: Path, content: str) -> None:
    """Write *content* to *target* atomically (temp file + rename).

    The temp file lives next to *target* so the rename is on the same
    filesystem. ``os.replace`` is atomic on POSIX even when the destination
    already exists.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / (target.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(target)


def materialise_shadow_plugin(
    canonical_plugin_dir: Path,
    workspace_root: Path,
    agent_models: AgentModelsConfig,
) -> Path | None:
    """Build the workspace-local shadow plugin and return its path.

    Walks *canonical_plugin_dir* recursively. Each file is mirrored into the
    same relative position under ``shadow_plugin_path(workspace_root)``:

    * Files whose relative path matches an overridden agent are copied as
      plain files with the ``model:`` frontmatter line rewritten.
    * Every other file is symlinked back to the canonical location so the
      shadow stays cheap to build and impossible to drift content-wise.

    The shadow tree is rebuilt from scratch on every call (cheap because it
    is mostly symlinks) which guarantees it exactly matches *agent_models*
    and never lingers stale after the operator removes the YAML section.

    When *agent_models* has no overrides, any existing shadow tree is
    removed and ``None`` is returned so callers fall back to the canonical
    plugin path.

    Args:
        canonical_plugin_dir: Absolute path to the canonical
            ``plugin/devbench`` tree shipped with this package.
        workspace_root: Workspace root whose ``.devbench/`` directory will
            hold the shadow.
        agent_models: Per-agent overrides from ``devbench.yaml`` (after
            ``config.py`` has merged ``JUDGE_AGENT_MODEL_*`` env vars).

    Returns:
        Absolute path to the shadow plugin root, or ``None`` when no
        overrides are configured.

    Raises:
        FileNotFoundError: If *canonical_plugin_dir* does not exist.
        ValueError: If an overridden agent file has no ``model:`` line, or
            if an override targets a relative path that does not exist
            under *canonical_plugin_dir* (typo / structural drift).
    """
    if not canonical_plugin_dir.is_dir():
        raise FileNotFoundError(
            f"Canonical plugin directory not found at '{canonical_plugin_dir}'. "
            f"Cannot materialise shadow plugin for workspace '{workspace_root}'."
        )

    overrides = _collect_overrides(agent_models)
    if not overrides:
        clear_shadow_plugin(workspace_root)
        return None

    # Verify every overridden path exists in the canonical tree BEFORE
    # touching the filesystem. Fail fast on typos / structural drift.
    for rel_path in overrides:
        if not (canonical_plugin_dir / rel_path).is_file():
            raise ValueError(
                f"Per-agent override targets '{rel_path}' which does not exist "
                f"under canonical plugin '{canonical_plugin_dir}'. The plugin "
                "layout has changed; update _AGENT_FILES / _REVIEW_TEAM_FILES "
                "in devbench.plugin_shadow."
            )

    clear_shadow_plugin(workspace_root)
    shadow_root = shadow_plugin_path(workspace_root)
    shadow_root.mkdir(parents=True, exist_ok=True)

    for src in canonical_plugin_dir.rglob("*"):
        rel = src.relative_to(canonical_plugin_dir)
        dest = shadow_root / rel
        if src.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        rel_str = str(rel)
        if rel_str in overrides:
            content = src.read_text(encoding="utf-8")
            _atomic_write(dest, _rewrite_agent_model(content, overrides[rel_str]))
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to(src)

    return shadow_root
