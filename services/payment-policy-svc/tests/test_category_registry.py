import pytest

from payment_policy.category_registry import (
    ProductCategoryEntry,
    ProductCategoryRegistry,
    ProductCategoryRegistryError,
)


def entry(**updates: object) -> ProductCategoryEntry:
    values = {
        "channel3_product_id": "p-1",
        "merchant_domain": "Merchant.Example",
        "canonical_category": "electronics",
        "expected_title": "Reviewed monitor",
    }
    values.update(updates)
    return ProductCategoryEntry.model_validate(values)


def test_registry_matches_reviewed_identity() -> None:
    registry = ProductCategoryRegistry([entry()])
    result = registry.match(
        {"id": "p-1", "title": "Reviewed monitor"},
        "merchant.example",
    )
    assert result.canonical_category == "electronics"


def test_registry_rejects_duplicate_product_merchant_mapping() -> None:
    with pytest.raises(ProductCategoryRegistryError, match="Duplicate"):
        ProductCategoryRegistry([entry(), entry()])


def test_registry_rejects_unknown_changed_and_wrong_merchant_products() -> None:
    registry = ProductCategoryRegistry([entry()])
    for product, merchant in (
        ({"id": "unknown", "title": "Reviewed monitor"}, "merchant.example"),
        ({"id": "p-1", "title": "Changed monitor"}, "merchant.example"),
        ({"id": "p-1", "title": "Reviewed monitor"}, "other.example"),
    ):
        with pytest.raises(ProductCategoryRegistryError):
            registry.match(product, merchant)


def test_registry_rejects_noncanonical_categories() -> None:
    with pytest.raises(ValueError):
        entry(canonical_category="Consumer Electronics")
