# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

from calendar_sync.calendar_client import SOURCE_KEY, TOOL_KEY, TOOL_VALUE
from calendar_sync.intervals import Interval, Window
from calendar_sync.sink import clear_blocks, sync_blocks
from tests.fakes import FakeCalendarClient
from tests.helpers import utc

CAL = "primary"
WINDOW = Window(utc(1, 7), utc(2, 7))
A = Interval(utc(1, 9), utc(1, 10))
B = Interval(utc(1, 16), utc(1, 17))


def test_creates_blocks_for_new_busy_times() -> None:
    client = FakeCalendarClient()
    plan = sync_blocks(client, CAL, "src1", "Google", [B], WINDOW, dry_run=False)
    assert plan.inserts == (B,)
    [event] = client.events_on(CAL)
    assert event.summary == "busy (Google)"
    assert event.interval == B
    assert event.private == {TOOL_KEY: TOOL_VALUE, SOURCE_KEY: "src1"}


def test_deletes_blocks_that_are_now_free() -> None:
    client = FakeCalendarClient()
    client.insert_block(CAL, "src1", "Google", B)
    sync_blocks(client, CAL, "src1", "Google", [], WINDOW, dry_run=False)
    assert client.events_on(CAL) == []


def test_never_touches_untagged_events() -> None:
    client = FakeCalendarClient()
    client.add_event(CAL, B, summary="Real meeting")
    sync_blocks(client, CAL, "src1", "Google", [], WINDOW, dry_run=False)
    assert [e.summary for e in client.events_on(CAL)] == ["Real meeting"]


def test_leaves_other_sources_blocks_alone() -> None:
    client = FakeCalendarClient()
    client.insert_block(CAL, "src2", "Acme", B)
    sync_blocks(client, CAL, "src1", "Google", [], WINDOW, dry_run=False)
    assert [e.summary for e in client.events_on(CAL)] == ["busy (Acme)"]


def test_leaves_blocks_that_started_before_the_window() -> None:
    client = FakeCalendarClient()
    overnight = Interval(utc(1, 5), utc(1, 9))  # began before the 07:00 window start
    client.insert_block(CAL, "src1", "Google", overnight)
    sync_blocks(client, CAL, "src1", "Google", [], WINDOW, dry_run=False)
    assert [e.interval for e in client.events_on(CAL)] == [overnight]


def test_ignores_intervals_clipped_at_fetch_start() -> None:
    # freebusy.query clips at its timeMin, which is a day before the window.
    client = FakeCalendarClient()
    clipped = Interval(WINDOW.fetch_start, utc(1, 8))
    plan = sync_blocks(client, CAL, "src1", "Google", [clipped], WINDOW, dry_run=False)
    assert plan.is_empty
    assert client.events_on(CAL) == []


def test_dry_run_changes_nothing() -> None:
    client = FakeCalendarClient()
    client.insert_block(CAL, "src1", "Google", A)
    plan = sync_blocks(client, CAL, "src1", "Google", [B], WINDOW, dry_run=True)
    assert plan.inserts == (B,)
    assert [d.interval for d in plan.deletes] == [A]
    assert [e.interval for e in client.events_on(CAL)] == [A]


def test_second_run_is_a_no_op() -> None:
    client = FakeCalendarClient()
    sync_blocks(client, CAL, "src1", "Google", [A, B], WINDOW, dry_run=False)
    plan = sync_blocks(client, CAL, "src1", "Google", [A, B], WINDOW, dry_run=False)
    assert plan.is_empty
    assert len(client.events_on(CAL)) == 2


def test_creates_blocks_in_the_source_color() -> None:
    client = FakeCalendarClient()
    sync_blocks(client, CAL, "src1", "Google", [B], WINDOW, dry_run=False, color_id="8")
    [event] = client.events_on(CAL)
    assert event.color_id == "8"


def test_recolors_and_relabels_existing_blocks_in_place() -> None:
    client = FakeCalendarClient()
    event_id = client.insert_block(CAL, "src1", "Old label", B)
    plan = sync_blocks(client, CAL, "src1", "Google", [B], WINDOW, dry_run=False, color_id="8")
    assert [u.event_id for u in plan.updates] == [event_id]
    assert plan.inserts == () and plan.deletes == ()
    [event] = client.events_on(CAL)
    assert (event.event_id, event.summary, event.color_id) == (event_id, "busy (Google)", "8")


def test_dry_run_reports_updates_without_applying_them() -> None:
    client = FakeCalendarClient()
    client.insert_block(CAL, "src1", "Google", B)
    plan = sync_blocks(client, CAL, "src1", "Google", [B], WINDOW, dry_run=True, color_id="8")
    assert len(plan.updates) == 1
    [event] = client.events_on(CAL)
    assert event.color_id is None


def test_clear_blocks_deletes_from_since_onward() -> None:
    client = FakeCalendarClient()
    past = Interval(utc(1, 1), utc(1, 2))
    future = Interval(utc(3, 9), utc(3, 10))
    client.insert_block(CAL, "src1", "Google", past)
    client.insert_block(CAL, "src1", "Google", future)
    client.add_event(CAL, future, summary="Real meeting")
    assert clear_blocks(client, CAL, "src1", since=utc(1, 7)) == 1
    assert [(e.interval, e.summary) for e in client.events_on(CAL)] == [
        (past, "busy (Google)"),
        (future, "Real meeting"),
    ]
