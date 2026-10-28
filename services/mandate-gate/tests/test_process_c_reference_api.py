import base64
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

PAYMENT_POLICY_ROOT = (
    Path(__file__).resolve().parents[2] / "payment-policy-svc"
)
sys.path.insert(0, str(PAYMENT_POLICY_ROOT))

from payment_policy.canonical import canonical_mandate_bytes  # noqa: E402
from payment_policy.models import SignedMandate  # noqa: E402
from payment_policy.product_reference import (  # noqa: E402
    ProductReference,
    sign_product_reference,
)

from mandate_gate.api import VerifyRequest, app  # noqa: E402


NOW = datetime.now(timezone.utc)


@pytest.fixture
def signed_request() -> tuple[dict[str, object], str]:
    private_key = Ed25519PrivateKey.generate()
    reference = ProductReference(
        schema_version="product-reference.v1",
        channel3_product_id="PpawoVb",
        offer_url="https://newegg.com/p/product",
        merchant_domain="newegg.com",
        price_minor=14399,
        currency="USD",
        availability="InStock",
        condition="new",
        issued_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    signed_reference = sign_product_reference(reference, private_key)
    mandate = SignedMandate(
        schema_version="1",
        negotiation_id="NEG-PROCESS-C-1",
        paypal_auth_id="AUTH-SYNTHETIC-1",
        authorized_cap_cents=45000,
        currency="USD",
        authorized_merchant_id="newegg.com",
        allowed_product_identifiers=["PpawoVb"],
        issued_at=NOW - timedelta(minutes=5),
        expires_at=NOW + timedelta(hours=2),
        mandate_id="MANDATE-PROCESS-C-1",
        product_references=[signed_reference],
    )
    signature = base64.b64encode(
        private_key.sign(canonical_mandate_bytes(mandate))
    ).decode("ascii")
    request = {
        "mandate": mandate.model_dump(mode="json"),
        "signature": signature,
        "candidate": {
            "negotiation_id": "NEG-PROCESS-C-1",
            "paypal_auth_id": "AUTH-SYNTHETIC-1",
            "merchant_id": "newegg.com",
            "total_cents": 14399,
            "currency": "USD",
            "products": [
                {
                    "product_identifier": "PpawoVb",
                    "quantity": 1,
                    "description": "MSI PRO MP273U",
                    "unit_price_cents": 14399,
                    "offer_url": "https://newegg.com/p/product",
                    "product_reference": signed_reference.model_dump(mode="json"),
                }
            ],
        },
    }
    return request, base64.b64encode(
        private_key.public_key().public_bytes_raw()
    ).decode("ascii")


def post(
    monkeypatch: pytest.MonkeyPatch,
    request: dict[str, object],
    public_key: str,
):
    monkeypatch.setenv("MANDATE_GATE_PUBLIC_KEY_B64", public_key)
    return TestClient(app).post("/api/v1/verify", json=request)


def assert_rejected(response) -> None:
    assert response.status_code == 200
    assert response.json()["verdict"] == "REJECT"
    assert response.json()["payment_ready"] is False


def test_process_c_signed_reference_is_verified_by_process_b(
    monkeypatch: pytest.MonkeyPatch,
    signed_request: tuple[dict[str, object], str],
) -> None:
    request, public_key = signed_request

    response = post(monkeypatch, request, public_key)

    assert response.json()["verdict"] == "PASS"
    assert response.json()["payment_ready"] is True
    checks = {check["name"]: check["verdict"] for check in response.json()["checks"]}
    assert checks["mandate_signature"] == "PASS"
    assert checks["trusted_product_references"] == "PASS"


def test_process_c_payload_matches_verify_request_schema(
    signed_request: tuple[dict[str, object], str],
) -> None:
    request, _ = signed_request

    parsed = VerifyRequest.model_validate(request)

    assert parsed.mandate.product_references
    assert parsed.candidate.products[0].product_reference


@pytest.mark.parametrize(
    "change",
    [
        lambda request: request["candidate"]["products"][0].pop("offer_url"),
        lambda request: request["candidate"]["products"][0].update(
            {"offer_url": "https://newegg.com/other-product"}
        ),
    ],
)
def test_candidate_offer_url_must_match_signed_reference(
    monkeypatch: pytest.MonkeyPatch,
    signed_request: tuple[dict[str, object], str],
    change,
) -> None:
    request, public_key = signed_request
    change(request)

    assert_rejected(post(monkeypatch, request, public_key))


def test_malformed_candidate_offer_url_is_rejected_by_schema(
    monkeypatch: pytest.MonkeyPatch,
    signed_request: tuple[dict[str, object], str],
) -> None:
    request, public_key = signed_request
    request["candidate"]["products"][0]["offer_url"] = "not-a-url"

    response = post(monkeypatch, request, public_key)

    assert response.status_code == 422


@pytest.mark.parametrize(
    "mutation",
    [
        lambda request: request["candidate"]["products"][0]["product_reference"][
            "reference"
        ].update({"price_minor": 14400}),
        lambda request: request["candidate"]["products"][0]["product_reference"].update(
            {"signature_b64": "tampered"}
        ),
        lambda request: request["mandate"].update({"authorized_cap_cents": 1}),
        lambda request: request.update({"signature": "tampered"}),
        lambda request: request["candidate"]["products"][0].update(
            {"product_identifier": "other-product"}
        ),
        lambda request: request["candidate"].update({"merchant_id": "other.com"}),
        lambda request: request["candidate"]["products"][0].update(
            {"unit_price_cents": 14400}
        ),
        lambda request: request["candidate"].update({"currency": "EUR"}),
    ],
)
def test_tampering_and_candidate_mismatches_reject(
    monkeypatch: pytest.MonkeyPatch,
    signed_request: tuple[dict[str, object], str],
    mutation,
) -> None:
    request, public_key = signed_request
    mutated = deepcopy(request)
    mutation(mutated)

    assert_rejected(post(monkeypatch, mutated, public_key))


def test_expired_reference_rejects(
    monkeypatch: pytest.MonkeyPatch,
    signed_request: tuple[dict[str, object], str],
) -> None:
    request, public_key = signed_request
    request["mandate"]["product_references"][0]["reference"]["issued_at"] = (
        (NOW - timedelta(hours=2)).isoformat()
    )
    request["mandate"]["product_references"][0]["reference"]["expires_at"] = (
        (NOW - timedelta(seconds=1)).isoformat()
    )
    assert_rejected(post(monkeypatch, request, public_key))


def test_unsupported_reference_version_is_schema_rejected(
    monkeypatch: pytest.MonkeyPatch,
    signed_request: tuple[dict[str, object], str],
) -> None:
    request, public_key = signed_request
    request["mandate"]["product_references"][0]["reference"]["schema_version"] = (
        "product-reference.v2"
    )
    response = post(monkeypatch, request, public_key)
    assert response.status_code == 422


@pytest.mark.parametrize(
    "remove_from",
    ["mandate", "candidate"],
)
def test_missing_reference_rejects_or_is_schema_rejected(
    monkeypatch: pytest.MonkeyPatch,
    signed_request: tuple[dict[str, object], str],
    remove_from: str,
) -> None:
    request, public_key = signed_request
    if remove_from == "mandate":
        request["mandate"]["product_references"] = []
    else:
        request["candidate"]["products"][0].pop("product_reference")
    assert_rejected(post(monkeypatch, request, public_key))


def test_valid_reference_does_not_override_another_failing_gate(
    monkeypatch: pytest.MonkeyPatch,
    signed_request: tuple[dict[str, object], str],
) -> None:
    request, public_key = signed_request
    request["signature"] = "tampered"

    response = post(monkeypatch, request, public_key)

    assert_rejected(response)
    checks = {check["name"]: check["verdict"] for check in response.json()["checks"]}
    assert checks["trusted_product_references"] == "PASS"
    assert checks["mandate_signature"] == "REJECT"
