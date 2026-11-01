"""Server-controlled mandate-consent evidence and persistence interfaces."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from .versioned_models import MandateV2
from .versioned_crypto import mandate_v2_digest_b64


class MandateConsentEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    consent_event_id: str = Field(min_length=1)
    mandate_id: str = Field(min_length=1)
    mandate_digest_b64: str = Field(min_length=1)
    negotiation_id: str = Field(min_length=1)
    shopper_account_id: str = Field(min_length=1)
    approved_cap_cents: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    approved_constraints: list[str] = Field(min_length=1)
    approved_merchant_restrictions: list[str] = Field(min_length=1)
    consented_at: datetime
    expires_at: datetime


class ConsentStore(Protocol):
    """Durable store required for production uniqueness and consumption."""

    def record_and_claim(self, evidence: MandateConsentEvidence) -> bool:
        """Atomically record evidence and reject an already-consumed event."""
        ...


class ConsentEvidenceProvider(Protocol):
    """Authenticated server-side source of consent evidence."""

    def get_for_mandate(self, mandate: MandateV2) -> MandateConsentEvidence | None:
        ...


def consent_matches_mandate(
    evidence: MandateConsentEvidence,
    mandate: MandateV2,
    *,
    now: datetime,
) -> bool:
    return (
        evidence.mandate_id == mandate.mandate_id
        and evidence.mandate_digest_b64 == mandate_v2_digest_b64(mandate)
        and evidence.negotiation_id == mandate.negotiation_id
        and evidence.approved_cap_cents == mandate.authorized_cap_cents
        and evidence.currency == mandate.currency
        and evidence.approved_constraints == mandate.allowed_product_categories
        and evidence.approved_merchant_restrictions
        == mandate.merchant_restrictions
        and evidence.consent_event_id != ""
        and evidence.consented_at <= now < evidence.expires_at
    )
