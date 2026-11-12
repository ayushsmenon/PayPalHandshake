from __future__ import annotations

from typing import Any
from uuid import UUID

from .consent import MandateConsentEvidence
from .db import PaymentPolicyDbAdapter
from .paypal_client import PayPalSandboxClient, PayPalClientError
from .process_b_client import ProcessBClient, ProcessBVerificationError, ProcessBResult
from .versioned_crypto import mandate_v2_digest_b64
from .versioned_models import CandidateDealV2, MandateV2, PostAuthorizationBindingV1
from src.repositories import (
    NotFoundError,
    PersistenceError,
    ReplayViolationError,
    MandateExpiredError,
    MandateAlreadyConsumedError,
)


class PaymentOrchestrationError(RuntimeError):
    pass


class PaymentExecutionOrchestrator:
    def __init__(
        self,
        db_adapter: PaymentPolicyDbAdapter,
        process_b_client: ProcessBClient,
        paypal_client: PayPalSandboxClient,
    ) -> None:
        self.db_adapter = db_adapter
        self.process_b_client = process_b_client
        self.paypal_client = paypal_client

    def verify_and_capture_deal(
        self,
        mandate: MandateV2,
        mandate_signature_b64: str,
        binding: PostAuthorizationBindingV1,
        binding_signature_b64: str,
        candidate: CandidateDealV2,
        consent_evidence: MandateConsentEvidence,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not idempotency_key or not idempotency_key.strip():
            raise ValueError("Non-empty idempotency_key is required for payment capture.")

        digest = mandate_v2_digest_b64(mandate)

        if consent_evidence.mandate_id != mandate.mandate_id:
            raise ValueError("Consent evidence does not match mandate ID.")

        if consent_evidence.mandate_digest_b64 != digest:
            raise ValueError("Consent evidence does not match mandate digest.")

        negotiation_uuid = None
        try:
            negotiation_uuid = UUID(mandate.negotiation_id)
        except ValueError:
            pass

        process_b_payload = {
            "mandate": mandate.model_dump(mode="json"),
            "mandate_signature": mandate_signature_b64,
            "post_authorization_binding": binding.model_dump(mode="json"),
            "binding_signature": binding_signature_b64,
            "candidate": candidate.model_dump(mode="json"),
        }

        try:
            process_b_result: ProcessBResult = (
                self.process_b_client.verify_authorization_binding(process_b_payload)
            )
        except ProcessBVerificationError as exc:
            self.db_adapter.record_verification_event(
                gate_name="PROCESS_B_V2_GATE",
                decision="BLOCKED",
                negotiation_id=negotiation_uuid,
                passed_checks=[],
                blocked_checks=["PROCESS_B_VERIFICATION_REJECTED"],
                reason_details=str(exc),
                manifest_digest=digest,
            )
            raise PaymentOrchestrationError(
                f"Process B verification gate rejected deal: {exc}"
            ) from exc

        passed_checks = [c.name for c in process_b_result.checks if c.verdict == "PASS"]
        blocked_checks = [c.name for c in process_b_result.checks if c.verdict != "PASS"]

        if process_b_result.verdict != "PASS" or not process_b_result.payment_ready:
            self.db_adapter.record_verification_event(
                gate_name="PROCESS_B_V2_GATE",
                decision="BLOCKED",
                negotiation_id=negotiation_uuid,
                passed_checks=passed_checks,
                blocked_checks=blocked_checks or ["PAYMENT_NOT_READY"],
                reason_details="Process B returned non-PASS or payment_ready=False.",
                manifest_digest=digest,
            )
            raise PaymentOrchestrationError("Deal rejected by Process B verification gate.")

        self.db_adapter.record_verification_event(
            gate_name="PROCESS_B_V2_GATE",
            decision="PASSED",
            negotiation_id=negotiation_uuid,
            passed_checks=passed_checks,
            blocked_checks=[],
            reason_details="All 14 Process B verification checks passed.",
            manifest_digest=digest,
        )

        consent_claimed = self.db_adapter.consent_store.record_and_claim(consent_evidence)
        if not consent_claimed:
            raise PaymentOrchestrationError(
                f"Shopper consent for mandate '{mandate.mandate_id}' was already claimed or replayed."
            )

        try:
            self.db_adapter.consume_mandate(
                mandate_id=mandate.mandate_id,
                expected_digest=digest,
                idempotency_key=idempotency_key,
                tx_id=f"TX-{idempotency_key}",
                paypal_order_id=mandate.paypal_order_id,
                raise_on_error=True,
            )
        except (MandateAlreadyConsumedError, MandateExpiredError, PersistenceError) as exc:
            raise PaymentOrchestrationError(f"Mandate consumption failed: {exc}") from exc

        capture_result = self.paypal_client.capture_authorization(
            authorization_id=binding.paypal_auth_id,
            amount_cents=candidate.total_cents,
            currency=candidate.currency,
            idempotency_key=idempotency_key,
            final_capture=True,
            negotiation_id=negotiation_uuid,
        )

        return {
            "status": "COMPLETED",
            "capture_id": capture_result.get("id"),
            "amount_cents": candidate.total_cents,
            "currency": candidate.currency,
            "paypal_order_id": mandate.paypal_order_id,
            "paypal_authorization_id": binding.paypal_auth_id,
            "mandate_id": mandate.mandate_id,
            "mandate_digest_b64": digest,
            "verification_passed": True,
            "mandate_consumed": True,
            "raw_capture_summary": capture_result,
        }
