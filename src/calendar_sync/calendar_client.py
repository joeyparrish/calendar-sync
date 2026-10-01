# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

"""A thin interface over the Google Calendar API, so everything above it can use a fake."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING, Any, Protocol

from googleapiclient.errors import HttpError

from calendar_sync.intervals import Interval
from calendar_sync.reconcile import Block

if TYPE_CHECKING:
    from google.oauth2.credentials import Credentials

TOOL_KEY = "calendarSyncTool"
TOOL_VALUE = "calendar-sync"
SOURCE_KEY = "calendarSyncSource"

# googleapiclient retries 429, 5xx, and rate-limit 403s with backoff. A retried insert can
# occasionally create a duplicate; the next reconcile deletes it.
RETRIES = 3

# Google Calendar's fixed event color palette, by the names its UI shows. Events cannot take
# arbitrary colors; "default" (no colorId) shows the calendar's own color.
EVENT_COLORS = {
    "Lavender": "1",
    "Sage": "2",
    "Grape": "3",
    "Flamingo": "4",
    "Banana": "5",
    "Tangerine": "6",
    "Peacock": "7",
    "Graphite": "8",
    "Blueberry": "9",
    "Basil": "10",
    "Tomato": "11",
}


def parse_color(name: str) -> str | None:
    """The canonical color name for `name` (any case), or None for "default"."""
    if name.strip().lower() == "default":
        return None
    for canonical in EVENT_COLORS:
        if canonical.lower() == name.strip().lower():
            return canonical
    choices = ", ".join(["default", *EVENT_COLORS])
    raise ValueError(f"unknown color {name!r}; choose one of: {choices}")


def event_color_id(name: str | None) -> str | None:
    return None if name is None else EVENT_COLORS[name]


def block_summary(label: str) -> str:
    return f"busy ({label})"


@dataclass(frozen=True)
class CalendarInfo:
    """A calendar in the account's calendar list."""

    id: str
    summary: str
    access_role: str
    primary: bool = False
    time_zone: str = "UTC"

    @property
    def writable(self) -> bool:
        """Blocks are written with the calendar.events.owned scope, so only owned calendars."""
        return self.access_role == "owner"


class CalendarClient(Protocol):
    def list_calendars(self) -> list[CalendarInfo]: ...

    def query_freebusy(
        self, address: str, time_min: datetime, time_max: datetime
    ) -> dict[str, Any]: ...

    def list_blocks(
        self, calendar_id: str, source_id: str, time_min: datetime, time_max: datetime | None
    ) -> list[Block]: ...

    def insert_block(
        self,
        calendar_id: str,
        source_id: str,
        label: str,
        interval: Interval,
        color_id: str | None = None,
    ) -> str: ...

    def update_block(
        self, calendar_id: str, event_id: str, summary: str, color_id: str | None
    ) -> None: ...

    def delete_block(self, calendar_id: str, event_id: str) -> None: ...


def rfc3339(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class GoogleCalendarClient:
    def __init__(self, service: Any) -> None:
        self._service = service

    @classmethod
    def from_credentials(cls, credentials: Credentials) -> GoogleCalendarClient:
        from googleapiclient.discovery import build

        return cls(build("calendar", "v3", credentials=credentials, cache_discovery=False))

    def list_calendars(self) -> list[CalendarInfo]:
        calendars: list[CalendarInfo] = []
        page_token: str | None = None
        while True:
            response = (
                self._service.calendarList().list(pageToken=page_token).execute(num_retries=RETRIES)
            )
            calendars.extend(
                CalendarInfo(
                    item["id"],
                    item.get("summaryOverride") or item.get("summary", ""),
                    item.get("accessRole", ""),
                    primary=bool(item.get("primary")),
                    time_zone=item.get("timeZone", "UTC"),
                )
                for item in response.get("items", [])
            )
            page_token = response.get("nextPageToken")
            if not page_token:
                return calendars

    def query_freebusy(
        self, address: str, time_min: datetime, time_max: datetime
    ) -> dict[str, Any]:
        body = {
            "timeMin": rfc3339(time_min),
            "timeMax": rfc3339(time_max),
            "items": [{"id": address}],
        }
        return self._service.freebusy().query(body=body).execute(num_retries=RETRIES)

    def list_blocks(
        self, calendar_id: str, source_id: str, time_min: datetime, time_max: datetime | None
    ) -> list[Block]:
        blocks: list[Block] = []
        page_token: str | None = None
        while True:
            response = (
                self._service.events()
                .list(
                    calendarId=calendar_id,
                    privateExtendedProperty=[
                        f"{TOOL_KEY}={TOOL_VALUE}",
                        f"{SOURCE_KEY}={source_id}",
                    ],
                    timeMin=rfc3339(time_min),
                    timeMax=rfc3339(time_max) if time_max is not None else None,
                    singleEvents=True,
                    showDeleted=False,
                    maxResults=2500,
                    pageToken=page_token,
                )
                .execute(num_retries=RETRIES)
            )
            blocks.extend(_block_from_event(item) for item in response.get("items", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return blocks

    def insert_block(
        self,
        calendar_id: str,
        source_id: str,
        label: str,
        interval: Interval,
        color_id: str | None = None,
    ) -> str:
        body: dict[str, Any] = {
            "summary": block_summary(label),
            "start": {"dateTime": rfc3339(interval.start)},
            "end": {"dateTime": rfc3339(interval.end)},
            "visibility": "private",
            "transparency": "opaque",
            "reminders": {"useDefault": False},
            "extendedProperties": {"private": {TOOL_KEY: TOOL_VALUE, SOURCE_KEY: source_id}},
        }
        if color_id is not None:
            body["colorId"] = color_id
        created = (
            self._service.events()
            .insert(calendarId=calendar_id, body=body, sendUpdates="none")
            .execute(num_retries=RETRIES)
        )
        return created["id"]

    def update_block(
        self, calendar_id: str, event_id: str, summary: str, color_id: str | None
    ) -> None:
        # A null colorId resets the event to the calendar's color.
        body = {"summary": summary, "colorId": color_id}
        self._service.events().patch(
            calendarId=calendar_id, eventId=event_id, body=body, sendUpdates="none"
        ).execute(num_retries=RETRIES)

    def delete_block(self, calendar_id: str, event_id: str) -> None:
        try:
            self._service.events().delete(
                calendarId=calendar_id, eventId=event_id, sendUpdates="none"
            ).execute(num_retries=RETRIES)
        except HttpError as e:
            if e.resp.status in (404, 410):
                return  # already gone, which is what we wanted
            raise


def _block_from_event(item: dict[str, Any]) -> Block:
    return Block(
        item["id"],
        Interval(_event_time(item["start"]), _event_time(item["end"])),
        item.get("summary"),
        item.get("colorId"),
    )


def _event_time(value: dict[str, str]) -> datetime:
    if "dateTime" in value:
        return datetime.fromisoformat(value["dateTime"])
    return datetime.combine(date.fromisoformat(value["date"]), time.min, tzinfo=UTC)
