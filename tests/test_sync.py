# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from google.cloud import firestore

from calendar_sync.calendar_client import CalendarClient
from calendar_sync.intervals import Interval
from calendar_sync.oauth import NeedsLogin
from calendar_sync.sources import SourceError
from calendar_sync.store import Account, Store
from calendar_sync.sync import run_worker, sync_account
from tests.fakes import FakeCalendarClient, FakeCrypto
from tests.helpers import utc

A = "a@example.com"
B = "b@example.com"
ADDRESS = "work@example.com"
FEED = "https://calendar.example.com/SECRETTOKEN/calendar.ics"
LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 10, 1, 9, 0, tzinfo=LA)  # window starts 2026-10-01T07:00Z
MEETING = Interval(utc(1, 17), utc(1, 18))
FEED_BODY = (
    b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\nBEGIN:VEVENT\r\n"
    b"UID:a@example.com\r\nDTSTAMP:20261001T000000Z\r\nDTSTART:20261001T170000Z\r\n"
    b"DTEND:20261001T180000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
)


def busy(*intervals: Interval) -> dict[str, Any]:
    entries = [{"start": i.start.isoformat(), "end": i.end.isoformat()} for i in intervals]
    return {"calendars": {ADDRESS: {"busy": entries}}}


@pytest.fixture
def store(db: firestore.Client) -> Store:
    s = Store(db, FakeCrypto())
    s.save_login(A, "token-a", "America/Los_Angeles")
    return s


def no_feed(url: str) -> bytes:
    raise AssertionError("no feed expected")


def logs(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]


def source_logs(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    return [e for e in logs(capsys) if e.get("event", "").startswith("source_")]


def account(store: Store, email: str = A) -> Account:
    result = store.get_account(email)
    assert result is not None
    return result


def test_google_source_creates_colored_blocks(store: Store) -> None:
    source = store.add_source(A, "google", ADDRESS, "Work", "Graphite")
    client = FakeCalendarClient()
    client.freebusy_responses[ADDRESS] = busy(MEETING)
    [result] = sync_account(store, account(store), client, now=NOW, fetch_feed=no_feed)
    assert result.error is None
    [event] = client.events_on("primary")
    assert (event.summary, event.interval, event.color_id) == ("busy (Work)", MEETING, "8")
    current = store.get_source(A, source.id)
    assert current is not None and current.last_success == NOW


def test_window_follows_the_account_time_zone(store: Store) -> None:
    store.add_source(A, "google", ADDRESS, "Work", None)
    client = FakeCalendarClient()
    client.freebusy_responses[ADDRESS] = busy()
    sync_account(store, account(store), client, now=NOW, fetch_feed=no_feed)
    [(_, time_min, _)] = client.freebusy_calls
    assert time_min == utc(30, 7, month=9)  # a day before local midnight in Los Angeles


def test_ics_source_gets_the_decrypted_url(store: Store) -> None:
    store.add_source(A, "ics", FEED, "Acme", None)
    client = FakeCalendarClient()
    fetched: list[str] = []

    def fetch(url: str) -> bytes:
        fetched.append(url)
        return FEED_BODY

    sync_account(store, account(store), client, now=NOW, fetch_feed=fetch)
    assert fetched == [FEED]
    assert [e.summary for e in client.events_on("primary")] == ["busy (Acme)"]


def test_failed_feed_keeps_blocks_and_never_logs_the_url(
    store: Store, capsys: pytest.CaptureFixture[str]
) -> None:
    source = store.add_source(A, "ics", FEED, "Acme", None)
    client = FakeCalendarClient()
    client.insert_block("primary", source.id, "Acme", MEETING)

    def fail(url: str) -> bytes:
        raise SourceError("feed returned HTTP 503")

    [result] = sync_account(store, account(store), client, now=NOW, fetch_feed=fail)
    assert result.error is not None
    assert [e.interval for e in client.events_on("primary")] == [MEETING]
    entries = logs(capsys)
    assert [e["severity"] for e in entries] == ["WARNING"]
    assert "SECRETTOKEN" not in json.dumps(entries)


def test_errors_that_quote_the_feed_url_are_scrubbed(
    store: Store, capsys: pytest.CaptureFixture[str]
) -> None:
    source = store.add_source(A, "ics", FEED, "Acme", None)

    def fail(url: str) -> bytes:
        raise ValueError(f"cannot fetch {url}")

    [result] = sync_account(store, account(store), FakeCalendarClient(), now=NOW, fetch_feed=fail)
    current = store.get_source(A, source.id)
    assert current is not None and current.last_error is not None
    for text in (result.error or "", current.last_error, json.dumps(logs(capsys))):
        assert "SECRETTOKEN" not in text
    assert "https://calendar.example.com/…" in current.last_error


def test_unexpected_errors_log_at_error(store: Store, capsys: pytest.CaptureFixture[str]) -> None:
    store.add_source(A, "google", ADDRESS, "Work", None)
    client = FakeCalendarClient()  # no free/busy response: the fake raises KeyError
    sync_account(store, account(store), client, now=NOW, fetch_feed=no_feed)
    assert [e["severity"] for e in source_logs(capsys)] == ["ERROR"]


def test_worker_syncs_every_account_and_releases_leases(store: Store) -> None:
    store.save_login(B, "token-b", "UTC")
    store.add_source(A, "google", ADDRESS, "Work", None)
    clients = {A: FakeCalendarClient(), B: FakeCalendarClient()}
    clients[A].freebusy_responses[ADDRESS] = busy(MEETING)
    report = run_worker(store, lambda acct: clients[acct.email], now=NOW, fetch_feed=no_feed)
    assert (report.synced, report.skipped, report.failed) == (2, 0, 0)
    assert len(clients[A].events_on("primary")) == 1
    assert store.try_acquire_lease(A, NOW, timedelta(minutes=9))


def test_worker_skips_an_account_whose_lease_is_held(store: Store) -> None:
    store.add_source(A, "google", ADDRESS, "Work", None)
    client = FakeCalendarClient()
    client.freebusy_responses[ADDRESS] = busy(MEETING)
    assert store.try_acquire_lease(A, NOW, timedelta(minutes=9))
    report = run_worker(store, lambda acct: client, now=NOW, fetch_feed=no_feed)
    assert (report.synced, report.skipped) == (0, 1)
    assert client.events_on("primary") == []


def test_worker_marks_revoked_accounts_and_carries_on(
    store: Store, capsys: pytest.CaptureFixture[str]
) -> None:
    store.save_login(B, "token-b", "UTC")
    client_b = FakeCalendarClient()

    def connect(acct: Account) -> CalendarClient:
        if acct.email == A:
            raise NeedsLogin("invalid_grant")
        return client_b

    report = run_worker(store, connect, now=NOW, fetch_feed=no_feed)
    assert (report.synced, report.failed) == (1, 1)
    assert account(store).needs_login
    [entry] = [e for e in logs(capsys) if e.get("event") == "needs_login"]
    assert entry["account"] == A


def test_worker_skips_accounts_that_need_login(store: Store) -> None:
    store.set_needs_login(A)
    report = run_worker(store, lambda acct: FakeCalendarClient(), now=NOW, fetch_feed=no_feed)
    assert (report.synced, report.skipped) == (0, 1)
