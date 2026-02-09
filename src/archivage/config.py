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
            "cookies": str(Path.home() / "Archive/.gallery-dl/x.obteneur.cookies.txt"),
            "accounts": str(Path.home() / ".config/archivage/twitter/accounts.txt"),
            "include_retweets": False,
        },
        "withings": {
            "tokens": str(Path.home() / ".config/archivage/withings/tokens.json"),
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


def getWithingsTokens() -> Path:
    config = loadConfig()
    return Path(config["withings"]["tokens"]).expanduser()
