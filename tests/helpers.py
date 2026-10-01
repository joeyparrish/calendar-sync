# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import UTC, datetime


def utc(day: int, hour: int, minute: int = 0, month: int = 10) -> datetime:
    """A UTC datetime in 2026, October unless another month is given."""
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)
