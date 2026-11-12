from __future__ import annotations

import base64
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from payment_policy.consent import MandateConsentEvidence
from payment_policy.db import PaymentPolicyDbAdapter
from payment_policy.versioned_crypto import (
    canonical_mandate_v2_bytes,
    mandate_v2_digest_b64,
    sign_mandate_v2,
)
from payment_policy.versioned_models import MandateV2
from src.repositories import (
    DatabaseSessionManager,
    ReplayViolationError,
    MandateExpiredError,
    MandateAlreadyConsumedError,
)


@pytest.fixture
def db_adapter():
    manager = DatabaseSessionManager("sqlite:///:memory:")
    manager.create_tables()
    adapter = PaymentPolicyDbAdapter(session_manager=manager)
    yield adapter
    manager.drop_tables()


def test_db_adapter_negotiation_and_mandate_lifecycle(db_adapter: PaymentPolicyDbAdapter):
    neg = db_adapter.save_negotiation(
        shopper_id="shopper_adapter_01",
        request_prompt="Search monitor and keyboard",
        budget_cents=45000,
        currency_code="USD",
    )
    assert neg.id is not None
    assert neg.budget_cents == 45000

    private_key = Ed25519PrivateKey.generate()
    public_key_b64 = base64.b64encode(
        private_key.public_key().public_bytes_raw()
    ).decode("ascii")

    now = datetime.now(timezone.utc)
    mandate = MandateV2(
        schema_version="mandate.v2",
        mandate_id="mnd_adapter_001",
        negotiation_id=str(neg.id),
        paypal_order_id="ORD-ADAPTER-001",
        authorized_cap_cents=45000,
        currency="USD",
        allowed_product_categories=["monitors", "keyboards"],
        merchant_restrictions=["bestbuy.com"],
        issued_at=now,
        expires_at=now + timedelta(hours=2),
    )
    sig = sign_mandate_v2(mandate, private_key)
    digest = mandate_v2_digest_b64(mandate)

    saved_mandate = db_adapter.save_mandate_v2(
        mandate=mandate,
        public_key=public_key_b64,
        signature=sig,
        nonce="nonce_12345",
    )
    assert saved_mandate.mandate_id == "mnd_adapter_001"
    assert saved_mandate.mandate_digest == digest
    assert saved_mandate.status == "ACTIVE"

    retrieved = db_adapter.get_mandate("mnd_adapter_001")
    assert retrieved is not None
    assert retrieved.mandate_id == "mnd_adapter_001"

    with pytest.raises(ReplayViolationError):
        db_adapter.save_mandate_v2(
            mandate=mandate,
            public_key=public_key_b64,
            signature=sig,
            nonce="nonce_12345",
        )

    evidence = MandateConsentEvidence(
        consent_event_id="EV-ADAPTER-CONSENT-01",
        mandate_id="mnd_adapter_001",
        mandate_digest_b64=digest,
        negotiation_id=str(neg.id),
        shopper_account_id="shopper_adapter_01",
        approved_cap_cents=45000,
        currency="USD",
        approved_constraints=["monitors", "keyboards"],
        approved_merchant_restrictions=["bestbuy.com"],
        consented_at=now,
        expires_at=now + timedelta(hours=2),
    )

    consent_store = db_adapter.consent_store
    claimed_first = consent_store.record_and_claim(evidence)
    assert claimed_first is True

    claimed_second = consent_store.record_and_claim(evidence)
    assert claimed_second is False

    consumed = db_adapter.consume_mandate(
        mandate_id="mnd_adapter_001",
        expected_digest=digest,
        idempotency_key="REQ-CONSUME-001",
        tx_id="TX-001",
        raise_on_error=True,
    )
    assert consumed is True

    with pytest.raises(MandateAlreadyConsumedError):
        db_adapter.consume_mandate(
            mandate_id="mnd_adapter_001",
            expected_digest=digest,
            idempotency_key="REQ-CONSUME-002",
            tx_id="TX-002",
            raise_on_error=True,
        )


def test_db_adapter_audit_events(db_adapter: PaymentPolicyDbAdapter):
    neg_id = uuid4()
    verif = db_adapter.record_verification_event(
        gate_name="PRICE_CAP_GATE",
        decision="PASSED",
        negotiation_id=neg_id,
        passed_checks=["CAP_WITHIN_BOUNDS"],
        reason_details="Under budget cap",
        manifest_digest="a" * 64,
    )
    assert verif.id is not None
    assert verif.decision == "PASSED"

    pay = db_adapter.record_payment_event(
        idempotency_key="REQ-PAY-ADAPTER-01",
        event_type="AUTHORIZED",
        amount_cents=41850,
        currency_code="USD",
        negotiation_id=neg_id,
        paypal_order_id="ORD-123",
        paypal_authorization_id="AUTH-123",
        status="COMPLETED",
    )
    assert pay.id is not None
    assert pay.amount_cents == 41850

    with pytest.raises(ReplayViolationError):
        db_adapter.record_payment_event(
            idempotency_key="REQ-PAY-ADAPTER-01",
            event_type="AUTHORIZED",
            amount_cents=41850,
            currency_code="USD",
        )
