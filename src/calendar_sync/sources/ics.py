# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

"""Busy intervals from any calendar that publishes an iCalendar (ICS) feed."""

from __future__ import annotations

import http.client
import urllib.error
import urllib.request
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

import icalendar
import recurring_ical_events

from calendar_sync.intervals import Interval
from calendar_sync.sources import SourceError

USER_AGENT = "calendar-sync"


def normalize_feed_url(url: str) -> str:
    """Outlook and others hand out webcal:// links, which are plain HTTPS underneath."""
    if url[:9].lower() == "webcal://":
        return "https://" + url[9:]
    return url


def fetch_ics(url: str, timeout: float = 30.0) -> bytes:
    """Download a feed. Error messages never include the URL, which is a secret."""
    request = urllib.request.Request(normalize_feed_url(url), headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as e:
        raise SourceError(f"feed returned HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise SourceError(f"could not fetch feed: {e.reason}") from None
    except OSError as e:
        raise SourceError(f"could not fetch feed: {e}") from None
    except http.client.InvalidURL:
        raise SourceError("feed URL is malformed") from None  # its message quotes the URL
    except http.client.HTTPException as e:
        raise SourceError(f"could not fetch feed: {type(e).__name__}") from None
    except ValueError:
        raise SourceError("feed URL is malformed") from None  # e.g. "unknown url type: <url>"


def parse_ics(data: bytes, start: datetime, end: datetime, tz: tzinfo) -> list[Interval]:
    """Busy intervals for events overlapping [start, end).

    Floating times and all-day dates are interpreted in `tz`. Cancelled events and events
    marked free are skipped.
    """
    try:
        calendar = icalendar.Calendar.from_ical(data)
    except Exception as e:  # the parser raises several exception types on garbage input
        raise SourceError(f"feed is not an iCalendar file: {e}") from None
    if calendar.name != "VCALENDAR":
        raise SourceError(f"feed is not an iCalendar file: found {calendar.name}")
    try:
        occurrences = recurring_ical_events.of(calendar).between(
            start.astimezone(tz), end.astimezone(tz)
        )
    except Exception as e:
        raise SourceError(f"could not expand recurring events: {e}") from None
    intervals: list[Interval] = []
    for event in occurrences:
        if _is_free(event):
            continue
        interval = _busy_interval(event, tz)
        if interval is not None:
            intervals.append(interval)
    return intervals


def _is_free(event: Any) -> bool:
    if str(event.get("STATUS", "")).upper() == "CANCELLED":
        return True
    if str(event.get("TRANSP", "")).upper() == "TRANSPARENT":
        return True
    # Outlook marks free time this way, sometimes without setting TRANSP.
    return str(event.get("X-MICROSOFT-CDO-BUSYSTATUS", "")).upper() == "FREE"


def _busy_interval(event: Any, tz: tzinfo) -> Interval | None:
    dtstart = event.get("DTSTART")
    if dtstart is None:
        raise SourceError(f"event {event.get('UID')} has no DTSTART")
    start = _aware(dtstart.dt, tz)
    if event.get("DTEND") is not None:
        end = _aware(event["DTEND"].dt, tz)
    elif event.get("DURATION") is not None:
        end = start + event["DURATION"].dt
    elif not isinstance(dtstart.dt, datetime):
        end = start + timedelta(days=1)  # RFC 5545: an all-day event without an end lasts a day
    else:
        return None  # an instant with no end takes no time
    if end <= start:
        return None
    return Interval(start, end)


def _aware(value: date | datetime, tz: tzinfo) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=tz)
    return datetime.combine(value, time.min, tzinfo=tz)
