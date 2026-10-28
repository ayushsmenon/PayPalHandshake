"""Verify a Process C-signed product reference through a running Process B."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

PAYMENT_POLICY_ROOT = Path(__file__).resolve().parents[1]
GATE_VERIFY_URL = "http://127.0.0.1:8010/api/v1/verify"
sys.path.insert(0, str(PAYMENT_POLICY_ROOT))

from payment_policy.models import SignedMandate  # noqa: E402
from payment_policy.product_reference import (  # noqa: E402
    ProductReference,
    sign_product_reference,
)
from payment_policy.signing import (  # noqa: E402
    load_signing_private_key,
    sign_mandate,
)


def build_request() -> dict[str, object]:
    issued_at = datetime.now(timezone.utc)
    reference = sign_product_reference(
        ProductReference(
            schema_version="product-reference.v1",
            channel3_product_id="PpawoVb",
            offer_url="https://newegg.com/p/product",
            merchant_domain="newegg.com",
            price_minor=14399,
            currency="USD",
            availability="InStock",
            condition="new",
            issued_at=issued_at,
            expires_at=issued_at + timedelta(minutes=5),
        ),
        load_signing_private_key(os.environ),
    )
    mandate = SignedMandate(
        schema_version="1",
        negotiation_id="SMOKE-REFERENCE-ONLY",
        paypal_auth_id="SMOKE-AUTHORIZATION-ONLY",
        authorized_cap_cents=45000,
        currency="USD",
        authorized_merchant_id="newegg.com",
        allowed_product_identifiers=["PpawoVb"],
        issued_at=issued_at,
        expires_at=issued_at + timedelta(minutes=5),
        mandate_id=f"mandate-reference-smoke-{issued_at.strftime('%Y%m%d%H%M%S%f')}",
        product_references=[reference],
    )
    envelope = sign_mandate(mandate, environment=os.environ)
    return {
        "mandate": envelope.mandate.model_dump(mode="json"),
        "signature": envelope.signature_b64,
        "candidate": {
            "negotiation_id": "SMOKE-REFERENCE-ONLY",
            "paypal_auth_id": "SMOKE-AUTHORIZATION-ONLY",
            "merchant_id": "newegg.com",
            "total_cents": 14399,
            "currency": "USD",
            "products": [
                {
                    "product_identifier": "PpawoVb",
                    "quantity": 1,
                    "description": "Synthetic Channel3 reference smoke test",
                    "unit_price_cents": 14399,
                    "product_reference": reference.model_dump(mode="json"),
                }
            ],
        },
    }


def safe_error_body(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[redacted]"
            if key in {"signature", "signature_b64"}
            else safe_error_body(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [safe_error_body(item) for item in value]
    return value


def main() -> int:
    load_dotenv(PAYMENT_POLICY_ROOT / ".env", override=False)
    try:
        request = build_request()
    except ValueError as error:
        print(f"Product-reference smoke test stopped: {error}")
        return 2
    try:
        response = httpx.post(
            GATE_VERIFY_URL,
            json=request,
            timeout=httpx.Timeout(10.0, connect=3.0),
        )
        response.raise_for_status()
        result = response.json()
    except httpx.HTTPStatusError as error:
        print(
            "Product-reference smoke test failed: "
            f"HTTP {error.response.status_code}."
        )
        try:
            print(f"Response body: {safe_error_body(error.response.json())}")
        except ValueError:
            print(f"Response body: {error.response.text}")
        return 1
    except (httpx.RequestError, ValueError) as error:
        print(f"Product-reference smoke test failed: {error.__class__.__name__}.")
        return 1

    checks = {
        check.get("name"): check.get("verdict")
        for check in result.get("checks", [])
        if isinstance(check, dict)
    }
    print(f"HTTP status: {response.status_code}")
    print(f"Mandate signature: {checks.get('mandate_signature')}")
    print(f"Product-reference check: {checks.get('trusted_product_references')}")
    print(f"Overall gate verdict: {result.get('verdict')}")
    print(f"Payment ready: {'yes' if result.get('payment_ready') is True else 'no'}")
    return 0 if checks.get("trusted_product_references") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
