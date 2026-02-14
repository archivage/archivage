"""
JSONL.gz storage for archived tweets.
"""

import gzip
import json
from pathlib import Path


def getTweetId(tweet: dict) -> str | None:
    """Extract tweet ID from tweet object."""
    # New format: rest_id (string)
    if "rest_id" in tweet:
        return tweet["rest_id"]
    # New format: legacy.id_str
    if "legacy" in tweet and "id_str" in tweet["legacy"]:
        return tweet["legacy"]["id_str"]
    # Old format (from migration): tweet_id (integer)
    if "tweet_id" in tweet:
        return str(tweet["tweet_id"])
    return None


def loadExistingIds(path: Path) -> set[str]:
    """Load existing tweet IDs from JSONL.gz file."""
    ids = set()
    if not path.exists():
        return ids

    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            try:
                tweet = json.loads(line)
                tweet_id = getTweetId(tweet)
                if tweet_id:
                    ids.add(tweet_id)
            except json.JSONDecodeError:
                continue
    return ids


def appendTweets(path: Path, tweets: list[dict], existing_ids: set[str] = None) -> int:
    """
    Append tweets to JSONL.gz file, deduplicating against existing IDs.

    Returns the number of new tweets written.
    """
    if existing_ids is None:
        existing_ids = loadExistingIds(path)

    new_tweets = []
    for tweet in tweets:
        tweet_id = getTweetId(tweet)
        if tweet_id and tweet_id not in existing_ids:
            new_tweets.append(tweet)
            existing_ids.add(tweet_id)

    if not new_tweets:
        return 0

    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    # Append to gzip file
    with gzip.open(path, "at", encoding="utf-8") as f:
        for tweet in new_tweets:
            f.write(json.dumps(tweet, ensure_ascii=False) + "\n")

    return len(new_tweets)


def newerTweetId(a, b):
    if a is None: return b
    if b is None: return a
    return a if int(a) > int(b) else b


def olderTweetId(a, b):
    if a is None: return b
    if b is None: return a
    return a if int(a) < int(b) else b


def countTweets(path: Path) -> int:
    """Count tweets in JSONL.gz file."""
    if not path.exists():
        return 0

    count = 0
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for _ in f:
            count += 1
    return count
