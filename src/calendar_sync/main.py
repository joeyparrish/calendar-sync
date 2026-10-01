# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

"""Builds the app from environment variables set by the Cloud Run services."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask
from google.cloud import firestore

from calendar_sync.calendar_client import CalendarClient, GoogleCalendarClient
from calendar_sync.crypto import KmsCrypto
from calendar_sync.oauth import NeedsLogin, OAuthConfig, refresh_credentials
from calendar_sync.sources.ics import fetch_ics
from calendar_sync.store import Account, Store
from calendar_sync.web import Deps, create_app


def create_app_from_env() -> Flask:
    role = os.environ.get("ROLE", "web")
    public_url = os.environ["PUBLIC_URL"].rstrip("/")
    oauth = OAuthConfig.from_client_file(
        Path(os.environ["OAUTH_CLIENT_FILE"]),
        redirect_uri=public_url + "/oauth/callback",
        allowed_domain=os.environ["ALLOWED_DOMAIN"],
    )
    store = Store(
        firestore.Client(project=os.environ["GOOGLE_CLOUD_PROJECT"]),
        KmsCrypto(os.environ["KMS_KEY"]),
    )

    def connect(account: Account) -> CalendarClient:
        token = store.refresh_token(account.email)
        if token is None:
            raise NeedsLogin("no stored token")
        return GoogleCalendarClient.from_credentials(refresh_credentials(oauth, token))

    session_key = os.environ.get("SESSION_KEY", "")
    if role == "web" and not session_key:
        raise RuntimeError("SESSION_KEY must be set for the web role")
    deps = Deps(
        store=store,
        oauth=oauth,
        connect=connect,
        fetch_feed=fetch_ics,
        now=lambda: datetime.now(UTC),
    )
    return create_app(
        deps, role=role, session_key=session_key, canonical_host=urlsplit(public_url).netloc
    )
