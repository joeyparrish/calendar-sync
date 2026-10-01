# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

from collections import deque
from typing import Any

import httplib2
import pytest
from googleapiclient.errors import HttpError

from calendar_sync.calendar_client import (
    CalendarClient,
    CalendarInfo,
    GoogleCalendarClient,
    event_color_id,
    parse_color,
)
from calendar_sync.intervals import Interval
from calendar_sync.reconcile import Block
from tests.fakes import FakeCalendarClient
from tests.helpers import utc

CAL = "primary"


class StubRequest:
    def __init__(self, result: Any) -> None:
        self._result = result
        self.num_retries: int | None = None

    def execute(self, num_retries: int = 0) -> Any:
        self.num_retries = num_retries
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class StubCollection:
    """Records calls and replays queued results, standing in for a googleapiclient resource."""

    def __init__(self, results: dict[str, Any]) -> None:
        self._results: dict[str, deque[Any]] = {k: deque(v) for k, v in results.items()}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.requests: list[StubRequest] = []

    def _call(self, method: str, kwargs: dict[str, Any]) -> StubRequest:
        self.calls.append((method, kwargs))
        request = StubRequest(self._results[method].popleft())
        self.requests.append(request)
        return request

    def query(self, **kwargs: Any) -> StubRequest:
        return self._call("query", kwargs)

    def insert(self, **kwargs: Any) -> StubRequest:
        return self._call("insert", kwargs)

    def delete(self, **kwargs: Any) -> StubRequest:
        return self._call("delete", kwargs)

    def patch(self, **kwargs: Any) -> StubRequest:
        return self._call("patch", kwargs)

    def list(self, **kwargs: Any) -> StubRequest:
        return self._call("list", kwargs)


class StubService:
    def __init__(self, **results: Any) -> None:
        self.collection = StubCollection(results)

    def events(self) -> StubCollection:
        return self.collection

    def freebusy(self) -> StubCollection:
        return self.collection

    def calendarList(self) -> StubCollection:  # noqa: N802 (googleapiclient's name)
        return self.collection


def http_error(status: int) -> HttpError:
    return HttpError(httplib2.Response({"status": status}), b"{}")


def event_json(event_id: str, start: str, end: str) -> dict[str, Any]:
    return {"id": event_id, "start": {"dateTime": start}, "end": {"dateTime": end}}


def test_clients_satisfy_protocol() -> None:
    clients: list[CalendarClient] = [GoogleCalendarClient(StubService()), FakeCalendarClient()]
    assert len(clients) == 2


def test_freebusy_query_sends_window_and_address() -> None:
    service = StubService(query=[{"calendars": {}}])
    result = GoogleCalendarClient(service).query_freebusy(
        "someone@example.com", utc(1, 7), utc(2, 7)
    )
    assert result == {"calendars": {}}
    method, kwargs = service.collection.calls[0]
    assert method == "query"
    assert kwargs["body"] == {
        "timeMin": "2026-10-01T07:00:00Z",
        "timeMax": "2026-10-02T07:00:00Z",
        "items": [{"id": "someone@example.com"}],
    }


def test_list_blocks_filters_by_tags_and_follows_pages() -> None:
    page1 = {
        "items": [event_json("e1", "2026-10-01T09:00:00Z", "2026-10-01T10:00:00Z")],
        "nextPageToken": "p2",
    }
    page2 = {"items": [event_json("e2", "2026-10-01T03:00:00-07:00", "2026-10-01T04:00:00-07:00")]}
    service = StubService(list=[page1, page2])
    blocks = GoogleCalendarClient(service).list_blocks(CAL, "src1", utc(1, 7), utc(2, 7))
    assert blocks == [
        Block("e1", Interval(utc(1, 9), utc(1, 10))),
        Block("e2", Interval(utc(1, 10), utc(1, 11))),
    ]
    first = service.collection.calls[0][1]
    assert first["calendarId"] == CAL
    assert first["privateExtendedProperty"] == [
        "calendarSyncTool=calendar-sync",
        "calendarSyncSource=src1",
    ]
    assert first["singleEvents"] is True
    assert first["timeMin"] == "2026-10-01T07:00:00Z"
    assert first["timeMax"] == "2026-10-02T07:00:00Z"
    assert first["pageToken"] is None
    assert service.collection.calls[1][1]["pageToken"] == "p2"


def test_list_blocks_without_time_max() -> None:
    service = StubService(list=[{"items": []}])
    assert GoogleCalendarClient(service).list_blocks(CAL, "src1", utc(1, 7), None) == []
    assert service.collection.calls[0][1]["timeMax"] is None


def test_list_blocks_reads_all_day_events() -> None:
    # A user may edit a block into an all-day event; it must still parse so it can be deleted.
    service = StubService(
        list=[
            {
                "items": [
                    {"id": "e1", "start": {"date": "2026-10-01"}, "end": {"date": "2026-10-02"}}
                ]
            }
        ]
    )
    blocks = GoogleCalendarClient(service).list_blocks(CAL, "src1", utc(1, 0), None)
    assert blocks == [Block("e1", Interval(utc(1, 0), utc(2, 0)))]


def test_insert_block_creates_private_busy_placeholder() -> None:
    service = StubService(insert=[{"id": "new1"}])
    event_id = GoogleCalendarClient(service).insert_block(
        CAL, "src1", "Google", Interval(utc(1, 9), utc(1, 10))
    )
    assert event_id == "new1"
    kwargs = service.collection.calls[0][1]
    assert kwargs["calendarId"] == CAL
    assert kwargs["sendUpdates"] == "none"
    assert kwargs["body"] == {
        "summary": "busy (Google)",
        "start": {"dateTime": "2026-10-01T09:00:00Z"},
        "end": {"dateTime": "2026-10-01T10:00:00Z"},
        "visibility": "private",
        "transparency": "opaque",
        "reminders": {"useDefault": False},
        "extendedProperties": {
            "private": {"calendarSyncTool": "calendar-sync", "calendarSyncSource": "src1"}
        },
    }


def test_requests_retry_transient_errors() -> None:
    service = StubService(insert=[{"id": "new1"}])
    GoogleCalendarClient(service).insert_block(
        CAL, "src1", "Google", Interval(utc(1, 9), utc(1, 10))
    )
    assert service.collection.requests[0].num_retries == 3


def test_delete_block() -> None:
    service = StubService(delete=[""])
    GoogleCalendarClient(service).delete_block(CAL, "e1")
    assert service.collection.calls == [
        ("delete", {"calendarId": CAL, "eventId": "e1", "sendUpdates": "none"})
    ]


@pytest.mark.parametrize("status", [404, 410])
def test_delete_of_already_deleted_event_succeeds(status: int) -> None:
    service = StubService(delete=[http_error(status)])
    GoogleCalendarClient(service).delete_block(CAL, "gone")


def test_delete_propagates_other_errors() -> None:
    service = StubService(delete=[http_error(403)])
    with pytest.raises(HttpError):
        GoogleCalendarClient(service).delete_block(CAL, "e1")


def test_list_blocks_reads_title_and_color() -> None:
    item = event_json("e1", "2026-10-01T09:00:00Z", "2026-10-01T10:00:00Z")
    item.update(summary="busy (Google)", colorId="8")
    service = StubService(list=[{"items": [item]}])
    [block] = GoogleCalendarClient(service).list_blocks(CAL, "src1", utc(1, 0), None)
    assert block == Block("e1", Interval(utc(1, 9), utc(1, 10)), "busy (Google)", "8")


def test_insert_block_with_a_color() -> None:
    service = StubService(insert=[{"id": "new1"}])
    GoogleCalendarClient(service).insert_block(
        CAL, "src1", "Google", Interval(utc(1, 9), utc(1, 10)), color_id="8"
    )
    assert service.collection.calls[0][1]["body"]["colorId"] == "8"


def test_update_block_patches_title_and_color() -> None:
    service = StubService(patch=[{"id": "e1"}])
    GoogleCalendarClient(service).update_block(CAL, "e1", "busy (Work)", "11")
    assert service.collection.calls == [
        (
            "patch",
            {
                "calendarId": CAL,
                "eventId": "e1",
                "body": {"summary": "busy (Work)", "colorId": "11"},
                "sendUpdates": "none",
            },
        )
    ]


def test_update_block_can_clear_the_color() -> None:
    service = StubService(patch=[{"id": "e1"}])
    GoogleCalendarClient(service).update_block(CAL, "e1", "busy (Work)", None)
    assert service.collection.calls[0][1]["body"] == {"summary": "busy (Work)", "colorId": None}


def test_color_names_are_case_insensitive() -> None:
    assert parse_color("graphite") == "Graphite"
    assert event_color_id("Graphite") == "8"
    assert event_color_id("Tomato") == "11"
    assert event_color_id("Lavender") == "1"


def test_default_color_means_none() -> None:
    assert parse_color("Default") is None
    assert event_color_id(None) is None


def test_unknown_color_lists_the_choices() -> None:
    with pytest.raises(ValueError, match="Graphite"):
        parse_color("chartreuse")


def test_list_calendars_follows_pages() -> None:
    page1 = {
        "items": [
            {
                "id": "owner@example.com",
                "summary": "Owner",
                "accessRole": "owner",
                "primary": True,
                "timeZone": "America/Los_Angeles",
            }
        ],
        "nextPageToken": "p2",
    }
    page2 = {
        "items": [
            {
                "id": "team@group.calendar.google.com",
                "summary": "Team",
                "accessRole": "reader",
                "timeZone": "UTC",
            }
        ]
    }
    service = StubService(list=[page1, page2])
    calendars = GoogleCalendarClient(service).list_calendars()
    assert calendars == [
        CalendarInfo(
            "owner@example.com", "Owner", "owner", primary=True, time_zone="America/Los_Angeles"
        ),
        CalendarInfo(
            "team@group.calendar.google.com", "Team", "reader", primary=False, time_zone="UTC"
        ),
    ]
    assert service.collection.calls[1][1]["pageToken"] == "p2"


def test_calendar_info_knows_which_calendars_are_writable() -> None:
    assert CalendarInfo("a", "A", "owner", primary=False).writable
    assert not CalendarInfo("b", "B", "writer", primary=False).writable


def test_fake_lists_only_tagged_blocks_of_the_source_in_range() -> None:
    fake = FakeCalendarClient()
    inside = Interval(utc(1, 9), utc(1, 10))
    fake.add_event(CAL, inside, "Real meeting")
    mine = fake.insert_block(CAL, "src1", "Google", inside)
    fake.insert_block(CAL, "src2", "Acme", inside)
    fake.insert_block(CAL, "src1", "Google", Interval(utc(3, 9), utc(3, 10)))
    assert fake.list_blocks(CAL, "src1", utc(1, 0), utc(2, 0)) == [
        Block(mine, inside, "busy (Google)")
    ]
