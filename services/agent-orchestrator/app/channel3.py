"""Read-only Channel3 discovery and deal assembly."""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from itertools import product
from typing import Any, Protocol
from urllib.parse import quote, urlparse

import httpx


SEARCH_URL = "https://api.trychannel3.com/v1/search"
PRODUCTS_URL = "https://api.trychannel3.com/v1/products"
SUPPORTED_CURRENCY_EXPONENTS = {"USD": 2}


class Channel3Transport(Protocol):
    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        ...

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        ...


class Channel3UnavailableError(RuntimeError):
    """Raised when Channel3 cannot provide a usable discovery response."""


@dataclass(frozen=True)
class DiscoveredOffer:
    product_id: str
    name: str
    merchant_domain: str
    offer_url: str
    price_cents: int
    currency: str
    availability: str


def _minor_units(value: object, currency: str) -> int | None:
    if currency not in SUPPORTED_CURRENCY_EXPONENTS:
        return None
    if not isinstance(value, (int, float, str)) or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value)) * (10 ** SUPPORTED_CURRENCY_EXPONENTS[currency])
    except InvalidOperation:
        return None
    if amount < 0 or amount != amount.to_integral_value():
        return None
    return int(amount)


def _offer_from_detail(product: dict[str, Any], currency: str) -> list[DiscoveredOffer]:
    product_id = product.get("id")
    title = product.get("title")
    offers = product.get("offers")
    if not isinstance(product_id, str) or not product_id.strip():
        return []
    if not isinstance(title, str) or not title.strip() or not isinstance(offers, list):
        return []

    discovered: list[DiscoveredOffer] = []
    for offer in offers:
        if not isinstance(offer, dict) or offer.get("availability") != "InStock":
            continue
        domain = offer.get("domain")
        price = offer.get("price")
        if not isinstance(domain, str) or not domain.strip() or not isinstance(price, dict):
            continue
        offer_currency = price.get("currency")
        offer_url = offer.get("url") or offer.get("offer_url")
        if offer_currency != currency or not isinstance(offer_url, str):
            continue
        parsed_url = urlparse(offer_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            continue
        price_cents = _minor_units(price.get("price"), currency)
        if price_cents is None:
            continue
        discovered.append(
            DiscoveredOffer(
                product_id=product_id,
                name=title.strip(),
                merchant_domain=domain.strip().lower(),
                offer_url=offer_url,
                price_cents=price_cents,
                currency=currency,
                availability="InStock",
            )
        )
    return discovered


class Channel3Client:
    def __init__(
        self,
        *,
        api_key: str,
        transport: Channel3Transport | None = None,
        timeout: float = 15.0,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Channel3 API key is required.")
        self.api_key = api_key
        self.transport = transport or httpx.Client(timeout=timeout)
        self.timeout = httpx.Timeout(timeout, connect=5.0)

    def _request_kwargs(self) -> dict[str, Any]:
        return {
            "headers": {
                "x-api-key": self.api_key,
                "Content-Type": "application/json",
            },
            "timeout": self.timeout,
        }

    def discover(self, query: str, currency: str, *, limit: int = 5) -> list[DiscoveredOffer]:
        try:
            search_response = self.transport.post(
                SEARCH_URL,
                json={"query": query, "limit": limit},
                **self._request_kwargs(),
            )
            search_response.raise_for_status()
            search_payload = search_response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise Channel3UnavailableError("Channel3 search was unavailable.") from error

        products = search_payload.get("products") if isinstance(search_payload, dict) else None
        if not isinstance(products, list):
            raise Channel3UnavailableError("Channel3 search returned an invalid response.")

        offers: list[DiscoveredOffer] = []
        seen_ids: set[str] = set()
        for result in products[:limit]:
            if not isinstance(result, dict):
                continue
            product_id = result.get("id")
            if not isinstance(product_id, str) or not product_id.strip() or product_id in seen_ids:
                continue
            seen_ids.add(product_id)
            try:
                detail_response = self.transport.get(
                    f"{PRODUCTS_URL}/{quote(product_id, safe='')}",
                    headers={"x-api-key": self.api_key},
                    timeout=self.timeout,
                )
                detail_response.raise_for_status()
                detail_payload = detail_response.json()
            except (httpx.HTTPError, ValueError):
                continue
            if not isinstance(detail_payload, dict) or detail_payload.get("id") != product_id:
                continue
            offers.extend(_offer_from_detail(detail_payload, currency))
        return offers


def assemble_deal(
    client: Channel3Client,
    requirements: list[str],
    budget_cents: int,
    currency: str,
) -> tuple[list[DiscoveredOffer], list[str]]:
    discovered: list[list[DiscoveredOffer]] = []
    limitations: list[str] = []
    for requirement in requirements:
        offers = client.discover(requirement, currency)
        if not offers:
            limitations.append(
                f"No usable in-stock Channel3 offer was returned for {requirement!r}."
            )
        discovered.append(offers)

    if any(not offers for offers in discovered):
        return [], limitations

    combinations = (
        combination
        for combination in product(*discovered)
        if len({offer.product_id for offer in combination}) == len(combination)
    )
    eligible = [
        combination
        for combination in combinations
        if sum(offer.price_cents for offer in combination) <= budget_cents
    ]
    if not eligible:
        limitations.append("No combination of returned Channel3 offers fits the budget.")
        return [], limitations
    selected = min(eligible, key=lambda combination: sum(item.price_cents for item in combination))
    return list(selected), limitations


def channel3_client_from_environment() -> Channel3Client:
    return Channel3Client(api_key=os.getenv("CHANNEL3_API_KEY", ""))
