"""
Configuration management for archivage.
"""

import tomllib
from pathlib import Path


CONFIG_FILE = Path.home() / ".config/archivage/config.toml"

_config = None


def loadConfig() -> dict:
    """Load config from TOML file, with defaults."""
    global _config
    if _config is not None:
        return _config

    defaults = {
        "archive_dir": str(Path.home() / "Archive"),
        "twitter": {
            "cookies": str(Path.home() / ".config/archivage/twitter/cookies.txt"),
            "accounts": str(Path.home() / ".config/archivage/twitter/accounts.txt"),
            "include_retweets": False,
        },
        "withings": {
            "tokens": str(Path.home() / ".config/archivage/withings/tokens.json"),
        },
        "polar": {
            "tokens": str(Path.home() / ".config/archivage/polar/tokens.json"),
        },
        "telegram": {
            "session": str(Path.home() / ".config/archivage/telegram/session"),
        },
        "gmail": {
            "user": None,
            "password_file": str(Path.home() / ".config/archivage/gmail/password"),
            "imap_host": "imap.gmail.com",
            "imap_port": 993,
        },
    }

    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "rb") as f:
            user_config = tomllib.load(f)
        # Merge user config into defaults
        _config = _mergeConfig(defaults, user_config)
    else:
        _config = defaults

    return _config


def _mergeConfig(defaults: dict, overrides: dict) -> dict:
    """Deep merge overrides into defaults."""
    result = defaults.copy()
    for key, value in overrides.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _mergeConfig(result[key], value)
        else:
            result[key] = value
    return result


def getArchiveDir() -> Path:
    """Get archive directory."""
    config = loadConfig()
    return Path(config["archive_dir"]).expanduser()


def getTwitterCookies() -> Path:
    """Get Twitter cookies file path."""
    config = loadConfig()
    return Path(config["twitter"]["cookies"]).expanduser()


def getTwitterAccounts() -> Path:
    """Get Twitter accounts file path (relative to archive_dir if not absolute)."""
    config = loadConfig()
    path = Path(config["twitter"]["accounts"]).expanduser()
    if not path.is_absolute():
        path = getArchiveDir() / path
    return path


def getTwitterIncludeRetweets() -> bool:
    """Get whether to include retweets (default: False)."""
    config = loadConfig()
    return config["twitter"].get("include_retweets", False)


def getTwitterStateDir() -> Path:
    """Get Twitter state directory (default: twitter/.state, relative to archive_dir)."""
    config = loadConfig()
    state_dir = config["twitter"].get("state_dir", "twitter/.state")
    path = Path(state_dir).expanduser()
    if not path.is_absolute():
        path = getArchiveDir() / path
    return path


def getTwitterPersonalCookies() -> Path:
    """Get personal cookies for likes/bookmarks. Falls back to getTwitterCookies()."""
    config = loadConfig()
    personal = config["twitter"].get("personal_cookies")
    if personal:
        return Path(personal).expanduser()
    return getTwitterCookies()


def getTwitterPersonalAccount() -> str | None:
    """Get personal account screen name for likes."""
    config = loadConfig()
    return config["twitter"].get("personal_account")


def getWithingsTokens() -> Path:
    config = loadConfig()
    return Path(config["withings"]["tokens"]).expanduser()


def getPolarTokens() -> Path:
    config = loadConfig()
    return Path(config["polar"]["tokens"]).expanduser()


def getTelegramSession() -> Path:
    config = loadConfig()
    return Path(config["telegram"]["session"]).expanduser()


def getGmailUser() -> str | None:
    """Gmail address used for IMAP login. Falls back to ARCHIVAGE_GMAIL_USER env."""
    import os
    config = loadConfig()
    return config["gmail"].get("user") or os.environ.get("ARCHIVAGE_GMAIL_USER")


def getGmailPassword() -> str | None:
    """Read the Gmail app password from password_file, or ARCHIVAGE_GMAIL_PASSWORD env."""
    import os
    env = os.environ.get("ARCHIVAGE_GMAIL_PASSWORD")
    if env:
        return env.strip()
    config = loadConfig()
    path = Path(config["gmail"]["password_file"]).expanduser()
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip()


def getGmailPasswordFile() -> Path:
    config = loadConfig()
    return Path(config["gmail"]["password_file"]).expanduser()


def getGmailImapHost() -> str:
    config = loadConfig()
    return config["gmail"].get("imap_host", "imap.gmail.com")


def getGmailImapPort() -> int:
    config = loadConfig()
    return int(config["gmail"].get("imap_port", 993))
