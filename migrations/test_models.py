from datetime import datetime, timezone, timedelta
from decimal import Decimal
from uuid import uuid4

from src.models import (
    NegotiationSchema,
    ProductSchema,
    OfferSnapshotSchema,
    MandateSchema,
    VerificationEventSchema,
    PaymentEventSchema,
    Negotiation,
    Product,
    OfferSnapshot,
    Mandate,
    VerificationEvent,
    PaymentEvent,
    Base,
)


def test_models_compilation_and_validation():
    neg_id = uuid4()
    neg_schema = NegotiationSchema(
        id=neg_id,
        shopper_id="shopper_usr_12345",
        request_prompt="Find 27-inch 4K monitor under $500",
        budget_amount=Decimal("500.00"),
        currency="USD",
        status="OPEN",
        metadata={"delivery_zip": "94105"},
    )
    assert neg_schema.shopper_id == "shopper_usr_12345"

    prod_id = uuid4()
    prod_schema = ProductSchema(
        id=prod_id,
        negotiation_id=neg_id,
        external_product_id="ch3_prod_987654",
        title="Dell UltraSharp 27 4K Monitor",
        description="4K UHD Monitor",
        catalog_metadata={"brand": "Dell", "resolution": "3840x2160"},
    )
    assert prod_schema.external_product_id == "ch3_prod_987654"

    offer_id = uuid4()
    offer_schema = OfferSnapshotSchema(
        id=offer_id,
        negotiation_id=neg_id,
        product_id=prod_id,
        merchant_id="merch_amazon_01",
        merchant_name="Amazon",
        offered_price=Decimal("459.99"),
        currency="USD",
        terms_payload={"shipping": "free", "condition": "NEW"},
        offer_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    )
    assert offer_schema.offered_price == Decimal("459.99")

    now = datetime.now(timezone.utc)
    mandate_id = uuid4()
    mandate_schema = MandateSchema(
        id=mandate_id,
        mandate_id="mnd_schema_test_01",
        negotiation_id=neg_id,
        public_key="a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90",
        signature="base64_or_hex_ed25519_signature_payload",
        mandate_payload={"max_cap": 500.0, "currency": "USD"},
        expires_at=now + timedelta(hours=2),
        status="ACTIVE",
    )
    assert mandate_schema.public_key.startswith("a1b2c3d4")

    verif_id = uuid4()
    verif_schema = VerificationEventSchema(
        id=verif_id,
        negotiation_id=neg_id,
        gate_name="PRICE_CAP_GATE",
        decision="PASSED",
        passed_checks=["CAP_CHECK_WITHIN_BUDGET"],
        blocked_checks=[],
        reason_details="Price within budget limit",
        manifest_digest="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    )
    assert verif_schema.decision == "PASSED"

    pay_id = uuid4()
    pay_schema = PaymentEventSchema(
        id=pay_id,
        negotiation_id=neg_id,
        idempotency_key="REQ-IDEMP-001",
        paypal_order_id="5O190127TN364715T",
        paypal_authorization_id="AUTH-99998888",
        event_type="AUTHORIZED",
        amount=Decimal("459.99"),
        currency="USD",
        status="COMPLETED",
        raw_response_summary={"intent": "AUTHORIZE"},
    )
    assert pay_schema.idempotency_key == "REQ-IDEMP-001"

    table_names = set(Base.metadata.tables.keys())
    expected_tables = {
        "negotiations",
        "products",
        "offer_snapshots",
        "mandates",
        "verification_events",
        "payment_events",
    }
    assert expected_tables.issubset(table_names), f"Missing tables: {expected_tables - table_names}"
    print("All 6 PostgreSQL models and Pydantic schemas successfully verified!")


if __name__ == "__main__":
    test_models_compilation_and_validation()
