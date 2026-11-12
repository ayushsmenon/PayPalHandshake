from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .paypal_adapter import ObservedPayPalAuthorization, ObservedPayPalOrder
from .signing import load_signing_private_key
from .versioned_crypto import (
    canonical_post_authorization_binding_v1_bytes,
    mandate_v2_digest_b64,
    sign_post_authorization_binding_v1,
)
from .versioned_models import MandateV2, PostAuthorizationBindingV1


def issue_post_authorization_binding(
    mandate: MandateV2,
    order: ObservedPayPalOrder,
    authorization: ObservedPayPalAuthorization,
    private_key: Ed25519PrivateKey | None = None,
    binding_id: str | None = None,
    now: datetime | None = None,
    ttl: timedelta = timedelta(days=365),
) -> tuple[PostAuthorizationBindingV1, str, str]:
    if order.order_id != mandate.paypal_order_id:
        raise ValueError(
            f"Order ID mismatch: expected '{mandate.paypal_order_id}', got '{order.order_id}'"
        )

    if authorization.order_id != order.order_id:
        raise ValueError(
            f"Authorization order mismatch: expected '{order.order_id}', got '{authorization.order_id}'"
        )

    if authorization.status != "AUTHORIZED":
        raise ValueError(f"Authorization is not in AUTHORIZED status: got '{authorization.status}'")

    if authorization.amount_cents > mandate.authorized_cap_cents:
        raise ValueError(
            f"Authorized amount ({authorization.amount_cents}) exceeds mandate cap ({mandate.authorized_cap_cents})"
        )

    signing_key = private_key or load_signing_private_key()
    public_key_b64 = base64.b64encode(
        signing_key.public_key().public_bytes_raw()
    ).decode("ascii")

    issue_time = now or datetime.now(timezone.utc)
    expires_time = issue_time + ttl
    bid = binding_id or f"bind_{uuid4().hex[:16]}"
    digest = mandate_v2_digest_b64(mandate)

    binding = PostAuthorizationBindingV1(
        schema_version="post-authorization-binding.v1",
        binding_id=bid,
        mandate_id=mandate.mandate_id,
        mandate_digest_b64=digest,
        paypal_order_id=order.order_id,
        paypal_auth_id=authorization.authorization_id,
        authorization_status="AUTHORIZED",
        authorized_amount_cents=authorization.amount_cents,
        currency=authorization.currency.upper(),
        issued_at=issue_time,
        expires_at=expires_time,
    )

    signature_b64 = sign_post_authorization_binding_v1(binding, signing_key)

    raw_sig = base64.b64decode(signature_b64)
    raw_pk = base64.b64decode(public_key_b64)
    verifier = Ed25519PublicKey.from_public_bytes(raw_pk)
    try:
        verifier.verify(raw_sig, canonical_post_authorization_binding_v1_bytes(binding))
    except InvalidSignature as exc:
        raise ValueError("Post-authorization binding signature verification failed.") from exc

    return binding, signature_b64, public_key_b64
