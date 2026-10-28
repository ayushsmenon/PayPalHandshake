from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


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
