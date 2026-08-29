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

``find_drifted_surfaces`` (invoked via ``make check-vocabulary-drift``,
which runs this module in check mode as
``python -m devbench.vocabulary_generation --check``) regenerates every
surface listed by :func:`all_generated_relative_paths` into a scratch
directory and diffs it against the committed tree, never writing to the
tree it inspects. It reuses ``all_generated_relative_paths`` -- the same
enumeration ``generate_all`` iterates -- so there is exactly one place that
lists the guard-marked surfaces; a second hand-maintained copy of this list
in the build file would let a newly added surface sit outside the gate.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from collections.abc import Sequence
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

#: Command an operator runs to fix reported drift. A single module constant
#: so the text is never repeated (and so can never itself drift) across the
#: per-surface stderr lines `_run_check` prints, and the default remediation
#: text `_find_guard_block`/`replace_guarded_block` name in their own
#: raised errors for callers that do not pass their own command.
DRIFT_REMEDIATION_COMMAND: Final[str] = "make generate-vocabulary"


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
            "container, shell), or record a legitimate deferral with `uv run devbench log-waiver "
            "<judge> <unit-id> --gate reachability --target <t> --reason <r> --operator` "
            "(the operator is the only waiver authority for the reachability gate).",
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
            "Fix the fixture to reference a real canonical key, attach a "
            '`{"allow_missing": {"reason": "<non-empty reason>"}}` marker directly to the record '
            "in the scanned fixture file if it is an intentional edge case, or complete the "
            "backfill to satisfy `expected_count`.",
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


def _find_guard_block(
    content: str,
    source: str,
    *,
    search_from: int = 0,
    start_marker: str = GUARD_MARKER_START,
    end_marker: str = GUARD_MARKER_END,
    remediation_command: str = DRIFT_REMEDIATION_COMMAND,
    reject_duplicate: bool = False,
) -> tuple[int, int]:
    """Locate the first guard-marker pair in *content* at or after *search_from*.

    Args:
        content: Full text of the target surface.
        source: Human-readable identifier for the surface (typically its
            file path), used in raised error messages.
        search_from: Offset into *content* to start searching from -- lets
            a caller with multiple sequential pairs (the docs surface)
            process them left to right without re-matching an already
            replaced pair.
        start_marker: Opening guard-marker literal. Defaults to
            :data:`GUARD_MARKER_START`; a caller generating a distinctly
            named surface (so its own regeneration can never clobber this
            module's ``vocabulary`` block) passes its own marker text.
        end_marker: Closing guard-marker literal, paired with
            *start_marker*. Defaults to :data:`GUARD_MARKER_END`.
        remediation_command: Command named in every raised error's
            remediation text. Defaults to :data:`DRIFT_REMEDIATION_COMMAND`;
            a caller with its own regeneration entry point passes that
            command instead so the error message never suggests the wrong
            fix.
        reject_duplicate: When ``True``, also raise if a second
            *start_marker* occurs after the located pair -- for a surface
            whose marker name is used for exactly one block, so a second
            occurrence is unambiguously a stale leftover rather than a
            second intentional pair (unlike a multi-pair surface such as
            the docs table, which relies on *search_from* to process
            several pairs left to right and must leave duplicates alone).
            Defaults to ``False`` to preserve that multi-pair behaviour.

    Returns:
        A ``(start, end)`` tuple: *start* is the index of *start_marker*'s
        first character; *end* is the index of *end_marker*'s first
        character.

    Raises:
        GuardMarkerError: *content* has no *start_marker* at or after
            *search_from* (naming *source*); has an opening marker with no
            matching *end_marker* (naming *source* and the opening marker's
            1-indexed line number); or, when *reject_duplicate* is
            ``True``, has a second *start_marker* after the located pair
            (naming *source*).
    """
    start = content.find(start_marker, search_from)
    if start == -1:
        raise GuardMarkerError(
            f"'{source}' has no '{start_marker}' guard-marker pair. Add "
            f"'{start_marker}' ... '{end_marker}' around the block to generate, "
            f"then re-run '{remediation_command}'."
        )
    end = content.find(end_marker, start + len(start_marker))
    if end == -1:
        line_no = content.count("\n", 0, start) + 1
        raise GuardMarkerError(
            f"'{source}' has a '{start_marker}' (line {line_no}) with no matching "
            f"'{end_marker}'. Close the guard-marker block, then re-run "
            f"'{remediation_command}'."
        )
    if reject_duplicate:
        duplicate_start = content.find(start_marker, start + len(start_marker))
        if duplicate_start != -1:
            raise GuardMarkerError(
                f"'{source}' has more than one '{start_marker}' guard-marker pair; exactly one is "
                f"expected. Remove the duplicate block, then re-run '{remediation_command}'."
            )
    return start, end


def replace_guarded_block(
    content: str,
    new_inner: str,
    *,
    source: str,
    search_from: int = 0,
    start_marker: str = GUARD_MARKER_START,
    end_marker: str = GUARD_MARKER_END,
    remediation_command: str = DRIFT_REMEDIATION_COMMAND,
    reject_duplicate: bool = False,
) -> tuple[str, int]:
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
        start_marker: See :func:`_find_guard_block`.
        end_marker: See :func:`_find_guard_block`.
        remediation_command: See :func:`_find_guard_block`.
        reject_duplicate: See :func:`_find_guard_block`.

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
    start, end = _find_guard_block(
        content,
        source,
        search_from=search_from,
        start_marker=start_marker,
        end_marker=end_marker,
        remediation_command=remediation_command,
        reject_duplicate=reject_duplicate,
    )
    inserted = "\n" + new_inner + "\n"
    new_content = content[: start + len(start_marker)] + inserted + content[end:]
    offset = start + len(start_marker) + len(inserted)
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


def all_generated_relative_paths() -> tuple[str, ...]:
    """Every guard-marked surface this module generates or verifies, repo-relative.

    Single source of truth combining :data:`DOC_RELATIVE_PATH` and
    :data:`PROMPT_TARGETS`'s keys, in generation order (the docs surface
    first, then each prompt file in ``PROMPT_TARGETS`` declaration order).
    Both :func:`generate_all` and :func:`find_drifted_surfaces` iterate this
    one enumeration, so the two can never disagree on which surfaces exist
    -- the failure mode a hand-maintained, second copy of this list (as the
    ``Makefile`` previously carried) allowed.

    Returns:
        Repo-relative paths, doc surface first.
    """
    return (DOC_RELATIVE_PATH, *PROMPT_TARGETS.keys())


def _generate_surface(repo_root: Path, relative_path: str) -> Path:
    """Regenerate exactly one guard-marked surface identified by *relative_path*, in place.

    Args:
        repo_root: Absolute path of the repository root *relative_path* is
            resolved against.
        relative_path: One entry of :func:`all_generated_relative_paths`.

    Returns:
        The absolute path written.

    Raises:
        GuardMarkerError: The surface is missing its guard-marker pair, or
            has an unterminated one.
        ValueError: *relative_path* is the docs surface and a
            ``CATEGORY_DESCRIPTIONS`` entry has drifted from
            ``JUDGE_CATEGORIES`` for one of ``DOC_JUDGES``.
    """
    path = repo_root / relative_path
    if relative_path == DOC_RELATIVE_PATH:
        generate_doc_file(path)
    else:
        generate_prompt_file(path, PROMPT_TARGETS[relative_path])
    return path


def generate_all(repo_root: Path) -> list[Path]:
    """Regenerate every guard-marked surface under *repo_root*.

    Args:
        repo_root: Absolute path of the repository root (the directory
            containing ``Makefile`` and ``docs/``).

    Returns:
        The absolute paths written, in :func:`all_generated_relative_paths`
        order.

    Raises:
        GuardMarkerError: Any target surface is missing its guard-marker
            pair, or has an unterminated one. Earlier surfaces in the
            generation order may already have been written when this is
            raised for a later one -- each individual write is atomic, but
            the overall run is not transactional across files.
        ValueError: A ``CATEGORY_DESCRIPTIONS`` entry is out of sync with
            ``JUDGE_CATEGORIES``.
    """
    return [_generate_surface(repo_root, relative_path) for relative_path in all_generated_relative_paths()]


# ---------------------------------------------------------------------------
# Drift check (spec 4.10, AC-11; ``make check-vocabulary-drift`` runs this
# module in check mode via ``python -m devbench.vocabulary_generation
# --check``).
# ---------------------------------------------------------------------------


def find_drifted_surfaces(repo_root: Path) -> list[str]:
    """Regenerate every guard-marked surface into a scratch directory and diff it against *repo_root*.

    Never writes to *repo_root* itself: every committed surface is copied
    into a temporary scratch directory, which is what :func:`generate_all`
    actually regenerates; *repo_root*'s own files are only ever read.

    Args:
        repo_root: Absolute path of the repository root whose committed
            surfaces are inspected.

    Returns:
        Repo-relative paths, in :func:`all_generated_relative_paths` order,
        whose committed bytes differ from freshly regenerated bytes. An
        empty list means no drift.

    Raises:
        GuardMarkerError: A surface's guard markers are missing or
            malformed. Propagated unchanged rather than swallowed -- a
            marker-less surface is a real defect the check must fail fast
            on, not a signal to treat that surface as clean.
        ValueError: A ``CATEGORY_DESCRIPTIONS`` entry has drifted from
            ``JUDGE_CATEGORIES``. Propagated unchanged, same reasoning.
    """
    relative_paths = all_generated_relative_paths()
    with tempfile.TemporaryDirectory() as scratch_dir:
        scratch_root = Path(scratch_dir)
        for relative_path in relative_paths:
            destination = scratch_root / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(repo_root / relative_path, destination)
        generate_all(scratch_root)
        return [
            relative_path
            for relative_path in relative_paths
            if (repo_root / relative_path).read_bytes() != (scratch_root / relative_path).read_bytes()
        ]


# ---------------------------------------------------------------------------
# Script entry point (``make generate-vocabulary`` / ``make
# check-vocabulary-drift`` run this module via ``python -m
# devbench.vocabulary_generation`` -- the latter with ``--check``).
# ---------------------------------------------------------------------------

#: ``main`` argv flag that switches from regenerate mode to verify mode.
CHECK_FLAG: Final[str] = "--check"


def _repo_root() -> Path:
    """Return this checkout's own root, resolved from this module's file location.

    Three parents up from ``src/devbench/vocabulary_generation.py``.
    """
    return Path(__file__).resolve().parent.parent.parent


def _run_generate(repo_root: Path) -> int:
    """Regenerate every vocabulary surface under *repo_root*, in place.

    Returns:
        ``0`` on success, printing one ``generated:`` line per surface
        written. ``1`` if a target surface's guard markers are
        missing/malformed or a ``CATEGORY_DESCRIPTIONS`` entry drifted from
        ``JUDGE_CATEGORIES`` -- an ``ERROR:`` line naming the surface and
        the remedy is printed to stderr.
    """
    try:
        written = generate_all(repo_root)
    except (GuardMarkerError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    for path in written:
        print(f"generated: {path.relative_to(repo_root)}")
    return 0


def _run_check(repo_root: Path) -> int:
    """Verify every vocabulary surface under *repo_root* without regenerating it.

    Returns:
        ``0`` if no surface has drifted. ``1`` if any surface has drifted
        (one ``ERROR:`` stderr line naming each drifted surface, followed
        by a single summary line naming :data:`DRIFT_REMEDIATION_COMMAND`),
        or if a target surface's guard markers are missing/malformed (an
        ``ERROR:`` line naming the surface).
    """
    try:
        drifted = find_drifted_surfaces(repo_root)
    except (GuardMarkerError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if not drifted:
        print("vocabulary drift check passed: generated surfaces match the committed tree")
        return 0
    for relative_path in drifted:
        print(
            f"ERROR: '{relative_path}' has drifted from its generated form "
            "(hand-edited after generation, or JUDGE_CATEGORIES changed without regenerating).",
            file=sys.stderr,
        )
    print(
        f"ERROR: vocabulary surface(s) have drifted. Run '{DRIFT_REMEDIATION_COMMAND}', "
        "review the diff, and commit the result.",
        file=sys.stderr,
    )
    return 1


def main(argv: Sequence[str] = ()) -> int:
    """Regenerate (default) or verify (``--check``) every vocabulary surface in this checkout.

    Args:
        argv: Command-line arguments after the program name. Defaults to an
            empty tuple so calling ``main()`` directly (as this module's own
            tests do) never inherits the calling process's ``sys.argv``.
            Contains :data:`CHECK_FLAG` to switch to verify mode.

    Returns:
        See :func:`_run_generate` / :func:`_run_check` for the mode-specific
        meaning of the returned code.
    """
    repo_root = _repo_root()
    if CHECK_FLAG in argv:
        return _run_check(repo_root)
    return _run_generate(repo_root)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
