from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

SERVICE_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(SERVICE_ENV_PATH, override=False)

from .paypal_adapter import PayPalVerificationAdapter
from .paypal_client import PayPalSandboxClient
from .process_b_client import ProcessBClient, ProcessBVerificationError
from .consent import ConsentEvidenceProvider, MandateConsentEvidence, consent_matches_mandate
from .db import PaymentPolicyDbAdapter
from .mandate_service import (
    IssueMandateV2Request,
    IssueMandateV2Response,
    issue_and_record_mandate_v2,
)
from .payment_service import (
    PaymentExecutionOrchestrator,
    PaymentOrchestrationError,
)
from .versioned_models import (
    CandidateDealV2,
    MandateV2,
    PostAuthorizationBindingV1,
)
from .product_reference import (
    ProductReferenceIssuanceError,
    SignedProductReference,
    SignedProductReferenceV2,
    issue_product_reference,
    issue_product_reference_v2,
)
from .category_registry import load_product_category_registry


class ProductCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    product_identifier: str = Field(min_length=1)
    merchant_domain: str = Field(min_length=1)
    price_cents: int = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    offer_url: str | None = None
    availability: str | None = None
    is_real: bool | None = None
    verified: bool | None = None


class ProductReferenceIssuanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    products: list[ProductCandidate] = Field(min_length=1)


class ProductReferenceIssuanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    references: list[SignedProductReference]


class ProductReferenceV2IssuanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    references: list[SignedProductReferenceV2]


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


class CapturePaymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mandate: MandateV2
    mandate_signature: str
    post_authorization_binding: PostAuthorizationBindingV1
    binding_signature: str
    candidate: CandidateDealV2
    consent_evidence: MandateConsentEvidence
    idempotency_key: str


class CapturePaymentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    capture_id: str | None = None
    amount_cents: int
    currency: str
    paypal_order_id: str
    paypal_authorization_id: str
    mandate_id: str
    mandate_digest_b64: str
    verification_passed: bool
    mandate_consumed: bool


def create_app(
    *,
    paypal_adapter: PayPalVerificationAdapter | None = None,
    paypal_client: PayPalSandboxClient | None = None,
    process_b_client: ProcessBClient | None = None,
    consent_provider: ConsentEvidenceProvider | None = None,
    db_adapter: PaymentPolicyDbAdapter | None = None,
    environment: str = "production",
    category_registry=None,
) -> FastAPI:
    if environment == "production" and paypal_adapter is not None:
        from .paypal_adapter import MockPayPalAdapter

        if isinstance(paypal_adapter, MockPayPalAdapter):
            raise ValueError("Production cannot use the mock PayPal adapter.")

    app = FastAPI(
        title="PAYPAL HANDSHAKE Payment Policy Service",
        description="Internal read-only decision boundary for verified payment data.",
    )

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "payment-policy-svc"}

    registry = category_registry or load_product_category_registry()

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

    @app.post(
        "/internal/v1/product-references/issue",
        response_model=ProductReferenceIssuanceResponse,
    )
    def issue_product_references(
        request: ProductReferenceIssuanceRequest,
    ) -> ProductReferenceIssuanceResponse:
        references: list[SignedProductReference] = []
        for candidate in request.products:
            try:
                reference = issue_product_reference(
                    candidate.product_identifier,
                    candidate.merchant_domain,
                    _price_cents_as_amount(candidate.price_cents),
                    candidate.currency,
                )
            except ProductReferenceIssuanceError as error:
                raise HTTPException(
                    status_code=422,
                    detail="A product candidate could not be independently verified.",
                ) from error
            references.append(reference)
        return ProductReferenceIssuanceResponse(references=references)

    @app.post(
        "/internal/v2/product-references/issue",
        response_model=ProductReferenceV2IssuanceResponse,
    )
    def issue_product_references_v2(
        request: ProductReferenceIssuanceRequest,
    ) -> ProductReferenceV2IssuanceResponse:
        references = []
        for candidate in request.products:
            try:
                reference = issue_product_reference_v2(
                    candidate.product_identifier,
                    candidate.merchant_domain,
                    _price_cents_as_amount(candidate.price_cents),
                    candidate.currency,
                    registry=registry,
                )
            except ProductReferenceIssuanceError as error:
                raise HTTPException(
                    status_code=422,
                    detail="A product candidate could not be independently verified.",
                ) from error
            references.append(reference)
        return ProductReferenceV2IssuanceResponse(references=references)

    @app.post(
        "/internal/v2/mandates/issue",
        response_model=IssueMandateV2Response,
    )
    def issue_mandate_endpoint(
        request: IssueMandateV2Request,
    ) -> IssueMandateV2Response:
        resolved_db = db_adapter or PaymentPolicyDbAdapter()
        shopper_id = "shopper_authenticated_demo"
        try:
            return issue_and_record_mandate_v2(
                request=request,
                shopper_id=shopper_id,
                db_adapter=resolved_db,
            )
        except Exception as error:
            raise HTTPException(
                status_code=422,
                detail=str(error),
            ) from error

    @app.post(
        "/internal/v2/payments/capture",
        response_model=CapturePaymentResponse,
    )
    def capture_payment_endpoint(
        request: CapturePaymentRequest,
    ) -> CapturePaymentResponse:
        if process_b_client is None:
            raise HTTPException(
                status_code=503,
                detail="Process B verification client is not configured.",
            )
        resolved_db = db_adapter or PaymentPolicyDbAdapter()
        resolved_paypal = (
            paypal_client
            or (
                PayPalSandboxClient(db_adapter=resolved_db)
                if paypal_adapter is None
                else paypal_adapter
            )
        )
        orchestrator = PaymentExecutionOrchestrator(
            db_adapter=resolved_db,
            process_b_client=process_b_client,
            paypal_client=resolved_paypal,
        )
        try:
            result = orchestrator.verify_and_capture_deal(
                mandate=request.mandate,
                mandate_signature_b64=request.mandate_signature,
                binding=request.post_authorization_binding,
                binding_signature_b64=request.binding_signature,
                candidate=request.candidate,
                consent_evidence=request.consent_evidence,
                idempotency_key=request.idempotency_key,
            )
            return CapturePaymentResponse(
                status=result["status"],
                capture_id=result["capture_id"],
                amount_cents=result["amount_cents"],
                currency=result["currency"],
                paypal_order_id=result["paypal_order_id"],
                paypal_authorization_id=result["paypal_authorization_id"],
                mandate_id=result["mandate_id"],
                mandate_digest_b64=result["mandate_digest_b64"],
                verification_passed=result["verification_passed"],
                mandate_consumed=result["mandate_consumed"],
            )
        except (PaymentOrchestrationError, ValueError) as error:
            raise HTTPException(
                status_code=422,
                detail=str(error),
            ) from error

    return app


def _price_cents_as_amount(price_cents: int) -> str:
    return format(Decimal(price_cents) / Decimal(100), ".2f")


app = create_app()
