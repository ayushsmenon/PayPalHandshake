from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


@compiles(PG_UUID, "sqlite")
def _compile_pg_uuid_sqlite(type_, compiler, **kw):
    return "VARCHAR(36)"


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"


class Base(DeclarativeBase):
    pass


class Negotiation(Base):
    __tablename__ = "negotiations"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    shopper_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    request_prompt: Mapped[str] = mapped_column(
        Text, nullable=False
    )
    budget_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    budget_amount_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    budget_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )
    currency_code: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD"
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="OPEN", index=True
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    products: Mapped[list["Product"]] = relationship(
        back_populates="negotiation", cascade="all, delete-orphan", lazy="select"
    )
    offer_snapshots: Mapped[list["OfferSnapshot"]] = relationship(
        back_populates="negotiation", cascade="all, delete-orphan", lazy="select"
    )
    mandates: Mapped[list["Mandate"]] = relationship(
        back_populates="negotiation", cascade="all, delete-orphan", lazy="select"
    )
    consent_evidences: Mapped[list["ConsentEvidence"]] = relationship(
        back_populates="negotiation", cascade="all, delete-orphan", lazy="select"
    )
    verification_events: Mapped[list["VerificationEvent"]] = relationship(
        back_populates="negotiation", cascade="all, delete-orphan", lazy="select"
    )
    payment_events: Mapped[list["PaymentEvent"]] = relationship(
        back_populates="negotiation", cascade="all, delete-orphan", lazy="select"
    )
    product_references: Mapped[list["ProductReference"]] = relationship(
        back_populates="negotiation", cascade="all, delete-orphan", lazy="select"
    )

    def __init__(self, **kw: Any) -> None:
        if "budget_cents" in kw and "budget_amount" not in kw:
            kw["budget_amount"] = Decimal(kw["budget_cents"]) / Decimal(100)
            kw["budget_amount_cents"] = kw["budget_cents"]
        elif "budget_amount" in kw and "budget_cents" not in kw:
            kw["budget_cents"] = int(kw["budget_amount"] * 100)
            kw["budget_amount_cents"] = kw["budget_cents"]
        if "currency" in kw and "currency_code" not in kw:
            kw["currency_code"] = kw["currency"]
        elif "currency_code" in kw and "currency" not in kw:
            kw["currency"] = kw["currency_code"]
        super().__init__(**kw)

    __table_args__ = (
        CheckConstraint("budget_cents >= 0", name="chk_negotiations_budget_cents_positive"),
        CheckConstraint("length(currency_code) = 3", name="chk_negotiations_currency_len"),
    )


class Product(Base):
    __tablename__ = "products"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    negotiation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("negotiations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_product_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(
        Text, nullable=False
    )
    description: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    catalog_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    negotiation: Mapped["Negotiation"] = relationship(
        back_populates="products", lazy="select"
    )
    offer_snapshots: Mapped[list["OfferSnapshot"]] = relationship(
        back_populates="product", lazy="select"
    )
    product_references: Mapped[list["ProductReference"]] = relationship(
        back_populates="product", lazy="select"
    )


class OfferSnapshot(Base):
    __tablename__ = "offer_snapshots"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    negotiation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("negotiations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    merchant_id: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    merchant_name: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    price_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    offered_price_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    offered_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )
    currency_code: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD"
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD"
    )
    terms_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    offer_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    negotiation: Mapped["Negotiation"] = relationship(
        back_populates="offer_snapshots", lazy="select"
    )
    product: Mapped["Product | None"] = relationship(
        back_populates="offer_snapshots", lazy="select"
    )

    __table_args__ = (
        CheckConstraint("price_cents >= 0", name="chk_offer_snapshots_price_cents_positive"),
        CheckConstraint("length(currency_code) = 3", name="chk_offer_snapshots_currency_len"),
    )


class Mandate(Base):
    __tablename__ = "mandates"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    negotiation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("negotiations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mandate_id: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False, index=True
    )
    schema_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="mandate.v2"
    )
    paypal_order_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    authorized_cap_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    currency_code: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD"
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD"
    )
    allowed_product_categories: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    merchant_restrictions: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    expected_paypal_payee_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    public_key: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    signature: Mapped[str] = mapped_column(
        Text, nullable=False
    )
    mandate_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    nonce: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    mandate_digest: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True, index=True
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ACTIVE", index=True
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    consumption_tx_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    negotiation: Mapped["Negotiation"] = relationship(
        back_populates="mandates", lazy="select"
    )
    consumptions: Mapped[list["MandateConsumption"]] = relationship(
        back_populates="mandate", cascade="all, delete-orphan", lazy="select"
    )
    consent_evidence: Mapped["ConsentEvidence | None"] = relationship(
        back_populates="mandate", uselist=False, cascade="all, delete-orphan", lazy="select"
    )

    __table_args__ = (
        CheckConstraint("expires_at > issued_at", name="chk_mandates_expiry_valid"),
        CheckConstraint("authorized_cap_cents >= 0", name="chk_mandates_cap_cents_positive"),
        CheckConstraint("length(currency_code) = 3", name="chk_mandates_currency_len"),
    )


class ConsentEvidence(Base):
    __tablename__ = "consent_evidence"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    evidence_id: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False, index=True
    )
    mandate_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("mandates.mandate_id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    mandate_digest: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    negotiation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("negotiations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    shopper_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    approval_action: Mapped[str] = mapped_column(
        String(64), nullable=False, default="AUTHORIZE_AND_MANDATE"
    )
    evidence_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    mandate: Mapped["Mandate"] = relationship(
        back_populates="consent_evidence", lazy="select"
    )
    negotiation: Mapped["Negotiation"] = relationship(
        back_populates="consent_evidences", lazy="select"
    )

    __table_args__ = (
        Index("idx_consent_evidence_mandate_lookup", "mandate_id", "mandate_digest"),
    )


class ProductReference(Base):
    __tablename__ = "product_references"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    negotiation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("negotiations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    protocol_version: Mapped[str] = mapped_column(
        String(16), nullable=False, default="v1"
    )
    schema_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="product-reference.v1"
    )
    channel3_product_id: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    offer_url: Mapped[str] = mapped_column(
        Text, nullable=False
    )
    merchant_domain: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    canonical_category: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    registry_version: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    canonical_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    price_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    price_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    currency_code: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD"
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD"
    )
    availability: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    condition: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    digest: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    canonical_digest: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    signature: Mapped[str] = mapped_column(
        Text, nullable=False
    )
    signature_b64: Mapped[str] = mapped_column(
        Text, nullable=False
    )
    public_key: Mapped[str] = mapped_column(
        String(128), nullable=False
    )
    public_key_b64: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    negotiation: Mapped["Negotiation"] = relationship(
        back_populates="product_references", lazy="select"
    )
    product: Mapped["Product | None"] = relationship(
        back_populates="product_references", lazy="select"
    )

    def __init__(self, **kw: Any) -> None:
        if "canonical_digest" in kw and "digest" not in kw:
            kw["digest"] = kw["canonical_digest"]
        elif "digest" in kw and "canonical_digest" not in kw:
            kw["canonical_digest"] = kw["digest"]
        if "signature_b64" in kw and "signature" not in kw:
            kw["signature"] = kw["signature_b64"]
        elif "signature" in kw and "signature_b64" not in kw:
            kw["signature_b64"] = kw["signature"]
        if "public_key_b64" in kw and "public_key" not in kw:
            kw["public_key"] = kw["public_key_b64"] or ""
        elif "public_key" in kw and "public_key_b64" not in kw:
            kw["public_key_b64"] = kw["public_key"]
        if "price_minor" in kw and "price_cents" not in kw:
            kw["price_cents"] = kw["price_minor"]
        elif "price_cents" in kw and "price_minor" not in kw:
            kw["price_minor"] = kw["price_cents"]
        if "currency" in kw and "currency_code" not in kw:
            kw["currency_code"] = kw["currency"]
        elif "currency_code" in kw and "currency" not in kw:
            kw["currency"] = kw["currency_code"]
        super().__init__(**kw)

    __table_args__ = (
        CheckConstraint("expires_at > issued_at", name="chk_product_references_expiry"),
        CheckConstraint("price_cents >= 0", name="chk_product_references_price_positive"),
        CheckConstraint("length(currency_code) = 3", name="chk_product_references_currency"),
        CheckConstraint("protocol_version != 'v2' OR canonical_category IS NOT NULL", name="chk_product_references_v2_category"),
    )


class MandateConsumption(Base):
    __tablename__ = "mandate_consumptions"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    mandate_id: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("mandates.mandate_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mandate_digest: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    consumed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False, index=True
    )
    transaction_id: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    paypal_order_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    payment_event_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("payment_events.id", ondelete="SET NULL"),
        nullable=True,
    )
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    mandate: Mapped["Mandate"] = relationship(
        back_populates="consumptions", lazy="select"
    )


class VerificationEvent(Base):
    __tablename__ = "verification_events"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    negotiation_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("negotiations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    gate_name: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    decision: Mapped[str] = mapped_column(
        String(16), nullable=False, index=True
    )
    passed_checks: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    blocked_checks: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    reason_details: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    manifest_digest: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    negotiation: Mapped["Negotiation | None"] = relationship(
        back_populates="verification_events", lazy="select"
    )

    __table_args__ = (
        CheckConstraint("decision IN ('PASSED', 'BLOCKED')", name="chk_verification_events_decision"),
    )


class PaymentEvent(Base):
    __tablename__ = "payment_events"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    negotiation_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("negotiations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False, index=True
    )
    paypal_order_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    paypal_authorization_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    paypal_capture_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    paypal_payout_batch_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    event_type: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    amount_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )
    currency_code: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD"
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="USD"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="CREATED", index=True
    )
    raw_response_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    negotiation: Mapped["Negotiation | None"] = relationship(
        back_populates="payment_events", lazy="select"
    )

    __table_args__ = (
        CheckConstraint("amount_cents >= 0", name="chk_payment_events_amount_cents_positive"),
        CheckConstraint("length(currency_code) = 3", name="chk_payment_events_currency_len"),
    )


class NegotiationSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    shopper_id: str = Field(min_length=1, max_length=128)
    request_prompt: str = Field(min_length=1)
    budget_cents: int = Field(default=0, ge=0)
    budget_amount_cents: int = Field(default=0, ge=0)
    budget_amount: Decimal = Field(ge=0)
    currency_code: str = Field(default="USD", min_length=3, max_length=3)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    status: str = Field(default="OPEN", max_length=32)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProductSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    negotiation_id: UUID
    external_product_id: str | None = Field(default=None, max_length=255)
    title: str = Field(min_length=1)
    description: str | None = None
    catalog_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OfferSnapshotSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    negotiation_id: UUID
    product_id: UUID | None = None
    merchant_id: str = Field(min_length=1, max_length=255)
    merchant_name: str | None = Field(default=None, max_length=255)
    price_cents: int = Field(default=0, ge=0)
    offered_price_cents: int = Field(default=0, ge=0)
    offered_price: Decimal = Field(ge=0)
    currency_code: str = Field(default="USD", min_length=3, max_length=3)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    terms_payload: dict[str, Any] = Field(default_factory=dict)
    offer_hash: str = Field(min_length=64, max_length=64)
    snapshot_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MandateSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    negotiation_id: UUID
    mandate_id: str = Field(default_factory=lambda: f"mnd_{uuid4().hex[:12]}", min_length=1, max_length=128)
    schema_version: str = Field(default="mandate.v2", max_length=32)
    paypal_order_id: str | None = Field(default=None, max_length=128)
    authorized_cap_cents: int = Field(default=0, ge=0)
    currency_code: str = Field(default="USD", min_length=3, max_length=3)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    allowed_product_categories: list[str] = Field(default_factory=list)
    merchant_restrictions: list[str] = Field(default_factory=list)
    expected_paypal_payee_id: str | None = Field(default=None, max_length=128)
    public_key: str = Field(min_length=1, max_length=128)
    signature: str = Field(min_length=1)
    mandate_payload: dict[str, Any] = Field(default_factory=dict)
    nonce: str | None = Field(default=None, max_length=128)
    mandate_digest: str | None = Field(default=None, max_length=64)
    status: str = Field(default="ACTIVE", max_length=32)
    consumed_at: datetime | None = None
    consumption_tx_id: str | None = None
    issued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ConsentEvidenceSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    evidence_id: str = Field(min_length=1, max_length=128)
    mandate_id: str = Field(min_length=1, max_length=128)
    mandate_digest: str = Field(min_length=64, max_length=64)
    negotiation_id: UUID
    shopper_id: str = Field(min_length=1, max_length=128)
    approval_action: str = Field(default="AUTHORIZE_AND_MANDATE", max_length=64)
    evidence_payload: dict[str, Any] = Field(default_factory=dict)
    approved_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProductReferenceSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    negotiation_id: UUID
    product_id: UUID | None = None
    protocol_version: str = Field(default="v1", max_length=16)
    schema_version: str = Field(default="product-reference.v1", max_length=32)
    channel3_product_id: str = Field(min_length=1, max_length=255)
    offer_url: str = Field(min_length=1)
    merchant_domain: str = Field(min_length=1, max_length=255)
    canonical_category: str | None = None
    registry_version: str | None = None
    canonical_payload: dict[str, Any] = Field(default_factory=dict)
    price_cents: int = Field(default=0, ge=0)
    price_minor: int = Field(default=0, ge=0)
    currency_code: str = Field(default="USD", min_length=3, max_length=3)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    availability: str = Field(min_length=1, max_length=64)
    condition: str | None = None
    digest: str = Field(min_length=64, max_length=64)
    canonical_digest: str = Field(min_length=64, max_length=64)
    signature: str = Field(min_length=1)
    signature_b64: str = Field(min_length=1)
    public_key: str = Field(min_length=1, max_length=128)
    public_key_b64: str | None = None
    issued_at: datetime
    expires_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def model_validate(cls, obj: Any, *args: Any, **kwargs: Any) -> ProductReferenceSchema:
        if isinstance(obj, dict):
            obj = dict(obj)
            if "canonical_digest" in obj and "digest" not in obj:
                obj["digest"] = obj["canonical_digest"]
            elif "digest" in obj and "canonical_digest" not in obj:
                obj["canonical_digest"] = obj["digest"]
            if "signature_b64" in obj and "signature" not in obj:
                obj["signature"] = obj["signature_b64"]
            elif "signature" in obj and "signature_b64" not in obj:
                obj["signature_b64"] = obj["signature"]
            if "public_key_b64" in obj and "public_key" not in obj:
                obj["public_key"] = obj["public_key_b64"] or "pk_placeholder"
            elif "public_key" in obj and "public_key_b64" not in obj:
                obj["public_key_b64"] = obj["public_key"]
            if "price_minor" in obj and "price_cents" not in obj:
                obj["price_cents"] = obj["price_minor"]
            elif "price_cents" in obj and "price_minor" not in obj:
                obj["price_minor"] = obj["price_cents"]
            if "currency" in obj and "currency_code" not in obj:
                obj["currency_code"] = obj["currency"]
            elif "currency_code" in obj and "currency" not in obj:
                obj["currency"] = obj["currency_code"]
        return super().model_validate(obj, *args, **kwargs)


class MandateConsumptionSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    mandate_id: str = Field(min_length=1, max_length=128)
    mandate_digest: str = Field(min_length=64, max_length=64)
    consumed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    idempotency_key: str = Field(min_length=1, max_length=128)
    transaction_id: str = Field(min_length=1, max_length=128)
    paypal_order_id: str | None = Field(default=None, max_length=128)
    payment_event_id: UUID | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class VerificationEventSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    negotiation_id: UUID | None = None
    gate_name: str = Field(min_length=1, max_length=64)
    decision: Literal["PASSED", "BLOCKED"]
    passed_checks: list[Any] = Field(default_factory=list)
    blocked_checks: list[Any] = Field(default_factory=list)
    reason_details: str | None = None
    manifest_digest: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PaymentEventSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    negotiation_id: UUID | None = None
    idempotency_key: str = Field(min_length=1, max_length=128)
    paypal_order_id: str | None = Field(default=None, max_length=128)
    paypal_authorization_id: str | None = Field(default=None, max_length=128)
    paypal_capture_id: str | None = Field(default=None, max_length=128)
    paypal_payout_batch_id: str | None = Field(default=None, max_length=128)
    event_type: str = Field(min_length=1, max_length=64)
    amount_cents: int = Field(default=0, ge=0)
    amount: Decimal = Field(ge=0)
    currency_code: str = Field(default="USD", min_length=3, max_length=3)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    status: str = Field(default="CREATED", max_length=32)
    raw_response_summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
