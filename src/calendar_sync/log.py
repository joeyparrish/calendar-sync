# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

"""Structured logs: Cloud Run turns each JSON line on stdout into a log entry."""

from __future__ import annotations

import json
import sys
from typing import Any


def log(severity: str, message: str, **fields: Any) -> None:
    entry = {"severity": severity, "message": message, **fields}
    print(json.dumps(entry, default=str), file=sys.stdout, flush=True)
