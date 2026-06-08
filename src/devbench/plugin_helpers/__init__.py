"""Plugin-helper utilities invoked from the bundled devbench skills.

The bundled ``spec-to-backlog`` and ``create-spec`` skills are LLM-driven
prompt files; they cannot perform deterministic bulk transforms of the
backlog files they author. ``plugin_helpers`` provides that deterministic
layer: each module exposes pure functions the skill prompt can invoke
between authoring and validation passes to land a first-try-green
backlog (issue #221 A1, A4, A8-A17).

Modules:

- :mod:`devbench.plugin_helpers.backlog_post_processor` -- per-WU-file
  post-processing passes the spec-to-backlog skill runs after Step 5
  authoring and before Step 5d's ``validate-backlog`` invocation.
- :mod:`devbench.plugin_helpers.spec_gates` -- deterministic gates for the
  create-spec adversarial hardening loop.
"""
