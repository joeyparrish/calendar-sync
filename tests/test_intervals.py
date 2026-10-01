# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from calendar_sync.intervals import (
    Interval,
    Window,
    compute_window,
    merge_intervals,
    owned_intervals,
)
from tests.helpers import utc

LA = ZoneInfo("America/Los_Angeles")


def test_interval_normalizes_to_utc() -> None:
    interval = Interval(datetime(2026, 10, 1, 9, tzinfo=LA), datetime(2026, 10, 1, 10, tzinfo=LA))
    assert interval.start == utc(1, 16)
    assert interval.start.tzinfo is UTC
    assert interval.end.tzinfo is UTC


def test_equal_instants_in_different_zones_are_equal() -> None:
    local = Interval(datetime(2026, 10, 1, 9, tzinfo=LA), datetime(2026, 10, 1, 10, tzinfo=LA))
    assert local == Interval(utc(1, 16), utc(1, 17))
    assert hash(local) == hash(Interval(utc(1, 16), utc(1, 17)))


def test_interval_rejects_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Interval(datetime(2026, 10, 1, 9), datetime(2026, 10, 1, 10))


def test_interval_rejects_empty_or_backwards() -> None:
    with pytest.raises(ValueError, match="not after"):
        Interval(utc(1, 9), utc(1, 9))
    with pytest.raises(ValueError, match="not after"):
        Interval(utc(1, 10), utc(1, 9))


def test_merge_overlapping_touching_and_nested() -> None:
    merged = merge_intervals(
        [
            Interval(utc(1, 13), utc(1, 14)),
            Interval(utc(1, 9), utc(1, 10)),
            Interval(utc(1, 10), utc(1, 11)),
            Interval(utc(1, 9, 30), utc(1, 9, 45)),
        ]
    )
    assert merged == [Interval(utc(1, 9), utc(1, 11)), Interval(utc(1, 13), utc(1, 14))]


def test_merge_empty() -> None:
    assert merge_intervals([]) == []


def test_window_starts_at_local_midnight() -> None:
    window = compute_window(datetime(2026, 10, 1, 15, 30, tzinfo=LA), LA, weeks=8)
    assert window.start == utc(1, 7)
    assert window.end == utc(1, 7) + timedelta(weeks=8)


def test_window_uses_local_date_not_utc_date() -> None:
    # 05:00 UTC on Oct 2 is 22:00 on Oct 1 in Los Angeles.
    window = compute_window(utc(2, 5), LA, weeks=8)
    assert window.start == utc(1, 7)


def test_fetch_start_is_one_day_early() -> None:
    window = compute_window(datetime(2026, 10, 1, 15, 30, tzinfo=LA), LA, weeks=8)
    assert window.fetch_start == utc(30, 7, month=9)


def test_owns_is_half_open() -> None:
    window = Window(utc(1, 7), utc(2, 7))
    assert window.owns(utc(1, 7))
    assert not window.owns(utc(2, 7))
    assert not window.owns(utc(1, 6, 59))


def test_owned_intervals_drop_those_starting_outside_window() -> None:
    window = Window(utc(1, 7), utc(2, 7))
    intervals = [
        Interval(utc(1, 5), utc(1, 8)),  # overnight meeting that began before the window
        Interval(utc(1, 9), utc(1, 10)),
        Interval(utc(2, 7), utc(2, 8)),  # starts exactly at the window end
    ]
    assert owned_intervals(intervals, window) == [Interval(utc(1, 9), utc(1, 10))]
