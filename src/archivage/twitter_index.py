"""SQLite FTS index for the lossless Twitter JSONL archives."""

import gzip
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .config import getArchiveDir
from .state import parseTweetDate
from .storage import getTweetId, tweetAuthor, tweetText


SCHEMA = """
CREATE TABLE IF NOT EXISTS tweets (
    id              TEXT PRIMARY KEY,
    archive         TEXT NOT NULL,
    user_id         TEXT,
    handle          TEXT,
    name            TEXT,
    created_at      TEXT,
    text            TEXT NOT NULL,
    conversation_id TEXT,
    reply_to_id     TEXT,
    quoted_id       TEXT,
    urls_json       TEXT NOT NULL DEFAULT '[]',
    media_json      TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_tweets_conversation
    ON tweets(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tweets_handle
    ON tweets(handle, created_at);
CREATE TABLE IF NOT EXISTS sources (
    path        TEXT PRIMARY KEY,
    size        INTEGER NOT NULL,
    mtime_ns    INTEGER NOT NULL,
    indexed_at  TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS tweets_fts USING fts5(
    text,
    handle,
    name,
    content='tweets',
    content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS tweets_ai AFTER INSERT ON tweets BEGIN
    INSERT INTO tweets_fts(rowid, text, handle, name)
    VALUES (new.rowid, new.text, new.handle, new.name);
END;
CREATE TRIGGER IF NOT EXISTS tweets_ad AFTER DELETE ON tweets BEGIN
    INSERT INTO tweets_fts(tweets_fts, rowid, text, handle, name)
    VALUES ('delete', old.rowid, old.text, old.handle, old.name);
END;
CREATE TRIGGER IF NOT EXISTS tweets_au AFTER UPDATE ON tweets BEGIN
    INSERT INTO tweets_fts(tweets_fts, rowid, text, handle, name)
    VALUES ('delete', old.rowid, old.text, old.handle, old.name);
    INSERT INTO tweets_fts(rowid, text, handle, name)
    VALUES (new.rowid, new.text, new.handle, new.name);
END;
"""


def indexPath() -> Path:
    return Path.home() / '.cache/archivage/twitter/search.sqlite'


def connect(path: Path = None) -> sqlite3.Connection:
    path = path or indexPath()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=NORMAL')
    db.executescript(SCHEMA)
    return db


def _urls(tweet: dict) -> list[str]:
    urls = []
    legacy = tweet.get('legacy', {})
    for item in legacy.get('entities', {}).get('urls', []):
        url = item.get('expanded_url') or item.get('url')
        if url:
            urls.append(url)
    return list(dict.fromkeys(urls))


def _media(tweet: dict) -> list[dict]:
    legacy = tweet.get('legacy', {})
    entities = legacy.get('extended_entities') or legacy.get('entities') or {}
    media = []

    for item in entities.get('media', []):
        record = {
            'id': item.get('id_str'),
            'type': item.get('type'),
            'url': item.get('media_url_https') or item.get('media_url'),
            'expanded_url': item.get('expanded_url'),
        }
        variants = item.get('video_info', {}).get('variants', [])
        if variants:
            record['variants'] = variants
        media.append(record)

    return media


def tweetRecord(tweet: dict, archive: str) -> dict | None:
    tweet_id = getTweetId(tweet)
    if not tweet_id:
        return None

    legacy = tweet.get('legacy', {})
    author = tweetAuthor(tweet)
    quoted = tweet.get('quoted_status_result', {}).get('result', {})
    created_at = parseTweetDate(tweet)
    created_iso = created_at.isoformat() if created_at else None

    return {
        'id': tweet_id,
        'archive': archive,
        'user_id': author['user_id'],
        'handle': author['handle'],
        'name': author['name'],
        'created_at': created_iso,
        'text': tweetText(tweet),
        'conversation_id': legacy.get('conversation_id_str') or tweet_id,
        'reply_to_id': legacy.get('in_reply_to_status_id_str'),
        'quoted_id': getTweetId(quoted) if quoted else None,
        'urls_json': json.dumps(_urls(tweet), ensure_ascii=False),
        'media_json': json.dumps(_media(tweet), ensure_ascii=False),
    }


def indexTweets(tweets: list[dict], archive: str, path: Path = None) -> int:
    rows = [tweetRecord(tweet, archive) for tweet in tweets]
    rows = [row for row in rows if row]
    if not rows:
        return 0

    query = """
        INSERT INTO tweets (
            id, archive, user_id, handle, name, created_at, text,
            conversation_id, reply_to_id, quoted_id, urls_json, media_json
        ) VALUES (
            :id, :archive, :user_id, :handle, :name, :created_at, :text,
            :conversation_id, :reply_to_id, :quoted_id, :urls_json, :media_json
        )
        ON CONFLICT(id) DO UPDATE SET
            archive=excluded.archive,
            user_id=COALESCE(excluded.user_id, tweets.user_id),
            handle=COALESCE(excluded.handle, tweets.handle),
            name=COALESCE(excluded.name, tweets.name),
            created_at=COALESCE(excluded.created_at, tweets.created_at),
            text=CASE WHEN length(excluded.text) > length(tweets.text)
                      THEN excluded.text ELSE tweets.text END,
            conversation_id=COALESCE(excluded.conversation_id, tweets.conversation_id),
            reply_to_id=COALESCE(excluded.reply_to_id, tweets.reply_to_id),
            quoted_id=COALESCE(excluded.quoted_id, tweets.quoted_id),
            urls_json=CASE WHEN excluded.urls_json != '[]'
                           THEN excluded.urls_json ELSE tweets.urls_json END,
            media_json=CASE WHEN excluded.media_json != '[]'
                            THEN excluded.media_json ELSE tweets.media_json END
    """

    with connect(path) as db:
        db.executemany(query, rows)
    return len(rows)


def _sourceCurrent(db: sqlite3.Connection, path: Path) -> bool:
    stat = path.stat()
    row = db.execute(
        'SELECT size, mtime_ns FROM sources WHERE path = ?',
        (str(path),),
    ).fetchone()
    return bool(row and row['size'] == stat.st_size and row['mtime_ns'] == stat.st_mtime_ns)


def sourceCurrent(path: Path, db_path: Path = None) -> bool:
    """Return whether a source was completely indexed at this fingerprint."""
    if not path.exists():
        return False
    with connect(db_path) as db:
        return _sourceCurrent(db, path)


def markSource(path: Path, db_path: Path = None):
    stat = path.stat()
    with connect(db_path) as db:
        db.execute(
            """
            INSERT INTO sources(path, size, mtime_ns, indexed_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                size=excluded.size,
                mtime_ns=excluded.mtime_ns,
                indexed_at=excluded.indexed_at
            """,
            (str(path), stat.st_size, stat.st_mtime_ns, datetime.now().astimezone().isoformat()),
        )


def indexFile(path: Path, archive: str, db_path: Path = None, force: bool = False) -> int:
    with connect(db_path) as db:
        if not force and _sourceCurrent(db, path):
            return 0

    batch = []
    count = 0
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            try:
                tweet = json.loads(line)
            except json.JSONDecodeError:
                continue
            batch.append(tweet)
            if len(batch) >= 1000:
                count += indexTweets(batch, archive, db_path)
                batch.clear()

    if batch:
        count += indexTweets(batch, archive, db_path)
    markSource(path, db_path)
    return count


def archiveFiles(root: Path = None) -> list[tuple[Path, str]]:
    root = root or getArchiveDir() / 'twitter'
    files = [(path, path.name.removesuffix('.jsonl.gz'))
             for path in sorted((root / 'archive').glob('*.jsonl.gz'))]
    for collection in ('likes', 'bookmarks'):
        path = root / f'{collection}.jsonl.gz'
        if path.exists():
            files.append((path, collection))
    return files


def rebuildIndex(root: Path = None, db_path: Path = None, force: bool = False):
    for path, archive in archiveFiles(root):
        yield path, indexFile(path, archive, db_path, force)


def _decode(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    record = dict(row)
    record['urls'] = json.loads(record.pop('urls_json'))
    record['media'] = json.loads(record.pop('media_json'))
    handle = record.get('handle') or 'i'
    record['url'] = f"https://x.com/{handle}/status/{record['id']}"
    return record


def readTweet(tweet_id: str, path: Path = None) -> dict | None:
    with connect(path) as db:
        row = db.execute('SELECT * FROM tweets WHERE id = ?', (tweet_id,)).fetchone()
    return _decode(row)


def searchTweets(query: str, limit: int = 20, handle: str = None,
                 path: Path = None) -> list[dict]:
    where = ['tweets_fts MATCH ?']
    params = [query]
    if handle:
        where.append('lower(t.handle) = lower(?)')
        params.append(handle.lstrip('@'))
    params.append(limit)
    sql = f"""
        SELECT t.*, bm25(tweets_fts) AS rank
        FROM tweets_fts
        JOIN tweets t ON t.rowid = tweets_fts.rowid
        WHERE {' AND '.join(where)}
        ORDER BY rank, t.created_at DESC
        LIMIT ?
    """

    with connect(path) as db:
        try:
            rows = db.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            params[0] = f'"{query.replace(chr(34), chr(34) * 2)}"'
            rows = db.execute(sql, params).fetchall()
    return [_decode(row) for row in rows]


def readThread(tweet_id: str, path: Path = None) -> list[dict]:
    with connect(path) as db:
        target = db.execute(
            'SELECT conversation_id FROM tweets WHERE id = ?',
            (tweet_id,),
        ).fetchone()
        if not target:
            return []
        conversation_id = target['conversation_id'] or tweet_id
        rows = db.execute(
            """
            SELECT * FROM tweets
            WHERE conversation_id = ? OR id = ?
            ORDER BY created_at, id
            """,
            (conversation_id, conversation_id),
        ).fetchall()
    return [_decode(row) for row in rows]


def mediaUrls(tweet: dict) -> list[dict]:
    candidates = []
    for i, item in enumerate(tweet.get('media', []), start=1):
        variants = [v for v in item.get('variants', [])
                    if v.get('content_type') == 'video/mp4' and v.get('url')]
        if variants:
            best = max(variants, key=lambda v: v.get('bitrate', 0))
            candidates.append({'index': i, 'type': item.get('type'), 'url': best['url']})
        elif item.get('url'):
            candidates.append({'index': i, 'type': item.get('type'), 'url': item['url']})
    return candidates


def downloadMedia(tweet: dict, directory: Path, max_bytes: int = 200 * 1024 * 1024):
    directory.mkdir(parents=True, exist_ok=True)
    downloaded = []

    for item in mediaUrls(tweet):
        parsed = urlparse(item['url'])
        suffix = Path(parsed.path).suffix or ('.mp4' if item['type'] in ('video', 'animated_gif') else '.jpg')
        suffix = re.sub(r'[^.A-Za-z0-9]', '', suffix) or '.bin'
        destination = directory / f"{tweet['id']}-{item['index']}{suffix}"
        if destination.exists():
            downloaded.append(str(destination))
            continue

        temp = destination.with_suffix(destination.suffix + '.tmp')
        size = 0
        try:
            with httpx.stream('GET', item['url'], follow_redirects=True, timeout=60) as response:
                response.raise_for_status()
                with open(temp, 'wb') as stream:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise ValueError(
                                f'Media exceeds {max_bytes} bytes: {item["url"]}'
                            )
                        stream.write(chunk)
            temp.replace(destination)
        except Exception:
            temp.unlink(missing_ok=True)
            raise
        downloaded.append(str(destination))

    return downloaded


def stats(path: Path = None) -> dict:
    with connect(path) as db:
        tweets = db.execute('SELECT count(*) FROM tweets').fetchone()[0]
        sources = db.execute('SELECT count(*) FROM sources').fetchone()[0]
        latest = db.execute('SELECT max(created_at) FROM tweets').fetchone()[0]
    return {'tweets': tweets, 'sources': sources, 'latest': latest}
