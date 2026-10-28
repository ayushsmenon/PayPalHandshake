import base64
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mandate_gate.gate import evaluate_candidate
from mandate_gate.canonical import canonical_mandate_bytes
from mandate_gate.models import SignedMandate
from mandate_gate.product_reference import (
    ProductReference,
    SignedProductReference,
    canonical_product_reference_bytes,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def signed_reference(
    private_key: Ed25519PrivateKey,
    *,
    price_minor: int = 32900,
    merchant_domain: str = "example.com",
    expires_at: datetime | None = None,
    issued_at: datetime = NOW,
) -> SignedProductReference:
    reference = ProductReference(
        schema_version="product-reference.v1",
        channel3_product_id="PpawoVb",
        offer_url="https://example.com/product-123",
        merchant_domain=merchant_domain,
        price_minor=price_minor,
        currency="USD",
        availability="InStock",
        condition="new",
        issued_at=issued_at,
        expires_at=expires_at or NOW + timedelta(hours=1),
    )
    signature = private_key.sign(canonical_product_reference_bytes(reference))
    return SignedProductReference(
        reference=reference,
        signature_b64=base64.b64encode(signature).decode("ascii"),
    )


def mandate_and_candidate(reference: SignedProductReference) -> tuple[dict, dict]:
    mandate = {
        "schema_version": "1",
        "negotiation_id": "N-1",
        "paypal_auth_id": "A-1",
        "authorized_cap_cents": 45000,
        "currency": "USD",
        "authorized_merchant_id": "example.com",
        "allowed_product_identifiers": ["PpawoVb"],
        "issued_at": NOW,
        "expires_at": NOW + timedelta(hours=2),
        "mandate_id": "M-1",
        "product_references": [reference.model_dump(mode="json")],
    }
    candidate = {
        "negotiation_id": "N-1",
        "paypal_auth_id": "A-1",
        "merchant_id": "example.com",
        "total_cents": 32900,
        "currency": "USD",
        "products": [
            {
                "product_identifier": "PpawoVb",
                "quantity": 1,
                "description": "MSI PRO MP273U",
                "unit_price_cents": reference.reference.price_minor,
                "offer_url": reference.reference.offer_url,
                "product_reference": reference.model_dump(mode="json"),
            }
        ],
    }
    return mandate, candidate


def test_valid_reference_is_verified_but_payment_is_ready() -> None:
    private_key = Ed25519PrivateKey.generate()
    reference = signed_reference(private_key)
    mandate, candidate = mandate_and_candidate(reference)
    public_key = base64.b64encode(private_key.public_key().public_bytes_raw()).decode()

    mandate_model = SignedMandate.model_validate(mandate)
    mandate_signature = base64.b64encode(
        private_key.sign(canonical_mandate_bytes(mandate_model))
    ).decode()
    result = evaluate_candidate(mandate, mandate_signature, candidate, public_key, now=NOW)

    assert result.verdict == "PASS"
    assert result.payment_ready is True


def test_wrong_signature_expiry_price_and_merchant_reject() -> None:
    private_key = Ed25519PrivateKey.generate()
    other_key = Ed25519PrivateKey.generate()
    for reference in [
        signed_reference(other_key),
        signed_reference(
            private_key,
            issued_at=NOW - timedelta(hours=2),
            expires_at=NOW - timedelta(seconds=1),
        ),
        signed_reference(private_key, price_minor=33000),
        signed_reference(private_key, merchant_domain="other.com"),
    ]:
        mandate, candidate = mandate_and_candidate(reference)
        public_key = base64.b64encode(private_key.public_key().public_bytes_raw()).decode()
        mandate_model = SignedMandate.model_validate(mandate)
        mandate_signature = base64.b64encode(
            private_key.sign(canonical_mandate_bytes(mandate_model))
        ).decode()
        result = evaluate_candidate(
            mandate, mandate_signature, candidate, public_key, now=NOW
        )
        assert result.verdict == "REJECT"


def test_missing_reference_remains_fail_closed() -> None:
    private_key = Ed25519PrivateKey.generate()
    reference = signed_reference(private_key)
    mandate, candidate = mandate_and_candidate(reference)
    del candidate["products"][0]["product_reference"]
    public_key = base64.b64encode(private_key.public_key().public_bytes_raw()).decode()

    result = evaluate_candidate(mandate, "not-used", candidate, public_key, now=NOW)

    assert result.verdict == "REJECT"
    assert result.payment_ready is False
