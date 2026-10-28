"""Inspect a PayPal Sandbox order without changing its state."""

from __future__ import annotations

import sys
from urllib.parse import quote

import httpx

from paypal_auth import request_sandbox_access_token

ORDERS_URL = "https://api-m.sandbox.paypal.com/v2/checkout/orders"


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print("Usage: python scripts\\inspect_sandbox_order.py <order_id>")
        return 2

    order_id = sys.argv[1].strip()

    try:
        access_token = request_sandbox_access_token()
    except ValueError as error:
        print(f"Order inspection stopped: {error}")
        return 2
    except httpx.HTTPStatusError as error:
        print(
            "Order inspection stopped: OAuth returned "
            f"HTTP {error.response.status_code}."
        )
        return 1
    except httpx.RequestError as error:
        print(
            "Order inspection stopped: OAuth network error "
            f"({error.__class__.__name__})."
        )
        return 1

    try:
        response = httpx.get(
            f"{ORDERS_URL}/{quote(order_id, safe='')}",
            headers={"Authorization": "Bearer " + access_token},
            timeout=httpx.Timeout(20.0, connect=5.0),
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        print(
            "Order inspection failed: PayPal returned "
            f"HTTP {error.response.status_code}."
        )
        return 1
    except httpx.RequestError as error:
        print(
            "Order inspection failed: network error "
            f"({error.__class__.__name__})."
        )
        return 1

    try:
        order = response.json()
    except ValueError:
        print("Order inspection failed: PayPal returned invalid JSON.")
        return 1

    returned_order_id = order.get("id")
    order_status = order.get("status")
    purchase_units = order.get("purchase_units")

    if not isinstance(returned_order_id, str) or not isinstance(order_status, str):
        print("Order inspection failed: response did not include order details.")
        return 1
    if not isinstance(purchase_units, list):
        print("Order inspection failed: response did not include purchase units.")
        return 1

    print("PayPal Sandbox order inspection succeeded.")
    print(f"Order ID: {returned_order_id}")
    print(f"PayPal order status: {order_status}")
    print(f"Approved: {'yes' if order_status == 'APPROVED' else 'no'}")

    for index, purchase_unit in enumerate(purchase_units, start=1):
        if not isinstance(purchase_unit, dict):
            print(f"Purchase unit {index}: currency/amount unavailable")
            continue
        amount = purchase_unit.get("amount")
        if not isinstance(amount, dict):
            print(f"Purchase unit {index}: currency/amount unavailable")
            continue
        currency = amount.get("currency_code")
        value = amount.get("value")
        if isinstance(currency, str) and isinstance(value, str):
            print(f"Purchase unit {index}: {currency} {value}")
        else:
            print(f"Purchase unit {index}: currency/amount unavailable")

    print("No state-changing PayPal request was made.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
