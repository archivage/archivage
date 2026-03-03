# Automated sync with systemd

Run archivage daily as a user service. The service runs once, syncs all
platforms, then exits. A timer restarts it daily.

## Prerequisites

Before setting up the service, complete the interactive setup steps on the
machine (these require user input and can't run unattended):

```bash
# Install
uv tool install archivage

# Twitter — export cookies from browser, create accounts list
# See docs/setup.md for details

# Withings — interactive OAuth flow
archivage withings setup    # enter client_id + client_secret
archivage withings auth     # opens browser for OAuth

# Telegram — interactive phone auth
archivage telegram setup    # enter api_id + api_hash
archivage telegram auth     # enter phone + verification code
```

All credentials end up under `~/.config/archivage/`:

```
~/.config/archivage/
├── config.toml
├── twitter/
│   ├── cookies.txt
│   └── personal.cookies.txt   # optional, for likes/bookmarks
├── withings/
│   ├── credentials.json       # client_id + client_secret
│   └── tokens.json            # OAuth tokens (auto-refreshed)
└── telegram/
    ├── credentials.json       # api_id + api_hash
    └── session.session        # Telethon session
```

Test manually before enabling the timer:

```bash
archivage sync
```

## Service file

Create `~/.config/systemd/user/archivage.service`:

```ini
[Unit]
Description=Archive social media
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=%h/.local/bin/archivage sync
StandardOutput=journal
StandardError=journal
```

This runs `archivage sync` which syncs Twitter accounts, Withings measures,
and Telegram chats in sequence. To only sync Twitter, replace with
`archivage twitter sync`.

## Timer file

Create `~/.config/systemd/user/archivage.timer`:

```ini
[Unit]
Description=Daily archive sync

[Timer]
OnCalendar=*-*-* 08:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

`Persistent=true` means if the machine was off at 08:00, the sync runs on next
boot.

## Enable

```bash
systemctl --user daemon-reload
systemctl --user enable --now archivage.timer
```

## Check status

```bash
# Timer schedule
systemctl --user list-timers

# Last run logs
journalctl --user -u archivage.service -n 50

# Manual run
systemctl --user start archivage.service
```

## Lingering

For the timer to fire when you're not logged in (e.g. on a server), enable
lingering:

```bash
loginctl enable-linger $USER
```

## Notes

- Likes and bookmarks (`twitter likes`, `twitter bookmarks`) use personal
  cookies and are not included in `archivage sync` — run manually or add as a
  separate service
- Twitter cookies expire periodically — re-export when sync starts failing
- Withings OAuth tokens auto-refresh; should not need manual intervention
- Telegram sessions are long-lived but may expire after months of inactivity
- Logs go to journald; use `journalctl --user -u archivage` to inspect
