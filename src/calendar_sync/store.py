# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

"""Accounts and sources in Firestore. Tokens and ICS URLs are stored only encrypted."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from google.cloud import firestore

from calendar_sync.crypto import Crypto

SOURCE_KINDS = ("google", "ics")


@dataclass(frozen=True)
class Account:
    email: str
    calendar_id: str = "primary"
    time_zone: str = "UTC"
    needs_login: bool = False
    has_token: bool = False


@dataclass(frozen=True)
class Source:
    id: str
    kind: str
    display: str
    label: str
    color: str | None = None
    last_success: datetime | None = None
    last_error: str | None = None
    location: str | None = None  # filled only by list_sources(with_locations=True)


def display_location(kind: str, location: str) -> str:
    """ICS URLs are secrets, so only their scheme and host are ever shown."""
    if kind != "ics":
        return location
    parts = urlsplit(location)
    return f"{parts.scheme}://{parts.netloc}/…"


class Store:
    def __init__(self, db: firestore.Client, crypto: Crypto) -> None:
        self._db = db
        self._crypto = crypto

    def _account_ref(self, email: str) -> Any:
        return self._db.collection("accounts").document(email.lower())

    def _sources(self, email: str) -> Any:
        return self._account_ref(email).collection("sources")

    def get_account(self, email: str) -> Account | None:
        snap = self._account_ref(email).get()
        return _account(snap) if snap.exists else None

    def list_accounts(self) -> list[Account]:
        return [_account(snap) for snap in self._db.collection("accounts").stream()]

    def save_login(self, email: str, refresh_token: str, time_zone: str) -> Account:
        """Store a fresh token. A new account starts on its primary calendar in `time_zone`."""
        email = email.lower()
        ref = self._account_ref(email)
        fields: dict[str, Any] = {
            "token": self._crypto.encrypt(refresh_token, email),
            "needs_login": False,
        }
        if not ref.get().exists:
            fields.update(calendar_id="primary", time_zone=time_zone, lease_until=None)
        ref.set(fields, merge=True)
        account = self.get_account(email)
        assert account is not None
        return account

    def refresh_token(self, email: str) -> str | None:
        snap = self._account_ref(email).get()
        token = (snap.to_dict() or {}).get("token") if snap.exists else None
        return None if token is None else self._crypto.decrypt(token, email.lower())

    def set_calendar(self, email: str, calendar_id: str, time_zone: str) -> None:
        self._account_ref(email).update({"calendar_id": calendar_id, "time_zone": time_zone})

    def set_needs_login(self, email: str) -> None:
        self._account_ref(email).update({"needs_login": True})

    def delete_account(self, email: str) -> None:
        for snap in self._sources(email).stream():
            snap.reference.delete()
        self._account_ref(email).delete()

    def add_source(
        self, email: str, kind: str, location: str, label: str, color: str | None
    ) -> Source:
        if kind not in SOURCE_KINDS:
            raise ValueError(f"unknown source kind {kind!r}; expected one of {SOURCE_KINDS}")
        email = email.lower()
        source_id = uuid.uuid4().hex[:12]
        stored = self._crypto.encrypt(location, email) if kind == "ics" else location
        display = display_location(kind, location)
        self._sources(email).document(source_id).set(
            {
                "kind": kind,
                "location": stored,
                "display": display,
                "label": label,
                "color": color,
                "last_success": None,
                "last_error": None,
            }
        )
        return Source(source_id, kind, display, label, color)

    def get_source(self, email: str, source_id: str) -> Source | None:
        snap = self._sources(email).document(source_id).get()
        return self._source(snap, email, with_location=False) if snap.exists else None

    def list_sources(self, email: str, *, with_locations: bool = False) -> list[Source]:
        sources = [
            self._source(snap, email, with_location=with_locations)
            for snap in self._sources(email).stream()
        ]
        return sorted(sources, key=lambda s: (s.label.lower(), s.id))

    def update_source(self, email: str, source_id: str, *, label: str, color: str | None) -> None:
        self._sources(email).document(source_id).update({"label": label, "color": color})

    def remove_source(self, email: str, source_id: str) -> None:
        self._sources(email).document(source_id).delete()

    def record_success(self, email: str, source_id: str, when: datetime) -> None:
        self._sources(email).document(source_id).update({"last_success": when, "last_error": None})

    def record_failure(self, email: str, source_id: str, error: str) -> None:
        self._sources(email).document(source_id).update({"last_error": error})

    def try_acquire_lease(self, email: str, now: datetime, duration: timedelta) -> bool:
        """Take the account's sync lease unless someone holds an unexpired one."""
        ref = self._account_ref(email)

        @firestore.transactional
        def take(transaction: firestore.Transaction) -> bool:
            snap = ref.get(transaction=transaction)
            if not snap.exists:
                return False
            until = (snap.to_dict() or {}).get("lease_until")
            if until is not None and until > now:
                return False
            transaction.update(ref, {"lease_until": now + duration})
            return True

        return take(self._db.transaction())

    def release_lease(self, email: str) -> None:
        self._account_ref(email).update({"lease_until": None})

    def _source(self, snap: Any, email: str, *, with_location: bool) -> Source:
        data = snap.to_dict() or {}
        location = None
        if with_location:
            stored = data["location"]
            location = (
                self._crypto.decrypt(stored, email.lower()) if data["kind"] == "ics" else stored
            )
        return Source(
            snap.id,
            data["kind"],
            data["display"],
            data["label"],
            data.get("color"),
            data.get("last_success"),
            data.get("last_error"),
            location,
        )


def _account(snap: Any) -> Account:
    data = snap.to_dict() or {}
    return Account(
        snap.id,
        data.get("calendar_id", "primary"),
        data.get("time_zone", "UTC"),
        bool(data.get("needs_login")),
        data.get("token") is not None,
    )
