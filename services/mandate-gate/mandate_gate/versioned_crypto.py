"""Domain-separated verification for versioned Process C objects."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .versioned_models import MandateV2, PostAuthorizationBindingV1


MANDATE_V2_DOMAIN = b"PAYPAL-HANDSHAKE/MANDATE/v2\0"
POST_AUTHORIZATION_BINDING_V1_DOMAIN = (
    b"PAYPAL-HANDSHAKE/POST-AUTHORIZATION-BINDING/v1\0"
)


def _canonical(domain: bytes, value: Any) -> bytes:
    return domain + json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_mandate_v2_bytes(mandate: MandateV2) -> bytes:
    return _canonical(MANDATE_V2_DOMAIN, mandate)


def canonical_post_authorization_binding_v1_bytes(
    binding: PostAuthorizationBindingV1,
) -> bytes:
    return _canonical(POST_AUTHORIZATION_BINDING_V1_DOMAIN, binding)


def verify_versioned_signature(
    value: MandateV2 | PostAuthorizationBindingV1,
    signature_b64: str,
    public_key_b64: str,
) -> bool:
    domain = (
        MANDATE_V2_DOMAIN
        if isinstance(value, MandateV2)
        else POST_AUTHORIZATION_BINDING_V1_DOMAIN
    )
    try:
        signature = base64.b64decode(signature_b64, validate=True)
        public_key = base64.b64decode(public_key_b64, validate=True)
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature,
            _canonical(domain, value),
        )
    except (ValueError, TypeError, binascii.Error, InvalidSignature):
        return False
    return True
