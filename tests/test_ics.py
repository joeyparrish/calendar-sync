# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

import urllib.error
import urllib.request
from datetime import datetime
from email.message import Message
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from calendar_sync.intervals import Interval
from calendar_sync.sources import SourceError
from calendar_sync.sources.ics import fetch_ics, normalize_feed_url, parse_ics
from tests.helpers import utc

FIXTURES = Path(__file__).parent / "fixtures" / "ics"
LA = ZoneInfo("America/Los_Angeles")
START = datetime(2026, 10, 25, tzinfo=LA)
END = datetime(2026, 11, 30, tzinfo=LA)


def parse(name: str) -> list[Interval]:
    return sorted(parse_ics((FIXTURES / name).read_bytes(), START, END, LA))


def test_weekly_recurrence_with_exclusion_move_and_dst_change() -> None:
    assert parse("weekly.ics") == [
        Interval(utc(26, 17), utc(26, 18)),  # 10:00 PDT
        Interval(utc(2, 18, month=11), utc(2, 19, month=11)),  # 10:00 PST, after DST ends
        Interval(utc(17, 22, month=11), utc(17, 23, month=11)),  # moved to Tue 14:00 PST
    ]


def test_misc_event_shapes() -> None:
    assert parse("misc.ics") == [
        Interval(utc(3, 8, month=11), utc(4, 8, month=11)),  # all-day, local midnights
        Interval(utc(6, 17, month=11), utc(6, 18, month=11)),  # UTC
        Interval(utc(10, 17, month=11), utc(10, 18, month=11)),  # floating 09:00 local
        Interval(utc(11, 20, month=11), utc(11, 20, 30, month=11)),  # DURATION
    ]


def test_outlook_windows_time_zone_and_busy_status() -> None:
    assert parse("outlook.ics") == [
        Interval(utc(4, 21, month=11), utc(4, 22, month=11)),  # busy
        Interval(utc(5, 21, month=11), utc(5, 22, month=11)),  # tentative counts as busy
    ]


def test_empty_calendar_is_genuinely_free() -> None:
    assert parse("empty.ics") == []


def test_html_page_is_an_error() -> None:
    with pytest.raises(SourceError, match="not an iCalendar"):
        parse("not_a_calendar.html")


def test_empty_body_is_an_error() -> None:
    with pytest.raises(SourceError):
        parse_ics(b"", START, END, LA)


def test_webcal_urls_are_fetched_over_https() -> None:
    assert (
        normalize_feed_url("webcal://outlook.office365.com/owa/calendar/x/reachcalendar.ics")
        == "https://outlook.office365.com/owa/calendar/x/reachcalendar.ics"
    )
    assert normalize_feed_url("WEBCAL://example.com/a.ics") == "https://example.com/a.ics"


def test_https_urls_are_unchanged() -> None:
    assert normalize_feed_url("https://example.com/a.ics") == "https://example.com/a.ics"


def test_fetch_failure_raises_source_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    with pytest.raises(SourceError, match="connection refused"):
        fetch_ics("https://example.com/feed.ics")


def test_fetch_http_error_does_not_reveal_url(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://calendar.example.com/secret-token-123/calendar.ics"

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise urllib.error.HTTPError(url, 404, "Not Found", Message(), None)

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    with pytest.raises(SourceError) as error:
        fetch_ics(url)
    assert "404" in str(error.value)
    assert "secret-token-123" not in str(error.value)


def test_malformed_url_error_does_not_reveal_url() -> None:
    # http.client rejects a space in the path with InvalidURL, whose message quotes the URL.
    # It is raised before any connection is made.
    with pytest.raises(SourceError) as error:
        fetch_ics("https://127.0.0.1:9/secret-token-123 /calendar.ics")
    assert "secret-token-123" not in str(error.value)
