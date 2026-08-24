## What this changes

<!-- What behaviour is different after this PR, and why that is needed. -->

Closes #

## Why

<!-- The failure mode this fixes, or the capability it unlocks. If a bug, what
     goes wrong today and under what conditions. -->

## Verification

<!-- Evidence it works. Not "tests pass" but what you ran and what it showed.
     If you fixed a bug, say how you confirmed the test fails without the fix. -->

```
make validate
```

## Checklist

- [ ] `make validate` passes locally
- [ ] Tests would fail without this change
- [ ] Docs updated in this PR, if behaviour or configuration changed
- [ ] Superseded code deleted and all callers updated
- [ ] No new `# noqa`, `# type: ignore`, `# nosec`, or `pragma: no cover`
- [ ] Unrelated changes kept out
