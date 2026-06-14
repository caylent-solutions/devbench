"""Materialise a workspace-local shadow plugin directory for per-agent model overrides.

When operators set ``agents.<name>: <model>`` in ``devbench.yaml`` the canonical
marketplace plugin's agent ``.md`` files cannot be edited safely (the
marketplace install is shared and treated as immutable). Instead a shadow tree
is built under ``<workspace>/.devbench/plugin-shadow/devbench/`` that mirrors
the canonical plugin. Every agent ``.md`` file (anything under ``agents/``) is
materialised as a plain real file -- copied verbatim when its model is not
overridden, or with the ``model:`` frontmatter line rewritten to the
operator-supplied value when it is. Every non-agent file is symlinked back to
the canonical location so the shadow stays cheap to build.

Materialising *all* agent files as real files (rather than only the overridden
ones) is required because the Claude Agent SDK discovers subagents by walking
the plugin tree on disk: a symlinked agent ``.md`` is not registered as a
dispatchable agent type, so any agent left as a symlink silently disappears
from the session. Copying every agent file guarantees the full agent roster
registers regardless of which (if any) models are overridden, and is robust to
agents added to the canonical plugin in future without touching this module.

Both ``cmd_start`` (non-interactive, via ``ClaudeAgentOptions(plugins=...)``)
and ``devbench prepare-plugin-shadow`` (interactive, via
``claude --plugin-dir``) point at the same materialised path, so the override
behaviour is identical across modes.

The module is intentionally small and side-effect-isolated so 100% line +
branch coverage (Makefile :: ``test-coverage-new``) is achievable.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from devbench.constants import (
    PLUGIN_SHADOW_DIR_NAME,
    SHADOW_OVERRIDES_FINGERPRINT_FILENAME,
    SHADOW_PID_SENTINEL_FILENAME,
)

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
    "iac_deploy_reviewer": "agents/iac-deploy-reviewer.md",
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

# Relative-path prefix (POSIX separators, as produced by ``str(PurePosixPath)``
# / ``str(Path)`` on the POSIX hosts this module targets) identifying agent
# definition files inside the canonical plugin tree.
_AGENTS_DIR_PREFIX = "agents/"


def _is_agent_markdown(rel_str: str) -> bool:
    """Return True when *rel_str* names an agent definition ``.md`` file.

    Any markdown file under ``agents/`` (including nested ``agents/review_team/``)
    is an agent definition that the Claude Agent SDK must discover as a real
    file. Non-markdown files under ``agents/`` and every file outside ``agents/``
    are not agent definitions and stay symlinked.

    Args:
        rel_str: File path relative to the canonical plugin root, using ``/``
            separators (``agents/executor.md``,
            ``agents/review_team/code-reviewer.md``).

    Returns:
        ``True`` when the path is an agent ``.md`` file, ``False`` otherwise.
    """
    return rel_str.startswith(_AGENTS_DIR_PREFIX) and rel_str.endswith(".md")


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
    """Return the absolute path to the shadow plugin's owner-PID sentinel file.

    Lives at ``<workspace_root>/<PLUGIN_SHADOW_DIR_NAME>/devbench/<SHADOW_PID_SENTINEL_FILENAME>``
    so a ``shutil.rmtree`` of the shadow tree removes the sentinel atomically.
    The path is deterministic; this function does not consult the filesystem.
    """
    return shadow_plugin_path(workspace_root) / SHADOW_PID_SENTINEL_FILENAME


def _fingerprint_path(workspace_root: Path) -> Path:
    """Return the absolute path to the shadow plugin's overrides-fingerprint file.

    Lives next to the owner sentinel inside the shadow tree so a clean rebuild
    (``shutil.rmtree``) removes it atomically. The path is deterministic; this
    function does not consult the filesystem.
    """
    return shadow_plugin_path(workspace_root) / SHADOW_OVERRIDES_FINGERPRINT_FILENAME


def _overrides_fingerprint(overrides: dict[str, str]) -> str:
    """Return a stable hash of the per-agent override map.

    Pure function: deterministic, no I/O, order-independent. Two materialise
    calls with the same set of ``{relative_path: model}`` overrides produce the
    same fingerprint, so an already-built shadow can be recognised as
    up-to-date and REUSED instead of cleared and rebuilt. Returns the full
    SHA-256 hex digest of the canonical JSON encoding (sorted keys).
    """
    canonical = json.dumps(overrides, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _write_fingerprint(workspace_root: Path, fingerprint: str) -> None:
    """Record *fingerprint* inside the shadow tree atomically (temp + rename).

    The shadow root is assumed to exist (the materialiser created it).
    """
    target = _fingerprint_path(workspace_root)
    tmp = target.parent / (target.name + ".tmp")
    tmp.write_text(fingerprint, encoding="utf-8")
    tmp.replace(target)


def _read_fingerprint(workspace_root: Path) -> str | None:
    """Return the recorded overrides fingerprint, or ``None`` when absent.

    ``None`` covers a shadow built by a pre-fingerprint devbench build (no
    marker yet); callers treat that as "fingerprint unknown -> not a safe
    reuse" and fall through to the clear+rebuild path (which is itself owner-
    guarded), so the missing marker can never silently reuse a stale tree.
    """
    target = _fingerprint_path(workspace_root)
    if not target.exists():
        return None
    return target.read_text(encoding="utf-8").strip()


def write_pid_sentinel(workspace_root: Path, pid: int) -> None:
    """Register *pid* as an additional owner of the materialised shadow plugin.

    Called by ``cmd_start`` (and by ``materialise_shadow_plugin`` on the reuse
    path) so every concurrent orchestrator that shares one identical shadow is
    recorded as an owner. Multi-owner: the sentinel holds the SET of owning
    PIDs, one per line. Dead PIDs already present in the sentinel are pruned
    before this PID is added, so the file never grows unbounded across runs.
    Atomic: writes to ``.pid.tmp`` then renames over the target, so a
    concurrent reader always sees a consistent owner set, never a half-written
    value. The shadow root is assumed to exist (the materialiser created it).

    Args:
        workspace_root: Workspace whose shadow tree was just materialised.
        pid: Process id of the orchestrator registering as an owner.

    Raises:
        FileNotFoundError: If the shadow plugin root does not exist (caller
            invoked the function before materialise_shadow_plugin).
    """
    sentinel = _sentinel_path(workspace_root)
    if not sentinel.parent.is_dir():
        raise FileNotFoundError(
            f"Cannot write owner sentinel: shadow plugin root '{sentinel.parent}' "
            "does not exist. Call materialise_shadow_plugin first."
        )
    owners = {owner for owner in _read_owner_pids(workspace_root) if _is_pid_alive(owner)}
    owners.add(pid)
    tmp = sentinel.parent / (sentinel.name + ".tmp")
    tmp.write_text("\n".join(str(owner) for owner in sorted(owners)) + "\n", encoding="utf-8")
    tmp.replace(sentinel)


def _read_owner_pids(workspace_root: Path) -> set[int]:
    """Return the set of owner PIDs recorded in the shadow plugin's sentinel.

    Returns an empty set when the sentinel does not exist (the common case
    before ``cmd_start`` has materialised a shadow or after a clean rebuild).

    Raises:
        ValueError: If the sentinel exists but contains a line that is not a
            valid integer. Intentional fail-fast: a corrupt sentinel is a sign
            of file-system corruption or operator interference; we surface it
            rather than silently overwriting.
    """
    sentinel = _sentinel_path(workspace_root)
    if not sentinel.exists():
        return set()
    raw = sentinel.read_text(encoding="utf-8")
    return {int(line.strip()) for line in raw.splitlines() if line.strip()}


def _live_owner_pids(workspace_root: Path) -> set[int]:
    """Return the subset of recorded owner PIDs that name a live process.

    Dead PIDs are pruned. Propagates ``ValueError`` from ``_read_owner_pids``
    on a corrupt sentinel.
    """
    return {pid for pid in _read_owner_pids(workspace_root) if _is_pid_alive(pid)}


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

    Refuses to delete the tree when ANY recorded owner PID names a live
    process. Lets an operator ``clear`` / ``prepare-plugin-shadow`` rebuild a
    shadow nobody owns, but prevents a stray invocation from clearing a
    running orchestrator's plugin files out from under it (the bug that
    silently stops hook telemetry mid-run) -- and prevents a second concurrent
    session's rebuild from yanking the shadow away from a live sibling.

    Args:
        workspace_root: Workspace root whose shadow tree should be removed.

    Returns:
        ``True`` when the shadow tree was present and removed,
        ``False`` when it did not exist.

    Raises:
        RuntimeError: When one or more recorded owner PIDs are alive. The
            caller is presumed to be a stray clear; the recommended remedy is
            to stop the named orchestrator(s) first.
        ValueError: When the sentinel exists but is corrupt (propagated
            from ``_read_owner_pids``).
    """
    shadow_root = workspace_root / PLUGIN_SHADOW_DIR_NAME
    if not shadow_root.exists():
        return False
    live = _live_owner_pids(workspace_root)
    if live:
        pids = ", ".join(str(pid) for pid in sorted(live))
        raise RuntimeError(
            f"Refusing to clear shadow plugin at '{shadow_root}': "
            f"orchestrator process(es) PID {pids} are alive and using it. "
            f"Stop the orchestrator(s) first (e.g. send SIGTERM to PID {pids}) "
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

    * Every agent ``.md`` file (any file under ``agents/`` ending in ``.md``)
      is materialised as a plain real file -- with the ``model:`` frontmatter
      line rewritten when that agent's model is overridden, or copied verbatim
      otherwise. Agent files must be real files (not symlinks) because the
      Claude Agent SDK does not register a symlinked agent ``.md`` as a
      dispatchable agent type.
    * Every non-agent file is symlinked back to the canonical location so the
      shadow stays cheap to build and impossible to drift content-wise.

    Reentrant / idempotent across concurrent sessions. The shadow content is
    a pure function of the canonical plugin + the requested overrides, so all
    sessions on one workspace want the SAME shadow:

    * If an up-to-date shadow already exists (its recorded overrides
      fingerprint matches the requested overrides), REUSE it: register this
      process as an additional owner, skip the clear+rebuild, and return the
      existing path. A live sibling owner is therefore shared, not clobbered.
    * If a shadow exists but its fingerprint DIFFERS and a live owner holds
      it, fail fast (``RuntimeError``) naming the owner(s) -- a rebuild would
      yank the plugin files out from under the running sibling.
    * Otherwise (no shadow, or a stale shadow with no live owner) clear +
      rebuild from scratch (cheap because it is mostly symlinks), write the
      fingerprint, register this process as the owner, and return the path.

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
        RuntimeError: If a stale shadow (overrides differ) is held by a live
            owner -- rebuilding would clobber the running sibling.
        ValueError: If an overridden agent file has no ``model:`` line, or
            if an override targets a relative path that does not exist
            under *canonical_plugin_dir* (typo / structural drift), or if the
            owner sentinel is corrupt.
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

    shadow_root = shadow_plugin_path(workspace_root)
    fingerprint = _overrides_fingerprint(overrides)

    # Reuse path: an up-to-date shadow already exists. Register this process
    # as an additional owner and return without clearing or rebuilding so a
    # live sibling session is shared rather than clobbered.
    if shadow_root.exists() and _read_fingerprint(workspace_root) == fingerprint:
        write_pid_sentinel(workspace_root, os.getpid())
        return shadow_root

    # Stale shadow held by a live owner: a rebuild would yank the plugin files
    # out from under the running sibling. Fail fast naming the owner(s).
    if shadow_root.exists():
        live = _live_owner_pids(workspace_root)
        if live:
            pids = ", ".join(str(pid) for pid in sorted(live))
            raise RuntimeError(
                f"Refusing to rebuild shadow plugin at '{shadow_root}': the requested "
                f"per-agent model overrides differ from the running session(s) "
                f"(owner PID {pids}), and rebuilding would clobber the shadow they "
                f"are using. Stop the orchestrator(s) (e.g. send SIGTERM to PID {pids}), "
                "or launch the new session with matching agents.* overrides."
            )

    clear_shadow_plugin(workspace_root)
    shadow_root.mkdir(parents=True, exist_ok=True)

    for src in canonical_plugin_dir.rglob("*"):
        rel = src.relative_to(canonical_plugin_dir)
        dest = shadow_root / rel
        if src.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        rel_str = str(rel)
        if _is_agent_markdown(rel_str):
            content = src.read_text(encoding="utf-8")
            if rel_str in overrides:
                content = _rewrite_agent_model(content, overrides[rel_str])
            _atomic_write(dest, content)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to(src)

    _write_fingerprint(workspace_root, fingerprint)
    write_pid_sentinel(workspace_root, os.getpid())
    return shadow_root
