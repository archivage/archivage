"""
SQLite storage for Withings body measures.
"""

import json
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
    conn.execute('''
        CREATE TABLE IF NOT EXISTS intraday (
            datetime   TEXT NOT NULL PRIMARY KEY,
            heart_rate INTEGER,
            steps      INTEGER,
            calories   REAL,
            distance   REAL
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS workouts (
            id         INTEGER PRIMARY KEY,
            category   INTEGER,
            startdate  TEXT NOT NULL,
            enddate    TEXT NOT NULL,
            calories   REAL,
            effduration INTEGER,
            intensity  INTEGER,
            steps      INTEGER,
            distance   REAL,
            elevation  INTEGER,
            hr_average INTEGER,
            hr_min     INTEGER,
            hr_max     INTEGER,
            raw        TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS sleep (
            startdate          TEXT NOT NULL PRIMARY KEY,
            enddate            TEXT NOT NULL,
            sleep_score        INTEGER,
            sleep_efficiency   REAL,
            sleep_latency      INTEGER,
            wakeup_latency     INTEGER,
            total_sleep_time   INTEGER,
            total_timeinbed    INTEGER,
            deepsleepduration  INTEGER,
            lightsleepduration INTEGER,
            remsleepduration   INTEGER,
            wakeupduration     INTEGER,
            wakeupcount        INTEGER,
            out_of_bed_count   INTEGER,
            nb_rem_episodes    INTEGER,
            hr_average         INTEGER,
            hr_min             INTEGER,
            hr_max             INTEGER,
            rr_average         INTEGER,
            rr_min             INTEGER,
            rr_max             INTEGER,
            snoring            INTEGER,
            snoringepisodecount INTEGER,
            breathing_disturbances_intensity REAL,
            raw                TEXT
        )
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


def insertIntraday(conn: sqlite3.Connection, rows: list[dict]) -> int:
    new = 0
    for r in rows:
        dt = datetime.fromtimestamp(r['datetime'], tz=timezone.utc)
        dt_str = dt.strftime('%Y-%m-%d %H:%M:%S')
        try:
            conn.execute(
                'INSERT INTO intraday'
                ' (datetime, heart_rate, steps, calories, distance)'
                ' VALUES (?, ?, ?, ?, ?)',
                (dt_str, r.get('heart_rate'), r.get('steps'),
                 r.get('calories'), r.get('distance'))
            )
            new += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    return new


def insertWorkouts(conn: sqlite3.Connection, workouts: list[dict]) -> int:
    new = 0
    for w in workouts:
        wid = w.get('id')
        data = w.get('data', {})
        start = datetime.fromtimestamp(w['startdate'], tz=timezone.utc)
        end   = datetime.fromtimestamp(w['enddate'], tz=timezone.utc)
        try:
            conn.execute(
                'INSERT INTO workouts'
                ' (id, category, startdate, enddate, calories, effduration,'
                '  intensity, steps, distance, elevation,'
                '  hr_average, hr_min, hr_max, raw)'
                ' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (wid, w.get('category'),
                 start.strftime('%Y-%m-%d %H:%M:%S'),
                 end.strftime('%Y-%m-%d %H:%M:%S'),
                 data.get('calories'), data.get('effduration'),
                 data.get('intensity'), data.get('steps'),
                 data.get('distance'), data.get('elevation'),
                 data.get('hr_average'), data.get('hr_min'),
                 data.get('hr_max'),
                 json.dumps(w, ensure_ascii=False, default=str))
            )
            new += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    return new


def getLastIntraday(conn: sqlite3.Connection) -> str | None:
    row = conn.execute('SELECT MAX(datetime) FROM intraday').fetchone()
    return row[0] if row and row[0] else None


def getLastWorkoutUpdate(conn: sqlite3.Connection) -> str | None:
    row = conn.execute('SELECT MAX(enddate) FROM workouts').fetchone()
    return row[0] if row and row[0] else None


def insertSleep(conn: sqlite3.Connection, nights: list[dict]) -> int:
    new = 0
    for s in nights:
        start = datetime.fromtimestamp(s['startdate'], tz=timezone.utc)
        end   = datetime.fromtimestamp(s['enddate'], tz=timezone.utc)
        data  = s.get('data', {})
        try:
            conn.execute(
                'INSERT INTO sleep'
                ' (startdate, enddate, sleep_score, sleep_efficiency,'
                '  sleep_latency, wakeup_latency,'
                '  total_sleep_time, total_timeinbed,'
                '  deepsleepduration, lightsleepduration, remsleepduration,'
                '  wakeupduration, wakeupcount, out_of_bed_count,'
                '  nb_rem_episodes,'
                '  hr_average, hr_min, hr_max,'
                '  rr_average, rr_min, rr_max,'
                '  snoring, snoringepisodecount,'
                '  breathing_disturbances_intensity, raw)'
                ' VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (start.strftime('%Y-%m-%d %H:%M:%S'),
                 end.strftime('%Y-%m-%d %H:%M:%S'),
                 data.get('sleep_score'),
                 data.get('sleep_efficiency'),
                 data.get('sleep_latency'),
                 data.get('wakeup_latency'),
                 data.get('total_sleep_time'),
                 data.get('total_timeinbed'),
                 data.get('deepsleepduration'),
                 data.get('lightsleepduration'),
                 data.get('remsleepduration'),
                 data.get('wakeupduration'),
                 data.get('wakeupcount'),
                 data.get('out_of_bed_count'),
                 data.get('nb_rem_episodes'),
                 data.get('hr_average'),
                 data.get('hr_min'),
                 data.get('hr_max'),
                 data.get('rr_average'),
                 data.get('rr_min'),
                 data.get('rr_max'),
                 data.get('snoring'),
                 data.get('snoringepisodecount'),
                 data.get('breathing_disturbances_intensity'),
                 json.dumps(s, ensure_ascii=False, default=str))
            )
            new += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    return new


def getLastSleep(conn: sqlite3.Connection) -> str | None:
    row = conn.execute('SELECT MAX(startdate) FROM sleep').fetchone()
    return row[0] if row and row[0] else None
