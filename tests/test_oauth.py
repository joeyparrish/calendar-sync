# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials

from calendar_sync.oauth import (
    CALENDAR_SCOPES,
    SCOPES,
    MissingPermissions,
    NeedsLogin,
    OAuthConfig,
    SignInError,
    SignInResult,
    finish_sign_in,
    refresh_credentials,
    start_sign_in,
)

CONFIG = OAuthConfig(
    "client-id", "client-secret", "https://app.example/oauth/callback", "example.com"
)
CALLBACK = "https://app.example/oauth/callback?state=s1&code=c1"


def query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


def test_start_sign_in_requests_offline_access_for_the_domain() -> None:
    start = start_sign_in(CONFIG, force_consent=False)
    params = query(start.url)
    assert params["client_id"] == ["client-id"]
    assert params["redirect_uri"] == ["https://app.example/oauth/callback"]
    assert params["access_type"] == ["offline"]
    assert params["hd"] == ["example.com"]
    assert set(params["scope"][0].split()) == set(SCOPES)
    assert params["code_challenge_method"] == ["S256"]
    assert "prompt" not in params
    assert start.state and start.verifier


def test_start_sign_in_can_force_consent() -> None:
    assert query(start_sign_in(CONFIG, force_consent=True).url)["prompt"] == ["consent"]


def test_client_file_is_read(tmp_path: Path) -> None:
    path = tmp_path / "client.json"
    path.write_text(json.dumps({"web": {"client_id": "id", "client_secret": "secret"}}))
    config = OAuthConfig.from_client_file(path, "https://app.example/cb", "Example.com")
    assert config == OAuthConfig("id", "secret", "https://app.example/cb", "example.com")


def token(scopes: frozenset[str] = CALENDAR_SCOPES, refresh: str | None = "r1") -> dict[str, Any]:
    result: dict[str, Any] = {"id_token": "jwt", "scope": " ".join(["openid", *scopes])}
    if refresh is not None:
        result["refresh_token"] = refresh
    return result


def claims(email: str = "Joe@Example.com", hd: str | None = "example.com") -> dict[str, Any]:
    result: dict[str, Any] = {"email": email, "email_verified": True}
    if hd is not None:
        result["hd"] = hd
    return result


def finish(tok: dict[str, Any], cl: dict[str, Any], url: str = CALLBACK) -> SignInResult:
    return finish_sign_in(
        CONFIG, url, "s1", "v1", exchange=lambda *args: tok, verify=lambda *args: cl
    )


def test_finish_returns_the_lowercased_email_and_refresh_token() -> None:
    result = finish(token(), claims())
    assert (result.email, result.refresh_token) == ("joe@example.com", "r1")


def test_finish_accepts_scopes_as_a_list() -> None:
    # oauthlib's parse_token_response turns the "scope" string into a list.
    tok = token()
    tok["scope"] = ["openid", *CALENDAR_SCOPES]
    assert finish(tok, claims()).email == "joe@example.com"


def test_finish_without_a_refresh_token() -> None:
    assert finish(token(refresh=None), claims()).refresh_token is None


def test_finish_rejects_another_domain() -> None:
    with pytest.raises(SignInError, match="example.com"):
        finish(token(), claims("joe@gmail.com", hd=None))


def test_finish_rejects_missing_calendar_permissions() -> None:
    with pytest.raises(MissingPermissions):
        finish(token(scopes=frozenset()), claims())


def test_finish_rejects_a_state_mismatch() -> None:
    with pytest.raises(SignInError):
        finish(token(), claims(), url="https://app.example/oauth/callback?state=evil&code=c1")


def test_finish_reports_a_cancelled_consent() -> None:
    url = "https://app.example/oauth/callback?state=s1&error=access_denied"
    with pytest.raises(SignInError, match="cancel"):
        finish(token(), claims(), url=url)


def test_finish_rejects_an_unverified_email() -> None:
    unverified = claims()
    unverified["email_verified"] = False
    with pytest.raises(SignInError):
        finish(token(), unverified)


def test_refresh_rejected_by_google_needs_login(monkeypatch: pytest.MonkeyPatch) -> None:
    def refresh(self: Credentials, request: Any) -> None:
        raise RefreshError("invalid_grant")

    monkeypatch.setattr(Credentials, "refresh", refresh)
    with pytest.raises(NeedsLogin):
        refresh_credentials(CONFIG, "r1")
