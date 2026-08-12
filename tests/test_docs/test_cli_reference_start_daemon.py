"""Structural pin for docs/cli-reference.md's `start` section daemon documentation.

Verifies that docs/cli-reference.md documents the `--daemon`/`-d` flag delivered by
E9-F2-S1-T1 (spec Section 4 FR-3, FR-9; AC-12, AC-20):

- The `### `start`` synopsis line contains `[--daemon]` (AC-E9-F3-S2-T1-1).
- The flag list under `### `start`` documents a `--daemon, -d` entry describing
  daemon mode (detached run, PID file, `devbench instances` discovery, issue #209).

Non-vacuity: before E9-F2-S1-T1 (commit 8490027), docs/cli-reference.md's
`### `start`` section contained no `daemon` token anywhere -- confirmed via
`git show 8490027^:docs/cli-reference.md | grep -n daemon`, which returns zero
hits inside the `start` section (only unrelated hits elsewhere in the doc, for
`quota-watcher` and the `instances` example). See the TDD Cycle Log for the
recorded `git show` diff proving both assertions below are RED against that
pre-fix text and GREEN after.

Source: E9-F2-S1-T1 (docs/cli-reference.md). Spec Section 4 FR-3, FR-9; AC-12, AC-20.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
CLI_REFERENCE_DOC = REPO_ROOT / "docs" / "cli-reference.md"

_HEADING_PREFIX_RE = re.compile(r"^(#{1,6}) ")
_HEADING_LINE_RE = re.compile(r"^(#{1,6}) ", re.MULTILINE)

_START_SYNOPSIS = (
    'uv run devbench start [--daemon] [--include "<tokens>"] [--exclude "<tokens>"] [--name <name>] [--allow-overlap]'
)


def _read_doc() -> str:
    return CLI_REFERENCE_DOC.read_text(encoding="utf-8")


def _extract_section(text: str, heading: str) -> str:
    """Return the content of the section starting at ``heading``.

    ``heading`` must be the full markdown heading line, including its leading
    ``#`` markers (e.g. ``"### `start`"``), not a bare title. The heading's
    level is derived from that prefix, and the returned section is bounded at
    the next heading whose level is the same as or higher (fewer ``#``
    characters) than the starting heading. Passing a bare title (no ``#``
    prefix) raises ``ValueError`` immediately rather than silently deriving a
    bogus level.
    """
    match = _HEADING_PREFIX_RE.match(heading)
    if match is None:
        raise ValueError(
            f"heading must be a full markdown heading starting with '#' markers "
            f"(e.g. '#### {heading}'), got: {heading!r}"
        )
    level = len(match.group(1))
    idx = text.find(heading)
    if idx == -1:
        return ""
    section_text = text[idx:]
    for candidate in _HEADING_LINE_RE.finditer(section_text):
        if candidate.start() == 0:
            continue
        if len(candidate.group(1)) <= level:
            return section_text[: candidate.start()]
    return section_text


def _extract_daemon_flag_entry(section_text: str) -> str:
    """Return the full ``- `--daemon, -d` -- ...`` bullet line from *section_text*.

    The bullet is a single unwrapped markdown line terminated by the blank
    line before the next ``**...**`` flag-group heading, so the boundary is
    the next ``\\n\\n`` after the bullet's start.
    """
    idx = section_text.find("`--daemon, -d`")
    assert idx != -1, "The '--daemon, -d' flag bullet must exist to extract its description."
    end = section_text.find("\n\n", idx)
    return section_text[idx:] if end == -1 else section_text[idx:end]


@pytest.mark.unit
class TestStartSynopsisDocumentsDaemonFlag:
    """AC-E9-F3-S2-T1-1 / AC-20: the start synopsis contains [--daemon]."""

    def test_start_section_exists(self) -> None:
        text = _read_doc()
        start_section = _extract_section(text, "### `start`")
        assert start_section, "docs/cli-reference.md must contain a '### `start`' section."

    def test_start_synopsis_contains_daemon_flag(self) -> None:
        text = _read_doc()
        start_section = _extract_section(text, "### `start`")
        assert start_section, "docs/cli-reference.md must contain a '### `start`' section."
        synopsis_match = re.search(r"```\nuv run devbench start (.+?)\n```", start_section)
        assert synopsis_match is not None, (
            "docs/cli-reference.md's '### `start`' section must contain a fenced "
            "'uv run devbench start ...' synopsis block."
        )
        assert "[--daemon]" in synopsis_match.group(1), (
            "docs/cli-reference.md's start synopsis must contain '[--daemon]' (spec Section 4 FR-3, AC-12, AC-20)."
        )

    def test_daemon_flag_is_the_exact_first_synopsis_token(self) -> None:
        """Pins the specific synopsis text the fix introduced (AC-20), not merely
        the substring's presence anywhere in the section -- a doc that moved
        '[--daemon]' to a different position, or introduced it with different
        surrounding spacing, would still pass the substring check above but
        fail this exact-text pin."""
        text = _read_doc()
        assert _START_SYNOPSIS in text, (
            f"docs/cli-reference.md must contain the exact start synopsis line "
            f"{_START_SYNOPSIS!r} with '[--daemon]' as the first optional flag "
            "(spec Section 4 FR-3)."
        )


@pytest.mark.unit
class TestFlagListDocumentsDaemonEntry:
    """AC-E9-F3-S2-T1-1 / AC-20: the flag list documents `--daemon, -d`."""

    def test_daemon_flag_entry_present(self) -> None:
        text = _read_doc()
        start_section = _extract_section(text, "### `start`")
        assert start_section, "docs/cli-reference.md must contain a '### `start`' section."
        assert "`--daemon, -d`" in start_section, (
            "docs/cli-reference.md's '### `start`' section must document a "
            "'--daemon, -d' flag entry (spec Section 4 FR-3, AC-12, AC-20)."
        )

    def test_daemon_flag_entry_describes_detached_run(self) -> None:
        text = _read_doc()
        start_section = _extract_section(text, "### `start`")
        assert start_section, "docs/cli-reference.md must contain a '### `start`' section."
        entry = _extract_daemon_flag_entry(start_section)
        assert "detach the orchestrator into the background" in entry, (
            "The '--daemon, -d' flag entry must describe detaching the orchestrator "
            "into the background (spec Section 4 FR-3)."
        )
        assert "issue #209" in entry, "The '--daemon, -d' flag entry must cite issue #209 (spec Section 4 FR-3)."

    def test_daemon_flag_entry_describes_pid_file_and_discovery(self) -> None:
        text = _read_doc()
        start_section = _extract_section(text, "### `start`")
        assert start_section, "docs/cli-reference.md must contain a '### `start`' section."
        entry = _extract_daemon_flag_entry(start_section)
        assert "PID file" in entry, (
            "The '--daemon, -d' flag entry must describe the PID file it writes (spec Section 4 FR-3)."
        )
        assert "devbench instances" in entry, (
            "The '--daemon, -d' flag entry must name 'devbench instances' as the "
            "discovery mechanism, consistent with the existing 'instances' section "
            "(spec Section 4 FR-3)."
        )

    def test_daemon_flag_entry_describes_log_location(self) -> None:
        text = _read_doc()
        start_section = _extract_section(text, "### `start`")
        assert start_section, "docs/cli-reference.md must contain a '### `start`' section."
        entry = _extract_daemon_flag_entry(start_section)
        assert "<workspace>/logs/orchestrator.log" in entry, (
            "The '--daemon, -d' flag entry must document the log location the "
            "detached daemon appends stdout/stderr to (spec Section 4 FR-3)."
        )
