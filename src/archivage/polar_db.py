"""
SQLite storage for Polar exercises and HR samples.
"""

import json
import sqlite3
from pathlib import Path

from .config import getArchiveDir


def _dbPath() -> Path:
    return getArchiveDir() / 'polar' / 'polar.sqlite'


def initDb() -> sqlite3.Connection:
    path = _dbPath()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS exercises (
            id        TEXT PRIMARY KEY,
            start     TEXT NOT NULL,
            duration  INTEGER,
            sport     TEXT,
            calories  REAL,
            distance  REAL,
            hr_avg    INTEGER,
            hr_max    INTEGER,
            device    TEXT,
            has_route INTEGER DEFAULT 0,
            raw       TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS hr_samples (
            exercise_id TEXT    NOT NULL,
            offset_sec  INTEGER NOT NULL,
            heart_rate  INTEGER NOT NULL,
            PRIMARY KEY (exercise_id, offset_sec)
        )
    ''')
    conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_hr_exercise
        ON hr_samples(exercise_id)
    ''')
    conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_exercises_start
        ON exercises(start)
    ''')
    conn.commit()
    return conn


def insertExercise(conn: sqlite3.Connection, ex: dict) -> bool:
    """Insert an exercise. Returns True if new, False if duplicate."""
    ex_id = str(ex['id'])
    hr = ex.get('heart-rate') or ex.get('heart_rate') or {}
    try:
        conn.execute(
            'INSERT INTO exercises'
            ' (id, start, duration, sport, calories, distance,'
            '  hr_avg, hr_max, device, has_route, raw)'
            ' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                ex_id,
                ex.get('start-time', ex.get('start_time', '')),
                _parseDuration(ex.get('duration', '')),
                ex.get('sport', ex.get('detailed-sport-info', '')),
                ex.get('calories'),
                ex.get('distance'),
                hr.get('average'),
                hr.get('maximum'),
                ex.get('device', ''),
                1 if ex.get('has-route') else 0,
                json.dumps(ex, ensure_ascii=False, default=str),
            ),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def insertHrSamples(conn: sqlite3.Connection, exercise_id: str,
                    hr_values: list[int]) -> int:
    """Insert per-second HR samples. Returns count of new rows."""
    new = 0
    for offset, hr in enumerate(hr_values):
        if hr <= 0:
            continue
        try:
            conn.execute(
                'INSERT INTO hr_samples (exercise_id, offset_sec, heart_rate)'
                ' VALUES (?, ?, ?)',
                (exercise_id, offset, hr),
            )
            new += 1
        except sqlite3.IntegrityError:
            pass
    return new


def getExerciseIds(conn: sqlite3.Connection) -> set[str]:
    """Return set of all stored exercise IDs."""
    rows = conn.execute('SELECT id FROM exercises').fetchall()
    return {r[0] for r in rows}


def stats(conn: sqlite3.Connection) -> dict:
    exercises = conn.execute('SELECT COUNT(*) FROM exercises').fetchone()[0]
    samples   = conn.execute('SELECT COUNT(*) FROM hr_samples').fetchone()[0]
    row       = conn.execute('SELECT MIN(start), MAX(start) FROM exercises').fetchone()
    min_date, max_date = (row[0], row[1]) if row[0] else (None, None)
    return {
        'exercises':  exercises,
        'hr_samples': samples,
        'min_date':   min_date,
        'max_date':   max_date,
    }


def _parseDuration(dur_str: str) -> int | None:
    """Parse ISO 8601 duration (PT45M30S) to seconds."""
    if not dur_str or not dur_str.startswith('PT'):
        return None
    s = dur_str[2:]
    total = 0
    num = ''
    for c in s:
        if c.isdigit():
            num += c
        elif c == 'H' and num:
            total += int(num) * 3600
            num = ''
        elif c == 'M' and num:
            total += int(num) * 60
            num = ''
        elif c == 'S' and num:
            total += int(num)
            num = ''
    return total
