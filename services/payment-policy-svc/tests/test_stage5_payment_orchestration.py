from __future__ import annotations

import base64
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from payment_policy.api import create_app
from payment_policy.consent import MandateConsentEvidence
from payment_policy.db import PaymentPolicyDbAdapter
from payment_policy.payment_service import (
    PaymentExecutionOrchestrator,
    PaymentOrchestrationError,
)
from payment_policy.process_b_client import ProcessBCheck, ProcessBClient, ProcessBResult
from payment_policy.versioned_crypto import (
    canonical_mandate_v2_bytes,
    canonical_post_authorization_binding_v1_bytes,
    mandate_v2_digest_b64,
    sign_mandate_v2,
    sign_post_authorization_binding_v1,
)
from payment_policy.versioned_models import (
    CandidateDealV2,
    MandateV2,
    PostAuthorizationBindingV1,
    ProductLineV2,
)
from src.repositories import DatabaseSessionManager


@pytest.fixture
def orchestration_env():
    manager = DatabaseSessionManager("sqlite:///:memory:")
    manager.create_tables()
    adapter = PaymentPolicyDbAdapter(session_manager=manager)
    key = Ed25519PrivateKey.generate()
    yield adapter, key
    manager.drop_tables()


def make_test_bundle(key: Ed25519PrivateKey, adapter: PaymentPolicyDbAdapter):
    now = datetime.now(timezone.utc)
    neg = adapter.save_negotiation(
        shopper_id="shopper_orch_test",
        request_prompt="Search monitor and keyboard",
        budget_cents=45000,
        currency_code="USD",
    )
    mandate = MandateV2(
        schema_version="mandate.v2",
        mandate_id=f"mnd_orch_{uuid4().hex[:8]}",
        negotiation_id=str(neg.id),
        paypal_order_id="ORD-ORCH-100",
        authorized_cap_cents=45000,
        currency="USD",
        allowed_product_categories=["monitors"],
        merchant_restrictions=["bestbuy.com"],
        issued_at=now,
        expires_at=now + timedelta(hours=2),
    )
    mandate_sig = sign_mandate_v2(mandate, key)
    digest = mandate_v2_digest_b64(mandate)

    pk_b64 = base64.b64encode(key.public_key().public_bytes_raw()).decode("ascii")
    adapter.save_mandate_v2(
        mandate=mandate,
        public_key=pk_b64,
        signature=mandate_sig,
        nonce="nonce_orch",
    )

    binding = PostAuthorizationBindingV1(
        schema_version="post-authorization-binding.v1",
        binding_id=f"bind_orch_{uuid4().hex[:8]}",
        mandate_id=mandate.mandate_id,
        mandate_digest_b64=digest,
        paypal_order_id="ORD-ORCH-100",
        paypal_auth_id="AUTH-ORCH-200",
        authorization_status="AUTHORIZED",
        authorized_amount_cents=45000,
        currency="USD",
        issued_at=now,
        expires_at=now + timedelta(hours=2),
    )
    binding_sig = sign_post_authorization_binding_v1(binding, key)

    candidate = CandidateDealV2(
        negotiation_id=str(neg.id),
        paypal_order_id="ORD-ORCH-100",
        merchant_id="bestbuy.com",
        total_cents=41850,
        currency="USD",
        products=[
            ProductLineV2(
                product_identifier="monitor-1",
                quantity=1,
                description="4K Monitor",
                unit_price_cents=41850,
            )
        ],
    )

    consent = MandateConsentEvidence(
        consent_event_id=f"EV-ORCH-{uuid4().hex[:8]}",
        mandate_id=mandate.mandate_id,
        mandate_digest_b64=digest,
        negotiation_id=str(neg.id),
        shopper_account_id="shopper_orch_test",
        approved_cap_cents=45000,
        currency="USD",
        approved_constraints=["monitors"],
        approved_merchant_restrictions=["bestbuy.com"],
        consented_at=now,
        expires_at=now + timedelta(hours=2),
    )

    return mandate, mandate_sig, binding, binding_sig, candidate, consent, neg.id


def test_payment_orchestrator_success(orchestration_env):
    adapter, key = orchestration_env
    (
        mandate,
        mandate_sig,
        binding,
        binding_sig,
        candidate,
        consent,
        neg_id,
    ) = make_test_bundle(key, adapter)

    mock_process_b = Mock(spec=ProcessBClient)
    mock_process_b.verify_authorization_binding.return_value = ProcessBResult(
        verdict="PASS",
        payment_ready=True,
        checks=[
            ProcessBCheck(name="mandate_v2_signature", verdict="PASS", detail="ok"),
            ProcessBCheck(name="authorization_cap", verdict="PASS", detail="ok"),
        ],
    )

    mock_paypal = Mock()
    mock_paypal.capture_authorization.return_value = {
        "id": "CAPTURE-SUCCESS-999",
        "status": "COMPLETED",
        "amount": {"value": "418.50", "currency_code": "USD"},
    }

    orchestrator = PaymentExecutionOrchestrator(
        db_adapter=adapter,
        process_b_client=mock_process_b,
        paypal_client=mock_paypal,
    )

    result = orchestrator.verify_and_capture_deal(
        mandate=mandate,
        mandate_signature_b64=mandate_sig,
        binding=binding,
        binding_signature_b64=binding_sig,
        candidate=candidate,
        consent_evidence=consent,
        idempotency_key="REQ-EXEC-001",
    )

    assert result["status"] == "COMPLETED"
    assert result["capture_id"] == "CAPTURE-SUCCESS-999"
    assert result["amount_cents"] == 41850
    assert result["verification_passed"] is True
    assert result["mandate_consumed"] is True

    db_mandate = adapter.get_mandate(mandate.mandate_id)
    assert db_mandate.status == "CONSUMED"
    assert db_mandate.consumed_at is not None

    with orchestration_env[0].session_manager.session_scope() as session:
        from src.repositories import AuditRepository

        audit = AuditRepository(session)
        events = audit.get_verification_events_for_negotiation(neg_id)
        assert len(events) >= 1
        assert events[-1].decision == "PASSED"


def test_payment_orchestrator_blocks_on_process_b_rejection(orchestration_env):
    adapter, key = orchestration_env
    (
        mandate,
        mandate_sig,
        binding,
        binding_sig,
        candidate,
        consent,
        neg_id,
    ) = make_test_bundle(key, adapter)

    mock_process_b = Mock(spec=ProcessBClient)
    from payment_policy.process_b_client import ProcessBVerificationError

    mock_process_b.verify_authorization_binding.side_effect = (
        ProcessBVerificationError("Spending cap check failed.")
    )

    mock_paypal = Mock()
    orchestrator = PaymentExecutionOrchestrator(
        db_adapter=adapter,
        process_b_client=mock_process_b,
        paypal_client=mock_paypal,
    )

    with pytest.raises(PaymentOrchestrationError, match="Process B verification gate rejected"):
        orchestrator.verify_and_capture_deal(
            mandate=mandate,
            mandate_signature_b64=mandate_sig,
            binding=binding,
            binding_signature_b64=binding_sig,
            candidate=candidate,
            consent_evidence=consent,
            idempotency_key="REQ-EXEC-FAIL-01",
        )

    mock_paypal.capture_authorization.assert_not_called()

    db_mandate = adapter.get_mandate(mandate.mandate_id)
    assert db_mandate.status == "ACTIVE"
    assert db_mandate.consumed_at is None

    with orchestration_env[0].session_manager.session_scope() as session:
        from src.repositories import AuditRepository

        audit = AuditRepository(session)
        events = audit.get_verification_events_for_negotiation(neg_id)
        assert len(events) >= 1
        assert events[-1].decision == "BLOCKED"


def test_api_capture_endpoint_integration(orchestration_env):
    adapter, key = orchestration_env
    (
        mandate,
        mandate_sig,
        binding,
        binding_sig,
        candidate,
        consent,
        _,
    ) = make_test_bundle(key, adapter)

    mock_process_b = Mock(spec=ProcessBClient)
    mock_process_b.verify_authorization_binding.return_value = ProcessBResult(
        verdict="PASS",
        payment_ready=True,
        checks=[ProcessBCheck(name="mandate_v2_signature", verdict="PASS", detail="ok")],
    )

    mock_paypal = Mock()
    mock_paypal.capture_authorization.return_value = {
        "id": "CAPTURE-API-111",
        "status": "COMPLETED",
    }

    app = create_app(
        db_adapter=adapter,
        process_b_client=mock_process_b,
        paypal_client=mock_paypal,
        environment="test",
    )

    payload = {
        "mandate": mandate.model_dump(mode="json"),
        "mandate_signature": mandate_sig,
        "post_authorization_binding": binding.model_dump(mode="json"),
        "binding_signature": binding_sig,
        "candidate": candidate.model_dump(mode="json"),
        "consent_evidence": consent.model_dump(mode="json"),
        "idempotency_key": "REQ-API-CAPTURE-001",
    }

    with TestClient(app) as client:
        resp = client.post("/internal/v2/payments/capture", json=payload)

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "COMPLETED"
    assert data["capture_id"] == "CAPTURE-API-111"
    assert data["mandate_consumed"] is True
