from fastapi import APIRouter
from fastapi import HTTPException

from .schemas import (
    DemoNegotiation,
    DealAssemblyRequest,
    DealAssemblyResponse,
    DealItem,
    NegotiationEvent,
    NegotiationStatus,
    Product,
    VerificationCheck,
    VerificationState,
)
from .channel3 import Channel3UnavailableError, assemble_deal, channel3_client_from_environment

router = APIRouter(prefix="/api/v1", tags=["negotiations"])


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
