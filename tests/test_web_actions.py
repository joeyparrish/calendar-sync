# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import timedelta

import pytest
from flask.testing import FlaskClient

from calendar_sync.calendar_client import CalendarClient
from calendar_sync.intervals import Interval
from calendar_sync.store import Account
from calendar_sync.web import create_app
from tests.helpers import utc
from tests.web_support import NOW, A, World, sign_in

MEETING = Interval(utc(1, 17), utc(1, 18))


@pytest.fixture
def signed_in(client: FlaskClient) -> FlaskClient:
    sign_in(client)
    return client


def busy_for(world: World, address: str) -> None:
    entries = [{"start": MEETING.start.isoformat(), "end": MEETING.end.isoformat()}]
    world.client.freebusy_responses[address] = {"calendars": {address: {"busy": entries}}}


def test_sync_now_creates_blocks(signed_in: FlaskClient, world: World) -> None:
    world.store.add_source(A, "google", "work@example.com", "Work", None)
    busy_for(world, "work@example.com")
    page = signed_in.post("/sync-now", follow_redirects=True).get_data(as_text=True)
    assert "added 1" in page
    assert [e.summary for e in world.client.events_on("primary")] == ["busy (Work)"]
    assert world.store.try_acquire_lease(A, NOW, timedelta(minutes=9))  # released


def test_slow_buttons_show_progress(signed_in: FlaskClient, world: World) -> None:
    world.store.add_source(A, "google", "work@example.com", "Work", None)
    page = signed_in.get("/").get_data(as_text=True)
    assert 'data-busy="Syncing…"' in page
    assert 'data-busy="Switching…"' in page
    assert 'data-busy="Removing…"' in page
    assert 'addEventListener("submit"' in page


def test_sync_now_waits_for_a_running_sync(signed_in: FlaskClient, world: World) -> None:
    world.store.add_source(A, "google", "work@example.com", "Work", None)
    busy_for(world, "work@example.com")
    assert world.store.try_acquire_lease(A, NOW, timedelta(minutes=9))
    page = signed_in.post("/sync-now", follow_redirects=True).get_data(as_text=True)
    assert "already running" in page
    assert world.client.events_on("primary") == []


def test_sync_now_reports_a_failing_feed_without_its_url(
    signed_in: FlaskClient, world: World
) -> None:
    world.store.add_source(A, "ics", "https://cal.example.com/SECRETTOKEN/c.ics", "Acme", None)
    page = signed_in.post("/sync-now", follow_redirects=True).get_data(as_text=True)
    assert "Acme" in page and "SECRETTOKEN" not in page


def test_forget_deletes_blocks_revokes_and_signs_out(signed_in: FlaskClient, world: World) -> None:
    source = world.store.add_source(A, "google", "work@example.com", "Work", None)
    world.client.insert_block("primary", source.id, "Work", MEETING)
    assert "Remove my data" in signed_in.get("/forget").get_data(as_text=True)
    signed_in.post("/forget")
    assert world.client.events_on("primary") == []
    assert world.revoked == ["refresh-a"]
    assert world.store.get_account(A) is None
    assert 'href="/login"' in signed_in.get("/").get_data(as_text=True)


def test_forget_keeps_everything_if_blocks_cannot_be_deleted(world: World) -> None:
    # Builds its own app so its deps can hold a connect that always fails.
    deps = world.deps()

    def broken(account: Account) -> CalendarClient:
        raise OSError("network unreachable")

    deps.connect = broken
    app = create_app(deps, role="web", session_key="k", secure_cookies=False)
    app.config["WTF_CSRF_ENABLED"] = False
    world.store.save_login(A, "refresh-a", "UTC")
    world.store.add_source(A, "google", "work@example.com", "Work", None)
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["email"] = A
        page = c.post("/forget", follow_redirects=True).get_data(as_text=True)
    assert "nothing was deleted" in page
    assert world.store.get_account(A) is not None
    assert world.revoked == []
