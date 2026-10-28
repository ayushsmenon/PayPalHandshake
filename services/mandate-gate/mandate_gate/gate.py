"""Deterministic candidate-deal verification."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from .crypto import verify_mandate_signature
from .models import CandidateDeal, SignedMandate, VerificationCheck, VerificationResult


def _check(name: str, verdict: bool, detail: str) -> VerificationCheck:
    return VerificationCheck(name=name, verdict="PASS" if verdict else "REJECT", detail=detail)


def _parse_mandate(value: SignedMandate | dict[str, Any]) -> SignedMandate:
    if isinstance(value, SignedMandate):
        return value
    return SignedMandate.model_validate(value)


def _parse_candidate(value: CandidateDeal | dict[str, Any]) -> CandidateDeal:
    if isinstance(value, CandidateDeal):
        return value
    return CandidateDeal.model_validate(value)


def evaluate_candidate(
    mandate: SignedMandate | dict[str, Any],
    signature_b64: str,
    candidate: CandidateDeal | dict[str, Any],
    public_key_b64: str,
    *,
    trusted_product_references_configured: bool = False,
    now: datetime | None = None,
) -> VerificationResult:
    checks: list[VerificationCheck] = []
    try:
        parsed_mandate = _parse_mandate(mandate)
    except ValidationError:
        return VerificationResult(
            verdict="REJECT",
            payment_ready=False,
            checks=[_check("mandate_shape", False, "Mandate is malformed.")],
        )

    signature_valid = verify_mandate_signature(
        parsed_mandate,
        signature_b64,
        public_key_b64,
    )
    checks.append(
        _check(
            "mandate_signature",
            signature_valid,
            "Ed25519 signature is valid."
            if signature_valid
            else "Ed25519 signature is invalid.",
        )
    )

    current_time = now or datetime.now(timezone.utc)
    not_expired = current_time < parsed_mandate.expires_at
    checks.append(
        _check(
            "mandate_expiry",
            not_expired,
            "Mandate is not expired."
            if not_expired
            else "Mandate has expired.",
        )
    )

    try:
        parsed_candidate = _parse_candidate(candidate)
    except ValidationError:
        checks.append(_check("candidate_shape", False, "Candidate deal is malformed."))
        return VerificationResult(verdict="REJECT", payment_ready=False, checks=checks)

    checks.extend(
        [
            _check(
                "negotiation_id",
                parsed_candidate.negotiation_id == parsed_mandate.negotiation_id,
                "Negotiation ID matches the mandate."
                if parsed_candidate.negotiation_id == parsed_mandate.negotiation_id
                else "Negotiation ID does not match the mandate.",
            ),
            _check(
                "paypal_auth_id",
                parsed_candidate.paypal_auth_id == parsed_mandate.paypal_auth_id,
                "PayPal authorization ID matches the mandate."
                if parsed_candidate.paypal_auth_id == parsed_mandate.paypal_auth_id
                else "PayPal authorization ID does not match the mandate.",
            ),
            _check(
                "currency",
                parsed_candidate.currency == parsed_mandate.currency,
                "Currency matches the mandate."
                if parsed_candidate.currency == parsed_mandate.currency
                else "Currency does not match the mandate.",
            ),
            _check(
                "spending_cap",
                parsed_candidate.total_cents <= parsed_mandate.authorized_cap_cents,
                "Total is within the authorized cap."
                if parsed_candidate.total_cents <= parsed_mandate.authorized_cap_cents
                else "Total exceeds the authorized cap.",
            ),
            _check(
                "merchant",
                parsed_candidate.merchant_id == parsed_mandate.authorized_merchant_id,
                "Merchant matches the mandate."
                if parsed_candidate.merchant_id == parsed_mandate.authorized_merchant_id
                else "Merchant does not match the mandate.",
            ),
        ]
    )

    candidate_ids = [product.product_identifier for product in parsed_candidate.products]
    allowed_ids = parsed_mandate.allowed_product_identifiers
    identifiers_match = sorted(candidate_ids) == sorted(allowed_ids)
    checks.append(
        _check(
            "product_identifiers",
            identifiers_match,
            "Product identifiers exactly match the mandate."
            if identifiers_match
            else "Product identifiers are missing or do not exactly match the mandate.",
        )
    )
    checks.append(
        _check(
            "trusted_product_references",
            trusted_product_references_configured,
            "Trusted product references are configured."
            if trusted_product_references_configured
            else "Trusted product references are not configured; identity is not proven.",
        )
    )

    passed = all(check.verdict == "PASS" for check in checks)
    return VerificationResult(
        verdict="PASS" if passed else "REJECT",
        payment_ready=passed and trusted_product_references_configured,
        checks=checks,
    )
