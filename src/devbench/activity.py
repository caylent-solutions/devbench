"""Live activity dashboard reader for the ``devbench watch`` command.

This module is pure read-only: it inspects on-disk state (hook logs, the
orchestrator log, Claude Code session transcripts, the target repository's
git tree, and any pending amendment request) and returns a typed snapshot
plus a terminal-friendly rendering of the currently-active orchestration.

No mutation anywhere -- every subprocess call is a known read-only ``git``
command, every file is opened in read mode, and every walk is bounded in
size. Safe to run concurrently with an active orchestrator.

Design principles:

- **One-shot by default, watch mode optional.** The CLI entry (``cmd_watch``
  in :mod:`devbench.cli`) calls :func:`collect_snapshot` plus
  :func:`render_snapshot` once and exits. ``--watch N`` polls on a fixed
  cadence; the polling lives in the CLI, not here.
- **Deterministic parsing with fail-open tolerance.** Malformed JSONL lines
  are skipped silently so a partially-flushed log does not crash the
  dashboard. Missing files render as blank sections, not errors.
- **Typed dataclasses everywhere.** The snapshot is a frozen dataclass so
  the renderer is a pure function of its input; the same snapshot always
  produces the same output.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from stat import S_ISREG
from typing import TYPE_CHECKING, Any

from devbench.constants import DEFAULT_COMMAND_TIMEOUT

if TYPE_CHECKING:
    from devbench.config_loader import RuntimeConfig

logger = logging.getLogger("devbench.activity")


MAX_AGENT_TEXT_CHARS: int = 500

MAX_TOOL_SUMMARY_CHARS: int = 80

DEFAULT_MAX_TOOLS: int = 5

DEFAULT_MAX_CLI_EVENTS: int = 3

IDLE_THRESHOLD_SECONDS: int = 30

DEFAULT_GIT_READ_TIMEOUT: int = DEFAULT_COMMAND_TIMEOUT

AMENDMENT_DIR_NAME: str = ".devbench/amendments"


@dataclass(frozen=True)
class ToolCallEvent:
    """One tool invocation extracted from the active subagent's transcript."""

    at: datetime
    tool: str
    summary: str


@dataclass(frozen=True)
class SubagentActivity:
    """The subset of a subagent transcript surfaced in the dashboard."""

    transcript_path: Path | None
    subagent_type: str | None
    latest_text: str | None
    recent_tools: list[ToolCallEvent] = field(default_factory=list)
    last_activity_at: datetime | None = None


@dataclass(frozen=True)
class CliEvent:
    """One log line emitted by the ``devbench.cli`` logger."""

    at: datetime
    message: str


@dataclass(frozen=True)
class RepoState:
    """git status summary for the active work unit's repo."""

    repo_path: Path | None
    staged: list[str] = field(default_factory=list)
    unstaged: list[str] = field(default_factory=list)
    untracked: list[str] = field(default_factory=list)
    head_sha: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class AmendmentState:
    """Pending amendment request summary."""

    task_id: str
    exists: bool
    reason: str | None = None
    files_to_add: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ActivitySnapshot:
    """Typed snapshot of the currently-active orchestration."""

    now: datetime
    mode_label: str
    active_task_id: str | None
    active_task_title: str | None
    active_task_status: str | None
    claimed_at: datetime | None
    phase: str
    last_tool_call_at: datetime | None
    subagent: SubagentActivity | None
    recent_cli: list[CliEvent]
    repo_state: RepoState | None
    amendment: AmendmentState | None
    idle_seconds: int


def discover_session_dir(hook_log_path: Path) -> Path | None:
    """Return the Claude Code session directory inferred from the hook log.

    Each PostToolUse hook entry carries ``input.transcript_path`` pointing to
    the active session's ``~/.claude/projects/<slug>/<session-id>.jsonl``
    file; the parent directory holds every transcript for that workspace.
    Returns ``None`` when the hook log is missing, empty, or contains no
    entry with a valid ``transcript_path``.

    Tolerant of partial lines: JSONDecodeError on any one entry is skipped
    rather than raised, so a hook log flushed mid-write still reads cleanly.
    """
    if not hook_log_path.is_file():
        return None
    for line in hook_log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        input_block = entry.get("input")
        if not isinstance(input_block, dict):
            continue
        path_str = input_block.get("transcript_path")
        if isinstance(path_str, str) and path_str:
            return Path(path_str).parent
    return None


def find_active_subagent(session_dir: Path) -> Path | None:
    """Return the most-recently-modified ``agent-*.jsonl`` file, or ``None``.

    Scans the ``<session_dir>/subagents/`` directory (created by Claude Code
    for every Agent tool invocation). Uses mtime rather than content parsing
    so concurrent writes do not produce a partial-read crash.

    Returns ``None`` when the directory does not exist or contains no
    matching files.
    """
    subagents_dir = session_dir / "subagents"
    if not subagents_dir.is_dir():
        return None
    candidates: list[tuple[float, Path]] = []
    for path in subagents_dir.iterdir():
        if not path.name.startswith("agent-") or path.suffix != ".jsonl":
            continue
        try:
            stat_result = path.stat()
        except OSError:
            continue
        if not S_ISREG(stat_result.st_mode):
            continue
        candidates.append((stat_result.st_mtime, path))
    if not candidates:
        return None
    candidates.sort(key=lambda kv: kv[0], reverse=True)
    return candidates[0][1]


def _parse_iso_timestamp(raw: str) -> datetime | None:
    """Parse an ISO-8601 timestamp, returning ``None`` on failure."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


_TOOL_SUMMARY_FIELDS: dict[str, tuple[str, ...]] = {
    "Bash": ("command",),
    "Read": ("file_path",),
    "Write": ("file_path",),
    "Edit": ("file_path",),
    "Glob": ("pattern",),
}


def _extract_tool_summary(tool: str, tool_input: Any) -> str:
    """Render a short human-readable summary of a tool invocation."""
    if not isinstance(tool_input, dict):
        return ""
    fields = _TOOL_SUMMARY_FIELDS.get(tool)
    if fields is not None:
        joined = " ".join(str(tool_input.get(f, "")) for f in fields).strip()
        return _truncate(joined, MAX_TOOL_SUMMARY_CHARS)
    if tool == "Grep":
        pattern = str(tool_input.get("pattern", ""))
        path = str(tool_input.get("path") or "")
        return _truncate(f"{pattern} @ {path}" if path else pattern, MAX_TOOL_SUMMARY_CHARS)
    try:
        return _truncate(json.dumps(tool_input, separators=(",", ":")), MAX_TOOL_SUMMARY_CHARS)
    except (TypeError, ValueError):
        return ""


def _truncate(text: str, limit: int) -> str:
    """Return ``text`` trimmed to ``limit`` characters with a continuation marker."""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _extract_latest_text_from_entry(entry: dict) -> str | None:
    """Return the most recent text content fragment from one transcript entry, or ``None``.

    The Claude Code transcript format nests a list of content blocks under
    ``message.content``. Text-only responses (assistant thinking or a
    narration turn) produce blocks whose ``type`` is ``"text"`` and whose
    ``text`` is a string. We return the concatenated text from every
    text block in this entry so mid-thought continuations are preserved.
    """
    message = entry.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content or None
    if not isinstance(content, list):
        return None
    pieces: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            pieces.append(text)
    if not pieces:
        return None
    return "\n".join(pieces)


def _extract_tools_from_entry(entry: dict, at: datetime | None) -> list[ToolCallEvent]:
    """Return every ``tool_use`` content block from one transcript entry."""
    if at is None:
        return []
    message = entry.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    tools: list[ToolCallEvent] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_use":
            continue
        name = block.get("name")
        if not isinstance(name, str):
            continue
        summary = _extract_tool_summary(name, block.get("input"))
        tools.append(ToolCallEvent(at=at, tool=name, summary=summary))
    return tools


def _coerce_transcript_timestamp(entry: dict) -> datetime | None:
    """Return the most specific timestamp available for one transcript entry.

    Claude Code writes ``timestamp`` at the top level on every line. Older
    formats also write ``message.timestamp``; fall back gracefully.
    """
    top = entry.get("timestamp")
    if isinstance(top, str):
        parsed = _parse_iso_timestamp(top)
        if parsed is not None:
            return parsed
    message = entry.get("message")
    if isinstance(message, dict):
        nested = message.get("timestamp")
        if isinstance(nested, str):
            parsed = _parse_iso_timestamp(nested)
            if parsed is not None:
                return parsed
    return None


def parse_subagent_recent_activity(
    transcript_path: Path,
    *,
    max_tools: int = DEFAULT_MAX_TOOLS,
) -> SubagentActivity:
    """Read the last chunks of ``transcript_path`` and extract display signals.

    Returns a :class:`SubagentActivity` with the most recent ``text`` block
    content and the last ``max_tools`` ``tool_use`` events. Malformed lines
    are skipped so an actively-written transcript is never a crash source.
    """
    if not transcript_path.is_file():
        return SubagentActivity(transcript_path=transcript_path, subagent_type=None, latest_text=None)

    latest_text: str | None = None
    tools: list[ToolCallEvent] = []
    subagent_type: str | None = None
    last_activity_at: datetime | None = None

    for raw_line in transcript_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            entry = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue

        at = _coerce_transcript_timestamp(entry)
        if at is not None and (last_activity_at is None or at > last_activity_at):
            last_activity_at = at

        raw_type = entry.get("subagent_type") or entry.get("agentType") or entry.get("agent_type")
        if isinstance(raw_type, str) and raw_type:
            subagent_type = raw_type

        text = _extract_latest_text_from_entry(entry)
        if text is not None:
            latest_text = text

        tools.extend(_extract_tools_from_entry(entry, at))

    tools = tools[-max_tools:]

    truncated_text = _truncate(latest_text, MAX_AGENT_TEXT_CHARS) if latest_text is not None else None

    return SubagentActivity(
        transcript_path=transcript_path,
        subagent_type=subagent_type,
        latest_text=truncated_text,
        recent_tools=tools,
        last_activity_at=last_activity_at,
    )


def parse_orchestrator_recent_cli(
    log_path: Path,
    *,
    max_entries: int = DEFAULT_MAX_CLI_EVENTS,
) -> list[CliEvent]:
    """Return the most recent ``max_entries`` log lines emitted by the ``devbench.cli`` logger.

    The orchestrator log is written by :mod:`devbench.log_setup` with a
    fixed format: ``"YYYY-MM-DDTHH:MM:SSZ [logger.name] LEVEL message"``.
    We parse those four fields and keep only the rows whose logger name
    starts with ``devbench.cli`` so unrelated chatter from judge agents,
    backlog mutations, and the SDK is excluded from this panel.
    """
    if not log_path.is_file():
        return []

    kept: list[CliEvent] = []
    for raw_line in log_path.read_text(encoding="utf-8").splitlines():
        event = _parse_log_line(raw_line)
        if event is None:
            continue
        kept.append(event)

    return kept[-max_entries:] if max_entries > 0 else []


_LOG_LEVELS_WITH_SPACE: tuple[str, ...] = tuple(
    f"{level} " for level in ("INFO", "WARNING", "ERROR", "DEBUG", "CRITICAL")
)


def _parse_log_line(line: str) -> CliEvent | None:
    """Parse one orchestrator log line into a :class:`CliEvent`, or ``None`` if unrecognised."""
    logger_name, rest, at = _split_log_line(line)
    if logger_name != "devbench.cli" or at is None or rest is None:
        return None
    for prefix in _LOG_LEVELS_WITH_SPACE:
        if rest.startswith(prefix):
            rest = rest[len(prefix) :]
            break
    return CliEvent(at=at, message=rest.strip())


def _split_log_line(line: str) -> tuple[str | None, str | None, datetime | None]:
    """Split one ``orchestrator.log`` line into ``(logger_name, remainder, ts)``.

    Returns a tuple of (``None``, ``None``, ``None``) when the line does not
    match the ``"YYYY-MM-DDTHH:MM:SSZ [logger] ..."`` format.
    """
    if not line or " " not in line:
        return None, None, None
    ts_raw, rest = line.split(" ", 1)
    at = _parse_iso_timestamp(ts_raw.rstrip("Z") + "Z")
    if at is None or not rest.startswith("["):
        return None, None, None
    close = rest.find("]")
    if close < 0:
        return None, None, None
    return rest[1:close], rest[close + 1 :].lstrip(), at


def parse_repo_state(repo_path: Path, *, timeout: int = DEFAULT_GIT_READ_TIMEOUT) -> RepoState:
    """Return a :class:`RepoState` for the repo at ``repo_path``.

    Shells out to ``git status --porcelain=v1`` and ``git rev-parse HEAD``
    inside ``repo_path``. Both commands are strictly read-only. Missing or
    non-git directories return a :class:`RepoState` with ``error`` set and
    empty lists.
    """
    if not repo_path.is_dir():
        return RepoState(repo_path=repo_path, error=f"repo path {repo_path} does not exist")

    status_rc, status_out = _run_git(repo_path, ["status", "--porcelain=v1"], timeout=timeout)
    if status_rc != 0:
        return RepoState(repo_path=repo_path, error=f"git status returned {status_rc}")

    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    for line in status_out.splitlines():
        if len(line) < 3:
            continue
        xy = line[:2]
        path = line[3:].strip()
        if xy == "??":
            untracked.append(path)
            continue
        x, y = xy[0], xy[1]
        if x not in {" ", "?"}:
            staged.append(path)
        if y not in {" ", "?"}:
            unstaged.append(path)

    head_rc, head_out = _run_git(repo_path, ["rev-parse", "HEAD"], timeout=timeout)
    head_sha = head_out.strip() if head_rc == 0 else None

    return RepoState(
        repo_path=repo_path,
        staged=staged,
        unstaged=unstaged,
        untracked=untracked,
        head_sha=head_sha,
    )


def _run_git(repo_path: Path, args: list[str], *, timeout: int) -> tuple[int, str]:
    """Run ``git -C <repo_path> <args...>`` with a fixed argv; return ``(rc, stdout)``."""
    cmd = ["git", "-C", str(repo_path), *args]
    try:
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 1, ""
    except FileNotFoundError:
        return 1, ""
    return result.returncode, result.stdout or ""


def check_amendment_request(workspace_root: Path, task_id: str) -> AmendmentState:
    """Return the pending amendment request for ``task_id``, or a stub state.

    Reads ``<workspace_root>/.devbench/amendments/<task_id>.json`` and
    returns a :class:`AmendmentState` with the summary fields needed by
    the dashboard. When the file is missing, ``exists`` is ``False``.
    """
    target = workspace_root / AMENDMENT_DIR_NAME / f"{task_id}.json"
    if not target.is_file():
        return AmendmentState(task_id=task_id, exists=False)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AmendmentState(task_id=task_id, exists=True, reason=None, files_to_add=[])
    reason = raw.get("reason") if isinstance(raw, dict) else None
    files_to_add: list[str] = []
    if isinstance(raw, dict):
        entries = raw.get("files_to_add")
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict):
                    path = entry.get("path")
                    if isinstance(path, str) and path:
                        files_to_add.append(path)
    return AmendmentState(task_id=task_id, exists=True, reason=reason, files_to_add=files_to_add)


def detect_phase(
    *,
    subagent_type: str | None,
    recent_cli: list[CliEvent],
    idle_seconds: int,
) -> str:
    """Label the current orchestration phase from its most recent signals.

    Returns one of:

    - ``"executor subagent active"``
    - ``"review-supervisor running"``
    - ``"security-reviewer running"``
    - ``"git-ops running"``
    - ``"blocker-resolver running"``
    - ``"manifest-amender running"``
    - ``"idle"``

    Preference order: an actively-writing subagent transcript wins over
    anything else; when no subagent is writing (or its type is unknown) we
    fall back to the orchestrator log's most recent CLI event. Idle state
    wins over both when no signal is fresher than the idle threshold.
    """
    if idle_seconds >= IDLE_THRESHOLD_SECONDS and not recent_cli:
        return "idle"

    if subagent_type:
        mapped = _phase_label_from_subagent(subagent_type)
        if mapped is not None:
            return mapped

    if recent_cli:
        latest_message = recent_cli[-1].message.lower()
        for token, label in _PHASE_MESSAGE_HINTS:
            if token in latest_message:
                return label

    return "idle"


_PHASE_MESSAGE_HINTS: list[tuple[str, str]] = [
    ("security", "security-reviewer running"),
    ("review", "review-supervisor running"),
    ("git_ops", "git-ops running"),
    ("git-ops", "git-ops running"),
    ("amendment", "manifest-amender running"),
    ("blocker", "blocker-resolver running"),
]


_SUBAGENT_PHASE_HINTS: list[tuple[tuple[str, ...], str]] = [
    (("executor",), "executor subagent active"),
    (("review-supervisor", "review_supervisor"), "review-supervisor running"),
    (("security",), "security-reviewer running"),
    (("git_ops", "git-ops"), "git-ops running"),
    (("blocker",), "blocker-resolver running"),
    (("amender", "amendment"), "manifest-amender running"),
    (("task-factory", "task_factory"), "task-factory running"),
]


def _phase_label_from_subagent(subagent_type: str) -> str | None:
    """Map a subagent_type token to a dashboard phase label."""
    name = subagent_type.lower()
    for tokens, label in _SUBAGENT_PHASE_HINTS:
        if any(token in name for token in tokens):
            return label
    return None


def mode_label(runtime_config: RuntimeConfig) -> str:
    """Return a short human-readable label for the current git-ops mode.

    - ``defer_pr=True`` + ``single_branch=<name>`` -> single-branch deferred PR.
    - ``defer_pr=True`` without ``single_branch`` -> invalid config fallback.
    - ``defer_pr=False``, no ``single_branch`` -> standard multi-PR.
    - Future ``pause_before_merge`` (if/when added) -> multi-PR with pause.
    """
    git_ops = runtime_config.git_ops
    single = git_ops.single_branch
    deferred = git_ops.defer_pr

    if deferred and single:
        return f"single-branch + defer_pr (branch: {single})"
    if deferred and not single:
        return "deferred-PR (no shared branch)  [invalid config]"

    pause_flag = getattr(git_ops, "pause_before_merge", None)
    if pause_flag:
        return "multi-PR with pause-before-merge"

    return "standard multi-PR"


RepoPathResolver = Any


def collect_snapshot(
    *,
    workspace_root: Path,
    backlog_index: Path,
    runtime_config: RuntimeConfig,
    orchestrator_log: Path,
    hook_log: Path,
    repo_path_resolver: RepoPathResolver,
    now: datetime | None = None,
) -> ActivitySnapshot:
    """Collect every signal that feeds the dashboard and return a typed snapshot.

    This is the integration seam between the CLI layer and the individual
    parsers. Each source is read once; parsers are called with the paths
    they expect; on any missing source the corresponding field is left as
    ``None`` or an empty list so the renderer can skip that panel
    gracefully.

    Args:
        workspace_root: ``DEVBENCH_WORKSPACE_ROOT``. Used to locate
            ``.devbench/amendments/<id>.json`` and anchor relative paths.
        backlog_index: Path to ``BACKLOG.md``. Used to identify the active
            task via the :class:`devbench.backlog.parser.BacklogParser`.
        runtime_config: Loaded :class:`RuntimeConfig`. Used for the mode
            label and the ``timeouts.command`` value for git reads.
        orchestrator_log: Path to ``orchestrator.log``. Used for recent
            CLI events and the most-recent ``Claimed X`` timestamp.
        hook_log: Path to ``hook-logs.jsonl``. Used to discover the
            current Claude Code session directory.
        repo_path_resolver: Callable mapping ``repo_name -> Path`` for the
            active work unit's repo. Lets this module stay decoupled from
            :mod:`devbench.config` (which loads at import time).
        now: Override for the "now" timestamp (tests). Defaults to
            ``datetime.now(UTC)``.
    """
    now = now or datetime.now(UTC)

    active_task = _find_active_task(backlog_index)
    active_task_id = active_task.unit_id if active_task is not None else None

    subagent: SubagentActivity | None = None
    session_dir = discover_session_dir(hook_log)
    if session_dir is not None:
        transcript = find_active_subagent(session_dir)
        if transcript is not None:
            subagent = parse_subagent_recent_activity(transcript)

    recent_cli = parse_orchestrator_recent_cli(orchestrator_log)

    claimed_at = _find_most_recent_claim(orchestrator_log, active_task_id) if active_task_id else None

    repo_state: RepoState | None = None
    if active_task is not None and active_task.repo:
        repo_path = repo_path_resolver(active_task.repo)
        if repo_path is not None:
            timeout = runtime_config.timeouts.command or DEFAULT_GIT_READ_TIMEOUT
            repo_state = parse_repo_state(repo_path, timeout=timeout)

    amendment: AmendmentState | None = None
    if active_task_id is not None:
        amendment = check_amendment_request(workspace_root, active_task_id)

    last_tool_call_at = subagent.last_activity_at if subagent is not None else None
    if recent_cli and (last_tool_call_at is None or recent_cli[-1].at > last_tool_call_at):
        last_tool_call_at = recent_cli[-1].at

    idle_seconds = _compute_idle_seconds(now, last_tool_call_at)

    phase = detect_phase(
        subagent_type=subagent.subagent_type if subagent is not None else None,
        recent_cli=recent_cli,
        idle_seconds=idle_seconds,
    )

    return ActivitySnapshot(
        now=now,
        mode_label=mode_label(runtime_config),
        active_task_id=active_task_id,
        active_task_title=active_task.title if active_task is not None else None,
        active_task_status=active_task.status_value if active_task is not None else None,
        claimed_at=claimed_at,
        phase=phase,
        last_tool_call_at=last_tool_call_at,
        subagent=subagent,
        recent_cli=recent_cli,
        repo_state=repo_state,
        amendment=amendment,
        idle_seconds=idle_seconds,
    )


@dataclass(frozen=True)
class _ActiveTask:
    """Minimal projection of the currently-active work unit."""

    unit_id: str
    title: str
    status_value: str
    repo: str


def _find_active_task(backlog_index: Path) -> _ActiveTask | None:
    """Return the one task the orchestrator is working on, if any.

    "Active" means ``in-progress`` first; when no task is in-progress,
    the most recent ``in-review`` or ``blocked`` task serves as the
    display fallback so the dashboard always has a focal point. Returns
    ``None`` when the backlog is empty or fails to parse.
    """
    from devbench.backlog.parser import BacklogParser
    from devbench.backlog.work_unit import WorkUnitStatus, WorkUnitType

    if not backlog_index.is_file():
        return None

    try:
        parser = BacklogParser(backlog_root=backlog_index.parent / "backlog", backlog_index=backlog_index)
        units = parser.parse_index()
    except (FileNotFoundError, ValueError) as exc:
        logger.warning("cannot parse backlog index %s: %s", backlog_index, exc)
        return None

    tasks = [u for u in units if u.unit_type is WorkUnitType.TASK]
    for status in (WorkUnitStatus.IN_PROGRESS, WorkUnitStatus.IN_REVIEW, WorkUnitStatus.BLOCKED):
        for unit in tasks:
            if unit.status is status:
                return _ActiveTask(
                    unit_id=unit.id,
                    title=unit.title,
                    status_value=unit.status.value,
                    repo=unit.repo,
                )
    return None


def _find_most_recent_claim(log_path: Path, unit_id: str) -> datetime | None:
    """Return the most recent claim timestamp for ``unit_id`` from the orchestrator log."""
    if not log_path.is_file():
        return None
    latest: datetime | None = None
    marker = f"Claimed {unit_id}"
    for raw_line in log_path.read_text(encoding="utf-8").splitlines():
        if marker not in raw_line:
            continue
        event = _parse_log_line(raw_line)
        if event is not None:
            latest = event.at
    return latest


def _compute_idle_seconds(now: datetime, last_activity_at: datetime | None) -> int:
    """Return ``(now - last_activity_at).total_seconds()`` as a non-negative int."""
    if last_activity_at is None:
        return 0
    delta = now - last_activity_at
    if delta < timedelta(seconds=0):
        return 0
    return int(delta.total_seconds())


def render_snapshot(snapshot: ActivitySnapshot) -> str:
    """Return the terminal-friendly dashboard text for ``snapshot``.

    Pure function -- no I/O, no env reads. Golden-output tests live in
    :mod:`tests.test_activity` and compare the returned string against
    stored expected values.
    """
    lines: list[str] = []
    lines.append(f"DevBench activity -- {_fmt_timestamp(snapshot.now)}")
    lines.append("")
    lines.append(f"Mode: {snapshot.mode_label}")
    lines.extend(_render_active_task(snapshot))
    lines.append("")
    lines.append(f"Phase: {snapshot.phase}  ({_fmt_last_activity(snapshot)})")
    lines.extend(_render_agent_text(snapshot))
    lines.extend(_render_recent_tools(snapshot))
    lines.extend(_render_recent_cli(snapshot))
    lines.extend(_render_repo_state(snapshot))
    lines.extend(_render_amendment(snapshot))
    lines.append("")
    lines.append(_render_footer(snapshot))
    return "\n".join(lines)


def _fmt_timestamp(dt: datetime) -> str:
    """Format a datetime for the dashboard header, always in UTC."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fmt_last_activity(snapshot: ActivitySnapshot) -> str:
    """Return the parenthetical following the phase label."""
    if snapshot.last_tool_call_at is None:
        return "no activity yet"
    seconds = snapshot.idle_seconds
    if seconds < 60:
        return f"last activity {seconds}s ago"
    minutes = seconds // 60
    return f"last activity {minutes}m ago"


def _render_active_task(snapshot: ActivitySnapshot) -> list[str]:
    """Return the 1-2 lines describing the active task, or a no-task placeholder."""
    if snapshot.active_task_id is None:
        return ["Active task: (none)"]
    title = snapshot.active_task_title or ""
    lines = [f"Active task: {snapshot.active_task_id}  {title!r}"]
    if snapshot.claimed_at is not None and snapshot.active_task_status:
        delta = snapshot.now - snapshot.claimed_at
        minutes = max(int(delta.total_seconds() // 60), 0)
        lines.append(f"             claimed {minutes}m ago -- {snapshot.active_task_status}")
    elif snapshot.active_task_status:
        lines.append(f"             status: {snapshot.active_task_status}")
    return lines


def _render_agent_text(snapshot: ActivitySnapshot) -> list[str]:
    """Return the "Latest agent thinking" panel, or nothing when no text is present."""
    if snapshot.subagent is None or not snapshot.subagent.latest_text:
        return []
    lines = ["", "Latest agent thinking (most recent text from the active subagent):"]
    for raw_line in snapshot.subagent.latest_text.splitlines() or [snapshot.subagent.latest_text]:
        lines.append(f"  {raw_line}")
    return lines


def _render_recent_tools(snapshot: ActivitySnapshot) -> list[str]:
    """Return the "Recent tool calls" panel, or nothing when no tools are logged."""
    if snapshot.subagent is None or not snapshot.subagent.recent_tools:
        return []
    count = len(snapshot.subagent.recent_tools)
    lines = ["", f"Recent tool calls (most recent {count}):"]
    for event in snapshot.subagent.recent_tools:
        ts = event.at.astimezone(UTC).strftime("%H:%M:%S")
        lines.append(f"  {ts}  {event.tool:<6} {event.summary}")
    return lines


def _render_recent_cli(snapshot: ActivitySnapshot) -> list[str]:
    """Return the "Recent devbench CLI calls" panel, or nothing when no events."""
    if not snapshot.recent_cli:
        return []
    count = len(snapshot.recent_cli)
    lines = ["", f"Recent devbench CLI calls (last {count}):"]
    for event in snapshot.recent_cli:
        ts = event.at.astimezone(UTC).strftime("%H:%M:%S")
        lines.append(f"  {ts}  {event.message}")
    return lines


def _render_repo_state(snapshot: ActivitySnapshot) -> list[str]:
    """Return the "Target repo state" panel, or nothing when no repo state."""
    rs = snapshot.repo_state
    if rs is None:
        return []
    header = "Target repo state"
    if rs.repo_path is not None:
        header = f"Target repo state ({rs.repo_path.name})"
    lines = ["", f"{header}:"]
    if rs.error:
        lines.append(f"  error: {rs.error}")
        return lines
    for path in rs.staged:
        lines.append(f"  M  {path}               (staged)")
    for path in rs.unstaged:
        lines.append(f"  M  {path}               (unstaged)")
    for path in rs.untracked:
        lines.append(f"  ?? {path}               (untracked)")
    lines.append(f"  Staged count: {len(rs.staged)}  Unstaged: {len(rs.unstaged)}  Untracked: {len(rs.untracked)}")
    return lines


def _render_amendment(snapshot: ActivitySnapshot) -> list[str]:
    """Return the "Pending amendment request" summary or a single no-request line."""
    if snapshot.amendment is None:
        return []
    if not snapshot.amendment.exists:
        return ["", "Pending amendment request: no"]
    file_count = len(snapshot.amendment.files_to_add)
    reason = snapshot.amendment.reason or "unknown"
    return [
        "",
        f"Pending amendment request: yes  ({file_count} file(s); reason={reason})",
    ]


def _render_footer(snapshot: ActivitySnapshot) -> str:
    """Return the trailing one-line footer."""
    seconds = snapshot.idle_seconds
    return f"Idle for {seconds}s.  (Ctrl+C to stop; `devbench watch --watch N` for live tail.)"
