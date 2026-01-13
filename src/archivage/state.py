"""
State persistence for archivage.

State file tracks cursor (for resume) and archived_until (oldest tweet date).
"""

import json
from pathlib import Path
from datetime import datetime


STATE_DIR = Path.home() / ".local/state/archivage/twitter"
STATE_FILE = STATE_DIR / "state.json"


def loadState() -> dict:
    """Load state from file."""
    if not STATE_FILE.exists():
        return {"accounts": {}}
    with open(STATE_FILE) as f:
        return json.load(f)


def saveState(state: dict):
    """Save state to file."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def getAccountState(account: str) -> dict:
    """Get state for a specific account."""
    state = loadState()
    return state.get("accounts", {}).get(account, {})


def setAccountState(account: str, cursor: str = None, archived_until: str = None,
                    status: str = None):
    """Update state for a specific account."""
    state = loadState()
    if "accounts" not in state:
        state["accounts"] = {}
    if account not in state["accounts"]:
        state["accounts"][account] = {}

    acc = state["accounts"][account]
    if cursor is not None:
        if cursor:
            acc["cursor"] = cursor
        elif "cursor" in acc:
            del acc["cursor"]
    if archived_until is not None:
        acc["archived_until"] = archived_until
    if status is not None:
        acc["status"] = status

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
