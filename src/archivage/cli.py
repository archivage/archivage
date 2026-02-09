"""
CLI entry point for archivage.
"""

import os
import sys
import signal
from pathlib import Path
import click
from .twitter import TwitterClient
from .storage import appendTweets, loadExistingIds, countTweets, getTweetId
from .state import getAccountState, setAccountState, parseTweetDate
from .config import getArchiveDir, getTwitterCookies, getTwitterAccounts, getTwitterIncludeRetweets
from .log import setupLogging, logger


def output(msg: str):
    """Print and flush immediately (for journald)."""
    print(msg, flush=True)


# Track current sync state for graceful interrupt handling
_sync_context = {
    "account": None,
    "newest_id": None,
    "oldest_id": None,
    "active": False,
}


def _handleInterrupt(signum, frame):
    """Save state and exit gracefully on SIGINT/SIGTERM."""
    ctx = _sync_context
    if ctx["active"] and ctx["account"]:
        output("\n  Interrupted. Saving state...")
        logger.info(f"Interrupted: saving state for @{ctx['account']}")
        setAccountState(
            ctx["account"],
            newest_id=ctx["newest_id"],
            oldest_id=ctx["oldest_id"],
            status="in_progress",
        )
        output("State saved. Run sync again to resume.")
    sys.exit(130 if signum == signal.SIGINT else 143)


signal.signal(signal.SIGINT, _handleInterrupt)
signal.signal(signal.SIGTERM, _handleInterrupt)


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
    """Archive a single Twitter account using Search API only."""
    output_path = archive_dir / f"{account}.jsonl.gz"

    logger.info(f"Sync start: @{account}" + (" (full)" if full else ""))
    client = TwitterClient(cookies_path)

    try:

        # Check state
        state = getAccountState(account)
        prev_newest_id = state.get("newest_id")
        prev_oldest_id = state.get("oldest_id")
        status = state.get("status")

        # Load existing IDs for dedup
        existing_ids = loadExistingIds(output_path)
        logger.debug(f"Loaded {len(existing_ids)} existing IDs")

        include_retweets = getTwitterIncludeRetweets()

        # Decide sync mode:
        # - Full sync: no newest_id yet, or explicit --full, or resuming in_progress
        # - Incremental: have newest_id and status is complete
        if full or not prev_newest_id or status == "in_progress":
            syncBackwards(client, account, output_path, existing_ids,
                          include_retweets, prev_oldest_id, prev_newest_id)
        else:
            syncForward(client, account, output_path, existing_ids,
                        include_retweets, prev_newest_id)

    finally:
        client.close()


def syncBackwards(client, account: str, output_path: Path, existing_ids: set,
                  include_retweets: bool, resume_oldest_id: str = None,
                  preserve_newest_id: str = None):
    """Full sync: paginate backwards through timeline using Search API."""
    initial_count = len(existing_ids)
    total_new = 0
    page = 0
    empty_pages = 0
    newest_id = preserve_newest_id
    oldest_id = resume_oldest_id
    cursor = None

    if resume_oldest_id:
        output(f"{account}: resuming from {resume_oldest_id}")
        logger.info(f"Resuming backwards sync from oldest_id={resume_oldest_id}")
    else:
        output(f"{account}: full sync")
        logger.info("Starting full backwards sync")

    setAccountState(account, status="in_progress")

    # Set up interrupt context
    _sync_context["account"] = account
    _sync_context["newest_id"] = newest_id
    _sync_context["oldest_id"] = oldest_id
    _sync_context["active"] = True

    try:
        while True:
            page += 1

            # Build query: from:account, optionally with max_id for pagination
            if oldest_id:
                query = f"from:{account} max_id:{oldest_id}"
            else:
                query = f"from:{account}"

            tweets, next_cursor = client.searchTweets(
                query, cursor=cursor, count=20, include_retweets=include_retweets
            )

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
                # Show account name every 50 pages for log readability
                if page % 50 == 1:
                    output(f"[{account}] Page {page}: {len(tweets)} tweets, {new_count} new{date_range}")
                else:
                    output(f"Page {page}: {len(tweets)} tweets, {new_count} new{date_range}")
                logger.debug(f"Page {page}: {len(tweets)} tweets, {new_count} new{date_range}")

                # Save progress (for resume on error or interrupt)
                _sync_context["newest_id"] = newest_id
                _sync_context["oldest_id"] = oldest_id

                # Update count every 50 pages
                running_count = initial_count + total_new
                if page % 50 == 0:
                    setAccountState(account, newest_id=newest_id, oldest_id=oldest_id,
                                    count=running_count)
                else:
                    setAccountState(account, newest_id=newest_id, oldest_id=oldest_id)
            else:
                empty_pages += 1
                output(f"Page {page}: 0 tweets (empty {empty_pages}/10)")
                logger.debug(f"Page {page}: 0 tweets (empty {empty_pages}/10)")

            cursor = next_cursor

            # End of results
            if not next_cursor:
                output("End of timeline.")
                logger.info("Sync complete: end of timeline")
                setAccountState(account, newest_id=newest_id, oldest_id=oldest_id,
                                status="complete")
                break

            # Too many empty pages
            if empty_pages >= 10:
                output("End of timeline (10 empty pages).")
                logger.info("Sync complete: 10 empty pages")
                setAccountState(account, newest_id=newest_id, oldest_id=oldest_id,
                                status="complete")
                break

    except Exception:
        # Save count on error so status shows progress
        running_count = initial_count + total_new
        setAccountState(account, count=running_count)
        raise

    _sync_context["active"] = False
    total = countTweets(output_path)
    setAccountState(account, count=total)
    output(f"New: {total_new}, Total: {total}")
    logger.info(f"Sync done: @{account} new={total_new} total={total}")


def syncForward(client, account: str, output_path: Path, existing_ids: set,
                include_retweets: bool, since_id: str):
    """Incremental sync: fetch new tweets since last sync using Search API.

    Note: newest_id is only updated on successful completion to avoid gaps
    if interrupted. An interrupted forward sync will re-fetch on next run.
    """
    initial_count = len(existing_ids)
    total_new = 0
    page = 0
    empty_pages = 0
    cursor = None
    newest_id = since_id  # Only updated in state on completion

    output(f"{account}: incremental since {since_id}")
    logger.info(f"Incremental sync since_id={since_id}")

    # Set up interrupt context - don't update newest_id on interrupt
    _sync_context["account"] = account
    _sync_context["newest_id"] = since_id  # Keep original to avoid gaps
    _sync_context["oldest_id"] = None
    _sync_context["active"] = True

    query = f"from:{account} since_id:{since_id}"

    try:
        while True:
            page += 1
            tweets, next_cursor = client.searchTweets(
                query, cursor=cursor, count=20, include_retweets=include_retweets
            )

            if tweets:
                empty_pages = 0
                new_count = appendTweets(output_path, tweets, existing_ids)
                total_new += new_count

                # Track newest ID (but don't save to state until completion)
                for tweet in tweets:
                    tid = getTweetId(tweet)
                    if tid and tid > newest_id:
                        newest_id = tid

                date_range = formatDateRange(tweets)
                # Show account name every 50 pages for log readability
                if page % 50 == 1:
                    output(f"[{account}] Page {page}: {len(tweets)} tweets, {new_count} new{date_range}")
                else:
                    output(f"Page {page}: {len(tweets)} tweets, {new_count} new{date_range}")
                logger.debug(f"Page {page}: {len(tweets)} tweets, {new_count} new{date_range}")
            else:
                empty_pages += 1
                output(f"Page {page}: 0 tweets (empty {empty_pages}/5)")
                logger.debug(f"Page {page}: 0 tweets (empty {empty_pages}/5)")

            if not next_cursor:
                output("Caught up.")
                logger.info("Incremental sync complete")
                setAccountState(account, newest_id=newest_id, status="complete")
                break

            if empty_pages >= 5:
                output("Caught up (5 empty pages).")
                logger.info("Incremental sync complete (5 empty pages)")
                setAccountState(account, newest_id=newest_id, status="complete")
                break

            cursor = next_cursor

    except Exception:
        # Save count on error so status shows progress
        # Don't update newest_id - will re-fetch on next run (dedup handles duplicates)
        running_count = initial_count + total_new
        setAccountState(account, count=running_count)
        raise

    _sync_context["active"] = False
    total = countTweets(output_path)
    setAccountState(account, count=total)
    output(f"New: {total_new}, Total: {total}")
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


@cli.group()
def withings():
    """Withings body measures archiving."""
    pass


def completeAccounts(ctx, param, incomplete):
    """Shell completion for account names."""
    accounts = list(dict.fromkeys(loadAccountsList()))
    return [a for a in accounts if a.startswith(incomplete)]


@twitter.command("sync")
@click.argument("accounts", nargs=-1, shell_complete=completeAccounts)
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


@twitter.command("reindex")
@click.argument("accounts", nargs=-1)
@click.option("--force", is_flag=True, help="Force reindex even if state exists")
@click.option("--sort", is_flag=True, help="Sort tweets by ID (chronological)")
def twitter_reindex(accounts, force, sort):
    """Rebuild state from archive files (find oldest/newest IDs)."""
    import gzip
    import json

    archive_dir = getArchiveDir() / "twitter/archive"
    if not archive_dir.exists():
        click.echo(f"No archive directory: {archive_dir}")
        return

    if accounts:
        archives = []
        for account in accounts:
            path = archive_dir / f"{account}.jsonl.gz"
            if path.exists():
                archives.append(path)
            else:
                click.echo(f"  {account}: no archive found")
    else:
        archives = sorted(archive_dir.glob("*.jsonl.gz"))

    if not archives:
        click.echo("No archives found")
        return

    click.echo(f"Scanning {len(archives)} archive(s)...")

    for path in archives:
        account = path.stem.replace(".jsonl", "")
        tweets = []
        oldest_id = None
        newest_id = None

        with gzip.open(path, "rt") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    tweet = json.loads(line)
                    tid = getTweetId(tweet)
                    if tid:
                        tweets.append((tid, line))
                        if oldest_id is None or tid < oldest_id:
                            oldest_id = tid
                        if newest_id is None or tid > newest_id:
                            newest_id = tid
                except Exception:
                    continue

        count = len(tweets)
        if count == 0:
            click.echo(f"  {account}: empty, skipping")
            continue

        state = getAccountState(account)
        state_ok = state.get("newest_id") and state.get("oldest_id")

        if not force and state_ok and not sort:
            if not state.get("count"):
                setAccountState(account, count=count)
                click.echo(f"  {account}: updated count to {count:,}")
            else:
                click.echo(f"  {account}: state ok, skipping (use --force)")
            continue

        # Sort and rewrite archive if requested
        if sort:
            tweets.sort(key=lambda x: x[0])
            with gzip.open(path, "wt", encoding="utf-8") as f:
                for _, line in tweets:
                    f.write(line if line.endswith("\n") else line + "\n")
            click.echo(f"  {account}: {count:,} tweets, sorted, newest={newest_id}")
        else:
            click.echo(f"  {account}: {count:,} tweets, newest={newest_id}")

        setAccountState(account, newest_id=newest_id, oldest_id=oldest_id,
                        status="complete", count=count)


@twitter.command("status")
def twitter_status():
    """Show sync status for all accounts."""
    accounts = list(dict.fromkeys(loadAccountsList()))  # dedupe, preserve order

    if not accounts:
        click.echo(f"No accounts in {getTwitterAccounts()}")
        return

    # Gather data from state (fast, no file I/O)
    rows = []
    for account in accounts:
        state = getAccountState(account)
        status = state.get("status", "-")
        tweets = state.get("count", 0)
        rows.append((account, tweets, status))

    # Column widths
    max_name   = max(len(r[0]) for r in rows)
    max_tweets = max(len(f"{r[1]:,}") for r in rows)

    for account, tweets, status in rows:
        click.echo(f"{account:<{max_name}}  {tweets:>{max_tweets},}  {status}")


def _withingsCredentials():
    """Load Withings client_id and client_secret from credentials file."""
    from .withings import loadCredentials
    creds = loadCredentials()
    if not creds:
        click.echo("No Withings credentials found.")
        click.echo("Run: archivage withings setup")
        sys.exit(1)
    return creds['client_id'], creds['client_secret']


@withings.command("setup")
def withings_setup():
    """Store Withings API credentials (client_id + client_secret)."""
    from .withings import saveCredentials
    client_id = click.prompt("Client ID")
    client_secret = click.prompt("Client secret", hide_input=True)
    saveCredentials(client_id, client_secret)
    click.echo("Credentials saved. Now run: archivage withings auth")


@withings.command("auth")
def withings_auth():
    """OAuth2 authorization flow (one-time setup)."""
    from .withings import runAuthFlow
    client_id, client_secret = _withingsCredentials()
    runAuthFlow(client_id, client_secret)


@withings.command("fetch")
def withings_fetch():
    """Sync measures from Withings (incremental)."""
    from .withings import getMeasures
    from .withings_db import initDb, insertMeasures, getLastDatetime, countMeasures
    from datetime import datetime

    client_id, client_secret = _withingsCredentials()
    conn = initDb()

    last = getLastDatetime(conn)
    startdate = None
    if last:
        dt = datetime.strptime(last, '%Y-%m-%d %H:%M:%S')
        startdate = int(dt.timestamp()) + 1
        output(f"Incremental since {last}")
    else:
        output("Full fetch")

    measures = getMeasures(client_id, client_secret, startdate=startdate)
    new = insertMeasures(conn, measures)
    total = countMeasures(conn)
    conn.close()
    output(f"New: {new}, Total: {total}")


@withings.command("status")
def withings_status():
    """Show latest measures and stats."""
    from .withings_db import initDb, getLatestByType, countMeasures

    conn = initDb()
    total = countMeasures(conn)
    latest = getLatestByType(conn)
    conn.close()

    if not latest:
        click.echo("No measures yet. Run: archivage withings fetch")
        return

    click.echo(f"Total measures: {total}")
    click.echo()

    # Show latest values, weight first
    order = ['weight', 'fat_ratio', 'fat_mass', 'fat_free_mass',
             'muscle_mass', 'bone_mass', 'hydration']
    units = {
        'weight': 'kg', 'fat_ratio': '%', 'fat_mass': 'kg',
        'fat_free_mass': 'kg', 'muscle_mass': 'kg',
        'bone_mass': 'kg', 'hydration': 'kg',
    }

    max_name = max(len(t) for t in order if t in latest)
    for t in order:
        if t not in latest:
            continue
        v = latest[t]
        unit = units.get(t, '')
        click.echo(f"  {t:<{max_name}}  {v['value']:>8.2f} {unit:>2}  ({v['datetime']})")


@cli.command("sync")
@click.option("--full", is_flag=True, help="Full sync from scratch (ignore state)")
@click.pass_context
def sync(ctx, full):
    """Sync all platforms."""
    ctx.invoke(twitter_sync, accounts=(), full=full)
    try:
        ctx.invoke(withings_fetch)
    except Exception as e:
        logger.error(f"Withings sync error: {e}")
        click.echo(f"Withings sync error: {e}")


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
