"""
CLI entry point for archivage.
"""

import os
import sys
import signal
from pathlib import Path
import click
from .twitter import TwitterClient
from .storage import appendTweets, loadExistingIds, countTweets, getTweetId, newerTweetId, olderTweetId
from .state import getAccountState, setAccountState, getCollectionState, setCollectionState, parseTweetDate
from .config import (getArchiveDir, getTwitterCookies, getTwitterAccounts,
                     getTwitterIncludeRetweets, getTwitterPersonalCookies,
                     getTwitterPersonalAccount, getTelegramSession)
from .log import setupLogging, logger
from .web import savePage, saveAll


def output(msg: str):
    """Print and flush immediately (for journald)."""
    print(msg, flush=True)


# Track current sync state for graceful interrupt handling
_sync_context = {
    "account": None,
    "collection": None,
    "newest_id": None,
    "oldest_id": None,
    "cursor": None,
    "active": False,
}


def _handleInterrupt(signum, frame):
    """Save state and exit gracefully on SIGINT/SIGTERM."""
    ctx = _sync_context
    if ctx["active"] and ctx["collection"]:
        output("\n  Interrupted. Saving state...")
        logger.info(f"Interrupted: saving state for {ctx['collection']}")
        setCollectionState(
            ctx["collection"],
            newest_id=ctx["newest_id"],
            oldest_id=ctx["oldest_id"],
            cursor=ctx.get("cursor"),
            status="in_progress",
        )
        output("State saved. Run again to resume.")
    elif ctx["active"] and ctx["account"]:
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
                        newest_id = newerTweetId(newest_id, tid)
                        oldest_id = olderTweetId(oldest_id, tid)

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
    dupe_pages = 0
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
                new_count = appendTweets(output_path, tweets, existing_ids)
                total_new += new_count

                # Track newest ID (but don't save to state until completion)
                for tweet in tweets:
                    tid = getTweetId(tweet)
                    if tid:
                        newest_id = newerTweetId(newest_id, tid)

                date_range = formatDateRange(tweets)
                # Show account name every 50 pages for log readability
                if page % 50 == 1:
                    output(f"[{account}] Page {page}: {len(tweets)} tweets, {new_count} new{date_range}")
                else:
                    output(f"Page {page}: {len(tweets)} tweets, {new_count} new{date_range}")
                logger.debug(f"Page {page}: {len(tweets)} tweets, {new_count} new{date_range}")

                # Bail out if entire page was duplicates
                if new_count == 0:
                    dupe_pages += 1
                    if dupe_pages >= 3:
                        output("Caught up (3 all-duplicate pages).")
                        logger.info("Incremental sync complete (duplicate bail-out)")
                        setAccountState(account, newest_id=newest_id, status="complete")
                        break
                else:
                    dupe_pages = 0
            else:
                output(f"Page {page}: 0 tweets (empty)")
                logger.debug(f"Page {page}: 0 tweets (empty)")
                # since_id bounds the query — one empty page is definitive
                output("Caught up.")
                logger.info("Incremental sync complete (empty page)")
                setAccountState(account, newest_id=newest_id, status="complete")
                break

            if not next_cursor:
                output("Caught up.")
                logger.info("Incremental sync complete")
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


def syncCollection(client, collection: str, output_path, existing_ids, fetch_fn):
    """Sync a collection (likes or bookmarks) by paginating newest-first.

    fetch_fn(cursor, count) -> (tweets, next_cursor)
    """
    initial_count = len(existing_ids)
    total_new = 0
    page = 0
    dupe_pages = 0
    newest_id = None
    oldest_id = None

    state = getCollectionState(collection)
    prev_status = state.get("status")
    prev_newest = state.get("newest_id")
    prev_oldest = state.get("oldest_id")
    resume_cursor = state.get("cursor")
    is_incremental = prev_status == "complete" and prev_newest

    cursor = None
    if prev_status == "in_progress" and resume_cursor:
        cursor = resume_cursor
        output(f"{collection}: resuming from saved cursor")
        logger.info(f"Resuming {collection} from cursor")
    elif is_incremental:
        output(f"{collection}: incremental")
        logger.info(f"Incremental {collection} sync")
    else:
        output(f"{collection}: full sync")
        logger.info(f"Full {collection} sync")

    # Preserve existing IDs from state
    newest_id = prev_newest
    oldest_id = prev_oldest

    setCollectionState(collection, status="in_progress")

    # Set up interrupt context
    _sync_context["collection"] = collection
    _sync_context["account"] = None
    _sync_context["newest_id"] = newest_id
    _sync_context["oldest_id"] = oldest_id
    _sync_context["cursor"] = cursor
    _sync_context["active"] = True

    try:
        while True:
            page += 1
            tweets, next_cursor = fetch_fn(cursor, 100)

            if tweets:
                new_count = appendTweets(output_path, tweets, existing_ids)
                total_new += new_count

                for tweet in tweets:
                    tid = getTweetId(tweet)
                    if tid:
                        newest_id = newerTweetId(newest_id, tid)
                        oldest_id = olderTweetId(oldest_id, tid)

                date_range = formatDateRange(tweets)
                if page % 50 == 1:
                    output(f"[{collection}] Page {page}: {len(tweets)} tweets, {new_count} new{date_range}")
                else:
                    output(f"Page {page}: {len(tweets)} tweets, {new_count} new{date_range}")

                _sync_context["newest_id"] = newest_id
                _sync_context["oldest_id"] = oldest_id
                _sync_context["cursor"] = next_cursor

                # Save progress
                running_count = initial_count + total_new
                if page % 50 == 0:
                    setCollectionState(collection, newest_id=newest_id,
                                       oldest_id=oldest_id, cursor=next_cursor,
                                       count=running_count)
                else:
                    setCollectionState(collection, newest_id=newest_id,
                                       oldest_id=oldest_id, cursor=next_cursor)

                # Incremental: stop on dupe pages
                if is_incremental and new_count == 0:
                    dupe_pages += 1
                    if dupe_pages >= 3:
                        output("Caught up (3 all-duplicate pages).")
                        logger.info(f"{collection} sync complete (dupe bail-out)")
                        break
                else:
                    dupe_pages = 0
            else:
                output(f"Page {page}: 0 tweets")
                if is_incremental:
                    output("Caught up.")
                    logger.info(f"{collection} incremental complete (empty)")
                else:
                    output("End of collection.")
                    logger.info(f"{collection} complete (empty page)")
                break

            if not next_cursor:
                output("End of collection.")
                logger.info(f"{collection} complete (no cursor)")
                break

            cursor = next_cursor

    except Exception:
        running_count = initial_count + total_new
        setCollectionState(collection, count=running_count)
        raise

    _sync_context["active"] = False
    _sync_context["collection"] = None
    total = countTweets(output_path)
    setCollectionState(collection, newest_id=newest_id, oldest_id=oldest_id,
                       status="complete", count=total)
    output(f"New: {total_new}, Total: {total}")
    logger.info(f"{collection} done: new={total_new} total={total}")


def _resolvePersonalUserId(client):
    """Resolve personal user ID from config, caching in state."""
    account = getTwitterPersonalAccount()
    if not account:
        raise click.ClickException(
            "personal_account not set in config.toml [twitter] section")

    # Check if cached in likes state
    state = getCollectionState("likes")
    cached = state.get("user_id")
    if cached:
        return cached

    user_id = client.getUserId(account)
    setCollectionState("likes", user_id=user_id)
    return user_id


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
    """Generate digest files. No args = all archives + likes/bookmarks."""
    from .digest import generateDigest, generateCollectionDigest, listArchives

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

    # Generate collection digests (likes, bookmarks)
    for collection in ("likes", "bookmarks"):
        path = generateCollectionDigest(collection)
        if path:
            click.echo(f"  {collection} → {path}")


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
                        oldest_id = olderTweetId(oldest_id, tid)
                        newest_id = newerTweetId(newest_id, tid)
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
            tweets.sort(key=lambda x: int(x[0]))
            with gzip.open(path, "wt", encoding="utf-8") as f:
                for _, line in tweets:
                    f.write(line if line.endswith("\n") else line + "\n")
            click.echo(f"  {account}: {count:,} tweets, sorted, newest={newest_id}")
        else:
            click.echo(f"  {account}: {count:,} tweets, newest={newest_id}")

        prev_status = state.get("status", "complete")
        setAccountState(account, newest_id=newest_id, oldest_id=oldest_id,
                        status=prev_status, count=count)


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

    # Add likes/bookmarks to status
    for name in ("likes", "bookmarks"):
        state = getCollectionState(name)
        if state:
            status = state.get("status", "-")
            tweets = state.get("count", 0)
            rows.append((name, tweets, status))

    # Column widths
    max_name   = max(len(r[0]) for r in rows)
    max_tweets = max(len(f"{r[1]:,}") for r in rows)

    for account, tweets, status in rows:
        click.echo(f"{account:<{max_name}}  {tweets:>{max_tweets},}  {status}")


@twitter.command("likes")
@click.option("--full", is_flag=True, help="Full sync from scratch (ignore state)")
def twitter_likes(full):
    """Archive personal likes."""
    cookies = getTwitterPersonalCookies()
    if not cookies.exists():
        click.echo(f"Personal cookies not found: {cookies}")
        click.echo("Set personal_cookies in [twitter] config.toml")
        sys.exit(1)

    output_path = getArchiveDir() / "twitter/likes.jsonl.gz"
    existing_ids = loadExistingIds(output_path)

    if full:
        setCollectionState("likes", status=None)

    client = TwitterClient(cookies)
    try:
        user_id = _resolvePersonalUserId(client)

        def fetch_likes(cursor, count):
            return client.getLikes(user_id, cursor=cursor, count=count)

        syncCollection(client, "likes", output_path, existing_ids, fetch_likes)
    finally:
        client.close()


@twitter.command("bookmarks")
@click.option("--full", is_flag=True, help="Full sync from scratch (ignore state)")
def twitter_bookmarks(full):
    """Archive personal bookmarks."""
    cookies = getTwitterPersonalCookies()
    if not cookies.exists():
        click.echo(f"Personal cookies not found: {cookies}")
        click.echo("Set personal_cookies in [twitter] config.toml")
        sys.exit(1)

    output_path = getArchiveDir() / "twitter/bookmarks.jsonl.gz"
    existing_ids = loadExistingIds(output_path)

    if full:
        setCollectionState("bookmarks", status=None)

    client = TwitterClient(cookies)
    try:
        def fetch_bookmarks(cursor, count):
            return client.getBookmarks(cursor=cursor, count=count)

        syncCollection(client, "bookmarks", output_path, existing_ids, fetch_bookmarks)
    finally:
        client.close()


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
    """Sync measures, intraday activity, workouts, and sleep from Withings."""
    from .withings import (getMeasures, getIntradayActivity, getWorkouts,
                           getSleepSummary)
    from .withings_db import (initDb, insertMeasures, getLastDatetime,
                              countMeasures, insertIntraday, getLastIntraday,
                              insertWorkouts, getLastWorkoutUpdate,
                              insertSleep, getLastSleep)
    from datetime import datetime, timedelta
    import time as _time

    client_id, client_secret = _withingsCredentials()
    conn = initDb()

    # ── Body measures ──
    last = getLastDatetime(conn)
    startdate = None
    if last:
        dt = datetime.strptime(last, '%Y-%m-%d %H:%M:%S')
        startdate = int(dt.timestamp()) + 1
        output(f"Measures: incremental since {last}")
    else:
        output("Measures: full fetch")

    measures = getMeasures(client_id, client_secret, startdate=startdate)
    new_m = insertMeasures(conn, measures)
    total_m = countMeasures(conn)
    output(f"  {new_m} new ({total_m} total)")

    # ── Intraday activity (max 24h per request) ──
    last_intra = getLastIntraday(conn)
    if last_intra:
        start_ts = int(datetime.strptime(last_intra, '%Y-%m-%d %H:%M:%S')
                        .timestamp()) + 1
    else:
        start_ts = int((datetime.now() - timedelta(days=7)).timestamp())

    now_ts = int(_time.time())
    new_intra = 0
    chunk_start = start_ts
    while chunk_start < now_ts:
        chunk_end = min(chunk_start + 86400, now_ts)
        try:
            rows = getIntradayActivity(
                client_id, client_secret, chunk_start, chunk_end)
            new_intra += insertIntraday(conn, rows)
        except Exception as e:
            output(f"  Intraday {chunk_start}: {e}")
        chunk_start = chunk_end

    output(f"Intraday: {new_intra} new points")

    # ── Workouts ──
    last_w = getLastWorkoutUpdate(conn)
    w_start = None
    if last_w:
        w_start = last_w[:10]
    try:
        workouts = getWorkouts(client_id, client_secret, startdate=w_start)
        new_w = insertWorkouts(conn, workouts)
        output(f"Workouts: {new_w} new")
    except Exception as e:
        output(f"Workouts: {e}")

    # ── Sleep ──
    last_sleep = getLastSleep(conn)
    s_start = None
    if last_sleep:
        s_start = last_sleep[:10]
        output(f"Sleep: incremental since {last_sleep}")
    else:
        output("Sleep: full fetch")

    try:
        nights = getSleepSummary(
            client_id, client_secret, startdate=s_start)
        new_s = insertSleep(conn, nights)
        output(f"  {new_s} new nights")
    except Exception as e:
        output(f"Sleep: {e}")

    conn.close()


@withings.command("status")
def withings_status():
    """Show latest measures and stats."""
    from datetime import datetime, timezone
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

    order = ['weight', 'fat_ratio', 'fat_mass', 'fat_free_mass',
             'muscle_mass', 'bone_mass', 'hydration',
             'systolic_bp', 'diastolic_bp', 'heart_pulse']
    units = {
        'weight': 'kg', 'fat_ratio': '%', 'fat_mass': 'kg',
        'fat_free_mass': 'kg', 'muscle_mass': 'kg',
        'bone_mass': 'kg', 'hydration': 'kg',
        'systolic_bp': 'mmHg', 'diastolic_bp': 'mmHg',
        'heart_pulse': 'bpm',
    }

    max_name = max(len(t) for t in order if t in latest)
    for t in order:
        if t not in latest:
            continue
        v = latest[t]
        unit = units.get(t, '')
        # UTC → local
        utc_dt = datetime.strptime(v['datetime'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        local_dt = utc_dt.astimezone().strftime('%Y-%m-%d %H:%M:%S')
        click.echo(f"  {t:<{max_name}}  {v['value']:>8.2f} {unit:>2}  ({local_dt})")


# ────────────
# Telegram

@cli.group()
def telegram():
    """Telegram chat archiving."""
    pass


def _telegramCredentials():
    from .telegram import loadCredentials
    creds = loadCredentials()
    if not creds:
        click.echo("No Telegram credentials found.")
        click.echo("Run: archivage telegram setup")
        sys.exit(1)
    return creds['api_id'], creds['api_hash']


@telegram.command("setup")
def telegram_setup():
    """Store Telegram API credentials (api_id + api_hash)."""
    from .telegram import saveCredentials
    click.echo("Get credentials at https://my.telegram.org")
    api_id = click.prompt("API ID", type=int)
    api_hash = click.prompt("API hash")
    saveCredentials(api_id, api_hash)
    click.echo("Credentials saved. Now run: archivage telegram auth")


@telegram.command("auth")
def telegram_auth():
    """Authenticate with Telegram (phone + code)."""
    import asyncio
    from .telegram import createClient, authenticate

    api_id, api_hash = _telegramCredentials()
    client = createClient(api_id, api_hash)

    async def run():
        me = await authenticate(client)
        output(f"Authenticated as {me.first_name} (id={me.id})")
        session = getTelegramSession()
        output(f"Session saved to {session}.session")
        await client.disconnect()

    asyncio.run(run())


@telegram.command("import")
@click.argument("file", type=click.Path(exists=True))
def telegram_import(file):
    """Import from Telegram Desktop export (result.json)."""
    from pathlib import Path
    from .telegram import parseExport
    from .telegram_db import initDb, upsertChat, insertMessages, setSyncState

    path = Path(file)
    output(f"Parsing {path.name}...")
    chats = parseExport(path)
    output(f"Found {len(chats)} chats")

    conn = initDb()
    total_new = 0
    total_msgs = 0

    for chat in chats:
        upsertChat(conn, chat['id'], chat['name'], chat['type'])
        msgs = chat['messages']
        total_msgs += len(msgs)

        if not msgs:
            conn.commit()
            continue

        # Batch insert in chunks
        BATCH = 5000
        chat_new = 0
        for i in range(0, len(msgs), BATCH):
            batch = msgs[i:i + BATCH]
            chat_new += insertMessages(conn, chat['id'], batch, 'export')
            conn.commit()

        # Set sync_state to max message ID
        max_id = max(m['id'] for m in msgs)
        setSyncState(conn, chat['id'], max_id)
        conn.commit()

        total_new += chat_new
        status = f"{chat_new:,} new" if chat_new < len(msgs) else f"{len(msgs):,}"
        output(f"  {chat['name']}: {status}")

    conn.close()
    output(f"Total: {total_new:,} new messages ({total_msgs:,} processed)")


@telegram.command("fetch")
def telegram_fetch():
    """Incremental sync via Telegram API."""
    import asyncio
    from .telegram import createClient, iterMessages, fetchDialogs
    from .telegram_db import initDb, upsertChat, insertMessages, getMaxId, setSyncState

    api_id, api_hash = _telegramCredentials()
    client = createClient(api_id, api_hash)
    conn = initDb()

    async def run():
        await client.start()
        total_new = 0

        dialogs = await fetchDialogs(client)
        output(f"Found {len(dialogs)} dialogs")

        for chat_id, name, chat_type, top_id in dialogs:
            upsertChat(conn, chat_id, name, chat_type)
            max_id = getMaxId(conn, chat_id) or 0

            if top_id and top_id <= max_id:
                continue

            chat_new = 0
            chat_max = max_id

            try:
                async for batch in iterMessages(client, chat_id, min_id=max_id):
                    new = insertMessages(conn, chat_id, batch, 'api')
                    chat_new += new
                    batch_max = max(m['id'] for m in batch)
                    chat_max = max(chat_max, batch_max)
                    setSyncState(conn, chat_id, chat_max)
                    conn.commit()
            except Exception as e:
                output(f"  {name}: error — {e}")
                logger.error(f"Telegram fetch {name} ({chat_id}): {e}")
                if chat_new:
                    conn.commit()
                continue

            if chat_new:
                total_new += chat_new
                output(f"  {name}: {chat_new} new")

        await client.disconnect()
        output(f"Total: {total_new} new messages")

    try:
        asyncio.run(run())
    finally:
        conn.close()


@telegram.command("download-media")
@click.option("--chat", required=True, type=int, help="Chat ID")
@click.option("--msg", required=True, type=int, help="Message ID")
@click.option("-o", "--output", default=".", help="Output directory")
def telegram_download_media(chat, msg, output):
    """Download media from a specific message."""
    import asyncio
    from pathlib import Path
    from .telegram import createClient, downloadMedia

    api_id, api_hash = _telegramCredentials()
    client = createClient(api_id, api_hash)
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)

    async def run():
        await client.start()
        path = await downloadMedia(client, chat, msg, out)
        await client.disconnect()
        if path:
            click.echo(path)
        else:
            click.echo("No media found or download failed.")

    asyncio.run(run())


@telegram.command("status")
def telegram_status():
    """Show Telegram archive stats."""
    from .telegram_db import initDb, stats

    conn = initDb()
    s = stats(conn)
    conn.close()

    if s['messages'] == 0:
        click.echo("No messages yet.")
        click.echo("Run: archivage telegram import <result.json>")
        click.echo("  or: archivage telegram fetch")
        return

    click.echo(f"Chats:    {s['chats']:,}")
    click.echo(f"Messages: {s['messages']:,}")
    if s['min_date'] and s['max_date']:
        click.echo(f"Range:    {s['min_date'][:10]} → {s['max_date'][:10]}")
    if s['last_sync']:
        click.echo(f"Synced:   {s['synced']} chats, last {s['last_sync']}")


# ────────────
# Top-level commands

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
    try:
        ctx.invoke(telegram_fetch)
    except Exception as e:
        logger.error(f"Telegram sync error: {e}")
        click.echo(f"Telegram sync error: {e}")


@cli.group()
def web():
    """Web page archiving to markdown."""
    pass


@web.command("save")
@click.argument("url")
def web_save(url):
    """Save a single web page as markdown."""
    archive_dir = getArchiveDir()
    try:
        path = savePage(url, archive_dir)
        output(f"Saved: {path}")
    except Exception as e:
        logger.error(f"Failed to save {url}: {e}")
        output(f"Error: {e}")
        sys.exit(1)


@web.command("save-all")
@click.argument("url")
@click.option("--delay", default=0.5, help="Delay between requests in seconds.")
def web_save_all(url, delay):
    """Save all same-domain pages linked from a URL."""
    archive_dir = getArchiveDir()

    def progress(i, total, filename, status):
        if status == "skip":
            return
        output(f"  [{i}/{total}] {filename} ({status})")

    try:
        output(f"Fetching links from {url}...")
        saved = saveAll(url, archive_dir, delay=delay, on_progress=progress)
        output(f"Done. Saved {len(saved)} pages.")
    except Exception as e:
        logger.error(f"Failed: {e}")
        output(f"Error: {e}")
        sys.exit(1)


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
