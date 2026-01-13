#!/usr/bin/env python3
"""
Validate Twitter archive migration from JSON to JSONL.gz.

Checks:
1. Line count in JSONL.gz matches original JSON file count
2. Digest generated from JSONL.gz matches existing (tweets only, frontmatter may differ)
3. Can reconstruct original JSON files from JSONL

Usage:
    python validate.py <account>  # Validate one account
    python validate.py --all      # Validate all migrated accounts
"""

import sys
import json
import gzip
import tempfile
from pathlib import Path

ARCHIVE_DIR = Path.home() / "Archive/twitter/archive"
MIGRATED_DIR = Path("/tmp/archivage")
DIGESTS_DIR = Path.home() / "Archive/twitter/digests"


def countJsonFiles(account: str) -> int:
    """Count original JSON files for an account."""
    account_dir = ARCHIVE_DIR / account
    if not account_dir.exists():
        return -1
    return len(list(account_dir.glob("*.json")))


def countJsonlLines(jsonl_gz: Path) -> int:
    """Count lines in JSONL.gz file."""
    count = 0
    with gzip.open(jsonl_gz, 'rt', encoding='utf-8') as f:
        for _ in f:
            count += 1
    return count


def validateLineCount(account: str, migrated_dir: Path = MIGRATED_DIR) -> dict:
    """Check that JSONL line count matches JSON file count."""
    jsonl_gz = migrated_dir / f"{account}.jsonl.gz"
    if not jsonl_gz.exists():
        return {"valid": False, "error": f"JSONL.gz not found: {jsonl_gz}"}

    original_count = countJsonFiles(account)
    if original_count < 0:
        return {"valid": False, "error": f"Original directory not found"}

    migrated_count = countJsonlLines(jsonl_gz)

    return {
        "valid": original_count == migrated_count,
        "original": original_count,
        "migrated": migrated_count
    }


def validateDigest(account: str, migrated_dir: Path = MIGRATED_DIR) -> dict:
    """Check that digest generated from JSONL matches existing (tweets only)."""
    from digest import generateDigest

    existing_digest = DIGESTS_DIR / f"{account}.txt"
    if not existing_digest.exists():
        return {"valid": None, "reason": "No existing digest to compare"}

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_output = Path(tmpdir)
        result = generateDigest(account, migrated_dir, tmp_output)
        if "error" in result:
            return {"valid": False, "error": result["error"]}

        new_digest = tmp_output / f"{account}.txt"

        # Compare tweets only (skip frontmatter which has changing metadata)
        def getTweets(content: str) -> str:
            parts = content.split('---')
            return parts[2].strip() if len(parts) > 2 else content.strip()

        new_tweets = getTweets(new_digest.read_text(encoding="utf-8"))
        old_tweets = getTweets(existing_digest.read_text(encoding="utf-8"))

        return {
            "valid": new_tweets == old_tweets,
            "new_length": len(new_tweets),
            "old_length": len(old_tweets)
        }


def validateReconstruction(account: str, migrated_dir: Path = MIGRATED_DIR,
                           sample_size: int = 5) -> dict:
    """Check that we can reconstruct original JSON from JSONL."""
    jsonl_gz = migrated_dir / f"{account}.jsonl.gz"
    if not jsonl_gz.exists():
        return {"valid": False, "error": "JSONL.gz not found"}

    account_dir = ARCHIVE_DIR / account
    if not account_dir.exists():
        return {"valid": False, "error": "Original directory not found"}

    # Read a sample of lines from JSONL
    samples = []
    with gzip.open(jsonl_gz, 'rt', encoding='utf-8') as f:
        for i, line in enumerate(f):
            if i < sample_size:
                samples.append(json.loads(line.strip()))
            else:
                break

    # For each sample, find and compare with original
    matches = 0
    for data in samples:
        tweet_id = data.get('tweet_id')
        if not tweet_id:
            continue

        # Find original file
        original_files = list(account_dir.glob(f"*{tweet_id}.json"))
        if not original_files:
            continue

        original = json.loads(original_files[0].read_text(encoding='utf-8'))

        # Compare (should be identical)
        if data == original:
            matches += 1

    return {
        "valid": matches == len(samples),
        "matched": matches,
        "sampled": len(samples)
    }


def validateAccount(account: str, migrated_dir: Path = MIGRATED_DIR) -> dict:
    """Run all validations for an account."""
    results = {
        "account": account,
        "line_count": validateLineCount(account, migrated_dir),
        "digest": validateDigest(account, migrated_dir),
        "reconstruction": validateReconstruction(account, migrated_dir)
    }

    # Overall validity
    results["valid"] = (
        results["line_count"].get("valid", False) and
        results["digest"].get("valid") in (True, None) and  # None = no existing digest (OK)
        results["reconstruction"].get("valid", False)
    )

    return results


def listMigratedAccounts(migrated_dir: Path = MIGRATED_DIR) -> list[str]:
    """List accounts that have been migrated."""
    return sorted([
        p.stem.replace('.jsonl', '')
        for p in migrated_dir.glob("*.jsonl.gz")
    ])


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Validate JSONL.gz migration")
    parser.add_argument("account", nargs="?", help="Account to validate")
    parser.add_argument("--all", action="store_true", help="Validate all migrated accounts")
    parser.add_argument("--migrated", "-m", type=Path, default=MIGRATED_DIR,
                        help=f"Migrated files directory (default: {MIGRATED_DIR})")
    args = parser.parse_args()

    if not args.account and not args.all:
        parser.print_help()
        print("\nMigrated accounts:")
        for acc in listMigratedAccounts(args.migrated):
            print(f"  {acc}")
        sys.exit(1)

    accounts = listMigratedAccounts(args.migrated) if args.all else [args.account]

    all_valid = True
    for account in accounts:
        results = validateAccount(account, args.migrated)

        status = "✓" if results["valid"] else "✗"
        print(f"{status} {account}")

        # Line count
        lc = results["line_count"]
        if lc.get("valid"):
            print(f"  Line count: {lc['original']} ✓")
        elif "error" in lc:
            print(f"  Line count: ERROR - {lc['error']}")
        else:
            print(f"  Line count: {lc['original']} vs {lc['migrated']} ✗")

        # Digest
        dg = results["digest"]
        if dg.get("valid") is True:
            print(f"  Digest: tweets match ✓")
        elif dg.get("valid") is None:
            print(f"  Digest: {dg.get('reason', 'skipped')}")
        elif "error" in dg:
            print(f"  Digest: ERROR - {dg['error']}")
        else:
            print(f"  Digest: differs ({dg['new_length']} vs {dg['old_length']}) ✗")

        # Reconstruction
        rc = results["reconstruction"]
        if rc.get("valid"):
            print(f"  Reconstruction: {rc['matched']}/{rc['sampled']} samples ✓")
        elif "error" in rc:
            print(f"  Reconstruction: ERROR - {rc['error']}")
        else:
            print(f"  Reconstruction: {rc['matched']}/{rc['sampled']} samples ✗")

        if not results["valid"]:
            all_valid = False

        print()

    sys.exit(0 if all_valid else 1)


if __name__ == "__main__":
    main()
