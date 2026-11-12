from __future__ import annotations

import base64
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PAYMENT_POLICY_PATH = REPO_ROOT / "services" / "payment-policy-svc"
if str(PAYMENT_POLICY_PATH) not in sys.path:
    sys.path.insert(0, str(PAYMENT_POLICY_PATH))

MANDATE_GATE_PATH = REPO_ROOT / "services" / "mandate-gate"
if str(MANDATE_GATE_PATH) not in sys.path:
    sys.path.insert(0, str(MANDATE_GATE_PATH))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mandate_gate.versioned_gate import evaluate_v2_candidate
from payment_policy.consent import MandateConsentEvidence
from payment_policy.db import PaymentPolicyDbAdapter
from payment_policy.product_reference import (
    ProductReferenceV2,
    SignedProductReferenceV2,
    canonical_product_reference_v2_bytes,
    sign_product_reference_v2,
)
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
from src.repositories import DatabaseSessionManager, AuditRepository, NotFoundError
from .schemas import (
    AuditTrailItem,
    AuditTrailResponse,
    DealApprovalCaptureRequest,
    DealApprovalCaptureResponse,
    DealEvidenceProduct,
    DealVerificationResponse,
    VerificationCheckDetail,
)

DEMO_NEGOTIATION_UUID = UUID("00000000-0000-0000-0000-000000002048")
DEMO_PAYPAL_ORDER_ID = "ORD-HS2048-SANDBOX"
DEMO_PAYPAL_AUTH_ID = "AUTH-HS2048-SANDBOX"
DEMO_MERCHANT = "merchant.example"


class DealFlowManager:
    def __init__(self, db_adapter: PaymentPolicyDbAdapter | None = None) -> None:
        self.db_adapter = db_adapter or PaymentPolicyDbAdapter()
        self.signing_key = Ed25519PrivateKey.generate()
        self.public_key_b64 = base64.b64encode(
            self.signing_key.public_key().public_bytes_raw()
        ).decode("ascii")

    def run_deal_verification(self, negotiation_id_str: str) -> DealVerificationResponse:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=4)

        neg_uuid = DEMO_NEGOTIATION_UUID
        try:
            with self.db_adapter.session_manager.session_scope() as session:
                from src.repositories import NegotiationRepository
                from src.models import Negotiation

                repo = NegotiationRepository(session)
                existing = repo.get_negotiation(neg_uuid)
                if not existing:
                    neg = Negotiation(
                        id=neg_uuid,
                        shopper_id="shopper_avery_singh",
                        request_prompt="27-inch 4K monitor + mechanical keyboard",
                        budget_cents=45000,
                        currency_code="USD",
                        status="OPEN",
                    )
                    session.add(neg)
        except Exception:
            pass

        mandate = MandateV2(
            schema_version="mandate.v2",
            mandate_id=f"mnd_{negotiation_id_str.lower()}",
            negotiation_id=str(neg_uuid),
            paypal_order_id=DEMO_PAYPAL_ORDER_ID,
            authorized_cap_cents=45000,
            currency="USD",
            allowed_product_categories=["electronics.monitors", "electronics.keyboards"],
            merchant_restrictions=[DEMO_MERCHANT],
            issued_at=now,
            expires_at=expires_at,
        )
        mandate_sig = sign_mandate_v2(mandate, self.signing_key)
        digest = mandate_v2_digest_b64(mandate)

        try:
            self.db_adapter.save_mandate_v2(
                mandate=mandate,
                public_key=self.public_key_b64,
                signature=mandate_sig,
                nonce="nonce_hs2048",
            )
        except Exception:
            pass

        ref_monitor = ProductReferenceV2(
            schema_version="product-reference.v2",
            channel3_product_id="monitor-1",
            offer_url="https://merchant.example/monitor-1",
            merchant_domain=DEMO_MERCHANT,
            price_minor=32900,
            currency="USD",
            availability="InStock",
            condition="new",
            issued_at=now,
            expires_at=expires_at,
            canonical_category="electronics.monitors",
            registry_version="channel3-reviewed-2026.11",
        )
        signed_monitor = sign_product_reference_v2(ref_monitor, self.signing_key)

        ref_keyboard = ProductReferenceV2(
            schema_version="product-reference.v2",
            channel3_product_id="keyboard-1",
            offer_url="https://merchant.example/keyboard-1",
            merchant_domain=DEMO_MERCHANT,
            price_minor=8950,
            currency="USD",
            availability="InStock",
            condition="new",
            issued_at=now,
            expires_at=expires_at,
            canonical_category="electronics.keyboards",
            registry_version="channel3-reviewed-2026.11",
        )
        signed_keyboard = sign_product_reference_v2(ref_keyboard, self.signing_key)

        binding = PostAuthorizationBindingV1(
            schema_version="post-authorization-binding.v1",
            binding_id=f"bind_{negotiation_id_str.lower()}",
            mandate_id=mandate.mandate_id,
            mandate_digest_b64=digest,
            paypal_order_id=DEMO_PAYPAL_ORDER_ID,
            paypal_auth_id=DEMO_PAYPAL_AUTH_ID,
            authorization_status="AUTHORIZED",
            authorized_amount_cents=45000,
            currency="USD",
            issued_at=now,
            expires_at=expires_at,
        )
        binding_sig = sign_post_authorization_binding_v1(binding, self.signing_key)

        candidate = CandidateDealV2(
            negotiation_id=str(neg_uuid),
            paypal_order_id=DEMO_PAYPAL_ORDER_ID,
            merchant_id=DEMO_MERCHANT,
            total_cents=41850,
            currency="USD",
            products=[
                ProductLineV2(
                    product_identifier="monitor-1",
                    quantity=1,
                    description="27-inch 4K monitor",
                    unit_price_cents=32900,
                    offer_url="https://merchant.example/monitor-1",
                    product_reference=signed_monitor,
                ),
                ProductLineV2(
                    product_identifier="keyboard-1",
                    quantity=1,
                    description="Mechanical keyboard",
                    unit_price_cents=8950,
                    offer_url="https://merchant.example/keyboard-1",
                    product_reference=signed_keyboard,
                ),
            ],
        )

        verification_result = evaluate_v2_candidate(
            mandate=mandate.model_dump(mode="json"),
            mandate_signature_b64=mandate_sig,
            binding=binding.model_dump(mode="json"),
            binding_signature_b64=binding_sig,
            candidate=candidate.model_dump(mode="json"),
            mandate_public_key_b64=self.public_key_b64,
            binding_public_key_b64=self.public_key_b64,
            now=now,
        )

        passed_check_names = [c.name for c in verification_result.checks if c.verdict == "PASS"]
        blocked_check_names = [c.name for c in verification_result.checks if c.verdict != "PASS"]

        self.db_adapter.record_verification_event(
            gate_name="MANDATE_GATE_V2",
            decision="PASSED" if verification_result.payment_ready else "BLOCKED",
            passed_checks=passed_check_names,
            blocked_checks=blocked_check_names,
            reason_details="Process B v2 deterministic evaluation complete.",
            manifest_digest=digest,
        )

        checks_out = [
            VerificationCheckDetail(
                name=c.name,
                verdict=c.verdict,
                detail=c.detail,
            )
            for c in verification_result.checks
        ]

        products_out = [
            DealEvidenceProduct(
                name="27-inch 4K monitor",
                product_identifier="monitor-1",
                price_cents=32900,
                canonical_category="electronics.monitors",
                verified=True,
                offer_url="https://merchant.example/monitor-1",
                merchant_domain=DEMO_MERCHANT,
            ),
            DealEvidenceProduct(
                name="Mechanical keyboard",
                product_identifier="keyboard-1",
                price_cents=8950,
                canonical_category="electronics.keyboards",
                verified=True,
                offer_url="https://merchant.example/keyboard-1",
                merchant_domain=DEMO_MERCHANT,
            ),
        ]

        return DealVerificationResponse(
            verdict=verification_result.verdict,
            payment_ready=verification_result.payment_ready,
            checks=checks_out,
            mandate_id=mandate.mandate_id,
            mandate_digest_b64=digest,
            authorized_cap_cents=45000,
            paypal_order_id=DEMO_PAYPAL_ORDER_ID,
            paypal_auth_id=DEMO_PAYPAL_AUTH_ID,
            products=products_out,
        )

    def approve_and_capture_deal(
        self,
        request: DealApprovalCaptureRequest,
    ) -> DealApprovalCaptureResponse:
        if request.confirmed_amount_cents != 41850:
            raise ValueError(
                f"Confirmed amount {request.confirmed_amount_cents} does not match verified total 41850 cents."
            )
        if request.confirmed_merchant != DEMO_MERCHANT:
            raise ValueError(f"Merchant mismatch: expected {DEMO_MERCHANT}")

        mandate_id = request.mandate_id
        db_mandate = self.db_adapter.get_mandate(mandate_id)
        if not db_mandate:
            raise NotFoundError(f"Mandate {mandate_id} not found in database.")

        digest = db_mandate.mandate_digest
        now = datetime.now(timezone.utc)

        evidence = MandateConsentEvidence(
            consent_event_id=f"EV-APPROVAL-{uuid4().hex[:8]}",
            mandate_id=mandate_id,
            mandate_digest_b64=digest,
            negotiation_id=str(db_mandate.negotiation_id),
            shopper_account_id=request.shopper_id,
            approved_cap_cents=db_mandate.authorized_cap_cents,
            currency="USD",
            approved_constraints=list(db_mandate.allowed_product_categories or ["electronics.monitors"]),
            approved_merchant_restrictions=list(db_mandate.merchant_restrictions or [DEMO_MERCHANT]),
            consented_at=now,
            expires_at=now + timedelta(hours=2),
        )

        consent_claimed = self.db_adapter.consent_store.record_and_claim(evidence)
        if not consent_claimed:
            raise ValueError("Shopper consent has already been claimed or replayed.")

        idempotency_key = f"REQ-CAPTURE-{mandate_id}-{uuid4().hex[:6]}"
        self.db_adapter.consume_mandate(
            mandate_id=mandate_id,
            expected_digest=digest,
            idempotency_key=idempotency_key,
            tx_id=f"TX-{idempotency_key}",
            paypal_order_id=DEMO_PAYPAL_ORDER_ID,
            raise_on_error=True,
        )

        capture_id = f"CAPTURE-HS2048-{uuid4().hex[:8].upper()}"
        self.db_adapter.record_payment_event(
            idempotency_key=idempotency_key,
            event_type="PAYMENT_CAPTURED",
            amount_cents=41850,
            currency_code="USD",
            paypal_order_id=DEMO_PAYPAL_ORDER_ID,
            paypal_authorization_id=DEMO_PAYPAL_AUTH_ID,
            paypal_capture_id=capture_id,
            status="COMPLETED",
            raw_response_summary={"intent": "CAPTURE", "capture_id": capture_id},
        )

        return DealApprovalCaptureResponse(
            status="COMPLETED",
            paypal_order_id=DEMO_PAYPAL_ORDER_ID,
            paypal_authorization_id=DEMO_PAYPAL_AUTH_ID,
            paypal_capture_id=capture_id,
            captured_amount_cents=41850,
            currency="USD",
            mandate_id=mandate_id,
            mandate_digest_b64=digest,
            timestamp=now.isoformat(),
        )

    def get_audit_trail(self, negotiation_id_str: str) -> AuditTrailResponse:
        with self.db_adapter.session_manager.session_scope() as session:
            from sqlalchemy import select
            from src.models import VerificationEvent, PaymentEvent

            v_stmt = select(VerificationEvent).order_by(VerificationEvent.created_at.desc()).limit(15)
            v_events = list(session.scalars(v_stmt).all())

            p_stmt = select(PaymentEvent).order_by(PaymentEvent.created_at.desc()).limit(15)
            p_events = list(session.scalars(p_stmt).all())

            items: list[AuditTrailItem] = []
            for ve in v_events:
                items.append(
                    AuditTrailItem(
                        event_id=str(ve.id),
                        event_type="VERIFICATION",
                        name_or_gate=ve.gate_name,
                        decision_or_status=ve.decision,
                        details=ve.reason_details,
                        created_at=ve.created_at.isoformat() if ve.created_at else "",
                    )
                )
            for pe in p_events:
                items.append(
                    AuditTrailItem(
                        event_id=str(pe.id),
                        event_type="PAYMENT",
                        name_or_gate=pe.event_type,
                        decision_or_status=pe.status,
                        amount_cents=pe.amount_cents,
                        details=f"PayPal Order: {pe.paypal_order_id or 'none'}, Auth: {pe.paypal_authorization_id or 'none'}, Capture: {pe.paypal_capture_id or 'none'}",
                        created_at=pe.created_at.isoformat() if pe.created_at else "",
                    )
                )

            return AuditTrailResponse(
                negotiation_id=negotiation_id_str,
                events=items,
            )
