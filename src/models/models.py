"""PostgreSQL ORM models and Pydantic schemas for the PayPal Agent Platform.

Architecture Roles & Invariants:
- Process A / Shopper Intent: Tracks shopper constraints, prompt, budget (cents & decimal), currency.
- Process B / Negotiation: Tracks products, quotes, and cryptographic offer snapshots.
- Process C / Payment Policy Service: Audit logs for PayPal Orders v2, Authorizations,
  Captures, and Payouts with idempotency keys for replay protection.
- Verification Gates: Audit log of deterministic gate decisions and digests.
- Protocol Alignment: mandate.v2, product-reference.v1, minor unit integer precision.

SECURITY INVARIANTS:
- NO PayPal credentials, client secrets, or private Ed25519 signing keys are stored.
- Only public keys, cryptographic signatures, or verifiable reference digests are allowed.
- Replay prevention enforced for mandates and payment events.
"""

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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ============================================================================
# SQLite Compatibility Compilation Hooks
# Allows models to run seamlessly in local test suites with SQLite :memory:
# without altering PostgreSQL native DDL.
# ============================================================================

@compiles(PG_UUID, "sqlite")
def _compile_pg_uuid_sqlite(type_, compiler, **kw):  # type: ignore[no-untyped-def]
    return "VARCHAR(36)"


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):  # type: ignore[no-untyped-def]
    return "TEXT"


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
    # Integer minor units (cents) for exact cryptographic and financial comparison
    budget_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    budget_amount_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
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
    product_references: Mapped[list["ProductReference"]] = relationship(
        back_populates="negotiation", cascade="all, delete-orphan", lazy="select"
    )

    __table_args__ = (
        CheckConstraint("budget_amount >= 0", name="chk_negotiations_budget_positive"),
        CheckConstraint("budget_cents >= 0", name="chk_negotiations_budget_cents_positive"),
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
    product_references: Mapped[list["ProductReference"]] = relationship(
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
    # Minor integer units
    price_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    offered_price_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
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
        CheckConstraint("price_cents >= 0", name="chk_offer_snapshots_price_cents_positive"),
        CheckConstraint("length(currency) = 3", name="chk_offer_snapshots_currency_len"),
    )


class Mandate(Base):
    """Signed shopper spending authorizations aligned with mandate.v2 protocol.

    SECURITY INVARIANT: Contains NO private keys. Only Ed25519 public keys and signatures.
    Replay protection supported by unique mandate_id, digest, and consumption logging.
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
    # mandate.v2 explicit protocol fields
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

    # Relationships
    negotiation: Mapped["Negotiation"] = relationship(
        back_populates="mandates", lazy="select"
    )
    consumptions: Mapped[list["MandateConsumption"]] = relationship(
        back_populates="mandate", cascade="all, delete-orphan", lazy="select"
    )

    __table_args__ = (
        CheckConstraint("expires_at > issued_at", name="chk_mandates_expiry_valid"),
        CheckConstraint("authorized_cap_cents >= 0", name="chk_mandates_cap_cents_positive"),
    )


class ProductReference(Base):
    """Canonical, signed product reference conforming to product-reference.v1."""

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
    price_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False
    )
    availability: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    condition: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    canonical_digest: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    signature_b64: Mapped[str] = mapped_column(
        Text, nullable=False
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

    # Relationships
    negotiation: Mapped["Negotiation"] = relationship(
        back_populates="product_references", lazy="select"
    )
    product: Mapped["Product | None"] = relationship(
        back_populates="product_references", lazy="select"
    )

    __table_args__ = (
        CheckConstraint("expires_at > issued_at", name="chk_product_references_expiry"),
        CheckConstraint("price_minor >= 0", name="chk_product_references_price_positive"),
        CheckConstraint("length(currency) = 3", name="chk_product_references_currency"),
    )


class MandateConsumption(Base):
    """Enforces strict single-use mandate consumption replay prevention."""

    __tablename__ = "mandate_consumptions"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    mandate_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("mandates.id", ondelete="CASCADE"),
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

    # Relationships
    mandate: Mapped["Mandate"] = relationship(
        back_populates="consumptions", lazy="select"
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
    SECURITY INVARIANT: NO PayPal credentials, client secrets, or private auth tokens.
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
    amount_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
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
        CheckConstraint("amount_cents >= 0", name="chk_payment_events_amount_cents_positive"),
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
    budget_cents: int = Field(default=0, ge=0)
    budget_amount_cents: int = Field(default=0, ge=0)
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
    price_cents: int = Field(default=0, ge=0)
    offered_price_cents: int = Field(default=0, ge=0)
    currency: str = Field(min_length=3, max_length=3)
    terms_payload: dict[str, Any] = Field(default_factory=dict)
    offer_hash: str = Field(min_length=64, max_length=64)
    snapshot_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MandateSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    negotiation_id: UUID
    mandate_id: str = Field(min_length=1, max_length=128)
    schema_version: str = Field(default="mandate.v2", max_length=32)
    paypal_order_id: str | None = Field(default=None, max_length=128)
    authorized_cap_cents: int = Field(default=0, ge=0)
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


class ProductReferenceSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    negotiation_id: UUID
    product_id: UUID | None = None
    schema_version: str = Field(default="product-reference.v1", max_length=32)
    channel3_product_id: str = Field(min_length=1, max_length=255)
    offer_url: str = Field(min_length=1)
    merchant_domain: str = Field(min_length=1, max_length=255)
    price_minor: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    availability: str = Field(min_length=1, max_length=64)
    condition: str | None = None
    canonical_digest: str = Field(min_length=64, max_length=64)
    signature_b64: str = Field(min_length=1)
    public_key_b64: str | None = None
    issued_at: datetime
    expires_at: datetime
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MandateConsumptionSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    mandate_id: UUID
    mandate_digest: str = Field(min_length=64, max_length=64)
    consumed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    idempotency_key: str = Field(min_length=1, max_length=128)
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
    amount: Decimal = Field(ge=0)
    amount_cents: int = Field(default=0, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    status: str = Field(default="CREATED", max_length=32)
    raw_response_summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
