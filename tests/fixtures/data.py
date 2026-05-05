"""Static data shared across the test suite.

Centralised here per TD-9 so the markdown templates and other static
test data live next to the YAML fixtures under ``tests/fixtures/``,
and ``conftest.py`` is restricted to pytest plumbing (autouse hooks,
fixture functions). Pure-data definitions only -- no pytest imports,
no fixtures.
"""

from __future__ import annotations

WORK_UNIT_MARKDOWN_TEMPLATE: str = """\
# {unit_id}: {title}

## Status: {status}

## Description

{description}

## Target Repository

- **Repo:** `{repo}`
- **Local path:** `{workspace_root}/{repo_short}`
- **Branch:** `backlog/{unit_id_lower}`

## Dependencies

| ID | Title | Status |
|----|-------|--------|
{dep_rows}

## Acceptance Criteria

- [ ] AC-FUNC-001 Implement the primary feature
- [ ] AC-TEST-001 All tests pass
- [ ] AC-DOC-001 Update `README.md` with new feature documentation

## Changes Manifest

- `src/main.py`
- `tests/test_main.py`
- `README.md`

## Comments
"""
