---
started: 2026-08-15
done:
---

# Twitter identity, local search, and agent access

Keep the existing lossless Twitter mirror while making account identity stable,
failures observable, archived tweets directly searchable, and live tweet reads
available to agents through a dedicated read-only workflow.

## Approach

- Treat Twitter user IDs as canonical identity. Keep the configured handle as
  the permanent archive key so existing filenames and history do not move.
- Store the current handle and aliases in state, recover identity during
  reindex, and keep deleted/unavailable accounts as preserved archives.
- Fail the daily service on transient per-account errors, but classify
  definitive unavailable accounts without deleting or repeatedly alarming.
- Build a rebuildable SQLite FTS5 index in the machine-local cache, outside the
  synced JSONL.gz source files. Index new tweets during sync and provide local
  search, tweet, thread, and media lookup commands.
- Use the public `twitter-cli` only for live cache misses. Put agent routing and
  account-safety rules in a dedicated `twitter` skill, not in generic ops.
- Prefer the Obtener session for public reads. Never expose cookies in prompts,
  command arguments, logs, or committed files. Keep all write operations out of
  the default agent workflow.

## Compatibility

- Existing JSONL.gz files remain canonical and unchanged.
- Existing account keys and filenames remain valid after handle changes.
- Reindex enriches state and the search index without fetching Twitter.
- A deleted account remains queryable locally and receives an explicit
  unavailable status.

## Iteration loop

1. Exercise identity and index helpers against temporary archives in tests.
2. Run reindex/search/read/thread locally against the synchronized archive.
3. Verify `twitter-cli` authentication without printing credentials.
4. Deploy the editable package to Lune, migrate state, run the three affected
   accounts manually, then inspect exit status and journald.
5. Validate the skill and forward-test representative tweet URL prompts.

## Tasks

- [x] Add stable identity and availability state
- [x] Detect handle changes and preserve aliases
- [x] Surface transient sync failures to systemd
- [x] Add SQLite FTS5 indexing and local lookup commands
- [x] Add live lookup wrappers and media handling
- [x] Install twitter-cli locally and on Lune
- [x] Create and validate the twitter skill
- [x] Reindex and deploy identities on Lune
- [x] Update archive documentation
- [ ] Pin and verify the intended Obtener cookie after explicit account confirmation
- [x] Finish the targeted Lune sync and build its initial local FTS index
