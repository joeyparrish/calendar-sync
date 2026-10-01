# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

"""The web UI (role "web") and the private sync endpoint (role "worker")."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import wraps
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_wtf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

from calendar_sync.calendar_client import (
    EVENT_COLORS,
    CalendarClient,
    CalendarInfo,
    parse_color,
)
from calendar_sync.intervals import compute_window
from calendar_sync.log import log
from calendar_sync.oauth import (
    NeedsLogin,
    OAuthConfig,
    SignInError,
    finish_sign_in,
    revoke,
    start_sign_in,
)
from calendar_sync.sink import clear_blocks
from calendar_sync.store import SOURCE_KINDS, Account, Store
from calendar_sync.sync import LEASE, run_worker, sync_account

ICS_SCHEMES = ("https", "http", "webcal")


@dataclass
class Deps:
    """Everything that reaches outside the process, so tests can replace it."""

    store: Store
    oauth: OAuthConfig
    connect: Callable[[Account], CalendarClient]
    fetch_feed: Callable[[str], bytes]
    now: Callable[[], datetime]
    sign_in_start: Callable[..., Any] = field(default=start_sign_in)
    sign_in_finish: Callable[..., Any] = field(default=finish_sign_in)
    revoke: Callable[[str], None] = field(default=revoke)


def create_app(
    deps: Deps,
    *,
    role: str,
    session_key: str,
    secure_cookies: bool = True,
    canonical_host: str | None = None,
) -> Flask:
    """`canonical_host`: if set, the web UI redirects other hostnames (such as the run.app
    address behind a custom domain) to it, so sign-in starts and ends on one host."""
    app = Flask(__name__)
    # Cloud Run terminates TLS; trust its forwarded scheme and host for redirect URLs.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)  # type: ignore[method-assign]
    app.config.update(
        SECRET_KEY=session_key or secrets.token_hex(32),
        SESSION_COOKIE_SECURE=secure_cookies,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    )
    # Not /healthz: Cloud Run reserves paths ending in "z".
    app.add_url_rule("/health", "health", lambda: "ok")
    if role == "worker":
        app.register_blueprint(_worker_routes(deps))
    elif role == "web":
        CSRFProtect(app)
        app.register_blueprint(_web_routes(deps))
        if canonical_host:

            @app.before_request
            def redirect_to_canonical_host() -> Any:
                if request.host != canonical_host and request.path != "/health":
                    path = request.full_path if request.query_string else request.path
                    return redirect(f"https://{canonical_host}{path}", code=301)
                return None
    else:
        raise ValueError(f"unknown role {role!r}")
    return app


def _worker_routes(deps: Deps) -> Blueprint:
    bp = Blueprint("worker", __name__)

    @bp.post("/sync")
    def sync_all() -> dict[str, int]:
        report = run_worker(deps.store, deps.connect, now=deps.now(), fetch_feed=deps.fetch_feed)
        return {"synced": report.synced, "skipped": report.skipped, "failed": report.failed}

    return bp


def _message(text: str, *, status: int, retry: bool = False) -> tuple[str, int]:
    return render_template("message.html", message=text, retry=retry), status


def _today_start(deps: Deps, account: Account) -> datetime:
    return compute_window(deps.now(), ZoneInfo(account.time_zone), 1).start


def _source_form_error(kind: str, location: str, label: str) -> str | None:
    if kind not in SOURCE_KINDS:
        return "Choose Google or ICS."
    if not label:
        return "Give the source a label."
    if kind == "google" and "@" not in location:
        return "Enter the Google calendar's email address."
    if kind == "ics" and urlsplit(location).scheme.lower() not in ICS_SCHEMES:
        return "The ICS link must start with https://, http://, or webcal://."
    return None


def _web_routes(deps: Deps) -> Blueprint:
    bp = Blueprint("web", __name__)

    def owned_calendars(account: Account) -> list[CalendarInfo] | None:
        """The calendars the user can pick, or None if Google could not be reached."""
        try:
            return [c for c in deps.connect(account).list_calendars() if c.writable]
        except NeedsLogin:
            deps.store.set_needs_login(account.email)
            return []
        except Exception as e:
            log("WARNING", f"{account.email}: could not list calendars", error=str(e))
            return None

    @bp.get("/")
    def home() -> Any:
        email = session.get("email")
        if email is None:
            return render_template("signed_out.html")
        account = deps.store.get_account(email)
        if account is None:
            session.clear()
            return redirect("/")
        calendars = [] if account.needs_login else owned_calendars(account)
        account = deps.store.get_account(email) or account  # owned_calendars may flag it
        return render_template(
            "home.html",
            account=account,
            sources=deps.store.list_sources(email),
            calendars=calendars,
            colors=list(EVENT_COLORS),
        )

    @bp.get("/login")
    def login() -> Any:
        start = deps.sign_in_start(deps.oauth, force_consent=request.args.get("consent") == "1")
        session["oauth"] = {"state": start.state, "verifier": start.verifier}
        return redirect(start.url)

    @bp.get("/oauth/callback")
    def callback() -> Any:
        pending = session.pop("oauth", None)
        if pending is None:
            return _message("Sign-in expired. Please try again.", status=400, retry=True)
        try:
            result = deps.sign_in_finish(
                deps.oauth, request.url, pending["state"], pending["verifier"]
            )
        except SignInError as e:
            return _message(str(e), status=400, retry=True)

        account = deps.store.get_account(result.email)
        if result.refresh_token is None:
            if account is None or account.needs_login or not account.has_token:
                return redirect(url_for("web.login", consent="1"))
        else:
            is_new = account is None
            account = deps.store.save_login(
                result.email, result.refresh_token, account.time_zone if account else "UTC"
            )
            if is_new:
                _adopt_primary_time_zone(deps, account)
        session.clear()
        session["email"] = result.email
        session.permanent = True
        log("INFO", f"{result.email} signed in", event="sign_in", account=result.email)
        return redirect("/")

    @bp.post("/logout")
    def logout() -> Any:
        session.clear()
        return redirect("/")

    def signed_in(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            email = session.get("email")
            account = deps.store.get_account(email) if email else None
            if account is None:
                return redirect("/")
            return view(account, *args, **kwargs)

        return wrapper

    @bp.post("/calendar")
    @signed_in
    def change_calendar(account: Account) -> Any:
        calendar_id = request.form.get("calendar_id", "")
        if calendar_id == account.calendar_id:
            return redirect("/")
        client = deps.connect(account)
        choice = next(
            (c for c in client.list_calendars() if c.id == calendar_id and c.writable), None
        )
        if choice is None:
            flash("Choose a calendar you own.")
            return redirect("/")
        since = _today_start(deps, account)
        try:
            for source in deps.store.list_sources(account.email):
                clear_blocks(client, account.calendar_id, source.id, since)
        except Exception as e:
            flash(
                "Could not remove blocks from your current calendar, so nothing changed."
                f" ({type(e).__name__})"
            )
            return redirect("/")
        deps.store.set_calendar(account.email, choice.id, choice.time_zone)
        flash(f"Blocks now go to {choice.summary}. Press Sync now to fill it.")
        return redirect("/")

    @bp.post("/sources")
    @signed_in
    def add_source(account: Account) -> Any:
        kind = request.form.get("kind", "")
        location = request.form.get("location", "").strip()
        label = request.form.get("label", "").strip()
        error = _source_form_error(kind, location, label)
        try:
            color = parse_color(request.form.get("color", "default"))
        except ValueError as e:
            error = error or str(e)
            color = None
        if error:
            flash(error)
            return redirect("/")
        deps.store.add_source(account.email, kind, location, label, color)
        flash(f"Added {label}. It syncs within 10 minutes, or press Sync now.")
        return redirect("/")

    @bp.route("/sources/<source_id>/edit", methods=["GET", "POST"])
    @signed_in
    def edit_source(account: Account, source_id: str) -> Any:
        source = deps.store.get_source(account.email, source_id)
        if source is None:
            abort(404)
        if request.method == "GET":
            return render_template("edit.html", source=source, colors=list(EVENT_COLORS))
        label = request.form.get("label", "").strip()
        try:
            color = parse_color(request.form.get("color", "default"))
        except ValueError as e:
            flash(str(e))
            return redirect(url_for("web.edit_source", source_id=source_id))
        if not label:
            flash("Give the source a label.")
            return redirect(url_for("web.edit_source", source_id=source_id))
        deps.store.update_source(account.email, source_id, label=label, color=color)
        flash("Saved. The next sync updates its blocks.")
        return redirect("/")

    @bp.post("/sources/<source_id>/remove")
    @signed_in
    def remove_source(account: Account, source_id: str) -> Any:
        source = deps.store.get_source(account.email, source_id)
        if source is None:
            abort(404)
        try:
            removed = clear_blocks(
                deps.connect(account),
                account.calendar_id,
                source_id,
                _today_start(deps, account),
            )
        except Exception as e:
            flash(f"Could not delete its blocks, so the source was kept. ({type(e).__name__})")
            return redirect("/")
        deps.store.remove_source(account.email, source_id)
        flash(f"Removed {source.label} and {removed} upcoming block(s).")
        return redirect("/")

    @bp.post("/sync-now")
    @signed_in
    def sync_now(account: Account) -> Any:
        now = deps.now()
        if not deps.store.try_acquire_lease(account.email, now, LEASE):
            flash("A sync is already running. Try again in a minute.")
            return redirect("/")
        try:
            results = sync_account(
                deps.store, account, deps.connect(account), now=now, fetch_feed=deps.fetch_feed
            )
        except NeedsLogin:
            deps.store.set_needs_login(account.email)
            flash("Google stopped accepting Calendar Sync's access. Please sign in again.")
            return redirect("/")
        finally:
            deps.store.release_lease(account.email)
        for r in results:
            if r.plan is None:
                flash(f"{r.label}: failed, blocks kept. See its status below.")
            else:
                flash(
                    f"{r.label}: added {len(r.plan.inserts)}, updated {len(r.plan.updates)},"
                    f" removed {len(r.plan.deletes)}."
                )
        if not results:
            flash("Nothing to sync yet. Add a source first.")
        return redirect("/")

    @bp.route("/forget", methods=["GET", "POST"])
    @signed_in
    def forget(account: Account) -> Any:
        if request.method == "GET":
            return render_template("forget.html")
        try:
            client = deps.connect(account)
            since = _today_start(deps, account)
            for source in deps.store.list_sources(account.email):
                clear_blocks(client, account.calendar_id, source.id, since)
        except Exception as e:
            flash(
                "Could not delete your blocks, so nothing was deleted."
                f" Sign in again and retry. ({type(e).__name__})"
            )
            return redirect("/")
        token = deps.store.refresh_token(account.email)
        if token:
            deps.revoke(token)
        deps.store.delete_account(account.email)
        session.clear()
        log("INFO", f"{account.email} removed their data", event="forget", account=account.email)
        flash("Your blocks, settings, and Calendar Sync's access were removed.")
        return redirect("/")

    return bp


def _adopt_primary_time_zone(deps: Deps, account: Account) -> None:
    try:
        calendars = deps.connect(account).list_calendars()
    except Exception as e:
        log("WARNING", f"{account.email}: could not read calendars", error=str(e))
        return
    primary = next((c for c in calendars if c.primary), None)
    if primary is not None:
        deps.store.set_calendar(account.email, "primary", primary.time_zone)
