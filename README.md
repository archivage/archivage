# archivage

Archive Twitter timelines, Withings body measures, and Telegram chats to local
storage (JSONL.gz and SQLite). Designed to run daily via systemd timer.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/archivage/archivage.git
uv tool install ./archivage
```

## Quick start — archive your own likes

The fastest way to see it work. You only need your browser cookies.

1. Install the [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
   browser extension
2. Log in to x.com, export cookies for the site, save to
   `~/.config/archivage/twitter/cookies.txt`
3. Create `~/.config/archivage/config.toml`:
   ```toml
   [twitter]
   personal_account = "your_handle"
   ```
4. Run:
   ```bash
   archivage twitter likes
   archivage twitter bookmarks
   archivage twitter digest
   ```
5. Check `~/Archive/twitter/digests/likes.txt` — a readable timeline of your
   liked tweets, searchable with grep

## Archiving other accounts

To archive other people's public timelines, you need a **separate Twitter
account** dedicated to archiving. Using your personal account risks getting it
flagged or suspended due to the volume of API requests.

Create a burner account, log in from a separate browser profile, export its
cookies, and configure both:

```toml
[twitter]
cookies = "~/.config/archivage/twitter/cookies.txt"
personal_cookies = "~/.config/archivage/twitter/personal.cookies.txt"
personal_account = "your_handle"
accounts = "twitter/.config/accounts.txt"    # relative to archive_dir
```

- `cookies` — burner account, used for `twitter sync` (other accounts' tweets)
- `personal_cookies` — your real account, used for `twitter likes/bookmarks`
- If omitted, `personal_cookies` falls back to `cookies`

New Twitter accounts get heavily rate-limited — expect the burner to need a few
weeks of aging before it can make enough API requests to sync reliably.

Create the accounts list (default: `~/Archive/twitter/.config/accounts.txt`):

```
# one handle per line
karpathy
paulg
adam_tooze
```

Then: `archivage twitter sync`

## Commands

```bash
# Twitter
archivage twitter sync [accounts]    # sync account timelines
archivage twitter likes              # archive personal likes
archivage twitter bookmarks          # archive personal bookmarks
archivage twitter digest [accounts]  # generate readable digests
archivage twitter status             # show sync progress
archivage twitter reindex            # rebuild state and identities from archives
archivage twitter index              # build/update the local full-text index
archivage twitter search <query>     # search locally, without Twitter quotas
archivage twitter read <id-or-url>   # read one locally archived tweet
archivage twitter thread <id-or-url> # read its locally archived conversation
archivage twitter media <id-or-url>  # list or download archived media

# Withings
archivage withings setup             # store API credentials
archivage withings auth              # OAuth2 flow
archivage withings fetch             # sync measures, intraday, workouts, sleep
archivage withings status            # show latest measures

# Telegram
archivage telegram setup             # store API credentials
archivage telegram auth              # phone + code auth
archivage telegram fetch             # incremental sync
archivage telegram import <file>     # import Desktop export
archivage telegram status            # show stats

# Web pages
archivage web save <url>             # save one page as markdown
archivage web save-all <url>         # save all same-domain pages

# YouTube transcripts
archivage youtube save <url>         # save a video transcript as markdown
archivage youtube status             # count archived videos by channel

# Newsletters / email
archivage newsletters setup          # configure Gmail app password
archivage newsletters add <email>    # track a sender (e.g. tibo@tmaker.io)
archivage newsletters fetch          # incremental sync via Gmail IMAP
archivage newsletters status         # count archived messages by sender
archivage newsletters import <eml>   # ingest a single .eml file

# All platforms
archivage sync                       # sync everything
```

## Data layout

```
~/Archive/
├── twitter/
│   ├── archive/            # per-account tweet archives
│   │   └── {handle}.jsonl.gz
│   ├── likes.jsonl.gz      # personal likes
│   ├── bookmarks.jsonl.gz  # personal bookmarks
│   ├── digests/            # readable text digests
│   └── .state/state.json   # sync progress
├── telegram/
│   └── telegram.sqlite
├── withings/
│   └── withings.sqlite
├── web/
│   └── {domain}/...        # extracted pages as markdown
├── youtube/
│   └── {channel-handle}/   # transcripts as markdown
└── newsletters/
    ├── .config/senders.txt # tracked sender list
    ├── .state/state.json   # per-sender UID state
    └── {sender}/...        # archived emails as markdown
```

Raw tweet objects are stored as-is from Twitter's GraphQL API — one JSON object
per line, gzip-compressed. This preserves all fields without transformation.
Account state is keyed by the original archive name but also records the stable
Twitter user ID, current handle, and previous handles. A handle rename therefore
continues writing to the same archive. Deleted or suspended accounts are marked
unavailable while their local data remains intact.
The rebuildable full-text index lives outside the synced archive at
`~/.cache/archivage/twitter/search.sqlite` to avoid SQLite/Syncthing conflicts.

## Further setup

- [docs/setup.md](docs/setup.md) — detailed per-platform setup
- [docs/systemd.md](docs/systemd.md) — automated daily sync with systemd
