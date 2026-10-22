"""The reminder schedule as a pure function: milestones, then a repeat every N days, continuing after expiry."""

from dataclasses import replace

import pytest

from app.services.notification_rule_service import default_rule
from app.services.reminder_schedule import due_point, latest_point, next_point

RULE = default_rule("vehicle:casco")  # milestones 30, 15, 10, then every day


def rule(**changes):
    return replace(RULE, **changes)


@pytest.mark.parametrize(
    ("days_left", "expected"),
    [
        (45, None),  # first milestone not reached yet
        (31, None),
        (30, 30),
        (29, 30),
        (16, 30),  # between milestones: the latest reached one
        (15, 15),
        (12, 15),
        (10, 10),
        (9, 9),  # daily phase
        (8, 8),
        (7, 7),
        (1, 1),
        (0, 0),  # expiry day
        (-1, -1),  # continues after expiry
        (-30, -30),
    ],
)
def test_daily_repeat_after_milestones(days_left, expected):
    assert latest_point(days_left, RULE) == expected
    assert due_point(days_left, RULE, set()) == expected


def test_sent_points_are_not_sent_again_and_missed_days_send_only_the_latest():
    assert due_point(29, RULE, {30}) is None
    assert due_point(15, RULE, {30}) == 15
    # three days of downtime: 9, 8, 7 were missed, only the latest (7) is due, never a flood
    assert due_point(7, RULE, {30, 15, 10}) == 7
    assert due_point(7, RULE, {30, 15, 10, 7}) is None


@pytest.mark.parametrize(
    ("days_left", "expected"),
    [(11, 15), (10, 10), (9, 10), (8, 8), (7, 8), (6, 6), (1, 2), (0, 0), (-1, 0), (-2, -2)],
)
def test_repeat_every_two_days(days_left, expected):
    assert latest_point(days_left, rule(repeat_every_days=2)) == expected


def test_without_repeat_only_the_milestones_exist():
    no_repeat = rule(repeat_every_days=None)
    assert latest_point(5, no_repeat) == 10
    assert latest_point(-3, no_repeat) == 10
    assert due_point(5, no_repeat, {10}) is None
    assert due_point(-3, no_repeat, {30, 15, 10}) is None
    assert latest_point(31, no_repeat) is None


def test_stop_after_overdue_days_cuts_the_sequence_off():
    capped = rule(stop_after_overdue_days=3)
    assert latest_point(-3, capped) == -3
    assert latest_point(-4, capped) is None
    capped_by_two = rule(stop_after_overdue_days=3, repeat_every_days=2)
    assert latest_point(-3, capped_by_two) == -2  # points ... 2, 0, -2
    assert latest_point(-4, capped_by_two) is None


def test_a_document_created_late_starts_at_the_next_point():
    # created when 12 days were left: the 30 and 15 points were never reached while it existed
    assert latest_point(12, RULE, first_seen_days=12) is None
    assert latest_point(11, RULE, first_seen_days=12) is None
    assert latest_point(10, RULE, first_seen_days=12) == 10
    assert latest_point(9, RULE, first_seen_days=12) == 9
    # created before the first milestone: nothing is skipped
    assert latest_point(30, RULE, first_seen_days=200) == 30
    # created already expired: the next point after creation is the current day
    assert latest_point(-5, RULE, first_seen_days=-5) == -5


def test_renewal_restarts_because_sent_points_belong_to_one_end_date():
    # the dispatcher passes only the deliveries of the current end date; an empty set means "nothing sent yet"
    assert due_point(30, RULE, set()) == 30


@pytest.mark.parametrize(
    ("days_left", "expected"),
    [(40, 30), (30, 15), (20, 15), (15, 10), (12, 10), (10, 9), (7, 6), (0, -1), (-1, -2)],
)
def test_next_point_is_the_next_future_reminder(days_left, expected):
    assert next_point(days_left, RULE) == expected


def test_next_point_edge_cases():
    assert next_point(10, rule(repeat_every_days=None)) is None  # milestones used up
    assert next_point(40, rule(repeat_every_days=None)) == 30
    assert next_point(-2, rule(stop_after_overdue_days=3)) == -3
    assert next_point(-3, rule(stop_after_overdue_days=3)) is None
    assert next_point(40, RULE, first_seen_days=12) == 10  # the earlier points can no longer fire
    assert next_point(9, rule(repeat_every_days=2)) == 8
    assert next_point(8, rule(repeat_every_days=2)) == 6
