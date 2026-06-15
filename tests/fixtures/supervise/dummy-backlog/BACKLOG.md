# Dummy Backlog -- supervise integration fixture

> Glyph note: this file uses ASCII double-hyphen `--` everywhere a dash is needed.
> The em-dash glyph (U+2014) does not appear in this file.

A deliberately TINY, TRIVIAL, NON-AWS throwaway backlog used ONLY by the
`devbench supervise` Phase-6 integration layer (Section 10.0 of
`spec/devbench-supervise-screen-orchestrator/devbench-supervise-screen-orchestrator.md`).

It is NOT a production backlog. It exists so the integration tests (and the
deferred live ACs AC-23/AC-34 when a human runs them against a real `claude`)
have a real, parseable backlog whose work units are pure local docs edits with
NO cloud, NO terraform/terragrunt, NO AWS, NO network -- a live run can never
collide with any other workload on the machine.

## Full Work Unit Index

| ID | Title | Type | Status | Repo | Depends On | File |
|---|---|---|---|---|---|---|
| E1-F1-S1-T1 | Append a greeting line to NOTES.md | Task | In Queue | caylent-solutions/devbench | -- | `backlog/E1-F1-S1-T1.md` |
| E1-F1-S1-T2 | Append a farewell line to NOTES.md | Task | In Queue | caylent-solutions/devbench | E1-F1-S1-T1 | `backlog/E1-F1-S1-T2.md` |
