# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

"""Sources turn an outside calendar into busy intervals."""


class SourceError(Exception):
    """A source could not produce trustworthy busy intervals.

    Callers must treat this as "unknown", never as "free".
    """
