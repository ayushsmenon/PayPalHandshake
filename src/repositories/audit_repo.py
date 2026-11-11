"""Append-only audit repository for gate verification and PayPal payment events."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.models import VerificationEvent, PaymentEvent
from src.repositories.base import BaseRepository, ReplayViolationError


class AuditRepository(BaseRepository):
    """Append-only logging for deterministic security gate verification and PayPal payments."""

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
        """Appends an immutable gate verification record."""
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
        amount: Decimal,
        amount_cents: int | None = None,
        currency: str = "USD",
        negotiation_id: UUID | None = None,
        paypal_order_id: str | None = None,
        paypal_authorization_id: str | None = None,
        paypal_capture_id: str | None = None,
        paypal_payout_batch_id: str | None = None,
        status: str = "CREATED",
        raw_response_summary: dict[str, Any] | None = None,
    ) -> PaymentEvent:
        """Appends an immutable payment transaction event.

        Enforces replay protection via idempotency_key uniqueness.
        Raises ReplayViolationError if idempotency_key already exists.
        """
        calculated_cents = amount_cents if amount_cents is not None else int(amount * 100)
        event = PaymentEvent(
            negotiation_id=negotiation_id,
            idempotency_key=idempotency_key,
            paypal_order_id=paypal_order_id,
            paypal_authorization_id=paypal_authorization_id,
            paypal_capture_id=paypal_capture_id,
            paypal_payout_batch_id=paypal_payout_batch_id,
            event_type=event_type,
            amount=amount,
            amount_cents=calculated_cents,
            currency=currency.upper(),
            status=status,
            raw_response_summary=raw_response_summary or {},
        )

        try:
            self.session.add(event)
            self.session.flush()
            return event
        except IntegrityError as exc:
            self.session.rollback()
            raise ReplayViolationError(
                f"Duplicate payment request detected for idempotency_key '{idempotency_key}'"
            ) from exc

    def get_payment_event_by_idempotency_key(self, idempotency_key: str) -> PaymentEvent | None:
        """Retrieves a payment event by its unique idempotency key."""
        stmt = select(PaymentEvent).where(PaymentEvent.idempotency_key == idempotency_key)
        return self.session.scalar(stmt)

    def get_verification_events_for_negotiation(
        self, negotiation_id: UUID
    ) -> list[VerificationEvent]:
        """Returns all verification event records associated with a negotiation."""
        stmt = (
            select(VerificationEvent)
            .where(VerificationEvent.negotiation_id == negotiation_id)
            .order_by(VerificationEvent.created_at.asc())
        )
        return list(self.session.scalars(stmt).all())
