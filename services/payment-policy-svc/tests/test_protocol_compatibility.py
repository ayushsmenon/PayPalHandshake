import base64
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mandate-gate"))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mandate_gate.crypto import verify_mandate_signature
from mandate_gate.models import SignedMandate as GateMandate
from mandate_gate.product_reference import (
    ProductReference as GateProductReference,
    SignedProductReference as GateSignedProductReference,
    verify_product_reference_signature,
)
from payment_policy.canonical import canonical_mandate_bytes
from payment_policy.models import SignedMandate
from payment_policy.product_reference import (
    ProductReference,
    canonical_product_reference_bytes,
    sign_product_reference,
)


def fixture_mandate() -> SignedMandate:
    return SignedMandate(
        schema_version="1",
        negotiation_id="fixture-negotiation",
        paypal_auth_id="AUTH-FIXTURE",
        authorized_cap_cents=45000,
        currency="USD",
        authorized_merchant_id="merchant.example",
        allowed_product_identifiers=["P-1"],
        issued_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        expires_at=datetime(2027, 1, 2, tzinfo=timezone.utc),
        mandate_id="mandate-fixture",
    )


def fixture_reference() -> ProductReference:
    return ProductReference(
        schema_version="product-reference.v1",
        channel3_product_id="P-1",
        offer_url="https://merchant.example/p/P-1",
        merchant_domain="merchant.example",
        price_minor=41850,
        currency="USD",
        availability="InStock",
        condition="new",
        issued_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        expires_at=datetime(2027, 1, 2, tzinfo=timezone.utc),
    )


def test_v1_mandate_canonical_bytes_are_golden() -> None:
    assert canonical_mandate_bytes(fixture_mandate()) == (
        b'{"allowed_product_identifiers":["P-1"],"authorized_cap_cents":45000,'
        b'"authorized_merchant_id":"merchant.example","currency":"USD",'
        b'"expires_at":"2027-01-02T00:00:00Z","issued_at":"2027-01-01T00:00:00Z",'
        b'"mandate_id":"mandate-fixture","negotiation_id":"fixture-negotiation",'
        b'"paypal_auth_id":"AUTH-FIXTURE","product_references":[],"schema_version":"1"}'
    )


def test_product_reference_v1_canonical_bytes_are_golden() -> None:
    assert canonical_product_reference_bytes(fixture_reference()) == (
        b"PAYPAL-HANDSHAKE/PRODUCT-REFERENCE/v1\0"
        b'{"availability":"InStock","channel3_product_id":"P-1","condition":"new",'
        b'"currency":"USD","expires_at":"2027-01-02T00:00:00Z",'
        b'"issued_at":"2027-01-01T00:00:00Z","merchant_domain":"merchant.example",'
        b'"offer_url":"https://merchant.example/p/P-1","price_minor":41850,'
        b'"schema_version":"product-reference.v1"}'
    )


def test_existing_cross_service_signatures_remain_compatible() -> None:
    key = Ed25519PrivateKey.generate()
    public_key = base64.b64encode(key.public_key().public_bytes_raw()).decode()
    mandate = fixture_mandate()
    mandate_signature = base64.b64encode(
        key.sign(canonical_mandate_bytes(mandate))
    ).decode()
    assert verify_mandate_signature(
        GateMandate.model_validate(mandate.model_dump(mode="json")),
        mandate_signature,
        public_key,
    )

    reference = fixture_reference()
    signed_reference = sign_product_reference(reference, key)
    gate_reference = GateSignedProductReference.model_validate(
        signed_reference.model_dump(mode="json")
    )
    assert verify_product_reference_signature(gate_reference, public_key)
    assert GateProductReference.model_validate(reference.model_dump(mode="json"))
