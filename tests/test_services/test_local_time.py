"""One shared "today": the business (Europe/Sofia) calendar date, storage stays UTC."""

from datetime import UTC, date, datetime

from app.services.validity import local_today, to_local


def test_today_rolls_over_at_local_midnight_in_winter():
    assert local_today(datetime(2026, 1, 15, 21, 59, tzinfo=UTC)) == date(2026, 1, 15)  # 23:59 in Sofia (UTC+2)
    assert local_today(datetime(2026, 1, 15, 22, 0, tzinfo=UTC)) == date(2026, 1, 16)


def test_today_rolls_over_at_local_midnight_in_summer():
    assert local_today(datetime(2026, 7, 15, 20, 59, tzinfo=UTC)) == date(2026, 7, 15)  # 23:59 in Sofia (UTC+3)
    assert local_today(datetime(2026, 7, 15, 21, 0, tzinfo=UTC)) == date(2026, 7, 16)


def test_dst_change_days():
    # Spring forward 2026-03-29 03:00 -> 04:00 local; clocks go from UTC+2 to UTC+3.
    assert local_today(datetime(2026, 3, 28, 21, 59, tzinfo=UTC)) == date(2026, 3, 28)
    assert local_today(datetime(2026, 3, 28, 22, 0, tzinfo=UTC)) == date(2026, 3, 29)
    assert local_today(datetime(2026, 3, 29, 20, 59, tzinfo=UTC)) == date(2026, 3, 29)
    assert local_today(datetime(2026, 3, 29, 21, 0, tzinfo=UTC)) == date(2026, 3, 30)
    # Fall back 2026-10-25 04:00 -> 03:00 local; clocks go from UTC+3 to UTC+2.
    assert local_today(datetime(2026, 10, 24, 20, 59, tzinfo=UTC)) == date(2026, 10, 24)
    assert local_today(datetime(2026, 10, 24, 21, 0, tzinfo=UTC)) == date(2026, 10, 25)
    assert local_today(datetime(2026, 10, 25, 21, 59, tzinfo=UTC)) == date(2026, 10, 25)
    assert local_today(datetime(2026, 10, 25, 22, 0, tzinfo=UTC)) == date(2026, 10, 26)


def test_to_local_handles_aware_and_naive_values():
    aware = to_local(datetime(2026, 7, 1, 10, 0, tzinfo=UTC))
    naive = to_local(datetime(2026, 7, 1, 10, 0))
    assert aware == naive and aware.hour == 13 and aware.utcoffset().total_seconds() == 3 * 3600
    assert to_local(datetime(2026, 1, 1, 10, 0, tzinfo=UTC)).hour == 12
