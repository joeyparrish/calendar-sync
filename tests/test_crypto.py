# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

from types import SimpleNamespace
from typing import Any

import pytest

from calendar_sync.crypto import KmsCrypto
from tests.fakes import FakeCrypto

KEY = "projects/p/locations/us-west1/keyRings/r/cryptoKeys/k"


class StubKms:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def encrypt(self, request: dict[str, Any]) -> Any:
        self.requests.append(("encrypt", request))
        return SimpleNamespace(ciphertext=b"sealed")

    def decrypt(self, request: dict[str, Any]) -> Any:
        self.requests.append(("decrypt", request))
        return SimpleNamespace(plaintext=b"refresh-token")


def test_kms_encrypt_binds_the_context() -> None:
    kms = StubKms()
    assert KmsCrypto(KEY, kms).encrypt("refresh-token", "joey@example.com") == b"sealed"
    assert kms.requests == [
        (
            "encrypt",
            {
                "name": KEY,
                "plaintext": b"refresh-token",
                "additional_authenticated_data": b"joey@example.com",
            },
        )
    ]


def test_kms_decrypt_binds_the_context() -> None:
    kms = StubKms()
    assert KmsCrypto(KEY, kms).decrypt(b"sealed", "joey@example.com") == "refresh-token"
    assert kms.requests[0][1]["additional_authenticated_data"] == b"joey@example.com"


def test_fake_crypto_round_trips_and_hides_plaintext() -> None:
    crypto = FakeCrypto()
    sealed = crypto.encrypt("secret-token", "a@example.com")
    assert b"secret-token" not in sealed
    assert crypto.decrypt(sealed, "a@example.com") == "secret-token"


def test_fake_crypto_refuses_another_context() -> None:
    crypto = FakeCrypto()
    with pytest.raises(ValueError):
        crypto.decrypt(crypto.encrypt("secret", "a@example.com"), "b@example.com")
