"""Versioned, domain-separated Channel3 product-reference issuance."""

from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


PRODUCT_REFERENCE_DOMAIN = b"PAYPAL-HANDSHAKE/PRODUCT-REFERENCE/v1\0"
CHANNEL3_PRODUCTS_URL = "https://api.trychannel3.com/v1/products"
SUPPORTED_CURRENCY_EXPONENTS = {"USD": 2}


class ProductReference(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: str = Field(min_length=1)
    channel3_product_id: str = Field(min_length=1)
    offer_url: str = Field(min_length=1)
    merchant_domain: str = Field(min_length=1)
    price_minor: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    availability: str = Field(min_length=1)
    condition: str | None = None
    issued_at: datetime
    expires_at: datetime

    @field_validator("currency")
    @classmethod
    def currency_must_be_supported(cls, value: str) -> str:
        if value not in SUPPORTED_CURRENCY_EXPONENTS:
            raise ValueError("unsupported product-reference currency")
        return value

    @field_validator("schema_version")
    @classmethod
    def supported_schema_version(cls, value: str) -> str:
        if value != "product-reference.v1":
            raise ValueError("unsupported product-reference schema version")
        return value

    @field_validator("offer_url")
    @classmethod
    def offer_url_must_be_http(cls, value: str) -> str:
        if not value.startswith(("https://", "http://")):
            raise ValueError("offer_url must be an HTTP(S) URL")
        return value

    @model_validator(mode="after")
    def expiry_must_follow_issue(self) -> ProductReference:
        if self.expires_at <= self.issued_at:
            raise ValueError("product-reference expires_at must be later than issued_at")
        return self


class SignedProductReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference: ProductReference
    signature_b64: str = Field(min_length=1)


class ProductReferenceIssuanceError(ValueError):
    """Raised when Channel3 cannot support a safe exact reference."""


def canonical_product_reference_bytes(reference: ProductReference) -> bytes:
    import json

    return PRODUCT_REFERENCE_DOMAIN + json.dumps(
        reference.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sign_product_reference(
    reference: ProductReference,
    private_key: Ed25519PrivateKey,
) -> SignedProductReference:
    signature = private_key.sign(canonical_product_reference_bytes(reference))
    return SignedProductReference(
        reference=reference,
        signature_b64=base64.b64encode(signature).decode("ascii"),
    )


def verify_product_reference_signature(
    signed_reference: SignedProductReference,
    public_key_b64: str,
) -> bool:
    try:
        public_key = base64.b64decode(public_key_b64, validate=True)
        signature = base64.b64decode(signed_reference.signature_b64, validate=True)
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature,
            canonical_product_reference_bytes(signed_reference.reference),
        )
    except (ValueError, TypeError, binascii.Error):
        return False
    return True


def _price_minor(value: object, currency: str) -> int:
    if currency not in SUPPORTED_CURRENCY_EXPONENTS or not isinstance(value, (int, float, str)):
        raise ProductReferenceIssuanceError("Channel3 returned an unsupported or invalid price.")
    try:
        decimal_value = Decimal(str(value))
        minor = decimal_value * (10 ** SUPPORTED_CURRENCY_EXPONENTS[currency])
    except InvalidOperation as error:
        raise ProductReferenceIssuanceError("Channel3 returned an invalid price.") from error
    if minor < 0 or minor != minor.to_integral_value():
        raise ProductReferenceIssuanceError("Channel3 price is not a valid minor-unit amount.")
    return int(minor)


def issue_product_reference(
    product_id: str,
    expected_merchant: str,
    expected_price: str,
    expected_currency: str,
    *,
    environment: dict[str, str] | None = None,
    now: datetime | None = None,
    ttl: timedelta = timedelta(minutes=15),
) -> SignedProductReference:
    from .signing import load_signing_private_key

    values = os.environ if environment is None else environment
    api_key = values.get("CHANNEL3_API_KEY", "").strip()
    if not api_key:
        raise ProductReferenceIssuanceError("CHANNEL3_API_KEY is missing.")
    if expected_currency not in SUPPORTED_CURRENCY_EXPONENTS:
        raise ProductReferenceIssuanceError("Unsupported expected currency.")
    if not expected_merchant.strip():
        raise ProductReferenceIssuanceError("Expected merchant is required.")
    expected_minor = _price_minor(expected_price, expected_currency)

    try:
        response = httpx.get(
            f"{CHANNEL3_PRODUCTS_URL}/{quote(product_id, safe='')}",
            headers={"x-api-key": api_key},
            timeout=httpx.Timeout(15.0, connect=5.0),
        )
        response.raise_for_status()
        product = response.json()
    except httpx.HTTPStatusError as error:
        raise ProductReferenceIssuanceError(
            f"Channel3 returned HTTP {error.response.status_code}."
        ) from error
    except httpx.TimeoutException as error:
        raise ProductReferenceIssuanceError("Channel3 request timed out.") from error
    except httpx.RequestError as error:
        raise ProductReferenceIssuanceError("Channel3 request failed.") from error
    except ValueError as error:
        raise ProductReferenceIssuanceError("Channel3 returned invalid JSON.") from error

    if not isinstance(product, dict) or product.get("id") != product_id:
        raise ProductReferenceIssuanceError("Channel3 response did not match the requested product.")
    offers = product.get("offers")
    if not isinstance(offers, list):
        raise ProductReferenceIssuanceError("Channel3 response did not include offers.")

    matches: list[dict[str, Any]] = []
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        price = offer.get("price")
        if not isinstance(price, dict):
            continue
        if (
            offer.get("domain") == expected_merchant
            and price.get("currency") == expected_currency
            and offer.get("availability") == "InStock"
            and isinstance(offer.get("url"), str)
            and offer["url"].startswith(("https://", "http://"))
            and _price_minor(price.get("price"), expected_currency) == expected_minor
        ):
            matches.append(offer)

    if len(matches) == 0:
        raise ProductReferenceIssuanceError("No unique available offer matched merchant, currency, and expected price.")
    if len(matches) > 1:
        raise ProductReferenceIssuanceError("Multiple offers matched; refusing an ambiguous reference.")

    offer = matches[0]
    issued_at = now or datetime.now(timezone.utc)
    try:
        reference = ProductReference(
            schema_version="product-reference.v1",
            channel3_product_id=product_id,
            offer_url=offer["url"],
            merchant_domain=expected_merchant,
            price_minor=expected_minor,
            currency=expected_currency,
            availability=offer["availability"],
            condition=offer.get("condition"),
            issued_at=issued_at,
            expires_at=issued_at + ttl,
        )
    except ValidationError as error:
        raise ProductReferenceIssuanceError(
            "Channel3 offer did not contain valid reference fields."
        ) from error
    return sign_product_reference(reference, load_signing_private_key(values))
