import base64
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from mandate_gate.api import app
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mandate_gate.models import ProductLine
from mandate_gate.product_reference import (
    ProductReference,
    SignedProductReference,
    canonical_product_reference_bytes,
)
from mandate_gate.versioned_crypto import (
    canonical_mandate_v2_bytes,
    canonical_post_authorization_binding_v1_bytes,
)
from mandate_gate.versioned_gate import evaluate_v2_candidate, mandate_v2_digest_b64
from mandate_gate.versioned_models import (
    CandidateDealV2,
    MandateV2,
    PostAuthorizationBindingV1,
)


NOW = datetime(2027, 1, 1, tzinfo=timezone.utc)


def signed_objects() -> tuple[
    MandateV2,
    PostAuthorizationBindingV1,
    CandidateDealV2,
    str,
    str,
    str,
]:
    private_key = Ed25519PrivateKey.generate()
    public_key = base64.b64encode(private_key.public_key().public_bytes_raw()).decode()
    mandate = MandateV2(
        schema_version="mandate.v2",
        mandate_id="mandate-v2",
        negotiation_id="negotiation-v2",
        paypal_order_id="ORDER-1",
        authorized_cap_cents=45000,
        currency="USD",
        allowed_product_categories=["electronics"],
        merchant_restrictions=["newegg.com"],
        issued_at=NOW - timedelta(minutes=5),
        expires_at=NOW + timedelta(hours=1),
    )
    binding = PostAuthorizationBindingV1(
        schema_version="post-authorization-binding.v1",
        binding_id="binding-1",
        mandate_id=mandate.mandate_id,
        mandate_digest_b64=mandate_v2_digest_b64(mandate),
        paypal_order_id=mandate.paypal_order_id,
        paypal_auth_id="AUTH-1",
        authorization_status="AUTHORIZED",
        authorized_amount_cents=45000,
        currency="USD",
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(hours=1),
    )
    reference = ProductReference(
        schema_version="product-reference.v1",
        channel3_product_id="P-1",
        offer_url="https://newegg.com/p/P-1",
        merchant_domain="newegg.com",
        price_minor=41850,
        currency="USD",
        availability="InStock",
        condition="new",
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(hours=1),
    )
    signed_reference = SignedProductReference(
        reference=reference,
        signature_b64=base64.b64encode(
            private_key.sign(canonical_product_reference_bytes(reference))
        ).decode(),
    )
    candidate = CandidateDealV2(
        negotiation_id=mandate.negotiation_id,
        paypal_order_id=mandate.paypal_order_id,
        merchant_id="newegg.com",
        total_cents=41850,
        currency="USD",
        products=[
            ProductLine(
                product_identifier="P-1",
                quantity=1,
                description="Monitor",
                unit_price_cents=41850,
                offer_url=reference.offer_url,
                product_reference=signed_reference,
            )
        ],
    )
    mandate_signature = base64.b64encode(
        private_key.sign(canonical_mandate_v2_bytes(mandate))
    ).decode()
    binding_signature = base64.b64encode(
        private_key.sign(canonical_post_authorization_binding_v1_bytes(binding))
    ).decode()
    return mandate, binding, candidate, mandate_signature, binding_signature, public_key


def test_v2_accepts_lower_final_deal_without_initial_product_references() -> None:
    mandate, binding, candidate, mandate_signature, binding_signature, public_key = signed_objects()

    result = evaluate_v2_candidate(
        mandate,
        mandate_signature,
        binding,
        binding_signature,
        candidate,
        public_key,
        public_key,
        now=NOW,
    )

    assert result.verdict == "PASS"
    assert result.payment_ready is True
    assert mandate.model_dump()["schema_version"] == "mandate.v2"


def test_v2_rejects_tampered_binding_and_over_cap_deal() -> None:
    mandate, binding, candidate, mandate_signature, binding_signature, public_key = signed_objects()
    binding.authorized_amount_cents = 1
    candidate.total_cents = 45001

    result = evaluate_v2_candidate(
        mandate,
        mandate_signature,
        binding,
        binding_signature,
        candidate,
        public_key,
        public_key,
        now=NOW,
    )

    assert result.verdict == "REJECT"
    assert result.payment_ready is False
    assert any(check.name == "authorization_amount" and check.verdict == "REJECT" for check in result.checks)


def test_signature_domains_are_not_interchangeable() -> None:
    mandate, binding, _, mandate_signature, _, public_key = signed_objects()

    assert mandate_signature
    assert canonical_mandate_v2_bytes(mandate) != canonical_post_authorization_binding_v1_bytes(binding)
    assert mandate_v2_digest_b64(mandate)


def test_v2_endpoint_verifies_signed_binding(monkeypatch) -> None:
    mandate, binding, candidate, mandate_signature, binding_signature, public_key = signed_objects()
    monkeypatch.setenv("MANDATE_GATE_PUBLIC_KEY_B64", public_key)
    monkeypatch.setenv("MANDATE_GATE_BINDING_PUBLIC_KEY_B64", public_key)
    response = TestClient(app).post(
        "/api/v2/verify-authorization-binding",
        json={
            "mandate": mandate.model_dump(mode="json"),
            "mandate_signature": mandate_signature,
            "post_authorization_binding": binding.model_dump(mode="json"),
            "binding_signature": binding_signature,
            "candidate": candidate.model_dump(mode="json"),
        },
    )

    assert response.status_code == 200
    assert response.json()["verdict"] == "PASS"
