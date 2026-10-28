"""Run one safe, read-only Channel3 product-search smoke test."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

SERVICE_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = SERVICE_DIR / ".env"
SEARCH_URL = "https://api.trychannel3.com/v1/search"
SEARCH_QUERY = "27-inch 4K monitor"
RESULT_LIMIT = 5


def _format_price(product: dict[str, Any]) -> str:
    offers = product.get("offers")
    if not isinstance(offers, list) or not offers:
        return "unavailable"
    first_offer = offers[0]
    if not isinstance(first_offer, dict):
        return "unavailable"
    price = first_offer.get("price")
    if not isinstance(price, dict):
        return "unavailable"
    amount = price.get("price")
    currency = price.get("currency")
    if isinstance(amount, (int, float)) and isinstance(currency, str):
        return f"{amount} {currency}"
    return "unavailable"


def _format_brand(product: dict[str, Any]) -> str:
    brands = product.get("brands")
    if not isinstance(brands, list):
        return "unavailable"
    names = [
        brand.get("name")
        for brand in brands
        if isinstance(brand, dict) and isinstance(brand.get("name"), str)
    ]
    return ", ".join(names) if names else "unavailable"


def _format_identifier(product: dict[str, Any]) -> str:
    for field in ("gtin", "upc", "ean", "product_identifier"):
        value = product.get(field)
        if isinstance(value, str) and value.strip():
            return value
    return "unavailable"


def _format_offer_count(product: dict[str, Any]) -> str:
    offers = product.get("offers")
    return str(len(offers)) if isinstance(offers, list) else "unavailable"


def _print_results(payload: object, query: str) -> bool:
    if not isinstance(payload, dict) or not isinstance(payload.get("products"), list):
        print("Channel3 search failed: response did not contain a products list.")
        return False

    products = payload["products"]
    print(f"Channel3 search results for: {query}")
    print("These are catalogue search results, not shopper-authorized products or trusted mandate references.")
    if not products:
        print("No products returned.")
        return True

    for index, product in enumerate(products[:RESULT_LIMIT], start=1):
        if not isinstance(product, dict):
            print(f"{index}. Invalid product entry; details unavailable.")
            continue
        title = product.get("title")
        product_id = product.get("id")
        print(f"{index}. Product: {title if isinstance(title, str) else 'unavailable'}")
        print(f"   Brand: {_format_brand(product)}")
        print(
            "   Channel3 product ID: "
            f"{product_id if isinstance(product_id, str) else 'unavailable'}"
        )
        print(f"   Price: {_format_price(product)}")
        print(f"   Merchant/offer count: {_format_offer_count(product)}")
        print(f"   GTIN/UPC/product identifier: {_format_identifier(product)}")
    return True


def _parse_args(arguments: list[str] | None = None) -> str:
    parser = argparse.ArgumentParser(
        description="Run one safe, read-only Channel3 product search."
    )
    parser.add_argument(
        "query",
        nargs="?",
        default=SEARCH_QUERY,
        help=f"Search query (default: {SEARCH_QUERY!r})",
    )
    args = parser.parse_args(arguments)
    query = args.query.strip()
    if not query:
        print(
            "Channel3 search stopped: query must not be empty or whitespace-only.",
            file=sys.stderr,
        )
        raise ValueError("empty query")
    return query


def main(arguments: list[str] | None = None) -> int:
    try:
        query = _parse_args(arguments)
    except ValueError:
        return 2

    load_dotenv(ENV_PATH, override=False)
    api_key = os.getenv("CHANNEL3_API_KEY", "").strip()
    if not api_key:
        print(
            "Channel3 search stopped: CHANNEL3_API_KEY is missing or empty in "
            "services/agent-orchestrator/.env."
        )
        return 2

    try:
        response = httpx.post(
            SEARCH_URL,
            headers={
                "x-api-key": api_key,
                "Content-Type": "application/json",
            },
            json={"query": query, "limit": RESULT_LIMIT},
            timeout=httpx.Timeout(15.0, connect=5.0),
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        if status == 401:
            detail = "authentication failed; check the local API key."
        elif status == 402:
            detail = "Channel3 credits are unavailable for this account."
        elif status == 429:
            detail = "rate limit reached; try again later."
        else:
            detail = f"Channel3 returned HTTP {status}."
        print(f"Channel3 search failed: {detail}")
        return 1
    except httpx.TimeoutException:
        print("Channel3 search failed: request timed out.")
        return 1
    except httpx.RequestError as error:
        print(f"Channel3 search failed: network error ({error.__class__.__name__}).")
        return 1

    try:
        payload = response.json()
    except ValueError:
        print("Channel3 search failed: response was not valid JSON.")
        return 1

    return 0 if _print_results(payload, query) else 1


if __name__ == "__main__":
    sys.exit(main())
