import base64
import importlib
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
PROCESS_A_DIR = Path(__file__).resolve().parents[2] / "agent-orchestrator"
MANDATE_GATE_DIR = Path(__file__).resolve().parents[2] / "mandate-gate"
sys.path.insert(0, str(PROCESS_A_DIR))

from app.main import app as process_a_app
from app.channel3 import DiscoveredOffer
from payment_policy.api import create_app
from payment_policy.product_reference import ProductReference, sign_product_reference
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


NOW = datetime.now(timezone.utc)


def _response(payload: object) -> Mock:
    response = Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


class MockChannel3Client:
    def __init__(self) -> None:
        self.details = {
            "monitor-1": {
                "id": "monitor-1",
                "title": "27-inch 4K monitor",
                "offers": [
                    {
                        "domain": "merchant.example",
                        "url": "https://merchant.example/monitor-1",
                        "price": {"price": "329.00", "currency": "USD"},
                        "availability": "InStock",
                        "condition": "new",
                    }
                ],
            },
            "keyboard-1": {
                "id": "keyboard-1",
                "title": "Mechanical keyboard",
                "offers": [
                    {
                        "domain": "merchant.example",
                        "url": "https://merchant.example/keyboard-1",
                        "price": {"price": "89.50", "currency": "USD"},
                        "availability": "InStock",
                        "condition": "new",
                    }
                ],
            },
        }

    def post(self, url: str, **kwargs: object) -> Mock:
        query = kwargs["json"]["query"]
        product_id = "monitor-1" if "monitor" in query else "keyboard-1"
        return _response({"products": [{"id": product_id}]})

    def get(self, url: str, **kwargs: object) -> Mock:
        return _response(self.details[url.rsplit("/", 1)[-1]])

    def discover(
        self, query: str, currency: str, *, limit: int = 5
    ) -> list[DiscoveredOffer]:
        product_id = "monitor-1" if "monitor" in query else "keyboard-1"
        detail = self.details[product_id]
        offer = detail["offers"][0]
        return [
            DiscoveredOffer(
                product_id=product_id,
                name=detail["title"],
                merchant_domain=offer["domain"],
                offer_url=offer["url"],
                price_cents=32900 if product_id == "monitor-1" else 8950,
                currency=currency,
                availability=offer["availability"],
            )
        ]


@pytest.fixture
def process_b_server() -> tuple[str, Ed25519PrivateKey]:
    private_key = Ed25519PrivateKey.generate()
    public_key = base64.b64encode(
        private_key.public_key().public_bytes_raw()
    ).decode("ascii")
    with socket.socket() as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        port = server_socket.getsockname()[1]
    environment = os.environ.copy()
    environment["MANDATE_GATE_PUBLIC_KEY_B64"] = public_key
    environment["MANDATE_GATE_BINDING_PUBLIC_KEY_B64"] = public_key
    environment["PYTHONPATH"] = str(MANDATE_GATE_DIR)
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
        cwd=MANDATE_GATE_DIR,
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
                if httpx.get(f"{base_url}/ready", timeout=0.2).json().get("ready"):
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Process B did not become ready.")
        yield base_url, private_key
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def _process_a_deal() -> dict[str, object]:
    with patch(
        "app.routes.channel3_client_from_environment",
        return_value=MockChannel3Client(),
    ):
        response = TestClient(process_a_app).post(
            "/api/v1/deals/assemble",
            json={
                "negotiation_id": "negotiation-e2e",
                "requirements": ["monitor", "keyboard"],
                "budget_cents": 45000,
                "currency": "USD",
            },
        )
    assert response.status_code == 200
    return response.json()


def _issue_references(
    deal: dict[str, object],
    signing_key: Ed25519PrivateKey,
) -> list[dict[str, object]]:
    application = create_app()
    candidates = [
        {
            "product_identifier": item["product_identifier"],
            "merchant_domain": item["merchant_domain"],
            "price_cents": item["price_cents"],
            "currency": item["currency"],
            "offer_url": "https://untrusted.invalid/claim",
            "is_real": False,
            "verified": True,
        }
        for item in deal["items"]
    ]
    environment = {
        "CHANNEL3_API_KEY": "test-key",
        "MANDATE_SIGNING_PRIVATE_KEY_B64": base64.b64encode(
            signing_key.private_bytes_raw()
        ).decode("ascii"),
    }
    detail_responses = [
        _response(MockChannel3Client().details[item["product_identifier"]])
        for item in deal["items"]
    ]
    with (
        patch.dict("os.environ", environment, clear=False),
        patch(
            "payment_policy.product_reference.httpx.get",
            side_effect=detail_responses,
        ),
        TestClient(application) as client,
    ):
        response = client.post(
            "/internal/v1/product-references/issue",
            json={"products": candidates},
        )
    assert response.status_code == 200
    return response.json()["references"]


def _process_b_payload(
    deal: dict[str, object],
    references: list[dict[str, object]],
    signing_key: Ed25519PrivateKey,
) -> dict[str, object]:
    mandate = MandateV2(
        schema_version="mandate.v2",
        mandate_id="mandate-e2e",
        negotiation_id=deal["negotiation_id"],
        paypal_order_id="ORDER-E2E",
        authorized_cap_cents=deal["budget_cents"],
        currency=deal["currency"],
        allowed_product_categories=["electronics"],
        merchant_restrictions=["merchant.example"],
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(hours=1),
    )
    binding = PostAuthorizationBindingV1(
        schema_version="post-authorization-binding.v1",
        binding_id="binding-e2e",
        mandate_id=mandate.mandate_id,
        mandate_digest_b64=mandate_v2_digest_b64(mandate),
        paypal_order_id=mandate.paypal_order_id,
        paypal_auth_id="AUTH-E2E",
        authorization_status="AUTHORIZED",
        authorized_amount_cents=mandate.authorized_cap_cents,
        currency=mandate.currency,
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(hours=1),
    )
    products = [
        ProductLineV2(
            product_identifier=item["product_identifier"],
            quantity=1,
            description=item["name"],
            unit_price_cents=item["price_cents"],
            offer_url=reference["reference"]["offer_url"],
            product_reference=reference,
        )
        for item, reference in zip(deal["items"], references, strict=True)
    ]
    candidate = CandidateDealV2(
        negotiation_id=mandate.negotiation_id,
        paypal_order_id=mandate.paypal_order_id,
        merchant_id="merchant.example",
        total_cents=deal["total_cents"],
        currency=mandate.currency,
        products=products,
    )
    return {
        "mandate": mandate.model_dump(mode="json"),
        "mandate_signature": base64.b64encode(
            signing_key.sign(canonical_mandate_v2_bytes(mandate))
        ).decode("ascii"),
        "post_authorization_binding": binding.model_dump(mode="json"),
        "binding_signature": base64.b64encode(
            signing_key.sign(canonical_post_authorization_binding_v1_bytes(binding))
        ).decode("ascii"),
        "candidate": candidate.model_dump(mode="json"),
    }


def test_process_a_to_c_to_b_accepts_independently_referenced_deal(
    process_b_server: tuple[str, Ed25519PrivateKey],
) -> None:
    deal = _process_a_deal()
    signing_key = process_b_server[1]
    references = _issue_references(deal, signing_key)
    payload = _process_b_payload(deal, references, signing_key)

    response = httpx.post(
        f"{process_b_server[0]}/api/v2/verify-authorization-binding",
        json=payload,
        timeout=5,
    )

    assert response.status_code == 200
    assert response.json()["verdict"] == "PASS"
    assert response.json()["payment_ready"] is True


@pytest.mark.parametrize(
    "mutation",
    ["price", "merchant", "product_id", "expired", "invalid_signature"],
)
def test_process_b_rejects_tampered_or_expired_references(
    process_b_server: tuple[str, Ed25519PrivateKey],
    mutation: str,
) -> None:
    deal = _process_a_deal()
    signing_key = process_b_server[1]
    references = _issue_references(deal, signing_key)
    payload = _process_b_payload(deal, references, signing_key)
    product = payload["candidate"]["products"][0]
    reference = product["product_reference"]

    if mutation == "price":
        product["unit_price_cents"] = 32901
    elif mutation == "merchant":
        payload["candidate"]["merchant_id"] = "other.example"
    elif mutation == "product_id":
        product["product_identifier"] = "wrong-product"
    elif mutation == "expired":
        expired = ProductReference.model_validate(reference["reference"]).model_copy(
            update={
                "issued_at": NOW - timedelta(hours=2),
                "expires_at": NOW - timedelta(hours=1),
            }
        )
        expired_signed = sign_product_reference(expired, signing_key)
        product["product_reference"] = expired_signed.model_dump(mode="json")
    else:
        reference["signature_b64"] = "invalid"

    response = httpx.post(
        f"{process_b_server[0]}/api/v2/verify-authorization-binding",
        json=payload,
        timeout=5,
    )

    assert response.status_code == 200
    assert response.json()["verdict"] == "REJECT"
    assert response.json()["payment_ready"] is False


def test_process_b_unavailable_and_malformed_responses_fail_closed() -> None:
    from payment_policy.process_b_client import ProcessBClient, ProcessBVerificationError

    unavailable = ProcessBClient("http://127.0.0.1:1")
    with pytest.raises(ProcessBVerificationError):
        unavailable.verify_authorization_binding({})

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"verdict": "PASS", "payment_ready": True, "checks": []},
        )
    )
    malformed = ProcessBClient(
        "http://process-b",
        client=httpx.Client(transport=transport),
    )
    with pytest.raises(ProcessBVerificationError):
        malformed.verify_authorization_binding({})
