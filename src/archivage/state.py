"""
State persistence for archivage.

State tracks:
- newest_id: most recent tweet ID archived
- oldest_id: oldest tweet ID archived
- status: complete/in_progress
"""

import json
from pathlib import Path
from datetime import datetime
from .config import getTwitterStateDir


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
    """Save state to file."""
    state_file = _stateFile()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


def getAccountState(account: str) -> dict:
    """Get state for a specific account."""
    state = loadState()
    return state.get("accounts", {}).get(account, {})


def setAccountState(account: str, newest_id: str = None, oldest_id: str = None,
                    status: str = None, count: int = None):
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

    # Clean up legacy fields
    for field in ["archived_until", "cursor", "method"]:
        if field in acc:
            del acc[field]

    saveState(state)


def getCollectionState(name: str) -> dict:
    """Get state for a collection (likes, bookmarks)."""
    state = loadState()
    return state.get(name, {})


def setCollectionState(name: str, newest_id: str = None, oldest_id: str = None,
                       status: str = None, count: int = None,
                       cursor: str = None, user_id: str = None):
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

    # cursor: save for resume, clear on completion
    if cursor is not None:
        col["cursor"] = cursor
    elif status == "complete" and "cursor" in col:
        del col["cursor"]

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
