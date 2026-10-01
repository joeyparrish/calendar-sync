# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

from flask import Flask
from flask.testing import FlaskClient

from calendar_sync.calendar_client import CalendarClient
from calendar_sync.oauth import SignInError, SignInResult
from calendar_sync.store import Account
from calendar_sync.web import create_app
from tests.web_support import A, World, sign_in


def test_signed_out_home_offers_sign_in(client: FlaskClient) -> None:
    page = client.get("/").get_data(as_text=True)
    assert 'href="/login"' in page
    assert "<title>Calendar Sync App</title>" in page
    assert "<h1>Calendar Sync App</h1>" in page


def test_login_redirects_to_google(client: FlaskClient) -> None:
    response = client.get("/login")
    assert response.status_code == 302
    assert response.headers["Location"] == "https://accounts.example/auth"


def test_first_sign_in_creates_the_account_in_the_primary_time_zone(
    client: FlaskClient, world: World
) -> None:
    response = sign_in(client)
    assert response.status_code == 302 and response.headers["Location"] == "/"
    account = world.store.get_account(A)
    assert account is not None
    assert (account.calendar_id, account.time_zone, account.has_token) == (
        "primary",
        "America/Los_Angeles",
        True,
    )
    assert A in client.get("/").get_data(as_text=True)


def test_sign_in_error_is_shown_and_no_session_is_set(client: FlaskClient, world: World) -> None:
    world.sign_in = SignInError("Please sign in with your example.com account.")
    response = sign_in(client)
    assert response.status_code == 400
    assert "example.com account" in response.get_data(as_text=True)
    assert 'href="/login"' in client.get("/").get_data(as_text=True)


def test_callback_without_a_pending_sign_in(client: FlaskClient) -> None:
    assert client.get("/oauth/callback?state=s1&code=c1").status_code == 400


def test_new_account_without_a_refresh_token_forces_consent(
    client: FlaskClient, world: World
) -> None:
    world.sign_in = SignInResult(A, None)
    response = sign_in(client)
    assert response.headers["Location"] == "/login?consent=1"


def test_returning_account_without_a_new_token_just_signs_in(
    client: FlaskClient, world: World
) -> None:
    world.store.save_login(A, "refresh-a", "UTC")
    world.sign_in = SignInResult(A, None)
    response = sign_in(client)
    assert response.headers["Location"] == "/"
    assert world.store.refresh_token(A) == "refresh-a"


def test_account_needing_login_is_asked_to_sign_in_again(client: FlaskClient, world: World) -> None:
    sign_in(client)
    world.store.set_needs_login(A)
    assert "Sign in again" in client.get("/").get_data(as_text=True)


def test_logout(client: FlaskClient) -> None:
    sign_in(client)
    client.post("/logout")
    assert 'href="/login"' in client.get("/").get_data(as_text=True)


def test_session_cookie_flags(world: World) -> None:
    app = create_app(world.deps(), role="web", session_key="k", secure_cookies=True)
    with app.test_client() as c:
        cookie = c.get("/login").headers.get("Set-Cookie", "")
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie


def test_csrf_is_enforced(world: World) -> None:
    app = create_app(world.deps(), role="web", session_key="k", secure_cookies=False)
    with app.test_client() as c:
        assert c.post("/logout").status_code == 400


def test_worker_role_only_serves_sync(world: World) -> None:
    worker = create_app(world.deps(), role="worker", session_key="k")
    with worker.test_client() as c:
        assert c.get("/").status_code == 404
        assert c.get("/health").get_data(as_text=True) == "ok"
        response = c.post("/sync")
    assert response.status_code == 200
    assert response.get_json() == {"synced": 0, "skipped": 0, "failed": 0}


def test_home_still_loads_when_google_is_unreachable(world: World) -> None:
    deps = world.deps()

    def broken(account: Account) -> CalendarClient:
        raise OSError("network unreachable")

    deps.connect = broken
    app = create_app(deps, role="web", session_key="k", secure_cookies=False)
    world.store.save_login(A, "refresh-a", "UTC")
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["email"] = A
        response = c.get("/")
    assert response.status_code == 200
    assert "load your calendars" in response.get_data(as_text=True)


def canonical_app(world: World) -> Flask:
    return create_app(
        world.deps(), role="web", session_key="k", canonical_host="calsync.example.com"
    )


def test_other_hostnames_redirect_to_the_canonical_one(world: World) -> None:
    with canonical_app(world).test_client() as c:
        response = c.get("/sources/abc/edit?x=1", base_url="https://old.run.app")
    assert response.status_code == 301
    assert response.headers["Location"] == "https://calsync.example.com/sources/abc/edit?x=1"


def test_canonical_hostname_is_served(world: World) -> None:
    with canonical_app(world).test_client() as c:
        response = c.get("/", base_url="https://calsync.example.com")
    assert response.status_code == 200


def test_health_is_served_on_any_hostname(world: World) -> None:
    with canonical_app(world).test_client() as c:
        response = c.get("/health", base_url="https://old.run.app")
    assert response.get_data(as_text=True) == "ok"
