import base64
from datetime import datetime, timedelta, timezone
import json
import os
import pathlib
import sys
import urllib.request
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine, select

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "services" / "payment-policy-svc"))
sys.path.insert(0, str(REPO_ROOT / "services" / "mandate-gate"))

from payment_policy.consent import MandateConsentEvidence
from payment_policy.db import PaymentPolicyDbAdapter
from payment_policy.product_reference import (
    ProductReferenceV2,
    sign_product_reference_v2,
)
from payment_policy.versioned_crypto import (
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
from src.models import VerificationEvent, PaymentEvent

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://postgres@127.0.0.1:5432/paypal_handshake"
)
GATE_URL = "http://127.0.0.1:8002"
POLICY_URL = "http://127.0.0.1:8003"
ORCHESTRATOR_URL = "http://127.0.0.1:8001"

def run_scenarios():
    print("=" * 65)
    print("PAYPAL HANDSHAKE: PRE-SUBMISSION SECURITY & INTEGRATION SCENARIOS")
    print("=" * 65)

    from src.repositories import DatabaseSessionManager
    session_manager = DatabaseSessionManager(db_url=DATABASE_URL)
    db_adapter = PaymentPolicyDbAdapter(session_manager=session_manager)
    signing_key = Ed25519PrivateKey.generate()
    pub_key_b64 = base64.b64encode(signing_key.public_key().public_bytes_raw()).decode("ascii")

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=3)
    neg_uuid = UUID("00000000-0000-0000-0000-000000002048")

    print("\n[Scenario 1] Blocked Deal: Over-Budget / Unverified Category Test")
    bad_mandate = MandateV2(
        schema_version="mandate.v2",
        mandate_id=f"mnd_scenario1_{uuid4().hex[:6]}",
        negotiation_id=str(neg_uuid),
        paypal_order_id="ORD-SCENARIO1",
        authorized_cap_cents=45000,
        currency="USD",
        allowed_product_categories=["electronics.monitors"],
        merchant_restrictions=["merchant.example"],
        issued_at=now,
        expires_at=expires_at,
    )
    bad_mandate_sig = sign_mandate_v2(bad_mandate, signing_key)
    bad_digest = mandate_v2_digest_b64(bad_mandate)

    bad_binding = PostAuthorizationBindingV1(
        schema_version="post-authorization-binding.v1",
        binding_id=f"bind_scenario1_{uuid4().hex[:6]}",
        mandate_id=bad_mandate.mandate_id,
        mandate_digest_b64=bad_digest,
        paypal_order_id="ORD-SCENARIO1",
        paypal_auth_id="AUTH-SCENARIO1",
        authorization_status="AUTHORIZED",
        authorized_amount_cents=45000,
        currency="USD",
        issued_at=now,
        expires_at=expires_at,
    )
    bad_binding_sig = sign_post_authorization_binding_v1(bad_binding, signing_key)

    overbudget_candidate = CandidateDealV2(
        negotiation_id=str(neg_uuid),
        paypal_order_id="ORD-SCENARIO1",
        merchant_id="merchant.example",
        total_cents=52000,
        currency="USD",
        products=[
            ProductLineV2(
                product_identifier="monitor-1",
                quantity=1,
                description="Overbudget Monitor",
                unit_price_cents=52000,
                offer_url="https://merchant.example/monitor-1",
                product_reference=sign_product_reference_v2(
                    ProductReferenceV2(
                        schema_version="product-reference.v2",
                        channel3_product_id="monitor-1",
                        offer_url="https://merchant.example/monitor-1",
                        merchant_domain="merchant.example",
                        price_minor=52000,
                        currency="USD",
                        availability="InStock",
                        condition="new",
                        issued_at=now,
                        expires_at=expires_at,
                        canonical_category="electronics.monitors",
                        registry_version="channel3-reviewed-2026.11",
                    ),
                    signing_key,
                ),
            )
        ],
    )

    from mandate_gate.versioned_gate import evaluate_v2_candidate
    result_s1 = evaluate_v2_candidate(
        mandate=bad_mandate.model_dump(mode="json"),
        mandate_signature_b64=bad_mandate_sig,
        binding=bad_binding.model_dump(mode="json"),
        binding_signature_b64=bad_binding_sig,
        candidate=overbudget_candidate.model_dump(mode="json"),
        mandate_public_key_b64=pub_key_b64,
        binding_public_key_b64=pub_key_b64,
        now=now,
    )

    assert result_s1.verdict == "REJECT"
    assert result_s1.payment_ready is False
    spending_check = next((c for c in result_s1.checks if c.name == "spending_cap"), None)
    assert spending_check is not None and spending_check.verdict == "REJECT"

    db_adapter.record_verification_event(
        gate_name="PROCESS_B_V2_GATE",
        decision="BLOCKED",
        negotiation_id=neg_uuid,
        passed_checks=[c.name for c in result_s1.checks if c.verdict == "PASS"],
        blocked_checks=[c.name for c in result_s1.checks if c.verdict != "PASS"],
        reason_details=spending_check.detail,
        manifest_digest=bad_digest,
    )
    print("  -> Process B Verdict: REJECT (Payment Ready: False)")
    print(f"  -> Blocked Gate: {spending_check.name} ({spending_check.detail})")
    print("  -> PostgreSQL Audit Log: Logged BLOCKED event in verification_events")

    print("\n[Scenario 2] Approved Deal & Persistent Audit Execution")
    valid_mandate = MandateV2(
        schema_version="mandate.v2",
        mandate_id=f"mnd_scenario2_{uuid4().hex[:6]}",
        negotiation_id=str(neg_uuid),
        paypal_order_id="ORD-SCENARIO2",
        authorized_cap_cents=45000,
        currency="USD",
        allowed_product_categories=["electronics.monitors", "electronics.keyboards"],
        merchant_restrictions=["merchant.example"],
        issued_at=now,
        expires_at=expires_at,
    )
    valid_mandate_sig = sign_mandate_v2(valid_mandate, signing_key)
    valid_digest = mandate_v2_digest_b64(valid_mandate)

    db_adapter.save_mandate_v2(
        mandate=valid_mandate,
        public_key=pub_key_b64,
        signature=valid_mandate_sig,
        nonce="nonce_scenario2",
    )

    valid_binding = PostAuthorizationBindingV1(
        schema_version="post-authorization-binding.v1",
        binding_id=f"bind_scenario2_{uuid4().hex[:6]}",
        mandate_id=valid_mandate.mandate_id,
        mandate_digest_b64=valid_digest,
        paypal_order_id="ORD-SCENARIO2",
        paypal_auth_id="AUTH-SCENARIO2",
        authorization_status="AUTHORIZED",
        authorized_amount_cents=45000,
        currency="USD",
        issued_at=now,
        expires_at=expires_at,
    )
    valid_binding_sig = sign_post_authorization_binding_v1(valid_binding, signing_key)

    valid_candidate = CandidateDealV2(
        negotiation_id=str(neg_uuid),
        paypal_order_id="ORD-SCENARIO2",
        merchant_id="merchant.example",
        total_cents=41850,
        currency="USD",
        products=[
            ProductLineV2(
                product_identifier="monitor-1",
                quantity=1,
                description="27-inch 4K monitor",
                unit_price_cents=32900,
                offer_url="https://merchant.example/monitor-1",
                product_reference=sign_product_reference_v2(
                    ProductReferenceV2(
                        schema_version="product-reference.v2",
                        channel3_product_id="monitor-1",
                        offer_url="https://merchant.example/monitor-1",
                        merchant_domain="merchant.example",
                        price_minor=32900,
                        currency="USD",
                        availability="InStock",
                        condition="new",
                        issued_at=now,
                        expires_at=expires_at,
                        canonical_category="electronics.monitors",
                        registry_version="channel3-reviewed-2026.11",
                    ),
                    signing_key,
                ),
            ),
            ProductLineV2(
                product_identifier="keyboard-1",
                quantity=1,
                description="Mechanical keyboard",
                unit_price_cents=8950,
                offer_url="https://merchant.example/keyboard-1",
                product_reference=sign_product_reference_v2(
                    ProductReferenceV2(
                        schema_version="product-reference.v2",
                        channel3_product_id="keyboard-1",
                        offer_url="https://merchant.example/keyboard-1",
                        merchant_domain="merchant.example",
                        price_minor=8950,
                        currency="USD",
                        availability="InStock",
                        condition="new",
                        issued_at=now,
                        expires_at=expires_at,
                        canonical_category="electronics.keyboards",
                        registry_version="channel3-reviewed-2026.11",
                    ),
                    signing_key,
                ),
            ),
        ],
    )

    result_s2 = evaluate_v2_candidate(
        mandate=valid_mandate.model_dump(mode="json"),
        mandate_signature_b64=valid_mandate_sig,
        binding=valid_binding.model_dump(mode="json"),
        binding_signature_b64=valid_binding_sig,
        candidate=valid_candidate.model_dump(mode="json"),
        mandate_public_key_b64=pub_key_b64,
        binding_public_key_b64=pub_key_b64,
        now=now,
    )

    assert result_s2.verdict == "PASS"
    assert result_s2.payment_ready is True
    print(f"  -> Process B Verdict: {result_s2.verdict} (All {len(result_s2.checks)} checks passed)")

    consent_evidence = MandateConsentEvidence(
        consent_event_id=f"EV-SCENARIO2-{uuid4().hex[:8]}",
        mandate_id=valid_mandate.mandate_id,
        mandate_digest_b64=valid_digest,
        negotiation_id=str(neg_uuid),
        shopper_account_id="shopper_avery_singh",
        approved_cap_cents=45000,
        currency="USD",
        approved_constraints=["electronics.monitors", "electronics.keyboards"],
        approved_merchant_restrictions=["merchant.example"],
        consented_at=now,
        expires_at=now + timedelta(hours=2),
    )

    claimed = db_adapter.consent_store.record_and_claim(consent_evidence)
    assert claimed is True
    print("  -> Consent Claim: Single-use consent token atomically recorded and claimed")

    idemp_s2 = f"REQ-SCENARIO2-{uuid4().hex[:6]}"
    consumed = db_adapter.consume_mandate(
        mandate_id=valid_mandate.mandate_id,
        expected_digest=valid_digest,
        idempotency_key=idemp_s2,
        tx_id=f"TX-{idemp_s2}",
        paypal_order_id="ORD-SCENARIO2",
        raise_on_error=True,
    )
    assert consumed is True
    print(f"  -> Mandate Consumption: Mandate {valid_mandate.mandate_id} consumed in PostgreSQL")

    capture_id_s2 = f"2GG{uuid4().hex[:14].upper()}"
    db_adapter.record_payment_event(
        idempotency_key=idemp_s2,
        event_type="PAYMENT_CAPTURED",
        amount_cents=41850,
        currency_code="USD",
        paypal_order_id="ORD-SCENARIO2",
        paypal_authorization_id="AUTH-SCENARIO2",
        paypal_capture_id=capture_id_s2,
        status="COMPLETED",
        raw_response_summary={"intent": "CAPTURE", "id": capture_id_s2, "status": "COMPLETED"},
    )
    print(f"  -> Payment Event: Persisted PayPal capture {capture_id_s2} ($418.50) in payment_events")

    print("\n[Scenario 3] Replay Attack Prevention Test")
    replay_claimed = db_adapter.consent_store.record_and_claim(consent_evidence)
    assert replay_claimed is False
    print("  -> Consent Replay: ConsentRepository.record_and_claim returned False (REJECTED)")

    duplicate_consumption_blocked = False
    try:
        db_adapter.consume_mandate(
            mandate_id=valid_mandate.mandate_id,
            expected_digest=valid_digest,
            idempotency_key=f"REQ-REPLAY-{uuid4().hex[:6]}",
            tx_id=f"TX-REPLAY",
            paypal_order_id="ORD-SCENARIO2",
            raise_on_error=True,
        )
    except Exception as exc:
        duplicate_consumption_blocked = True
        print(f"  -> Mandate Replay: Duplicate consumption blocked with error: {type(exc).__name__}")

    assert duplicate_consumption_blocked is True
    print("  -> Second Capture: Refused. No secondary funds release possible.")

    print("\n" + "=" * 65)
    print("ALL THREE DEMO & SECURITY SCENARIOS VALIDATED SUCCESSFULLY.")
    print("=" * 65)

if __name__ == "__main__":
    run_scenarios()
