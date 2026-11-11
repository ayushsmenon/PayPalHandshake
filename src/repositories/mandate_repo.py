"""Repository for mandate.v2 storage and replay-safe atomic consumption."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.models import Mandate, MandateConsumption
from src.repositories.base import (
    BaseRepository,
    NotFoundError,
    MandateAlreadyConsumedError,
    MandateExpiredError,
    PersistenceError,
    ReplayViolationError,
)


class MandateRepository(BaseRepository):
    """Storage, validation, and atomic consumption of mandate.v2 agreements."""

    def save_mandate(
        self,
        negotiation_id: UUID,
        mandate_id: str,
        public_key: str,
        signature: str,
        expires_at: datetime,
        authorized_cap_cents: int = 0,
        allowed_product_categories: list[str] | None = None,
        merchant_restrictions: list[str] | None = None,
        expected_paypal_payee_id: str | None = None,
        paypal_order_id: str | None = None,
        mandate_payload: dict[str, Any] | None = None,
        nonce: str | None = None,
        mandate_digest: str | None = None,
        schema_version: str = "mandate.v2",
        status: str = "ACTIVE",
        issued_at: datetime | None = None,
    ) -> Mandate:
        """Stores a mandate conforming to the mandate.v2 protocol."""
        # Ensure UTC timezone awareness if not provided
        now = datetime.now(timezone.utc)
        issue_time = issued_at or now

        mandate = Mandate(
            negotiation_id=negotiation_id,
            mandate_id=mandate_id,
            schema_version=schema_version,
            paypal_order_id=paypal_order_id,
            authorized_cap_cents=authorized_cap_cents,
            allowed_product_categories=allowed_product_categories or [],
            merchant_restrictions=merchant_restrictions or [],
            expected_paypal_payee_id=expected_paypal_payee_id,
            public_key=public_key,
            signature=signature,
            mandate_payload=mandate_payload or {},
            nonce=nonce,
            mandate_digest=mandate_digest,
            status=status,
            issued_at=issue_time,
            expires_at=expires_at,
        )
        try:
            self.session.add(mandate)
            self.session.flush()
            return mandate
        except IntegrityError as exc:
            self.session.rollback()
            raise ReplayViolationError(
                f"Mandate with id '{mandate_id}' or digest '{mandate_digest}' already exists"
            ) from exc

    def get_mandate_by_id(self, mandate_id: str) -> Mandate | None:
        """Retrieves a mandate by its protocol string ID (e.g. 'mnd_123')."""
        stmt = select(Mandate).where(Mandate.mandate_id == mandate_id)
        return self.session.scalar(stmt)

    def get_mandate_by_uuid(self, id: UUID) -> Mandate | None:
        """Retrieves a mandate by its internal database UUID."""
        stmt = select(Mandate).where(Mandate.id == id)
        return self.session.scalar(stmt)

    def consume_mandate(
        self,
        mandate_id: str,
        idempotency_key: str,
        consumption_tx_id: str,
        paypal_order_id: str | None = None,
        payment_event_id: UUID | None = None,
        details: dict[str, Any] | None = None,
    ) -> MandateConsumption:
        """Atomically checks and consumes an active mandate with replay prevention.

        Raises:
            NotFoundError: If the mandate cannot be found.
            MandateAlreadyConsumedError: If the mandate has already been consumed.
            MandateExpiredError: If the mandate is expired.
            PersistenceError: If the mandate status is not ACTIVE.
            ReplayViolationError: If idempotency_key or mandate_digest has already been recorded.
        """
        # Fetch mandate with row-level lock if supported by the dialect
        stmt = select(Mandate).where(Mandate.mandate_id == mandate_id)
        if self.session.bind and self.session.bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update()

        mandate = self.session.scalar(stmt)
        if not mandate:
            raise NotFoundError(f"Mandate '{mandate_id}' not found")

        # 1. Check if already consumed
        if mandate.status == "CONSUMED" or mandate.consumed_at is not None:
            raise MandateAlreadyConsumedError(
                f"Mandate '{mandate_id}' has already been consumed at {mandate.consumed_at}"
            )

        # 2. Check if expired
        now = datetime.now(timezone.utc)
        # Normalize timezone for comparison if needed
        expiry = mandate.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)

        if now >= expiry:
            raise MandateExpiredError(
                f"Mandate '{mandate_id}' expired at {mandate.expires_at} (current time: {now})"
            )

        # 3. Check status is ACTIVE
        if mandate.status != "ACTIVE":
            raise PersistenceError(
                f"Cannot consume mandate '{mandate_id}' with status '{mandate.status}'"
            )

        # 4. Use or generate mandate digest
        digest = mandate.mandate_digest or f"digest_{mandate.mandate_id}"

        # 5. Mark mandate as consumed
        mandate.status = "CONSUMED"
        mandate.consumed_at = now
        mandate.consumption_tx_id = consumption_tx_id

        # 6. Record durable consumption audit record
        consumption = MandateConsumption(
            mandate_id=mandate.id,
            mandate_digest=digest,
            consumed_at=now,
            idempotency_key=idempotency_key,
            paypal_order_id=paypal_order_id or mandate.paypal_order_id,
            payment_event_id=payment_event_id,
            details=details or {},
        )

        try:
            self.session.add(consumption)
            self.session.flush()
            return consumption
        except IntegrityError as exc:
            self.session.rollback()
            raise ReplayViolationError(
                f"Replay detected: Mandate digest '{digest}' or idempotency key '{idempotency_key}' already consumed"
            ) from exc
