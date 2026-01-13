"""
Logging configuration for archivage.
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Module-level logger
logger = logging.getLogger("archivage")

# Track if already configured
_configured = False


def setupLogging(log_dir: Path = None):
    """Configure rotating file logger (1MB max, 3 backups)."""
    global _configured
    if _configured:
        return logger

    if log_dir is None:
        log_dir = Path.home() / ".local/state/archivage"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "archivage.log"

    # Rotating handler: 1MB max, keep 3 backups
    handler = RotatingFileHandler(
        log_file,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

    _configured = True
    return logger
