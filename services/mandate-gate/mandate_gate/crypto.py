"""Ed25519 signature verification for signed mandates."""

from __future__ import annotations

import base64
import binascii

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .canonical import canonical_mandate_bytes
from .models import SignedMandate


def verify_mandate_signature(
    mandate: SignedMandate,
    signature_b64: str,
    public_key_b64: str,
) -> bool:
    try:
        signature = base64.b64decode(signature_b64, validate=True)
        public_key = base64.b64decode(public_key_b64, validate=True)
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature,
            canonical_mandate_bytes(mandate),
        )
    except (ValueError, TypeError, binascii.Error, InvalidSignature):
        return False
    return True


def is_valid_public_key_b64(public_key_b64: str) -> bool:
    try:
        public_key = base64.b64decode(public_key_b64, validate=True)
        Ed25519PublicKey.from_public_bytes(public_key)
    except (ValueError, TypeError, binascii.Error):
        return False
    return True
