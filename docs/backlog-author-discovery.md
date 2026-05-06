# Backlog Author Discovery Phase

## Purpose

Before authoring a new Backlog (or rewriting an existing one), an agentic backlog author SHOULD inspect the operator's current AWS, GitHub, and DNS state to learn what infrastructure already exists and incorporate that into the spec. Skipping discovery leads to two failure modes observed in practice: (a) the spec assumes external infrastructure must be created when it already exists, requiring a full rebrand mid-orchestration; (b) the spec hardcodes resource names that conflict with already-deployed resources, requiring rename rounds.

This document is the canonical pre-authoring checklist. Run every command in the relevant section before writing the spec; record the actual values observed; reference the observed values in the spec instead of placeholder names.

## When to run discovery

- Before authoring a new Backlog from scratch.
- Before regenerating an existing Backlog from a generator script.
- Before adding a new Epic to an existing Backlog if the Epic involves infrastructure (DNS, AWS resources, GitHub repos, secrets, branch protection).

## Prerequisites for the author

The author needs:

- `gh` CLI authenticated against the target GitHub organization (`gh auth status` shows logged in with `repo` and `read:org` scopes).
- `aws` CLI configured with profiles for every account the spec might touch (verify with `aws sts get-caller-identity --profile <name>` per profile).
- `dig` (system tool, no auth needed).
- Access to whatever shell/devcontainer the operator uses (so file-system inspections of pre-existing checkouts work).

If any of the above is missing, halt and ask the operator to authenticate before proceeding. Discovery output is only as good as the access available.

## Discovery checklist

Run each section in order. Record results in the spec's `Discovery` preamble (a new section authors should add to every spec, see `creating-specs-and-backlogs.md` Phase 0).

### 1. AWS account topology

```bash
# Per profile in ~/.aws/config:
for p in $(aws configure list-profiles); do
  echo "--- $p ---"
  aws sts get-caller-identity --profile "$p" --query '{account: Account, arn: Arn}' --output json 2>&1
done
```

Record: every account ID the operator can reach, its profile name, and the ARN format (SSO vs IAM-user). Reference these EXACTLY in the spec; do not assume profile names like `caylent-prod` -- use whatever the operator actually has.

### 2. AWS Route53 hosted zones (every account)

```bash
for p in <list of profiles>; do
  echo "--- $p ---"
  aws route53 list-hosted-zones --profile "$p" \
    --query 'HostedZones[].{Name:Name, Private:Config.PrivateZone, Id:Id}' \
    --output table 2>&1
done
```

Record: every public hosted zone the operator already owns, and its account. The spec MUST anchor any new DNS work under existing zones if a suitable parent zone already exists. Creating a new top-level zone (e.g., `<product>.caylent.com`) is appropriate ONLY when no existing zone could host the subdomain.

### 3. AWS Secrets Manager paths in scope

```bash
for p in <list of profiles>; do
  echo "--- $p ---"
  aws secretsmanager list-secrets --profile "$p" \
    --query 'SecretList[].{Name:Name, ARN:ARN, Description:Description}' \
    --output table 2>&1
done
```

Record: existing secrets in each account. The spec MUST not assume a Secret path is unused; if a path collision exists, choose a non-colliding path and explain in the spec.

### 4. GitHub organization repositories

```bash
gh repo list <org> --limit 200 --json name,visibility,defaultBranchRef,isArchived \
  --jq '.[] | "\(.name)\t\(.visibility)\t\(.defaultBranchRef.name // "(empty)")\t\(.isArchived)"'
```

Record: every repo in the org, its visibility, default branch, and archive state. The spec MUST reference existing repos by name when the work is in those repos; the spec MUST NOT request creation of repos that already exist.

### 5. GitHub repo branch protection (per repo the spec touches)

```bash
for r in <list of repos>; do
  echo "--- $r ---"
  gh api "repos/<org>/$r/branches/$(gh api repos/<org>/$r --jq .default_branch)/protection" 2>&1 | head -20
  gh api "repos/<org>/$r/rulesets" --jq '.[].name' 2>&1
done
```

Record: which branches are protected, what rules apply, and what bypass actors exist. The spec's `## Code Standards` section MUST not request actions (e.g., direct push to `main`) that branch protection blocks.

### 6. GitHub organization secrets (no values readable; only names)

```bash
gh api orgs/<org>/actions/secrets --jq '.secrets[].name'
```

Record: every org-level secret name. The spec MUST not assume a secret is set; if the spec requires a new secret, the spec MUST include a Task that creates it (or reference an existing manual prereq that does).

### 7. DNS state (external propagation check)

```bash
for d in <list of domains the spec might use>; do
  echo "--- $d ---"
  dig NS "$d" +short 2>&1
  dig "$d" +short 2>&1
done
```

Record: which domains resolve to which name servers. If a domain resolves to Route53 NS records belonging to one of the operator's accounts, that domain is OWNED; the spec MUST anchor sub-domains under it rather than requesting a new external delegation.

### 8. Local clone state (every repo the spec might touch)

```bash
for r in <list of repos>; do
  d="<workspace-root>/$r"
  if [ -d "$d/.git" ]; then
    echo "$r: $(git -C "$d" rev-parse --abbrev-ref HEAD) @ $(git -C "$d" rev-parse HEAD)"
    git -C "$d" remote -v | head -1
  else
    echo "$r: NOT CLONED"
  fi
done
```

Record: which repos are cloned locally, what branch each is on, what its origin URL is. The spec MUST not assume a clone exists if it does not; any not-yet-cloned repo MUST be explicitly called out so the operator can clone it before the orchestrator launches.

### 9. devbench `default_branch` alignment

For every repo listed in `devbench.yaml` `repos:` section:

```bash
yq '.repos | to_entries[] | "\(.key) \(.value.default_branch)"' < devbench.yaml | while read repo db; do
  remote_db=$(gh api "repos/$repo" --jq .default_branch 2>&1)
  if [ "$db" != "$remote_db" ]; then
    echo "MISMATCH: $repo -- yaml: $db, remote: $remote_db"
  fi
done
```

Record: any mismatch between `devbench.yaml`'s `default_branch` and the remote's actual default branch. The author MUST resolve every mismatch BEFORE the orchestrator launches; either flip the remote default via `gh repo edit --default-branch <name>` or update `devbench.yaml` to match the remote.

## Recording results in the spec

Add a `## Discovery` section to the spec BEFORE the architecture section. Include:

- Date discovery was run, by whom (or which agent).
- Verbatim output of each check above (truncated where verbose, full where compact).
- A `## Discovery -- Decisions` subsection listing every decision the spec makes that depends on a discovery result, with the result cited.

Example:

```markdown
## Discovery

Run on 2026-04-30 by claude-opus-4-7 against operator profiles
{default, platform-root-admin, platform-qa-admin, platform-prod-admin}.

### Route53 zones (Root account 468627576856)

- `solutions.caylent.com` -- public, NS-delegated upstream from caylent.com.
- `platform.solutions.caylent.com` -- public child.
- `registry.caylent.com` -- public, separate.

(Output of `aws route53 list-hosted-zones --profile platform-root-admin` truncated.)

### Decisions based on discovery

- DNS anchor: `telemetry.solutions.caylent.com` (NEW zone in Root account, NS-delegated
  from existing `solutions.caylent.com` via single in-account record). RATIONALE:
  no existing telemetry zone; `solutions.caylent.com` already in Caylent's accounts so
  no external NS delegation request needed.
- 5 per-env zones (`<env>.telemetry.solutions.caylent.com`) live in env accounts;
  rationale: each env's API GW + ALIAS records can be authored in the env account
  without cross-account writes.
```

The `## Discovery -- Decisions` subsection becomes the input to the spec's `## Architecture` and `## Backlog` sections; downstream tasks reference it.

## What "do not assume external infrastructure must be created" means

If discovery shows a piece of infrastructure exists, the spec and backlog MUST consume it. Common cases:

- **Existing Route53 hosted zone**: anchor sub-domains under it; do NOT request a new top-level NS delegation.
- **Existing GitHub repo**: reference it by name in `devbench.yaml` and tasks that target it; do NOT include a `gh repo create` step.
- **Existing AWS Secrets Manager secret at the path the spec wants**: reference by ARN; if conflict, choose a different path.
- **Existing org secret**: reference by name; do NOT include a "create org secret" step unless the value must be rotated.
- **Existing branch protection**: design the orchestrator's git-ops to comply (e.g., `single_branch` mode + PR-required); do NOT request direct pushes to `main`.

## Authority

This document is the source of truth for the discovery phase. `creating-specs-and-backlogs.md` Phase 0 is the entry point that points back here. If a backlog author skips discovery and the resulting spec hardcodes an assumption that discovery would have caught, the resulting orchestration cost is on the author.
