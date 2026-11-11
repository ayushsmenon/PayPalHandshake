from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NegotiationStatus(StrEnum):
    VERIFYING = "VERIFYING"


class VerificationState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PRELIMINARY = "PRELIMINARY"


class Product(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    price_cents: int = Field(ge=0)
    illustrative: bool = True
    verified: bool = False
    product_identifier: None = None


class NegotiationEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    participant: str
    amount_cents: int = Field(ge=0)


class VerificationCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    check: str
    state: VerificationState


class DemoNegotiation(BaseModel):
    model_config = ConfigDict(frozen=True)

    is_demo: bool = True
    demo_notice: str = "Illustrative mock data. No real negotiation or payment occurred."
    negotiation_id: str
    shopping_goal: str
    merchant: str
    currency: str
    market_reference_cents: int = Field(ge=0)
    authorized_budget_cents: int = Field(ge=0)
    proposed_total_cents: int = Field(ge=0)
    captured_amount_cents: int = Field(ge=0)
    status: NegotiationStatus
    products: list[Product]
    history: list[NegotiationEvent]
    verification_checks: list[VerificationCheck]


class DealAssemblyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    negotiation_id: str = Field(min_length=1)
    requirements: list[str] = Field(min_length=1)
    budget_cents: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def currency_must_be_uppercase(cls, value: str) -> str:
        if value != value.upper():
            raise ValueError("currency must use uppercase ISO-style letters")
        return value

    @field_validator("requirements")
    @classmethod
    def requirements_must_be_non_empty(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("requirements must be non-empty")
        return cleaned


class DealItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    requirement: str
    product_identifier: str
    name: str
    merchant_domain: str
    offer_url: str
    price_cents: int = Field(ge=0)
    currency: str
    availability: str
    source: str = "channel3"
    illustrative: bool = False


class DealAssemblyResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    is_real: bool
    source: str
    negotiation_id: str
    budget_cents: int
    currency: str
    total_cents: int = Field(ge=0)
    remaining_budget_cents: int = Field(ge=0)
    items: list[DealItem]
    limitations: list[str]
