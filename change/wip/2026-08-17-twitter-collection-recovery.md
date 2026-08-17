---
started: 2026-08-17
done:
priority: high
---

# Twitter collection endpoint recovery

Restore the daily Twitter archive after the identity/access rollout replaced a
working Bookmarks query ID and exposed a fragile recovery path.

## Approach

- Keep live GraphQL query discovery, but make discovery failures non-fatal.
- Prefer the last verified Bookmarks query ID and rotate away from a rejected
  cached ID before retrying.
- Persist whether a collection run is full or incremental, mark failed runs as
  errors, and recover the legacy `in_progress`-without-cursor state as an
  incremental run when a previous boundary exists.
- Make the existing `--full` option actually force a full traversal.

## Iteration loop

1. Reproduce endpoint rotation and collection recovery with isolated tests.
2. Run the full test suite.
3. Start the Lune user service and verify Bookmarks, Likes, accounts, final
   state, and the audit verdict.

## Tasks

- [x] Add resilient query-ID rotation
- [x] Repair collection state transitions and `--full`
- [x] Add regression tests
- [ ] Validate the daily service on Lune
- [ ] Commit and push
