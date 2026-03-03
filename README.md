# archivage

Archive Twitter timelines, Withings body measures, and Telegram chats to local
storage (JSONL.gz and SQLite). Designed to run daily via systemd timer.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv tool install archivage            # from PyPI (when published)
uv tool install ~/Code/archivage     # from local clone
uv tool install --editable ~/Code/archivage  # dev mode
```

This installs the `archivage` CLI globally.

## Quick start

```bash
archivage --help
archivage twitter sync               # sync all accounts in accounts.txt
archivage twitter likes              # archive personal likes
archivage twitter bookmarks          # archive personal bookmarks
archivage twitter digest             # generate readable digests
archivage twitter status             # show sync progress
archivage withings fetch             # sync body measures
archivage telegram fetch             # sync telegram chats
archivage sync                       # sync all platforms
```

## Configuration

Config file: `~/.config/archivage/config.toml`

```toml
archive_dir = "~/Archive"

[twitter]
cookies = "~/.config/archivage/twitter/cookies.txt"
accounts = "twitter/.config/accounts.txt"      # relative to archive_dir
personal_cookies = "~/.config/archivage/twitter/personal.cookies.txt"
personal_account = "your_handle"
```

See [docs/setup.md](docs/setup.md) for detailed setup instructions per platform.

## Data layout

```
~/Archive/
├── twitter/
│   ├── archive/            # per-account tweet archives
│   │   ├── account1.jsonl.gz
│   │   └── account2.jsonl.gz
│   ├── likes.jsonl.gz      # personal likes
│   ├── bookmarks.jsonl.gz  # personal bookmarks
│   ├── digests/            # readable text digests
│   └── .state/
│       └── state.json      # sync progress
├── telegram/
│   └── telegram.sqlite     # all chats and messages
└── withings/
    └── withings.sqlite     # body measures, intraday, workouts, sleep
```

Raw tweet objects are stored as-is from Twitter's GraphQL API — one JSON object
per line, gzip-compressed. This preserves all fields without transformation.

## Automated sync

See [docs/systemd.md](docs/systemd.md) for setting up daily automated syncing.
