# Setup

## Config file

Create `~/.config/archivage/config.toml`:

```toml
archive_dir = "~/Archive"

[twitter]
cookies = "~/.config/archivage/twitter/cookies.txt"
accounts = "twitter/.config/accounts.txt"
personal_cookies = "~/.config/archivage/twitter/personal.cookies.txt"
personal_account = "your_handle"
```

All paths support `~` expansion. Relative paths under `[twitter]` resolve
against `archive_dir`.

## Twitter

### Cookies

Twitter auth uses browser cookies exported in Netscape format. You need the
[Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
browser extension (or equivalent).

**Two cookie files** serve different purposes:

| File | Purpose | Account |
|------|---------|---------|
| `cookies.txt` | Archiving other accounts' timelines | Can be any logged-in account |
| `personal.cookies.txt` | Archiving your own likes & bookmarks | Must be your personal account |

To export cookies:
1. Log in to x.com in your browser
2. Click the "Get cookies.txt LOCALLY" extension
3. Export for `x.com`
4. Save to the path in your config

Cookies expire periodically — re-export when sync starts failing with auth
errors.

If you only use one account for everything, set both `cookies` and
`personal_cookies` to the same file (or omit `personal_cookies` to fall back
to `cookies`).

### Accounts list

Create your accounts file (default: `~/Archive/twitter/.config/accounts.txt`):

```
# One handle per line, without @
adam_tooze
karpathy
paulg
# Lines starting with # are ignored
```

### Commands

```bash
archivage twitter sync                # sync all accounts from accounts.txt
archivage twitter sync karpathy       # sync specific account(s)
archivage twitter sync --full         # ignore state, full re-sync
archivage twitter likes               # archive personal likes
archivage twitter bookmarks           # archive personal bookmarks
archivage twitter digest              # generate text digests for all
archivage twitter status              # show progress table
archivage twitter reindex             # rebuild state from archive files
```

### How sync works

**Account timelines** use the Search API with `since_id`/`max_id` for precise
pagination. First run does a full backwards sync; subsequent runs are
incremental (newest-first until hitting known tweets).

**Likes and bookmarks** use opaque cursor pagination (no `since_id`). First run
paginates to the end. Subsequent runs stop after 3 all-duplicate pages.
Cursors are saved to state for resume on interruption.

Sync is idempotent — tweets are deduplicated by ID on every write.

## Withings

Archives body composition data from a Withings smart scale (weight, fat mass,
muscle mass, etc.), plus intraday heart rate, workouts, and sleep.

### Setup

1. Create a Withings developer app at https://developer.withings.com/
2. Store credentials:
   ```bash
   archivage withings setup    # prompts for client_id + client_secret
   archivage withings auth     # OAuth2 flow (opens browser)
   ```
3. Fetch data:
   ```bash
   archivage withings fetch    # incremental sync
   archivage withings status   # show latest measures
   ```

Data is stored in `~/Archive/withings/withings.sqlite`.

## Telegram

Archives all Telegram chats (groups, channels, DMs) to SQLite.

### Setup

1. Get API credentials at https://my.telegram.org
2. Store and authenticate:
   ```bash
   archivage telegram setup    # prompts for api_id + api_hash
   archivage telegram auth     # phone + code auth
   ```
3. Optionally import a Telegram Desktop export:
   ```bash
   archivage telegram import ~/Downloads/Telegram/result.json
   ```
4. Fetch new messages:
   ```bash
   archivage telegram fetch    # incremental sync
   archivage telegram status   # show stats
   ```

Data is stored in `~/Archive/telegram/telegram.sqlite`.

### Media

Download media from a specific message:

```bash
archivage telegram download-media --chat <chat_id> --msg <msg_id> -o /tmp/
```
