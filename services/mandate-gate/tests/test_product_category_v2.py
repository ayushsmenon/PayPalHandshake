import base64
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mandate_gate.product_reference import (
    ProductReferenceV2,
    SignedProductReferenceV2,
    canonical_product_reference_v2_bytes,
)
from mandate_gate.versioned_gate import evaluate_v2_candidate, mandate_v2_digest_b64
from mandate_gate.versioned_models import (
    CandidateDealV2,
    MandateV2,
    PostAuthorizationBindingV1,
)
from mandate_gate.models import ProductLineV2
from mandate_gate.versioned_crypto import (
    canonical_mandate_v2_bytes,
    canonical_post_authorization_binding_v1_bytes,
)


NOW = datetime(2027, 1, 1, tzinfo=timezone.utc)


def context(category: str | None = "electronics"):
    key = Ed25519PrivateKey.generate()
    public_key = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    mandate = MandateV2(
        schema_version="mandate.v2",
        mandate_id="m-1",
        negotiation_id="n-1",
        paypal_order_id="o-1",
        authorized_cap_cents=1000,
        currency="USD",
        allowed_product_categories=["electronics"],
        merchant_restrictions=["merchant.example"],
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(hours=1),
    )
    binding = PostAuthorizationBindingV1(
        schema_version="post-authorization-binding.v1",
        binding_id="b-1",
        mandate_id=mandate.mandate_id,
        mandate_digest_b64=mandate_v2_digest_b64(mandate),
        paypal_order_id="o-1",
        paypal_auth_id="a-1",
        authorization_status="AUTHORIZED",
        authorized_amount_cents=1000,
        currency="USD",
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(hours=1),
    )
    reference = ProductReferenceV2(
        schema_version="product-reference.v2",
        channel3_product_id="p-1",
        offer_url="https://merchant.example/p-1",
        merchant_domain="merchant.example",
        price_minor=500,
        currency="USD",
        availability="InStock",
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(hours=1),
        canonical_category=category or "",
        registry_version="test-1",
    )
    signed_reference = SignedProductReferenceV2(
        reference=reference,
        signature_b64=base64.b64encode(
            key.sign(canonical_product_reference_v2_bytes(reference))
        ).decode(),
    )
    candidate = CandidateDealV2(
        negotiation_id="n-1",
        paypal_order_id="o-1",
        merchant_id="merchant.example",
        total_cents=500,
        currency="USD",
        products=[
            ProductLineV2(
                product_identifier="p-1",
                quantity=1,
                description="Reviewed product",
                unit_price_cents=500,
                offer_url=reference.offer_url,
                product_reference=signed_reference,
            )
        ],
    )
    mandate_signature = base64.b64encode(
        key.sign(canonical_mandate_v2_bytes(mandate))
    ).decode()
    binding_signature = base64.b64encode(
        key.sign(canonical_post_authorization_binding_v1_bytes(binding))
    ).decode()
    return mandate, binding, candidate, mandate_signature, binding_signature, public_key


def test_allowed_category_passes() -> None:
    values = context()
    result = evaluate_v2_candidate(
        values[0], values[3], values[1], values[4], values[2],
        values[5], values[5], now=NOW,
    )
    assert result.verdict == "PASS"


@pytest.mark.parametrize("category", ["toys", None])
def test_disallowed_or_missing_category_rejects(category: str | None) -> None:
    if category is None:
        with pytest.raises(Exception):
            context(category)
        return
    values = context(category)
    result = evaluate_v2_candidate(
        values[0], values[3], values[1], values[4], values[2],
        values[5], values[5], now=NOW,
    )
    assert result.verdict == "REJECT"


def test_v1_reference_is_not_accepted_by_v2_candidate() -> None:
    values = context()
    payload = values[2].model_dump(mode="json")
    payload["products"][0]["product_reference"]["reference"]["schema_version"] = "product-reference.v1"
    with pytest.raises(Exception):
        CandidateDealV2.model_validate(payload)
