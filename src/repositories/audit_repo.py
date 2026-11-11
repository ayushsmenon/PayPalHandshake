from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.models import VerificationEvent, PaymentEvent
from src.repositories.base import BaseRepository, ReplayViolationError


class AuditRepository(BaseRepository):
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
        event = VerificationEvent(
            negotiation_id=negotiation_id,
            gate_name=gate_name,
            decision=decision.upper(),
            passed_checks=passed_checks or [],
            blocked_checks=blocked_checks or [],
            reason_details=reason_details,
            manifest_digest=manifest_digest,
        )
        self.session.add(event)
        self.session.flush()
        return event

    def record_payment_event(
        self,
        idempotency_key: str,
        event_type: str,
        amount_cents: int,
        currency_code: str = "USD",
        currency: str = "USD",
        amount: Decimal | None = None,
        negotiation_id: UUID | None = None,
        paypal_order_id: str | None = None,
        paypal_authorization_id: str | None = None,
        paypal_capture_id: str | None = None,
        paypal_payout_batch_id: str | None = None,
        status: str = "CREATED",
        raw_response_summary: dict[str, Any] | None = None,
    ) -> PaymentEvent:
        curr = (currency_code or currency).upper()
        decimal_amount = amount if amount is not None else Decimal(amount_cents) / Decimal(100)

        event = PaymentEvent(
            negotiation_id=negotiation_id,
            idempotency_key=idempotency_key,
            paypal_order_id=paypal_order_id,
            paypal_authorization_id=paypal_authorization_id,
            paypal_capture_id=paypal_capture_id,
            paypal_payout_batch_id=paypal_payout_batch_id,
            event_type=event_type,
            amount_cents=amount_cents,
            amount=decimal_amount,
            currency_code=curr,
            currency=curr,
            status=status,
            raw_response_summary=raw_response_summary or {},
        )

        try:
            with self.session.begin_nested():
                self.session.add(event)
                self.session.flush()
                return event
        except IntegrityError as exc:
            raise ReplayViolationError(
                f"Duplicate payment request detected for idempotency_key '{idempotency_key}'"
            ) from exc

    def get_payment_event_by_idempotency_key(self, idempotency_key: str) -> PaymentEvent | None:
        stmt = select(PaymentEvent).where(PaymentEvent.idempotency_key == idempotency_key)
        return self.session.scalar(stmt)

    def get_verification_events_for_negotiation(
        self, negotiation_id: UUID
    ) -> list[VerificationEvent]:
        stmt = (
            select(VerificationEvent)
            .where(VerificationEvent.negotiation_id == negotiation_id)
            .order_by(VerificationEvent.created_at.asc())
        )
        return list(self.session.scalars(stmt).all())
