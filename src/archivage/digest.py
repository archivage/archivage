"""
Generate digest files from archived tweets.
"""

import gzip
import json
from pathlib import Path
from datetime import datetime
from .config import getArchiveDir


def extractTweetData(tweet: dict) -> dict | None:
    """Extract normalized tweet data from either format."""
    # New API format
    if "__typename" in tweet:
        legacy = tweet.get("legacy", {})
        if not legacy:
            return None

        # Get author info from core.user_results.result
        user_result = tweet.get("core", {}).get("user_results", {}).get("result", {})
        user_legacy = user_result.get("legacy", {})
        user_core = user_result.get("core", {})

        content = legacy.get("full_text", "")
        if not content:
            return None

        # Parse date: "Sat Dec 21 00:34:19 +0000 2024"
        created_at = legacy.get("created_at", "")
        try:
            dt = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
            date_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        except:
            date_str = created_at

        # Parse user join date
        user_created = user_core.get("created_at", "")
        try:
            udt = datetime.strptime(user_created, "%a %b %d %H:%M:%S %z %Y")
            user_date_str = udt.strftime("%Y-%m-%d %H:%M:%S")
        except:
            user_date_str = user_created

        # Extract URL from entities
        url_entity = user_legacy.get("entities", {}).get("url", {}).get("urls", [])
        user_url = url_entity[0].get("expanded_url", "") if url_entity else ""

        return {
            "tweet_id": tweet.get("rest_id", ""),
            "date": date_str,
            "content": content,
            "favorite_count": legacy.get("favorite_count", 0),
            "bookmark_count": legacy.get("bookmark_count", 0),
            "retweet_count": legacy.get("retweet_count", 0),
            "author": {
                "handle": user_core.get("screen_name", ""),
                "display_name": user_core.get("name", ""),
                "id": user_result.get("rest_id", ""),
                "date_joined": user_date_str,
                "description": user_legacy.get("description", ""),
                "url": user_url,
                "favourites_count": user_legacy.get("favourites_count", 0),
                "followers_count": user_legacy.get("followers_count", 0),
                "statuses_count": user_legacy.get("statuses_count", 0),
                "verified": user_result.get("is_blue_verified", False),
            },
        }

    # Old gallery-dl format
    if "author" in tweet:
        content = tweet.get("content", "")
        if not content:
            return None

        author = tweet.get("author", {})
        return {
            "tweet_id": str(tweet.get("tweet_id", "")),
            "date": tweet.get("date", ""),
            "content": content,
            "favorite_count": tweet.get("favorite_count", 0),
            "bookmark_count": tweet.get("bookmark_count", 0),
            "retweet_count": tweet.get("retweet_count", 0),
            "author": {
                "handle": author.get("name", ""),
                "display_name": author.get("nick", ""),
                "id": str(author.get("id", "")),
                "date_joined": author.get("date", ""),
                "description": author.get("description", ""),
                "url": author.get("url", ""),
                "favourites_count": author.get("favourites_count", 0),
                "followers_count": author.get("followers_count", 0),
                "statuses_count": author.get("statuses_count", 0),
                "verified": author.get("verified", False),
            },
        }

    return None


def loadTweets(jsonl_path: Path) -> list[dict]:
    """Load and normalize tweets from JSONL.gz file."""
    tweets = []
    seen_ids = set()

    with gzip.open(jsonl_path, "rt", encoding="utf-8") as f:
        for line in f:
            try:
                raw = json.loads(line)
                tweet = extractTweetData(raw)
                if tweet and tweet["tweet_id"] not in seen_ids:
                    tweets.append(tweet)
                    seen_ids.add(tweet["tweet_id"])
            except json.JSONDecodeError:
                continue

    return tweets


def formatDigest(tweets: list[dict], account: str) -> str:
    """Format tweets into digest text with YAML frontmatter."""
    if not tweets:
        return ""

    # Get author info from most recent tweet
    tweets_sorted = sorted(tweets, key=lambda t: t["date"], reverse=True)
    author = tweets_sorted[0]["author"]

    # Build frontmatter (alphabetical order like old script)
    lines = ["---"]
    if author.get("date_joined"):
        lines.append(f"date_joined: '{author['date_joined']}'")
    if author.get("description"):
        # Multi-line description like old script
        desc = author["description"]
        if "\n" in desc or len(desc) > 60:
            lines.append(f"description: {desc}")
        else:
            lines.append(f"description: {desc}")
    if author.get("display_name"):
        lines.append(f"display_name: {author['display_name']}")
    if author.get("favourites_count"):
        lines.append(f"favourites_count: {author['favourites_count']}")
    lines.append(f"followers_count: {author['followers_count']}")
    lines.append(f"handle: {author['handle']}")
    if author.get("id"):
        lines.append(f"id: {author['id']}")
    lines.append(f"statuses_count: {author['statuses_count']}")
    if author.get("url"):
        lines.append(f"url: {author['url']}")
    lines.append(f"verified: {str(author['verified']).lower()}")
    lines.append("---")
    lines.append("")

    # Add tweets
    for tweet in tweets_sorted:
        # Parse and format date
        try:
            dt = datetime.fromisoformat(tweet["date"].replace(" ", "T"))
            date_str = dt.strftime("%Y.%m.%d %H:%M")
        except:
            date_str = tweet["date"][:16] if tweet["date"] else "unknown"

        header = f"🐦 {date_str} ♥️ {tweet['favorite_count']} 🔖 {tweet['bookmark_count']}"
        lines.append(header)
        lines.append("")
        lines.append(tweet["content"])
        lines.append("")

    return "\n".join(lines)


def generateDigest(account: str, archive_dir: Path = None, output_dir: Path = None) -> Path | None:
    """Generate digest for a single account."""
    if archive_dir is None:
        archive_dir = getArchiveDir() / "twitter/archive"
    if output_dir is None:
        output_dir = getArchiveDir() / "twitter/digests"

    jsonl_path = archive_dir / f"{account}.jsonl.gz"
    if not jsonl_path.exists():
        return None

    tweets = loadTweets(jsonl_path)
    if not tweets:
        return None

    content = formatDigest(tweets, account)
    if not content:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{account}.txt"
    output_path.write_text(content, encoding="utf-8")

    return output_path


def listArchives(archive_dir: Path = None) -> list[str]:
    """List all archived accounts."""
    if archive_dir is None:
        archive_dir = getArchiveDir() / "twitter/archive"

    accounts = []
    for f in archive_dir.glob("*.jsonl.gz"):
        # f.stem gives "account.jsonl", need to strip .jsonl too
        name = f.name.replace(".jsonl.gz", "")
        accounts.append(name)
    return sorted(accounts)
