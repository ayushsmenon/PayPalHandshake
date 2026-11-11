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
        budget_amount: Decimal,
        currency: str,
        budget_cents: int | None = None,
        status: str = "OPEN",
        metadata: dict[str, Any] | None = None,
    ) -> Negotiation:
        """Creates and persists a new negotiation."""
        calculated_cents = budget_cents if budget_cents is not None else int(budget_amount * 100)
        negotiation = Negotiation(
            shopper_id=shopper_id,
            request_prompt=request_prompt,
            budget_amount=budget_amount,
            budget_cents=calculated_cents,
            budget_amount_cents=calculated_cents,
            currency=currency.upper(),
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
        offered_price: Decimal,
        currency: str,
        offer_hash: str,
        product_id: UUID | None = None,
        merchant_name: str | None = None,
        price_cents: int | None = None,
        terms_payload: dict[str, Any] | None = None,
    ) -> OfferSnapshot:
        """Records an immutable snapshot of an agreed/evaluated offer."""
        calculated_cents = price_cents if price_cents is not None else int(offered_price * 100)
        snapshot = OfferSnapshot(
            negotiation_id=negotiation_id,
            product_id=product_id,
            merchant_id=merchant_id,
            merchant_name=merchant_name,
            offered_price=offered_price,
            price_cents=calculated_cents,
            offered_price_cents=calculated_cents,
            currency=currency.upper(),
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
        price_minor: int,
        currency: str,
        availability: str,
        canonical_digest: str,
        signature_b64: str,
        issued_at: datetime,
        expires_at: datetime,
        product_id: UUID | None = None,
        condition: str | None = None,
        public_key_b64: str | None = None,
    ) -> ProductReference:
        """Stores a canonical signed product reference conforming to product-reference.v1."""
        ref = ProductReference(
            negotiation_id=negotiation_id,
            product_id=product_id,
            channel3_product_id=channel3_product_id,
            offer_url=offer_url,
            merchant_domain=merchant_domain,
            price_minor=price_minor,
            currency=currency.upper(),
            availability=availability,
            condition=condition,
            canonical_digest=canonical_digest,
            signature_b64=signature_b64,
            public_key_b64=public_key_b64,
            issued_at=issued_at,
            expires_at=expires_at,
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
