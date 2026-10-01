# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

import pytest
from flask.testing import FlaskClient

from calendar_sync.intervals import Interval
from tests.helpers import utc
from tests.web_support import A, B, World, sign_in

FEED = "https://outlook.example.com/owa/calendar/abc/SECRETTOKEN/reachcalendar.ics"
PAST = Interval(utc(30, 16, month=9), utc(30, 17, month=9))
UPCOMING = Interval(utc(2, 16), utc(2, 17))


@pytest.fixture
def signed_in(client: FlaskClient) -> FlaskClient:
    sign_in(client)
    return client


def add(client: FlaskClient, **form: str) -> str:
    return client.post("/sources", data=form, follow_redirects=True).get_data(as_text=True)


def test_add_google_source_with_label_and_color(signed_in: FlaskClient, world: World) -> None:
    page = add(
        signed_in, kind="google", location="work@example.com", label="Work", color="Graphite"
    )
    [source] = world.store.list_sources(A)
    assert (source.kind, source.display, source.label, source.color) == (
        "google",
        "work@example.com",
        "Work",
        "Graphite",
    )
    assert "Work" in page


def test_add_ics_source_shows_only_the_host(signed_in: FlaskClient, world: World) -> None:
    page = add(signed_in, kind="ics", location=FEED, label="Acme", color="default")
    assert "SECRETTOKEN" not in page
    assert "https://outlook.example.com/…" in page
    [source] = world.store.list_sources(A)
    assert source.color is None


@pytest.mark.parametrize(
    "form",
    [
        {"kind": "ics", "location": "file:///etc/passwd", "label": "X", "color": "default"},
        {"kind": "google", "location": "not-an-address", "label": "X", "color": "default"},
        {"kind": "google", "location": "x@example.com", "label": " ", "color": "default"},
        {"kind": "google", "location": "x@example.com", "label": "X", "color": "teal"},
        {"kind": "outlook", "location": "x@example.com", "label": "X", "color": "default"},
    ],
)
def test_invalid_sources_are_rejected(
    signed_in: FlaskClient, world: World, form: dict[str, str]
) -> None:
    add(signed_in, **form)
    assert world.store.list_sources(A) == []


def test_edit_source(signed_in: FlaskClient, world: World) -> None:
    source = world.store.add_source(A, "google", "x@example.com", "X", None)
    assert "X" in signed_in.get(f"/sources/{source.id}/edit").get_data(as_text=True)
    signed_in.post(f"/sources/{source.id}/edit", data={"label": "Work", "color": "Tomato"})
    updated = world.store.get_source(A, source.id)
    assert updated is not None and (updated.label, updated.color) == ("Work", "Tomato")


def test_another_users_source_is_not_found(signed_in: FlaskClient, world: World) -> None:
    world.store.save_login(B, "refresh-b", "UTC")
    theirs = world.store.add_source(B, "google", "x@example.com", "Theirs", None)
    assert signed_in.get(f"/sources/{theirs.id}/edit").status_code == 404
    edit = signed_in.post(f"/sources/{theirs.id}/edit", data={"label": "Mine", "color": "default"})
    assert edit.status_code == 404
    assert signed_in.post(f"/sources/{theirs.id}/remove").status_code == 404
    assert [s.label for s in world.store.list_sources(B)] == ["Theirs"]


def test_remove_source_deletes_upcoming_blocks(signed_in: FlaskClient, world: World) -> None:
    source = world.store.add_source(A, "google", "x@example.com", "X", None)
    world.client.insert_block("primary", source.id, "X", PAST)
    world.client.insert_block("primary", source.id, "X", UPCOMING)
    signed_in.post(f"/sources/{source.id}/remove")
    assert [e.interval for e in world.client.events_on("primary")] == [PAST]
    assert world.store.list_sources(A) == []


def test_change_calendar_clears_old_blocks_and_adopts_its_time_zone(
    signed_in: FlaskClient, world: World
) -> None:
    source = world.store.add_source(A, "google", "x@example.com", "X", None)
    world.client.insert_block("primary", source.id, "X", UPCOMING)
    signed_in.post("/calendar", data={"calendar_id": "busy@group.calendar.google.com"})
    account = world.store.get_account(A)
    assert account is not None
    assert (account.calendar_id, account.time_zone) == (
        "busy@group.calendar.google.com",
        "Europe/Berlin",
    )
    assert world.client.events_on("primary") == []


def test_calendar_the_user_does_not_own_is_rejected(signed_in: FlaskClient, world: World) -> None:
    source = world.store.add_source(A, "google", "x@example.com", "X", None)
    world.client.insert_block("primary", source.id, "X", UPCOMING)
    signed_in.post("/calendar", data={"calendar_id": "team@group.calendar.google.com"})
    account = world.store.get_account(A)
    assert account is not None and account.calendar_id == "primary"
    assert len(world.client.events_on("primary")) == 1


def test_home_lists_owned_calendars_only(signed_in: FlaskClient) -> None:
    page = signed_in.get("/").get_data(as_text=True)
    assert "busy@group.calendar.google.com" in page
    assert "team@group.calendar.google.com" not in page


def test_settings_need_a_session(client: FlaskClient) -> None:
    response = client.post("/sources", data={"kind": "google", "location": "x@example.com"})
    assert response.status_code == 302 and response.headers["Location"] == "/"
