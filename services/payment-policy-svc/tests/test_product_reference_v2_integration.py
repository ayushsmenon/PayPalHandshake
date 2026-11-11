import base64
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mandate-gate"))

from mandate_gate.api import app as mandate_gate_app
from payment_policy.api import create_app
from payment_policy.category_registry import ProductCategoryEntry, ProductCategoryRegistry
from payment_policy.versioned_crypto import (
    canonical_mandate_v2_bytes,
    canonical_post_authorization_binding_v1_bytes,
    mandate_v2_digest_b64,
)


NOW = datetime(2027, 1, 1, tzinfo=timezone.utc)


def channel3_detail() -> Mock:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "id": "p-1",
        "title": "Reviewed monitor",
        "offers": [
            {
                "domain": "merchant.example",
                "url": "https://merchant.example/p-1",
                "price": {"price": "329.00", "currency": "USD"},
                "availability": "InStock",
            }
        ],
    }
    return response


def test_process_c_v2_reference_passes_process_b_http_endpoint(monkeypatch) -> None:
    signing_key = Ed25519PrivateKey.generate()
    public_key = base64.b64encode(signing_key.public_key().public_bytes_raw()).decode()
    monkeypatch.setenv("MANDATE_GATE_PUBLIC_KEY_B64", public_key)
    monkeypatch.setenv("MANDATE_GATE_BINDING_PUBLIC_KEY_B64", public_key)
    environment = {
        "CHANNEL3_API_KEY": "test-key",
        "MANDATE_SIGNING_PRIVATE_KEY_B64": base64.b64encode(
            signing_key.private_bytes_raw()
        ).decode(),
    }
    registry = ProductCategoryRegistry(
        [
            ProductCategoryEntry(
                channel3_product_id="p-1",
                merchant_domain="merchant.example",
                canonical_category="electronics",
                expected_title="Reviewed monitor",
            )
        ],
        version="test-1",
    )
    with (
        patch.dict(os.environ, environment, clear=False),
        patch("payment_policy.product_reference.httpx.get", return_value=channel3_detail()),
    ):
        issued = TestClient(create_app(category_registry=registry)).post(
            "/internal/v2/product-references/issue",
            json={
                "products": [
                    {
                        "product_identifier": "p-1",
                        "merchant_domain": "merchant.example",
                        "price_cents": 32900,
                        "currency": "USD",
                    }
                ]
            },
        )
    assert issued.status_code == 200
    reference = issued.json()["references"][0]
    from payment_policy.versioned_models import (
        CandidateDealV2,
        MandateV2,
        PostAuthorizationBindingV1,
        ProductLineV2,
    )

    mandate = MandateV2(
        schema_version="mandate.v2",
        mandate_id="m-1",
        negotiation_id="n-1",
        paypal_order_id="o-1",
        authorized_cap_cents=40000,
        currency="USD",
        allowed_product_categories=["electronics"],
        merchant_restrictions=["merchant.example"],
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(hours=1),
    )
    binding = PostAuthorizationBindingV1(
        schema_version="post-authorization-binding.v1",
        binding_id="b-1",
        mandate_id="m-1",
        mandate_digest_b64=mandate_v2_digest_b64(mandate),
        paypal_order_id="o-1",
        paypal_auth_id="a-1",
        authorization_status="AUTHORIZED",
        authorized_amount_cents=40000,
        currency="USD",
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(hours=1),
    )
    candidate = CandidateDealV2(
        negotiation_id="n-1",
        paypal_order_id="o-1",
        merchant_id="merchant.example",
        total_cents=32900,
        currency="USD",
        products=[
            ProductLineV2(
                product_identifier="p-1",
                quantity=1,
                description="Reviewed monitor",
                unit_price_cents=32900,
                offer_url=reference["reference"]["offer_url"],
                product_reference=reference,
            )
        ],
    )
    payload = {
        "mandate": mandate.model_dump(mode="json"),
        "mandate_signature": base64.b64encode(
            signing_key.sign(canonical_mandate_v2_bytes(mandate))
        ).decode(),
        "post_authorization_binding": binding.model_dump(mode="json"),
        "binding_signature": base64.b64encode(
            signing_key.sign(canonical_post_authorization_binding_v1_bytes(binding))
        ).decode(),
        "candidate": candidate.model_dump(mode="json"),
    }
    response = TestClient(mandate_gate_app).post(
        "/api/v2/verify-authorization-binding",
        json=payload,
    )
    assert response.status_code == 200
    assert response.json()["verdict"] == "PASS"
