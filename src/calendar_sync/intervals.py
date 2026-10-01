# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

"""Busy intervals and the sync window."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, tzinfo

DEFAULT_WEEKS = 8


@dataclass(frozen=True, order=True)
class Interval:
    """A half-open busy interval [start, end), stored in UTC."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Interval datetimes must be timezone-aware")
        if self.end <= self.start:
            raise ValueError(f"Interval end {self.end} is not after start {self.start}")
        object.__setattr__(self, "start", self.start.astimezone(UTC))
        object.__setattr__(self, "end", self.end.astimezone(UTC))


@dataclass(frozen=True)
class Window:
    """The span of time a sync run manages blocks in."""

    start: datetime
    end: datetime

    @property
    def fetch_start(self) -> datetime:
        """Sources fetch from a day early so that any clipping they do lands outside the window."""
        return self.start - timedelta(days=1)

    def owns(self, start: datetime) -> bool:
        """Whether an interval or block starting at `start` is managed by this window."""
        return self.start <= start < self.end


def compute_window(now: datetime, tz: tzinfo, weeks: int) -> Window:
    """From local midnight today, so an in-progress meeting is never re-clipped, for `weeks`."""
    midnight = datetime.combine(now.astimezone(tz).date(), time.min, tzinfo=tz).astimezone(UTC)
    return Window(start=midnight, end=midnight + timedelta(weeks=weeks))


def merge_intervals(intervals: Iterable[Interval]) -> list[Interval]:
    """Sort intervals and merge any that overlap or touch."""
    merged: list[Interval] = []
    for interval in sorted(intervals):
        if merged and interval.start <= merged[-1].end:
            last = merged[-1]
            if interval.end > last.end:
                merged[-1] = Interval(last.start, interval.end)
        else:
            merged.append(interval)
    return merged


def owned_intervals(intervals: Iterable[Interval], window: Window) -> list[Interval]:
    """Merged intervals whose start falls inside the window.

    Intervals that began earlier were handled by the runs of earlier days.
    """
    return [interval for interval in merge_intervals(intervals) if window.owns(interval.start)]
