#!/usr/bin/env python3
"""
Generate user digest files from JSONL.gz Twitter archives.
Compatible with the original gallery-dl digest.py output format.

Usage:
    python digest.py <account>       # Generate digest for one account
    python digest.py --all           # Generate all digests
    python digest.py --compare <acc> # Compare with existing digest
"""

import sys
import json
import gzip
import yaml
from pathlib import Path
from datetime import datetime
from collections import defaultdict

ARCHIVE_DIR = Path("/tmp/archivage")  # Default to migrated location
LEGACY_ARCHIVE = Path.home() / "Archive/twitter/archive"
OUTPUT_DIR = Path.home() / "Archive/twitter/digests"


def readJsonl(jsonl_gz_path: Path):
    """Read tweets from a JSONL.gz file."""
    tweets = []
    with gzip.open(jsonl_gz_path, 'rt', encoding='utf-8') as f:
        for line in f:
            try:
                data = json.loads(line.strip())
                tweets.append(data)
            except json.JSONDecodeError as e:
                print(f"  Warning: Failed to parse line: {e}")
    return tweets


def getTweetsByUser(tweets: list, filter_user: str = None) -> dict:
    """Group tweets by user, matching original digest.py logic."""
    users = defaultdict(lambda: {"tweets": [], "info": None})
    tweet_ids = {}  # Track tweet_id -> set of subcategories

    for data in tweets:
        # Skip if no author info
        if 'author' not in data or not data['author']:
            continue

        author = data['author']
        author_name = author.get('name')

        if not author_name:
            continue

        if filter_user and author_name != filter_user:
            continue

        # Store user info
        users[author_name]["info"] = {
            "handle": author_name,
            "display_name": author.get('nick', ''),
            "id": author.get('id', ''),
            "date_joined": author.get('date', ''),
            "location": author.get('location', ''),
            "url": author.get('url', ''),
            "description": author.get('description', ''),
            "favourites_count": author.get('favourites_count', 0),
            "followers_count": author.get('followers_count', 0),
            "statuses_count": author.get('statuses_count', 0),
            "verified": author.get('verified', False)
        }

        # Skip tweets with no text content
        content = (data.get('content') or '').strip()
        if not content:
            continue

        # Determine subcategory
        subcategory = data.get('subcategory', 'timeline')
        tweet_id = data.get('tweet_id', '')

        # Track subcategories per tweet_id
        if tweet_id:
            if tweet_id not in tweet_ids:
                tweet_ids[tweet_id] = set()
            tweet_ids[tweet_id].add(subcategory)

        # Store tweet
        users[author_name]["tweets"].append({
            "date": data.get('date', ''),
            "content": content,
            "favorite_count": data.get('favorite_count', 0),
            "bookmark_count": data.get('bookmark_count', 0),
            "tweet_id": tweet_id,
            "subcategory": subcategory
        })

    # Add subcategory sets to each tweet
    for user_data in users.values():
        for tweet in user_data["tweets"]:
            if tweet["tweet_id"] in tweet_ids:
                tweet["subcategories"] = tweet_ids[tweet["tweet_id"]]

    return users


def formatUserFile(user_data: dict) -> str:
    """Format user data into text with YAML frontmatter (matches original)."""
    info = user_data["info"]
    tweets = user_data["tweets"]

    # Build YAML frontmatter
    frontmatter = {
        "handle": info["handle"],
        "display_name": info["display_name"],
        "id": info["id"],
        "date_joined": info["date_joined"],
        "location": info["location"],
        "url": info["url"],
        "description": info["description"],
        "favourites_count": info["favourites_count"],
        "followers_count": info["followers_count"],
        "statuses_count": info["statuses_count"],
        "verified": info["verified"]
    }

    # Remove empty fields
    frontmatter = {k: v for k, v in frontmatter.items() if v != ""}

    # Build file content
    lines = ["---"]
    lines.append(yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True).strip())
    lines.append("---")
    lines.append("")

    # Sort tweets by date (most recent first)
    tweets_sorted = sorted(tweets, key=lambda t: t["date"], reverse=True)

    # Deduplicate by tweet_id (keep first occurrence)
    seen_ids = set()
    unique_tweets = []
    for tweet in tweets_sorted:
        tid = tweet.get("tweet_id")
        if tid and tid in seen_ids:
            continue
        if tid:
            seen_ids.add(tid)
        unique_tweets.append(tweet)

    # Add tweets
    for tweet in unique_tweets:
        # Parse date
        try:
            dt = datetime.fromisoformat(tweet["date"].replace("Z", "+00:00"))
            date_str = dt.strftime("%Y.%m.%d %H:%M")
        except:
            date_str = tweet["date"]

        # Add emoji indicators for subcategories
        indicators = ""
        if "subcategories" in tweet:
            if "likes" in tweet["subcategories"]:
                indicators += " 🌟"
            if "bookmark" in tweet["subcategories"]:
                indicators += " 📂"

        # Format header
        header = f"🐦 {date_str} ♥️ {tweet['favorite_count']} 🔖 {tweet['bookmark_count']}{indicators}"
        lines.append(header)
        lines.append("")
        lines.append(tweet["content"])
        lines.append("")

    return "\n".join(lines)


def generateDigest(account: str, archive_dir: Path = ARCHIVE_DIR,
                   output_dir: Path = OUTPUT_DIR) -> dict:
    """Generate digest for a single account."""
    jsonl_gz = archive_dir / f"{account}.jsonl.gz"
    if not jsonl_gz.exists():
        return {"error": f"JSONL.gz not found: {jsonl_gz}"}

    tweets = readJsonl(jsonl_gz)
    users = getTweetsByUser(tweets, account)

    if account not in users:
        return {"error": f"No tweets found for {account}"}

    user_data = users[account]
    content = formatUserFile(user_data)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{account}.txt"
    output_file.write_text(content, encoding="utf-8")

    return {
        "account": account,
        "tweets": len(user_data["tweets"]),
        "output": str(output_file)
    }


def compareDigests(account: str, archive_dir: Path = ARCHIVE_DIR) -> dict:
    """Compare generated digest with existing one."""
    # Generate new digest to temp
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_output = Path(tmpdir)
        result = generateDigest(account, archive_dir, tmp_output)
        if "error" in result:
            return result

        new_file = tmp_output / f"{account}.txt"
        old_file = OUTPUT_DIR / f"{account}.txt"

        if not old_file.exists():
            return {"error": f"Existing digest not found: {old_file}"}

        new_content = new_file.read_text(encoding="utf-8")
        old_content = old_file.read_text(encoding="utf-8")

        if new_content == old_content:
            return {"match": True, "account": account}
        else:
            # Find first difference
            new_lines = new_content.splitlines()
            old_lines = old_content.splitlines()
            for i, (n, o) in enumerate(zip(new_lines, old_lines)):
                if n != o:
                    return {
                        "match": False,
                        "account": account,
                        "first_diff_line": i + 1,
                        "new": n[:100],
                        "old": o[:100]
                    }
            return {
                "match": False,
                "account": account,
                "reason": f"Different line counts: {len(new_lines)} vs {len(old_lines)}"
            }


def listAccounts(archive_dir: Path = ARCHIVE_DIR) -> list[str]:
    """List available JSONL.gz accounts."""
    return sorted([
        p.stem.replace('.jsonl', '')
        for p in archive_dir.glob("*.jsonl.gz")
    ])


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate digests from JSONL.gz")
    parser.add_argument("account", nargs="?", help="Account name")
    parser.add_argument("--all", action="store_true", help="Process all accounts")
    parser.add_argument("--compare", action="store_true", help="Compare with existing digest")
    parser.add_argument("--archive", "-a", type=Path, default=ARCHIVE_DIR,
                        help=f"Archive directory (default: {ARCHIVE_DIR})")
    parser.add_argument("--output", "-o", type=Path, default=OUTPUT_DIR,
                        help=f"Output directory (default: {OUTPUT_DIR})")
    args = parser.parse_args()

    if not args.account and not args.all:
        parser.print_help()
        print("\nAvailable accounts:")
        for acc in listAccounts(args.archive):
            print(f"  {acc}")
        sys.exit(1)

    accounts = listAccounts(args.archive) if args.all else [args.account]

    for account in accounts:
        if args.compare:
            result = compareDigests(account, args.archive)
            if "error" in result:
                print(f"  {account}: ERROR - {result['error']}")
            elif result.get("match"):
                print(f"  {account}: ✓ MATCH")
            else:
                print(f"  {account}: ✗ DIFFERS")
                if "first_diff_line" in result:
                    print(f"    Line {result['first_diff_line']}:")
                    print(f"      new: {result['new']}")
                    print(f"      old: {result['old']}")
                else:
                    print(f"    {result.get('reason', 'Unknown difference')}")
        else:
            result = generateDigest(account, args.archive, args.output)
            if "error" in result:
                print(f"  {account}: ERROR - {result['error']}")
            else:
                print(f"  {account}: {result['tweets']} tweets -> {result['output']}")


if __name__ == "__main__":
    main()
