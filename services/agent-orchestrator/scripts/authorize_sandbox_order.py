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
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print("Usage: python scripts\\authorize_sandbox_order.py <order_id>")
        return 2

    order_id = sys.argv[1].strip()

    try:
        access_token = request_sandbox_access_token()
    except ValueError as error:
        print(f"Authorization stopped: {error}")
        return 2
    except httpx.HTTPStatusError as error:
        print(
            "Authorization stopped: OAuth returned "
            f"HTTP {error.response.status_code}."
        )
        return 1
    except httpx.RequestError as error:
        print(
            "Authorization stopped: OAuth network error "
            f"({error.__class__.__name__})."
        )
        return 1

    order_url = f"{ORDERS_URL}/{quote(order_id, safe='')}"
    try:
        response = httpx.get(
            order_url,
            headers={"Authorization": "Bearer " + access_token},
            timeout=httpx.Timeout(20.0, connect=5.0),
        )
        response.raise_for_status()
        order = response.json()
    except httpx.HTTPStatusError as error:
        print(
            "Authorization stopped: order inspection returned "
            f"HTTP {error.response.status_code}."
        )
        return 1
    except httpx.RequestError as error:
        print(
            "Authorization stopped: order inspection network error "
            f"({error.__class__.__name__})."
        )
        return 1
    except ValueError:
        print("Authorization stopped: PayPal returned invalid order JSON.")
        return 1

    is_valid, reason = validate_order(order, order_id)
    if not is_valid:
        print(f"Authorization stopped: {reason}")
        return 1

    authorizations = existing_authorizations(order)
    if authorizations:
        print("An authorization already exists; no new request was made.")
        for authorization in authorizations:
            print(
                f"Authorization ID: {authorization['id']}; "
                f"status: {authorization['status']}"
            )
        return 0

    request_id = authorization_request_id(order_id)
    try:
        response = httpx.post(
            f"{order_url}/authorize",
            headers={
                "Authorization": "Bearer " + access_token,
                "Content-Type": "application/json",
                "Prefer": "return=representation",
                "PayPal-Request-Id": request_id,
            },
            json={},
            timeout=httpx.Timeout(20.0, connect=5.0),
        )
        response.raise_for_status()
        authorization_result = response.json()
    except httpx.TimeoutException:
        print(
            "Authorization request timed out after submission may have occurred. "
            "Stop and reconcile the order and authorization state before retrying."
        )
        return 1
    except httpx.HTTPStatusError as error:
        print(
            "Authorization failed: PayPal returned "
            f"HTTP {error.response.status_code}."
        )
        return 1
    except httpx.RequestError as error:
        print(
            "Authorization failed: network error "
            f"({error.__class__.__name__}). Reconcile before retrying."
        )
        return 1
    except ValueError:
        print("Authorization failed: PayPal returned invalid JSON.")
        return 1

    result_authorizations = existing_authorizations(authorization_result)
    if not result_authorizations:
        print("Authorization failed: response did not include an authorization.")
        return 1

    authorization = result_authorizations[0]
    authorized_amount = "unavailable"
    authorized_currency = "unavailable"
    result_units = authorization_result.get("purchase_units")
    if isinstance(result_units, list):
        for purchase_unit in result_units:
            if not isinstance(purchase_unit, dict):
                continue
            payments = purchase_unit.get("payments")
            if not isinstance(payments, dict):
                continue
            payment_authorizations = payments.get("authorizations")
            if not isinstance(payment_authorizations, list):
                continue
            for item in payment_authorizations:
                if not isinstance(item, dict) or item.get("id") != authorization["id"]:
                    continue
                amount = item.get("amount")
                if isinstance(amount, dict):
                    authorized_currency = str(amount.get("currency_code", "unavailable"))
                    authorized_amount = str(amount.get("value", "unavailable"))

    print("PayPal Sandbox order authorized.")
    print(f"Order ID: {authorization_result.get('id', order_id)}")
    print(f"Resulting order status: {authorization_result.get('status', 'unknown')}")
    print(f"Authorization ID: {authorization['id']}")
    print(f"Authorization status: {authorization['status']}")
    print(f"Authorized amount: {authorized_currency} {authorized_amount}")
    print("Captured amount: $0.00; no capture request was made.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
