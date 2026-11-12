from __future__ import annotations

import base64
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict, Field

from .db import PaymentPolicyDbAdapter
from .signing import load_signing_private_key
from .versioned_crypto import (
    canonical_mandate_v2_bytes,
    mandate_v2_digest_b64,
    sign_mandate_v2,
)
from .versioned_models import MandateV2


class IssueMandateV2Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    negotiation_id: str = Field(min_length=1)
    paypal_order_id: str = Field(min_length=1)
    authorized_cap_cents: int = Field(ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    allowed_product_categories: list[str] = Field(min_length=1)
    merchant_restrictions: list[str] = Field(min_length=1)
    expected_paypal_payee_id: str | None = Field(default=None, min_length=1)
    expiry_seconds: int = Field(default=7200, gt=0)
    mandate_id: str | None = None


class IssueMandateV2Response(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mandate: MandateV2
    signature_b64: str
    public_key_b64: str
    mandate_digest_b64: str
    nonce: str
    shopper_id: str


def issue_and_record_mandate_v2(
    request: IssueMandateV2Request,
    shopper_id: str,
    db_adapter: PaymentPolicyDbAdapter,
    private_key: Ed25519PrivateKey | None = None,
) -> IssueMandateV2Response:
    if not shopper_id or not shopper_id.strip():
        raise ValueError("Authenticated shopper identity is required.")

    signing_key = private_key or load_signing_private_key()
    public_key_bytes = signing_key.public_key().public_bytes_raw()
    public_key_b64 = base64.b64encode(public_key_bytes).decode("ascii")

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=request.expiry_seconds)
    nonce = secrets.token_hex(16)
    mandate_id = request.mandate_id or f"mnd_{uuid4().hex[:16]}"

    mandate = MandateV2(
        schema_version="mandate.v2",
        mandate_id=mandate_id,
        negotiation_id=request.negotiation_id,
        paypal_order_id=request.paypal_order_id,
        authorized_cap_cents=request.authorized_cap_cents,
        currency=request.currency.upper(),
        allowed_product_categories=request.allowed_product_categories,
        merchant_restrictions=request.merchant_restrictions,
        expected_paypal_payee_id=request.expected_paypal_payee_id,
        issued_at=now,
        expires_at=expires_at,
    )

    signature_b64 = sign_mandate_v2(mandate, signing_key)
    digest_b64 = mandate_v2_digest_b64(mandate)

    raw_signature = base64.b64decode(signature_b64)
    raw_public_key = base64.b64decode(public_key_b64)
    verifier = Ed25519PublicKey.from_public_bytes(raw_public_key)
    try:
        verifier.verify(raw_signature, canonical_mandate_v2_bytes(mandate))
    except InvalidSignature as exc:
        raise ValueError("Mandate signature round-trip verification failed.") from exc

    try:
        negotiation_uuid = UUID(request.negotiation_id)
    except ValueError:
        negotiation_uuid = uuid4()
        mandate = MandateV2(
            schema_version="mandate.v2",
            mandate_id=mandate_id,
            negotiation_id=str(negotiation_uuid),
            paypal_order_id=request.paypal_order_id,
            authorized_cap_cents=request.authorized_cap_cents,
            currency=request.currency.upper(),
            allowed_product_categories=request.allowed_product_categories,
            merchant_restrictions=request.merchant_restrictions,
            expected_paypal_payee_id=request.expected_paypal_payee_id,
            issued_at=now,
            expires_at=expires_at,
        )
        signature_b64 = sign_mandate_v2(mandate, signing_key)
        digest_b64 = mandate_v2_digest_b64(mandate)

    try:
        db_adapter.save_negotiation(
            shopper_id=shopper_id,
            request_prompt=f"Negotiation {negotiation_uuid}",
            budget_cents=request.authorized_cap_cents,
            currency_code=request.currency.upper(),
        )
    except Exception:
        pass

    db_adapter.save_mandate_v2(
        mandate=mandate,
        public_key=public_key_b64,
        signature=signature_b64,
        nonce=nonce,
    )

    return IssueMandateV2Response(
        mandate=mandate,
        signature_b64=signature_b64,
        public_key_b64=public_key_b64,
        mandate_digest_b64=digest_b64,
        nonce=nonce,
        shopper_id=shopper_id,
    )
