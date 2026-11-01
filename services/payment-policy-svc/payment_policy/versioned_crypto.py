"""Domain-separated Process C signing for versioned objects."""

from __future__ import annotations

import base64
import hashlib
import json

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .versioned_models import MandateV2, PostAuthorizationBindingV1


MANDATE_V2_DOMAIN = b"PAYPAL-HANDSHAKE/MANDATE/v2\0"
POST_AUTHORIZATION_BINDING_V1_DOMAIN = (
    b"PAYPAL-HANDSHAKE/POST-AUTHORIZATION-BINDING/v1\0"
)


def _canonical(domain: bytes, value: MandateV2 | PostAuthorizationBindingV1) -> bytes:
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


def mandate_v2_digest_b64(mandate: MandateV2) -> str:
    return base64.b64encode(
        hashlib.sha256(canonical_mandate_v2_bytes(mandate)).digest()
    ).decode("ascii")


def sign_mandate_v2(mandate: MandateV2, private_key: Ed25519PrivateKey) -> str:
    return base64.b64encode(
        private_key.sign(canonical_mandate_v2_bytes(mandate))
    ).decode("ascii")


def sign_post_authorization_binding_v1(
    binding: PostAuthorizationBindingV1,
    private_key: Ed25519PrivateKey,
) -> str:
    return base64.b64encode(
        private_key.sign(canonical_post_authorization_binding_v1_bytes(binding))
    ).decode("ascii")
