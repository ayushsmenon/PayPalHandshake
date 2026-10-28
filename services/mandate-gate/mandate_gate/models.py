"""Strict data models used by the deterministic mandate gate."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProductLine(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    product_identifier: str = Field(min_length=1)
    quantity: int = Field(gt=0)
    description: str = Field(min_length=1)


class SignedMandate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: str = Field(min_length=1)
    negotiation_id: str = Field(min_length=1)
    paypal_auth_id: str = Field(min_length=1)
    authorized_cap_cents: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    authorized_merchant_id: str = Field(min_length=1)
    allowed_product_identifiers: list[str] = Field(min_length=1)
    issued_at: datetime
    expires_at: datetime
    mandate_id: str = Field(min_length=1)

    @field_validator("currency")
    @classmethod
    def currency_must_be_uppercase(cls, value: str) -> str:
        if value != value.upper():
            raise ValueError("currency must use uppercase ISO-style letters")
        return value

    @field_validator("allowed_product_identifiers")
    @classmethod
    def identifiers_must_be_non_empty_and_unique(cls, value: list[str]) -> list[str]:
        if any(not identifier for identifier in value):
            raise ValueError("product identifiers must be non-empty")
        if len(set(value)) != len(value):
            raise ValueError("product identifiers must be unique")
        return value

    @model_validator(mode="after")
    def expiry_must_follow_issue(self) -> SignedMandate:
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be later than issued_at")
        return self


class CandidateDeal(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    negotiation_id: str = Field(min_length=1)
    paypal_auth_id: str = Field(min_length=1)
    merchant_id: str = Field(min_length=1)
    total_cents: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    products: list[ProductLine] = Field(min_length=1)

    @field_validator("currency")
    @classmethod
    def currency_must_be_uppercase(cls, value: str) -> str:
        if value != value.upper():
            raise ValueError("currency must use uppercase ISO-style letters")
        return value


class VerificationCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    verdict: Literal["PASS", "REJECT"]
    detail: str


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["PASS", "REJECT"]
    payment_ready: bool
    checks: list[VerificationCheck]
