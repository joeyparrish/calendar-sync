# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

"""Busy intervals from a Google calendar that shares free/busy with the destination account."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from calendar_sync.intervals import Interval
from calendar_sync.sources import SourceError


def parse_freebusy_response(response: Mapping[str, Any], address: str) -> list[Interval]:
    """Busy intervals for `address` from a freebusy.query response.

    The API reports per-calendar problems (such as a calendar the caller cannot see) inside a
    successful response, next to an empty busy list. Those must fail the source; read naively
    they would mean "free all week" and every block would be deleted.
    """
    if response.get("errors"):
        raise SourceError(f"free/busy query failed: {_describe(response['errors'])}")
    calendars = response.get("calendars")
    if not isinstance(calendars, Mapping):
        raise SourceError("free/busy response has no calendars")
    entry = _find_calendar(calendars, address)
    if entry.get("errors"):
        raise SourceError(f"cannot read free/busy for {address}: {_describe(entry['errors'])}")
    busy = entry.get("busy")
    if not isinstance(busy, list):
        raise SourceError(f"free/busy response for {address} has no busy list")
    try:
        return [
            Interval(datetime.fromisoformat(item["start"]), datetime.fromisoformat(item["end"]))
            for item in busy
        ]
    except (KeyError, TypeError, ValueError) as e:
        raise SourceError(f"malformed busy entry for {address}: {e}") from None


def _find_calendar(calendars: Mapping[str, Any], address: str) -> Mapping[str, Any]:
    if address in calendars:
        return calendars[address]
    for key, value in calendars.items():
        if key.lower() == address.lower():
            return value
    raise SourceError(f"free/busy response does not mention {address}")


def _describe(errors: Any) -> str:
    if not isinstance(errors, list):
        return str(errors)
    return ", ".join(
        str(e.get("reason") or e.get("message") or e) if isinstance(e, Mapping) else str(e)
        for e in errors
    )
