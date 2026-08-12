# Security policy

## Reporting a vulnerability

**Do not open a public issue for a security vulnerability.**

Report it privately through GitHub:

**<https://github.com/caylent-solutions/devbench/security/advisories/new>**

Private vulnerability reporting is enabled on this repository, so the report is visible only to
maintainers until a fix is published.

A useful report includes:

- what an attacker can do, and what they need in order to do it,
- the affected version (`devbench --version`) and configuration,
- reproduction steps,
- any mitigation you have found.

You will get an acknowledgement, an assessment of severity and affected versions, and notification
when a fix ships. If we conclude the behaviour is not a vulnerability we will say so and explain
why.

Please give us a reasonable opportunity to release a fix before disclosing publicly.

## Scope

devbench executes agent-authored code and shells out to `git` and `gh` on the operator's behalf, so
the boundaries that matter most are:

- **Guard bypass** -- anything that lets an agent escape the PreToolUse hooks or the read-only
  constraints placed on review agents.
- **Credential exposure** -- tokens or credentials reaching logs, work-unit files, commits, or
  notification payloads.
- **Scope escape** -- a work unit committing, staging, or mutating files outside its declared
  Changes Manifest.
- **Verification bypass** -- anything that lets a work unit satisfy a review or completion gate
  without the evidence that gate exists to require.
- **Command or prompt injection** -- untrusted content reaching a shell, or agent-controlled text
  forging the structured markers devbench reads back as state.

Reports about a *configured* devbench doing what its configuration says are not vulnerabilities.
Granting an agent write access and observing it write is expected behaviour.
