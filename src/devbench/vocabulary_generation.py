"""Generate the vocabulary docs table and prompt sentences from JUDGE_CATEGORIES.

Spec `integration-reality-gates-hardening.md` section 4.10 (PM-4, decision
D-4), section 5.7 (guard-marker format), Section 0.4 (behaviour-change
notice), G5 (worked example).

Before this module existed, the codes in
``devbench.backlog.review_feedback_vocabulary.JUDGE_CATEGORIES`` were
hand-maintained THREE times: once as the source of truth, once as tables in
``docs/review-feedback-vocabulary.md``, and once as inline sentences in five
judge prompts. Every new code this drifted further out of sync by
construction -- nothing enforced the copies matched the source.

``generate_all`` (invoked via ``make generate-vocabulary``, which runs this
module as ``python -m devbench.vocabulary_generation``) renders both
surfaces from ``JUDGE_CATEGORIES``, writing only the content between
``<!-- generated:vocabulary -->`` / ``<!-- /generated:vocabulary -->`` guard
markers (spec 5.7) so hand-written prose outside the markers is preserved
byte for byte. Generation is idempotent: a second consecutive run produces
zero diff (AC-11). A target file missing its guard markers, or carrying an
unterminated pair, raises :class:`GuardMarkerError` naming the file (and,
for an unterminated pair, the opening marker's line number) rather than
being silently skipped -- a skip would leave a stale, hand-edited copy in
the tree with no signal that it drifted.

The doc table's "Meaning" / "Example remediation" prose is not derivable
from ``JUDGE_CATEGORIES`` (which stores only codes) so it is captured once,
here, in ``CATEGORY_DESCRIPTIONS`` -- the single remaining hand-maintained
copy of that prose, validated against ``JUDGE_CATEGORIES`` at generation
time so the two cannot silently drift apart.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

from devbench.backlog.review_feedback_vocabulary import JUDGE_CATEGORIES
from devbench.utils.io import atomic_write_text

# ---------------------------------------------------------------------------
# Guard-marker constants (spec 5.7). These are the exact literal strings
# transcribed from the spec; both the generator and the generated surfaces
# use these constants (surfaces embed the literal text; the generator
# locates it via these same constants), so there is exactly one place that
# defines the marker grammar.
# ---------------------------------------------------------------------------

GUARD_MARKER_START: Final[str] = "<!-- generated:vocabulary -->"
GUARD_MARKER_END: Final[str] = "<!-- /generated:vocabulary -->"


class GuardMarkerError(ValueError):
    """Raised when a target surface's guard markers are missing or malformed (spec Section 7)."""


# ---------------------------------------------------------------------------
# Generation targets.
# ---------------------------------------------------------------------------

#: Path (repo-relative) of the docs surface, relative to the repo root.
DOC_RELATIVE_PATH: Final[str] = "docs/review-feedback-vocabulary.md"

#: Repo-relative prompt-file path -> the judge whose vocabulary sentence it carries.
#: Iteration order matches the doc's top-to-bottom section order (``DOC_JUDGES``)
#: only by convention; the two are independent mappings kept in sync by
#: ``TestModuleConsistency`` in the test suite.
PROMPT_TARGETS: Final[dict[str, str]] = {
    "plugin/devbench-orchestrate/agents/review_team/code-reviewer.md": "code_review",
    "plugin/devbench-orchestrate/agents/review_team/test-reviewer.md": "test_review",
    "plugin/devbench-orchestrate/agents/review_team/doc-reviewer.md": "doc_review",
    "plugin/devbench-orchestrate/agents/review_team/changes-manifest.md": "changes_manifest",
    "plugin/devbench-orchestrate/agents/security-reviewer.md": "security_review",
}

#: Judges rendered as tables in the docs surface, in the file's top-to-bottom
#: section order. The doc file carries this many sequential guard-marker
#: pairs; ``generate_doc_file`` replaces them left to right, one per judge.
#: ``manifest_amender`` is intentionally excluded: its docs section mirrors a
#: different source of truth (``AMENDER_REJECTION_CATEGORIES``) and carries
#: no prompt-file sentence, so it stays hand-maintained prose outside this
#: module's scope.
DOC_JUDGES: Final[tuple[str, ...]] = (
    "code_review",
    "test_review",
    "doc_review",
    "changes_manifest",
    "security_review",
)

#: Per-code "Meaning" / "Example remediation" prose for the docs table.
#: Validated against ``JUDGE_CATEGORIES`` in ``render_doc_table`` -- a
#: mismatch (added/removed code on one side only) raises loudly rather than
#: rendering a table that silently omits or invents a code.
CATEGORY_DESCRIPTIONS: Final[dict[str, dict[str, tuple[str, str]]]] = {
    "code_review": {
        "MAKE_VALIDATE_FAILURE": (
            "`make validate` returned non-zero in the staged diff",
            "Run `make validate` locally, fix the named failure, re-stage.",
        ),
        "HARDCODED_URL": (
            "Hardcoded URL / hostname / endpoint",
            "Read from environment variable; document the env var in the config docs.",
        ),
        "MISSING_AC_EVIDENCE": (
            "Diff does not satisfy a required Acceptance Criterion",
            "Add the missing implementation; reference the AC ID in the TDD log.",
        ),
        "SOLID_VIOLATION": (
            "Single-responsibility / open-closed / etc. violation",
            "Refactor to comply with the named SOLID principle.",
        ),
        "SECURITY_BYPASS_ANNOTATION": (
            "`# noqa` / `# nosec` / equivalent suppression",
            "Remove the suppression and fix the underlying finding.",
        ),
        "SCOPE_VIOLATION": (
            "Diff touches files outside the Changes Manifest",
            "Either revert the out-of-scope change OR file an amendment request.",
        ),
        "MANIFEST_TODO_UNFILLED": (
            "Manifest still has a `TBD` placeholder row",
            "Replace placeholder with real file/change rows before claim.",
        ),
        "AGENT_LOG_CONTRADICTS_DIFF": (
            "TDD log claims work that does not appear in the diff",
            "Reconcile log + diff; re-stage if work was lost, or trim the log claim.",
        ),
        "NEWLY_REACHABLE_PATH_UNVERIFIED": (
            "Bug-fix-shaped task has no `[NEWLY_REACHABLE]` entry, or an entry with unverified paths",
            "Enumerate the paths the fix newly unlocks and live-verify each at smoke-test level; "
            "see `docs/newly-reachable-paths.md`.",
        ),
        "UNREACHABLE_ARTIFACT": (
            "New component/hook/slice/function has zero non-test importers per `devbench check-reachability` evidence",
            "Import and wire the artifact into its real composition root (route table, parent "
            "container, shell), or add a `devbench-defer-reachability: <reason>` comment if "
            "intentionally deferred.",
        ),
    },
    "test_review": {
        "GIT_COMPLETENESS": (
            "Test files exist on disk but are not staged",
            "`git add` the test files.",
        ),
        "STUB_TEST": (
            "Placeholder test (`assert True`, TODO body, etc.)",
            "Replace with a real test that can fail when the code regresses.",
        ),
        "COVERAGE_REGRESSION": (
            "Coverage on the gated modules dropped below 100%",
            "Add tests that exercise every modified branch.",
        ),
        "TDD_CYCLE_MISSING": (
            "No `[RED]` / `[GREEN]` / `[REFACTOR]` audit entries",
            "Re-run the TDD cycle and log the phases via `devbench log-tdd`.",
        ),
        "DRY_VIOLATION": (
            "Duplicated test logic that should be parameterised",
            "Extract a helper or use `pytest.mark.parametrize`.",
        ),
        "FIXTURE_CATALOG_MISMATCH": (
            "`devbench check-fixture-consistency` reported a `FAIL:` finding -- a mock/fixture "
            "file references an identifier absent from its designated canonical dataset, or a "
            "canonical source's coverage fell short of a declared `expected_count`",
            "Fix the fixture to reference a real canonical key, add the value to "
            "`gates.fixture_consistency.scan[].allow_missing` if it is an intentional edge case, "
            "or complete the backfill to satisfy `expected_count`.",
        ),
        "COMPOSITION_ROOT_MISSING": (
            "Only coverage for a state-consuming UI component is an isolated render with "
            "hand-supplied props/mocked store/DI container "
            "(caylent-solutions/devbench-internal-backlog#11)",
            "Add a test that renders/exercises the component through the app's real composition "
            "root, or a documented smallest-real-ancestor exception -- see "
            "`docs/composition-root-testing.md`.",
        ),
        "LAYOUT_STUB_WITHOUT_LIVE_TEST": (
            "Diff stubs a DOM-layout/rendering primitive (`offsetHeight`, "
            "`getBoundingClientRect`, `ResizeObserver`, etc.) for a `[LAYOUT-AC]`-tagged AC with "
            "no companion real-render test for the same AC",
            "Add a companion real-render/live-browser test (e.g. Playwright) at the "
            "viewport/breakpoint the AC names; the stub alone does not prove the fix.",
        ),
    },
    "doc_review": {
        "README_SYNC": (
            "README out of sync with code change",
            "Update the README in the same commit as the code.",
        ),
        "CHANGELOG_SYNC": (
            "CHANGELOG missing the matching entry",
            "Add a bullet under the v-next block.",
        ),
        "API_DOCS_STALE": (
            "Docstring / API doc lags behind the implementation",
            "Update the docstring; verify any generated docs.",
        ),
        "EVIDENCE_BASED_CLAIM": (
            'Speculative quantitative claim ("30% faster" without data)',
            "Restate qualitatively or cite the measurement.",
        ),
        "CONFIG_DOCS": (
            "New env var / config field undocumented",
            "Document the new variable in `docs/cli-reference.md` / `sample-config.yaml`.",
        ),
    },
    "changes_manifest": {
        "SCOPE_GAP": (
            "Manifest declares files not in the diff",
            "Either implement the missing change OR remove the row.",
        ),
        "MANIFEST_MISMATCH": (
            "Diff vs. manifest row disagreement",
            "Update the row's `Change` cell to match the actual edit.",
        ),
        "STAGING_GAP": (
            "Diff has files outside the manifest, no amendment filed",
            "File an amendment OR revert the out-of-scope file.",
        ),
        "OUT_OF_SCOPE_FILES": (
            "Files clearly belonging to another task",
            "File a proposal for a follow-up task; revert here.",
        ),
    },
    "security_review": {
        "SECRET_LEAK": (
            "Credential / token / key materialised in code or logs",
            "Rotate the secret; move to AWS Secrets Manager / Parameter Store.",
        ),
        "UNAUTHORIZED_DEP": (
            "Dependency added without security review",
            "Open a dependency-vetting ticket; remove the dep or wait for review.",
        ),
        "SCOPE_VIOLATION": (
            "Security-relevant change outside the manifest",
            "File an amendment with a security justification.",
        ),
    },
}


# ---------------------------------------------------------------------------
# Guard-marker block replacement (the single implementation of the
# guard-marker contract, used by both surface kinds).
# ---------------------------------------------------------------------------


def _find_guard_block(content: str, source: str, *, search_from: int = 0) -> tuple[int, int]:
    """Locate the first guard-marker pair in *content* at or after *search_from*.

    Args:
        content: Full text of the target surface.
        source: Human-readable identifier for the surface (typically its
            file path), used in raised error messages.
        search_from: Offset into *content* to start searching from -- lets
            a caller with multiple sequential pairs (the docs surface)
            process them left to right without re-matching an already
            replaced pair.

    Returns:
        A ``(start, end)`` tuple: *start* is the index of
        :data:`GUARD_MARKER_START`'s first character; *end* is the index of
        :data:`GUARD_MARKER_END`'s first character.

    Raises:
        GuardMarkerError: *content* has no :data:`GUARD_MARKER_START` at or
            after *search_from* (naming *source*), or has an opening marker
            with no matching :data:`GUARD_MARKER_END` (naming *source* and
            the opening marker's 1-indexed line number).
    """
    start = content.find(GUARD_MARKER_START, search_from)
    if start == -1:
        raise GuardMarkerError(
            f"'{source}' has no '{GUARD_MARKER_START}' guard-marker pair. Add "
            f"'{GUARD_MARKER_START}' ... '{GUARD_MARKER_END}' around the block to generate, "
            f"then re-run 'make generate-vocabulary'."
        )
    end = content.find(GUARD_MARKER_END, start + len(GUARD_MARKER_START))
    if end == -1:
        line_no = content.count("\n", 0, start) + 1
        raise GuardMarkerError(
            f"'{source}' line {line_no}: '{GUARD_MARKER_START}' has no matching "
            f"'{GUARD_MARKER_END}'. Close the guard-marker block, then re-run "
            f"'make generate-vocabulary'."
        )
    return start, end


def replace_guarded_block(content: str, new_inner: str, *, source: str, search_from: int = 0) -> tuple[str, int]:
    """Replace one guard-marker pair's inner content, leaving everything else untouched.

    Args:
        content: Full text of the target surface.
        new_inner: Replacement content to place between the markers (a
            single trailing/leading newline is added by this function; do
            not include one in *new_inner*).
        source: Human-readable identifier for the surface, used in raised
            error messages.
        search_from: Offset into *content* to start searching for the pair
            (see :func:`_find_guard_block`).

    Returns:
        A ``(new_content, offset)`` tuple. *new_content* is *content* with
        exactly the located pair's inner text replaced by *new_inner*;
        every byte outside the pair is unchanged. *offset* is the index in
        *new_content* immediately before the (unmodified) closing marker --
        pass it as the next call's *search_from* to process the next pair
        in a multi-pair surface.

    Raises:
        GuardMarkerError: See :func:`_find_guard_block`.
    """
    start, end = _find_guard_block(content, source, search_from=search_from)
    inserted = "\n" + new_inner + "\n"
    new_content = content[: start + len(GUARD_MARKER_START)] + inserted + content[end:]
    offset = start + len(GUARD_MARKER_START) + len(inserted)
    return new_content, offset


# ---------------------------------------------------------------------------
# Per-surface renderers.
# ---------------------------------------------------------------------------


def render_prompt_sentence(judge: str) -> str:
    """Render the single generated sentence embedded in a judge prompt.

    Args:
        judge: A key of ``JUDGE_CATEGORIES``.

    Returns:
        ``"Every `code` MUST come from the controlled vocabulary for `<judge>`: `<CODE1>`, ...."``
        with codes sorted alphabetically for deterministic, idempotent output.

    Raises:
        ValueError: *judge* is not a key of ``JUDGE_CATEGORIES``.
    """
    if judge not in JUDGE_CATEGORIES:
        raise ValueError(f"unknown judge '{judge}'; must be one of {sorted(JUDGE_CATEGORIES)}.")
    codes = ", ".join(f"`{code}`" for code in sorted(JUDGE_CATEGORIES[judge]))
    return f"Every `code` MUST come from the controlled vocabulary for `{judge}`: {codes}."


def render_doc_table(judge: str) -> str:
    """Render one judge's markdown table for the docs surface.

    Args:
        judge: A key of ``DOC_JUDGES`` (and of ``JUDGE_CATEGORIES``).

    Returns:
        A three-column ``| Code | Meaning | Example remediation |`` markdown
        table (header, separator, one row per code, codes sorted
        alphabetically for deterministic, idempotent output).

    Raises:
        ValueError: *judge* is not a key of ``JUDGE_CATEGORIES``, or
            ``CATEGORY_DESCRIPTIONS[judge]``'s codes do not exactly match
            ``JUDGE_CATEGORIES[judge]`` (naming the judge and the
            mismatched codes).
    """
    if judge not in JUDGE_CATEGORIES:
        raise ValueError(f"unknown judge '{judge}'; must be one of {sorted(JUDGE_CATEGORIES)}.")
    descriptions = CATEGORY_DESCRIPTIONS[judge]
    codes = JUDGE_CATEGORIES[judge]
    if set(descriptions) != set(codes):
        missing = sorted(set(codes) - set(descriptions))
        extra = sorted(set(descriptions) - set(codes))
        raise ValueError(
            f"CATEGORY_DESCRIPTIONS['{judge}'] is out of sync with JUDGE_CATEGORIES['{judge}']: "
            f"missing description for {missing or 'none'}; description for unknown code "
            f"{extra or 'none'}. Update CATEGORY_DESCRIPTIONS in vocabulary_generation.py to match."
        )
    lines = ["| Code | Meaning | Example remediation |", "|------|---------|---------------------|"]
    for code in sorted(codes):
        meaning, remediation = descriptions[code]
        lines.append(f"| `{code}` | {meaning} | {remediation} |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# File-level generation.
# ---------------------------------------------------------------------------


def generate_prompt_file(path: Path, judge: str) -> None:
    """Regenerate one judge prompt's guard-marked vocabulary sentence in place.

    Args:
        path: Absolute path of the prompt file.
        judge: The judge whose vocabulary sentence this prompt carries.

    Raises:
        GuardMarkerError: *path*'s content has no guard-marker pair, or an
            unterminated one.
        ValueError: *judge* is not a key of ``JUDGE_CATEGORIES``.
    """
    content = path.read_text(encoding="utf-8")
    new_content, _ = replace_guarded_block(content, render_prompt_sentence(judge), source=str(path))
    atomic_write_text(path, new_content)


def generate_doc_file(path: Path) -> None:
    """Regenerate every judge table in the docs surface in place.

    Processes ``DOC_JUDGES`` in order, replacing each judge's guard-marker
    pair left to right through the file.

    Args:
        path: Absolute path of ``docs/review-feedback-vocabulary.md``.

    Raises:
        GuardMarkerError: *path* is missing one of the expected guard-marker
            pairs, or one is unterminated.
        ValueError: A ``CATEGORY_DESCRIPTIONS`` entry is out of sync with
            ``JUDGE_CATEGORIES`` for one of ``DOC_JUDGES``.
    """
    content = path.read_text(encoding="utf-8")
    offset = 0
    for judge in DOC_JUDGES:
        content, offset = replace_guarded_block(content, render_doc_table(judge), source=str(path), search_from=offset)
    atomic_write_text(path, content)


def generate_all(repo_root: Path) -> list[Path]:
    """Regenerate every guard-marked surface under *repo_root*.

    Args:
        repo_root: Absolute path of the repository root (the directory
            containing ``Makefile`` and ``docs/``).

    Returns:
        The absolute paths written, in the order generated: the docs
        surface first, then each prompt file in ``PROMPT_TARGETS``
        declaration order.

    Raises:
        GuardMarkerError: Any target surface is missing its guard-marker
            pair, or has an unterminated one. Earlier surfaces in the
            generation order may already have been written when this is
            raised for a later one -- each individual write is atomic, but
            the overall run is not transactional across files.
        ValueError: A ``CATEGORY_DESCRIPTIONS`` entry is out of sync with
            ``JUDGE_CATEGORIES``.
    """
    written: list[Path] = []
    doc_path = repo_root / DOC_RELATIVE_PATH
    generate_doc_file(doc_path)
    written.append(doc_path)
    for relative_path, judge in PROMPT_TARGETS.items():
        prompt_path = repo_root / relative_path
        generate_prompt_file(prompt_path, judge)
        written.append(prompt_path)
    return written


# ---------------------------------------------------------------------------
# Script entry point (``make generate-vocabulary`` runs this module via
# ``python -m devbench.vocabulary_generation``).
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    """Return this checkout's own root, resolved from this module's file location.

    Three parents up from ``src/devbench/vocabulary_generation.py``.
    """
    return Path(__file__).resolve().parent.parent.parent


def main() -> int:
    """Regenerate every vocabulary surface in this checkout.

    Returns:
        ``0`` on success. ``1`` if any target surface's guard markers are
        missing/malformed or a ``CATEGORY_DESCRIPTIONS`` entry drifted from
        ``JUDGE_CATEGORIES`` -- an ``ERROR:`` line naming the surface and
        the remedy is printed to stderr.
    """
    repo_root = _repo_root()
    try:
        written = generate_all(repo_root)
    except (GuardMarkerError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    for path in written:
        print(f"generated: {path.relative_to(repo_root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
