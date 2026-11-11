import base64
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from payment_policy.api import create_app
from payment_policy.category_registry import ProductCategoryEntry, ProductCategoryRegistry
from payment_policy.product_reference import (
    ProductReferenceV2,
    SignedProductReferenceV2,
    canonical_product_reference_v2_bytes,
    verify_product_reference_v2_signature,
)


def signing_environment() -> dict[str, str]:
    key = Ed25519PrivateKey.generate()
    return {
        "CHANNEL3_API_KEY": "test-key",
        "MANDATE_SIGNING_PRIVATE_KEY_B64": base64.b64encode(
            key.private_bytes_raw()
        ).decode("ascii"),
        "PUBLIC_KEY_B64": base64.b64encode(
            key.public_key().public_bytes_raw()
        ).decode("ascii"),
    }


def detail(title: str = "Reviewed monitor") -> Mock:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "id": "p-1",
        "title": title,
        "offers": [
            {
                "domain": "merchant.example",
                "url": "https://merchant.example/p-1",
                "price": {"price": "329.00", "currency": "USD"},
                "availability": "InStock",
                "condition": "new",
            }
        ],
    }
    return response


def registry() -> ProductCategoryRegistry:
    return ProductCategoryRegistry(
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


def request_payload(category: str | None = None) -> dict[str, object]:
    product: dict[str, object] = {
        "product_identifier": "p-1",
        "merchant_domain": "merchant.example",
        "price_cents": 32900,
        "currency": "USD",
    }
    if category is not None:
        product["category"] = category
    return {"products": [product]}


def test_v2_endpoint_issues_registry_backed_reference() -> None:
    environment = signing_environment()
    with (
        patch.dict("os.environ", environment, clear=False),
        patch("payment_policy.product_reference.httpx.get", return_value=detail()),
        TestClient(create_app(category_registry=registry())) as client,
    ):
        response = client.post(
            "/internal/v2/product-references/issue",
            json=request_payload(),
        )

    assert response.status_code == 200
    reference = response.json()["references"][0]
    assert reference["reference"]["schema_version"] == "product-reference.v2"
    assert reference["reference"]["canonical_category"] == "electronics"
    assert reference["reference"]["registry_version"] == "test-1"
    assert verify_product_reference_v2_signature(
        SignedProductReferenceV2.model_validate(reference),
        environment["PUBLIC_KEY_B64"],
    )


def test_v2_endpoint_fails_for_unknown_products() -> None:
    environment = signing_environment()
    with (
        patch.dict("os.environ", environment, clear=False),
        patch("payment_policy.product_reference.httpx.get", return_value=detail()),
        TestClient(create_app(category_registry=ProductCategoryRegistry([], version="test-1"))) as client,
    ):
        response = client.post(
            "/internal/v2/product-references/issue",
            json=request_payload(),
        )
    assert response.status_code == 422


def test_v2_endpoint_fails_for_changed_product_identity() -> None:
    environment = signing_environment()
    with (
        patch.dict("os.environ", environment, clear=False),
        patch("payment_policy.product_reference.httpx.get", return_value=detail("Changed monitor")),
        TestClient(create_app(category_registry=registry())) as client,
    ):
        response = client.post(
            "/internal/v2/product-references/issue",
            json=request_payload(),
        )
    assert response.status_code == 422


def test_v2_endpoint_fails_for_wrong_merchant() -> None:
    environment = signing_environment()
    with (
        patch.dict("os.environ", environment, clear=False),
        patch("payment_policy.product_reference.httpx.get", return_value=detail()),
        TestClient(create_app(category_registry=registry())) as client,
    ):
        response = client.post(
            "/internal/v2/product-references/issue",
            json={
                "products": [
                    {
                        "product_identifier": "p-1",
                        "merchant_domain": "wrong.example",
                        "price_cents": 32900,
                        "currency": "USD",
                    }
                ]
            },
        )

    assert response.status_code == 422


def test_v2_endpoint_rejects_caller_category_field() -> None:
    with TestClient(create_app(category_registry=registry())) as client:
        response = client.post(
            "/internal/v2/product-references/issue",
            json=request_payload("toys"),
        )
    assert response.status_code == 422


def test_v2_signature_domain_is_distinct() -> None:
    reference = ProductReferenceV2(
        channel3_product_id="p-1",
        offer_url="https://merchant.example/p-1",
        merchant_domain="merchant.example",
        price_minor=32900,
        currency="USD",
        availability="InStock",
        issued_at="2027-01-01T00:00:00Z",
        expires_at="2027-01-02T00:00:00Z",
        canonical_category="electronics",
        registry_version="test-1",
    )
    assert canonical_product_reference_v2_bytes(reference).startswith(
        b"PAYPAL-HANDSHAKE/PRODUCT-REFERENCE/v2\0"
    )
