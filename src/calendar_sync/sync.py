# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

"""Syncing one account, and the worker loop that syncs them all."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

from google.auth.exceptions import TransportError
from googleapiclient.errors import HttpError

from calendar_sync.calendar_client import CalendarClient, event_color_id
from calendar_sync.intervals import DEFAULT_WEEKS, Interval, Window, compute_window
from calendar_sync.log import log
from calendar_sync.oauth import NeedsLogin
from calendar_sync.reconcile import ReconcilePlan
from calendar_sync.sink import sync_blocks
from calendar_sync.sources import SourceError
from calendar_sync.sources.google_freebusy import parse_freebusy_response
from calendar_sync.sources.ics import parse_ics
from calendar_sync.store import Account, Store

LEASE = timedelta(minutes=9)

# Failures we expect from the outside world. Anything else is a bug and is logged at ERROR,
# which emails the admin.
EXPECTED_ERRORS = (SourceError, HttpError, TransportError, OSError)


@dataclass(frozen=True)
class SourceResult:
    source_id: str
    label: str
    plan: ReconcilePlan | None
    error: str | None


@dataclass(frozen=True)
class WorkerReport:
    synced: int
    skipped: int
    failed: int


def fetch_intervals(
    kind: str,
    location: str,
    client: CalendarClient,
    window: Window,
    tz: tzinfo,
    fetch_feed: Callable[[str], bytes],
) -> list[Interval]:
    if kind == "google":
        response = client.query_freebusy(location, window.fetch_start, window.end)
        return parse_freebusy_response(response, location)
    if kind == "ics":
        return parse_ics(fetch_feed(location), window.fetch_start, window.end, tz)
    raise SourceError(f"unknown source kind {kind!r}")


def sync_account(
    store: Store,
    account: Account,
    client: CalendarClient,
    *,
    now: datetime,
    fetch_feed: Callable[[str], bytes],
    weeks: int = DEFAULT_WEEKS,
) -> list[SourceResult]:
    tz = ZoneInfo(account.time_zone)
    window = compute_window(now, tz, weeks)
    results: list[SourceResult] = []
    for source in store.list_sources(account.email, with_locations=True):
        assert source.location is not None
        # Any failure means this source's busy times are unknown, never that they are free,
        # so its blocks are left alone.
        try:
            intervals = fetch_intervals(
                source.kind, source.location, client, window, tz, fetch_feed
            )
            plan = sync_blocks(
                client,
                account.calendar_id,
                source.id,
                source.label,
                intervals,
                window,
                dry_run=False,
                color_id=event_color_id(source.color),
            )
        except Exception as e:
            # Some library errors quote their input; never let an ICS URL's secret through.
            message = f"{type(e).__name__}: {e}".replace(source.location, source.display)
            store.record_failure(account.email, source.id, message)
            log(
                "WARNING" if isinstance(e, EXPECTED_ERRORS) else "ERROR",
                f"{account.email}: source {source.label!r} failed, blocks kept",
                event="source_failed",
                account=account.email,
                source=source.id,
                error=message,
            )
            results.append(SourceResult(source.id, source.label, None, message))
            continue
        store.record_success(account.email, source.id, now)
        log(
            "INFO",
            f"{account.email}: source {source.label!r} synced",
            event="source_synced",
            account=account.email,
            source=source.id,
            added=len(plan.inserts),
            updated=len(plan.updates),
            removed=len(plan.deletes),
        )
        results.append(SourceResult(source.id, source.label, plan, None))
    return results


def run_worker(
    store: Store,
    connect: Callable[[Account], CalendarClient],
    *,
    now: datetime,
    fetch_feed: Callable[[str], bytes],
) -> WorkerReport:
    synced = skipped = failed = 0
    for account in store.list_accounts():
        if account.needs_login or not account.has_token:
            skipped += 1
            continue
        if not store.try_acquire_lease(account.email, now, LEASE):
            skipped += 1
            continue
        try:
            client = connect(account)
            sync_account(store, account, client, now=now, fetch_feed=fetch_feed)
            synced += 1
        except NeedsLogin as e:
            store.set_needs_login(account.email)
            log(
                "WARNING",
                f"{account.email} needs to sign in again",
                event="needs_login",
                account=account.email,
                error=str(e),
            )
            failed += 1
        except Exception as e:
            log(
                "WARNING" if isinstance(e, EXPECTED_ERRORS) else "ERROR",
                f"{account.email}: could not sync",
                event="account_failed",
                account=account.email,
                error=f"{type(e).__name__}: {e}",
            )
            failed += 1
        finally:
            store.release_lease(account.email)
    log(
        "INFO",
        f"sync run: {synced} synced, {skipped} skipped, {failed} failed",
        event="run_complete",
        synced=synced,
        skipped=skipped,
        failed=failed,
    )
    return WorkerReport(synced, skipped, failed)
