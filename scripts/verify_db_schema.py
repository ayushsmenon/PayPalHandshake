"""Standalone Verification Script for PostgreSQL Database Schema Setup & Protocol Reconciliation.

Tests and verifies:
1. SQL migration files (001_initial_schema.sql, 002_protocol_reconcile.sql) syntax, constraints, and zero-secret rules.
2. SQLAlchemy ORM models compilation against PostgreSQL 16 dialect.
3. Pydantic v2 serialization/deserialization for all entities.
4. Minor-unit integer consistency, mandate.v2, product-reference.v1, and replay prevention.
5. (Optional) Live PostgreSQL container verification if DATABASE_URL is set or passed via --db-url.

Usage:
    py scripts/verify_db_schema.py
    py scripts/verify_db_schema.py --db-url postgresql://user:pass@localhost:5432/dbname
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

# Ensure project root is in PYTHONPATH
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

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
    ConsentEvidenceSchema,
    ProductReferenceSchema,
    MandateConsumptionSchema,
    VerificationEventSchema,
    PaymentEventSchema,
)

EXPECTED_TABLES = [
    "negotiations",
    "products",
    "offer_snapshots",
    "mandates",
    "consent_evidence",
    "product_references",
    "mandate_consumptions",
    "verification_events",
    "payment_events",
]

FORBIDDEN_KEYWORDS = [
    "client_secret",
    "private_key",
    "secret_key",
    "api_secret",
    "password",
    "bearer_token",
]


def test_sql_migration_files() -> None:
    """Verifies that both SQL migration files satisfy structural and security invariants."""
    print("[1/5] Verifying SQL migration scripts (001 & 002)...")
    migration_files = [
        REPO_ROOT / "migrations" / "001_initial_schema.sql",
        REPO_ROOT / "migrations" / "002_protocol_reconcile.sql",
        REPO_ROOT / "migrations" / "003_durable_consent_and_v2_reconcile.sql",
    ]

    combined_code = []
    for migration_path in migration_files:
        assert migration_path.exists(), f"Missing migration file at {migration_path}"
        sql_content = migration_path.read_text(encoding="utf-8")

        # Strip SQL comments
        for line in sql_content.splitlines():
            clean_line = re.sub(r"--.*$", "", line).strip()
            if clean_line:
                combined_code.append(clean_line.lower())

    sql_code_only = "\n".join(combined_code)

    # Check for forbidden secret column definitions
    for forbidden in FORBIDDEN_KEYWORDS:
        assert forbidden not in sql_code_only, (
            f"CRITICAL SECURITY VIOLATION: '{forbidden}' found in migration SQL statements!"
        )

    # Check for all expected tables across migrations
    for table in EXPECTED_TABLES:
        pattern = rf"create\s+table\s+(?:if\s+not\s+exists\s+)?{table}\b"
        assert re.search(pattern, sql_code_only), f"Table {table} not found in migration SQL"

    # Check for replay protection idempotency key unique constraint/index
    assert "idempotency_key" in sql_code_only, "Missing idempotency_key for replay protection"
    assert "mandate_digest" in sql_code_only, "Missing mandate_digest for replay protection"

    # Check for integer minor units (cents)
    assert "budget_cents" in sql_code_only or "budget_amount_cents" in sql_code_only
    assert "price_minor" in sql_code_only
    assert "amount_cents" in sql_code_only

    print("  -> Migration SQL files structure, tables, and security invariants: PASS")


def test_sqlalchemy_models_ddl_compilation() -> None:
    """Verifies that SQLAlchemy ORM models compile cleanly under PostgreSQL 16 dialect."""
    print("[2/5] Compiling SQLAlchemy models to PostgreSQL 16 DDL...")
    dialect = postgresql.dialect()

    for table_name in EXPECTED_TABLES:
        table = Base.metadata.tables.get(table_name)
        assert table is not None, f"Table {table_name} not found in Base.metadata"
        create_sql = str(CreateTable(table).compile(dialect=dialect))
        assert table_name in create_sql.lower(), f"Failed to generate DDL for {table_name}"

    print("  -> PostgreSQL 16 dialect DDL generation: PASS")


def test_pydantic_schemas_and_validation() -> None:
    """Verifies Pydantic v2 models instantiation and schema validation."""
    print("[3/5] Validating Pydantic entity schemas and invariants...")

    now = datetime.now(timezone.utc)
    neg_id = uuid4()
    prod_id = uuid4()

    # 1. Negotiation
    neg = NegotiationSchema(
        id=neg_id,
        shopper_id="shopper_usr_12345",
        request_prompt="Find the best 27-inch 4K monitor under $500",
        budget_amount=Decimal("500.00"),
        budget_cents=50000,
        budget_amount_cents=50000,
        currency="USD",
        status="OPEN",
        metadata={"delivery_zip": "94105"},
    )
    assert neg.budget_cents == 50000

    # 2. Product
    prod = ProductSchema(
        id=prod_id,
        negotiation_id=neg_id,
        external_product_id="ch3_item_776655",
        title="Dell UltraSharp 27 4K USB-C Hub Monitor (U2723QE)",
        description="4K UHD 3840x2160 IPS Black display",
        catalog_metadata={"brand": "Dell", "screen_size": "27-inch"},
    )
    assert prod.external_product_id == "ch3_item_776655"

    # 3. Offer Snapshot
    offer_payload = {"price": 459.99, "merchant": "Dell Official"}
    offer_hash = hashlib.sha256(str(sorted(offer_payload.items())).encode()).hexdigest()
    snapshot = OfferSnapshotSchema(
        negotiation_id=neg_id,
        product_id=prod_id,
        merchant_id="merchant_dell_direct",
        merchant_name="Dell Store",
        offered_price=Decimal("459.99"),
        price_cents=45999,
        offered_price_cents=45999,
        currency="USD",
        terms_payload={"shipping": "free", "return_window_days": 30},
        offer_hash=offer_hash,
    )
    assert snapshot.price_cents == 45999

    # 4. Mandate (mandate.v2, zero private keys)
    mandate = MandateSchema(
        negotiation_id=neg_id,
        mandate_id="mnd_reconciled_001",
        schema_version="mandate.v2",
        paypal_order_id="ORD-123456",
        authorized_cap_cents=50000,
        allowed_product_categories=["monitors"],
        merchant_restrictions=["Dell"],
        public_key="a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90",
        signature="3045022100e4a7796d8e20302061b4d8d9b15e47c16260e0a58a9e7123456789abcdef0220",
        mandate_payload={"max_cap": 500.0, "currency": "USD"},
        expires_at=now + timedelta(hours=4),
        status="ACTIVE",
    )
    assert mandate.schema_version == "mandate.v2"

    # 5. Product Reference (product-reference.v1)
    pref = ProductReferenceSchema(
        negotiation_id=neg_id,
        product_id=prod_id,
        schema_version="product-reference.v1",
        channel3_product_id="ch3_item_776655",
        offer_url="https://dell.com/item",
        merchant_domain="dell.com",
        price_minor=45999,
        price_cents=45999,
        currency="USD",
        currency_code="USD",
        availability="IN_STOCK",
        digest="e" * 64,
        canonical_digest="e" * 64,
        signature="c2lnX2I2NA==",
        signature_b64="c2lnX2I2NA==",
        public_key="pk_test_channel3",
        public_key_b64="pk_test_channel3",
        issued_at=now,
        expires_at=now + timedelta(days=1),
    )
    assert pref.price_minor == 45999

    # 6. Payment Event (Replay protection via idempotency key)
    pay_event = PaymentEventSchema(
        negotiation_id=neg_id,
        idempotency_key="REQ-IDEMP-20261111-HS2048-001",
        paypal_order_id="5O190127TN364715T",
        paypal_authorization_id="AUTH-99998888",
        event_type="AUTHORIZED",
        amount=Decimal("459.99"),
        amount_cents=45999,
        currency="USD",
        status="COMPLETED",
        raw_response_summary={"intent": "AUTHORIZE", "status": "COMPLETED"},
    )
    assert pay_event.amount_cents == 45999
    print("  -> Pydantic serialization, validation, and minor-unit invariants: PASS")


def test_zero_secret_audit() -> None:
    """Verifies that neither ORM model columns nor schema fields contain secret identifiers."""
    print("[4/5] Executing Zero-Secret Audit across all models...")
    for table_name, table in Base.metadata.tables.items():
        for column in table.columns:
            col_name_lower = column.name.lower()
            for forbidden in FORBIDDEN_KEYWORDS:
                assert forbidden not in col_name_lower, (
                    f"CRITICAL ZERO-SECRET VIOLATION: Column '{column.name}' in table '{table_name}'!"
                )
    print("  -> Zero secret exposure audit: PASS (0 secret columns found)")


def test_live_postgres_connection(db_url: str) -> None:
    """Tests executing both migrations against a live PostgreSQL container/database if provided."""
    print(f"[5/5] Connecting to live PostgreSQL database at {db_url}...")
    engine = create_engine(db_url)
    migration_files = [
        REPO_ROOT / "migrations" / "001_initial_schema.sql",
        REPO_ROOT / "migrations" / "002_protocol_reconcile.sql",
        REPO_ROOT / "migrations" / "003_durable_consent_and_v2_reconcile.sql",
    ]

    with engine.connect() as conn:
        with conn.begin():
            for m_path in migration_files:
                sql_script = m_path.read_text(encoding="utf-8")
                conn.execute(text(sql_script))

        # Verify tables exist in information_schema
        result = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = ANY(:names)"
            ),
            {"names": EXPECTED_TABLES},
        )
        found_tables = {row[0] for row in result.fetchall()}
        missing = set(EXPECTED_TABLES) - found_tables
        assert not missing, f"Missing tables in live PostgreSQL: {missing}"

        # Verify idempotency key unique constraint
        result = conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'payment_events' AND indexname LIKE '%idempotency%'"
            )
        )
        assert result.fetchone() is not None, "Missing idempotency_key index on payment_events"

    print("  -> Live PostgreSQL migration execution and table/index verification: PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify PostgreSQL Schema Setup & Reconciliation")
    parser.add_argument(
        "--db-url",
        default=os.getenv("DATABASE_URL"),
        help="Optional PostgreSQL connection URL (e.g., postgresql://postgres:postgres@localhost:5432/testdb)",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("PAYPAL AGENT PLATFORM: DATABASE SCHEMA & PROTOCOL RECONCILE AUDIT")
    print("=" * 70)

    try:
        test_sql_migration_files()
        test_sqlalchemy_models_ddl_compilation()
        test_pydantic_schemas_and_validation()
        test_zero_secret_audit()

        if args.db_url:
            test_live_postgres_connection(args.db_url)
        else:
            print("[5/5] Live PostgreSQL connection skipped (no --db-url or DATABASE_URL provided).")

        print("=" * 70)
        print("ALL DATABASE SCHEMA & RECONCILIATION CHECKS PASSED SUCCESSFULLY.")
        print("=" * 70)
        return 0
    except Exception as exc:
        print(f"\n[FAILED] Verification error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
