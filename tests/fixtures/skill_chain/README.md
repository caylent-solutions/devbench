# Skill Chain Integration Test Fixture

This directory contains the fixture infrastructure for the end-to-end skill chain
integration tests in `tests/test_plugin/test_skill_chain_integration.py`.

## Purpose

The tests in `test_skill_chain_integration.py` exercise the four-skill onboarding
chain described in `docs/onboarding.md`:

```
create-spec -> spec-to-backlog -> configure-devbench -> bootstrap-environment
```

No LLM calls are made during tests. Instead, each test constructs a minimal fake
workspace on disk that satisfies the FILE SYSTEM CONTRACT each skill's output must
meet, then validates those contracts using real devbench production code.

## Skill Output Contracts Tested

### create-spec

- Writes a spec file to `spec/<project-name>.md`
- The spec file is non-empty Markdown starting with an H1 heading

### spec-to-backlog

- Writes `BACKLOG.md` at the workspace root containing a Status Summary table
  and a Full Work Unit Index
- Writes work-unit `.md` files under `backlog/` in the 4-level hierarchy
- All task files default to `## Status: draft`
- `devbench validate-backlog` returns rc=0 on the generated backlog

### configure-devbench

- Writes `backlog/config/devbench.yaml` containing at least a `repos:` section
- The YAML loads without errors via `load_runtime_config`

### bootstrap-environment

- Reads `backlog/config/devbench.yaml` for the repos list
- Checks for `<checkout_directory>/.git` to determine EXISTS vs MISSING
- Uses `git clone https://github.com/<repo>.git <checkout_directory>` when MISSING
- Skips clone when EXISTS (the fake workspace sets up the `.git` sentinel)

## Fixture Helpers

The helper functions in `test_skill_chain_integration.py` build each skill's
expected output directly on disk using `tmp_path`. They do not invoke the skills
themselves -- they simulate what a correctly-implemented skill would produce. The
integration assertions then verify that the simulated output satisfies the
production code contracts (validate-backlog, load_runtime_config).

## Acceptance Criteria Covered

- **AC-191-7** -- Chained invocation: all four skill outputs coexist on one workspace;
  validate-backlog and load_runtime_config both pass on the combined artefacts.
- **AC-191-10** -- `make validate` baseline: ruff lint, ruff format, and
  validate-backlog all pass with the new skills installed.

## Running the Tests

```bash
# From the repo root:
uv run pytest tests/test_plugin/test_skill_chain_integration.py -v

# As part of the full test suite:
make test-unit
```
