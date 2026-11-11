"""Durable consent evidence repository matching ConsentStore Protocol."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from src.models import Mandate, ConsentEvidence
from src.repositories.base import (
    BaseRepository,
    NotFoundError,
    PersistenceError,
)


class ConsentRepository(BaseRepository):
    """Durable consent store adapter enforcing atomic single-use claims and zero synthetic fallbacks."""

    def record_and_claim(self, evidence: Any) -> bool:
        """Atomically record evidence and claim single-use consent for a mandate.

        Args:
            evidence: An instance of MandateConsentEvidence, ConsentEvidenceSchema,
                      or equivalent object/dict containing consent claims.

        Returns:
            True if consent was newly recorded and claimed.
            False if consent for this mandate or evidence_id was already claimed/replayed.

        Raises:
            ValueError: If evidence_id, mandate_id, or mandate_digest is missing, empty, or synthetic.
            NotFoundError: If the target mandate does not exist.
            PersistenceError: If the mandate has no authoritative digest or if digest mismatches.
            Exception: Any database connection/execution error propagates immediately (fails closed).
        """
        # 1. Extract fields supporting both protocol and model field naming
        if isinstance(evidence, dict):
            evidence_id = evidence.get("consent_event_id") or evidence.get("evidence_id")
            mandate_id = evidence.get("mandate_id")
            mandate_digest = evidence.get("mandate_digest_b64") or evidence.get("mandate_digest")
            negotiation_id_raw = evidence.get("negotiation_id")
            shopper_id = evidence.get("shopper_account_id") or evidence.get("shopper_id")
            approval_action = evidence.get("approval_action", "AUTHORIZE_AND_MANDATE")
            approved_at = evidence.get("consented_at") or evidence.get("approved_at")
            payload = dict(evidence)
        else:
            evidence_id = getattr(evidence, "consent_event_id", None) or getattr(evidence, "evidence_id", None)
            mandate_id = getattr(evidence, "mandate_id", None)
            mandate_digest = getattr(evidence, "mandate_digest_b64", None) or getattr(evidence, "mandate_digest", None)
            negotiation_id_raw = getattr(evidence, "negotiation_id", None)
            shopper_id = getattr(evidence, "shopper_account_id", None) or getattr(evidence, "shopper_id", None)
            approval_action = getattr(evidence, "approval_action", "AUTHORIZE_AND_MANDATE")
            approved_at = getattr(evidence, "consented_at", None) or getattr(evidence, "approved_at", None)
            payload = (
                evidence.model_dump(mode="json")
                if hasattr(evidence, "model_dump")
                else (vars(evidence) if hasattr(evidence, "__dict__") else {})
            )

        # 2. Strict Zero Synthetic / Fail Closed Validation
        if not evidence_id or not str(evidence_id).strip():
            raise ValueError("Missing or empty evidence_id: consent cannot be claimed without valid event identity")

        if not mandate_id or not str(mandate_id).strip():
            raise ValueError("Missing or empty mandate_id: consent cannot be claimed without target mandate identity")

        if not mandate_digest or not str(mandate_digest).strip():
            raise ValueError("Missing or empty mandate_digest: zero-tolerance for missing or synthetic digests")

        if str(mandate_digest).startswith("digest_"):
            raise ValueError("Synthetic fallback digest detected: strictly prohibited")

        if not shopper_id or not str(shopper_id).strip():
            raise ValueError("Missing or empty shopper_id: consent requires verifiable shopper account")

        if approved_at is None:
            approved_at = datetime.now(timezone.utc)
        elif isinstance(approved_at, str):
            approved_at = datetime.fromisoformat(approved_at)

        # Ensure negotiation_id is a UUID instance
        if isinstance(negotiation_id_raw, str):
            negotiation_id = UUID(negotiation_id_raw)
        elif isinstance(negotiation_id_raw, UUID):
            negotiation_id = negotiation_id_raw
        else:
            raise ValueError("negotiation_id must be a valid UUID or UUID string")

        # 3. Validate against target mandate before attempting claim (fail closed)
        stmt = select(Mandate).where(Mandate.mandate_id == mandate_id)
        mandate = self.session.scalar(stmt)
        if not mandate:
            raise NotFoundError(f"Mandate '{mandate_id}' not found; cannot claim consent")

        if not mandate.mandate_digest or not mandate.mandate_digest.strip():
            raise PersistenceError(f"Mandate '{mandate_id}' has no authoritative digest; failing closed")

        if mandate.mandate_digest.startswith("digest_"):
            raise PersistenceError(f"Mandate '{mandate_id}' contains an invalid synthetic digest; failing closed")

        if mandate.mandate_digest != mandate_digest:
            raise PersistenceError(
                f"Mandate digest mismatch for '{mandate_id}': expected '{mandate.mandate_digest}', got '{mandate_digest}'"
            )

        # 4. Atomic claim pattern using nested transaction (savepoint)
        # Guarantees caller-owned session is never committed or rolled back
        insert_stmt = text(
            """
            INSERT INTO consent_evidence (
                id, evidence_id, mandate_id, mandate_digest, negotiation_id,
                shopper_id, approval_action, evidence_payload, approved_at, created_at
            )
            VALUES (
                :id, :evidence_id, :mandate_id, :mandate_digest, :negotiation_id,
                :shopper_id, :approval_action, :evidence_payload, :approved_at, :created_at
            )
            ON CONFLICT (mandate_id) DO NOTHING
            RETURNING id;
            """
        )

        record_id = uuid4()
        now = datetime.now(timezone.utc)

        # Handle JSON serialization for payload
        import json
        payload_data = payload if isinstance(payload, dict) else {}
        # In SQLite, bind as JSON text; in Postgres, JSONB binds directly or as serialized text
        payload_bind = json.dumps(payload_data, default=str)

        params = {
            "id": str(record_id) if self.session.bind and self.session.bind.dialect.name == "sqlite" else record_id,
            "evidence_id": str(evidence_id),
            "mandate_id": str(mandate_id),
            "mandate_digest": str(mandate_digest),
            "negotiation_id": str(negotiation_id) if self.session.bind and self.session.bind.dialect.name == "sqlite" else negotiation_id,
            "shopper_id": str(shopper_id),
            "approval_action": str(approval_action),
            "evidence_payload": payload_bind,
            "approved_at": approved_at,
            "created_at": now,
        }

        try:
            with self.session.begin_nested():
                result = self.session.execute(insert_stmt, params)
                row = result.fetchone()
                if row is None:
                    # ON CONFLICT (mandate_id) DO NOTHING fired -> already claimed
                    return False
                return True
        except IntegrityError:
            # Replay of existing evidence_id or duplicate constraint caught cleanly by savepoint
            return False

    def get_consent_for_mandate(self, mandate_id: str) -> ConsentEvidence | None:
        """Retrieves claimed consent evidence for a given mandate ID."""
        stmt = select(ConsentEvidence).where(ConsentEvidence.mandate_id == mandate_id)
        return self.session.scalar(stmt)
