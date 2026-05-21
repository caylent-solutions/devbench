# Skill exemplar references

The bundled `spec-to-backlog` and `create-spec` skills are application-agnostic. They author backlogs and specs against the canonical sections embedded in their SKILL.md prompts; no built-in path to any reference workspace is hardcoded.

When the operator's workspace has its own representative BACKLOG.md or spec they want the skills to additionally consult for richer wording and shape, they point the skills at those exemplars via the `skills:` block in `backlog/config/devbench.yaml`.

## Configuration keys

```yaml
skills:
  exemplar_backlog_path: backlog/_exemplars/representative-backlog/BACKLOG.md
  exemplar_spec_path: spec/_exemplars/representative-spec.md
  fan_out_threshold: 10
  max_iterations: 5
```

All keys are optional:

- **`skills.exemplar_backlog_path`** -- workspace-relative or absolute path the `spec-to-backlog` skill will `Read` in Step 1a. Pointed at a representative BACKLOG.md plus the skill will additionally read one leaf task under it. When absent or pointing at a non-existent path, the skill skips the read entirely and relies on the 15-section list embedded in the SKILL.md (the canonical floor).
- **`skills.exemplar_spec_path`** -- workspace-relative or absolute path the `create-spec` skill will `Read` in Step 1a. When absent or pointing at a non-existent path, the skill skips the read and relies on the 16-section structural skeleton embedded in the SKILL.md.
- **`skills.fan_out_threshold`** -- integer (>= 1, default 10). When the Epic decomposition produces strictly more than this many leaf tasks, `spec-to-backlog` fans the per-task authoring out across one general-purpose sub-Agent per Feature instead of writing tasks serially.
- **`skills.max_iterations`** -- integer (>= 1, default 5). Maximum self-critique iterations per skill invocation. When exceeded, the skill emits a `[SKILL_MAX_ITERATIONS_REACHED]` audit comment with the unresolved rubric items instead of silently shipping a sub-quality artefact.

## Resolution order

The skill resolves the exemplar path with the following precedence:

1. **`skills.exemplar_backlog_path`** (or `exemplar_spec_path`) in `backlog/config/devbench.yaml`.
2. **No exemplar.** The skill operates exclusively against the embedded canonical-section list.

There is no implicit default. The skills never read from `/workspaces/...` or any other built-in path.

## Provenance audit comment

Every skill invocation emits a `[QUALITY_REFERENCE]` audit row after a successful run:

- When `skills.exemplar_*_path` was set and the file existed: the resolved absolute path.
- When the key was absent OR the file did not exist: the literal token `<embedded-canonical-sections>`.

Operators can grep audit logs for the literal token to verify the skill operated agnostically without falling back to any hardcoded path.

## Canonical-section sources of truth

Even when no exemplar is configured, every skill invocation produces an artefact that satisfies the canonical structure:

- **`spec-to-backlog`** -- every leaf task `.md` contains the 15 canonical sections enumerated in `plugin-authoring/devbench-authoring/skills/spec-to-backlog/SKILL.md` Step 1b.
- **`create-spec`** -- every spec covers the 16 canonical sections (Sections 0-15) enumerated in `plugin-authoring/devbench-authoring/skills/create-spec/SKILL.md` Step 1b, or each absent section carries an explicit "N/A -- reason" statement.

The embedded section lists are the authoritative quality bar. The optional workspace exemplar is an additional reference for richer wording, never a substitute for the structural skeleton.
