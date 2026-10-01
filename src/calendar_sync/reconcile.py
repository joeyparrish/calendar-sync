# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

"""Decide which blocks to insert, update, and delete to match the desired busy times."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from calendar_sync.intervals import Interval


@dataclass(frozen=True)
class Block:
    """A placeholder event this tool created on the destination calendar."""

    event_id: str
    interval: Interval
    summary: str | None = None
    color_id: str | None = None


@dataclass(frozen=True)
class Appearance:
    """How a source's blocks should look: their title and Google event color ID."""

    summary: str
    color_id: str | None


@dataclass(frozen=True)
class ReconcilePlan:
    inserts: tuple[Interval, ...]
    deletes: tuple[Block, ...]
    updates: tuple[Block, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.inserts and not self.deletes and not self.updates


def plan_reconcile(
    desired: Iterable[Interval],
    existing: Iterable[Block],
    appearance: Appearance | None = None,
) -> ReconcilePlan:
    """Match blocks to desired intervals by exact start and end.

    Each desired interval keeps at most one block (the one with the smallest event ID); every
    other block is deleted, and every desired interval without a block is inserted. A kept block
    whose title or color differs from `appearance` is updated in place.
    """
    wanted = sorted(set(desired))
    unmatched = set(wanted)
    deletes: list[Block] = []
    updates: list[Block] = []
    for block in sorted(existing, key=lambda b: (b.interval, b.event_id)):
        if block.interval in unmatched:
            unmatched.discard(block.interval)
            if appearance is not None and (
                block.summary != appearance.summary or block.color_id != appearance.color_id
            ):
                updates.append(block)
        else:
            deletes.append(block)
    inserts = tuple(interval for interval in wanted if interval in unmatched)
    return ReconcilePlan(inserts=inserts, deletes=tuple(deletes), updates=tuple(updates))
