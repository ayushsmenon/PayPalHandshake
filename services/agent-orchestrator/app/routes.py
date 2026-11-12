from fastapi import APIRouter, HTTPException

from .schemas import (
    AuditTrailResponse,
    DealApprovalCaptureRequest,
    DealApprovalCaptureResponse,
    DealAssemblyRequest,
    DealAssemblyResponse,
    DealItem,
    DealVerificationRequest,
    DealVerificationResponse,
    DemoNegotiation,
    NegotiationEvent,
    NegotiationStatus,
    Product,
    VerificationCheck,
    VerificationState,
)
from .channel3 import Channel3UnavailableError, assemble_deal, channel3_client_from_environment
from .deal_flow import DealFlowManager

router = APIRouter(prefix="/api/v1", tags=["negotiations"])
deal_flow = DealFlowManager()


@router.get("/negotiations/demo", response_model=DemoNegotiation)
def get_demo_negotiation() -> DemoNegotiation:
    return DemoNegotiation(
        negotiation_id="HS-2048",
        shopping_goal="27-inch 4K monitor + mechanical keyboard",
        merchant="Best Buy Marketplace",
        currency="USD",
        market_reference_cents=47800,
        authorized_budget_cents=45000,
        proposed_total_cents=41850,
        captured_amount_cents=0,
        status=NegotiationStatus.VERIFYING,
        products=[
            Product(name="27-inch 4K monitor", price_cents=32900),
            Product(name="Mechanical keyboard", price_cents=8950),
        ],
        history=[
            NegotiationEvent(
                label="Market reference",
                participant="Market data",
                amount_cents=47800,
            ),
            NegotiationEvent(
                label="Merchant opening offer",
                participant="Merchant",
                amount_cents=46500,
            ),
            NegotiationEvent(
                label="Buyer counter-offer",
                participant="Buyer",
                amount_cents=41000,
            ),
            NegotiationEvent(
                label="Merchant final offer",
                participant="Merchant",
                amount_cents=41850,
            ),
        ],
        verification_checks=[
            VerificationCheck(
                check="Signed mandate",
                state=VerificationState.PENDING,
            ),
            VerificationCheck(
                check="Product identity",
                state=VerificationState.RUNNING,
            ),
            VerificationCheck(
                check="Merchant/payee identity",
                state=VerificationState.PENDING,
            ),
            VerificationCheck(
                check="Total versus spending cap",
                state=VerificationState.PRELIMINARY,
            ),
            VerificationCheck(
                check="Purchase description",
                state=VerificationState.PENDING,
            ),
        ],
    )


@router.post("/deals/assemble", response_model=DealAssemblyResponse)
def assemble_shopping_deal(request: DealAssemblyRequest) -> DealAssemblyResponse:
    try:
        client = channel3_client_from_environment()
        selected, limitations = assemble_deal(
            client,
            request.requirements,
            request.budget_cents,
            request.currency,
        )
    except (ValueError, Channel3UnavailableError) as error:
        raise HTTPException(
            status_code=503,
            detail="Channel3 product discovery is currently unavailable.",
        ) from error

    total_cents = sum(item.price_cents for item in selected)
    try:
        import hashlib
        from uuid import NAMESPACE_URL, uuid5
        from src.models import Negotiation
        from src.repositories import DatabaseSessionManager, NegotiationRepository

        db_manager = DatabaseSessionManager()
        neg_uuid = uuid5(NAMESPACE_URL, request.negotiation_id)
        with db_manager.session_scope() as session:
            repo = NegotiationRepository(session)
            neg = repo.get_negotiation(neg_uuid)
            if not neg:
                neg = Negotiation(
                    id=neg_uuid,
                    shopper_id="shopper_avery_singh",
                    request_prompt="; ".join(request.requirements),
                    budget_cents=request.budget_cents,
                    currency_code=request.currency,
                    currency=request.currency,
                    status="ASSEMBLED" if selected else "OPEN",
                )
                session.add(neg)
                session.flush()
            for item in selected:
                prod = repo.add_product(
                    negotiation_id=neg_uuid,
                    title=item.name,
                    external_product_id=item.product_id,
                    catalog_metadata={"merchant_domain": item.merchant_domain, "offer_url": item.offer_url},
                )
                offer_hash = hashlib.sha256(f"{item.product_id}:{item.price_cents}:{item.merchant_domain}".encode()).hexdigest()
                repo.create_offer_snapshot(
                    negotiation_id=neg_uuid,
                    merchant_id=item.merchant_domain,
                    merchant_name=item.merchant_domain,
                    price_cents=item.price_cents,
                    currency_code=item.currency,
                    currency=item.currency,
                    offer_hash=offer_hash,
                    product_id=prod.id,
                )
    except Exception:
        pass

    return DealAssemblyResponse(
        status="ASSEMBLED" if selected else "NO_ELIGIBLE_DEAL",
        is_real=bool(selected),
        source="channel3",
        negotiation_id=request.negotiation_id,
        budget_cents=request.budget_cents,
        currency=request.currency,
        total_cents=total_cents,
        remaining_budget_cents=request.budget_cents - total_cents,
        items=[
            DealItem(
                requirement=requirement,
                product_identifier=item.product_id,
                name=item.name,
                merchant_domain=item.merchant_domain,
                offer_url=item.offer_url,
                price_cents=item.price_cents,
                currency=item.currency,
                availability=item.availability,
            )
            for requirement, item in zip(request.requirements, selected, strict=False)
        ],
        limitations=limitations,
    )


@router.post("/deals/verify", response_model=DealVerificationResponse)
def verify_deal_endpoint(request: DealVerificationRequest) -> DealVerificationResponse:
    try:
        return deal_flow.run_deal_verification(request.negotiation_id)
    except Exception as error:
        raise HTTPException(
            status_code=422,
            detail=f"Verification failed: {error}",
        ) from error


@router.post("/deals/approve-and-capture", response_model=DealApprovalCaptureResponse)
def approve_and_capture_deal_endpoint(
    request: DealApprovalCaptureRequest,
) -> DealApprovalCaptureResponse:
    try:
        return deal_flow.approve_and_capture_deal(request)
    except Exception as error:
        raise HTTPException(
            status_code=422,
            detail=f"Approval and capture failed: {error}",
        ) from error


@router.get("/audit/trail/{negotiation_id}", response_model=AuditTrailResponse)
def get_audit_trail_endpoint(negotiation_id: str) -> AuditTrailResponse:
    try:
        return deal_flow.get_audit_trail(negotiation_id)
    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch audit trail: {error}",
        ) from error
