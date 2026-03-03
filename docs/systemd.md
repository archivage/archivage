# Automated sync with systemd

Run archivage daily as a user service. The service runs once, syncs all
platforms, then exits. A timer restarts it daily.

## Service file

Create `~/.config/systemd/user/archivage.service`:

```ini
[Unit]
Description=Archive social media
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=%h/.local/bin/archivage sync
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
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

- Likes and bookmarks (`twitter likes`, `twitter bookmarks`) are not included
  in `archivage sync` — they use personal cookies and are meant to be run
  manually or added as a separate service
- Cookies expire periodically; re-export when the service starts failing
- Logs go to journald; use `journalctl --user -u archivage` to inspect
