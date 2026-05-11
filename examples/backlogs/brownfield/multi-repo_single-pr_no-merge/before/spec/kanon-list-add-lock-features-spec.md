# Spec: kanon dependency-manager features (`list`, `add`, `remove`, `outdated`, `why`, `doctor`, `.kanon.lock`, shell completions, bootstrap deprecation)

> **Status:** Draft. All review feedback and code-verified facts folded in. Ready to promote to a devbench backlog under `kanon-deps-work/backlog/` (multiple epics).
> **Scope:** Open-source / vendor-agnostic. No organization-specific names in code, docs, or error messages of the open-source kanon CLI. A parallel scope updates the caylent-private-kanon catalog repo's own docs + CI to enforce the standards this spec codifies AND to delete the legacy nested `catalog/<name>/` directory.
> **PEP 440:** Required. All version specifiers MUST be PEP 440 compliant. Tag names whose last path component is not a valid PEP 440 version are unaddressable by kanon; `kanon catalog audit` warns about non-PEP-440 tag names so catalog authors know they're unaddressable. Monorepo-style tags use a path prefix on the LAST `/` boundary followed by a PEP 440 version (e.g., `subpackage/1.0.0`, `dev/python/lib/~=1.2`); this is already implemented in `version.py::_resolve_constraint_from_tags`.
> **Git-provider agnostic.** `kanon` works with any git host (any vendor; self-hosted; local `file://`) and any git transport (HTTPS, SSH). It NEVER shells out to a provider-specific CLI (`gh`, `glab`, `bb`, `tea`, `aws codecommit`, `az repos`) and NEVER calls a provider-specific HTTP API. Every git interaction is `git` (plumbing or porcelain) only — primarily `git ls-remote` for refs and `git clone` for content. Provider-agnosticism is an enforced invariant, tested in CI via a tree-wide grep.
> **Embedded repo fork.** Dependency resolution, transitive `<include>` walking, `<project>` cloning, and lockfile writes are implemented within kanon using its own embedded fork of the repo tool (under `src/kanon_cli/repo/`). Operators do NOT install Gerrit's repo tool separately. The XML manifest format is kanon's own.
> **No default manifest repo at runtime (after bootstrap removal).** Today, `resolve_catalog_dir()` falls back to a bundled `src/kanon_cli/catalog/` directory shipped with the wheel; this is used for `kanon bootstrap kanon` self-bootstrap only. The bundled catalog is deleted as part of the bootstrap deprecation (Section 4.9 + Section 9). Post-deprecation: missing `--catalog-source` AND missing `KANON_CATALOG_SOURCE` is always a hard error for `list`/`add`/`outdated`/`why`/`catalog audit`. For `kanon install` and `kanon doctor`, the lockfile's `[catalog].source` field is used as a fallback when present and consistent (Section 4 header).
> **No credential handling.** kanon NEVER prompts for credentials, NEVER caches credentials, NEVER interacts with auth providers. Every `git ls-remote` and `git clone` inherits the operator's git client configuration (`~/.gitconfig`, credential helpers, SSH agent, `url.insteadOf` rewrites). Auth setup is the operator's responsibility; `docs/git-auth-setup.md` covers supported configurations. kanon DOES detect specific auth-failure patterns (`Authentication`, `Permission denied`) in git stderr to skip retries — this is retry-policy logic, not credential handling.
> **Network retries (existing behavior, preserved).** `git ls-remote` is retried up to `KANON_GIT_RETRY_COUNT` times (default 3) with `KANON_GIT_RETRY_DELAY` seconds between attempts (default 1) on transient errors. Auth-failure patterns skip retries. This is existing kanon behavior (`src/kanon_cli/constants.py`); the spec preserves it.
> **No interactive prompts.** kanon never asks the operator a question. Every choice is made up-front via CLI flag, env var, or fail-fast error. Includes color (auto-detect TTY + `NO_COLOR` + `--no-color`), credentials (delegated to git), and conflict resolution (always hard-error with remediation).

---

## 0. Items that change existing user-facing behavior (review before backlog)

Four items in this spec interact with features that exist in `kanon` today. All four are folded in non-disruptively so that no scripted invocation breaks on day one. They are listed here so the operator can decide policy:

| # | Item | Existing behavior | This spec | Reason flagged |
|---|---|---|---|---|
| 0.1 | `kanon clean` orphans | `kanon clean` removes `.packages/` and `.kanon-data/` (and the marketplace dir when `KANON_MARKETPLACE_INSTALL=true`) | Add `--orphans` flag (default off) that ALSO prunes per-project clones present on disk but absent from `.kanon` / `.kanon.lock` | Default-off keeps existing behavior; operator may prefer orphan-pruning as default |
| 0.2 | `kanon outdated` exit code | (command does not exist) | Default exit 0 always; `--fail-on-upgrade` opt-in for CI gates | Original spec direction was non-zero-on-upgrade; pip/npm/cargo all use 0 |
| 0.3 | `kanon bootstrap` deprecation | `kanon bootstrap <name>` copies files from `<catalog-dir>/<name>/` (using `--catalog-source` → `KANON_CATALOG_SOURCE` → **bundled `src/kanon_cli/catalog/` fallback**) into `--output-dir` (default cwd); `kanon bootstrap list` lists subdirectories of the catalog | Both commands print a WARN to stderr naming the exact replacement command (`kanon add <name>` / `kanon list`) AND exit with status 3 without performing any work. Operators run the suggested command explicitly. **The bundled `src/kanon_cli/catalog/` directory is deleted** alongside the shim. The per-entry `catalog/<name>/` directory in manifest repos is also deleted (Section 9) | Forces migration immediately at the CI / script boundary while keeping the command discoverable; removes both the legacy template-fallback model AND the bundled-catalog default-source path in one coordinated change |
| 0.4 | PEP 440 enforcement on tag names | Today's `_resolve_constraint_from_tags` silently skips tags whose last component fails `Version()` parsing; raises only when zero parseable tags remain | (a) `kanon catalog audit` adds a new check: warn on every `<project>` tag whose last path component is not a valid PEP 440 version; (b) `kanon install` / `kanon outdated` emit a louder error when constraint resolution finds zero parseable PEP 440 tags under the prefix, listing the non-PEP-440 names that were skipped | Catalog authors get explicit feedback that non-PEP-440 tags (e.g., `v1.0.0`) are unaddressable. Consumers get clearer errors. Warning-only (not error) so manifest repos with mixed legitimate non-version tags (e.g., ops markers) still work |

If either of 0.1–0.2 should become defaults rather than opt-ins, decide before backlog generation. 0.3's behavior (WARN + exit 3, no delegate, bundled catalog deleted) is fixed; hard removal of the `kanon bootstrap` command shell is a separate future decision (Section 15). 0.4's warning vs error severity is fixed (warn in audit; loud-error in resolution when zero parseable tags).

---

## 1. Context

`kanon` is an open-source declarative dependency manager for git-hosted assets. Today's verified state (read from `src/kanon_cli/` at spec time):

- A **manifest repo** model: a git repository whose `repo-specs/` directory holds XML manifest files matching `*-marketplace.xml` (the `MARKETPLACE_FILE_GLOB` constant in `constants.py`). XML format is kanon's own (built on a fork of the repo tool's manifest schema; embedded under `src/kanon_cli/repo/`). **Today**, manifest repos may also have a legacy nested `catalog/<package-name>/` directory holding pre-baked `.kanon` snippets and per-entry READMEs; this directory is the source of the deprecated `kanon bootstrap` flow and is removed by this spec (Section 9).
- **`--catalog-source <git_url>@<ref>`** CLI flag (currently defined on `kanon bootstrap` only) and **`KANON_CATALOG_SOURCE`** env var (`CATALOG_ENV_VAR = "KANON_CATALOG_SOURCE"`). CLI flag wins over env var. Falls back to a **bundled** `src/kanon_cli/catalog/` directory shipped with the wheel (contains one entry, `kanon`, for self-bootstrap).
- **PEP 440 version specifiers** resolved against git tags via `git ls-remote`. The constraint resolver (`version.py`) supports:
  - Operators: `~=`, `>=`, `<=`, `!=`, `==`, `>`, `<`, `===`
  - Wildcard: `*`
  - Literal: `latest` (alias for `*`)
  - Range constraints: `>=1.0.0,<2.0.0`
  - Optional monorepo path prefix at the last `/`: `dev/python/my-lib/~=1.2`
- **`<catalog-metadata>`** XML element planned for every marketplace XML (REQUIRED fields per Section 3.5; the helper `_parse_catalog_metadata()` is NEW). Carries `name`, `display-name`, `description`, `version` (author-claimed, informational only — see Section 1.1), and RECOMMENDED fields (`type`, `owner-name`, `owner-email`, `keywords`).
- **`<include name="...">`** for transitive XML manifests, **`<project name=".." remote=".." revision="..">`** for package repos.
- **`KANON_SOURCE_<name>_{URL,REVISION,PATH}`** triples in `.kanon` files. Multiple sources supported per file. Parsed by `core/kanonenv.py::parse_kanonenv`.
- **Standard `.kanon` header** (today's template at `src/kanon_cli/catalog/kanon/.kanon`):
  ```
  GITBASE=<value>
  CLAUDE_MARKETPLACES_DIR=${HOME}/.claude-marketplaces
  KANON_MARKETPLACE_INSTALL=<true|false>
  ```
  Plus the `KANON_SOURCE_*` triples below.
- **Install workspace layout** (created by `kanon install`):
  - `.kanon-data/sources/<name>/` — per-source isolated workspace (where embedded `repo init` runs)
  - `.kanon-data/.kanon-install.lock` — exclusive `fcntl.flock` lock serializing concurrent installs
  - `.packages/` — aggregated symlinks to packages from all sources
  - `.gitignore` — auto-updated with `.packages/` and `.kanon-data/`
- **Marketplace install** (today's optional, Claude-specific path): when `KANON_MARKETPLACE_INSTALL=true` AND `CLAUDE_MARKETPLACES_DIR` is set, `kanon install` cleans + populates the marketplace directory; `kanon clean` runs the uninstall script and removes it. The `MARKETPLACE_DIR_PREFIX = "${CLAUDE_MARKETPLACES_DIR}/"` is a marketplace XML linkfile-dest convention.
- **Embedded repo tool** under `src/kanon_cli/repo/`. Provides `repo_init`, `repo_envsubst`, `repo_sync`, and constraint-resolution helpers. Forked from the upstream `repo` tool (Gerrit); kanon depends on its OWN copy, not on an externally-installed `repo` binary.
- **`git ls-remote` retry policy** (today): up to `KANON_GIT_RETRY_COUNT` (default 3) attempts with `KANON_GIT_RETRY_DELAY` (default 1s) between attempts. Auth-error patterns (`Authentication`, `Permission denied`) skip retries to avoid credential lockout.
- **Legacy environment variables**: `REPO_URL` and `REPO_REV` are deprecated (`commands/install.py::_warn_if_legacy_env_vars_set`); a DeprecationWarning + stderr message is emitted when set.
- **Validate command**: today has two sub-subcommands — `kanon validate xml` and `kanon validate marketplace`, each with `--repo-root`. Section 3.5 below ADDS a new `kanon validate metadata` sub-subcommand for the soft-spot checks.

Three things change with this spec:

1. **Discovery + management commands** that did not exist before (`kanon list`, `kanon add`, `kanon remove`, `kanon outdated`, `kanon why`, `kanon doctor`, `kanon catalog audit`).
2. **A lockfile** capturing every resolved version across the dependency tree, plus reproducible reinstalls (`.kanon.lock`).
3. **The legacy nested `catalog/<name>/` directory is removed** from manifest repos AND **the bundled `src/kanon_cli/catalog/` is deleted** from the kanon wheel. Every catalog-entry definition lives in a single `*-marketplace.xml` under `repo-specs/`, identified by its `<catalog-metadata>` block. `kanon bootstrap` and `kanon bootstrap list` become deprecation shims (Section 4.9).

The operator's intent: a user finds a kanon dependency the same way they would for `pip` / `npm` / `cargo`, runs one command to add it, and gets reproducible installs from a committed lockfile.

### 1.1 Terminology (canonical for this spec and the resulting docs)

| Term | Definition |
|---|---|
| **manifest repo** | A git repository containing `repo-specs/**/*-marketplace.xml` files. The git URL @ ref of this repository IS what `--catalog-source` points at. "Catalog repo" is a synonym. |
| **catalog source** | A `<git-url>@<ref>` value identifying a manifest repo at a specific revision. Passed via `--catalog-source` or `KANON_CATALOG_SOURCE`. The `<ref>` portion may itself be a PEP 440 spec (e.g., `https://h/r.git@==1.0.0`); kanon's existing `_clone_remote_catalog` resolves it to a concrete tag before `git clone`. |
| **catalog entry** | One `*-marketplace.xml` file inside a manifest repo. Identified by the `<name>` child of its `<catalog-metadata>` block. Exactly one `<catalog-metadata>` block per XML file. |
| **entry name** | The value of `<catalog-metadata><name>` inside a `*-marketplace.xml`. Must be unique across the manifest repo (soft-spot rule 3). |
| **catalog metadata** | The `<catalog-metadata>` XML element. The single source of truth for entry identity, display, ownership, keywords. The `<version>` child is **author-claimed, informational only** — displayed in `kanon list --detail` and indexed in `kanon list --all-versions`, but NOT used for resolution. Actual versioning uses git refs on the manifest repo (tags, branches, SHAs). `kanon validate metadata` does not cross-check `<catalog-metadata><version>` against any git ref. |
| **source name** | The `<source-name>` token in `KANON_SOURCE_<source-name>_{URL,REVISION,PATH}` triples within a `.kanon` file. Derived from the entry name by deterministic normalization (soft-spot rule 2): always lowercase, always replace `-` with `_`. Normalization is one-way and lossy. |
| **`.kanon` file** | The consumer-side declaration file. Parsed by `core/kanonenv.py::parse_kanonenv`. Standard header: `GITBASE`, `CLAUDE_MARKETPLACES_DIR`, `KANON_MARKETPLACE_INSTALL`. Plus `KANON_SOURCE_<name>_{URL,REVISION,PATH}` triples per source. |
| **`.kanon.lock` file** | The committed lockfile (new, this spec). Captures resolved SHAs for reproducible installs. Default path derived from `--kanon-file` (Section 4.7). |
| **install workspace** | The consumer's working directory. Contains `.kanon`, `.kanon.lock`, and the kanon-managed install artifacts at concrete paths: `.kanon-data/sources/<name>/` (per-source workspaces), `.kanon-data/.kanon-install.lock` (install lock), `.packages/` (aggregated symlinks). `.gitignore` is auto-updated. |
| **monorepo tag pattern** | A git tag whose last `/`-delimited path component is a PEP 440 version, with arbitrary path-prefix above (e.g., `refs/tags/subpackage/1.0.0`, `refs/tags/dev/python/my-lib/2.1.3`). Already supported in `version.py::_resolve_constraint_from_tags` via prefix filtering. |
| **legacy `catalog/<name>/` directory** | Historical nested directory inside manifest repos containing pre-baked `.kanon` templates for `kanon bootstrap`. **Removed by this spec.** Also: bundled `src/kanon_cli/catalog/` inside the kanon wheel — same purpose, also removed. Do NOT confuse with "catalog source" or "catalog entry"; those refer to the manifest-repo-IS-the-catalog model and survive. |
| **bootstrap (deprecated)** | The legacy `kanon bootstrap` command. Becomes a deprecation shim per Section 4.9. Exit 3 on any non-`--help` invocation. |

Docs that reuse old phrasings ("bootstrap catalog", "the catalog folder", "catalog template") are rewritten using the table above as canonical.

---

## 2. Goals

- `kanon list` and `kanon add` both take the standard `--catalog-source` arg or the `KANON_CATALOG_SOURCE` env var (arg wins). If neither is provided, exit with a clear, actionable error. There is no fallback to a default catalog (the existing bundled fallback is removed alongside bootstrap deprecation — Section 0.3).
- `kanon list` (default) prints every catalog entry by name (one per line, from `<catalog-metadata><name>`); output feedable into `kanon add`.
- `kanon list --tree` prints the full three-layer dependency tree (catalog entry -> XML manifests with transitive `<include>`s -> `<project>` package repos), each annotated with the version that resolved at command-execution time. Requires a filter for large catalogs (Section 4.1).
- `kanon list --all-versions` walks historical versions; default-capped at 50 most recent catalog versions, `--limit N` / `--no-limit` to override. Mutually exclusive with `--tree`.
- `kanon list <substring>` filters by substring; `kanon list --regex <pattern>` filters by regex.
- `kanon list --format json` for tooling; default format is `names` (one-per-line).
- `kanon add <name>[@<spec>] [<name>[@<spec>] ...]` adds one or more catalog entries to a `.kanon` file (creates the file if missing). Default spec = manifest repo's latest PEP 440 tag (hard error if zero PEP 440-valid tags); explicit spec accepts PEP 440 specs or a branch name. Source name is derived from the entry name (Section 1.1). See Section 4.0 for the formal `@<spec>` parsing rules.
- `kanon remove <name> [<name> ...]` is the inverse of `add`. Accepts source name OR entry name (Section 4.3).
- `kanon outdated` reports installable upgrades per source.
- `kanon why <pkg-or-source>` explains which top-level source pulled in a given transitive package.
- `kanon doctor` cross-checks `.kanon` + `.kanon.lock` consistency; reports the effective catalog source for operator verification.
- `kanon catalog audit` enforces the standards-audit checks from Section 3.5 against a manifest repo (workspace path or remote URL). Separate from `doctor` for SRP.
- `kanon install` resolves every transitive version and writes `.kanon.lock` on first run. Subsequent installs read from the lockfile. `--refresh-lock` rebuilds; `--refresh-lock-source <name>` rebuilds one source's chain only. Conflicts -> hard error.
- `kanon bootstrap` and `kanon bootstrap list` become deprecation shims that WARN with the exact replacement command and exit with status 3 without performing any work (Section 4.9). The legacy `catalog/<name>/` directory in manifest repos AND the bundled `src/kanon_cli/catalog/` are removed (Section 9 + the shim's epic).
- Branches are interchangeable with versions everywhere. In the lockfile, branch-pinned deps are captured as immutable SHAs; drift requires `--refresh-lock` (or `--refresh-lock-source`).
- All work conforms to project CLAUDE.md: no silent failures, no fallback logic outside the documented exceptions (lockfile catalog-source fallback for `install`/`doctor`; existing `git ls-remote` retry policy preserved), no hardcoding, fully event-driven, configurable via env vars or args with sensible constant defaults. **No interactive prompts** under any circumstance.
- 100% line coverage on every line of new code (unit). 100% coverage on the new commands end-to-end (functional + integration). Test fixtures cover every user journey + every error scenario. Defensive "should-never-happen" branches are restructured (raise on impossible state) rather than excluded from coverage.
- README + sub-docs fully updated. `kanon repo` subcommand (currently undocumented in README) must also be linked. `kanon validate xml` and `kanon validate marketplace` (existing) plus `kanon validate metadata` (new) all documented.
- A parallel scope (Section 9) updates caylent-private-kanon's docs + CI so the catalog repo enforces 100% compliance to the standards this spec codifies AND removes the legacy `catalog/<name>/` directory entirely.

### 2.1 Worked example (the operator on-ramp)

The README quick-start follows this five-step sequence verbatim, using placeholder URLs.

```
# 1. Discover what's in a manifest repo
$ kanon list --catalog-source https://example.com/org/manifest-repo.git@main
package-a
package-b
package-c

# 2. Inspect a specific entry
$ kanon list package-a --detail --catalog-source https://example.com/org/manifest-repo.git@main
package-a
  display-name : Package A
  description  : Example dependency
  version      : 1.4.2          # author-claimed; informational only
  type         : library

# 3. Add it to .kanon (creates the file if absent)
$ kanon add 'package-a@==1.4.2' --catalog-source https://example.com/org/manifest-repo.git@main
Wrote KANON_SOURCE_package_a_URL, _REVISION, _PATH to ./.kanon

# 4. Install -- first run writes .kanon.lock
$ kanon install
kanon install: parsing /workspace/.kanon...
kanon install: syncing source 'package_a'...
  repo init (repo-specs/package-a/package-a-marketplace.xml)...
  repo envsubst...
  repo sync...
kanon install: aggregating packages into .packages/...
Wrote .kanon.lock (1 source, M projects).

# 5. Commit both
$ git add .kanon .kanon.lock && git commit -m "add package-a@1.4.2"
```

Subsequent installs replay from the lockfile (no resolution; info-line: "installing from lockfile (1 source, M projects)"). Upgrade with `kanon outdated`, then `kanon add 'package-a@==1.5.0'` + `kanon install --refresh-lock-source package_a`.

Operators previously using `kanon bootstrap <name>` get a WARN pointing at `kanon add <name>` plus a one-time migration doc (Section 8: `docs/migration-bootstrap-to-add.md`).

---

## 3. Existing primitives to reuse (DO NOT reinvent)

| Need | Already in kanon | File of record (as of spec drafting) |
|---|---|---|
| Resolve catalog URL + ref to a local clone | `resolve_catalog_dir()` (today's behavior: CLI flag → env var → bundled fallback) | `src/kanon_cli/core/catalog.py` |
| CLI flag + env var for catalog source | `--catalog-source` / `KANON_CATALOG_SOURCE` | currently defined ONLY on `kanon bootstrap`'s argparse (`commands/bootstrap.py`); **moves to a shared module `core/cli_args.py`** as part of this spec (bootstrap is deprecating to a shim, so the canonical flag definition must not live there). Every new command imports the flag's `add_argument()` factory from the shared module. |
| Catalog source format `<git_url>@<ref>` parsing (split on last `@`) | `_parse_catalog_source()` | `core/catalog.py` |
| PEP 440 spec -> git ref resolution (with monorepo prefix support) | `is_version_constraint()`, `resolve_version()`, `_resolve_constraint_from_tags()` | `src/kanon_cli/version.py` |
| Bare-semver-to-tag normalization | `_normalize_bare_semver_to_tag()` (today: matches `^\d+(?:\.\d+){1,2}$` only) | `version.py` — **widened by this spec** to accept any valid `packaging.version.Version` (Section 4.0) |
| Embedded repo init/envsubst/sync | `kanon_cli.repo.repo_init`, `repo_envsubst`, `repo_sync` | `src/kanon_cli/repo/` |
| `<catalog-metadata>` parsing | `_parse_catalog_metadata()` (NEW helper, this spec) | `core/marketplace_validator.py` (likely extension target) |
| `.kanon` parsing | `parse_kanonenv()` | `core/kanonenv.py` |
| Install workspace concurrency lock | `fcntl.flock(LOCK_EX)` on `.kanon-data/.kanon-install.lock` | `core/install.py::install` |
| Per-source install pipeline | `_run_install` (repo init/envsubst/sync) | `core/install.py` |
| Auto-update `.gitignore` | `update_gitignore()` | `core/install.py` |
| Git ls-remote retry policy | `KANON_GIT_RETRY_COUNT`, `KANON_GIT_RETRY_DELAY`, `GIT_AUTH_ERROR_PATTERNS` | `constants.py` + the embedded repo module |
| Marketplace install lifecycle | `core/marketplace.py::install_marketplace_plugins` / `uninstall_marketplace_plugins`; gated on `KANON_MARKETPLACE_INSTALL=true` | `core/marketplace.py` + `core/install.py` + `core/clean.py` |

The new commands compose these primitives; they do not duplicate them.

---

## 3.5 Standards audit and tightening

Today's de-facto convention has soft spots that need to be codified into the open-source standard, enforced by:
- `kanon validate metadata` (NEW sub-subcommand under existing `kanon validate`; sits alongside `kanon validate xml` and `kanon validate marketplace`)
- `kanon catalog audit` (NEW command for the consumer-side; see Section 4.8)
- Tests

After this spec removes the legacy `catalog/<name>/` directory, the soft-spot set is five entries — four centered on `<catalog-metadata>` plus the PEP 440 tag-name check.

| # | Soft spot | Codified rule |
|---|---|---|
| 1 | `<catalog-metadata>` field set is informal | REQUIRED on every `*-marketplace.xml`: `name`, `display-name`, `description`, `version` (`<version>` is author-claimed and informational only — see Section 1.1). RECOMMENDED: `type`, `owner-name`, `owner-email`, `keywords`. `kanon validate metadata` errors on missing required; warns on missing recommended. Duplicate child elements within the same `<catalog-metadata>` block are an error. Exactly one `<catalog-metadata>` block per XML file (multiple blocks → error). |
| 2 | Source-name derivation is informal | The source name used in `KANON_SOURCE_<source-name>_*` triples is derived from `<catalog-metadata><name>` by deterministic normalization: **always lowercase the input AND always replace `-` with `_`.** No conditional logic; same input always yields same output. `kanon validate metadata` warns when the normalized form differs from the entry name AND when the entry name contains characters outside `[a-zA-Z0-9_-]` (still legal; flag for author awareness). `kanon add` always writes the normalized source name. Normalization is one-way and lossy. |
| 3 | Entry-name uniqueness | `<catalog-metadata><name>` MUST be unique across the manifest repo. `kanon validate metadata` errors on collisions, naming every offending XML path. |
| 4 | `<remote>` definition discoverability | `kanon validate marketplace` (existing) already resolves `<project remote="X">` to a concrete fetch URL by walking `<include>` chains. `kanon catalog audit` re-runs this against a remote `<url>@<ref>`-supplied catalog so consumers can audit before adopting. |
| 5 | Tag-name PEP 440 compliance | `kanon catalog audit` warns about tags in the manifest repo whose last path component (after final `/`) is not a valid PEP 440 version (e.g., `v1.0.0`, `release-2024`). These tags are unaddressable by kanon's resolver. Warning-only (not error) so manifest repos with legitimate non-version tags (ops markers, release-prep tags) still work. Catalog authors fix by renaming tags to PEP 440 form. |

These rules replace the prior soft-spot set. The removed items — README naming inside `catalog/<name>/`, `catalog/<name>/.kanon` template required, per-template source-name verbatim copy — are obsoleted by the removal of the legacy `catalog/<name>/` directory tree (Section 9).

The new commands work today against any standards-conformant manifest repo. Section 9 codifies that migration for caylent-private-kanon.

---

## 3.6 Trust model

`kanon` clones and reads manifest repos, resolves transitive XML manifests, and (via `kanon install`) `git clone`s every `<project>` reference. Every step executes git operations against URLs the catalog author controls. **Manifest-repo content is trusted code from the operator's perspective**, equivalent in trust level to a pip index URL or an npm registry.

This spec does NOT introduce signing, attestation, or a central allow-list. The trust model is:

- **The operator chooses the catalog source.** Whoever can write to the manifest repo can change what `kanon install` fetches.
- **Provider-agnostic.** Any git URL is acceptable: any vendor-hosted git service, self-hosted GitOps, local `file://` paths for testing. `kanon` never inspects the host, never special-cases known providers, never reaches for a provider CLI. Operators on git providers without a public CLI (or behind a corporate firewall using a self-hosted git server) are first-class users.
- **No credential handling.** kanon NEVER prompts for credentials, NEVER caches them, NEVER interacts with auth providers. Every `git ls-remote` / `git clone` inherits the operator's local git client configuration: `~/.gitconfig`, credential helpers (e.g., `osxkeychain`, `git-credential-oauth`, `git-credential-manager`), SSH agent + `~/.ssh/config`, `url.insteadOf` rewrites. Auth setup is the operator's responsibility; see `docs/git-auth-setup.md` for supported configurations on common platforms. kanon detects auth-error stderr patterns (`Authentication`, `Permission denied` — `GIT_AUTH_ERROR_PATTERNS` in `constants.py`) to skip retries — this is retry-policy logic, not credential handling.
- **HTTPS by default for *remote* URLs in manifests.** `kanon catalog audit` (new) and existing `kanon validate marketplace` checks refuse non-HTTPS `<remote>` URLs unless `KANON_ALLOW_INSECURE_REMOTES=1` is explicitly set. SSH URLs (`git@host:org/repo.git` or `ssh://git@host/org/repo`) are HTTPS-equivalent for trust purposes and allowed. `file://` URLs are allowed only when `KANON_ALLOW_INSECURE_REMOTES=1` (intended for tests / local fixtures). The operator's choice between HTTPS and SSH transports is handled by their git client's `url.insteadOf` rewrites; kanon does not see the difference.
- **The catalog source is surfaced.** `kanon doctor` prints the effective catalog source so the operator can verify before running side-effecting commands. This catches accidental leakage of `KANON_CATALOG_SOURCE` from a shell profile into an unrelated workspace.
- **Cache files are user-private.** `${KANON_CACHE_DIR}` and every file under it are created with mode `0700` / `0600` respectively to prevent another local user from poisoning completion candidates.
- **Completion candidates are shell-escaped.** Cached names that contain shell metacharacters or embedded newlines are filtered out (would otherwise break the shell completion protocol or, in pathological cases, inject characters into the shell line).

**Out of scope (future work):** signed catalogs, transparency logs, central registry, allow-list of approved manifest repos. Tracked as follow-ups; not blocking this spec.

`docs/security-model.md` (NEW) captures the trust model verbatim, with a "what manifest repos can do to you" section so operators understand the threat surface, plus a "what kanon does NOT do" section (no auth, no credential storage, no provider APIs).

---

## 4. New command surface

All new commands share the catalog-source resolution rule:

> **Required input** (one of, in this precedence): `--catalog-source <git_url>@<ref>` CLI flag (highest), or `KANON_CATALOG_SOURCE` env var. If neither is set, the command exits fail-fast with the canonical error below — **except `kanon install` and `kanon doctor`, which fall back to `.kanon.lock` `[catalog].source` when the lockfile is present and consistent.** No other command has a lockfile fallback; `kanon list`, `kanon add`, `kanon outdated`, `kanon why`, `kanon catalog audit` all hard-error on missing source. For `kanon install`, the lockfile-fallback path applies only when re-resolution is not needed (no `--refresh-lock`, no `--refresh-lock-source`); refresh paths require CLI/env. **The bundled `src/kanon_cli/catalog/` directory is deleted by this spec's bootstrap-deprecation epic; the existing third-tier "bundled fallback" in `resolve_catalog_dir()` is removed at the same time.**

Canonical missing-catalog error (verbatim string for commands that require a catalog with no lockfile fallback):

```
ERROR: <command> requires a catalog source.
Provide one of:
  --catalog-source <git-url>@<ref>      # e.g. --catalog-source https://example.com/org/manifest-repo.git@main
  KANON_CATALOG_SOURCE=<git-url>@<ref>  # set as env var, then re-run

The CLI flag takes precedence when both are set.
A catalog source identifies a manifest repo (a git repository whose
repo-specs/ directory exposes installable kanon dependencies).
See docs/catalogs-explained.md for what a manifest repo is and how to find one.
See docs/configuration.md for the full configuration reference.
```

**Standard error-message structure.** Every error message follows this shape:

```
ERROR: <one-line summary>
<optional context lines, wrapped at 80 cols>
<remediation line, always present if a remediation exists>
```

Snapshot tests cover the top-N error messages by frequency (canonical missing-catalog, lockfile-hash-mismatch, lockfile-sha-unreachable, entry-not-found, source-collision, conflict-detected, missing-required-metadata-field, zero-pep440-tags-under-prefix).

### 4.0 Resolver semantics (formal rules — matches existing `version.py` plus the widening below)

These rules apply uniformly across `kanon add`, `kanon install`, `kanon outdated`, and any completer that resolves a `@<spec>` token.

**`@<spec>` parsing precedence** (first match wins; mirrors `version.py::is_version_constraint` + `_normalize_bare_semver_to_tag` semantics):

1. **PEP 440 constraint** — spec's last `/`-delimited path component starts with a PEP 440 operator (`==`, `~=`, `>=`, `<=`, `>`, `<`, `!=`, `===`), OR equals `*`, OR equals `latest`, OR is a comma-separated range with operators. Optional path prefix for monorepos (`subpackage/==1.0.0`, `dev/python/lib/~=1.2`). Resolves via `_resolve_constraint_from_tags`: filters tags by the prefix (if any), parses each tag's last path component as `packaging.version.Version`, evaluates `SpecifierSet` against the parsed versions, returns the highest-matching tag's full ref (e.g., `refs/tags/subpackage/1.0.0`). **If after filtering zero PEP 440-parseable tags remain under the prefix, hard error** listing the non-PEP-440 tags that were skipped (this is a loud variant of today's silent-skip behavior — see Section 0.4).
2. **Full git ref** — spec starts with `refs/` (e.g., `refs/tags/x`, `refs/heads/x`). Passes through to git unchanged.
3. **Bare PEP 440 version** — spec is a valid `packaging.version.Version` literal (any PEP 440 version: `1.0.0`, `1.0`, `1.0.0a1`, `1.0.0+local`, `2026.4.1`, etc.) AND contains no `/`. Resolves to `refs/tags/<spec>`. **This widens today's `_normalize_bare_semver_to_tag` regex (`^\d+(?:\.\d+){1,2}$`) to accept the full PEP 440 grammar via `packaging.version.Version`.**
4. **Raw git SHA** — 40-char or 64-char hex string AND contains no `/`. Passes through to git as-is; git resolves to a commit ref.
5. **Pass-through to git** — anything else (contains `/` but not a constraint, or non-PEP-440 bare value). Passes through to git unchanged; git resolves following its standard ref priority (typically `refs/heads/<spec>`, then `refs/tags/<spec>`, then other matches). The actual ref that resolves depends on what exists in the target repository; kanon does not constrain or override git's resolution order. To force a specific ref kind, use the explicit `refs/heads/<name>` or `refs/tags/<name>` form (rule 2).

**`@` separator parsing within a catalog-source string.** A catalog source is `<git-url>@<ref>`. SSH URLs may contain `@` in their user-info (`git@host:org/repo.git`). The split rule is implemented in `_parse_catalog_source`: **split on the LAST `@`.** This handles `git@host:org/repo.git@main` → `(git@host:org/repo.git, main)`. A catalog-source string with no `@` is a hard error ("missing ref; use `<url>@<ref>` form").

Edge cases documented with these results in `docs/version-resolution.md`:

| Input | Resolved as | Notes |
|---|---|---|
| `2.10` | `refs/tags/2.10` | rule 3 (widened bare PEP 440) |
| `1.0.0a1` | `refs/tags/1.0.0a1` | rule 3 (widened to accept PEP 440 prereleases) |
| `1.0.0+local.build` | `refs/tags/1.0.0+local.build` | rule 3 (widened to accept PEP 440 local-version) |
| `v1.0.0` | passes through as branch (rule 5) → git fails if no branch `v1.0.0` | non-PEP-440. To address a literal `v1.0.0` tag, the operator MUST use the explicit full ref `refs/tags/v1.0.0` (rule 2). `kanon catalog audit` warns about non-PEP-440 tags (soft-spot 5). |
| `>=1.0,<2.0` | `>=1.0,<2.0` (PEP 440 verbatim) | rule 1; must be shell-quoted |
| `main` | rule 5 (pass-through) | git typically resolves as `refs/heads/main` |
| `release/1.0` | rule 5 (pass-through — last component `1.0` has no PEP 440 operator, so it's not a constraint) | git resolves following its standard ref priority. If both `refs/heads/release/1.0` and `refs/tags/release/1.0` exist, git's default resolution wins. To explicitly pin a monorepo tag bare-style, use `refs/tags/release/1.0` (rule 2) or the constraint form `release/==1.0` (rule 1). |
| `subpackage/==1.0.0` | rule 1 with prefix `subpackage` and constraint `==1.0.0` → resolves to `refs/tags/subpackage/1.0.0` if present | monorepo support |
| `dev/python/lib/~=1.2.0` | rule 1 with prefix `dev/python/lib` and constraint `~=1.2.0` → resolves to `refs/tags/dev/python/lib/<highest-1.2.x>` | monorepo support |
| `abc123…` (40 hex) | passes through (rule 4); git resolves as SHA | |
| `refs/tags/foo/bar` | passes through (rule 2) | already a full ref |
| (none — `kanon add foo` with no `@`) | manifest repo's latest PEP 440 tag (hard error if zero PEP 440-valid tags) | special case; see Section 4.2 |

**No `--ref-kind` flag** — does not exist in kanon and is not added by this spec. To disambiguate (e.g., a branch named with PEP 440-looking digits like `1.0.0` vs the tag), the operator uses the explicit `refs/heads/<name>` or `refs/tags/<name>` form (rule 2).

**No fallback between resolution kinds.** If a rule fires and the underlying git operation fails (tag not found, branch not found, SHA not reachable), kanon errors. It does NOT silently try the next rule. Fail-fast philosophy.

**Repo URL canonicalization.** Two URLs identify the same repository iff `canonicalize_repo_url(a) == canonicalize_repo_url(b)`. The canonicalization function (defined in `core/url.py`, NEW):

- Lowercases the host.
- Normalizes scheme equivalents: `git@host:org/repo` ↔ `ssh://git@host/org/repo` ↔ `https://host/org/repo`.
- Strips trailing `/`, trailing `.git`, and any embedded user-info.
- Preserves path case (some hosts are case-sensitive on path).
- Rejects URLs with query strings or fragments (canonicalization output undefined for those; hard error at canonicalization time, so they never enter the lockfile).

Used by conflict detection (Section 4.7), `kanon why` URL matching (Section 4.5), and the lockfile (Section 5).

### 4.1 `kanon list`

Discover catalog entries.

**Data source.** `kanon list` walks `repo-specs/**/*-marketplace.xml` inside the resolved manifest repo and emits one entry per `*-marketplace.xml` whose `<catalog-metadata>` block validates against soft-spot rule 1. Entry name = `<catalog-metadata><name>`. The legacy `catalog/<name>/` directory (when present) is ignored; `kanon list` reads ONLY the XML manifests.

**Default output** (no extra flags): one entry name per line. Pipeable into `kanon add`.

```
$ kanon list --catalog-source <url>@<ref>
package-a
package-b
package-c
```

**Flags:**

| Flag | Behaviour |
|---|---|
| `--detail` | Per-entry: name (first column) + `<catalog-metadata>` summary (display-name, type, description, version). Human-readable; NOT pipeable into `kanon add`. For machine use, combine with `--format json`. |
| `--tree` | Full three-layer dep tree per entry. catalog entry -> XML manifests (with transitive `<include>`s) -> `<project>` package repos. Each layer annotated with resolved version. **Requires a filter** for catalogs with > `KANON_TREE_NO_FILTER_THRESHOLD` (default 20) entries; without a filter, hard error suggesting `<substring>`, `--regex`, or `--max-depth 0`. Override with `--no-filter-required`. **Mutually exclusive with `--all-versions`.** |
| `--max-depth N` | Cap tree depth when used with `--tree`. Default: unlimited. `--max-depth 0` shows the catalog entry only (no XML, no projects). |
| `--all-versions` | Walk historical catalog versions (one row per `name@version` per version walked, where version comes from manifest-repo git tags). Default-capped at `KANON_LIST_LIMIT=50` most recent. **Mutually exclusive with `--tree`.** |
| `--limit N` / `--no-limit` | Override the cap for `--all-versions`. |
| `--since-version <spec>` | Restrict `--all-versions` to versions matching the spec (PEP 440). |
| `--format {names,json}` | Output format. Default `names`. Env: `KANON_LIST_FORMAT`. |
| `<substring>` (positional) | Filter entries by substring against name, display-name, description, AND keywords (full metadata search). |
| `--regex <pattern>` | Filter entries by regex against the same field set as substring (alternative to positional). |
| `--match-fields <csv>` | Narrow the search to a specific subset: `name,display-name,description,keywords`. Default: all four. **Requires `<substring>` or `--regex`**; passing `--match-fields` alone (without a filter) is a hard error. |
| `--catalog-source <url>@<ref>` | Catalog source override. Env: `KANON_CATALOG_SOURCE`. |
| `--no-color` | Disable color (default: auto-detect TTY + `NO_COLOR` env var). |

**Mutually exclusive combinations.** `--tree` + `--all-versions` is a hard error. `--match-fields` without a filter (substring or regex) is a hard error.

**Zero-match behavior.** If a filter (substring or `--regex`) returns zero entries, exit 0 with empty stdout. A single-line note is written to stderr ("0 entries match filter") so a human invocation gets feedback while pipelines stay clean.

**Empty manifest repo.** If the manifest repo has zero `*-marketplace.xml` files (empty catalog), exit 0 with empty stdout and a stderr note: "manifest repo contains 0 entries".

**Streaming.** Output is streamed line-by-line; large manifest repos do not buffer the full result in memory.

**`--all-versions` worked example:**

```
$ kanon list --all-versions --limit 3 --catalog-source <url>@main
package-a@2.10.0
package-a@2.9.1
package-a@2.9.0
package-b@1.5.0
package-b@1.4.0
package-b@1.3.0
```

`--format json` for the same invocation emits an array of `{name, version, ref, sha}` objects.

### 4.2 `kanon add`

Add one or more catalog entries to a `.kanon` file.

```
kanon add [--catalog-source ...] <name>[@<spec>] [<name>[@<spec>] ...]
          [--kanon-file <path>] [--force] [--dry-run] [--no-color]
```

| Arg / flag | Behaviour |
|---|---|
| `<name>` | Catalog entry name (must match a `<catalog-metadata><name>` in the resolved manifest repo). |
| `@<spec>` | Optional spec per Section 4.0 resolver rules. Default: manifest repo's latest PEP 440 tag. |
| `--kanon-file <path>` | Target file. Default: `./.kanon`. Env: `KANON_KANON_FILE`. |
| `--force` | Overwrite an existing source block with the same source name. Without `--force`, collision is a hard error. |
| `--dry-run` | Print the diff that would be written to `<kanon-file>`. Make no changes. Exit 0. |

**Behaviour:**

1. Resolve the catalog (precedence per Section 4 header).
2. Build the entry index by walking `repo-specs/**/*-marketplace.xml` and reading each `<catalog-metadata>`. If soft-spot rule 1 (required fields) or rule 3 (entry-name uniqueness) is violated, hard error with: `"manifest repo <url>@<ref> has integrity issues (<count>); the catalog author must fix these via 'kanon catalog audit'. Affected entries: <list>"`. Operator can't fix this from the consumer side. **`kanon add` does NOT validate soft-spot 4 (`<remote>` resolvability) or 5 (PEP 440 tag-name compliance); those are validated at `kanon install` time and by `kanon catalog audit`. A successful `kanon add` does NOT guarantee a successful install.**
3. Pre-flight: for every requested `<name>`, locate the matching `*-marketplace.xml`. Compute the derived source name per soft-spot rule 2 (entry-name → normalize). Check for source-name collisions: (a) among the requested set; (b) against existing source-name blocks in the destination file. Any collision without `--force` is a hard error with full details: `"source-name 'foo' already mapped to <existing-url>/<existing-path> (revision <existing-spec>); requested mapping is <new-url>/<new-path> (revision <new-spec>). Use --force to overwrite, or 'kanon remove foo' first."`
4. For each `<name>`:
   - Resolve the manifest repo URL (the catalog source's git URL) and the `*-marketplace.xml` path inside it.
   - If `<spec>` was supplied, resolve per Section 4.0. Otherwise default to **the highest PEP 440-valid git tag on the manifest repo** (queried via `git ls-remote --tags`, parsed by `packaging.version.Version`). If zero PEP 440-valid tags exist, hard error: `"manifest repo has no PEP 440-valid tags; pin to a branch or SHA explicitly (e.g., 'kanon add foo@main') or ask the catalog author to publish a release tag."`
   - Construct the triples directly (no pre-baked template lookup):
     ```
     KANON_SOURCE_<source_name>_URL=<manifest_repo_url>
     KANON_SOURCE_<source_name>_REVISION=<resolved_spec>
     KANON_SOURCE_<source_name>_PATH=<path_in_repo_to_marketplace_xml>
     ```
   - Append to the destination `.kanon` file. Create the file with the standard header (today's `src/kanon_cli/catalog/kanon/.kanon` template) if it does not exist:
     ```
     GITBASE=<YOUR_GIT_ORG_BASE_URL>
     CLAUDE_MARKETPLACES_DIR=${HOME}/.claude-marketplaces
     KANON_MARKETPLACE_INSTALL=<true|false>
     ```
     `CLAUDE_MARKETPLACES_DIR` is the Claude-specific install-target convention; other AI-tool integrations will get their own analogous variables in future releases (Section 15).
5. Refuse to overwrite an existing source-name block silently. Fail-fast unless `--force`.

**Shell quoting reminder.** PEP 440 range specifiers contain `>` and `<`. The operator MUST quote: `kanon add 'package-a@>=1.0,<2.0'`. The `kanon add --help` text and every docs example shows the quotes. The CLI emits a friendly error when it detects an unquoted operator (parsed as an empty argument with shell redirect).

Output names every triple written so the operator can review the diff before `git add`-ing it.

### 4.3 `kanon remove`

Inverse of `add`. Strips named source blocks from a `.kanon` file.

```
kanon remove [--kanon-file <path>] <name> [<name> ...] [--force] [--dry-run] [--no-color]
```

| Arg / flag | Behaviour |
|---|---|
| `<name>` | **Either** the source name (the `<source-name>` token in `KANON_SOURCE_<source-name>_*` triples, as derived by Section 1.1's normalization rule) **OR** the original entry name (e.g., `Foo-Bar` resolves to the same block as `foo_bar`). kanon normalizes both forms via `derive_source_name()` before lookup. |
| `--kanon-file <path>` | Target file. Default: `./.kanon`. |
| `--force` | Remove blocks whose source name does NOT match any catalog entry. Without `--force`, removing an unknown source name is a hard error (likely indicates a typo). |
| `--dry-run` | Print the diff that would be written. Make no changes. |

**Behaviour:**

1. Read the `.kanon` file. Fail-fast if missing.
2. For each `<name>`:
   - Normalize via `derive_source_name()`. Locate every line in the file matching `KANON_SOURCE_<normalized>_{URL,REVISION,PATH}=...` — these MAY be non-contiguous in hand-written `.kanon` files (e.g., comments or other keys interleaved). All three matching lines are removed wherever they appear, preserving the line order of remaining content.
   - If fewer than three matching lines (or zero) are found: hard error ("source 'X' (normalized form 'Y') not fully present in .kanon; found <n> of 3 expected `KANON_SOURCE_<Y>_*` keys").
3. Comments adjacent to removed keys are NOT removed automatically (operator may manually clean up); the spec preserves all other content byte-for-byte except for the three removed lines.
4. Write the file back with these explicit rules:
   - Line endings preserved per-file (sniff the dominant ending; mixed → normalize to `\n` with a warning to stderr).
   - Runs of `≥3` consecutive blank lines collapse to 2.
   - File always ends with exactly one `\n`.

Lockfile interaction: if `.kanon.lock` exists and references the removed source, the next `kanon install` detects the orphan and either prunes it (default) or errors (with `--strict-lock`). Behavior defined in 4.7.

### 4.4 `kanon outdated`

Report installable upgrades per source.

```
kanon outdated [--catalog-source ...] [--kanon-file <path>] [--lock-file <path>]
               [--format {table,json}] [--fail-on-upgrade] [--no-color]
```

**Behaviour:**

1. Resolve the catalog (required; no lockfile fallback for this command).
2. Read the `.kanon` file. For each `KANON_SOURCE_<name>_*` block:
   - Determine the current resolved version (from `.kanon.lock` if present, otherwise live-resolved against the catalog).
   - Query the catalog for the latest version matching the spec (uses `_resolve_constraint_from_tags`; if zero PEP 440-parseable tags under the prefix → loud error per soft-spot 5).
   - Query the catalog for the latest version ignoring the spec (latest possible upgrade if the spec were relaxed).
3. Emit one row per source: `name | current | latest-matching-spec | latest-available | upgrade-type`.

**Branch-pinned sources.** For sources whose REVISION is a branch name (e.g., `main`, `develop`), both "latest-matching-spec" and "latest-available" columns display the current HEAD SHA of that branch (truncated to 12 chars). The "upgrade-type" column reads `drift` when the locked SHA differs from the branch HEAD. There is no "latest available across all branches" notion; operators who want that should switch to tag-based pinning.

**Exit code (Section 0.2):** 0 always, regardless of upgrade availability. With `--fail-on-upgrade`, exit non-zero (specifically: 1) if any source has an available upgrade, for CI gating use.

### 4.5 `kanon why`

Explain why a transitive package or source is in the resolved tree.

```
kanon why <name-or-url> [--kanon-file <path>] [--lock-file <path>]
          [--catalog-source ...] [--format {text,json}] [--no-color]
```

**Behaviour:**

1. Read the `.kanon` file. Resolve the full tree (from `.kanon.lock` if present; otherwise live-resolve, which requires a catalog source).
2. The argument is matched in this precedence:
   - **A `<project>` repo URL** (the most common case; canonicalized via `canonicalize_repo_url`).
   - A transitive XML manifest path (e.g., `repo-specs/git-connection/remote.xml`).
   - A top-level source name (one of the `KANON_SOURCE_<name>_*` keys; matched via `derive_source_name()` so both `foo_bar` and `Foo-Bar` work).
3. **Ambiguity detection.** If the argument exact-matches in more than one category (e.g., an entry's URL is identical to a transitive XML path — extremely unlikely but possible with `file://` test fixtures), hard error listing both interpretations.
4. For every chain in the tree ending at the requested node, print:

```
<top-level-source> -> <xml-manifest-path>@<resolved-sha> -> ... -> <project>@<resolved-sha>
```

   With `--format json`: an array of chain objects, each chain a list of `{kind, name, ref, sha, url}` nodes.
5. If the requested node is not in the tree: hard error with the closest-match suggestion (Levenshtein distance ≤ 3 against the union of source names + project URLs + XML paths; suggest the top 3).

### 4.6 `kanon doctor`

Workspace health check. Single mode (catalog-author checks moved to `kanon catalog audit`, Section 4.8).

```
kanon doctor [--kanon-file <path>] [--lock-file <path>] [--catalog-source ...]
             [--refresh-completion-cache] [--strict-drift] [--prune-cache] [--no-color]
```

**Behaviour:**

1. Cross-check `.kanon` + `.kanon.lock` consistency (Section 5.1's `kanon_hash`). If `.kanon` is absent: hard error (`"no kanon workspace in <cwd>: '.kanon' not found"`). If `.kanon` is present but `.kanon.lock` is absent: info-level notice ("No lockfile present; run `kanon install` to generate one."); **skip checks 2-5 and 11**; still run checks 6-10.
2. Detect hand-edits to `.kanon` (`kanon_hash` mismatch) -> error.
3. Detect orphaned lock entries (source removed from `.kanon` but still in lock) -> error.
4. Detect branch drift (lockfile says SHA X for a branch-pinned dep; branch tip is now SHA Y). Behavior consistent across `doctor` and `install`: info-level notice on every invocation; `kanon doctor --strict-drift` upgrades to error. Each branch-pinned source costs one `git ls-remote refs/heads/<branch>` call, bounded by `KANON_RESOLVE_TIMEOUT` (default 30s) and subject to the existing `KANON_GIT_RETRY_COUNT` retry policy.
5. Verify every locked SHA is still reachable from its remote (`git ls-remote --exit-code <url> <sha>` check) -> error on dangling SHA.
6. **Effective catalog source.** Resolved via this precedence: (a) `--catalog-source` CLI flag; (b) `KANON_CATALOG_SOURCE` env var; (c) lockfile `[catalog].source` field (used here for display and as the effective source when CLI/env are absent — see Section 4 header for the `kanon install` / `kanon doctor` lockfile fallback); (d) none → print "no catalog source configured; commands requiring one will fail." Print the resolved value so the operator can sanity-check leakage from shell profiles.
7. Report the N most recent completion errors from `${KANON_CACHE_DIR}/completion-errors.log` (default N=5).
8. With `--refresh-completion-cache`: invalidate the completion cache before any other checks; useful escape hatch when cache is corrupt.
9. **Completion-script staleness check** (when a static completion script is installed): compare the on-disk script's hash to a fresh `kanon completion <shell>` invocation; warn on drift.
10. With `--prune-cache`: prune `${KANON_CACHE_DIR}` entries that have not been accessed in `KANON_CACHE_PRUNE_AGE_DAYS` days (default 30). Reports what was pruned. Also reports any stale legacy `.kanon-data/.kanon-install.lock` files held by no live process (advisory; doctor does not delete them — `fcntl.flock` already self-cleans on process exit, so a leftover file on disk is harmless and does not block subsequent installs).
11. **Remote reachability sanity check.** Runs ONLY when `.kanon.lock` is present. For every distinct `<remote>` URL in the lockfile, run `git ls-remote --exit-code <url> HEAD` (with `KANON_RESOLVE_TIMEOUT` and the standard retry policy). Errors are reported but do not fail the doctor command (network issues are transient); they surface as actionable diagnostics. Helps catch missing SSH keys / unconfigured credential helpers without bypassing the operator's git client (Section 3.6).

`--help` prominently lists each subcheck as a section so operators discover the surface.

### 4.7 `kanon install` (extension: lockfile)

**Today:** kanon-native install engine (using the embedded repo fork under `src/kanon_cli/repo/`) reads `.kanon`, creates per-source workspaces under `.kanon-data/sources/<name>/`, runs `repo init / envsubst / sync` per source, aggregates symlinks into `.packages/`, updates `.gitignore`, and (when `KANON_MARKETPLACE_INSTALL=true`) installs marketplace plugins into `CLAUDE_MARKETPLACES_DIR`. Concurrency serialized via `fcntl.flock(LOCK_EX)` on `.kanon-data/.kanon-install.lock`. Today's `kanon install` takes a single optional positional argument (the path to `.kanon`); no other flags.

**With this spec:** `kanon install` gains the flags below and the lockfile state machine.

| State | Behaviour |
|---|---|
| `.kanon` exists, `.kanon.lock` absent | Resolve every transitive version fresh. Install. Write `.kanon.lock` capturing every resolved SHA + the catalog source + the `kanon_hash` (Section 5.1). Info-line: `"lockfile rebuilt from .kanon (N sources, M projects)"`. |
| `.kanon` exists, `.kanon.lock` exists, both consistent (matching `kanon_hash`) | Install EXACTLY the SHAs in the lockfile. Ignore newer tags. Do NOT re-resolve. Info-line: `"installing from lockfile (N sources, M projects)"`. **Catalog source may be read from the lockfile's `[catalog].source` when no CLI/env source is set (see Section 4 header).** |
| `.kanon` modified (hash mismatch) | Hard error. Operator runs `kanon install --refresh-lock` (full) or `--refresh-lock-source <name>` (one chain) to rebuild. |
| `.kanon.lock` references a SHA no longer reachable | Hard error. |
| `.kanon.lock` records a different catalog source than CLI/env (when CLI/env is set; the lockfile is authoritative) | Hard error; remediation is `--refresh-lock` (operator changed catalogs intentionally). |
| Branch drift (lockfile SHA != branch tip) | Reuse locked SHA. Info-level notice printed. Drift requires `--refresh-lock` or `--refresh-lock-source <name>` to accept. `--strict-drift` upgrades to error. |
| Transitive conflict (two `<project>` entries point at same canonicalized repo URL, different SHAs) | Hard error. List both source paths AND the canonicalization that produced the match. |
| Orphaned lock entry (source removed from `.kanon`) | Default: prune lock entry, proceed. With `--strict-lock`: hard error. |
| `<include>` cycle detected | Hard error; print the cycle as `A.xml -> B.xml -> A.xml` (detection via DFS visited-set; first re-visit triggers the error). |
| `<include>` diamond (two paths to the same XML) | Allowed; dedupe by canonical path. |
| `<remote>` non-HTTPS URL (in resolved manifest) | Hard error unless `KANON_ALLOW_INSECURE_REMOTES=1`. |
| Resolution finds zero PEP 440-parseable tags under prefix | Hard error listing the non-PEP-440 tag names that were skipped, with remediation: "ask the catalog author to publish PEP 440-compliant release tags, or use 'kanon catalog audit --check tag-format' to identify which tags are unaddressable." |
| Git auth failure (e.g., missing SSH key, expired token; matches `Authentication` / `Permission denied` patterns) | Propagate the raw git stderr verbatim, prefixed with `"ERROR: git authentication failed against <url>. See docs/git-auth-setup.md."`. No retry (existing auth-error retry-skip policy). No prompting. |

**Flags (all new; today's `kanon install` has none):**

| Flag | Behaviour |
|---|---|
| `--refresh-lock` | Ignore the lockfile, re-resolve from scratch, overwrite `.kanon.lock`. Requires CLI/env catalog source (no lockfile fallback when refreshing). |
| `--refresh-lock-source <name>` | Re-resolve one top-level source's full chain; preserve all other lockfile entries. Accepts source name or entry name (same normalization as `kanon remove`). Requires CLI/env catalog source. |
| `--strict-lock` | Treat orphaned lock entries as a hard error instead of pruning. |
| `--strict-drift` | Treat branch-drift notices as hard errors (same semantics as `kanon doctor --strict-drift`). |
| `--lock-file <path>` | Override default lockfile path. Default: derived from `--kanon-file` (see below). Env: `KANON_LOCK_FILE`. |
| `--kanon-file <path>` | Override default `./.kanon` (formalizes today's positional `kanonenv_path` argument as a flag; positional retained as legacy). Env: `KANON_KANON_FILE`. |
| `--no-color` | Disable color (default: auto-detect TTY + `NO_COLOR`). |

**Default `--lock-file` derivation.** When `--kanon-file` is the default (`./.kanon`), `--lock-file` defaults to `./.kanon.lock`. When `--kanon-file` is set to a non-default path (CLI flag or `KANON_KANON_FILE` env var), `--lock-file` defaults to `<kanon-file-path>.lock`. Operators running parallel installs in the same directory with different `--kanon-file` values therefore get distinct lockfile paths by default; explicit `--lock-file` always wins. `KANON_LOCK_FILE` env var, when set, also wins over the derivation.

**Atomicity.** Writes to the lockfile use a write-temp-then-rename pattern. A SIGTERM mid-install leaves either the previous lockfile or the new lockfile, never a partial one. Per-project clones (which touch many files in `.kanon-data/sources/<name>/`) are NOT atomic in aggregate; a SIGTERM during clone may leave a partially-cloned project directory. `kanon clean --orphans` (Section 0.1) helps recover; documented in `docs/troubleshooting.md`. Today's `fcntl.flock(LOCK_EX)` on `.kanon-data/.kanon-install.lock` is preserved (kernel releases on process exit; leftover file harmless).

### 4.7.1 Install engine details (the contract)

`kanon install` is implemented by `core/install.py::install` orchestrating kanon's embedded repo fork (`src/kanon_cli/repo/`). No external `repo` binary is required; the fork is part of the kanon wheel.

The engine's responsibilities (today, plus this spec's lockfile additions):

1. **Resolve top-level sources.** Read `.kanon` triples (`parse_kanonenv`). For each source, resolve the manifest-repo URL @ revision to a concrete git ref + SHA via `git ls-remote` (subject to `KANON_GIT_RETRY_COUNT` retries).
2. **Create per-source workspaces** under `.kanon-data/sources/<source-name>/`. Run `repo init -u <url> -b <resolved-revision> -m <manifest-path>` in each (embedded `kanon_cli.repo.repo_init`).
3. **`repo envsubst`** with `GITBASE` and `CLAUDE_MARKETPLACES_DIR` from the `.kanon` globals, expanding `${VAR}` references inside the manifest XML.
4. **`repo sync`** per source — clones each `<project>` at the resolved SHA. Walks `<include>` chains transitively. Cycle detection per Section 4.7.
5. **Aggregate symlinks** into `.packages/` (`aggregate_symlinks` in `core/install.py`). Conflict detection if two sources produce the same package name (existing behavior; this spec extends to canonical-URL-based conflict at the `<project>` level).
6. **Auto-update `.gitignore`** with `.packages/` and `.kanon-data/` (existing).
7. **Marketplace install** (gated on `KANON_MARKETPLACE_INSTALL=true`): clean + populate `CLAUDE_MARKETPLACES_DIR`, run `install_marketplace_plugins`.
8. **Write `.kanon.lock`** atomically (write-temp + rename) at the path derived per Section 4.7.

The contract that `kanon install` exposes to consumers:
- `.kanon` is the only mutable input.
- The lockfile is the only mutable output (atomically). The install workspace (`.kanon-data/`, `.packages/`, `.gitignore`) is rebuildable from the lockfile + network access.
- Per-project clones live under `.kanon-data/sources/<source-name>/.repo/...` (managed by the embedded repo fork) and are surfaced via symlinks in `.packages/`. **Operators MUST treat directory layout as opaque; use the lockfile as the source of truth for which clones exist.**
- Exit code 0 = success; 1 = resolution/clone error; 3 = deprecated invocation (does not apply to `install`).

`docs/architecture.md` (NEW) documents the engine internals: the embedded repo fork, directory layout, lockfile-to-clone mapping, retry policy, and the error-propagation contract.

### 4.8 `kanon catalog audit`

Catalog-author health check. Separated from `kanon doctor` (workspace concerns) and from `kanon validate metadata` (in-repo author concerns) for single-responsibility: `kanon catalog audit` is the **consumer-side** check, runnable against a remote `<url>@<ref>` catalog source.

```
kanon catalog audit [<dir-or-source>]
                    [--check <subset>[,<subset>...]]
                    [--format {text,json}] [--strict] [--no-color]
```

- Default argument: `.` (current directory must be a manifest repo root).
- Otherwise: a directory path, OR a `<git_url>@<ref>` catalog source (clones to cache, audits there).
- `--check` accepts a comma-separated list (e.g., `--check metadata,tag-format`) OR the single value `all` (default). Valid subset names: `metadata`, `source-name-derivation`, `entry-name-uniqueness`, `remote-url`, `tag-format`. Empty list, invalid name, or mixing `all` with other subsets (e.g., `--check all,metadata`) → hard error.
- Runs the soft-spot 1-5 checks from Section 3.5:
  - `metadata`: required + recommended fields per soft-spot 1.
  - `source-name-derivation`: normalization warnings per soft-spot 2.
  - `entry-name-uniqueness`: cross-XML name collisions per soft-spot 3.
  - `remote-url`: `<remote>` resolvability per soft-spot 4.
  - `tag-format`: PEP 440 tag-name compliance per soft-spot 5 (warn-only: lists tags whose last path component is not a valid PEP 440 version).
- One entry per violation, naming the entry/path and the missing piece.
- Exit non-zero (1) if any error. With `--strict`, also fail on warnings.

The audit MUST detect the presence of a legacy `catalog/<name>/` directory tree in the manifest repo and emit a warning: `"Legacy catalog/ directory detected; this directory is unused by kanon ≥ <this-version> and should be deleted; see docs/migration-bootstrap-to-add.md"`. Not a hard error during the deprecation window. The deprecation window's end (and the version at which the warning is promoted to an error) is tracked in Section 15.

`docs/catalog-author-guide.md` (NEW) explains each audit check, what it looks for, and how to fix violations.

### 4.9 `kanon bootstrap` (deprecated)

Both `kanon bootstrap` and `kanon bootstrap list` are retained as **deprecation shims** for one release cycle. Each prints a WARN to stderr that names the exact replacement command and **exits with status 3 without performing any work**. The operator runs the suggested replacement explicitly. The shim does NOT delegate, does NOT read manifest-repo content, and does NOT touch the filesystem.

Both shims live in `commands/bootstrap.py`; the canonical `--catalog-source` flag definition lives in `core/cli_args.py` (Section 3) so its lifecycle is decoupled from the shim's eventual removal.

**Behaviour:**

| Invocation | WARN text (stderr) | Exit code |
|---|---|---|
| `kanon bootstrap list [...flags]` | `WARN: 'kanon bootstrap list' is deprecated. Run instead:`<br>`    kanon list [...translated flags]`<br>`See docs/migration-bootstrap-to-add.md.` | 3 |
| `kanon bootstrap <name> [...flags]` | `WARN: 'kanon bootstrap <name>' is deprecated. Run instead:`<br>`    kanon add <name> [...translated flags]`<br>`See docs/migration-bootstrap-to-add.md.` | 3 |
| `kanon bootstrap --help` (or no args) | `--help` output is unchanged for discoverability BUT prepended with `DEPRECATED: 'kanon bootstrap' is replaced by 'kanon add' and 'kanon list'. See docs/migration-bootstrap-to-add.md.` | 0 (help is informational) |

**Exit code 3** is reserved for "invocation uses a deprecated command." Distinct from: 0 (success), 1 (runtime/usage error), 2 (argparse usage error — already used by `cli.py:117` for "no subcommand given"). Documented in `docs/exit-codes.md`.

**Today's bootstrap flag set** (verified from `commands/bootstrap.py`): only `<package>` (positional), `--output-dir <path>` (default `.`), `--catalog-source <git_url>@<ref>`. No `--force`, no `--dry-run`, no `--kanon-file`. The flag translation table reflects this:

| Bootstrap flag (today) | Replacement (`kanon add`) | Replacement (`kanon list`) | Notes |
|---|---|---|---|
| `<package>` positional | `<name>` positional | (n/a — `bootstrap list` triggers `kanon list`) | identical semantics |
| `--catalog-source <v>` | `--catalog-source <v>` | `--catalog-source <v>` | identical |
| `--output-dir <v>` | (no equivalent) | (no equivalent) | WARN includes: `"--output-dir has no direct equivalent in 'kanon add'; the install workspace is the current directory."` Today's bootstrap copies files INTO `--output-dir`; the new `kanon add` writes to `--kanon-file` in cwd. |

**`kanon bootstrap list` flag set.** Only `--catalog-source` is meaningful. Any other flag triggers a generic `"--<flag> has no equivalent in 'kanon list'"` notice in the WARN.

**Removed behavior.** `kanon bootstrap` no longer reads `catalog/<name>/.kanon` template files from the manifest repo OR the bundled `src/kanon_cli/catalog/`. The shim does not call into `<catalog-metadata>` parsing or any catalog/filesystem boundary; when the operator runs the suggested replacement (`kanon add <name>`), that command reads from `<catalog-metadata>` per Section 4.2. **The bundled `src/kanon_cli/catalog/` directory is deleted from the kanon wheel** as part of the same epic that introduces the shim, so the third-tier "bundled fallback" in `resolve_catalog_dir()` is removed.

**Completion (`__complete_*` subcommands) IS allowed to read manifest-repo content.** Completion runs in a separate process invoked by the shell at tab-time, not by the operator at command-execution time. The shim's no-boundary-call rule applies to its invocation path only. Tab-completion for `kanon bootstrap <name>` reuses the same `__complete_catalog_entries` Python function as `kanon add` (Section 11.3), keeping interactive UX continuous during the deprecation window.

**Documentation.** `docs/migration-bootstrap-to-add.md` (NEW) covers the deprecation: what changed, why, the 1:1 command translations, the exit-3 contract (no delegation; operators run the suggested replacement command explicitly), the rationale (force migration at the CI / script boundary), how manifest repos changed (no more `catalog/<name>/` directory; no more bundled catalog in the wheel), the migration timeline. Linked from every WARN line and from CHANGELOG.

**Hard removal.** Tracked as a future-work item (Section 15). Not done in this spec.

---

## 5. Lockfile format (`.kanon.lock`)

TOML. Schema-versioned. Schema version 1:

```toml
schema_version = 1
generated_at = "2026-05-11T13:42:00Z"
generator = "kanon-cli/<version>"
kanon_hash = "sha256:..."                                  # see Section 5.1

[catalog]
source = "https://example.com/org/manifest-repo.git@==2.10.0"
url = "https://example.com/org/manifest-repo.git"
revision_spec = "==2.10.0"
resolved_ref = "refs/tags/2.10.0"
resolved_sha = "abc123..."

[[sources]]
name = "package_a"
url = "https://example.com/org/manifest-repo.git"
revision_spec = "==2.10.0"                                 # value the operator wrote (or 'main' for branch)
resolved_ref = "refs/tags/2.10.0"                          # canonical ref kanon found
resolved_sha = "abc123..."                                 # immutable git object id at install time
path = "repo-specs/common/package-a/package-a-marketplace.xml"

  [[sources.includes]]
  name = "git-connection/remote"
  path_in_repo = "repo-specs/git-connection/remote.xml"
  url = "https://example.com/org/manifest-repo.git"
  resolved_sha = "abc123..."

    [[sources.includes.includes]]                          # nested <include> chain example
    name = "git-connection/transitive"
    path_in_repo = "repo-specs/git-connection/transitive.xml"
    url = "https://example.com/org/manifest-repo.git"
    resolved_sha = "abc123..."

  [[sources.projects]]
  name = "vendor/example-package"
  url = "https://example.com/vendor/example-package.git"
  canonical_url = "https://example.com/vendor/example-package"     # output of canonicalize_repo_url
  revision_spec = ">=1.0.0,<2.0.0"
  resolved_ref = "refs/tags/1.4.2"
  resolved_sha = "def456..."
```

**Validation rules:**

- Every `resolved_sha` is a valid git object identifier (40 hex chars for SHA-1; 64 hex chars for SHA-256; future-proofed).
- Every `revision_spec` is either: a valid PEP 440 spec (with optional monorepo path prefix; e.g., `subpackage/==1.0.0`), a literal git ref (`refs/...`), or a branch name matching `[a-zA-Z0-9_./+-]+` (rejects branch names with embedded NUL, newline, tab, or shell metacharacters). **Note: kanon's branch-name regex is tighter than git's actual ref-name rules.** Manifest repos that use exotic branch names (e.g., names containing `@`, `{`, `}`) must rename branches before they can be addressed by kanon.
- Every `resolved_ref` ending in `refs/tags/...` MUST have a last path component that is a valid PEP 440 version (per soft-spot 5).
- `canonical_url` on every `[[sources.projects]]` row is the deterministic output of `canonicalize_repo_url(url)`.
- `path` and `path_in_repo` fields reject embedded tab, NUL, and newline characters (would break `kanon_hash` computation).
- `<include>` chains are represented as nested TOML tables (flattening loses the chain order needed by `kanon why`). Nesting depth is unbounded in principle; bounded in practice by manifest-repo structure.

### 5.1 Lockfile consistency hash (`kanon_hash`)

A deterministic SHA-256 over the normalized form of `.kanon`:

1. Parse the `.kanon` file via `parse_kanonenv`.
2. Extract every `KANON_SOURCE_<name>_{URL,REVISION,PATH}` triple. Discard comments, blank lines, and any non-`KANON_SOURCE_*` keys (`GITBASE`, `CLAUDE_MARKETPLACES_DIR`, `KANON_MARKETPLACE_INSTALL`).
3. Sort triples by source name; within a source, sort `URL` before `REVISION` before `PATH`.
4. Serialize as `name\turl\trevision\tpath\n` per source. Path or URL containing literal tab is rejected at serialization time (hard error during `kanon install` / `kanon add` write).
5. SHA-256 the serialized bytes; prefix with `sha256:`.

**Properties:**

- Re-ordering source blocks does NOT change the hash.
- Adding a comment does NOT change the hash.
- Changing any `REVISION` value DOES change the hash.
- Changing the source name DOES change the hash.
- Changing `GITBASE`, `CLAUDE_MARKETPLACES_DIR`, or `KANON_MARKETPLACE_INSTALL` does NOT change the hash (these are workspace-environment values, not consumer state).

**Relationship to the `[catalog]` lockfile block.** `kanon_hash` covers ONLY the `.kanon` triples (consumer state — sources the operator declared). The `[catalog].source` field in the lockfile is compared SEPARATELY to the CLI/env catalog source on every `kanon install` (Section 4.7). The two checks are independent: `kanon_hash` detects consumer-side drift; `[catalog].source` detects catalog-source switches. Both can fire independently.

`kanon install` and `kanon doctor` compare the `.kanon` file's current hash to the lockfile's `kanon_hash`. Mismatch is the *semantic* consistency check (replaces filesystem-mtime comparisons, which are unreliable under git checkouts and CI clones).

### 5.2 Lockfile schema migration policy

- Forward-incompatible reads (older kanon reading a newer schema) error: `"lockfile schema vN written by newer kanon; upgrade kanon-cli."`
- Backward-compatible reads (newer kanon reading an older schema): explicit per-version upgrade path. Schema v1 → v2 (when v2 lands) defines the diff.
- In-place migration: `kanon install --refresh-lock` rewrites at the current schema version.
- No silent rewrites. The operator always invokes the rewrite explicitly.

Documented in `docs/lockfile.md` with a worked v1→v2 example placeholder.

---

## 6. Version + branch interchangeability

Wherever an input is a "version", the same input slot accepts a branch name. Wherever an output reports a resolved value, both flavors are normalized to:

- `spec`: the string the operator wrote (`==1.0.0`, `~=2.0.0`, `main`, `feat/x`).
- `resolved_ref`: the canonical git ref (`refs/tags/1.0.0`, `refs/heads/main`).
- `resolved_sha`: the immutable commit SHA at command-execution time.

Lockfile captures all three (Section 5). Branch drift detection compares `resolved_sha` to current branch tip. The full resolver precedence is Section 4.0.

---

## 7. Error handling, logging, configuration

Per CLAUDE.md project-wide rules:

- **No silent failures.** Every error path emits a structured message naming what failed, what was expected, and what the operator can do next.
- **No fallback logic** beyond the documented exceptions: (a) `kanon install`/`kanon doctor` reading the lockfile's `[catalog].source` when CLI/env is absent; (b) the existing `git ls-remote` retry policy (3 attempts by default, configurable).
- **No hardcoding.** Every threshold, timeout, default is a constant (canonical default) AND override-able via env var and/or CLI flag.
- **Event-driven.** No `sleep`-based polling. `git ls-remote` retries use `KANON_GIT_RETRY_DELAY` between attempts (today's behavior, preserved).
- **No interactive prompts.** kanon NEVER asks the operator a question. Every decision is made via CLI flag, env var, or fail-fast error. Includes color, credentials, conflict resolution.
- **Temporal logs.** Single structured log line at command start + end with command, flags, exit code, duration. Reuse kanon's existing logging.
- **Legacy env var deprecation (existing).** `REPO_URL` and `REPO_REV` are deprecated; setting them emits a DeprecationWarning + stderr message via `_warn_if_legacy_env_vars_set` in `commands/install.py`. Out of scope for this spec; preserved.

**Verbosity flags:**

- `--quiet` suppresses info-level stderr (drift notices, "0 entries match" notes, completion-error reports from doctor, install info-lines). Errors and warnings still appear.
- `--verbose` enables debug-level logging including per-network-call detail (URL, latency, exit code, retry attempts).
- Mutually exclusive; passing both is a hard error.

**Color / TTY policy:**

- kanon NEVER prompts the operator about color. The choice is made deterministically:
  1. `--no-color` flag → no color, regardless of other settings.
  2. `NO_COLOR` env var set to any non-empty value → no color (de-facto standard: https://no-color.org).
  3. stdout is NOT a TTY → no color.
  4. Otherwise → color.
- `--no-color` and `NO_COLOR` are alternative ways to disable color; either is sufficient.
- No `--force-color` flag is added (out of scope; tracked in Section 15 if needed).
- All snapshot tests run with `NO_COLOR=1` set so output is deterministic.

**Timeout precedence.** `KANON_RESOLVE_TIMEOUT` (default 30s, NEW) bounds non-completion `git ls-remote` calls in `kanon install`, `kanon outdated`, `kanon why`, `kanon doctor`. `KANON_COMPLETION_TIMEOUT` (default 2s, NEW) bounds completer calls (Section 11). Today's `KANON_GIT_RETRY_DELAY` is preserved as the retry-between-attempts wait.

**Existing env vars** (preserved, listed for completeness):

| Env var | Purpose | Default |
|---|---|---|
| `KANON_CATALOG_SOURCE` | catalog source override | unset |
| `KANON_REPO_DIR` | embedded repo data dir | `.repo` |
| `KANON_GIT_RETRY_COUNT` | `git ls-remote` retry attempts | `3` |
| `KANON_GIT_RETRY_DELAY` | seconds between retries | `1` |
| `REPO_URL`, `REPO_REV` | DEPRECATED legacy env vars | unset |

**New env vars** (added by this spec; constant defaults; all override-able):

| Env var | Purpose | Default constant |
|---|---|---|
| `KANON_LOCK_FILE` | path to lockfile relative to cwd; when unset, derived from `KANON_KANON_FILE` (see Section 4.7) | derived |
| `KANON_KANON_FILE` | default target for `kanon add` / `remove` writes; also default `--kanon-file` for install/clean/etc. | `./.kanon` |
| `KANON_LIST_FORMAT` | default output format for `kanon list` | `names` |
| `KANON_LIST_LIMIT` | default cap on `--all-versions` | `50` |
| `KANON_OUTDATED_FORMAT` | default `kanon outdated` format | `table` |
| `KANON_WHY_FORMAT` | default `kanon why` format | `text` |
| `KANON_TREE_NO_FILTER_THRESHOLD` | entry count above which `kanon list --tree` requires a filter | `20` |
| `KANON_RESOLVE_TIMEOUT` | timeout per `git ls-remote` call (non-completion) | `30` seconds |
| `KANON_CACHE_PRUNE_AGE_DAYS` | age (in days) beyond which `kanon doctor --prune-cache` removes cache entries | `30` |
| `KANON_ALLOW_INSECURE_REMOTES` | allow non-HTTPS `<remote>` URLs and `file://` URLs | `0` (deny) |

See also Section 11.6 for completion-cache env vars (`KANON_CACHE_DIR`, `KANON_COMPLETION_*`).

**New CLI flags** (each mirrors its env var where applicable):

| Flag | Mirrors env | Notes |
|---|---|---|
| `--catalog-source` | `KANON_CATALOG_SOURCE` | already exists on `bootstrap` only; **canonical definition moves to `core/cli_args.py`** (Section 3) and is reused by every new command + new install/clean/validate flags below |
| `--kanon-file` | `KANON_KANON_FILE` | for `add`, `remove`, `outdated`, `why`, `doctor`, `install`, `clean` (today's positional `kanonenv_path` becomes legacy alias) |
| `--lock-file` | `KANON_LOCK_FILE` | for `install`, `doctor`, `outdated`, `why`; default derived from `--kanon-file` (Section 4.7) |
| `--format` | command-specific env var | `list` (`names`/`json`), `outdated` (`table`/`json`), `why` (`text`/`json`), `catalog audit` (`text`/`json`) |
| `--limit N` / `--no-limit` | `KANON_LIST_LIMIT` | for `list --all-versions` |
| `--all-versions` | -- | for `list` history walk; mutually exclusive with `--tree` |
| `--since-version <spec>` | -- | for `list --all-versions` filtering |
| `--tree` | -- | for `list` tree mode; mutually exclusive with `--all-versions` |
| `--max-depth N` | -- | for `list --tree` |
| `--no-filter-required` | -- | for `list --tree` (bypass the threshold guardrail) |
| `--detail` | -- | for `list` per-entry detail |
| `--regex <pattern>` | -- | for `list` regex filter |
| `--match-fields <csv>` | -- | for `list` filter scope (default: all four metadata fields); requires positional substring or `--regex` |
| `--refresh-lock` | -- | for `install` (rebuild lock) |
| `--refresh-lock-source <name>` | -- | for `install` (rebuild one chain) |
| `--strict-lock` | -- | for `install` (orphans -> error) |
| `--strict-drift` | -- | for `install` / `doctor` (drift notice -> error) |
| `--force` | -- | for `add` (overwrite block) / `remove` (unknown source name) |
| `--dry-run` | -- | for `add`, `remove` |
| `--fail-on-upgrade` | -- | for `outdated` (CI gate; Section 0.2) |
| `--refresh-completion-cache` | -- | for `doctor` |
| `--prune-cache` | -- | for `doctor` (remove stale cache entries beyond `KANON_CACHE_PRUNE_AGE_DAYS`) |
| `--check <csv>` | -- | for `catalog audit` (comma-separated subset; `all` accepts) |
| `--strict` | -- | for `catalog audit` (warnings -> errors) |
| `--orphans` | -- | for `clean` (Section 0.1) |
| `--quiet` / `--verbose` | -- | global; mutually exclusive |
| `--no-color` | (alternative to `NO_COLOR` env var) | global; suppresses ANSI color regardless of TTY |

### 7.5 Concurrency and atomicity

- **File locking (existing behavior, preserved).** `kanon install` uses `fcntl.flock(LOCK_EX)` on `.kanon-data/.kanon-install.lock` (defined as `INSTALL_LOCK_FILENAME` in `constants.py`). This is a blocking flock — concurrent `kanon install` invocations on the same workspace wait for the first to finish. `fcntl.flock` releases on process exit (graceful or crash), so a leftover lock file on disk is harmless. This spec extends the lock to also cover `kanon add` / `kanon remove` (which mutate `.kanon`) and `kanon doctor --refresh-completion-cache` (cache mutation) — same `.kanon-data/.kanon-install.lock`. If `.kanon-data/` does not exist yet (first invocation), kanon creates it eagerly before lock acquisition.
- **Atomic writes.** Every mutating operation (`add`, `remove`, `install` lockfile write, completer cache refresh) writes to a temp file in the same directory and renames into place. A SIGTERM/SIGINT mid-operation leaves either the previous state or the new state; never a partial file.
- **Cache file integrity.** Each cache `index.txt` / `tags.txt` is paired with `fetched_at.txt`. Both are written under the same temp+rename pattern. A reader that sees `index.txt` without `fetched_at.txt` (corruption) treats the cache as missing.
- **Per-project clone atomicity is NOT guaranteed.** A SIGTERM during a project clone may leave a partially-cloned directory under `.kanon-data/sources/<name>/.repo/`. Operators recover via `kanon clean --orphans` followed by re-`kanon install`. Documented in `docs/troubleshooting.md`.

---

## 8. Documentation updates (`kanon` open-source repo)

All in the `kanon` open-source repo, vendor-agnostic.

- **`README.md`**:
  - Add a top-level **"Quick start: find and add dependencies"** section above the existing quick start. Worked example using placeholder catalog URL (mirrors Section 2.1 verbatim).
  - Add a **"Subcommands"** overview table. Every `kanon <subcommand>` listed with one-line summary and link to its own doc. `kanon bootstrap` row marked `deprecated; see kanon add / kanon list`. Include existing `kanon repo` (currently missing from README) and `kanon validate xml|marketplace|metadata`.
  - Add a one-paragraph "Tab completion" section under Quick Start, linking to `docs/shell-completion.md`.
  - Add a one-paragraph "Git authentication" section linking to `docs/git-auth-setup.md`.
  - Add a one-paragraph "Migration from `kanon bootstrap`" section linking to `docs/migration-bootstrap-to-add.md`.
- **`docs/catalogs-explained.md`** (NEW): what a manifest repo is, who runs them, how to find one to point at, link to `docs/creating-manifest-repos.md` for authors. Explicitly states the manifest-repo-IS-the-catalog terminology (Section 1.1). First-time-user on-ramp; linked from every missing-catalog-source error.
- **`docs/list-and-add.md`** (NEW): full reference for `kanon list`, `kanon add`, `kanon remove`. Every flag, every env var, every output format, every error scenario with reproducer. Shell-quoting examples for PEP 440 range specs prominently shown. Documents source-name derivation rule (Section 1.1). Includes the `--all-versions` worked example. Explains why `kanon add` does not validate `<remote>` resolvability or tag-format (Section 4.2).
- **`docs/outdated-and-why.md`** (NEW): full reference for `kanon outdated`, `kanon why`. CI integration patterns including `--fail-on-upgrade`.
- **`docs/doctor.md`** (NEW): full reference for `kanon doctor`. Each subcheck named with the error message it emits.
- **`docs/catalog-author-guide.md`** (NEW): full reference for `kanon catalog audit`. Each soft-spot check (1-5) named, with what it looks for and how to fix.
- **`docs/lockfile.md`** (NEW): `.kanon.lock` format, semantics, conflict resolution, refresh flow, schema migration policy (Section 5.2), `kanon_hash` semantics (Section 5.1), `[catalog].source` vs `kanon_hash` distinction, default-lockfile derivation from `--kanon-file` (Section 4.7).
- **`docs/version-resolution.md`**: extend with Section 4.0 resolver precedence + `@` parsing rule + monorepo prefix support + bare-PEP-440-version widening. Note that non-PEP-440 tag names are unaddressable via `kanon add`/install resolution.
- **`docs/configuration.md`**: add every new env var to the existing table, grouped into subsections: "Catalog source", "Resolver behavior", "File paths", "Lockfile", "Concurrency", "Completion cache", "Retry policy" (documents existing `KANON_GIT_RETRY_COUNT` / `KANON_GIT_RETRY_DELAY`). Add the warning about `KANON_CATALOG_SOURCE` leaking from shell profiles. Explicitly note there is no default catalog source (post-bootstrap-deprecation) and no rc-file mechanism.
- **`docs/multi-source-guide.md`**: add a note that `kanon add` is the recommended way to produce `KANON_SOURCE_*` triples.
- **`docs/creating-manifest-repos.md`**: REWRITE to remove all references to the legacy `catalog/<name>/` directory. New shape: a manifest repo has `repo-specs/**/*-marketplace.xml`; each XML is a catalog entry identified by `<catalog-metadata>`. NEW **"Catalog entry contract"** section listing the soft-spot 1-5 rules from Section 3.5 as MUST requirements. NEW **"Tag publishing"** subsection: tag names MUST be PEP 440 compliant (with optional monorepo path prefix); examples + counter-examples. NEW **"Migrating away from `catalog/<name>/`"** subsection. NEW **"Testing your manifest repo"** subsection: (1) `kanon catalog audit . --strict`; (2) `kanon validate xml`; (3) `kanon validate marketplace`; (4) `kanon validate metadata`; (5) clone to a scratch dir; (6) `kanon list --catalog-source ./scratch@main`; (7) `kanon add <one-entry> --catalog-source ./scratch@main`; (8) `kanon install`.
- **`docs/migration-bootstrap-to-add.md`** (NEW): operator-facing migration guide. Covers: why bootstrap is deprecated, the 1:1 command translations (`kanon bootstrap X` → `kanon add X`; `kanon bootstrap list` → `kanon list`), the flag-translation table from Section 4.9, the exit-3 contract (no delegation; operators run the suggested replacement command explicitly), the rationale (force migration at the CI / script boundary), how manifest repos changed (no more `catalog/<name>/` directory), how the kanon wheel changed (bundled `src/kanon_cli/catalog/` removed), the migration timeline. Linked from every WARN line, from CHANGELOG, and from README.
- **`docs/security-model.md`** (NEW): the trust model from Section 3.6 verbatim, with a "what manifest repos can do to you" section AND a "what kanon does NOT do" section (no credential handling, no provider APIs, no interactive prompts). Documents the existing auth-error retry-skip policy.
- **`docs/git-auth-setup.md`** (NEW): supported git authentication configurations on common platforms (macOS, Linux, Windows). HTTPS via credential helpers (OAuth, PAT, native OS keychains), SSH via `url.insteadOf` rewrites. Per-host configuration patterns. Clean-slate procedures for common conflict scenarios. Explicitly says kanon does NOT handle credentials; it inherits the operator's local git config.
- **`docs/troubleshooting.md`** (NEW): top errors with reproducer + fix. Includes unquoted PEP 440 specs, missing catalog source, lockfile schema mismatch, branch drift, completion cache corruption, missing `<catalog-metadata>`, entry-name collision, git auth failure (cross-ref `docs/git-auth-setup.md`), partial clone after SIGTERM (cross-ref `kanon clean --orphans`), zero-PEP-440-tags manifest-repo (use branch pin OR ask catalog author to rename non-PEP-440 tags), `REPO_URL`/`REPO_REV` legacy-env-var warnings.
- **`docs/migrating-existing-kanon-files.md`** (NEW): for operators with hand-written `.kanon` predating these features. Covers first-`install` lockfile generation, source-name compliance, source-name normalization warnings, common fixes.
- **`docs/catalog-format-versioning.md`** (NEW): catalog-format version handshake placeholder (Section 15 future work).
- **`docs/shell-completion.md`** (NEW): the full operator-facing completions guide. See Section 11.7.
- **`docs/exit-codes.md`** (NEW): canonical exit-code table. 0 = success; 1 = usage / runtime / resolution error; 2 = argparse usage error (existing; "no subcommand given"); 3 = deprecated invocation (`kanon bootstrap`); future codes reserved. Cross-referenced from every command's `--help` (the "Exit codes" section).
- **`docs/architecture.md`** (NEW): the embedded-repo-fork install engine internals (Section 4.7.1). Directory layout under the install workspace (`.kanon-data/`, `.packages/`), lockfile-to-clone mapping, retry policy, error-propagation contract. Includes a "Why kanon doesn't use an external repo tool" note.
- **`docs/coming-from-pip-npm-cargo.md`** (NEW): translation guide for new users from neighbor ecosystems. `kanon install` ≈ `pip install -r requirements.txt`; `.kanon` ≈ `requirements.txt`; `.kanon.lock` ≈ `Pipfile.lock` / `package-lock.json`; manifest repo ≈ index URL (with the caveat that there's no central instance).
- **`docs/repo/*`**: existing untouched, but the README "Subcommands" table must link to them.
- **`CHANGELOG.md`**: Keep-a-Changelog format (https://keepachangelog.com). One entry per new feature under "Added"; one entry under "Changed" for `kanon validate` extensions (new `metadata` sub-subcommand); one under "Deprecated" for `kanon bootstrap`; two under "Removed" — one for the legacy `catalog/<name>/` model from the catalog-author standard, one for the bundled `src/kanon_cli/catalog/` directory from the wheel.

---

## 9. Caylent-private-kanon scope (parallel work)

The kanon CLI work above does not depend on caylent-private-kanon changes; the new commands work against caylent-private-kanon as-is today once `<catalog-metadata>` is complete and entry names are unique. But the operator's directive is that we update caylent-private-kanon's own docs + CI + work units to enforce 100% compliance with the standards codified in Section 3.5 AND delete the legacy `catalog/<name>/` directory tree.

Split into engineering scope (9a), operational migration (9b), and sibling-repo scope (9c):

### 9a. Engineering (caylent-private-kanon repo)

**Docs:**

- Update `caylent-private-kanon/README.md` to document the contract every catalog entry must satisfy (copy the relevant rules from kanon's `docs/creating-manifest-repos.md` once they land upstream, or link to them). Remove any reference to `catalog/<name>/` from operator-facing docs.
- Update or create a `caylent-private-kanon/docs/contributing.md` section: how to add a new catalog entry (a new `*-marketplace.xml` with a complete `<catalog-metadata>` block; no per-entry directory). Soft-spot 1-5 rules called out explicitly. PEP 440 tag-publishing convention documented.
- Remove any doc passage describing `catalog/<name>/` as the entry-author surface.

**CI:**

- Add a CI job that runs `kanon catalog audit .` against the repo root on every PR + every push to main. Fails the build on any violation. With `--strict` after the migration in 9b is complete.
- Add a CI job that runs `kanon validate xml`, `kanon validate marketplace`, AND `kanon validate metadata` on every PR. Fails the build on any violation.
- Add a CI job that installs the latest kanon CLI, runs `kanon list --catalog-source <this-repo-at-HEAD>` (smoke test), and asserts every catalog entry returned matches what `git ls-files repo-specs/**/*-marketplace.xml` returns (no silent skips, no orphans).
- Add a CI guard that fails the build if a new `catalog/<name>/` directory is added to the repo after Phase 2 (9b) completes. Implementation: check `git diff --name-only origin/main..HEAD` for paths matching `catalog/`; fail if any present (post-Phase 2). Before Phase 2, the guard tolerates the existing tree but errors on new entries.
- Add a CI guard that fails the build if any tag pushed to the repo has a last-path-component that is not a valid PEP 440 version (soft-spot 5; warns first, hard error after a deprecation window).

### 9b. Operational migration (caylent-private-kanon repo)

Bulk-processable per-entry work; the autonomous loop runs these in parallel. Two phases:

**Phase 1 — Per-entry `<catalog-metadata>` migration (one task per non-conforming entry):**

- Audit the entry's `*-marketplace.xml` `<catalog-metadata>` block. Add any missing REQUIRED fields (`name`, `display-name`, `description`, `version`) per soft-spot 1.
- Migrate any author-facing metadata that lived in the legacy `catalog/<name>/README.md` or `catalog/<name>/kanon-readme.md` into the XML's `<catalog-metadata><description>` **verbatim** (preserve full README content; truncation/editing is a human-review decision at PR time, not an autonomous-loop decision).
- Verify the entry name is unique across the repo (soft-spot 3).
- Verify every `<project remote="X">` resolves to a concrete fetch URL via reachable `<include>` chain (soft-spot 4).
- Verify the entry's referenced `<project>` revisions are PEP 440-compliant tags (soft-spot 5); if not, file an upstream issue against the target repo and document in the entry's description.
- Acceptance: `kanon catalog audit --check all <this-entry-XML>` passes.

**Phase 2 — Delete the legacy `catalog/` directory tree (single task; runs after all of Phase 1 is complete):**

- Delete `caylent-private-kanon/catalog/` in its entirety.
- Remove any internal tooling references to `catalog/<name>/` paths.
- Acceptance: `git ls-files catalog/` returns zero tracked files; CI green; `kanon list --catalog-source <this-repo-at-HEAD>` still returns every expected entry.

**Work-unit traceability.** One Phase-1 task per non-conforming entry in the backlog. Each task's Manifest covers the entry's XML; AC enforces post-migration `kanon catalog audit` passes against that entry. One Phase-2 task at the end gated on every Phase-1 task being done. Task shape is homogeneous to let the autonomous loop parallelize Phase 1.

### 9c. Sibling repo: kanon-claude-marketplaces

Out of scope for active migration in this spec. A single backlog task audits kanon-claude-marketplaces against the same standards and files follow-up issues per violation (does NOT auto-fix). Rationale: lower-priority repo; explicit follow-up keeps blast radius small.

---

## 10. Testing requirements

100% line coverage on every line of new code. 100% functional coverage on every command end-to-end. Every workflow + every error scenario. Defensive "should-never-happen" branches are restructured (raise on impossible state) rather than excluded from coverage; CLAUDE.md forbids `# pragma: no cover` annotations.

**Unit tests** (`tests/unit/`):

- `_parse_catalog_metadata()` handles missing fields, extra fields, duplicate children, malformed XML, multiple `<catalog-metadata>` blocks in one file (error).
- `derive_source_name()` (the entry-name → source-name normalizer per soft-spot rule 2): identity case, dash→underscore, mixed case (always lowercased), characters outside `[a-zA-Z0-9_-]` (still legal; warning emitted). Determinism test: same input always yields same output.
- `_format_list_output()` for every `--format` value.
- `kanon_hash()` (Section 5.1) determinism: same content → same hash; comment edits → same hash; `KANON_MARKETPLACE_INSTALL` edits → same hash; spec change → different hash; reorder blocks → same hash; tab-in-path rejection.
- `canonicalize_repo_url()` (Section 4.0): ssh ↔ https, `.git` suffix, trailing slash, embedded user-info, case sensitivity, query string rejection, fragment rejection.
- `_parse_catalog_source()` (existing) `@` splitter: `git@host:org/repo.git@main` → `(git@host:org/repo.git, main)`; `https://h/o/r.git@==1.0.0` → `(..., ==1.0.0)`; no-`@` rejected; empty URL/ref rejected.
- Default-lockfile derivation (Section 4.7): `--kanon-file` default → lockfile `./.kanon.lock`; `--kanon-file ./alt.kanon` → lockfile `./alt.kanon.lock`; explicit `--lock-file` always wins; `KANON_LOCK_FILE` env var wins over derivation.
- Lockfile reader/writer round-trip; corrupt-lockfile detection; schema-version mismatch (older + newer).
- Version-spec resolver (Section 4.0): PEP 440 specs (operators), PEP 440 local versions (`1.0+local`), PEP 440 prereleases (`1.0a1`), `*` and `latest` wildcards, monorepo-prefixed constraints (`subpackage/==1.0`, `dev/python/lib/~=1.2`), branch names, full refs (`refs/tags/x`, `refs/heads/x`), raw SHAs (40 + 64 hex). **Widened bare PEP 440** (`1.0.0a1`, `1.0+local`, `2026.4.1` all → `refs/tags/<spec>`). No fallback between kinds.
- Zero-PEP-440-tags case: resolver against fixture with only non-PEP-440 tags (e.g., `v1.0.0`, `release-2024`) → hard error listing the skipped names.
- `kanon add` triple construction from `<catalog-metadata>` + manifest-repo URL + XML path (no legacy template); writes standard header (`GITBASE`, `CLAUDE_MARKETPLACES_DIR`, `KANON_MARKETPLACE_INSTALL`) when file is new.
- `kanon add` zero-tags case (no `@<spec>` against manifest repo with no PEP 440 tags) → hard error suggesting branch pin.
- `kanon add` collision detection: across requested entries; against destination file; full error-message detail.
- `kanon add` manifest-repo integrity failure → consumer-side hard error pointing at `kanon catalog audit`.
- `kanon add` does NOT validate `<remote>` resolvability or tag-format (soft-spots 4 + 5 not invoked by add).
- `kanon remove` accepts source name AND entry name (normalization symmetry); non-contiguous triples handled.
- Conflict detector: same canonicalized repo URL with different resolved SHAs.
- `kanon catalog audit` checks 1-5 individually; legacy `catalog/<name>/` directory detection (warning); `--check` multi-select parser; `--check all,metadata` rejection. Tag-format check: scans `git ls-remote --tags`, flags non-PEP-440 last-path-components.
- `kanon validate metadata` (new sub-subcommand): same checks 1-3 as catalog audit but run in-repo (no clone).
- `kanon why` chain-walking against synthetic trees; cycle detection; diamond dedup; ambiguity detection; Levenshtein suggestion.
- `kanon outdated` comparison logic; branch-pinned source columns; zero-PEP-440-tags → loud error in latest-* columns.
- `kanon remove` line-ending preservation: LF-only, CRLF, mixed (with warning), no-trailing-newline; non-contiguous triples removed correctly.
- `kanon bootstrap` shim (Section 4.9): WARN text snapshot for both `kanon bootstrap <name>` and `kanon bootstrap list`; flag-translation correctness against the actual today's flag table (Section 4.9: `<package>`, `--output-dir`, `--catalog-source`); `--output-dir` no-equivalent message; `bootstrap list` non-`--catalog-source` flag triggers generic no-equivalent notice; exit code is 3 for any invocation other than `--help`; `--help` prints DEPRECATED-prefixed help and exits 0; the shim makes ZERO calls into `core/catalog.py` / `<catalog-metadata>` parsing / filesystem mutators (assert via mocked-out boundary). Completion (`__complete_*`) IS allowed to read manifest content; tested separately.
- Bundled-catalog removal: assert `src/kanon_cli/catalog/` is gone from the wheel after the deprecation epic; `resolve_catalog_dir()` errors on missing CLI/env source (no bundled fallback).
- Color/TTY policy: `NO_COLOR=1` suppresses color even on TTY; `--no-color` flag wins; non-TTY stdout never emits color; all snapshot tests run with `NO_COLOR=1`.
- `--quiet` / `--verbose` mutually exclusive (passing both → hard error).
- Mutually exclusive list flags: `--tree` + `--all-versions` → hard error; `--match-fields` without filter → hard error.
- Retry policy (existing, regression-tested): `git ls-remote` retried `KANON_GIT_RETRY_COUNT` times with `KANON_GIT_RETRY_DELAY` between; auth-error patterns skip retries.

**Functional tests** (`tests/functional/`):

- Every command:
  - With `--catalog-source` only.
  - With env var only.
  - With both (flag wins).
  - With neither: `list`/`add`/`outdated`/`why`/`catalog audit` fail-fast with canonical error; `install`/`doctor` fall back to lockfile `[catalog].source` if present and consistent.
- `kanon list` default vs `--detail` vs `--tree` vs `--all-versions` vs `--format json`.
- `kanon list <substring>` filter + `kanon list --regex <pattern>`; matches against name/display-name/description/keywords; `--match-fields` narrowing; zero-match exits 0 with stderr note.
- `kanon list` streams output (memory ceiling test against 1000-entry fixture).
- `kanon list` reads ONLY `*-marketplace.xml` (a fixture with a stale legacy `catalog/<name>/` directory is ignored).
- `kanon list` empty manifest repo → exit 0 with stderr note.
- `kanon list --tree` against 1000-entry catalog → hard error without filter; succeeds with filter or `--no-filter-required`.
- `kanon list --tree --all-versions` → hard error (mutually exclusive).
- `kanon list --match-fields name` (no positional, no `--regex`) → hard error.
- `kanon list --all-versions` worked output snapshot.
- `kanon add` no spec / `==` / `~=` / `>=`/`<` range / branch name / SHA / bare prerelease (`1.0.0a1`) / monorepo (`sub/==1.0`).
- `kanon add` against manifest repo with zero PEP 440 tags + no `@<spec>` → hard error suggesting branch pin.
- `kanon add` against manifest repo with only `v`-prefixed tags + `kanon add foo@1.0.0` → hard error (no `refs/tags/1.0.0` exists; non-PEP-440 `v1.0.0` skipped from resolution).
- `kanon add` multiple entries in one invocation.
- `kanon add` create-new vs append-existing.
- `kanon add` collision (existing block) without/with `--force`; full error message snapshot.
- `kanon add` cross-entry source-name collision in same invocation.
- `kanon add --dry-run`: prints diff, file unchanged.
- `kanon add` with unquoted PEP 440 range (simulated): friendly error.
- `kanon add` against a manifest repo where the requested entry's `<catalog-metadata>` is missing required fields: hard error pointing at `kanon catalog audit`.
- `kanon remove` source name; entry name; mixed case; unknown source name without/with `--force`; non-contiguous-triples `.kanon` fixture.
- `kanon remove --dry-run`.
- `kanon remove` non-existent `.kanon` (hard error).
- `kanon remove` line-ending preservation (LF, CRLF, mixed).
- `kanon outdated` no upgrades available (exit 0) vs upgrades available (exit 0 default, exit 1 with `--fail-on-upgrade`).
- `kanon outdated` branch-pinned source: drift column populated.
- `kanon outdated` against manifest repo with non-PEP-440 tags only → loud error.
- `kanon why` source name / entry name / XML path / project URL / ambiguous (hard error) / not-found (closest-match suggestion).
- `kanon why --format json`.
- `kanon doctor`: every consistency-check error path; effective-catalog-source print (with/without lockfile fallback); recent-completion-errors print; `--strict-drift`; `--prune-cache`; `--refresh-completion-cache`.
- `kanon doctor` with `.kanon` present + `.kanon.lock` absent → info notice; runs checks 6-10; skips 2-5 and 11.
- `kanon doctor` with no `.kanon` → hard error.
- `kanon doctor` with no catalog source configured (and no lockfile fallback) → reports "no catalog source configured" line.
- `kanon doctor` remote-reachability check: succeeds and fails gracefully (non-blocking); skipped when no lockfile.
- `kanon catalog audit`: every standards-audit violation (soft-spots 1-5); `--check` single + multi (comma-separated) + invalid + `all,metadata` mix; `--strict` (warnings → errors); against local dir and against remote `<url>@<ref>`; legacy `catalog/<name>/` directory detection (warning); non-PEP-440 tag warnings.
- `kanon install` first run (writes lock + info-line); second run (reads lock + info-line); `--refresh-lock`; `--refresh-lock-source <name>` (accepts source AND entry name); `--strict-lock`; `--strict-drift`; orphan pruning.
- `kanon install` with no CLI/env catalog source AND consistent lockfile → uses lockfile's `[catalog].source`; with `--refresh-lock` → hard error (refresh requires CLI/env).
- `kanon install`: lockfile catalog-source mismatch (operator set CLI/env to different source) → hard error.
- `kanon install`: `<include>` cycle → hard error printing the cycle.
- `kanon install`: `<include>` diamond → success with dedup.
- `kanon install`: `<remote>` non-HTTPS URL → hard error unless `KANON_ALLOW_INSECURE_REMOTES=1`.
- `kanon install`: git auth failure → propagates raw stderr with the `docs/git-auth-setup.md` reference prefix; no retry.
- `kanon install`: legacy `REPO_URL` / `REPO_REV` env vars set → DeprecationWarning + stderr message (existing).
- `kanon install`: `.gitignore` auto-updated with `.packages/` and `.kanon-data/` (existing).
- `kanon install`: workspace layout `.kanon-data/sources/<name>/` + `.packages/` + `.kanon-data/.kanon-install.lock` (existing).
- Branch drift detection: same install twice with a moved branch tip in between.
- Default-lockfile path derivation: `kanon install --kanon-file ./alt.kanon` (no `--lock-file`) reads/writes `./alt.kanon.lock`.
- Effective catalog-source reporting under various combinations (CLI, env, lockfile, none).
- `kanon clean` default (existing behavior); `kanon clean --orphans` (NEW): prunes per-project clones absent from `.kanon` / `.kanon.lock`.
- `kanon bootstrap` (deprecation shim): full 1:1 translation matrix; WARN to stderr with exact replacement command; exit 3; no side effects on the filesystem; no manifest-repo network calls; `--output-dir` no-equivalent message; `--help` shows DEPRECATED prefix and exits 0.
- Canonical missing-catalog-source error message: snapshot-tested verbatim.

**Integration tests** (`tests/integration/`):

- End-to-end on synthetic manifest-repo fixtures committed under `tests/fixtures/catalogs/`:
  - `single-package/` -- one entry.
  - `multi-package/` -- many entries with shared transitive `<include>`s.
  - `conflict/` -- two top-level entries that pull the same `<project>` at incompatible specs.
  - `conflict-url-normalization/` -- ssh-vs-https for the same repo; must be detected as conflict.
  - `branch-pinned/` -- entry with `REVISION=main`; lockfile captures SHA; second install detects drift.
  - `cycle-include/` -- A includes B includes A; hard error.
  - `diamond-include/` -- A includes B and C, both include D; D appears once in resolution.
  - `broken-soft-spot-1/` through `-5/` -- one fixture per soft-spot violation.
  - `legacy-catalog-dir/` -- a manifest repo that still has a `catalog/<name>/` directory; `kanon list` ignores it; `kanon catalog audit` warns.
  - `empty-catalog/` -- zero `*-marketplace.xml` files; `kanon list` exits 0 with stderr note.
  - `zero-pep440-tags/` -- manifest repo with only non-PEP-440 tags (`v1.0.0`, `release-2024`); `kanon add foo` (no @spec) → hard error; `kanon outdated` against it → loud error.
  - `large-catalog-1000/` -- streaming + memory ceiling test.
  - `monorepo-tags/` -- tags like `subpackage/1.0.0`, `lib/2.1.3`; `kanon add foo@subpackage/==1.0` resolves correctly.
  - `weird-branch-names/` -- branches with `/`, `.`, `+`, mixed case; branch names with `@`/`{`/`}` are out-of-scope (kanon regex rejects).
  - `forty-char-hex-branch/` -- branch literally named with 40 hex chars; resolver chooses SHA (rule 4 wins); explicit `refs/heads/<hex>` overrides.
  - `bare-pep440-prerelease/` -- `kanon add foo@1.0.0a1` → resolves to `refs/tags/1.0.0a1` if present (widened bare detection).
- Real `git ls-remote` exercised against a fixture git server. Mocks are only used at the unit level; integration always uses a real server.
- Concurrency: spawn two `kanon install` subprocesses against the same workspace; flock serializes (one waits, both succeed in sequence). Two installs against different `--kanon-file` paths in the same dir both succeed (with default-lockfile derivation each gets a distinct lockfile path; but they still share the `.kanon-data/.kanon-install.lock` so serialize per workspace).
- Mid-write SIGTERM: install with signal injected during lockfile write; assert atomic state invariant (lockfile is either pre or post, never partial). Per-project clone interruption recoverable via `kanon clean --orphans`. Process-crash mid-flock-hold releases lock automatically (kernel-managed); next invocation succeeds.
- Clock skew: completion cache `fetched_at = now + 1 hour` → treat as fresh, no crash; `fetched_at = -1` → treat as stale, refresh.
- Dangling-SHA: rewrite fixture git history, lockfile SHA becomes dangling; `kanon install` → hard error suggesting `--refresh-lock`.
- Empty SSH agent / missing key: simulate missing credentials against a fixture SSH `<remote>` URL; assert kanon's error message includes the `docs/git-auth-setup.md` prefix; auth-error pattern matches, no retry.
- Retry exhaustion: simulate transient network failure on `git ls-remote`; assert 3 attempts (default `KANON_GIT_RETRY_COUNT`) with `KANON_GIT_RETRY_DELAY` between; final failure surfaces clean error.
- **Provider-agnosticism enforcement.** A CI test greps the entire source tree for provider-specific CLI invocations (`gh`, `glab`, `bb`, `tea`, `aws codecommit`, `az repos`) and provider-specific REST/GraphQL hostnames (`api.github.com`, `gitlab.com/api`, `bitbucket.org/!api`, `dev.azure.com/_apis`). Any match fails the build. Allowlist: docs files (where examples may reference providers by name for illustration in auth-setup docs) and test fixtures explicitly labeled as multi-provider parity tests.
- **Multi-transport fixture matrix.** End-to-end tests run against at least three transports: HTTPS to a local fixture git server, SSH to the same fixture server, and local `file://` (under `KANON_ALLOW_INSECURE_REMOTES=1`). Asserts identical behavior across all three.

**Error fixtures** (every error path has a test):

- Missing catalog source (with/without lockfile fallback path).
- Network failure during `git ls-remote` (after retry exhaustion).
- `git ls-remote` timeout (configurable via `KANON_RESOLVE_TIMEOUT`).
- Invalid PEP 440 spec.
- Branch that does not exist.
- Tag that does not exist.
- Catalog entry name that does not exist.
- Lockfile present but corrupt.
- Lockfile schema version newer than CLI supports.
- Lockfile SHA no longer exists upstream.
- Lockfile out-of-sync with `.kanon` (`kanon_hash` mismatch).
- Lockfile catalog source mismatch with CLI/env source.
- `kanon add` collision without `--force`.
- `kanon add` zero PEP 440 tags + no `@<spec>`.
- `kanon remove` unknown source without `--force`.
- `kanon catalog audit` against each of the five soft-spot fixtures.
- Non-HTTPS `<remote>` URL (denied unless explicit opt-in).
- Manifest repo with duplicate entry names (soft-spot 3).
- Manifest repo with `<catalog-metadata>` missing required field (soft-spot 1).
- Manifest repo with multiple `<catalog-metadata>` blocks per XML (soft-spot 1).
- Manifest repo with only non-PEP-440 tags (soft-spot 5; resolution error).
- Tab-in-path in `.kanon` triple: `kanon install` → hard error during `kanon_hash` serialization.
- Git auth failure mid-clone: assert prefix, no retry, exit code 1.
- `kanon list --tree` + `--all-versions` combination → hard error.
- `kanon list --match-fields` without filter → hard error.
- `kanon catalog audit --check all,metadata` → hard error.

---

## 11. Shell completions (100% coverage)

Every `kanon` command, every argument, and every flag is tab-completable in bash and zsh. The completion infrastructure is built on the proven `shtab` pattern (modeled on an existing internal CLI's `shtab` integration). Net new in kanon's case: catalog-entry names and git tag versions that require network calls; these are completed via a TTL-cached local mirror so a warm tab-press has no network round-trip, with cold tab-presses bounded by `KANON_COMPLETION_TIMEOUT`.

### 11.1 `kanon completion <shell>` subcommand

New top-level subcommand:

```
kanon completion bash | zsh
```

Emits a completion script to stdout. Two install paths the operator picks from:

1. **Auto-updating** (recommended): `eval "$(kanon completion bash)"` in `~/.bashrc`. Always in sync with the installed CLI; dynamic completers call back into `kanon` at tab-press time.
2. **Static file**: `kanon completion bash > ~/.local/share/bash-completion/completions/kanon`. Faster shell startup; must be regenerated after `pipx upgrade kanon-cli`. `kanon doctor` warns when the on-disk static script drifts from a fresh `kanon completion` invocation (Section 4.6 item 9).

Doc this in `docs/shell-completion.md`.

Implementation: `shtab.complete(parser, shell, preamble=PREAMBLE)` where `PREAMBLE` defines kanon-specific shell functions for dynamic lookups (described below).

### 11.2 Completion coverage matrix (every command, every arg, every flag)

| Command | Positional / flag | Completion source | Static or dynamic |
|---|---|---|---|
| _all_ | `--help`, `--version` | -- | static |
| _all_ | `--quiet`, `--verbose`, `--no-color` | -- | static (no value) |
| _global_ | `--catalog-source <url>@<ref>` | cached catalog clones under `${KANON_CACHE_DIR}/catalogs/*` | dynamic local (cache) |
| `kanon bootstrap` (deprecated) | `<package>` | catalog entries via `__complete_catalog_entries` (separate process; not subject to the shim's no-boundary-call rule, see Section 4.9) | dynamic (catalog) |
| `kanon bootstrap` (deprecated) | `--output-dir <path>` | filesystem | `shtab.DIRECTORY` |
| `kanon install` | `[<kanon-file>]` (legacy positional) | filesystem | `shtab.FILE` |
| `kanon install` | `--kanon-file`, `--lock-file` | filesystem | `shtab.FILE` |
| `kanon install` | `--refresh-lock`, `--strict-lock`, `--strict-drift` | -- | static (no value) |
| `kanon install` | `--refresh-lock-source <name>` | source names parsed from current `.kanon` | dynamic local (file parse) |
| `kanon list` | `[<substring>]` | -- | free text (no completion) |
| `kanon list` | `--format <name>` | `{names, json}` | static enum |
| `kanon list` | `--tree`, `--detail`, `--all-versions`, `--no-limit`, `--no-filter-required` | -- | static (no value) |
| `kanon list` | `--limit N`, `--max-depth N` | -- | free integer |
| `kanon list` | `--regex <pattern>` | -- | free text |
| `kanon list` | `--match-fields <csv>` | `{name, display-name, description, keywords}` (comma-separable) | static enum (csv) |
| `kanon list` | `--since-version <spec>` | git tags of manifest repo (PEP 440-valid only) | dynamic (catalog ls-remote) |
| `kanon add` | `<name>[@<spec>]` | catalog entries; spec from PEP 440-valid git tags + branches of resolved repo when `@` is present | dynamic (catalog + per-name ls-remote) |
| `kanon add` | `--kanon-file` | filesystem | `shtab.FILE` |
| `kanon add` | `--force`, `--dry-run` | -- | static (no value) |
| `kanon remove` | `<name>` | source names parsed from current `.kanon` (normalized form only — see Section 11.3) | dynamic local (file parse) |
| `kanon remove` | `--kanon-file`, `--force`, `--dry-run` | filesystem / static | same as above |
| `kanon outdated` | `--format <name>` | `{table, json}` | static enum |
| `kanon outdated` | `--kanon-file`, `--lock-file` | filesystem | `shtab.FILE` |
| `kanon outdated` | `--fail-on-upgrade` | -- | static (no value) |
| `kanon why` | `<name-or-url>` | source names from `.kanon.lock` + transitive include paths + project URLs | dynamic local (lockfile parse) |
| `kanon why` | `--format <name>` | `{text, json}` | static enum |
| `kanon why` | `--kanon-file`, `--lock-file` | filesystem | `shtab.FILE` |
| `kanon doctor` | `--kanon-file`, `--lock-file` | filesystem | `shtab.FILE` |
| `kanon doctor` | `--refresh-completion-cache`, `--strict-drift`, `--prune-cache` | -- | static (no value) |
| `kanon catalog` | `<subcommand>` | `{audit}` | static enum |
| `kanon catalog audit` | `[<dir-or-url>]` | filesystem OR cached catalog URLs | dynamic local (cache + dir) |
| `kanon catalog audit` | `--check <csv>` | `{all, metadata, source-name-derivation, entry-name-uniqueness, remote-url, tag-format}` (comma-separable; `all` not mixable) | static enum (csv) |
| `kanon catalog audit` | `--format <name>` | `{text, json}` | static enum |
| `kanon catalog audit` | `--strict` | -- | static (no value) |
| `kanon completion` | `<shell>` | `{bash, zsh}` | static enum |
| `kanon validate` | `<sub>` | `{xml, marketplace, metadata}` | static enum |
| `kanon validate xml`/`marketplace`/`metadata` | `--repo-root <path>` | filesystem | `shtab.DIRECTORY` |
| `kanon clean` | `[<kanon-file>]` (legacy positional) | filesystem | `shtab.FILE` |
| `kanon clean` | `--orphans`, `--kanon-file` | static / filesystem | static (no value) / `shtab.FILE` |
| `kanon repo <subcommand>` | (existing repo wrapper) | static subcommand list; existing repo args | static (existing surface; this spec wires it through the completion script) |

`kanon bootstrap` retains its rich completion behavior for the deprecation window so existing scripted invocations keep tab-completing while operators migrate.

If a command, arg, or flag is added later, the completion script picks it up automatically for static cases (argparse introspection); dynamic completers must be added in the same PR per "100% coverage" rule.

### 11.3 Dynamic completers

Each dynamic completer is a Python function exposed by a dedicated `kanon __complete_<name>` hidden subcommand that the shell function calls. The shell function (in the preamble) shells out to `kanon __complete_<name> <current-token>`, captures stdout, hands it to the shell as completion candidates.

| Completer | Reads | Writes (stdout) |
|---|---|---|
| `__complete_catalog_entries` | Resolved manifest repo (clone + cache); each `*-marketplace.xml`'s `<catalog-metadata><name>` | one entry name per line |
| `__complete_source_names_in_kanon` | `${KANON_KANON_FILE:-./.kanon}` | one normalized source name per line (parsed from `KANON_SOURCE_<name>_URL` keys). Note: normalization is one-way and lossy — the original entry name cannot be reconstructed from the source name and is NOT emitted. Operators who remember the entry name can still type it at the command line; `derive_source_name()` normalizes it at command time. Tab-completion is suggest-only. |
| `__complete_names_in_lockfile` | `${KANON_LOCK_FILE}` (resolved per Section 4.7) | one top-level source name + every transitive `<include>` path + every `<project>` URL, one per line |
| `__complete_catalog_versions` | `git ls-remote --tags` + `--heads` of the manifest repo (PEP 440-valid tags only) | one tag + one branch per line (deduped) |
| `__complete_project_versions <repo-url>` | `git ls-remote --tags` + `--heads` `<repo-url>` (PEP 440-valid tags only) | one tag + one branch per line (deduped) |
| `__complete_cached_catalogs` | `${KANON_CACHE_DIR}/catalogs/*` index | one `<url>@<ref>` per line for every manifest repo the operator has touched |

**Output sanitization.** Every completer filters its candidates through a sanitizer that rejects entries containing newlines, NULs, or shell metacharacters that would break the completion protocol. Rejected entries are dropped from stdout AND logged to `completion-errors.log`.

Each `__complete_*` subcommand is failure-quiet on stdout (returns empty list) but failure-loud on stderr (writes a structured error to `${KANON_CACHE_DIR}/completion-errors.log`). This satisfies CLAUDE.md's no-silent-failure rule at the architecture level while keeping shell UX non-blocking. `kanon doctor` surfaces the most recent N completion errors so operators see what's wrong.

### 11.4 Caching for network-backed completions

Completions that hit the network use a TTL cache so a warm tab-press has no network round-trip:

| Env var | Purpose | Default |
|---|---|---|
| `KANON_CACHE_DIR` | Root of the local cache | `${XDG_CACHE_HOME:-~/.cache}/kanon` |
| `KANON_COMPLETION_CACHE_TTL` | Seconds before a cached lookup is considered stale | `300` (5 min) |
| `KANON_COMPLETION_TIMEOUT` | Hard cap per network call inside a completer | `2` seconds |
| `KANON_COMPLETION_REFRESH_BG` | Kick off async refresh when cache is stale but usable | `1` (on) |
| `KANON_COMPLETION_ENABLED` | Disable all dynamic completion (return empty candidates immediately) | `1` (on) |
| `KANON_ACCESSED_AT_COALESCE_SEC` | Minimum seconds between `accessed_at.txt` updates on the same cache entry (avoid write amplification on rapid tab-completions) | `60` |

**Cache layout (directory mode `0700`, file mode `0600`):**

```
${KANON_CACHE_DIR}/
  catalogs/
    <sha256-of-catalog-url@ref>/
      index.txt                    # one catalog entry name per line
      tags.txt                     # one PEP 440-valid tag + one branch per line for the manifest repo
      fetched_at.txt               # epoch seconds
      accessed_at.txt              # epoch seconds (updated on read, coalesced)
  projects/
    <sha256-of-project-repo-url>/
      tags.txt
      fetched_at.txt
      accessed_at.txt
  completion-errors.log
```

**Cache lifecycle:**

- On every completer call: read the relevant cache file. If `fetched_at` is within TTL, return contents immediately. Update `accessed_at` only if the current epoch is more than `KANON_ACCESSED_AT_COALESCE_SEC` past the prior `accessed_at` value.
- If stale, return current contents AND fork a background refresh (unless `KANON_COMPLETION_REFRESH_BG=0`).
- If cache file does not exist (first call): perform the network call inline, bounded by `KANON_COMPLETION_TIMEOUT`. On timeout: return empty, log to `completion-errors.log`.
- Cache invalidated by `kanon doctor --refresh-completion-cache`.
- Cache pruned by `kanon doctor --prune-cache`: removes entries whose `accessed_at` is older than `KANON_CACHE_PRUNE_AGE_DAYS` (default 30). Pruning is opt-in.
- `kanon install` updates the cache automatically for the catalog + every transitive repo it touches (warm cache as a side effect of normal use).
- Clock-skew handling: `fetched_at` in the future → treat as fresh; `fetched_at` negative or non-numeric → treat as missing (refresh inline).

### 11.5 Mid-token splitting (the `<name>@<spec>` case)

`kanon add foo@<TAB>` puts the cursor mid-token. The shell function detects the `@` separator (last `@` per Section 4.0 splitter rule) and, when present, calls `__complete_project_versions` against the repo URL backing the catalog entry named to the left of the `@`. Returns PEP 440-valid tags + branches (deduped). Without `@`, calls `__complete_catalog_entries` against the catalog. Reference implementation lives in a small shell helper inside the preamble; tested with golden snapshots per shell.

### 11.6 Configuration recap (env vars added by this section)

| Env var | Default constant | Override |
|---|---|---|
| `KANON_CACHE_DIR` | `${XDG_CACHE_HOME:-~/.cache}/kanon` | env only |
| `KANON_COMPLETION_CACHE_TTL` | `300` | env only |
| `KANON_COMPLETION_TIMEOUT` | `2` | env only |
| `KANON_COMPLETION_REFRESH_BG` | `1` | env only |
| `KANON_COMPLETION_ENABLED` | `1` | env only |
| `KANON_ACCESSED_AT_COALESCE_SEC` | `60` | env only |
| `KANON_COMPLETION_LOG` | `${KANON_CACHE_DIR}/completion-errors.log` | env only |

No CLI flags for completion-specific *tuning*; operator-facing maintenance is via `kanon doctor --refresh-completion-cache` and `kanon doctor --prune-cache`.

### 11.7 Documentation requirement

- **`docs/shell-completion.md`** (NEW): full operator-facing guide. Install (bash + zsh), update lifecycle, every dynamic completer named, every cache env var documented, troubleshooting (stale cache, log file location, disabling completion). bash floor: 4.0; macOS stock bash 3.2 not supported (documented).
- **`README.md`**: one-paragraph "Tab completion" section under Quick Start, linking to the full doc.
- **`docs/configuration.md`**: add the new env vars to the existing table.

### 11.8 Testing requirement (100% coverage applies)

**Unit tests** (`tests/unit/`):
- Every `__complete_*` Python function: happy path, empty source, malformed source, timeout simulation, cache-hit, cache-miss, cache-stale, network-error, sanitization (entry with embedded newline / NUL / metachar dropped).
- `__complete_catalog_versions` and `__complete_project_versions` filter out non-PEP-440 tag names.
- Cache TTL math: stale boundary at exactly TTL seconds, ahead/behind clock skew.
- Cache file integrity: corrupt `fetched_at`, missing `index.txt`, missing `fetched_at` with present `index.txt` (corruption — treat as missing).
- Cache file permissions: dir `0700`, files `0600` after every write.
- `accessed_at` coalescing: rapid back-to-back reads within `KANON_ACCESSED_AT_COALESCE_SEC` do not rewrite the file.
- Mid-token splitter: input variations (`foo`, `foo@`, `foo@1`, `foo@1.0.0`, `foo@bar@baz` → split on last `@`).
- Disabled completion (`KANON_COMPLETION_ENABLED=0`): every completer returns empty immediately, no cache touch.
- Pruning: pre-seed entries with old `accessed_at`; `kanon doctor --prune-cache` removes only those beyond threshold.

**Functional tests** (`tests/functional/`):
- `kanon completion bash` and `kanon completion zsh` produce non-empty scripts that parse cleanly under their respective shells (`bash -n`, `zsh -n`).
- Generated scripts snapshot-tested against golden files (`tests/fixtures/completion/expected-bash.sh`, `expected-zsh.sh`); diffs fail CI. Workflow for refresh: `make update-completion-snapshots`.
- Every CLI flag listed in Section 11.2 appears in the generated script's completion table for its parent command.

**Integration tests** (`tests/integration/`):
- A test container with bash + zsh installed sources the generated script and exercises tab-completion via `compgen -F` (bash) and `_main_complete` (zsh) shell helpers. Asserts the candidate list returned for every command/arg/flag combination in Section 11.2 matches the expected set.
- Synthetic manifest-repo fixtures populate the cache; tests assert dynamic completers return the seeded entry names from `<catalog-metadata>`.
- Cache-stale path: pre-seed an old `fetched_at`, run completer, assert it returned stale data immediately AND that a background refresh updated the cache.
- Error path: simulate `git ls-remote` failure (point the cache mirror at a non-existent URL), assert empty candidate list AND that `completion-errors.log` received a structured entry.
- First-call-cold path: empty cache + `KANON_COMPLETION_REFRESH_BG=0`; assert latency is bounded by `KANON_COMPLETION_TIMEOUT`.

**Coverage gates:**
- 100% line coverage on every `__complete_*` function and every cache utility.
- 100% pass on every row of Section 11.2 (each row is a test case).
- Snapshot diff covers every static surface change in the completion script.

### 11.9 Acceptance for "100% coverage of completions"

The feature is "done" only when every one of the following holds:

1. Every row in Section 11.2 has a passing integration test asserting the expected candidate list.
2. `kanon completion bash` and `kanon completion zsh` both produce shells that pass `shell -n` syntax check.
3. Snapshot tests for the generated scripts pass.
4. `kanon doctor` includes the "completion script staleness" check (Section 4.6 item 9) AND the `--prune-cache` flag.
5. Doc `docs/shell-completion.md` exists with the full operator guide.
6. CI runs the integration tests on bash + zsh on every PR.

---

## 12. Out of scope (this spec)

- A central package registry. `kanon list` works against whatever manifest repo the operator points at.
- A default manifest repo (the existing bundled `src/kanon_cli/catalog/` is removed by this spec; nothing replaces it).
- An rc-file / named-catalog system for catalog-source defaults.
- Tag-name rewriting / prefix stripping. kanon resolves literal tag names only; non-PEP-440 tag names are unaddressable via the resolver (use full `refs/tags/<name>` if absolutely needed).
- Auto-publishing of manifest repos.
- Cross-catalog dependency resolution beyond what multi-source `.kanon` already supports today.
- Mutating remote manifest repos.
- Any organization-specific catalog URL, name, or example in kanon's open-source surface.
- Search across multiple manifest repos in one invocation. `kanon list --catalog-source A; kanon list --catalog-source B` is the pattern.
- Hard removal of `kanon bootstrap` (the deprecation shim is what ships in this spec).
- Replacing the embedded repo fork with a different implementation (the fork lives in `src/kanon_cli/repo/`; structural rework is future work).
- Credential management. kanon delegates 100% to the operator's git client.
- Interactive prompts of any kind.
- A `--force-color` flag (would re-enable color when stdout is not a TTY). Tracked for future work if demanded.

---

## 13. Resolved decisions (interview record)

For traceability. All items below were confirmed by the operator during spec drafting:

1. Lockfile format: **TOML**.
2. Backlog folder name: **`kanon-deps-work/`** (mirrors `devbench-work`).
3. `kanon install` when no lockfile exists: **resolve fresh + auto-write lock**.
4. Branch-pinned dep + lockfile: **use locked SHA (immutable); drift requires `--refresh-lock`** (or `--refresh-lock-source`).
5. `kanon list --all-versions` default: **cap at `KANON_LIST_LIMIT=50`; `--limit N` / `--no-limit` override**.
6. `kanon list` output formats: **`names` (default) + `json`**.
7. `kanon list` filter: **positional substring + `--regex` flag; matches across name/display-name/description/keywords by default; `--match-fields` narrows; requires a filter to use `--match-fields`**.
8. `kanon list --tree` default depth: **all three layers**, `--max-depth N` override; **filter required for catalogs > 20 entries** (`--no-filter-required` to bypass); **mutually exclusive with `--all-versions`**.
9. Lockfile refresh flag: **`--refresh-lock`** (full) + **`--refresh-lock-source <name>`** (one chain; accepts source name or entry name). Refresh paths require CLI/env catalog source.
10. Additional commands included in THIS backlog: **`kanon remove`, `kanon outdated`, `kanon why`, `kanon doctor`, `kanon catalog audit`, `kanon validate metadata` (new sub-subcommand)**.
11. Catalog source-name derivation (soft-spot rule 2): **deterministic — always lowercase + always replace `-` with `_`**. `kanon add` writes the normalized form. `kanon validate metadata` warns on non-canonical entry names. `kanon remove` / `kanon why` / `--refresh-lock-source` accept both entry name and source name. Tab-completion suggests normalized source names only.
12. Caylent-private-kanon scope: **in scope across 9a (engineering + CI), 9b (operational per-entry migration, Phases 1 + 2 — README content preserved verbatim into `<description>`), and 9c (sibling-repo audit only)**.
13. **Shell completions (Section 11): in scope.** 100% coverage of every command / arg / flag in bash and zsh via `shtab`. Modeled on an existing internal CLI's proven shtab pattern. Dynamic completions for catalog entries + git tag versions use a TTL-cached local mirror; first call inline-bounded by `KANON_COMPLETION_TIMEOUT=2s`; subsequent calls hit cache (TTL `KANON_COMPLETION_CACHE_TTL=300s`); stale-but-usable triggers a background refresh. Completion errors are non-blocking at the shell but structured-logged; `kanon doctor` surfaces them. `--prune-cache` flag removes stale entries beyond `KANON_CACHE_PRUNE_AGE_DAYS`. `accessed_at` writes are coalesced.
14. **PEP 440 required** for all version specifiers AND for git tag names (last path component of any tag must be valid PEP 440). Non-PEP-440 tags are unaddressable; `kanon catalog audit --check tag-format` warns about them; resolution hard-errors when zero PEP 440 tags are available under a prefix. **No tag-prefix-stripping or rewriting.** Bare PEP 440 detection widened from today's `^\d+(?:\.\d+){1,2}$` to accept any `packaging.version.Version`.
15. **Monorepo tag pattern supported** (existing behavior in `version.py`): `<path>/<PEP440-version>` (e.g., `subpackage/1.0.0`, `dev/python/lib/2.1.3`). Constraint form: `<path>/<PEP440-constraint>` (e.g., `subpackage/==1.0`).
16. **`kanon doctor` and `kanon catalog audit` are separate commands**. Workspace vs catalog-author responsibilities don't share enough to merge. `kanon validate metadata` is the in-repo author-side check (sub-subcommand under existing `kanon validate`); `kanon catalog audit` is the consumer-side check (top-level command).
17. **Behavior-change watch list (Section 0):** four items. 0.1 and 0.2 are opt-in (`--orphans`, `--fail-on-upgrade`). 0.3 (bootstrap deprecation) and 0.4 (PEP 440 tag-name enforcement) are fixed-behavior. Operator decides separately whether 0.1/0.2 should become defaults.
18. **`kanon bootstrap` is deprecated to a shim** that WARNs with the exact replacement command and exits with status 3 WITHOUT performing any work. The shim does not delegate, does not read manifest-repo content, does not touch the filesystem. The bundled `src/kanon_cli/catalog/` directory is deleted alongside the shim. Reading the legacy `catalog/<name>/` directory in manifest repos is removed entirely. Operators copy-paste the suggested `kanon add <name>` / `kanon list` invocation. `--help` retains discoverability (exit 0, DEPRECATED-prefixed). Untranslatable flags (e.g., `--output-dir`) named in WARN. Tab-completion for `kanon bootstrap <name>` reuses `__complete_catalog_entries`.
19. **No default manifest repo** after bootstrap deprecation epic completes. Missing both `--catalog-source` and `KANON_CATALOG_SOURCE` is a hard error for `list`/`add`/`outdated`/`why`/`catalog audit`; for `install` and `doctor`, the lockfile's `[catalog].source` is used as a fallback when present and consistent (refresh paths still require CLI/env). No `~/.kanonrc`, no named-catalog system.
20. **Provider-agnostic.** No GitHub-specific (or any provider-specific) CLI or API in code, tests, CI. Enforced by a tree-wide grep test in CI.
21. **Git-provider terminology cleanup (Section 1.1).** Manifest repo = catalog. Legacy `catalog/<name>/` directory deleted from manifest repos AND `src/kanon_cli/catalog/` deleted from the kanon wheel.
22. **Embedded repo fork** (existing). kanon's install engine uses its own copy of the repo logic (`src/kanon_cli/repo/`). No external `repo` binary required.
23. **No credential handling.** kanon delegates 100% to the operator's git client. Auth-error stderr patterns trigger retry-skip (existing); not credential handling.
24. **Network retries preserved** (existing). `KANON_GIT_RETRY_COUNT=3`, `KANON_GIT_RETRY_DELAY=1s` by default. Auth-error patterns skip retries.
25. **No interactive prompts.** Color: auto-detect TTY + `NO_COLOR` + `--no-color`, never prompt.
26. **`CLAUDE_MARKETPLACES_DIR` retained** as the Claude-specific install-target convention. Future AI-tool integrations (e.g., Copilot) will get their own analogous variables. The `CLAUDE_*` prefix names the consumer. `KANON_MARKETPLACE_INSTALL=<true|false>` (existing) is the gate.
27. **`<catalog-metadata><version>` is author-claimed informational.** Not used for resolution; not cross-checked against any git ref. Displayed in `--detail`, indexed by `--all-versions`.
28. **Default `--lock-file` derived from `--kanon-file`** when `--kanon-file` is non-default (Section 4.7).
29. **Exit code 3** reserved for "deprecated invocation" to avoid collision with existing exit code 2 ("no subcommand given" via argparse).

---

## 14. CLI `--help` reference (snapshot-tested)

Every command's `--help` output is specified verbatim and snapshot-tested. The full set lives in `tests/fixtures/help/`. Below: the top-level entry point plus representative shapes. The remaining commands follow the same template.

**Global behavior:**
- `kanon` (no args) prints `kanon --help` to stdout and exits 2 (today's behavior, preserved — `cli.py:117`).
- `kanon --version` prints `kanon <semver>` on one line and exits 0 (existing; argparse `action="version"`).
- All output respects the color policy in Section 7.

### `kanon --help`

```
kanon — declarative dependency manager for git-hosted assets

Usage: kanon <command> [options]

Discovery & management:
  list             Discover catalog entries
  add              Add catalog entries to .kanon
  remove           Remove sources from .kanon
  outdated         Report installable upgrades
  why              Explain why a transitive dep is in the tree

Lifecycle:
  install          Install/sync everything in .kanon
  clean            Remove installed artifacts (use --orphans to also prune unreferenced)
  validate         Validate XML manifests (subcommands: xml, marketplace, metadata)
  doctor           Diagnose .kanon / .kanon.lock health

Manifest repo (catalog author):
  catalog audit    Audit a manifest repo against the standards contract
  repo             Catalog-author repo subcommands (see kanon repo --help)

Shell integration:
  completion       Generate shell completion script

Deprecated:
  bootstrap        DEPRECATED — use 'kanon add' / 'kanon list'. See docs/migration-bootstrap-to-add.md.

Global options (always available):
  --version                      Print kanon version and exit.
  --help                         Show this and exit.
  --quiet / --verbose            Logging verbosity (mutually exclusive).
  --no-color                     Disable ANSI color (also respects NO_COLOR env var).

Catalog source (required by commands that resolve a manifest repo; see each subcommand's --help):
  --catalog-source <url>@<ref>   Override KANON_CATALOG_SOURCE. No default; one of
                                 --catalog-source or KANON_CATALOG_SOURCE is required
                                 for list/add/outdated/why/catalog audit. For install
                                 and doctor, .kanon.lock [catalog].source is used as
                                 fallback when present and consistent.
```

### `kanon list --help`, `kanon add --help`, `kanon install --help`, `kanon catalog --help`, `kanon catalog audit --help`, `kanon bootstrap --help`

(Verbatim help text for each is snapshot-tested in `tests/fixtures/help/`. The earlier draft of this spec contained inline samples; for brevity here, refer to the snapshots and to the per-command docs in Section 8.)

Key requirements for every command's `--help` output:
- Usage line with positional + options structure.
- Arguments / Filtering / Output / History / Behavior / Catalog source sections as relevant.
- Mutually exclusive combinations called out explicitly.
- Exit codes section listing 0 (success) and 1 (runtime/usage error) for all normal commands; `kanon bootstrap` additionally lists 3 (deprecated invocation).
- "See: docs/<...>.md" link at the bottom.

`kanon bootstrap --help` MUST be prepended with `DEPRECATED: 'kanon bootstrap' is replaced by 'kanon add' and 'kanon list'.` and include the verbatim flag-translation table from Section 4.9.

---

## 15. Future work (explicitly deferred)

Tracked here so the operator can file follow-up issues but NOT scoped into this backlog:

- **Hard removal of `kanon bootstrap`.** This spec ships the deprecation shim (Section 4.9). A follow-up release decides when to delete the shim and `commands/bootstrap.py` entirely.
- **Hard error on legacy `catalog/<name>/` directory.** `kanon catalog audit` currently warns; future release promotes to error.
- **Hard error on non-PEP-440 tag names.** `kanon catalog audit --check tag-format` currently warns; future release promotes to error after a deprecation window.
- **Signed manifest repos / transparency log** (Section 3.6).
- **Central catalog registry.**
- **Multi-catalog merge.**
- **`<catalog-format-version>` element.** Version handshake between client and manifest repos.
- **`kanon clean --orphans` as default** (Section 0.1).
- **`kanon outdated` non-zero-on-upgrade as default** (Section 0.2).
- **`--force-color` flag** to re-enable color when stdout is not a TTY.
- **`kanon why --kind {source,xml,project}`** disambiguator.
- **`kanon add --as <name>`** for non-default source names.
- **AI-tool-specific marketplace conventions** beyond `CLAUDE_MARKETPLACES_DIR` (e.g., future `COPILOT_MARKETPLACES_DIR`).
- **Restructure / replace the embedded repo fork** (`src/kanon_cli/repo/`).

---

*When this spec is locked, promote to a devbench backlog under `kanon-deps-work/backlog/` with the layout already used by `devbench-work/`. Suggested epic decomposition (dependencies noted):*

- *E1 — Resolver semantics + URL canonicalization + `@` splitter + widened bare PEP 440 (4.0). Foundation; blocks E2.*
- *E2 — `kanon list` + `kanon add` + `kanon remove` (4.1, 4.2, 4.3). Includes the metadata-driven entry-discovery path. Depends on E1; blocks E3.*
- *E3 — Lockfile + `kanon install` extension (5, 5.1, 5.2, 4.7, 4.7.1). Depends on E2; blocks E4.*
- *E4 — `kanon outdated` + `kanon why` (4.4, 4.5). Depends on E3.*
- *E5 — `kanon doctor` + `kanon catalog audit` + `kanon validate metadata` + standards codification (3.5, 3.6, 4.6, 4.8). Depends on E2; blocks E10.*
- *E6 — `kanon bootstrap` deprecation shim + bundled `src/kanon_cli/catalog/` removal + `docs/migration-bootstrap-to-add.md` + `core/cli_args.py` flag-definition move (4.9, 3). No hard dependency; can land anytime after E1.*
- *E7 — Shell completions (11.*). Depends on E2 + E3.*
- *E8 — Open-source repo doc set + `creating-manifest-repos.md` rewrite + terminology cleanup + new docs (`git-auth-setup`, `architecture`, `exit-codes`, `coming-from-pip-npm-cargo`, `security-model`, `troubleshooting`). Parallel to E1-E7.*
- *E9 — `--help` snapshots (14). Folds into each command's epic OR a single epic at the end.*
- *E10 — Caylent-private-kanon engineering + CI + regression guards (9a). Depends on E5.*
- *E11 — Caylent-private-kanon Phase-1 operational migration: per-entry `<catalog-metadata>` enrichment + tag-format audit, one task per non-conforming entry (9b Phase 1). Depends on E10.*
- *E12 — Caylent-private-kanon Phase-2: delete the legacy `catalog/` directory tree (9b Phase 2). Depends on E11.*
- *E13 — kanon-claude-marketplaces audit + follow-up issue filing (9c). Depends on E5.*

*The autonomous loop implements against the upstream open-source kanon repo and the caylent-private-kanon target in coordinated PRs.*
