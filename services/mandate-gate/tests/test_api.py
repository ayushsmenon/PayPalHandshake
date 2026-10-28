import base64
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from mandate_gate.api import app
from mandate_gate.canonical import canonical_mandate_bytes
from mandate_gate.models import SignedMandate


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
client = TestClient(app)


@pytest.fixture
def signed_request() -> tuple[dict[str, object], str, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    mandate = SignedMandate(
        schema_version="1",
        negotiation_id="HS-2048",
        paypal_auth_id="AUTH-SYNTHETIC",
        authorized_cap_cents=45000,
        currency="USD",
        authorized_merchant_id="merchant-best-buy-sandbox",
        allowed_product_identifiers=["SYNTH-MONITOR-27-4K"],
        issued_at=NOW - timedelta(minutes=5),
        expires_at=NOW + timedelta(hours=1),
        mandate_id="mandate-synthetic-api",
    )
    mandate_data = mandate.model_dump(mode="json")
    signature = base64.b64encode(
        private_key.sign(canonical_mandate_bytes(mandate))
    ).decode("ascii")
    public_key_b64 = base64.b64encode(
        public_key.public_bytes_raw()
    ).decode("ascii")
    request = {
        "mandate": mandate_data,
        "signature": signature,
        "candidate": {
            "negotiation_id": "HS-2048",
            "paypal_auth_id": "AUTH-SYNTHETIC",
            "merchant_id": "merchant-best-buy-sandbox",
            "total_cents": 41850,
            "currency": "USD",
            "products": [
                {
                    "product_identifier": "SYNTH-MONITOR-27-4K",
                    "quantity": 1,
                    "description": "Synthetic monitor",
                }
            ],
        },
    }
    return request, signature, public_key_b64


def test_health_endpoint() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "mandate-gate"}


def test_readiness_requires_valid_public_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MANDATE_GATE_PUBLIC_KEY_B64", raising=False)
    missing = client.get("/ready")
    assert missing.json()["ready"] is False

    monkeypatch.setenv("MANDATE_GATE_PUBLIC_KEY_B64", "not-a-key")
    invalid = client.get("/ready")
    assert invalid.json()["ready"] is False
    assert "missing or invalid" in invalid.json()["reason"]


def test_verify_rejects_when_public_key_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    signed_request: tuple[dict[str, object], str, str],
) -> None:
    monkeypatch.delenv("MANDATE_GATE_PUBLIC_KEY_B64", raising=False)

    response = client.post("/api/v1/verify", json=signed_request[0])

    assert response.status_code == 200
    assert response.json()["verdict"] == "REJECT"
    assert response.json()["payment_ready"] is False
    assert response.json()["checks"][0]["name"] == "public_key_configuration"


def test_valid_signed_request_remains_not_payment_ready(
    monkeypatch: pytest.MonkeyPatch,
    signed_request: tuple[dict[str, object], str, str],
) -> None:
    request, _, public_key = signed_request
    monkeypatch.setenv("MANDATE_GATE_PUBLIC_KEY_B64", public_key)

    response = client.post("/api/v1/verify", json=request)
    data = response.json()

    assert response.status_code == 200
    assert data["verdict"] == "REJECT"
    assert data["payment_ready"] is False
    assert data["checks"][-1]["name"] == "trusted_product_references"


def test_tampered_mandate_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    signed_request: tuple[dict[str, object], str, str],
) -> None:
    request, _, public_key = signed_request
    request["mandate"]["authorized_cap_cents"] = 99999
    monkeypatch.setenv("MANDATE_GATE_PUBLIC_KEY_B64", public_key)

    response = client.post("/api/v1/verify", json=request)

    assert response.json()["verdict"] == "REJECT"
    assert response.json()["checks"][0]["verdict"] == "REJECT"


@pytest.mark.parametrize(
    "change",
    [
        {"candidate": {"total_cents": 45001}},
        {"candidate": {"merchant_id": "merchant-other"}},
        {"candidate": {"paypal_auth_id": "AUTH-OTHER"}},
    ],
)
def test_deal_mismatches_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    signed_request: tuple[dict[str, object], str, str],
    change: dict[str, dict[str, object]],
) -> None:
    request, _, public_key = signed_request
    request["candidate"].update(change["candidate"])
    monkeypatch.setenv("MANDATE_GATE_PUBLIC_KEY_B64", public_key)

    response = client.post("/api/v1/verify", json=request)

    assert response.json()["verdict"] == "REJECT"


def test_malformed_request_is_rejected_by_schema(
    monkeypatch: pytest.MonkeyPatch,
    signed_request: tuple[dict[str, object], str, str],
) -> None:
    request, _, public_key = signed_request
    del request["candidate"]["products"]
    monkeypatch.setenv("MANDATE_GATE_PUBLIC_KEY_B64", public_key)

    response = client.post("/api/v1/verify", json=request)

    assert response.status_code == 422
