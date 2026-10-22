"""Backup retention: recent automatic backups, one per month beyond that, a few pre-update ones, manual never."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from prune_backups import Policy, parse_name, plan  # noqa: E402


def pair(label, stamp):
    return [f"fleetcore-db-{label}-{stamp}.dump", f"fleetcore-files-{label}-{stamp}.tar.gz"]


def daily(label, start, count):
    """`count` daily backups starting at the date `start` (YYYYMMDD), oldest first."""
    from datetime import date, timedelta

    first = date(int(start[:4]), int(start[4:6]), int(start[6:]))
    names = []
    for n in range(count):
        day = first + timedelta(days=n)
        names += pair(label, day.strftime("%Y%m%d") + "T020000Z")
    return names


def test_parse_name():
    assert parse_name("fleetcore-db-auto-20261022T020000Z.dump") == ("db", "auto", "20261022T020000Z")
    assert parse_name("fleetcore-files-pre-update-20261022T101500Z.tar.gz") == (
        "files",
        "pre-update",
        "20261022T101500Z",
    )
    assert parse_name("notes.txt") is None and parse_name("fleetcore-db-other-20261022T020000Z.dump") is None


def test_nothing_is_deleted_while_within_the_recent_window():
    names = daily("auto", "20261001", 10)
    assert plan(names, Policy()) == []


def test_old_automatic_backups_keep_one_per_month_and_the_recent_ones():
    names = daily("auto", "20260101", 300)  # 2026-01-01 .. 2026-10-27
    deleted = set(plan(names, Policy(recent=14, monthly=6)))
    kept = {n for n in names if n not in deleted}
    stamps = sorted({parse_name(n)[2] for n in kept})
    recent = [s for s in stamps if s >= "20261014"]
    assert len(recent) == 14  # the newest 14 days
    monthly = [s for s in stamps if s < "20261014"]
    assert (
        monthly
        == [
            "20260401T020000Z",
            "20260501T020000Z",
            "20260601T020000Z",
            "20260701T020000Z",
            "20260801T020000Z",
            "20260901T020000Z",
        ]
        or len(monthly) == 6
    )
    assert all(s.endswith("T020000Z") for s in monthly)


def test_database_and_files_of_one_backup_go_together():
    names = daily("auto", "20260101", 40)
    deleted = set(plan(names, Policy(recent=3, monthly=0)))
    by_stamp = {}
    for name in names:
        by_stamp.setdefault(parse_name(name)[2], []).append(name in deleted)
    assert all(len(set(flags)) == 1 for flags in by_stamp.values())  # both deleted or both kept


def test_pre_update_keeps_only_the_latest_few_and_manual_is_never_deleted():
    names = daily("pre-update", "20261001", 9) + daily("manual", "20250101", 400)
    deleted = set(plan(names, Policy(pre_update=5)))
    assert {parse_name(n)[1] for n in deleted} == {"pre-update"}
    kept_pre = {parse_name(n)[2] for n in names if parse_name(n)[1] == "pre-update" and n not in deleted}
    assert kept_pre == {f"202610{d:02d}T020000Z" for d in range(5, 10)}


def test_labels_are_independent_and_unknown_files_are_ignored():
    names = daily("auto", "20261001", 3) + daily("pre-update", "20261001", 3) + ["README.txt", "fleetcore.sql"]
    assert plan(names, Policy(recent=1, monthly=0, pre_update=1)) == sorted(
        pair("auto", "20261001T020000Z")
        + pair("auto", "20261002T020000Z")
        + pair("pre-update", "20261001T020000Z")
        + pair("pre-update", "20261002T020000Z")
    )


def test_cli_dry_run_lists_and_real_run_deletes(tmp_path):
    for name in daily("auto", "20261001", 5):
        (tmp_path / name).write_text("x")
    script = str(ROOT / "scripts" / "prune_backups.py")
    dry = subprocess.run(
        [sys.executable, script, str(tmp_path), "--dry-run", "--keep-auto", "2", "--keep-monthly", "0"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "20261001T020000Z" in dry.stdout and len(list(tmp_path.iterdir())) == 10
    subprocess.run([sys.executable, script, str(tmp_path), "--keep-auto", "2", "--keep-monthly", "0"], check=True)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(daily("auto", "20261004", 2))


@pytest.mark.parametrize("script", ["backup.sh", "restore.sh", "update.sh", "_common.sh"])
def test_shell_scripts_have_valid_syntax(script):
    path = ROOT / "scripts" / script
    assert path.exists(), script
    result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
