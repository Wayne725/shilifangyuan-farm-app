from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from typing import Any, Dict, Mapping, Union

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..config import Settings
from .common import IntegrationConfigurationError, IntegrationResponseError


PII_ENVELOPE_PREFIX = "pii"
PII_ENVELOPE_FORMAT_VERSION = "1"
PII_NONCE_BYTES = 12
PII_KEY_BYTES = 32
KEY_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,32}$")


def _decode_key(value: Union[str, bytes]) -> bytes:
    if isinstance(value, bytes):
        key = value
    else:
        try:
            key = base64.b64decode(value, validate=True)
        except (ValueError, TypeError) as exc:
            raise IntegrationConfigurationError(
                "PII encryption keys must be valid Base64"
            ) from exc
    if len(key) != PII_KEY_BYTES:
        raise IntegrationConfigurationError(
            "PII encryption keys must decode to 32 bytes"
        )
    return key


class VersionedPIICipher:
    """Encrypt private fields with a versioned AES-256-GCM keyring."""

    def __init__(
        self,
        keys: Mapping[str, Union[str, bytes]],
        current_version: str,
    ) -> None:
        if not keys:
            raise IntegrationConfigurationError(
                "At least one PII encryption key is required"
            )
        if not KEY_VERSION_PATTERN.fullmatch(current_version):
            raise IntegrationConfigurationError(
                "Invalid current PII encryption key version"
            )
        decoded: Dict[str, bytes] = {}
        for version, value in keys.items():
            if not KEY_VERSION_PATTERN.fullmatch(version):
                raise IntegrationConfigurationError(
                    "Invalid PII encryption key version"
                )
            decoded[version] = _decode_key(value)
        if current_version not in decoded:
            raise IntegrationConfigurationError(
                "Current PII encryption key version is missing"
            )
        self._keys = decoded
        self.current_version = current_version

    @classmethod
    def from_json(
        cls, serialized_keys: str, current_version: str
    ) -> "VersionedPIICipher":
        try:
            keys = json.loads(serialized_keys)
        except json.JSONDecodeError as exc:
            raise IntegrationConfigurationError(
                "PII encryption keyring must be valid JSON"
            ) from exc
        if not isinstance(keys, dict):
            raise IntegrationConfigurationError(
                "PII encryption keyring must be a JSON object"
            )
        return cls(keys, current_version)

    def encrypt_text(
        self,
        plaintext: str,
        *,
        associated_data: Union[str, bytes] = b"",
    ) -> str:
        if not isinstance(plaintext, str):
            raise TypeError("PII plaintext must be a string")
        aad = self._associated_data_bytes(associated_data)
        nonce = os.urandom(PII_NONCE_BYTES)
        ciphertext = AESGCM(self._keys[self.current_version]).encrypt(
            nonce,
            plaintext.encode("utf-8"),
            aad,
        )
        payload = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")
        return ":".join(
            (
                PII_ENVELOPE_PREFIX,
                PII_ENVELOPE_FORMAT_VERSION,
                self.current_version,
                payload,
            )
        )

    def decrypt_text(
        self,
        envelope: str,
        *,
        associated_data: Union[str, bytes] = b"",
    ) -> str:
        try:
            prefix, format_version, key_version, payload = envelope.split(
                ":", 3
            )
        except ValueError as exc:
            raise IntegrationResponseError(
                "Invalid encrypted PII envelope"
            ) from exc
        if (
            prefix != PII_ENVELOPE_PREFIX
            or format_version != PII_ENVELOPE_FORMAT_VERSION
        ):
            raise IntegrationResponseError("Unsupported encrypted PII envelope")
        key = self._keys.get(key_version)
        if key is None:
            raise IntegrationResponseError("Unknown PII encryption key version")
        try:
            decoded = base64.b64decode(
                payload.encode("ascii"),
                altchars=b"-_",
                validate=True,
            )
        except (ValueError, UnicodeEncodeError) as exc:
            raise IntegrationResponseError(
                "Invalid encrypted PII envelope"
            ) from exc
        if len(decoded) <= PII_NONCE_BYTES:
            raise IntegrationResponseError("Invalid encrypted PII envelope")
        nonce = decoded[:PII_NONCE_BYTES]
        ciphertext = decoded[PII_NONCE_BYTES:]
        aad = self._associated_data_bytes(associated_data)
        try:
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
            return plaintext.decode("utf-8")
        except (InvalidTag, UnicodeDecodeError) as exc:
            raise IntegrationResponseError(
                "Encrypted PII could not be authenticated"
            ) from exc

    def encrypt_json(
        self,
        value: Mapping[str, Any],
        *,
        associated_data: Union[str, bytes] = b"",
    ) -> str:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return self.encrypt_text(
            serialized,
            associated_data=associated_data,
        )

    def decrypt_json(
        self,
        envelope: str,
        *,
        associated_data: Union[str, bytes] = b"",
    ) -> Dict[str, Any]:
        plaintext = self.decrypt_text(
            envelope,
            associated_data=associated_data,
        )
        try:
            value = json.loads(plaintext)
        except json.JSONDecodeError as exc:
            raise IntegrationResponseError(
                "Decrypted PII was not valid JSON"
            ) from exc
        if not isinstance(value, dict):
            raise IntegrationResponseError(
                "Decrypted PII JSON must be an object"
            )
        return value

    @staticmethod
    def _associated_data_bytes(value: Union[str, bytes]) -> bytes:
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode("utf-8")
        raise TypeError("PII associated data must be a string or bytes")


def pii_cipher_from_settings(settings: Settings) -> VersionedPIICipher:
    if (
        settings.environment.strip().lower() == "preview"
        and not settings.pii_encryption_keys_json.strip()
    ):
        preview_key = hashlib.sha256(
            f"shilifangyuan-preview-pii:{settings.jwt_secret}".encode("utf-8")
        ).digest()
        return VersionedPIICipher(
            {settings.pii_encryption_current_version: preview_key},
            settings.pii_encryption_current_version,
        )
    return VersionedPIICipher.from_json(
        settings.pii_encryption_keys_json,
        settings.pii_encryption_current_version,
    )


def auth_outbox_aad(event_type: str, user_id: str) -> str:
    return f"auth-outbox:{event_type}:{user_id}"


def auth_outbox_cipher_from_settings(settings: Settings) -> VersionedPIICipher:
    environment = settings.environment.strip().lower()
    if settings.pii_encryption_keys_json.strip() or environment == "preview":
        return pii_cipher_from_settings(settings)
    derived_key = hashlib.sha256(
        f"shilifangyuan-auth-outbox:{settings.jwt_secret}".encode("utf-8")
    ).digest()
    return VersionedPIICipher({"runtime": derived_key}, "runtime")


def encrypt_auth_outbox_credential(
    settings: Settings,
    event_type: str,
    user_id: str,
    credential: str,
) -> str:
    return auth_outbox_cipher_from_settings(settings).encrypt_text(
        credential,
        associated_data=auth_outbox_aad(event_type, user_id),
    )


def decrypt_auth_outbox_credential(
    settings: Settings,
    event_type: str,
    user_id: str,
    envelope: str,
) -> str:
    return auth_outbox_cipher_from_settings(settings).decrypt_text(
        envelope,
        associated_data=auth_outbox_aad(event_type, user_id),
    )
