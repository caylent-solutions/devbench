# Repository Security Status

Generated: 2026-03-05

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
- **Branch protection**: All repos require PRs to merge to main — no direct push, no force push, branches must be up-to-date before merge.
- **No open alerts**: All 4 repos are clean — zero Dependabot and zero secret scanning alerts.
