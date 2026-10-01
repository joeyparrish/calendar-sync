# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import timedelta

import pytest
from google.cloud import firestore

from calendar_sync.store import Account, Source, Store
from tests.fakes import FakeCrypto
from tests.helpers import utc

A = "a@example.com"
B = "b@example.com"
FEED = "https://outlook.example.com/owa/calendar/abc/SECRETTOKEN/reachcalendar.ics"


@pytest.fixture
def store(db: firestore.Client) -> Store:
    return Store(db, FakeCrypto())


def test_new_login_starts_on_primary_in_the_given_time_zone(store: Store) -> None:
    account = store.save_login("A@Example.com", "token-a", "America/Los_Angeles")
    assert account == Account(A, "primary", "America/Los_Angeles", False, True)
    assert store.refresh_token(A) == "token-a"


def test_later_login_keeps_calendar_and_clears_needs_login(store: Store) -> None:
    store.save_login(A, "token-a", "UTC")
    store.set_calendar(A, "busy@group.calendar.google.com", "Europe/Berlin")
    store.set_needs_login(A)
    account = store.save_login(A, "token-a2", "UTC")
    assert account == Account(A, "busy@group.calendar.google.com", "Europe/Berlin", False, True)
    assert store.refresh_token(A) == "token-a2"


def test_token_is_bound_to_its_account(db: firestore.Client, store: Store) -> None:
    store.save_login(A, "token-a", "UTC")
    store.save_login(B, "token-b", "UTC")
    data = db.collection("accounts").document(A).get().to_dict()
    assert data is not None
    db.collection("accounts").document(B).update({"token": data["token"]})
    with pytest.raises(ValueError):
        store.refresh_token(B)


def test_unknown_account(store: Store) -> None:
    assert store.get_account(A) is None
    assert store.refresh_token(A) is None


def test_list_accounts(store: Store) -> None:
    store.save_login(A, "t", "UTC")
    store.save_login(B, "t", "UTC")
    assert sorted(a.email for a in store.list_accounts()) == [A, B]


def test_ics_location_is_encrypted_and_displayed_by_host(
    db: firestore.Client, store: Store
) -> None:
    store.save_login(A, "t", "UTC")
    source = store.add_source(A, "ics", FEED, "Acme", "Tomato")
    assert source.display == "https://outlook.example.com/…"
    raw = db.collection("accounts").document(A).collection("sources").document(source.id).get()
    assert "SECRETTOKEN" not in str(raw.to_dict())
    [listed] = store.list_sources(A)
    assert listed.location is None
    [with_location] = store.list_sources(A, with_locations=True)
    assert with_location.location == FEED


def test_google_location_is_plain(store: Store) -> None:
    store.save_login(A, "t", "UTC")
    source = store.add_source(A, "google", "work@example.com", "Work", None)
    assert source == Source(source.id, "google", "work@example.com", "Work")


def test_sources_are_per_account(store: Store) -> None:
    store.save_login(A, "t", "UTC")
    store.save_login(B, "t", "UTC")
    source = store.add_source(A, "google", "x@example.com", "X", None)
    assert store.list_sources(B) == []
    assert store.get_source(B, source.id) is None
    assert store.get_source(A, source.id) == source


def test_update_and_remove_source(store: Store) -> None:
    store.save_login(A, "t", "UTC")
    source = store.add_source(A, "google", "x@example.com", "X", None)
    store.update_source(A, source.id, label="Work", color="Graphite")
    updated = store.get_source(A, source.id)
    assert updated is not None and (updated.label, updated.color) == ("Work", "Graphite")
    store.remove_source(A, source.id)
    assert store.list_sources(A) == []


def test_unknown_source_kind_is_rejected(store: Store) -> None:
    store.save_login(A, "t", "UTC")
    with pytest.raises(ValueError, match="kind"):
        store.add_source(A, "outlook", "x", "X", None)


def test_record_success_and_failure(store: Store) -> None:
    store.save_login(A, "t", "UTC")
    source = store.add_source(A, "google", "x@example.com", "X", None)
    store.record_failure(A, source.id, "boom")
    current = store.get_source(A, source.id)
    assert current is not None and (current.last_success, current.last_error) == (None, "boom")
    store.record_success(A, source.id, utc(1, 9))
    current = store.get_source(A, source.id)
    assert current is not None
    assert (current.last_success, current.last_error) == (utc(1, 9), None)


def test_delete_account_removes_sources(store: Store) -> None:
    store.save_login(A, "t", "UTC")
    store.add_source(A, "google", "x@example.com", "X", None)
    store.delete_account(A)
    assert store.get_account(A) is None
    assert store.list_sources(A) == []


def test_lease_excludes_a_second_holder_until_released_or_expired(store: Store) -> None:
    store.save_login(A, "t", "UTC")
    nine = timedelta(minutes=9)
    assert store.try_acquire_lease(A, utc(1, 9), nine)
    assert not store.try_acquire_lease(A, utc(1, 9, 5), nine)
    assert store.try_acquire_lease(A, utc(1, 9, 10), nine)  # the first lease expired
    store.release_lease(A)
    assert store.try_acquire_lease(A, utc(1, 9, 11), nine)


def test_no_lease_for_an_unknown_account(store: Store) -> None:
    assert not store.try_acquire_lease(A, utc(1, 9), timedelta(minutes=9))
