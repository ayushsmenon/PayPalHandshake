import base64
from unittest.mock import Mock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from payment_policy.api import create_app


def channel3_response(product_id: str, offers: list[dict[str, object]]) -> Mock:
    response = Mock()
    response.json.return_value = {
        "id": product_id,
        "title": f"Product {product_id}",
        "offers": offers,
    }
    response.raise_for_status.return_value = None
    return response


def offer(
    *,
    domain: str = "merchant.example",
    price: object = 329,
    url: str = "https://merchant.example/product",
    availability: str = "InStock",
) -> dict[str, object]:
    return {
        "domain": domain,
        "url": url,
        "price": {"price": price, "currency": "USD"},
        "availability": availability,
        "condition": "new",
    }


def signing_environment() -> dict[str, str]:
    key = Ed25519PrivateKey.generate()
    return {
        "CHANNEL3_API_KEY": "test-key",
        "MANDATE_SIGNING_PRIVATE_KEY_B64": base64.b64encode(
            key.private_bytes_raw()
        ).decode("ascii"),
    }


def request(products: list[dict[str, object]]) -> dict[str, object]:
    return {"products": products}


def candidate(
    product_identifier: str,
    *,
    merchant_domain: str = "merchant.example",
    price_cents: int = 32900,
) -> dict[str, object]:
    return {
        "product_identifier": product_identifier,
        "merchant_domain": merchant_domain,
        "price_cents": price_cents,
        "currency": "USD",
        "offer_url": "https://untrusted.example/not-used",
        "is_real": False,
        "verified": True,
    }


def test_issue_endpoint_independently_issues_both_product_references() -> None:
    application = create_app()
    products = [
        candidate("monitor-1"),
        candidate("keyboard-1", price_cents=8950),
    ]
    responses = [
        channel3_response(
            "monitor-1",
            [offer(price="329.00", url="https://merchant.example/monitor")],
        ),
        channel3_response(
            "keyboard-1",
            [offer(price="89.50", url="https://merchant.example/keyboard")],
        ),
    ]

    with (
        patch.dict("os.environ", signing_environment(), clear=False),
        patch("payment_policy.product_reference.httpx.get", side_effect=responses) as get,
        TestClient(application) as client,
    ):
        response = client.post(
            "/internal/v1/product-references/issue",
            json=request(products),
        )

    assert response.status_code == 200
    references = response.json()["references"]
    assert [item["reference"]["channel3_product_id"] for item in references] == [
        "monitor-1",
        "keyboard-1",
    ]
    assert [item["reference"]["price_minor"] for item in references] == [32900, 8950]
    assert [
        item["reference"]["offer_url"] for item in references
    ] == [
        "https://merchant.example/monitor",
        "https://merchant.example/keyboard",
    ]
    assert get.call_count == 2
    assert get.call_args_list[0].args[0].endswith("/monitor-1")
    assert get.call_args_list[1].args[0].endswith("/keyboard-1")


@pytest.mark.parametrize(
    "channel3_offer",
    [
        offer(price="328.99"),
        offer(domain="other.example"),
        offer(availability="OutOfStock"),
    ],
)
def test_issue_endpoint_rejects_independent_offer_mismatches(
    channel3_offer: dict[str, object],
) -> None:
    application = create_app()
    with (
        patch.dict("os.environ", signing_environment(), clear=False),
        patch(
            "payment_policy.product_reference.httpx.get",
            return_value=channel3_response("monitor-1", [channel3_offer]),
        ),
        TestClient(application) as client,
    ):
        response = client.post(
            "/internal/v1/product-references/issue",
            json=request([candidate("monitor-1")]),
        )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "A product candidate could not be independently verified."
    )


def test_issue_endpoint_rejects_ambiguous_matching_offers() -> None:
    application = create_app()
    offers = [
        offer(url="https://merchant.example/one"),
        offer(url="https://merchant.example/two"),
    ]
    with (
        patch.dict("os.environ", signing_environment(), clear=False),
        patch(
            "payment_policy.product_reference.httpx.get",
            return_value=channel3_response("monitor-1", offers),
        ),
        TestClient(application) as client,
    ):
        response = client.post(
            "/internal/v1/product-references/issue",
            json=request([candidate("monitor-1")]),
        )

    assert response.status_code == 422
