"""Structural pins for the layout-geometry gate's shipped surfaces (E10-F1-S1-T2;
spec `integration-reality-gates-hardening.md` sections 4.1, 4.9c, 4.2, 4.9 PM-5).

E10-F1-S1-T1 (dependency) moved ``[LAYOUT-AC]`` tagging onto the AC-line grammar
and gave the keyword heuristic a single source of truth
(``devbench.constants.LAYOUT_GEOMETRY_KEYWORDS``), and shipped its own
byte-identical drift pin in ``tests/test_constants.py::
TestLayoutGeometryKeywordSurfacesMatchConstant`` (proving the shipped
test-reviewer prompt and spec-to-backlog SKILL match
``devbench.backlog.manager.render_layout_ac_keyword_block()``'s own output).

This module covers the two things that pin does NOT: (1) an independent,
generator-free membership check straight against the constant (defense in
depth -- see ``TestLayoutGeometryKeywordSurfacesContainEveryConstantMemberDirectly``
below for why this is not a duplicate of the byte-identical pin), and (2) that
``docs/devbench-yaml-reference.md`` documents the ``gates.layout_geometry`` gate
itself: its judge-evidence tier and its ``log-waiver`` exception route (spec 4.9,
PM-5). This module never skips: a missing pinned file is an assertion failure
naming the file, never a ``pytest.skip`` -- a self-disabling pin is precisely
the zero-test-gap failure mode this campaign (spec 4.9c; PR #319) exists to
remove.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from devbench.backlog.manager import LAYOUT_AC_KEYWORD_SURFACE_RELATIVE_PATHS

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]

_YAML_REFERENCE_RELATIVE_PATH = "docs/devbench-yaml-reference.md"

_GUARD_START = "<!-- generated:layout-ac-keywords -->"
_GUARD_END = "<!-- /generated:layout-ac-keywords -->"

_YAML_REFERENCE_SECTION_MARKER = "### `gates.layout_geometry`"
_YAML_REFERENCE_SECTION_END_MARKER = "\n---\n"


def _read(relative_path: str) -> str:
    """Read *relative_path* under the repo root, asserting (never skipping) it exists."""
    path = REPO_ROOT / relative_path
    assert path.is_file(), f"required pinned file is missing: {relative_path}"
    return path.read_text(encoding="utf-8")


def _guard_block_text(content: str, relative_path: str) -> str:
    """Return the inner text between the layout-AC-keywords guard-marker pair.

    Asserts (never skips) when either marker is absent -- a stripped guard
    block is exactly the drift this module exists to catch, not a reason to
    stay silent.
    """
    start = content.find(_GUARD_START)
    end = content.find(_GUARD_END)
    assert start != -1 and end > start, (
        f"'{relative_path}' is missing its {_GUARD_START} / {_GUARD_END} guard-marker pair"
    )
    return content[start + len(_GUARD_START) : end]


def _rendered_tokens(relative_path: str) -> set[str]:
    content = _read(relative_path)
    block = _guard_block_text(content, relative_path)
    return {token.strip() for token in block.strip().split(",") if token.strip()}


class TestLayoutGeometryKeywordSurfacesContainEveryConstantMemberDirectly:
    """Independent membership check complementing ``tests/test_constants.py::
    TestLayoutGeometryKeywordSurfacesMatchConstant``'s byte-identical-to-
    generator-output pin (E10-F1-S1-T1). That pin proves each committed
    surface matches ``render_layout_ac_keyword_block()``'s OWN output; by
    itself it cannot catch a bug inside the generator/render helpers (a wrong
    separator, a normalization bug) that would make both the surface and the
    generator agree with EACH OTHER while both silently disagree with
    ``LAYOUT_GEOMETRY_KEYWORDS`` itself. This class instead parses the raw
    guard-block text and checks membership directly against the constant,
    with zero dependency on ``render_layout_ac_keyword_block`` /
    ``render_layout_ac_keyword_surface_content`` (the render logic) -- a
    second, independently-derived layer of assurance. Not a duplicate under
    DRY: it never compares the two surfaces to each other or to the
    generator, the way the byte-identical pin does; it only ever compares
    one surface's raw text to the constant. Parametrized off
    ``devbench.backlog.manager.LAYOUT_AC_KEYWORD_SURFACE_RELATIVE_PATHS``
    (the single source of truth for which files carry the generated
    keyword block) rather than a locally re-typed path list, so a third
    surface added to that tuple is pinned automatically instead of silently
    passing unchecked.
    """

    @pytest.mark.parametrize("relative_path", LAYOUT_AC_KEYWORD_SURFACE_RELATIVE_PATHS)
    def test_every_constant_keyword_appears_in_the_guard_block(self, relative_path: str) -> None:
        from devbench.constants import LAYOUT_GEOMETRY_KEYWORDS

        rendered_tokens = _rendered_tokens(relative_path)
        missing = LAYOUT_GEOMETRY_KEYWORDS - rendered_tokens
        assert not missing, (
            f"'{relative_path}' guard block is missing keyword(s) from LAYOUT_GEOMETRY_KEYWORDS: {missing}"
        )

    @pytest.mark.parametrize("relative_path", LAYOUT_AC_KEYWORD_SURFACE_RELATIVE_PATHS)
    def test_guard_block_names_no_keyword_absent_from_the_constant(self, relative_path: str) -> None:
        from devbench.constants import LAYOUT_GEOMETRY_KEYWORDS

        rendered_tokens = _rendered_tokens(relative_path)
        extra = rendered_tokens - LAYOUT_GEOMETRY_KEYWORDS
        assert not extra, (
            f"'{relative_path}' guard block names keyword(s) absent from LAYOUT_GEOMETRY_KEYWORDS: {extra}"
        )


class TestYamlReferenceDocumentsLayoutGeometryGate:
    """AC-TEST-006 (spec 4.9, PM-5), AC-TEST-007 (spec 4.2, goal G3):
    ``docs/devbench-yaml-reference.md`` documents the ``gates.layout_geometry``
    gate itself -- its declared judge-evidence tier, that browser geometry is
    verified outside devbench, and the ``log-waiver`` exception route with its
    mandatory, non-empty ``--reason`` and its ``--operator``-not-required
    caveat (judge-evidence gates accept either attribution, unlike a
    machine-blocking gate). Scoped to the gate's own subsection (not the whole
    document) so this pin dies only when THIS section drifts.
    """

    def _section(self) -> str:
        content = _read(_YAML_REFERENCE_RELATIVE_PATH)
        assert _YAML_REFERENCE_SECTION_MARKER in content, (
            f"'{_YAML_REFERENCE_RELATIVE_PATH}' does not document a {_YAML_REFERENCE_SECTION_MARKER} section"
        )
        start = content.index(_YAML_REFERENCE_SECTION_MARKER)
        rest = content[start:]
        assert _YAML_REFERENCE_SECTION_END_MARKER in rest, (
            f"'{_YAML_REFERENCE_RELATIVE_PATH}' gates.layout_geometry section has no closing "
            "'---' separator to scope the pin to"
        )
        end = rest.index(_YAML_REFERENCE_SECTION_END_MARKER)
        return rest[:end]

    def test_documents_judge_evidence_tier_and_browser_geometry_verified_outside_devbench(self) -> None:
        section = self._section()
        assert "judge-evidence" in section
        assert "browser geometry" in section
        assert "verified OUTSIDE devbench" in section

    def test_documents_log_waiver_route_with_mandatory_reason_and_no_operator_requirement(self) -> None:
        section = self._section()
        assert "log-waiver" in section
        assert "--reason" in section
        assert "mandatory" in section
        assert "--operator" in section
        assert "NOT required" in section
