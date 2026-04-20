# Repository Security Status

> **Snapshot:** generated **2026-03-05**. This file is a point-in-time snapshot, not a live view. For the authoritative current state, check each repository's **Security** tab on GitHub (Dependabot alerts, CodeQL results, secret scanning). Regenerate this snapshot when you want a captured picture of all tracked repos in one place (typically by re-running the security audit script that produced it).

**CodeQL** (one of the features tracked below) is GitHub's static-analysis service that scans source code for security vulnerabilities. It runs as a workflow on each PR and can be configured to require passing analysis before merge.

## Summary

| Feature | git-repo | caylent-private-rpm | rpm-claude-marketplaces | rpm-claude-marketplaces-install |
|---------|----------|---------------------|-------------------------|---------------------------------|
| Dependabot Alerts | Enabled | Enabled | Enabled | Enabled |
| Automated Fixes | Enabled | Enabled | Enabled | Enabled |
| CodeQL Scanning | Enabled (python) | Enabled (no languages detected yet) | Enabled (no languages detected yet) | Enabled (no languages detected yet) |
| Secret Scanning | Enabled | Enabled | Enabled | Enabled |
| Push Protection | Enabled | Enabled | Enabled | Enabled |
| Branch Protection | main | main | main | main |
| Strict (up-to-date) | Yes | Yes | Yes | Yes |
| Required Checks | Analyze (python) | None yet | None yet | None yet |
| Open Dependabot Alerts | 0 | 0 | 0 | 0 |
| Open Secret Alerts | 0 | 0 | 0 | 0 |

## Notes

- **CodeQL languages**: Only `git-repo` has detected languages (python) because it has code. The other 3 repos will detect languages once code is added by the backlog.
- **Required checks**: Only `git-repo` has a required check (`Analyze (python)` from CodeQL). The other repos will get required checks auto-discovered when CI workflows are created by the backlog and the orchestrator re-runs `enable_security_features`.
- **Branch protection**: All repos require PRs to merge to main -- no direct push, no force push, branches must be up-to-date before merge.
- **No open alerts**: All 4 repos are clean -- zero Dependabot and zero secret scanning alerts.
