# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

"""Make one source's blocks on the destination calendar match its busy intervals."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from calendar_sync.calendar_client import CalendarClient, block_summary
from calendar_sync.intervals import Interval, Window, owned_intervals
from calendar_sync.reconcile import Appearance, ReconcilePlan, plan_reconcile


def sync_blocks(
    client: CalendarClient,
    calendar_id: str,
    source_id: str,
    label: str,
    intervals: Iterable[Interval],
    window: Window,
    *,
    dry_run: bool,
    color_id: str | None = None,
) -> ReconcilePlan:
    """Reconcile blocks that start inside the window. Earlier blocks are never touched.

    Kept blocks whose title or color no longer match `label` and `color_id` are updated in place.
    """
    desired = owned_intervals(intervals, window)
    existing = [
        block
        for block in client.list_blocks(calendar_id, source_id, window.start, window.end)
        if window.owns(block.interval.start)
    ]
    appearance = Appearance(block_summary(label), color_id)
    plan = plan_reconcile(desired, existing, appearance)
    if not dry_run:
        for block in plan.deletes:
            client.delete_block(calendar_id, block.event_id)
        for block in plan.updates:
            client.update_block(calendar_id, block.event_id, appearance.summary, color_id)
        for interval in plan.inserts:
            client.insert_block(calendar_id, source_id, label, interval, color_id)
    return plan


def clear_blocks(client: CalendarClient, calendar_id: str, source_id: str, since: datetime) -> int:
    """Delete a source's blocks that start at or after `since`. Returns how many were deleted."""
    blocks = [
        block
        for block in client.list_blocks(calendar_id, source_id, since, None)
        if block.interval.start >= since
    ]
    for block in blocks:
        client.delete_block(calendar_id, block.event_id)
    return len(blocks)
