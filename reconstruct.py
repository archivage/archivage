#!/usr/bin/env python3
"""
Reconstruct original JSON files from JSONL.gz archive.

Usage:
    python reconstruct.py <account>              # Reconstruct all to /tmp
    python reconstruct.py <account> --tweet-id X # Reconstruct specific tweet
    python reconstruct.py <account> --output DIR # Custom output directory
"""

import sys
import json
import gzip
from pathlib import Path
from datetime import datetime

MIGRATED_DIR = Path("/tmp/archivage")
OUTPUT_DIR = Path("/tmp/archivage/reconstructed")


def reconstructAccount(account: str, migrated_dir: Path = MIGRATED_DIR,
                       output_dir: Path = OUTPUT_DIR,
                       tweet_id: int = None) -> dict:
    """Reconstruct JSON files from JSONL.gz."""
    jsonl_gz = migrated_dir / f"{account}.jsonl.gz"
    if not jsonl_gz.exists():
        return {"error": f"JSONL.gz not found: {jsonl_gz}"}

    account_output = output_dir / account
    account_output.mkdir(parents=True, exist_ok=True)

    stats = {"account": account, "reconstructed": 0, "skipped": 0}

    with gzip.open(jsonl_gz, 'rt', encoding='utf-8') as f:
        for line in f:
            try:
                data = json.loads(line.strip())
            except json.JSONDecodeError:
                stats["skipped"] += 1
                continue

            tid = data.get('tweet_id')
            if tweet_id and tid != tweet_id:
                continue

            # Reconstruct filename: YYYY.MM.DD HH꞉MM꞉SS tweet_id.json
            date_str = data.get('date', '')
            try:
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                # Use special colon ꞉ (U+A789) as in original
                formatted = dt.strftime("%Y.%m.%d %H꞉%M꞉%S")
            except:
                formatted = date_str.replace(":", "꞉").replace(" ", "_")

            filename = f"{formatted} {tid}.json"
            output_file = account_output / filename

            # Write JSON (pretty-printed like original, with trailing newline)
            with open(output_file, 'w', encoding='utf-8') as out:
                json.dump(data, out, ensure_ascii=False, indent=4)
                out.write('\n')

            stats["reconstructed"] += 1

            if tweet_id:
                stats["output"] = str(output_file)
                break

    stats["output_dir"] = str(account_output)
    return stats


def listMigratedAccounts(migrated_dir: Path = MIGRATED_DIR) -> list[str]:
    """List accounts that have been migrated."""
    return sorted([
        p.stem.replace('.jsonl', '')
        for p in migrated_dir.glob("*.jsonl.gz")
    ])


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Reconstruct JSON from JSONL.gz")
    parser.add_argument("account", nargs="?", help="Account to reconstruct")
    parser.add_argument("--tweet-id", type=int, help="Specific tweet ID")
    parser.add_argument("--migrated", "-m", type=Path, default=MIGRATED_DIR,
                        help=f"Migrated files directory (default: {MIGRATED_DIR})")
    parser.add_argument("--output", "-o", type=Path, default=OUTPUT_DIR,
                        help=f"Output directory (default: {OUTPUT_DIR})")
    args = parser.parse_args()

    if not args.account:
        parser.print_help()
        print("\nMigrated accounts:")
        for acc in listMigratedAccounts(args.migrated):
            print(f"  {acc}")
        sys.exit(1)

    result = reconstructAccount(
        args.account,
        args.migrated,
        args.output,
        args.tweet_id
    )

    if "error" in result:
        print(f"ERROR: {result['error']}")
        sys.exit(1)

    if args.tweet_id:
        print(f"Reconstructed tweet {args.tweet_id} -> {result.get('output')}")
    else:
        print(f"Reconstructed {result['reconstructed']} tweets -> {result['output_dir']}")
        if result["skipped"]:
            print(f"  ({result['skipped']} lines skipped due to errors)")


if __name__ == "__main__":
    main()
