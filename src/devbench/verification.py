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

    - VERIFY AC-3 | type=terratest | tool=terragrunt | cmd=`make tf-test UNIT=...` | expect-exit=0
    - VERIFY AC-7 | type=smoke | cmd=`make smoke URL=$URL` | expect-exit=0
    - VERIFY AC-9 | type=deferred | owner=operator | reason="prod apply is operator-only"

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

#: The :func:`utils.process.run_command` shape -- ``(cmd, cwd=...) -> (rc, out, err)``.
#: Declared here (rather than importing the function) so :mod:`verification`
#: stays dependency-light and the runner can be injected in tests.
CommandRunner = Callable[..., tuple[int, str, str]]

# ---------------------------------------------------------------------------
# Verification types
# ---------------------------------------------------------------------------


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


#: Types whose AC must carry tool-captured exit-0 evidence before ``mark-done``.
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

#: Types that intrinsically denote infrastructure / deploy work (independent of the
#: command text). A unit with any of these -- or any ``command`` whose ``cmd`` matches
#: :data:`IAC_TOOL_PATTERNS` -- requires the optional ``iac_review`` judge when enabled.
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

# ---------------------------------------------------------------------------
# IaC tool matrix -- the single maintained, extensible source of truth.
# Adding support for a new IaC tool is a one-line addition here.
#
# Each pattern matches the tool only when it is the **invoked command paired
# with an IaC lifecycle verb/subcommand** (e.g. ``terraform validate``,
# ``terragrunt run-all apply``, ``cdk deploy``) -- never when the tool's name
# appears solely as a **path operand** (e.g. ``test -d terragrunt/common/x``,
# ``jq . providers/aws/accounts.json``). Path-substring matches were the
# proximate cause of data-file tasks being routed to the optional ``iac_review``
# judge unnecessarily (TDI-007); requiring an adjacent lifecycle verb makes the
# predicate precise. The terratest test-runners (``go test``, ``make tf-test``,
# ``terratest``) are matched as invocation tokens, never as path components.
# ---------------------------------------------------------------------------

#: Lifecycle subcommands shared by the terraform/opentofu/terragrunt family.
#: Their presence immediately after the tool name is what distinguishes an
#: actual provisioning/plan/validate/destroy invocation from a path operand.
_IAC_LIFECYCLE_VERB: str = (
    r"(?:init|validate|plan|apply|destroy|refresh|import|output|state|providers|"
    r"graph|fmt|workspace|console|show|taint|untaint|force-unlock|"
    r"run-all|run|render-json|hcl|hclfmt|graph-dependencies|output-module-groups)"
)

IAC_TOOL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("terraform", re.compile(rf"\bterraform\s+{_IAC_LIFECYCLE_VERB}\b", re.IGNORECASE)),
    ("opentofu", re.compile(rf"\btofu\s+{_IAC_LIFECYCLE_VERB}\b", re.IGNORECASE)),
    ("terragrunt", re.compile(rf"\bterragrunt\s+{_IAC_LIFECYCLE_VERB}\b", re.IGNORECASE)),
    # Terratest is exercised via Go tests and/or the conventional ``make tf-test``
    # target. Anchored so a path component named ``terratest/`` does not match.
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


# ---------------------------------------------------------------------------
# Execution-verb detection for the validator lint (Workstream A).
# Any AC/DoD item whose prose contains one of these verbs MUST be backed by a
# VERIFY directive -- this is what makes "a real terragrunt apply succeeds"
# un-checkable without proof.
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Deterministic per-unit gate environment.
#
# A unit's ``## Verification`` pytest gate must be *reproducible*: the same
# input must yield the same verdict on every run. When the target repo uses
# ``pytest-randomly`` (random test order per run, seeded from the clock by
# default), an order-dependent sibling test can pass on one seed and fail on
# the next -- so an otherwise-complete, unrelated unit is blocked
# non-deterministically by a seed it did not choose. Pinning the seed makes
# the gate's verdict a deterministic function of the code under test; the
# orthogonal randomized-order signal is surfaced by a separate scheduled pass
# (an epic-capstone / CI gate), never as a random per-unit block.
#
# We pin via environment variables overlaid on the runner's environment so the
# mechanism requires no change to the target repo's pyproject/pytest config:
#   * ``PYTHONHASHSEED`` -- ALWAYS set. Removes hash-ordering nondeterminism in
#     the interpreter itself (dict/set iteration order under the hood). Safe in
#     every environment; no plugin dependency.
#   * ``PYTEST_ADDOPTS`` -- set ONLY when ``pin_randomly`` is True (i.e. the
#     target repo has ``pytest-randomly`` installed). Pins the plugin's seed
#     (``--randomly-seed=<seed>``) so test ORDER is fixed run-to-run. Keeping
#     randomization active with a *fixed* seed (rather than disabling it) means
#     order-dependence is still exercised -- just deterministically -- so a unit
#     that genuinely breaks ordering still fails its own gate, while a
#     pre-existing sibling flake is reproducible.
#
# ``--randomly-seed`` is a CLI option that ONLY exists when ``pytest-randomly``
# is installed; injecting it into ``PYTEST_ADDOPTS`` for a repo WITHOUT the
# plugin would make every pytest invocation error on an unknown option. So the
# caller probes the target repo once (``pytest_randomly_available``) and passes
# the result as ``pin_randomly`` -- fail-safe: when the plugin is absent we
# still pin ``PYTHONHASHSEED`` and never destabilise the gate.
# ---------------------------------------------------------------------------

#: pytest-randomly's seed flag. A directive that already sets a seed in
#: ``PYTEST_ADDOPTS`` is normalised so the pinned seed is the only one present
#: (a duplicate flag would let pytest-randomly pick the last-wins value
#: silently, defeating reproducibility).
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


# ---------------------------------------------------------------------------
# Command-path extraction + classification for the validator path lints
# (TDI-001 verify-ac working-directory contract, TDI-004 deferred-vs-command,
# TDI-005 AC referential integrity). Pure, stdlib-only, fully unit-testable.
# ---------------------------------------------------------------------------

#: A ``$(...)`` command substitution or a backtick substitution. Stripped before
#: extracting literal path operands so a substitution's contents are not mistaken
#: for a literal path the author must back.
_CMD_SUBSTITUTION_RE: re.Pattern[str] = re.compile(r"\$\([^)]*\)|`[^`]*`")

#: A ``grep`` (optionally negated) whose file operands come from a ``$(find ...)``
#: substitution. When ``find`` yields zero operands the recursive ``grep`` falls
#: back to scanning the whole working tree -- a silent, unbounded check (TDI-001
#: layer 3 / AC-5). Matched on the command head + the substitution operand.
_FIND_FEEDS_GREP_RE: re.Pattern[str] = re.compile(r"\bgrep\b[^|;&]*\$\(\s*find\b", re.IGNORECASE)

#: File extensions that mark a bare (slash-less) token as a path operand.
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


#: Runnable project tools/test-runners available in the orchestrator's execution
#: environment (the same environment ``verify-ac`` and the review judges run in).
#: A ``type=deferred`` directive whose reason names one of these is almost always
#: a mis-classified runnable check (TDI-004).
_RUNNABLE_TOOL_RE: re.Pattern[str] = re.compile(
    r"\b("
    r"terraform|terragrunt|tofu|terratest|tf-test|cdktf|cdk|sam|"
    r"pytest|go\s+test|make|npm|yarn|pnpm|cargo|gradle|mvn|tox|"
    r"toolchain|at\s+execution\s+time"
    r")\b",
    re.IGNORECASE,
)

#: Signals that a deferred check genuinely cannot run in the orchestrator
#: environment (live mutation, credentials, manual sign-off). When present these
#: veto the runnable-tool finding so a legitimately operator-only directive that
#: happens to name a tool (e.g. "real production terragrunt apply against a live
#: account") is not flagged.
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


# ---------------------------------------------------------------------------
# Verification item model + parser
# ---------------------------------------------------------------------------

_VERIFICATION_HEADER = "Verification"

# A ``## Verification`` directive line begins with ``- VERIFY``.
_VERIFY_LINE_RE: re.Pattern[str] = re.compile(r"^\s*-\s*VERIFY\b(.*)$", re.MULTILINE)
# ``cmd=`...```` -- captured first so a ``|`` inside the command does not break field splitting.
_CMD_FIELD_RE: re.Pattern[str] = re.compile(r"cmd\s*=\s*`([^`]*)`")
# AC ids in the leading segment (before the first ``|``): ``AC-3``, ``AC-FINAL-001`` ...
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
    # Extract cmd first (it may contain a literal ``|``), then strip it out.
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

    return VerificationItem(
        ac_ids=ac_ids,
        vtype=vtype,
        command=command,
        tool=fields.get("tool") or detect_iac_tool(command),
        expect_exit=expect_exit,
        owner=fields.get("owner"),
        reason=fields.get("reason"),
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


# ---------------------------------------------------------------------------
# Evidence model -- the ledger written by ``devbench verify-ac`` and read by the
# done-gate (deterministic) and the iac_review judge (qualitative).
# ---------------------------------------------------------------------------


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
    # Map each AC id to the best (lowest |exit - expect|) record covering it.
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


# ---------------------------------------------------------------------------
# Evidence ledger persistence -- the single source of truth for the on-disk
# layout, shared by the writer (``devbench verify-ac``) and the readers (the
# done-gate in ``BacklogManager`` and the ``iac_review`` judge). Keeping the
# path convention here (rather than duplicating it in the CLI and the manager)
# is what lets the gate reliably load exactly what the runner wrote.
#
# Layout under the workspace root::
#
#     .devbench/evidence/<task-id>/<attempt>/<sanitized-ac>.log   (per-AC artifact)
#     .devbench/evidence/<task-id>/<attempt>/evidence.json        (ledger: list[EvidenceRecord])
#     .devbench/evidence/<task-id>/latest.json                    ({"attempt": <n>}) pointer
# ---------------------------------------------------------------------------

_EVIDENCE_SUBDIR = ".devbench/evidence"
_LEDGER_FILENAME = "evidence.json"
_LATEST_POINTER_FILENAME = "latest.json"
#: Characters allowed verbatim in an artifact filename; everything else is
#: collapsed to ``_`` so a multi-AC item or an exotic AC id cannot escape the
#: attempt directory or collide with the ledger filename.
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


#: Proof sentinels a trimmed log must retain regardless of position. These are
#: generic CI/IaC/test markers (not coupled to any backlog or provider): a log
#: that contains none of them falls back to a pure head+tail window, which is
#: still strictly better than the old tail-only slice. Substrings are matched
#: anywhere in a line; line-prefix markers (``ok ``/``FAIL ``) are the Go test
#: per-package summary lines and are matched after stripping leading whitespace.
_TRIM_SENTINEL_SUBSTRINGS: tuple[str, ...] = (
    "Apply complete!",  # terraform/terratest real apply succeeded
    "Destroy complete!",  # terraform/terratest cleanup succeeded
    "No changes.",  # terraform idempotency re-plan (no drift)
    "--- PASS:",  # go test per-test pass
    "--- FAIL:",  # go test per-test fail
    "panic:",  # go/runtime panic
    "Error:",  # generic tool error line
)
_TRIM_SENTINEL_LINE_PREFIXES: tuple[str, ...] = (
    "ok ",  # go test package summary (pass)
    "FAIL ",  # go test package summary (fail)
)
#: Marker emitted between two non-contiguous retained regions. ``{n}`` is the
#: number of bytes (characters) dropped between them.
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
    # Per-line byte cost including the newline that joins it to the next line.
    # The final line carries no trailing newline, matching ``"\n".join``.
    line_costs = [len(line) + (1 if i < len(lines) - 1 else 0) for i, line in enumerate(lines)]

    # Reserve roughly a quarter of the budget for head context and a quarter for
    # the trailing summary; the middle half is spent on sentinel lines. The tail
    # is favoured on ties so the package summary is never the casualty.
    head_budget = max_bytes // 4
    tail_budget = max_bytes // 4

    keep: list[bool] = [False] * len(lines)

    # (1) Head window: take whole lines from the front until the head budget runs
    # out. ``head_kept`` is the count of retained leading lines, used to bound the
    # tail loop so the two windows cannot collide.
    used = 0
    head_kept = 0
    for i, cost in enumerate(line_costs):
        if used + cost > head_budget and used > 0:
            break
        keep[i] = True
        used += cost
        head_kept = i + 1

    # (3) Tail window: take whole lines from the back until the tail budget runs
    # out. The head loop stops at <= max_bytes // 4 bytes, so with the over-budget
    # guard above (len(text) > max_bytes) the head and tail windows are always
    # disjoint and need no overlap check here.
    used = 0
    for i in range(len(lines) - 1, head_kept - 1, -1):
        cost = line_costs[i]
        if used + cost > tail_budget and used > 0:
            break
        keep[i] = True
        used += cost

    # Running total of everything already retained by head + tail.
    retained = sum(cost for i, cost in enumerate(line_costs) if keep[i])

    # (2) Sentinel lines, in original order, until the overall budget is reached.
    for i, line in enumerate(lines):
        if keep[i] or not _line_is_sentinel(line):
            continue
        cost = line_costs[i]
        if retained + cost > max_bytes:
            continue
        keep[i] = True
        retained += cost

    # Re-assemble in original order, inserting an elision marker for every gap
    # of dropped lines between two retained regions.
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
