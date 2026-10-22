#!/usr/bin/env python3
"""Delete old backups according to the retention policy (standard library only).

Backups are written by ``scripts/backup.sh`` as pairs that share a timestamp and label::

    fleetcore-db-<label>-<YYYYmmddTHHMMSSZ>.dump         PostgreSQL dump
    fleetcore-files-<label>-<YYYYmmddTHHMMSSZ>.tar.gz    uploaded files (MinIO)

Policy (per label):

* ``auto``        scheduled backups: keep the newest ``--keep-auto`` (default 14), plus the oldest backup of each
                  of the last ``--keep-monthly`` (default 6) earlier months.
* ``pre-update``  taken before each update: keep the newest ``--keep-pre-update`` (default 5).
* ``manual``      never deleted.

The database dump and the files archive of one backup are always kept or deleted together. Files that do not
match the naming scheme are never touched.

    python3 scripts/prune_backups.py backups/            # prune
    python3 scripts/prune_backups.py backups/ --dry-run  # only list what would be deleted
"""

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

NAME = re.compile(r"^fleetcore-(db|files)-(auto|pre-update|manual)-(\d{8}T\d{6}Z)\.(?:dump|tar\.gz)$")


@dataclass(frozen=True)
class Policy:
    recent: int = 14
    monthly: int = 6
    pre_update: int = 5


def parse_name(name: str) -> tuple[str, str, str] | None:
    """(kind, label, stamp) of a backup file name, or None for any other file."""
    match = NAME.match(name)
    return (match.group(1), match.group(2), match.group(3)) if match else None


def _stamps_to_keep(stamps: list[str], recent: int, monthly: int) -> set[str]:
    newest_first = sorted(stamps, reverse=True)
    keep = set(newest_first[:recent])
    older = newest_first[recent:]
    months_kept: list[str] = []
    for stamp in reversed(older):  # oldest first: the first backup of a month represents that month
        month = stamp[:6]
        if month not in months_kept:
            months_kept.append(month)
    for month in months_kept[-monthly:] if monthly else []:
        keep.add(min(s for s in older if s[:6] == month))
    return keep


def plan(names: list[str], policy: Policy) -> list[str]:
    """The file names to delete (sorted)."""
    by_label: dict[str, dict[str, list[str]]] = {}
    for name in names:
        parsed = parse_name(name)
        if parsed:
            _, label, stamp = parsed
            by_label.setdefault(label, {}).setdefault(stamp, []).append(name)
    doomed: list[str] = []
    for label, sets in by_label.items():
        if label == "auto":
            keep = _stamps_to_keep(list(sets), policy.recent, policy.monthly)
        elif label == "pre-update":
            keep = _stamps_to_keep(list(sets), policy.pre_update, 0)
        else:
            continue  # manual backups are never deleted
        for stamp, files in sets.items():
            if stamp not in keep:
                doomed += files
    return sorted(doomed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="only list what would be deleted")
    parser.add_argument("--keep-auto", type=int, default=14)
    parser.add_argument("--keep-monthly", type=int, default=6)
    parser.add_argument("--keep-pre-update", type=int, default=5)
    args = parser.parse_args(argv)
    if not args.directory.is_dir():
        print(f"Backup directory not found: {args.directory}", file=sys.stderr)
        return 1
    policy = Policy(args.keep_auto, args.keep_monthly, args.keep_pre_update)
    doomed = plan([p.name for p in args.directory.iterdir() if p.is_file()], policy)
    for name in doomed:
        print(("would delete " if args.dry_run else "deleting ") + name)
        if not args.dry_run:
            (args.directory / name).unlink()
    if not doomed:
        print("nothing to delete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
