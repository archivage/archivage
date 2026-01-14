"""
CLI entry point for archivage.
"""

import os
import sys
from pathlib import Path
import click
from .twitter import TwitterClient
from .storage import appendTweets, loadExistingIds, countTweets, getTweetId
from .state import getAccountState, setAccountState, parseTweetDate
from .config import getArchiveDir, getTwitterCookies, getTwitterAccounts, getTwitterIncludeRetweets
from .log import setupLogging, logger


def formatDateRange(tweets: list) -> str:
    """Get date range string from tweets (newest → oldest)."""
    dates = []
    for tweet in tweets:
        dt = parseTweetDate(tweet)
        if dt:
            dates.append(dt)
    if not dates:
        return ""
    dates.sort(reverse=True)
    newest = dates[0].strftime("%Y-%m-%d")
    oldest = dates[-1].strftime("%Y-%m-%d")
    if newest == oldest:
        return f" [{newest}]"
    return f" [{newest} → {oldest}]"


def archiveAccount(account: str, cookies_path: Path, archive_dir: Path, full: bool = False):
    """Archive a single Twitter account using tweet ID-based pagination."""
    output_path = archive_dir / f"{account}.jsonl.gz"

    logger.info(f"Sync start: @{account}" + (" (full)" if full else ""))
    client = TwitterClient(cookies_path)

    try:
        # Get user ID (needed for UserTweets API)
        user_id = client.getUserId(account)
        print(f"@{account} (ID: {user_id})" + (" [full sync]" if full else ""))
        logger.info(f"@{account} user_id={user_id}")

        # Check state
        state = getAccountState(account)
        prev_newest_id = state.get("newest_id")
        prev_oldest_id = state.get("oldest_id")
        resume_cursor = state.get("cursor")

        # Load existing IDs for dedup
        existing_ids = loadExistingIds(output_path)
        logger.debug(f"Loaded {len(existing_ids)} existing IDs")

        include_retweets = getTwitterIncludeRetweets()

        # Decide sync mode
        if full or not prev_newest_id:
            # Full sync: use UserTweets API, go backwards
            syncFull(client, account, user_id, output_path, existing_ids,
                     include_retweets, resume_cursor if not full else None)
        else:
            # Incremental sync: use Search API with since_id
            syncIncremental(client, account, output_path, existing_ids,
                            include_retweets, prev_newest_id)

    finally:
        client.close()


def syncFull(client, account: str, user_id: str, output_path: Path,
             existing_ids: set, include_retweets: bool, resume_cursor: str = None):
    """Full sync using UserTweets API, with Search API fallback on gaps."""
    total_new = 0
    page = 0
    empty_pages = 0
    newest_id = None  # First tweet we see (most recent)
    oldest_id = None  # Last tweet we see (oldest)

    # Track cursors for both methods separately
    api_cursor = resume_cursor
    search_cursor = None
    using_search = False

    setAccountState(account, status="in_progress")

    if resume_cursor:
        print("  Resuming from saved cursor...")
        logger.info(f"Resuming from cursor: {resume_cursor[:30]}...")

    while True:
        page += 1

        # Fetch tweets
        if using_search:
            query = f"from:{account} max_id:{oldest_id}"
            tweets, next_cursor = client.searchTweets(
                query, cursor=search_cursor, count=20, include_retweets=include_retweets
            )
        else:
            tweets, next_cursor = client.getUserTweets(
                user_id, cursor=api_cursor, count=100, include_retweets=include_retweets
            )

        method = "search" if using_search else "api"

        if tweets:
            empty_pages = 0
            new_count = appendTweets(output_path, tweets, existing_ids)
            total_new += new_count

            # Track newest/oldest IDs
            for tweet in tweets:
                tid = getTweetId(tweet)
                if tid:
                    if newest_id is None or tid > newest_id:
                        newest_id = tid
                    if oldest_id is None or tid < oldest_id:
                        oldest_id = tid

            date_range = formatDateRange(tweets)
            print(f"  Page {page} ({method}): {len(tweets)} tweets, {new_count} new{date_range}")
            logger.debug(f"Page {page} ({method}): {len(tweets)} tweets, {new_count} new{date_range}")

            # Update cursor for current method
            if using_search:
                search_cursor = next_cursor
            else:
                api_cursor = next_cursor

            # Save progress
            if next_cursor:
                setAccountState(account, cursor=next_cursor, newest_id=newest_id)
        else:
            empty_pages += 1
            print(f"  Page {page} ({method}): 0 tweets (empty {empty_pages}/20)")
            logger.debug(f"Page {page} ({method}): 0 tweets (empty {empty_pages}/20)")

            # Update cursor for current method before switching
            if using_search:
                search_cursor = next_cursor
            else:
                api_cursor = next_cursor

        # End of results (both methods exhausted)
        if not next_cursor and (using_search or not oldest_id):
            print("  End of timeline.")
            logger.info("Sync complete: end of timeline")
            setAccountState(
                account, cursor="", newest_id=newest_id, oldest_id=oldest_id,
                status="complete"
            )
            break

        # Too many empty pages — give up
        if empty_pages >= 20:
            print(f"  Pausing: {empty_pages} consecutive empty pages")
            logger.warning(f"Sync pause: {empty_pages} empty pages")
            setAccountState(account, cursor=api_cursor, newest_id=newest_id)
            total = countTweets(output_path)
            print(f"  New: {total_new}, Total: {total}")
            print(f"  ⚠ Archive incomplete — will resume next sync")
            logger.info(f"Sync incomplete: @{account} new={total_new}")
            return

        # On empty page, alternate method (if we have oldest_id to search with)
        if not tweets and oldest_id:
            if using_search:
                # Switch back to UserTweets
                using_search = False
                logger.debug(f"Switching to UserTweets API")
            else:
                # Switch to Search API
                using_search = True
                search_cursor = None  # Fresh search with max_id
                logger.debug(f"Switching to Search API with max_id:{oldest_id}")

    total = countTweets(output_path)
    print(f"  New: {total_new}, Total: {total}")
    logger.info(f"Sync done: @{account} new={total_new} total={total}")


def syncIncremental(client, account: str, output_path: Path, existing_ids: set,
                    include_retweets: bool, since_id: str):
    """Incremental sync using Search API with since_id."""
    total_new = 0
    page = 0
    cursor = None
    newest_id = since_id  # Start with previous newest, update as we go

    print(f"  Incremental sync (since_id: {since_id})")
    logger.info(f"Incremental sync since_id={since_id}")
    setAccountState(account, status="in_progress")

    query = f"from:{account} since_id:{since_id}"

    while True:
        page += 1
        tweets, next_cursor = client.searchTweets(
            query, cursor=cursor, count=20, include_retweets=include_retweets
        )

        if tweets:
            new_count = appendTweets(output_path, tweets, existing_ids)
            total_new += new_count

            # Track newest ID
            for tweet in tweets:
                tid = getTweetId(tweet)
                if tid and tid > newest_id:
                    newest_id = tid

            date_range = formatDateRange(tweets)
            print(f"  Page {page}: {len(tweets)} tweets, {new_count} new{date_range}")
            logger.debug(f"Page {page}: {len(tweets)} tweets, {new_count} new{date_range}")
        else:
            print(f"  Page {page}: 0 tweets")
            logger.debug(f"Page {page}: 0 tweets")

        if not next_cursor:
            print("  Caught up.")
            logger.info("Incremental sync complete")
            setAccountState(account, cursor="", newest_id=newest_id, status="complete")
            break

        cursor = next_cursor

    total = countTweets(output_path)
    print(f"  New: {total_new}, Total: {total}")
    logger.info(f"Sync done: @{account} new={total_new} total={total}")


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
    setupLogging()


@cli.group()
def twitter():
    """Twitter archiving commands."""
    pass


@twitter.command("sync")
@click.argument("accounts", nargs=-1)
@click.option("--full", is_flag=True, help="Full sync from scratch (ignore state)")
def twitter_sync(accounts, full):
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
            archiveAccount(account, cookies, archive_dir, full=full)
        except Exception as e:
            logger.error(f"Error archiving @{account}: {e}")
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
@click.option("--full", is_flag=True, help="Full sync from scratch (ignore state)")
@click.pass_context
def sync(ctx, full):
    """Sync all platforms (currently: twitter)."""
    ctx.invoke(twitter_sync, accounts=(), full=full)


@cli.command("completion")
@click.argument("shell", type=click.Choice(["bash", "zsh"]), required=False)
def completion(shell):
    """Generate shell completion script.

    \b
    To activate:
    • archivage completion bash | sudo tee /etc/bash_completion.d/archivage
    • archivage completion zsh | sudo tee /usr/local/share/zsh/site-functions/_archivage
    """
    if shell is None:
        detected = os.path.realpath(f"/proc/{os.getppid()}/exe").split("/")[-1]
        if detected == "bash":
            click.echo("archivage completion bash | sudo tee /etc/bash_completion.d/archivage")
        elif detected == "zsh":
            click.echo("archivage completion zsh | sudo tee /usr/local/share/zsh/site-functions/_archivage")
        return

    os.environ["_ARCHIVAGE_COMPLETE"] = f"{shell}_source"
    script = cli._main_shell_completion(
        ctx_args=None,
        prog_name="archivage",
        complete_var="_ARCHIVAGE_COMPLETE"
    )
    click.echo(script.strip())


def main():
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
