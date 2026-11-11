"""Internal, read-only Process C payment-decision endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .paypal_adapter import PayPalVerificationAdapter
from .process_b_client import ProcessBClient, ProcessBVerificationError
from .consent import ConsentEvidenceProvider, consent_matches_mandate
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


def create_app(
    *,
    paypal_adapter: PayPalVerificationAdapter | None = None,
    process_b_client: ProcessBClient | None = None,
    consent_provider: ConsentEvidenceProvider | None = None,
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

    return app


def _price_cents_as_amount(price_cents: int) -> str:
    return format(Decimal(price_cents) / Decimal(100), ".2f")


app = create_app()
