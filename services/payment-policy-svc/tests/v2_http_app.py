from unittest.mock import Mock

from payment_policy.api import create_app
from payment_policy.category_registry import (
    ProductCategoryEntry,
    ProductCategoryRegistry,
)


def _channel3_response() -> Mock:
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
                "condition": "new",
            }
        ],
    }
    return response


def _channel3_get(url: str, **kwargs: object) -> Mock:
    del kwargs
    if not url.endswith("/p-1"):
        raise AssertionError(f"Unexpected test Channel3 URL: {url}")
    return _channel3_response()


import payment_policy.product_reference as product_reference

product_reference.httpx.get = _channel3_get

app = create_app(
    category_registry=ProductCategoryRegistry(
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
)
