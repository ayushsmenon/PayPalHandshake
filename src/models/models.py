"""PostgreSQL ORM models and Pydantic schemas for the PayPal Agent Platform.

Architecture Roles & Invariants:
- Process A / Shopper Intent: Tracks shopper constraints, prompt, budget, currency.
- Process B / Negotiation: Tracks products, quotes, and cryptographic offer snapshots.
- Process C / Payment Policy Service: Audit logs for PayPal Orders v2, Authorizations,
  Captures, and Payouts with idempotency keys for replay protection.
- Verification Gates: Audit log of deterministic gate decisions and digests.

SECURITY INVARIANT:
- NO PayPal credentials, client secrets, or private Ed25519 signing keys are stored.
- Only public keys, cryptographic signatures, or verifiable reference digests are allowed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ============================================================================
# SQLAlchemy Declarative Base & Models
# ============================================================================

class Base(DeclarativeBase):
    pass


class Negotiation(Base):
    """Stores shopper intent, budget constraints, currency code, and negotiation lifecycle status."""

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
    budget_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False
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

    # Relationships (lazy loading by default)
    products: Mapped[list["Product"]] = relationship(
        back_populates="negotiation", cascade="all, delete-orphan", lazy="select"
    )
    offer_snapshots: Mapped[list["OfferSnapshot"]] = relationship(
        back_populates="negotiation", cascade="all, delete-orphan", lazy="select"
    )
    mandates: Mapped[list["Mandate"]] = relationship(
        back_populates="negotiation", cascade="all, delete-orphan", lazy="select"
    )
    verification_events: Mapped[list["VerificationEvent"]] = relationship(
        back_populates="negotiation", cascade="all, delete-orphan", lazy="select"
    )
    payment_events: Mapped[list["PaymentEvent"]] = relationship(
        back_populates="negotiation", cascade="all, delete-orphan", lazy="select"
    )

    __table_args__ = (
        CheckConstraint("budget_amount >= 0", name="chk_negotiations_budget_positive"),
        CheckConstraint("length(currency) = 3", name="chk_negotiations_currency_len"),
    )


class Product(Base):
    """Tracks items discovered or evaluated during the negotiation."""

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

    # Relationships
    negotiation: Mapped["Negotiation"] = relationship(
        back_populates="products", lazy="select"
    )
    offer_snapshots: Mapped[list["OfferSnapshot"]] = relationship(
        back_populates="product", lazy="select"
    )


class OfferSnapshot(Base):
    """Immutable record of merchant, price, currency, and terms frozen at selection/agreement time."""

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
    offered_price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False
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

    # Relationships
    negotiation: Mapped["Negotiation"] = relationship(
        back_populates="offer_snapshots", lazy="select"
    )
    product: Mapped["Product | None"] = relationship(
        back_populates="offer_snapshots", lazy="select"
    )

    __table_args__ = (
        CheckConstraint("offered_price >= 0", name="chk_offer_snapshots_price_positive"),
        CheckConstraint("length(currency) = 3", name="chk_offer_snapshots_currency_len"),
    )


class Mandate(Base):
    """Signed shopper spending authorizations and bounded agreements.

    SECURITY RULE: Contains NO private keys. Only Ed25519 public keys and signatures.
    """

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
    public_key: Mapped[str] = mapped_column(
        String(128), nullable=False, index=True
    )
    signature: Mapped[str] = mapped_column(
        Text, nullable=False
    )
    mandate_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ACTIVE", index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    negotiation: Mapped["Negotiation"] = relationship(
        back_populates="mandates", lazy="select"
    )

    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="chk_mandates_expiry_valid"),
    )


class VerificationEvent(Base):
    """Immutable audit log of all deterministic gate evaluations."""

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

    # Relationships
    negotiation: Mapped["Negotiation | None"] = relationship(
        back_populates="verification_events", lazy="select"
    )

    __table_args__ = (
        CheckConstraint("decision IN ('PASSED', 'BLOCKED')", name="chk_verification_events_decision"),
    )


class PaymentEvent(Base):
    """Immutable transaction log for PayPal Orders v2, Authorizations, Captures, and Payouts.

    REPLAY PROTECTION: Enforces uniqueness on idempotency_key (PayPal-Request-Id).
    SECURITY RULE: NO PayPal credentials, client secrets, or private auth tokens.
    """

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
    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False
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

    # Relationships
    negotiation: Mapped["Negotiation | None"] = relationship(
        back_populates="payment_events", lazy="select"
    )

    __table_args__ = (
        CheckConstraint("amount >= 0", name="chk_payment_events_amount_positive"),
        CheckConstraint("length(currency) = 3", name="chk_payment_events_currency_len"),
    )


# ============================================================================
# Pydantic v2 Models (DTO / Serializers / Validation)
# ============================================================================

class NegotiationSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    shopper_id: str = Field(min_length=1, max_length=128)
    request_prompt: str = Field(min_length=1)
    budget_amount: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
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
    offered_price: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    terms_payload: dict[str, Any] = Field(default_factory=dict)
    offer_hash: str = Field(min_length=64, max_length=64)
    snapshot_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MandateSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    negotiation_id: UUID
    public_key: str = Field(min_length=1, max_length=128)
    signature: str = Field(min_length=1)
    mandate_payload: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime
    status: str = Field(default="ACTIVE", max_length=32)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


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
    amount: Decimal = Field(ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    status: str = Field(default="CREATED", max_length=32)
    raw_response_summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
