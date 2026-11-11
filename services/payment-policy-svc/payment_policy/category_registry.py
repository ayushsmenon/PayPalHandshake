"""Human-reviewed product category registry for Process C."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


_CATEGORY_PATTERN = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")


class ProductCategoryRegistryError(ValueError):
    """Raised when the reviewed category registry is invalid or cannot match."""


class ProductCategoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    channel3_product_id: str = Field(min_length=1)
    merchant_domain: str = Field(min_length=1)
    canonical_category: str = Field(min_length=1)
    expected_title: str = Field(min_length=1)
    expected_brand: str | None = Field(default=None, min_length=1)
    expected_identifiers: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("merchant_domain")
    @classmethod
    def normalize_domain(cls, value: str) -> str:
        return value.lower()

    @field_validator("canonical_category")
    @classmethod
    def validate_category(cls, value: str) -> str:
        if not _CATEGORY_PATTERN.fullmatch(value):
            raise ValueError("canonical_category must use lowercase identifier syntax")
        return value

    @field_validator("expected_identifiers")
    @classmethod
    def validate_identifiers(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        if any(not key.strip() or not values for key, values in value.items()):
            raise ValueError("expected identifiers must contain non-empty values")
        return value


class ProductCategoryRegistry:
    def __init__(
        self,
        entries: list[ProductCategoryEntry],
        *,
        version: str = "1",
    ) -> None:
        if not version.strip():
            raise ProductCategoryRegistryError("Registry version is required.")
        self.version = version
        self._entries: dict[tuple[str, str], ProductCategoryEntry] = {}
        for entry in entries:
            key = (entry.channel3_product_id, entry.merchant_domain)
            if key in self._entries:
                raise ProductCategoryRegistryError(
                    "Duplicate product and merchant mapping in category registry."
                )
            self._entries[key] = entry

    def match(
        self,
        product: dict[str, Any],
        merchant_domain: str,
    ) -> ProductCategoryEntry:
        product_id = product.get("id")
        title = product.get("title")
        if not isinstance(product_id, str) or not isinstance(title, str):
            raise ProductCategoryRegistryError(
                "Channel3 product identity evidence is missing."
            )
        key = (product_id, merchant_domain.strip().lower())
        entry = self._entries.get(key)
        if entry is None:
            raise ProductCategoryRegistryError(
                "Product is not present in the reviewed category registry."
            )
        if title != entry.expected_title:
            raise ProductCategoryRegistryError(
                "Channel3 product identity does not match the reviewed registry."
            )
        if entry.expected_brand is not None:
            brands = product.get("brands")
            names = {
                brand.get("name")
                for brand in brands
                if isinstance(brand, dict) and isinstance(brand.get("name"), str)
            } if isinstance(brands, list) else set()
            if entry.expected_brand not in names:
                raise ProductCategoryRegistryError(
                    "Channel3 brand does not match the reviewed registry."
                )
        if entry.expected_identifiers:
            attributes = product.get("structured_attributes")
            if not isinstance(attributes, dict):
                raise ProductCategoryRegistryError(
                    "Channel3 product identifiers are missing."
                )
            for name, expected_values in entry.expected_identifiers.items():
                actual = attributes.get(name)
                actual_values = actual if isinstance(actual, list) else [actual]
                if not set(expected_values).issubset(set(actual_values)):
                    raise ProductCategoryRegistryError(
                        "Channel3 product identifiers do not match the reviewed registry."
                    )
        return entry


def load_product_category_registry(
    path: Path | None = None,
) -> ProductCategoryRegistry:
    registry_path = path or Path(__file__).with_name("data") / "product_category_registry.json"
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
        entries = payload["entries"]
        version = payload["version"]
        if not isinstance(entries, list) or not isinstance(version, str):
            raise ValueError
        return ProductCategoryRegistry(
            [ProductCategoryEntry.model_validate(entry) for entry in entries],
            version=version,
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ProductCategoryRegistryError(
            "Product category registry is invalid or unavailable."
        ) from error
