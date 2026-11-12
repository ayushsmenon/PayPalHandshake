from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from .paypal_adapter import ObservedPayPalAuthorization, ObservedPayPalOrder
from .db import PaymentPolicyDbAdapter

SANDBOX_API_BASE = "https://api-m.sandbox.paypal.com"
TOKEN_PATH = "/v1/oauth2/token"
ORDERS_PATH = "/v2/checkout/orders"
AUTHORIZATIONS_PATH = "/v2/payments/authorizations"


class PayPalClientError(RuntimeError):
    pass


class PayPalReconciliationError(PayPalClientError):
    pass


def amount_cents_from_decimal_str(value: str) -> int:
    try:
        dec = Decimal(value)
        cents = dec * 100
        if cents != cents.to_integral_value():
            raise ValueError
        return int(cents)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise PayPalClientError(f"Invalid monetary string from PayPal: {value}") from exc


def amount_cents_to_decimal_str(cents: int) -> str:
    return format(Decimal(cents) / Decimal(100), ".2f")


class PayPalSandboxClient:
    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        db_adapter: PaymentPolicyDbAdapter | None = None,
        http_client: httpx.Client | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.client_id = client_id or os.getenv("PAYPAL_CLIENT_ID", "").strip()
        self.client_secret = client_secret or os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
        self.db_adapter = db_adapter
        self.http_client = http_client or httpx.Client(timeout=timeout)
        self.base_url = SANDBOX_API_BASE

    def _require_credentials(self) -> tuple[str, str]:
        if not self.client_id or not self.client_secret:
            raise PayPalClientError("PAYPAL_CLIENT_ID or PAYPAL_CLIENT_SECRET is missing.")
        return self.client_id, self.client_secret

    def get_access_token(self) -> str:
        cid, secret = self._require_credentials()
        try:
            resp = self.http_client.post(
                f"{self.base_url}{TOKEN_PATH}",
                auth=(cid, secret),
                data={"grant_type": "client_credentials"},
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            payload = resp.json()
            token = payload.get("access_token")
            if not isinstance(token, str) or not token:
                raise PayPalClientError("PayPal returned an empty access token.")
            return token
        except httpx.HTTPError as exc:
            raise PayPalClientError(f"PayPal OAuth2 token acquisition failed: {exc}") from exc

    def create_order(
        self,
        authorized_cap_cents: int,
        currency: str = "USD",
        idempotency_key: str = "",
        reference_id: str = "handshake-auth",
        negotiation_id: UUID | None = None,
    ) -> dict[str, Any]:
        token = self.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["PayPal-Request-Id"] = idempotency_key
            if self.db_adapter is not None:
                self.db_adapter.record_payment_event(
                    idempotency_key=idempotency_key,
                    event_type="ORDER_CREATION_PENDING",
                    amount_cents=authorized_cap_cents,
                    currency_code=currency,
                    negotiation_id=negotiation_id,
                    status="CREATED",
                )

        payload = {
            "intent": "AUTHORIZE",
            "purchase_units": [
                {
                    "reference_id": reference_id,
                    "description": "PAYPAL HANDSHAKE Bounded Authorization",
                    "amount": {
                        "currency_code": currency.upper(),
                        "value": amount_cents_to_decimal_str(authorized_cap_cents),
                    },
                }
            ],
            "application_context": {
                "return_url": "http://localhost:3000/paypal/sandbox/return",
                "cancel_url": "http://localhost:3000/paypal/sandbox/cancel",
            },
        }

        try:
            resp = self.http_client.post(
                f"{self.base_url}{ORDERS_PATH}",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            order_data = resp.json()
            order_id = order_data.get("id")
            if idempotency_key and self.db_adapter is not None:
                self.db_adapter.record_payment_event(
                    idempotency_key=f"{idempotency_key}-created",
                    event_type="ORDER_CREATED",
                    amount_cents=authorized_cap_cents,
                    currency_code=currency,
                    negotiation_id=negotiation_id,
                    paypal_order_id=order_id,
                    status="CREATED",
                    raw_response_summary=order_data,
                )
            return order_data
        except httpx.HTTPError as exc:
            raise PayPalClientError(f"PayPal order creation failed: {exc}") from exc

    def inspect_order(self, order_id: str) -> ObservedPayPalOrder:
        token = self.get_access_token()
        try:
            resp = self.http_client.get(
                f"{self.base_url}{ORDERS_PATH}/{quote(order_id, safe='')}",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise PayPalClientError(f"Failed to inspect PayPal order '{order_id}': {exc}") from exc

        returned_id = data.get("id", "")
        intent = data.get("intent", "")
        status = data.get("status", "")
        purchase_units = data.get("purchase_units", [])
        if not purchase_units or not isinstance(purchase_units, list):
            raise PayPalClientError("Order contains no purchase units.")

        unit = purchase_units[0]
        amount_dict = unit.get("amount", {})
        currency = amount_dict.get("currency_code", "USD")
        val_str = amount_dict.get("value", "0.00")
        amount_cents = amount_cents_from_decimal_str(val_str)

        payee_id = None
        payee_dict = unit.get("payee")
        if isinstance(payee_dict, dict):
            payee_id = payee_dict.get("merchant_id") or payee_dict.get("email_address")

        return ObservedPayPalOrder(
            order_id=returned_id,
            intent=intent,
            status=status,
            amount_cents=amount_cents,
            currency=currency,
            payee_id=payee_id,
        )

    def authorize_order(
        self,
        order_id: str,
        idempotency_key: str,
        negotiation_id: UUID | None = None,
    ) -> ObservedPayPalAuthorization:
        token = self.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "PayPal-Request-Id": idempotency_key,
        }

        if self.db_adapter is not None:
            self.db_adapter.record_payment_event(
                idempotency_key=idempotency_key,
                event_type="AUTHORIZATION_PENDING",
                amount_cents=0,
                negotiation_id=negotiation_id,
                paypal_order_id=order_id,
                status="CREATED",
            )

        try:
            resp = self.http_client.post(
                f"{self.base_url}{ORDERS_PATH}/{quote(order_id, safe='')}/authorize",
                headers=headers,
                json={},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise PayPalClientError(f"PayPal order authorization failed for '{order_id}': {exc}") from exc

        auth_id = ""
        auth_status = "UNKNOWN"
        amount_cents = 0
        currency = "USD"

        purchase_units = data.get("purchase_units", [])
        if purchase_units and isinstance(purchase_units, list):
            payments = purchase_units[0].get("payments", {})
            auths = payments.get("authorizations", [])
            if auths and isinstance(auths, list):
                first_auth = auths[0]
                auth_id = first_auth.get("id", "")
                auth_status = first_auth.get("status", "")
                amt_dict = first_auth.get("amount", {})
                currency = amt_dict.get("currency_code", "USD")
                val_str = amt_dict.get("value", "0.00")
                amount_cents = amount_cents_from_decimal_str(val_str)

        if not auth_id:
            raise PayPalClientError("PayPal authorization response contained no authorization ID.")

        if self.db_adapter is not None:
            self.db_adapter.record_payment_event(
                idempotency_key=f"{idempotency_key}-completed",
                event_type="AUTHORIZED",
                amount_cents=amount_cents,
                currency_code=currency,
                negotiation_id=negotiation_id,
                paypal_order_id=order_id,
                paypal_authorization_id=auth_id,
                status="COMPLETED",
                raw_response_summary=data,
            )

        return ObservedPayPalAuthorization(
            authorization_id=auth_id,
            order_id=order_id,
            status=auth_status,
            amount_cents=amount_cents,
            currency=currency,
        )

    def inspect_authorization(self, authorization_id: str) -> ObservedPayPalAuthorization:
        token = self.get_access_token()
        try:
            resp = self.http_client.get(
                f"{self.base_url}{AUTHORIZATIONS_PATH}/{quote(authorization_id, safe='')}",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise PayPalClientError(f"Failed to inspect PayPal authorization '{authorization_id}': {exc}") from exc

        auth_id = data.get("id", "")
        status = data.get("status", "")
        amt_dict = data.get("amount", {})
        currency = amt_dict.get("currency_code", "USD")
        val_str = amt_dict.get("value", "0.00")
        amount_cents = amount_cents_from_decimal_str(val_str)
        supplementary = data.get("supplementary_data", {})
        related_ids = supplementary.get("related_ids", {})
        order_id = related_ids.get("order_id", "")

        return ObservedPayPalAuthorization(
            authorization_id=auth_id,
            order_id=order_id,
            status=status,
            amount_cents=amount_cents,
            currency=currency,
        )

    def capture_authorization(
        self,
        authorization_id: str,
        amount_cents: int,
        currency: str,
        idempotency_key: str,
        final_capture: bool = True,
        negotiation_id: UUID | None = None,
    ) -> dict[str, Any]:
        token = self.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "PayPal-Request-Id": idempotency_key,
        }

        if self.db_adapter is not None:
            self.db_adapter.record_payment_event(
                idempotency_key=idempotency_key,
                event_type="CAPTURE_INITIATED",
                amount_cents=amount_cents,
                currency_code=currency,
                negotiation_id=negotiation_id,
                paypal_authorization_id=authorization_id,
                status="CAPTURING",
            )

        payload = {
            "amount": {
                "value": amount_cents_to_decimal_str(amount_cents),
                "currency_code": currency.upper(),
            },
            "final_capture": final_capture,
        }

        try:
            resp = self.http_client.post(
                f"{self.base_url}{AUTHORIZATIONS_PATH}/{quote(authorization_id, safe='')}/capture",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            if self.db_adapter is not None:
                self.db_adapter.record_payment_event(
                    idempotency_key=f"{idempotency_key}-error",
                    event_type="CAPTURE_FAILED",
                    amount_cents=amount_cents,
                    currency_code=currency,
                    negotiation_id=negotiation_id,
                    paypal_authorization_id=authorization_id,
                    status="RECONCILIATION_REQUIRED",
                    raw_response_summary={"error": str(exc)},
                )
            raise PayPalReconciliationError(
                f"Capture network request failed or timed out; state marked RECONCILIATION_REQUIRED: {exc}"
            ) from exc

        capture_id = data.get("id", "")
        capture_status = data.get("status", "")

        if self.db_adapter is not None:
            self.db_adapter.record_payment_event(
                idempotency_key=f"{idempotency_key}-captured",
                event_type="CAPTURE_COMPLETED",
                amount_cents=amount_cents,
                currency_code=currency,
                negotiation_id=negotiation_id,
                paypal_authorization_id=authorization_id,
                paypal_capture_id=capture_id,
                status="COMPLETED" if capture_status == "COMPLETED" else "CAPTURING",
                raw_response_summary=data,
            )

        return data
