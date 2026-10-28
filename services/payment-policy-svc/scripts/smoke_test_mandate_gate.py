"""Locally sign a synthetic mandate and verify it through Process B."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

PAYMENT_POLICY_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PAYMENT_POLICY_ROOT / ".env"
GATE_VERIFY_URL = "http://127.0.0.1:8010/api/v1/verify"

sys.path.insert(0, str(PAYMENT_POLICY_ROOT))

from payment_policy.models import SignedMandate  # noqa: E402
from payment_policy.signing import sign_mandate  # noqa: E402


def build_test_mandate() -> SignedMandate:
    issued_at = datetime.now(timezone.utc)
    return SignedMandate(
        schema_version="1",
        negotiation_id="SMOKE-NEGOTIATION-ONLY",
        paypal_auth_id="SMOKE-AUTHORIZATION-ONLY",
        authorized_cap_cents=45000,
        currency="USD",
        authorized_merchant_id="merchant-synthetic-smoke-only",
        allowed_product_identifiers=[
            "SYNTH-SMOKE-MONITOR",
            "SYNTH-SMOKE-KEYBOARD",
        ],
        issued_at=issued_at,
        expires_at=issued_at + timedelta(minutes=5),
        mandate_id=f"mandate-smoke-{issued_at.strftime('%Y%m%d%H%M%S%f')}",
    )


def build_candidate() -> dict[str, object]:
    return {
        "negotiation_id": "SMOKE-NEGOTIATION-ONLY",
        "paypal_auth_id": "SMOKE-AUTHORIZATION-ONLY",
        "merchant_id": "merchant-synthetic-smoke-only",
        "total_cents": 41850,
        "currency": "USD",
        "products": [
            {
                "product_identifier": "SYNTH-SMOKE-MONITOR",
                "quantity": 1,
                "description": "Synthetic smoke-test monitor",
            },
            {
                "product_identifier": "SYNTH-SMOKE-KEYBOARD",
                "quantity": 1,
                "description": "Synthetic smoke-test keyboard",
            },
        ],
    }


def main() -> int:
    load_dotenv(ENV_PATH, override=False)

    try:
        envelope = sign_mandate(build_test_mandate(), environment=os.environ)
    except ValueError as error:
        print(f"Mandate smoke test stopped: {error}")
        return 2

    request_body = {
        "mandate": envelope.mandate.model_dump(mode="json"),
        "signature": envelope.signature_b64,
        "candidate": build_candidate(),
    }

    try:
        response = httpx.post(
            GATE_VERIFY_URL,
            json=request_body,
            timeout=httpx.Timeout(10.0, connect=3.0),
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        print(
            "Mandate smoke test failed: Process B returned "
            f"HTTP {error.response.status_code}."
        )
        return 1
    except httpx.RequestError as error:
        print(
            "Mandate smoke test failed: Process B is unreachable or timed out "
            f"({error.__class__.__name__}). Start it on 127.0.0.1:8010."
        )
        return 1

    try:
        result = response.json()
    except ValueError:
        print("Mandate smoke test failed: Process B returned invalid JSON.")
        return 1

    checks = result.get("checks")
    if not isinstance(checks, list):
        print("Mandate smoke test failed: response did not contain verification checks.")
        return 1
    check_results = {
        check.get("name"): check.get("verdict")
        for check in checks
        if isinstance(check, dict)
    }
    signature_passed = check_results.get("mandate_signature") == "PASS"
    cap_passed = check_results.get("spending_cap") == "PASS"
    product_reference_rejected = (
        check_results.get("trusted_product_references") == "REJECT"
    )
    verdict = result.get("verdict")
    payment_ready = result.get("payment_ready")

    print(f"HTTP status: {response.status_code}")
    print(f"Signature check passed: {'yes' if signature_passed else 'no'}")
    print(f"Spending-cap check passed: {'yes' if cap_passed else 'no'}")
    print(f"Overall gate verdict: {verdict}")
    print(f"Payment ready: {'yes' if payment_ready is True else 'no'}")
    print(
        "Trusted product-reference check rejected: "
        f"{'yes' if product_reference_rejected else 'no'}"
    )

    if not signature_passed:
        print("Mandate smoke test failed: the signature did not verify.")
        return 1
    if not cap_passed:
        print("Mandate smoke test failed: the synthetic candidate exceeded the cap.")
        return 1
    if verdict == "PASS" or payment_ready is True:
        print("Mandate smoke test failed: Process B reported payment readiness.")
        return 1
    if not product_reference_rejected:
        print(
            "Mandate smoke test failed: the expected trusted product-reference "
            "rejection was not reported."
        )
        return 1

    print(
        "Mandate smoke test passed: signature and cap checks succeeded, "
        "while payment readiness remained fail-closed."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
