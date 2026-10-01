# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

"""In-memory stand-ins for Google Calendar and KMS, shared by tests."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from calendar_sync.calendar_client import (
    SOURCE_KEY,
    TOOL_KEY,
    TOOL_VALUE,
    CalendarInfo,
    block_summary,
)
from calendar_sync.intervals import Interval
from calendar_sync.reconcile import Block


@dataclass
class FakeEvent:
    event_id: str
    calendar_id: str
    interval: Interval
    summary: str
    private: dict[str, str] = field(default_factory=dict)
    color_id: str | None = None


class FakeCalendarClient:
    def __init__(self) -> None:
        self.events: dict[str, FakeEvent] = {}
        self.freebusy_responses: dict[str, dict[str, Any]] = {}
        self.freebusy_calls: list[tuple[str, datetime, datetime]] = []
        self.calendars: list[CalendarInfo] = []
        self.calendars_error: Exception | None = None
        self._next_id = 0

    def list_calendars(self) -> list[CalendarInfo]:
        if self.calendars_error is not None:
            raise self.calendars_error
        return list(self.calendars)

    def add_event(
        self,
        calendar_id: str,
        interval: Interval,
        summary: str = "meeting",
        private: dict[str, str] | None = None,
        color_id: str | None = None,
    ) -> str:
        self._next_id += 1
        event_id = f"evt{self._next_id:04d}"
        self.events[event_id] = FakeEvent(
            event_id, calendar_id, interval, summary, dict(private or {}), color_id
        )
        return event_id

    def events_on(self, calendar_id: str) -> list[FakeEvent]:
        return sorted(
            (e for e in self.events.values() if e.calendar_id == calendar_id),
            key=lambda e: (e.interval, e.event_id),
        )

    def query_freebusy(
        self, address: str, time_min: datetime, time_max: datetime
    ) -> dict[str, Any]:
        self.freebusy_calls.append((address, time_min, time_max))
        return self.freebusy_responses[address]

    def list_blocks(
        self, calendar_id: str, source_id: str, time_min: datetime, time_max: datetime | None
    ) -> list[Block]:
        return [
            Block(e.event_id, e.interval, e.summary, e.color_id)
            for e in self.events_on(calendar_id)
            if e.private.get(TOOL_KEY) == TOOL_VALUE
            and e.private.get(SOURCE_KEY) == source_id
            and e.interval.end > time_min
            and (time_max is None or e.interval.start < time_max)
        ]

    def insert_block(
        self,
        calendar_id: str,
        source_id: str,
        label: str,
        interval: Interval,
        color_id: str | None = None,
    ) -> str:
        return self.add_event(
            calendar_id,
            interval,
            block_summary(label),
            {TOOL_KEY: TOOL_VALUE, SOURCE_KEY: source_id},
            color_id,
        )

    def update_block(
        self, calendar_id: str, event_id: str, summary: str, color_id: str | None
    ) -> None:
        event = self.events[event_id]
        assert event.calendar_id == calendar_id
        event.summary = summary
        event.color_id = color_id

    def delete_block(self, calendar_id: str, event_id: str) -> None:
        event = self.events.get(event_id)
        if event is not None and event.calendar_id == calendar_id:
            del self.events[event_id]


class FakeCrypto:
    """Reversible stand-in for KMS that, like KMS, refuses a mismatched context."""

    def encrypt(self, plaintext: str, context: str) -> bytes:
        return context.encode() + b"|" + base64.b64encode(plaintext.encode())

    def decrypt(self, ciphertext: bytes, context: str) -> str:
        prefix = context.encode() + b"|"
        if not ciphertext.startswith(prefix):
            raise ValueError("ciphertext belongs to another context")
        return base64.b64decode(ciphertext[len(prefix) :]).decode()
