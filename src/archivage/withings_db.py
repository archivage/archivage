"""
SQLite storage for Withings body measures.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import getArchiveDir


def _dbPath() -> Path:
    return getArchiveDir() / 'withings' / 'withings.sqlite'


def initDb() -> sqlite3.Connection:
    path = _dbPath()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS measures (
            datetime TEXT    NOT NULL,
            type     TEXT    NOT NULL,
            value    REAL    NOT NULL,
            grpid    INTEGER,
            PRIMARY KEY (datetime, type)
        )
    ''')
    conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_datetime
        ON measures(datetime)
    ''')
    conn.commit()
    return conn


def insertMeasures(conn: sqlite3.Connection, measures: list[dict]) -> int:
    """Insert measures, skipping duplicates. Returns count of new rows."""
    new = 0
    for m in measures:
        dt = datetime.fromtimestamp(m['datetime'], tz=timezone.utc)
        dt_str = dt.strftime('%Y-%m-%d %H:%M:%S')
        try:
            conn.execute(
                'INSERT INTO measures (datetime, type, value, grpid) '
                'VALUES (?, ?, ?, ?)',
                (dt_str, m['type'], m['value'], m['grpid'])
            )
            new += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    return new


def getLastDatetime(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        'SELECT MAX(datetime) FROM measures'
    ).fetchone()
    return row[0] if row and row[0] else None


def getLatestByType(conn: sqlite3.Connection) -> dict:
    """Get the most recent value for each measure type."""
    rows = conn.execute('''
        SELECT type, value, datetime
        FROM measures m1
        WHERE datetime = (
            SELECT MAX(datetime) FROM measures m2
            WHERE m2.type = m1.type
        )
        ORDER BY type
    ''').fetchall()
    return {row[0]: {'value': row[1], 'datetime': row[2]} for row in rows}


def countMeasures(conn: sqlite3.Connection) -> int:
    row = conn.execute('SELECT COUNT(*) FROM measures').fetchone()
    return row[0]
