from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.models import (
    Mandate,
    Negotiation,
    ProductReference,
    VerificationEvent,
    PaymentEvent,
)
from src.repositories import (
    DatabaseSessionManager,
    NegotiationRepository,
    MandateRepository,
    ConsentRepository,
    AuditRepository,
    PersistenceError,
    NotFoundError,
    ReplayViolationError,
    MandateExpiredError,
    MandateAlreadyConsumedError,
)
from .consent import MandateConsentEvidence
from .versioned_crypto import mandate_v2_digest_b64
from .versioned_models import MandateV2
from .product_reference import SignedProductReferenceV2


class PostgresConsentStore:
    def __init__(self, session_manager: DatabaseSessionManager) -> None:
        self.session_manager = session_manager

    def record_and_claim(self, evidence: MandateConsentEvidence) -> bool:
        with self.session_manager.session_scope() as session:
            repo = ConsentRepository(session)
            return repo.record_and_claim(evidence)


class PaymentPolicyDbAdapter:
    def __init__(
        self,
        db_url: str | None = None,
        session_manager: DatabaseSessionManager | None = None,
    ) -> None:
        if session_manager is not None:
            self.session_manager = session_manager
        else:
            resolved_url = db_url or os.getenv("DATABASE_URL", "sqlite:///:memory:")
            self.session_manager = DatabaseSessionManager(resolved_url)
            self.session_manager.create_tables()
        self.consent_store = PostgresConsentStore(self.session_manager)

    def save_negotiation(
        self,
        shopper_id: str,
        request_prompt: str,
        budget_cents: int,
        currency_code: str = "USD",
        status: str = "OPEN",
        metadata: dict[str, Any] | None = None,
    ) -> Negotiation:
        with self.session_manager.session_scope() as session:
            repo = NegotiationRepository(session)
            neg = repo.create_negotiation(
                shopper_id=shopper_id,
                request_prompt=request_prompt,
                budget_cents=budget_cents,
                currency_code=currency_code,
                status=status,
                metadata=metadata,
            )
            return neg

    def save_mandate_v2(
        self,
        mandate: MandateV2,
        public_key: str,
        signature: str,
        nonce: str | None = None,
        mandate_payload: dict[str, Any] | None = None,
    ) -> Mandate:
        negotiation_uuid = UUID(mandate.negotiation_id)
        digest = mandate_v2_digest_b64(mandate)
        with self.session_manager.session_scope() as session:
            repo = MandateRepository(session)
            saved = repo.save_mandate(
                negotiation_id=negotiation_uuid,
                mandate_id=mandate.mandate_id,
                public_key=public_key,
                signature=signature,
                expires_at=mandate.expires_at,
                authorized_cap_cents=mandate.authorized_cap_cents,
                currency_code=mandate.currency,
                allowed_product_categories=mandate.allowed_product_categories,
                merchant_restrictions=mandate.merchant_restrictions,
                expected_paypal_payee_id=mandate.expected_paypal_payee_id,
                paypal_order_id=mandate.paypal_order_id,
                mandate_payload=mandate_payload or mandate.model_dump(mode="json"),
                nonce=nonce,
                mandate_digest=digest,
                schema_version=mandate.schema_version,
                status="ACTIVE",
                issued_at=mandate.issued_at,
            )
            return saved

    def get_mandate(self, mandate_id: str) -> Mandate | None:
        with self.session_manager.session_scope() as session:
            repo = MandateRepository(session)
            return repo.get_mandate_by_id(mandate_id)

    def consume_mandate(
        self,
        mandate_id: str,
        expected_digest: str,
        idempotency_key: str,
        tx_id: str = "",
        paypal_order_id: str | None = None,
        details: dict[str, Any] | None = None,
        raise_on_error: bool = False,
    ) -> bool:
        with self.session_manager.session_scope() as session:
            repo = MandateRepository(session)
            return repo.consume_mandate(
                mandate_id=mandate_id,
                expected_digest=expected_digest,
                idempotency_key=idempotency_key,
                tx_id=tx_id,
                paypal_order_id=paypal_order_id,
                details=details,
                raise_on_error=raise_on_error,
            )

    def record_verification_event(
        self,
        gate_name: str,
        decision: str,
        negotiation_id: UUID | None = None,
        passed_checks: list[Any] | None = None,
        blocked_checks: list[Any] | None = None,
        reason_details: str | None = None,
        manifest_digest: str | None = None,
    ) -> VerificationEvent:
        with self.session_manager.session_scope() as session:
            repo = AuditRepository(session)
            event = repo.record_verification_event(
                gate_name=gate_name,
                decision=decision,
                negotiation_id=negotiation_id,
                passed_checks=passed_checks,
                blocked_checks=blocked_checks,
                reason_details=reason_details,
                manifest_digest=manifest_digest,
            )
            return event

    def record_payment_event(
        self,
        idempotency_key: str,
        event_type: str,
        amount_cents: int,
        currency_code: str = "USD",
        negotiation_id: UUID | None = None,
        paypal_order_id: str | None = None,
        paypal_authorization_id: str | None = None,
        paypal_capture_id: str | None = None,
        status: str = "CREATED",
        raw_response_summary: dict[str, Any] | None = None,
    ) -> PaymentEvent:
        with self.session_manager.session_scope() as session:
            repo = AuditRepository(session)
            event = repo.record_payment_event(
                idempotency_key=idempotency_key,
                event_type=event_type,
                amount_cents=amount_cents,
                currency_code=currency_code,
                negotiation_id=negotiation_id,
                paypal_order_id=paypal_order_id,
                paypal_authorization_id=paypal_authorization_id,
                paypal_capture_id=paypal_capture_id,
                status=status,
                raw_response_summary=raw_response_summary,
            )
            return event

    def record_product_reference_v2(
        self,
        signed_ref: SignedProductReferenceV2,
        negotiation_id: UUID,
        digest: str,
        public_key: str = "",
        product_id: UUID | None = None,
    ) -> ProductReference:
        ref = signed_ref.reference
        with self.session_manager.session_scope() as session:
            repo = NegotiationRepository(session)
            pref = repo.add_product_reference(
                negotiation_id=negotiation_id,
                channel3_product_id=ref.channel3_product_id,
                offer_url=ref.offer_url,
                merchant_domain=ref.merchant_domain,
                price_cents=ref.price_minor,
                digest=digest,
                canonical_digest=digest,
                signature=signed_ref.signature_b64,
                signature_b64=signed_ref.signature_b64,
                public_key=public_key,
                public_key_b64=public_key,
                issued_at=ref.issued_at,
                expires_at=ref.expires_at,
                protocol_version="v2",
                schema_version=ref.schema_version,
                currency_code=ref.currency,
                availability=ref.availability,
                canonical_category=ref.canonical_category,
                registry_version=ref.registry_version,
                canonical_payload=ref.model_dump(mode="json"),
                product_id=product_id,
                condition=ref.condition,
            )
            return pref
