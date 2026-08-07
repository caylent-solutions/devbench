"""Copy-pattern permission/eligibility flag write-path audit (QA finding 07).

Specs sometimes introduce a new derived boolean permission/eligibility
field by instructing the implementer to "follow the exact existing
pattern of `<some-existing-flag>`." Left unchecked, ``spec-to-backlog``
would translate that into tasks for "add the field to the state slice"
and "gate the UI on the field" -- but never a task for "wire this flag
to a real (or explicit placeholder) data source." If the referenced
existing flag itself has no working write-path (hardcoded to a default
with no setter anywhere), the new flag silently inherits the same
defect, and nothing in backlog generation ever surfaces that risk.

This module gives the ``spec-to-backlog`` skill (Step 3b) a deterministic,
best-effort way to check a referenced flag's write-path status in the
target repo checkout BEFORE backlog generation proceeds, and to locate an
existing placeholder/mock permission-provider seam so the mandatory
new-flag write-path task (Step 4a) can name a concrete destination
instead of leaving it unspecified.

Heuristic, not an oracle
========================

Static analysis cannot reliably prove "this flag is never written" across
arbitrary languages and frameworks. This audit is a fast, source-grep-based
heuristic meant to surface a finding for a human/agent to confirm or
dismiss -- it is deliberately conservative: any verdict other than
``"live"`` (:attr:`WritePathAudit.is_verified_live` is ``False``) is
treated as a BLOCKING finding by the skill, requiring explicit operator
acknowledgement before Step 4 proceeds for that spec clause. False
positives (calling a real write path "not live") cost a confirmation
round-trip; false negatives (calling a dead flag "live") would silently
reproduce exactly the defect this audit exists to catch, so the
classifier never guesses in that direction.

The skill invokes this module via the Bash tool, e.g.::

    uv run python -c "from devbench.plugin_helpers.permission_flag_writepath \\
        import audit_write_path; from pathlib import Path; \\
        print(audit_write_path(Path('<target-repo-checkout>'), \\
        '<existing-flag-name>').render())"
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# File extensions the audit scans. Kept narrow and explicit -- scanning
# vendored/build artefacts produces noise that undermines trust in the
# finding.
_SOURCE_EXTENSIONS: frozenset[str] = frozenset(
    {".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".go", ".rb", ".cs"}
)

# Directories excluded from the scan: dependency trees, build output, and
# the devbench backlog tree itself (a flag name can legitimately appear in
# work-unit prose without that being a write path).
_EXCLUDED_DIR_NAMES: frozenset[str] = frozenset(
    {
        "node_modules",
        ".git",
        "dist",
        "build",
        "out",
        "__pycache__",
        ".venv",
        "venv",
        "backlog",
        ".mypy_cache",
        ".pytest_cache",
        "coverage",
    }
)

# A relative-path signal that the assignment site is a static default /
# type / constant definition rather than a runtime write path.
_DEFAULT_SIGNAL_RE = re.compile(
    r"(default|initial[-_]?state|constants?\b|types?\b|schema|models?\b|fixtures?)",
    re.IGNORECASE,
)

# A relative-path signal that the assignment site is code that actually
# mutates state at runtime in response to an external event.
_LIVE_WRITE_SIGNAL_RE = re.compile(
    r"(reducer|actions?\b|handler|service|controller|\bapi\b|endpoint|route|"
    r"provider|mutation|resolver|\bstore\b|slice|repository|\bdao\b|command|"
    r"use[-_]?case|middleware)",
    re.IGNORECASE,
)

# A relative-path signal that a file is a placeholder/mock permission or
# eligibility data-provider seam -- the destination the mandatory new-flag
# write-path task should register into when one exists (issue AC 3).
_PLACEHOLDER_SEAM_RE = re.compile(
    r"(mock|placeholder|stub|fake)[^/\\]*(permission|entitlement|eligib|flag)",
    re.IGNORECASE,
)

_VERDICT_LIVE = "live"
_VERDICT_DEFAULT_ONLY = "default_only"
_VERDICT_NO_WRITE_PATH = "no_write_path"
_VERDICT_NOT_FOUND = "not_found"
_VERDICT_INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class FlagAssignmentSite:
    """One line in the target repo that looks like it writes the flag."""

    relative_path: str
    line_number: int
    line_text: str


@dataclass(frozen=True)
class WritePathAudit:
    """Result of auditing a single flag name's write-path status."""

    flag_name: str
    verdict: str
    assignment_sites: tuple[FlagAssignmentSite, ...]
    mention_count: int

    @property
    def is_verified_live(self) -> bool:
        """``True`` only when the audit found a confirmed runtime write site.

        Every other verdict (``default_only``, ``no_write_path``,
        ``not_found``, ``indeterminate``) is treated as a blocking finding
        by the calling skill step -- see module docstring.
        """
        return self.verdict == _VERDICT_LIVE

    def render(self) -> str:
        """Render the `[PERMISSION_FLAG_WRITE_PATH_AUDIT]` audit line + site list."""
        lines = [
            f"[PERMISSION_FLAG_WRITE_PATH_AUDIT] {self.flag_name}: "
            f"verdict={self.verdict} mentions={self.mention_count} "
            f"assignment_sites={len(self.assignment_sites)}"
        ]
        if self.assignment_sites:
            for site in self.assignment_sites:
                lines.append(f"  - {site.relative_path}:{site.line_number} | {site.line_text.strip()}")
        else:
            lines.append("  (no assignment/setter sites found)")
        return "\n".join(lines)


def _iter_source_files(repo_root: Path) -> list[Path]:
    """Return every scanned source file under *repo_root*, excluded dirs pruned."""
    files: list[Path] = []
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in _SOURCE_EXTENSIONS:
            continue
        if _EXCLUDED_DIR_NAMES.intersection(path.relative_to(repo_root).parts[:-1]):
            continue
        files.append(path)
    return sorted(files)


def _assignment_regex(flag_name: str) -> re.Pattern[str]:
    """Regex matching an assignment- or setter-shaped occurrence of *flag_name*.

    Matches three shapes:

    - ``<flag> = value`` / ``<flag>: Type = value`` / ``obj.<flag> =
      value`` (via the leading ``\\b`` word boundary). Deliberately
      excludes comparisons (``==``, ``!=``, ``>=``, ``<=``) via the
      negative lookahead after the ``=``.
    - ``<flag>: <literal>`` object-literal key/value shorthand (JS/TS/
      Python dict), e.g. ``isPremiumEligible: false,`` -- distinguished
      from an interface/type field declaration (``isPremiumEligible:
      boolean;``) by requiring the value to be a literal (bool/null/
      number/string), not a bare identifier.
    - ``set<flag>(`` / ``set_<flag>(`` setter calls.
    """
    escaped = re.escape(flag_name)
    assignment = rf"\b{escaped}\b\s*(:\s*[\w\[\].,<> ]+)?\s*=\s*(?!=)"
    literal = r"(true|false|null|undefined|none|-?\d+(\.\d+)?|'[^']*'|\"[^\"]*\")"
    object_literal = rf"\b{escaped}\b\s*:\s*{literal}\s*[,;]?\s*$"
    setter = rf"\bset[_-]?{escaped}\s*\("
    return re.compile(f"(?:{assignment})|(?:{object_literal})|(?:{setter})", re.IGNORECASE)


def audit_write_path(repo_root: Path, flag_name: str) -> WritePathAudit:
    """Audit whether *flag_name* has a live, non-default write path in *repo_root*.

    Args:
        repo_root: Target repo checkout to scan.
        flag_name: The existing flag name a spec clause instructs a new
            field to "follow the pattern of."

    Returns:
        A :class:`WritePathAudit` recording every assignment/setter-shaped
        occurrence found and a conservative verdict.

    Raises:
        FileNotFoundError: when ``repo_root`` does not exist. A typo'd
            repo path is a defect the caller should see immediately, not
            something this helper silently returns an empty result for.
    """
    if not repo_root.exists():
        raise FileNotFoundError(
            f"repo_root does not exist: {repo_root}. Pass the target repo checkout to audit."
        )

    assignment_re = _assignment_regex(flag_name)
    sites: list[FlagAssignmentSite] = []
    mention_count = 0

    for path in _iter_source_files(repo_root):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        relative_path = path.relative_to(repo_root).as_posix()
        for line_number, line in enumerate(text.splitlines(), start=1):
            if flag_name not in line:
                continue
            mention_count += 1
            if assignment_re.search(line):
                sites.append(
                    FlagAssignmentSite(
                        relative_path=relative_path,
                        line_number=line_number,
                        line_text=line,
                    )
                )

    verdict = _classify(sites, mention_count)
    return WritePathAudit(
        flag_name=flag_name,
        verdict=verdict,
        assignment_sites=tuple(sites),
        mention_count=mention_count,
    )


def _classify(sites: list[FlagAssignmentSite], mention_count: int) -> str:
    """Return a conservative verdict for the collected assignment sites."""
    if mention_count == 0:
        return _VERDICT_NOT_FOUND
    if not sites:
        return _VERDICT_NO_WRITE_PATH

    live_sites = [
        s
        for s in sites
        if _LIVE_WRITE_SIGNAL_RE.search(s.relative_path) and not _DEFAULT_SIGNAL_RE.search(s.relative_path)
    ]
    if live_sites:
        return _VERDICT_LIVE

    default_sites = [s for s in sites if _DEFAULT_SIGNAL_RE.search(s.relative_path)]
    if len(default_sites) == len(sites):
        return _VERDICT_DEFAULT_ONLY

    return _VERDICT_INDETERMINATE


def find_placeholder_seam(repo_root: Path) -> str | None:
    """Return the relative path of an existing placeholder/mock permission-provider seam, if any.

    Used by Step 4a of ``spec-to-backlog`` so the mandatory new-flag
    write-path task can name a concrete minimum-viable destination
    (issue AC 3) instead of leaving "wire this to real or placeholder
    data" unspecified. Returns the first match in sorted (deterministic)
    order, or ``None`` when no such seam exists in the checkout.

    Raises:
        FileNotFoundError: when ``repo_root`` does not exist.
    """
    if not repo_root.exists():
        raise FileNotFoundError(
            f"repo_root does not exist: {repo_root}. Pass the target repo checkout to scan."
        )

    candidates: list[str] = []
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(repo_root)
        if _EXCLUDED_DIR_NAMES.intersection(relative.parts[:-1]):
            continue
        relative_posix = relative.as_posix()
        if _PLACEHOLDER_SEAM_RE.search(relative_posix):
            candidates.append(relative_posix)

    return sorted(candidates)[0] if candidates else None


def render_blocking_finding(new_field_name: str, audit: WritePathAudit) -> str:
    """Render the `[BLOCKING_FINDING]` line for a non-live referenced-flag audit.

    The calling skill step (Step 3b) emits this line and requires explicit
    operator acknowledgement before Step 4 proceeds for the spec clause
    that referenced ``audit.flag_name`` -- see module docstring.
    """
    sites = "; ".join(f"{s.relative_path}:{s.line_number}" for s in audit.assignment_sites) or "(none found)"
    return (
        f"[BLOCKING_FINDING] Spec instructs new field '{new_field_name}' to follow the pattern of "
        f"'{audit.flag_name}', but '{audit.flag_name}' has no verified live write-path "
        f"(verdict={audit.verdict}). Assignment/setter sites found: {sites}. Copying this pattern "
        "would propagate the same defect to the new field. Confirm with the operator (spec "
        "amendment, or confirmation that a fix is already planned) before generating tasks that "
        "assume this pattern is sound."
    )
