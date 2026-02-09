"""
SQLite storage for Telegram messages.
"""

import sqlite3
from pathlib import Path

from .config import getArchiveDir


def _dbPath() -> Path:
    return getArchiveDir() / 'telegram' / 'telegram.sqlite'


def initDb() -> sqlite3.Connection:
    path = _dbPath()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS chats (
            id    INTEGER PRIMARY KEY,
            name  TEXT,
            type  TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id       INTEGER NOT NULL,
            chat_id  INTEGER NOT NULL,
            date     TEXT    NOT NULL,
            from_id  TEXT,
            from_name TEXT,
            text     TEXT,
            reply_to INTEGER,
            type     TEXT    NOT NULL,
            raw      TEXT,
            source   TEXT,
            PRIMARY KEY (chat_id, id)
        )
    ''')
    conn.execute('''
        CREATE INDEX IF NOT EXISTS idx_messages_date
        ON messages(date)
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS sync_state (
            chat_id  INTEGER PRIMARY KEY,
            max_id   INTEGER NOT NULL,
            updated  TEXT    NOT NULL
        )
    ''')
    conn.commit()
    return conn


def upsertChat(conn: sqlite3.Connection, chat_id: int, name: str, chat_type: str):
    conn.execute(
        'INSERT INTO chats (id, name, type) VALUES (?, ?, ?)'
        ' ON CONFLICT(id) DO UPDATE SET name=excluded.name, type=excluded.type',
        (chat_id, name, chat_type),
    )


def insertMessages(conn: sqlite3.Connection, chat_id: int,
                   messages: list[dict], source: str) -> int:
    """Insert messages for a chat, skipping duplicates. Returns new count."""
    new = 0
    for m in messages:
        try:
            conn.execute(
                'INSERT INTO messages'
                ' (id, chat_id, date, from_id, from_name, text, reply_to, type, raw, source)'
                ' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (
                    m['id'], chat_id, m['date'], m.get('from_id'),
                    m.get('from_name'), m.get('text'), m.get('reply_to'),
                    m['type'], m.get('raw'), source,
                ),
            )
            new += 1
        except sqlite3.IntegrityError:
            pass
    return new


def getMaxId(conn: sqlite3.Connection, chat_id: int) -> int | None:
    row = conn.execute(
        'SELECT max_id FROM sync_state WHERE chat_id = ?', (chat_id,)
    ).fetchone()
    return row[0] if row else None


def setSyncState(conn: sqlite3.Connection, chat_id: int, max_id: int):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    conn.execute(
        'INSERT INTO sync_state (chat_id, max_id, updated) VALUES (?, ?, ?)'
        ' ON CONFLICT(chat_id) DO UPDATE SET max_id=excluded.max_id, updated=excluded.updated',
        (chat_id, max_id, now),
    )


def stats(conn: sqlite3.Connection) -> dict:
    """Return summary stats."""
    chats   = conn.execute('SELECT COUNT(*) FROM chats').fetchone()[0]
    msgs    = conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
    row     = conn.execute('SELECT MIN(date), MAX(date) FROM messages').fetchone()
    min_date, max_date = (row[0], row[1]) if row[0] else (None, None)
    synced  = conn.execute('SELECT COUNT(*) FROM sync_state').fetchone()[0]
    last_up = conn.execute('SELECT MAX(updated) FROM sync_state').fetchone()[0]
    return {
        'chats':     chats,
        'messages':  msgs,
        'min_date':  min_date,
        'max_date':  max_date,
        'synced':    synced,
        'last_sync': last_up,
    }
