"""Internal, read-only Process C payment-decision endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict

from .paypal_adapter import PayPalVerificationAdapter
from .process_b_client import ProcessBClient, ProcessBVerificationError
from .consent import ConsentEvidenceProvider, consent_matches_mandate
from .versioned_models import (
    CandidateDealV2,
    MandateV2,
    PostAuthorizationBindingV1,
)


class DryRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mandate: MandateV2
    mandate_signature: str
    post_authorization_binding: PostAuthorizationBindingV1
    binding_signature: str
    candidate: CandidateDealV2


class DryRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["APPROVE", "REJECT"]
    payment_ready: bool
    security_checks_passed: bool
    mandate_consent_verified: bool
    authorization_permitted: bool
    reason: str


def create_app(
    *,
    paypal_adapter: PayPalVerificationAdapter | None = None,
    process_b_client: ProcessBClient | None = None,
    consent_provider: ConsentEvidenceProvider | None = None,
    environment: str = "production",
) -> FastAPI:
    if environment == "production" and paypal_adapter is not None:
        from .paypal_adapter import MockPayPalAdapter

        if isinstance(paypal_adapter, MockPayPalAdapter):
            raise ValueError("Production cannot use the mock PayPal adapter.")

    app = FastAPI(
        title="PAYPAL HANDSHAKE Payment Policy Service",
        description="Internal read-only decision boundary for verified payment data.",
    )

    @app.post("/internal/v1/payment-decisions/dry-run", response_model=DryRunResponse)
    def dry_run(request: DryRunRequest) -> DryRunResponse:
        if paypal_adapter is None or process_b_client is None:
            return DryRunResponse(
                decision="REJECT",
                payment_ready=False,
                security_checks_passed=False,
                mandate_consent_verified=False,
                authorization_permitted=False,
                reason="Payment verification dependencies are not configured.",
            )

        current_time = datetime.now(timezone.utc)
        consent_evidence = (
            consent_provider.get_for_mandate(request.mandate)
            if consent_provider is not None
            else None
        )
        consent_verified = consent_evidence is not None and consent_matches_mandate(
            consent_evidence, request.mandate, now=current_time
        )
        try:
            order = paypal_adapter.inspect_order(request.mandate.paypal_order_id)
            authorization = paypal_adapter.inspect_authorization(
                request.post_authorization_binding.paypal_auth_id
            )
            payee_matches = (
                request.mandate.expected_paypal_payee_id is None
                or order.payee_id == request.mandate.expected_paypal_payee_id
            )
            observations_pass = (
                order.order_id == request.mandate.paypal_order_id
                and order.intent == "AUTHORIZE"
                and order.status == "APPROVED"
                and order.amount_cents == request.mandate.authorized_cap_cents
                and order.currency == request.mandate.currency
                and payee_matches
                and authorization.order_id == order.order_id
                and authorization.status == "AUTHORIZED"
                and authorization.amount_cents == request.mandate.authorized_cap_cents
                and authorization.currency == request.mandate.currency
                and request.candidate.total_cents <= request.mandate.authorized_cap_cents
                and request.candidate.total_cents <= authorization.amount_cents
            )
            if not observations_pass or not consent_verified:
                return DryRunResponse(
                    decision="REJECT",
                    payment_ready=False,
                    security_checks_passed=observations_pass,
                    mandate_consent_verified=consent_verified,
                    authorization_permitted=False,
                    reason="Security checks or separate mandate consent failed.",
                )

            payload = request.model_dump(mode="json")
            process_b_client.verify_authorization_binding(payload)
        except (ValueError, ProcessBVerificationError):
            return DryRunResponse(
                decision="REJECT",
                payment_ready=False,
                security_checks_passed=False,
                mandate_consent_verified=consent_verified,
                authorization_permitted=False,
                reason="Independent payment verification failed.",
            )

        return DryRunResponse(
            decision="APPROVE",
            payment_ready=True,
            security_checks_passed=True,
            mandate_consent_verified=True,
            authorization_permitted=True,
            reason="Process B, PayPal observations, and mandate consent passed.",
        )

    return app


app = create_app()
