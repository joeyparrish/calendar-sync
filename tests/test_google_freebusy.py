# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

import json
from pathlib import Path

import pytest

from calendar_sync.intervals import Interval
from calendar_sync.sources import SourceError
from calendar_sync.sources.google_freebusy import parse_freebusy_response
from tests.helpers import utc

ADDRESS = "someone@example.com"
NOT_SHARED_FIXTURE = Path(__file__).parent / "fixtures" / "freebusy_not_shared.json"


def test_busy_intervals_are_parsed() -> None:
    response = {
        "kind": "calendar#freeBusy",
        "calendars": {
            ADDRESS: {
                "busy": [
                    {"start": "2026-10-01T16:00:00Z", "end": "2026-10-01T17:00:00Z"},
                    {"start": "2026-10-01T10:30:00-07:00", "end": "2026-10-01T11:00:00-07:00"},
                ]
            }
        },
    }
    assert parse_freebusy_response(response, ADDRESS) == [
        Interval(utc(1, 16), utc(1, 17)),
        Interval(utc(1, 17, 30), utc(1, 18)),
    ]


def test_empty_busy_list_means_free() -> None:
    assert parse_freebusy_response({"calendars": {ADDRESS: {"busy": []}}}, ADDRESS) == []


def test_calendar_error_inside_success_response_fails() -> None:
    response = {
        "calendars": {ADDRESS: {"errors": [{"domain": "global", "reason": "notFound"}], "busy": []}}
    }
    with pytest.raises(SourceError, match="notFound"):
        parse_freebusy_response(response, ADDRESS)


def test_top_level_error_fails() -> None:
    response = {"errors": [{"domain": "global", "reason": "backendError"}], "calendars": {}}
    with pytest.raises(SourceError, match="backendError"):
        parse_freebusy_response(response, ADDRESS)


def test_missing_calendar_fails() -> None:
    response = {"calendars": {"other@example.com": {"busy": []}}}
    with pytest.raises(SourceError, match="does not mention"):
        parse_freebusy_response(response, ADDRESS)


def test_calendar_lookup_ignores_case() -> None:
    response = {"calendars": {"Someone@Example.com": {"busy": []}}}
    assert parse_freebusy_response(response, ADDRESS) == []


def test_missing_busy_field_fails() -> None:
    with pytest.raises(SourceError, match="no busy list"):
        parse_freebusy_response({"calendars": {ADDRESS: {}}}, ADDRESS)


def test_malformed_busy_entry_fails() -> None:
    response = {"calendars": {ADDRESS: {"busy": [{"start": "soon", "end": "later"}]}}}
    with pytest.raises(SourceError, match="malformed"):
        parse_freebusy_response(response, ADDRESS)


@pytest.mark.skipif(not NOT_SHARED_FIXTURE.exists(), reason="spike returned no error response")
def test_real_not_shared_response_fails() -> None:
    response = json.loads(NOT_SHARED_FIXTURE.read_text())
    with pytest.raises(SourceError):
        parse_freebusy_response(response, ADDRESS)
