"""Controlled Ed25519 mandate signing for Process C."""

from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .canonical import canonical_mandate_bytes
from .models import SignedMandate

PRIVATE_KEY_ENV = "MANDATE_SIGNING_PRIVATE_KEY_B64"
ED25519_PRIVATE_KEY_BYTES = 32


class PrivateKeyConfigurationError(ValueError):
    """Raised when the Process C private signing key is unavailable or invalid."""


@dataclass(frozen=True)
class SignedMandateEnvelope:
    mandate: SignedMandate
    signature_b64: str


def load_signing_private_key(
    environment: dict[str, str] | None = None,
) -> Ed25519PrivateKey:
    values = os.environ if environment is None else environment
    encoded = values.get(PRIVATE_KEY_ENV, "").strip()
    if not encoded:
        raise PrivateKeyConfigurationError(
            f"{PRIVATE_KEY_ENV} is missing from Process C configuration."
        )
    try:
        raw_key = base64.b64decode(encoded, validate=True)
        if len(raw_key) != ED25519_PRIVATE_KEY_BYTES:
            raise ValueError
        return Ed25519PrivateKey.from_private_bytes(raw_key)
    except (ValueError, TypeError, binascii.Error) as error:
        raise PrivateKeyConfigurationError(
            f"{PRIVATE_KEY_ENV} is malformed."
        ) from error


def sign_mandate(
    mandate: SignedMandate | dict[str, Any],
    *,
    environment: dict[str, str] | None = None,
) -> SignedMandateEnvelope:
    """Sign a server-constructed mandate, never returning the private key."""
    parsed_mandate = (
        mandate
        if isinstance(mandate, SignedMandate)
        else SignedMandate.model_validate(mandate)
    )
    private_key = load_signing_private_key(environment)
    signature = private_key.sign(canonical_mandate_bytes(parsed_mandate))
    return SignedMandateEnvelope(
        mandate=parsed_mandate,
        signature_b64=base64.b64encode(signature).decode("ascii"),
    )
