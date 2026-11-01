"""PayPal observation boundary used by Process C.

The production implementation is intentionally not included in this milestone.
Only the protocol and an explicit in-memory adapter are provided for tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ObservedPayPalOrder:
    order_id: str
    intent: str
    status: str
    amount_cents: int
    currency: str
    payee_id: str | None = None


@dataclass(frozen=True)
class ObservedPayPalAuthorization:
    authorization_id: str
    order_id: str
    status: str
    amount_cents: int
    currency: str


class PayPalVerificationAdapter(Protocol):
    def inspect_order(self, order_id: str) -> ObservedPayPalOrder:
        ...

    def inspect_authorization(
        self, authorization_id: str
    ) -> ObservedPayPalAuthorization:
        ...


class MockAdapterConfigurationError(ValueError):
    """Raised when the mock adapter is selected outside test/development."""


class MockPayPalAdapter:
    """Explicit test adapter; it never contacts PayPal."""

    def __init__(
        self,
        order: ObservedPayPalOrder,
        authorization: ObservedPayPalAuthorization,
        *,
        environment: str,
    ) -> None:
        if environment not in {"test", "development"}:
            raise MockAdapterConfigurationError(
                "Mock PayPal adapter is only available in test/development."
            )
        self.order = order
        self.authorization = authorization

    def inspect_order(self, order_id: str) -> ObservedPayPalOrder:
        if order_id != self.order.order_id:
            raise ValueError("PayPal order ID was not found in the mock adapter.")
        return self.order

    def inspect_authorization(
        self, authorization_id: str
    ) -> ObservedPayPalAuthorization:
        if authorization_id != self.authorization.authorization_id:
            raise ValueError(
                "PayPal authorization ID was not found in the mock adapter."
            )
        return self.authorization
