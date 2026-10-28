"""Create one PayPal Sandbox AUTHORIZE order and show its approval URL."""

from __future__ import annotations

import sys
import uuid

import httpx

from paypal_auth import request_sandbox_access_token

ORDERS_URL = "https://api-m.sandbox.paypal.com/v2/checkout/orders"
RETURN_URL = "http://localhost:3000/paypal/sandbox/return"
CANCEL_URL = "http://localhost:3000/paypal/sandbox/cancel"


def main() -> int:
    try:
        access_token = request_sandbox_access_token()
    except ValueError as error:
        print(f"Order creation stopped: {error}")
        return 2
    except httpx.HTTPStatusError as error:
        print(
            "Order creation stopped: OAuth returned "
            f"HTTP {error.response.status_code}."
        )
        return 1
    except httpx.RequestError as error:
        print(
            "Order creation stopped: OAuth network error "
            f"({error.__class__.__name__})."
        )
        return 1

    payload = {
        "intent": "AUTHORIZE",
        "purchase_units": [
            {
                "reference_id": "handshake-demo",
                "description": "PAYPAL HANDSHAKE Sandbox authorization test",
                "amount": {
                    "currency_code": "USD",
                    "value": "450.00",
                },
            }
        ],
        "application_context": {
            "return_url": RETURN_URL,
            "cancel_url": CANCEL_URL,
        },
    }

    try:
        response = httpx.post(
            ORDERS_URL,
            headers={
                "Authorization": "Bearer " + access_token,
                "Content-Type": "application/json",
                "PayPal-Request-Id": str(uuid.uuid4()),
            },
            json=payload,
            timeout=httpx.Timeout(20.0, connect=5.0),
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        print(
            "Order creation failed: PayPal returned "
            f"HTTP {error.response.status_code}."
        )
        return 1
    except httpx.RequestError as error:
        print(
            "Order creation failed: network error "
            f"({error.__class__.__name__})."
        )
        return 1

    try:
        order = response.json()
    except ValueError:
        print("Order creation failed: PayPal returned invalid JSON.")
        return 1

    order_id = order.get("id")
    order_status = order.get("status")
    approval_url = next(
        (
            link.get("href")
            for link in order.get("links", [])
            if link.get("rel") == "approve" and isinstance(link.get("href"), str)
        ),
        None,
    )

    if not isinstance(order_id, str) or not isinstance(order_status, str):
        print("Order creation failed: response did not include order details.")
        return 1

    print("PayPal Sandbox order created.")
    print(f"HTTP status: {response.status_code}")
    print(f"Order ID: {order_id}")
    print(f"Current order status: {order_status}")
    if approval_url:
        print(f"Buyer approval URL: {approval_url}")
    else:
        print("Buyer approval URL: not returned; the order is not ready for approval.")
    print("No authorization, capture, void, or payout request was made.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
