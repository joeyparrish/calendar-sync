# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

import json

import pytest

from calendar_sync.log import log


def test_log_writes_one_json_line(capsys: pytest.CaptureFixture[str]) -> None:
    log("WARNING", "source failed", event="source_failed", account="a@example.com")
    line = capsys.readouterr().out.strip()
    assert json.loads(line) == {
        "severity": "WARNING",
        "message": "source failed",
        "event": "source_failed",
        "account": "a@example.com",
    }
