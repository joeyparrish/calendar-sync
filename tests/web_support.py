# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

"""A fake outside world for the web tests: real store on the emulator, fake Google."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from flask.testing import FlaskClient
from google.cloud import firestore

from calendar_sync.calendar_client import CalendarClient, CalendarInfo
from calendar_sync.oauth import OAuthConfig, SignInResult, SignInStart
from calendar_sync.store import Account, Store
from calendar_sync.web import Deps
from tests.fakes import FakeCalendarClient, FakeCrypto

A = "a@example.com"
B = "b@example.com"
LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 10, 1, 9, 0, tzinfo=LA)
CONFIG = OAuthConfig("id", "secret", "https://app.example/oauth/callback", "example.com")
CALENDARS = [
    CalendarInfo(A, "Me", "owner", primary=True, time_zone="America/Los_Angeles"),
    CalendarInfo("busy@group.calendar.google.com", "Busy", "owner", time_zone="Europe/Berlin"),
    CalendarInfo("team@group.calendar.google.com", "Team", "reader"),
]


class World:
    def __init__(self, db: firestore.Client) -> None:
        self.store = Store(db, FakeCrypto())
        self.client = FakeCalendarClient()
        self.client.calendars = list(CALENDARS)
        self.sign_in: SignInResult | Exception = SignInResult(A, "refresh-a")
        self.revoked: list[str] = []
        self.feeds: dict[str, bytes] = {}

    def connect(self, account: Account) -> CalendarClient:
        return self.client

    def finish(self, config: OAuthConfig, url: str, state: str, verifier: str) -> SignInResult:
        assert (state, verifier) == ("s1", "v1")
        if isinstance(self.sign_in, Exception):
            raise self.sign_in
        return self.sign_in

    def deps(self) -> Deps:
        return Deps(
            store=self.store,
            oauth=CONFIG,
            connect=self.connect,
            fetch_feed=lambda url: self.feeds[url],
            now=lambda: NOW,
            sign_in_start=lambda config, force_consent: SignInStart(
                "https://accounts.example/auth" + ("?consent" if force_consent else ""), "s1", "v1"
            ),
            sign_in_finish=self.finish,
            revoke=self.revoked.append,
        )


def sign_in(client: FlaskClient) -> Any:
    client.get("/login")
    return client.get("/oauth/callback?state=s1&code=c1")
