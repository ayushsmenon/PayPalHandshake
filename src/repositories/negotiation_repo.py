"""Repository for negotiations, products, offer snapshots, and product references."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select

from src.models import (
    Negotiation,
    Product,
    OfferSnapshot,
    ProductReference,
)
from src.repositories.base import BaseRepository, NotFoundError


class NegotiationRepository(BaseRepository):
    """CRUD operations and query patterns for negotiations and catalog artifacts."""

    def create_negotiation(
        self,
        shopper_id: str,
        request_prompt: str,
        budget_cents: int | None = None,
        currency_code: str = "USD",
        currency: str = "USD",
        budget_amount: Decimal | None = None,
        status: str = "OPEN",
        metadata: dict[str, Any] | None = None,
    ) -> Negotiation:
        """Creates and persists a new negotiation using integer minor units (budget_cents)."""
        curr = (currency_code or currency).upper()
        if budget_cents is not None:
            cents = budget_cents
            dec = budget_amount if budget_amount is not None else Decimal(budget_cents) / Decimal(100)
        elif budget_amount is not None:
            cents = int(budget_amount * 100)
            dec = budget_amount
        else:
            cents = 0
            dec = Decimal("0.00")

        negotiation = Negotiation(
            shopper_id=shopper_id,
            request_prompt=request_prompt,
            budget_cents=cents,
            budget_amount_cents=cents,
            budget_amount=dec,
            currency_code=curr,
            currency=curr,
            status=status,
            metadata_=metadata or {},
        )
        self.session.add(negotiation)
        self.session.flush()
        return negotiation

    def get_negotiation(self, negotiation_id: UUID) -> Negotiation | None:
        """Retrieves a negotiation by its primary UUID."""
        stmt = select(Negotiation).where(Negotiation.id == negotiation_id)
        return self.session.scalar(stmt)

    def update_status(self, negotiation_id: UUID, new_status: str) -> Negotiation:
        """Updates the lifecycle status of a negotiation."""
        negotiation = self.get_negotiation(negotiation_id)
        if not negotiation:
            raise NotFoundError(f"Negotiation with id {negotiation_id} not found")
        negotiation.status = new_status
        self.session.flush()
        return negotiation

    def add_product(
        self,
        negotiation_id: UUID,
        title: str,
        external_product_id: str | None = None,
        description: str | None = None,
        catalog_metadata: dict[str, Any] | None = None,
    ) -> Product:
        """Associates a discovered product with a negotiation."""
        product = Product(
            negotiation_id=negotiation_id,
            external_product_id=external_product_id,
            title=title,
            description=description,
            catalog_metadata=catalog_metadata or {},
        )
        self.session.add(product)
        self.session.flush()
        return product

    def get_products_for_negotiation(self, negotiation_id: UUID) -> list[Product]:
        """Lists all products discovered for a negotiation."""
        stmt = (
            select(Product)
            .where(Product.negotiation_id == negotiation_id)
            .order_by(Product.created_at.desc())
        )
        return list(self.session.scalars(stmt).all())

    def create_offer_snapshot(
        self,
        negotiation_id: UUID,
        merchant_id: str,
        price_cents: int | None = None,
        currency_code: str = "USD",
        currency: str = "USD",
        offer_hash: str = "",
        product_id: UUID | None = None,
        merchant_name: str | None = None,
        offered_price: Decimal | None = None,
        terms_payload: dict[str, Any] | None = None,
    ) -> OfferSnapshot:
        """Records an immutable snapshot of an agreed/evaluated offer using integer minor units."""
        curr = (currency_code or currency).upper()
        if price_cents is not None:
            cents = price_cents
            dec = offered_price if offered_price is not None else Decimal(price_cents) / Decimal(100)
        elif offered_price is not None:
            cents = int(offered_price * 100)
            dec = offered_price
        else:
            cents = 0
            dec = Decimal("0.00")

        snapshot = OfferSnapshot(
            negotiation_id=negotiation_id,
            product_id=product_id,
            merchant_id=merchant_id,
            merchant_name=merchant_name,
            price_cents=cents,
            offered_price_cents=cents,
            offered_price=dec,
            currency_code=curr,
            currency=curr,
            offer_hash=offer_hash,
            terms_payload=terms_payload or {},
        )
        self.session.add(snapshot)
        self.session.flush()
        return snapshot

    def add_product_reference(
        self,
        negotiation_id: UUID,
        channel3_product_id: str,
        offer_url: str,
        merchant_domain: str,
        price_cents: int | None = None,
        price_minor: int | None = None,
        digest: str = "",
        canonical_digest: str = "",
        signature: str = "",
        signature_b64: str = "",
        public_key: str = "",
        public_key_b64: str | None = None,
        issued_at: datetime | None = None,
        expires_at: datetime | None = None,
        protocol_version: str = "v1",
        schema_version: str = "product-reference.v1",
        currency_code: str = "USD",
        currency: str = "USD",
        availability: str = "IN_STOCK",
        canonical_category: str | None = None,
        registry_version: str | None = None,
        canonical_payload: dict[str, Any] | None = None,
        product_id: UUID | None = None,
        condition: str | None = None,
    ) -> ProductReference:
        """Stores a canonical signed product reference conforming to product-reference.v1 or v2."""
        curr = (currency_code or currency).upper()
        cents = price_cents if price_cents is not None else (price_minor if price_minor is not None else 0)
        d = digest or canonical_digest
        sig = signature or signature_b64
        pk = public_key or (public_key_b64 or "")

        ref = ProductReference(
            negotiation_id=negotiation_id,
            product_id=product_id,
            protocol_version=protocol_version,
            schema_version=schema_version,
            channel3_product_id=channel3_product_id,
            offer_url=offer_url,
            merchant_domain=merchant_domain,
            canonical_category=canonical_category,
            registry_version=registry_version,
            canonical_payload=canonical_payload or {},
            price_cents=cents,
            price_minor=cents,
            currency_code=curr,
            currency=curr,
            availability=availability,
            condition=condition,
            digest=d,
            canonical_digest=d,
            signature=sig,
            signature_b64=sig,
            public_key=pk,
            public_key_b64=pk,
            issued_at=issued_at or datetime.now(timezone.utc),
            expires_at=expires_at or (datetime.now(timezone.utc) + (issued_at - issued_at if issued_at else datetime.now(timezone.utc))),
        )
        self.session.add(ref)
        self.session.flush()
        return ref

    def get_product_references(self, negotiation_id: UUID) -> list[ProductReference]:
        """Returns all canonical product references registered for a negotiation."""
        stmt = (
            select(ProductReference)
            .where(ProductReference.negotiation_id == negotiation_id)
            .order_by(ProductReference.created_at.desc())
        )
        return list(self.session.scalars(stmt).all())
