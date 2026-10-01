# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

import json
from pathlib import Path

import pytest

from calendar_sync.main import create_app_from_env


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, firestore_emulator: str) -> None:
    client_file = tmp_path / "client.json"
    client_file.write_text(json.dumps({"web": {"client_id": "id", "client_secret": "s"}}))
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-main")
    monkeypatch.setenv("KMS_KEY", "projects/p/locations/l/keyRings/r/cryptoKeys/k")
    monkeypatch.setenv("ALLOWED_DOMAIN", "example.com")
    monkeypatch.setenv("PUBLIC_URL", "https://app.example/")
    monkeypatch.setenv("OAUTH_CLIENT_FILE", str(client_file))


def test_web_role_needs_a_session_key(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROLE", "web")
    monkeypatch.delenv("SESSION_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SESSION_KEY"):
        create_app_from_env()


def test_web_role(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROLE", "web")
    monkeypatch.setenv("SESSION_KEY", "k")
    app = create_app_from_env()
    with app.test_client() as c:
        page = c.get("/", base_url="https://app.example").get_data(as_text=True)
        assert 'href="/login"' in page
        other = c.get("/", base_url="https://old.run.app")
        assert other.status_code == 301
        assert other.headers["Location"] == "https://app.example/"


def test_worker_role(env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROLE", "worker")
    with create_app_from_env().test_client() as c:
        assert c.get("/health").get_data(as_text=True) == "ok"
        assert c.get("/").status_code == 404
