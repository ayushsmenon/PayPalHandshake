from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from payment_policy.db import PaymentPolicyDbAdapter
from payment_policy.mandate_service import (
    IssueMandateV2Request,
    issue_and_record_mandate_v2,
)
from src.repositories import DatabaseSessionManager


@pytest.fixture
def test_setup():
    manager = DatabaseSessionManager("sqlite:///:memory:")
    manager.create_tables()
    adapter = PaymentPolicyDbAdapter(session_manager=manager)
    private_key = Ed25519PrivateKey.generate()
    yield adapter, private_key
    manager.drop_tables()


def test_issue_and_record_mandate_v2_success(test_setup):
    adapter, private_key = test_setup
    neg_id = str(uuid4())
    req = IssueMandateV2Request(
        negotiation_id=neg_id,
        paypal_order_id="ORD-SANDBOX-101",
        authorized_cap_cents=50000,
        currency="USD",
        allowed_product_categories=["monitors"],
        merchant_restrictions=["bestbuy.com"],
        expected_paypal_payee_id="PAYEE-BESTBUY-01",
        expiry_seconds=3600,
    )

    resp = issue_and_record_mandate_v2(
        request=req,
        shopper_id="shopper_avery_singh",
        db_adapter=adapter,
        private_key=private_key,
    )

    assert resp.mandate.schema_version == "mandate.v2"
    assert resp.mandate.authorized_cap_cents == 50000
    assert resp.mandate.currency == "USD"
    assert resp.signature_b64 != ""
    assert resp.public_key_b64 != ""
    assert resp.mandate_digest_b64 != ""
    assert resp.nonce != ""
    assert resp.shopper_id == "shopper_avery_singh"

    db_mandate = adapter.get_mandate(resp.mandate.mandate_id)
    assert db_mandate is not None
    assert db_mandate.mandate_id == resp.mandate.mandate_id
    assert db_mandate.mandate_digest == resp.mandate_digest_b64
    assert db_mandate.status == "ACTIVE"


def test_issue_mandate_v2_requires_shopper_identity(test_setup):
    adapter, private_key = test_setup
    req = IssueMandateV2Request(
        negotiation_id=str(uuid4()),
        paypal_order_id="ORD-102",
        authorized_cap_cents=10000,
        allowed_product_categories=["keyboards"],
        merchant_restrictions=["bestbuy.com"],
    )

    with pytest.raises(ValueError, match="Authenticated shopper identity is required"):
        issue_and_record_mandate_v2(
            request=req,
            shopper_id="",
            db_adapter=adapter,
            private_key=private_key,
        )
