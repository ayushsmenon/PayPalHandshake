"""Inspect one Channel3 product record without modifying catalogue data."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

SERVICE_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = SERVICE_DIR / ".env"
PRODUCTS_URL = "https://api.trychannel3.com/v1/products"


def _identifier_summary(product: dict[str, Any]) -> str:
    attributes = product.get("structured_attributes")
    if not isinstance(attributes, dict):
        return "unavailable"

    identifier_names = {"gtin", "upc", "ean", "sku", "mpn"}
    found: list[str] = []
    for key, value in attributes.items():
        normalized_key = str(key).strip().lower().replace("-", "_")
        if normalized_key not in identifier_names:
            continue
        if isinstance(value, list):
            values = [str(item) for item in value if str(item).strip()]
            if values:
                found.append(f"{key}={', '.join(values)}")
        elif isinstance(value, str) and value.strip():
            found.append(f"{key}={value}")
    return ", ".join(found) if found else "unavailable"


def _selected_variants(product: dict[str, Any]) -> str:
    variants = product.get("variants")
    if not isinstance(variants, dict):
        return "unavailable"
    selected = variants.get("selected")
    if not isinstance(selected, list) or not selected:
        return "unavailable"

    values: list[str] = []
    for option in selected:
        if not isinstance(option, dict):
            continue
        name = option.get("name")
        label = option.get("label")
        if isinstance(name, str) and isinstance(label, str):
            values.append(f"{name}={label}")
        elif isinstance(label, str):
            values.append(label)
    return ", ".join(values) if values else "unavailable"


def _structured_attributes(product: dict[str, Any]) -> str:
    attributes = product.get("structured_attributes")
    if not isinstance(attributes, dict) or not attributes:
        return "unavailable"
    parts: list[str] = []
    for key, value in attributes.items():
        if isinstance(value, list):
            rendered = ", ".join(str(item) for item in value)
        else:
            rendered = str(value)
        parts.append(f"{key}={rendered}")
    return "; ".join(parts)


def _print_product(payload: object, requested_id: str) -> bool:
    if not isinstance(payload, dict):
        print("Channel3 product inspection failed: response was not a product object.")
        return False

    product_id = payload.get("id")
    title = payload.get("title")
    if not isinstance(product_id, str) or not isinstance(title, str):
        print(
            "Channel3 product inspection failed: response lacked documented "
            "product id and title fields."
        )
        return False

    brands = payload.get("brands")
    brand_names = (
        ", ".join(
            brand["name"]
            for brand in brands
            if isinstance(brand, dict) and isinstance(brand.get("name"), str)
        )
        if isinstance(brands, list)
        else ""
    ) or "unavailable"
    description = payload.get("description")
    images = payload.get("images")
    offers = payload.get("offers")

    print("Channel3 product-detail result")
    print(f"Requested product ID: {requested_id}")
    print(f"Returned Channel3 product ID: {product_id}")
    print(f"Product title: {title}")
    print(f"Brand names: {brand_names}")
    print(
        "Short description: "
        f"{description if isinstance(description, str) and description else 'unavailable'}"
    )
    print(f"Product image count: {len(images) if isinstance(images, list) else 'unavailable'}")
    print(f"Structured attributes: {_structured_attributes(payload)}")
    print(f"Selected variant options: {_selected_variants(payload)}")
    print(f"Explicit product identifiers: {_identifier_summary(payload)}")
    print(f"Merchant offer count: {len(offers) if isinstance(offers, list) else 'unavailable'}")

    if isinstance(offers, list) and offers:
        for index, offer in enumerate(offers, start=1):
            if not isinstance(offer, dict):
                print(f"Offer {index}: unavailable")
                continue
            price = offer.get("price")
            price_text = "unavailable"
            if isinstance(price, dict):
                amount = price.get("price")
                currency = price.get("currency")
                if amount is not None and isinstance(currency, str):
                    price_text = f"{amount} {currency}"
            print(
                f"Offer {index}: domain={offer.get('domain', 'unavailable')}, "
                f"price={price_text}, "
                f"availability={offer.get('availability', 'unavailable')}, "
                f"condition={offer.get('condition', 'unavailable')}"
            )
    else:
        print("Offers: unavailable")
    return True


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print("Usage: python scripts\\inspect_channel3_product.py <product_id>")
        return 2

    product_id = sys.argv[1].strip()
    load_dotenv(ENV_PATH, override=False)
    api_key = os.getenv("CHANNEL3_API_KEY", "").strip()
    if not api_key:
        print(
            "Channel3 product inspection stopped: CHANNEL3_API_KEY is missing "
            "or empty in services/agent-orchestrator/.env."
        )
        return 2

    try:
        response = httpx.get(
            f"{PRODUCTS_URL}/{quote(product_id, safe='')}",
            headers={"x-api-key": api_key},
            timeout=httpx.Timeout(15.0, connect=5.0),
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        if status == 401:
            detail = "authentication failed; check the local API key."
        elif status == 402:
            detail = "Channel3 credits are unavailable for this account."
        elif status == 404:
            detail = "the product was not found."
        elif status == 429:
            detail = "the product-detail rate limit was reached."
        else:
            detail = f"Channel3 returned HTTP {status}."
        print(f"Channel3 product inspection failed: {detail}")
        return 1
    except httpx.TimeoutException:
        print("Channel3 product inspection failed: request timed out.")
        return 1
    except httpx.RequestError as error:
        print(
            "Channel3 product inspection failed: network error "
            f"({error.__class__.__name__})."
        )
        return 1

    try:
        payload = response.json()
    except ValueError:
        print("Channel3 product inspection failed: response was not valid JSON.")
        return 1

    return 0 if _print_product(payload, product_id) else 1


if __name__ == "__main__":
    sys.exit(main())
