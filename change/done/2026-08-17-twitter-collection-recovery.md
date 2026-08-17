---
started: 2026-08-17
done: 2026-08-17
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
- Reuse one Twitter client across the account batch so rate-limit state and the
  initialized HTTP/TLS session survive long quota pauses.
- Persist account sync mode too, so an interrupted incremental run never turns
  into an accidental full backfill.
- Keep full tracebacks in the file log for future per-account failures.

## Iteration loop

1. Reproduce endpoint rotation and collection recovery with isolated tests.
2. Run the full test suite.
3. Start the Lune user service and verify Bookmarks, Likes, account recovery,
   and resume after a real rate-limit reset.

## Tasks

- [x] Add resilient query-ID rotation
- [x] Repair collection state transitions and `--full`
- [x] Share the account client across rate-limit windows
- [x] Preserve account full/incremental mode across failures and interrupts
- [x] Add regression tests
- [x] Validate Bookmarks, Likes, account recovery, and quota resume on Lune
- [x] Commit and push
