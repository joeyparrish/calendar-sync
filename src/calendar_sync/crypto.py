# Copyright 2026 Joey Parrish
# SPDX-License-Identifier: GPL-3.0-or-later

"""Application-level encryption for stored credentials, backed by Cloud KMS."""

from __future__ import annotations

from typing import Any, Protocol


class Crypto(Protocol):
    def encrypt(self, plaintext: str, context: str) -> bytes: ...

    def decrypt(self, ciphertext: bytes, context: str) -> str: ...


class KmsCrypto:
    """Encrypts with one KMS key. `context` (the account email) is bound as additional
    authenticated data, so a ciphertext copied to another account will not decrypt."""

    def __init__(self, key_name: str, client: Any | None = None) -> None:
        self._key_name = key_name
        self._client = client

    def _kms(self) -> Any:
        if self._client is None:
            from google.cloud import kms

            self._client = kms.KeyManagementServiceClient()
        return self._client

    def encrypt(self, plaintext: str, context: str) -> bytes:
        response = self._kms().encrypt(
            request={
                "name": self._key_name,
                "plaintext": plaintext.encode(),
                "additional_authenticated_data": context.encode(),
            }
        )
        return response.ciphertext

    def decrypt(self, ciphertext: bytes, context: str) -> str:
        response = self._kms().decrypt(
            request={
                "name": self._key_name,
                "ciphertext": ciphertext,
                "additional_authenticated_data": context.encode(),
            }
        )
        return response.plaintext.decode()
