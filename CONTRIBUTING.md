# Contributing to devbench

Thanks for taking the time. This page covers reporting problems and contributing changes.

**Security vulnerabilities do not belong in issues.** See [SECURITY.md](SECURITY.md).

---

## Reporting a bug or requesting a feature

Use one of the forms at
<https://github.com/caylent-solutions/devbench/issues/new/choose>. They ask which area of devbench
is involved and apply the labels for you, so you do not need to know the label scheme.

[docs/issue-tracker.md](docs/issue-tracker.md) explains the labels and what happens after you file.

A good bug report answers three things:

- what you expected,
- what happened instead,
- the exact commands that produce it.

Version (`devbench --version`) and relevant log output make the difference between a report someone
can act on and one that needs a round trip. Redact anything you would not want in a public
repository: tokens, hostnames, internal paths.

---

## Contributing a change

### Before you start

For anything beyond a typo, **open an issue first** and say you intend to work on it. devbench has
opinionated internals, and a short conversation up front is cheaper than a rewritten pull request.

Look for [`good first issue`](https://github.com/caylent-solutions/devbench/labels/good%20first%20issue)
if you are getting oriented.

### Setup

```bash
git clone https://github.com/caylent-solutions/devbench.git
cd devbench
make install
```

### The bar for a change

Every change must pass:

```bash
make validate
```

which runs ruff (lint and format), bandit, mypy, a duplicate-code check, and the test suite with a
coverage floor. CI runs the same gates, so a green `make validate` locally means a green PR.

Beyond the tooling, changes are expected to:

- **Fail fast.** No fallback paths, no silent degradation. When devbench cannot do the right thing
  it stops with an actionable message rather than doing an approximate thing quietly.
- **Come with real tests.** Tests that would fail if the behaviour regressed, not tests that assert
  the code ran. If you fix a bug, the test should fail without your fix.
- **Update the docs in the same change.** Documentation drift is treated as a defect, not follow-up
  work.
- **Replace, not accumulate.** If you supersede a function, update every caller and delete the old
  one in the same change.
- **Never suppress a check.** No `# noqa`, `# type: ignore`, `# nosec`, or `pragma: no cover`. If a
  tool objects, fix the cause or raise it in the issue; the repository currently has zero
  suppressions and the intent is to keep it that way.

### Pull requests

Explain **why** the change is needed and what evidence says it works. A PR that names the failure
mode it fixes and shows the verification is far easier to review than one that lists the files it
touched.

Keep unrelated changes out. If you notice something else worth fixing, file an issue.

---

## Architecture

[docs/architecture.md](docs/architecture.md) for the end-to-end model,
[docs/plugin-architecture.md](docs/plugin-architecture.md) for agents, hooks, and skills, and
[docs/adr/](docs/adr) for the decision record behind the parts that look surprising.

---

## Code of conduct

Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
