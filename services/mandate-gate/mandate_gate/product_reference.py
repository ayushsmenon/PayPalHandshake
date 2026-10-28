"""Verification model and protocol for trusted Channel3 product references."""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PRODUCT_REFERENCE_DOMAIN = b"PAYPAL-HANDSHAKE/PRODUCT-REFERENCE/v1\0"
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
    def supported_currency(cls, value: str) -> str:
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
    def valid_expiry(self) -> ProductReference:
        if self.expires_at <= self.issued_at:
            raise ValueError("product-reference expires_at must be later than issued_at")
        return self


class SignedProductReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference: ProductReference
    signature_b64: str = Field(min_length=1)


def canonical_product_reference_bytes(reference: ProductReference) -> bytes:
    return PRODUCT_REFERENCE_DOMAIN + json.dumps(
        reference.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def verify_product_reference_signature(
    signed_reference: SignedProductReference,
    public_key_b64: str,
) -> bool:
    try:
        public_key = base64.b64decode(public_key_b64, validate=True)
        signature = base64.b64decode(signed_reference.signature_b64, validate=True)
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature,
            canonical_product_reference_bytes(signed_reference.reference),
        )
    except (ValueError, TypeError, binascii.Error, InvalidSignature):
        return False
    return True
