<!--
AC-FINAL canonical set for Markdown-only tasks (doc-only changes, no Python
source authored). The Python-tooling ACs carry the verbatim N/A suffix per
docs/acceptance-criteria-canonical.md.
-->

- [ ] AC-FINAL-001 Every AC-TEST-* and AC-CYCLE-* test defined above runs and PASSES (not merely exists). No skipped, no xfail, no xpassed, no stubs.
- [ ] AC-FINAL-002 `ruff check src tests` exits zero in the relevant service directory -- N/A for Markdown Tasks (no Python source authored)
- [ ] AC-FINAL-003 `ruff format --check src tests` exits zero -- N/A for Markdown Tasks (no Python source authored)
- [ ] AC-FINAL-004 `mypy src` exits zero -- N/A for Markdown Tasks (no Python source authored)
- [ ] AC-FINAL-005 `pytest tests/unit -v` exits zero (full unit suite green) -- N/A for Markdown Tasks (no Python source authored)
- [ ] AC-FINAL-006 `pytest tests/integration -v` exits zero (full integration suite green) -- N/A for Markdown Tasks (no Python source authored)
- [ ] AC-FINAL-007 `pytest tests/functional -v` exits zero (full functional suite green) -- N/A for Markdown Tasks (no Python source authored)
- [ ] AC-FINAL-008 `bandit -r src -ll` exits zero -- N/A for Markdown Tasks (no Python source authored)
- [ ] AC-FINAL-009 `JUDGE_CLAUDE_MODEL=$JUDGE_CLAUDE_MODEL JUDGE_WORKSPACE_ROOT=/workspaces/rpm-migration/kanon-deps-work uv run --project /workspaces/rpm-migration/devbench devbench validate-backlog` exits zero.
- [ ] AC-FINAL-010 The code under test is functionally verified end-to-end (the AC-CYCLE-* evidence above).
- [ ] AC-FINAL-011 No bypass annotations: no `# noqa`, `# nosec`, `# type: ignore`, `@SuppressWarnings`, `# pragma: no cover`, `--no-verify`, no raised lint thresholds, no added exclusions to linter configs.
- [ ] AC-FINAL-012 No em-dash characters (U+2014) introduced anywhere.
- [ ] AC-FINAL-013 No new test skips, xfails, or `pytest.mark.skip*` annotations.
- [ ] AC-FINAL-014 Coverage: `pytest --cov=src --cov-fail-under=100` exits zero for new files; baseline not regressed for modified files -- N/A for Markdown Tasks (no Python source authored)
- [ ] AC-FINAL-015 The Task's Changes Manifest matches exactly the files changed by git (no extra, no missing). Paths are repo-relative (NOT prefixed with `kanon/` or any other checkout_directory).
