"""
State persistence for archivage.

State tracks:
- newest_id: most recent tweet ID archived
- oldest_id: oldest tweet ID archived
- status: complete/in_progress
"""

import json
import os
from pathlib import Path
from datetime import datetime
from .config import getTwitterStateDir


_UNSET = object()


def _stateFile() -> Path:
    """Get state file path (inside archive dir for syncing)."""
    return getTwitterStateDir() / "state.json"


def loadState() -> dict:
    """Load state from file."""
    state_file = _stateFile()
    if not state_file.exists():
        return {"accounts": {}}
    with open(state_file) as f:
        return json.load(f)


def saveState(state: dict):
    """Save state atomically."""
    state_file = _stateFile()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    temp_file = state_file.with_suffix('.tmp')
    with open(temp_file, 'w') as f:
        json.dump(state, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    temp_file.replace(state_file)


def getAccountState(account: str) -> dict:
    """Get state for a specific account."""
    state = loadState()
    return state.get("accounts", {}).get(account, {})


def setAccountState(account: str, newest_id: str = None, oldest_id: str = None,
                    status: str = None, count: int = None, user_id: str = None,
                    current_handle: str = None, aliases: list[str] = None,
                    availability: str = None, last_error: str = None,
                    checked_at: str = None):
    """Update state for a specific account."""
    state = loadState()
    if "accounts" not in state:
        state["accounts"] = {}
    if account not in state["accounts"]:
        state["accounts"][account] = {}

    acc = state["accounts"][account]

    if newest_id is not None:
        acc["newest_id"] = newest_id

    if oldest_id is not None:
        acc["oldest_id"] = oldest_id

    if status is not None:
        acc["status"] = status

    if count is not None:
        acc["count"] = count

    if user_id is not None:
        acc['user_id'] = str(user_id)

    if current_handle is not None:
        old_handle = acc.get('current_handle')
        known_aliases = set(acc.get('aliases', []))
        known_aliases.add(account)
        if old_handle:
            known_aliases.add(old_handle)
        known_aliases.add(current_handle)
        acc['current_handle'] = current_handle
        acc['aliases'] = sorted(known_aliases, key=str.lower)

    if aliases is not None:
        known_aliases = set(acc.get('aliases', []))
        known_aliases.update(a for a in aliases if a)
        known_aliases.add(account)
        acc['aliases'] = sorted(known_aliases, key=str.lower)

    if availability is not None:
        acc['availability'] = availability

    if last_error is not None:
        acc['last_error'] = last_error

    if checked_at is not None:
        acc['checked_at'] = checked_at

    # Clean up legacy fields
    for field in ["archived_until", "cursor", "method"]:
        if field in acc:
            del acc[field]

    saveState(state)


def clearAccountError(account: str):
    """Clear transient error fields after a successful check."""
    state = loadState()
    acc = state.get('accounts', {}).get(account)
    if not acc:
        return
    acc.pop('last_error', None)
    acc.pop('error_at', None)
    acc.pop('consecutive_errors', None)
    saveState(state)


def markAccountError(account: str, message: str):
    """Record a transient failure without discarding prior sync state."""
    state = loadState()
    accounts = state.setdefault('accounts', {})
    acc = accounts.setdefault(account, {})
    acc['status'] = 'error'
    acc['last_error'] = message
    acc['error_at'] = datetime.now().astimezone().isoformat()
    acc['consecutive_errors'] = acc.get('consecutive_errors', 0) + 1
    saveState(state)


def markAccountUnavailable(account: str, message: str):
    """Preserve an archive while marking its remote account unavailable."""
    now = datetime.now().astimezone().isoformat()
    setAccountState(
        account,
        status='unavailable',
        availability='unavailable',
        last_error=message,
        checked_at=now,
    )


def getCollectionState(name: str) -> dict:
    """Get state for a collection (likes, bookmarks)."""
    state = loadState()
    return state.get(name, {})


def setCollectionState(name: str, newest_id: str = None, oldest_id: str = None,
                       status: str = None, count: int = None,
                       cursor: str | None | object = _UNSET,
                       user_id: str = None,
                       sync_mode: str = None):
    """Update state for a collection (likes, bookmarks)."""
    state = loadState()
    if name not in state:
        state[name] = {}

    col = state[name]

    if newest_id is not None: col["newest_id"] = newest_id
    if oldest_id is not None: col["oldest_id"] = oldest_id
    if status   is not None: col["status"]    = status
    if count    is not None: col["count"]      = count
    if user_id  is not None: col["user_id"]    = user_id
    if sync_mode is not None: col["sync_mode"] = sync_mode

    # cursor: save for resume, clear on completion
    if cursor is not _UNSET and cursor is not None:
        col["cursor"] = cursor
    elif cursor is None or status == "complete":
        col.pop("cursor", None)
    if status == "complete":
        col.pop("sync_mode", None)

    saveState(state)


def parseTweetDate(tweet: dict) -> datetime | None:
    """Parse created_at from tweet."""
    if "legacy" not in tweet:
        return None
    created_at = tweet["legacy"].get("created_at")
    if not created_at:
        return None
    # Format: "Wed Dec 10 21:44:03 +0000 2025"
    try:
        return datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return None
