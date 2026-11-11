import base64
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mandate-gate"))

from payment_policy.api import create_app
from payment_policy.consent import MandateConsentEvidence
from payment_policy.paypal_adapter import (
    MockPayPalAdapter,
    ObservedPayPalAuthorization,
    ObservedPayPalOrder,
)
from payment_policy.process_b_client import ProcessBClient
from payment_policy.product_reference import (
    ProductReferenceV2,
    sign_product_reference_v2,
)
from payment_policy.versioned_crypto import (
    canonical_mandate_v2_bytes,
    canonical_post_authorization_binding_v1_bytes,
    mandate_v2_digest_b64,
)
from payment_policy.versioned_models import (
    CandidateDealV2,
    MandateV2,
    PostAuthorizationBindingV1,
    ProductLineV2,
)


class StaticConsentProvider:
    def __init__(self, evidence: MandateConsentEvidence | None) -> None:
        self.evidence = evidence

    def get_for_mandate(self, mandate: MandateV2) -> MandateConsentEvidence | None:
        return self.evidence


class CountingMockPayPalAdapter(MockPayPalAdapter):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.order_inspections = 0
        self.authorization_inspections = 0

    def inspect_order(self, order_id: str) -> ObservedPayPalOrder:
        self.order_inspections += 1
        return super().inspect_order(order_id)

    def inspect_authorization(self, authorization_id: str) -> ObservedPayPalAuthorization:
        self.authorization_inspections += 1
        return super().inspect_authorization(authorization_id)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        return int(server_socket.getsockname()[1])


@pytest.fixture
def process_b_server() -> Iterator[tuple[str, Ed25519PrivateKey]]:
    private_key = Ed25519PrivateKey.generate()
    public_key = base64.b64encode(
        private_key.public_key().public_bytes_raw()
    ).decode("ascii")
    port = _free_port()
    environment = os.environ.copy()
    environment["MANDATE_GATE_PUBLIC_KEY_B64"] = public_key
    environment["MANDATE_GATE_BINDING_PUBLIC_KEY_B64"] = public_key
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2] / "mandate-gate")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "mandate_gate.api:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "error",
        ],
        cwd=Path(__file__).resolve().parents[2] / "mandate-gate",
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            if process.poll() is not None:
                raise RuntimeError("Process B exited before becoming ready.")
            try:
                response = httpx.get(f"{base_url}/ready", timeout=0.2)
                if response.status_code == 200 and response.json()["ready"] is True:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("Process B did not become ready.")
        yield base_url, private_key
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def _fixtures(
    private_key: Ed25519PrivateKey,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, object], MandateV2, CountingMockPayPalAdapter, MandateConsentEvidence]:
    current_time = now or datetime.now(timezone.utc)
    mandate = MandateV2(
        schema_version="mandate.v2",
        mandate_id="mandate-integration",
        negotiation_id="negotiation-integration",
        paypal_order_id="ORDER-INTEGRATION",
        authorized_cap_cents=45000,
        currency="USD",
        allowed_product_categories=["electronics"],
        merchant_restrictions=["merchant.example"],
        expected_paypal_payee_id="PAYEE-INTEGRATION",
        issued_at=current_time - timedelta(minutes=1),
        expires_at=current_time + timedelta(hours=1),
    )
    binding = PostAuthorizationBindingV1(
        schema_version="post-authorization-binding.v1",
        binding_id="binding-integration",
        mandate_id=mandate.mandate_id,
        mandate_digest_b64=mandate_v2_digest_b64(mandate),
        paypal_order_id=mandate.paypal_order_id,
        paypal_auth_id="AUTH-INTEGRATION",
        authorization_status="AUTHORIZED",
        authorized_amount_cents=45000,
        currency="USD",
        issued_at=current_time - timedelta(seconds=30),
        expires_at=current_time + timedelta(hours=1),
    )
    reference = ProductReferenceV2(
        schema_version="product-reference.v2",
        channel3_product_id="monitor-4k",
        offer_url="https://merchant.example/products/monitor-4k",
        merchant_domain="merchant.example",
        price_minor=41850,
        currency="USD",
        availability="InStock",
        condition="new",
        issued_at=current_time - timedelta(minutes=1),
        expires_at=current_time + timedelta(hours=1),
        canonical_category="electronics",
        registry_version="test-1",
    )
    signed_reference = sign_product_reference_v2(reference, private_key)
    candidate = CandidateDealV2(
        negotiation_id=mandate.negotiation_id,
        paypal_order_id=mandate.paypal_order_id,
        merchant_id="merchant.example",
        total_cents=41850,
        currency="USD",
        products=[
            ProductLineV2(
                product_identifier="monitor-4k",
                quantity=1,
                description="27-inch 4K monitor",
                unit_price_cents=41850,
                offer_url=reference.offer_url,
                product_reference=signed_reference,
            )
        ],
    )
    consent = MandateConsentEvidence(
        consent_event_id="consent-integration",
        mandate_id=mandate.mandate_id,
        mandate_digest_b64=mandate_v2_digest_b64(mandate),
        negotiation_id=mandate.negotiation_id,
        shopper_account_id="shopper-integration",
        approved_cap_cents=mandate.authorized_cap_cents,
        currency=mandate.currency,
        approved_constraints=mandate.allowed_product_categories,
        approved_merchant_restrictions=mandate.merchant_restrictions,
        consented_at=current_time - timedelta(seconds=30),
        expires_at=current_time + timedelta(hours=1),
    )
    payload = {
        "mandate": mandate.model_dump(mode="json"),
        "mandate_signature": base64.b64encode(
            private_key.sign(canonical_mandate_v2_bytes(mandate))
        ).decode("ascii"),
        "post_authorization_binding": binding.model_dump(mode="json"),
        "binding_signature": base64.b64encode(
            private_key.sign(canonical_post_authorization_binding_v1_bytes(binding))
        ).decode("ascii"),
        "candidate": candidate.model_dump(mode="json"),
    }
    adapter = CountingMockPayPalAdapter(
        ObservedPayPalOrder(
            mandate.paypal_order_id,
            "AUTHORIZE",
            "APPROVED",
            45000,
            "USD",
            "PAYEE-INTEGRATION",
        ),
        ObservedPayPalAuthorization(
            "AUTH-INTEGRATION",
            mandate.paypal_order_id,
            "AUTHORIZED",
            45000,
            "USD",
        ),
        environment="test",
    )
    return payload, mandate, adapter, consent


def _application(
    process_b_url: str,
    private_key: Ed25519PrivateKey,
    *,
    consent: MandateConsentEvidence | None,
    adapter: CountingMockPayPalAdapter,
    consent_provider: StaticConsentProvider | None = None,
):
    return create_app(
        paypal_adapter=adapter,
        process_b_client=ProcessBClient(process_b_url),
        consent_provider=consent_provider
        if consent_provider is not None
        else StaticConsentProvider(consent) if consent is not None else None,
        environment="test",
    )


def _post(
    process_b_url: str,
    private_key: Ed25519PrivateKey,
    payload: dict[str, object],
    *,
    consent: MandateConsentEvidence,
    adapter: CountingMockPayPalAdapter,
) -> dict[str, object]:
    application = _application(
        process_b_url,
        private_key,
        consent=consent,
        adapter=adapter,
    )
    with TestClient(application) as client:
        response = client.post(
            "/internal/v1/payment-decisions/dry-run",
            json=payload,
        )
    assert response.status_code == 200
    return response.json()


def test_local_process_c_to_process_b_dry_run_is_independent_and_read_only(
    process_b_server: tuple[str, Ed25519PrivateKey],
) -> None:
    process_b_url, private_key = process_b_server
    payload, _, adapter, consent = _fixtures(private_key)

    result = _post(
        process_b_url, private_key, payload, consent=consent, adapter=adapter
    )

    assert result == {
        "decision": "APPROVE",
        "payment_ready": True,
        "security_checks_passed": True,
        "mandate_consent_verified": True,
        "authorization_permitted": True,
        "reason": "Process B, PayPal observations, and mandate consent passed.",
    }
    assert adapter.order_inspections == 1
    assert adapter.authorization_inspections == 1
    assert not any(
        hasattr(adapter, method)
        for method in ("create_order", "authorize", "capture", "void")
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "mandate_signature",
        "binding_signature",
        "tampered_binding",
        "wrong_order",
        "wrong_authorization",
        "wrong_payee",
        "over_ceiling",
        "invalid_product_signature",
        "invalid_offer_url",
    ],
)
def test_local_process_b_rejects_tampered_payment_data(
    process_b_server: tuple[str, Ed25519PrivateKey],
    mutation: str,
) -> None:
    process_b_url, private_key = process_b_server
    payload, mandate, adapter, consent = _fixtures(private_key)
    if mutation == "mandate_signature":
        payload["mandate_signature"] = "invalid"
    elif mutation == "binding_signature":
        payload["binding_signature"] = "invalid"
    elif mutation == "tampered_binding":
        payload["post_authorization_binding"]["authorized_amount_cents"] = 44999
    elif mutation == "wrong_order":
        payload["candidate"]["paypal_order_id"] = "ORDER-WRONG"
    elif mutation == "wrong_authorization":
        payload["post_authorization_binding"]["paypal_auth_id"] = "AUTH-WRONG"
    elif mutation == "wrong_payee":
        adapter.order = ObservedPayPalOrder(
            mandate.paypal_order_id, "AUTHORIZE", "APPROVED", 45000, "USD", "PAYEE-WRONG"
        )
    elif mutation == "over_ceiling":
        payload["candidate"]["total_cents"] = 45001
    elif mutation == "invalid_product_signature":
        payload["candidate"]["products"][0]["product_reference"]["signature_b64"] = "invalid"
    elif mutation == "invalid_offer_url":
        payload["candidate"]["products"][0]["offer_url"] = "https://merchant.example/wrong"

    result = _post(
        process_b_url, private_key, payload, consent=consent, adapter=adapter
    )

    assert result["decision"] == "REJECT"
    assert result["payment_ready"] is False
    assert result["authorization_permitted"] is False


@pytest.mark.parametrize("expired_object", ["mandate", "binding"])
def test_local_process_b_rejects_expired_signed_objects(
    process_b_server: tuple[str, Ed25519PrivateKey],
    expired_object: str,
) -> None:
    process_b_url, private_key = process_b_server
    payload, _, adapter, consent = _fixtures(private_key)
    expired_issue = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    target = payload[
        "mandate" if expired_object == "mandate" else "post_authorization_binding"
    ]
    target["issued_at"] = expired_issue
    target["expires_at"] = expired_at

    result = _post(
        process_b_url, private_key, payload, consent=consent, adapter=adapter
    )

    assert result["decision"] == "REJECT"


def test_local_process_c_rejects_missing_or_expired_consent(
    process_b_server: tuple[str, Ed25519PrivateKey],
) -> None:
    process_b_url, private_key = process_b_server
    payload, _, adapter, consent = _fixtures(private_key)
    missing_application = _application(
        process_b_url,
        private_key,
        consent=None,
        adapter=adapter,
    )
    with TestClient(missing_application) as client:
        missing_response = client.post(
            "/internal/v1/payment-decisions/dry-run", json=payload
        )
    missing = missing_response.json()
    assert missing["mandate_consent_verified"] is False
    assert missing["authorization_permitted"] is False

    expired = consent.model_copy(
        update={"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
    )
    result = _post(
        process_b_url, private_key, payload, consent=expired, adapter=adapter
    )
    assert result["decision"] == "REJECT"
    assert result["mandate_consent_verified"] is False
    assert result["authorization_permitted"] is False


def test_local_process_c_rejects_unavailable_or_erroring_process_b(
    process_b_server: tuple[str, Ed25519PrivateKey],
) -> None:
    process_b_url, private_key = process_b_server
    payload, _, adapter, consent = _fixtures(private_key)

    unavailable = _post(
        "http://127.0.0.1:1",
        private_key,
        payload,
        consent=consent,
        adapter=adapter,
    )
    assert unavailable["decision"] == "REJECT"

    erroring = _post(
        process_b_url + "/missing",
        private_key,
        payload,
        consent=consent,
        adapter=adapter,
    )
    assert erroring["decision"] == "REJECT"
