"""The reminder schedule as pure functions (no database, no clock).

A rule's scheduled **points** are values of "days left": its milestones (for example 30, 15, 10), then, when a repeat
interval N is set, ``last - N, last - 2N, ...`` without a lower bound, so reminders continue after expiry. An optional
cut-off drops points below ``-stop_after_overdue_days``. Example 30/15/10 with N=1: 30, 15, 10, 9, 8, ... 0, -1, ...

A point is *reached* once ``days_left <= point``. Only the latest reached point is ever due, so days of downtime never
cause a flood. A document first seen with ``first_seen_days`` left can only use points at or below that value: the
earlier points were never reached while it existed.
"""

from collections.abc import Collection
from math import ceil, floor
from typing import Protocol


class ScheduleRule(Protocol):
    milestone_days: tuple[int, ...]
    repeat_every_days: int | None
    stop_after_overdue_days: int | None


def latest_point(days_left: int, rule: ScheduleRule, first_seen_days: int | None = None) -> int | None:
    """The latest schedule point reached at ``days_left``, or None when none applies yet (or any more)."""
    if rule.stop_after_overdue_days is not None and days_left < -rule.stop_after_overdue_days:
        return None
    milestones = sorted(rule.milestone_days, reverse=True)
    candidates = [m for m in milestones if m >= days_left]
    last = milestones[-1]
    if rule.repeat_every_days and days_left < last:
        step = rule.repeat_every_days
        candidates.append(last - step * floor((last - days_left) / step))
    if not candidates:
        return None
    point = min(candidates)
    if first_seen_days is not None and point > first_seen_days:
        return None
    return point


def due_point(
    days_left: int, rule: ScheduleRule, sent_points: Collection[int], first_seen_days: int | None = None
) -> int | None:
    """The point to send now, or None when it was already sent (or nothing is due).

    ``sent_points`` are the successful sends for one document entry (one end date), channel and recipient.
    """
    point = latest_point(days_left, rule, first_seen_days)
    return None if point is None or point in sent_points else point


def next_point(days_left: int, rule: ScheduleRule, first_seen_days: int | None = None) -> int | None:
    """The next point that will fire after today (largest point below ``days_left``), or None."""
    upper = days_left - 1
    if first_seen_days is not None:
        upper = min(upper, first_seen_days)
    milestones = sorted(rule.milestone_days, reverse=True)
    candidates = [m for m in milestones if m <= upper]
    last = milestones[-1]
    if rule.repeat_every_days and upper < last:
        step = rule.repeat_every_days
        candidates.append(last - step * ceil((last - upper) / step))
    if not candidates:
        return None
    point = max(candidates)
    if rule.stop_after_overdue_days is not None and point < -rule.stop_after_overdue_days:
        return None
    return point
