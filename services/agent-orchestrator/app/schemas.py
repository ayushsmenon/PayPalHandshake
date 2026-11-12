from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NegotiationStatus(StrEnum):
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    CAPTURED = "CAPTURED"


class VerificationState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PRELIMINARY = "PRELIMINARY"
    PASSED = "PASSED"
    BLOCKED = "BLOCKED"


class Product(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    price_cents: int = Field(ge=0)
    illustrative: bool = True
    verified: bool = False
    product_identifier: str | None = None
    canonical_category: str | None = None
    merchant_domain: str | None = None
    offer_url: str | None = None


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


class DealVerificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    negotiation_id: str = "HS-2048"


class VerificationCheckDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    verdict: str
    detail: str


class DealEvidenceProduct(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    product_identifier: str
    price_cents: int
    canonical_category: str
    verified: bool
    offer_url: str
    merchant_domain: str


class DealVerificationResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    verdict: str
    payment_ready: bool
    checks: list[VerificationCheckDetail]
    mandate_id: str
    mandate_digest_b64: str
    authorized_cap_cents: int
    paypal_order_id: str
    paypal_auth_id: str
    products: list[DealEvidenceProduct]


class DealApprovalCaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    negotiation_id: str = "HS-2048"
    mandate_id: str
    shopper_id: str = "shopper_avery_singh"
    confirmed_amount_cents: int = 41850
    confirmed_merchant: str = "merchant.example"


class DealApprovalCaptureResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    paypal_order_id: str
    paypal_authorization_id: str
    paypal_capture_id: str
    captured_amount_cents: int
    currency: str
    mandate_id: str
    mandate_digest_b64: str
    timestamp: str


class AuditTrailItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str
    event_type: str
    name_or_gate: str
    decision_or_status: str
    amount_cents: int | None = None
    details: str | None = None
    created_at: str


class AuditTrailResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    negotiation_id: str
    events: list[AuditTrailItem]
