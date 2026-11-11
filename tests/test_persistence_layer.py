"""Unit and failure-mode tests for the isolated repository and persistence layer."""

from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.repositories import (
    DatabaseSessionManager,
    NegotiationRepository,
    MandateRepository,
    AuditRepository,
    ReplayViolationError,
    MandateExpiredError,
    MandateAlreadyConsumedError,
    NotFoundError,
)


@pytest.fixture
def db_session():
    """Provides a transactional session connected to an in-memory test database."""
    manager = DatabaseSessionManager("sqlite:///:memory:")
    manager.create_tables()
    with manager.session_scope() as session:
        yield session
    manager.drop_tables()


# ============================================================================
# Failure Mode Test 1: Replay Protection on Payment Events (Duplicate Idempotency Key)
# ============================================================================

def test_duplicate_idempotency_key_rejected(db_session):
    """Verifies duplicate idempotency keys on payment_events are rejected deterministically."""
    audit_repo = AuditRepository(db_session)
    idempotency_key = "REQ-IDEMP-FAILMODE-001"

    # 1. First payment event records cleanly
    first_event = audit_repo.record_payment_event(
        idempotency_key=idempotency_key,
        event_type="AUTHORIZED",
        amount=Decimal("150.00"),
        amount_cents=15000,
        currency="USD",
        currency_code="USD",
        status="COMPLETED",
        paypal_order_id="ORD-111",
    )
    assert first_event.id is not None
    assert first_event.idempotency_key == idempotency_key

    # 2. Second attempt with the exact same idempotency key must raise ReplayViolationError
    with pytest.raises(ReplayViolationError) as exc_info:
        audit_repo.record_payment_event(
            idempotency_key=idempotency_key,
            event_type="AUTHORIZED",
            amount=Decimal("150.00"),
            amount_cents=15000,
            currency="USD",
            currency_code="USD",
            status="COMPLETED",
            paypal_order_id="ORD-111",
        )
    assert "Duplicate payment request detected" in str(exc_info.value)


# ============================================================================
# Failure Mode Test 2: Mandate Expiry Enforcement
# ============================================================================

def test_consume_expired_mandate_fails(db_session):
    """Verifies that attempting to consume an expired mandate fails deterministically."""
    neg_repo = NegotiationRepository(db_session)
    mandate_repo = MandateRepository(db_session)

    # Create negotiation
    neg = neg_repo.create_negotiation(
        shopper_id="shopper_expired_test",
        request_prompt="Buy widget",
        budget_cents=5000,
        currency="USD",
    )

    digest_val = "1" * 64
    # Create a mandate with expiration in the past
    past_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    mandate = mandate_repo.save_mandate(
        negotiation_id=neg.id,
        mandate_id="mnd_expired_001",
        public_key="pubkey_test_123",
        signature="sig_test_123",
        mandate_digest=digest_val,
        issued_at=past_time - timedelta(hours=1),
        expires_at=past_time,
        status="ACTIVE",
    )

    # Attempt to consume expired mandate with raise_on_error=True
    with pytest.raises(MandateExpiredError) as exc_info:
        mandate_repo.consume_mandate(
            mandate_id="mnd_expired_001",
            expected_digest=digest_val,
            idempotency_key="CONSUME-IDEMP-001",
            consumption_tx_id="TX-EXPIRED-001",
            raise_on_error=True,
        )
    assert "expired" in str(exc_info.value).lower()


# ============================================================================
# Failure Mode Test 3: Mandate Double-Consumption Prevention
# ============================================================================

def test_consume_already_consumed_mandate_fails(db_session):
    """Verifies that attempting to consume an already-consumed mandate fails deterministically."""
    neg_repo = NegotiationRepository(db_session)
    mandate_repo = MandateRepository(db_session)

    # Create negotiation
    neg = neg_repo.create_negotiation(
        shopper_id="shopper_double_consume_test",
        request_prompt="Buy gadget",
        budget_cents=10000,
        currency="USD",
    )

    digest_val = "2" * 64
    # Create active mandate expiring in future
    future_time = datetime.now(timezone.utc) + timedelta(hours=2)
    mandate = mandate_repo.save_mandate(
        negotiation_id=neg.id,
        mandate_id="mnd_double_consume_001",
        public_key="pubkey_test_456",
        signature="sig_test_456",
        mandate_digest=digest_val,
        expires_at=future_time,
        status="ACTIVE",
    )

    # 1. First consumption succeeds
    assert (
        mandate_repo.consume_mandate(
            mandate_id="mnd_double_consume_001",
            expected_digest=digest_val,
            idempotency_key="CONSUME-FIRST-001",
            consumption_tx_id="TX-SUCCESS-001",
        )
        is True
    )

    # Verify mandate status is updated to CONSUMED
    refreshed_mandate = mandate_repo.get_mandate_by_id("mnd_double_consume_001")
    assert refreshed_mandate.status == "CONSUMED"
    assert refreshed_mandate.consumed_at is not None

    # 2. Second consumption attempt must raise MandateAlreadyConsumedError when raise_on_error=True
    with pytest.raises(MandateAlreadyConsumedError) as exc_info:
        mandate_repo.consume_mandate(
            mandate_id="mnd_double_consume_001",
            expected_digest=digest_val,
            idempotency_key="CONSUME-SECOND-002",
            consumption_tx_id="TX-SECOND-002",
            raise_on_error=True,
        )
    assert "already" in str(exc_info.value).lower()


# ============================================================================
# Failure Mode Test 4: Monetary Minor-Unit Precision and Exact Boundaries
# ============================================================================

def test_monetary_values_minor_unit_boundaries(db_session):
    """Verifies exact integer minor unit (cents) consistency across monetary tables."""
    neg_repo = NegotiationRepository(db_session)
    audit_repo = AuditRepository(db_session)

    # 1. Negotiation budget cents
    budget_decimal = Decimal("499.99")
    neg = neg_repo.create_negotiation(
        shopper_id="shopper_cents_test",
        request_prompt="4K monitor under $500",
        budget_cents=49999,
        budget_amount=budget_decimal,
        currency="USD",
    )
    assert neg.budget_cents == 49999
    assert neg.budget_amount_cents == 49999
    assert neg.budget_amount == Decimal("499.99")

    # 2. Offer snapshot cents
    snapshot_price = Decimal("459.95")
    snapshot = neg_repo.create_offer_snapshot(
        negotiation_id=neg.id,
        merchant_id="merchant_dell",
        price_cents=45995,
        currency="USD",
        offer_hash="a" * 64,
    )
    assert snapshot.price_cents == 45995
    assert snapshot.offered_price_cents == 45995
    assert snapshot.offered_price == Decimal("459.95")

    # 3. Product reference minor units
    now = datetime.now(timezone.utc)
    pref = neg_repo.add_product_reference(
        negotiation_id=neg.id,
        channel3_product_id="ch3_prod_9988",
        offer_url="https://dell.com/item",
        merchant_domain="dell.com",
        price_cents=45995,
        currency="USD",
        availability="IN_STOCK",
        digest="b" * 64,
        signature="sig_b64_here",
        public_key="pk_here",
        issued_at=now,
        expires_at=now + timedelta(days=1),
    )
    assert pref.price_cents == 45995
    assert pref.price_minor == 45995

    # 4. Payment event cents
    pay_event = audit_repo.record_payment_event(
        idempotency_key="REQ-CENTS-TEST-001",
        event_type="AUTHORIZED",
        amount_cents=45995,
        currency="USD",
    )
    assert pay_event.amount_cents == 45995
    assert pay_event.amount == Decimal("459.95")


# ============================================================================
# Additional Test: Verification Event Append-Only Audit Logging
# ============================================================================

def test_verification_events_audit_logging(db_session):
    """Verifies recording and retrieving deterministic gate verification events."""
    neg_repo = NegotiationRepository(db_session)
    audit_repo = AuditRepository(db_session)

    neg = neg_repo.create_negotiation(
        shopper_id="shopper_gate_audit",
        request_prompt="Check policy gate",
        budget_cents=10000,
        currency="USD",
    )

    # Record PASS event
    pass_event = audit_repo.record_verification_event(
        negotiation_id=neg.id,
        gate_name="PRICE_CAP_GATE",
        decision="PASSED",
        passed_checks=["CAP_WITHIN_BOUNDS"],
        blocked_checks=[],
        reason_details="Under cap",
        manifest_digest="c" * 64,
    )
    assert pass_event.id is not None
    assert pass_event.decision == "PASSED"

    # Record BLOCK event
    block_event = audit_repo.record_verification_event(
        negotiation_id=neg.id,
        gate_name="MERCHANT_GATE",
        decision="BLOCKED",
        passed_checks=[],
        blocked_checks=["UNTRUSTED_MERCHANT"],
        reason_details="Merchant not in allowed list",
    )
    assert block_event.id is not None
    assert block_event.decision == "BLOCKED"

    # Query all events for this negotiation
    events = audit_repo.get_verification_events_for_negotiation(neg.id)
    assert len(events) == 2
    assert events[0].gate_name == "PRICE_CAP_GATE"
    assert events[1].gate_name == "MERCHANT_GATE"
