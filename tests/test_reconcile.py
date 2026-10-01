# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

from calendar_sync.intervals import Interval
from calendar_sync.reconcile import Appearance, Block, plan_reconcile
from tests.helpers import utc

A = Interval(utc(1, 9), utc(1, 10))
B = Interval(utc(1, 13), utc(1, 14))
C = Interval(utc(1, 15), utc(1, 16))


def test_identical_sets_need_no_changes() -> None:
    plan = plan_reconcile([A, B], [Block("e1", A), Block("e2", B)])
    assert plan.inserts == ()
    assert plan.deletes == ()
    assert plan.is_empty


def test_new_meeting_is_inserted() -> None:
    plan = plan_reconcile([A, B], [Block("e1", A)])
    assert plan.inserts == (B,)
    assert plan.deletes == ()


def test_cancelled_meeting_is_deleted() -> None:
    plan = plan_reconcile([A], [Block("e1", A), Block("e2", B)])
    assert plan.inserts == ()
    assert plan.deletes == (Block("e2", B),)


def test_moved_meeting_is_a_delete_plus_an_insert() -> None:
    plan = plan_reconcile([C], [Block("e1", B)])
    assert plan.inserts == (C,)
    assert plan.deletes == (Block("e1", B),)


def test_duplicate_blocks_keep_one() -> None:
    plan = plan_reconcile([A], [Block("e2", A), Block("e1", A)])
    assert plan.inserts == ()
    assert plan.deletes == (Block("e2", A),)


def test_duplicate_desired_intervals_insert_once() -> None:
    assert plan_reconcile([A, A], []).inserts == (A,)


def test_inserts_are_sorted() -> None:
    assert plan_reconcile([C, A], []).inserts == (A, C)


LOOK = Appearance("busy (Google)", "8")


def test_matching_blocks_with_the_right_look_need_no_update() -> None:
    plan = plan_reconcile([A], [Block("e1", A, "busy (Google)", "8")], LOOK)
    assert plan.is_empty


def test_matching_blocks_with_an_old_color_or_title_are_updated() -> None:
    old_color = Block("e1", A, "busy (Google)", None)
    old_title = Block("e2", B, "busy (Work)", "8")
    plan = plan_reconcile([A, B], [old_color, old_title], LOOK)
    assert plan.updates == (old_color, old_title)
    assert plan.inserts == ()
    assert plan.deletes == ()
    assert not plan.is_empty


def test_deleted_duplicates_are_not_also_updated() -> None:
    plan = plan_reconcile([A], [Block("e1", A, "busy (Google)", "8"), Block("e2", A, "old")], LOOK)
    assert plan.updates == ()
    assert plan.deletes == (Block("e2", A, "old"),)


def test_without_an_appearance_nothing_is_updated() -> None:
    assert plan_reconcile([A], [Block("e1", A, "anything", "3")]).is_empty


def test_empty_desired_deletes_everything() -> None:
    plan = plan_reconcile([], [Block("e1", A)])
    assert plan.deletes == (Block("e1", A),)
    assert not plan.is_empty
