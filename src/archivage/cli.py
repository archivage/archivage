"""
CLI entry point for archivage.
"""

import sys
from pathlib import Path
import click
from .twitter import TwitterClient
from .storage import appendTweets, loadExistingIds, countTweets, getTweetId
from .state import getAccountState, setAccountState, parseTweetDate
from .config import getArchiveDir, getTwitterCookies, getTwitterAccounts


def archiveAccount(account: str, cookies_path: Path, archive_dir: Path):
    """Archive a single Twitter account."""
    output_path = archive_dir / f"{account}.jsonl.gz"

    client = TwitterClient(cookies_path)

    try:
        # Get user ID
        user_id = client.getUserId(account)
        print(f"@{account} (ID: {user_id})")

        # Check state
        state = getAccountState(account)
        status = state.get("status")
        resume_cursor = state.get("cursor")
        archived_until = state.get("archived_until")

        # Load existing IDs for dedup
        existing_ids = loadExistingIds(output_path)

        # Determine starting point
        if status == "in_progress" and resume_cursor:
            print(f"Resuming from saved cursor...")
            cursor = resume_cursor
        else:
            cursor = None

        total_new = 0
        page = 0
        oldest_date = None
        setAccountState(account, status="in_progress")

        while True:
            page += 1
            tweets, next_cursor = client.getUserTweets(user_id, cursor=cursor, count=100)

            if tweets:
                new_count = appendTweets(output_path, tweets, existing_ids)
                total_new += new_count

                # Track oldest tweet date
                for tweet in tweets:
                    tweet_date = parseTweetDate(tweet)
                    if tweet_date:
                        if oldest_date is None or tweet_date < oldest_date:
                            oldest_date = tweet_date

                print(f"  Page {page}: {len(tweets)} tweets, {new_count} new")

                # Save cursor after each page for resume
                if next_cursor:
                    setAccountState(account, cursor=next_cursor)
            else:
                print(f"  Page {page}: 0 tweets")

            # Stop conditions
            if not next_cursor or not tweets:
                print("  End of timeline.")
                break

            cursor = next_cursor

        # Archive complete
        setAccountState(
            account,
            cursor="",  # Clear cursor
            archived_until=oldest_date.isoformat() if oldest_date else None,
            status="complete"
        )

        print(f"  New: {total_new}, Total: {countTweets(output_path)}")

    finally:
        client.close()


def loadAccountsList() -> list[str]:
    """Load accounts from config file."""
    accounts_file = getTwitterAccounts()
    if not accounts_file.exists():
        return []

    accounts = []
    with open(accounts_file) as f:
        for line in f:
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue
            # Handle @prefix
            if line.startswith("@"):
                line = line[1:]
            accounts.append(line)
    return accounts


@click.group()
def cli():
    """Archive social media to JSONL.gz."""
    pass


@cli.group()
def twitter():
    """Twitter archiving commands."""
    pass


@twitter.command("sync")
@click.argument("accounts", nargs=-1)
def twitter_sync(accounts):
    """Sync Twitter accounts. No args = read from accounts.txt."""
    cookies = getTwitterCookies()
    if not cookies.exists():
        click.echo(f"Cookies file not found: {cookies}")
        sys.exit(1)

    archive_dir = getArchiveDir() / "twitter/archive"

    if not accounts:
        accounts = loadAccountsList()
        if not accounts:
            click.echo(f"No accounts in {getTwitterAccounts()}")
            sys.exit(1)
        click.echo(f"Syncing {len(accounts)} accounts")

    for account in accounts:
        try:
            archiveAccount(account, cookies, archive_dir)
        except Exception as e:
            click.echo(f"Error archiving @{account}: {e}")


@twitter.command("digest")
@click.argument("accounts", nargs=-1)
def twitter_digest(accounts):
    """Generate digest files. No args = all archives."""
    from .digest import generateDigest, listArchives

    if not accounts:
        accounts = listArchives()
        if not accounts:
            click.echo("No archives found")
            sys.exit(1)
        click.echo(f"Generating digests for {len(accounts)} accounts")

    for account in accounts:
        path = generateDigest(account)
        if path:
            click.echo(f"  {account} → {path}")
        else:
            click.echo(f"  {account}: no archive or empty")


@cli.command("sync")
@click.pass_context
def sync(ctx):
    """Sync all platforms (currently: twitter)."""
    ctx.invoke(twitter_sync, accounts=())


def main():
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
