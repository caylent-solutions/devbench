"""Acceptance-Criteria verification contract: parsing, IaC-tool detection, evidence model.

DevBench anchors deterministic completion proof on **Acceptance Criteria** (AC).
A work unit's optional ``## Verification`` section maps each *executable* AC to a
command whose **real** exit code is captured by ``devbench verify-ac`` (never
self-reported) and gated at ``mark-done``: a unit cannot be marked done until every
executable AC has a tool-captured exit-0 evidence record for the current attempt.

This module is intentionally dependency-light (stdlib only) so it can be imported by
the parser, the backlog manager (the done-gate), the CLI runner, and the validator
without creating import cycles.

Directive grammar (one per AC, inside ``## Verification``)::

    - VERIFY AC-3 | type=terratest | tool=terragrunt | cmd=`make tf-test UNIT=...` | expect-exit=0 | timeout=5400
    - VERIFY AC-7 | type=smoke | cmd=`make smoke URL=$URL` | expect-exit=0
    - VERIFY AC-9 | type=deferred | owner=operator | reason="prod apply is operator-only"

The optional ``timeout=<seconds>`` field overrides the global per-command budget
(``DEVBENCH_TEST_TIMEOUT`` / ``DEFAULT_TEST_TIMEOUT``) for that one directive, so a
backlog can derive a long-running directive's bound from the test's own declared
timeout without raising the global default for fast unit-test ACs.

``type`` is one of :class:`VerificationType`. ACs with no executable claim default to
``type=judge`` (qualitative -- left to the core review judges, never gated here).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

CommandRunner = Callable[..., tuple[int, str, str]]


class VerificationType(Enum):
    """How a single Acceptance Criterion is verified."""

    TERRATEST = "terratest"
    APPLY = "apply"
    PLAN = "plan"
    DESTROY = "destroy"
    DEPLOY = "deploy"
    SMOKE = "smoke"
    COMMAND = "command"
    DEFERRED = "deferred"
    JUDGE = "judge"


EXECUTABLE_TYPES: frozenset[VerificationType] = frozenset(
    {
        VerificationType.TERRATEST,
        VerificationType.APPLY,
        VerificationType.PLAN,
        VerificationType.DESTROY,
        VerificationType.DEPLOY,
        VerificationType.SMOKE,
        VerificationType.COMMAND,
    }
)

INFRA_TYPES: frozenset[VerificationType] = frozenset(
    {
        VerificationType.TERRATEST,
        VerificationType.APPLY,
        VerificationType.PLAN,
        VerificationType.DESTROY,
        VerificationType.DEPLOY,
        VerificationType.SMOKE,
    }
)


_IAC_LIFECYCLE_VERB: str = (
    r"(?:init|validate|plan|apply|destroy|refresh|import|output|state|providers|"
    r"graph|fmt|workspace|console|show|taint|untaint|force-unlock|"
    r"run-all|run|render-json|hcl|hclfmt|graph-dependencies|output-module-groups)"
)

IAC_TOOL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("terraform", re.compile(rf"\bterraform\s+{_IAC_LIFECYCLE_VERB}\b", re.IGNORECASE)),
    ("opentofu", re.compile(rf"\btofu\s+{_IAC_LIFECYCLE_VERB}\b", re.IGNORECASE)),
    ("terragrunt", re.compile(rf"\bterragrunt\s+{_IAC_LIFECYCLE_VERB}\b", re.IGNORECASE)),
    ("terratest", re.compile(r"(?<![\w/-])(?:tf-test|terratest)(?![\w/-])|\bgo\s+test\b", re.IGNORECASE)),
    ("cdktf", re.compile(r"\bcdktf\s+(?:deploy|synth|destroy|diff|plan|apply)\b", re.IGNORECASE)),
    ("aws-cdk", re.compile(r"\bcdk\s+(?:deploy|synth|destroy|diff)\b", re.IGNORECASE)),
    (
        "cloudformation",
        re.compile(
            r"\baws\s+cloudformation\s+(?:deploy|create-stack|update-stack|delete-stack|execute-change-set)\b",
            re.IGNORECASE,
        ),
    ),
    ("aws-sam", re.compile(r"\bsam\s+(?:build|deploy|sync|validate)\b", re.IGNORECASE)),
)


def detect_iac_tool(command: str | None) -> str | None:
    """Return the IaC tool name *invoked* in *command*, or ``None``.

    A tool is detected only when *command* invokes it with a recognised
    lifecycle verb/subcommand (see :data:`IAC_TOOL_PATTERNS`); a bare path
    operand that merely contains an IaC tool/directory name (``terragrunt/...``,
    ``providers/aws/...``) does not count. The first pattern in
    :data:`IAC_TOOL_PATTERNS` to match wins (ordering puts the more specific
    tools first).
    """
    if not command:
        return None
    for name, pattern in IAC_TOOL_PATTERNS:
        if pattern.search(command):
            return name
    return None


_EXECUTION_VERB_RE: re.Pattern[str] = re.compile(
    r"\b("
    r"terraform|tofu|terragrunt|terratest|tf-test|tf-apply|tg-apply|tf-destroy|tg-destroy|"
    r"cdktf|cdk\s+(?:deploy|synth|destroy)|cloudformation|sam\s+(?:build|deploy)|"
    r"apply|deploy|provision|pytest|go\s+test|"
    r"make\s+[a-z][\w-]*|"
    r"passes|succeeds|exits?\s+zero|smoke"
    r")\b",
    re.IGNORECASE,
)


def text_has_execution_verb(text: str) -> bool:
    """Return ``True`` when *text* asserts an executable/testable outcome.

    Used by the validator to require a ``VERIFY`` directive for any such AC, and to
    forbid un-AC'd executable claims in the Definition of Done.
    """
    return bool(_EXECUTION_VERB_RE.search(text or ""))


_RANDOMLY_SEED_FLAG_RE: re.Pattern[str] = re.compile(r"--randomly-seed=\S+")


def deterministic_gate_env(base_env: dict[str, str], *, seed: int, pin_randomly: bool = True) -> dict[str, str]:
    """Return *base_env* overlaid with a pinned, reproducible pytest ordering seed.

    Produces a NEW mapping (never mutates *base_env*) suitable for passing as a
    subprocess ``env``. The overlay:

    * ALWAYS sets ``PYTHONHASHSEED`` to *seed* (interpreter hash-order
      determinism -- safe in any environment, no plugin dependency);
    * when *pin_randomly* is True, sets/normalises ``PYTEST_ADDOPTS`` so it
      pins ``pytest-randomly`` with ``--randomly-seed=<seed>`` exactly once,
      preserving any other addopts the caller already had and dropping any
      stale ``--randomly-seed``. When *pin_randomly* is False the
      ``--randomly-seed`` flag is NOT injected (a repo without the plugin would
      error on the unknown option) and any pre-existing addopts are left
      untouched.

    Args:
        base_env: The environment to overlay (typically ``os.environ`` copied).
        seed: The fixed, non-negative ordering seed (operator-configurable via
            ``config.VERIFY_AC_PYTEST_SEED``).
        pin_randomly: Whether ``pytest-randomly`` is available in the execution
            environment, so its seed flag can be safely pinned. Defaults to
            True; the caller probes the target repo and passes the real value.

    Returns:
        A new ``dict`` with the deterministic overlay applied.

    Raises:
        ValueError: When *seed* is negative (a seed must be a non-negative int;
            fail fast rather than silently coerce).
    """
    if seed < 0:
        raise ValueError(f"deterministic gate seed must be a non-negative integer; got {seed}")
    env = dict(base_env)
    env["PYTHONHASHSEED"] = str(seed)
    if pin_randomly:
        existing = _RANDOMLY_SEED_FLAG_RE.sub("", env.get("PYTEST_ADDOPTS", "")).strip()
        pinned = f"--randomly-seed={seed}"
        env["PYTEST_ADDOPTS"] = f"{existing} {pinned}".strip() if existing else pinned
    return env


def pytest_randomly_available(repo_path: Path, runner: CommandRunner) -> bool:
    """Return ``True`` when ``pytest-randomly`` is importable in *repo_path*.

    Probes the target-repo execution environment (the same one ``verify-ac``
    runs the gate commands in) by attempting to import the plugin via the
    repo's ``python``. This decides whether :func:`deterministic_gate_env` may
    safely pin the plugin's ``--randomly-seed`` flag: the flag is only a
    recognised pytest option when the plugin is installed, so pinning it for a
    repo WITHOUT the plugin would make every gate ``pytest`` invocation error.

    Fail-safe: a missing interpreter or an import failure yields a non-zero
    exit (``run_command`` returns ``SUBPROCESS_ERROR_EXIT_CODE`` rather than
    raising), so the gate degrades to the always-safe ``PYTHONHASHSEED`` pin
    rather than risking a broken ``pytest`` invocation.

    Args:
        repo_path: The target-repo checkout root the gate commands run in.
        runner: A callable with the signature of
            ``utils.process.run_command`` -- ``(cmd, cwd=...) -> (rc, out, err)``.
            Injected so this stays import-light and unit-testable. It MUST NOT
            raise on a missing executable (the shared ``run_command`` does not).

    Returns:
        ``True`` iff the probe command exits 0 (the plugin imported cleanly).
    """
    probe = ["python", "-c", "import pytest_randomly"]
    rc, _, _ = runner(probe, cwd=repo_path)
    return rc == 0


_CMD_SUBSTITUTION_RE: re.Pattern[str] = re.compile(r"\$\([^)]*\)|`[^`]*`")

_FIND_FEEDS_GREP_RE: re.Pattern[str] = re.compile(r"\bgrep\b[^|;&]*\$\(\s*find\b", re.IGNORECASE)

_PATH_EXTENSIONS: tuple[str, ...] = (
    ".tf",
    ".tfvars",
    ".hcl",
    ".py",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".md",
    ".sh",
    ".txt",
    ".cfg",
    ".ini",
    ".go",
)

_PATH_EXT_RE: re.Pattern[str] = re.compile(r"\.[A-Za-z0-9]{1,6}$")


def _looks_like_path_operand(token: str) -> bool:
    """Return ``True`` when *token* is a literal filesystem path operand.

    A path operand contains a ``/`` separator or ends in a recognised file
    extension. Shell flags (``-r``), key=value forms, variable refs (``$X``),
    glob patterns (``*``), and bare command words (``grep``, ``test``) are
    excluded so only resolvable literal paths are returned.
    """
    if not token or token.startswith(("-", "$")):
        return False
    if "=" in token or "*" in token or "?" in token:
        return False
    if "/" in token:
        return True
    return bool(_PATH_EXT_RE.search(token)) and token.endswith(_PATH_EXTENSIONS)


def extract_command_paths(command: str | None) -> list[str]:
    """Return the literal filesystem path operands invoked in *command*.

    Command substitutions (``$(...)``/backticks) are stripped first so their
    contents are not treated as literal operands. Each surviving whitespace
    token is unquoted and kept only when it :func:`_looks_like_path_operand`.
    Order-preserving and de-duplicated. Used by the validator to check that a
    ``type=command`` directive's paths resolve against the target-repo checkout
    root (the ``verify-ac`` working directory).
    """
    if not command:
        return []
    stripped = _CMD_SUBSTITUTION_RE.sub(" ", command)
    seen: set[str] = set()
    out: list[str] = []
    for raw in stripped.split():
        token = raw.strip().strip("'\"").strip("'\"")
        if not _looks_like_path_operand(token):
            continue
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


def command_substitution_feeds_grep(command: str | None) -> bool:
    """Return ``True`` when *command* pipes a ``$(find ...)`` into a ``grep``.

    Flags the ``! grep ... $(find <path> ...)`` shape whose recursive ``grep``
    silently scans the whole tree when ``find`` returns zero operands (TDI-001).
    """
    if not command:
        return False
    return bool(_FIND_FEEDS_GREP_RE.search(command))


_RUNNABLE_TOOL_RE: re.Pattern[str] = re.compile(
    r"\b("
    r"terraform|terragrunt|tofu|terratest|tf-test|cdktf|cdk|sam|"
    r"pytest|go\s+test|make|npm|yarn|pnpm|cargo|gradle|mvn|tox|"
    r"toolchain|at\s+execution\s+time"
    r")\b",
    re.IGNORECASE,
)

_OPERATOR_ONLY_RE: re.Pattern[str] = re.compile(
    r"\b("
    r"live|prod|production|operator[- ]only|manual|human|sign[- ]?off|"
    r"credential|secret|real\s+account|cannot\s+run|not\s+available|"
    r"requires\s+aws|against\s+(?:a\s+)?live"
    r")\b",
    re.IGNORECASE,
)


def deferred_reason_names_runnable_tool(reason: str | None) -> str | None:
    """Return the runnable tool/phrase named in a ``type=deferred`` *reason*, or ``None``.

    A ``type=deferred`` directive is legitimate only for a check that cannot run
    in the orchestrator environment. When the reason instead names a project
    tool that IS runnable there (and carries no live/production/operator-only
    signal), the directive is a mis-classified runnable check that should be
    ``type=command`` (TDI-004). Returns the matched tool/phrase so the validator
    message can name it; returns ``None`` when the reason is clean or genuinely
    operator-only.
    """
    if not reason:
        return None
    if _OPERATOR_ONLY_RE.search(reason):
        return None
    match = _RUNNABLE_TOOL_RE.search(reason)
    return match.group(1) if match is not None else None


_VERIFICATION_HEADER = "Verification"

_VERIFY_LINE_RE: re.Pattern[str] = re.compile(r"^\s*-\s*VERIFY\b(.*)$", re.MULTILINE)
_CMD_FIELD_RE: re.Pattern[str] = re.compile(r"cmd\s*=\s*`([^`]*)`")
_AC_ID_RE: re.Pattern[str] = re.compile(r"AC-[A-Za-z0-9-]+")


@dataclass(frozen=True)
class VerificationItem:
    """One parsed ``- VERIFY`` directive."""

    ac_ids: tuple[str, ...]
    vtype: VerificationType
    command: str | None = None
    tool: str | None = None
    expect_exit: int = 0
    owner: str | None = None
    reason: str | None = None
    timeout: int | None = None
    raw: str = ""

    def is_executable(self) -> bool:
        """Return ``True`` when this item must carry exit-code evidence."""
        return self.vtype in EXECUTABLE_TYPES

    def is_infra(self) -> bool:
        """Return ``True`` when this item denotes IaC/deploy work (judge applicability)."""
        if self.vtype in INFRA_TYPES:
            return True
        if self.vtype is VerificationType.COMMAND:
            return detect_iac_tool(self.command) is not None
        return False


def _extract_verification_section(content: str) -> str:
    """Return the body of the ``## Verification`` section, or ``""`` if absent."""
    pattern = re.compile(
        rf"^##\s+{re.escape(_VERIFICATION_HEADER)}\s*\n(.*?)(?=^##\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(content)
    return match.group(1).strip() if match else ""


def has_verification_section(content: str) -> bool:
    """Return ``True`` when the work unit declares a ``## Verification`` section."""
    return re.search(rf"^##\s+{re.escape(_VERIFICATION_HEADER)}\s*$", content, re.MULTILINE) is not None


def _parse_fields(remainder: str) -> dict[str, str]:
    """Parse the ``| key=value`` fields of a VERIFY directive (cmd already removed)."""
    fields: dict[str, str] = {}
    for raw_segment in remainder.split("|"):
        segment = raw_segment.strip()
        if not segment or "=" not in segment:
            continue
        key, _, value = segment.partition("=")
        fields[key.strip().lower()] = value.strip().strip('"').strip()
    return fields


def parse_verification_item(line_body: str) -> VerificationItem:
    """Parse the text following ``- VERIFY`` into a :class:`VerificationItem`.

    Raises :class:`ValueError` with an actionable message on a malformed directive
    (no AC id, or an unknown ``type``) so ``validate-backlog`` fails fast.
    """
    raw = "VERIFY" + line_body
    command: str | None = None
    cmd_match = _CMD_FIELD_RE.search(line_body)
    if cmd_match is not None:
        command = cmd_match.group(1).strip()
        line_body = line_body[: cmd_match.start()] + line_body[cmd_match.end() :]

    leading, _, field_text = line_body.partition("|")
    ac_ids = tuple(_AC_ID_RE.findall(leading))
    if not ac_ids:
        raise ValueError(
            f"VERIFY directive names no AC id (expected e.g. 'AC-3'): {raw.strip()!r}. "
            "Every verification directive must reference at least one AC-N."
        )

    fields = _parse_fields(field_text)

    raw_type = fields.get("type", "").lower()
    if not raw_type:
        raise ValueError(f"VERIFY directive missing 'type=': {raw.strip()!r}. Allowed: {_allowed_types_str()}.")
    try:
        vtype = VerificationType(raw_type)
    except ValueError as exc:
        raise ValueError(
            f"VERIFY directive has unknown type {raw_type!r}: {raw.strip()!r}. Allowed: {_allowed_types_str()}."
        ) from exc

    expect_exit = 0
    if "expect-exit" in fields:
        try:
            expect_exit = int(fields["expect-exit"])
        except ValueError as exc:
            raise ValueError(
                f"VERIFY directive has non-integer expect-exit {fields['expect-exit']!r}: {raw.strip()!r}."
            ) from exc

    timeout: int | None = None
    if "timeout" in fields:
        try:
            timeout = int(fields["timeout"])
        except ValueError as exc:
            raise ValueError(
                f"VERIFY directive has non-integer timeout {fields['timeout']!r}: {raw.strip()!r}. "
                "timeout= must be a positive integer number of seconds."
            ) from exc
        if timeout <= 0:
            raise ValueError(
                f"VERIFY directive has non-positive timeout {timeout!r}: {raw.strip()!r}. "
                "timeout= must be a positive integer number of seconds."
            )

    return VerificationItem(
        ac_ids=ac_ids,
        vtype=vtype,
        command=command,
        tool=fields.get("tool") or detect_iac_tool(command),
        expect_exit=expect_exit,
        owner=fields.get("owner"),
        reason=fields.get("reason"),
        timeout=timeout,
        raw=raw.strip(),
    )


def _allowed_types_str() -> str:
    return ", ".join(t.value for t in VerificationType)


def parse_verification_section(content: str) -> list[VerificationItem]:
    """Parse all ``- VERIFY`` directives in the ``## Verification`` section.

    Returns an empty list when the section is absent. Raises :class:`ValueError`
    on a malformed directive (propagated from :func:`parse_verification_item`).
    """
    section = _extract_verification_section(content)
    if not section:
        return []
    return [parse_verification_item(m.group(1)) for m in _VERIFY_LINE_RE.finditer(section)]


def executable_items(items: list[VerificationItem]) -> list[VerificationItem]:
    """Return the items that must carry exit-code evidence."""
    return [i for i in items if i.is_executable()]


def deferred_items(items: list[VerificationItem]) -> list[VerificationItem]:
    """Return the items explicitly deferred to a human/operator."""
    return [i for i in items if i.vtype is VerificationType.DEFERRED]


def unit_requires_iac_judge(content: str) -> bool:
    """Return ``True`` when the unit's Verification contract includes infra work.

    Deterministic: the optional ``iac_review`` judge is auto-required for a unit iff
    this returns ``True`` (and the judge is enabled) -- never authored by hand, never
    self-judged. Malformed directives are ignored here (the validator reports them).
    """
    try:
        items = parse_verification_section(content)
    except ValueError:
        return False
    return any(i.is_infra() for i in items)


def _optional_str(value: object) -> str | None:
    """Coerce a deserialised ledger value to ``str`` or ``None`` (never other types)."""
    if value is None:
        return None
    return str(value)


@dataclass
class EvidenceRecord:
    """One captured verification result (tool-run exit code, never self-reported)."""

    ac_ids: list[str]
    vtype: str
    command: str | None
    exit_code: int
    tool: str | None = None
    started_at: str = ""
    finished_at: str = ""
    artifact: str | None = None
    summary: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "ac_ids": list(self.ac_ids),
            "vtype": self.vtype,
            "command": self.command,
            "exit_code": self.exit_code,
            "tool": self.tool,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "artifact": self.artifact,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> EvidenceRecord:
        raw_ac_ids = data.get("ac_ids", [])
        ac_ids = [str(a) for a in raw_ac_ids] if isinstance(raw_ac_ids, list) else []

        raw_exit = data.get("exit_code", 1)
        exit_code = raw_exit if isinstance(raw_exit, int) else 1

        return cls(
            ac_ids=ac_ids,
            vtype=str(data.get("vtype", "")),
            command=_optional_str(data.get("command")),
            exit_code=exit_code,
            tool=_optional_str(data.get("tool")),
            started_at=str(data.get("started_at", "")),
            finished_at=str(data.get("finished_at", "")),
            artifact=_optional_str(data.get("artifact")),
            summary=str(data.get("summary", "")),
        )


@dataclass
class EvidenceCompleteness:
    """Result of checking an evidence ledger against a unit's executable ACs."""

    complete: bool
    missing: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)

    def message(self) -> str:
        """Actionable summary for a ``mark-done`` rejection."""
        parts: list[str] = []
        if self.missing:
            parts.append(f"no exit-0 evidence for: {', '.join(self.missing)}")
        if self.failed:
            parts.append(f"non-zero exit recorded for: {', '.join(self.failed)}")
        if self.deferred:
            parts.append(f"deferred (operator-only) ACs block done: {', '.join(self.deferred)}")
        return "; ".join(parts)


def evidence_completeness(
    items: list[VerificationItem],
    records: list[EvidenceRecord],
    *,
    allow_deferred: bool = False,
) -> EvidenceCompleteness:
    """Check that every executable AC has a satisfying evidence record.

    An AC is *satisfied* when at least one record covering it has
    ``exit_code == item.expect_exit``. Deferred ACs block completion unless
    *allow_deferred* is ``True``.

    Args:
        items: Parsed verification items for the work unit.
        records: Evidence records from the current attempt's ledger.
        allow_deferred: When ``True``, ``type=deferred`` items do not block.

    Returns:
        An :class:`EvidenceCompleteness` with the per-category AC id lists. The
        ``complete`` flag is ``True`` only when there are no missing, failed, or
        (unless allowed) deferred ACs.
    """
    records_by_ac: dict[str, list[EvidenceRecord]] = {}
    for rec in records:
        for ac in rec.ac_ids:
            records_by_ac.setdefault(ac, []).append(rec)

    missing: list[str] = []
    failed: list[str] = []
    deferred: list[str] = []

    for item in items:
        if item.vtype is VerificationType.DEFERRED:
            if not allow_deferred:
                deferred.extend(item.ac_ids)
            continue
        if not item.is_executable():
            continue
        for ac in item.ac_ids:
            recs = records_by_ac.get(ac, [])
            if not recs:
                missing.append(ac)
            elif not any(r.exit_code == item.expect_exit for r in recs):
                failed.append(ac)

    complete = not missing and not failed and not deferred
    return EvidenceCompleteness(
        complete=complete,
        missing=sorted(set(missing)),
        failed=sorted(set(failed)),
        deferred=sorted(set(deferred)),
    )


_EVIDENCE_SUBDIR = ".devbench/evidence"
_LEDGER_FILENAME = "evidence.json"
_LATEST_POINTER_FILENAME = "latest.json"
_SANITIZE_RE: re.Pattern[str] = re.compile(r"[^A-Za-z0-9._-]+")


def evidence_root(workspace_root: Path, task_id: str) -> Path:
    """Return the per-task evidence directory under *workspace_root*."""
    return workspace_root / _EVIDENCE_SUBDIR / task_id


def evidence_attempt_dir(workspace_root: Path, task_id: str, attempt: int) -> Path:
    """Return the directory holding one attempt's artifacts and ledger."""
    return evidence_root(workspace_root, task_id) / str(attempt)


def sanitize_ac_label(ac_ids: list[str] | tuple[str, ...]) -> str:
    """Return a filesystem-safe label for an evidence artifact from its AC ids.

    Joins the ids with ``-`` and replaces any character outside
    ``[A-Za-z0-9._-]`` with ``_``. Falls back to ``unknown`` when *ac_ids* is
    empty so a malformed item still yields a stable, non-empty filename.
    """
    if not ac_ids:
        return "unknown"
    return _SANITIZE_RE.sub("_", "-".join(ac_ids))


_TRIM_SENTINEL_SUBSTRINGS: tuple[str, ...] = (
    "Apply complete!",
    "Destroy complete!",
    "No changes.",
    "--- PASS:",
    "--- FAIL:",
    "panic:",
    "Error:",
)
_TRIM_SENTINEL_LINE_PREFIXES: tuple[str, ...] = (
    "ok ",
    "FAIL ",
)
_TRIM_ELISION_TEMPLATE = "[... {n} bytes elided ...]"


def _line_is_sentinel(line: str) -> bool:
    """Return ``True`` when *line* carries a proof sentinel worth retaining."""
    if any(token in line for token in _TRIM_SENTINEL_SUBSTRINGS):
        return True
    stripped = line.lstrip()
    return any(stripped.startswith(prefix) for prefix in _TRIM_SENTINEL_LINE_PREFIXES)


def trim_log(text: str, max_bytes: int) -> str:
    """Return *text* bounded near *max_bytes* characters, retaining proof sentinels.

    Sentinel-aware trim. A non-positive *max_bytes* or a *text* already within
    budget is returned verbatim. Otherwise the result is assembled, in original
    order, from three sources so the size stays near *max_bytes* while the
    actionable lines survive regardless of where they sit in the log:

    1. a leading **head** window (so the run's setup/context is visible),
    2. every **sentinel** line (see :data:`_TRIM_SENTINEL_SUBSTRINGS` and
       :data:`_TRIM_SENTINEL_LINE_PREFIXES`) the byte budget can hold, and
    3. a trailing **tail** window (the package summary / post-run lines).

    This fixes the old tail-only slice, which discarded the head-of-log
    ``Apply complete!`` / idempotency re-plan / ``Destroy complete!`` proof on
    test packages that order plan-only negative tests after their apply tests.
    Where retained regions are not contiguous in the original text a
    ``[... N bytes elided ...]`` marker records how many characters were dropped.
    A log carrying no sentinels degrades to a plain head+tail window.
    """
    if max_bytes <= 0 or len(text) <= max_bytes:
        return text

    lines = text.split("\n")
    line_costs = [len(line) + (1 if i < len(lines) - 1 else 0) for i, line in enumerate(lines)]

    head_budget = max_bytes // 4
    tail_budget = max_bytes // 4

    keep: list[bool] = [False] * len(lines)

    used = 0
    head_kept = 0
    for i, cost in enumerate(line_costs):
        if used + cost > head_budget and used > 0:
            break
        keep[i] = True
        used += cost
        head_kept = i + 1

    used = 0
    for i in range(len(lines) - 1, head_kept - 1, -1):
        cost = line_costs[i]
        if used + cost > tail_budget and used > 0:
            break
        keep[i] = True
        used += cost

    retained = sum(cost for i, cost in enumerate(line_costs) if keep[i])

    for i, line in enumerate(lines):
        if keep[i] or not _line_is_sentinel(line):
            continue
        cost = line_costs[i]
        if retained + cost > max_bytes:
            continue
        keep[i] = True
        retained += cost

    out: list[str] = []
    gap_bytes = 0
    for i, line in enumerate(lines):
        if keep[i]:
            if gap_bytes:
                out.append(_TRIM_ELISION_TEMPLATE.format(n=gap_bytes))
                gap_bytes = 0
            out.append(line)
        else:
            gap_bytes += line_costs[i]
    return "\n".join(out)


def next_attempt_number(workspace_root: Path, task_id: str) -> int:
    """Return the attempt number a fresh ``verify-ac`` run should write to.

    Attempts are 1-indexed. The next attempt is one past the highest existing
    numeric attempt directory, so a re-run never overwrites prior evidence and
    the latest pointer always advances monotonically.
    """
    root = evidence_root(workspace_root, task_id)
    if not root.is_dir():
        return 1
    highest = 0
    for child in root.iterdir():
        if child.is_dir() and child.name.isdigit():
            highest = max(highest, int(child.name))
    return highest + 1


def write_evidence_ledger(
    workspace_root: Path,
    task_id: str,
    attempt: int,
    records: list[EvidenceRecord],
) -> Path:
    """Write *records* as the attempt's ledger and advance the latest pointer.

    Returns the path to the written ``evidence.json``. The directory is created
    if absent. The latest pointer is updated last so a reader never sees a
    pointer to a half-written ledger.
    """
    attempt_dir = evidence_attempt_dir(workspace_root, task_id, attempt)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = attempt_dir / _LEDGER_FILENAME
    ledger_path.write_text(
        json.dumps([rec.to_dict() for rec in records], indent=2) + "\n",
        encoding="utf-8",
    )
    pointer_path = evidence_root(workspace_root, task_id) / _LATEST_POINTER_FILENAME
    pointer_path.write_text(json.dumps({"attempt": attempt}) + "\n", encoding="utf-8")
    return ledger_path


def latest_attempt_number(workspace_root: Path, task_id: str) -> int | None:
    """Return the attempt number named by the latest pointer, or ``None``.

    Reads ``<task>/latest.json``. Returns ``None`` when the pointer is absent,
    unreadable, or malformed -- the done-gate treats that as "no evidence yet".
    """
    pointer_path = evidence_root(workspace_root, task_id) / _LATEST_POINTER_FILENAME
    if not pointer_path.is_file():
        return None
    try:
        data = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    attempt = data.get("attempt") if isinstance(data, dict) else None
    return attempt if isinstance(attempt, int) else None


def read_latest_evidence_ledger(workspace_root: Path, task_id: str) -> list[EvidenceRecord]:
    """Return the evidence records from the latest attempt, or ``[]``.

    Resolves the attempt via :func:`latest_attempt_number`, then loads and
    deserialises that attempt's ``evidence.json``. Returns an empty list when
    no pointer exists, the ledger is missing/unreadable, or the JSON payload is
    not a list -- callers (the gate) treat an empty ledger as "no proof".
    """
    attempt = latest_attempt_number(workspace_root, task_id)
    if attempt is None:
        return []
    ledger_path = evidence_attempt_dir(workspace_root, task_id, attempt) / _LEDGER_FILENAME
    if not ledger_path.is_file():
        return []
    try:
        data = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [EvidenceRecord.from_dict(entry) for entry in data if isinstance(entry, dict)]
