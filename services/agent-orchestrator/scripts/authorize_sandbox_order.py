"""Authorize one expected PayPal Sandbox order after strict preflight checks."""

from __future__ import annotations

import hashlib
import sys
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

import httpx

from paypal_auth import request_sandbox_access_token

ORDERS_URL = "https://api-m.sandbox.paypal.com/v2/checkout/orders"
EXPECTED_CURRENCY = "USD"
EXPECTED_AMOUNT_CENTS = 45000


def authorization_request_id(order_id: str) -> str:
    digest = hashlib.sha256(
        f"paypal-handshake-authorize:{order_id}".encode("utf-8")
    ).hexdigest()
    return f"handshake-authorize-{digest[:32]}"


def amount_to_cents(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return None
    cents = amount * 100
    if cents != cents.to_integral_value():
        return None
    return int(cents)


def existing_authorizations(order: dict[str, object]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    purchase_units = order.get("purchase_units")
    if not isinstance(purchase_units, list):
        return found

    for purchase_unit in purchase_units:
        if not isinstance(purchase_unit, dict):
            continue
        payments = purchase_unit.get("payments")
        if not isinstance(payments, dict):
            continue
        authorizations = payments.get("authorizations")
        if not isinstance(authorizations, list):
            continue
        for authorization in authorizations:
            if not isinstance(authorization, dict):
                continue
            authorization_id = authorization.get("id")
            status = authorization.get("status")
            if isinstance(authorization_id, str):
                found.append(
                    {
                        "id": authorization_id,
                        "status": status if isinstance(status, str) else "unknown",
                    }
                )
    return found


def validate_order(order: object, order_id: str) -> tuple[bool, str]:
    if not isinstance(order, dict):
        return False, "PayPal returned an invalid order payload."
    if order.get("id") != order_id:
        return False, "PayPal returned a different order ID."
    if order.get("status") != "APPROVED":
        return False, f"order status is {order.get('status', 'unknown')}, not APPROVED."
    if order.get("intent") != "AUTHORIZE":
        return False, f"order intent is {order.get('intent', 'unknown')}, not AUTHORIZE."

    purchase_units = order.get("purchase_units")
    if not isinstance(purchase_units, list) or not purchase_units:
        return False, "the order has no purchase units."

    total_cents = 0
    for purchase_unit in purchase_units:
        if not isinstance(purchase_unit, dict):
            return False, "the order contains an invalid purchase unit."
        amount = purchase_unit.get("amount")
        if not isinstance(amount, dict):
            return False, "a purchase unit has no amount."
        if amount.get("currency_code") != EXPECTED_CURRENCY:
            return False, "a purchase unit currency is not USD."
        cents = amount_to_cents(amount.get("value"))
        if cents is None:
            return False, "a purchase unit amount is not a valid whole-cent value."
        total_cents += cents

    if total_cents != EXPECTED_AMOUNT_CENTS:
        return False, f"the total is {total_cents} cents, not 45000 cents."
    return True, ""


def main() -> int:
    print(
        "Authorization stopped: Process A cannot execute PayPal authorization. "
        "Process C must own payment execution after Process B verification."
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
