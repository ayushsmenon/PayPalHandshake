import base64
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from payment_policy.product_reference import (
    ProductReferenceIssuanceError,
    issue_product_reference,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def channel3_response(offers: list[dict[str, object]]) -> Mock:
    response = Mock()
    response.json.return_value = {
        "id": "PpawoVb",
        "title": "MSI PRO MP273U",
        "offers": offers,
    }
    response.raise_for_status.return_value = None
    return response


def offer(
    *,
    domain: str = "example.com",
    price: object = 329,
    url: str = "https://example.com/product-123",
    availability: str = "InStock",
) -> dict[str, object]:
    return {
        "domain": domain,
        "url": url,
        "price": {"price": price, "currency": "USD"},
        "availability": availability,
        "condition": "new",
    }


def private_environment() -> dict[str, str]:
    key = Ed25519PrivateKey.generate()
    return {
        "CHANNEL3_API_KEY": "test-key",
        "MANDATE_SIGNING_PRIVATE_KEY_B64": base64.b64encode(
            key.private_bytes_raw()
        ).decode("ascii"),
    }


def test_issues_unique_exact_available_offer() -> None:
    with patch(
        "payment_policy.product_reference.httpx.get",
        return_value=channel3_response([offer()]),
    ):
        signed = issue_product_reference(
            "PpawoVb",
            "example.com",
            "329.00",
            "USD",
            environment=private_environment(),
            now=NOW,
        )

    assert signed.reference.channel3_product_id == "PpawoVb"
    assert signed.reference.offer_url == "https://example.com/product-123"
    assert signed.reference.price_minor == 32900
    assert signed.reference.availability == "InStock"


@pytest.mark.parametrize(
    "offers",
    [
        [offer(price=328)],
        [offer(domain="other.com")],
        [offer(availability="OutOfStock")],
        [offer(url="")],
        [offer(price="not-a-price")],
    ],
)
def test_mismatch_or_missing_required_offer_data_fails(offers) -> None:
    with patch(
        "payment_policy.product_reference.httpx.get",
        return_value=channel3_response(offers),
    ):
        with pytest.raises(ProductReferenceIssuanceError):
            issue_product_reference(
                "PpawoVb",
                "example.com",
                "329.00",
                "USD",
                environment=private_environment(),
                now=NOW,
            )


def test_ambiguous_offers_fail_closed() -> None:
    with patch(
        "payment_policy.product_reference.httpx.get",
        return_value=channel3_response([offer(), offer(url="https://example.com/other")]),
    ):
        with pytest.raises(ProductReferenceIssuanceError, match="ambiguous"):
            issue_product_reference(
                "PpawoVb",
                "example.com",
                "329.00",
                "USD",
                environment=private_environment(),
                now=NOW,
            )
