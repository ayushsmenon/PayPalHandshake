"""Verify a freshly issued Process C reference through a running Process B."""

from __future__ import annotations

import argparse
import json
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
from payment_policy.product_reference import SignedProductReference  # noqa: E402
from payment_policy.signing import sign_mandate  # noqa: E402


def load_reference(path: Path) -> SignedProductReference:
    try:
        with path.open(encoding="utf-8") as handle:
            return SignedProductReference.model_validate(json.load(handle))
    except (OSError, ValueError) as error:
        raise ValueError(f"could not load a valid signed reference from {path}") from error


def build_request(
    reference: SignedProductReference,
    *,
    environment: dict[str, str],
    now: datetime | None = None,
) -> dict[str, object]:
    issued_at = now or datetime.now(timezone.utc)
    payload = reference.reference
    mandate = SignedMandate(
        schema_version="1",
        negotiation_id="SMOKE-ISSUED-REFERENCE",
        paypal_auth_id="SMOKE-AUTHORIZATION-ONLY",
        authorized_cap_cents=45000,
        currency=payload.currency,
        authorized_merchant_id=payload.merchant_domain,
        allowed_product_identifiers=[payload.channel3_product_id],
        issued_at=issued_at,
        expires_at=min(payload.expires_at, issued_at + timedelta(minutes=5)),
        mandate_id=f"mandate-issued-reference-{issued_at.strftime('%Y%m%d%H%M%S%f')}",
        product_references=[reference],
    )
    envelope = sign_mandate(mandate, environment=environment)
    price = payload.price_minor
    return {
        "mandate": envelope.mandate.model_dump(mode="json"),
        "signature": envelope.signature_b64,
        "candidate": {
            "negotiation_id": mandate.negotiation_id,
            "paypal_auth_id": mandate.paypal_auth_id,
            "merchant_id": payload.merchant_domain,
            "total_cents": price,
            "currency": payload.currency,
            "products": [
                {
                    "product_identifier": payload.channel3_product_id,
                    "quantity": 1,
                    "description": "Issued Channel3 product reference",
                    "unit_price_cents": price,
                    "offer_url": payload.offer_url,
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


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a Process C-issued reference through Process B."
    )
    parser.add_argument("reference_file", type=Path)
    args = parser.parse_args(arguments)
    load_dotenv(PAYMENT_POLICY_ROOT / ".env", override=False)
    try:
        reference = load_reference(args.reference_file)
        request = build_request(reference, environment=os.environ)
    except (ValueError, OSError) as error:
        print(f"Issued-reference smoke test stopped: {error}")
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
        print(f"Issued-reference smoke test failed: HTTP {error.response.status_code}.")
        try:
            print(f"Response body: {safe_error_body(error.response.json())}")
        except ValueError:
            print(f"Response body: {error.response.text}")
        return 1
    except (httpx.RequestError, ValueError) as error:
        print(f"Issued-reference smoke test failed: {error.__class__.__name__}.")
        return 1

    checks = {
        check.get("name"): check.get("verdict")
        for check in result.get("checks", [])
        if isinstance(check, dict)
    }
    print(f"HTTP status: {response.status_code}")
    print(f"Mandate signature: {checks.get('mandate_signature')}")
    print(f"Product-reference verification: {checks.get('trusted_product_references')}")
    print(f"Overall gate verdict: {result.get('verdict')}")
    print(f"Payment ready: {'yes' if result.get('payment_ready') is True else 'no'}")
    return 0 if checks.get("trusted_product_references") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
