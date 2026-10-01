# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

"""Sign in with Google. One consent both identifies the user and grants calendar access."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import requests
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2 import id_token as google_id_token
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
REVOKE_URI = "https://oauth2.googleapis.com/revoke"

CALENDAR_SCOPES = frozenset(
    {
        "https://www.googleapis.com/auth/calendar.events.owned",
        "https://www.googleapis.com/auth/calendar.events.freebusy",
        "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    }
)
SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email", *sorted(CALENDAR_SCOPES)]


class SignInError(Exception):
    """Sign-in failed in a way the user should see. The message is safe to display."""


class MissingPermissions(SignInError):
    pass


class NeedsLogin(Exception):
    """A stored refresh token was rejected; the user must sign in again."""


@dataclass(frozen=True)
class OAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    allowed_domain: str

    @classmethod
    def from_client_file(cls, path: Path, redirect_uri: str, allowed_domain: str) -> OAuthConfig:
        web = json.loads(path.read_text())["web"]
        return cls(web["client_id"], web["client_secret"], redirect_uri, allowed_domain.lower())

    def client_config(self) -> dict[str, Any]:
        return {
            "web": {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "auth_uri": AUTH_URI,
                "token_uri": TOKEN_URI,
                "redirect_uris": [self.redirect_uri],
            }
        }


@dataclass(frozen=True)
class SignInStart:
    url: str
    state: str
    verifier: str


@dataclass(frozen=True)
class SignInResult:
    email: str
    refresh_token: str | None


def start_sign_in(config: OAuthConfig, *, force_consent: bool) -> SignInStart:
    flow = Flow.from_client_config(
        config.client_config(),
        scopes=SCOPES,
        redirect_uri=config.redirect_uri,
        autogenerate_code_verifier=True,
    )
    options: dict[str, str] = {
        "access_type": "offline",
        "include_granted_scopes": "true",
        "hd": config.allowed_domain,
    }
    if force_consent:
        options["prompt"] = "consent"
    url, state = flow.authorization_url(**options)
    assert flow.code_verifier is not None
    return SignInStart(url, state, flow.code_verifier)


Exchange = Callable[[OAuthConfig, str, str, str], dict[str, Any]]
Verify = Callable[[str, str], dict[str, Any]]


def finish_sign_in(
    config: OAuthConfig,
    authorization_response: str,
    state: str,
    verifier: str,
    *,
    exchange: Exchange | None = None,
    verify: Verify | None = None,
) -> SignInResult:
    params = parse_qs(urlsplit(authorization_response).query)
    if params.get("state", [None])[0] != state:
        raise SignInError("Sign-in expired or was interrupted. Please try again.")
    if "error" in params:
        raise SignInError("Sign-in was cancelled. Please try again.")

    token = (exchange or _exchange_code)(config, authorization_response, state, verifier)
    raw_id_token = token.get("id_token")
    if not raw_id_token:
        raise SignInError("Google did not identify the account. Please try again.")
    claims = (verify or _verify_id_token)(raw_id_token, config.client_id)

    email = str(claims.get("email", "")).lower()
    if not email or not claims.get("email_verified"):
        raise SignInError("Google did not confirm the account's email address.")
    if str(claims.get("hd", "")).lower() != config.allowed_domain:
        raise SignInError(f"Please sign in with your {config.allowed_domain} account.")
    if not CALENDAR_SCOPES <= _granted_scopes(token):
        raise MissingPermissions(
            "Calendar Sync needs every permission Google asks for. Please sign in again and"
            " allow them all."
        )
    return SignInResult(email, token.get("refresh_token"))


def _granted_scopes(token: dict[str, Any]) -> set[str]:
    """Google sends a space-separated string; oauthlib turns it into a list."""
    scope = token.get("scope", "")
    return set(scope.split()) if isinstance(scope, str) else {str(s) for s in scope}


def _exchange_code(
    config: OAuthConfig, authorization_response: str, state: str, verifier: str
) -> dict[str, Any]:
    # Users can untick scopes; accept the reduced grant here and check it in finish_sign_in.
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
    flow = Flow.from_client_config(
        config.client_config(), scopes=SCOPES, redirect_uri=config.redirect_uri, state=state
    )
    flow.code_verifier = verifier
    try:
        return dict(flow.fetch_token(authorization_response=authorization_response))
    except Exception as e:  # oauthlib raises several types for bad or reused codes
        raise SignInError("Google sign-in did not complete. Please try again.") from e


def _verify_id_token(raw_id_token: str, client_id: str) -> dict[str, Any]:
    try:
        return dict(google_id_token.verify_oauth2_token(raw_id_token, Request(), client_id))
    except ValueError as e:
        raise SignInError("Google's identity token could not be verified.") from e


def refresh_credentials(config: OAuthConfig, refresh_token: str) -> Credentials:
    creds = Credentials(
        None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=config.client_id,
        client_secret=config.client_secret,
        scopes=SCOPES,
    )
    try:
        creds.refresh(Request())
    except RefreshError as e:
        raise NeedsLogin(str(e)) from e
    return creds


def revoke(refresh_token: str) -> None:
    """Best effort: a token Google already invalidated answers 400, which is fine."""
    try:
        requests.post(REVOKE_URI, params={"token": refresh_token}, timeout=10)
    except requests.RequestException:
        pass
