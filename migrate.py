#!/usr/bin/env python3
"""
Migrate Twitter archive from individual JSON files to JSONL.gz format.

Usage:
    python migrate.py <account>           # Migrate one account to /tmp
    python migrate.py <account> --replace # Migrate and replace original
    python migrate.py --all               # Migrate all accounts to /tmp
    python migrate.py --all --replace     # Migrate all and replace
"""

import sys
import json
import gzip
from pathlib import Path
from datetime import datetime

ARCHIVE_DIR = Path.home() / "Archive/twitter/archive"
OUTPUT_DIR = Path("/tmp/archivage")


def migrate_account(account: str, output_dir: Path = OUTPUT_DIR) -> dict:
    """
    Migrate a single account from JSON files to JSONL.gz.
    Returns stats dict with counts and any errors.
    """
    account_dir = ARCHIVE_DIR / account
    if not account_dir.exists():
        return {"error": f"Account directory not found: {account_dir}"}

    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / f"{account}.jsonl"
    gz_path = output_dir / f"{account}.jsonl.gz"

    json_files = list(account_dir.glob("*.json"))
    if not json_files:
        return {"error": f"No JSON files found in {account_dir}"}

    stats = {
        "account": account,
        "json_files": len(json_files),
        "tweets_written": 0,
        "errors": [],
        "output": str(gz_path),
    }

    # Write JSONL (uncompressed first for easier debugging)
    with open(jsonl_path, 'w', encoding='utf-8') as f:
        for json_file in sorted(json_files):
            try:
                with open(json_file, 'r', encoding='utf-8') as jf:
                    data = json.load(jf)
                # Write as single line
                f.write(json.dumps(data, ensure_ascii=False) + '\n')
                stats["tweets_written"] += 1
            except (json.JSONDecodeError, IOError) as e:
                stats["errors"].append(f"{json_file.name}: {e}")

    # Compress to gzip
    with open(jsonl_path, 'rb') as f_in:
        with gzip.open(gz_path, 'wb') as f_out:
            f_out.writelines(f_in)

    # Get sizes
    stats["jsonl_size"] = jsonl_path.stat().st_size
    stats["gz_size"] = gz_path.stat().st_size
    stats["compression_ratio"] = stats["gz_size"] / stats["jsonl_size"] if stats["jsonl_size"] > 0 else 0

    # Remove uncompressed JSONL
    jsonl_path.unlink()

    return stats


def list_accounts() -> list[str]:
    """List all account directories in the archive."""
    return sorted([
        d.name for d in ARCHIVE_DIR.iterdir()
        if d.is_dir() and not d.name.startswith('.')
    ])


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Migrate Twitter JSON to JSONL.gz")
    parser.add_argument("account", nargs="?", help="Account name to migrate")
    parser.add_argument("--all", action="store_true", help="Migrate all accounts")
    parser.add_argument("--replace", action="store_true", help="Replace original files")
    parser.add_argument("--output", "-o", type=Path, default=OUTPUT_DIR,
                        help=f"Output directory (default: {OUTPUT_DIR})")
    args = parser.parse_args()

    if not args.account and not args.all:
        parser.print_help()
        print("\nAvailable accounts:")
        for acc in list_accounts():
            print(f"  {acc}")
        sys.exit(1)

    accounts = list_accounts() if args.all else [args.account]
    output_dir = args.output

    print(f"Migrating {len(accounts)} account(s) to {output_dir}")
    print()

    total_stats = {"accounts": 0, "tweets": 0, "errors": 0}

    for account in accounts:
        print(f"  {account}...", end=" ", flush=True)
        stats = migrate_account(account, output_dir)

        if "error" in stats:
            print(f"ERROR: {stats['error']}")
            total_stats["errors"] += 1
            continue

        size_mb = stats["gz_size"] / 1024 / 1024
        ratio = stats["compression_ratio"] * 100
        print(f"{stats['tweets_written']} tweets, {size_mb:.1f}MB ({ratio:.0f}% of original)")

        if stats["errors"]:
            print(f"    Warnings: {len(stats['errors'])} files had errors")

        total_stats["accounts"] += 1
        total_stats["tweets"] += stats["tweets_written"]
        total_stats["errors"] += len(stats["errors"])

    print()
    print(f"Done: {total_stats['accounts']} accounts, {total_stats['tweets']} tweets")
    if total_stats["errors"]:
        print(f"  {total_stats['errors']} errors/warnings")

    if args.replace:
        print("\n--replace not yet implemented (run validate.py first)")


if __name__ == "__main__":
    main()
