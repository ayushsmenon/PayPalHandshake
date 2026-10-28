from fastapi import APIRouter

from .schemas import (
    DemoNegotiation,
    NegotiationEvent,
    NegotiationStatus,
    Product,
    VerificationCheck,
    VerificationState,
)

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
