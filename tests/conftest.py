# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

"""Starts one Firestore emulator for the test session; each test gets its own project."""

import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient
from google.cloud import firestore

from calendar_sync.web import create_app
from tests.web_support import World


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_ready(host: str, proc: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("Firestore emulator exited during startup")
        try:
            with urllib.request.urlopen(f"http://{host}/", timeout=2):
                return
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    raise RuntimeError("Firestore emulator did not start within 90 seconds")


@pytest.fixture(scope="session")
def firestore_emulator() -> Iterator[str]:
    existing = os.environ.get("FIRESTORE_EMULATOR_HOST")
    if existing:
        yield existing
        return
    host = f"127.0.0.1:{_free_port()}"
    proc = subprocess.Popen(
        ["gcloud", "emulators", "firestore", "start", f"--host-port={host}", "--quiet"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        _wait_until_ready(host, proc)
        os.environ["FIRESTORE_EMULATOR_HOST"] = host
        yield host
    finally:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=30)
        os.environ.pop("FIRESTORE_EMULATOR_HOST", None)


@pytest.fixture
def db(firestore_emulator: str) -> firestore.Client:
    return firestore.Client(project=f"test-{uuid.uuid4().hex[:10]}")


@pytest.fixture
def world(db: firestore.Client) -> World:
    return World(db)


@pytest.fixture
def app(world: World) -> Flask:
    app = create_app(world.deps(), role="web", session_key="test-key", secure_cookies=False)
    app.config["WTF_CSRF_ENABLED"] = False
    return app


@pytest.fixture
def client(app: Flask) -> Iterator[FlaskClient]:
    with app.test_client() as c:
        yield c
