"""Standalone test suite verifying SQLAlchemy declarative models, DDL generation, and schemas."""

from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.models import (
    Base,
    Negotiation,
    Product,
    OfferSnapshot,
    Mandate,
    ProductReference,
    MandateConsumption,
    VerificationEvent,
    PaymentEvent,
    NegotiationSchema,
    ProductSchema,
    OfferSnapshotSchema,
    MandateSchema,
    ProductReferenceSchema,
    MandateConsumptionSchema,
    VerificationEventSchema,
    PaymentEventSchema,
)
from src.repositories import DatabaseSessionManager


@pytest.fixture
def db_manager():
    """In-memory SQLite session manager with full model tables created."""
    manager = DatabaseSessionManager("sqlite:///:memory:")
    manager.create_tables()
    yield manager
    manager.drop_tables()


def test_models_metadata_completeness():
    """Verifies all expected core and reconciled tables exist in SQLAlchemy metadata."""
    expected_tables = {
        "negotiations",
        "products",
        "offer_snapshots",
        "mandates",
        "product_references",
        "mandate_consumptions",
        "verification_events",
        "payment_events",
    }
    actual_tables = set(Base.metadata.tables.keys())
    assert expected_tables.issubset(actual_tables), f"Missing tables: {expected_tables - actual_tables}"


def test_postgresql_dialect_ddl_generation():
    """Verifies that all models cleanly generate DDL under PostgreSQL 16 dialect without errors."""
    dialect = postgresql.dialect()
    for table_name, table in Base.metadata.tables.items():
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert table_name in ddl.lower()
        # Verify no secret keywords in generated DDL statements
        for forbidden in ["client_secret", "private_key", "secret_key"]:
            assert forbidden not in ddl.lower(), f"Forbidden keyword '{forbidden}' in table '{table_name}' DDL"


def test_sqlite_session_create_all_and_query(db_manager: DatabaseSessionManager):
    """Verifies that models create cleanly and allow session operations against a test session."""
    with db_manager.session_scope() as session:
        # Create a negotiation
        neg = Negotiation(
            shopper_id="shopper_baseline_01",
            request_prompt="Search monitor under $400",
            budget_amount=Decimal("400.00"),
            budget_cents=40000,
            budget_amount_cents=40000,
            currency="USD",
            status="OPEN",
            metadata_={"test": True},
        )
        session.add(neg)
        session.flush()

        # Create a product
        prod = Product(
            negotiation_id=neg.id,
            external_product_id="ch3_456",
            title="Dell 27 Monitor",
            catalog_metadata={"brand": "Dell"},
        )
        session.add(prod)
        session.flush()

        # Create a product reference
        now = datetime.now(timezone.utc)
        pref = ProductReference(
            negotiation_id=neg.id,
            product_id=prod.id,
            schema_version="product-reference.v1",
            channel3_product_id="ch3_456",
            offer_url="https://example.com/product",
            merchant_domain="example.com",
            price_minor=37999,
            currency="USD",
            availability="IN_STOCK",
            canonical_digest="d" * 64,
            signature_b64="c2lnbmF0dXJl",
            issued_at=now,
            expires_at=now + timedelta(days=1),
        )
        session.add(pref)
        session.flush()

        # Query back and verify relations
        assert neg.id is not None
        assert len(neg.products) == 1
        assert neg.products[0].title == "Dell 27 Monitor"
        assert len(neg.product_references) == 1
        assert neg.product_references[0].price_minor == 37999


def test_pydantic_schemas_validation():
    """Verifies Pydantic v2 schemas instantiation and constraints."""
    now = datetime.now(timezone.utc)
    neg_id = uuid4()

    mandate = MandateSchema(
        negotiation_id=neg_id,
        mandate_id="mnd_test_baseline_01",
        schema_version="mandate.v2",
        paypal_order_id="ORD-998877",
        authorized_cap_cents=50000,
        allowed_product_categories=["monitors", "displays"],
        merchant_restrictions=["Dell", "Amazon"],
        public_key="a" * 64,
        signature="b" * 64,
        expires_at=now + timedelta(hours=2),
    )
    assert mandate.schema_version == "mandate.v2"
    assert mandate.authorized_cap_cents == 50000
    assert len(mandate.allowed_product_categories) == 2
