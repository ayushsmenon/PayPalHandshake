"""Deterministic verification for the versioned payment binding."""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from .models import VerificationCheck, VerificationResult
from .product_reference import verify_product_reference_signature
from .versioned_crypto import verify_versioned_signature
from .versioned_models import CandidateDealV2, MandateV2, PostAuthorizationBindingV1


def _check(name: str, verdict: bool, detail: str) -> VerificationCheck:
    return VerificationCheck(name=name, verdict="PASS" if verdict else "REJECT", detail=detail)


def mandate_v2_digest_b64(mandate: MandateV2) -> str:
    from .versioned_crypto import canonical_mandate_v2_bytes

    return base64.b64encode(
        hashlib.sha256(canonical_mandate_v2_bytes(mandate)).digest()
    ).decode("ascii")


def evaluate_v2_candidate(
    mandate: MandateV2 | dict[str, Any],
    mandate_signature_b64: str,
    binding: PostAuthorizationBindingV1 | dict[str, Any],
    binding_signature_b64: str,
    candidate: CandidateDealV2 | dict[str, Any],
    mandate_public_key_b64: str,
    binding_public_key_b64: str,
    *,
    now: datetime | None = None,
) -> VerificationResult:
    checks: list[VerificationCheck] = []
    try:
        parsed_mandate = mandate if isinstance(mandate, MandateV2) else MandateV2.model_validate(mandate)
        parsed_binding = binding if isinstance(binding, PostAuthorizationBindingV1) else PostAuthorizationBindingV1.model_validate(binding)
        parsed_candidate = candidate if isinstance(candidate, CandidateDealV2) else CandidateDealV2.model_validate(candidate)
    except ValidationError:
        return VerificationResult(
            verdict="REJECT",
            payment_ready=False,
            checks=[_check("versioned_shape", False, "Versioned payment binding is malformed.")],
        )

    current_time = now or datetime.now(timezone.utc)
    mandate_signature_valid = verify_versioned_signature(
        parsed_mandate, mandate_signature_b64, mandate_public_key_b64
    )
    binding_signature_valid = verify_versioned_signature(
        parsed_binding, binding_signature_b64, binding_public_key_b64
    )
    checks.extend(
        [
            _check("mandate_v2_signature", mandate_signature_valid, "Mandate v2 signature is valid." if mandate_signature_valid else "Mandate v2 signature is invalid."),
            _check("post_authorization_signature", binding_signature_valid, "Post-authorization binding signature is valid." if binding_signature_valid else "Post-authorization binding signature is invalid."),
            _check("mandate_v2_expiry", current_time < parsed_mandate.expires_at, "Mandate v2 is not expired." if current_time < parsed_mandate.expires_at else "Mandate v2 has expired."),
            _check("post_authorization_expiry", current_time < parsed_binding.expires_at, "Post-authorization binding is not expired." if current_time < parsed_binding.expires_at else "Post-authorization binding has expired."),
            _check("mandate_binding", parsed_binding.mandate_id == parsed_mandate.mandate_id and parsed_binding.mandate_digest_b64 == mandate_v2_digest_b64(parsed_mandate), "Binding references the exact mandate." if parsed_binding.mandate_id == parsed_mandate.mandate_id and parsed_binding.mandate_digest_b64 == mandate_v2_digest_b64(parsed_mandate) else "Binding does not reference the exact mandate."),
            _check("order_binding", parsed_candidate.paypal_order_id == parsed_mandate.paypal_order_id == parsed_binding.paypal_order_id, "Candidate, mandate, and authorization use the same PayPal order." if parsed_candidate.paypal_order_id == parsed_mandate.paypal_order_id == parsed_binding.paypal_order_id else "PayPal order IDs do not match."),
            _check("authorization", parsed_binding.authorization_status == "AUTHORIZED", "PayPal authorization is authorized." if parsed_binding.authorization_status == "AUTHORIZED" else "PayPal authorization is not authorized."),
            _check("authorization_amount", parsed_binding.authorized_amount_cents == parsed_mandate.authorized_cap_cents, "Authorization equals the signed ceiling." if parsed_binding.authorized_amount_cents == parsed_mandate.authorized_cap_cents else "Authorization does not equal the signed ceiling."),
            _check("currency", parsed_candidate.currency == parsed_mandate.currency == parsed_binding.currency, "Currency matches across all signed objects." if parsed_candidate.currency == parsed_mandate.currency == parsed_binding.currency else "Currency does not match."),
            _check("negotiation_id", parsed_candidate.negotiation_id == parsed_mandate.negotiation_id, "Negotiation ID matches the mandate." if parsed_candidate.negotiation_id == parsed_mandate.negotiation_id else "Negotiation ID does not match the mandate."),
            _check("merchant", parsed_candidate.merchant_id in parsed_mandate.merchant_restrictions, "Merchant is allowed by the mandate." if parsed_candidate.merchant_id in parsed_mandate.merchant_restrictions else "Merchant is not allowed by the mandate."),
            _check("spending_cap", parsed_candidate.total_cents <= parsed_mandate.authorized_cap_cents, "Total is within the signed spending ceiling." if parsed_candidate.total_cents <= parsed_mandate.authorized_cap_cents else "Total exceeds the signed spending ceiling."),
            _check("authorization_cap", parsed_candidate.total_cents <= parsed_binding.authorized_amount_cents, "Total is within the verified authorization." if parsed_candidate.total_cents <= parsed_binding.authorized_amount_cents else "Total exceeds the verified authorization."),
        ]
    )
    product_references_valid = _verify_product_references(
        parsed_mandate, parsed_candidate, mandate_public_key_b64, current_time
    )
    checks.append(_check("product_references", product_references_valid, "Discovered product references are valid." if product_references_valid else "Product references are missing or invalid."))
    passed = all(check.verdict == "PASS" for check in checks)
    return VerificationResult(verdict="PASS" if passed else "REJECT", payment_ready=passed, checks=checks)


def _verify_product_references(
    mandate: MandateV2,
    candidate: CandidateDealV2,
    public_key_b64: str,
    now: datetime,
) -> bool:
    referenced_total = 0
    for product in candidate.products:
        reference = product.product_reference
        if reference is None or now >= reference.reference.expires_at:
            return False
        if not verify_product_reference_signature(reference, public_key_b64):
            return False
        signed = reference.reference
        if signed.channel3_product_id != product.product_identifier:
            return False
        if signed.merchant_domain != candidate.merchant_id:
            return False
        if signed.currency != candidate.currency or product.offer_url != signed.offer_url:
            return False
        if product.unit_price_cents != signed.price_minor or signed.availability != "InStock":
            return False
        referenced_total += signed.price_minor * product.quantity
    return referenced_total == candidate.total_cents
