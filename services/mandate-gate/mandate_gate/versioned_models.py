"""Versioned pre-authorization and post-authorization protocol models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import ProductLineV2


class MandateV2(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["mandate.v2"]
    mandate_id: str = Field(min_length=1)
    negotiation_id: str = Field(min_length=1)
    paypal_order_id: str = Field(min_length=1)
    authorized_cap_cents: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    allowed_product_categories: list[str] = Field(min_length=1)
    merchant_restrictions: list[str] = Field(min_length=1)
    expected_paypal_payee_id: str | None = Field(default=None, min_length=1)
    issued_at: datetime
    expires_at: datetime

    @field_validator("currency")
    @classmethod
    def currency_must_be_uppercase(cls, value: str) -> str:
        if value != value.upper():
            raise ValueError("currency must use uppercase ISO-style letters")
        return value

    @field_validator("allowed_product_categories", "merchant_restrictions")
    @classmethod
    def values_must_be_unique(cls, value: list[str]) -> list[str]:
        if any(not item for item in value) or len(set(value)) != len(value):
            raise ValueError("constraint values must be non-empty and unique")
        return value

    @model_validator(mode="after")
    def expiry_must_follow_issue(self) -> MandateV2:
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be later than issued_at")
        return self


class PostAuthorizationBindingV1(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["post-authorization-binding.v1"]
    binding_id: str = Field(min_length=1)
    mandate_id: str = Field(min_length=1)
    mandate_digest_b64: str = Field(min_length=1)
    paypal_order_id: str = Field(min_length=1)
    paypal_auth_id: str = Field(min_length=1)
    authorization_status: Literal["AUTHORIZED"]
    authorized_amount_cents: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    issued_at: datetime
    expires_at: datetime

    @field_validator("currency")
    @classmethod
    def currency_must_be_uppercase(cls, value: str) -> str:
        if value != value.upper():
            raise ValueError("currency must use uppercase ISO-style letters")
        return value

    @model_validator(mode="after")
    def expiry_must_follow_issue(self) -> PostAuthorizationBindingV1:
        if self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be later than issued_at")
        return self


class CandidateDealV2(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    negotiation_id: str = Field(min_length=1)
    paypal_order_id: str = Field(min_length=1)
    merchant_id: str = Field(min_length=1)
    total_cents: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    products: list[ProductLineV2] = Field(min_length=1)

    @field_validator("currency")
    @classmethod
    def currency_must_be_uppercase(cls, value: str) -> str:
        if value != value.upper():
            raise ValueError("currency must use uppercase ISO-style letters")
        return value
