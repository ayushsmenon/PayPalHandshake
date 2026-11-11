from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from src.models import Mandate, MandateConsumption
from src.repositories.base import (
    BaseRepository,
    NotFoundError,
    PersistenceError,
    ReplayViolationError,
    MandateExpiredError,
    MandateAlreadyConsumedError,
)


class MandateRepository(BaseRepository):
    def save_mandate(
        self,
        negotiation_id: UUID,
        mandate_id: str,
        public_key: str,
        signature: str,
        expires_at: datetime,
        authorized_cap_cents: int = 0,
        currency_code: str = "USD",
        currency: str = "USD",
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
        if mandate_digest is not None:
            if not str(mandate_digest).strip():
                raise ValueError("Empty mandate_digest: zero-tolerance for empty digests")
            if str(mandate_digest).startswith("digest_"):
                raise ValueError("Synthetic fallback digest detected: strictly prohibited")

        now = datetime.now(timezone.utc)
        issue_time = issued_at or now

        curr = (currency_code or currency).upper()

        mandate = Mandate(
            negotiation_id=negotiation_id,
            mandate_id=mandate_id,
            schema_version=schema_version,
            paypal_order_id=paypal_order_id,
            authorized_cap_cents=authorized_cap_cents,
            currency_code=curr,
            currency=curr,
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
            with self.session.begin_nested():
                self.session.add(mandate)
                self.session.flush()
                return mandate
        except IntegrityError as exc:
            raise ReplayViolationError(
                f"Mandate with id '{mandate_id}' or digest '{mandate_digest}' already exists"
            ) from exc

    def get_mandate_by_id(self, mandate_id: str) -> Mandate | None:
        stmt = select(Mandate).where(Mandate.mandate_id == mandate_id)
        return self.session.scalar(stmt)

    def get_mandate_by_uuid(self, id: UUID) -> Mandate | None:
        stmt = select(Mandate).where(Mandate.id == id)
        return self.session.scalar(stmt)

    def consume_mandate(
        self,
        mandate_id: str,
        expected_digest: str,
        idempotency_key: str,
        tx_id: str = "",
        consumption_tx_id: str = "",
        paypal_order_id: str | None = None,
        payment_event_id: UUID | None = None,
        details: dict[str, Any] | None = None,
        raise_on_error: bool = False,
    ) -> bool:
        transaction_id = tx_id or consumption_tx_id or idempotency_key

        if not expected_digest or not str(expected_digest).strip():
            raise ValueError("Missing or empty expected_digest: synthetic fallbacks are strictly prohibited")

        if str(expected_digest).startswith("digest_"):
            raise ValueError("Synthetic fallback digest detected: strictly prohibited")

        if not mandate_id or not str(mandate_id).strip():
            raise ValueError("Missing or empty mandate_id")

        if not idempotency_key or not str(idempotency_key).strip():
            raise ValueError("Missing or empty idempotency_key")

        if not transaction_id or not str(transaction_id).strip():
            raise ValueError("Missing or empty tx_id")

        mandate = self.get_mandate_by_id(mandate_id)
        if not mandate:
            raise NotFoundError(f"Mandate '{mandate_id}' not found; cannot consume")

        if not mandate.mandate_digest or not mandate.mandate_digest.strip():
            raise PersistenceError(f"Mandate '{mandate_id}' has no authoritative digest; failing closed")

        if mandate.mandate_digest.startswith("digest_"):
            raise PersistenceError(f"Mandate '{mandate_id}' has invalid synthetic digest; failing closed")

        if mandate.mandate_digest != expected_digest:
            raise PersistenceError(
                f"Mandate digest mismatch for '{mandate_id}': expected '{mandate.mandate_digest}', got '{expected_digest}'"
            )

        now = datetime.now(timezone.utc)

        expiry = mandate.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)

        if now >= expiry:
            if raise_on_error:
                raise MandateExpiredError(f"Mandate '{mandate_id}' expired at {mandate.expires_at}")
            return False

        if mandate.status == "CONSUMED" or mandate.consumed_at is not None:
            if raise_on_error:
                raise MandateAlreadyConsumedError(f"Mandate '{mandate_id}' has already been consumed")
            return False

        if mandate.status != "ACTIVE":
            if raise_on_error:
                raise PersistenceError(f"Cannot consume mandate '{mandate_id}' with status '{mandate.status}'")
            return False

        update_stmt = text(
            """
            UPDATE mandates
            SET consumed_at = :now, status = 'CONSUMED', consumption_tx_id = :tx_id
            WHERE mandate_id = :mandate_id
              AND mandate_digest = :expected_digest
              AND status = 'ACTIVE'
              AND consumed_at IS NULL
              AND expires_at > :now
            RETURNING mandate_id;
            """
        )

        insert_consumption_stmt = text(
            """
            INSERT INTO mandate_consumptions (
                id, mandate_id, mandate_digest, consumed_at, idempotency_key, transaction_id, paypal_order_id, details
            )
            VALUES (
                :id, :mandate_id, :mandate_digest, :consumed_at, :idempotency_key, :transaction_id, :paypal_order_id, :details
            );
            """
        )

        consumption_id = uuid4()
        details_bind = json.dumps(details or {}, default=str)

        try:
            with self.session.begin_nested():
                result = self.session.execute(
                    update_stmt,
                    {
                        "mandate_id": mandate_id,
                        "expected_digest": expected_digest,
                        "tx_id": transaction_id,
                        "now": now,
                    },
                )
                row = result.fetchone()
                if row is None:
                    if raise_on_error:
                        raise MandateAlreadyConsumedError(f"Mandate '{mandate_id}' could not be transitioned")
                    return False

                self.session.execute(
                    insert_consumption_stmt,
                    {
                        "id": str(consumption_id) if self.session.bind and self.session.bind.dialect.name == "sqlite" else consumption_id,
                        "mandate_id": mandate_id,
                        "mandate_digest": expected_digest,
                        "consumed_at": now,
                        "idempotency_key": idempotency_key,
                        "transaction_id": transaction_id,
                        "paypal_order_id": paypal_order_id or mandate.paypal_order_id,
                        "details": details_bind,
                    },
                )

                mandate.status = "CONSUMED"
                mandate.consumed_at = now
                mandate.consumption_tx_id = transaction_id
                return True
        except IntegrityError as exc:
            if raise_on_error:
                raise ReplayViolationError(f"Duplicate consumption for idempotency_key '{idempotency_key}'") from exc
            return False
